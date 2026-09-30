"""Non-verbose FreeBSD inventory at the controller command boundary."""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/opnsense/scripts/dhcp_interface_ha'))
from runtime import Controller, carrier_error, interface_snapshot


IFCONFIG = '''ix0: flags=8802<BROADCAST,SIMPLEX,MULTICAST> metric 0 mtu 1500
\toptions=0
\tcapabilities=0
\tether 02:11:22:33:44:55
\thwaddr 00:11:22:33:44:01
\tmedia: Ethernet autoselect (10Gbase-SR <full-duplex>)
\tstatus: active
ix1: flags=8943<UP,BROADCAST,RUNNING,PROMISC,SIMPLEX,MULTICAST> metric 0 mtu 1500
\tether 00:11:22:33:44:02
\tinet 192.0.2.1 netmask 0xffffff00 broadcast 192.0.2.255 vhid 10
\tinet6 fe80::1%ix1 prefixlen 64 scopeid 0x2
\tcarp: MASTER vhid 10 advbase 1 advskew 0
\tcarp: BACKUP vhid 11 advbase 1 advskew 100
\tstatus: active
dhcpha0lagg: flags=8943<UP,BROADCAST,RUNNING,PROMISC,SIMPLEX,MULTICAST> metric 0 mtu 1500
\tether 02:11:22:33:44:55
\tinet 198.51.100.10 netmask 0xffffff00 broadcast 198.51.100.255
\tgroups: lagg wh0123456789abx
\tlaggproto failover lagghash l2,l3,l4
\tlaggport: ix0 flags=5<MASTER,ACTIVE>
\tstatus: active
vlan7: flags=8843<UP,BROADCAST,RUNNING,SIMPLEX,MULTICAST> metric 0 mtu 1500
\tether 00:11:22:33:44:02
\tvlan: 7 vlanproto: 802.1q vlanpcp: 0 parent interface: ix1
bridge0: flags=8843<UP,BROADCAST,RUNNING,SIMPLEX,MULTICAST> metric 0 mtu 1500
\tether 00:11:22:33:44:03
\tmember: ix1 flags=143<LEARNING,DISCOVER,AUTOEDGE,AUTOPTP>
\t        ifmaxaddr 0 port 2 priority 128 path cost 2000
lo0: flags=8049<UP,LOOPBACK,RUNNING,MULTICAST> metric 0 mtu 16384
\tinet 127.0.0.1 netmask 0xff000000
'''


class InventoryTests(unittest.TestCase):
    def test_inventory_uses_one_fresh_nonverbose_read(self):
        def command(argv):
            self.assertEqual(argv, ['/sbin/ifconfig', '-Lm'])
            return IFCONFIG

        controller = Controller(command=Mock(side_effect=command))
        first = controller.inventory()
        second = controller.inventory()
        self.assertEqual(controller.command.call_count, 2)
        self.assertEqual(first, second)
        self.assertIsNot(first, second)
        self.assertEqual(controller._last_command, 'interface_inventory')

    def test_safety_fields_survive_nonverbose_output(self):
        items = Controller(command=lambda _: IFCONFIG).inventory()
        self.assertEqual(items['ix0']['macaddr_hw'], '00:11:22:33:44:01')
        self.assertEqual(items['ix1']['macaddr_hw'], items['ix1']['macaddr'])
        self.assertEqual(items['ix1']['carp'], {
            '10': {'status': 'MASTER'}, '11': {'status': 'BACKUP'},
        })
        self.assertEqual(items['ix1']['ipv6'], [{'ipaddr': 'fe80::1%ix1'}])
        self.assertEqual(items['dhcpha0lagg']['ipv4'], [{'ipaddr': '198.51.100.10'}])
        self.assertEqual(items['dhcpha0lagg']['groups'], ['lagg', 'wh0123456789abx'])
        snapshot = interface_snapshot('dhcpha0lagg', items)
        self.assertTrue(snapshot.up and snapshot.promiscuous and snapshot.link_up)
        self.assertEqual(snapshot.lagg_protocol, 'failover')
        self.assertEqual(snapshot.lagg_members, ('ix0',))
        self.assertEqual(snapshot.mtu, 1500)
        self.assertFalse(interface_snapshot('ix0', items).up)
        for name in ('dhcpha0lagg', 'vlan7', 'bridge0', 'lo0'):
            self.assertFalse(items[name]['is_physical'], name)
        self.assertTrue(items['ix0']['is_physical'])

    def test_runtime_topology_still_reserves_otherwise_usable_carriers(self):
        for row, reason in [
            ('vlan: 7 vlanproto: 802.1q vlanpcp: 0 parent interface: ix0', 'runtime bridge or VLAN'),
            ('member: ix0 flags=143<LEARNING,DISCOVER>', 'runtime bridge or VLAN'),
            ('laggport: ix0 flags=5<MASTER,ACTIVE>', 'another runtime LAGG'),
        ]:
            with self.subTest(row=row):
                text = IFCONFIG.split('ix1:', 1)[0] + '\nother0: flags=0 metric 0 mtu 1500\n\t' + row
                items = Controller(command=lambda _: text).inventory()
                self.assertIn(reason, carrier_error(ET.fromstring('<opnsense/>'), 'ix0', items))
        for row in ('tunnel inet 192.0.2.1 --> 192.0.2.2', 'vxlan vni 1 local 192.0.2.1 group 239.0.0.1'):
            with self.subTest(row=row):
                text = IFCONFIG.split('ix1:', 1)[0] + '\t' + row
                items = Controller(command=lambda _: text).inventory()
                self.assertIn('tunnel', carrier_error(ET.fromstring('<opnsense/>'), 'ix0', items))

    def test_malformed_safety_observations_are_not_silently_dropped(self):
        header = 'ix0: flags=8802<BROADCAST,SIMPLEX,MULTICAST> metric 0 mtu 1500\n'
        for text in [
            '', '\tether 00:11:22:33:44:01', 'ix0: flags=8802 metric 0 mtu 1500',
            header + header, header + '\tether invalid', header + '\thwaddr invalid',
            header + '\tinet', header + '\tgroups:', header + '\tstatus:',
            header + '\tlaggproto', header + '\tlaggport: ix1',
            header + '\tmember: ix1', header + '\tvlan: 7',
            header + '\tlaggport ix1 flags=5<MASTER,ACTIVE>',
            header + '\tcarp: MASTER',
            header + '\tcarp: MASTER vhid 10\n\tcarp: BACKUP vhid 10',
        ]:
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    Controller(command=lambda _: text).inventory()

    def test_inventory_command_failures_keep_the_operation_category(self):
        controller = Controller(command=Mock(side_effect=TimeoutError('inventory timed out')))
        with self.assertRaises(TimeoutError):
            controller.inventory()
        self.assertEqual(controller._failed_command, 'interface_inventory')


if __name__ == '__main__':
    unittest.main()
