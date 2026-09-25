# os-dhcp-interface-ha experimental controller

This plugin implements the experimental carrier controller described in
[the design specification](../../docs/dhcp-interface-ha.md). It follows native CARP
on other interfaces and attaches one dedicated Ethernet adapter to
`dhcpha0lagg` only while the local node is eligible and MASTER. The selected
logical interface may be WAN or another IPv4 DHCP client interface.

The controller sets the configured shared MAC on the **down, detached**
carrier before attachment. It verifies the MAC and topology before bringing
the LAGG up. Demotion downs the LAGG and members before detachment. Normal
detachment preserves the shared MAC; it does not restore the hardware MAC.
OPNsense retains ownership of DHCP, addressing, routing, NAT and PF.

## Current scope

- Separate shared and node-local MVC settings; XMLRPC syncs only shared settings.
- Virtual device registration with verified runtime ownership.
- Serialized, bounded transitions with configuration/CARP rereads and readback.
- CARP event wakeups and a five-second reconcile service.
- Native CARP service-health integration for local incapacity after fencing.
- Stop inhibits promotion before fencing; a failed stop leaves the service
  running to retry. Start clears the inhibition.
- Removal refuses assigned or unverified devices and preserves the lock inode.

This is **not production qualified**. Appliance investigation demonstrated the
native LAGG/MAC ordering and isolated native DHCP acquisition. Cold boot at the
external segment, complete configured DHCP interface gateway/NAT integration, two-node
handoff, and pfsync session continuity still require qualification. VLAN
carriers are rejected. Delayed failback is not implemented: set its value to
zero; native CARP preemption policy remains in effect.

## Renaming an experimental installation

The older `os-wan-ha-dhcp` package uses a different virtual device and
configuration namespace. Reassign any logical interface away from
`wanha0lagg`, stop the old service, and remove that package before installing
`os-dhcp-interface-ha`. The two packages conflict. The old package's disabled
test settings are not migrated automatically; set the new shared and local
settings explicitly.

## Test configuration

Use a dedicated spare adapter on a test segment. Hypervisors must allow its
source MAC to change (Hyper-V: enable MAC spoofing). Choose one fixed shared
unicast MAC for both nodes; a preferred node's dedicated carrier hardware MAC is
valid. Do not dynamically choose whichever node obtains DHCP first.

1. Install the experimental plugin and select the dedicated local carrier.
   The selected logical interface may keep its original addressing while the
   feature is disabled; enablement requires IPv4 DHCP.
2. Assign the desired logical DHCP interface to `dhcpha0lagg`; clear its native
   spoof MAC, IPv6, hardware overrides and custom media settings. Keep CARP
   VIPs on other interfaces.
3. Configure the same shared MAC on both nodes, set failback delay to zero,
   then enable and save. Saving applies the configuration. Local reservation,
   topology, MAC collision, and native CARP checks also run inside the root
   controller, including after XMLRPC changes.
4. Check the displayed controller result and system logs. Native DHCP must
   run on `dhcpha0lagg`, never directly on the reserved carrier.

Read current status without changing interfaces:

```sh
/usr/local/bin/python3 /usr/local/opnsense/scripts/dhcp_interface_ha/dhcp_interface_ha.py status --from-config
```

Stop/fence or restart the controller:

```sh
service dhcp_interface_ha onestop
service dhcp_interface_ha onestart
```

A failed fence is an error, not a successful stop. Inspect the live LAGG and
member state before further testing. Reassign every logical interface away
from `dhcpha0lagg` before uninstalling. Package upgrades preserve the device;
restart the controller after an upgrade to load the new implementation.

## Verification

```sh
python3 -m compileall -q net/dhcp-interface-ha/src/opnsense/scripts/dhcp_interface_ha
python3 -m unittest discover -s net/dhcp-interface-ha/tests -v
```

The controller tests simulate kernel side effects and exercise cancellation,
readback failures, fencing failures, reservation/collision checks, ownership,
stop inhibition, local-health recovery, and transition serialization. Actual
appliance results and the remaining qualification matrix belong in the design
specification. Unit tests do not prove external first-frame behavior.

Native CARP split brain remains possible during a partition. The plugin adds
no witness or second election. Session preservation requires a stable public
lease and working pfsync. These limitations also require pair testing.
