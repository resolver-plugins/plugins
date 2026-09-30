from pathlib import Path
import json
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
import unittest


PLUGIN = Path(__file__).resolve().parents[1]


class MetadataTests(unittest.TestCase):
    def test_mvc_xml_is_well_formed(self):
        xml_files = [
            PLUGIN / "src/opnsense/mvc/app/models/OPNsense/DhcpInterfaceHa/Shared.xml",
            PLUGIN / "src/opnsense/mvc/app/models/OPNsense/DhcpInterfaceHa/Local.xml",
            PLUGIN / "src/opnsense/mvc/app/models/OPNsense/DhcpInterfaceHa/Menu/Menu.xml",
            PLUGIN / "src/opnsense/mvc/app/models/OPNsense/DhcpInterfaceHa/ACL/ACL.xml",
            PLUGIN / "src/opnsense/mvc/app/controllers/OPNsense/DhcpInterfaceHa/forms/settings.xml",
        ]
        for path in xml_files:
            with self.subTest(path=path):
                ET.parse(path)

    def test_local_mapping_is_not_in_shared_model(self):
        shared = (
            PLUGIN
            / "src/opnsense/mvc/app/models/OPNsense/DhcpInterfaceHa/Shared.xml"
        ).read_text()
        local = (
            PLUGIN
            / "src/opnsense/mvc/app/models/OPNsense/DhcpInterfaceHa/Local.xml"
        ).read_text()
        self.assertNotIn("<carrier", shared)
        self.assertIn("<carrier", local)
        self.assertNotIn("<managed_interface", shared)
        self.assertIn("<managed_interface", local)
        self.assertNotIn("<Default>wan</Default>", local)
        self.assertIn("<version>1.1.0</version>", shared)
        self.assertIn("<version>1.2.0</version>", local)
        for field in ('standby_enabled', 'standby_interface', 'standby_vip'):
            self.assertIn('<' + field, local)
            self.assertNotIn('<' + field, shared)

    def test_shared_config_registers_without_local_config(self):
        integration = (
            PLUGIN / "src/etc/inc/plugins.inc.d/dhcp_interface_ha.inc"
        ).read_text()
        self.assertIn(
            "'section' => 'OPNsense.DhcpInterfaceHaShared'",
            integration,
        )
        self.assertNotIn(
            "'section' => 'OPNsense.DhcpInterfaceHaLocal'",
            integration,
        )

    def test_virtual_device_name_avoids_core_lagg_collision(self):
        # OPNsense 26.7 legacy virtual classification splits names on digits
        # and looks for known tokens such as "lagg". The plugin name must
        # contain that token after a digit without beginning with "lagg".
        device = "dhcpha0lagg"
        tokens = [token for token in re.split(r"\d+", device) if token]
        self.assertIn("lagg", tokens)
        self.assertFalse(device.startswith("lagg"))

    def test_device_registration_is_fail_closed(self):
        integration = (
            PLUGIN / "src/etc/inc/plugins.inc.d/dhcp_interface_ha.inc"
        ).read_text()
        self.assertIn("'pattern' => '^dhcpha[0-9]+lagg$'", integration)
        self.assertIn("'spoofmac' => false", integration)
        self.assertIn("'volatile' => true", integration)
        self.assertIn("'name' => 'dhcpha0lagg'", integration)

    def test_registration_functions_are_unique(self):
        integration = (
            PLUGIN / "src/etc/inc/plugins.inc.d/dhcp_interface_ha.inc"
        ).read_text()
        self.assertEqual(integration.count("function dhcp_interface_ha_devices("), 1)
        self.assertEqual(integration.count("function dhcp_interface_ha_prepare_device("), 1)
        self.assertEqual(integration.count("function dhcp_interface_ha_xmlrpc_sync("), 1)

    def test_plugin_reserves_migrated_local_carrier(self):
        integration = (PLUGIN / "src/etc/inc/plugins.inc.d/dhcp_interface_ha.inc").read_text()
        self.assertIn("$dhcpha_assigned", integration)
        self.assertIn("'exclude' => $exclude", integration)
        self.assertIn("(string)$interface->if === 'dhcpha0lagg'", integration)

    def test_saved_reserved_physical_carrier_remains_eligible_by_runtime_shape(self):
        php = shutil.which("php")
        if php is None:
            self.fail("PHP CLI is required for the carrier model behavior check")
        source = PLUGIN / "src/opnsense/mvc/app/models/OPNsense/DhcpInterfaceHa/Local.php"
        script = f'''\
namespace OPNsense\\Base {{ class BaseModel {{}} }}
namespace OPNsense\\Base\\Messages {{ class Message {{}} }}
namespace OPNsense\\Core {{ class Config {{ public static function getInstance() {{ return new self(); }} public function object() {{ return new \\stdClass(); }} }} }}
namespace {{
    require {str(source)!r};
    $valid = \\OPNsense\\DhcpInterfaceHa\\Local::carrierRuntimeEligibility(
        'em0', [], ['em0' => ['is_physical' => true, 'macaddr' => '02:11:22:33:44:55']]
    );
    $vlan = \\OPNsense\\DhcpInterfaceHa\\Local::carrierRuntimeEligibility(
        'vlan0', [], ['vlan0' => ['vlan' => ['tag' => 4], 'macaddr' => '02:11:22:33:44:66']]
    );
    $malformed = \\OPNsense\\DhcpInterfaceHa\\Local::carrierRuntimeEligibility(
        'em0', [], [
            'em0' => ['is_physical' => true, 'macaddr' => '02:11:22:33:44:55'],
            'lagg0' => ['laggport' => 'em0'],
        ]
    );
    $carp = \\OPNsense\\DhcpInterfaceHa\\Local::carrierRuntimeEligibility(
        'em0', [], ['em0' => ['is_physical' => true, 'macaddr' => '02:11:22:33:44:55', 'carp' => ['10' => ['status' => 'BACKUP']]]]
    );
    echo json_encode([$valid, $vlan, $malformed, $carp]);
}}
'''
        result = subprocess.run([php, "-r", script], check=True, capture_output=True, text=True)
        self.assertEqual(json.loads(result.stdout), [
            None,
            "requires VLAN carrier qualification; this experimental release supports Ethernet adapters only",
            "cannot be checked because the runtime membership inventory is malformed",
            "already carries runtime CARP instances",
        ])

    def test_settings_ui_is_one_form_with_both_model_roots(self):
        form = ET.parse(PLUGIN / "src/opnsense/mvc/app/controllers/OPNsense/DhcpInterfaceHa/forms/settings.xml").getroot()
        fields = {item.findtext("id"): item.findtext("type") for item in form.findall("field") if item.findtext("id")}
        self.assertIn("dhcphalocal.managed_interface", fields)
        self.assertNotIn("dhcphalocal.carrier", fields)
        self.assertEqual(fields["dhcphalocal.managed_interface"], "dropdown")
        self.assertFalse((PLUGIN / "src/opnsense/mvc/app/controllers/OPNsense/DhcpInterfaceHa/Api/SharedController.php").exists())
        self.assertFalse((PLUGIN / "src/opnsense/mvc/app/controllers/OPNsense/DhcpInterfaceHa/Api/LocalController.php").exists())
        page = (PLUGIN / "src/opnsense/mvc/app/views/OPNsense/DhcpInterfaceHa/index.volt").read_text()
        self.assertEqual(page.count("partial(\"layout_partials/base_form\""), 1)

    def test_status_api_keeps_managed_observation_and_lease_availability_distinct(self):
        php = shutil.which("php")
        if php is None:
            self.fail("PHP CLI is required for the status API behavior check")
        harness = PLUGIN / "tests/fixtures/status_controller.php"
        controller = PLUGIN / "src/opnsense/mvc/app/controllers/OPNsense/DhcpInterfaceHa/Api/StatusController.php"
        result = subprocess.run([php, str(harness), str(controller)], check=True, capture_output=True, text=True)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["result"], "ok")
        self.assertEqual(payload["controller"]["state"], "STANDBY")
        self.assertEqual(payload["managed"]["identifier"], "wan")
        self.assertEqual(payload["connection"]["ipv4"]["address"], {"address": "192.0.2.10", "prefix": 24})
        self.assertEqual(payload["connection"]["dhcp"]["state"], "unavailable")
        self.assertFalse(payload["connection"]["dhcp"]["available"])
        self.assertEqual(payload["connection"]["gateway"]["status"], "unknown")
        self.assertEqual(payload["ha"]["peer_readiness"], "unverified")
        self.assertFalse(payload["removal"]["allowed"])
        self.assertNotIn("private_fixture_secret", json.dumps(payload))

        malformed = subprocess.run(
            [php, str(harness), str(controller), "malformed"],
            check=True,
            capture_output=True,
            text=True,
        )
        malformed_payload = json.loads(malformed.stdout)
        self.assertEqual(malformed_payload["result"], "unavailable")
        self.assertEqual(malformed_payload["controller"]["state"], "UNKNOWN")
        self.assertIn("controller", malformed_payload["errors"])

        bad_address = subprocess.run(
            [php, str(harness), str(controller), "bad_address"],
            check=True,
            capture_output=True,
            text=True,
        )
        address_payload = json.loads(bad_address.stdout)
        self.assertEqual(address_payload["result"], "partial")
        self.assertFalse(address_payload["connection"]["ipv4"]["available"])
        self.assertIn("addresses", address_payload["errors"])

        for case in ("addressless", "unrelated_addressless"):
            result = subprocess.run(
                [php, str(harness), str(controller), case], check=True, capture_output=True, text=True,
            )
            observation = json.loads(result.stdout)
            self.assertEqual(observation["result"], "ok")
            self.assertTrue(observation["connection"]["ipv4"]["available"])
            expected = None if case == "addressless" else {"address": "192.0.2.10", "prefix": 24}
            self.assertEqual(observation["connection"]["ipv4"]["address"], expected)

    def test_settings_api_saves_both_roots_once_and_rejects_stale_or_live_identity_edits(self):
        php = shutil.which("php")
        if php is None:
            self.fail("PHP CLI is required for the settings API behavior check")
        harness = PLUGIN / "tests/fixtures/settings_controller.php"
        controller = PLUGIN / "src/opnsense/mvc/app/controllers/OPNsense/DhcpInterfaceHa/Api/SettingsController.php"

        def run_case(name):
            result = subprocess.run([php, str(harness), str(controller), name], check=True, capture_output=True, text=True)
            return json.loads(result.stdout)

        saved = run_case("success")
        self.assertEqual(saved["response"]["result"], "saved")
        self.assertTrue(saved["response"]["saved"])
        self.assertEqual(saved["save_count"], 1)
        self.assertEqual(saved["save_snapshot"]["shared"]["shared_mac"], "02:11:22:33:44:66")
        self.assertEqual(saved["save_snapshot"]["local"]["carrier"], "hn2")
        self.assertEqual(saved["events"][-1], "dhcp_interface_ha apply")
        self.assertFalse(saved["locked"])

        local_assignment = run_case("local_assignment")
        self.assertEqual(local_assignment["response"]["result"], "saved")
        self.assertEqual(local_assignment["save_snapshot"]["local"]["managed_interface"], "opt7")
        self.assertNotIn("managed_interface", local_assignment["save_snapshot"]["shared"])
        self.assertNotIn("managed_interface", local_assignment["loaded_settings"]["dhcphashared"])
        self.assertEqual(local_assignment["loaded_settings"]["dhcphalocal"]["managed_interface"]["wan"]["selected"], 1)

        for case, result in [("stale_local", "conflict"), ("legacy_field", "failed"), ("enabled_assignment", "failed")]:
            rejected = run_case(case)
            self.assertEqual(rejected["response"]["result"], result)
            self.assertEqual(rejected["save_count"], 0)
            self.assertNotIn("dhcp_interface_ha apply", rejected["events"])
        self.assertIn("dhcphalocal.managed_interface", run_case("enabled_assignment")["response"]["validations"])

        stale = run_case("stale")
        self.assertEqual(stale["response"]["result"], "conflict")
        self.assertEqual(stale["save_count"], 0)
        self.assertNotIn("dhcp_interface_ha apply", stale["events"])
        self.assertFalse(stale["locked"])

        live_edit = run_case("enabled_identity")
        self.assertEqual(live_edit["response"]["result"], "failed")
        self.assertEqual(live_edit["save_count"], 0)
        self.assertFalse(live_edit["locked"])

        draft = run_case("draft")
        self.assertEqual(draft["response"]["result"], "saved")
        self.assertEqual(draft["save_snapshot"]["shared"]["shared_mac"], "")
        self.assertEqual(draft["save_snapshot"]["local"]["carrier"], "")
        self.assertEqual(draft["events"], ["dhcp_interface_ha apply"])

        absent_device = run_case("absent_device")
        self.assertEqual(absent_device["response"]["result"], "saved")
        self.assertEqual(absent_device["save_snapshot"]["local"]["carrier"], "hn2")

        read_only = run_case("readonly")
        self.assertEqual(read_only["response"]["result"], "denied")
        self.assertEqual(read_only["save_count"], 0)

        extra_field = run_case("extra_field")
        self.assertEqual(extra_field["response"]["result"], "failed")
        self.assertEqual(extra_field["save_count"], 0)
        self.assertEqual(extra_field["events"], [])

        enabled = run_case("enable")
        self.assertEqual(enabled["response"]["result"], "saved")
        self.assertEqual(enabled["save_count"], 1)
        self.assertEqual(enabled["save_snapshot"]["shared"]["enabled"], "1")
        self.assertEqual(enabled["events"][-1], "dhcp_interface_ha apply")

        attached_enable = run_case("attached_enable")
        self.assertEqual(attached_enable["response"]["result"], "failed")
        self.assertEqual(attached_enable["save_count"], 0)
        self.assertNotIn("dhcp_interface_ha apply", attached_enable["events"])

    def test_current_mac_suggestion_requires_usable_identity_and_preserves_spoof_precedence(self):
        php = shutil.which("php")
        self.assertIsNotNone(php)
        harness = PLUGIN / "tests/fixtures/status_controller.php"
        controller = PLUGIN / "src/opnsense/mvc/app/controllers/OPNsense/DhcpInterfaceHa/Api/StatusController.php"
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
                result = subprocess.run(
                    [php, str(harness), str(controller), "mac_suggestion", *values],
                    check=True, capture_output=True, text=True,
                )
                managed = json.loads(result.stdout)["managed"]
                self.assertEqual(managed["effective_mac_suggestion"], expected)
                self.assertEqual(managed["mac_source"], source)

    def test_disabled_clears_plugin_settings_preserves_mac_and_requires_verified_detachment(self):
        php = shutil.which("php")
        self.assertIsNotNone(php, "PHP CLI is required for the settings API behavior check")
        harness = PLUGIN / "tests/fixtures/settings_controller.php"
        controller = PLUGIN / "src/opnsense/mvc/app/controllers/OPNsense/DhcpInterfaceHa/Api/SettingsController.php"
        for case in ("clear_none", "clear_enabled", "clear_attached", "clear_unavailable"):
            with self.subTest(case=case):
                result = subprocess.run(
                    [php, str(harness), str(controller), case], check=True, capture_output=True, text=True,
                )
                data = json.loads(result.stdout)
                self.assertEqual(
                    data["loaded_settings"]["dhcphalocal"]["managed_interface"][""]["value"],
                    "Disabled",
                )
                if case in ("clear_none", "clear_enabled"):
                    self.assertEqual(data["response"]["result"], "saved")
                    self.assertTrue(data["response"]["applied"])
                    self.assertEqual(data["save_count"], 2 if case == "clear_enabled" else 1)
                    self.assertEqual(data["save_snapshot"], {
                        "shared": {"enabled": "0", "shared_mac": "02:11:22:33:44:55", "failback_delay": "0"},
                        "local": {"managed_interface": "", "carrier": ""},
                    })
                    self.assertEqual(data["events"][-1], "dhcp_interface_ha apply")
                else:
                    self.assertEqual(data["response"]["result"], "failed")
                    self.assertEqual(data["save_count"], 0)
                    self.assertNotIn("dhcp_interface_ha apply", data["events"])
                self.assertFalse(data["locked"])

        failed_apply = subprocess.run(
            [php, str(harness), str(controller), "clear_enabled", "settings", "failed"],
            check=True, capture_output=True, text=True,
        )
        staged = json.loads(failed_apply.stdout)
        self.assertEqual(staged["response"]["result"], "staged")
        self.assertTrue(staged["response"]["saved"])
        self.assertFalse(staged["response"]["cleared"])
        self.assertEqual(staged["save_count"], 1)
        self.assertEqual(staged["save_snapshot"], {
            "shared": {"enabled": "0", "shared_mac": "02:11:22:33:44:55", "failback_delay": "60"},
            "local": {"managed_interface": "wan", "carrier": "hn1"},
        })
        self.assertEqual(staged["events"].count("dhcp_interface_ha apply"), 1)

    def test_save_and_retry_apply_preserve_failure_and_unknown_results(self):
        php = shutil.which("php")
        self.assertIsNotNone(php, "PHP CLI is required for the apply API behavior check")
        harness = PLUGIN / "tests/fixtures/settings_controller.php"
        controller = PLUGIN / "src/opnsense/mvc/app/controllers/OPNsense/DhcpInterfaceHa/Api/SettingsController.php"
        errors = {
            "settings": {
                False: "Settings were saved, but the controller could not apply them. apply failed Review the plugin Log before using Retry Apply.",
                None: "Settings were saved, but the apply result is unknown. Check status before retrying.",
            },
            "service": {
                False: "Apply failed; check system logs.",
                None: "Apply result is unknown. Refresh status before retrying.",
            },
        }
        for action in errors:
            for outcome, applied in (("success", True), ("failed", False), ("empty", None),
                                     ("timeout", None), ("no_status", None)):
                with self.subTest(action=action, outcome=outcome):
                    result = subprocess.run(
                        [php, str(harness), str(controller), "draft", action, outcome],
                        check=True, capture_output=True, text=True,
                    )
                    payload = json.loads(result.stdout)
                    response = payload["response"]
                    self.assertEqual(payload["save_count"], 1 if action == "settings" else 0)
                    self.assertIs(response["applied"], applied)
                    self.assertEqual(response["error"], None if applied is True else errors[action][applied])
                    events = ["dhcp_interface_ha apply"]
                    if applied is not True:
                        events.append("dhcp_interface_ha status")
                    self.assertEqual(payload["events"], events)
                    if outcome == "no_status":
                        self.assertIsNone(response.get("status"))
                        self.assertEqual("status" in response, action == "service")
                    else:
                        self.assertEqual(response["status"]["state"], "DISABLED")

    def test_uninstall_guard_uses_stable_device_name(self):
        pre = (PLUGIN / "+PRE_DEINSTALL.pre").read_text()
        post = (PLUGIN / "+POST_DEINSTALL.post").read_text()
        self.assertIn("dhcpha0lagg", pre)
        self.assertIn("dhcpha0lagg", post)
        self.assertNotIn("<if>dhcpha0</if>", pre)


if __name__ == "__main__":
    unittest.main()
