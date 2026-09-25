# `os-dhcp-interface-ha` design specification and implementation plan

- **Status:** Proposed design; implementation is gated by the prototype tests in this document.
- **Target repository:** `resolver-plugins/plugins`
- **Proposed plugin path:** `net/dhcp-interface-ha/`
- **Package name:** `os-dhcp-interface-ha`
- **Initial platform:** OPNsense 26.7 and later
- **Scope:** high availability for one IPv4 DHCP client interface in an existing active/passive OPNsense CARP cluster. WAN is the default use case, not a required interface role.
- **Implementation scope:** experimental phase 3 adds the reviewed carrier controller to the existing MVC/discovery scaffold using the recorded appliance evidence. Production boot/forwarding/two-node qualification remains open; delayed failback (Gate C) is not implemented in this increment.

## 1. Problem statement

An OPNsense interface using IPv4 DHCP may have an address that cannot be represented by a CARP VIP. The plugin lets an existing CARP pair move one selected DHCP client interface between nodes through **one ordinary shared Ethernet identity**. The logical interface can be `wan`, `lan`, or another assignment; its role does not change the controller's election, attachment, or DHCP behavior.

The initial deployment is an ISP-facing WAN whose ONT binds a lease to the first MAC it sees. That network may provide only one dynamic public IPv4 address and reject the standardized CARP virtual-MAC range (`00:00:5e:00:01:xx`). WAN and ISP examples in the qualification sections describe this deployment, not an interface-role restriction.

The plugin does **not** implement a second HA election protocol and does **not** replace OPNsense DHCP, routing, NAT, gateway monitoring, PF, pfsync, or CARP.

The core design principle is:

> **OPNsense owns HA election and native interface networking. `os-dhcp-interface-ha` owns the exclusive Layer-2 attachment of the selected DHCP client interface to the node OPNsense has elected MASTER.**

## 2. Goals

The plugin MUST:

1. Reuse OPNsense/FreeBSD CARP as the sole HA election authority.
2. Determine attachment ownership using OPNsense's existing global CARP semantics: the node is eligible to attach the selected interface only when all configured active CARP instances are unequivocally `MASTER`.
3. Fail closed: `BACKUP`, `INIT`, mixed CARP state, disabled CARP, unknown state, or invalid plugin state MUST fence the selected carrier.
4. Present the same logical kernel interface name on both nodes so PF/pfsync state references can match.
5. Permit a user-selected Ethernet-capable local carrier regardless of NIC driver (`ix`, `igb`, `igc`, `em`, `hn`, `vtnet`, `vmx`, `re`, VLAN, or future compatible drivers).
6. Present one configurable ordinary unicast shared MAC address to the upstream network from the active node only.
7. Continue to use the native OPNsense interface configuration and native DHCP client behavior.
8. Preserve OPNsense CARP maintenance mode, temporary CARP disable, demotion, advskew, preemption, pfsync, and XMLRPC behavior.
9. Permit configurable delayed failback without preventing emergency takeover if the current MASTER disappears during the delay.
10. Avoid OPNsense core patches.
11. Minimize dependencies on private OPNsense implementation details; prefer documented plugin hooks plus FreeBSD interface/CARP primitives.
12. Integrate with the existing Resolver Plugins package/release infrastructure after that infrastructure is generalized from its current BIND-specific form.
13. Preserve existing TCP/NAT sessions across failover when the ISP reissues the same public IPv4 lease and normal pfsync prerequisites are met.

## 3. Non-goals for v1

The first release will NOT attempt to support:

- IPv6, DHCPv6, DHCPv6-PD, shared DUID/IAID, or tracked-prefix HA.
- PPPoE as the directly managed carrier.
- Tunnels such as WireGuard, OpenVPN/tun, GIF, or GRE as carriers.
- Active/active or intentionally split CARP ownership.
- Multiple simultaneously managed DHCP client interfaces.
- Replacing or reimplementing OPNsense DHCP, gateway creation, routing, NAT, PF, pfsync, or CARP.
- Internet-reachability-based role election.
- Forcing an ISP to retain a DHCP lease when the ISP chooses to issue a different public address.
- OPNsense core modifications.

## 4. Existing OPNsense behavior used as authority

### 4.1 Global MASTER semantics

OPNsense already has a system-wide concept of a CARP MASTER node. Its current `carp_status.php` returns `MASTER` only when CARP instances exist and all observed CARP states are `MASTER`; if any CARP instance is `BACKUP`, the node is treated as `BACKUP`; mixed/other states are not MASTER.

OPNsense's HA configuration synchronization independently uses the same effective rule before master-only synchronization: if any configured CARP instance is not `MASTER`, synchronization exits as a backup node.

The plugin MUST follow this semantic. It MUST NOT select an arbitrary role VHID and MUST NOT implement voting or majority logic.

Relevant upstream code:

- `src/opnsense/scripts/monit/carp_status.php`
- `src/etc/rc.filter_synchronize` (`pre_check_master`)
- `src/etc/devd/carp.conf`
- `src/opnsense/service/conf/actions.d/actions_interface.conf`

### 4.2 Native CARP controls remain authoritative

The plugin MUST follow actual kernel CARP state and therefore naturally honor:

- Persistent CARP maintenance mode.
- Temporary CARP disable.
- CARP demotion.
- advskew priorities.
- Native preemption settings.
- Link/service-induced CARP demotion.

The plugin MUST NOT add a second manual-failover control when OPNsense's existing CARP controls already perform that function.

### 4.3 Native interface configuration remains authoritative

The selected logical OPNsense DHCP client interface remains a normal interface configured in OPNsense. Existing settings remain in the native interface configuration, including where applicable:

- IPv4 type = DHCP.
- DHCP hostname / client-ID behavior.
- MTU and MSS.
- DHCP timing/options supported by OPNsense.
- Block private networks / bogons.
- DNS behavior.
- Gateway creation and monitoring.
- Firewall rules.
- Outbound NAT.
- `rc.newwanip` processing.

The plugin MUST NOT duplicate these settings.

## 5. User-visible configuration surface

The normal configuration surface is intentionally small.

### 5.1 Shared settings

These settings are cluster-wide and SHOULD be eligible for OPNsense XMLRPC synchronization:

1. **Enable DHCP Interface HA**
2. **Managed logical interface**
   - Default: `WAN`.
   - v1 validation requires IPv4 DHCP.
3. **Shared interface MAC**
   - User-entered, imported from an existing WAN spoof MAC, or generated.
   - Generated addresses MUST be locally administered unicast addresses, e.g. `02:xx:xx:xx:xx:xx`, with cryptographically secure random remaining bits.
   - Validation MUST reject multicast, broadcast, all-zero, and otherwise invalid addresses.
   - The UI SHOULD warn for standardized virtual-router ranges such as CARP/VRRP MACs because some access networks reject them.
