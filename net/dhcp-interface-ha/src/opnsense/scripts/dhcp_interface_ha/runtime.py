"""Root operations for the experimental DHCP Interface HA controller."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import json
from pathlib import Path
import re
import socket
import secrets
import subprocess
import time
import xml.etree.ElementTree as ET

from core import (
    DesiredAttachment, InterfaceSnapshot, ObservedState, Settings, DHCPHA_DEVICE,
    desired_state, plan_reconcile, validate_shared_mac,
)


RUNTIME_DIR = Path("/var/run/dhcp-interface-ha")
COMMAND_TIMEOUT = 5
LOCK_TIMEOUT = 5
DEVICE_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_.-]{0,14}\Z")


def run(argv):
    return subprocess.run(argv, check=True, capture_output=True, text=True,
                          timeout=COMMAND_TIMEOUT).stdout.strip()


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
    local_error: str | None
    carrier_safe: bool
    stopped: bool


class Controller:
    def __init__(self, config_path=Path("/conf/config.xml"), runtime_dir=RUNTIME_DIR,
                 command=run, ifindex=socket.if_nametoindex):
        self.config_path = Path(config_path)
        self.runtime_dir = Path(runtime_dir)
        self.command = command
        self.ifindex = ifindex
        self.marker = self.runtime_dir / "device.dhcpha0lagg"
        self.stopped = self.runtime_dir / "stopped"

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

    def inventory(self):
        items = json.loads(self.command(["/usr/local/sbin/pluginctl", "-D"]))
        if not isinstance(items, dict) or not items or any(not isinstance(v, dict) for v in items.values()):
            raise ValueError("invalid native interface inventory")
        for name, item in items.items():
            if not DEVICE_RE.fullmatch(name) or not isinstance(item.get("flags"), list):
                raise ValueError("incomplete native interface inventory")
            for field in ("laggport", "carp", "vlan", "members"):
                if field in item and not isinstance(item[field], dict):
                    raise ValueError("invalid native interface topology")
        return items

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
        carrier = value(root.find("OPNsense/DhcpInterfaceHaLocal"), "carrier")
        name = value(shared, "managed_interface", "wan")
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
        for device, item in inventory.items():
            if device in (carrier, DHCPHA_DEVICE):
                continue
            if settings.shared_mac and settings.shared_mac in (item.get("macaddr"), item.get("macaddr_hw")):
                error = "shared MAC collides with another local interface"
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
        states = tuple(str(carp.get("status", "UNKNOWN")).upper()
                       for item in inventory.values() for carp in item.get("carp", {}).values())
        if not expected or actual != expected:
            error = "configured CARP instances do not match the live inventory"
            states = ("UNKNOWN",)
        allow = self.command(["/sbin/sysctl", "-n", "net.inet.carp.allow"])
        if allow not in ("0", "1"):
            raise ValueError("invalid CARP allow state")
        observed = ObservedState(
            states, allow == "1", flag(root, "virtualip_carp_maintenancemode"), self.owned(inventory),
            interface_snapshot(carrier, inventory), interface_snapshot(DHCPHA_DEVICE, inventory),
        )
        return Snapshot(raw, settings, observed, inventory, error, carrier_safe, self.stopped.exists())

    def prepare_locked(self):
        items = self.inventory()
        if DHCPHA_DEVICE in items:
            if not self.owned(items) or items[DHCPHA_DEVICE].get("laggproto") != "failover":
                raise RuntimeError("refusing an unverified existing dhcpha0lagg")
            return
        created = self.command(["/sbin/ifconfig", "lagg", "create"])
        if not re.fullmatch(r"lagg[0-9]+", created):
            raise RuntimeError("unexpected LAGG creation result")
        try:
            self.command(["/sbin/ifconfig", created, "name", DHCPHA_DEVICE])
            created = DHCPHA_DEVICE
            self.command(["/sbin/ifconfig", created, "laggproto", "failover"])
            check = self.inventory().get(created, {})
            if check.get("laggproto") != "failover" or check.get("laggport"):
                raise RuntimeError("new device verification failed")
            index = self.ifindex(created)
            group = "wh" + secrets.token_hex(6) + "x"
            self.command(["/sbin/ifconfig", created, "group", group])
            if group not in self.inventory()[created].get("groups", []):
                raise RuntimeError("new device identity verification failed")
            temporary = self.marker.with_suffix(".tmp")
            temporary.write_text(json.dumps({"index": index, "group": group}) + "\n")
            temporary.replace(self.marker)
        except Exception:
            self.command(["/sbin/ifconfig", created, "destroy"])
            raise

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
                    self.command(["/sbin/ifconfig", DHCPHA_DEVICE, "down"])
                except Exception as exc:
                    errors.append(str(exc))
            for member in dhcpha.get("laggport", {}):
                if not DEVICE_RE.fullmatch(member):
                    raise RuntimeError("invalid member name in native inventory")
                try:
                    current = self.inventory()
                    if not self.owned(current) or member not in current.get(DHCPHA_DEVICE, {}).get("laggport", {}):
                        raise RuntimeError("member ownership changed before fencing")
                    self.command(["/sbin/ifconfig", member, "down"])
                    fresh = self.inventory()
                    if (not self.owned(fresh) or member not in fresh.get(DHCPHA_DEVICE, {}).get("laggport", {})
                            or member not in fresh or "up" in fresh[member].get("flags", [])):
                        raise RuntimeError("member remained up; refusing live detach")
                    self.command(["/sbin/ifconfig", DHCPHA_DEVICE, "-laggport", member])
                except Exception as exc:
                    errors.append(str(exc))
            fresh = self.inventory().get(DHCPHA_DEVICE, {})
            if not self.owned() or fresh.get("laggport"):
                raise RuntimeError("fencing failed; members remain: " + "; ".join(errors))
        # Never down a newly reassigned interface based on a stale config read.
        if snapshot and snapshot.settings.managed_by_dhcpha and snapshot.carrier_safe:
            current = self.snapshot()
            if current.raw_config == snapshot.raw_config and current.carrier_safe and current.observed.carrier.up:
                self.command(["/sbin/ifconfig", current.settings.carrier, "down"])
                if self.snapshot().observed.carrier.up:
                    raise RuntimeError("reserved carrier remained up after fencing")

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
                if (fresh.observed.dhcpha.lagg_members != (start.settings.carrier,)
                        or fresh.observed.carrier.mac != start.settings.shared_mac
                        or fresh.observed.dhcpha.mac != start.settings.shared_mac
                        or (start.settings.managed_mtu is not None and
                            not fresh.observed.carrier.mtu == fresh.observed.dhcpha.mtu == start.settings.managed_mtu)):
                    raise RuntimeError("member, MAC or MTU verification failed before activation")
            self.command(list(args))
        final = self.snapshot()
        if (final.raw_config != start.raw_config or final.local_error or final.stopped
                or desired_state(final.settings, final.observed).attachment is not DesiredAttachment.ATTACHED
                or plan_reconcile(final.settings, final.observed).commands
                or not final.observed.dhcpha_owned or final.observed.dhcpha.lagg_protocol != "failover"):
            raise RuntimeError("active attachment verification failed")

    def status(self):
        snapshot = self.snapshot()
        plan = plan_reconcile(snapshot.settings, snapshot.observed)
        dhcpha = snapshot.observed.dhcpha
        actual = "ATTACHED" if dhcpha.lagg_members else "FENCED"
        if (not dhcpha.lagg_members and snapshot.settings.managed_by_dhcpha
                and snapshot.observed.carrier.up):
            actual = "UNVERIFIED"
        if dhcpha.exists and not snapshot.observed.dhcpha_owned:
            actual = "UNVERIFIED"
        elif not snapshot.settings.managed_by_dhcpha and not dhcpha.lagg_members:
            actual = "UNMANAGED"
        return {
            "actual_attachment": actual,
            "desired_attachment": ("UNMANAGED" if not snapshot.settings.managed_by_dhcpha else
                                   "FENCED" if snapshot.local_error or snapshot.stopped else plan.desired.attachment.value),
            "global_role": plan.desired.role.value,
            "reason": snapshot.local_error or ("service stopped" if snapshot.stopped else plan.desired.reason),
            "enabled": snapshot.settings.enabled,
            "carrier": snapshot.observed.carrier.__dict__,
            "dhcpha": dhcpha.__dict__,
            "owned": snapshot.observed.dhcpha_owned,

        }

    def reconcile(self):
        with self.locked():
            snapshot = None
            try:
                snapshot = self.snapshot()
                if snapshot.settings.managed_by_dhcpha and not snapshot.observed.dhcpha.exists:
                    self.prepare_locked()
                    snapshot = self.snapshot()
                desired = desired_state(snapshot.settings, snapshot.observed)
                if snapshot.local_error or snapshot.stopped or desired.attachment is not DesiredAttachment.ATTACHED:
                    self.fence_locked(snapshot)
                else:
                    self.promote_locked(snapshot)
            except Exception as exc:
                try:
                    self.fence_locked(snapshot)
                except Exception as fence_error:
                    raise RuntimeError(f"{exc}; fence could not be verified: {fence_error}") from exc
                raise RuntimeError(f"{exc}; owned attachment fenced") from exc
        return self.status()

    def prepare(self):
        with self.locked():
            self.prepare_locked()

    def fence(self, stop=False):
        with self.locked():
            if stop:
                self.stopped.touch(mode=0o600)
            try:
                snapshot = self.snapshot()
            except Exception:
                snapshot = None
            self.fence_locked(snapshot)

    def resume(self):
        with self.locked():
            self.stopped.unlink(missing_ok=True)

    def remove(self):
        with self.locked():
            root = ET.fromstring(self.config_path.read_bytes())
            if any(value(i, "if") == DHCPHA_DEVICE for i in root.findall("interfaces/*")):
                raise RuntimeError("reassign dhcpha0lagg before package removal")
            self.stopped.touch(mode=0o600)
            self.fence_locked()
            if DHCPHA_DEVICE in self.inventory():
                if not self.owned():
                    raise RuntimeError("refusing removal of an unowned interface")
                self.command(["/sbin/ifconfig", DHCPHA_DEVICE, "destroy"])
            if DHCPHA_DEVICE in self.inventory():
                raise RuntimeError("device removal could not be verified")
            self.marker.unlink(missing_ok=True)

    def health(self):
        # Eligibility deliberately ignores current role; BACKUP can recover.
        with self.locked():
            snapshot = self.snapshot()
            if not snapshot.settings.enabled or not snapshot.settings.managed_by_dhcpha:
                self.fence_locked(snapshot)
                return True
            incapable = (snapshot.local_error or snapshot.stopped
                         or not snapshot.observed.dhcpha_owned
                         or snapshot.observed.dhcpha.lagg_protocol != "failover"
                         or not snapshot.observed.carrier.link_up)
            if incapable:
                self.fence_locked(snapshot)
                return False
            return True
