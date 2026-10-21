import json
import secrets
import unittest
from pathlib import Path

from php_fixture import run_php_fixture


PLUGIN = Path(__file__).resolve().parents[1]
FIXTURE = PLUGIN / "tests/fixtures/settings_streamlining_controller.php"
CONTROLLER = PLUGIN / "src/opnsense/mvc/app/controllers/OPNsense/DhcpInterfaceHa/Api/SettingsController.php"
BRIDGE = PLUGIN / "src/opnsense/mvc/app/models/OPNsense/DhcpInterfaceHa/SetupAssignmentBridge.php"


def run_controller(case, action="configure", flags=None):
    return run_php_fixture(FIXTURE, CONTROLLER, BRIDGE, case, action, json.dumps(flags or {}))


class SettingsStreamliningTests(unittest.TestCase):
    def test_standby_options_exclude_disabled_lan_and_loopback(self):
        result = run_controller('save_unchanged', action='get', flags={'standby': {}})
        options = result['response']['dhcphalocal']['standby_interface']
        self.assertIn('opt2', options)
        self.assertNotIn('lan', options)
        self.assertNotIn('lo0', options)

    def test_standby_enablement_rejects_live_foreign_default_before_save(self):
        result = run_controller('save_unchanged', action='settings', flags={
            'standby': {'standby_enabled': '1', 'standby_interface': 'opt2', 'standby_vip': '198.51.100.1'},
            'default_route': {'gateway': '203.0.113.1', 'netif': 'wg0'}})
        self.assertFalse(result['response']['saved'])
        self.assertIn('dhcphalocal.standby_enabled', result['response']['validations'])
        self.assertEqual(result['save_count'], 0)
        self.assertEqual(result['local'], {'managed_interface': 'wan', 'carrier': 'hn1'})

    def test_standby_path_uses_selected_internal_interface_and_validates_its_vip(self):
        fields = {'standby_enabled': '1', 'standby_interface': 'opt2', 'standby_vip': '198.51.100.1'}
        good = run_controller('save_unchanged', action='settings', flags={'standby': fields})
        self.assertTrue(good['response']['saved'])
        for key, value in fields.items():
            self.assertEqual(good['local'][key], value)
        for interface, vip in [('lan', '198.51.100.1'), ('opt2', '198.51.100.3'), ('opt2', '203.0.113.100')]:
            with self.subTest(interface=interface, vip=vip):
                bad = run_controller('save_unchanged', action='settings', flags={'standby': dict(fields,
                    standby_interface=interface, standby_vip=vip)})
                self.assertFalse(bad['response']['saved'])
                self.assertIn('dhcphalocal.standby_vip', bad['response']['validations'])
                self.assertEqual(bad['save_count'], 0)

    def test_native_receive_mode_is_saved_before_assignment_apply(self):
        for original in (None, "", "0", "1"):
            with self.subTest(original=original):
                flags = {} if original is None else {"original_promisc": original}
                outcome = run_controller("configure_success", flags=flags)
                self.assertEqual(outcome["response"]["result"], "saved")
                native = outcome["receive_modes"]["wan"]
                self.assertEqual(native["promisc"], "1")
                self.assertEqual(json.loads(native["dhcpha_original_promisc"]),
                                 [original is not None, original or ""])
                self.assertEqual(outcome["applied_receive_modes"][0]["wan"], native)

    def test_missing_native_receive_mode_is_repaired_without_relink_or_apply(self):
        outcome = run_controller("save_unchanged", action="settings", flags={"receive_missing": True})
        self.assertEqual(outcome["response"]["result"], "saved")
        self.assertTrue(outcome["response"]["receive_mode_only"])
        self.assertEqual(outcome["save_count"], 1)
        self.assertEqual(outcome["receive_modes"]["wan"]["promisc"], "1")
        self.assertNotIn("interface apply", outcome["events"])
        self.assertNotIn("dhcp_interface_ha apply", outcome["events"])
        denied = run_controller("save_unchanged", action="settings", flags={
            "receive_missing": True,
            "denied_routes": ["/api/interfaces/assignment/set_item/wan"],
        })
        self.assertEqual(denied["save_count"], 0)
        self.assertEqual(denied["response"]["result"], "failed")

    def test_receive_mode_restore_preserves_original_values_and_rejects_bad_backup(self):
        for original in (None, "", "0", "1"):
            with self.subTest(original=original):
                outcome = run_controller("teardown_success", action="settings", flags={
                    "receive_backup": json.dumps([original is not None, original or ""]),
                })
                self.assertEqual(outcome["response"]["result"], "saved")
                expected = [] if original is None else {"promisc": original}
                self.assertEqual(outcome["receive_modes"]["wan"], expected)
                self.assertEqual(outcome["applied_receive_modes"][0]["wan"], expected)
        invalid = run_controller("save_unchanged", action="settings", flags={"receive_backup": "invalid"})
        self.assertEqual(invalid["response"]["result"], "failed")
        self.assertEqual(invalid["save_count"], 0)
        self.assertNotIn("dhcp_interface_ha apply", invalid["events"])

    def test_progress_is_live_request_scoped_and_not_configuration_authority(self):
        outcome = run_controller('configure_success', flags={'progress_id': secrets.token_hex(16)})
        applying = next(item['progress'] for item in outcome['progress_reads'] if item['event'] == 'interface apply')
        self.assertEqual(applying['state'], 'running')
        self.assertEqual(applying['steps'][-1]['state'], 'running')
        self.assertIn('assignment', applying['steps'][-1]['message'])
        self.assertEqual(outcome['progress_checks']['own']['state'], 'success')
        for key in ('wrong_id', 'other_user', 'expired'):
            self.assertEqual(outcome['progress_checks'][key]['state'], 'unavailable')
        invalid = run_controller('configure_success', flags={'progress_id': '../invalid'})
        self.assertEqual(invalid['save_count'], 0)
        self.assertEqual(invalid['events'], [])

    def test_progress_does_not_confuse_saved_settings_with_runtime_readiness(self):
        failed = run_controller('configure_success', flags={'enabled': '1', 'runtime_state': 'FAULT'})['response']
        self.assertTrue(failed['saved'])
        self.assertTrue(failed['applied'])
        self.assertEqual(failed['progress']['state'], 'failed')
        self.assertIn('no link', failed['progress']['steps'][-1]['message'])

    def test_configure_derives_carrier_and_verifies_native_apply(self):
        for case, mac in (("configure_success", "02:11:22:33:44:55"),
                          ("configure_default_mac", "02:11:22:33:44:01")):
            with self.subTest(case=case):
                outcome = run_controller(case)
                self.assertEqual(outcome["shared"]["shared_mac"], mac)

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
                progress = outcome["response"]["progress"]
                self.assertEqual(progress["state"], "success")
                self.assertTrue(progress["steps"])
                self.assertTrue(all(step["state"] == "success" for step in progress["steps"]))

    def test_setup_rejects_nonempty_or_malformed_native_members(self):
        for members in (["hn1"], "invalid"):
            outcome = run_controller("configure_success", flags={"inventory_members": members})
            self.assertEqual(outcome["response"]["setup_stage"], "none")
            self.assertEqual(outcome["save_count"], 0)

    def test_unchanged_settings_and_stale_revision_have_no_side_effects(self):
        for case, result, enabled in (
            ("save_unchanged_enabled", "unchanged", "1"),
            ("save_unchanged_disabled", "unchanged", "0"),
            ("save_unchanged_stale", "conflict", "1"),
        ):
            with self.subTest(case=case):
                outcome = run_controller(case, action="settings")
                self.assertEqual(outcome["response"]["result"], result)
                self.assertEqual(outcome["save_count"], 0)
                self.assertEqual(outcome["events"], [])
                self.assertEqual(outcome["logs"], [])
                self.assertEqual(outcome["shared"]["enabled"], enabled)
                self.assertEqual(outcome["assignments"]["wan"], "dhcpha0lagg" if enabled == "1" else "hn1")
                self.assertEqual(outcome["pending"], [])
                if result == "unchanged":
                    self.assertEqual(outcome["response"]["progress"]["state"], "success")

    def test_disabled_restores_native_assignment_before_clearing_plugin_identity(self):
        for case in ("teardown_success", "teardown_disabled", "api_save"):
            with self.subTest(case=case):
                outcome = run_controller(case, action="settings", flags={
                    "post_shared": {"enabled": "1", "failback_delay": "60"}})

                self.assertEqual(outcome["response"]["result"], "saved")
                self.assertTrue(outcome["response"]["cleared"])
                self.assertEqual(outcome["assignments"]["wan"], "hn1")
                self.assertEqual(outcome["pending"], [])
                self.assertEqual(outcome["local"], {"managed_interface": "", "carrier": "",
                    "standby_enabled": "0", "standby_interface": "", "standby_vip": ""})
                self.assertEqual(outcome["shared"]["shared_mac"], "02:11:22:33:44:55")
                self.assertEqual(outcome["shared"]["enabled"], "0")
                self.assertEqual(outcome["shared"]["failback_delay"], "0")
                if case != "api_save":
                    self.assertIn("interface apply", outcome["events"])
                    self.assertLess(outcome["events"].index("dhcp_interface_ha apply"),
                                    outcome["events"].index("interface apply"))
                else:
                    self.assertNotIn("interface apply", outcome["events"])

    def test_disabled_teardown_retry_requires_fencing_before_native_restore(self):
        for case in ("teardown_success", "teardown_disabled"):
            for flags in ({"controller_apply": "failed"}, {"inventory_members": {"hn1": {}}},
                          {"status_unavailable": True}):
                with self.subTest(case=case, flags=flags):
                    outcome = run_controller(case, action="settings", flags=flags)
                    self.assertNotIn("interface apply", outcome["events"])
                    self.assertFalse(outcome["response"].get("cleared", False))
                    self.assertEqual(outcome["assignments"]["wan"], "dhcpha0lagg")
                    self.assertEqual(outcome["local"]["carrier"], "hn1")
                    self.assertEqual(outcome["shared"]["enabled"], "0")

    def test_disabled_retains_carrier_when_native_restore_fails_or_conflicts(self):
        for case in ("teardown_apply_failed", "teardown_unrelated_pending"):
            with self.subTest(case=case):
                outcome = run_controller(case, action="settings")
                self.assertEqual(outcome["response"]["result"], "staged")
                self.assertFalse(outcome["response"]["cleared"])
                self.assertEqual(outcome["assignments"]["wan"], "dhcpha0lagg")
                self.assertEqual(outcome["local"], {"managed_interface": "wan", "carrier": "hn1"})
                self.assertEqual(outcome["shared"]["enabled"], "0")

    def test_revision_validation_and_unrelated_pending_edits_block_before_mutation(self):
        for case, result in (("configure_stale_revision", "conflict"),
                             ("configure_invalid", "failed"), ("configure_unrelated_pending", "conflict")):
            with self.subTest(case=case):
                outcome = run_controller(case)
                self.assertEqual(outcome["response"]["result"], result)
                self.assertEqual(outcome["response"]["setup_stage"], "none")
                self.assertEqual(outcome["save_count"], 0)
                self.assertNotIn("dhcp_interface_ha prepare_setup", outcome["events"])
                if case == "configure_unrelated_pending":
                    self.assertEqual(outcome["pending"]["lan"]["pending_if"], "hn3")

    def test_partial_setup_preserves_stage_outcome_and_retry_state(self):
        for case, applied, verified, stage in (
            ("configure_apply_failed", False, False, "assignment_staged"),
            ("configure_timeout", None, False, "assignment_staged"),
            ("configure_native_save_unknown", None, False, "assignment_staged"),
            ("configure_runtime_readback_unknown", True, None, "assignment_applied"),
            ("configure_runtime_mismatch", True, False, "assignment_applied"),
        ):
            with self.subTest(case=case):
                outcome = run_controller(case, flags={"enabled": "1"})
                response = outcome["response"]
                self.assertEqual(response["result"], "failed")
                self.assertIs(response["applied"], applied)
                self.assertIs(response["assignment_verified"], verified)
                self.assertEqual(response["setup_stage"], stage)
                self.assertEqual(outcome["shared"]["enabled"], "0")
                self.assertEqual(outcome["local"]["carrier"], "hn1")
                self.assertFalse(outcome["locked"])
                self.assertEqual(outcome["assignments"]["wan"], "dhcpha0lagg" if applied else "hn1")
                if stage == "assignment_staged":
                    self.assertEqual(outcome["pending"]["wan"]["pending_if"], "dhcpha0lagg")
                    progress = response["progress"]
                    self.assertEqual(progress["state"], "failed" if applied is False else "unknown")
                    self.assertEqual(progress["steps"][-1]["state"], progress["state"])
                    self.assertIn("assignment", progress["steps"][-1]["message"].lower())
                    self.assertTrue(any(step["state"] == "success" for step in progress["steps"][:-1]))
                else:
                    self.assertEqual(outcome["pending"], [])

    def test_setup_and_retries_honor_enable_after_verified_assignment(self):
        for case, native_apply in (("configure_success", True), ("configure_exact_pending", True),
                                   ("configure_already_mapped", False), ("configure_missing_device_retry", False)):
            for enabled in ("0", "1"):
                with self.subTest(case=case, enabled=enabled):
                    outcome = run_controller(case, flags={"enabled": enabled})
                    self.assertEqual(outcome["response"]["result"], "saved")
                    self.assertTrue(outcome["response"]["applied"])
                    self.assertTrue(outcome["response"]["assignment_verified"])
                    self.assertEqual(outcome["response"]["setup_stage"], "verified")
                    self.assertEqual(outcome["shared"]["enabled"], enabled)
                    self.assertEqual("interface apply" in outcome["events"], native_apply)
                    if enabled == "1":
                        self.assertEqual(outcome["events"][-1], "dhcp_interface_ha apply")
                    if case == "configure_missing_device_retry":
                        self.assertIn("dhcp_interface_ha prepare_setup", outcome["events"])

    def test_native_privilege_and_read_only_boundaries_prevent_setup_or_sync_writes(self):
        for action in ("configure", "sync"):
            for case, result in (("native_acl", "failed"), ("readonly", "denied")):
                with self.subTest(action=action, case=case):
                    outcome = run_controller(action + "_" + case, action=action)
                    self.assertEqual(outcome["response"]["result"], result)
                    self.assertEqual(outcome["save_count"], 0)
                    self.assertNotIn("dhcp_interface_ha prepare_setup", outcome["events"])

    def test_sync_outcomes_preserve_selection_and_committed_config(self):
        for case, result, changed, selected, saves in (
            ('success', 'saved', True, True, 1),
            ('already_selected', 'saved', False, True, 0),
            ('no_destination', 'failed', False, False, 0),
            ('invalid', 'failed', False, False, 0),
            ('save_unknown', 'failed', None, False, 0),
        ):
            with self.subTest(case=case):
                outcome = run_controller('sync_' + case, action='sync')
                response = outcome['response']
                self.assertEqual(response['result'], result)
                self.assertIs(response['changed'], changed)
                self.assertIs(response['selected'], selected)
                self.assertEqual(outcome['save_count'], saves)
                self.assertEqual(outcome['sync']['syncitems'], 'interfaces,firewall' + (',dhcp-interface-ha' if selected else ''))
                self.assertFalse(outcome['locked'])
                self.assertNotIn('interface apply', outcome['events'])
                if case == 'success':
                    self.assertIsNone(response['error'])
                    self.assertTrue(any('sync_selection_changed' in item['message'] for item in outcome['logs']))
                if case == 'already_selected':
                    self.assertEqual(outcome['logs'], [])

    def test_settings_save_both_roots_once_and_keep_assignment_local(self):
        for interface in ('wan', 'opt7'):
            with self.subTest(interface=interface):
                result = run_controller('api_save', 'settings', {
                    'post_shared': {'shared_mac': '02:11:22:33:44:66'},
                    'post_local': {'managed_interface': interface, 'carrier': 'hn2'},
                })
                self.assertEqual(result['response']['result'], 'saved')
                self.assertEqual(result['save_count'], 1)
                self.assertEqual(result['shared']['shared_mac'], '02:11:22:33:44:66')
                self.assertEqual(result['local']['managed_interface'], interface)
                self.assertEqual(result['local']['carrier'], 'hn2')
                self.assertNotIn('managed_interface', result['shared'])
                self.assertNotIn('managed_interface', result['loaded_settings']['dhcphashared'])
                self.assertEqual(result['events'][-1], 'dhcp_interface_ha apply')
                self.assertFalse(result['locked'])

    def test_settings_reject_stale_unknown_or_live_identity_changes_before_mutation(self):
        for case, flags, expected in (
            ('api_save', {'changed_shared': {'shared_mac': '02:11:22:33:44:99'}}, 'conflict'),
            ('api_save', {'changed_local': {'managed_interface': 'opt7'}}, 'conflict'),
            ('api_save', {'post_shared': {'managed_interface': 'wan'}}, 'failed'),
            ('api_save', {'post_shared': {'unrecognized': 'must not persist'}}, 'failed'),
            ('api_save', {'read_only': True}, 'denied'),
            ('save_unchanged', {'post_shared': {'shared_mac': '02:11:22:33:44:99'}}, 'failed'),
            ('save_unchanged', {'post_local': {'managed_interface': 'opt7'}}, 'failed'),
        ):
            with self.subTest(case=case, flags=flags):
                result = run_controller(case, 'settings', flags)
                self.assertEqual(result['response']['result'], expected)
                self.assertEqual(result['save_count'], 0)
                self.assertNotIn('dhcp_interface_ha apply', result['events'])
                self.assertFalse(result['locked'])

    def test_enable_requires_fresh_detached_evidence(self):
        for attachment, expected in (('FENCED', 'saved'), ('ATTACHED', 'failed')):
            with self.subTest(attachment=attachment):
                result = run_controller('save_unchanged', 'settings', {
                    'saved_shared': {'enabled': '0'}, 'post_shared': {'enabled': '1'},
                    'attachment': attachment,
                })
                self.assertEqual(result['response']['result'], expected)
                self.assertEqual(result['save_count'], int(expected == 'saved'))

    def test_save_and_retry_apply_keep_failure_and_unknown_outcomes_distinct(self):
        for action in ('settings', 'service'):
            for outcome, applied in (('success', True), ('failed', False), ('empty', None),
                                     ('timeout', None), ('no_status', None)):
                with self.subTest(action=action, outcome=outcome):
                    result = run_controller('api_save', action, {
                        'post_shared': {'shared_mac': ''},
                        'post_local': {'managed_interface': 'wan', 'carrier': ''},
                        'controller_apply': outcome, 'device_present': False,
                    })
                    response = result['response']
                    self.assertEqual(result['save_count'], int(action == 'settings'))
                    self.assertIs(response['applied'], applied)
                    self.assertEqual(bool(response['error']), applied is not True)
                    self.assertEqual(result['events'].count('dhcp_interface_ha apply'), 1)
                    self.assertFalse(result['locked'])
                    if action == 'settings':
                        self.assertEqual(result['shared']['shared_mac'], '')
                        self.assertEqual(result['local']['carrier'], '')
                    if outcome == 'no_status':
                        self.assertIsNone(response.get('status'))
                    else:
                        self.assertEqual(response['status']['state'], 'DISABLED')


if __name__ == "__main__":
    unittest.main()
