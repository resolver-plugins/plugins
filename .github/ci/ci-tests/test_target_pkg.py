"""Durable tests for selecting the immutable target pkg creator."""

from __future__ import annotations

from module_fixtures import *

import pytest

from package_fixtures import package_creator, write_target_metadata


MODULE_PATH = CI / "shared/target_pkg.py"
target_pkg = load_module("target_pkg", MODULE_PATH, register=True)
FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "ci-local"


def write_metadata(path: Path, archive: Path, pkg_static: Path) -> None:
    write_target_metadata(path, package_creator(
        abi="FreeBSD:14:amd64",
        sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
        pkg_static_sha256=hashlib.sha256(pkg_static.read_bytes()).hexdigest(),
    ))


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
            "    if [ \"$2\" = -y ]; then : > \"$lock\"; elif [ \"$2\" = -l ]; then [ ! -f \"$lock\" ] || printf '%s\\n' 'pkg-2.3.1_1'; else exit 64; fi;;\n"
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

        assert selected.identity == target_pkg.PackageIdentity(
            'pkg', '2.3.1_1', 'ports-mgmt/pkg', 'FreeBSD:14:amd64')
        assert selected.sha256 == hashlib.sha256(archive.read_bytes()).hexdigest()
        assert selected.pkg_static_sha256 == hashlib.sha256(pkg_static.read_bytes()).hexdigest()
        calls = log.read_text(encoding="utf-8").splitlines()
        assert calls[0].startswith("fetch -y -r OPNsense -o ")
        assert calls[0].endswith(" pkg-2.3.1_1")
        downloaded = Path(calls[0].split()[5]) / 'All/pkg-2.3.1_1.pkg'
        assert calls[1:3] == [f'query -F {downloaded} %n|%v|%o|%q', f'add -f {downloaded}']
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


@pytest.mark.parametrize('fault', ['identity', 'lock', 'static-bytes'])
def test_verify_rejects_changed_creator_state(tmp_path: Path, fault: str) -> None:
    with pkg_fixture() as (pkg, archive, pkg_static, _):
        metadata = tmp_path / "target-pkg.json"
        write_metadata(metadata, archive, pkg_static)
        selected = target_pkg.select_target_pkg(
            metadata, "26.1", str(pkg), pkg_static_path=pkg_static
        )
        if fault == 'identity':
            pkg.write_text(pkg.read_text().replace('pkg|2.3.1_1|', 'pkg|2.3.2|'))
        elif fault == 'lock':
            (pkg.parent / 'locked').unlink()
        else:
            pkg_static.write_bytes(b'unexpected replacement\n')
        error = {'identity': 'installed pkg identity', 'lock': 'not locked',
                 'static-bytes': 'pkg-static SHA-256'}[fault]
        with pytest.raises(target_pkg.TargetPackageError, match=error):
            target_pkg.verify_target_pkg(selected, str(pkg), pkg_static_path=pkg_static)


def test_refreshes_only_the_outer_archive_hash_for_identical_contents(tmp_path: Path) -> None:
    with pkg_fixture() as (pkg, archive, pkg_static, _):
        metadata = tmp_path / "target-pkg.json"
        content_metadata = tmp_path / "target-pkg-content.json"
        write_metadata(metadata, archive, pkg_static)
        write_content_metadata(content_metadata, archive)
        expected = json.loads(metadata.read_text())
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
        expected["series"]["26.1"]["sha256"] = digest
        assert json.loads(metadata.read_text()) == expected


@pytest.mark.parametrize("changes", [
    {"payload": b"changed\n"},
    {"payload_mode": 0o755},
    {"link_target": "usr/local/sbin/pkg-static"},
    {"hardlink_payload": False},
    {"version": "2.3.2"},
], ids=["bytes", "mode", "symlink", "hardlink", "identity"])
def test_refresh_preserves_metadata_when_content_or_identity_changes(tmp_path: Path, changes: dict) -> None:
    with pkg_fixture() as (pkg, archive, pkg_static, _):
        metadata = tmp_path / "target-pkg.json"
        content_metadata = tmp_path / "target-pkg-content.json"
        write_metadata(metadata, archive, pkg_static)
        write_content_metadata(content_metadata, archive)
        if 'version' in changes:
            document = json.loads(metadata.read_text())
            document['series']['26.1']['version'] = changes['version']
            metadata.write_text(json.dumps(document))
        else:
            write_archive(archive, pkg_static, **changes)
        before = metadata.read_bytes()
        error = 'identity' if 'version' in changes else 'extracted contents'
        with pytest.raises(target_pkg.TargetPackageError, match=error):
            target_pkg.refresh_archive_sha256(
                metadata, content_metadata, "26.1", str(pkg), "OPNsense", metadata
            )
        assert metadata.read_bytes() == before


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
