from git_fixtures import *

import pytest


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[3]
PLANNER = pathlib.Path(os.environ.get('SYNC_UPSTREAM', REPOSITORY_ROOT / '.github/ci/sync_upstream.py'))
METADATA_PATH = '.resolver-plugins/upstream.json'
AUDIT_PATH = '.resolver-plugins/sync-plan.json'
CALLER_PATH = '.github/workflows/bind-tests.yml'


@pytest.fixture
def repositories(tmp_path):
    return bind_sync_repositories(tmp_path)


def add_release(repositories, series, upstream_commit):
    repository = repositories['repository']
    git(repository, 'checkout', '-B', f'release/bind-rp/{series}', upstream_commit)
    commit(repository, {
        'dns/bind/fork.conf': 'resolver overlay\n',
        'Mk/plugins.mk': 'native framework\nfork dependency formula\n',
        'tools/not-an-overlay.txt': 'must not copy\n',
    }, f'release {series}')
    git(repository, 'checkout', 'master')


def plan(repositories, tmp_path):
    return seal_sync(discover_sync(repositories, PLANNER), tmp_path, PLANNER)


def configure_overlay_merge(repositories, base, overlay, target):
    repository = repositories['repository']
    path = 'dns/bind/merge.conf'
    git(repository, 'checkout', '-B', 'overlay-base', repositories['initial'])
    baseline = commit(repository, {path: base}, 'upstream overlay base')
    git(repository, 'update-ref', 'refs/remotes/upstream/stable/26.1', baseline)
    git(repository, 'checkout', '-B', 'release/bind-rp/26.1', baseline)
    commit(repository, {path: overlay}, 'reviewed fork overlay')
    git(repository, 'checkout', '-B', 'overlay-target', baseline)
    target_commit = commit(repository, {path: target}, 'changed upstream overlay target')
    git(repository, 'update-ref', 'refs/remotes/upstream/stable/26.7', target_commit)
    git(repository, 'checkout', 'master')
    return path


def test_unrelated_existing_upstream_change_is_noop(repositories):
    add_release(repositories, '26.7', repositories['initial'])
    git(repositories['repository'], 'update-ref', '-d', 'refs/remotes/upstream/stable/27.1')

    discovery = discover_sync(repositories, PLANNER)

    assert discovery['action'] == 'noop'
    assert discovery['upstream_head'] == repositories['stable_26_7']
    assert discovery['upstream_commit'] == repositories['initial']
    assert (discovery['tools_tag'], discovery['freebsd_release']) == ('26.7.1', '15.1')
    assert discovery['bind_changed'] is False
    assert 'sync_branch' not in discovery and 'core_commit' not in discovery


def test_missing_source_ancestry_blocks_planning(repositories):
    repository = repositories['repository']
    git(repository, 'checkout', '--orphan', 'unrelated-source')
    git(repository, 'rm', '-rf', '.')
    unrelated = commit(repository, {'dns/bind/bind.conf': 'unrelated source\n'}, 'unrelated history')
    git(repository, 'branch', '-f', 'release/bind-rp/26.1', unrelated)
    git(repository, 'checkout', 'master')

    assert discover_sync(repositories, PLANNER)['action'] == 'blocked'


