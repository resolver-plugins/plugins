# HA DHCP Interface standby Internet access specification

Status: implementation contract, 2026-09-30. Source implementation and initial
HA-2 path probes are recorded in the [implementation plan](dhcp-interface-ha-standby-internet-plan.md), which
records the work and evidence required to ship it.

“LAN” in this document means a selected internal network, not the logical
assignment named `lan`. The selected assignment may have any native logical
name and may use Ethernet, VLAN or another native CARP-capable interface.
Each node selects its own enabled internal assignment and existing CARP VIP.
There are no hardcoded interface names, subnets, peer addresses or NIC drivers.

This increment adds optional IPv4 Internet access for the standby firewall
through the active firewall over an existing LAN. The [controller design](dhcp-interface-ha.md)
continues to govern CARP election, shared DHCP identity and carrier fencing.
This specification extends its routing integration boundary only for the
explicitly enabled standby path. Native OPNsense retains DHCP, WAN gateway
discovery, routing machinery, firewall rules, NAT and pfsync.

## Problem statement

While BACKUP, the managed DHCP interface has no physical member and its carrier
is down. This correctly prevents a second node from transmitting the shared
identity to the ISP, but also prevents that firewall from using this WAN for
updates, DNS, NTP and other outbound services. Administrators need to maintain
the standby without making it MASTER just to download updates.

A permanent default route through the other node is insufficient: after
takeover the new MASTER must use its own native DHCP WAN, and two nodes must
never deliberately keep default routes pointing at each other.

## Solution

Provide an optional **Standby Internet access** setting. While verified BACKUP,
the firewall uses the existing LAN CARP VIP as its IPv4 default next hop. The
active node forwards and NATs this traffic through the managed DHCP WAN.
Before attaching its WAN on promotion, the new MASTER removes the standby
selection and returns default-gateway selection to native WAN handling.

Use the LAN CARP VIP rather than a permanently configured peer physical address.
The VIP follows the active node and becomes locally owned on promotion. Both
nodes can therefore use the same next-hop value without selecting each other
as permanent gateways. Local role and VIP ownership checks must prevent a
node from deliberately installing a route to its own VIP. This choice needs
native qualification; a VIP is not proof of remote WAN readiness.

The VIP can appear in native interface configuration on both nodes. Here,
ownership means the matching CARP instance is MASTER, not simply that the IPv4
address appears in an interface listing. The first native gate must prove that
a BACKUP can use this configured VIP as a remote next hop over LAN rather than
resolve it through a local or loopback route. If that cannot be demonstrated,
revise the next-hop design before implementation; do not silently substitute
a permanent route through the peer's physical address.

Example addresses are illustrative, not observations of the current appliances:

| Resource | Address |
|---|---|
| HA-1 native LAN address | 192.168.10.2/24 |
| HA-2 native LAN address | 192.168.10.3/24 |
| Existing LAN CARP VIP | 192.168.10.1/24 |

With HA-1 active, the new path is
`HA-2 (192.168.10.3) → LAN VIP (HA-1) → DHCP WAN → Internet`.
After takeover, HA-2 uses its DHCP WAN and HA-1 can use the same VIP on HA-2.

This path depends on a working active firewall and ISP. It provides maintenance
connectivity, not a second Internet connection. Brief interruptions and restarted
standby service connections during role changes are acceptable.

## User stories

1. As a maintainer, I want to download updates on the standby without changing
   CARP ownership, so that I can update the pair in the normal HA sequence.
2. As an administrator, I want standby DNS and NTP to work, so that services
   can resolve names and keep time before takeover.
3. As an administrator, I want to opt in separately on each node, so that an
   existing management route remains usable until I choose to replace it.
4. As an administrator, I want to use an existing LAN and VIP, so that I do not
   need another NIC, Internet subscription or routing appliance.
5. As an administrator, I want the newly active node to use its DHCP WAN, so
   that takeover does not depend on the failed peer.
6. As an administrator, I want the former MASTER to regain standby access,
   so that I can maintain either node after a role reversal.
7. As an administrator, I want disabling or removing the feature to release
   its routing changes, so that native networking remains recoverable.
8. As an administrator, I want existing firewall and NAT policy respected,
   so that enabling maintenance access does not silently broaden permissions.
9. As an administrator, I want routing failures distinguished from WAN fencing
   failures, so that I understand whether the standby is safe and connected.
10. As a maintainer, I want native reconfiguration and restarts to converge to
    the intended route, so that the daemon does not fight the native router.
11. As an administrator, I want a common ISP outage to leave CARP ownership
    unchanged, so that maintenance connectivity does not cause role flapping.
12. As an administrator, I want maintenance mode handled safely, so that a
    demoted node can use the active peer once its observed role is BACKUP.

## Implementation decisions

### Configuration and prerequisites

