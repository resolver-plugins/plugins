#!/usr/bin/env python3
"""Regression coverage for the BIND source eligibility policy."""

from __future__ import annotations

from module_fixtures import *


MODULE_PATH = CI / "bind/bind_compatibility.py"


class BindCompatibilityTest(unittest.TestCase):
    def test_policy_commands_expose_minimum_version_and_required_identity(self) -> None:
        policy_path = MODULE_PATH.parents[3] / '.resolver-plugins/bind-compatibility.json'
        for command, series, arguments, expected in [
            ('minimum-version', '26.7', [], '9.20.26\n'),
            ('identity', '26.1', ['bind920'], 'bind920\tdns/bind920\n'),
        ]:
            with self.subTest(command=command):
                result = subprocess.run(['python3', str(MODULE_PATH), command, str(policy_path), series, *arguments],
                                        text=True, capture_output=True)
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual(expected, result.stdout)


if __name__ == "__main__":
    unittest.main()