@pytest.mark.parametrize('bind_changed', [False, True], ids=['unchanged-bind', 'changed-bind'])
def test_apply_bootstrap_review_creates_pristine_release_and_bounded_overlay(repositories, tmp_path, bind_changed):
    repository = repositories['repository']
    upstream = repositories['stable_27_1' if bind_changed else 'stable_26_7']
    git(repository, 'update-ref', 'refs/remotes/upstream/stable/26.7', upstream)
    if bind_changed:
        git(repository, 'checkout', 'release/bind-rp/26.1')
        commit(repository, {'.resolver-plugins/overlay-paths.txt': 'tools/ci\n.github\n',
                            '.resolver-plugins/bind920.json': 'historical approval\n',
                            '.resolver-plugins/bind-compatibility.json': 'historical policy\n'},
               'historical source policy copies')
        git(repository, 'checkout', 'master')
    sealed = plan(repositories, tmp_path)
    checkout_before = git(repository, 'rev-parse', 'HEAD')
    (repository / 'local-notes').write_text('preserve dirty user work\n')

    result = apply_sync(repositories, sealed, tmp_path, PLANNER)

    assert result.returncode == 0, result.stderr
    assert (sealed['action'], sealed['series'], sealed['bind_changed']) == ('bootstrap-review', '26.7', bind_changed)
    assert sealed['tools_commit'] == git(repositories['tools'], 'rev-parse', '26.7.1^{commit}')
    assert git(repository, 'rev-parse', 'HEAD') == checkout_before
    assert (repository / 'local-notes').read_text() == 'preserve dirty user work\n'
    target, review = sealed['target_release'], sealed['sync_branch']
    assert re.fullmatch(rf'sync/bootstrap/26.7/{upstream[:12]}-[0-9a-f]{{12}}', review)
    assert git(repository, 'rev-parse', f'{target}^') == upstream
    assert git(repository, 'rev-parse', f'{review}^') == git(repository, 'rev-parse', target)
    assert git(repository, 'diff-tree', '--no-commit-id', '--name-only', '-r', upstream, target) == ''
    assert git(repository, 'show', f'{review}:dns/bind/fork.conf') == 'resolver overlay'
    assert git(repository, 'show', f'{review}:Mk/plugins.mk') == 'native framework\nfork dependency formula'
    for branch in (target, review):
        assert git(repository, 'ls-tree', branch, '--', 'tools/not-an-overlay.txt', 'dns/bind/tests') == ''
        assert git(repository, 'show', f'{branch}:dns/bind/bind.conf') == ('bind-v2' if bind_changed else 'bind-v1')
        assert git(repository, 'show', f'{branch}:README') == 'unrelated 26.7'
        assert git(repository, 'ls-tree', branch, '--', '.resolver-plugins/overlay-paths.txt',
                   '.resolver-plugins/bind920.json', '.resolver-plugins/bind-compatibility.json') == ''
    for branch in (target, review):
        assert git(repository, 'ls-tree', branch, '--', '.resolver-plugins') == ''
    profile_path = tmp_path / 'generated-upstream.json'
    subprocess.run(['python3', str(PLANNER), 'profile', '--plan', str(tmp_path / 'plan.json'),
                    '--output', str(profile_path)], check=True)
    assert json.loads(profile_path.read_text()) == upstream_profile('26.7', upstream)
    caller = git(repository, 'show', f'{review}:{CALLER_PATH}')
    assert 'contents: read' in caller and 'secrets:' not in caller
    assert 'pull_request_base:' in caller and 'pull_request_sha:' in caller
    assert '.resolver-plugins' not in caller and 'Mk/plugins.mk' in caller
    assert git(repository, 'ls-tree', target, '--', AUDIT_PATH, CALLER_PATH) == ''
    original = git(repository, 'rev-parse', target, review)
    retry = apply_sync(repositories, sealed, tmp_path, PLANNER,
                       {'GIT_AUTHOR_DATE': '2030-01-01T00:00:00+00:00',
                        'GIT_COMMITTER_DATE': '2030-01-01T00:00:00+00:00'})
    assert retry.returncode == 0, retry.stderr
    assert git(repository, 'rev-parse', target, review) == original


def test_bootstrap_accepts_a_source_baseline_on_its_own_stable_lineage(repositories, tmp_path):
    repository = repositories['repository']
    git(repository, 'checkout', '-B', 'source-upstream', repositories['initial'])
    baseline = commit(repository, {'README': 'source stable branch\n'}, 'divergent source lineage')
    git(repository, 'update-ref', 'refs/remotes/upstream/stable/26.1', baseline)
    git(repository, 'checkout', 'release/bind-rp/26.1')
    git(repository, 'merge', '--no-ff', '-m', 'retain source stable ancestry', baseline)
    git(repository, 'checkout', 'master')
    sealed = plan(repositories, tmp_path)

    result = apply_sync(repositories, sealed, tmp_path, PLANNER)

    assert result.returncode == 0, result.stderr
    assert git(repository, 'show', f"{sealed['sync_branch']}:dns/bind/fork.conf") == 'resolver overlay'
    assert git(repository, 'ls-tree', sealed['sync_branch'], '--', '.resolver-plugins') == ''


