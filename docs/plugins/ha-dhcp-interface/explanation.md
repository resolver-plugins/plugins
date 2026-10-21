# Understanding os-ha-dhcp-interface

`os-ha-dhcp-interface` gives an existing active/passive CARP pair one IPv4
DHCP client interface, such as a WAN from an ISP that hands out a single
address by DHCP. It is experimental and **not production qualified**; see the
[qualification record](reference.md#qualification-record).

## The idea

CARP shares addresses between nodes, but a DHCP client interface has no
static address to share. The plugin shares the link instead. Each node has a
dedicated Ethernet adapter, the carrier, on the same segment. Only the CARP
MASTER attaches its carrier to the plugin's LAGG, `dhcpha0lagg`, and both
nodes use the same MAC on it. To the ISP, one device is present and keeps the
same identity across a failover.

OPNsense keeps everything else. The selected logical interface stays a native
DHCP client, and OPNsense still owns DHCP, gateway discovery, firewall rules,
NAT, pfsync and routing. The plugin neither starts a second DHCP client nor
replicates leases.

## Native CARP decides

The plugin adds no election of its own. The controller attaches the carrier
only when the plugin is enabled, the local configuration and ownership are
valid, and every observed CARP instance on the node is MASTER. BACKUP, INIT,
mixed roles, disabled CARP, missing observations and unsafe local
configuration all prevent attachment. Active/active CARP is out of scope.

The controller has no peer API credentials, release acknowledgments,
ping-based handover checks or passive conflict observation. Missing leases,
DNS or Internet failures and ISP outages do not elect a new owner. Because
native CARP decides, its split-brain behavior during a partition applies
unchanged: the plugin adds no witness. Session continuity across a failover
depends on a stable public lease and working pfsync.

Delayed failback is not implemented; keep the value at zero and use native
CARP preemption. A future failback hold would have to preserve takeover when
the active node disappears and keep the administrator's preemption baseline.

## Node-local by design

The shared settings, Enable and the shared MAC, synchronize through native
XMLRPC. The logical interface, the captured carrier and standby Internet
access are local to each node, so the nodes may use different interface IDs
and adapter names, such as `opt2`/`ix3` on one and `opt7`/`hn1` on the other.
There is no peer adapter discovery or name matching.

Native rules, NAT and gateways can refer to logical IDs. The plugin does not
remap those references when the IDs differ between nodes; arrange equivalent
references on each node or align the IDs. Matching the device name and MAC
alone does not make a failover session-preserving.

## The controller

Configd actions, CARP event wakeups, native routing callbacks and a
five-second reconcile loop all converge through one transition lock.
Commands and observations have bounded timeouts. Configuration, role and
ownership are read again before every destructive operation and before
attachment, and a successful command exit alone is never taken as
verification. The lock serializes the plugin's own work, but it cannot make
kernel CARP changes and native OPNsense changes atomic with it.

Decisions are made in one place (`core.py`) and changes to the system in
another (`runtime.py`), so every change passes the same checks.

### Ownership

The plugin records the device's interface index and a random native
interface-group token under `/var/run/dhcp-interface-ha/`. An index alone is
not ownership, because FreeBSD reuses indexes. The controller never adopts a
foreign interface with the same name.

### Attaching

On promotion the controller first verifies that any standby route it owns is
gone. It then prepares a down, detached carrier, sets the shared MAC and any
configured MTU, and checks configuration and CARP again before adding the
carrier to the LAGG. It verifies membership, both MACs and the receive mode
before accepting the attachment. The LAGG runs in promiscuous mode so that
virtual NIC filters accept the shared MAC, and the controller repairs a
cleared filter without disturbing a healthy attachment. A correct attachment
is kept as it is.

Physical link is only telemetry. A carrier that is down on purpose may report
no link, so an eligible MASTER activates first and then waits for
negotiation. Link loss alone does not demote the node.

### Fencing

On demotion the controller takes the LAGG and its members down before
removing the members, and then checks that the LAGG is empty and the carrier
down. A fence that cannot be verified is an error, never a successful result.
Detaching keeps the shared MAC, because FreeBSD restores the address that was
saved at attachment. Owned attachments are fenced even after a reassignment
or a failed configuration read; unrelated interfaces are left alone.

### Health

The native CARP service-health hook reports whether this node can attach
safely, independently of its current role. If MAC or receive-mode
verification fails at activation, the controller records the failure, fences,
and keeps reporting the node unhealthy, so a MASTER that cannot use the link
does not keep claiming it. Fix the cause shown in the log and restart the
service to allow a new attempt.

A deliberate stop inhibits promotion before fencing; if fencing fails, the
service keeps running so it can retry. Starting clears the inhibition. At
boot, the native hook prepares an owned, detached device before normal
interface configuration.

## Setup through native machinery

Save & Apply submits the shared and local settings as one revision-checked
transaction and rejects stale revisions and unsafe identity changes. For a new
setup it captures the original device of the selected assignment, saves the
plugin disabled, prepares the detached LAGG and uses OPNsense's own
assignment controller to move the interface. Enabling follows only after the
mapping is verified. An unchanged save avoids the native apply and any
restart.

Native apply consumes a shared queue of pending assignment edits, so the
plugin blocks setup when it sees unrelated pending edits instead of applying
or discarding them. The native API offers no isolation from another writer
after that check.

Setup also stores the interface's promiscuous-mode setting so native link
configuration does not undo shared-MAC reception. The original value is
kept in interface metadata for removal and is never overwritten by later
saves.

Clearing the setup first disables and verifies fencing, then restores the
native assignment and receive mode before clearing the plugin's local
configuration. If restoration is uncertain, the captured carrier is kept for
a retry.

## Removal and upgrades

`pkg` ignores a failing removal script and removes the files anyway, so the
plugin cannot refuse removal. With the device still assigned it leaves the
device and controller in place and warns. The same behavior makes switching
between the `-devel` and release packages, or from the former
`os-dhcp-interface-ha`, behave like an upgrade. Upgrades keep the
configuration and the device. No OPNsense core file is patched.

## Standby Internet access

While a node is BACKUP its WAN is fenced, so it has no Internet path of its
own for updates, DNS or NTP. Standby Internet access lends it one: an IPv4
default route through an existing CARP VIP on an internal interface, which
leads to the active node.

The route is selected only when every expected CARP instance is BACKUP, the
VIP is not owned locally and the WAN is verified fenced. The controller
records its intent before changing the default and then verifies the
gateway, interface and source address. Native routing callbacks and the
reconcile loop maintain the route without an always-enabled gateway or
changed gateway priorities.

Order matters in both directions. Promotion withdraws the standby route
before attaching the WAN; demotion fences the WAN before selecting the route.
Mixed or unknown roles, disabling, stopping, removal or a changed mapping
withdraw the route through OPNsense's own IPv4 routing recalculation. Cleanup
never replays a saved DHCP gateway, changes IPv6 routes or deletes unrelated
defaults. A cleanup failure keeps its evidence for a retry and can block
promotion; an unavailable Internet path alone never demotes a capable BACKUP.

A selected route is not proof of connectivity: the path still depends on the
active node's firewall rules, NAT and uplink. The route is a system default,
so forwarded traffic using that table follows it too. Unsupported multi-WAN
or dynamic-routing setups, and conflicting defaults on other interfaces, are
reported rather than replaced.

Native reassignment can leave a stale `link#N` default route on the carrier.
The controller removes it, even with standby access disabled, but only when
it owns the device, the mapping is committed, the interface index matches and
the carrier is addressless, down and detached. Fresh readbacks precede the
deletion; other defaults are left alone.

## Releases

The plugin follows no upstream plugin; it is built and published on its own
lifecycle in the same signed feed as `os-bind-rp`, as GitHub pre-releases.
Publishing a package never updates an appliance. See the
[package repository guide](../../maintainers/reference.md#releases).
