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


@pytest.mark.parametrize('snapshot', [
    pytest.param(None, id='ordinary'),
    pytest.param('26.1.11', id='snapshot'),
])
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
