# os-ha-dhcp-interface reference

## Package

| Property | Value |
| --- | --- |
| Package | `os-ha-dhcp-interface`, origin `opnsense/os-ha-dhcp-interface` |
| Source | `net/ha-dhcp-interface/` |
| Supported platform | OPNsense 26.7, amd64 |
| Conflicts | `os-dhcp-interface-ha`, `os-wan-ha-dhcp` (and their `-devel` packages) |
| Version | `<series>_<n>`, starting at `26.7_1`; published as a GitHub pre-release |
| Release branch | `release/ha-dhcp-interface/<series>` |
| Former name | `os-dhcp-interface-ha` up to `26.7_3`; its Releases and branch are frozen |

## Pages

| Menu entry | Content |
| --- | --- |
| **Services → HA DHCP Interface → General** | Overview |
| **Settings** | Tabs: **Settings** (state and setup), **Diagnostics** (observed details, guarded recovery, native configuration links, downloadable snapshot), **Log** |

## Settings

| Setting | Scope | Default | Description |
| --- | --- | --- | --- |
| Enable HA DHCP Interface | shared | off | Enables CARP-managed operation. |
| Managed logical interface | local | none | The node's OPNsense assignment to manage. **Disabled** clears this node's mapping. |
| Shared interface MAC | shared | empty | One unicast MAC for both nodes. Empty uses the carrier's observed MAC at setup. **Generate** proposes a locally administered address. |
| Standby Internet access | local | off | Routes the BACKUP's IPv4 traffic through the active node. |
| Standby internal interface | local | none | An enabled interface with static IPv4; VLANs allowed. Not the managed interface. |
| Standby gateway CARP VIP | local | none | Existing IPv4 CARP VIP on that interface. |
| Failback delay | shared | `0` | Not implemented; keep at `0`. Not shown in the form. |
| Carrier | local | none | Captured from the selected assignment at setup; not shown in the form. |

Shared settings live in `OPNsense/DhcpInterfaceHaShared` and synchronize
through XMLRPC. Local settings live in `OPNsense/DhcpInterfaceHaLocal` and
never synchronize.

## Save & Apply

| Report | Meaning |
| --- | --- |
| `WORKING` | Step running |
| `OK` | Step verified |
| `FAIL` | Step failed; earlier steps and the reason stay visible |
| `UNKNOWN` | Timed out; the firewall may still be applying it |

A successful save does not mean a working link; the final readiness check
reports controller faults separately. An unchanged save reports that no
actions were needed.

## Summary states

The **Settings** tab summarizes the node in one label.

| Label | Condition |
| --- | --- |
| Not configured | No managed interface |
| Status unavailable | Status could not be read or is stale; never shown as success |
| Needs attention | An unverified or unsafe attachment, or a reported fault |
| Ready to enable · Disabled | Setup complete on `dhcpha0lagg`; HA disabled |
| Disabled · Interface disconnected | HA disabled; carrier detached |
| Maintenance · Interface disconnected | CARP maintenance mode; carrier detached |
| Standby · Interface intentionally disconnected | BACKUP; carrier detached |
| Standby · Internet via internal network | BACKUP; standby route selected |
| Standby · Internet path unknown / unavailable | BACKUP; standby route not confirmed or not selected |
| Active · CARP MASTER · *address* | MASTER with the owned carrier attached; *address* is the IPv4 address or `Waiting for DHCP` |
| Unable to proceed | Ready, but no state above applies |

## Controller command

`/usr/local/bin/python3 /usr/local/opnsense/scripts/dhcp_interface_ha/dhcp_interface_ha.py <command>`

| Command | Purpose |
| --- | --- |
| `status [--from-config]` | Observed state and dry-run plan; changes nothing |
| `generate-mac` | Print a locally administered unicast MAC |
| `validate-mac <mac>` | Validate a candidate shared MAC |
| `serve` | Run the controller (used by the service) |
| `apply`, `prepare`, `prepare_setup`, `reconcile`, `fence`, `suspend`, `resume`, `remove`, `health`, `routing`, `route_status` | Root controller operations used by the hooks, the service and the web interface |

## configd actions

Run as `configctl dhcp_interface_ha <action>`.

| Action | Runs |
| --- | --- |
| `status` | `status --from-config` |
| `apply` | `apply` |
| `prepare_setup` | `prepare_setup` |
| `route_status` | `route_status` |

## Service and hooks

| Path | Role |
| --- | --- |
| `/usr/local/etc/rc.d/dhcp_interface_ha` | Service; supervises `serve` with `daemon` |
| `/usr/local/etc/rc.syshook.d/start/95-dhcp-interface-ha` | Starts the service at boot |
| `/usr/local/etc/rc.syshook.d/carp/10-dhcp-interface-ha` | Wakes the controller on a CARP event; fences directly if it is not running |
| `/usr/local/etc/rc.syshook.d/monitor/10-dhcp-interface-ha` | Re-evaluates standby routing after native routing changes |
| `/usr/local/etc/rc.carp_service_status.d/dhcp-interface-ha` | CARP service health; reports local incapacity |
| `/usr/local/etc/inc/plugins.inc.d/dhcp_interface_ha.inc` | Device registration, boot preparation and routing callbacks |

The controller also reconciles every five seconds.

## Runtime files

| Path | Content |
| --- | --- |
| `/var/run/dhcp-interface-ha/supervisor.pid` | Service supervisor PID |
| `/var/run/dhcp-interface-ha/controller.pid` | Controller PID |
| `/var/run/dhcp-interface-ha/` | Ownership (interface index and group token), route intent, activation-failure evidence, stop inhibition and the transition lock |

All runtime files are node-local and never synchronized.