4. **Failback delay in seconds**
   - Experimental default: 0 seconds. Nonzero values are rejected until the Gate C failback implementation is qualified.
   - Meaning: a recovered preferred node must remain continuously healthy for this period before it may preempt a living MASTER.

### 5.2 Node-local setting

The following setting MUST NOT be XMLRPC-synchronized:

1. **Local carrier**
   - Selected independently on each firewall.
   - Example: `ix0` on a physical node and `hn1` on a Hyper-V node.
   - The UI MUST filter by capability, not driver-name allowlists.
   - v1 candidates are Ethernet and L2 VLAN interfaces that the chosen FreeBSD fencing primitive can safely use.

### 5.3 Settings that MUST NOT be duplicated

The plugin MUST NOT ask users to re-enter:

- CARP VIPs, VHIDs, password, advskew, or MASTER/BACKUP identity.
- pfsync interface or pfsync peer.
- XMLRPC synchronization peer.
- Public IPv4, gateway, subnet, DHCP server, or DNS.
- DHCP hostname/client-ID.
- MTU/MSS.
- ISP VLAN configuration if an existing eligible VLAN interface is used as the carrier.
- NAT or firewall configuration.
- Gateway-monitoring targets.
- Internal reconciliation timing unless a demonstrated requirement appears later.

## 6. Automatic discovery and validation

The plugin SHOULD discover and display, but not duplicate as configuration:

- OPNsense version.
- CARP enabled state.
- Persistent maintenance mode.
- Current CARP demotion.
- Native preemption configuration.
- All configured/active CARP instances and their states.
- Derived global CARP role (`MASTER`, `BACKUP`, mixed/unknown).
- pfsync interface, configured peer, runtime peer/node status, and defer setting.
- XMLRPC synchronization target when present.
- Logical OPNsense interface assignments.
- Selected logical interface IPv4 configuration type.
- Current WAN backing interface during migration.
- Current DHCP address, gateway, and lease/runtime status where available.
- Existing WAN spoof MAC.
- Available compatible local carrier interfaces.
- Carrier media and link state.
- Plugin virtual interface state.
- Effective shared MAC.

Validation MUST clearly distinguish between:

- **Configuration error**: cannot safely enable.
- **Node not currently eligible**: valid configuration but this node is BACKUP or lacks local carrier link.
- **Common upstream outage**: DHCP/gateway/Internet unavailable, but node-local HA eligibility is unchanged.

## 7. HA membership and role discovery

The plugin does not need its own configurable cluster-member list.

For a normal two-node OPNsense HA deployment it can derive useful peer context from:

- pfsync configuration and runtime nodes.
- XMLRPC synchronization target when configured.
- Local OPNsense system identity.

The absence of an XMLRPC target on a secondary is not an error; OPNsense treats that as normal.

v1 is designed and tested for a two-node active/passive CARP pair. Multi-node CARP topologies are outside the initial support contract.

## 8. Stable logical interface abstraction

### 8.1 Requirement

Both nodes MUST expose the same kernel interface name to OPNsense/PF for the selected DHCP interface. This addresses the pfsync/state-continuity problem created when one node uses, for example, `ix0` and the other uses `vlan0.100` or `hn1`.

The desired logical device name is provisionally **`dhcpha0lagg`**.

The suffix is deliberate. OPNsense 26.7 still has legacy paths whose virtual-interface classifier splits device names on digits and compares the resulting tokens against a hard-coded set including `lagg`. A bare `dhcpha0` would therefore be misclassified as physical. Conversely, a name beginning with `lagg` risks colliding with OPNsense's core-managed `^lagg` device family and normal `<laggs>` configuration. `dhcpha0lagg` is intended to satisfy both constraints: it contains a post-digit `lagg` token for virtual classification, but does not start with `lagg`. Prototype Gate A MUST verify this behavior on every supported OPNsense series.

Both nodes ultimately present:

```text
OPNsense logical WAN
        |
      dhcpha0lagg
        |
local carrier (MASTER only)
```

### 8.2 Provisional implementation: single-member LAGG

The leading implementation candidate is a FreeBSD LAGG used as an abstraction/fencing layer:

```text
HA-1: ix0  -> dhcpha0lagg -> OPNsense WAN
HA-2: hn1  -> dhcpha0lagg -> OPNsense WAN
```

On MASTER, the local carrier is inserted as the sole member. On BACKUP, `dhcpha0lagg` remains present but has no physical member.

Single-member failover LAGG is selected for the experimental controller based on the 2026-09-24 appliance results below. Production qualification still requires the remaining Gate A/B checks, including boot observation and external forwarding.

The implementation MUST NOT contain NIC-driver-specific branches such as `if ix ... elif hn ...`.

### 8.3 Supported carrier model

The experimental executor accepts exclusive physical/virtual Ethernet adapters reported as physical by the native inventory. VLAN carrier execution is explicitly rejected until qualified; the following list describes the intended v1 qualification scope.

The plugin SHOULD accept any local interface type proven compatible with the fencing primitive, including physical or virtual Ethernet and eligible L2 VLAN devices. Examples include:

- `ix`, `igb`, `igc`, `em`, `re`.
- Hyper-V `hn`.
- VirtIO `vtnet`.
- VMware `vmx`.
- Other FreeBSD Ethernet drivers.
- Existing VLAN interfaces when FreeBSD permits them as members of the selected abstraction.

Direct PPPoE/tunnel interfaces are excluded from v1.

## 9. Layer-2 fencing invariant

Safety depends on **physical/L2 exclusivity**, not on racing OPNsense DHCP process start/stop behavior.

The fundamental invariant is:

> A node that is not unequivocally global CARP MASTER MUST have no Layer-2 path from `dhcpha0lagg` to its selected carrier.

Consequences:

- A BACKUP may still have a native DHCP process associated with the selected logical interface; this is safe if it has no carrier member and cannot emit frames upstream.
- The plugin does not need to kill DHCP fast enough to guarantee safety.
- Promotion attaches the carrier only after re-validating global MASTER state.
- Demotion fences the carrier **before** cleanup.

### 9.1 Promotion ordering

Promotion sequence for the experimental controller:

