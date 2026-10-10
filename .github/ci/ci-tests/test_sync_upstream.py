from git_fixtures import *

import pytest


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[3]
PLANNER = pathlib.Path(
    os.environ.get('SYNC_UPSTREAM', REPOSITORY_ROOT / '.github/ci/sync_upstream.py')
)
METADATA_PATH = '.resolver-plugins/upstream.json'
OVERLAY_MANIFEST = '.resolver-plugins/overlay-paths.txt'
CORE_COMMIT = '2' * 40
CORE_ARCHIVE_SHA256 = 'a' * 64


def metadata(series, upstream_commit, freebsd_release='14.3', tools_tag=None):
    return json.dumps(upstream_profile(series, upstream_commit, core_commit=CORE_COMMIT,
                                      freebsd_release=freebsd_release, tools_tag=tools_tag))


@pytest.fixture
def repositories(tmp_path):
    upstream = tmp_path / 'upstream.git'
    origin = tmp_path / 'origin.git'
    source = tmp_path / 'source'
    repository = tmp_path / 'repository'
    tools = tmp_path / 'tools'
    init_repository(tools)
    commit(tools, {'config/26.1/build.conf': 'OS?=14.2\n'}, 'tools 26.1')
    git(tools, 'tag', '26.1')
    commit(tools, {'config/26.1/build.conf': 'OS?=14.3\n'}, 'tools 26.1.11')
    git(tools, 'tag', '26.1.11')
    commit(tools, {'config/26.1/build.conf': 'OS?=99.1\n'}, 'prerelease 26.1')
    git(tools, 'tag', '26.1.b')
    git(tools, 'tag', '26.1.r1')
    commit(tools, {'config/26.7/build.conf': 'OS?=15.0\n'}, 'tools 26.7')
    git(tools, 'tag', '26.7')
    commit(tools, {'config/26.7/build.conf': 'OS?=15.1\n'}, 'tools 26.7.1')
    git(tools, 'tag', '26.7.1')
    commit(tools, {'config/26.7/build.conf': 'OS?=99.7\n'}, 'prerelease 26.7')
    git(tools, 'tag', '26.7.b')
    git(tools, 'tag', '26.7.r1')
    git(tmp_path, 'init', '--bare', '--initial-branch=master', upstream)
    git(tmp_path, 'init', '--bare', '--initial-branch=master', origin)
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
        'tools': tools,
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


def configure_overlay_merge(
    repositories,
    path: str,
    base_contents: str,
    overlay_contents: str,
    target_contents: str,
    *,
    bind_changed: bool = False,
) -> None:
    repository = repositories['repository']
    git(repository, 'checkout', '-B', 'overlay-base', repositories['initial'])
    overlay_base = commit(repository, {path: base_contents}, 'add upstream overlay base')
    git(repository, 'update-ref', 'refs/remotes/upstream/stable/26.1', overlay_base)

    git(repository, 'checkout', '-B', 'release/bind-rp/26.1', overlay_base)
    commit(
        repository,
        {
            METADATA_PATH: metadata('26.1', overlay_base),
            OVERLAY_MANIFEST: f'{OVERLAY_MANIFEST}\ntools/resolver-overlay.txt\n{path}\n',
            'tools/resolver-overlay.txt': 'resolver overlay\n',
            path: overlay_contents,
        },
        'release overlay fixture',
    )

    git(repository, 'checkout', '-B', 'overlay-target', overlay_base)
    target_files = {path: target_contents}
    if bind_changed:
        target_files['dns/bind/bind.conf'] = 'bind-v2\n'
    overlay_target = commit(repository, target_files, 'update overlay target upstream')
    git(repository, 'update-ref', 'refs/remotes/upstream/stable/26.7', overlay_target)
    git(repository, 'checkout', 'master')


def plan(repositories) -> dict:
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
        '--tools-repository',
        str(repositories['tools']),
    ]
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    return json.loads(result.stdout)


def apply(
    repositories,
    decision: dict,
    tmp_path: pathlib.Path,
    environment: dict[str, str] | None = None,
    *,
    core_commit: str = CORE_COMMIT,
    core_archive_url: str | None = None,
    core_archive_sha256: str = CORE_ARCHIVE_SHA256,
) -> subprocess.CompletedProcess:
    plan_path = tmp_path / 'plan.json'
    plan_path.write_text(json.dumps(decision))
    if core_archive_url is None:
        core_archive_url = f'https://github.com/opnsense/core/archive/{core_commit}.tar.gz'
    return subprocess.run(
        [
            'python3', str(PLANNER), 'apply',
            '--repository', str(repositories['repository']),
            '--plan', str(plan_path),
            '--core-commit', core_commit,
            '--core-archive-url', core_archive_url,
            '--core-archive-sha256', core_archive_sha256,
        ],
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, **(environment or {})},
    )


