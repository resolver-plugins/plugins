# `os-dhcp-interface-ha` design specification and implementation plan

For the 0.2_9 GUI/setup/logging increment, use the
[UI streamlining specification](dhcp-interface-ha-ui-streamlining-spec.md) and
[implementation plan](dhcp-interface-ha-ui-streamlining-plan.md). They supersede
older manual-setup and UI presentation requirements below, particularly sections
21–22, while preserving this design's controller safety boundaries. Requirements
in the new documents are tracked with implementation and verification evidence
in the linked plan; source changes are not evidence of deployed behavior.

- **Status:** Experimental. The API-coordinated handoff increment was withdrawn and rolled back to 0.2_1 on 2026-09-27; later revisions below retain native CARP-driven handoff. HA-1 and HA-2 run 0.2_26. HA-1 was upgraded from 0.2_8 with configuration unchanged and returned to ACTIVE/MASTER with DHCP address 10.250.100.100. Authenticated browser, enabled boot and paired-network qualification remain outstanding. This is not production qualified.
- **Target repository:** `resolver-plugins/plugins`
- **Plugin path:** `net/dhcp-interface-ha/`
- **Last verified deployment (before this increment):** HA-1 and HA-2 have 0.2_8 with automatic carrier capture and no separate carrier selector. At that earlier verification both were configured and enabled, HA-1 was in operator-selected CARP maintenance and HA-2 was MASTER. HA-2 acquired 10.250.100.100 with automatic promiscuous reception verified. See the [deployment records](dhcp-interface-ha-ui-plan.md#ha-1-installation--2026-09-27).
- **Package name:** `os-dhcp-interface-ha`
- **Initial platform:** OPNsense 26.7 and later
- **Scope:** high availability for one IPv4 DHCP client interface in an existing active/passive OPNsense CARP cluster. WAN is the default use case, not a required interface role.
- **Restored controller baseline:** 0.2_1 provides the CARP-following carrier controller, combined Settings transaction, Settings/Status/Diagnostics page, structured local status/readiness, explicit setup preparation and independent node-local logical/NIC assignments. Peer API credentials and release holds are absent. See the [rollback record](dhcp-interface-ha-ui-plan.md#source-and-ha-2-rollback--2026-09-27).
- **Source 0.2_7:** Configure LAGG captures the selected assignment's original device and saves it through the existing validated Settings transaction before native relinking. It saves the submitted shared MAC and other plugin form settings with enablement off. The updated revision is retained; failed settings save/apply prevents relinking. Native apply failures leave the carrier saved for recovery. The carrier selector is removed; preview detects the current native device and retains a saved carrier for migrated assignments. If a migrated assignment has no saved carrier, restore its original device while disabled and repeat setup. Native assignment privileges and confirmation still apply.
- **Source 0.2_8:** The controller automatically sets promiscuous receive mode on its owned LAGG before attachment, verifies member inheritance before activation, and repairs cleared filters on an otherwise correct active attachment without bringing it down. This addresses Hyper-V receive filtering with cloned MACs. Failed filter verification fences the attachment. The setting is scoped to the runtime LAGG; native configuration is unchanged, and detached members release the inherited filter.
- **Source 0.2_9:** The UI/setup/logging increment adds a guarded server-side Configure action, sender-specific sync selection, structured diagnostic responsibility/resolution and native event logging. Settings, Diagnostics and Log replace the separate Status tab. See the linked plan for source verification and outstanding native acceptance; HA-2 deployment is recorded in section 10 of that plan; HA-2 was STANDBY before and after installation.
- **Source 0.2_15:** Save & Apply is the only Settings mutation action. It invokes guarded native setup when the selected logical interface is not migrated, and ordinary settings save afterward. Initial setup defaults an empty shared MAC to the freshly observed usable MAC of the selected carrier. The separate Configure and Save draft controls are removed.
- **Source 0.2_19:** The native device hook releases the carrier reservation while disabled, allowing Disabled + Save to restore the native assignment and clear local plugin settings in one request. HA-2 removal and the resulting Disabled selection were verified.
- **Source 0.2_18:** Save & Apply accepts the native omitted member list for an empty LAGG, explicitly reconciles after native assignment apply, and honors Enable only after setup verification. Successful saves have no confirmation popup or persistent page notification. Native HA-2 setup completed in one request and reached STANDBY with Enable selected.
- **Source 0.2_16:** Saving Disabled restores the managed logical interface to its saved carrier through the native assignment controller and verifies the committed mapping before clearing plugin settings. Failed or uncertain restoration retains the saved carrier for a safe retry.
- **Next work:** complete the linked increment's browser/native acceptance gates and retain outstanding controller and paired handover qualification. Passive conflict observations and ping checks remain proposals; delayed failback (Gate C) is separate work.

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
4. Present the same kernel interface name (`dhcpha0lagg`) on both nodes so PF/pfsync state references can match.
5. Permit a user-selected Ethernet-capable local carrier regardless of NIC driver (`ix`, `igb`, `igc`, `em`, `hn`, `vtnet`, `vmx`, `re`, VLAN, or future compatible drivers).
6. Present one configurable ordinary unicast shared MAC address to the upstream network from the active node only.
7. Continue to use the native OPNsense interface configuration and native DHCP client behavior.
8. Preserve OPNsense CARP maintenance mode, temporary CARP disable, demotion, advskew, preemption, pfsync, and XMLRPC behavior.
9. Permit configurable delayed failback without preventing emergency takeover if the current MASTER disappears during the delay.
10. Avoid OPNsense core patches.
11. Minimize dependencies on private OPNsense implementation details; prefer documented plugin hooks plus FreeBSD interface/CARP primitives.
12. Integrate with the existing Resolver Plugins package/release infrastructure after that infrastructure is generalized from its current BIND-specific form.
13. Preserve existing TCP/NAT sessions across failover when the ISP reissues the same public IPv4 lease and normal pfsync prerequisites are met.
14. Let an administrator configure, inspect and retire the shared DHCP connection through a coherent OPNsense UI, with explicit local/peer evidence and safe migration steps.
15. Keep a plugin preparation or observation failure from terminating OPNsense boot or rendering the rest of the web UI unusable.

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

1. **Enable HA DHCP Interface**
2. **Shared interface MAC**
   - User-entered, imported from an existing WAN spoof MAC, or generated.
   - Generated addresses MUST be locally administered unicast addresses, e.g. `02:xx:xx:xx:xx:xx`, with cryptographically secure random remaining bits.
   - Validation MUST reject multicast, broadcast, all-zero, and otherwise invalid addresses.
   - The UI SHOULD warn for standardized virtual-router ranges such as CARP/VRRP MACs because some access networks reject them.
3. **Failback delay in seconds**
   - Experimental stored value: 0 seconds. Nonzero values are rejected until the Gate C failback implementation is qualified.
   - The repair UI MUST NOT offer an editable failback-delay field. Show that native CARP preemption currently applies. A pre-existing nonzero value requires an explicit reset to zero when saving; do not silently change it while loading the page.
   - Reintroducing the field requires a qualified backend, documented timing semantics and Gate C evidence in the same change.
   - Meaning: a recovered preferred node must remain continuously healthy for this period before it may preempt a living MASTER.

### 5.2 Node-local settings

The following settings MUST NOT be XMLRPC-synchronized:

1. **Managed logical interface**
   - Select this firewall's existing assignment, such as `wan` or `opt7`.
   - There is no implicit WAN default. An unconfigured node stays unselected
     and cannot attach a carrier, even if it receives enabled shared settings.
   - The selected assignment must use `dhcpha0lagg`, be enabled and use IPv4
     DHCP before enablement. Incomplete disabled drafts remain saveable.
   - Logical identifiers and descriptions may differ between nodes.
2. **Local carrier**
   - Selected independently on each firewall.
   - Example: `ix0` on a physical node and `hn1` on a Hyper-V node.
   - The UI MUST filter by capability, not driver-name allowlists.
   - The repair supports the controller's current Ethernet adapter scope. VLANs remain rejected pending qualification; do not present them as supported choices.

Shared and node-local storage remain separate. Their placement in separate models MUST NOT force two independent UI saves; section 21.7 defines one validated save on the local node.

Both nodes MUST connect their independently selected carriers to the same intended
Ethernet broadcast domain. They MUST use the fixed kernel device `dhcpha0lagg`
and the same configured shared MAC. For example, HA-1 may use
`opt2 → dhcpha0lagg → ix3`, while HA-2 uses `opt7 → dhcpha0lagg → hn1`.
This release manages one connection per cluster. It does not discover, match or
address peer NICs; administrators configure each local mapping explicitly.

Native XMLRPC rules, NAT, gateways and other configuration can reference OPNsense
logical identifiers. The plugin does not rewrite those references for differing
`optN` assignments. Configure equivalent references independently or align IDs
for the native sections that are synchronized. Matching this plugin's device and
MAC does not establish whole-firewall configuration equivalence or pfsync session
continuity; those remain pair qualification requirements.

### 5.2.1 Upgrade from schema 1.0.0

Package 0.2 uses model version 1.1.0. Move `managed_interface` from
`OPNsense/DhcpInterfaceHaShared` to `OPNsense/DhcpInterfaceHaLocal`; the other
fields and XML mounts retain their meanings. XMLRPC still registers only Shared.

- Copy the legacy selection once on each node before serializing Shared without
  that field. Migration MUST work regardless of which model migrates first.
- Preserve an existing local field, including an explicitly empty selection.
  Preserve an unavailable legacy identifier visibly for the administrator to fix.
- A fresh installation has an empty local selection. Runtime and API MUST NOT
  fall back to a legacy shared selection or to WAN.
- Once Local is version 1.1.0, later shared sync, including from an older sender,
  MUST NOT copy a managed-interface value into Local.
- The Settings API uses `dhcphalocal.managed_interface`. A legacy
  `dhcphashared.managed_interface` POST is rejected; refresh open pages after
  upgrade. No stale browser payload may overwrite the node's mapping.
- Upgrade both nodes while the plugin is disabled and verify each local mapping
  before enabling or resuming shared synchronization. Mixed controller versions
  are not a supported pair; there is no reverse schema migration on downgrade.

The local model stores the selection as optional text so removed assignments can
survive migration and remain visible. The API supplies native assignment choices
and validates existence and eligibility before enablement; the root controller
rechecks the local assignment on every transition.

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

v1 targets a two-node active/passive CARP pair; real pair qualification is still outstanding. Multi-node CARP topologies are outside the initial support contract.

Configured pfsync/XMLRPC addresses are discovery hints, not proof of peer plugin installation, matching settings, carrier readiness or exclusive attachment. The repair UI MUST label peer plugin readiness **Not verified from this node** unless actual fresh peer observations establish it. Section 21.4 defines the evidence boundary.

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

The restored 0.2_1 controller follows native CARP and enforces local carrier
ordering. It does not request or acknowledge peer release. A local MASTER event
is not proof that the previous owner's carrier has detached; paired capture is
required to characterize overlap and transition timing. Passive traffic or ping
observations would provide supplementary evidence, not authoritative fencing,
and are not implemented in this baseline.

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
2. PF sees the same kernel interface name (`dhcpha0lagg`) on both nodes.
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

This remains a proposal for the separate delayed-failback feature. The restored
controller preserves native preemption. Keep `failback_delay=0` until Gate C is
qualified; no failback hold is active in this baseline.

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

OPNsense also calls registered device-preparation callbacks for unassigned devices
at the end of `interfaces_configure()`. The plugin enable checkbox therefore does
not bypass this boot integration. Preparation uses the supported `mwexecf()`
helper; a controller failure is logged and returns no prepared device without
terminating PHP. Exercise this callback, including its failure path, before
installing a build even when the plugin will remain disabled.

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

## 21. UI/API contract for the repair

This section is the normative UI/API contract for the repair and its target
qualification. The original scaffold put a long diagnostic table ahead of the
forms, loaded observations once, saved two models independently and exposed an
unimplemented failback control. The local source now contains the R0–R5 repair
shape; each requirement below has an observable check in the
[repair plan](dhcp-interface-ha-ui-plan.md), and source presence alone does not
pass that check.

### 21.1 Navigation and page ownership

Keep the existing **Services → HA DHCP Interface** entry and
`/ui/dhcpinterfaceha` route. Use three native OPNsense tabs in this order:
**Settings**, **Status**, **Diagnostics**, with matching `#settings`, `#status`,
`#diagnostics` anchors. The default is Settings; preserve an explicitly selected
anchor. Reuse the installed framework's forms, tabs, validation and buttons.
Do not introduce a frontend framework or a separate dashboard application.

All tabs identify the local node and managed interface. Settings explains how to
configure this node; Status answers whether this node can carry the connection;
Diagnostics explains the observations behind that answer. Use interface
descriptions with stable identifiers, e.g. `WAN (wan)`, throughout. The same
behavior MUST work for an eligible `lan` or `optN` assignment.

### 21.2 Settings: one connection and one local adapter

Render one form and one **Save & Apply** action. The visible order is:

| Control or display | Scope and required behavior |
|---|---|
| Enable HA DHCP Interface | Shared checkbox. Explain that enabling permits CARP-controlled attachment and disabling disconnects an already-migrated interface. |
| Managed interface | This node only; never XMLRPC-synchronized. Selector of existing logical assignments, showing description and identifier; IDs may differ on the peer. Ineligible choices remain understandable through their reason; do not silently substitute WAN. |
| Local carrier | This node only. Label with local hostname, device, available description and observed media status. State beside the field that it is never XMLRPC-synchronized. |
| Shared MAC | Shared field with adjacent **Generate** and **Use current interface MAC** actions. Display the identity source and scope. |
| Failback policy | Read-only text: native CARP preemption applies; delayed failback is unavailable in this experimental release. |
| Setup checklist | Computed prerequisites, specific next actions and links to native pages, as defined in section 22. |
| Save & Apply | Validates and saves both configuration sections together, then requests convergence. Show progress and the exact outcome. |

Below the interface/carrier fields, show a compact mapping:
`WAN (wan) → DHCP HA device → Local adapter hn1` and the current native assignment.
The generated device name may be shown as supporting detail. The administrator
MUST NOT need to create or edit a native LAGG object manually.

The empty managed-interface choice is **Disabled**. Selecting it clears the
form's interface and carrier selections, unchecks and locks enablement, and
resets failback delay to zero while retaining the shared MAC. **Save & Apply**
persists this reset; the API also normalizes these fields for direct callers.
When the saved configuration is enabled, the request first saves and applies
Disable with the old identity. It then restores the logical interface to its
saved carrier through the native assignment controller and verifies the
committed mapping and empty pending queue before clearing plugin settings. A
failed or unknown fence or restoration retains the mapping for a safe retry.
This removes the plugin setup without deleting the native logical interface or
its firewall rules.

Carrier candidates must use the existing capability/reservation checks. Preserve
a configured carrier in the selector when it is excluded from general native
assignment options by this plugin. A missing or newly ineligible saved carrier
must remain visible with its reason, not become an empty field that will silently
clear the selection. Show why candidates are blocked. Distinguish **allowed to
record during disabled setup** (e.g. the selected interface's current adapter)
from **safe to attach now** (exclusive, migrated, unaddressed carrier).

**Generate** changes only the unsaved MAC field. **Use current interface MAC**
shows the source and exact value before copying: prefer a native spoof MAC when
configured, otherwise the observed MAC of the selected interface's current
backing device. Offer only a usable unicast address. An all-zero MAC from an empty LAGG
means the suggestion is unavailable; disable copying while unavailable or loading
and preserve the configured MAC.

Require explicit selection of this action, never copy on load,
and never choose a different MAC automatically on the peer. Copying a native
spoof MAC does not remove the native setting; the checklist directs the user to
clear that setting before enablement. Refresh the suggestion when the selected
logical interface changes. Never act on stale data from the previously selected
interface. Keep collision validation in both the save path and root controller.

Fields holding unsaved input MUST survive status refreshes and failed requests.
Show which values are shared and which are local without splitting the save
operation. While saved configuration is enabled, changing the managed interface,
carrier or shared MAC requires disabling and successfully fencing first. Enforce
this in the API, including a request that tries to disable and change identity in
one step. After disabling, an identity edit also requires fresh proof that the
plugin-owned path is detached or absent; a prior failed/timed-out fence is not
proof. The disable-only request must remain possible even if current local
readiness is broken.

### 21.3 Status: ownership and connection health are separate observations

Show, in order:

1. Local node, managed logical interface, observation time and freshness.
2. A prominent operational state with one concrete reason and next action.
3. Configured shared MAC and observed carrier/device MACs; intended and observed
   attachment; controller process status; local media link status.
4. The selected interface's current IPv4 address/prefix, native DHCP observations
   and associated IPv4 gateway/monitor status. Label the address **IPv4 address**,
   since a managed interface is not necessarily a public WAN.
5. The HA context and peer evidence described in section 21.4.

Derive the operational state on the backend from the root controller's validated
observation. The browser renders it. Do not create another CARP reducer in the
browser or PHP presentation layer. Report the observed CARP role independently
of whether plugin enablement is off; the current planner's early-return role is
not sufficient for this display.

Evaluate the following rules in order:

| State | Conditions and display |
|---|---|
| **UNKNOWN** | Required config/controller/topology observation failed, timed out or is inconsistent/busy. Show the unavailable source; never infer Disabled or Standby from an empty JSON object. A browser request failure also marks old data stale. |
| **FAULT** | Observed attachment violates eligibility, ownership/member/MAC/MTU verification fails, or an enabled migrated node has invalid local configuration, lost local media, a stopped/missing controller or a recorded stopped marker. A disabled configuration with an observed attached carrier is a fault. |
| **SETUP_INCOMPLETE** | Managed assignment has not migrated, or disabled setup has missing prerequisites, and no unsafe plugin attachment is observed. Show the ordered outstanding steps. |
| **DISABLED** | Saved enable is off, setup is otherwise complete, and the plugin path is verified detached. Explain whether the logical interface remains on the disconnected DHCP HA device. |
| **STANDBY** | Saved enable is on, local readiness is valid, controller is running, CARP is allowed, maintenance is off, all expected CARP instances are present, derived role is BACKUP, and attachment is verified fenced. Say **Standby — local adapter intentionally disconnected**. |
| **FENCED** | Enabled, locally valid and detached, but native maintenance/administrative disable/INIT or another indeterminate CARP state prevents attachment. Give the exact native reason. |
| **ACTIVE** | Enabled and locally valid, controller is running, unequivocal global MASTER, and the owned device has exactly the selected carrier with verified MACs and configured MTU. |
| **PENDING** | Remaining valid case: global MASTER is eligible but the controller has not yet produced a verified attachment. Show **Waiting for controller convergence**; do not call it Active. |

An enabled but unmigrated interface is Setup incomplete, consistent with the
controller's UNMANAGED behavior; it is never a working HA connection. Mixed
MASTER/BACKUP observations remain visible in Diagnostics even though the global
reducer yields BACKUP. Missing expected CARP instances cannot produce Standby.
A missing device during disabled setup is a missing prerequisite; it is not the
same observation as an existing device that fails ownership verification. Process
presence is labeled **Running**, not proof that a stuck loop is healthy.
No new heartbeat/transition-history database is required by this repair.

Connection observations MUST NOT influence election or carrier eligibility:

- Active with no current IPv4 address: **Awaiting DHCP address**.
- Active with an address: show the address; do not claim an Internet check passed.
- Gateway reports down: show **Gateway reports down** alongside the ownership
  state. Do not demote or move the carrier because of this display.
- Gateway monitoring is disabled: show **Not monitored**, not a successful probe.
- Standby: say DHCP acquisition is not required while intentionally disconnected.
  A retained address or old lease is not proof of an active upstream path.
- This repair does not read DHCP lease files or private DHCP state. Report native
  lease details as **Unavailable**; a live address is an address observation,
  not proof of lease expiry, server identity or future validity.
- Unavailable lease duration/server/expiry information is **Unavailable**; do not
  invent lease validity from an address or a lease file's existence.

### 21.4 HA pair evidence and its limits

Display local hostname, observed global CARP role and instance alignment, native
maintenance/preemption/demotion, configured pfsync interface/peer and runtime
observations, XMLRPC target and whether shared plugin settings are selected for
sync. Display configured pfsync and XMLRPC addresses with their sources; they may
be different addresses of the same node. Do not equate them by guessing a name.

The repair MUST show **Peer plugin readiness: Not verified from this node** and
**Shared settings synchronization: Selected/Not selected/Not configured** as
separate facts. Selecting XMLRPC synchronization is not evidence of successful
delivery or a matching remote MAC. A secondary with no outbound XMLRPC target is
normal; explain that it may receive synchronization rather than declaring an
error. A local MASTER observation does not prove the peer is fenced.

Use the existing native HA Settings/Status links for peer inspection. Full pair
qualification includes opening the plugin page on each node and observing the
same kernel device `dhcpha0lagg` and shared MAC, appropriate independent local
logical assignments/carriers on the same segment, and exclusive attachment.
Logical IDs and NIC names need not match; native rules/gateway references still
require independent verification when IDs differ. This is an explicit manual verification requirement.

Automatic remote plugin readiness is outside the 0.2 UI repair's implementation scope.
No new peer credentials, browser cross-origin requests, remote shell calls,
XMLRPC execution methods, polling daemon or second HA protocol may be introduced.
A later automatic verification change must identify an authenticated, bounded
native read-only transport, define freshness and configuration matching, and
pass a two-node test before replacing **Not verified** with a readiness claim.
Native HA version/service reachability alone is insufficient.

The API-coordinated release increment was withdrawn. Peer readiness remains
**Not verified** from this node; inspect the other node through native HA tools.

### 21.5 Diagnostics and actionable readiness

Diagnostics contains selected, structured observations: configured versus live
CARP instance identifiers/states, device ownership, membership, MAC/MTU checks,
controller process/stopped state, and the native DHCP/gateway/pfsync evidence.
Include **Refresh**, **Reconcile now** (explicit POST using saved settings), and
links to native CARP controls and logs. Do not provide an independent failover,
force-attach or raw-command control.

Readiness is a list of records with stable `code`, `scope` (`local`, `peer`,
`connection`), `severity` (`blocker`, `warning`, `info`), `status` (`pass`, `fail`,
`unknown`), setup `stage`, `message`, and a named `action` (or null) from the fixed
native-link/action allowlist. A blocker prevents enablement only when its status
is fail/unknown. Return passing records too so checklist completion is computed
from evidence rather than inferred from missing errors. Do not parse human prose to
choose behavior. Distinguish blockers for enablement from warnings about pfsync,
provider behavior or unavailable peer evidence. Show a disabled draft's missing
steps without reporting that saving the draft failed.

Required checks cover: selected logical assignment; IPv4 DHCP/enabled and IPv6
constraints; exclusive compatible carrier; owned failover device; native spoof
MAC conflict; CARP VIP conflict on the selected interface; matching expected/live
CARP inventory; hardware/media overrides; shared-MAC validity/collision; and
unsupported failback delay. Runtime BACKUP and common upstream failure are not
configuration blockers. Local link failure is operational unavailability, not a
reason to prevent saving otherwise valid settings.

Use these readiness codes consistently across API responses and tests:

| Codes | Fact or stage covered |
|---|---|
| `managed_interface`, `managed_assignment`, `managed_ipv4`, `managed_ipv6` | Existence, correct generated-device assignment, enabled IPv4 DHCP and no unsupported IPv6. |
| `carrier_selection`, `carrier_capability`, `carrier_exclusive` | Local selection, Ethernet capability and configuration/runtime reservation checks. |
| `device_ownership`, `device_topology` | Current-boot ownership and failover LAGG shape, including unwanted members. |
| `shared_mac`, `native_spoof_mac`, `hardware_media`, `failback_policy` | Valid unique identity and incompatible native/unsupported settings. |
| `managed_carp_vips`, `carp_inventory` | No CARP on the managed path and matching expected/live cluster instances. |
| `pfsync_context`, `xmlrpc_selection`, `peer_readiness` | Session/sync context and explicit unavailable remote verification; warnings/information, not automatic enable blockers. |

Return local media/role/controller convergence as operational state reasons,
not additional configuration blockers. `reason_code` names must be stable and
covered by the state-table tests; present `reason` as a localized explanation,
never a control-flow input.

A downloadable **diagnostic snapshot** is the same allowlisted status/readiness
JSON shown by the page plus plugin/platform versions and collection time. Produce
it on explicit user request. Exclude full config.xml, credentials, keys, DHCP
client secrets, arbitrary files and unrelated interface addresses. Explain that
the snapshot contains this node's interface, MAC and HA peer details. No server
bundle archive or log collector is needed. Do not export a browser cache as a
fresh observation. Transition history remains unavailable in this increment;
link to native logs instead of inventing a last-transition time on page load.

### 21.6 API contracts and observation sources

All paths below are under `/api/dhcpinterfaceha`. Reuse native session/ACL/CSRF
handling. GET endpoints MUST NOT save configuration, create devices, start
services, reconcile, modify interfaces or contact the peer. Privileged operations
remain behind fixed configd actions; never expose a caller-supplied command/path.

| Endpoint | Required contract |
|---|---|
| `GET settings/get` | Return both existing form roots `dhcphashared` and `dhcphalocal`, plus a `revision` derived from their canonical persisted values. Preserve native select-field option mapping. |
| `GET status/carriers` | Return `items`, `blocked`, and `managed` preview metadata (logical identifier/description, current device, IPv4 type, native spoof MAC and effective MAC suggestion), including saved unavailable choices and migration-versus-attachment eligibility. Accept optional `interface=<existing-logical-id>` for an unsaved selection; echo its identifier so late replies can be ignored. Preview never persists or reconciles. |
| `POST status/generate_mac` | Return one random locally administered unicast MAC; no configuration/device changes. |
| `GET status/environment` | Return the structured current observation described below. No raw all-interface/config dumps. |
| `POST settings/set` | Receive both complete form roots and `revision`; validate/save together, release the config lock, then invoke apply. Return persistence and apply outcomes separately (section 21.7). |
| `POST service/apply` | Retry convergence of saved settings only. No arbitrary carrier/role/MAC overrides and no configuration save. Also used for **Reconcile now**. |
| `POST service/prepare` | Explicitly prepare/read back the detached owned device during disabled setup. Route through fixed configd/CLI `prepare_setup`, which checks disabled state and absence of attached/foreign topology under the existing root lock before reusing preparation. The ordinary boot `prepare` entry point must still support enabled configurations. No PHP ifconfig code. |

`settings/set` replaces independent `shared/set` and `local/set` writes. Remove
those exposed write routes in this experimental plugin so they cannot bypass
combined validation. Update controllers, form mapping and ACL tests together;
there is no published compatibility promise requiring an unsafe write path.
Keep the Shared and Local XML mounts and shared-only XMLRPC registration intact.

`status/environment` has a stable envelope with these named members:

| Member | Meaning |
|---|---|
| `collected_at`, `result` | UTC observation time and `ok`, `partial` or `unavailable`. Collection time is not the last role transition. |
| `local` | Hostname and installed OPNsense/plugin versions. |
| `managed` | Logical identifier/description, configured native device and current IPv4 type. |
| `controller` | `running` (boolean or null), `stopped` (boolean or null), `state`, stable `reason_code`, human `reason`. |
| `attachment` | `desired`, `actual`, `owned`, selected carrier, observed members/MACs/MTUs; unknown observations use null, not false/empty success. |
| `carp` | Authoritative observed global role, allowed/maintenance/demotion/preemption, expected/live instances and alignment. |
| `connection` | Current IPv4 addresses/prefixes, native DHCP observation with availability/source, associated IPv4 gateway and monitor status. |
| `ha` | Source-labeled pfsync and XMLRPC facts, synchronization selection, peer readiness `unverified` and its reason. |
| `readiness`, `errors` | Readiness records above and per-source bounded error messages. No raw exceptions containing configuration. |
| `removal` | Current logical assignments to `dhcpha0lagg`, verified detached/owned state and a computed package-removal readiness result. This is guidance only; the package guard remains authoritative. |

Use root `status --from-config` as the source of controller/attachment eligibility,
extending its output with structured checks rather than reverse-engineering
`reason` strings. On the PHP side use native inventory, `interface address`,
`OPNsense\Routing\Gateways::getInterfaceGateway(..., 'inet')` and
`interface gateways status` in the same manner as core Interfaces Overview.
Use native Autoconf observations where exposed; do not introduce a DHCP lease
parser solely for this UI. The installed version's shape and field meaning MUST
be verified with fixtures. In particular, native gateway output may say Online
when no monitor is configured; preserve that distinction in the UI.

Read-only observation calls must have explicit finite timeouts. Set a total
server observation deadline of 15 seconds, with individual external observations
bounded to at most 5 seconds and remaining-budget checks before each call. The
root status command must itself finish within 5 seconds, using remaining-budget
timeouts for its subprocesses; a PHP timeout alone cannot enforce this. Use
Backend's supported timeout/connect_timeout arguments and the existing bounded
Python runner; do not merely abandon a browser request while leaving PHP waiting
on default 120-second backend calls. A required source failure makes controller
state Unknown; an optional DHCP/gateway/pfsync failure produces partial data with
that section unavailable. Do not repeat a failed configd connection for every
field. Collect each native source once per response where possible.

### 21.7 Save, validation, apply and failure semantics

The action is **Save & Apply**, because the running controller observes persisted
configuration independently of browser actions. Do not promise that saved settings
remain inactive until a later Apply click.

1. Require POST, normal write permissions and both form roots. Collect any needed
   bounded runtime observations **before** acquiring the native config lock.
   Never invoke configd, pluginctl, root status or any other config-reading child
   while holding `Config::lock()`, including from model validation: a child may
   wait on the same file lock while PHP waits on the child.
2. Acquire `Config::lock()`, then instantiate/reload fresh models. Compare the
   supplied revision against current persisted plugin settings; a mismatch
   returns conflict with no writes. Build both candidates in memory and validate
   field/cross-model rules using the current locked native configuration and the
   previously collected observations. Reject enablement/identity changes if
   needed evidence is unavailable or older than 15 seconds; release the lock
   before requesting new evidence. Kernel state can still race; the root
   controller must always revalidate before mutation. Existing Shared/Local
   validators must not reload an old persisted counterpart or call a backend
   during this transaction. Extract the PHP checks into a small function accepting
   both candidates, current config and observations; retain independent root
   safety validation.
3. Disabled draft saves allow incomplete migration, an empty MAC/carrier and a
   logical interface still using its original addressing. Validate supplied
   values' syntax and reject unsafe identity edits while previously enabled.
   Disable-only saves must work despite unavailable carrier/backend observations;
   their apply result must still report any inability to verify fencing.
4. Enablement requires the local prerequisites in section 22. Current BACKUP role,
   unknown peer readiness, absent DHCP lease or a common gateway outage do not
   prohibit a valid save. Root runtime validation remains mandatory after XMLRPC,
   config reload and every transition; PHP validation does not replace it.
5. After both candidates pass, serialize the separate XML sections and perform
   one native config save/audit revision. Do not write the first section to disk
   before validating the second. Native file-write failure must not be reported
   as saved. Release the config lock before calling configd/apply; otherwise a
   child that reads config can deadlock on the writer's lock. No backend call is
   permitted inside serialization-time validation either.
6. Request the existing root apply operation, then obtain/read back current status.
   A saved request with failed/timed-out apply remains saved: no silent rollback
   against a controller that may already have observed it. Explain this and offer
   **Retry Apply**. After a transport timeout, persistence is **Unknown** until a
   settings read establishes it; do not resubmit automatically.

The JSON response uses `result` (`saved`, `failed`, `conflict`), `saved` (boolean),
`applied` (boolean or null if not attempted/unknown), `validations` keyed by the
existing form field IDs, `error` (safe message or null), and the new `revision`
when saved. Include current status when obtainable. Omit `validations` entirely
on success; the native form helper interprets its presence, even an empty object,
as validation failure. The UI must inspect `result` and `saved` rather than assume
the native callback means a commit succeeded. Validation/conflict responses
make zero persistent changes. A successful save with apply failure uses
`result=saved, saved=true, applied=false`; a timed-out apply whose outcome is not
known uses `applied=null` and explains the uncertainty. HTTP 200 alone is never a
success test.
Device readiness on BACKUP can be a successful apply while intentionally fenced.
`service/apply` returns `applied`, `error` and `status` with the same meanings;
it never claims to save settings. `service/prepare` returns `prepared` (boolean,
or null when the outcome cannot be established), `error` and readback `status`.
Do not report preparation success solely from the command exit code.

A browser disables duplicate submissions and shows progress until completion,
restores controls on every success/error path, renders field errors inline and
request errors visibly, and refreshes status after apply success or failure.
Preserve entered values on failure. A read-only user can inspect the tabs but
cannot save, prepare or reconcile, including by calling endpoints directly.

### 21.8 Refresh and frontend failure behavior

Status refreshes immediately when opened, on **Refresh**, and five seconds after
the previous request completes while Status is visible. Allow at most one status
request in flight per page. Pause when the document is hidden or another tab is
active; refresh upon returning. Diagnostics refresh is explicit; Settings refresh
checks on load, after Save & Apply/Prepare, and on **Recheck setup**. Do not add
independent polling loops to every widget.

Give status AJAX a 20-second timeout, longer than its server deadline. On failure,
keep the last observation only with **Stale — last updated ...** or show
**Unavailable** if no successful observation exists. Never retain a green Active
badge as current. Distinguish a timed-out source, an expired login/HTML response,
a malformed payload and an empty legitimate list. Readiness cannot turn green
because error handling substituted `{}`. Polling never reloads the settings form.

Render returned names/messages as text, use localized labels and native focus/
validation behavior, and do not communicate state by color alone. Runtime and
preview responses are tied to the requested logical identifier; late responses
must not overwrite a newer interface selection.

## 22. Migration workflow

Migration MUST use an ordered, computed setup checklist in Settings. Each step shows its current observation, blocker/warning and next action. Native interface changes use links to the native pages and an explicit return/recheck; automated reassignment, native spoof-MAC removal and remote-node changes are outside this repair. This is a required guided workflow, not a future unspecified wizard.

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

1. Install the plugin on both nodes with **Enable HA DHCP Interface off**.
2. Configure each node's local carrier independently.
3. Select the managed logical interface and carrier independently on each node. Configure the shared MAC and failback settings on the preferred configuration source while the plugin remains disabled.
4. Synchronize the **disabled** shared plugin configuration only after both nodes have valid node-local interface and carrier configuration. A peer that has not yet migrated its logical WAN remains safe because the controller treats "managed interface is not assigned to `dhcpha0lagg`" as `UNMANAGED` and performs no carrier mutation.
5. Remove any CARP VIPs from the managed ISP-facing WAN, then validate native CARP/pfsync/global role and carrier compatibility on both nodes.
6. Create/validate `dhcpha0lagg` detached on both nodes.
7. Migrate the BACKUP logical WAN assignment to `dhcpha0lagg`; verify it remains fenced. This should not affect active Internet service.
8. Perform a controlled migration of the MASTER logical WAN assignment to `dhcpha0lagg`. Because the plugin is still disabled and the virtual WAN is intentionally detached, expect a bounded deployment interruption at this point.
   If the selected shared MAC differs from the identity the ISP previously saw, the provider/ONT may retain a CPE/DHCP session and require its normal customer-side reset procedure before the first lease is issued. This is deployment-specific and MUST NOT be automated by the plugin. Subsequent HA failovers keep the same shared MAC and should not look like a client-MAC change upstream.
9. Enable HA DHCP Interface on the MASTER only after its logical WAN is assigned to `dhcpha0lagg` and all local validation passes. The controller may then prepare the shared MAC on the down carrier, attach it, and allow native DHCP to converge.
10. Synchronize/confirm the enabled shared setting to the already-migrated BACKUP and verify that it remains physically fenced.
11. Verify only MASTER emits ISP-facing frames/shared MAC.
12. Perform controlled failover tests before declaring deployment complete.

The UI/model MUST permit shared settings to be saved while disabled even when migration is incomplete, but MUST reject **enablement** until the local node has a valid carrier, a correctly created `dhcpha0lagg`, an enabled IPv4 DHCP logical interface assigned to that device, and no CARP VIP/native spoof-MAC conflict on the selected interface.

### 22.3 Required checklist and native actions

Use the section 22.2 order to derive these visible stages from current evidence:

| Stage | Completion evidence and next action |
|---|---|
| Record this node's carrier and shared connection | Disabled draft saved; selected local device and shared identity shown. Prompt the administrator to perform local setup on the other node. |
| Inspect native HA | Expected/live CARP membership available, no selected-interface CARP VIP; show pfsync as a separate session-preservation warning. Link to HA settings and VIP settings. |
| Prepare the DHCP HA device | Device exists with verified ownership and failover topology. If absent, offer **Prepare device** while disabled; success leaves zero members and returns readback. If an active or unowned device already exists, report the conflict without detaching/adopting it. |
| Migrate native interface | Selected logical interface uses the generated device, is enabled IPv4 DHCP with incompatible native settings cleared. Show old/current assignment and the exact destination in the instructions. Link to native assignments and that logical interface's settings. |
| Enable and verify locally | Save & Apply enabled settings; show verified Active or intentionally fenced Standby with connection details, rather than completion based on a successful save response. |
| Verify the pair | Explain the checks required on both nodes and link to native HA Status. Peer verification cannot become complete automatically from local state alone. |

Native links for the reviewed 26.7 source are `/interfaces_assign.php`,
`/interfaces.php?if=<encoded-existing-logical-id>`, `/ui/interfaces/overview`,
`/ui/interfaces/vip`, `/ui/core/hasync`, `/ui/core/hasync_status`,
`/ui/routing/configuration`, and `/ui/diagnostics/log/core/system`. Verify these
against the installed series before implementation. Only use identifiers from
native assignments and fixed internal routes; do not accept arbitrary URLs.

Before directing a native reassignment or enabling/disabling an already-migrated
interface, show that this can interrupt connectivity through that interface and
identify it by description/device. Preserve console or another management path
when that interface carries the administrator's access. The plugin cannot prove
which browser path will survive; do not claim it can.

Readiness is derived afresh; do not create a persistent wizard progress flag that
can remain complete after native configuration changes. Page load, preview and
**Recheck setup** are observational. Device preparation is a separately labeled
explicit action. Do not make a GET create the missing device.

### 22.4 Return to ordinary interface operation

Settings MUST also contain **Disable and remove guidance**, not an uninstall
button. Its ordered instructions are:

1. Check the peer and plan which node will retain the upstream connection. Warn
   against reconnecting two ordinary adapters with the shared identity.
2. Save enable off and verify local fencing; coordinate shared XMLRPC disablement
   deliberately because it affects the peer. Failed fencing is a visible blocker
   to treating the interface as safely disconnected.
3. Reassign the logical interface on each affected node through native Assignments
   to the intended ordinary adapter; review native DHCP/MAC settings and apply.
   Show the current mapping; do not guess the original adapter or restore a
   hardware MAC automatically.
4. Recheck that **no** logical assignment uses the plugin device and that the
   plugin-owned path has no member. Only then show that normal package removal
   through native Firmware/Plugins is possible. The package guard remains final
   authority and must also inspect assignments other than the configured target.

Disabling alone leaves a migrated interface disconnected. Removal alone does not
restore native assignments. State both facts beside the guidance. Emergency
console recovery after a boot failure is a separate maintainer procedure, not a
UI button or a way around the normal assignment/fencing guards.

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

The existing implementation follows this layout. Extend these components for
the repair; the companion plan maps exact current files. Boot/start rc hooks and
the supervised service are already present in `src/etc/rc.d/` and
`src/etc/rc.syshook.d/start/`:

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
│   │   │       └── 10-dhcp-interface-ha
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

Production dataplane mutation MUST remain gated by focused prototypes. Local
source/fixture work can precede appliance qualification, but an installed package
can execute boot hooks while disabled. The UI repair's A01 callback compatibility
check is required before test installation; A16 installs and boots the actual
package in a designated disposable environment with console recovery. Passing
isolated Python tests or syntax lint does not replace either gate. Exploratory
mutation code must not be treated as production implementation until its gate
passes.

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

### Boot integration regression — 2026-09-25

HA-2 boot logs on OPNsense 26.7.3_11 recorded a fatal call to the removed
`mwexec()` helper in `dhcp_interface_ha_prepare_device()`, reached from
`interfaces_configure()` during boot. Removing the experimental package restored
boot, SSH and the web UI. The hook now uses `mwexecf()`, and a regression test
executes the actual callback against the supported command-helper boundary for
both successful and failed preparation. The earlier isolated controller/service
tests did not exercise this PHP boot callback; they did not establish that the
installed plugin could boot safely. Full installed-package boot qualification
remains required.

### UI/API repair source verification — 2026-09-25

The local source now contains the combined settings controller, normalized
status/readiness API, one-form Settings/Status/Diagnostics view, guarded setup
preparation, and removal guidance. Python tests invoke the real PHP callback and
the real settings/status controller source with stubbed OPNsense core classes.
Those fixtures cover transaction ordering, stale revisions, disabled drafts,
detached identity edits, attached-path rejection, status-shape failure, address
shape failure and exclusion of a fixture secret. They do not exercise native
model serialization, a real config file/audit record, MVC routing or Volt
rendering.

The [repair-plan verification record](dhcp-interface-ha-ui-plan.md)
contains the exact local checks and versions. A browser-handler smoke test could
not be run: this checkout has no browser test runner, and npm registry access
for jsdom returned HTTP 403. No browser test dependency was retained. No
OPNsense package was installed and no appliance was modified for this
source-verification pass. Native MVC/browser and installed-package
qualification remain open under R6.

### Independent local assignments and installed upgrade — 2026-09-25

Package `os-dhcp-interface-ha-devel-0.2_1` (`product_hash=5765e6e83615`) is
installed on HA-2 / OPNsense 26.7.3_11. Schema 1.1.0 moves the logical assignment
into Local; NIC names and `optN` identifiers may differ between nodes. Upgrade
preserved HA-2's disabled `opt7 → dhcpha0lagg → hn1` mapping and shared MAC.
Native interface configuration matches the pre-upgrade backup.

The Linux suite passes 65 tests, including independent mappings, shared-sync
isolation, missing-local fencing, stale local revision and live identity guards.
Ten native MVC migration cases passed in memory on HA-2, covering both model
orders, existing/empty/unavailable selections, fresh install and subsequent
legacy shared sync. Installed Settings/Status GET handlers and native form parsing
pass; Status returns `ok` with no errors. Native addressless-interface placeholders
are accepted. Native Volt compilation and package checksum checks pass, the
controller is running and fenced, and HTTP remains reachable.

See [repair plan section 9](dhcp-interface-ha-ui-plan.md#9-independent-local-assignments--package-02_1)
for artifact provenance, backup location, commands and verification limits.
The earlier repaired 0.1 package completed an HA-2 reboot in disabled setup;
0.2_1 was not rebooted. Authenticated browser interactions, enabled two-node
handoff and pfsync continuity with differing logical assignments remain unqualified.

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

With both nodes using the same kernel interface name (`dhcpha0lagg`):

- Confirm pfsync states reference the compatible interface identity.
- Establish a long-lived TCP flow through MASTER.
- Hard-stop/power-off MASTER.
- Confirm peer takes ownership and receives the same DHCP public IPv4 where the ISP permits it.
- Verify whether the established flow survives.
- Measure the planned-failover timeline from old-MASTER carrier detach to new-MASTER carrier attach and capture the ISP-facing segment for shared-MAC overlap/flapping.
- Qualify native CARP-driven transitions, including asymmetric hook delays and network partitions. Local role changes or a settling interval do not prove that the old carrier is fenced.
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
| Node-local interface and carrier fields | Nodes may use different `optN` assignments and `ix`/`hn` adapters | Shared XMLRPC settings must never retarget a node |
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
| Combined local settings transaction | The controller polls persisted shared/local values | Prevents applying a half-saved pair of models |
| Settings/Status/Diagnostics and computed checklist | Configure and retire the connection using native interface controls | Makes scope, order, interruption and current readiness explicit |
| Structured state and evidence availability | Distinguish intentional standby, unsafe attachment and failed observation | Prevents a stale role label or empty JSON fallback from claiming health |
| Bounded single-request refresh | Keep the UI responsive when configd or a source fails | Prevents accumulating polls and long-lived PHP workers |
| Explicit unverified peer state | Local configuration cannot prove remote readiness | Avoids inventing cluster health or another peer protocol |
| PHP callback, native MVC and installed boot checks | Known boot failure crossed a boundary missing from the old tests | Verifies real integration before treating a package as deployable |

## 29. Phased implementation plan

Keep changes reviewable. Phases below describe the overall product, not a claim
that the existing skeleton/controller is complete. **The immediate work is the
ordered R0–R6 repair plan**, followed by its actual qualification results. Do not
rebuild earlier phases or generalize release infrastructure as a prerequisite to
local UI repair. The separate [UI repair plan](dhcp-interface-ha-ui-plan.md) owns
step dependencies, file boundaries and A01–A16 checks.

### Phase 0 — repository control-plane generalization

Separate PR:

- Generalize Resolver Plugins metadata/workflows from BIND-only assumptions to per-plugin profiles.
- Preserve `os-bind-rp` behavior exactly.
- Add focused regression coverage for existing BIND packaging/publication contracts.
- Do not mix HA DHCP Interface implementation into this infrastructure PR.

### Phase 1 — prototypes and architecture decision record

No production plugin release yet:

- Execute Gates A, B, and C on OPNsense 26.7.
- Record commands/results and choose the minimal fencing/failback mechanisms.
- Discard exploratory code that does not protect a durable behavior.
- Update this design if the chosen primitive changes.
- Follow the current repository review instructions; do not infer completed qualification or obsolete process requirements from earlier drafts of this specification.

### Phase 2 — plugin skeleton and read-only discovery

The original scaffold was incomplete. The UI/API source is now replaced by the
R0–R5 repair shape; native qualification remains open:

- `net/dhcp-interface-ha` package skeleton.
- MVC model/controller/view.
- Shared/local config split.
- Interface/capability discovery.
- Read-only HA status/validation API.
- Secure private-MAC generator.
- No production carrier mutation yet.

### Phase 3 — dataplane controller

Experimental source exists, with the documented isolated evidence and boot
regression. Preserve and qualify:

- `dhcpha0lagg` lifecycle using the Gate A-selected primitive.
- Idempotent reconciliation and transition lock.
- CARP fast-path hook.
- Periodic safety reconciliation.
- Fail-closed fencing.
- Shared MAC application.
- Local health integration.
- Focused tests for pure decision/state logic and command planning.

### Phase 4A — UI/API and guided migration repair (implemented locally; qualification open)

The source changes for R0–R5 are present. Complete R6 in the companion plan and
close each acceptance item from actual evidence:

- Supported PHP boot callback and regression, followed by real package boot tests.
- One validated local transaction for shared and node-local settings.
- Structured observations and operational states with bounded reads.
- Settings, Status and Diagnostics using native framework components.
- Guided native migration, explicit detached preparation and removal instructions.
- Live status, accurate failure/partial-save reporting and diagnostic snapshot.
- Explicit peer evidence limits and manual verification on both nodes.
- Native MVC/API/browser checks and installed-package boot evidence.

Phase 4A is incomplete until its required checks pass. Report any unexecuted
appliance checks as outstanding instead of claiming the UI or package qualified.

### Phase 4B — delayed failback (separate, after Gate C)

- Qualify the failback mechanism and emergency takeover during a hold.
- Implement the backend and test restart/reboot/admin-preemption interactions.
- Only then expose an editable delay, hold state and timing information in the UI.
- Preserve the current value-zero validation until this work is complete.

### Phase 5 — HA integration qualification

- Gate D and full failure matrix on representative physical + virtual nodes.
- Verify same-IP session preservation.
- Verify common ISP outage does not flap.
- Verify maintenance mode and temporary CARP disable.
- Document external switch/hypervisor requirements.

### Phase 6 — 26.7+ release integration

- Add per-series release metadata/profile for `os-dhcp-interface-ha`.
- Build/package via generalized Resolver control plane.
- Review correctness, supported core integration, test value and documentation against current repository instructions. Do not represent an unavailable review workflow as already run.
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

For the UI repair, the required behaviors and preferred seams are A01–A16 in
[the implementation plan](dhcp-interface-ha-ui-plan.md#5-required-acceptance-scenarios).
Tests must include real PHP callback execution, combined-save persistence/failure
behavior, status failure/freshness behavior, native MVC/Volt rendering and an
installed-package boot check. Source-text assertions, PHP lint and successful
Python controller tests alone cannot establish those contracts.

Do not retain exploratory tests merely because they were useful during investigation or increase coverage.

## 31. Acceptance criteria for v1

A release candidate is acceptable only when all of the following are demonstrated:

1. Installs cleanly on supported OPNsense 26.7+ target(s) without core patching.
2. Two heterogeneous nodes can select different local Ethernet-capable carriers.
3. Both nodes expose the same PF-facing kernel interface name, `dhcpha0lagg`; local OPNsense assignment IDs may differ.
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
19. Settings, Status and Diagnostics satisfy section 21; one save cannot partially persist the shared/local pair.
20. Setup/removal guidance satisfies section 22, including interruption notices, native links, detached preparation and all-assignment removal checks.
21. Healthy standby, missing DHCP, unknown observations, stale data and unverified peer readiness have distinct truthful presentations.
22. Boot completes with the installed package disabled and its device unassigned, with a disabled assigned test interface, and during controlled preparation failure; management access remains available.
23. The actual target MVC renders/forms/API and the documented UI failure cases pass; operational reads are bounded and do not mutate networking.

## 32. Open questions intentionally left to prototypes

Only these implementation questions remain intentionally unresolved:

1. Is a single-member LAGG the cleanest stable `dhcpha0lagg` implementation on OPNsense 26.7, including renamed/interface-registration behavior?
2. What exact MAC/member operation ordering guarantees the shared MAC after attach?
3. Does native DHCP automatically reconverge on member/carrier reattachment, or is one documented `configctl` reconfigure action required?
4. Which failback-hold mechanism prevents normal preemption while preserving immediate takeover after loss of the current MASTER?
5. Is explicit lease-state replication necessary for any supported use case after same-MAC/native-DHCP testing? The default answer remains no unless evidence says otherwise.
6. Does an administratively down/detached carrier on supported physical and virtual NICs retain a reliable media-link signal for standby health checks?
7. What overlap and recovery timing does native CARP-driven handover produce on the supported boot/reconfigure paths? Can non-authoritative ping or passive conflict observations improve diagnostics without introducing another ownership protocol?

The API-coordinated handoff increment was withdrawn on 2026-09-27. Additional
subsystems remain out of scope until evidence and a reviewed design establish
their need.

## 33. Repository process requirements

Read the current repository `AGENTS.md` before implementing. It remains the
source of repository policy; this specification must not invent review skills
or claim that earlier agent-process requirements are still present.

- Do not use worktrees or open PRs against official OPNsense repositories.
- Keep the HA DHCP Interface repair within its plugin, focused workflow/tests
  and relevant documentation; preserve unrelated work and BIND package policy.
- Preserve package provenance, pin/fingerprint checks and approved signing and
  publication boundaries. Infrastructure/publication changes need their own
  maintainer authorization and review.
- Keep durable tests under `net/dhcp-interface-ha/tests/` for plugin behavior;
  CI helper tests belong in `.github/ci/ci-tests/`. Exploratory CI harnesses may
  use ignored `.github/ci-local/` and must not be committed.
- Require executed evidence for correctness, compatibility and runtime safety.
  An unmet or unexecuted blocking check remains visible in the handoff.
- Update maintainer documentation with changed contracts. Do not claim a
  successful appliance test, workflow run or deployment without its real result.

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