1. Acquire the single local transition lock and read configuration, kernel CARP states, and interface inventory again.
2. Require a migrated, enabled IPv4 DHCP client interface; valid exclusive carrier; current-boot device ownership; and unequivocal global MASTER with local media active.
3. If the observed active attachment already matches the desired carrier, MAC on both interfaces, and explicit MTU, leave it alone.
4. Otherwise take the LAGG and its members down, then detach existing members. Verify detachment before preparing the selected carrier.
5. Keep the selected carrier down. Apply the fixed shared MAC directly to it, and inherit an explicitly configured interface MTU if present. Do not copy the empty LAGG's default MTU onto the carrier.
6. Re-read configuration and global CARP role. Verify the carrier is down and has the shared MAC before adding it to the empty LAGG. The LAGG inherits that MAC from its first member.
7. Verify the sole member, both MACs, and explicit MTU; re-read configuration and CARP role immediately before bringing the LAGG up. LAGG startup brings its member up; no standalone carrier-up command is permitted.
8. Verify the effective active attachment. Native OPNsense DHCP remains responsible for lease configuration and supervision.

A configuration change, role loss, command failure, timeout, or failed readback cancels promotion and attempts fencing. A stale command list must never be executed without revalidation. A local lock serializes plugin actions; it cannot make kernel CARP changes or native OPNsense interface actions atomic with userland commands. Any detected race fences and retries on a later event or periodic pass.

### 9.2 Demotion ordering

1. Detect that global CARP state is no longer unequivocally MASTER, the plugin was disabled, the configuration became unsafe, or the service is stopping.
2. Acquire the transition lock.
3. **Fence first:** take the owned LAGG and all its observed members down before removing members. Detaching a live member can restore a different MAC while transmission remains possible.
4. Remove members only after confirming they are down; verify no members remain. A detached, still-reserved carrier stays down.
5. Report the observed result. Failure to establish the fence is an error, never a successful `FENCED` state.

Existing members of a verified plugin-owned LAGG remain subject to fencing even after the selected logical interface is reassigned or a configuration read fails. An unmigrated interface with no owned attachment is left alone. Never claim or mutate an unrelated same-name interface.

## 10. Shared MAC behavior

Only the active node may expose the shared interface MAC to the selected upstream segment.

The shared MAC is a fixed cluster setting, independent of which node becomes MASTER first. It may be the preferred node's existing WAN hardware MAC or a generated ordinary unicast MAC. Both nodes always present that same setting; it is not learned from whichever node first obtains a lease.

FreeBSD `lagg(4)` saves the member address present at attachment and restores it on detach. The controller sets the shared MAC while the carrier is down **before attachment**, so the saved/restored address is the shared MAC. The hardware MAC remains a diagnostic property, not a second persistent database. Normal demotion does not restore the hardware MAC; intentional return of a carrier to unrelated use is a separate, disabled maintenance operation.

The plugin MUST:

- Validate the shared MAC before enablement.
- Offer import of an existing OPNsense WAN spoof MAC during migration.
- Offer secure random locally administered unicast generation.
- Have one source of truth for the shared MAC.
- Prevent conflicting normal OPNsense WAN spoof-MAC configuration after migration, or validate/migrate it so two mechanisms cannot fight each other.

No frame using a different carrier identity is acceptable during controller-owned transitions. First-frame behavior before plugin initialization at boot remains an external qualification gate; runtime tests alone do not prove boot silence.

## 11. Native DHCP behavior

The plugin does not implement DHCP.

The selected logical OPNsense interface remains IPv4 DHCP and continues to use native DHCP behavior. DHCP hostname, client ID, and other supported options remain configured through that interface's normal OPNsense settings.

The plugin MUST NOT encode behavior specific to a single ISP. On the tested build, native dhclient started detached observes the shared MAC after attachment, including its default client identifier. Administrative LAGG down exits dhclient; OPNsense already restarts it with its native ten-second supervisor. The controller does not start a competing DHCP client, copy leases, or change native DHCP options.

### 11.1 Lease continuity

State-preserving failover depends on the upstream DHCP server issuing the same public IPv4 address to the shared client identity.

The plugin can provide the same L2 identity and preserve native OPNsense DHCP settings, but it cannot force a provider to retain an address.

Lease-file/state replication is explicitly deferred until testing demonstrates a concrete requirement. It MUST NOT be added preemptively because doing so would unnecessarily couple the plugin to OPNsense DHCP internals.

## 12. Session preservation and pfsync

Session preservation is a first-class goal.

Required conditions include:

1. pfsync is healthy.
2. PF sees the same selected logical kernel interface name on both nodes.
3. Firewall/NAT configuration is synchronized appropriately.
4. The new MASTER obtains the same public IPv4 address.
5. The shared MAC identity moves to the new active carrier.
6. Failover does not flush states unnecessarily.

When those conditions hold, established TCP/NAT sessions SHOULD survive in the same way normal OPNsense CARP/pfsync HA is intended to preserve them.

The plugin status page SHOULD surface pfsync health and whether `pfsync defer` is enabled, but MUST NOT silently modify that global OPNsense setting.

## 13. Global role logic

The controller MUST use the following fail-closed interpretation:

```text
CARP instances exist AND every current active CARP instance == MASTER
    => eligible MASTER

anything else
    => not attachment owner
```

Examples that MUST fence the selected carrier:

- Any `BACKUP` CARP state.
- Any `INIT` state.
- Mixed MASTER/BACKUP or MASTER/INIT states.
- CARP disabled.
- No usable CARP instances.
- Failure to parse/obtain current state.

This intentionally means v1 requires active/passive CARP. Deliberate active/active/split-VIP deployments are unsupported.

## 14. Local health versus upstream health

The plugin MUST distinguish node-local eligibility from common-path Internet health.

### 14.1 Conditions that MAY make the node ineligible

Examples:

- Configured local carrier no longer exists.
- Local physical/virtual carrier link is down.
- Required `dhcpha0lagg` abstraction is missing or cannot be reconciled.
- Unsafe fencing state is detected.
- Controller cannot establish required local invariants.

### 14.2 Conditions that MUST NOT by themselves trigger HA movement

- DHCP has no lease.
- ISP-assigned gateway is unreachable.
- Public DNS is unreachable.
- External ping fails.
- Provider outage or ONT/PON upstream outage shared by both nodes.

These are common-path failures in a topology where both firewalls share the same switch/ONT/gateway. Failing over cannot repair them and would risk oscillation.

### 14.3 OPNsense service-health integration

Where node-local failure must influence CARP eligibility, the plugin SHOULD use OPNsense's supported CARP service-status facility (`rc.carp_service_status.d`) rather than directly owning CARP election.

A health check must answer only:

> Can this node safely attach the selected DHCP interface if native CARP elects it?

## 15. Failback policy

### 15.1 Required behavior

When the preferred node recovers while a healthy peer is already MASTER:

1. Do not immediately preempt.
2. Require `failback_delay` seconds of continuous local health.
3. After the delay, allow normal native CARP priority/preemption to operate.
4. If the current MASTER disappears during the hold, the recovering node MUST be able to take MASTER immediately; the delay must not create an avoidable outage.
5. Any new local health failure or reboot resets the hold timer.

### 15.2 Leading mechanism: temporary preemption suppression