def assert_plan_shape(decision: dict) -> None:
    assert set(decision) == {
        'action',
        'series',
        'upstream_ref',
        'upstream_commit',
        'source_release',
        'target_release',
        'sync_branch',
        'tools_tag',
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
    assert decision['tools_tag'] == '26.7.1'
    assert decision['freebsd_release'] == '15.1'
    assert decision['bind_changed'] is False


def test_source_metadata_blocks_planning_when_lineage_is_wrong(repositories):
    repository = repositories['repository']
    git(repository, 'checkout', 'release/bind-rp/26.1')
    invalid = json.loads(metadata('26.1', repositories['initial']))
    invalid['upstream_commit'] = repositories['stable_26_7']
    commit(repository, {METADATA_PATH: json.dumps(invalid)}, 'record wrong source lineage')
    git(repository, 'checkout', 'master')

    decision = plan(repositories)

    assert decision['action'] == 'blocked'


@pytest.mark.parametrize('bind_changed', [False, True], ids=['unchanged-bind', 'changed-bind'])
def test_apply_bootstrap_review_creates_pristine_release_and_overlay_branch(repositories, tmp_path, bind_changed):
    upstream_commit = repositories['stable_27_1' if bind_changed else 'stable_26_7']
    repository = repositories['repository']
    git(repository, 'update-ref', 'refs/remotes/upstream/stable/26.7', upstream_commit)
    decision = plan(repositories)
    assert decision['action'] == 'bootstrap-review'
    assert decision['series'] == '26.7'
    assert decision['source_release'] == 'release/bind-rp/26.1'
    assert decision['target_release'] == 'release/bind-rp/26.7'
    assert decision['tools_tag'] == '26.7.1'
    assert decision['freebsd_release'] == '15.1'
    assert decision['bind_changed'] is bind_changed
    assert decision['sync_branch'] == f'sync/bootstrap/26.7/{upstream_commit[:12]}'

    result = apply(repositories, decision, tmp_path)

    assert result.returncode == 0, result.stderr
    target = decision['target_release']
    assert git(repository, 'ls-tree', target, '--', 'tools/resolver-overlay.txt') == ''
    assert git(repository, 'show', f'{decision["sync_branch"]}:tools/resolver-overlay.txt') == 'resolver overlay'
    for branch in (target, decision['sync_branch']):
        assert git(repository, 'show', f'{branch}:dns/bind/bind.conf') == ('bind-v2' if bind_changed else 'bind-v1')
        assert git(repository, 'ls-tree', branch, '--', 'tools/not-an-overlay.txt') == ''
    target_metadata = json.loads(git(repository, 'show', f'{target}:{METADATA_PATH}'))
    assert target_metadata == {
        'series': '26.7',
        'upstream_branch': 'stable/26.7',
        'upstream_commit': upstream_commit,
        'tools_tag': '26.7.1',
        'freebsd_release': '15.1',
        'core_commit': CORE_COMMIT,
        'core_archive_url': f'https://github.com/opnsense/core/archive/{CORE_COMMIT}.tar.gz',
        'core_archive_sha256': CORE_ARCHIVE_SHA256,
    }


def test_apply_bootstrap_review_accepts_source_metadata_from_divergent_source_stable_branch(
    repositories, tmp_path
):
    repository = repositories['repository']
    git(repository, 'checkout', '-B', 'source-stable-26.1', repositories['initial'])
    source_upstream_commit = commit(
        repository, {'README': 'source stable 26.1\n'}, 'source stable 26.1'
    )
    git(repository, 'update-ref', 'refs/remotes/upstream/stable/26.1', source_upstream_commit)
    git(repository, 'checkout', 'release/bind-rp/26.1')
    commit(
        repository,
        {METADATA_PATH: metadata('26.1', source_upstream_commit)},
        'record divergent source upstream commit',
    )
    git(repository, 'checkout', 'master')
    decision = plan(repositories)

    result = apply(repositories, decision, tmp_path)

    assert result.returncode == 0, result.stderr
    target_metadata = json.loads(git(repository, 'show', f'{decision["target_release"]}:{METADATA_PATH}'))
    assert target_metadata['upstream_commit'] == repositories['stable_26_7']


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
    first_sync_commit = git(repositories['repository'], 'rev-parse', decision['sync_branch'])
    git(repositories['repository'], 'branch', '-D', decision['target_release'])
    git(repositories['repository'], 'branch', '-D', decision['sync_branch'])

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
    assert git(repositories['repository'], 'rev-parse', decision['sync_branch']) == first_sync_commit


def test_apply_update_review_creates_only_overlay_sync_branch(repositories, tmp_path):
    add_release(repositories, '26.7', repositories['initial'])
    git(repositories['repository'], 'update-ref', 'refs/remotes/upstream/stable/26.7', repositories['stable_27_1'])
    decision = plan(repositories)
    source_sha = git(repositories['repository'], 'rev-parse', decision['target_release'])

    result = apply(repositories, decision, tmp_path)

    assert result.returncode == 0, result.stderr
    assert git(repositories['repository'], 'rev-parse', decision['target_release']) == source_sha
    assert git(repositories['repository'], 'show', f'{decision["sync_branch"]}:tools/resolver-overlay.txt') == 'resolver overlay'
    assert decision['action'] == 'update-review'
    assert decision['series'] == '26.7'
    assert decision['bind_changed'] is True
    assert decision['sync_branch'] == f'sync/bind/26.7/{repositories["stable_27_1"][:12]}'


def test_apply_three_way_merges_same_result_and_retains_target_and_overlay_changes(
    repositories, tmp_path
):
    path = 'tools/mergeable-overlay.txt'
    configure_overlay_merge(
        repositories,
        path,
        (
            'header\nshared=old\nline-03\nline-04\nline-05\nline-06\nline-07\n'
            'line-08\nline-09\ncontext=base\nline-11\nline-12\nline-13\nline-14\n'
            'line-15\nline-16\nline-17\nline-18\nline-19\noverlay=old\ntail\n'
        ),
        (
            'header\nshared=new\nline-03\nline-04\nline-05\nline-06\nline-07\n'
            'line-08\nline-09\ncontext=base\nline-11\nline-12\nline-13\nline-14\n'
            'line-15\nline-16\nline-17\nline-18\nline-19\noverlay=new\ntail\n'
        ),
        (
            'header\nshared=new\nline-03\nline-04\nline-05\nline-06\nline-07\n'
            'line-08\nline-09\ncontext=target\nline-11\nline-12\nline-13\nline-14\n'
            'line-15\nline-16\nline-17\nline-18\nline-19\noverlay=old\ntail\n'
        ),
    )
    decision = plan(repositories)

    result = apply(repositories, decision, tmp_path)

    assert result.returncode == 0, result.stderr
    assert git(repositories['repository'], 'show', f'{decision["sync_branch"]}:{path}') == (
        'header\nshared=new\nline-03\nline-04\nline-05\nline-06\nline-07\n'
        'line-08\nline-09\ncontext=target\nline-11\nline-12\nline-13\nline-14\n'
        'line-15\nline-16\nline-17\nline-18\nline-19\noverlay=new\ntail'
    )


def test_apply_three_way_conflict_preserves_all_refs(
    repositories, tmp_path
):
    path = 'tools/conflicting-overlay.txt'
    repository = repositories['repository']
    configure_overlay_merge(
        repositories,
        path,
        'header\nvalue=base\ntail\n',
        'header\nvalue=overlay\ntail\n',
        'header\nvalue=target\ntail\n',
        bind_changed=True,
    )
    decision = plan(repositories)
    refs_before = git(repository, 'for-each-ref', '--format=%(refname) %(objectname)', 'refs/heads')

    result = apply(repositories, decision, tmp_path)

    assert result.returncode != 0
    assert git(repository, 'for-each-ref', '--format=%(refname) %(objectname)', 'refs/heads') == refs_before


@pytest.mark.parametrize('existing', ['target_release', 'sync_branch'])
def test_apply_preserves_all_refs_when_an_output_branch_already_exists(repositories, tmp_path, existing):
    decision = plan(repositories)
    git(repositories['repository'], 'branch', decision[existing], repositories['initial'])

    refs_before = git(repositories['repository'], 'show-ref')
    result = apply(repositories, decision, tmp_path)

    assert result.returncode != 0
    assert git(repositories['repository'], 'show-ref') == refs_before


def test_apply_rejects_pathspec_magic_in_the_overlay_manifest(repositories, tmp_path):
    repository = repositories['repository']
    decision = plan(repositories)
    git(repository, 'checkout', 'release/bind-rp/26.1')
    commit(repository, {OVERLAY_MANIFEST: ':(glob)tools/**\n'}, 'add glob overlay manifest entry')
    git(repository, 'checkout', 'master')

    refs_before = git(repositories['repository'], 'show-ref')
    result = apply(repositories, decision, tmp_path)

    assert result.returncode != 0
    assert git(repositories['repository'], 'show-ref') == refs_before
