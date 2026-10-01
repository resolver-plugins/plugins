"""Standby routing at the existing controller/kernel boundary."""
import json
import unittest

import test_runtime as baseline
from runtime import DHCPHA_DEVICE


class StandbyRoutingTests(unittest.TestCase):
    setUp = baseline.RuntimeTests.setUp
    edit = baseline.RuntimeTests.edit
    assert_fenced = baseline.RuntimeTests.assert_fenced

    def test_reassigned_carrier_default_is_removed_before_standby_enablement(self):
        self.enable_standby()
        self.edit('<standby_enabled>1</standby_enabled>', '<standby_enabled>0</standby_enabled>')
        self.host.route = {'gateway': 'link#42', 'netif': 'hn1'}
        status = self.controller.reconcile()
        self.assertIsNone(self.host.route)
        self.assert_fenced()
        self.assertEqual(status['standby_internet']['state'], 'disabled')
        self.assertFalse(self.controller.standby_marker.exists())
        self.edit('<standby_enabled>0</standby_enabled>', '<standby_enabled>1</standby_enabled>')
        self.controller.reconcile()
        self.assertEqual(self.host.route, {'gateway': '192.168.10.1', 'netif': 'hn0'})

    def test_carrier_cleanup_preserves_unrelated_defaults_and_unverified_carrier(self):
        self.enable_standby()
        self.edit('<standby_enabled>1</standby_enabled>', '<standby_enabled>0</standby_enabled>')
        for route in [{'gateway': '10.0.0.1', 'netif': 'hn1'},
                      {'gateway': 'link#99', 'netif': 'hn1'},
                      {'gateway': 'link#42', 'netif': 'wg0'}]:
            with self.subTest(route=route):
                self.host.route = route.copy()
                self.controller.reconcile()
                self.assertEqual(self.host.route, route)
        self.host.route = {'gateway': 'link#42', 'netif': 'hn1'}
        self.host.items['hn1']['ipv4'] = [{'ipaddr': '198.51.100.3'}]
        self.controller.reconcile()
        self.assertEqual(self.host.route, {'gateway': 'link#42', 'netif': 'hn1'})

    def test_carrier_cleanup_preserves_concurrent_gateway_replacement(self):
        self.enable_standby()
        self.edit('<standby_enabled>1</standby_enabled>', '<standby_enabled>0</standby_enabled>')
        self.host.route = {'gateway': 'link#42', 'netif': 'hn1'}
        foreign = {'gateway': '10.0.0.1', 'netif': 'wg0'}
        original = self.controller.command
        def replace(argv):
            if argv[0] == '/sbin/route' and 'delete' in argv:
                self.host.route = foreign.copy()
            return original(argv)
        self.controller.command = replace
        self.controller.reconcile()
        self.assertEqual(self.host.route, foreign)
        self.assert_fenced()

    def test_carrier_cleanup_cancels_when_assignment_changes(self):
        self.enable_standby()
        self.edit('<standby_enabled>1</standby_enabled>', '<standby_enabled>0</standby_enabled>')
        self.host.route = {'gateway': 'link#42', 'netif': 'hn1'}
        original = self.controller.command
        def reassign(argv):
            if argv[0] == '/usr/bin/netstat':
                self.edit('<if>dhcpha0lagg</if>', '<if>hn1</if>')
            return original(argv)
        self.controller.command = reassign
        self.controller.reconcile()
        self.assertEqual(self.host.route, {'gateway': 'link#42', 'netif': 'hn1'})

    def enable_standby(self):
        self.edit('<lan><enable>1</enable><if>hn0</if></lan>',
                  '<lan><if>unused0</if></lan><opt2><enable>1</enable><if>hn0</if>'
                  '<ipaddr>192.168.10.3</ipaddr><subnet>24</subnet></opt2>')
        self.edit('<interface>lan</interface>', '<interface>opt2</interface>')
        self.edit('<vhid>10</vhid>', '<subnet>192.168.10.1</subnet><vhid>10</vhid>')
        self.edit('</DhcpInterfaceHaLocal>', '<standby_enabled>1</standby_enabled>'
                  '<standby_interface>opt2</standby_interface>'
                  '<standby_vip>192.168.10.1</standby_vip></DhcpInterfaceHaLocal>')
        self.host.items['hn0']['carp']['10']['status'] = 'BACKUP'
        self.host.items['hn0']['ipv4'] = [{'ipaddr': '192.168.10.3'}]
        self.host.route = {'gateway': 'link#42', 'netif': DHCPHA_DEVICE}
        original = self.host.command

        def command(argv):
            if argv[0] == '/usr/bin/netstat':
                entries = [] if self.host.route is None else [{
                    'destination': 'default', 'gateway': self.host.route['gateway'],
                    'interface-name': self.host.route['netif'], 'flags': 'UGS'}]
                return json.dumps({'statistics': {'route-information': {'route-table': {
                    'rt-family': [{'address-family': 'Internet', 'rt-entry': entries}]}}}})
            if argv[0] == '/sbin/route':
                self.host.commands.append(tuple(argv))
                if self.host.fail and self.host.fail(argv):
                    raise OSError('injected routing failure')
                if 'delete' in argv:
                    route = self.host.route
                    target = argv[argv.index('default') + 1:]
                    if route is None or (target != ['-interface', route['netif']]
                            and target != [route['gateway']]):
                        raise OSError('route has not been found')
                    self.host.route = None
                else:
                    self.host.route = {'gateway': argv[argv.index('default') + 1], 'netif': 'hn0'}
                self.host.after(argv)
                return ''
            if argv[0] == '/usr/local/bin/php':
                self.host.commands.append(tuple(argv))
                self.host.route = {'gateway': 'link#42', 'netif': DHCPHA_DEVICE}
                return ''
            return original(argv)

        self.controller.command = command
        self.controller.route_source = lambda: '192.168.10.3'

    def test_selected_internal_assignment_works_with_literal_lan_disabled(self):
        self.enable_standby()
        status = self.controller.reconcile()
        self.assert_fenced()
        self.assertEqual(self.host.route, {'gateway': '192.168.10.1', 'netif': 'hn0'})
        self.assertEqual(status['standby_internet']['state'], 'selected')
        self.assertEqual(status['standby_internet']['interface'], 'opt2')
        self.assertEqual(status['standby_internet']['source_address'], '192.168.10.3')

    def test_native_route_replacement_converges_and_steady_state_is_quiet(self):
        self.enable_standby()
        self.controller.reconcile()
        self.host.commands.clear()
        self.controller.routing()
        self.assertEqual(self.host.commands, [])
        self.host.route = {'gateway': 'link#42', 'netif': DHCPHA_DEVICE}
        self.controller.routing()
        self.assertEqual(self.host.route['gateway'], '192.168.10.1')
        self.assert_fenced()

    def test_promotion_releases_standby_route_before_attaching(self):
        self.enable_standby()
        self.controller.reconcile()
        def check(argv):
            if 'laggport' in argv:
                self.assertNotEqual((self.host.route or {}).get('gateway'), '192.168.10.1')
        self.host.after = check
        self.host.items['hn0']['carp']['10']['status'] = 'MASTER'
        status = self.controller.reconcile()
        self.assertEqual(status['actual_attachment'], 'ATTACHED')
        self.assertFalse(self.controller.standby_marker.exists())
        self.assertEqual(status['standby_internet']['state'], 'inactive')

    def test_failed_cleanup_blocks_promotion_but_install_failure_does_not_demote(self):
        self.enable_standby()
        self.host.fail = lambda argv: argv[0] == '/sbin/route'
        status = self.controller.reconcile()
        self.assertEqual(status['state'], 'STANDBY')
        self.assertTrue(self.controller.health())
        self.host.fail = None
        self.controller.reconcile()
        self.host.items['hn0']['carp']['10']['status'] = 'MASTER'
        self.host.fail = lambda argv: argv[0] == '/sbin/route' and 'delete' in argv
        with self.assertRaisesRegex(RuntimeError, 'fenced'):
            self.controller.reconcile()
        self.assert_fenced()
        self.assertTrue(self.controller.standby_marker.exists())
        self.host.fail = None
        self.assertEqual(self.controller.reconcile()['actual_attachment'], 'ATTACHED')

    def test_unknown_role_withdraws_and_recalculates_native_default(self):
        self.enable_standby()
        self.controller.reconcile()
        self.host.items['hn0']['carp']['10']['status'] = 'INIT'
        self.controller.reconcile()
        self.assert_fenced()
        self.assertEqual(self.host.route['netif'], DHCPHA_DEVICE)
        self.assertFalse(self.controller.standby_marker.exists())

    def test_unrelated_replacement_is_preserved_on_stop(self):
        self.enable_standby()
        self.controller.reconcile()
        foreign = {'gateway': '10.0.0.1', 'netif': 'wg0'}
        self.host.route = foreign.copy()
        self.host.commands.clear()
        self.controller.fence(stop=True)
        self.assertEqual(self.host.route, foreign)
        self.assertFalse(any(cmd[0] in ('/sbin/route', '/usr/local/bin/php') for cmd in self.host.commands))

    def test_source_readback_failure_restores_native_routing(self):
        self.enable_standby()
        self.controller.route_source = lambda: '203.0.113.9'
        status = self.controller.reconcile()
        self.assert_fenced()
        self.assertEqual(status['standby_internet']['state'], 'unavailable')
        self.assertEqual(self.host.route, {'gateway': 'link#42', 'netif': DHCPHA_DEVICE})

    def test_mixed_carp_state_cannot_select_standby_even_when_reducer_reports_backup(self):
        self.enable_standby()
        self.controller.reconcile()
        self.edit('</virtualip>', '<vip><mode>carp</mode><interface>opt2</interface>'
                  '<subnet>192.168.10.2</subnet><vhid>20</vhid></vip></virtualip>')
        self.host.items['hn0']['carp']['20'] = {'status': 'MASTER'}
        status = self.controller.reconcile()
        self.assertEqual(status['global_role'], 'BACKUP')
        self.assert_fenced()
        self.assertNotEqual(self.host.route['gateway'], '192.168.10.1')

    def test_role_change_during_route_install_cancels_and_releases_owned_path(self):
        self.enable_standby()
        def change(argv):
            if argv[0] == '/sbin/route' and 'add' in argv:
                self.host.items['hn0']['carp']['10']['status'] = 'MASTER'
        self.host.after = change
        self.controller.reconcile()
        self.assert_fenced()
        self.assertFalse(self.controller.standby_marker.exists())
        self.assertEqual(self.host.route['netif'], DHCPHA_DEVICE)

    def test_source_drift_is_reconciled_after_initial_selection(self):
        self.enable_standby()
        self.controller.reconcile()
        self.controller.route_source = lambda: '203.0.113.9'
        self.controller.reconcile()
        self.assertEqual(self.host.route['netif'], DHCPHA_DEVICE)
        self.assertFalse(self.controller.standby_marker.exists())

    def test_foreign_default_replacing_native_route_at_delete_is_preserved(self):
        self.enable_standby()
        original = self.controller.command
        foreign = {'gateway': '10.0.0.1', 'netif': 'wg0'}
        def command(argv):
            if argv[0] == '/sbin/route' and 'delete' in argv:
                self.host.route = foreign.copy()
            return original(argv)
        self.controller.command = command
        self.controller.reconcile()
        self.assertEqual(self.host.route, foreign)
        self.assert_fenced()

    def test_foreign_default_installed_after_withdrawal_is_preserved(self):
        self.enable_standby()
        self.controller.reconcile()
        foreign = {'gateway': '10.0.0.1', 'netif': 'wg0'}
        self.host.after = lambda argv: setattr(self.host, 'route', foreign.copy())
        self.controller.fence(stop=True)
        self.assertEqual(self.host.route, foreign)
        self.assertFalse(self.controller.standby_marker.exists())

    def test_self_route_inserted_during_promotion_blocks_attachment(self):
        self.enable_standby()
        self.controller.reconcile()
        self.host.items['hn0']['carp']['10']['status'] = 'MASTER'
        def insert(argv):
            if 'promisc' in argv:
                self.host.route = {'gateway': '192.168.10.1', 'netif': 'hn0'}
        self.host.after = insert
        with self.assertRaisesRegex(RuntimeError, 'locally owned'):
            self.controller.reconcile()
        self.assert_fenced()

    def test_active_attachment_survives_unavailable_routing_observation(self):
        self.enable_standby()
        self.controller.reconcile()
        self.host.items['hn0']['carp']['10']['status'] = 'MASTER'
        self.controller.reconcile()
        self.controller.standby_marker.write_text(json.dumps({'gateway': '192.168.10.1', 'netif': 'hn0'}))
        original = self.controller.command
        def unavailable(argv):
            if argv[0] == '/usr/bin/netstat':
                raise OSError('route observation unavailable')
            return original(argv)
        self.controller.command = unavailable
        self.assertEqual(self.controller.reconcile()['actual_attachment'], 'ATTACHED')
        self.assertTrue(self.controller.health())

    def test_unreachable_source_releases_owned_path_on_install_and_later_drift(self):
        self.enable_standby()
        def unreachable():
            raise OSError('ENETUNREACH')
        self.controller.route_source = unreachable
        self.controller.reconcile()
        self.assertEqual(self.host.route['netif'], DHCPHA_DEVICE)
        self.assertFalse(self.controller.standby_marker.exists())
        self.controller.route_source = lambda: '192.168.10.3'
        self.controller.reconcile()
        self.controller.route_source = unreachable
        self.controller.reconcile()
        self.assertEqual(self.host.route['netif'], DHCPHA_DEVICE)
        self.assertFalse(self.controller.standby_marker.exists())
