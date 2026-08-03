import json
import pathlib
import subprocess

import pytest


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[3]
PLANNER = REPOSITORY_ROOT / 'tools/ci/sync_upstream.py'
METADATA_PATH = '.resolver-plugins/upstream.json'


def git(directory: pathlib.Path, *arguments: str) -> str:
    return subprocess.run(
        ['git', '-C', directory, *arguments],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()


def commit(directory: pathlib.Path, files: dict[str, str], message: str) -> str:
    for name, contents in files.items():
        destination = directory / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(contents)
    git(directory, 'add', *files)
    git(directory, 'commit', '-m', message)
    return git(directory, 'rev-parse', 'HEAD')


def metadata(series: str, upstream_commit: str, freebsd_release: str = '14.3') -> str:
    return json.dumps(
        {
            'series': series,
            'upstream_branch': f'stable/{series}',
            'upstream_commit': upstream_commit,
            'freebsd_release': freebsd_release,
            'core_archive_url': f'https://example.invalid/{upstream_commit}.tar.gz',
            'core_archive_sha256': 'fixture-sha256',
        }
    )


@pytest.fixture
def repositories(tmp_path):
    upstream = tmp_path / 'upstream.git'
    origin = tmp_path / 'origin.git'
    source = tmp_path / 'source'
    repository = tmp_path / 'repository'
    git(tmp_path, 'init', '--bare', upstream)
    git(tmp_path, 'init', '--bare', origin)
    git(tmp_path, 'clone', upstream, source)
    git(source, 'remote', 'rename', 'origin', 'upstream')
    git(source, 'config', 'user.email', 'tests@example.invalid')
    git(source, 'config', 'user.name', 'Planner tests')
    initial = commit(
        source,
        {'dns/bind/bind.conf': 'bind-v1\n', 'README': 'initial\n'},
        'initial',
    )
    git(source, 'branch', 'stable/26.1', initial)
    stable_26_7 = commit(source, {'README': 'unrelated 26.7\n'}, 'stable 26.7')
    git(source, 'branch', 'stable/26.7', stable_26_7)
    stable_27_1 = commit(source, {'dns/bind/bind.conf': 'bind-v2\n'}, 'stable 27.1')
    git(source, 'branch', 'stable/27.1', stable_27_1)
    git(source, 'push', 'upstream', 'stable/26.1', 'stable/26.7', 'stable/27.1')

    git(source, 'remote', 'add', 'origin', origin)
    git(source, 'push', 'origin', 'master')
    git(source, 'checkout', '-B', 'release/bind-rp/26.1', initial)
    commit(
        source,
        {METADATA_PATH: metadata('26.1', initial)},
        'release 26.1 metadata',
    )
    git(source, 'push', 'origin', 'release/bind-rp/26.1')

    git(tmp_path, 'clone', origin, repository)
    git(repository, 'config', 'user.email', 'tests@example.invalid')
    git(repository, 'config', 'user.name', 'Planner tests')
    git(repository, 'remote', 'add', 'upstream', upstream)
    git(repository, 'fetch', 'upstream')
    git(repository, 'branch', 'release/bind-rp/26.1', 'origin/release/bind-rp/26.1')
    return {
        'repository': repository,
        'upstream': upstream,
        'initial': initial,
        'stable_26_7': stable_26_7,
        'stable_27_1': stable_27_1,
    }


def add_release(
    repositories, series: str, upstream_commit: str, freebsd_release: str = '14.3'
) -> None:
    repository = repositories['repository']
    release = f'release/bind-rp/{series}'
    git(repository, 'checkout', '-B', release, upstream_commit)
    commit(
        repository,
        {METADATA_PATH: metadata(series, upstream_commit, freebsd_release)},
        f'release {series} metadata',
    )
    git(repository, 'checkout', 'master')


def plan(repositories, release_notes_directory: pathlib.Path | None = None) -> dict:
    command = [
        'python3',
        str(PLANNER),
        'plan',
        '--repository',
        str(repositories['repository']),
        '--upstream',
        'upstream',
        '--release-prefix',
        'release/bind-rp/',
        '--metadata-path',
        METADATA_PATH,
    ]
    if release_notes_directory is not None:
        command.extend(['--release-notes-directory', str(release_notes_directory)])
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def assert_plan_shape(decision: dict) -> None:
    assert set(decision) == {
        'action',
        'series',
        'upstream_ref',
        'upstream_commit',
        'source_release',
        'target_release',
        'sync_branch',
        'freebsd_release',
        'bind_changed',
        'reason',
    }


def test_unrelated_existing_upstream_change_is_noop(repositories):
    add_release(repositories, '26.7', repositories['initial'])
    git(repositories['repository'], 'update-ref', '-d', 'refs/remotes/upstream/stable/27.1')

    decision = plan(repositories)

    assert_plan_shape(decision)
    assert decision['action'] == 'noop'
    assert decision['series'] == '26.7'
    assert decision['upstream_commit'] == repositories['stable_26_7']
    assert decision['bind_changed'] is False


def test_existing_release_with_bind_change_requires_review(repositories):
    add_release(repositories, '26.7', repositories['initial'])
    git(repositories['repository'], 'update-ref', 'refs/remotes/upstream/stable/26.7', repositories['stable_27_1'])

    decision = plan(repositories)

    assert decision['action'] == 'update-review'
    assert decision['series'] == '26.7'
    assert decision['bind_changed'] is True
    assert decision['sync_branch'] == (
        f'sync/bind/26.7/{repositories["stable_27_1"][:12]}'
    )


def test_new_series_with_matching_bind_tree_bootstraps_a_build(repositories):
    decision = plan(repositories)

    assert decision['action'] == 'bootstrap-build'
    assert decision['series'] == '26.7'
    assert decision['source_release'] == 'release/bind-rp/26.1'
    assert decision['target_release'] == 'release/bind-rp/26.7'
    assert decision['bind_changed'] is False


def test_new_series_with_bind_change_requires_bootstrap_review(repositories):
    git(repositories['repository'], 'update-ref', 'refs/remotes/upstream/stable/26.7', repositories['stable_27_1'])

    decision = plan(repositories)

    assert decision['action'] == 'bootstrap-review'
    assert decision['series'] == '26.7'
    assert decision['bind_changed'] is True
    assert decision['sync_branch'] == (
        f'sync/bootstrap/26.7/{repositories["stable_27_1"][:12]}'
    )


def test_missing_source_metadata_blocks_planning(repositories):
    git(repositories['repository'], 'checkout', 'release/bind-rp/26.1')
    git(repositories['repository'], 'rm', METADATA_PATH)
    git(repositories['repository'], 'commit', '-m', 'remove release metadata')
    git(repositories['repository'], 'checkout', 'master')

    decision = plan(repositories)

    assert decision['action'] == 'blocked'
    assert decision['reason'] == 'missing or invalid source metadata'


def test_mismatched_metadata_upstream_branch_blocks_planning(repositories):
    repository = repositories['repository']
    git(repository, 'checkout', 'release/bind-rp/26.1')
    invalid_metadata = json.loads(metadata('26.1', repositories['initial']))
    invalid_metadata['upstream_branch'] = 'stable/26.7'
    commit(
        repository,
        {METADATA_PATH: json.dumps(invalid_metadata)},
        'record mismatched upstream branch',
    )
    git(repository, 'checkout', 'master')

    decision = plan(repositories)

    assert decision['action'] == 'blocked'
    assert decision['reason'] == 'missing or invalid source metadata'


def test_metadata_commit_outside_recorded_upstream_branch_blocks_planning(repositories):
    repository = repositories['repository']
    git(repository, 'checkout', 'release/bind-rp/26.1')
    invalid_metadata = json.loads(metadata('26.1', repositories['stable_26_7']))
    commit(
        repository,
        {METADATA_PATH: json.dumps(invalid_metadata)},
        'record commit outside stable 26.1',
    )
    git(repository, 'checkout', 'master')

    decision = plan(repositories)

    assert decision['action'] == 'blocked'
    assert decision['reason'] == 'missing or invalid source metadata'


def test_release_note_freebsd_declaration_overrides_source_profile(repositories, tmp_path):
    notes = tmp_path / 'notes'
    notes.mkdir()
    (notes / '26.7.rst').write_text('26.7\n====\nFreeBSD 15.1\n\nOlder notes\nFreeBSD 14\n')

    decision = plan(repositories, notes)

    assert decision['freebsd_release'] == '15.1'


def test_historical_release_note_does_not_override_inherited_profile(repositories, tmp_path):
    notes = tmp_path / 'notes'
    notes.mkdir()
    (notes / '26.7.rst').write_text('26.7\n====\nIntroduction text.\n\nHistory\n=======\nFreeBSD 15.1\n')

    decision = plan(repositories, notes)

    assert decision['freebsd_release'] == '14.3'
