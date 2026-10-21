# Understanding os-bind-rp

## Why a replacement package

`os-bind-rp` keeps the upstream OPNsense BIND plugin as its base and adds a
small set of DNS management features: DNS-over-TLS forwarding, the DHCP
watcher, reverse zones, DNSBLs taken from Unbound's definitions, listener
interfaces and a safer service lifecycle. Upstream does not carry these, so
the fork publishes them under its own package name.

Both packages install the same files and read the same configuration section,
`OPNsense/bind`. They therefore conflict instead of coexisting: installing
`os-bind-rp` removes `os-bind` and keeps its settings. The package name differs
so that a fork release never looks like an official update, and the
pre-install script refuses to install while a conflicting package is
present.

## BIND comes from OPNsense

The plugin does not ship BIND. It declares a minimum `bind920` version and
lets `pkg` take any provider that meets it, normally OPNsense's own package.
The fork once built its own BIND to work around a regression; current
OPNsense ships a fixed, newer BIND, so those builds were retired. The minimum
OPNsense version, `26.1.11_10`, is the first with the BIND fix that
DNS-over-TLS forwarding depends on.

## Forwarding

Plain and DNS-over-TLS forwarders form one pool: BIND picks among all of them
by response time. Mixing the tables therefore mixes encrypted and unencrypted
upstream traffic; use one table if every query must be encrypted.

A TLS hostname makes the connection strict: BIND checks the upstream
certificate against that name and the system CA bundle, and refuses an
upstream it cannot verify. Without a hostname the connection is encrypted but
open to an on-path impersonator.

**Forward First**, the default, lets BIND resolve on its own when no forwarder
answers. That keeps DNS working but sends queries unencrypted to the root and
authoritative servers. **Forward Only** keeps queries on the forwarders and
fails instead.

## Listener interfaces

Like Unbound, BIND can answer on selected interfaces only. The plugin turns
the selected interfaces into concrete addresses when it configures BIND,
always adding loopback. The list is fixed until BIND is configured again:
after an interface address changes, BIND answers on the new address once
OPNsense reconfigures its DNS services or the settings are saved.

With no interfaces selected, BIND listens on every address and registers its
port with OPNsense, which then keeps other DNS services off that port.

## Configure means restart

Saving a setting stops BIND, regenerates its configuration and zone files and
starts it again; it does not reload. If BIND does not stop, the configuration
is not regenerated, so a running BIND never sees a half-written setup.

Stopping is bounded. The plugin asks BIND to stop with `rndc stop`, then
escalates to `SIGTERM` and `SIGKILL` with fixed timeouts, logging each
escalation. A wedged `named` therefore cannot hang the web interface or a
boot.

## Dynamic zones

The DHCP watcher and other `nsupdate` clients change zones at run time. BIND
keeps those changes in a journal beside the zone file. The plugin regenerates
zone files from the configuration on every configure, and a new zone file with
an old journal can make BIND refuse the zone.

The plugin resolves this differently for the two cases:

- **Configure and restart.** Stopping BIND deletes the journals of reverse
  zones and of zones the DHCP watcher publishes into, and discards the
  watcher's state. On start the watcher republishes every current lease, so
  its records return within seconds. Records that reached these zones any
  other way are lost; keep other dynamic updates in separate zones.
- **Package install and upgrade.** A package transaction may also replace the
  templates. If BIND is running, the package scripts freeze each dynamic zone,
  save its effective contents, including records received over RNDC, let
  OPNsense regenerate the files, restore the saved zones and start BIND
  again. The transaction fails if it cannot restore the zones or the running
  state.

## The DHCP watcher

The watcher turns DHCP leases into DNS records. It does not ask the DHCP
server for its configuration at run time. Instead, the configuration template
works out which DHCP scopes advertise each mapping's domain and passes those
ranges to the watcher. A lease outside those ranges is ignored, so one mapping
cannot publish another network's clients into its zone.

Updates go through BIND's normal dynamic-update path, signed with the
mapping's TSIG key, and each key may only change the zones it is mapped to.
The watcher records an update as applied only after BIND accepts it, and it
fully reconciles every minute, so a failed update is retried and drift is
corrected. Kea is polled because it has no change feed; ISC DHCP is followed
through its lease file.

The watcher runs only while BIND runs. It exits when BIND stops and is started
again by BIND's start path.

## Reverse zones

A reverse zone is named after a directly connected subnet, which is why the
subnet must be octet-aligned for IPv4 or nibble-aligned for IPv6: only those
prefixes map to a single `in-addr.arpa` or `ip6.arpa` zone. The DHCP watcher
places each `PTR` record in the most specific reverse zone that contains the
address.

## DNSBL and Memory Guard

DNSBLs are served as response-policy zones. The plugin uses the blocklist
definitions that ship with OPNsense's Unbound, so both resolvers offer the
same lists without the plugin keeping its own copy.

Loading a large blocklist needs far more memory than serving it. On a small
firewall, BIND can exhaust memory during start-up and take other services
with it. While BIND loads the blocklists, Memory Guard watches free memory.
If it drops below the configured floor, Memory Guard stops BIND, turns DNSBL
off in the configuration and starts BIND without it. Name resolution keeps
working, and the General log explains what happened. The setting stays off
until you turn it back on, so the next boot does not repeat the failure.

## Series upgrades

Packages are published per OPNsense series. When OPNsense moves to a new
series, the system needs the plugin built for that series, from the new feed.
The plugin carries its own hooks for this: the upgrade marks the plugin as
pending, OPNsense's repository step points the repository file at the new
feed, and after boot a worker installs the matching plugin. The worker refuses
any transaction that would remove or downgrade a package. A failure never
blocks the firmware upgrade or the boot; it is logged and retried at the next
boot. [Series upgrades](../../maintainers/reference.md#series-upgrade) describes
the steps in detail.
