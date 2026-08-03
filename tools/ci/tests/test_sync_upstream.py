import json
import os
import pathlib
import subprocess

import pytest


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[3]
PLANNER = REPOSITORY_ROOT / 'tools/ci/sync_upstream.py'
METADATA_PATH = '.resolver-plugins/upstream.json'
OVERLAY_MANIFEST = '.resolver-plugins/overlay-paths.txt'
CORE_COMMIT = '8cc69b21e0f4c2622fc8a62df2a15ba7cb1e731f'


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
            'core_commit': CORE_COMMIT,
            'core_archive_url': f'https://github.com/opnsense/core/archive/{CORE_COMMIT}.tar.gz',
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
        {
            METADATA_PATH: metadata('26.1', initial),
            OVERLAY_MANIFEST: f'{OVERLAY_MANIFEST}\ntools/resolver-overlay.txt\n',
            'tools/resolver-overlay.txt': 'resolver overlay\n',
            'tools/not-an-overlay.txt': 'must not copy\n',
        },
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
        {
            METADATA_PATH: metadata(series, upstream_commit, freebsd_release),
            OVERLAY_MANIFEST: f'{OVERLAY_MANIFEST}\ntools/resolver-overlay.txt\n',
            'tools/resolver-overlay.txt': 'resolver overlay\n',
            'tools/not-an-overlay.txt': 'must not copy\n',
        },
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


def apply(
    repositories,
    decision: dict,
    tmp_path: pathlib.Path,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    plan_path = tmp_path / 'plan.json'
    plan_path.write_text(json.dumps(decision))
    return subprocess.run(
        [
            'python3', str(PLANNER), 'apply',
            '--repository', str(repositories['repository']),
            '--plan', str(plan_path),
            '--core-commit', CORE_COMMIT,
            '--core-archive-url', f'https://github.com/opnsense/core/archive/{CORE_COMMIT}.tar.gz',
            '--core-archive-sha256', 'immutable-fixture-sha256',
        ],
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, **(environment or {})},
    )


def assert_ref_absent(repository: pathlib.Path, reference: str) -> None:
    result = subprocess.run(
        ['git', '-C', repository, 'show-ref', '--verify', '--quiet', f'refs/heads/{reference}'],
        check=False,
    )
    assert result.returncode == 1


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


def test_apply_bootstrap_build_creates_release_with_only_manifest_overlay_and_metadata(
    repositories, tmp_path
):
    decision = plan(repositories)

    result = apply(repositories, decision, tmp_path)

    assert result.returncode == 0, result.stderr
    target = decision['target_release']
    assert git(repositories['repository'], 'show', f'{target}:tools/resolver-overlay.txt') == 'resolver overlay'
    with pytest.raises(subprocess.CalledProcessError):
        git(repositories['repository'], 'show', f'{target}:tools/not-an-overlay.txt')
    target_metadata = json.loads(git(repositories['repository'], 'show', f'{target}:{METADATA_PATH}'))
    assert target_metadata == {
        'series': '26.7',
        'upstream_branch': 'stable/26.7',
        'upstream_commit': repositories['stable_26_7'],
        'freebsd_release': '14.3',
        'core_commit': CORE_COMMIT,
        'core_archive_url': f'https://github.com/opnsense/core/archive/{CORE_COMMIT}.tar.gz',
        'core_archive_sha256': 'immutable-fixture-sha256',
    }


def test_apply_creates_the_same_commit_when_a_publish_retry_rebuilds_a_branch(
    repositories, tmp_path
):
    decision = plan(repositories)
    first = apply(
        repositories,
        decision,
        tmp_path,
        {
            'GIT_AUTHOR_DATE': '2001-01-01T00:00:00+00:00',
            'GIT_COMMITTER_DATE': '2001-01-01T00:00:00+00:00',
        },
    )
    assert first.returncode == 0, first.stderr
    first_commit = git(repositories['repository'], 'rev-parse', decision['target_release'])
    git(repositories['repository'], 'branch', '-D', decision['target_release'])

    second = apply(
        repositories,
        decision,
        tmp_path,
        {
            'GIT_AUTHOR_DATE': '2030-01-01T00:00:00+00:00',
            'GIT_COMMITTER_DATE': '2030-01-01T00:00:00+00:00',
        },
    )

    assert second.returncode == 0, second.stderr
    assert git(repositories['repository'], 'rev-parse', decision['target_release']) == first_commit


def test_apply_bootstrap_review_creates_pristine_release_and_overlay_sync_branch(
    repositories, tmp_path
):
    git(repositories['repository'], 'update-ref', 'refs/remotes/upstream/stable/26.7', repositories['stable_27_1'])
    decision = plan(repositories)

    result = apply(repositories, decision, tmp_path)

    assert result.returncode == 0, result.stderr
    assert git(repositories['repository'], 'show', f'{decision["target_release"]}:dns/bind/bind.conf') == 'bind-v2'
    with pytest.raises(subprocess.CalledProcessError):
        git(repositories['repository'], 'show', f'{decision["target_release"]}:tools/resolver-overlay.txt')
    assert git(repositories['repository'], 'show', f'{decision["sync_branch"]}:tools/resolver-overlay.txt') == 'resolver overlay'


