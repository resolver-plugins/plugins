# HA DHCP Interface maintainer guide

`os-dhcp-interface-ha` is an experimental OPNsense 26.7 / amd64 plugin for
one IPv4 DHCP client interface in an existing active/passive CARP pair. The
[plugin guide](../net/dhcp-interface-ha/README.md) covers configuration and recovery;
the [package repository guide](package-repository.md#ha-dhcp-interface) covers the
shared BIND/HA catalogue and publication. This guide records implemented behavior and
remaining qualification. Completed implementation plans are retained in Git history.

## Controller and ownership

Native CARP determines ownership. The controller attaches a dedicated Ethernet
carrier to the failover LAGG `dhcpha0lagg` only when the plugin is enabled, local
configuration and ownership are valid, and every observed CARP instance is MASTER.
BACKUP, INIT, mixed roles, disabled CARP, missing observations and unsafe local
configuration prevent attachment. Active/active CARP is outside the supported
scope. Each node may use different logical interface IDs and physical NIC names.
VLAN carriers are rejected; the optional internal standby path may use a VLAN.

Both nodes use the same ordinary unicast MAC. The selected logical interface
remains a native IPv4 DHCP client. OPNsense owns DHCP, gateway discovery, firewall
rules, NAT, pfsync and normal routing. The plugin neither starts a second DHCP
client nor replicates leases. DHCP options remain native interface settings.

The mutation boundary is `runtime.py`; role/configuration decisions belong in
`core.py`. Configd actions, CARP event wakeups, native routing callbacks and the
five-second reconciliation service converge through one transition lock. Commands
and observations have bounded timeouts. Configuration, role and ownership are
reread before destructive operations and attachment; successful command exit
alone is not verification. The lock serializes plugin operations, but cannot make
kernel CARP and native OPNsense changes atomic with userland commands.

Runtime ownership under `/var/run/dhcp-interface-ha/` includes the device's
interface index and random native interface-group token. An index alone is not
ownership: FreeBSD can reuse it. The controller never adopts a foreign same-name
interface. Runtime ownership, route intent, activation-failure evidence, stop
inhibition and PID files are node-local and are not XMLRPC-synchronized. Removal
preserves the transition lock inode.

## Attachment, fencing and health

Promotion first verifies cleanup of any owned standby route. It then establishes
a down, detached carrier, applies the shared MAC and any explicitly configured
MTU, and rechecks configuration and CARP before adding the carrier. The LAGG
inherits its first member's MAC. The controller verifies membership, both MACs
and receive mode before accepting an active attachment. Raising the LAGG brings
up its member; there is no independent carrier-up step. A correct active
attachment is retained without a gratuitous detach/reconnect.

Demotion takes the owned LAGG and observed members down before removing members.
It verifies that the LAGG is empty and the reserved carrier is down. Failure to
verify fencing is an error, never a successful FENCED result. Normal detachment
retains the shared MAC because FreeBSD restores the address saved at attachment.
Owned attachments remain subject to fencing after reassignment or a configuration
read failure; unrelated, unmigrated interfaces are left alone.

Physical link is readiness telemetry. A deliberately down carrier may report no
link, so an eligible MASTER activates the verified attachment before waiting for
negotiation. Link loss alone does not trigger plugin-driven CARP demotion. Missing
DHCP leases, DNS/Internet failures and common ISP outages likewise do not elect a
new owner. The native CARP service-health hook reports local ability to attach
safely, independently of the node's current role.

Failed MAC or receive-mode activation verification leaves persistent failure
evidence for the health hook after fencing. This prevents a repeatedly unusable
MASTER from reporting healthy. Repair the cause shown in the native Log, then
restart the service to permit retry. Health and teardown regression tests cover
these failure paths; native fault injection remains unqualified.

A deliberate stop inhibits promotion before fencing. If fencing fails, the
service remains available to retry; start clears stop inhibition. The native
boot hook prepares an owned detached device before normal interface configuration.
Installed, enabled cold-boot silence still requires external capture.

## Settings, setup and removal

Shared Enable and MAC settings synchronize through native XMLRPC. Logical
assignment, captured carrier and standby Internet settings are node-local. Native
rules, NAT and gateways can refer to logical IDs; the plugin does not remap those
references when IDs differ between nodes.

General provides the overview; Settings, Diagnostics and Log use the native UI.
Save & Apply submits shared and local models as one revision-checked transaction.
It rejects stale revisions and unsafe identity changes. Native configuration locks
must not be held across configd or another config-reading child process. GET/status
calls are read-only; unavailable observations must not appear as verified success.
Save, apply and readback outcomes remain distinct, including timeouts and retries.

For new setup, Save & Apply captures the selected assignment's original device,
saves disabled state, prepares the owned detached LAGG and uses the native
assignment controller to relink. An empty shared MAC may use the carrier's freshly
observed usable MAC. Captured mapping is retained after partial failure. Unrelated
pending native assignment edits block setup; the plugin does not discard them.
Enablement follows setup verification. Unchanged saves avoid native apply and
controller restarts when owned configuration is already correct.

Setup persists the native logical interface's promiscuous-mode setting so native
link configuration does not undo shared-MAC reception. Interface-local
`dhcpha_original_promisc` metadata preserves the original field/value for removal;
repeated saves do not overwrite it. Invalid restoration metadata fails closed.
Runtime verification and repair remain safeguards.

Saving the Disabled interface selection first disables and verifies fencing, then
restores the saved native assignment and original receive-mode setting before
clearing local plugin configuration. A retry with settings already disabled still
requires fresh verified fencing. Uncertain restoration retains the captured
carrier for retry. Package removal refuses remaining assignments, foreign devices
and unverified fencing. Upgrades preserve configuration and skip intentional
removal. No OPNsense core files are patched.

Events use native logging. Diagnostics distinguish configured intent, observed
attachment, route selection and tested reachability; they do not claim peer
readiness from local observations. Native log viewing and shared-sync selection
updates retain their separate privileges. Selecting this plugin on an existing
XMLRPC sender changes only its membership and does not trigger synchronization.

## Standby Internet

The optional IPv4 standby path uses an enabled static internal interface and an
existing on-link CARP VIP, both selected independently on each node. The node's
native address must differ from the VIP. The managed DHCP interface and its
reserved carrier cannot serve as this path. No hardcoded LAN name, peer address
or NIC driver is required. Conflicting defaults and unsupported multi-WAN or
dynamic-routing setups require separate qualification.

Selection requires a fresh inventory in which every expected CARP instance is
BACKUP, the VIP is not locally owned, and the managed WAN is verified fenced.
The controller records ownership intent before changing the default and verifies
the actual gateway, interface and source address afterward. A selected route is
not proof of Internet connectivity. Native post-routing callbacks and periodic
reconciliation maintain the path without creating an always-enabled internal
gateway or changing gateway priorities.

Promotion withdraws the owned standby route before WAN attachment. Demotion
fences the WAN before selecting the standby path. Mixed/unknown roles, disable,
stop, removal or a changed mapping withdraw owned routing through current native
IPv4 recalculation. Cleanup does not replay a saved DHCP gateway, change IPv6
routes or delete unrelated defaults. Failed cleanup retains ownership evidence
for retry and can block promotion; an unavailable optional Internet path alone
does not demote a capable BACKUP or tear down a correct active attachment.

Native reassignment can leave a stale `link#N` default on the reserved carrier.
Reconciliation may remove it even with standby Internet disabled, but only with
a committed managed mapping, verified ownership, matching interface index and an
addressless, down, detached carrier. Fresh route and safety readbacks precede
gateway-qualified deletion. Other defaults and concurrent replacements are
preserved; status and validation do not perform this cleanup.

Both potential active nodes need suitable native ingress policy and outbound NAT
for the peer's internal address. Services need usable source bindings. The system
default also affects forwarded traffic using that table; it is not a
local-services-only policy. The plugin does not create firewall rules, change NAT
mode or restart unrelated services. The plugin guide covers service bindings and
WireGuard CARP dependencies.

## Verification and remaining qualification

Local preparation for source version `0.2_43` passed 109 plugin Python/PHP tests,
both JavaScript UI suites, and 373 CI helper tests plus three subtests. Syntax,
workflow lint and diff checks passed. Fixtures use synthetic network identities.
Ten temporary mutation checks verify activation-health evidence, both teardown
fencing guards, UI retry freshness, event sanitization, forbidden CLI overrides,
address-loss observation, signer package identity, CARP input ordering and forward
publication retries. Each passing baseline fails when its corresponding guard is
removed. Controller fixture consolidation also preserves the captured settings
and status scenario responses, apart from timestamps and fixture interface bookkeeping.
Unit tests do not establish native browser, boot, failover or session behavior.

On 2026-10-08, the same manually built `0.2_42` archive from source `eaa73b948`
was installed and verified on both appliances. Its SHA-256 is
`5bc25df5f0bed6b7530c50567c3fc7f7223077a8b17106bffc460f7e564c4b2c`.
All 157 plugin tests passed on OPNsense 26.7.5 / FreeBSD 15.1-RELEASE-p3 and
OPNsense 26.7.1_1 / FreeBSD 15.1-RELEASE-p1. Installed source hashes, package
integrity, native UI routes and controller health passed; configuration and
ownership were unchanged. Observations retained the active node's attachment and
the backup's fence. The backup already had mixed CARP roles, so its optional
standby path remained inactive. This was not a deliberate paired failover test.
Rollback archives and detailed appliance evidence remain private.

Earlier native checks exercised standby route selection, routing/configuration
callbacks, stop/start, disable/re-enable and recovery from native default
replacement. ISP-facing capture observed a fenced backup emitting no packets.
These checks and a previously exercised `0.2_36` handoff do not qualify the current
feature set for production or prove pfsync session continuity.

The following qualification remains open. Record the exact package/core versions,
configuration and actual outcomes; preserve rollback packages, configuration
backups and console recovery before authorized disruptive tests.

| Boundary | Remaining evidence |
| --- | --- |
| Native browser and permissions | Authenticated setup, ordinary/unchanged save, Disabled restoration, partial-failure retry, stale revisions, pending native edits, dirty forms, unavailable status and rapid selection changes. Verify log registration/filtering and restricted-user access; sender selection must preserve other memberships without contacting the peer. |
| Boot and lifecycle | Enabled cold boot with external first-frame capture, native device preparation, crash/restart, failed stop/fence, upgrade and guarded removal. Verify shared identity and no backup transmission through each transition. |
| Paired handoff | Both promotion directions, abrupt MASTER loss and return, local activation failure and native demotion/recovery. Correlate CARP, carrier/LAGG events, DHCP and traffic recovery; measure overlap and outage intervals. |
| Standby routing | In both role directions, verify kernel gateway/interface/source, peer forwarding/NAT, DNS, NTP and an actual HTTPS package download. Capture zero standby WAN frames and cleanup before promotion. |
| Routing recovery | DHCP renewal, native interface/routing reconfiguration, gateway alarms, missed events, restart, feature disable and failed cleanup. Preserve unrelated routes, management, VPN/static routes, XMLRPC and pfsync. |
| Common failures | Shared ISP outage, no VIP owner, both nodes BACKUP, mixed roles and loss of the internal path. Show no sustained peer/self-routing loop or extra election; native split-brain limitations remain. |
| Session continuity | Verify stable public lease, equivalent native firewall/NAT policy and healthy pfsync with the same kernel interface name. Measure established TCP/NAT and application behavior; standby-originated connections may reconnect. |
| Release pipeline | Complete the first FreeBSD build/sign/staged-install/public-install workflow and record its URL/outcome. Signed `0.2_43` publication remains pending. |

Delayed failback is not implemented: keep its value at zero and retain native
CARP preemption policy. Peer release acknowledgments, Internet-health election,
passive conflict checks and lease replication are outside the implemented scope.
Any future failback hold must preserve takeover when the active node disappears
and the administrator's native preemption baseline; it requires separate design
and native qualification.
