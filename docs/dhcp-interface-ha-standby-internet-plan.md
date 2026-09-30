# HA DHCP Interface standby Internet access implementation plan

Status: source implementation for experimental 0.2_39, 2026-09-30. Focused local
behavior checks and initial HA-2 path probes pass. Package installation and
native callback qualification are pending at this checkpoint; full two-node
handover qualification remains separate.

Implement the [standby Internet specification](dhcp-interface-ha-standby-internet-spec.md)
as an optional IPv4 path through the active firewall's LAN CARP VIP. Preserve
the existing [controller safety contract](dhcp-interface-ha.md) and
[Settings and logging conventions](dhcp-interface-ha-ui-streamlining-spec.md).
Do not change package publication, signing, source provenance or unrelated
BIND/CI behavior. Preserve existing working-tree changes and use no worktrees.

## 1. Native routing investigation

This gate precedes feature code. Establish the routing mechanism on the exact
target OPNsense build, not only a development checkout.

- [ ] Trace native DHCP gateway discovery, default selection, route inventory,
  routing reconfigure and gateway-alarm paths, including their filter reloads.
- [ ] Prove that a locally configured LAN CARP VIP in BACKUP state is usable
  as a remote next hop: observe the gateway route, LAN neighbor/MAC and actual
  forwarded traffic. Distinguish CARP ownership from address presence and test
  for local/loopback resolution. If this fails, revise the proposed next-hop
  design before implementation.
- [ ] Determine whether a supported native mechanism can exclude the LAN VIP
  candidate on MASTER, including MASTER with no DHCP lease. Prove that gateway
  priorities or monitor status alone cannot re-enable it on MASTER.
- [ ] If native candidate control is unavailable, qualify the smallest runtime
  route adjustment with explicit ownership and readback. Trace every native
  writer that can replace the default and identify a supported event/reconcile
  boundary. Reject a design that alternates competing route writes or has no
  way to keep a stale DHCP default from replacing the standby path.
- [ ] Establish native routing locks/timeouts and interactions with the
  controller's existing lock. Avoid lock inversion and unbounded commands.
- [ ] Verify gateway/interface/source readback and VIP ownership observation on
  FreeBSD, including a stale lease, local VIP acquisition and missing evidence.
- [ ] Check the effect of a LAN gateway on automatic firewall rules, reply-to,
  outbound NAT generation, source-bound services and directly connected routes.
- [ ] Record reboot, controller crash/restart, stop and package-removal cleanup
  behavior. Do not require restoring an obsolete DHCP default snapshot.

Completion evidence: record target core/package versions, supported operations,
the ownership rule, event paths, observed route readback and a disposable
reproduction for native reconfiguration during each role. If the platform
cannot meet the specification without core patches or conflicting route
writers, document the exact limitation before proceeding with route code.

### Source observations at planning time

The available local core checkout describes itself as
`26.1.11-6-g480f191647`. It is older than the plugin's documented initial 26.7
platform and supplies investigation leads, not target qualification:

| Existing source | Relevant boundary |
|---|---|
| Core `src/etc/inc/system.inc`, `system_routing_configure()` | Chooses the native default, reads route inventory and applies routes. |
| Core `src/opnsense/mvc/app/models/OPNsense/Routing/Gateways.php` | Native gateway inventory and default selection; inspect exact target eligibility behavior. |
| Core `src/etc/rc.routing_configure` | Routing reconfigure can reload filters; gateway alarms also invoke the monitor hook. |
| Core `src/etc/rc.newwanip` and native DHCP lifecycle | WAN gateway changes and state handling must coexist with standby cleanup. |
| Core `src/opnsense/service/conf/actions.d/actions_interface.conf` | Existing route list/configure/alarm and gateway observation actions. |

Do not infer a role-aware API from these source names. The first gate must
record the actual supported target behavior.

## 2. Configuration and validation

Depends on the native routing gate.

- [ ] Extend the existing node-local model with disabled-by-default standby
  enablement, logical LAN interface and selected existing IPv4 CARP VIP.
- [ ] Preserve shared XMLRPC sync isolation and existing configuration revision
  checks. An old configuration must retain existing behavior after upgrade.
- [ ] Validate static LAN addressing, connected subnet, native VIP membership,
  distinct local/VIP addresses and exclusions in the specification.
- [ ] Reject unsupported conflicting defaults/multi-WAN before enablement.
  Require native privileges for any operation that actually changes native
  gateway configuration; do not bypass them with plugin-only access.
- [ ] Put the optional controls in Settings using existing form conventions.
  Apply on Save & Apply; do not introduce another setup action or wizard.
- [ ] Preserve retry information when disable/change cleanup fails. Do not
  clear the old selection before it can be safely released.

Completion evidence: settings fixtures demonstrate opt-in, validation, revision
conflicts, local-only storage and upgrade compatibility. Form saves report
configuration and runtime application outcomes separately.

