import hashlib
import io
import os
import pathlib
import subprocess
import tarfile


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[3]
SETUP_SCRIPT = REPOSITORY_ROOT / 'tools/ci/setup-opnsense-repository.sh'


def create_core_archive(path: pathlib.Path) -> str:
    files = {
        'core-stable-26.1/src/etc/pkg/repos/OPNsense.conf.shadow.in': (
            'OPNsense: {\n'
            '  fingerprints: "/usr/local/etc/pkg/fingerprints/OPNsense",\n'
            '  url: "%%CORE_PACKAGESITE%%/${ABI}/%%CORE_ABI%%/latest",\n'
            '  signature_type: "fingerprints",\n'
            '  priority: 11,\n'
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
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_installs_matching_opnsense_repository_configuration(tmp_path):
    repository_directory = tmp_path / 'repos'
    fingerprint_directory = tmp_path / 'fingerprints' / 'OPNsense'
    core_archive = tmp_path / 'opnsense-core-26.1.tar.gz'
    archive_sha256 = create_core_archive(core_archive)
    environment = os.environ.copy()
    environment['FETCH_COMMAND'] = str(
        REPOSITORY_ROOT / 'tools/ci/tests/fetch-opnsense-core-fixture.sh'
    )
    environment['FETCH_ARCHIVE'] = str(core_archive)
    environment['PKG_REPOS_DIR'] = str(repository_directory)
    environment['PKG_FINGERPRINTS_DIR'] = str(fingerprint_directory)

    assert SETUP_SCRIPT.is_file(), 'OPNsense repository setup script is missing'
    result = subprocess.run(
        [SETUP_SCRIPT, '26.1'],
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == f'{archive_sha256}\n'
    repository_config = (repository_directory / 'OPNsense.conf').read_text()
    assert 'https://pkg.opnsense.org/${ABI}/26.1/latest' in repository_config
    assert (repository_directory / 'FreeBSD.conf').read_text() == 'FreeBSD: {\n  enabled: no\n}\n'
    assert (
        fingerprint_directory / 'trusted/pkg.opnsense.org.fixture'
    ).is_file()