FreeBSD 15 CARP source makes temporary preemption suppression the leading mechanism:

- In BACKUP state, `net.inet.carp.preempt=1` permits a faster local CARP instance to treat a slower living MASTER as down and preempt it.
- With `net.inet.carp.preempt=0`, that early preemption path is skipped.
- Ordinary MASTER timeout processing remains independent of the preemption check, so loss of advertisements can still promote the BACKUP.

OPNsense 26.7 maps its native "Disable preempt" setting onto the same sysctl at startup. The plugin MUST preserve that administrator baseline:

- If native preemption is disabled, the plugin never enables it; configured failback delay is effectively superseded by the stricter native policy.
- If native preemption is enabled, the plugin may temporarily set runtime preemption to `0` during recovery hold and restore `1` only after the hold expires or the node becomes MASTER.
- Periodic reconciliation must reassert the temporary hold if another OPNsense lifecycle action restores the baseline early.
- Loss/restart of the controller while preemption is suppressed is availability-safe: it may delay automatic failback, but it must not prevent normal MASTER-timeout takeover.

The experimental carrier-controller increment does not change preemption or implement this timer. Its failback-delay default is zero, and nonzero delay blocks enablement/runtime promotion with an explicit diagnostic. Prototype Gate C still MUST validate these source-backed semantics on OPNsense 26.7 and confirm no native maintenance/demotion interaction is broken. Native CARP service-health remains the mechanism for **local managed-interface eligibility failures**, not the preferred failback timer mechanism.

## 16. Eventing and reconciliation

The controller uses two paths with one reconciliation implementation.

### 16.1 Fast path

Use OPNsense's supported CARP plugin event path (`rc.syshook.d/carp`) to request an immediate reconcile after a CARP transition. OPNsense already routes FreeBSD `devd` CARP events into this hook and existing plugins use it.

The hook wakes the running supervised controller for immediate reconciliation. If no controller is running, it attempts fencing instead of leaving an unmonitored active attachment. It never trusts the event payload as proof of current MASTER state.

### 16.2 Safety path

A lightweight controller/service performs local invariant reconciliation approximately every five seconds.

This interval is initially an implementation constant, not a user-visible tuning option.

The periodic path does not ping the Internet and does not conduct election. It only asserts local facts such as:

- current global CARP role;
- carrier attachment/fencing state;
- shared MAC state;
- local carrier existence/link;
- virtual DHCP interface device existence.

### 16.3 Concurrency

All mutations, including device preparation, explicit fencing, and removal, MUST use a single transition lock. The lock is held only for one bounded reconciliation, not for the service lifetime. Child commands have fixed timeouts. A lock timeout is an error and never permission to mutate without the lock. Reconciliation MUST be idempotent so duplicate CARP events, startup calls, manual reconcile actions, and the periodic loop converge to the same state.

## 17. Runtime controller state

Persistent configuration belongs in OPNsense `config.xml` via the plugin model.

Ephemeral state lives under `/var/run/dhcp-interface-ha/` and MUST NOT be XMLRPC-synchronized:

- `device.dhcpha0lagg`: the interface index and a random native interface-group token, recorded only after creation/readback. FreeBSD can reuse an index; ownership requires both the index and the group token. A replacement same-name device is never adopted from its index alone.
- `transition.lock`: the persistent lock inode shared by preparation, transitions, stop and removal.
- `stopped`: inhibits promotion between deliberate stop and explicit start, including periodic retries after a failed fence.
- Native daemon supervisor and child PID files for lifecycle control and event wakeups.

Current observations are read fresh. No cached status, DHCP lease store or failback timer is implemented in phase 3. A future qualified failback implementation must document any additional runtime state.

A reboot must be safe even if runtime state is lost: startup must reconstruct truth from OPNsense configuration plus current FreeBSD interface/CARP state.

## 18. Boot behavior

Boot MUST be fail closed.

Preferred sequence:

1. Plugin/interface registration creates the stable logical interface abstraction without an ISP carrier attached.
2. OPNsense may configure its normal DHCP client interface on that logical device; with no carrier this cannot leak upstream traffic.
3. CARP converges normally.
4. Controller starts/reconciles.
5. Only a node that is unequivocally global MASTER may attach its configured local carrier.

The implementation SHOULD avoid an early boot hook unless Prototype Gate A/B proves it necessary. A detached-by-default virtual interface is preferred because safety then derives from FreeBSD dataplane state rather than hook timing.

## 19. Controller failure behavior

A transient Python/controller restart on the current MASTER SHOULD NOT immediately drop a working dataplane. The kernel interface/member association may remain intact while the controller restarts.

The health hook requests demotion only for the controller’s explicit verified-incapacity result (exit 100). Interpreter/import/startup failures are unknown health and do not request demotion.

The controller MUST be supervised/restarted using the native FreeBSD daemon/rc service mechanism. A deliberate service stop records a stopped marker under the transition lock, fences, and only then terminates supervision. If fencing fails, stop reports failure and leaves the service alive to retry with promotion inhibited. Start clears the stopped marker under the same lock. An enabled, deliberately stopped instance is locally unhealthy only after confirmed fencing; disabled/unmanaged instances do not request native demotion. Health measures local capability independently of the current CARP role so recovered BACKUP nodes can clear demotion. A missed CARP event is repaired by the periodic pass; SIGKILL or a kernel fault cannot promise instantaneous fencing.

The native CARP service-health hook reports local inability only after verifying the local owned attachment is fenced. If fencing itself fails, it logs an error and does not deliberately demote a still-attached node. This cannot rule out independent peer promotion, but avoids intentionally creating that overlap. DHCP/gateway/Internet observations do not enter eligibility.

The five-second reconciliation loop and CARP event fast path use the same idempotent reconciliation logic so a missed event or process restart self-heals.

## 20. Disable, uninstall, and upgrade behavior

### 20.1 Disable

Disabling the plugin MUST fail closed: the managed carrier is fenced rather than leaving both nodes exposed.

### 20.2 Uninstall

If any logical OPNsense interface is still assigned to the plugin-owned `dhcpha0lagg`, uninstalling removes the management layer needed to recreate it. The UI and package lifecycle MUST therefore provide a strong warning/guard:

> Reassign the managed logical interface away from `dhcpha0lagg` before uninstalling `os-dhcp-interface-ha`.

Intentional pre-deinstall rejects remaining logical assignments, stops/fences, and removes only the current-boot verified owned device under the transition lock while the Python runtime is still installed. Failed fencing or an ownership collision fails removal. Post-deinstall does not destroy devices by name or unlink the lock directory. Upgrade skips this removal path.

### 20.3 Upgrade

Package upgrade must preserve configuration and leave the current dataplane stable where possible. Reconciliation after upgrade must reconstruct state rather than assume the previous process survived.