Add three node-local settings: enable standby Internet access, LAN interface,
and LAN CARP VIP IPv4 address. Default to disabled on fresh installs and upgrades.
These settings must not be copied by the plugin's shared XMLRPC synchronization;
local logical interface IDs may differ. Configure each node deliberately.

The selected LAN must be enabled, have a static native IPv4 address distinct
from the VIP, and reach the VIP on its directly connected subnet. The VIP must
be an existing native IPv4 CARP VIP on that interface, included in the verified
CARP inventory. Reject the managed DHCP interface, its reserved carrier, a
non-CARP alias, a peer physical address, a far gateway, and a VIP/address mismatch.
The first increment supports one LAN path and one existing CARP VIP.

Enabling requires an enabled, configured HA DHCP Interface instance and fresh
local prerequisite observations. Another management default, manual default
route or gateway policy that conflicts with the new selection must be reported
before applying changes. Do not silently replace an unrelated routing setup.
Existing multi-WAN or dynamic-routing configurations require separate
qualification and are outside the initial supported topology.

Save & Apply remains the sole settings mutation. Use existing revision, ACL,
locking and progress conventions. Saving configuration and verifying a route
are separate outcomes. Read-only status and opening the page must not change
routes or configuration.

### Routing authority and lifecycle

Reuse the existing controller, CARP event wakeups and periodic reconciliation.
Target OPNsense 26.7.3_11 has no role-aware gateway candidate hook. Use a narrow
runtime default-route adjustment at native post-routing boundaries: the plugin
monitor/newwanip callbacks and the gateway-alarm monitor syshook. These callbacks
and the existing reconcile loop share the controller's transition lock. Native
routing remains responsible for normal gateway configuration; the feature does
not create an always-enabled internal gateway or change gateway priorities.

Observe the IPv4 default through FreeBSD netstat's libxo JSON. Recreate a native
managed-WAN default as a route through the selected VIP, explicitly selecting
the internal interface and primary IPv4 address. HA-2 probing found that `route
change` retained unusable source selection from its addressless WAN; delete/add
with interface/source readback is required. This has a bounded interval without
a default. Store route ownership intent before mutation so cleanup survives an
interrupted operation. A native/admin default on an unrelated interface is a
conflict; do not replace it.

On cleanup, use current native IPv4 routing recalculation with monitor callbacks
suppressed, avoiding recursion while holding the transition lock. Retain the
ownership record until required cleanup/recalculation completes. Never replay
a saved WAN gateway or alter IPv6 routes.
No second daemon, peer API credentials, routing protocol or OPNsense core patch
is part of this increment.

Native OPNsense owns the active WAN default and learns its gateway from DHCP.
Do not save a DHCP gateway IP as plugin configuration, copy leases, replay a
pre-failover route snapshot, or rewrite gateway configuration on every CARP
event. Native gateway alarms and DHCP renewals must not reinstall a standby
default on MASTER or a stale WAN default on BACKUP. Do not add split-default
routes to evade native default-gateway selection.

Track only resources demonstrably created or selected by this feature. Before
mutation, compare current role, configuration revision, interface mapping, VIP
ownership and route identity. After mutation, read back the actual kernel route
and selected gateway. A successful command or the gateway GUI's active marker
alone is insufficient. Never delete an unrelated route or restore a stale
native default. Conflicting administrator edits stop optional route management
and produce a diagnostic condition.

| Observed condition | Required routing behavior |
|---|---|
| Feature enabled, fresh matching CARP inventory entirely BACKUP, VIP not locally owned, managed WAN verified fenced | Select the LAN VIP as IPv4 default and verify gateway, interface and source address. |
| Promotion to eligible MASTER | Remove any feature-owned standby selection and verify its absence before attaching the WAN; let native DHCP establish the WAN default. |
| MASTER waiting for DHCP or during an ISP outage | Keep the standby candidate excluded; report the missing WAN route separately. Never fall back through the peer. |
| Demotion to verified BACKUP | Fence the WAN first; then select the LAN VIP after fresh role and ownership checks. |
| Maintenance mode with verified all-BACKUP state | Permit the standby path while preserving carrier fencing. Maintenance alone is not proof of BACKUP. |
| Mixed roles, INIT, disabled CARP, missing inventory or unknown VIP ownership | Fence as required by the existing design and withdraw the feature-owned standby selection. |
| Feature disabled, plugin disabled or stopped, mapping removed, interface/VIP changed, package removal | Withdraw the feature-owned standby selection, return control to native routing, and report verified cleanup or failure. |

The existing role reducer treats any observed BACKUP as sufficient to fence the
WAN. The standby route requires the stronger condition that every expected CARP
instance is observed BACKUP; do not change the existing attachment reducer.

Recheck after operations and before attachment. The controller lock serializes
plugin operations; it does not make native CARP, DHCP and routing changes
atomic. A role change can briefly precede route cleanup. Qualification must
measure that interval and show convergence without sustained loops or
self-routing. Do not claim atomic handoff or uninterrupted standby connections.

