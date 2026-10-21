# os-bind-rp reference

## Package

| Property | Value |
| --- | --- |
| Package | `os-bind-rp`, origin `opnsense/os-bind-rp` |
| Source | `dns/bind/` |
| Replaces | official `os-bind` (`PLUGIN_CONFLICTS=bind`); never installed together |
| Minimum OPNsense | `26.1.11_10` |
| Minimum BIND | `bind920 >= 9.20.26`, from any enabled repository |
| Version | `<series>.<upstream>_<n>`, such as `26.7.1.34_1`: series, upstream BIND plugin version, local revision |
| Configuration | `OPNsense/bind` in `config.xml`, the same section as `os-bind` |
| XMLRPC sync | **BIND domain name service** syncs all of `OPNsense/bind` and restarts `named` on the peer |

The pre-install script stops the `pkg` transaction (exit 3) when OPNsense or
`bind920` is below its minimum or a conflicting package is installed.

## Pages

| Menu entry | Tabs |
| --- | --- |
| **Services → BIND → General Settings** | General, DNSBL |
| **Zones** | Primary Zones, Secondary Zones, Forward Zones |
| **Reverse DNS** | (single list) |
| **Records** | Primary Zone Records, DHCP Watcher, Active DHCP Records |
| **Query Forwarding** | DNS Forwarders (DNS and DNS over TLS tables) |
| **Access Control** | ACLs, TSIG Keys |
| **Log Files** | General, Query Log, Blocked Log, DHCP Watcher Log |

## General settings

| Setting | Default | Description |
| --- | --- | --- |
| Enable BIND Daemon | off | Runs `named`. |
| Disable IPv6 | off | Runs BIND in IPv4-only mode. |
| Disable Zone Notifications | off | Sets `notify no`. |
| Network Interfaces | empty (all addresses) | Interfaces whose IPv4 and IPv6 addresses BIND listens on, plus loopback. |
| Listen Port | `53530` | Refuses a port reserved by another configured DNS service. |
| Query Source IP / IPv6 | empty | `query-source` / `query-source-v6`. |
| Transfer Source IP / IPv6 | empty | `transfer-source` / `transfer-source-v6`. |
| Enable filter-aaaa on IPv4 / IPv6 Clients | off | Omits AAAA answers; signed answers only with DNSSEC Validation `No`. |
| ACL for filter-aaaa | empty | Clients the AAAA filter applies to. |
| Logsize in MB | `5` | Size limit of the query and blocked logs. |
| General Log level | Informational | Minimum severity in the General log. |
| Maximum Cache Size | `80` | Percent of system memory. |
| Recursion | empty | ACLs allowed to recurse. Empty uses BIND's default. |
| Allow Transfer | empty | Default ACLs allowed to transfer zones. |
| Allow Query | empty | Default ACLs allowed to query. |
| Forwarding Mode | Forward First | `forward first` or `forward only`. |
| DNSSEC Validation | `No` | `No` or `Auto`. |
| Hide Hostname / Hide Version | off | Hides the host name or BIND version. |
| Disable Prefetching | off | Disables prefetch. |
| Enable Rate Limiting | off | Response rate limiting. |
| Rate Limit Replies | empty | Replies per second. |
| Rate Limit Exceptions | `0.0.0.0`, `::` | Addresses exempt from rate limiting. |
| RNDC Key: Algorithm | `hmac-sha256` | Key `rndc-key` for `rndc` on `127.0.0.1:9530`. |
| RNDC Key: Secret | a fixed default | The same on every new installation; replace it. |

## DNSBL settings

| Setting | Default | Description |
| --- | --- | --- |
| Enable DNSBL and RPZ | off | Serves the selected blocklists as response-policy zones. |
| Memory Guard (MB) | `300` | Free memory to keep while BIND loads the blocklists. `0` disables the guard. |
| Type of DNSBL | empty | Blocklists, taken from Unbound's blocklist definitions. |
| Whitelist Domains | empty | Domains and their subdomains exempt from blocking. |
| Enable Google SafeSearch | off | Forces SafeSearch. |
| Enable DuckDuckGo SafeSearch | off | Forces SafeSearch. |
| Enable Youtube Adult Restrictions | off | Forces restricted YouTube. |
| Enable Strict Bing Search | off | Forces strict Bing search. |

The blocklist URLs come from
`/usr/local/opnsense/service/templates/OPNsense/Unbound/core/blocklists.conf`.
Refresh status is kept in `/var/run/bind/dnsbl-status.json`.

## Zones

| Type | Settings |
| --- | --- |
| Primary | Enabled, Zone Name, Allow Transfer, Allow RNDC key transfer, Allow Query, Allow RNDC key update, TTL, Refresh Time, Retry Time, Expire Time, Negative TTL, Mail Admin (`@` becomes `.`), DNS Server |
| Secondary | Enabled, Zone Name, Allow Transfer, Allow Query, Primary IP, Transfer Key Algorithm, Transfer Key Name, Transfer Key, Allow Notify |
| Forward | Enabled, Zone Name, Primary IP (the servers to forward to) |
| Reverse | Enabled, Subnet; the zone name is generated |

A reverse zone's subnet must be directly connected and octet-aligned (IPv4) or
nibble-aligned (IPv6). `192.168.1.0/24` gives `1.168.192.in-addr.arpa`.

## Records

Settings: Enabled, Zone, Name, Type, Value.

