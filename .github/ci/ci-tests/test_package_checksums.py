"""Durable regression tests for target-readable package file checksums."""

from __future__ import annotations

from module_fixtures import *

import pytest
import io
import json
import tarfile


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


@pytest.mark.parametrize("checksum", ["a" * 64, "1$" + "a" * 64, "2$" + "y" * 103])
def test_accepts_complete_target_readable_file_checksums(tmp_path: Path, checksum: str) -> None:
    archive = tmp_path / "bind920.pkg"
    archive.touch()
    with pkg_fixture(f"/usr/local/sbin/named|{checksum}\n") as pkg:
        rows = package_checksums.verify_archive(str(pkg), archive)

    assert rows == (("/usr/local/sbin/named", checksum),)


@pytest.mark.parametrize(
    "output",
    ["", "/usr/local/first|" + "a" * 64 + "\n/usr/local/sbin/named|(null)\n"],
)
def test_rejects_missing_or_null_file_checksums(tmp_path: Path, output: str) -> None:
    archive = tmp_path / "bind920.pkg"
    archive.touch()
    with pkg_fixture(output) as pkg:
        with pytest.raises(package_checksums.PackageChecksumError, match="incomplete target-readable"):
            package_checksums.verify_archive(str(pkg), archive)


def test_creator_object_checksums_keep_payload_and_metadata_readable_by_minimum_reader(tmp_path):
    archive = tmp_path / 'plugin.pkg'
    checksum = '2$' + 'y' * 103
    manifest = {'name': 'os-bind-rp', 'scripts': {'pre-install': 'guard'}, 'files': {
        '/usr/local/plugin': {'sum': checksum, 'uname': 'root', 'gname': 'wheel', 'perm': '0555'},
        '/usr/local/plugin-link': {'sum': checksum, 'uname': 'root', 'gname': 'wheel', 'perm': '0555'},
    }}
    with tarfile.open(archive, 'w:xz') as output:
        for name, data in [('+COMPACT_MANIFEST', b'{"name":"os-bind-rp"}'),
                           ('+MANIFEST', json.dumps(manifest).encode()), ('/usr/local/plugin', b'payload')]:
            member = tarfile.TarInfo(name)
            member.size = len(data)
            member.mode, member.uname, member.gname, member.mtime = 0o555, 'root', 'wheel', 123
            output.addfile(member, io.BytesIO(data))
        member = tarfile.TarInfo('/usr/local/plugin-link')
        member.type, member.linkname = tarfile.LNKTYPE, '/usr/local/plugin'
        output.addfile(member)
    def contents():
        with tarfile.open(archive) as source:
            result = {}
            for member in source:
                attributes = member.get_info()
                attributes.pop('chksum')  # Recomputed tar header checksum, not file metadata.
                result[member.name] = (attributes, source.extractfile(member).read() if member.isfile() else None)
            return result
    original = contents()
    package_checksums.use_scalar_file_checksums(archive)
    result = contents()
    expected = dict(manifest, files={path: checksum for path in manifest['files']})
    assert json.loads(result['+MANIFEST'][1]) == expected
    assert {name: data for name, data in result.items() if name != '+MANIFEST'} == {
        name: data for name, data in original.items() if name != '+MANIFEST'}
    # The 2.3.1 object parser discards non-64-character sums; its scalar parser
    # retains this BLAKE2 value. A second pass must preserve the exact archive.
    before = archive.read_bytes()
    package_checksums.use_scalar_file_checksums(archive)
    assert archive.read_bytes() == before


def test_newer_native_manifest_view_retains_original_digest(monkeypatch, tmp_path):
    release = load_module('release_channel', 'shared/release_channel.py')
    checksum = '2$' + 'y' * 103
    record = {'name': 'os-bind-rp', 'scripts': {'pre-install': 'guard'},
              'files': {'/usr/local/plugin': {'sum': checksum, 'uname': 'root'}}}
    monkeypatch.setattr(release.subprocess, 'run', lambda *args, **kwargs:
        subprocess.CompletedProcess(args[0], 0, stdout=json.dumps(record)))
    result = release.read_package_manifest(tmp_path / 'plugin.pkg', 'pkg-creator')
    assert result == dict(record, files={'/usr/local/plugin': checksum})


def test_invalid_creator_checksum_cannot_replace_original_archive(tmp_path):
    archive = tmp_path / 'plugin.pkg'
    manifest = json.dumps({'files': {'/usr/local/plugin': {'sum': '(null)'}}}).encode()
    with tarfile.open(archive, 'w:xz') as output:
        member = tarfile.TarInfo('+MANIFEST')
        member.size = len(manifest)
        output.addfile(member, io.BytesIO(manifest))
    before = archive.read_bytes()
    with pytest.raises(package_checksums.PackageChecksumError):
        package_checksums.use_scalar_file_checksums(archive)
    assert archive.read_bytes() == before
