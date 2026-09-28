import json
import subprocess
import unittest
from pathlib import Path


PLUGIN = Path(__file__).resolve().parents[1]
FIXTURE = PLUGIN / "tests/fixtures/settings_streamlining_controller.php"
CONTROLLER = PLUGIN / "src/opnsense/mvc/app/controllers/OPNsense/DhcpInterfaceHa/Api/SettingsController.php"
BRIDGE = PLUGIN / "src/opnsense/mvc/app/models/OPNsense/DhcpInterfaceHa/SetupAssignmentBridge.php"


def run_controller(case, action="configure", flags=None):
    result = subprocess.run(
        [
            "php",
            str(FIXTURE),
            str(CONTROLLER),
            str(BRIDGE),
            case,
            action,
            json.dumps(flags or {}),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise AssertionError(f"PHP fixture failed: {result.stderr or result.stdout}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"Invalid PHP fixture response: {result.stdout}\n{result.stderr}") from exc


class SettingsStreamliningTests(unittest.TestCase):
    def test_configure_derives_carrier_and_verifies_native_apply(self):
        outcome = run_controller("configure_success")

        self.assertEqual(outcome["response"]["result"], "saved")
        self.assertTrue(outcome["response"]["saved"])
        self.assertTrue(outcome["response"]["applied"])
        self.assertTrue(outcome["response"]["assignment_verified"])
        self.assertEqual(outcome["response"]["setup_stage"], "verified")
        self.assertEqual(outcome["local"]["carrier"], "hn1")
        self.assertEqual(outcome["shared"]["enabled"], "0")
        self.assertEqual(outcome["assignments"], {"wan": "dhcpha0lagg", "lan": "hn2"})
        self.assertEqual(outcome["pending"], [])
        self.assertIn("interface apply", outcome["events"])
        self.assertIn("/api/interfaces/assignment/set_item/wan", outcome["acl_checks"])
        self.assertIn("/api/interfaces/assignment/reconfigure", outcome["acl_checks"])
        self.assertTrue(all(item["identity"] == "dhcp-interface-ha" for item in outcome["logs"]))
        self.assertTrue(any("setup_completed" in item["message"] for item in outcome["logs"]))

    def test_setup_rejects_nonempty_or_malformed_native_members(self):
        for members in (["hn1"], "invalid"):
            outcome = run_controller("configure_success", flags={"inventory_members": members})
            self.assertEqual(outcome["response"]["setup_stage"], "none")
            self.assertEqual(outcome["save_count"], 0)

    def test_configure_honors_enable_only_after_native_assignment_is_verified(self):
        for case in ("configure_success", "configure_already_mapped", "configure_exact_pending", "configure_missing_device_retry"):
            with self.subTest(case=case):
                outcome = run_controller(case, flags={"enabled": "1"})
                self.assertEqual(outcome["response"]["result"], "saved")
                self.assertTrue(outcome["response"]["applied"])
                self.assertEqual(outcome["shared"]["enabled"], "1")
                self.assertEqual(outcome["events"][-1], "dhcp_interface_ha apply")
        failed = run_controller("configure_apply_failed", flags={"enabled": "1"})
        self.assertEqual(failed["shared"]["enabled"], "0")

    def test_unchanged_enabled_save_does_not_relink(self):
        outcome = run_controller("save_unchanged_enabled", action="settings")
        self.assertEqual(outcome["response"]["result"], "saved")
        self.assertEqual(outcome["assignments"]["wan"], "dhcpha0lagg")
        self.assertEqual(outcome["pending"], [])
        self.assertNotIn("interface apply", outcome["events"])
        self.assertNotIn("dhcp_interface_ha prepare_setup", outcome["events"])
        self.assertEqual(outcome["shared"]["enabled"], "1")

    def test_configure_defaults_empty_shared_mac_to_selected_interface(self):
        outcome = run_controller("configure_default_mac")

        self.assertEqual(outcome["response"]["result"], "saved")
        self.assertTrue(outcome["response"]["assignment_verified"])
        self.assertEqual(outcome["shared"]["shared_mac"], "02:11:22:33:44:01")

    def test_disabled_restores_native_assignment_before_clearing_plugin_identity(self):
        for case in ("teardown_success", "teardown_disabled"):
            with self.subTest(case=case):
                outcome = run_controller(case, action="settings")

                self.assertEqual(outcome["response"]["result"], "saved")
                self.assertTrue(outcome["response"]["cleared"])
                self.assertEqual(outcome["assignments"]["wan"], "hn1")
                self.assertEqual(outcome["pending"], [])
                self.assertEqual(outcome["local"], {"managed_interface": "", "carrier": ""})
                self.assertEqual(outcome["shared"]["shared_mac"], "02:11:22:33:44:55")
                self.assertIn("interface apply", outcome["events"])

    def test_disabled_retains_carrier_when_native_restore_fails_or_conflicts(self):
        failed = run_controller("teardown_apply_failed", action="settings")
        self.assertEqual(failed["response"]["result"], "staged")
        self.assertFalse(failed["response"]["cleared"])
        self.assertEqual(failed["assignments"]["wan"], "dhcpha0lagg")
        self.assertEqual(failed["local"], {"managed_interface": "wan", "carrier": "hn1"})
        self.assertEqual(failed["shared"]["enabled"], "0")

        conflict = run_controller("teardown_unrelated_pending", action="settings")
        self.assertEqual(conflict["response"]["result"], "staged")
        self.assertFalse(conflict["response"]["cleared"])
        self.assertEqual(conflict["assignments"]["wan"], "dhcpha0lagg")
        self.assertEqual(conflict["local"], {"managed_interface": "wan", "carrier": "hn1"})

    def test_revision_validation_and_unrelated_pending_edits_block_before_mutation(self):
        stale = run_controller("configure_stale_revision")
        self.assertEqual(stale["response"]["result"], "conflict")
        self.assertEqual(stale["response"]["setup_stage"], "none")
        self.assertEqual(stale["save_count"], 0)
        self.assertNotIn("dhcp_interface_ha prepare_setup", stale["events"])

        invalid = run_controller("configure_invalid")
        self.assertEqual(invalid["response"]["setup_stage"], "none")
        self.assertEqual(invalid["save_count"], 0)
        self.assertNotIn("dhcp_interface_ha prepare_setup", invalid["events"])

        pending = run_controller("configure_unrelated_pending")
        self.assertEqual(pending["response"]["result"], "conflict")
        self.assertEqual(pending["save_count"], 0)
        self.assertNotIn("dhcp_interface_ha prepare_setup", pending["events"])
        self.assertEqual(pending["pending"]["lan"]["pending_if"], "hn3")

    def test_partial_native_apply_keeps_carrier_and_exact_pending_retry(self):
        known_failure = run_controller("configure_apply_failed")
        self.assertEqual(known_failure["response"]["result"], "failed")
        self.assertFalse(known_failure["response"]["applied"])
        self.assertFalse(known_failure["response"]["assignment_verified"])
        self.assertEqual(known_failure["response"]["setup_stage"], "assignment_staged")
        self.assertEqual(known_failure["local"]["carrier"], "hn1")
        self.assertEqual(known_failure["assignments"]["wan"], "hn1")
        self.assertEqual(known_failure["pending"]["wan"]["pending_if"], "dhcpha0lagg")
        self.assertFalse(known_failure["locked"])

        timeout = run_controller("configure_timeout")
        self.assertEqual(timeout["response"]["result"], "failed")
        self.assertIsNone(timeout["response"]["applied"])
        self.assertFalse(timeout["response"]["assignment_verified"])
        self.assertEqual(timeout["response"]["setup_stage"], "assignment_staged")
        self.assertEqual(timeout["assignments"]["wan"], "hn1")
        self.assertEqual(timeout["pending"]["wan"]["pending_if"], "dhcpha0lagg")
        self.assertFalse(timeout["locked"])

    def test_native_save_exception_releases_lock_before_fresh_assignment_readback(self):
        outcome = run_controller("configure_native_save_unknown")

        self.assertEqual(outcome["response"]["result"], "failed")
        self.assertIsNone(outcome["response"]["applied"])
        self.assertFalse(outcome["response"]["assignment_verified"])
        self.assertEqual(outcome["response"]["setup_stage"], "assignment_staged")
        self.assertEqual(outcome["assignments"]["wan"], "hn1")
        self.assertEqual(outcome["pending"]["wan"]["pending_if"], "dhcpha0lagg")
        self.assertFalse(outcome["locked"])

    def test_native_apply_success_stays_partial_until_runtime_readback_is_verified(self):
        unknown = run_controller("configure_runtime_readback_unknown")
        self.assertEqual(unknown["response"]["result"], "failed")
        self.assertTrue(unknown["response"]["applied"])
        self.assertIsNone(unknown["response"]["assignment_verified"])
        self.assertEqual(unknown["response"]["setup_stage"], "assignment_applied")
        self.assertEqual(unknown["assignments"]["wan"], "dhcpha0lagg")
        self.assertFalse(unknown["locked"])

        mismatch = run_controller("configure_runtime_mismatch")
        self.assertEqual(mismatch["response"]["result"], "failed")
        self.assertTrue(mismatch["response"]["applied"])
        self.assertFalse(mismatch["response"]["assignment_verified"])
        self.assertEqual(mismatch["response"]["setup_stage"], "assignment_applied")
        self.assertEqual(mismatch["assignments"]["wan"], "dhcpha0lagg")

    def test_configure_retry_accepts_only_known_setup_and_repairs_missing_owned_device(self):
        pending = run_controller("configure_exact_pending")
        self.assertEqual(pending["response"]["result"], "saved")
        self.assertTrue(pending["response"]["assignment_verified"])
        self.assertIn("interface apply", pending["events"])

        already_mapped = run_controller("configure_already_mapped")
        self.assertEqual(already_mapped["response"]["result"], "saved")
        self.assertEqual(already_mapped["response"]["setup_stage"], "verified")
        self.assertNotIn("interface apply", already_mapped["events"])

        missing_device = run_controller("configure_missing_device_retry")
        self.assertEqual(missing_device["response"]["result"], "saved")
        self.assertTrue(missing_device["response"]["assignment_verified"])
        self.assertIn("dhcp_interface_ha prepare_setup", missing_device["events"])
        self.assertNotIn("interface apply", missing_device["events"])

    def test_native_privilege_and_read_only_boundaries_prevent_setup_or_sync_writes(self):
        denied = run_controller("configure_native_acl")
        self.assertEqual(denied["response"]["result"], "failed")
        self.assertEqual(denied["save_count"], 0)
        self.assertNotIn("dhcp_interface_ha prepare_setup", denied["events"])

        read_only = run_controller("configure_readonly")
        self.assertEqual(read_only["response"]["result"], "denied")
        self.assertEqual(read_only["save_count"], 0)

        sync_denied = run_controller("sync_native_acl", action="sync")
        self.assertEqual(sync_denied["response"]["result"], "failed")
        self.assertEqual(sync_denied["save_count"], 0)

        sync_read_only = run_controller("sync_readonly", action="sync")
        self.assertEqual(sync_read_only["response"]["result"], "denied")
        self.assertEqual(sync_read_only["save_count"], 0)

    def test_sync_unions_only_this_plugin_and_is_idempotent(self):
        enabled = run_controller("sync_success", action="sync")
        self.assertEqual(enabled["response"], {"result": "saved", "changed": True, "selected": True, "error": None})
        self.assertEqual(enabled["sync"]["syncitems"], "interfaces,firewall,dhcp-interface-ha")
        self.assertEqual(enabled["save_count"], 1)
        self.assertNotIn("interface apply", enabled["events"])
        self.assertTrue(any("sync_selection_changed" in item["message"] for item in enabled["logs"]))

        unchanged = run_controller("sync_already_selected", action="sync")
        self.assertEqual(unchanged["response"]["result"], "saved")
        self.assertFalse(unchanged["response"]["changed"])
        self.assertTrue(unchanged["response"]["selected"])
        self.assertEqual(unchanged["save_count"], 0)
        self.assertEqual(unchanged["logs"], [])

    def test_sync_requires_destination_and_distinguishes_unknown_save(self):
        no_destination = run_controller("sync_no_destination", action="sync")
        self.assertEqual(no_destination["response"]["result"], "failed")
        self.assertFalse(no_destination["response"]["selected"])
        self.assertEqual(no_destination["save_count"], 0)

        invalid = run_controller("sync_invalid", action="sync")
        self.assertEqual(invalid["response"]["result"], "failed")
        self.assertEqual(invalid["save_count"], 0)

        unknown = run_controller("sync_save_unknown", action="sync")
        self.assertEqual(unknown["response"]["result"], "failed")
        self.assertIsNone(unknown["response"]["changed"])
        self.assertFalse(unknown["response"]["selected"])
        self.assertEqual(unknown["sync"]["syncitems"], "interfaces,firewall")
        self.assertFalse(unknown["locked"])


if __name__ == "__main__":
    unittest.main()
