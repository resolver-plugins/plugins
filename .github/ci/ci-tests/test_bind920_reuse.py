#!/usr/bin/env python3
"""Local regression coverage for reusable BIND build provenance."""

from __future__ import annotations

from module_fixtures import *

from package_fixtures import BIND_PROFILE as PROFILE, bind_records, package_creator

MODULE_PATH = CI / "bind/bind920_profile.py"
bind920_profile = load_module("bind920_profile", MODULE_PATH)


PACKAGES = bind_records()
PACKAGE_CREATOR = package_creator("FreeBSD:14:amd64")


class Bind920ReuseTest(unittest.TestCase):
    def test_fingerprint_rejects_different_compatibility_inputs(self) -> None:
        """Changing any compatibility input must prevent package reuse."""
        baseline = bind920_profile.compatibility_fingerprint(
            PROFILE, "26.1", "14.3", "x86_64", PACKAGE_CREATOR
        )
        changed_profile = dict(PROFILE, makefile_sha256="0" * 64)
        self.assertNotEqual(baseline, bind920_profile.compatibility_fingerprint(PROFILE, "26.7", "14.3", "x86_64", PACKAGE_CREATOR))
        self.assertNotEqual(baseline, bind920_profile.compatibility_fingerprint(PROFILE, "26.1", "14.4", "x86_64", PACKAGE_CREATOR))
        self.assertNotEqual(baseline, bind920_profile.compatibility_fingerprint(PROFILE, "26.1", "14.3", "aarch64", PACKAGE_CREATOR))
        self.assertNotEqual(baseline, bind920_profile.compatibility_fingerprint(changed_profile, "26.1", "14.3", "x86_64", PACKAGE_CREATOR))
        self.assertNotEqual(baseline, bind920_profile.compatibility_fingerprint(PROFILE, "26.1", "14.3", "x86_64", dict(PACKAGE_CREATOR, sha256="c" * 64)))

    def test_fingerprint_changes_when_the_bind_build_recipe_changes(self) -> None:
        """A build-policy change must force fresh BIND package archives."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            recipe = Path(temporary_directory) / "build-bind920.sh"
            recipe.write_text("pkg install lmdb\n", encoding="utf-8")
            with patch.object(bind920_profile, "BUILD_RECIPE_PATH", recipe):
                baseline = bind920_profile.compatibility_fingerprint(
                    PROFILE, "26.1", "14.3", "x86_64", PACKAGE_CREATOR
                )
                recipe.write_text("pkg install lmdb0\n", encoding="utf-8")
                corrected = bind920_profile.compatibility_fingerprint(
                    PROFILE, "26.1", "14.3", "x86_64", PACKAGE_CREATOR
                )

        self.assertNotEqual(baseline, corrected)

    def test_provenance_requires_exact_bind_package_identities(self) -> None:
        """A matching version with the wrong BIND origin is not reusable."""
        invalid = dict(PACKAGES)
        invalid['bind920'] = dict(PACKAGES['bind920'], origin='dns/bind918')
        with self.assertRaisesRegex(ValueError, 'bind920 package'):
            bind920_profile.build_provenance(
                PROFILE, '26.1', '14.3', 'x86_64', PACKAGE_CREATOR, invalid
            )


    def test_profile_rejects_negative_portrevision(self) -> None:
        """Package identities must not be generated from impossible revisions."""
        profile = dict(PROFILE, portrevision=-1)
        with self.assertRaisesRegex(ValueError, "portrevision"):
            bind920_profile.validate_profile(profile)

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
                self.assertEqual(3, provenance['schema'])
                self.assertEqual(PACKAGE_CREATOR, provenance['package_creator'])
                self.assertEqual(hashlib.sha256(bind920_profile.BUILD_RECIPE_PATH.read_bytes()).hexdigest(),
                                 provenance['build_recipe_sha256'])


if __name__ == "__main__":
    unittest.main()