## 3. Controller routing lifecycle

Depends on sections 1 and 2. Extend the existing controller and lock rather
than creating a service or general routing abstraction.

- [ ] Add route/VIP observations at the explicit runtime boundary and derive
  standby eligibility from a fresh complete all-BACKUP inventory.
- [ ] Reconcile the qualified routing primitive idempotently. Check current
  configuration and ownership before mutation and verify kernel readback.
- [ ] Integrate demotion after verified carrier fencing, and promotion cleanup
  before WAN attachment. Recheck role and revision at mutation boundaries.
- [ ] Keep the standby candidate excluded on MASTER even before DHCP succeeds
  and throughout gateway alarms/common ISP outages.
- [ ] Cover maintenance, unknown/mixed states, disablement, stop, identity
  changes, mapping removal and uninstall through the same ownership contract.
- [ ] Preserve an unrelated native/admin route, detect competing edits and
  avoid restoring cached WAN gateway values.
- [ ] Recover from a missed event, interrupted operation or daemon restart
  using the existing periodic loop. Do not promise atomic kernel transitions.
- [ ] Keep ordinary standby routing failure outside CARP service-health
  demotion. Treat an owned standby route that cannot be removed as a promotion
  obstruction, with explicit evidence and bounded retries.

Completion evidence: controller boundary tests demonstrate final route,
attachment safety, cancellation, ownership conflicts and recovery. Record
whether native callbacks can reinstall an invalid default between passes and
how the qualified primitive prevents or bounds that behavior.

## 4. Status and operational guidance

- [ ] Extend existing read-only status with desired/observed path, gateway,
  LAN interface, source address, ownership, reason and evidence age.
- [ ] Show verified selection separately from Internet reachability; retain the
  safe Standby attachment state when optional Internet access is unavailable.
- [ ] Log path changes, conflicts, cleanup failures and recovery using existing
  structured native events and suppression. Do not add a probe daemon.
- [ ] Document policy and NAT on both potential active nodes: specific native
  LAN source addresses, adequate ingress permission and NAT to the DHCP WAN
  interface address. Inspect existing rules before adding any rule.
- [ ] Document DNS/NTP/update source binding, internal destination exclusions,
  role-dependent defaults, maintenance behavior and verified removal.

Completion evidence: existing PHP/UI fixtures cover truthful status and read-only
behavior. The maintainer guide supplies an example setup and recovery sequence
without changing administrator policy automatically.

## 5. Local regression matrix

Use existing tests under `net/dhcp-interface-ha/tests/`. Add durable tests for
distinct externally visible failures and invariants, not every permutation or
the wording of these documents. Temporary native discovery harnesses stay out
of the final implementation diff.

| Scenario | Required evidence |
|---|---|
| Feature absent/disabled and steady MASTER | Baseline route/attachment behavior; no standby route mutations. |
| Verified all-BACKUP with a fenced WAN | LAN VIP selected with correct interface/source; repeat reconcile has no needless mutations. |
| BACKUP becomes MASTER | Owned standby selection gone before attachment; WAN gateway learned natively. |
| MASTER becomes BACKUP | Carrier fenced before LAN path selected. |
| Maintenance, INIT, mixed roles, stale/missing role or VIP evidence | Only verified all-BACKUP can retain the standby path; unsafe evidence withdraws owned selection. |
| MASTER has no lease or common upstream outage | No standby candidate selected; routing loss does not elect another node. |
| Failed install versus failed cleanup | Install failure keeps safe standby eligible; retained owned standby route blocks promotion and can be retried. |
| Stop/restart, disable, interface/VIP change or removal | Owned state released or failure retained truthfully; restart reconciles actual state. |
| Native route replacement or administrator edit | Ownership readback prevents unrelated deletion and stale restoration; no competing write loop. |
| Save/sync/permissions/status | Node-local fields preserved, revisions enforced, unsupported setup rejected, GET remains read-only. |

