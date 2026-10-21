# os-bind-rp how-to guides

Task-focused steps for running `os-bind-rp`. Each guide assumes the plugin is
installed and BIND is enabled; the [tutorial](tutorial.md) covers that. Field
names match the web interface; the [reference](reference.md) describes every
setting.

## Replace the official os-bind

Settings carry over: both packages use the same configuration section.

1. Back up the configuration:

   ```sh
   cp /conf/config.xml "/conf/config.xml.os-bind-rp.$(date +%Y%m%d%H%M%S).bak"
   ```

2. Configure the Resolver Plugins repository as described in the
   [root README](../../../README.md#configure-the-package-repository).
3. Install the plugin. `pkg` removes `os-bind` in the same transaction:

   ```sh
   pkg install os-bind-rp
   ```

4. Open **Services → BIND → General Settings**, check the settings and click
   **Save**.

The install refuses with a message, and changes nothing, when OPNsense or the
installed `bind920` is older than the plugin's minimum.

## Move BIND to port 53

1. Disable Unbound, or move it to another port, in **Services → Unbound DNS →
   General**, and apply.
2. In **Services → BIND → General Settings**, set **Listen Port** to `53` and
   click **Save**.
3. Point **System → Settings → General → DNS servers** at the firewall, or set
   DHCP clients to use it, as your network requires.

**Listen Port** rejects a port that another configured DNS service still
reserves.

## Answer only on selected interfaces

1. In **General Settings**, choose the interfaces in **Network Interfaces**.
2. Click **Save**.

BIND then listens on those interfaces' IPv4 and IPv6 addresses plus the
loopback addresses. Leave the field empty to listen on every address.

## Forward over DNS-over-TLS

1. Open **Services → BIND → Query Forwarding**.
2. In **DNS over TLS**, add each upstream with its **IP Address** and **TLS
   Hostname**, such as `1.1.1.1` and `cloudflare-dns.com`.
3. Click **Save**.

Leave **TLS Hostname** empty only when you accept an unverified, encrypted
connection. To stop BIND from resolving on its own when every forwarder fails,
set **Forwarding Mode** to **Forward Only** in **General Settings**.

To use a local encryption proxy instead, add it in the **DNS** table with
address `127.0.0.1` and the proxy's **Port**, such as `5300`.

## Forward one domain to another server

1. Open **Services → BIND → Zones → Forward Zones** and add the zone, such as
   `corp.example`.
2. Select the servers in **Primary IP** and click **Save**.

## Publish DHCP clients in a zone

The DHCP watcher adds `A`, `AAAA` and `PTR` records for active leases and
removes them when leases end. It works with ISC DHCP, Kea DHCPv4 and Kea
DHCPv6.

1. Create the forward zone in **Zones → Primary Zones**, such as `home.arpa`.
2. Set the DHCP server to advertise the same domain:
   - ISC DHCP: the range's or pool's **Domain name**.
   - Kea DHCPv4: the subnet's domain name option; empty falls back to the
     system domain.
   - Kea DHCPv6: the subnet's domain search list.
3. Open **Services → BIND → Access Control → TSIG Keys**, add a key, generate
   its **Secret** and click **Save**.
4. Open **Services → BIND → Records → DHCP Watcher** and add a mapping:
   **DHCP Source**, **Hostname Suffix** `home.arpa` and the **TSIG Key**.
   Leave **Reverse Zone** empty to use any matching reverse zone.
5. Click **Save**.

**Records → Active DHCP Records** then lists the published leases. Problems are logged in **Log Files → DHCP Watcher Log**.

## Add reverse DNS for a subnet

1. Open **Services → BIND → Reverse DNS** and add a zone.
2. Select a directly connected **Subnet**. It must be octet-aligned for IPv4
   (`/8`, `/16`, `/24`) or nibble-aligned for IPv6 (a multiple of `/4`).
3. Click **Save**.

The zone name, such as `1.168.192.in-addr.arpa`, is generated from the subnet.
The DHCP watcher publishes `PTR` records into it.

## Block ads and malware with DNSBL

1. Open **General Settings → DNSBL**.
2. Select **Enable DNSBL and RPZ** and choose lists in **Type of DNSBL**.
3. Add exceptions in **Whitelist Domains** if needed.
4. Click **Save**. The plugin downloads the lists and restarts BIND; the tab
   reports the load.

To refresh the lists on a schedule, add a cron job in **System → Settings →
Cron** with the command **Update BIND DNSBLs**.

Large selections need a lot of memory while BIND loads them. Keep **Memory
Guard (MB)** above zero; see [Recover after Memory Guard disabled DNSBL](#recover-after-memory-guard-disabled-dnsbl).

## Recover after Memory Guard disabled DNSBL

When free memory fell below **Memory Guard (MB)** while BIND loaded the
blocklists, the plugin turned DNSBL off and restarted BIND without it. The
**General Log** records `DNSBL startup memory guard`.

1. Choose fewer lists in **Type of DNSBL**, or add memory.
2. Select **Enable DNSBL and RPZ** again and click **Save**.

## Check a zone before relying on it

1. Apply the zone first; Check and Show work only on enabled, applied zones.
2. In **Zones → Primary Zones**, use the zone's check command to run
   `named-checkzone`, or show to view the zone as BIND serves it.

From a shell, the same checks are:

```sh
configctl bind zone.check home.arpa
configctl bind zone.show home.arpa
```

## Add custom BIND configuration

Put `.conf` files in `/usr/local/etc/namedb/named.conf.d/`; they are included
in alphabetical order at the top level of `named.conf`, then restart BIND.
**General Settings** shows a notice while custom files are present, because
they can conflict with the plugin's settings.

## Replace the default RNDC key

New installations share the same default RNDC secret. Replace it:

1. Generate a secret, for example with `openssl rand -base64 32`.
2. Paste it into **General Settings → Secret** under **RNDC Key** and click
   **Save**. Saving restarts BIND with the new key.

## Move BIND to OPNsense's package

Systems that installed BIND 9.20.27 from the Resolver Plugins repository keep
it, because `pkg` upgrades a package only from the repository it came from.

1. Check where BIND came from:

   ```sh
   pkg query '%n %v %R' bind920 bind-tools
   ```

2. If either line ends in `resolver-plugins`, reinstall both from OPNsense:

   ```sh
   pkg install -f -r OPNsense bind920 bind-tools
   ```

3. Restart BIND to run the new binary, then check it:

   ```sh
   configctl bind restart
   named -v
   ```

On a CARP pair, do the standby node first.

## Upgrade on a CARP pair

Each node briefly stops answering DNS while its package is replaced.

1. Back up the configuration on both nodes.
2. Upgrade the BACKUP node with `pkg upgrade os-bind-rp`.
3. Check its generated configuration, zones, authoritative answers and
   **General Log**. Do not continue while a zone is unloaded or the log shows
   new errors.
4. Upgrade the MASTER node the same way.

## Upgrade OPNsense to a new series

Install pending plugin updates first; OPNsense offers the series upgrade only
then. After that, nothing to do. The plugin moves its repository to the new
series and upgrades itself after the first boot. If it cannot, it logs the reason to
`/var/log/os-bind-rp-reconcile.log` and retries at the next boot. If the log
shows a refusal, run `pkg install os-bind-rp`. For other manual fixes, see
[Series upgrades](../../maintainers/reference.md#series-upgrade).