No OPNsense core files may be patched in place.

## 21. UI/API design

### 21.1 Configuration page

Proposed normal fields:

```text
Enable                         [x]
Managed OPNsense interface     [ WAN                         v ]

Local carrier                  [ Intel X520 (ix0)           v ]
                               (local only; not synchronized)

Shared interface MAC           [ 02:xx:xx:xx:xx:xx ] [Generate]
Failback delay                 [ 0 ] seconds (experimental controller)
```

The UI should show the selected interface's current device and spoof MAC during migration and offer safe import/suggestion actions. `WAN` is the default example in the form.

### 21.2 Status page

Status SHOULD include:

- Global CARP role.
- CARP enabled/maintenance/demotion/preemption state.
- Number and alignment of CARP instances.
- Controller state (`ACTIVE`, `STANDBY`, `RECOVERY_HOLD`, `FAULT`, etc.).
- Managed logical interface.
- Local carrier and link state.
- `dhcpha0lagg` existence and carrier/member attachment.
- Shared and effective MAC.
- Native DHCP/public IPv4/gateway status where available.
- pfsync configuration/runtime health and defer status.
- Last transition, reason, and duration.

The BACKUP page must clearly state that a down/fenced managed carrier is intentional rather than simply showing an unexplained interface alarm.

### 21.3 Diagnostics/API

Provide bounded actions for:

- Validate configuration.
- Show discovered HA environment.
- Dry-run reconciliation.
- Reconcile now.
- Show global CARP derivation.
- Show carrier/member/MAC state.
- Show native DHCP/gateway observations.
- Export a diagnostic bundle suitable for issue reports.

Do not add a second manual failover button; use native OPNsense CARP controls.

## 22. Migration workflow

Migration should be wizard-assisted and deliberately reversible.

### 22.1 Preconditions

- Existing two-node OPNsense CARP HA is healthy.
- The managed ISP-facing DHCP WAN has **no CARP VIPs assigned to it**. Native CARP remains authoritative on the cluster's other HA interfaces, but the ISP-facing WAN itself must not emit a CARP virtual MAC.
- pfsync is configured if session preservation is desired.
- Selected managed interface is IPv4 DHCP.
- Each node has a compatible local carrier available.
- Shared L2 ISP segment can see whichever node is active.
- Hypervisors/switches allow the shared MAC to move between ports/vNICs (e.g. Hyper-V MAC spoofing where required).
- Native OPNsense WAN MAC spoofing is cleared; the plugin shared MAC is the only MAC source of truth.
- v1 rejects per-interface WAN hardware-offload overrides and custom media/mediaopt settings because those settings belong to the node-local carrier after migration. Global hardware settings continue to apply to physical interfaces; explicit WAN MTU is handled separately.

### 22.2 Safe deployment outline

1. Install the plugin on both nodes with **Enable DHCP Interface HA off**.
2. Configure each node's local carrier independently.
3. Configure the shared managed-interface, shared-MAC, and failback settings on the preferred configuration source while the plugin remains disabled.
4. Synchronize the **disabled** shared plugin configuration only after both nodes have valid node-local carrier configuration. A peer that has not yet migrated its logical WAN remains safe because the controller treats "managed interface is not assigned to `dhcpha0lagg`" as `UNMANAGED` and performs no carrier mutation.
5. Remove any CARP VIPs from the managed ISP-facing WAN, then validate native CARP/pfsync/global role and carrier compatibility on both nodes.
6. Create/validate `dhcpha0lagg` detached on both nodes.
7. Migrate the BACKUP logical WAN assignment to `dhcpha0lagg`; verify it remains fenced. This should not affect active Internet service.
8. Perform a controlled migration of the MASTER logical WAN assignment to `dhcpha0lagg`. Because the plugin is still disabled and the virtual WAN is intentionally detached, expect a bounded deployment interruption at this point.
   If the selected shared MAC differs from the identity the ISP previously saw, the provider/ONT may retain a CPE/DHCP session and require its normal customer-side reset procedure before the first lease is issued. This is deployment-specific and MUST NOT be automated by the plugin. Subsequent HA failovers keep the same shared MAC and should not look like a client-MAC change upstream.
9. Enable DHCP Interface HA on the MASTER only after its logical WAN is assigned to `dhcpha0lagg` and all local validation passes. The controller may then prepare the shared MAC on the down carrier, attach it, and allow native DHCP to converge.
10. Synchronize/confirm the enabled shared setting to the already-migrated BACKUP and verify that it remains physically fenced.
11. Verify only MASTER emits ISP-facing frames/shared MAC.
12. Perform controlled failover tests before declaring deployment complete.

The UI/model MUST permit shared settings to be saved while disabled even when migration is incomplete, but MUST reject **enablement** until the local node has a valid carrier, a correctly created `dhcpha0lagg`, an enabled IPv4 DHCP logical interface assigned to that device, and no CARP VIP/native spoof-MAC conflict on the selected interface.

Exact wizard automation is implementation-phase work; safety ordering is mandatory.

## 23. Repository and packaging plan

The plugin belongs in `resolver-plugins/plugins` and uses the repository's existing OPNsense plugin architecture rather than a parallel project layout.

The current Resolver Plugins control plane is intentionally BIND-centric. Before production publication of `os-dhcp-interface-ha`, the repository release infrastructure should be generalized to support multiple independently versioned plugins while preserving all existing `os-bind-rp` behavior and provenance checks.

### 23.1 Proposed release model

- `master` remains the CI/control plane.
- Add per-plugin release source branches, e.g.:
  - `release/bind-rp/<series>`
  - `release/dhcp-interface-ha/<series>`
- Generalize BIND-named release metadata/workflow assumptions into per-plugin profiles/manifests where needed.
- Continue using the signed `resolver-plugins/repository` distribution boundary.
- Do not weaken package fingerprints, provenance, or pin checks.
- Keep generalization behavior-preserving for BIND and covered by existing/focused regression tests.

The infrastructure generalization should be its own narrow PR before or independently from the plugin implementation PRs.

## 24. Proposed source layout

Exact paths are provisional but should follow normal OPNsense plugin conventions:

```text
net/dhcp-interface-ha/
├── Makefile
├── pkg-descr
├── src/
│   ├── etc/
│   │   ├── inc/plugins.inc.d/
│   │   │   └── dhcp_interface_ha.inc
│   │   ├── rc.syshook.d/
│   │   │   └── carp/
│   │   │       └── 50-dhcp-interface-ha
│   │   └── rc.carp_service_status.d/
│   │       └── dhcp-interface-ha
│   └── opnsense/
│       ├── mvc/app/
│       │   ├── controllers/OPNsense/DhcpInterfaceHa/
│       │   ├── models/OPNsense/DhcpInterfaceHa/
│       │   └── views/OPNsense/DhcpInterfaceHa/
│       ├── scripts/dhcp_interface_ha/
│       │   └── ...
│       └── service/conf/actions.d/
│           └── actions_dhcp_interface_ha.conf
└── tests/
```