Types: `A`, `AAAA`, `CAA`, `CNAME`, `DNAME`, `DNSKEY`, `DS`, `HTTPS`, `MX`,
`NAPTR`, `NS`, `PTR`, `RP`, `RRSIG`, `SRV`, `SSHFP`, `SVCB`, `TLSA`, `TXT`.

## Forwarders

| Table | Settings | Rendered as |
| --- | --- | --- |
| DNS | Enabled, IP Address, Port (default `53`) | `<ip> port <port>` |
| DNS over TLS | Enabled, IP Address, Port (default `853`), TLS Hostname | `<ip> port <port> tls "dot-<uuid>"` |

Both tables form one forwarder pool. A DNS-over-TLS forwarder with a TLS
Hostname verifies the certificate against that name and
`/usr/local/etc/ssl/cert.pem`; without one, the connection is encrypted but
not verified.

## Access control

| Item | Settings |
| --- | --- |
| ACL | Enabled, Name, Network List |
| TSIG key | Enabled, Name, Algorithm (`hmac-sha512`, `hmac-sha384`, `hmac-sha256`, `hmac-sha224`, `hmac-sha1`, `hmac-md5`), Secret (with a generate button) |

## DHCP watcher

| Setting | Description |
| --- | --- |
| Enabled | Enables the mapping. |
| DHCP Source | `isc-dhcp`, `kea-dhcp4` or `kea-dhcp6`. |
| Hostname Suffix | Primary zone used to build FQDNs. Leases are taken from the DHCP scopes that advertise this domain. |
| Reverse Zone | Optional. Limits the key to that reverse zone; empty uses the most specific matching reverse zone. |
| TSIG Key | Key used for the updates. |

| Behavior | Value |
| --- | --- |
| Records | `A` or `AAAA` in the hostname suffix zone, `PTR` in the reverse zone, TTL 300 |
| Updates | `nsupdate` to `127.0.0.1` on the listen port, signed with the TSIG key |
| Zone permission | `update-policy { grant <key> zonesub ANY; }` on the mapped zones |
| ISC DHCP | Follows `/var/dhcpd/var/db/dhcpd.leases` |
| Kea | Polls the control socket every 10 seconds |
| Full reconcile and expiry cleanup | Every 60 seconds |
| Rendered configuration | `/usr/local/etc/bind/dhcpwatcher.conf` |
| State | `/var/cache/bind/dhcplease_state.json` |
| PID file | `/var/run/bind_dhcplease.pid` |
| Syslog facility | `bind-dhcplease` |

The watcher runs only while `named` runs and at least one mapping renders, and
exits after two failed `named` checks in a row.

## configd actions

Run as `configctl bind <action>`.

| Action | Runs |
| --- | --- |
| `start` | `pluginctl -c bind_start` (BIND, Memory Guard, DHCP watcher) |
| `stop` | `bindStop.py` |
| `restart` | `bindStop.py`, then `bind_start` |
| `status` | `rc.d/named status` |
| `reload` | `rc.d/named reload` |
| `dnsbl [codes]` | Fetches blocklists, reloads the blocklist zone and flushes the cache |
| `dnsblapply` | Fetches blocklists, restarts BIND and waits up to 120 seconds for the load result |
| `dnsblcron` | As `dnsbl`; listed in Cron as **Update BIND DNSBLs** |
| `zone.check <zone>` | `named-checkzone`, including the dynamic-update journal |
| `zone.show <zone>` | Prints the zone as served |
| `dhcpwatcher` | Starts the DHCP watcher if needed |

## Service behavior

| Event | Behavior |
| --- | --- |
| Stop | `rndc stop`, then up to 30 seconds; then `SIGTERM` and 5 seconds; then `SIGKILL` and 5 seconds. Each escalation is logged. Then stops the watcher, removes the dynamic-update journals of managed zones and deletes the watcher state. |
| Configure | Stops BIND; if the stop fails, the configuration is not regenerated. Otherwise reloads the `OPNsense/Bind` templates and starts BIND. |
| Start with DNSBL | Memory Guard samples free memory for up to 90 seconds. Below the floor it stops `named`, turns DNSBL off in the configuration, regenerates it and starts BIND without blocklists. |

## Files

| Path | Content |
| --- | --- |
| `/usr/local/etc/namedb/named.conf` | Generated configuration |
| `/usr/local/etc/namedb/named.conf.d/*.conf` | Custom includes; a notice appears on General Settings when present |
| `/usr/local/etc/namedb/primary/<zone>.db` | Generated zone files |
| `/usr/local/etc/namedb/rndc.conf` | RNDC client configuration |
| `/etc/rc.conf.d/named` | Service variables, including DNSBL and Memory Guard |
| `/var/cache/bind/os-bind-rp-package.*` | Zone backup during a package transaction; retained and named in the error if restore fails |

## Series upgrade

| Role | Path |
| --- | --- |
| Upgrade hook | `/usr/local/etc/rc.syshook.d/upgrade/50-bind-rp-repository` |
| Repository provider | `/usr/local/opnsense/scripts/firmware/repos/ResolverPlugins.sh` |
| Start hook | `/usr/local/etc/rc.syshook.d/start/50-bind-rp-repository` |
| Reconcile worker | `/usr/local/opnsense/scripts/OPNsense/Bind/bindRepositoryReconcile.sh` |
| Pending marker | `/var/db/os-bind-rp/repository-reconcile.pending` |
| Log | `/var/log/os-bind-rp-reconcile.log` |

The behavior of these files is described in
[Series upgrades](../../maintainers/reference.md#series-upgrade).

## Logs

Syslog facilities `named` and `bind-dhcplease`, shown under **Log Files**.