## Log

The **Log** tab shows the native `dhcpinterfaceha/core` stream, with
Informational events visible by default and native filters and retention. If
native local logging is off, the tab says so. Log access does not grant
clearing logs, changing settings or controlling interfaces.

When observations change, the controller logs `interface_observed` before
reconciling: carrier and LAGG administrative state, link, promiscuous flags,
LAGG members and carrier IPv4, IPv6 and CARP address counts. Fields that the
system does not report say `unknown`.

## Series upgrade

| Role | Path |
| --- | --- |
| Upgrade hook | `/usr/local/etc/rc.syshook.d/upgrade/50-dhcp-interface-ha-repository` |
| Repository provider | `/usr/local/opnsense/scripts/firmware/repos/ResolverPluginsHA.sh` |
| Start hook | `/usr/local/etc/rc.syshook.d/start/50-dhcp-interface-ha-repository` |
| Reconcile worker | `/usr/local/opnsense/scripts/dhcp_interface_ha/repository_reconcile.sh` |
| Pending marker | `/var/db/os-dhcp-interface-ha/repository-reconcile.pending` |
| Log | `/var/log/os-dhcp-interface-ha-reconcile.log` |

See [Series upgrades](../../maintainers/reference.md#series-upgrade).

## Qualification record

### Local verification

Local preparation for source version `0.2_43` passed 109 plugin Python and
PHP tests, both JavaScript UI suites, and 373 CI helper tests plus three
subtests. Syntax, workflow lint and diff checks passed. Fixtures use synthetic
network identities. Ten temporary mutation checks covered activation-health
evidence, both teardown fencing guards, UI retry freshness, event
sanitization, forbidden CLI overrides, address-loss observation, signer
package identity, CARP input ordering and forward publication retries; each
passing baseline failed when its guard was removed. Controller fixture
consolidation preserved the captured settings and status responses, apart
from timestamps and fixture interface bookkeeping. Unit tests do not
establish native browser, boot, failover or session behavior.

### Published and installed builds

- **`0.2_45`**, first signed release, from source
  `8e29ddd8590e8b7708d5bcebd8a7b09238621788`, completed the
  [release workflow](https://github.com/resolver-plugins/plugins/actions/runs/38493507292)
  on 2026-10-14. Build, signing, staged installation, public-feed
  installation and source prerelease publication passed on OPNsense
  `26.7.4_1` / FreeBSD `15.1-RELEASE-p3` with pkg `2.3.1_1`. Native checks
  verified package file checksums, UI route resolution, interface inventory
  and a running controller with setup incomplete, HA disabled and no managed
  interface attached. All 37 published feed files matched the signed
  artifact. Package SHA-256:
  `f97b6b226c4d4450986a0d74ba6db7fd84ce8e7ab79fbca5b299963f701bd71b`. The
  VM package hooks reported `Reloading template OPNsense/Syslog: ERR`, so
  native log integration remains unqualified by this run.
- **`0.2_42`**, manually built from source `eaa73b948`, installed and
  verified on both appliances on 2026-10-08. SHA-256:
  `5bc25df5f0bed6b7530c50567c3fc7f7223077a8b17106bffc460f7e564c4b2c`. All
  157 plugin tests passed on OPNsense 26.7.5 / FreeBSD 15.1-RELEASE-p3 and
  OPNsense 26.7.1_1 / FreeBSD 15.1-RELEASE-p1. Installed source hashes,
  package integrity, native UI routes and controller health passed;
  configuration and ownership were unchanged. The active node kept its
  attachment and the backup its fence. The backup already had mixed CARP
  roles, so its optional standby path stayed inactive. This was not a
  deliberate paired failover test. Rollback archives and detailed appliance
  evidence are private.

Earlier native checks exercised standby route selection, routing and
configuration callbacks, stop/start, disable/re-enable and recovery from
native default replacement. ISP-facing capture observed a fenced backup
emitting no packets. These checks and an earlier `0.2_36` handoff do not
qualify the current feature set for production or prove pfsync session
continuity.

### Open qualification

Record the exact package and core versions, configuration and actual
outcomes. Keep rollback packages, configuration backups and console access
before authorized disruptive tests.

| Boundary | Remaining evidence |
| --- | --- |
| Native browser and permissions | Authenticated setup, ordinary and unchanged save, Disabled restoration, partial-failure retry, stale revisions, pending native edits, dirty forms, unavailable status and rapid selection changes. Log registration and filtering, restricted-user access; sender selection preserves other memberships without contacting the peer. |
| Boot and lifecycle | Enabled cold boot with external first-frame capture, native device preparation, crash and restart, failed stop or fence, upgrade and guarded removal. Shared identity and no backup transmission through each transition. |
| Paired handoff | Both promotion directions, abrupt MASTER loss and return, local activation failure and native demotion and recovery. Correlate CARP, carrier and LAGG events, DHCP and traffic recovery; measure overlap and outage. |
| Standby routing | In both role directions: kernel gateway, interface and source, peer forwarding and NAT, DNS, NTP and an actual HTTPS package download. Zero standby WAN frames and cleanup before promotion. |
| Routing recovery | DHCP renewal, native interface and routing reconfiguration, gateway alarms, missed events, restart, feature disable and failed cleanup. Unrelated routes, management, VPN and static routes, XMLRPC and pfsync preserved. |
| Common failures | Shared ISP outage, no VIP owner, both nodes BACKUP, mixed roles and loss of the internal path. No sustained peer or self-routing loop and no extra election. |
| Session continuity | Stable public lease, equivalent native firewall and NAT policy and healthy pfsync with the same kernel interface name. Established TCP/NAT and application behavior; standby-originated connections may reconnect. |