Only add abstractions that directly serve a requirement in this document.

## 25. Prototype gates before architecture freeze

Production dataplane mutation MUST remain gated by focused prototypes. Non-activating package/UI scaffolding and pure decision tests may precede those prototypes when they encode durable requirements, but exploratory mutation code must not be treated as production implementation until the relevant gate passes.

### Recorded appliance evidence — 2026-09-24

OPNsense 26.7.3_11 / FreeBSD 15.1-RELEASE-p3 on a Hyper-V `hn` carrier demonstrated:

- MAC changes while the carrier is down, attachment while down, and LAGG-controlled member up/down.
- Pre-setting the carrier MAC makes attachment inherit and detachment retain the shared identity.
- Ten attach/traffic/down/detach cycles with 11 shared-source frames and zero original-source frames in a guest capture; no kernel capture drops.
- A native DHCP client started on a detached LAGG uses the shared Ethernet MAC, DHCP chaddr, and default Option 61 after attachment.
- In an isolated VNET/epair test, the installed OPNsense DHCP script plus native daemon restart policy applied a lease and reacquired the same address after reattachment.
- Native interface inventory classifies `dhcpha0lagg` as virtual.

These results authorize experimental controller development, not production release. The external Hyper-V test network supplied no DHCP offer during this investigation; the maintainer subsequently confirmed MAC spoofing is enabled. Reboot first-frame observation, external forwarding, physical/VLAN carrier qualification, full configured-WAN gateway/NAT behavior, delayed failback, and real two-node pfsync failover remain unqualified. The maintainer has accepted native CARP's ordinary split-brain failure model; the plugin does not introduce a witness.

### Controller execution evidence — 2026-09-25

On the same HA-2 build, the actual controller executed three promotion/demotion
cycles on spare `hn1`, verified MAC/MTU readback, made no mutations on an
already-correct active pass, preserved the shared MAC while detached, and
passed stop inhibition/resume and BACKUP health recovery. These used an isolated
configuration file and substituted CARP observations; the appliance's real
CARP state and `/conf/config.xml` were unchanged.

The native `daemon`/rc service passed start/status, child SIGKILL/automatic
restart, USR1 wakeup, stop and restart/stop checks. A destroy/recreate test
actually reused interface index 14; the new same-name LAGG was rejected because
it lacked the original group token. Test devices were removed, the service was
stopped, and hn1's original down/MAC/MTU state was restored.

This demonstrates executor and service behavior on FreeBSD, not a two-node
production WAN cutover. External cold-boot first-frame capture, full configured
WAN routing/NAT/DNS, native pair handoff/pfsync and Gate C remain outstanding.

### Gate A — stable virtual interface and hard fencing

The ISP-facing WAN below is the first deployment scenario. The same invariants
apply when another eligible DHCP client interface is selected.

Prove on OPNsense 26.7:

- A plugin-created virtual interface abstraction can have a stable same name on both nodes.
- A single-member LAGG can accept representative physical and virtual Ethernet carriers and, if required, an L2 VLAN carrier.
- Member removal creates a real L2 fence while leaving the logical interface object present.
- Member re-add works repeatedly.
- The shared MAC can be applied deterministically without the member/LAGG MAC rules overwriting it unexpectedly.
- Removing the final LAGG member restores the address saved at attachment; with carrier pre-spoofing this must be the shared MAC, while the carrier remains down.
- An administratively fenced BACKUP carrier still exposes a reliable physical/media-link health signal suitable for local eligibility checks.
- Reboot recreates the abstraction detached by default.
- OPNsense can assign the selected logical interface to the abstraction normally.
- OPNsense classifies `dhcpha0lagg` as virtual rather than physical, and it does not collide with the core-managed `^lagg` device family.
- Normal `interfaces_configure()` boot ordering invokes the plugin device-preparation callback before configuring a logical interface assigned to `dhcpha0lagg`.
- An unset managed-interface MTU does not force the carrier to the empty LAGG's default MTU; an explicitly configured interface MTU can be applied safely to the carrier before attachment.
- While the BACKUP carrier is administratively fenced/down, its physical/media link state remains observable well enough to distinguish local carrier failure from intentional standby fencing.
- No unexpected frames using either the shared MAC or the carrier's hardware MAC escape during attach/detach transitions beyond behavior explicitly accepted by the gate.

If LAGG cannot meet these requirements cleanly without brittle hooks, select another FreeBSD-native abstraction before proceeding. Do not paper over a failed gate with driver-specific code.

### Gate B — native DHCP convergence

Prove for the selected DHCP client interface; ISP packet capture is the first
deployment's upstream check:

- OPNsense can keep the managed logical interface configured as DHCP while the virtual carrier is detached.
- No DHCP frames reach the ISP while fenced.
- Adding the active carrier causes native DHCP to converge automatically, or identify the smallest documented/supported OPNsense reconfigure action required.
- Removing/re-adding the carrier does not require private PHP/core manipulation.
- Existing selected-interface settings continue to apply.
- Packet capture confirms the promoted node sends the configured shared MAC as DHCP `chaddr` and preserves any explicitly configured native OPNsense DHCP client identifier/hostname behavior.
- Determine whether a dhclient started while detached observes the post-attach shared MAC automatically. If not, use the documented `configctl interface reconfigure <logical-interface>` path after shared-MAC installation rather than private DHCP internals.
- Confirm that native interface reconfigure preserves the controller-applied shared MAC. OPNsense 26.7 currently suppresses native MAC replacement for registered device types with `spoofmac=false`, which is how `dhcpha0lagg` is registered.
- Record the per-interface lease database behavior (currently `/var/db/dhclient.leases.<device>`) and confirm that lack of lease-file replication does not break basic failover.

### Gate C — delayed failback

Compare candidate mechanisms and prove:

- A recovered node that native CARP would otherwise preempt from does not displace a living MASTER before `failback_delay` expires; preference remains defined only by native CARP advskew/preemption semantics.
- If the living MASTER fails during the delay, the recovering node takes over promptly.
- Existing administrator preemption settings are preserved.
- Reboot/restart during hold resets/reconstructs safely.

### Gate D — pfsync and session continuity

With both nodes using the same selected logical kernel name:

