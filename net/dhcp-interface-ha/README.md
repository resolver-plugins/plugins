# HA DHCP Interface

Experimental `os-dhcp-interface-ha` plugin.

The [release guide](../../docs/dhcp-interface-ha-releases.md) covers the packaging
workflow and signed `os-dhcp-interface-ha-devel` feed for OPNsense 26.7 amd64.

This plugin implements the experimental carrier controller described in
[the design specification](../../docs/dhcp-interface-ha.md). It follows native CARP
on other interfaces and attaches one dedicated Ethernet adapter to
`dhcpha0lagg` only while the local node is eligible and MASTER. The selected
logical interface may be WAN or another IPv4 DHCP client interface. Each node
selects its own logical assignment and carrier: `opt2`/`ix3` on one and
`opt7`/`hn1` on the other is supported by the controller. Both use `dhcpha0lagg`
and the same shared MAC on the same Ethernet segment. The plugin has no peer NIC
discovery or name matching.

The controller sets the configured shared MAC on the **down, detached**
carrier before attachment. It verifies the MAC and topology before bringing
the LAGG up. The owned LAGG automatically uses promiscuous receive mode so
virtual NIC filters accept the shared MAC. The controller verifies inheritance
on the carrier before activation and repairs a cleared filter without detaching
a healthy active connection. No native Promiscuous mode checkbox is required.
Demotion downs the LAGG and members before detachment. Normal
detachment preserves the shared MAC; it does not restore the hardware MAC.
OPNsense retains ownership of DHCP, addressing, routing, NAT and PF.

The UI streamlining increment provides **Settings**, **Diagnostics** and **Log**.
Settings keeps the current state and normal setup controls together. Configure
is folded into Save & Apply, which captures the carrier and performs guarded
native assignment setup when needed;
Diagnostics keeps observed details, guarded recovery, native configuration links
and the downloadable snapshot. The Log tab uses OPNsense's native local log
viewer for operational events and problems.

The [UI streamlining specification](../../docs/dhcp-interface-ha-ui-streamlining-spec.md)
and [implementation plan](../../docs/dhcp-interface-ha-ui-streamlining-plan.md)
record the source contract, verification and remaining native acceptance checks.
The [older repair plan](../../docs/dhcp-interface-ha-ui-plan.md) retains deployment
history. Source implementation does not imply that an appliance was updated.

## Current scope

- Separate shared and node-local MVC settings; XMLRPC syncs only shared settings.
- Virtual device registration with verified runtime ownership.
- Serialized, bounded transitions with configuration/CARP rereads and readback.
- CARP event wakeups and a five-second reconcile service.
- Native CARP service-health integration for local incapacity after fencing.
- Stop inhibits promotion before fencing; a failed stop leaves the service
  running to retry. Start clears the inhibition.
- Removal refuses assigned or unverified devices and preserves the lock inode.
- Settings keeps the shared identity and local assignment/carrier in separate XMLRPC
  scopes while saving them in one local transaction.
- Status reports backend CARP/attachment state, source-labeled HA context and
  computed migration/removal readiness; peer plugin readiness stays unverified.
- Settings offers confirmed local setup and Save & Apply.
- Diagnostics keeps detailed evidence and guarded recovery actions available.
- Log shows native controller/setup events and verified recovery. Repeated daemon
  failures are bounded; inspecting the page does not create a second event history.

This is **not production qualified**. Appliance investigation demonstrated the
native LAGG/MAC ordering and isolated native DHCP acquisition. Cold boot at the
external segment, complete configured DHCP interface gateway/NAT integration, two-node
handoff, and pfsync session continuity still require qualification. VLAN
carriers are rejected. Delayed failback is not implemented: set its value to
zero; native CARP preemption policy remains in effect.

## Restored 0.2_1 baseline

