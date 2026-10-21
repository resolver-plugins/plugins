# Tutorial: share a DHCP WAN across a CARP pair

> `os-ha-dhcp-interface` is experimental and **not production qualified**.
> Follow this tutorial on a test segment, never on a production uplink.

In this tutorial you set up `os-ha-dhcp-interface` on an existing
active/passive CARP pair so that one IPv4 DHCP client interface, such as a
WAN, follows the CARP MASTER. At the end, the MASTER holds the DHCP lease on a
shared MAC and the BACKUP keeps its adapter detached.

## Before you start

You need:

- Two OPNsense 26.7 amd64 nodes in a working active/passive CARP pair, with
  CARP configured on other interfaces.
- On each node, a dedicated, spare Ethernet adapter on the same test segment.
  VLAN adapters are not supported.
- Permission for each adapter's source MAC to change. On Hyper-V, enable MAC
  spoofing.
- The Resolver Plugins repository configured on both nodes, as described in
  the [root README](../../../README.md#configure-the-package-repository).
- No pending, unapplied changes in **Interfaces → Assignments**.

Choose one fixed unicast MAC for both nodes. The hardware MAC of the preferred
node's dedicated adapter is a good choice.

## 1. Install the plugin on both nodes

```sh
pkg install os-ha-dhcp-interface
```

Install the same version on both nodes. **Services → HA DHCP Interface**
appears in the menu.

## 2. Configure the BACKUP node

1. Open **Services → HA DHCP Interface → Settings**.
2. Select **Enable HA DHCP Interface**.
3. In **Managed logical interface**, choose this node's DHCP interface on the
   test segment.
4. Enter the shared MAC in **Shared interface MAC**, or click **Generate**.
5. Click **Save & Apply**.

A progress panel opens below the button. Each step reports `WORKING`, then
`OK`, `FAIL` or `UNKNOWN`. Setup may briefly interrupt this interface while it
moves the assignment to the plugin's device, `dhcpha0lagg`.

When it finishes, the summary shows **Standby · Interface intentionally
disconnected**: this node is BACKUP, so its adapter stays detached.

## 3. Configure the MASTER node

Repeat step 2 on the MASTER with the same shared MAC. Its logical interface
and adapter may differ from the BACKUP's.

The summary shows **Active · CARP MASTER · Waiting for DHCP** and then the
leased IPv4 address. OPNsense's own DHCP client now holds the lease on
`dhcpha0lagg`.

## 4. Watch it from the shell

On either node, read the controller's view without changing anything:

```sh
/usr/local/bin/python3 /usr/local/opnsense/scripts/dhcp_interface_ha/dhcp_interface_ha.py status --from-config
```

The MASTER reports its adapter attached; the BACKUP reports it fenced.

## 5. Look at the log

Open **Services → HA DHCP Interface → Settings → Log**. It lists the setup
steps and the controller's attach and fence events on this node.

## What you have built

The DHCP interface now belongs to whichever node is CARP MASTER. The
controller attaches the adapter only on the MASTER and fences it on the
BACKUP, while OPNsense keeps handling DHCP, routing, NAT and firewall rules.

Next steps:

- Give the BACKUP Internet access through the MASTER:
  [Enable standby Internet access](how-to.md#enable-standby-internet-access).
- Learn what the controller does and does not guarantee:
  [explanation](explanation.md).
- Before any real use, read the open
  [qualification items](reference.md#qualification-record).