- Confirm pfsync states reference the compatible interface identity.
- Establish a long-lived TCP flow through MASTER.
- Hard-stop/power-off MASTER.
- Confirm peer takes ownership and receives the same DHCP public IPv4 where the ISP permits it.
- Verify whether the established flow survives.
- Measure the planned-failover timeline from old-MASTER carrier detach to new-MASTER carrier attach and capture the ISP-facing segment for shared-MAC overlap/flapping.
- If measurable overlap is unsafe, test the smallest fixed internal promotion-settle delay needed; do not expose another user tuning knob unless evidence requires one.
- Repeat with pfsync defer off/on and document observed behavior without silently changing the user's setting.

## 26. Failure test matrix

At minimum test:

1. Normal MASTER → BACKUP planned CARP maintenance transition.
2. Hard MASTER power-off.
3. BACKUP reboot.
4. Preferred-node recovery and delayed failback.
5. Current MASTER fails during preferred-node failback hold.
6. Local WAN carrier cable/virtual link failure on MASTER.
7. Common ISP/gateway outage with both local carrier links healthy — MUST NOT flap ownership.
8. ISP recovers while original MASTER is powered off — surviving node must recover DHCP as MASTER.
9. CARP enters mixed state — WAN must fail closed.
10. CARP temporarily disabled through native OPNsense UI.
11. Persistent CARP maintenance mode.
12. Controller process restart on active node — should not cause gratuitous immediate dataplane loss.
13. Controller process absent/stuck long enough to be unhealthy.
14. OPNsense config reload/interface reconfigure.
15. Plugin package upgrade.
16. Plugin disable.
17. Uninstall guard/warning while `WAN` still uses `dhcpha0lagg`.
18. Shared MAC changed intentionally.
19. Wrong/invalid shared MAC rejected.
20. Hyper-V MAC spoofing disabled — validation/diagnostics must make failure understandable.
21. Physical Ethernet carrier on one node and Hyper-V/VirtIO/VMware carrier on peer.
22. Existing VLAN interface as local carrier, if Gate A confirms support.
23. Existing CARP VIP on the managed WAN — enablement MUST be rejected until removed.
24. Native WAN spoof MAC left configured — enablement MUST be rejected.
25. Per-interface hardware override or media/mediaopt settings on managed WAN — v1 MUST reject rather than silently misapply them.
26. Planned failover packet capture confirms bounded/no unsafe shared-MAC overlap.
27. Detach/reattach preserves the shared MAC saved at attachment, with the carrier down before detachment.
28. Interface reconfigure on MASTER and BACKUP cannot accidentally reattach the BACKUP carrier.

## 27. Security and safety invariants

The following are blocking correctness requirements:

1. **Single-owner invariant under a non-partitioned CARP cluster:** the plugin MUST never deliberately attach a node that is not locally an unequivocal global CARP MASTER. As with ordinary two-node CARP, a network partition that causes both nodes to independently enter MASTER cannot be perfectly fenced without an external witness/fencing mechanism; the plugin MUST document this residual split-brain risk rather than claim to eliminate it.
2. **Fail-closed invariant:** local uncertainty never causes attachment.
3. **Fence-first invariant:** demotion detaches L2 before cleanup.
4. **No second election:** plugin follows CARP; it does not override CARP role decisions.
5. **No WAN-health flapping:** common Internet/gateway failure is not an automatic role trigger.
6. **No driver allowlist:** eligibility is capability-based.
7. **No core patching:** no production edits to OPNsense core scripts/PHP.
8. **One MAC source of truth:** no competing native spoof-MAC and plugin shared-MAC configuration.
9. **Node-local carrier isolation:** a carrier selection from one node is never synchronized onto the peer.
10. **Ephemeral-state reconstruction:** loss of `/var/run` state cannot cause dual ownership after reboot.

## 28. Requirement-to-architecture traceability

Repository policy requires every proposed abstraction/boundary/state store/retry/dependency/test to serve a current requirement. The following table records that mapping.

| Element | Current requirement served | Why it exists |
|---|---|---|
| Native CARP as sole election authority | Avoid split-brain between two election systems; preserve OPNsense maintenance/demotion semantics | Reuses an existing proven authority instead of inventing one |
| Global all-MASTER test | Keep all CARP ownership aligned with managed-interface attachment | Matches OPNsense's own master-only behavior |
| `dhcpha0lagg` stable logical interface | pfsync/PF need matching selected-interface identity across heterogeneous NICs | Removes driver/interface-name mismatch from the PF-facing dataplane |
| Provisional single-member LAGG | Need stable logical interface plus reversible hard L2 carrier fence | Minimal FreeBSD-native candidate; prototype-gated |
| Node-local carrier field | Physical nodes may use `ix`, VM nodes `hn`, etc. | Cannot be shared/synchronized safely |
| Shared MAC field | ISP sees one stable ordinary DHCP Ethernet client across nodes | CARP MAC may be rejected; hardware MACs differ |
| Generated private MAC action | Generic deployment where no existing accepted MAC must be cloned | Produces a standards-compliant ordinary unicast identity |
| Failback delay | Avoid gratuitous second outage/MAC move immediately after preferred node recovers | Policy explicitly requested for stable recovery |
| Five-second reconcile loop | Recover from missed events/process restart/manual drift | Safety net, not election/Internet monitoring |
| CARP syshook fast path | Reduce failover latency | Supported OPNsense plugin integration already used by existing plugins |
| Transition lock | Prevent concurrent event/periodic reconciles from racing interface mutations | Required by dual trigger paths |
| `/var/run` ownership, lock, stopped marker and native PID files | Verified device ownership, serialized mutation, inhibited stop and supervised wakeups | Ephemeral and intentionally not synced; no observation cache or phase-3 timer |
| CARP service-health integration | A node with a broken local carrier should not remain preferred MASTER | Uses native demotion instead of custom election |
| Native DHCP dependency | Remain ISP-agnostic and preserve OPNsense gateway/NAT behavior | Avoid duplicate DHCP implementation and private protocol assumptions |
| Prototype Gates A-D | Resolve concrete uncertainties before durable architecture/code | Prevent speculative complexity and brittle implementation |
| Failure test matrix | Protect exclusivity, outage behavior, and state continuity | Each test maps to an externally observable HA invariant |
| No lease replication in v1 | No demonstrated requirement yet | Avoid unnecessary OPNsense-internal coupling |
| No Internet health election | Both nodes share upstream path in target topology; failover cannot repair common outage | Avoid needless complexity/flapping |

## 29. Phased implementation plan

Keep changes reviewable and avoid a large initial plugin PR.

### Phase 0 — repository control-plane generalization

Separate PR:

- Generalize Resolver Plugins metadata/workflows from BIND-only assumptions to per-plugin profiles.
- Preserve `os-bind-rp` behavior exactly.
- Add focused regression coverage for existing BIND packaging/publication contracts.
- Do not mix DHCP Interface HA implementation into this infrastructure PR.

### Phase 1 — prototypes and architecture decision record

No production plugin release yet:

