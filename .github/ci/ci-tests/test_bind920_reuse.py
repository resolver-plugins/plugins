#!/usr/bin/env python3
"""Local regression coverage for reusable BIND build provenance."""

from __future__ import annotations

from module_fixtures import *

from package_fixtures import BIND_PROFILE as PROFILE, bind_records, creator_identity

MODULE_PATH = CI / "bind/bind920_profile.py"
bind920_profile = load_module("bind920_profile", MODULE_PATH)


PACKAGES = bind_records()
PACKAGE_CREATOR = creator_identity("FreeBSD:14:amd64")


class Bind920ReuseTest(unittest.TestCase):
    def test_provenance_command_writes_declared_package_filenames(self) -> None:
        """The shell wrapper writes complete provenance for both package version forms."""
        for version, revision, package_version in [('9.20.26', 2, '9.20.26_2'), ('9.20.27', 0, '9.20.27')]:
            with self.subTest(version=version, revision=revision), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                profile = directory / 'bind920.json'
                bind_tools = directory / f'bind-tools-{package_version}.pkg'
                bind920 = directory / f'bind920-{package_version}.pkg'
                output = directory / 'bind920-provenance.json'
                profile.write_text(json.dumps(dict(PROFILE, distversion=version, portrevision=revision)))
                bind_tools.touch()
                bind920.touch()
                result = subprocess.run([
                    sys.executable, str(MODULE_PATH), str(profile), 'provenance', '26.1', '14.3',
                    '--package-creator', json.dumps(PACKAGE_CREATOR),
                    '--bind-tools', str(bind_tools), '--bind920', str(bind920), '--output', str(output),
                ], capture_output=True, text=True)
                self.assertEqual(0, result.returncode, result.stderr)
                provenance = json.loads(output.read_text())
                self.assertEqual(bind_records(package_version), provenance['packages'])
                self.assertEqual(4, provenance['schema'])
                self.assertEqual(PACKAGE_CREATOR, provenance['package_creator'])
                self.assertEqual(hashlib.sha256(bind920_profile.BUILD_RECIPE_PATH.read_bytes()).hexdigest(),
                                 provenance['build_recipe_sha256'])


if __name__ == "__main__":
    unittest.main()
