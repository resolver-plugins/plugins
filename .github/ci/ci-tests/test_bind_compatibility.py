#!/usr/bin/env python3
"""Regression coverage for the BIND source eligibility policy."""

from __future__ import annotations

from module_fixtures import *


MODULE_PATH = CI / "bind/bind_compatibility.py"
bind_compatibility = load_module("bind_compatibility", MODULE_PATH)


POLICY = {
    "schema": 1,
    "minimum_version": "9.20.26",
    "bind920": {"name": "bind920", "origin": "dns/bind920"},
    "bind_tools": {"name": "bind-tools", "origin": "dns/bind-tools"},
    "series": {"26.1": "14.3", "26.7": "15.1"},
}


class BindCompatibilityTest(unittest.TestCase):
    def test_policy_requires_supported_series_and_complete_identities(self) -> None:
        """The policy rejects relaxed or incomplete compatibility metadata."""
        policy = bind_compatibility.validate_policy(POLICY)
        self.assertEqual("9.20.26", policy["minimum_version"])
        self.assertEqual("14.3", bind_compatibility.freebsd_release(policy, "26.1"))

        invalid = dict(POLICY, bind920={"name": "bind920"})
        with self.assertRaisesRegex(ValueError, "bind920"):
            bind_compatibility.validate_policy(invalid)
        with self.assertRaisesRegex(ValueError, "unsupported"):
            bind_compatibility.freebsd_release(policy, "27.1")

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
