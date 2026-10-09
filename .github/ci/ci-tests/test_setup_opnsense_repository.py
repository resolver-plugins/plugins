from git_fixtures import *

import pytest


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[3]
SETUP_SCRIPT = REPOSITORY_ROOT / '.github/ci/shared/setup-opnsense-repository.sh'


def environment(tmp_path: pathlib.Path, core_repository: pathlib.Path, metadata: pathlib.Path) -> dict[str, str]:
    result = os.environ.copy()
    result.pop('RP_OPNSENSE_SNAPSHOT', None)
    result.pop('GIT_COMMAND', None)
    result.update(
        {
            'OPNSENSE_CORE_REPOSITORY': str(core_repository),
            'PKG_REPOS_DIR': str(tmp_path / 'repos'),
            'PKG_FINGERPRINTS_DIR': str(tmp_path / 'fingerprints' / 'OPNsense'),
            'RP_UPSTREAM_METADATA': str(metadata),
        }
    )
    return result


def test_requires_immutable_upstream_metadata(tmp_path):
    env = environment(tmp_path, tmp_path / 'unused-core', tmp_path / 'unused.json')
    env.pop('RP_UPSTREAM_METADATA')
    result = subprocess.run([SETUP_SCRIPT, '26.1'], env=env, text=True, capture_output=True)
    assert result.returncode != 0
    assert 'RP_UPSTREAM_METADATA is required' in result.stderr


@pytest.mark.parametrize('snapshot', [None, '26.1.11'])
def test_checks_out_exact_pinned_core_commit_and_installs_fingerprints(tmp_path, snapshot):
    core = tmp_path / 'core'
    core_commit = create_core_repository(core)
    fingerprint = 'src/etc/pkg/fingerprints/OPNsense/trusted/pkg.opnsense.org.fixture'
    pinned_fingerprint = (core / fingerprint).read_bytes()
    commit(core, {fingerprint: 'unreviewed newer fingerprint\n'}, 'advance beyond pinned core')
    metadata = tmp_path / 'upstream.json'
    write_upstream_metadata(metadata, core_commit)
    env = environment(tmp_path, core, metadata)
    if snapshot:
        env['RP_OPNSENSE_SNAPSHOT'] = snapshot
    result = subprocess.run(
        [SETUP_SCRIPT, '26.1'], text=True, capture_output=True, check=False,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == f'{core_commit}\n'
    repository_series = f'26.1/MINT/{snapshot}' if snapshot else '26.1'
    config = (tmp_path / 'repos/OPNsense.conf').read_text()
    assert f'https://pkg.opnsense.org/${{ABI}}/{repository_series}/latest' in config
    assert 'signature_type: "fingerprints"' in config
    assert (tmp_path / 'repos/FreeBSD.conf').read_text() == 'FreeBSD: {\n  enabled: no\n}\n'
    assert (tmp_path / 'fingerprints/OPNsense/trusted/pkg.opnsense.org.fixture').read_bytes() == pinned_fingerprint


@pytest.mark.parametrize('snapshot', ['26.7.4', '26.1.', '26.1.11/../../latest'])
def test_rejects_foreign_or_malformed_snapshot_before_writing_configuration(tmp_path, snapshot):
    metadata = tmp_path / 'upstream.json'
    write_upstream_metadata(metadata, 'a' * 40)
    env = environment(tmp_path, tmp_path / 'unused-core', metadata)
    env['RP_OPNSENSE_SNAPSHOT'] = snapshot
    result = subprocess.run([SETUP_SCRIPT, '26.1'], env=env, text=True, capture_output=True)
    assert result.returncode != 0
    assert 'snapshot must' in result.stderr
    assert not (tmp_path / 'repos').exists()


def test_rejects_a_core_repository_without_the_pinned_commit(tmp_path):
    core = tmp_path / 'core'
    create_core_repository(core)
    metadata = tmp_path / 'upstream.json'
    write_upstream_metadata(metadata, '0' * 40)
    result = subprocess.run(
        [SETUP_SCRIPT, '26.1'], text=True, capture_output=True, check=False,
        env=environment(tmp_path, core, metadata),
    )
    assert result.returncode != 0

    assert 'not our ref ' + '0' * 40 in result.stderr
    assert not (tmp_path / 'repos').exists()
    assert not (tmp_path / 'fingerprints').exists()

def test_legacy_archive_digest_does_not_replace_git_commit_verification(tmp_path):
    core = tmp_path / 'core'
    commit = create_core_repository(core)
    metadata = tmp_path / 'upstream.json'
    write_upstream_metadata(metadata, commit, '0' * 64)
    result = subprocess.run(
        [SETUP_SCRIPT, '26.1'], text=True, capture_output=True, check=False,
        env=environment(tmp_path, core, metadata),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == f'{commit}\n'
