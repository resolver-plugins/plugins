#!/usr/local/bin/python3

"""
Pure decision and command-planning logic for os-dhcp-interface-ha.

This module deliberately does not read OPNsense configuration and does not
execute commands.  Runtime integration is kept at the boundary so the
fencing primitive can be replaced if the prototype gates disprove the
current LAGG candidate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import secrets
import re
from typing import Iterable


DHCPHA_DEVICE = "dhcpha0lagg"
_MAC_RE = re.compile(r"^(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$")


class GlobalRole(str, Enum):
    MASTER = "MASTER"
    BACKUP = "BACKUP"
    INDETERMINATE = "INDETERMINATE"


class DesiredAttachment(str, Enum):
    ATTACHED = "ATTACHED"
    FENCED = "FENCED"
    UNMANAGED = "UNMANAGED"


@dataclass(frozen=True)
class InterfaceSnapshot:
    name: str
    exists: bool = False
    up: bool = False
    link_up: bool = False
    mac: str | None = None
    lagg_protocol: str | None = None
    mtu: int | None = None
    lagg_members: tuple[str, ...] = ()
    promiscuous: bool = False


@dataclass(frozen=True)
class ObservedState:
    carp_states: tuple[str, ...] = ()
    carp_allowed: bool = True
    carp_maintenance: bool = False
    dhcpha_owned: bool = False
    carrier: InterfaceSnapshot | None = None
    dhcpha: InterfaceSnapshot | None = None


@dataclass(frozen=True)
class Settings:
    enabled: bool
    carrier: str
    shared_mac: str
    managed_by_dhcpha: bool = True
    managed_mtu: int | None = None


@dataclass(frozen=True)
class DesiredState:
    role: GlobalRole
    attachment: DesiredAttachment
    reason: str


@dataclass(frozen=True)
class Command:
    argv: tuple[str, ...]
    reason: str

    def shell_display(self) -> str:
        return " ".join(self.argv)


@dataclass
class Plan:
    desired: DesiredState
    commands: list[Command] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def normalize_mac(value: str) -> str:
    value = value.strip().lower()
    if not _MAC_RE.fullmatch(value):
        raise ValueError("invalid MAC address")
    return value


def validate_shared_mac(value: str) -> tuple[bool, str]:
    try:
        normalized = normalize_mac(value)
    except ValueError:
        return False, "invalid MAC address syntax"

    octets = bytes(int(part, 16) for part in normalized.split(":"))
    if octets == b"\x00" * 6:
        return False, "all-zero MAC address is not usable"
    if octets == b"\xff" * 6:
        return False, "broadcast MAC address is not usable"
    if octets[0] & 0x01:
        return False, "multicast MAC address is not usable"
    return True, ""


def is_locally_administered_unicast(value: str) -> bool:
    ok, _ = validate_shared_mac(value)
    if not ok:
        return False
    first = int(normalize_mac(value).split(":")[0], 16)
    return bool(first & 0x02) and not bool(first & 0x01)


def generate_private_mac() -> str:
    # Use the conventional 02: locally-administered unicast prefix and keep
    # the remaining 40 bits cryptographically random.
    octets = bytes([0x02]) + secrets.token_bytes(5)
    return ":".join(f"{byte:02x}" for byte in octets)


def is_virtual_router_mac(value: str) -> bool:
    """Return True for the IANA VRRP/CARP virtual-router prefix."""
    try:
        normalized = normalize_mac(value)
    except ValueError:
        return False
    return normalized.startswith(("00:00:5e:00:01:", "00:00:5e:00:02:"))


def reduce_carp_role(states: Iterable[str]) -> GlobalRole:
    """
    Match OPNsense's global active/passive semantic.

    A node is MASTER only when at least one CARP state exists and every
    observed state is MASTER.  Any BACKUP state makes the node BACKUP.
    INIT, mixed unknown states, disabled/no states, and parse failures are
    indeterminate and therefore fail closed.
    """
    normalized = tuple(str(state).strip().upper() for state in states if str(state).strip())
    if not normalized:
        return GlobalRole.INDETERMINATE
    if "BACKUP" in normalized:
        return GlobalRole.BACKUP
    if all(state == "MASTER" for state in normalized):
        return GlobalRole.MASTER
    return GlobalRole.INDETERMINATE


def desired_state(settings: Settings, observed: ObservedState) -> DesiredState:
    if not settings.managed_by_dhcpha:
        return DesiredState(
            GlobalRole.INDETERMINATE,
            DesiredAttachment.UNMANAGED,
            "managed OPNsense interface is not assigned to dhcpha0lagg",
        )

    if not settings.enabled:
        return DesiredState(
            GlobalRole.INDETERMINATE,
            DesiredAttachment.FENCED,
            "plugin disabled",
        )

    valid_mac, error = validate_shared_mac(settings.shared_mac)
    if not valid_mac:
        return DesiredState(
            GlobalRole.INDETERMINATE,
            DesiredAttachment.FENCED,
            f"invalid shared MAC: {error}",
        )

    if not observed.carp_allowed:
        return DesiredState(
            GlobalRole.INDETERMINATE,
            DesiredAttachment.FENCED,
            "CARP is administratively disabled",
        )

    if observed.carp_maintenance:
        return DesiredState(
            GlobalRole.INDETERMINATE,
            DesiredAttachment.FENCED,
            "persistent CARP maintenance mode is active",
        )

    role = reduce_carp_role(observed.carp_states)
    if role is not GlobalRole.MASTER:
        return DesiredState(role, DesiredAttachment.FENCED, f"global CARP role is {role.value}")

    if not settings.carrier.strip():
        return DesiredState(role, DesiredAttachment.FENCED, "local carrier is not configured")

    carrier = observed.carrier
    if carrier is None or not carrier.exists:
        return DesiredState(role, DesiredAttachment.FENCED, "configured local carrier is missing")
    # Physical drivers can report no link while administratively down. Link
    # negotiation follows activation; it cannot authorize that activation.
    return DesiredState(role, DesiredAttachment.ATTACHED, "global CARP MASTER and local carrier present")


def plan_reconcile(settings: Settings, observed: ObservedState) -> Plan:
    """Plan pre-spoofed attachment; runtime verifies each transition boundary."""
    desired = desired_state(settings, observed)
    plan = Plan(desired=desired)
    dhcpha = observed.dhcpha or InterfaceSnapshot(DHCPHA_DEVICE)
    carrier = observed.carrier or InterfaceSnapshot(settings.carrier)

    if desired.attachment is DesiredAttachment.UNMANAGED:
        return plan
    if not dhcpha.exists or not observed.dhcpha_owned or dhcpha.lagg_protocol != "failover":
        plan.desired = DesiredState(desired.role, DesiredAttachment.FENCED,
                                    "verified plugin-owned failover LAGG is required")
        plan.warnings.append("device preparation or ownership requires attention")
        return plan

    shared_mac = settings.shared_mac.strip().lower()
    correct = (
        desired.attachment is DesiredAttachment.ATTACHED
        and dhcpha.up and carrier.up
        and dhcpha.lagg_members == (settings.carrier,)
        and dhcpha.mac == shared_mac and carrier.mac == shared_mac
        and (settings.managed_mtu is None or
             dhcpha.mtu == carrier.mtu == settings.managed_mtu)
    )

    def command(device: str, *args: str, reason: str) -> None:
        plan.commands.append(Command(("/sbin/ifconfig", device, *args), reason))

    if correct:
        if not (dhcpha.promiscuous and carrier.promiscuous):
            command(DHCPHA_DEVICE, "promisc", reason="restore reception for the shared Ethernet identity")
        return plan

    # Silence members before detach can restore their saved Ethernet identity.
    if dhcpha.up or dhcpha.lagg_members:
        command(DHCPHA_DEVICE, "down", reason="silence the logical interface and its members")
    for member in dhcpha.lagg_members:
        command(member, "down", reason="verify member silence before detachment")
        command(DHCPHA_DEVICE, "-laggport", member, reason="remove the upstream Layer-2 path")
    if carrier.exists and (carrier.up or desired.attachment is DesiredAttachment.ATTACHED):
        command(settings.carrier, "down", reason="prepare the reserved carrier without transmission")
    if desired.attachment is DesiredAttachment.FENCED:
        return plan

    # Setting the carrier first also makes detach restore the shared identity.
    command(settings.carrier, "ether", shared_mac, reason="install shared identity before attachment")
    if settings.managed_mtu is not None and carrier.mtu != settings.managed_mtu:
        command(settings.carrier, "mtu", str(settings.managed_mtu),
                reason="inherit explicit native interface MTU before attachment")
    # A cloned MAC may not match the NIC's receive filter (e.g. Hyper-V hn).
    # Set this on our LAGG; native LAGG membership propagates it to the carrier.
    command(DHCPHA_DEVICE, "promisc", reason="receive the shared Ethernet identity before activation")
    command(DHCPHA_DEVICE, "laggport", settings.carrier,
            reason="attach only after fresh MASTER and shared-MAC verification")
    # The first member supplies the LAGG MAC and MTU. There is no carrier-up step.
    command(DHCPHA_DEVICE, "up", reason="activate only after both MACs and role are verified")
    if is_virtual_router_mac(shared_mac):
        plan.warnings.append("shared MAC is in a standardized VRRP/CARP virtual-router range")
    return plan


def parse_carp_states(ifconfig_text: str) -> tuple[str, ...]:
    states: list[str] = []
    for line in ifconfig_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("carp: "):
            parts = stripped.split()
            if len(parts) >= 2:
                states.append(parts[1].upper())
    return tuple(states)


def parse_interface_snapshot(name: str, ifconfig_text: str) -> InterfaceSnapshot:
    up = False
    link_up = False
    mac: str | None = None
    lagg_protocol: str | None = None
    mtu: int | None = None
    members: list[str] = []
    exists = False

    for raw in ifconfig_text.splitlines():
        line = raw.rstrip()
        if line.startswith(f"{name}:"):
            exists = True
            mtu_match = re.search(r"\bmtu\s+(\d+)", line)
            if mtu_match:
                mtu = int(mtu_match.group(1))
            match = re.search(r"<([^>]*)>", line)
            flags = set(match.group(1).split(",")) if match else set()
            up = "UP" in flags
        elif exists and line and not line[0].isspace():
            break
        elif exists:
            stripped = line.strip()
            if stripped.startswith("ether "):
                mac = stripped.split()[1].lower()
            elif stripped.startswith("status:"):
                link_up = stripped.split(":", 1)[1].strip().lower() == "active"
            elif stripped.startswith("laggproto "):
                parts = stripped.split()
                if len(parts) >= 2:
                    lagg_protocol = parts[1]
            elif stripped.startswith("laggport:"):
                # Typical FreeBSD form: laggport: ix0 flags=...
                member = stripped.split()[1]
                members.append(member)

    return InterfaceSnapshot(
        name=name,
        exists=exists,
        up=up,
        link_up=link_up,
        mac=mac,
        lagg_protocol=lagg_protocol,
        mtu=mtu,
        lagg_members=tuple(members),
    )
