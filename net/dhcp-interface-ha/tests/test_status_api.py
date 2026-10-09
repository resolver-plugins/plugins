import json
import unittest
from pathlib import Path

from php_fixture import run_php_fixture


PLUGIN = Path(__file__).resolve().parents[1]
FIXTURE = PLUGIN / "tests/fixtures/status_controller.php"
CONTROLLER = (
    PLUGIN
    / "src/opnsense/mvc/app/controllers/OPNsense/DhcpInterfaceHa/Api/StatusController.php"
)


class StatusApiTests(unittest.TestCase):

    def observe(self, case="", *args):
        return run_php_fixture(FIXTURE, CONTROLLER, case, *args)

    def checks(self, payload):
        return {check["code"]: check for check in payload["readiness"]}

    def test_readiness_response_has_valid_classifications_and_unique_checks(self):
        payload = self.observe()
        checks = self.checks(payload)
        self.assertCountEqual([check['code'] for check in payload['readiness']], {
            'managed_interface', 'managed_assignment', 'managed_ipv4', 'managed_ipv6',
            'carrier_selection', 'carrier_capability', 'carrier_exclusive', 'device_ownership',
            'device_topology', 'shared_mac', 'native_spoof_mac', 'hardware_media', 'managed_carp_vips',
            'carp_inventory', 'failback_policy', 'pfsync_context', 'xmlrpc_selection', 'peer_readiness',
            'receive_mode',
        })
        for code, check in checks.items():
            with self.subTest(code=code):
                self.assertIn(check["status"], {"pass", "fail", "unknown"})
                self.assertIn(check["severity"], {"blocker", "warning", "info"})
                self.assertIn(
                    check["resolution"],
                    {"none", "automatic", "retry", "user_action", "investigate"},
                )
                self.assertIsInstance(check["relevant"], bool)
                self.assertIsNone(check["attempt"])
                if check["status"] == "pass":
                    self.assertIsNone(check["action"])

        self.assertEqual(payload["summary"], {"state": "ready", "reason_code": "global_backup"})
        self.assertEqual(payload["attachment"]["receive_mode"]["required"], False)
        self.assertEqual(payload["attachment"]["receive_mode"]["status"], "pass")
        self.assertTrue(payload["ha"]["xmlrpc"]["sender_configured"])
        self.assertIsNone(payload["local"]["plugin_version"])

    def test_readiness_classification(self):
        for case, code, expected, summary in (
            ('missing_collision_inventory', 'shared_mac', dict(status='unknown', responsibility='environment', resolution='none'),
             ('status_unavailable', 'required_observation_unavailable')),
            ('malformed_collision_inventory', 'shared_mac', dict(status='unknown', responsibility='environment', resolution='none'),
             ('status_unavailable', 'required_observation_unavailable')),
            ('confirmed_collision', 'shared_mac', dict(status='fail', responsibility='environment', resolution='investigate'), None),
            ('active_receive_drift', 'receive_mode', dict(status='fail', resolution='automatic', relevant=True),
             ('needs_attention', 'attachment_unverified')),
            ('foreign_device', 'device_ownership', dict(status='fail', resolution='investigate', relevant=True),
             ('needs_attention', 'device_ownership_unverified')),
            ('native_ipv6_conflict', 'managed_ipv6', dict(status='fail', responsibility='native', resolution='user_action', action='interface_assignments'), None),
            ('legacy_failback', 'failback_policy', dict(status='fail', resolution='user_action', action='reset_failback'), None),
            ('sender_missing_plugin', 'xmlrpc_selection', dict(status='fail', relevant=True, resolution='user_action', action='enable_sync'), None),
            ('no_sync_target', 'xmlrpc_selection', dict(status='pass', relevant=False), None),
            ('unconfigured_enabled', 'managed_interface', dict(status='fail', relevant=True, responsibility='user', resolution='user_action', action='settings'), None),
        ):
            with self.subTest(case=case):
                payload = self.observe(case)
                check = self.checks(payload)[code]
                for key, value in dict({'action': None, 'attempt': None}, **expected).items():
                    self.assertEqual(check[key], value, key)
                if summary:
                    self.assertEqual(payload['summary'], dict(zip(('state', 'reason_code'), summary)))
                if code == 'receive_mode':
                    self.assertTrue(payload['attachment']['receive_mode']['required'])
                    self.assertEqual(payload['attachment']['receive_mode']['status'], 'fail')
                if code == 'xmlrpc_selection':
                    self.assertEqual(payload['ha']['xmlrpc']['sender_configured'], case == 'sender_missing_plugin')
                if case == 'unconfigured_enabled':
                    self.assertEqual(payload['summary']['state'], 'needs_attention')

    def test_unknown_required_evidence_precedes_generic_setup_failure(self):
        payload = self.observe("native_ipv6_conflict_unknown_mac")
        checks = self.checks(payload)
        self.assertEqual(checks["managed_ipv6"]["status"], "fail")
        self.assertEqual(checks["shared_mac"]["status"], "unknown")
        self.assertEqual(payload["summary"]["state"], "status_unavailable")
        draft = self.observe("preconfigure_unknown_mac")
        self.assertEqual(self.checks(draft)["shared_mac"]["status"], "unknown")
        self.assertEqual(draft["summary"]["state"], "status_unavailable")

    def test_setup_and_detached_states_keep_actions_relevant(self):
        for case, state, expected in (
            ('preconfigure', 'not_configured', [('managed_assignment', 'action', 'configure'),
                ('carrier_selection', 'action', 'configure'), ('carrier_exclusive', 'relevant', False)]),
            ('captured_original', 'not_configured', [('managed_assignment', 'action', 'configure'),
                ('carrier_exclusive', 'status', 'pass')]),
            ('maintenance', 'ready', [('receive_mode', 'relevant', False)]),
            ('stopped_master_detached', None, [('receive_mode', 'relevant', False)]),
            ('none', 'not_configured', [('managed_interface', 'relevant', False)]),
        ):
            with self.subTest(case=case):
                payload = self.observe(case)
                checks = self.checks(payload)
                if state:
                    self.assertEqual(payload['summary']['state'], state)
                for code, field, value in expected:
                    self.assertEqual(checks[code][field], value)
                if case == 'maintenance':
                    self.assertEqual(payload['controller']['state'], 'FENCED')
                if case == 'stopped_master_detached':
                    self.assertEqual(payload['carp']['role'], 'MASTER')
                    self.assertTrue(payload['controller']['stopped'])
                    self.assertFalse(payload['attachment']['receive_mode']['required'])
                if case == 'none':
                    self.assertEqual(payload['result'], 'ok')
                    self.assertEqual(payload['attachment']['configured_shared_mac'], '02:11:22:33:44:55')

    def test_pending_assignment_readback_distinguishes_safe_retry_conflict_and_unknown(self):
        for case, state in [
            ("standby", "clear"), ("pending_selected", "selected_relink"),
            ("pending_unrelated", "conflict"), ("pending_mismatched", "conflict"),
            ("pending_unavailable", "unknown"), ("pending_malformed", "unknown"),
        ]:
            with self.subTest(case=case):
                pending = self.observe(case)["setup"]["pending_assignment"]
                self.assertEqual(pending["state"], state)
                self.assertEqual(pending["available"], state != "unknown")

    def test_status_get_only_reads_observation_sources(self):
        payload = self.observe("readonly_calls")
        self.assertEqual(
            payload["_fixture_calls"],
            [
                "dhcp_interface_ha status",
                "interface show carp",
                "interface address",
                "interface gateways status",
                "filter list pfsync json",
            ],
        )

    def test_status_api_keeps_managed_observation_and_lease_availability_distinct(self):
        payload = self.observe()
        self.assertEqual(payload["result"], "ok")
        self.assertEqual(payload["controller"]["state"], "STANDBY")
        self.assertEqual(payload["managed"]["identifier"], "wan")
        self.assertEqual(payload["connection"]["ipv4"]["address"], {"address": "192.0.2.10", "prefix": 24})
        self.assertEqual(payload["connection"]["dhcp"]["state"], "unavailable")
        self.assertFalse(payload["connection"]["dhcp"]["available"])
        self.assertEqual(payload["connection"]["gateway"]["status"], "unknown")
        self.assertEqual(payload["ha"]["peer_readiness"], "unverified")
        self.assertFalse(payload["removal"]["allowed"])
        for secret in ('private_fixture_secret', 'do-not-export'):
            self.assertNotIn(secret, json.dumps(payload))

        malformed_payload = self.observe('malformed')
        self.assertEqual(malformed_payload["result"], "unavailable")
        self.assertEqual(malformed_payload["controller"]["state"], "UNKNOWN")
        self.assertIn("controller", malformed_payload["errors"])

        address_payload = self.observe('bad_address')
        self.assertEqual(address_payload["result"], "partial")
        self.assertFalse(address_payload["connection"]["ipv4"]["available"])
        self.assertIn("addresses", address_payload["errors"])

        for case in ("addressless", "unrelated_addressless"):
            observation = self.observe(case)
            self.assertEqual(observation["result"], "ok")
            self.assertTrue(observation["connection"]["ipv4"]["available"])
            expected = None if case == "addressless" else {"address": "192.0.2.10", "prefix": 24}
            self.assertEqual(observation["connection"]["ipv4"]["address"], expected)

    def test_current_mac_suggestion_requires_usable_identity_and_preserves_spoof_precedence(self):
        valid = "02:11:22:33:44:55"
        cases = [
            ([valid], valid, "observed_backing_device"),
            (["00:00:00:00:00:00"], "", "unavailable"),
            (["ff:ff:ff:ff:ff:ff"], "", "unavailable"),
            (["01:00:5e:00:00:01"], "", "unavailable"),
            (["invalid"], "", "unavailable"),
            (["00:00:00:00:00:00", valid], valid, "native_spoof_mac"),
            ([valid, "00:00:00:00:00:00"], "", "unavailable"),
        ]
        for values, expected, source in cases:
            with self.subTest(values=values):
                managed = self.observe("mac_suggestion", *values)["managed"]
                self.assertEqual(managed["effective_mac_suggestion"], expected)
                self.assertEqual(managed["mac_source"], source)


if __name__ == "__main__":
    unittest.main()