Existing baseline commands for later source verification:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s net/dhcp-interface-ha/tests -v
node net/dhcp-interface-ha/tests/ui/test_configure_lagg.js
node net/dhcp-interface-ha/tests/ui/test_log.js
git diff --check
```

Run the controller, settings and UI behavior suites, syntax checks and
`git diff --check`. Fixture results supplement the native evidence below;
they do not qualify paired handover.

## 6. Two node native qualification

Local fixtures cannot complete this gate. Perform disruptive role changes,
appliance installation and workflow runs only within explicit authorization.

- [ ] Record exact core/plugin versions, logical interface mappings, LAN
  addresses/VIP, existing routes, gateway settings, firewall/NAT mode and
  service bindings. Retain configuration backups and console recovery access.
- [ ] With HA-1 MASTER and HA-2 BACKUP, verify HA-2 kernel routes, source
  selection, DNS, NTP and an actual HTTPS package/update download. Capture
  forwarding and translation on HA-1; show zero HA-2 ISP-facing WAN frames.
- [ ] Reverse roles through native CARP maintenance; verify cleanup ordering,
  HA-2 WAN default and the same service checks on the now-standby HA-1.
- [ ] Test abrupt loss of MASTER, then return/failback under existing CARP
  policy. Measure role-change-to-route-cleanup and DHCP acquisition intervals;
  record service reconnections and any promotion obstruction.
- [ ] Reboot/restart the standby and exercise DHCP renewal, native interface
  apply, routing reconfigure, gateway alarms and missed CARP events. Observe
  actual kernel routes, not only the GUI gateway marker.
- [ ] Test a common ISP outage, both nodes BACKUP/no VIP owner, and loss of the
  LAN path. No sustained peer/self loop, extra role election or backup WAN
  transmission may result. Native split-brain limitations remain documented.
- [ ] Confirm local management, normal LAN clients, specific internal/VPN
  routes, XMLRPC and pfsync remain correct. Preserve existing state continuity;
  standby-originated connections may reconnect on path changes.
- [ ] Disable the feature on each node and verify native routing restoration.
  Exercise failed cleanup recovery and uninstall only on an authorized target.

Completion evidence: append actual outcomes, captures and measured timings
here. A successful ping alone is insufficient. Do not claim production readiness
or session preservation from fixtures or standby update downloads.

## 7. Delivery and evidence

- [x] Write the proposed specification and implementation plan, and link them
  from the maintainer index and controller design.
- [x] Record the native routing mechanism selected in section 1.
- [x] Implement and complete focused local checks.
- [ ] Complete authorized native two-node qualification and operational docs.
- [ ] Package/release through the existing approved publication system only
  when requested; record actual workflow URL/outcome if a run is authorized.

The maintainer authorized implementation and testing on HA-2. That authorization
includes the local development package and standby configuration on HA-2;
signed publication and changes to HA-1 are outside this increment's test scope.

## 8. Implementation and initial HA 2 evidence

The selected internal assignment is node-local. Neither code nor defaults depend
on the literal `lan` interface, NIC name or subnet. Tests cover a disabled `lan`
and an enabled `opt2` path, and the settings API validates that the selected VIP
belongs to the chosen enabled static IPv4 interface. Existing VLAN assignments
work through their native devices. Legacy settings requests that omit all three
optional fields retain saved standby settings; partial optional sections are
rejected and revisions include configured standby fields.

Native 26.7.3_11 source tracing found no gateway-candidate eligibility hook.
The implementation therefore postprocesses native routing through the plugin's
monitor/newwanip callbacks and the alarm monitor syshook. Actual defaults are
read from `netstat --libxo json -rn -f inet`. A known managed-WAN default is
recreated through the selected VIP with explicit native interface/source;
unrelated defaults are left alone. The current-boot ownership intent is written
before route mutation. Cleanup uses native IPv4 recalculation without monitor
callbacks or filter reload, retaining retry evidence on failure. Source choice
is observed using a UDP socket connect to 192.0.2.1 without an application send.

HA-2 is OPNsense 26.7.3_11 with installed baseline plugin 0.2_38. Its literal
`lan` assignment is disabled. All six native CARP instances were BACKUP and the
managed `wan`/`dhcpha0lagg` had no member, with `hn1` down. Configuration and
initial route/status backups are in the root-private directory
`/root/dhcpha-standby-discovery-ijtwtxpi`.

A temporary host route through home (`opt2`, `vlan0.10`, VIP 10.250.10.1)
resolved the VIP to its CARP MAC and returned Internet ICMP responses. Default
route probing then exposed a FreeBSD integration issue: changing an existing
addressless-WAN default changed its visible gateway/interface but UDP socket
source selection still returned ENETUNREACH. Recreating the default with
`-ifp` and `-ifa` selected the correct native internal source. Readback checks
guard this boundary; a source failure releases the owned path and recalculates
native routes instead of claiming success.

The home HTTPS probe timed out; its existing ingress policy routes traffic via
WireGuard. Using management (`opt1`, `vlan0.5`, source 10.250.5.4, VIP 10.250.5.1)
downloaded the OPNsense repository's actual 26.7 metadata over IPv4 HTTPS and
completed `ntpdate -q` queries against time.cloudflare.com. This qualifies those
requests and source selection, not every external destination or a timed
daemon synchronization. An HTTPS request to 1.1.1.1 timed out on both paths.
No firewall/NAT rules or service bindings were changed to make these probes
pass. Every temporary route was removed and the baseline native default was
restored; the actual configuration file was unchanged by discovery probes.

The existing controller seam now tests route selection with a disabled `lan`,
quiet steady state, native replacement, cleanup before promotion, optional
install failure versus promotion obstruction, unknown/mixed roles, unrelated
route preservation, source failure recovery and a role change during mutation.
The settings seam tests selected-interface/VIP validation and node-local storage.
Native package/callback/install evidence will be appended after testing.
