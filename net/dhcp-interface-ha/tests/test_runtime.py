"""Controller boundary tests: model native side effects, not just command plans."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/opnsense/scripts/dhcp_interface_ha'))
import runtime
from runtime import Controller, DHCPHA_DEVICE

MAC = '02:11:22:33:44:55'
CONFIG = '''<opnsense><interfaces>
<wan><enable>1</enable><if>dhcpha0lagg</if><ipaddr>dhcp</ipaddr><mtu>1400</mtu></wan>
<lan><enable>1</enable><if>hn0</if></lan></interfaces>
<virtualip><vip><mode>carp</mode><interface>lan</interface><vhid>10</vhid></vip></virtualip>
<OPNsense><DhcpInterfaceHaShared><enabled>1</enabled>
<shared_mac>02:11:22:33:44:55</shared_mac><failback_delay>0</failback_delay></DhcpInterfaceHaShared>
<DhcpInterfaceHaLocal><managed_interface>wan</managed_interface><carrier>hn1</carrier></DhcpInterfaceHaLocal></OPNsense></opnsense>'''


class Host:
    def __init__(self):
        self.items = {
            'hn0': dict(flags=['up'], macaddr='00:11:22:33:44:00', carp={'10': {'status': 'MASTER'}}),
            'hn1': dict(flags=[], is_physical=True, macaddr='00:11:22:33:44:01', mtu='1500', status='active'),
            DHCPHA_DEVICE: dict(flags=[], laggproto='failover', laggport={}, groups=['wh0123456789abx'], mtu='1500', macaddr='00:00:00:00:00:00'),
        }
        self.commands = []
        self.after = lambda argv: None
        self.fail = None
        self.ignore = None

    def command(self, argv):
        if argv == ['/sbin/ifconfig', '-Lm']:
            return self.ifconfig()
        if argv[0] == '/usr/bin/netstat':
            return json.dumps({'statistics': {'route-information': {'route-table': {
                'rt-family': [{'address-family': 'Internet', 'rt-entry': []}]}}}})
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
            item['flags'] = [flag for flag in item['flags'] if flag != 'up']
            if action == 'up':
                item['flags'].append('up')
            for member in item.get('laggport', {}):
                self.items[member]['flags'] = list(item['flags'])
        elif action == 'promisc':
            if 'promisc' not in item['flags']:
                item['flags'].append('promisc')
            for name in item.get('laggport', {}):
                if 'promisc' not in self.items[name]['flags']:
                    self.items[name]['flags'].append('promisc')
        elif action == 'ether':
            item['macaddr'] = args[0]
        elif action == 'mtu':
            item['mtu'] = args[0]
        elif action == 'laggport':
            member = self.items[args[0]]
            assert 'up' not in member['flags'], 'unsafe live attachment'
            assert member['macaddr'] == MAC, 'unsafe original-MAC attachment'
            item['laggport'][args[0]] = {}
            item['macaddr'] = member['macaddr']
            item['mtu'] = member['mtu']
            if 'promisc' in item['flags']:
                member['flags'].append('promisc')
        elif action == '-laggport':
            assert 'up' not in self.items[args[0]]['flags'], 'unsafe live detach'
            del item['laggport'][args[0]]
            self.items[args[0]]['flags'] = [f for f in self.items[args[0]]['flags'] if f != 'promisc']
        else:
            raise AssertionError(argv)
        self.after(argv)
        return ''

    def ifconfig(self):
        """Model kernel observations in FreeBSD's non-verbose wire format."""
        lines = []
        for name, item in self.items.items():
            flags = ','.join(flag.upper() for flag in item['flags'])
            lines.append(f"{name}: flags=1<{flags}> metric 0 mtu {item.get('mtu', '1500')}")
            for key, field in [('ether', 'macaddr'), ('hwaddr', 'macaddr_hw')]:
                if field in item:
                    lines.append(f"\t{key} {item[field]}")
            for family, key in [('ipv4', 'inet'), ('ipv6', 'inet6')]:
                for address in item.get(family, []):
                    lines.append(f"\t{key} {address['ipaddr']}")
            if item.get('groups'):
                lines.append('\tgroups: ' + ' '.join(item['groups']))
            for key in ('status', 'laggproto'):
                if key in item:
                    label = key + ':' if key == 'status' else key
                    lines.append(f"\t{label} {item[key]}")
            for field, key in [('laggport', 'laggport'), ('members', 'member')]:
                for member in item.get(field, {}):
                    lines.append(f"\t{key}: {member} flags=0<>")
            for vhid, carp in item.get('carp', {}).items():
                lines.append(f"\tcarp: {carp['status']} vhid {vhid} advbase 1 advskew 0")
            if item.get('vlan'):
                lines.append('\tvlan: 7 vlanproto: 802.1q vlanpcp: 0 parent interface: ' + item['vlan']['parent'])
        return '\n'.join(lines)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.config = self.directory / 'config.xml'
        self.config.write_text(CONFIG)
        self.host = Host()
        self.events = []
        self.controller = Controller(
            self.config, self.directory, self.host.command, lambda _: 42,
            event_sink=lambda severity, message: self.events.append((severity, message)),
        )
        (self.directory / 'transition.lock').touch()
        (self.directory / 'controller.pid').write_text(str(os.getpid()))
        self.controller.marker.write_text(json.dumps({'index': 42, 'group': 'wh0123456789abx'}))

    def edit(self, old, new):
        self.config.write_text(self.config.read_text().replace(old, new))

    def assert_fenced(self):
        self.assertEqual(self.host.items[DHCPHA_DEVICE]['laggport'], {})
        self.assertNotIn('up', self.host.items['hn1']['flags'])

    def event_messages(self, code):
        return [message for _, message in self.events if message.startswith(f'event={code} ')]

    def test_non_wan_dhcp_interface_uses_same_controller(self):
        self.edit('<wan>', '<opt1>')
        self.edit('</wan>', '</opt1>')
        self.edit('<managed_interface>wan</managed_interface>',
                  '<managed_interface>opt1</managed_interface>')
        status = self.controller.reconcile()
        self.assertEqual(status['actual_attachment'], 'ATTACHED')
        self.assertEqual(self.host.items[DHCPHA_DEVICE]['laggport'], {'hn1': {}})

    def test_identical_shared_settings_allow_different_local_mappings(self):
        for logical, carrier in [('opt7', 'hn1'), ('opt2', 'ix3')]:
            with self.subTest(logical=logical, carrier=carrier):
                self.config.write_text(CONFIG.replace('<wan>', f'<{logical}>')
                    .replace('</wan>', f'</{logical}>')
                    .replace('<managed_interface>wan<', f'<managed_interface>{logical}<')
                    .replace('<carrier>hn1<', f'<carrier>{carrier}<'))
                self.host = Host()
                self.host.items[carrier] = self.host.items.pop('hn1')
                self.controller.command = self.host.command
                status = self.controller.reconcile()
                self.assertEqual(status['state'], 'ACTIVE')
                self.assertEqual(self.host.items[DHCPHA_DEVICE]['laggport'], {carrier: {}})
                self.assertEqual(self.host.items[carrier]['macaddr'], MAC)

    def test_shared_sync_cannot_retarget_local_interface(self):
        # A legacy sender may still transmit its own assignment. Local wins.
        self.edit('<DhcpInterfaceHaShared>',
                  '<DhcpInterfaceHaShared><managed_interface>opt9</managed_interface>')
        self.assertEqual(self.controller.reconcile()['state'], 'ACTIVE')
        self.assertEqual(self.host.items[DHCPHA_DEVICE]['laggport'], {'hn1': {}})
        # An unconfigured receiver must fence, even with a valid legacy WAN.
        self.edit('<managed_interface>wan</managed_interface>', '')
        self.edit('<managed_interface>opt9</managed_interface>',
                  '<managed_interface>wan</managed_interface>')
        self.assertNotEqual(self.controller.reconcile()['state'], 'ACTIVE')
        self.assert_fenced()

    def test_active_round_trip_preserves_shared_identity_and_is_idempotent(self):
        result = self.controller.reconcile()
        self.assertEqual(result['actual_attachment'], 'ATTACHED')
        self.assertEqual(result['state'], 'ACTIVE')
        self.assertEqual(result['global_role'], 'MASTER')
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

    def test_physical_carrier_can_start_and_recover_when_down_reports_no_link(self):
        self.edit('<carrier>hn1</carrier>', '<carrier>ix0</carrier>')
        carrier = self.host.items['ix0'] = self.host.items.pop('hn1')
        carrier['status'] = 'no carrier'
        # Model the observed ix behavior, including negotiation after UP.
        def physical_link(argv):
            if argv[2] == 'down':
                carrier['status'] = 'no carrier'
        self.host.after = physical_link
        self.assertTrue(self.controller.health(), 'an intentionally down carrier is not incapable')
        result = self.controller.reconcile()
        self.assertEqual(result['actual_attachment'], 'ATTACHED')
        self.assertEqual(result['reason_code'], 'carrier_link_down')
        self.assertIn('up', carrier['flags'])
        self.assertEqual(carrier['macaddr'], MAC)
        self.assertIn('promisc', carrier['flags'])
        self.host.commands.clear()
        self.assertTrue(self.controller.health())
        self.controller.reconcile()
        self.assertEqual(self.host.commands, [], 'negotiation must not be interrupted by down/up retries')
        carrier['status'] = 'active'
        self.assertEqual(self.controller.reconcile()['state'], 'ACTIVE')
        self.assertEqual(self.host.commands, [])
        self.host.items['hn0']['carp']['10']['status'] = 'BACKUP'
        self.controller.reconcile()
        self.assertEqual(self.host.items[DHCPHA_DEVICE]['laggport'], {})
        self.assertNotIn('up', carrier['flags'])
        self.assertTrue(self.controller.health(), 'standby remains eligible with its carrier intentionally down')
        self.assertEqual(self.controller.status()['state'], 'STANDBY')
        self.host.items['hn0']['carp']['10']['status'] = 'MASTER'
        self.assertEqual(self.controller.reconcile()['actual_attachment'], 'ATTACHED')

    def test_shared_mac_receive_filter_is_set_before_activation_and_repaired(self):
        def verify_before_up(argv):
            if argv[1:3] == [DHCPHA_DEVICE, 'up']:
                self.assertIn('promisc', self.host.items[DHCPHA_DEVICE]['flags'])
                self.assertIn('promisc', self.host.items['hn1']['flags'])
        self.host.after = verify_before_up
        self.assertEqual(self.controller.reconcile()['state'], 'ACTIVE')
        # Native interface reconfigure can clear the receive filter. Repair it
        # without detaching the carrier or interrupting an established lease.
        for device in (DHCPHA_DEVICE, 'hn1'):
            self.host.items[device]['flags'].remove('promisc')
        self.host.commands.clear()
        self.assertNotEqual(self.controller.status()['state'], 'ACTIVE')
        self.assertEqual(self.controller.reconcile()['state'], 'ACTIVE')
        self.assertEqual(self.host.commands, [('/sbin/ifconfig', DHCPHA_DEVICE, 'promisc')])
        self.host.items['hn0']['carp']['10']['status'] = 'BACKUP'
        self.controller.reconcile()
        self.assert_fenced()
        self.assertNotIn('promisc', self.host.items['hn1']['flags'])

    def test_unverified_receive_filter_never_activates(self):
        self.host.ignore = lambda argv: argv[2] == 'promisc'
        with self.assertRaisesRegex(RuntimeError, 'receive filter'):
            self.controller.reconcile()
        self.assert_fenced()
        self.assertNotIn(('/sbin/ifconfig', DHCPHA_DEVICE, 'up'), self.host.commands)

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
                self.assertNotIn(('/sbin/ifconfig', DHCPHA_DEVICE, 'up'), self.host.commands)

    def test_each_promotion_failure_fences_without_claiming_success(self):
        for action in ('down', 'ether', 'mtu', 'promisc', 'laggport', 'up'):
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
        for mutation in ('collision', 'hardware_collision', 'vlan', 'assigned', 'delay', 'mtu', 'missing_carp', 'address'):
            with self.subTest(mutation=mutation):
                self.setUp()
                if mutation == 'collision': self.host.items['hn0']['macaddr'] = MAC
                if mutation == 'hardware_collision': self.host.items['hn0']['macaddr_hw'] = MAC
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
        for config in (CONFIG.replace('<if>dhcpha0lagg</if>', '<if>hn0</if>'), '<broken'):
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
        self.assertEqual(self.host.items[DHCPHA_DEVICE]['laggport'], {'hn1': {}})

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
        self.host.items[DHCPHA_DEVICE]['groups'] = []  # replacement reuses index42
        for operation in (self.controller.prepare, self.controller.reconcile, self.controller.fence):
            with self.assertRaises(RuntimeError): operation()
        self.edit('<if>dhcpha0lagg</if>', '<if>hn0</if>')
        with self.assertRaises(RuntimeError): self.controller.remove()
        self.assertEqual(self.host.commands, [])

    def test_prepare_and_remove_verify_ownership_and_assignment(self):
        self.host.items.pop(DHCPHA_DEVICE)
        self.controller.marker.unlink()
        self.controller.prepare()
        self.assertTrue(self.controller.owned())
        with self.assertRaisesRegex(RuntimeError, 'reassign'):
            self.controller.remove()
        self.edit('<if>dhcpha0lagg</if>', '<if>hn0</if>')
        self.controller.remove()
        self.assertNotIn(DHCPHA_DEVICE, self.host.items)
        self.assertFalse(self.controller.marker.exists())
        self.assertTrue((self.directory / 'transition.lock').exists())

    def test_unset_mtu_preserves_carrier_value(self):
        self.edit('<mtu>1400</mtu>', '')
        self.host.items['hn1']['mtu'] = '9000'
        self.controller.reconcile()
        self.assertEqual(self.host.items[DHCPHA_DEVICE]['mtu'], '9000')
        self.assertFalse(any(c[2] == 'mtu' for c in self.host.commands))

    def test_all_stale_members_are_silenced_before_detach(self):
        self.controller.reconcile()
        self.host.items['hn2'] = dict(flags=['up'], macaddr='00:11:22:33:44:02')
        self.host.items[DHCPHA_DEVICE]['laggport']['hn2'] = {}
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
        self.host.items[DHCPHA_DEVICE]['laggport']['hn2'] = {}
        def change(argv):
            if argv[1:3] == [DHCPHA_DEVICE, 'down']:
                self.host.items[DHCPHA_DEVICE]['laggport'].pop('hn2', None)
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

    def test_status_waits_for_short_transition_then_reads_fresh_state(self):
        other = Controller(self.config, self.directory, self.host.command, lambda _: 42)
        # Simulate the writer finishing on the first retry, without timing races.
        writer = self.controller.locked()
        writer.__enter__()
        released = False

        def finish_transition(_delay):
            nonlocal released
            self.host.items['hn0']['carp']['10']['status'] = 'BACKUP'
            writer.__exit__(None, None, None)
            released = True

        try:
            with patch('runtime.time.sleep', side_effect=finish_transition) as wait:
                status = other.status()
            self.assertTrue(released)
            wait.assert_called_once()
            self.assertEqual(status['global_role'], 'BACKUP')
            self.assertEqual(status['actual_attachment'], 'FENCED')
            self.assertEqual(self.host.commands, [])
        finally:
            if not released:
                writer.__exit__(None, None, None)

    def test_status_lock_wait_is_bounded_and_does_not_read_during_transition(self):
        other = Controller(self.config, self.directory, self.host.command, lambda _: 42)
        with self.controller.locked(), patch('runtime.STATUS_LOCK_TIMEOUT', 0), \
                patch.object(other, 'snapshot') as snapshot:
            with self.assertRaisesRegex(TimeoutError, 'transition is busy'):
                other.status()
            snapshot.assert_not_called()

    def test_local_link_loss_reports_fault_without_interrupting_native_recovery(self):
        self.controller.reconcile()
        self.host.items['hn1']['status'] = 'no carrier'
        self.host.commands.clear()
        self.assertTrue(self.controller.health())
        self.assertEqual(self.controller.reconcile()['reason_code'], 'carrier_link_down')
        self.assertEqual(self.host.commands, [])
        self.host.items['hn1']['status'] = 'active'
        self.assertTrue(self.controller.health())
        self.assertEqual(self.controller.reconcile()['state'], 'ACTIVE')
        self.assertEqual(self.host.commands, [])

    def test_status_keeps_observed_carp_role_when_disabled_and_reports_operational_state(self):
        self.edit('<enabled>1</enabled>', '<enabled>0</enabled>')
        result = self.controller.status()
        self.assertEqual(result['global_role'], 'MASTER')
        self.assertEqual(result['state'], 'DISABLED')
        self.assertEqual(result['expected_carp_instances'], [{'interface': 'hn0', 'vhid': '10'}])
        self.assertEqual(result['live_carp_instances'][0]['status'], 'MASTER')

        self.edit('<enabled>0</enabled>', '<enabled>1</enabled>')
        self.host.items['hn0']['carp']['10']['status'] = 'BACKUP'
        result = self.controller.status()
        self.assertEqual(result['global_role'], 'BACKUP')
        self.assertEqual(result['state'], 'STANDBY')

    def test_status_is_unknown_for_live_carp_inventory_mismatch(self):
        self.host.items['hn0']['carp'] = {}
        result = self.controller.status()
        self.assertEqual(result['state'], 'UNKNOWN')
        self.assertEqual(result['reason_code'], 'carp_inventory_mismatch')
        self.assertEqual(result['global_role'], 'INDETERMINATE')

    def test_status_faults_attachment_that_remains_on_backup(self):
        self.controller.reconcile()
        self.host.items['hn0']['carp']['10']['status'] = 'BACKUP'
        result = self.controller.status()
        self.assertEqual(result['actual_attachment'], 'ATTACHED')
        self.assertEqual(result['state'], 'FAULT')
        self.assertEqual(result['reason_code'], 'attachment_when_carp_ineligible')

    def test_status_faults_an_active_attachment_without_carrier_link(self):
        self.controller.reconcile()
        self.host.items['hn1']['status'] = 'no carrier'
        result = self.controller.status()
        self.assertEqual(result['state'], 'FAULT')
        self.assertEqual(result['reason_code'], 'carrier_link_down')

    def test_explicit_setup_prepare_is_disabled_only_and_verifies_detached_ownership(self):
        self.edit('<enabled>1</enabled>', '<enabled>0</enabled>')
        self.host.items.pop(DHCPHA_DEVICE)
        self.controller.marker.unlink()
        result = self.controller.prepare_setup()
        self.assertEqual(result, {
            'prepared': True, 'device': DHCPHA_DEVICE, 'detached': True, 'owned': True,
        })
        self.assertEqual(self.host.items[DHCPHA_DEVICE]['laggport'], {})

        self.setUp()
        with self.assertRaisesRegex(RuntimeError, 'disable'):
            self.controller.prepare_setup()
        self.assertEqual(self.host.commands, [])

    def test_setup_prepare_rejects_foreign_or_attached_same_name_device(self):
        self.edit('<enabled>1</enabled>', '<enabled>0</enabled>')
        self.host.items[DHCPHA_DEVICE]['groups'] = []
        with self.assertRaisesRegex(RuntimeError, 'unverified or attached'):
            self.controller.prepare_setup()
        self.assertEqual(self.host.commands, [])

    def test_explicit_prepare_and_reconcile_log_only_verified_changes(self):
        self.host.items.pop(DHCPHA_DEVICE)
        self.controller.marker.unlink()
        self.controller.prepare()
        self.controller.prepare()
        self.assertEqual(len(self.event_messages('device_prepared')), 1)
        self.assertIn('safety=owned_detached outcome=verified', self.event_messages('device_prepared')[0])

        self.controller.reconcile()
        self.assertEqual(len(self.event_messages('attachment_changed')), 1)
        self.assertIn('old=FENCED new=hn1', self.event_messages('attachment_changed')[0])
        self.host.items['hn0']['carp']['10']['status'] = 'BACKUP'
        self.controller.reconcile()
        self.assertEqual(len(self.event_messages('attachment_changed')), 2)
        self.assertIn('old=hn1 new=FENCED', self.event_messages('attachment_changed')[1])

    def test_receive_mode_repair_is_logged_without_a_false_attachment_change(self):
        self.controller.reconcile()
        self.events.clear()
        for device in (DHCPHA_DEVICE, 'hn1'):
            self.host.items[device]['flags'].remove('promisc')
        self.controller.reconcile()
        self.assertEqual(len(self.event_messages('repair_completed')), 1)
        self.assertIn('condition=receive_mode_missing', self.event_messages('repair_completed')[0])
        self.assertEqual(self.event_messages('attachment_changed'), [])

    def test_blocked_or_stopped_master_does_not_log_unperformed_repairs(self):
        for blocked in ('invalid_config', 'stopped'):
            with self.subTest(blocked=blocked):
                self.setUp()
                self.controller.reconcile()
                self.events.clear()
                if blocked == 'invalid_config':
                    self.edit('<failback_delay>0', '<failback_delay>120')
                else:
                    self.controller.stopped.touch()
                reason = self.controller.snapshot().local_error or 'controller stopped'
                self.controller.reconcile()
                self.assert_fenced()
                changes = self.event_messages('attachment_changed')
                self.assertEqual(len(changes), 1)
                self.assertIn('reason=' + reason, changes[0])
                # The next observation records the completed fence once.
                self.controller.reconcile()
                self.events.clear()
                self.controller.reconcile()
                self.controller.reconcile()
                self.assertEqual(self.events, [])

    def test_reconcile_logs_verified_carrier_replacement(self):
        self.controller.reconcile()
        self.events.clear()
        self.host.items['hn2'] = dict(
            flags=['up', 'promisc'], is_physical=True,
            macaddr='00:11:22:33:44:02', mtu='1500', status='active',
        )
        self.host.items[DHCPHA_DEVICE]['laggport'] = {'hn2': {}}
        self.controller.reconcile()
        changes = self.event_messages('attachment_changed')
        self.assertEqual(len(changes), 1)
        self.assertIn('old=hn2 new=hn1', changes[0])
        self.assertEqual(self.event_messages('repair_completed'), [])

    def test_status_and_healthy_health_probe_emit_no_events(self):
        self.controller.status()
        self.assertTrue(self.controller.health())
        self.assertEqual(self.events, [])

    def test_interface_observations_explain_blocked_physical_carrier_without_poll_spam(self):
        self.edit('<enabled>1</enabled>', '<enabled>0</enabled>')
        self.edit('<carrier>hn1</carrier>', '<carrier>ix0</carrier>')
        carrier = self.host.items['ix0'] = self.host.items.pop('hn1')
        carrier.update(status='no carrier', ipv4=[], ipv6=[], carp={})
        self.controller.reconcile()
        messages = self.event_messages('interface_observed')
        self.assertEqual(len(messages), 1)
        for fact in ('phase=before_reconcile', 'carrier=ix0', 'carrier_up=False',
                     'carrier_link=no carrier', 'carrier_promisc=False',
                     'carrier_ipv4=0', 'carrier_ipv6=0', 'carrier_carp=0',
                     'lagg_up=False', 'lagg_members=none'):
            self.assertIn(fact, messages[0])
        self.assertEqual(self.host.commands, [])
        self.controller.reconcile()
        self.assertEqual(len(self.event_messages('interface_observed')), 1)

        # A different administrative state with the same absent link matters.
        carrier['flags'] = ['up']
        self.controller.reconcile()
        self.assertIn('carrier_up=True', self.event_messages('interface_observed')[-1])
        # The next poll records the result of fencing, then becomes quiet.
        self.controller.reconcile()
        self.events.clear()
        self.controller.reconcile()
        self.assertEqual(self.events, [])

        carrier['ipv4'] = [{'ipaddr': '192.0.2.1'}]
        self.controller.reconcile()
        self.assertIn('carrier_ipv4=1', self.event_messages('interface_observed')[0])

        # Unmanaged desired state must not hide the actually observed CARP role.
        self.edit('<if>dhcpha0lagg</if>', '<if>ix0</if>')
        self.controller.reconcile()
        self.assertIn('role=MASTER', self.event_messages('interface_observed')[-1])

    def test_status_distinguishes_known_empty_ipv4_from_unknown_inventory(self):
        self.host.items[DHCPHA_DEVICE]['ipv4'] = []
        self.assertEqual(self.controller.status()['ipv4_addresses'], [])
        self.host.items[DHCPHA_DEVICE]['ipv4'] = [{'ipaddr': '192.0.2.10'}]
        self.assertEqual(self.controller.status()['ipv4_addresses'], ['192.0.2.10'])
        self.host.items[DHCPHA_DEVICE]['ipv4'] = [{'ipaddr': 'not-an-address'}]
        self.assertIsNone(self.controller.status()['ipv4_addresses'])

    def test_failed_reconcile_logs_once_and_logging_errors_do_not_change_fencing(self):
        self.host.fail = lambda argv: argv[2] == 'promisc'
        with self.assertRaisesRegex(RuntimeError, 'owned attachment fenced'):
            self.controller.reconcile()
        self.assert_fenced()
        self.assertEqual(len(self.event_messages('operation_failed')), 1)
        self.assertIn('safety=fenced outcome=failed', self.event_messages('operation_failed')[0])

        self.events.clear()
        self.controller.event_sink = lambda *_: (_ for _ in ()).throw(OSError('syslog unavailable'))
        with self.assertRaisesRegex(RuntimeError, 'owned attachment fenced'):
            self.controller.reconcile()
        self.assert_fenced()

    def test_daemon_failure_reminder_changed_safety_and_recovery(self):
        self.controller.reconcile()
        self.events.clear()
        for device in (DHCPHA_DEVICE, 'hn1'):
            self.host.items[device]['flags'].remove('promisc')
        self.host.fail = lambda argv: argv[2] in ('promisc', 'down')
        now = [100.0]
        with patch('runtime.time.monotonic', side_effect=lambda: now[0]):
            for _ in range(2):
                with self.assertRaises(RuntimeError):
                    self.controller.reconcile(daemon=True)
                self.controller.report_daemon_failure(
                    'carp_status_refresh', OSError('refresh failure'),
                )
            self.assertEqual(len(self.event_messages('operation_failed')), 2)

            now[0] = 159.0
            with self.assertRaises(RuntimeError):
                self.controller.reconcile(daemon=True)
            self.controller.report_daemon_failure('carp_status_refresh', OSError('refresh failure'))
            self.assertEqual(len(self.event_messages('operation_failed')), 2)

            now[0] = 160.0
            with self.assertRaises(RuntimeError):
                self.controller.reconcile(daemon=True)
            self.controller.report_daemon_failure('carp_status_refresh', OSError('refresh failure'))
            self.assertEqual(len(self.event_messages('operation_failed')), 4)
            reconcile_failures = [
                message for message in self.event_messages('operation_failed')
                if 'operation=reconcile ' in message
            ]
            self.assertIn('safety=unverified', reconcile_failures[0])

            # A verified fence changes the outcome and is reported immediately.
            self.host.fail = lambda argv: argv[2] == 'promisc'
            now[0] = 161.0
            with self.assertRaises(RuntimeError):
                self.controller.reconcile(daemon=True)
            self.controller.report_daemon_failure('carp_status_refresh', OSError('refresh failure'))
            self.assertEqual(len(self.event_messages('operation_failed')), 5)
            reconcile_failures = [
                message for message in self.event_messages('operation_failed')
                if 'operation=reconcile ' in message
            ]
            self.assertIn('safety=fenced', reconcile_failures[-1])

            self.host.fail = None
            self.controller.reconcile(daemon=True)
            self.controller.report_daemon_recovery('carp_status_refresh')
        recoveries = self.event_messages('operation_recovered')
        self.assertEqual(len(recoveries), 2)
        self.assertIn('operation=reconcile ', recoveries[0])
        self.assertIn('operation=carp_status_refresh ', recoveries[1])

    def test_daemon_lock_acquisition_failure_is_reported_without_unlocked_fencing(self):
        with patch.object(self.controller, 'locked', side_effect=TimeoutError('transition lock busy')):
            for _ in range(2):
                with self.assertRaisesRegex(TimeoutError, 'transition lock busy'):
                    self.controller.reconcile(daemon=True)

        failures = self.event_messages('operation_failed')
        self.assertEqual(len(failures), 1)
        self.assertIn('operation=reconcile', failures[0])
        self.assertIn('safety=unknown outcome=failed', failures[0])
        self.assertEqual(self.host.commands, [])

    def test_event_format_is_single_line_bounded_and_keeps_code_and_outcome(self):
        extra_fields = {f'extra{index}': 'z' * 4096 for index in range(20)}
        self.controller._event(
            'operation_failed', operation='reconcile', interface='wan', carrier='hn1',
            condition='command_failure', reason='x' * 4096,
            safety='unverified', outcome='failed', **extra_fields,
        )
        message = self.events[-1][1]
        self.assertLessEqual(len(message), 1024)
        self.assertNotIn('\n', message)
        self.assertTrue(message.startswith('event=operation_failed '))
        self.assertIn('reason=', message)
        self.assertTrue(message.endswith('safety=unverified outcome=failed'))

    def test_native_event_writer_uses_the_registered_program_identity(self):
        with patch('runtime.syslog.openlog') as openlog, patch('runtime.syslog.syslog') as sink:
            runtime.emit_event('state_observed', state='ACTIVE', outcome='observed')
        openlog.assert_called_once_with('dhcp-interface-ha')
        sink.assert_called_once_with(runtime.syslog.LOG_INFO, 'event=state_observed state=ACTIVE outcome=observed')


if __name__ == '__main__':
    unittest.main()
