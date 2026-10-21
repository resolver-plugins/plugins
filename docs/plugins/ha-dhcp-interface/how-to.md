# os-ha-dhcp-interface how-to guides

Task-focused steps for `os-ha-dhcp-interface`. The plugin is experimental;
see the [explanation](explanation.md) for its limits and the
[reference](reference.md) for every setting and command.

## Retry a partial setup

1. Keep the carrier mapping that setup saved.
2. Refresh the page and review **Diagnostics**. A timeout reports `UNKNOWN`
   and does not prove that the step failed; the firewall may still be
   applying it.
3. Click **Save & Apply** again and confirm the retry.

If the interface was already moved to `dhcpha0lagg` and no carrier was saved,
restore its original device in **Interfaces → Assignments** while the plugin
is disabled, then repeat setup. This needs the native assignment privileges.

Setup refuses to run while unrelated assignment edits are pending; finish or
discard them first, and do not edit interfaces at the same time.

## Include the plugin in configuration sync

On a node that already has an XMLRPC sync destination, use the sync action in
**Settings** to add this plugin to native configuration sync. It does not
contact the peer or restart services. Only the shared settings, **Enable** and
**Shared interface MAC**, are synchronized; each node keeps its own interface
and carrier.

## Enable standby Internet access

The BACKUP node can reach the Internet through the MASTER while its own WAN
is fenced. Configure each node separately.

1. On both nodes, allow ingress from the peer's internal IPv4 address and add
   outbound NAT to the current DHCP WAN address. Reuse adequate existing
   rules.
2. Check that policy routing on the chosen internal network allows the
   Internet path. A VPN-routed network gives different results from a
   management network.
3. In **Settings**, select **Standby Internet access**.
4. Choose a **Standby internal interface** with a static IPv4 address. Any
   suitable assignment works, including a VLAN.
5. Choose its existing **Standby gateway CARP VIP**.
6. Click **Save & Apply**. It verifies the selected path.

Services bound to the fenced WAN or its former address need native
source-binding settings that also work from the internal address.

To change the interface or VIP later, disable standby access, save, check
that the route is gone, and then select the new values.

## Keep WireGuard from running on both nodes

With standby Internet access, a BACKUP node can reach WireGuard peers too.

1. In each synchronized WireGuard instance, set the native CARP dependency to
   a valid, existing CARP VIP, on both nodes.
2. Check that the tunnel interfaces are UP on the MASTER and DOWN on the
   BACKUP.

A dependency on a deleted VIP leaves both copies UP, and both nodes then
contact the remote peer with the same identity.

## Upgrade the plugin

1. Upgrade the BACKUP node:

   ```sh
   pkg upgrade os-ha-dhcp-interface
   service dhcp_interface_ha onestop
   service dhcp_interface_ha onestart
   ```

2. Fail over deliberately, so the upgraded node becomes MASTER.
3. Upgrade the former MASTER the same way.

The upgrade keeps the device and configuration. The restart loads the new
code and fences the WAN on that node, which is why the MASTER restarts only
after a planned failover.

## Replace os-dhcp-interface-ha

The plugin was published as `os-dhcp-interface-ha` (and `-devel`) up to
`26.7_3`. The new name conflicts with the old one, so `pkg upgrade` does not
switch. On the BACKUP node first:

```sh
pkg install os-ha-dhcp-interface
service dhcp_interface_ha onestop
service dhcp_interface_ha onestart
```

Then fail over and repeat on the other node. Settings carry over, and the
assigned device and running controller stay in place. Old revisions up to
`0.2_45` print a refusal and `DEINSTALL script failed`; with `dhcpha0lagg`
assigned they stop before changing anything and `pkg` continues.

## Read the status without changing anything

```sh
/usr/local/bin/python3 /usr/local/opnsense/scripts/dhcp_interface_ha/dhcp_interface_ha.py status --from-config
```

## Fence or restart the controller

```sh
service dhcp_interface_ha onestop
service dhcp_interface_ha onestart
```

Stopping fences the managed interface and keeps this node from being
promoted until the service starts again. A failed fence is an error, not a
successful stop: inspect the live LAGG and member state before continuing.

## Clear the plugin setup

1. If the plugin is enabled, clear **Enable HA DHCP Interface**, click **Save
   & Apply** and check that the carrier is detached.
2. Choose **Disabled** in **Managed logical interface** and click **Save &
   Apply** again.

This restores the native assignment and receive mode, clears the managed
interface and carrier, leaves the plugin disabled and resets the failback
delay to zero. The shared MAC is kept. Changing the selection without saving
changes only the form.

## Uninstall

1. Clear the setup as above, so no logical interface uses `dhcpha0lagg`.
2. Remove the package:

   ```sh
   pkg remove os-ha-dhcp-interface
   ```

`pkg` removes the files even when the removal script fails. If an interface
is still assigned to `dhcpha0lagg`, the device and controller are left in
place with a warning.

## Run the test suites

Maintainers run the suites locally; CI runs no tests.

```sh
python3 -m compileall -q net/ha-dhcp-interface/src/opnsense/scripts/dhcp_interface_ha
python3 -m unittest discover -s net/ha-dhcp-interface/tests -v
node net/ha-dhcp-interface/tests/ui/test_configure_lagg.js
node net/ha-dhcp-interface/tests/ui/test_log.js
```

The hook and PHP fixture tests need the PHP CLI with the SimpleXML extension.
