"""Durable regression tests for target-readable package file checksums."""

from __future__ import annotations

from module_fixtures import *

import pytest


package_checksums = load_module("package_checksums", "shared/package_checksums.py")
FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "ci-local"


@contextmanager
def pkg_fixture(output: str) -> Iterator[Path]:
    """Return a pkg boundary fixture with one hand-written query result."""
    FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=FIXTURE_ROOT) as directory:
        executable = Path(directory) / "pkg"
        executable.write_text(
            "#!/bin/sh\n"
            "[ \"$1\" = query ] || exit 64\n"
            "[ \"$2\" = -F ] || exit 64\n"
            "[ \"$4\" = '%Fp|%Fs' ] || exit 64\n"
            f"printf %s {shlex.quote(output)}\n",
            encoding="utf-8",
        )
        executable.chmod(0o755)
        yield executable


@pytest.mark.parametrize("prefix", ["", "1$", "2$"])
def test_accepts_complete_target_readable_file_checksums(tmp_path: Path, prefix: str) -> None:
    archive = tmp_path / "bind920.pkg"
    archive.touch()
    checksum = prefix + "a" * 64
    with pkg_fixture(f"/usr/local/sbin/named|{checksum}\n") as pkg:
        rows = package_checksums.verify_archive(str(pkg), archive)

    assert rows == (("/usr/local/sbin/named", checksum),)


@pytest.mark.parametrize(
    "output",
    ["", "/usr/local/sbin/named|(null)\n", "/usr/local/sbin/named|\n"],
)
def test_rejects_missing_or_null_file_checksums(tmp_path: Path, output: str) -> None:
    archive = tmp_path / "bind920.pkg"
    archive.touch()
    with pkg_fixture(output) as pkg:
        with pytest.raises(package_checksums.PackageChecksumError, match="incomplete target-readable"):
            package_checksums.verify_archive(str(pkg), archive)


def test_rejects_malformed_checksum_rows(tmp_path: Path) -> None:
    archive = tmp_path / "bind920.pkg"
    archive.touch()
    with pkg_fixture("not-a-file-checksum-row\n") as pkg:
        with pytest.raises(package_checksums.PackageChecksumError, match="malformed"):
            package_checksums.verify_archive(str(pkg), archive)


@pytest.mark.parametrize("checksum", ["garbage", "3$" + "a" * 64, "1$abc"])
def test_rejects_unrecognized_checksum_formats(tmp_path: Path, checksum: str) -> None:
    archive = tmp_path / "bind920.pkg"
    archive.touch()
    with pkg_fixture(f"/usr/local/sbin/named|{checksum}\n") as pkg:
        with pytest.raises(package_checksums.PackageChecksumError, match="unrecognized"):
            package_checksums.verify_archive(str(pkg), archive)