The API-coordinated handoff increment was withdrawn on 2026-09-27. Source and
HA-2 were restored to the pre-handoff 0.2_1 baseline, preserving the UI repair, boot fix and
independent local assignments. The controller follows native CARP without peer
API credentials, a coordination switch or acknowledged release holds. HA-2 now
runs 0.2_16 with verified native assignment teardown recorded in the
[streamlining plan](../../docs/dhcp-interface-ha-ui-streamlining-plan.md#17-verified-native-teardown--ha-2-02_16-2026-09-28).
HA-1 runs 0.2_32 and reset HA-2 has a fresh, disabled/unconfigured 0.2_32 installation; HA-1’s upgrade preserved configuration and verified ACTIVE/MASTER with its DHCP address. See the
[deployment records](../../docs/dhcp-interface-ha-ui-plan.md#ha-1-installation--2026-09-27).

Ping-based handover checks and passive conflict observations were discussed but
are not implemented in this restored baseline. Native paired handover and boot
qualification remain open.

## Initial configuration

This is a fresh-install release. It has no custom schema migrations or import
path from older experimental configurations. The managed logical interface and
carrier are node-local from the outset; new installations select no interface.
Shared XMLRPC synchronization cannot replace the local mapping.

Native XMLRPC rules, NAT and gateways may refer to logical IDs. With differing
IDs, arrange equivalent references independently or align the IDs for synchronized
sections. This plugin does not remap native configuration. Matching the device
name/MAC alone does not establish session-preserving failover.

## Test configuration

Use a dedicated spare adapter on a test segment. Hypervisors must allow its
source MAC to change (Hyper-V: enable MAC spoofing). Choose one fixed shared
unicast MAC for both nodes; a preferred node's dedicated carrier hardware MAC is
valid. Do not dynamically choose whichever node obtains DHCP first.

1. Install matching plugin versions on both nodes. Select **Enable DHCP Interface
   HA** when this save should also activate CARP-managed operation. Choose each node's own logical DHCP interface and
   one shared unicast MAC for both nodes. The carrier is captured from the
   selected native assignment; it has no separate selector.
2. Click **Save & Apply**. Setup may interrupt this interface.
   If Shared interface MAC is empty, the action uses the selected carrier's
   freshly observed usable MAC. It saves the submitted settings while
   disabled, prepares the owned detached device, captures the original carrier
   before migration and moves the assignment using native OPNsense machinery.
   It verifies the resulting mapping, then validates and applies the submitted Enable choice. Finish any pending native assignment edits first and avoid
   concurrent interface editing: native apply consumes a shared pending queue.
   Observed unrelated edits block setup, but the native API does not provide
   atomic isolation from another writer after the final check.
3. If setup reports partial success, keep its saved carrier mapping. Refresh the
   evidence before a confirmed retry; a timeout does not prove the action failed.
   An already migrated interface needs an authoritative saved carrier. If none
   exists, restore its original device in **Interfaces → Assignments** while
   disabled and repeat setup. Native assignment privileges are required.
4. Review any remaining Settings issues. The plugin maintains its owned LAGG,
   attachment and receive filter. User action is reserved for choices or genuine
   conflicts, such as unsupported native addressing, another assignment using
   the carrier, CARP VIP placement, or hypervisor MAC-spoofing permissions.
   Native CARP must be configured on other interfaces.
5. On a node with an existing native XMLRPC destination, the optional sync action
   includes this plugin in future native configuration sync. It does not contact
   the peer or restart services. Shared settings sync; the logical assignment
   and carrier remain local to each node. Configure and inspect both nodes.
6. No second save is needed when Enable was selected during setup. The summary
   distinguishes Active, Waiting for DHCP, Standby, Maintenance and unavailable
   observations. Native DHCP runs on `dhcpha0lagg`; standby intentionally keeps
   its carrier detached. Address observations do not elect the active node.

Settings displays the configured shared MAC, without a separate physical MAC
suggestion. Use the same shared MAC on both nodes. Generate supplies a new value;
Save & Apply stores it. Polling reflects saved runtime state and preserves
unsaved form edits. Diagnostics shows connection details and native settings links.

The **Log** tab displays the native `dhcpinterfaceha/core` stream with normal
Informational events visible by default. It retains native filters and retention.
If native local logging is disabled, the tab reports that condition. Log access
alone does not permit clearing logs, changing settings or controlling interfaces.
The controller records `interface_observed` before reconciliation when observations
change: carrier and LAGG administrative state, native link reports, promiscuous
flags, LAGG members, and carrier IPv4/IPv6/CARP counts. Unreported fields say
`unknown`. This distinguishes a down interface from a missing link or receive
filter; it does not assume why a driver reports no link. The next reconcile
records any resulting state change; unchanged polls and read-only status calls
do not repeat these events. Observations use the existing inventory without
additional probes or interface changes.

Save & Apply opens a scrolling progress panel below the button, using the same
plain-text presentation as OPNsense firmware updates. Actual configuration steps
show WORKING, OK, FAIL or UNKNOWN; failures retain the completed steps and their
reason. A successful save does not imply a working link: the final readiness
check reports controller faults separately. A timeout is UNKNOWN, because the
firewall may still be applying configuration. Use the existing outcome check
before retrying setup. Unchanged saves report that no actions were needed.

Physical link status is checked after activation. A driver may report no link
while its carrier is intentionally down; this does not prevent MASTER attachment
or make a detached BACKUP ineligible. An activated interface with no link reports
a readiness fault while staying up for native link recovery. Link loss alone
does not trigger plugin-requested CARP demotion. CARP role, maintenance, ownership
and configuration safety checks still control attachment and fencing.

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

## Clearing the plugin setup

This behavior is available from revision 0.2_2 and is deployed on both nodes.

If enabled, disable the plugin and choose **Save & Apply** first; verify that the
carrier is detached. Select **None** under **Interface**, then
choose **Save & Apply** again. This clears the managed interface and carrier,
leaves the plugin disabled, and resets failback delay to zero. The shared MAC is
retained. Selecting None alone changes only the form. Native interface
assignments and firewall settings remain under OPNsense's native controls.

## Verification

### Standby Internet access

Revision 0.2_39 adds optional IPv4 Internet access through the active firewall
while the managed WAN stays fenced. Enable **Standby Internet access**, select
an enabled **Standby internal interface** with static IPv4, and select its
existing **Standby gateway CARP VIP**. Save & Apply verifies the selected path.
The interface can be any suitable local assignment, including a VLAN; it does
not need to be named `lan`. Configure each node separately, since these settings
are node-local and excluded from shared XMLRPC synchronization.

Revision 0.2_41 removes an unusable IPv4 interface default left on the original
carrier after native WAN reassignment. Cleanup requires the owned HA device,
committed mapping and an addressless, down, detached carrier. It also runs
before standby access is enabled. Defaults through other interfaces or IP
gateways remain subject to the existing conflict checks.

Both potential active nodes need ingress permission for the peer's native
internal IPv4 address and outbound NAT to their current DHCP WAN address.
Reuse existing adequate rules. Policy routing on the selected network must
allow the desired Internet path; a VPN-directed network may give different
results from a management network. Services bound to the disconnected WAN or
its former address require compatible native source-binding settings.

For synchronized WireGuard instances, select a valid existing CARP VIP in
WireGuard's native CARP dependency setting on both nodes. Verify the tunnel
interfaces are UP on MASTER and DOWN on BACKUP. A dependency referencing a
deleted VIP can leave both copies UP; standby Internet access then allows
them to contact the remote peer using the same WireGuard identity.

On promotion, the controller releases its standby default before attaching
the WAN. On demotion, it fences first, then selects the internal VIP. Native
routing callbacks and the existing reconcile loop maintain this selection.
A conflicting default on another interface is reported rather than replaced.
Disable standby access and verify cleanup before changing its interface/VIP.
Stop, disablement and removal release owned routing state through current
native IPv4 routing recalculation. IPv6 routing and existing firewall/NAT
policy remain native. Diagnostics distinguish a selected route from Internet
reachability; a missing optional path does not elect a different CARP owner.

See the [specification](../../docs/dhcp-interface-ha-standby-internet-spec.md)
and [implementation and qualification record](../../docs/dhcp-interface-ha-standby-internet-plan.md).

### Local checks

```sh
python3 -m compileall -q net/dhcp-interface-ha/src/opnsense/scripts/dhcp_interface_ha
python3 -m unittest discover -s net/dhcp-interface-ha/tests -v
node net/dhcp-interface-ha/tests/ui/test_configure_lagg.js
```

The controller tests simulate kernel side effects and exercise cancellation,
readback failures, fencing failures, reservation/collision checks, ownership,
stop inhibition, local-health recovery, and transition serialization. Hook tests
require PHP CLI and execute the PHP device-preparation callback against the
supported OPNsense command-helper boundary, including failed preparation. The
PHP controller fixtures also require the CLI and SimpleXML extension. The
GitHub workflow installs `php-cli` and `php-xml` before running the suite.
Current local test results and the unexecuted native MVC/browser/appliance gates
belong in the [streamlining plan](../../docs/dhcp-interface-ha-ui-streamlining-plan.md).
Historical results remain in the older repair plan. Unit tests do not prove Volt rendering, package boot
or external first-frame behavior.

Native CARP split brain remains possible during a partition. The plugin adds
no witness or second election. Session preservation requires a stable public
lease and working pfsync. These limitations also require pair testing.
