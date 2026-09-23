"""Durable tests for selecting the immutable target pkg creator."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "target_pkg.py"
SPEC = importlib.util.spec_from_file_location("target_pkg", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
target_pkg = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = target_pkg
SPEC.loader.exec_module(target_pkg)
FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "ci-local"


def write_metadata(path: Path, archive: Path, pkg_static: Path) -> None:
    record = {
        "name": "pkg",
        "version": "2.3.1_1",
        "origin": "ports-mgmt/pkg",
        "abi": "FreeBSD:14:amd64",
        "filename": "pkg-2.3.1_1.pkg",
        "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "pkg_static_sha256": hashlib.sha256(pkg_static.read_bytes()).hexdigest(),
    }
    path.write_text(
        json.dumps(
            {
                "schema": 1,
                "series": {
                    "26.1": record,
                    "26.7": dict(record, abi="FreeBSD:15:amd64"),
                },
            }
        ),
        encoding="utf-8",
    )


def write_content_metadata(path: Path, archive: Path) -> None:
    digest = target_pkg.package_content_sha256(archive)
    record = {
        "baseline_archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "content_sha256": digest,
    }
    path.write_text(
        json.dumps({"schema": 2, "series": {"26.1": record, "26.7": record}}),
        encoding="utf-8",
    )


def write_archive(
    path: Path,
    pkg_static: Path,
    payload: bytes = b"payload\n",
    *,
    payload_mode: int = 0o644,
    link_target: str = "payload",
    hardlink_payload: bool = True,
) -> None:
    content = path.parent / "archive-content"
    if content.exists():
        shutil.rmtree(content)
    executable = content / "usr/local/sbin/pkg-static"
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.write_bytes(pkg_static.read_bytes())
    executable.chmod(0o755)
    payload_path = content / "payload"
    payload_path.write_bytes(payload)
    payload_path.chmod(payload_mode)
    hardlink_path = content / "payload-hardlink"
    if hardlink_payload:
        hardlink_path.hardlink_to(payload_path)
    else:
        hardlink_path.write_bytes(payload)
        hardlink_path.chmod(payload_mode)
    (content / "payload-link").symlink_to(link_target)
    with tarfile.open(path, "w") as archive:
        archive.add(content, arcname="")


@contextmanager
def pkg_fixture() -> Iterator[tuple[Path, Path, Path, Path]]:
    """Create a stateful pkg boundary with real archive/hash side effects."""
    FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=FIXTURE_ROOT) as directory_text:
        directory = Path(directory_text)
        pkg_static = directory / "pkg-static"
        pkg_static.write_text(
            "#!/bin/sh\n[ \"$1\" = -v ] || exit 64\nprintf '%s\\n' '2.3.1'\n",
            encoding="utf-8",
        )
        pkg_static.chmod(0o755)
        archive = directory / "source-pkg-2.3.1_1.pkg"
        write_archive(archive, pkg_static)
        log = directory / "commands.log"
        lock = directory / "locked"
        executable = directory / "pkg"
        executable.write_text(
            "#!/bin/sh\n"
            f"log={str(log)!r}\n"
            f"archive={str(archive)!r}\n"
            f"lock={str(lock)!r}\n"
            "printf '%s\\n' \"$*\" >> \"$log\"\n"
            "case \"$1\" in\n"
            "  fetch)\n"
            "    [ \"$2\" = -y ] && [ \"$3\" = -r ] && [ \"$4\" = OPNsense ] || exit 64\n"
            "    [ \"$5\" = -o ] && [ \"$7\" = pkg-2.3.1_1 ] || exit 64\n"
            "    mkdir -p \"$6/All\"\n"
            "    cp \"$archive\" \"$6/All/pkg-2.3.1_1.pkg\";;\n"
            "  query)\n"
            "    printf '%s\\n' 'pkg|2.3.1_1|ports-mgmt/pkg|FreeBSD:14:amd64';;\n"
            "  add) [ \"$2\" = -f ] || exit 64;;\n"
            "  lock)\n"
            "    if [ \"$2\" = -y ]; then : > \"$lock\"; elif [ \"$2\" = -l ]; then [ -f \"$lock\" ] && printf '%s\\n' 'pkg-2.3.1_1'; else exit 64; fi;;\n"
            "  *) exit 64;;\n"
            "esac\n",
            encoding="utf-8",
        )
        executable.chmod(0o755)
        yield executable, archive, pkg_static, log


def test_installs_locks_and_verifies_the_exact_pinned_archive(tmp_path: Path) -> None:
    with pkg_fixture() as (pkg, archive, pkg_static, log):
        metadata = tmp_path / "target-pkg.json"
        write_metadata(metadata, archive, pkg_static)

        selected = target_pkg.select_target_pkg(
            metadata, "26.1", str(pkg), pkg_static_path=pkg_static
        )

        assert selected.identity.version == "2.3.1_1"
        assert selected.sha256 == hashlib.sha256(archive.read_bytes()).hexdigest()
        assert selected.pkg_static_sha256 == hashlib.sha256(pkg_static.read_bytes()).hexdigest()
        calls = log.read_text(encoding="utf-8").splitlines()
        assert calls[0].startswith("fetch -y -r OPNsense -o ")
        assert calls[0].endswith(" pkg-2.3.1_1")
        assert calls[1].startswith("query -F ")
        assert calls[2].startswith("add -f ")
        assert calls[3:] == ["lock -y pkg", "query -e %n = pkg %n|%v|%o|%q", "lock -l"]


def test_rejects_an_archive_with_the_wrong_sha256_before_install(tmp_path: Path) -> None:
    with pkg_fixture() as (pkg, archive, pkg_static, log):
        metadata = tmp_path / "target-pkg.json"
        write_metadata(metadata, archive, pkg_static)
        document = json.loads(metadata.read_text(encoding="utf-8"))
        document["series"]["26.1"]["sha256"] = "0" * 64
        metadata.write_text(json.dumps(document), encoding="utf-8")

        with pytest.raises(target_pkg.TargetPackageError, match="SHA-256"):
            target_pkg.select_target_pkg(
                metadata, "26.1", str(pkg), pkg_static_path=pkg_static
            )

        assert not any(
            call.startswith(("add ", "lock "))
            for call in log.read_text(encoding="utf-8").splitlines()
        )


def test_verify_rejects_a_changed_static_executable(tmp_path: Path) -> None:
    with pkg_fixture() as (pkg, archive, pkg_static, _):
        metadata = tmp_path / "target-pkg.json"
        write_metadata(metadata, archive, pkg_static)
        selected = target_pkg.select_target_pkg(
            metadata, "26.1", str(pkg), pkg_static_path=pkg_static
        )
        pkg_static.write_bytes(b"unexpected replacement\n")

        with pytest.raises(target_pkg.TargetPackageError, match="pkg-static SHA-256"):
            target_pkg.verify_target_pkg(selected, str(pkg), pkg_static_path=pkg_static)


def test_rejects_unknown_or_malformed_series_metadata(tmp_path: Path) -> None:
    metadata = tmp_path / "target-pkg.json"
    metadata.write_text('{"schema": 1, "series": {}}', encoding="utf-8")

    with pytest.raises(target_pkg.TargetPackageError, match="26.1"):
        target_pkg.load_target(metadata, "26.1")


def test_refreshes_only_the_outer_archive_hash_for_identical_contents(tmp_path: Path) -> None:
    with pkg_fixture() as (pkg, archive, pkg_static, _):
        metadata = tmp_path / "target-pkg.json"
        content_metadata = tmp_path / "target-pkg-content.json"
        write_metadata(metadata, archive, pkg_static)
        write_content_metadata(content_metadata, archive)
        archive.write_bytes(archive.read_bytes() + b"repacked\n")

        digest = target_pkg.refresh_archive_sha256(
            metadata,
            content_metadata,
            "26.1",
            str(pkg),
            "OPNsense",
            metadata,
        )

        assert digest == hashlib.sha256(archive.read_bytes()).hexdigest()
        assert target_pkg.load_target(metadata, "26.1").sha256 == digest


def test_refresh_rejects_changed_extracted_contents(tmp_path: Path) -> None:
    with pkg_fixture() as (pkg, archive, pkg_static, _):
        metadata = tmp_path / "target-pkg.json"
        content_metadata = tmp_path / "target-pkg-content.json"
        write_metadata(metadata, archive, pkg_static)
        write_content_metadata(content_metadata, archive)
        write_archive(archive, pkg_static, b"changed\n")

        with pytest.raises(target_pkg.TargetPackageError, match="extracted contents"):
            target_pkg.refresh_archive_sha256(
                metadata,
                content_metadata,
                "26.1",
                str(pkg),
                "OPNsense",
                metadata,
            )


def test_refresh_rejects_changed_file_mode(tmp_path: Path) -> None:
    with pkg_fixture() as (pkg, archive, pkg_static, _):
        metadata = tmp_path / "target-pkg.json"
        content_metadata = tmp_path / "target-pkg-content.json"
        write_metadata(metadata, archive, pkg_static)
        write_content_metadata(content_metadata, archive)
        write_archive(archive, pkg_static, payload_mode=0o755)

        with pytest.raises(target_pkg.TargetPackageError, match="extracted contents"):
            target_pkg.refresh_archive_sha256(
                metadata, content_metadata, "26.1", str(pkg), "OPNsense", metadata
            )


def test_refresh_rejects_changed_symlink_target(tmp_path: Path) -> None:
    with pkg_fixture() as (pkg, archive, pkg_static, _):
        metadata = tmp_path / "target-pkg.json"
        content_metadata = tmp_path / "target-pkg-content.json"
        write_metadata(metadata, archive, pkg_static)
        write_content_metadata(content_metadata, archive)
        write_archive(archive, pkg_static, link_target="usr/local/sbin/pkg-static")

        with pytest.raises(target_pkg.TargetPackageError, match="extracted contents"):
            target_pkg.refresh_archive_sha256(
                metadata, content_metadata, "26.1", str(pkg), "OPNsense", metadata
            )


def test_refresh_rejects_changed_hardlink_relationship(tmp_path: Path) -> None:
    with pkg_fixture() as (pkg, archive, pkg_static, _):
        metadata = tmp_path / "target-pkg.json"
        content_metadata = tmp_path / "target-pkg-content.json"
        write_metadata(metadata, archive, pkg_static)
        write_content_metadata(content_metadata, archive)
        write_archive(archive, pkg_static, hardlink_payload=False)

        with pytest.raises(target_pkg.TargetPackageError, match="extracted contents"):
            target_pkg.refresh_archive_sha256(
                metadata, content_metadata, "26.1", str(pkg), "OPNsense", metadata
            )


def test_refresh_rejects_changed_identity(tmp_path: Path) -> None:
    with pkg_fixture() as (pkg, archive, pkg_static, _):
        metadata = tmp_path / "target-pkg.json"
        content_metadata = tmp_path / "target-pkg-content.json"
        write_metadata(metadata, archive, pkg_static)
        write_content_metadata(content_metadata, archive)
        document = json.loads(metadata.read_text(encoding="utf-8"))
        document["series"]["26.1"]["version"] = "2.3.2"
        metadata.write_text(json.dumps(document), encoding="utf-8")

        with pytest.raises(target_pkg.TargetPackageError, match="identity"):
            target_pkg.refresh_archive_sha256(
                metadata, content_metadata, "26.1", str(pkg), "OPNsense", metadata
            )


def test_identifies_a_single_archive_only_change(tmp_path: Path) -> None:
    with pkg_fixture() as (_, archive, pkg_static, _):
        before = tmp_path / "before.json"
        after = tmp_path / "after.json"
        write_metadata(before, archive, pkg_static)
        write_metadata(after, archive, pkg_static)
        document = json.loads(after.read_text(encoding="utf-8"))
        document["series"]["26.1"]["sha256"] = "0" * 64
        after.write_text(json.dumps(document), encoding="utf-8")

        assert target_pkg.changed_archive_series(before, after) == "26.1"

        document["series"]["26.1"]["version"] = "2.3.2"
        after.write_text(json.dumps(document), encoding="utf-8")
        assert target_pkg.changed_archive_series(before, after) is None


def test_changed_series_command_fails_when_provenance_is_unavailable(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(MODULE_PATH),
            "changed-series",
            str(tmp_path / "missing-before.json"),
            str(tmp_path / "missing-after.json"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "target pkg selection failed" in result.stderr


def test_content_hash_rejects_archive_path_traversal(tmp_path: Path) -> None:
    archive_path = tmp_path / "traversal.pkg"
    with tarfile.open(archive_path, "w") as archive:
        entry = tarfile.TarInfo("../escape")
        entry.size = 0
        archive.addfile(entry)

    with pytest.raises(subprocess.CalledProcessError):
        target_pkg.package_content_sha256(archive_path)
    assert not (tmp_path / "escape").exists()


def test_content_hash_rejects_special_entries(tmp_path: Path) -> None:
    archive_path = tmp_path / "special.pkg"
    with tarfile.open(archive_path, "w") as archive:
        entry = tarfile.TarInfo("named-pipe")
        entry.type = tarfile.FIFOTYPE
        archive.addfile(entry)

    with pytest.raises(target_pkg.TargetPackageError, match="unsupported entry"):
        target_pkg.package_content_sha256(archive_path)
