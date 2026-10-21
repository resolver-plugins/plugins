"""Read the HA safety fields from non-verbose FreeBSD ifconfig output."""
import re


DEVICE_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_.-]{0,14}\Z")
MAC_RE = re.compile(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\Z")
# Match OPNsense interfaces_virtual_patterns(), including dhcpha0lagg's suffix.
VIRTUAL_PATTERNS = {
    "_stf", "_vlan", "_wlan", "bridge", "carp", "enc", "gif", "gre",
    "ipfw", "ipsec", "l2tp", "lagg", "lo", "ng", "ovpnc", "ovpns",
    "pflog", "pfsync", "plip", "ppp", "pppoe", "pptp", "qinq", "tap",
    "tun", "vlan", "vxlan", "wg",
}


def parse_interface_inventory(text):
    """Keep live topology/identity; never treat malformed safety rows as absent.

    ifconfig prints hwaddr when it differs from ether without requiring -v.
    Missing hwaddr has the same ether fallback as legacy_interfaces_details().
    Capabilities and other descriptive rows are not HA authority and are ignored.
    """
    items = {}
    item = None
    for raw in text.splitlines():
        if not raw.strip():
            continue
        if not raw[0].isspace():
            header = re.fullmatch(
                r"([^:\s]+): flags=([0-9a-fA-F]+)(?:<([^>]*)>)? .*?\bmtu (\d+)(?: .*)?", raw,
            )
            if not header:
                raise ValueError("incomplete interface header")
            name, flags_number, flags, mtu = header.groups()
            if (not DEVICE_RE.fullmatch(name) or name in items or int(mtu) <= 0
                    or (int(flags_number, 16) and flags is None)):
                raise ValueError("invalid interface header")
            item = items[name] = {
                "flags": flags.lower().split(",") if flags else [],
                "mtu": mtu,
                "is_physical": not VIRTUAL_PATTERNS.intersection(re.split(r"\d+", name)),
                "macaddr": "00:00:00:00:00:00",
                "ipv4": [], "ipv6": [], "carp": {},
            }
            continue
        if item is None:
            raise ValueError("interface details without a header")
        line = raw.strip()
        parts = line.split()
        key = parts[0]
        if key in ("groups", "status", "laggport", "member", "carp", "vlan"):
            raise ValueError("unrecognized interface safety row")
        if key in ("ether", "hwaddr"):
            if len(parts) != 2 or not MAC_RE.fullmatch(parts[1].lower()):
                raise ValueError("invalid interface MAC")
            field = "macaddr" if key == "ether" else "macaddr_hw"
            item[field] = parts[1].lower()
            if key == "ether":
                item.setdefault("macaddr_hw", item[field])
        elif key in ("inet", "inet6"):
            if len(parts) < 2:
                raise ValueError("incomplete interface address")
            # Preserve presence even for an unfamiliar address. The status
            # reader validates IPv4; carrier reservation rejects any address.
            item["ipv4" if key == "inet" else "ipv6"].append({"ipaddr": parts[1]})
        elif key == "groups:":
            if len(parts) < 2 or any(not DEVICE_RE.fullmatch(g) for g in parts[1:]):
                raise ValueError("invalid interface groups")
            item["groups"] = parts[1:]
        elif key == "status:":
            if len(parts) < 2:
                raise ValueError("incomplete interface link status")
            item["status"] = line.split(":", 1)[1].strip()
        elif key == "laggproto":
            if len(parts) < 2:
                raise ValueError("incomplete LAGG protocol")
            item["laggproto"] = parts[1]
        elif key in ("laggport:", "member:"):
            member = re.fullmatch(r"(?:laggport|member): (\S+) flags=[0-9a-fA-F]+<[^>]*>.*", line)
            if not member or not DEVICE_RE.fullmatch(member[1]):
                raise ValueError("invalid interface membership")
            field = "laggport" if key == "laggport:" else "members"
            members = item.setdefault(field, {})
            if member[1] in members:
                raise ValueError("duplicate interface membership")
            members[member[1]] = {}
        elif key == "carp:":
            carp = re.fullmatch(r"carp: (\S+) vhid (\d+)(?: .*)?", line)
            if not carp or not 1 <= int(carp[2]) <= 255:
                raise ValueError("invalid CARP instance")
            instances = item.setdefault("carp", {})
            if carp[2] in instances:
                raise ValueError("duplicate CARP instance")
            instances[carp[2]] = {"status": carp[1].upper()}
        elif key == "vlan:":
            vlan = re.fullmatch(r"vlan: \d+ .*parent interface: (\S+)", line)
            if not vlan or not (DEVICE_RE.fullmatch(vlan[1]) or vlan[1] == "<none>"):
                raise ValueError("invalid VLAN topology")
            item["vlan"] = {"parent": vlan[1]}
        elif key in ("tunnel", "vxlan"):
            # Their presence alone excludes an adapter from physical carriers.
            item[key] = {"present": True}
    if not items:
        raise ValueError("empty interface inventory")
    return items
