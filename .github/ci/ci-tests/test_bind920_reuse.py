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
    def test_package_version_uses_positive_portrevision_suffix(self) -> None:
        self.assertEqual("9.20.26_2", bind920_profile.package_version(PROFILE))

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
        """A cache candidate must identify both BIND package archives exactly."""
        provenance = bind920_profile.build_provenance(
            PROFILE, "26.1", "14.3", "x86_64", PACKAGE_CREATOR, PACKAGES
        )
        self.assertEqual("dns/bind920", provenance["packages"]["bind920"]["origin"])
        self.assertEqual(PACKAGE_CREATOR, provenance["package_creator"])
        self.assertEqual(3, provenance["schema"])
        self.assertEqual(hashlib.sha256(bind920_profile.BUILD_RECIPE_PATH.read_bytes()).hexdigest(),
                         provenance["build_recipe_sha256"])
        invalid = dict(PACKAGES)
        invalid["bind920"] = dict(PACKAGES["bind920"], origin="dns/bind918")
        with self.assertRaisesRegex(ValueError, "bind920 package"):
            bind920_profile.build_provenance(
                PROFILE, "26.1", "14.3", "x86_64", PACKAGE_CREATOR, invalid
            )


    def test_profile_rejects_negative_portrevision(self) -> None:
        """Package identities must not be generated from impossible revisions."""
        profile = dict(PROFILE, portrevision=-1)
        with self.assertRaisesRegex(ValueError, "portrevision"):
            bind920_profile.validate_profile(profile)

    def test_package_version_omits_zero_portrevision(self) -> None:
        """Fresh Ports releases must use normal pkg versions without a _0 suffix."""
        profile = dict(PROFILE, distversion="9.20.27", portrevision=0)
        self.assertEqual("9.20.27", bind920_profile.package_version(profile))

    def test_provenance_accepts_zero_portrevision_package_names(self):
        profile = dict(PROFILE, distversion="9.20.27", portrevision=0)
        provenance = bind920_profile.build_provenance(
            profile, "26.1", "14.3", "x86_64", PACKAGE_CREATOR, bind_records("9.20.27")
        )
        self.assertEqual("bind920-9.20.27.pkg", provenance["packages"]["bind920"]["filename"])

    def test_provenance_command_writes_declared_package_filenames(self) -> None:
        """The shell build wrapper must be able to write reusable provenance."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            profile = directory / "bind920.json"
            bind_tools = directory / "bind-tools-9.20.26_2.pkg"
            bind920 = directory / "bind920-9.20.26_2.pkg"
            output = directory / "bind920-provenance.json"
            profile.write_text(json.dumps(PROFILE), encoding="utf-8")
            bind_tools.touch()
            bind920.touch()
            result = subprocess.run(
                [
                    "python3", str(MODULE_PATH), str(profile), "provenance", "26.1", "14.3",
                    "--package-creator", json.dumps(PACKAGE_CREATOR),
                    "--bind-tools", str(bind_tools), "--bind920", str(bind920), "--output", str(output),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0)
            self.assertEqual(PACKAGES, json.loads(output.read_text())["packages"])


if __name__ == "__main__":
    unittest.main()
