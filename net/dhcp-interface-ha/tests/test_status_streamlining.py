import json
import shutil
import subprocess
import unittest
from pathlib import Path


PLUGIN = Path(__file__).resolve().parents[1]
FIXTURE = PLUGIN / "tests/fixtures/status_controller.php"
CONTROLLER = (
    PLUGIN
    / "src/opnsense/mvc/app/controllers/OPNsense/DhcpInterfaceHa/Api/StatusController.php"
)


class StatusStreamliningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.php = shutil.which("php")
        if cls.php is None:
            raise RuntimeError("PHP CLI is required for Status API behavior checks")

    def observe(self, case=""):
        result = subprocess.run(
            [self.php, str(FIXTURE), str(CONTROLLER), case],
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(result.stdout)

    def checks(self, payload):
        return {check["code"]: check for check in payload["readiness"]}

    def test_readiness_response_has_the_s1_contract_and_single_classification(self):
        payload = self.observe()
        checks = self.checks(payload)
        responsibilities = {
            "managed_interface": "user",
            "managed_assignment": "plugin",
            "managed_ipv4": "native",
            "managed_ipv6": "native",
            "carrier_selection": "plugin",
            "carrier_capability": "environment",
            "carrier_exclusive": "native",
            "device_ownership": "plugin",
            "device_topology": "plugin",
            "shared_mac": "user",
            "native_spoof_mac": "native",
            "hardware_media": "native",
            "managed_carp_vips": "native",
            "carp_inventory": "native",
            "failback_policy": "user",
            "pfsync_context": "native",
            "xmlrpc_selection": "user",
            "peer_readiness": "environment",
            "receive_mode": "plugin",
        }
        self.assertEqual(set(checks), set(responsibilities))
        for code, responsibility in responsibilities.items():
            with self.subTest(code=code):
                check = checks[code]
                self.assertEqual(check["responsibility"], responsibility)
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
        self.assertEqual(payload["controller"]["state"], "STANDBY")
        self.assertEqual(payload["attachment"]["receive_mode"]["required"], False)
        self.assertEqual(payload["attachment"]["receive_mode"]["status"], "pass")
        self.assertTrue(payload["ha"]["xmlrpc"]["sender_configured"])
        self.assertIsNone(payload["local"]["plugin_version"])

    def test_unknown_collision_inventory_does_not_turn_valid_mac_into_a_user_error(self):
        for case in ("missing_collision_inventory", "malformed_collision_inventory"):
            with self.subTest(case=case):
                payload = self.observe(case)
                check = self.checks(payload)["shared_mac"]
                self.assertEqual(check["status"], "unknown")
                self.assertEqual(check["responsibility"], "environment")
                self.assertEqual(check["resolution"], "none")
                self.assertIsNone(check["action"])
                self.assertIsNone(check["attempt"])
                self.assertEqual(payload["summary"]["state"], "status_unavailable")
                self.assertEqual(payload["summary"]["reason_code"], "required_observation_unavailable")

    def test_confirmed_mac_collision_requires_investigation_before_identity_change(self):
        payload = self.observe("confirmed_collision")
        check = self.checks(payload)["shared_mac"]
        self.assertEqual(check["status"], "fail")
        self.assertEqual(check["responsibility"], "environment")
        self.assertEqual(check["resolution"], "investigate")
        self.assertIsNone(check["action"])

    def test_active_receive_mode_drift_is_automatic_and_has_no_fabricated_attempt(self):
        payload = self.observe("active_receive_drift")
        check = self.checks(payload)["receive_mode"]
        self.assertEqual(payload["attachment"]["receive_mode"]["required"], True)
        self.assertEqual(payload["attachment"]["receive_mode"]["status"], "fail")
        self.assertEqual(check["status"], "fail")
        self.assertEqual(check["resolution"], "automatic")
        self.assertTrue(check["relevant"])
        self.assertIsNone(check["action"])
        self.assertIsNone(check["attempt"])
        self.assertEqual(payload["summary"]["state"], "needs_attention")
        self.assertEqual(payload["summary"]["reason_code"], "attachment_unverified")

    def test_foreign_device_is_preserved_and_not_offered_for_repair(self):
        payload = self.observe("foreign_device")
        check = self.checks(payload)["device_ownership"]
        self.assertEqual(check["status"], "fail")
        self.assertEqual(check["resolution"], "investigate")
        self.assertTrue(check["relevant"])
        self.assertIsNone(check["action"])
        self.assertIsNone(check["attempt"])
        self.assertEqual(payload["summary"]["state"], "needs_attention")
        self.assertEqual(payload["summary"]["reason_code"], "device_ownership_unverified")

    def test_native_conflicts_and_explicit_failback_reset_keep_server_action_ids(self):
        native = self.checks(self.observe("native_ipv6_conflict"))["managed_ipv6"]
        self.assertEqual(native["status"], "fail")
        self.assertEqual(native["responsibility"], "native")
        self.assertEqual(native["resolution"], "user_action")
        self.assertEqual(native["action"], "interface_assignments")

        failback = self.checks(self.observe("legacy_failback"))["failback_policy"]
        self.assertEqual(failback["status"], "fail")
        self.assertEqual(failback["action"], "reset_failback")
        self.assertEqual(failback["resolution"], "user_action")

    def test_unknown_required_evidence_precedes_generic_setup_failure(self):
        payload = self.observe("native_ipv6_conflict_unknown_mac")
        checks = self.checks(payload)
        self.assertEqual(checks["managed_ipv6"]["status"], "fail")
        self.assertEqual(checks["shared_mac"]["status"], "unknown")
        self.assertEqual(payload["summary"]["state"], "status_unavailable")
        draft = self.observe("preconfigure_unknown_mac")
        self.assertEqual(self.checks(draft)["shared_mac"]["status"], "unknown")
        self.assertEqual(draft["summary"]["state"], "status_unavailable")

    def test_sender_context_controls_sync_warning_and_action(self):
        sender = self.observe("sender_missing_plugin")
        check = self.checks(sender)["xmlrpc_selection"]
        self.assertTrue(sender["ha"]["xmlrpc"]["sender_configured"])
        self.assertEqual(check["status"], "fail")
        self.assertTrue(check["relevant"])
        self.assertEqual(check["action"], "enable_sync")
        self.assertEqual(check["resolution"], "user_action")

        receiver = self.observe("no_sync_target")
        check = self.checks(receiver)["xmlrpc_selection"]
        self.assertFalse(receiver["ha"]["xmlrpc"]["sender_configured"])
        self.assertEqual(check["status"], "pass")
        self.assertFalse(check["relevant"])
        self.assertIsNone(check["action"])

    def test_setup_standby_maintenance_and_none_are_classified_without_manual_lagg_steps(self):
        setup = self.observe("preconfigure")
        setup_checks = self.checks(setup)
        self.assertEqual(setup["summary"]["state"], "not_configured")
        self.assertEqual(setup_checks["managed_assignment"]["action"], "configure")
        self.assertEqual(setup_checks["carrier_selection"]["action"], "configure")
        self.assertFalse(setup_checks["carrier_exclusive"]["relevant"])

        captured = self.observe("captured_original")
        captured_checks = self.checks(captured)
        self.assertEqual(captured["summary"]["state"], "not_configured")
        self.assertEqual(captured_checks["managed_assignment"]["action"], "configure")
        self.assertEqual(captured_checks["carrier_exclusive"]["status"], "pass")

        maintenance = self.observe("maintenance")
        self.assertEqual(maintenance["controller"]["state"], "FENCED")
        self.assertEqual(maintenance["summary"]["state"], "ready")
        self.assertFalse(self.checks(maintenance)["receive_mode"]["relevant"])

        stopped = self.observe("stopped_master_detached")
        self.assertEqual(stopped["carp"]["role"], "MASTER")
        self.assertTrue(stopped["controller"]["stopped"])
        self.assertEqual(stopped["attachment"]["receive_mode"]["required"], False)
        self.assertFalse(self.checks(stopped)["receive_mode"]["relevant"])

        none = self.observe("none")
        self.assertEqual(none["result"], "ok")
        self.assertEqual(none["summary"]["state"], "not_configured")
        self.assertEqual(none["attachment"]["configured_shared_mac"], "02:11:22:33:44:55")
        self.assertFalse(self.checks(none)["managed_interface"]["relevant"])

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
        self.assertTrue(all("apply" not in event and "prepare" not in event for event in payload["_fixture_calls"]))

    def test_synced_enablement_without_local_mapping_requests_local_interface_choice(self):
        payload = self.observe("unconfigured_enabled")
        check = self.checks(payload)["managed_interface"]
        self.assertEqual(payload["summary"]["state"], "needs_attention")
        self.assertEqual(check["status"], "fail")
        self.assertTrue(check["relevant"])
        self.assertEqual(check["responsibility"], "user")
        self.assertEqual(check["resolution"], "user_action")
        self.assertEqual(check["action"], "settings")


if __name__ == "__main__":
    unittest.main()
