"""Root operations for the experimental HA DHCP Interface controller."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import secrets
import subprocess
import syslog
import time
import xml.etree.ElementTree as ET

from core import (
    DesiredAttachment, GlobalRole, InterfaceSnapshot, ObservedState, Settings, DHCPHA_DEVICE,
    desired_state, plan_reconcile, reduce_carp_role, validate_shared_mac,
)
from inventory import DEVICE_RE, parse_interface_inventory
import standby


RUNTIME_DIR = Path("/var/run/dhcp-interface-ha")
COMMAND_TIMEOUT = 5
LOCK_TIMEOUT = 5
STATUS_LOCK_TIMEOUT = 2
EVENT_PROGRAM = "dhcp-interface-ha"
EVENT_MAX_LENGTH = 1024


def emit_event(code, severity=syslog.LOG_INFO, sink=None, **fields):
    """Write one bounded native syslog event; logging must never affect control."""
    try:
        def clean(value, limit=160):
            value = re.sub(r"[\x00-\x1f\x7f\u0085\u2028\u2029]+", " ", str(value)).strip()
            return value if len(value) <= limit else value[:limit - 3] + "..."

        code_part = f"event={clean(code, 48)}"
        context = [(name, value) for name, value in fields.items() if name not in ("safety", "outcome")]
        final = [(name, fields[name]) for name in ("safety", "outcome") if name in fields]
        parts = [code_part]

        def render(name, value, limit, budget):
            prefix = f" {clean(name, 24)}="
            room = budget - len(prefix)
            if room <= 0:
                return None
            return prefix + clean(value, min(limit, room))

        reserve = sum(len(render(name, value, 64, 256)) for name, value in final)
        for name, value in context:
            limit = 180 if name == "reason" else 128 if name == "address" else 64
            used = len(" ".join(parts))
            budget = EVENT_MAX_LENGTH - 1 - used - reserve - 1
            field = render(name, value, limit, budget)
            if field is not None:
                parts.append(field.lstrip())
        parts.extend(f"{clean(name, 24)}={clean(value, 64)}" for name, value in final)
        message = " ".join(parts)[:EVENT_MAX_LENGTH - 1]
        if sink is None:
            syslog.openlog(EVENT_PROGRAM)
            sink = syslog.syslog
        sink(severity, message)
    except Exception:
        pass


def _ipv4_addresses(inventory, device):
    """Return (addresses, known); a failed or unfamiliar read stays unknown."""
    item = inventory.get(device)
    if item is None or not isinstance(item.get("ipv4"), list):
        return None, False
    addresses = set()
    for entry in item["ipv4"]:
        candidate = entry if isinstance(entry, str) else (
            entry.get("ipaddr", entry.get("address")) if isinstance(entry, dict) else None
        )
        if not isinstance(candidate, str) or not candidate.strip():
            return None, False
        try:
            addresses.add(str(ipaddress.IPv4Address(candidate.strip())))
        except ipaddress.AddressValueError:
            return None, False
    return sorted(addresses), True


def _command_category(argv):
    name = Path(str(argv[0])).name
    if name == "ifconfig" and argv[1:] == ["-Lm"]:
        return "interface_inventory"
    if name == "ifconfig" and len(argv) > 2:
        action = str(argv[2])
        return "ifconfig_detach" if action.startswith("-") else "ifconfig_" + action
    return {
        "pluginctl": "interface_inventory",
        "sysctl": "carp_allow_read",
    }.get(name, name)


def run(argv, timeout=COMMAND_TIMEOUT):
    return subprocess.run(argv, check=True, capture_output=True, text=True,
                          timeout=timeout).stdout.strip()


def ipv4_route_source():
    # UDP connect selects a source without sending an application packet.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.connect(('192.0.2.1', 9))
        return probe.getsockname()[0]


def flag(node, name):
    child = node.find(name) if node is not None else None
    return child is not None and (child.text or "").strip().lower() not in ("0", "false", "no")


def value(node, name, default=""):
    return node.findtext(name, default=default).strip() if node is not None else default


def interface_snapshot(name, inventory):
    item = inventory.get(name, {})
    return InterfaceSnapshot(
        name, exists=name in inventory, up="up" in item.get("flags", []),
        link_up=item.get("status") == "active", mac=item.get("macaddr"),
        lagg_protocol=item.get("laggproto"),
        mtu=int(item["mtu"]) if item.get("mtu") else None,
        lagg_members=tuple(item.get("laggport", {})),
        promiscuous="promisc" in item.get("flags", []),
    )


def carrier_error(root, carrier, inventory):
    """Reservation checks apply even when shared enablement is off."""
    if not DEVICE_RE.fullmatch(carrier) or carrier == DHCPHA_DEVICE:
        return "configure a valid dedicated Ethernet carrier"
    item = inventory.get(carrier, {})
    if not item or not item.get("is_physical") or item.get("vlan"):
        return "experimental execution requires a physical Ethernet adapter; VLAN carriers are not qualified"
    if not validate_shared_mac(item.get("macaddr", ""))[0]:
        return "carrier has no usable Ethernet MAC"
    if item.get("ipv4") or item.get("ipv6") or item.get("carp"):
        return "carrier must have no IP addresses or CARP instances"
    if item.get("laggproto") or item.get("members") or item.get("tunnel") or item.get("vxlan"):
        return "carrier is an aggregation, bridge, or tunnel"
    interfaces = root.find("interfaces")
    if interfaces is not None:
        for interface in interfaces:
            if value(interface, "if") == carrier:
                return "carrier is still assigned to a logical interface"
    for vlan in root.findall("vlans/vlan"):
        if value(vlan, "if") == carrier:
            return "carrier is the parent of a configured VLAN"
    for lagg in root.findall("laggs/lagg"):
        if carrier in value(lagg, "members").split(","):
            return "carrier belongs to a configured LAGG"
    for ppp in root.findall("ppps/ppp"):
        if carrier in value(ppp, "ports").split(","):
            return "carrier belongs to a configured PPP interface"
    for name, other in inventory.items():
        if name != DHCPHA_DEVICE and carrier in other.get("laggport", {}):
            return "carrier belongs to another runtime LAGG"
        if carrier in other.get("members", {}) or other.get("vlan", {}).get("parent") == carrier:
            return "carrier belongs to a runtime bridge or VLAN"
    return None


@dataclass
class Snapshot:
    raw_config: bytes
    settings: Settings
    observed: ObservedState
    inventory: dict
    managed_interface: str
    local_error: str | None
    carrier_safe: bool
    stopped: bool
    expected_carp_instances: list
    live_carp_instances: list
    carp_aligned: bool
    shared_mac_collisions: list


class Controller:
    def __init__(self, config_path=Path("/conf/config.xml"), runtime_dir=RUNTIME_DIR,
                 command=run, ifindex=socket.if_nametoindex, event_sink=None,
                 route_source=ipv4_route_source):
        self.config_path = Path(config_path)
        self.runtime_dir = Path(runtime_dir)
        self.command = command
        self.command_deadline = None
        self.ifindex = ifindex
        self.event_sink = event_sink
        self.route_source = route_source
        self._daemon_failures = {}
        self._last_interface_observation = None
        self._last_command = None
        self._failed_command = None
        self.marker = self.runtime_dir / "device.dhcpha0lagg"
        self.stopped = self.runtime_dir / "stopped"
        self.activation_failed = self.runtime_dir / "activation-failed"
        self.standby_marker = self.runtime_dir / 'standby-route.json'

    def _default_route(self):
        return standby.default_route(self._command(['/usr/bin/netstat', '--libxo', 'json', '-rn', '-f', 'inet']))

    def _cleanup_carrier_default_locked(self, snapshot):
        # Native reassignment can leave an addressless interface default on
        # the original NIC. It cannot carry traffic once we reserve and fence it.
        if (not snapshot.settings.managed_by_dhcpha or snapshot.local_error
                or not snapshot.carrier_safe or not snapshot.observed.dhcpha_owned
                or snapshot.observed.dhcpha.lagg_members or snapshot.observed.carrier.up):
            return
        carrier = snapshot.settings.carrier
        carrier_index = self.ifindex(carrier)
        stale = {'gateway': f'link#{carrier_index}', 'netif': carrier}
        if self._default_route() != stale:
            return
        fresh = self.snapshot()
        if (fresh.raw_config != snapshot.raw_config or fresh.local_error or not fresh.carrier_safe
                or not fresh.observed.dhcpha_owned or fresh.observed.dhcpha.lagg_members
                or fresh.observed.carrier.up or self.ifindex(carrier) != carrier_index
                or self._default_route() != stale):
            return
        # Match the AF_LINK gateway as well as the destination. A concurrent
        # replacement through another gateway must survive this delete.
        self._command(['/sbin/route', '-n', 'delete', '-inet', 'default', '-interface', carrier])
        if self._default_route() == stale:
            raise RuntimeError('Stale carrier default route cleanup did not verify.')
        self._event('stale_carrier_default_removed', interface=snapshot.managed_interface,
                    carrier=carrier, gateway=stale['gateway'], outcome='verified')

    def _standby_record(self):
        if not self.standby_marker.exists():
            return None
        if self.standby_marker.is_symlink():
            raise ValueError('Standby route ownership file is a symlink.')
        record = json.loads(self.standby_marker.read_text())
        ipaddress.IPv4Address(record['gateway'])
        if not DEVICE_RE.fullmatch(record['netif']):
            raise ValueError('Invalid standby route ownership.')
        return record

    @staticmethod
    def _standby_path(snapshot):
        return standby.configuration(ET.fromstring(snapshot.raw_config), snapshot.managed_interface,
                                     snapshot.settings.carrier)

    @staticmethod
    def _standby_eligible(snapshot, path):
        addresses, known = _ipv4_addresses(snapshot.inventory, path['device'])
        return (path['enabled'] and not path['error'] and snapshot.settings.enabled
                and snapshot.settings.managed_by_dhcpha and not snapshot.local_error and not snapshot.stopped
                and snapshot.carp_aligned and snapshot.observed.carp_allowed
                and bool(snapshot.observed.carp_states)
                and all(state == 'BACKUP' for state in snapshot.observed.carp_states)
                and snapshot.observed.dhcpha_owned and not snapshot.observed.dhcpha.lagg_members
                and not snapshot.observed.carrier.up and known and path['source_address'] in addresses
                and 'up' in snapshot.inventory.get(path['device'], {}).get('flags', []))

    def _withdraw_standby_locked(self, restore=False):
        record = self._standby_record()
        if record is None:
            return False
        current = self._default_route()
        if current == record:
            self._command(['/sbin/route', '-n', 'delete', '-inet', 'default', record['gateway']])
            if self._default_route() == record:
                raise RuntimeError('Standby default route cleanup did not verify.')
        if restore and self._default_route() is None:
            # Do not clear retry evidence until native recalculation completes.
            # This bridge suppresses monitor callbacks to avoid lock recursion.
            self._command(['/usr/local/bin/php',
                           '/usr/local/opnsense/scripts/dhcp_interface_ha/restore_routing.php'])
            if self._default_route() == record:
                raise RuntimeError('Native routing reselected the standby default during cleanup.')
        # A native/admin replacement belongs to its writer. Never delete it.
        self.standby_marker.unlink()
        self._event('standby_path_withdrawn', gateway=record['gateway'], interface=record['netif'],
                    outcome='verified')
        return current == record

    def _standby_source_matches(self, path):
        try:
            return self.route_source() == path['source_address']
        except OSError:
            return False

    def _reconcile_standby_locked(self, snapshot):
        path = self._standby_path(snapshot)
        record = self._standby_record()
        if not path['enabled'] and record is None:
            return
        if not self._standby_eligible(snapshot, path):
            self._withdraw_standby_locked(restore=True)
            if path['error']:
                raise RuntimeError(path['error'])
            return
        expected = {'gateway': path['vip'], 'netif': path['device']}
        if record is not None and record != expected:
            self._withdraw_standby_locked()
        current = self._default_route()
        if current == expected:
            if record != expected:
                raise RuntimeError('An unowned default already uses the selected CARP VIP.')
            if self._standby_source_matches(path):
                return
            self._withdraw_standby_locked(restore=True)
            current = self._default_route()
        if current is not None and current['netif'] != DHCPHA_DEVICE:
            raise RuntimeError('An unrelated IPv4 default route conflicts with the standby path.')
        fresh = self.snapshot()
        if (fresh.raw_config != snapshot.raw_config or not self._standby_eligible(fresh, path)
                or self._default_route() != current):
            raise RuntimeError('Role or settings changed before standby routing.')
        # Write intent before mutation, so a crash can still release our route.
        temporary = self.standby_marker.with_suffix('.tmp')
        with temporary.open('w') as handle:
            os.chmod(temporary, 0o600)
            json.dump(expected, handle)
        temporary.replace(self.standby_marker)
        # FreeBSD route change can retain the addressless WAN's source nexthop.
        # Recreate the default, as native system_default_route() does, and bind
        # both interface and primary address before source readback.
        if current:
            delete = ['/sbin/route', '-n', 'delete', '-inet', 'default']
            if current['gateway'].startswith('link#'):
                delete += ['-interface', current['netif']]
            else:
                delete.append(current['gateway'])
            self._command(delete)
        self._command(['/sbin/route', '-n', 'add', '-inet', 'default', path['vip'],
                       '-ifp', path['device'], '-ifa', path['source_address']])
        final = self.snapshot()
        if (final.raw_config != snapshot.raw_config or not self._standby_eligible(final, path)
                or self._default_route() != expected or not self._standby_source_matches(path)):
            self._withdraw_standby_locked(restore=True)
            raise RuntimeError('Standby route/interface/source readback did not verify.')
        self._event('standby_path_selected', gateway=path['vip'], interface=path['interface'],
                    source=path['source_address'], outcome='verified')

    def route_status(self):
        return {'observed_default': self._default_route()}

    def routing(self):
        """Postprocess native routing without triggering interface transitions."""
        snapshot = None
        try:
            with self.locked():
                snapshot = self.snapshot()
                self._cleanup_carrier_default_locked(snapshot)
                self._reconcile_standby_locked(snapshot)
        except Exception as error:
            self._report_failure('standby_routing', error, 'unchanged', snapshot)
            raise

    def _standby_status(self, snapshot):
        path = self._standby_path(snapshot)
        result = dict(path, state='disabled', observed_default=None, owned=False,
                      observed_at=time.time(), source_address=None, reason=path['error'],
                      cleanup_pending=self.standby_marker.exists())
        try:
            record = self._standby_record()
            if not path['enabled'] and record is None:
                return result
            current = self._default_route()
            eligible = self._standby_eligible(snapshot, path)
            result.update(observed_default=current, owned=record is not None and current == record,
                          cleanup_pending=record is not None and not eligible)
            expected = {'gateway': path['vip'], 'netif': path['device']}
            if eligible and current == expected and result['owned']:
                source = self.route_source()
                result['source_address'] = source
                if source == path['source_address']:
                    result.update(state='selected', reason='IPv4 default selected through the internal CARP VIP; reachability not tested.')
                else:
                    result.update(state='unavailable', reason='Default route source address differs from the selected internal address.')
            elif record is not None:
                result.update(state='unavailable', reason='Owned standby routing requires reconciliation.')
            elif path['enabled']:
                result.update(state='unavailable' if snapshot.observed.carp_states and
                              all(s == 'BACKUP' for s in snapshot.observed.carp_states) else 'inactive',
                              reason=path['error'] or 'Standby path is not selected in the current role or configuration.')
        except Exception as error:
            result.update(state='unknown', reason=str(error))
        return result

    def _event(self, code, severity=syslog.LOG_INFO, **fields):
        emit_event(code, severity, self.event_sink, **fields)

    @staticmethod
    def _event_context(snapshot):
        if snapshot is None:
            return {}
        return {
            "interface": snapshot.managed_interface or "unknown",
            "carrier": snapshot.settings.carrier or "unknown",
        }

    @staticmethod
    def _attachment_event_state(snapshot):
        """Classify the observed attachment before a verified mutation event."""
        device = snapshot.observed.dhcpha
        if (device.exists and not snapshot.observed.dhcpha_owned) or (
            not device.lagg_members and snapshot.settings.managed_by_dhcpha
            and snapshot.observed.carrier.up
        ):
            return "UNVERIFIED"
        return "ATTACHED" if device.lagg_members else "FENCED"

    def _observe_interfaces(self, snapshot):
        """Record pre-mutation facts on change, including admin-down/link-down.

        Driver link reports can differ while administratively down. Keep the
        native report separate from UP and PROMISC; none implies the others.
        This uses the existing snapshot and never probes or changes a NIC.
        """
        fields = self._event_context(snapshot)
        desired = desired_state(snapshot.settings, snapshot.observed)
        fields.update(enabled=snapshot.settings.enabled, role=reduce_carp_role(snapshot.observed.carp_states).value)
        for label, device in (("carrier", snapshot.observed.carrier), ("lagg", snapshot.observed.dhcpha)):
            item = snapshot.inventory.get(device.name, {})
            fields.update({
                f"{label}_exists": device.exists,
                f"{label}_up": device.up,
                f"{label}_link": item.get("status", "unknown"),
                f"{label}_promisc": device.promiscuous,
            })
            if label == "carrier":
                for family in ("ipv4", "ipv6", "carp"):
                    entries = item.get(family)
                    fields[f"carrier_{family}"] = len(entries) if isinstance(entries, (list, dict)) else "unknown"
        fields["lagg_members"] = ",".join(sorted(snapshot.observed.dhcpha.lagg_members)) or "none"
        fields["reason"] = snapshot.local_error or (
            "controller stopped" if snapshot.stopped else desired.reason
        )
        if fields != self._last_interface_observation:
            self._event("interface_observed", phase="before_reconcile", **fields)
            self._last_interface_observation = fields

    def _report_failure(self, operation, error, safety, snapshot=None, daemon=False, context=None):
        context = self._event_context(snapshot) if context is None else context
        condition = f"{type(error).__name__.lower()}_{self._failed_command or self._last_command or 'operation'}"
        key = (
            operation, context.get("interface", "unknown"), context.get("carrier", "unknown"),
            condition, safety,
        )
        if daemon:
            now = time.monotonic()
            previous = self._daemon_failures.get(operation)
            if previous is not None:
                previous_key, previous_time = previous
                if previous_key == key and now - previous_time < 60:
                    return
            self._daemon_failures[operation] = (key, now)
        self._event(
            "operation_failed", syslog.LOG_ERR, operation=operation, **context,
            condition=condition, reason=error, safety=safety, outcome="failed",
        )

    def _report_daemon_recovery(self, operation, safety="verified", context=None):
        failure = self._daemon_failures.pop(operation, None)
        if failure is None:
            return
        key, _ = failure
        fields = context or {"interface": key[1], "carrier": key[2]}
        self._event(
            "operation_recovered", operation=operation, **fields,
            condition=key[3], safety=safety,
            outcome="verified",
        )

    def _report_recovery(self, status):
        if (status.get("state") in ("FAULT", "UNKNOWN", "PENDING")
                or status.get("actual_attachment") == "UNVERIFIED"):
            return
        self._report_daemon_recovery(
            "reconcile", safety=status.get("actual_attachment", "unknown").lower(),
        )

    def report_daemon_failure(self, operation, error):
        if operation not in ("reconcile", "carp_status_refresh"):
            return
        self._last_command = operation
        self._failed_command = None
        self._report_failure(operation, error, "unknown", daemon=True)

    def report_daemon_recovery(self, operation):
        if operation not in ("reconcile", "carp_status_refresh"):
            return
        self._report_daemon_recovery(operation)

    @contextmanager
    def locked(self):
        self.runtime_dir.mkdir(mode=0o755, parents=True, exist_ok=True)
        # This inode is never removed while the package is installed.
        with (self.runtime_dir / "transition.lock").open("a") as lock:
            deadline = time.monotonic() + LOCK_TIMEOUT
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("interface transition lock is busy")
                    time.sleep(0.05)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    @contextmanager
    def observing(self):
        lock_path = self.runtime_dir / "transition.lock"
        if not lock_path.exists():
            raise TimeoutError("interface transition state is not initialized")
        with lock_path.open("r") as lock:
            # Normal reconciliation briefly owns the lock too. Wait for a fresh
            # snapshot within the UI's status budget instead of failing at once.
            deadline = time.monotonic() + STATUS_LOCK_TIMEOUT
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
                    break
                except BlockingIOError as exc:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("interface transition is busy") from exc
                    time.sleep(0.05)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def inventory(self):
        items = parse_interface_inventory(self._command(["/sbin/ifconfig", "-Lm"]))
        if not isinstance(items, dict) or not items or any(not isinstance(v, dict) for v in items.values()):
            raise ValueError("invalid native interface inventory")
        for name, item in items.items():
            if not DEVICE_RE.fullmatch(name) or not isinstance(item.get("flags"), list):
                raise ValueError("incomplete native interface inventory")
            for field in ("laggport", "carp", "vlan", "members"):
                if field in item and not isinstance(item[field], dict):
                    raise ValueError("invalid native interface topology")
        return items

    def _command(self, argv):
        self._last_command = _command_category(argv)
        try:
            if self.command_deadline is None:
                return self.command(argv)
            remaining = self.command_deadline - time.monotonic()
            if remaining <= 0.05:
                raise TimeoutError("controller status observation deadline expired")
            if self.command is run:
                return run(argv, max(0.05, min(COMMAND_TIMEOUT, remaining)))
            return self.command(argv)
        except Exception:
            self._failed_command = self._last_command
            raise

    def owned(self, inventory=None):
        try:
            identity = json.loads(self.marker.read_text())
            group = identity["group"]
            if not re.fullmatch(r"wh[0-9a-f]{12}x", group):
                return False
            items = self.inventory() if inventory is None else inventory
            return (identity["index"] == self.ifindex(DHCPHA_DEVICE)
                    and group in items.get(DHCPHA_DEVICE, {}).get("groups", []))
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def snapshot(self):
        raw = self.config_path.read_bytes()
        root = ET.fromstring(raw)
        if root.tag != "opnsense":
            raise ValueError("invalid OPNsense configuration root")
        inventory = self.inventory()
        shared = root.find("OPNsense/DhcpInterfaceHaShared")
        local = root.find("OPNsense/DhcpInterfaceHaLocal")
        carrier = value(local, "carrier")
        name = value(local, "managed_interface")
        managed = next((i for i in root.findall("interfaces/*") if i.tag == name), None)
        error = carrier_error(root, carrier, inventory)
        carrier_safe = error is None
        mtu_text = value(managed, "mtu")
        mtu = None
        if mtu_text:
            try:
                mtu = int(mtu_text)
                if not 576 <= mtu <= 65535:
                    raise ValueError()
            except ValueError:
                error = "managed DHCP interface MTU is invalid"
        settings = Settings(flag(shared, "enabled"), carrier, value(shared, "shared_mac").lower(),
                            value(managed, "if") == DHCPHA_DEVICE, mtu)
        if not validate_shared_mac(settings.shared_mac)[0]:
            error = "a valid shared unicast MAC is required"
        if value(shared, "failback_delay", "0") != "0":
            error = "delayed failback is not implemented; set failback delay to zero"
        if not flag(managed, "enable") or value(managed, "ipaddr") != "dhcp":
            error = "managed DHCP interface must be enabled with IPv4 DHCP"
        if value(managed, "ipaddrv6").lower() not in ("", "none"):
            error = "managed DHCP interface must be IPv4 only"
        if value(managed, "spoofmac") or flag(managed, "hw_settings_overwrite") or value(managed, "media") or value(managed, "mediaopt"):
            error = "clear native interface spoof-MAC, offload overrides and media settings"
        assignments = [i for i in root.findall("interfaces/*") if value(i, "if") == DHCPHA_DEVICE]
        if len(assignments) != 1 or assignments[0].tag != name:
            error = "exactly one managed logical interface must use dhcpha0lagg"
        mac_collisions = []
        for device, item in inventory.items():
            if device in (carrier, DHCPHA_DEVICE):
                continue
            observed_macs = {str(item.get("macaddr", "")).lower(), str(item.get("macaddr_hw", "")).lower()}
            if settings.shared_mac and settings.shared_mac.lower() in observed_macs:
                error = "shared MAC collides with another local interface"
                mac_collisions.append(device)
        expected = set()
        for vip in root.findall("virtualip/vip"):
            if value(vip, "mode") != "carp" or flag(vip, "disabled"):
                continue
            logical = value(vip, "interface")
            physical = value(root.find("interfaces/" + logical), "if") if DEVICE_RE.fullmatch(logical) else ""
            expected.add((physical, value(vip, "vhid")))
            if logical == name:
                error = "managed DHCP interface must not carry CARP VIPs"
        actual = {(device, str(vhid)) for device, item in inventory.items() for vhid in item.get("carp", {})}
        expected_instances = [
            {"interface": device, "vhid": vhid}
            for device, vhid in sorted(expected)
        ]
        live_instances = [
            {
                "interface": device,
                "vhid": str(vhid),
                "status": str(carp.get("status", "UNKNOWN")).upper(),
            }
            for device, item in sorted(inventory.items())
            for vhid, carp in sorted(item.get("carp", {}).items(), key=lambda entry: str(entry[0]))
        ]
        carp_aligned = bool(expected) and expected == actual
        states = tuple(str(carp.get("status", "UNKNOWN")).upper()
                       for item in inventory.values() for carp in item.get("carp", {}).values())
        if not carp_aligned:
            error = "configured CARP instances do not match the live inventory"
        allow = self._command(["/sbin/sysctl", "-n", "net.inet.carp.allow"])
        if allow not in ("0", "1"):
            raise ValueError("invalid CARP allow state")
        observed = ObservedState(
            states, allow == "1", flag(root, "virtualip_carp_maintenancemode"), self.owned(inventory),
            interface_snapshot(carrier, inventory), interface_snapshot(DHCPHA_DEVICE, inventory),
        )
        return Snapshot(raw, settings, observed, inventory, name, error, carrier_safe, self.stopped.exists(),
                        expected_instances, live_instances, carp_aligned, mac_collisions)

    def prepare_locked(self):
        items = self.inventory()
        if DHCPHA_DEVICE in items:
            if not self.owned(items) or items[DHCPHA_DEVICE].get("laggproto") != "failover":
                raise RuntimeError("refusing an unverified existing dhcpha0lagg")
            return False
        created = self._command(["/sbin/ifconfig", "lagg", "create"])
        if not re.fullmatch(r"lagg[0-9]+", created):
            raise RuntimeError("unexpected LAGG creation result")
        try:
            self._command(["/sbin/ifconfig", created, "name", DHCPHA_DEVICE])
            created = DHCPHA_DEVICE
            self._command(["/sbin/ifconfig", created, "laggproto", "failover"])
            check = self.inventory().get(created, {})
            if check.get("laggproto") != "failover" or check.get("laggport"):
                raise RuntimeError("new device verification failed")
            index = self.ifindex(created)
            group = "wh" + secrets.token_hex(6) + "x"
            self._command(["/sbin/ifconfig", created, "group", group])
            if group not in self.inventory()[created].get("groups", []):
                raise RuntimeError("new device identity verification failed")
            temporary = self.marker.with_suffix(".tmp")
            temporary.write_text(json.dumps({"index": index, "group": group}) + "\n")
            temporary.replace(self.marker)
        except Exception:
            self._command(["/sbin/ifconfig", created, "destroy"])
            raise
        return True

    def fence_locked(self, snapshot=None):
        """Best effort silence, then verified detach; never claim an unknown fence."""
        items = self.inventory()
        dhcpha = items.get(DHCPHA_DEVICE)
        if dhcpha is not None:
            if not self.owned(items) or not dhcpha.get("laggproto"):
                raise RuntimeError("cannot fence an unverified same-name device")
            errors = []
            if "up" in dhcpha.get("flags", []) or dhcpha.get("laggport"):
                try:
                    self._command(["/sbin/ifconfig", DHCPHA_DEVICE, "down"])
                except Exception as exc:
                    errors.append(str(exc))
            for member in dhcpha.get("laggport", {}):
                if not DEVICE_RE.fullmatch(member):
                    raise RuntimeError("invalid member name in native inventory")
                try:
                    current = self.inventory()
                    if not self.owned(current) or member not in current.get(DHCPHA_DEVICE, {}).get("laggport", {}):
                        raise RuntimeError("member ownership changed before fencing")
                    self._command(["/sbin/ifconfig", member, "down"])
                    fresh = self.inventory()
                    if (not self.owned(fresh) or member not in fresh.get(DHCPHA_DEVICE, {}).get("laggport", {})
                            or member not in fresh or "up" in fresh[member].get("flags", [])):
                        raise RuntimeError("member remained up; refusing live detach")
                    self._command(["/sbin/ifconfig", DHCPHA_DEVICE, "-laggport", member])
                except Exception as exc:
                    errors.append(str(exc))
            fresh = self.inventory().get(DHCPHA_DEVICE, {})
            if not self.owned() or fresh.get("laggport"):
                raise RuntimeError("fencing failed; members remain: " + "; ".join(errors))
        # Never down a newly reassigned interface based on a stale config read.
        if snapshot and snapshot.settings.managed_by_dhcpha and snapshot.carrier_safe:
            current = self.snapshot()
            if current.raw_config == snapshot.raw_config and current.carrier_safe and current.observed.carrier.up:
                self._command(["/sbin/ifconfig", current.settings.carrier, "down"])
                if self.snapshot().observed.carrier.up:
                    raise RuntimeError("reserved carrier remained up after fencing")

    def _guard_master_route(self, snapshot, strict=True):
        path = self._standby_path(snapshot)
        if not path['enabled']:
            return
        try:
            current = self._default_route()
        except Exception:
            if strict:
                raise
            return
        if current and current['gateway'] == path['vip']:
            raise RuntimeError('An IPv4 default points at the locally owned CARP VIP.')

    def promote_locked(self, start):
        plan = plan_reconcile(start.settings, start.observed)
        if plan.warnings and plan.desired.attachment is not DesiredAttachment.ATTACHED:
            raise RuntimeError(plan.desired.reason)
        for cmd in plan.commands:
            fresh = self.snapshot()
            if (fresh.raw_config != start.raw_config or fresh.local_error or fresh.stopped
                    or not fresh.observed.dhcpha_owned
                    or desired_state(fresh.settings, fresh.observed).attachment is not DesiredAttachment.ATTACHED):
                raise RuntimeError("promotion cancelled: configuration, ownership or CARP eligibility changed")
            self._guard_master_route(fresh, strict=self._attachment_event_state(start) != 'ATTACHED')
            args = cmd.argv
            if (args[2] == "down" and args[1] not in (DHCPHA_DEVICE, start.settings.carrier)
                    and args[1] not in fresh.observed.dhcpha.lagg_members):
                raise RuntimeError("old member no longer belongs to the owned LAGG")
            if args[2] == "-laggport":
                if args[3] not in fresh.observed.dhcpha.lagg_members:
                    raise RuntimeError("member topology changed during transition")
                member = fresh.inventory.get(args[3], {})
                if "up" in member.get("flags", []):
                    raise RuntimeError("refusing to detach a transmitting member")
            if args[2] == "ether":
                if fresh.observed.carrier.up or fresh.observed.dhcpha.lagg_members:
                    raise RuntimeError("carrier must be down and detached before MAC preparation")
            if args[2] == "laggport":
                if (fresh.observed.dhcpha.up or fresh.observed.carrier.up or fresh.observed.dhcpha.lagg_members
                        or fresh.observed.carrier.mac != start.settings.shared_mac):
                    raise RuntimeError("carrier MAC/down verification failed before attachment")
            if args[1] == DHCPHA_DEVICE and args[2] == "up":
                if not (fresh.observed.dhcpha.promiscuous and fresh.observed.carrier.promiscuous):
                    raise RuntimeError("shared-MAC receive filter verification failed before activation")
                if (fresh.observed.dhcpha.lagg_members != (start.settings.carrier,)
                        or fresh.observed.carrier.mac != start.settings.shared_mac
                        or fresh.observed.dhcpha.mac != start.settings.shared_mac
                        or (start.settings.managed_mtu is not None and
                            not fresh.observed.carrier.mtu == fresh.observed.dhcpha.mtu == start.settings.managed_mtu)):
                    raise RuntimeError("member, MAC or MTU verification failed before activation")
            self._command(list(args))
        final = self.snapshot()
        self._guard_master_route(final, strict=self._attachment_event_state(start) != 'ATTACHED')
        if (final.raw_config != start.raw_config or final.local_error or final.stopped
                or desired_state(final.settings, final.observed).attachment is not DesiredAttachment.ATTACHED
                or plan_reconcile(final.settings, final.observed).commands
                or not final.observed.dhcpha_owned or final.observed.dhcpha.lagg_protocol != "failover"):
            raise RuntimeError("active attachment verification failed")

    def status(self):
        self._last_command = None
        self._failed_command = None
        previous_deadline = self.command_deadline
        self.command_deadline = time.monotonic() + COMMAND_TIMEOUT
        try:
            with self.observing():
                snapshot = self.snapshot()
                standby_status = self._standby_status(snapshot)
                activation_failed = self.activation_failed.exists()
        finally:
            self.command_deadline = previous_deadline
        plan = plan_reconcile(snapshot.settings, snapshot.observed)
        dhcpha = snapshot.observed.dhcpha
        ipv4_addresses, ipv4_known = _ipv4_addresses(
            snapshot.inventory,
            DHCPHA_DEVICE if snapshot.settings.managed_by_dhcpha else "",
        )
        actual = "ATTACHED" if dhcpha.lagg_members else "FENCED"
        if (not dhcpha.lagg_members and snapshot.settings.managed_by_dhcpha
                and snapshot.observed.carrier.up):
            actual = "UNVERIFIED"
        if dhcpha.exists and not snapshot.observed.dhcpha_owned:
            actual = "UNVERIFIED"
        elif not snapshot.settings.managed_by_dhcpha and not dhcpha.lagg_members:
            actual = "UNMANAGED"
        role = reduce_carp_role(snapshot.observed.carp_states)
        controller_running = self.controller_running()
        managed = snapshot.settings.managed_by_dhcpha
        valid_attachment = (
            snapshot.observed.dhcpha_owned
            and dhcpha.lagg_protocol == "failover"
            and dhcpha.promiscuous and snapshot.observed.carrier.promiscuous
            and dhcpha.lagg_members == (snapshot.settings.carrier,)
            and snapshot.observed.carrier.mac == snapshot.settings.shared_mac
            and dhcpha.mac == snapshot.settings.shared_mac
            and (snapshot.settings.managed_mtu is None or
                 dhcpha.mtu == snapshot.settings.managed_mtu == snapshot.observed.carrier.mtu)
        )
        attached = actual == "ATTACHED"
        # A detached standby carrier is deliberately down, so its link report
        # cannot distinguish a cable fault from our own fencing.
        carrier_link_failed = snapshot.observed.carrier.up and not snapshot.observed.carrier.link_up
        attachment_unsafe = attached and (
            not valid_attachment or not managed or not snapshot.settings.enabled
            or not snapshot.observed.carp_allowed or snapshot.observed.carp_maintenance
            or role is not GlobalRole.MASTER
        )
        if actual == "UNVERIFIED" or attachment_unsafe:
            state = "FAULT"
            if actual == "UNVERIFIED" or not valid_attachment:
                reason_code = "attachment_unverified"
                reason = "The observed interface attachment does not match the verified plugin configuration."
            elif not managed or not snapshot.settings.enabled:
                reason_code = "attachment_while_disabled_or_unmanaged"
                reason = "A DHCP HA carrier is attached while the plugin is disabled or the logical interface is not managed."
            else:
                reason_code = "attachment_when_carp_ineligible"
                reason = "A DHCP HA carrier is attached while native CARP does not authorize this node."
        elif not managed:
            state, reason_code = "SETUP_INCOMPLETE", "managed_assignment_missing"
            reason = "Local interface setup is incomplete; Configure interface completes the guarded native assignment while disabled."
        elif not self._device_exists(snapshot):
            state, reason_code = "SETUP_INCOMPLETE", "device_missing"
            reason = "The plugin device is absent; preparation by the controller or guarded setup has not been verified."
        elif not snapshot.observed.dhcpha_owned:
            state, reason_code = "FAULT", "device_ownership_unverified"
            reason = "dhcpha0lagg exists but its current-boot ownership cannot be verified."
        elif not snapshot.expected_carp_instances:
            state, reason_code = "SETUP_INCOMPLETE", "carp_configuration_missing"
            reason = "Configure native OPNsense CARP instances before enabling HA DHCP Interface."
        elif not snapshot.carp_aligned:
            state, reason_code = "UNKNOWN", "carp_inventory_mismatch"
            reason = "Configured and live CARP instances do not match."
        elif snapshot.settings.enabled and (
            snapshot.local_error or snapshot.stopped or not controller_running
            or carrier_link_failed
        ):
            state = "FAULT"
            reason_code = (
                "service_stopped" if snapshot.stopped
                else "carrier_link_down" if carrier_link_failed
                else "local_readiness_failed"
            )
            reason = "The enabled node is not locally ready: " + (
                "service stopped" if snapshot.stopped
                else "selected carrier has no link" if carrier_link_failed
                else snapshot.local_error or "controller is not running"
            )
        elif snapshot.settings.enabled and activation_failed:
            state, reason_code = "FAULT", "activation_failed"
            reason = "Local activation failed. Repair the cause shown in the Log, then restart the service to retry."
        elif not snapshot.settings.enabled:
            if attached:
                state, reason_code = "FAULT", "disabled_but_attached"
                reason = "The plugin is disabled but the DHCP HA device still has a carrier attached."
            elif snapshot.local_error:
                state, reason_code = "SETUP_INCOMPLETE", "local_setup_incomplete"
                reason = snapshot.local_error
            elif actual == "FENCED":
                state, reason_code = "DISABLED", "plugin_disabled"
                reason = "The plugin is disabled and its owned device is verified detached."
            else:
                state, reason_code = "SETUP_INCOMPLETE", "device_not_verified_detached"
                reason = "Setup is incomplete because the plugin device is not verified detached."
        elif not snapshot.observed.carp_allowed:
            state, reason_code = "FENCED", "carp_disabled"
            reason = "Native CARP is administratively disabled on this node."
        elif snapshot.observed.carp_maintenance:
            state, reason_code = "FENCED", "carp_maintenance"
            reason = "Native CARP maintenance mode prevents attachment."
        elif role is GlobalRole.INDETERMINATE:
            state, reason_code = "FENCED", "carp_role_indeterminate"
            reason = "Native CARP role is not currently eligible for attachment."
        elif role is GlobalRole.BACKUP and actual == "FENCED":
            state, reason_code = "STANDBY", "global_backup"
            reason = "Standby — local adapter intentionally disconnected."
        elif role is GlobalRole.MASTER and actual == "ATTACHED" and valid_attachment:
            state, reason_code = "ACTIVE", "global_master_attached"
            reason = "Global CARP MASTER with the selected local adapter verified attached."
        else:
            state, reason_code = "PENDING", "controller_convergence"
            reason = "Waiting for controller convergence."

        return {
            "state": state,
            "reason_code": reason_code,
            "reason": reason,
            "controller_running": controller_running,
            "stopped": snapshot.stopped,
            "actual_attachment": actual,
            "desired_attachment": ("UNMANAGED" if not snapshot.settings.managed_by_dhcpha else
                                   "FENCED" if snapshot.local_error or snapshot.stopped else plan.desired.attachment.value),
            "global_role": role.value,
            "carp_allowed": snapshot.observed.carp_allowed,
            "carp_maintenance": snapshot.observed.carp_maintenance,
            "carp_aligned": snapshot.carp_aligned,
            "expected_carp_instances": snapshot.expected_carp_instances,
            "live_carp_instances": snapshot.live_carp_instances,
            "local_error": snapshot.local_error,
            "enabled": snapshot.settings.enabled,
            "managed_by_dhcpha": managed,
            "managed_interface": snapshot.managed_interface,
            "ipv4_addresses": ipv4_addresses if ipv4_known else None,
            "carrier": snapshot.observed.carrier.__dict__,
            "dhcpha": dhcpha.__dict__,
            "owned": snapshot.observed.dhcpha_owned,
            "carrier_safe": snapshot.carrier_safe,
            "carrier_capable": bool(
                snapshot.inventory.get(snapshot.settings.carrier, {}).get("is_physical")
                and not snapshot.inventory.get(snapshot.settings.carrier, {}).get("vlan")
                and validate_shared_mac(snapshot.inventory.get(snapshot.settings.carrier, {}).get("macaddr", ""))[0]
            ),
            "shared_mac_collisions": snapshot.shared_mac_collisions,
            "standby_internet": standby_status,
        }

    @staticmethod
    def _device_exists(snapshot):
        return snapshot.observed.dhcpha.exists

    def controller_running(self):
        try:
            pid = int((self.runtime_dir / "controller.pid").read_text().strip())
            os.kill(pid, 0)
            return True
        except (OSError, ValueError):
            return False

    def _repair_condition(self, plan):
        actions = {command.argv[2] for command in plan.commands}
        if "promisc" in actions:
            return "receive_mode_missing"
        if "ether" in actions:
            return "shared_mac_drift"
        if "mtu" in actions:
            return "mtu_drift"
        if "laggport" in actions or "-laggport" in actions:
            return "attachment_topology_drift"
        return "attachment_state_drift"

    def reconcile(self, daemon=False):
        self._last_command = None
        self._failed_command = None
        snapshot = None
        previous_attachment = None
        previous_members = ()
        operation = "reconcile"
        failure_reported = False
        activation_attempted = False
        try:
            with self.locked():
                try:
                    snapshot = self.snapshot()
                    self._observe_interfaces(snapshot)
                    if snapshot.settings.managed_by_dhcpha and not snapshot.observed.dhcpha.exists:
                        if self.prepare_locked():
                            self._event(
                                "device_prepared", **self._event_context(snapshot), device=DHCPHA_DEVICE,
                                safety="owned_detached", outcome="verified",
                            )
                        snapshot = self.snapshot()
                    dhcpha = snapshot.observed.dhcpha
                    previous_members = tuple(sorted(dhcpha.lagg_members))
                    previous_attachment = self._attachment_event_state(snapshot)
                    desired = desired_state(snapshot.settings, snapshot.observed)
                    plan = None
                    reason = snapshot.local_error or ("controller stopped" if snapshot.stopped else desired.reason)
                    if snapshot.local_error or snapshot.stopped or desired.attachment is not DesiredAttachment.ATTACHED:
                        self.fence_locked(snapshot)
                        if not snapshot.settings.enabled or not snapshot.settings.managed_by_dhcpha:
                            self.activation_failed.unlink(missing_ok=True)
                        final_members = ()
                    else:
                        plan = plan_reconcile(snapshot.settings, snapshot.observed)
                        if previous_attachment != 'ATTACHED':
                            self._withdraw_standby_locked()
                        self._guard_master_route(snapshot, strict=previous_attachment != 'ATTACHED')
                        activation_attempted = True
                        self.promote_locked(snapshot)
                        self.activation_failed.unlink(missing_ok=True)
                        final_members = (snapshot.settings.carrier,)

                    context = self._event_context(snapshot)
                    old_attachment = (
                        "UNVERIFIED" if previous_attachment == "UNVERIFIED"
                        else ",".join(previous_members) or "FENCED"
                    )
                    new_attachment = ",".join(final_members) or "FENCED"
                    if old_attachment != new_attachment:
                        self._event(
                            "attachment_changed", operation=operation, **context,
                            old=old_attachment, new=new_attachment, reason=reason,
                            safety="verified", outcome="verified",
                        )
                    elif plan is not None and plan.commands and previous_attachment in ("ATTACHED", "FENCED"):
                        self._event(
                            "repair_completed", operation=operation, **context,
                            condition=self._repair_condition(plan), safety="verified",
                            outcome="completed",
                        )
                    try:
                        if not final_members:
                            self._cleanup_carrier_default_locked(self.snapshot())
                        if self._standby_path(snapshot)['enabled'] or self.standby_marker.exists():
                            self._reconcile_standby_locked(self.snapshot())
                    except Exception as route_error:
                        # Optional connectivity failure must not demote a safe
                        # standby or tear down a correct active attachment.
                        self._report_failure('standby_routing', route_error, 'unchanged', snapshot, daemon)
                    else:
                        self._report_daemon_recovery('standby_routing')
                except Exception as exc:
                    try:
                        self.fence_locked(snapshot)
                    except Exception as fence_error:
                        failure_reported = True
                        self._report_failure(operation, fence_error, "unverified", snapshot, daemon)
                        raise RuntimeError(f"{exc}; fence could not be verified: {fence_error}") from exc
                    if activation_attempted:
                        fresh = self.snapshot()
                        # A normal CARP/configuration change cancels promotion;
                        # only failed activation with unchanged authority latches.
                        if (fresh.raw_config == snapshot.raw_config and not fresh.stopped
                                and desired_state(fresh.settings, fresh.observed).attachment is DesiredAttachment.ATTACHED):
                            self.activation_failed.touch(mode=0o600)
                    failure_reported = True
                    self._report_failure(operation, exc, "fenced", snapshot, daemon)
                    raise RuntimeError(f"{exc}; owned attachment fenced") from exc
        except Exception as exc:
            # Lock acquisition happens before the protected reconcile body. It
            # cannot be fenced safely without owning the transition lock.
            if not failure_reported:
                self._report_failure(operation, exc, "unknown", snapshot, daemon)
            raise
        try:
            status = self.status()
        except Exception as exc:
            self._report_failure(operation, exc, "unknown", snapshot, daemon)
            raise
        if daemon:
            self._report_recovery(status)
        return status

    def prepare(self):
        self._last_command = None
        self._failed_command = None
        try:
            with self.locked():
                created = self.prepare_locked()
        except Exception as exc:
            self._report_failure("prepare", exc, "unknown")
            raise
        if created:
            self._event(
                "device_prepared", device=DHCPHA_DEVICE, safety="owned_detached", outcome="verified",
            )

    def prepare_setup(self):
        """Prepare only during explicit, disabled setup and verify detached ownership."""
        self._last_command = None
        self._failed_command = None
        snapshot = None
        created = False
        try:
            with self.locked():
                snapshot = self.snapshot()
                if snapshot.settings.enabled:
                    raise RuntimeError("disable HA DHCP Interface before preparing the device")
                device = snapshot.inventory.get(DHCPHA_DEVICE)
                if device is not None and (
                    not snapshot.observed.dhcpha_owned
                    or device.get("laggproto") != "failover"
                    or device.get("laggport")
                ):
                    raise RuntimeError("refusing an unverified or attached existing dhcpha0lagg")
                created = self.prepare_locked()
                verified = self.inventory()
                device = verified.get(DHCPHA_DEVICE, {})
                if (
                    not self.owned(verified)
                    or device.get("laggproto") != "failover"
                    or device.get("laggport")
                ):
                    raise RuntimeError("prepared device did not verify as owned, failover and detached")
        except Exception as exc:
            self._report_failure("prepare_setup", exc, "unknown", snapshot)
            raise
        if created:
            self._event(
                "device_prepared", **self._event_context(snapshot), device=DHCPHA_DEVICE,
                safety="owned_detached", outcome="verified",
            )
        return {"prepared": True, "device": DHCPHA_DEVICE, "detached": True, "owned": True}

    def fence(self, stop=False):
        self._last_command = None
        self._failed_command = None
        snapshot = None
        previous_attachment = None
        previous_members = ()
        operation = "stop" if stop else "fence"
        try:
            with self.locked():
                if stop:
                    self.stopped.touch(mode=0o600)
                try:
                    snapshot = self.snapshot()
                except Exception:
                    snapshot = None
                if snapshot is not None:
                    dhcpha = snapshot.observed.dhcpha
                    previous_members = tuple(sorted(dhcpha.lagg_members))
                    previous_attachment = self._attachment_event_state(snapshot)
                self.fence_locked(snapshot)
                self._withdraw_standby_locked(restore=True)
        except Exception as exc:
            self._report_failure(operation, exc, "unverified", snapshot)
            raise
        if previous_attachment in ("ATTACHED", "UNVERIFIED"):
            old_attachment = (
                "UNVERIFIED" if previous_attachment == "UNVERIFIED"
                else ",".join(previous_members) or "FENCED"
            )
            self._event(
                "attachment_changed", operation=operation, **self._event_context(snapshot),
                old=old_attachment, new="FENCED", reason="explicit fencing",
                safety="verified", outcome="verified",
            )

    def resume(self):
        self._last_command = None
        self._failed_command = None
        try:
            with self.locked():
                if self.activation_failed.exists():
                    self.fence_locked(self.snapshot())
                    self.activation_failed.unlink()
                self.stopped.unlink(missing_ok=True)
        except Exception as exc:
            self._report_failure("resume", exc, "unknown")
            raise

    def remove(self):
        self._last_command = None
        self._failed_command = None
        previous_attachment = None
        previous_members = ()
        event_context = {}
        try:
            with self.locked():
                root = ET.fromstring(self.config_path.read_bytes())
                if any(value(i, "if") == DHCPHA_DEVICE for i in root.findall("interfaces/*")):
                    raise RuntimeError("reassign dhcpha0lagg before package removal")
                local = root.find("OPNsense/DhcpInterfaceHaLocal")
                event_context = {
                    "interface": value(local, "managed_interface") or "unknown",
                    "carrier": value(local, "carrier") or "unknown",
                }
                items = self.inventory()
                device = items.get(DHCPHA_DEVICE, {})
                previous_members = tuple(sorted(device.get("laggport", {})))
                previous_attachment = "ATTACHED" if previous_members else "FENCED"
                self.stopped.touch(mode=0o600)
                self.fence_locked()
                self._withdraw_standby_locked(restore=True)
                if DHCPHA_DEVICE in self.inventory():
                    if not self.owned():
                        raise RuntimeError("refusing removal of an unowned interface")
                    self._command(["/sbin/ifconfig", DHCPHA_DEVICE, "destroy"])
                if DHCPHA_DEVICE in self.inventory():
                    raise RuntimeError("device removal could not be verified")
                self.marker.unlink(missing_ok=True)
        except Exception as exc:
            self._report_failure("remove", exc, "unverified", context=event_context)
            raise
        if previous_attachment == "ATTACHED":
            self._event(
                "attachment_changed", operation="remove", **event_context,
                old=",".join(previous_members), new="FENCED",
                reason="package removal", safety="verified", outcome="verified",
            )

    def health(self):
        # Eligibility deliberately ignores current role; BACKUP can recover.
        self._last_command = None
        self._failed_command = None
        with self.locked():
            snapshot = self.snapshot()
            if not snapshot.settings.enabled or not snapshot.settings.managed_by_dhcpha:
                self.fence_locked(snapshot)
                return True
            incapable = (snapshot.local_error or snapshot.stopped
                         or self.activation_failed.exists()
                         or not snapshot.observed.dhcpha_owned
                         or snapshot.observed.dhcpha.lagg_protocol != "failover"
                         or not snapshot.observed.carrier.exists)
            # Link is readiness telemetry, not attachment authority. Fencing on
            # no-link would stop negotiation and make a down BACKUP ineligible.
            if incapable:
                self.fence_locked(snapshot)
                return False
            if (snapshot.observed.carp_states and all(s == 'MASTER' for s in snapshot.observed.carp_states)
                    and self.standby_marker.exists()):
                try:
                    self._withdraw_standby_locked()
                except Exception:
                    if self._attachment_event_state(snapshot) == 'ATTACHED':
                        try:
                            self._guard_master_route(snapshot, strict=False)
                        except Exception:
                            self.fence_locked(snapshot)
                            return False
                        return True
                    # Only a local promotion obstruction affects native health;
                    # an unavailable standby Internet path never demotes BACKUP.
                    self.fence_locked(snapshot)
                    return False
            return True
