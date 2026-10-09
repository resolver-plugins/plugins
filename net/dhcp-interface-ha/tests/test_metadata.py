from pathlib import Path
import json
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
        self.assertEqual((ET.fromstring(local).findtext('items/managed_interface/Default') or '').strip(), '')
        self.assertIn("<version>1.1.0</version>", shared)
        self.assertIn("<version>1.2.0</version>", local)
        for field in ('standby_enabled', 'standby_interface', 'standby_vip'):
            self.assertIn('<' + field, local)
            self.assertNotIn('<' + field, shared)

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

if __name__ == "__main__":
    unittest.main()
