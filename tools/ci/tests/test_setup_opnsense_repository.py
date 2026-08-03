import io
import json
import os
import pathlib
import subprocess
import tarfile


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[3]
SETUP_SCRIPT = REPOSITORY_ROOT / 'tools/ci/setup-opnsense-repository.sh'
SHA256_FIXTURE = REPOSITORY_ROOT / 'tools/ci/tests/sha256-fixture.sh'
OPNSENSE_26_1_ARCHIVE_SHA256 = (
    '95cb9d549165520de984adbe7bd740ca237dd470b779d7ef3706d5f11b8c321e'
)
UPSTREAM_COMMIT = '6f3937f938377464534ebebde66cc13d84186542'
CORE_COMMIT = '8cc69b21e0f4c2622fc8a62df2a15ba7cb1e731f'
CORE_ARCHIVE_URL = (
    f'https://github.com/opnsense/core/archive/{CORE_COMMIT}.tar.gz'
)


def write_upstream_metadata(path: pathlib.Path, archive_sha256: str) -> None:
    path.write_text(
        json.dumps(
            {
                'series': '26.1',
                'upstream_branch': 'stable/26.1',
                'upstream_commit': UPSTREAM_COMMIT,
                'freebsd_release': '14.3',
                'core_commit': CORE_COMMIT,
                'core_archive_url': CORE_ARCHIVE_URL,
                'core_archive_sha256': archive_sha256,
            }
        )
    )


def create_core_archive(path: pathlib.Path) -> None:
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


def test_installs_matching_opnsense_repository_configuration(tmp_path):
    repository_directory = tmp_path / 'repos'
    fingerprint_directory = tmp_path / 'fingerprints' / 'OPNsense'
    core_archive = tmp_path / 'opnsense-core-26.1.tar.gz'
    create_core_archive(core_archive)
    environment = os.environ.copy()
    environment['FETCH_COMMAND'] = str(
        REPOSITORY_ROOT / 'tools/ci/tests/fetch-opnsense-core-fixture.sh'
    )
    environment['FETCH_ARCHIVE'] = str(core_archive)
    environment['SHA256_COMMAND'] = str(SHA256_FIXTURE)
    environment['SHA256_VALUE'] = OPNSENSE_26_1_ARCHIVE_SHA256
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
    assert result.stdout == f'{OPNSENSE_26_1_ARCHIVE_SHA256}\n'
    repository_config = (repository_directory / 'OPNsense.conf').read_text()
    assert 'https://pkg.opnsense.org/${ABI}/26.1/latest' in repository_config
    assert (repository_directory / 'FreeBSD.conf').read_text() == 'FreeBSD: {\n  enabled: no\n}\n'
    assert (
        fingerprint_directory / 'trusted/pkg.opnsense.org.fixture'
    ).is_file()


def test_rejects_an_opnsense_core_archive_with_an_unexpected_hash(tmp_path):
    core_archive = tmp_path / 'opnsense-core-26.1.tar.gz'
    create_core_archive(core_archive)
    environment = os.environ.copy()
    environment['FETCH_COMMAND'] = str(
        REPOSITORY_ROOT / 'tools/ci/tests/fetch-opnsense-core-fixture.sh'
    )
    environment['FETCH_ARCHIVE'] = str(core_archive)
    environment['SHA256_COMMAND'] = str(SHA256_FIXTURE)
    environment['SHA256_VALUE'] = 'not-the-pinned-archive'
    environment['PKG_REPOS_DIR'] = str(tmp_path / 'repos')
    environment['PKG_FINGERPRINTS_DIR'] = str(tmp_path / 'fingerprints' / 'OPNsense')

    result = subprocess.run(
        [SETUP_SCRIPT, '26.1'],
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )

    assert result.returncode != 0
    assert 'does not match the pinned SHA-256' in result.stderr


def test_uses_core_archive_url_and_sha256_from_upstream_metadata(tmp_path):
    core_archive = tmp_path / 'opnsense-core-26.1.tar.gz'
    create_core_archive(core_archive)
    metadata = tmp_path / 'upstream.json'
    write_upstream_metadata(metadata, OPNSENSE_26_1_ARCHIVE_SHA256)
    fetch_url_log = tmp_path / 'fetch-url.log'
    environment = os.environ.copy()
    environment['FETCH_COMMAND'] = str(
        REPOSITORY_ROOT / 'tools/ci/tests/fetch-opnsense-core-fixture.sh'
    )
    environment['FETCH_ARCHIVE'] = str(core_archive)
    environment['FETCH_URL_LOG'] = str(fetch_url_log)
    environment['SHA256_COMMAND'] = str(SHA256_FIXTURE)
    environment['SHA256_VALUE'] = OPNSENSE_26_1_ARCHIVE_SHA256
    environment['PKG_REPOS_DIR'] = str(tmp_path / 'repos')
    environment['PKG_FINGERPRINTS_DIR'] = str(tmp_path / 'fingerprints' / 'OPNsense')
    environment['RP_UPSTREAM_METADATA'] = str(metadata)

    result = subprocess.run(
        [SETUP_SCRIPT, '26.1'],
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )

    assert result.returncode == 0, result.stderr
    assert fetch_url_log.read_text() == f'{CORE_ARCHIVE_URL}\n'
    assert result.stdout == f'{OPNSENSE_26_1_ARCHIVE_SHA256}\n'


def test_rejects_core_archive_when_metadata_sha256_does_not_match(tmp_path):
    core_archive = tmp_path / 'opnsense-core-26.1.tar.gz'
    create_core_archive(core_archive)
    metadata = tmp_path / 'upstream.json'
    write_upstream_metadata(metadata, 'not-the-pinned-archive')
    environment = os.environ.copy()
    environment['FETCH_COMMAND'] = str(
        REPOSITORY_ROOT / 'tools/ci/tests/fetch-opnsense-core-fixture.sh'
    )
    environment['FETCH_ARCHIVE'] = str(core_archive)
    environment['SHA256_COMMAND'] = str(SHA256_FIXTURE)
    environment['SHA256_VALUE'] = OPNSENSE_26_1_ARCHIVE_SHA256
    environment['PKG_REPOS_DIR'] = str(tmp_path / 'repos')
    environment['PKG_FINGERPRINTS_DIR'] = str(tmp_path / 'fingerprints' / 'OPNsense')
    environment['RP_UPSTREAM_METADATA'] = str(metadata)

    result = subprocess.run(
        [SETUP_SCRIPT, '26.1'],
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )

    assert result.returncode != 0
    assert 'does not match the pinned SHA-256' in result.stderr
