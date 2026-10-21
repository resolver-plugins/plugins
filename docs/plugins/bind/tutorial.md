# Tutorial: serve a local zone with os-bind-rp

In this tutorial you install `os-bind-rp`, start BIND next to OPNsense's own
Unbound resolver, publish one local zone and resolve a name from it. At the
end you will have a working authoritative zone and a recursive resolver with
encrypted forwarding.

You need an OPNsense `26.1.11_10` or newer system with a root shell and the
Resolver Plugins repository configured, as described in the
[root README](../../../README.md#configure-the-package-repository). The
example uses the zone `home.arpa` and the address `192.168.1.10`; substitute
your own.

## 1. Install the plugin

From the root shell:

```sh
pkg install os-bind-rp
```

If the official `os-bind` is installed, `pkg` replaces it. Reload the web
interface; **Services → BIND** now appears in the menu.

## 2. Start BIND

1. Open **Services → BIND → General Settings**.
2. Select **Enable BIND Daemon**.
3. Leave **Listen Port** at `53530`. Unbound keeps port 53, so the two
   services run side by side while you experiment.
4. Click **Save**.

Check that BIND answers:

```sh
drill @127.0.0.1 -p 53530 opnsense.org
```

The reply has `rcode: NOERROR` and an answer section. If it times out, check
**Services → BIND → Log Files → General**.

## 3. Allow your network to use recursion

With no **Recursion** ACL, BIND falls back to its built-in default and
recurses for the firewall itself and its directly connected networks. Name the
clients explicitly instead.

1. Open **Services → BIND → Access Control → ACLs**, add an ACL named `lan`
   with your LAN network, for example `192.168.1.0/24`, and click **Save**.
2. Open **General Settings**, select `lan` in **Recursion** and click
   **Save**.

## 4. Forward over DNS-over-TLS

1. Open **Services → BIND → Query Forwarding**.
2. In the **DNS over TLS** table, add a forwarder with **IP Address**
   `1.1.1.1` and **TLS Hostname** `cloudflare-dns.com`. Keep **Port** `853`.
3. Click **Save**.

BIND now sends recursive queries to Cloudflare over TLS and checks that the
certificate matches `cloudflare-dns.com`. Repeat the `drill` command from step
2; it still answers.

## 5. Publish a zone

1. Open **Services → BIND → Zones → Primary Zones** and add a zone:
   - **Zone Name**: `home.arpa`
   - **Mail Admin**: your address
   - **DNS Server**: the firewall's host name, such as `opnsense.home.arpa`

   Click **Save**.
2. Open **Services → BIND → Records → Primary Zone Records** and add a
   record: **Zone** `home.arpa`, **Name** `nas`, **Type** `A`, **Value**
   `192.168.1.10`.
3. Click **Save**.

Resolve the new name:

```sh
drill @127.0.0.1 -p 53530 nas.home.arpa
```

The answer section contains `nas.home.arpa. ... A 192.168.1.10`, and the
header carries the `aa` (authoritative) flag.

## What you have built

BIND now runs beside Unbound, answers authoritatively for `home.arpa`,
resolves everything else through an encrypted, verified forwarder, and serves
only your LAN. Next steps:

- Publish DHCP clients automatically:
  [Publish DHCP clients in a zone](how-to.md#publish-dhcp-clients-in-a-zone).
- Make BIND the system's resolver on port 53:
  [Move BIND to port 53](how-to.md#move-bind-to-port-53).
- Look up every setting in the [reference](reference.md).