def test_tools_updates_are_discovered_without_metadata_refresh_reviews(repositories):
    repository, tools = repositories['repository'], repositories['tools']
    git(repository, 'update-ref', '-d', 'refs/remotes/upstream/stable/26.7')
    git(repository, 'update-ref', '-d', 'refs/remotes/upstream/stable/27.1')
    source = git(repository, 'rev-parse', 'release/bind-rp/26.1')
    for tag, release in [('26.1.12', '14.4'), ('26.1.13', '14.5')]:
        tools_commit = commit(tools, {'config/26.1/build.conf': f'OS?= {release}\n'}, tag)
        git(tools, 'tag', tag)
        discovery = discover_sync(repositories, PLANNER)
        assert discovery['action'] == 'noop'
        assert (discovery['tools_tag'], discovery['freebsd_release'], discovery['tools_commit']) == (tag, release, tools_commit)
        assert git(repository, 'rev-parse', 'release/bind-rp/26.1') == source
        assert git(repository, 'for-each-ref', '--format=%(refname)', 'refs/heads/sync') == ''


def test_apply_update_review_keeps_release_head_and_uses_new_upstream_baseline(repositories, tmp_path):
    repository = repositories['repository']
    add_release(repositories, '26.7', repositories['initial'])
    git(repository, 'update-ref', 'refs/remotes/upstream/stable/26.7', repositories['stable_27_1'])
    sealed = plan(repositories, tmp_path)
    source = git(repository, 'rev-parse', sealed['target_release'])

    result = apply_sync(repositories, sealed, tmp_path, PLANNER)

    assert result.returncode == 0, result.stderr
    assert sealed['action'] == 'update-review' and sealed['bind_changed'] is True
    assert git(repository, 'rev-parse', sealed['target_release']) == source
    assert git(repository, 'rev-parse', f"{sealed['sync_branch']}^") == repositories['stable_27_1']
    assert git(repository, 'show', f"{sealed['sync_branch']}:dns/bind/bind.conf") == 'bind-v2'
    assert git(repository, 'show', f"{sealed['sync_branch']}:dns/bind/fork.conf") == 'resolver overlay'
    assert git(repository, 'ls-tree', sealed['sync_branch'], '--', 'tools/not-an-overlay.txt') == ''


def test_apply_three_way_merges_shared_result_and_retains_both_changes(repositories, tmp_path):
    base = [f'context-{number}\n' for number in range(24)]
    base[1], base[10], base[20] = 'shared=old\n', 'upstream=old\n', 'fork=old\n'
    overlay, target, expected = base.copy(), base.copy(), base.copy()
    overlay[1] = target[1] = expected[1] = 'shared=new\n'
    target[10] = expected[10] = 'upstream=new\n'
    overlay[20] = expected[20] = 'fork=new\n'
    path = configure_overlay_merge(repositories, ''.join(base), ''.join(overlay), ''.join(target))
    sealed = plan(repositories, tmp_path)

    result = apply_sync(repositories, sealed, tmp_path, PLANNER)

    assert result.returncode == 0, result.stderr
    assert git(repositories['repository'], 'show', f"{sealed['sync_branch']}:{path}") == ''.join(expected).strip()


@pytest.mark.parametrize('failure', ['conflict', 'source-drift', 'tools-drift', 'existing-target', 'existing-review'])
def test_apply_failure_preserves_existing_refs(repositories, tmp_path, failure):
    repository = repositories['repository']
    if failure == 'conflict':
        configure_overlay_merge(repositories, 'value=base\n', 'value=fork\n', 'value=upstream\n')
    sealed = plan(repositories, tmp_path)
    if failure == 'source-drift':
        git(repository, 'checkout', sealed['source_release'])
        commit(repository, {'dns/bind/late-change': 'reviewed after discovery\n'}, 'source advanced')
        git(repository, 'checkout', 'master')
    elif failure == 'tools-drift':
        replacement = commit(repositories['tools'], {'config/26.7/other.conf': 'upstream retag\n'},
                             'tools tag moved after discovery')
        git(repositories['tools'], 'tag', '--force', sealed['tools_tag'], replacement)
    elif failure.startswith('existing-'):
        branch = sealed['target_release' if failure == 'existing-target' else 'sync_branch']
        git(repository, 'branch', branch, repositories['initial'])
    refs_before = git(repository, 'show-ref')

    result = apply_sync(repositories, sealed, tmp_path, PLANNER)

    assert result.returncode != 0
    assert git(repository, 'show-ref') == refs_before
