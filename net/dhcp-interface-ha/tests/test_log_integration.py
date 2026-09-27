import shutil
import subprocess
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


PLUGIN = Path(__file__).resolve().parents[1]
ACL_XML = PLUGIN / "src/opnsense/mvc/app/models/OPNsense/DhcpInterfaceHa/ACL/ACL.xml"
CONTROLLER = PLUGIN / "src/opnsense/mvc/app/controllers/OPNsense/DhcpInterfaceHa/IndexController.php"
FIXTURE = PLUGIN / "tests/fixtures/log_controller.php"


class LogIntegrationTests(unittest.TestCase):
    def test_log_acl_is_limited_to_wrapper_and_native_read_routes(self):
        root = ET.parse(ACL_XML).getroot()
        permission = next(
            item for item in root if item.findtext("name") == "Services: DHCP Interface HA: Log File"
        )
        patterns = [item.text for item in permission.findall("./patterns/pattern")]

        self.assertEqual(
            patterns,
            [
                "ui/dhcpinterfaceha/log",
                "api/diagnostics/log/dhcpinterfaceha/core",
                "api/diagnostics/log/dhcpinterfaceha/core/export",
                "api/diagnostics/log/dhcpinterfaceha/core/live",
            ],
        )

    def test_index_controller_preserves_capabilities_and_enforces_log_access(self):
        php = shutil.which("php")
        if php is None:
            self.skipTest("PHP is unavailable for the controller fixture")

        result = subprocess.run(
            [php, str(FIXTURE), str(CONTROLLER)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertIn("Log controller access checks passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