def test_apply_update_review_creates_only_overlay_sync_branch(repositories, tmp_path):
    add_release(repositories, '26.7', repositories['initial'])
    git(repositories['repository'], 'update-ref', 'refs/remotes/upstream/stable/26.7', repositories['stable_27_1'])
    decision = plan(repositories)
    source_sha = git(repositories['repository'], 'rev-parse', decision['target_release'])

    result = apply(repositories, decision, tmp_path)

    assert result.returncode == 0, result.stderr
    assert git(repositories['repository'], 'rev-parse', decision['target_release']) == source_sha
    assert git(repositories['repository'], 'show', f'{decision["sync_branch"]}:tools/resolver-overlay.txt') == 'resolver overlay'


def test_apply_refuses_existing_target_release_without_changing_it(repositories, tmp_path):
    decision = plan(repositories)
    git(repositories['repository'], 'branch', decision['target_release'], repositories['initial'])
    target_sha = git(repositories['repository'], 'rev-parse', decision['target_release'])

    result = apply(repositories, decision, tmp_path)

    assert result.returncode != 0
    assert git(repositories['repository'], 'rev-parse', decision['target_release']) == target_sha


def test_apply_rejects_plan_commit_that_does_not_match_the_upstream_ref(repositories, tmp_path):
    decision = plan(repositories)
    decision['upstream_commit'] = repositories['initial']

    result = apply(repositories, decision, tmp_path)

    assert result.returncode != 0
    assert_ref_absent(repositories['repository'], decision['target_release'])


def test_apply_rejects_pathspec_magic_in_the_overlay_manifest(repositories, tmp_path):
    repository = repositories['repository']
    decision = plan(repositories)
    git(repository, 'checkout', 'release/bind-rp/26.1')
    commit(repository, {OVERLAY_MANIFEST: ':(glob)tools/**\n'}, 'add glob overlay manifest entry')
    git(repository, 'checkout', 'master')

    result = apply(repositories, decision, tmp_path)

    assert result.returncode != 0
    assert_ref_absent(repository, decision['target_release'])


def test_apply_refuses_dirty_checkout_before_creating_a_target_branch(repositories, tmp_path):
    decision = plan(repositories)
    dirty_file = repositories['repository'] / 'dirty'
    dirty_file.write_text('dirty\n')

    result = apply(repositories, decision, tmp_path)

    assert result.returncode != 0
    assert_ref_absent(repositories['repository'], decision['target_release'])


def test_apply_refuses_an_unknown_action_before_creating_a_target_branch(repositories, tmp_path):
    decision = plan(repositories)
    decision['action'] = 'unexpected'

    result = apply(repositories, decision, tmp_path)

    assert result.returncode != 0
    assert_ref_absent(repositories['repository'], decision['target_release'])


def test_apply_refuses_a_duplicate_sync_branch_before_creating_the_target(repositories, tmp_path):
    repository = repositories['repository']
    git(repository, 'update-ref', 'refs/remotes/upstream/stable/26.7', repositories['stable_27_1'])
    decision = plan(repositories)
    git(repository, 'branch', decision['sync_branch'], repositories['initial'])
    sync_sha = git(repository, 'rev-parse', decision['sync_branch'])

    result = apply(repositories, decision, tmp_path)

    assert result.returncode != 0
    assert git(repository, 'rev-parse', decision['sync_branch']) == sync_sha
    assert_ref_absent(repository, decision['target_release'])


def test_apply_refuses_missing_freebsd_profile_before_creating_a_target_branch(repositories, tmp_path):
    decision = plan(repositories)
    decision['freebsd_release'] = ''

    result = apply(repositories, decision, tmp_path)

    assert result.returncode != 0
    assert_ref_absent(repositories['repository'], decision['target_release'])


def test_apply_refuses_an_overlay_that_fails_preflight(repositories, tmp_path):
    repository = repositories['repository']
    decision = plan(repositories)
    git(repository, 'checkout', 'release/bind-rp/26.1')
    commit(
        repository,
        {
            OVERLAY_MANIFEST: f'{OVERLAY_MANIFEST}\ntools/resolver-overlay.txt\nREADME\n',
            'README': 'overlay-specific README\n',
        },
        'make overlay conflict with target upstream',
    )
    git(repository, 'checkout', 'master')

    result = apply(repositories, decision, tmp_path)

    assert result.returncode != 0
    assert_ref_absent(repository, decision['target_release'])
