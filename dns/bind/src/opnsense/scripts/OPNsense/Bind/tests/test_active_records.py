"""Regression coverage for DHCP watcher record visibility."""

import json
import ipaddress
import pathlib
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch


SCRIPTS = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))


class TestActiveRecords(unittest.TestCase):
    def test_successful_record_is_persisted_for_the_read_only_grid(self):
        from dhcpwatcher.state import StateManager

        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / 'dhcplease_state.json'
            state = StateManager(str(path))
            state.save({
                StateManager.key('mapping-1', 'isc-dhcp', '192.0.2.15'):
                    StateManager.record(
                        'mapping-1',
                        {'hostname_suffix': 'home.example'},
                        {
                            'hostname': 'laptop',
                            'address': '192.0.2.15',
                            'ends': 2000000000,
                            'source': 'isc-dhcp',
                            'mac': '00:11:22:33:44:55',
                        },
                        None,
                    ),
            })
            self.assertEqual(json.loads(path.read_text())['mapping-1|isc-dhcp|192.0.2.15']['suffix'], 'home.example')

    def test_watcher_persists_a_successfully_published_record(self):
        from dhcpwatcher.watcher import Watcher

        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / 'dhcplease_state.json'
            watcher = Watcher(state_path=str(path), run_nsupdate_func=lambda *_: True)
            watcher.mappings = {'mapping-1': {
                'dhcp_source': 'isc-dhcp',
                'hostname_suffix': 'home.example',
                'lease_scopes': [ipaddress.ip_network('192.0.2.0/24')],
            }}
            watcher._reconcile({('isc-dhcp', '192.0.2.15'): {
                'hostname': 'laptop', 'address': ipaddress.ip_address('192.0.2.15'),
                'ends': 2000000000, 'source': 'isc-dhcp',
            }})
            self.assertEqual(json.loads(path.read_text())['mapping-1|isc-dhcp|192.0.2.15']['hostname'], 'laptop')

    def test_watcher_page_exposes_active_records_grid(self):
        view = SCRIPTS.parents[4] / 'src/opnsense/mvc/app/views/OPNsense/Bind/watcher.volt'
        self.assertIn('Active DHCP Records', view.read_text())
        self.assertIn('/api/bind/dhcprecord/search_record', view.read_text())

    def test_logs_page_exposes_watcher_log_tab(self):
        view = SCRIPTS.parents[4] / 'src/opnsense/mvc/app/views/OPNsense/Bind/logs.volt'
        self.assertIn('DHCP Watcher Log', view.read_text())
        self.assertIn('/api/diagnostics/log/bind/dhcplease', view.read_text())

    def test_watcher_uses_the_registered_dhcp_log_facility(self):
        entrypoint = SCRIPTS / 'dhcplease_watcher.py'
        self.assertIn("syslog.openlog('bind-dhcplease', facility=syslog.LOG_LOCAL4)", entrypoint.read_text())

    def test_syslog_template_defines_a_valid_watcher_program_filter(self):
        template = SCRIPTS.parents[4] / 'src/opnsense/service/templates/OPNsense/Syslog/local/bind_dhcplease.conf'
        content = template.read_text()
        self.assertIn('filter f_local_bind_dhcplease', content)
        self.assertIn('program("bind-dhcplease")', content)
        self.assertNotIn('!bind-dhcplease', content)

    def test_failed_dynamic_update_is_logged(self):
        from dhcpwatcher import updater

        mapping = {
            'tsigkey_name': 'test-key', 'tsigkey_algo': 'hmac-sha256',
            'tsigkey_secret': 'secret', 'nsupdate_address': '127.0.0.1',
            'nsupdate_port': 53, 'hostname_suffix': 'home.example',
        }
        with patch('dhcpwatcher.updater.subprocess.run', return_value=SimpleNamespace(returncode=1, stderr='REFUSED')):
            with patch('dhcpwatcher.updater.syslog.syslog') as log:
                self.assertFalse(updater.run_nsupdate(mapping, ['send'], 'home.example'))
        self.assertTrue(log.called)


if __name__ == '__main__':
    unittest.main()
