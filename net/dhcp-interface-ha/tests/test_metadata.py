from pathlib import Path
import re
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
            PLUGIN / "src/opnsense/mvc/app/controllers/OPNsense/DhcpInterfaceHa/forms/shared.xml",
            PLUGIN / "src/opnsense/mvc/app/controllers/OPNsense/DhcpInterfaceHa/forms/local.xml",
        ]
        for path in xml_files:
            with self.subTest(path=path):
                ET.parse(path)

    def test_local_carrier_is_not_in_shared_model(self):
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

    def test_reserved_current_carrier_has_runtime_fallback(self):
        local_model = (PLUGIN / "src/opnsense/mvc/app/models/OPNsense/DhcpInterfaceHa/Local.php").read_text()
        status = (PLUGIN / "src/opnsense/mvc/app/controllers/OPNsense/DhcpInterfaceHa/Api/StatusController.php").read_text()
        self.assertIn("if ($group === null)", local_model)
        self.assertIn("$runtime['is_physical']", local_model)
        self.assertIn("current DHCP Interface HA carrier", status)

    def test_uninstall_guard_uses_stable_device_name(self):
        pre = (PLUGIN / "+PRE_DEINSTALL.pre").read_text()
        post = (PLUGIN / "+POST_DEINSTALL.post").read_text()
        self.assertIn("dhcpha0lagg", pre)
        self.assertIn("dhcpha0lagg", post)
        self.assertNotIn("<if>dhcpha0</if>", pre)


if __name__ == "__main__":
    unittest.main()