- Execute Gates A, B, and C on OPNsense 26.7.
- Record commands/results and choose the minimal fencing/failback mechanisms.
- Discard exploratory code that does not protect a durable behavior.
- Update this design if the chosen primitive changes.
- Obtain the independent-agent design/plan review required by repository `AGENTS.md` before implementation proceeds.

### Phase 2 — plugin skeleton and read-only discovery

Small PR:

- `net/dhcp-interface-ha` package skeleton.
- MVC model/controller/view.
- Shared/local config split.
- Interface/capability discovery.
- Read-only HA status/validation API.
- Secure private-MAC generator.
- No production carrier mutation yet.

### Phase 3 — dataplane controller

Focused PR:

- `dhcpha0lagg` lifecycle using the Gate A-selected primitive.
- Idempotent reconciliation and transition lock.
- CARP fast-path hook.
- Periodic safety reconciliation.
- Fail-closed fencing.
- Shared MAC application.
- Local health integration.
- Focused tests for pure decision/state logic and command planning.

### Phase 4 — failback and migration

Focused PR:

- Gate C-selected failback implementation.
- Migration/validation workflow for existing DHCP client interface.
- Existing spoof-MAC import/cleanup.
- Disable/uninstall safeguards.
- Status/diagnostics improvements.

### Phase 5 — HA integration qualification

- Gate D and full failure matrix on representative physical + virtual nodes.
- Verify same-IP session preservation.
- Verify common ISP outage does not flap.
- Verify maintenance mode and temporary CARP disable.
- Document external switch/hypervisor requirements.

### Phase 6 — 26.7+ release integration

- Add per-series release metadata/profile for `os-dhcp-interface-ha`.
- Build/package via generalized Resolver control plane.
- Run repository-required independent code review, `code-simplifier`, `test-suite-simplifier`, and documentation-impact review.
- Publish only after blocking correctness/security/compatibility findings are resolved.

## 30. Durable testing strategy

Prefer small pure-function tests for:

- global CARP role reduction;
- configuration validation;
- compatible-carrier filtering;
- MAC generation/validation;
- desired-state calculation;
- failback timer state transitions;
- command planning from observed → desired state.

Use integration tests/harnesses only where they protect a real boundary:

- FreeBSD interface mutation command behavior.
- OPNsense config/model/API integration.
- package lifecycle.
- runtime parsing of representative `ifconfig`/pfsync outputs.

Do not retain exploratory tests merely because they were useful during investigation or increase coverage.

## 31. Acceptance criteria for v1

A release candidate is acceptable only when all of the following are demonstrated:

1. Installs cleanly on supported OPNsense 26.7+ target(s) without core patching.
2. Two heterogeneous nodes can select different local Ethernet-capable carriers.
3. Both nodes expose the same PF-facing selected logical interface name.
4. BACKUP emits no ISP-facing frames/shared MAC through the managed path.
5. Native CARP maintenance/demotion/disable controls continue to govern ownership.
6. Hard MASTER failure transfers carrier ownership automatically.
7. Local carrier failure on MASTER makes the peer eligible through native CARP handling.
8. Common ISP/gateway outage does not cause repeated ownership flapping.
9. Surviving MASTER recovers DHCP when provider connectivity returns even if the original MASTER remains down.
10. Configured failback delay works and does not block emergency takeover during the hold.
11. Native OPNsense DHCP/gateway/NAT behavior continues to work without duplicated DHCP configuration.
12. Shared/local configuration synchronization behaves correctly.
13. Plugin/controller restart does not create dual ownership.
14. Boot is fail closed.
15. Invalid/mixed CARP state is fail closed.
16. Session continuity succeeds when the ISP retains the public lease and normal pfsync prerequisites are satisfied.
17. Diagnostic output is sufficient to identify local carrier incompatibility, MAC-spoofing restrictions, CARP misalignment, and pfsync problems.
18. BIND package/release behavior remains unchanged by Resolver control-plane generalization.

## 32. Open questions intentionally left to prototypes

Only these implementation questions remain intentionally unresolved:

1. Is a single-member LAGG the cleanest stable `dhcpha0lagg` implementation on OPNsense 26.7, including renamed/interface-registration behavior?
2. What exact MAC/member operation ordering guarantees the shared MAC after attach?
3. Does native DHCP automatically reconverge on member/carrier reattachment, or is one documented `configctl` reconfigure action required?
4. Which failback-hold mechanism prevents normal preemption while preserving immediate takeover after loss of the current MASTER?
5. Is explicit lease-state replication necessary for any supported use case after same-MAC/native-DHCP testing? The default answer remains no unless evidence says otherwise.
6. Does an administratively down/detached carrier on supported physical and virtual NICs retain a reliable media-link signal for standby health checks?
7. Is any promotion-settle delay required to prevent unsafe transient same-MAC overlap during planned CARP transitions?

No other speculative subsystem should be introduced until one of these gates proves it necessary.

## 33. Repository process requirements

Implementation work must follow `resolver-plugins/plugins/AGENTS.md`, including:

- Do not open implementation PRs against official OPNsense repositories.
- Keep changes narrow and reviewable.
- Preserve Resolver package/provenance/signing boundaries.
- Independently review the written implementation plan before implementation.
- Independently review executable changes before declaring implementation PRs ready.
- Run the required code-simplifier and test-suite-simplifier review passes.
- Treat correctness, security, data-loss, compatibility, provenance, and public-contract findings as blocking.
- Keep exploratory/process artifacts out of durable source unless they protect a current product/maintainer contract.

## 34. Upstream/reference points for implementers

Review current OPNsense/FreeBSD sources for the target series before coding. Particularly relevant current paths include:

- OPNsense 26.7 build baseline:
  - Python 3.13 (`config/26.7/build.conf: PYTHON=313` in `opnsense/tools`).
- OPNsense core:
  - `src/opnsense/scripts/monit/carp_status.php`
  - `src/etc/rc.filter_synchronize`
  - `src/etc/devd/carp.conf`
  - `src/opnsense/service/conf/actions.d/actions_interface.conf`
  - `src/sbin/carp_service_status`
  - `src/etc/rc.carp_service_status.d/`
  - `src/etc/inc/interfaces.inc`
  - `src/etc/inc/plugins.inc`
- OPNsense plugins examples:
  - CARP-aware syshooks in `net/frr` and `net/mdns-repeater`
- Resolver Plugins:
  - `AGENTS.md`
  - `.resolver-plugins/`
  - `.github/ci/`
  - `.github/workflows/package-release.yml`
- FreeBSD:
  - `carp(4)`
  - `lagg(4)`
  - `ifconfig(8)`
  - `devd(8)`

Pin implementation decisions to OPNsense 26.7 source behavior first, then validate compatibility for later supported releases.