A standby installation failure leaves the WAN fenced and exposes a connectivity
failure. It must not demote CARP or make an otherwise capable BACKUP ineligible.
A cleanup failure that leaves an owned standby default in place blocks plugin
WAN attachment until cleanup is verified, because attaching with a route to
the now-owned VIP violates the promotion contract. Log this local obstruction
and retry using existing reconciliation; record its takeover impact explicitly.
An unrelated route failure must not tear down an already correct active WAN.

### Firewall policy and application traffic

The active node must permit IPv4 traffic from the other node's native LAN
address and provide outbound NAT to its current DHCP WAN interface address.
No WAN CARP VIP or second ISP lease is required. Inspect existing rules first:
reuse adequate policy and NAT rather than add duplicates. If NAT is missing,
document a narrowly scoped rule in the existing native configuration mode;
do not automatically switch NAT modes or edit administrator rules.

Configure policy on both nodes so either can serve the standby. Prefer a source
alias containing the two native LAN addresses and keep management, local
networks and HA traffic governed by their existing rules. If the interface has
policy routing, ensure the relevant peer Internet traffic leaves through the
active DHCP WAN. Check native reply-to, automatic gateway rules and source NAT
generation when a LAN gateway is introduced. Do not kill all PF/pfsync states
to repair a standby service connection.

The requested use is firewall-originated traffic, but a system default route
also affects other traffic that consults that routing table. It is not a
local-services-only policy mechanism. Qualification must show that existing
LAN forwarding, VPN/static routes, CARP, pfsync and XMLRPC paths remain correct;
the feature must not create broader forwarding permissions.

Unbound/BIND forwarding, system DNS, NTP and update tools must have usable
destinations and source-address choices. A service forced to bind to the
detached WAN or stale public address is not automatically repaired by adding
a LAN default. Document compatible native service settings and demonstrate
source selection. Do not copy DNS/NTP configuration, restart unrelated services
or add a proxy as part of this feature.

### Observations and recovery

Keep the existing attachment state authoritative. The compact Settings summary
may say **Standby · Internet via LAN** only after route/interface/source
readback. This means a verified path selection, not proven Internet reachability.
Show **Standby · Internet path unavailable** or **Internet path unknown** when
the corresponding evidence fails; do not turn safe standby into an attachment
fault solely because the Internet path is absent.

Diagnostics report feature enablement, selected LAN/VIP, observed default,
egress interface, source address, observation age, native/feature ownership and
the reason a path is selected or withdrawn. Reachability, if observed, is a
separate field with a target and timestamp. It never controls CARP or WAN
attachment. Do not introduce continuous Internet probes in this increment.

Extend the existing structured status and native event log with path selection,
withdrawal, conflict, cleanup failure and recovery. Suppress unchanged failures
using existing logging rules. Stop/remove must retain enough configuration and
ownership evidence to retry failed cleanup; a restart must reobserve rather
than trust a saved success flag. A daemon crash can leave runtime state behind;
startup reconciliation and promotion preflight must handle it.

## Testing decisions

Use the existing controller boundary as the primary seam: observed configuration,
CARP/VIP state, route inventory, native routing side effects and final readback.
Extend the host fixture to model route effects and competing native changes.
Test observable outcomes and transition ordering rather than exact private
helper calls. Keep pure policy tests only for distinct eligibility invariants
that are difficult to isolate at the boundary.

Reuse existing PHP settings/status fixtures and UI checks for node-local
configuration, privileges, revision conflicts and truthful summaries. No new
test framework is needed. The plan supplies the transition matrix and native
acceptance gates. Local fixtures cannot qualify source selection, native
routing races, NAT, DNS/NTP, first-frame fencing or two-node takeover.

## Out of scope

IPv6; general multi-WAN integration; standby Internet independent of the active
node; a dedicated transit-network product flow; proxy configuration; automatic
firewall/NAT mode conversion; automatic DNS/NTP configuration; active/active
routing; an additional HA election, witness or peer API; ISP-outage-driven role
changes; release publication or appliance deployment as part of writing this
specification.

## Further notes

The following primary references establish native behavior, not proof that the
proposed role-aware integration works on the target appliances:

- OPNsense has one system default per IP family, and gateway priority,
  eligibility and the actual kernel route must be distinguished. [Gateways](https://docs.opnsense.org/manual/gateways.html).
- Firewall-originated traffic uses the system default; a gateway group on a
  LAN ingress rule does not supply that default. [Multi WAN](https://docs.opnsense.org/manual/how-tos/multiwan.html).
- Native outbound NAT supports automatic, hybrid and manual configuration;
  choose rules according to the existing mode. [Network Address Translation](https://docs.opnsense.org/manual/nat.html).
- Native route status and traceroute expose the selected path. [Routes](https://docs.opnsense.org/manual/routes.html).

The exact native route integration, cleanup behavior and service source
selection remain qualification gates. The LAN VIP choice is a proposed design
decision; these references do not certify it for our DHCP carrier topology.
