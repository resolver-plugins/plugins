import io
import os
import pathlib
import subprocess
import tarfile


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[3]
BUILD_SCRIPT = REPOSITORY_ROOT / 'tools/ci/build-os-bind-rp.sh'
OPNSENSE_26_1_ARCHIVE_SHA256 = (
    '95cb9d549165520de984adbe7bd740ca237dd470b779d7ef3706d5f11b8c321e'
)


def create_core_archive(path: pathlib.Path) -> None:
    files = {
        'core-stable-26.1/src/etc/pkg/repos/OPNsense.conf.shadow.in': (
            'OPNsense: {\n'
            '  url: "%%CORE_PACKAGESITE%%/${ABI}/%%CORE_ABI%%/latest",\n'
            '  signature_type: "fingerprints",\n'
            '  enabled: yes\n'
            '}\n'
        ),
        'core-stable-26.1/src/etc/pkg/fingerprints/OPNsense/trusted/'
        'pkg.opnsense.org.fixture': 'function: "sha256"\nfingerprint: "fixture"\n',
    }
    with tarfile.open(path, 'w:gz') as archive:
        for name, contents in files.items():
            entry = tarfile.TarInfo(name)
            payload = contents.encode()
            entry.size = len(payload)
            archive.addfile(entry, io.BytesIO(payload))


def test_build_wrapper_creates_package_and_metadata_for_26_1(tmp_path):
    core_archive = tmp_path / 'opnsense-core-26.1.tar.gz'
    create_core_archive(core_archive)
    environment = os.environ.copy()
    environment['MAKE_COMMAND'] = str(
        REPOSITORY_ROOT / 'tools/ci/tests/make-package-fixture.sh'
    )
    environment['PKG_COMMAND'] = str(
        REPOSITORY_ROOT / 'tools/ci/tests/pkg-build-fixture.sh'
    )
    environment['PKG_SWITCH_COMMAND'] = str(
        REPOSITORY_ROOT / 'tools/ci/tests/pkg-switch-fixture.sh'
    )
    environment['GIT_COMMAND'] = '/usr/bin/false'
    environment['FETCH_COMMAND'] = str(
        REPOSITORY_ROOT / 'tools/ci/tests/fetch-opnsense-core-fixture.sh'
    )
    environment['FETCH_ARCHIVE'] = str(core_archive)
    environment['SHA256_COMMAND'] = str(
        REPOSITORY_ROOT / 'tools/ci/tests/sha256-fixture.sh'
    )
    environment['SHA256_VALUE'] = OPNSENSE_26_1_ARCHIVE_SHA256
    environment['PKG_REPOS_DIR'] = str(tmp_path / 'repos')
    environment['PKG_FINGERPRINTS_DIR'] = str(tmp_path / 'fingerprints' / 'OPNsense')
    package_call_log = tmp_path / 'pkg-calls.log'
    environment['PKG_CALL_LOG'] = str(package_call_log)

    assert BUILD_SCRIPT.is_file(), 'non-publishing build wrapper is missing'
    result = subprocess.run(
        [BUILD_SCRIPT, '26.1', str(tmp_path)],
        cwd=REPOSITORY_ROOT,
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )

    assert result.returncode == 0, result.stderr
    assert (tmp_path / 'os-bind-rp-1.36_3.pkg').is_file()
    assert (tmp_path / 'repos' / 'OPNsense.conf').is_file()
    metadata = (tmp_path / 'build-metadata.txt').read_text()
    assert 'series=26.1\n' in metadata
    assert 'pkg_abi=FreeBSD:14:amd64\n' in metadata
    assert 'bind920=9.20.24\n' in metadata
    assert 'opnsense=26.1.11_10\n' in metadata
    assert 'switch_test=passed\n' in metadata
    assert 'opnsense_core_archive_sha256=' in metadata
    assert 'source_commit=unknown\n' in metadata
    package_calls = package_call_log.read_text().splitlines()
    assert 'update -f' in package_calls
    assert 'install -y git' in package_calls
    assert 'install -y bind920' in package_calls
