from git_fixtures import *

import pytest
from package_fixtures import write_resolved_target


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


@pytest.mark.parametrize('series,snapshot,role', [
    pytest.param('26.1', None, 'creator', id='ordinary'),
    pytest.param('26.1', '26.1.11', 'reader', id='snapshot'),
    pytest.param('26.7', None, 'creator', id='archived-creator'),
    pytest.param('26.7', '26.7.5', 'reader', id='independent-reader-snapshot'),
])
def test_checks_out_exact_pinned_core_commit_and_installs_fingerprints(tmp_path, series, snapshot, role):
    core = tmp_path / 'core'
    create_core_repository(core)
    revoked = 'src/etc/pkg/fingerprints/OPNsense/revoked/old.fixture'
    core_commit = commit(core, {revoked: 'revoked fixture\n'}, 'revoked trust input')
    fingerprint = 'src/etc/pkg/fingerprints/OPNsense/trusted/pkg.opnsense.org.fixture'
    pinned_fingerprint = (core / fingerprint).read_bytes()
    commit(core, {fingerprint: 'unreviewed newer fingerprint\n'}, 'advance beyond pinned core')
    metadata = tmp_path / 'upstream.json'
    metadata.write_text(json.dumps(upstream_profile(series, core_commit=core_commit)))
    env = environment(tmp_path, core, metadata)
    target = write_resolved_target(tmp_path / 'target.json', json.loads(metadata.read_text()), role=role)
    env['RP_TARGET_PKG_METADATA'] = str(target)
    if snapshot:
        env['RP_OPNSENSE_SNAPSHOT'] = snapshot
    result = subprocess.run(
        [SETUP_SCRIPT, series], text=True, capture_output=True, check=False,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == f'{core_commit}\n'
    repository_series = f'{series}/MINT/{snapshot}' if snapshot else series
    config = (tmp_path / 'repos/OPNsense.conf').read_text()
    assert f'https://pkg.opnsense.org/${{ABI}}/{repository_series}/latest' in config
    assert 'signature_type: "fingerprints"' in config
    assert (tmp_path / 'repos/FreeBSD.conf').read_text() == ''.join(
        f'{name}: {{\n  enabled: no\n}}\n'
        for name in ('FreeBSD', 'FreeBSD-kmods', 'FreeBSD-ports', 'FreeBSD-ports-kmods', 'FreeBSD-base'))
    assert (tmp_path / 'fingerprints/OPNsense/trusted/pkg.opnsense.org.fixture').read_bytes() == pinned_fingerprint
    assert (tmp_path / 'fingerprints/OPNsense/revoked/old.fixture').read_bytes() == b'revoked fixture\n'
    assert not (tmp_path / 'repos/OPNsense-creator.conf').exists()
    creator = (tmp_path / 'repos/target-pkg/OPNsense-creator.conf').read_text()
    release = json.loads(target.read_text())['release']
    assert f'/{series}/MINT/{release}/latest' in creator
    assert 'OPNsense-creator:' in creator and 'enabled: yes' in creator
    assert 'signature_type: "fingerprints"' in creator
