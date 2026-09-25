"""Controller boundary tests: model native side effects, not just command plans."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/opnsense/scripts/wan_ha_dhcp'))
from runtime import Controller, WANHA_DEVICE

MAC = '02:11:22:33:44:55'
CONFIG = '''<opnsense><interfaces>
<wan><enable>1</enable><if>wanha0lagg</if><ipaddr>dhcp</ipaddr><mtu>1400</mtu></wan>
<lan><enable>1</enable><if>hn0</if></lan></interfaces>
<virtualip><vip><mode>carp</mode><interface>lan</interface><vhid>10</vhid></vip></virtualip>
<OPNsense><WanHaDhcpShared><enabled>1</enabled><managed_interface>wan</managed_interface>
<shared_mac>02:11:22:33:44:55</shared_mac><failback_delay>0</failback_delay></WanHaDhcpShared>
<WanHaDhcpLocal><carrier>hn1</carrier></WanHaDhcpLocal></OPNsense></opnsense>'''


class Host:
    def __init__(self):
        self.items = {
            'hn0': dict(flags=['up'], macaddr='00:11:22:33:44:00', carp={'10': {'status': 'MASTER'}}),
            'hn1': dict(flags=[], is_physical=True, macaddr='00:11:22:33:44:01', mtu='1500', status='active'),
            WANHA_DEVICE: dict(flags=[], laggproto='failover', laggport={}, groups=['wh0123456789abx'], mtu='1500', macaddr='00:00:00:00:00:00'),
        }
        self.commands = []
        self.after = lambda argv: None
        self.fail = None
        self.ignore = None

    def command(self, argv):
        if argv[0].endswith('pluginctl'):
            return json.dumps(self.items)
        if argv[0].endswith('sysctl'):
            return '1'
        self.commands.append(tuple(argv))
        if self.fail and self.fail(argv):
            raise OSError('injected command failure')
        device, action, *args = argv[1:]
        if self.ignore and self.ignore(argv):
            return ''
        if action == 'create':
            self.items['lagg9'] = dict(flags=[], laggport={}, mtu='1500')
            return 'lagg9'
        item = self.items[device]
        if action == 'name':
            self.items[args[0]] = self.items.pop(device)
        elif action == 'destroy':
            del self.items[device]
        elif action == 'group':
            item.setdefault('groups', []).append(args[0])
        elif action == 'laggproto':
            item['laggproto'] = args[0]
        elif action in ('up', 'down'):
            item['flags'] = ['up'] if action == 'up' else []
            for member in item.get('laggport', {}):
                self.items[member]['flags'] = list(item['flags'])
        elif action == 'ether':
            item['macaddr'] = args[0]
        elif action == 'mtu':
            item['mtu'] = args[0]
        elif action == 'laggport':
            member = self.items[args[0]]
            assert not member['flags'], 'unsafe live attachment'
            assert member['macaddr'] == MAC, 'unsafe original-MAC attachment'
            item['laggport'][args[0]] = {}
            item['macaddr'] = member['macaddr']
            item['mtu'] = member['mtu']
        elif action == '-laggport':
            assert not self.items[args[0]]['flags'], 'unsafe live detach'
            del item['laggport'][args[0]]
        else:
            raise AssertionError(argv)
        self.after(argv)
        return ''


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.config = self.directory / 'config.xml'
        self.config.write_text(CONFIG)
        self.host = Host()
        self.controller = Controller(self.config, self.directory, self.host.command, lambda _: 42)
        self.controller.marker.write_text(json.dumps({'index': 42, 'group': 'wh0123456789abx'}))

    def edit(self, old, new):
        self.config.write_text(self.config.read_text().replace(old, new))

    def assert_fenced(self):
        self.assertEqual(self.host.items[WANHA_DEVICE]['laggport'], {})
        self.assertNotIn('up', self.host.items['hn1']['flags'])

    def test_active_round_trip_preserves_shared_identity_and_is_idempotent(self):
        result = self.controller.reconcile()
        self.assertEqual(result['actual_attachment'], 'ATTACHED')
        self.assertEqual(self.host.items['hn1']['mtu'], '1400')
        self.host.commands.clear()
        self.controller.reconcile()
        self.assertEqual(self.host.commands, [])
        self.host.items['hn0']['carp']['10']['status'] = 'BACKUP'
        self.controller.reconcile()
        self.assert_fenced()
        self.assertEqual(self.host.items['hn1']['macaddr'], MAC)
        # Role-independent health lets a recovered BACKUP remain eligible.
        self.assertTrue(self.controller.health())

    def test_config_and_role_changes_cancel_before_activation(self):
        for change in ('config', 'role'):
            with self.subTest(change=change):
                self.setUp()
                def changed(argv):
                    if argv[2] == 'laggport':
                        if change == 'config':
                            self.edit('<enabled>1', '<enabled>0')
                        else:
                            self.host.items['hn0']['carp']['10']['status'] = 'BACKUP'
                self.host.after = changed
                with self.assertRaisesRegex(RuntimeError, 'cancelled'):
                    self.controller.reconcile()
                self.assert_fenced()
                self.assertNotIn(('/sbin/ifconfig', WANHA_DEVICE, 'up'), self.host.commands)

    def test_each_promotion_failure_fences_without_claiming_success(self):
        for action in ('down', 'ether', 'mtu', 'laggport', 'up'):
            with self.subTest(action=action):
                self.setUp()
                fired = False
                def fail(argv):
                    nonlocal fired
                    if argv[2] == action and not fired:
                        fired = True
                        return True
                    return False
                self.host.fail = fail
                with self.assertRaisesRegex(RuntimeError, 'owned attachment fenced'):
                    self.controller.reconcile()
                self.assert_fenced()

    def test_success_exit_without_mac_readback_never_attaches(self):
        self.host.ignore = lambda argv: argv[2] == 'ether'
        with self.assertRaisesRegex(RuntimeError, 'MAC/down verification'):
            self.controller.reconcile()
        self.assert_fenced()

    def test_invalid_configuration_and_inventory_block_promotion(self):
        for mutation in ('collision', 'vlan', 'assigned', 'delay', 'mtu', 'missing_carp', 'address'):
            with self.subTest(mutation=mutation):
                self.setUp()
                if mutation == 'collision': self.host.items['hn0']['macaddr'] = MAC
                if mutation == 'vlan': self.host.items['hn1']['vlan'] = {'parent': 'hn0'}
                if mutation == 'assigned': self.edit('<if>hn0</if>', '<if>hn1</if>')
                if mutation == 'delay': self.edit('<failback_delay>0', '<failback_delay>120')
                if mutation == 'mtu': self.edit('<mtu>1400', '<mtu>invalid')
                if mutation == 'missing_carp': self.host.items['hn0']['carp'] = {}
                if mutation == 'address': self.host.items['hn1']['ipv4'] = [{'ipaddr': '192.0.2.1'}]
                self.assertIsNotNone(self.controller.snapshot().local_error)
                self.controller.reconcile()
                self.assert_fenced()
                self.assertFalse(any(c[2] == 'laggport' for c in self.host.commands))

    def test_reassignment_and_unreadable_config_still_fence_owned_attachment(self):
        for config in (CONFIG.replace('<if>wanha0lagg</if>', '<if>hn0</if>'), '<broken'):
            with self.subTest(config=config[:20]):
                self.setUp()
                self.controller.reconcile()
                self.config.write_text(config)
                try:
                    self.controller.reconcile()
                except RuntimeError:
                    pass
                self.assert_fenced()

    def test_stop_inhibits_periodic_promotion_until_explicit_resume(self):
        self.controller.reconcile()
        self.controller.fence(stop=True)
        self.controller.reconcile()
        self.assert_fenced()
        self.assertFalse(self.controller.health())
        self.controller.resume()
        self.controller.reconcile()
        self.assertEqual(self.host.items[WANHA_DEVICE]['laggport'], {'hn1': {}})

    def test_failed_stop_stays_inhibited_and_never_claims_verified_fence(self):
        self.controller.reconcile()
        self.host.fail = lambda argv: argv[2] in ('down', '-laggport')
        with self.assertRaisesRegex(RuntimeError, 'fencing failed'):
            self.controller.fence(stop=True)
        self.assertTrue(self.controller.stopped.exists())
        with self.assertRaises(RuntimeError):
            self.controller.health()
        self.host.fail = None
        self.controller.reconcile()
        self.assert_fenced()

    def test_unknown_same_name_device_is_untouched_in_all_mutations(self):
        self.host.items[WANHA_DEVICE]['groups'] = []  # replacement reuses index42
        for operation in (self.controller.prepare, self.controller.reconcile, self.controller.fence):
            with self.assertRaises(RuntimeError): operation()
        self.edit('<if>wanha0lagg</if>', '<if>hn0</if>')
        with self.assertRaises(RuntimeError): self.controller.remove()
        self.assertEqual(self.host.commands, [])

    def test_prepare_and_remove_verify_ownership_and_assignment(self):
        self.host.items.pop(WANHA_DEVICE)
        self.controller.marker.unlink()
        self.controller.prepare()
        self.assertTrue(self.controller.owned())
        with self.assertRaisesRegex(RuntimeError, 'reassign'):
            self.controller.remove()
        self.edit('<if>wanha0lagg</if>', '<if>hn0</if>')
        self.controller.remove()
        self.assertNotIn(WANHA_DEVICE, self.host.items)
        self.assertFalse(self.controller.marker.exists())
        self.assertTrue((self.directory / 'transition.lock').exists())

    def test_unset_mtu_preserves_carrier_value(self):
        self.edit('<mtu>1400</mtu>', '')
        self.host.items['hn1']['mtu'] = '9000'
        self.controller.reconcile()
        self.assertEqual(self.host.items[WANHA_DEVICE]['mtu'], '9000')
        self.assertFalse(any(c[2] == 'mtu' for c in self.host.commands))

    def test_all_stale_members_are_silenced_before_detach(self):
        self.controller.reconcile()
        self.host.items['hn2'] = dict(flags=['up'], macaddr='00:11:22:33:44:02')
        self.host.items[WANHA_DEVICE]['laggport']['hn2'] = {}
        self.controller.fence()
        self.assert_fenced()
        self.assertNotIn('up', self.host.items['hn2']['flags'])

    def test_detached_carrier_down_failure_is_not_reported_as_fenced(self):
        self.host.items['hn1']['flags'] = ['up']
        self.host.fail = lambda argv: argv[1:3] == ['hn1', 'down']
        with self.assertRaises(OSError): self.controller.fence(stop=True)
        self.assertEqual(self.controller.status()['actual_attachment'], 'UNVERIFIED')

    def test_stale_member_moved_elsewhere_is_not_forced_down(self):
        self.host.items['hn2'] = dict(flags=['up'], macaddr='00:11:22:33:44:02')
        self.host.items[WANHA_DEVICE]['laggport']['hn2'] = {}
        def change(argv):
            if argv[1:3] == [WANHA_DEVICE, 'down']:
                self.host.items[WANHA_DEVICE]['laggport'].pop('hn2', None)
                self.host.items['hn2']['flags'] = ['up']
        self.host.after = change
        with self.assertRaisesRegex(RuntimeError, 'old member'):
            self.controller.reconcile()
        self.assertNotIn(('/sbin/ifconfig', 'hn2', 'down'), self.host.commands)

    def test_busy_lock_does_not_allow_concurrent_transition(self):
        other = Controller(self.config, self.directory, self.host.command, lambda _: 42)
        with self.controller.locked(), patch('runtime.LOCK_TIMEOUT', 0):
            with self.assertRaises(TimeoutError): other.reconcile()
        self.assertEqual(self.host.commands, [])

    def test_local_link_health_fences_before_reporting_incapable(self):
        self.controller.reconcile()
        self.host.items['hn1']['status'] = 'no carrier'
        self.assertFalse(self.controller.health())
        self.assert_fenced()
        self.host.items['hn1']['status'] = 'active'
        self.assertTrue(self.controller.health())


if __name__ == '__main__':
    unittest.main()
