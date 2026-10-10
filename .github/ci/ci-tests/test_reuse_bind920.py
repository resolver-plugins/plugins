#!/usr/bin/env python3
"""Local regression coverage for BIND reuse candidate selection."""

from __future__ import annotations

from module_fixtures import *

from package_fixtures import BIND_PROFILE as PROFILE, bind_records, package_creator

reuse_bind920 = load_module("reuse_bind920", "bind/reuse_bind920.py")
FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "ci-local"


PACKAGES = bind_records()
PACKAGE_CREATOR = package_creator("FreeBSD:14:amd64")
PROVENANCE = reuse_bind920.bind920_profile.build_provenance(
    PROFILE, "26.1", "14.3", "x86_64", PACKAGE_CREATOR, PACKAGES
)


@contextmanager
def pkg_static_fixture(checksum: str) -> Iterator[Path]:
    FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=FIXTURE_ROOT) as directory_text:
        executable = Path(directory_text) / "pkg-static"
        executable.write_text(
            "#!/bin/sh\n"
            "[ \"$1\" = query ] && [ \"$2\" = -F ] || exit 64\n"
            f"printf '%s\\n' '/usr/local/sbin/named|{checksum}'\n",
            encoding="utf-8",
        )
        executable.chmod(0o755)
        yield executable


class ReuseBind920Test(unittest.TestCase):
    def test_incompatible_archive_checksums_force_a_cache_miss(self) -> None:
        with tempfile.TemporaryDirectory() as directory_text:
            archive = Path(directory_text) / "bind920-9.20.26_2.pkg"
            archive.touch()
            with pkg_static_fixture("(null)") as pkg_static:
                with self.assertRaisesRegex(reuse_bind920.CacheMiss, "target-readable"):
                    reuse_bind920.verify_archive_compatibility(str(pkg_static), archive)

    def test_matching_provenance_selects_the_declared_pair(self) -> None:
        """Only a fully matching package pair may take the no-build path."""
        self.assertEqual(PACKAGES, reuse_bind920.select_candidate(
            PROVENANCE, PROFILE, "26.1", "14.3", "x86_64", PACKAGE_CREATOR))

    def test_compatibility_drift_is_an_ordinary_cache_miss(self) -> None:
        """Compatibility drift selects a fresh build rather than an old BIND pair."""
        for changes, diagnostic in [
            ({'fingerprint': '0' * 64}, 'fingerprint differs'),
            ({'package_creator': dict(PACKAGE_CREATOR, sha256='c' * 64)}, 'creator differs'),
            ({'schema': 2}, 'schema differs'),
        ]:
            with self.subTest(diagnostic=diagnostic):
                with self.assertRaisesRegex(reuse_bind920.CacheMiss, diagnostic):
                    reuse_bind920.select_candidate(
                        dict(PROVENANCE, **changes), PROFILE, '26.1', '14.3', 'x86_64', PACKAGE_CREATOR
                    )

    def test_installed_identity_compares_pkg_query_fields_in_python(self) -> None:
        """Version/origin verification must not rely on pkg predicate support."""
        command: list[str] = []

        def fake_run(arguments: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            command.extend(arguments)
            return subprocess.CompletedProcess(arguments, 0, stdout="bind920\t9.20.26_2\tdns/bind920\n")

        with patch.object(reuse_bind920, "run", side_effect=fake_run):
            identity = reuse_bind920.installed_package_identity("pkg", "bind920")

        self.assertEqual(("bind920", "9.20.26_2", "dns/bind920"), identity)
        self.assertEqual(["pkg", "query", "-e", "%n = bind920", "%n\t%v\t%o"], command)


    def test_downloaded_archive_accepts_pkg_named_fetch_layouts(self):
        with tempfile.TemporaryDirectory() as temporary:
            downloads = Path(temporary)
            for layout in (".", "All"):
                with self.subTest(layout=layout):
                    archive = downloads / layout / "bind-tools-9.20.26_2.pkg"
                    archive.parent.mkdir(exist_ok=True)
                    archive.touch()
                    self.assertEqual(archive, reuse_bind920.downloaded_archive(downloads, archive.name))
                    archive.unlink()


if __name__ == "__main__":
    unittest.main()
