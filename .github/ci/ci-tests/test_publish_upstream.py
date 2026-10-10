from git_fixtures import *

import pytest

from module_fixtures import *


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[3]
PUBLISHER = pathlib.Path(
    os.environ.get('PUBLISH_UPSTREAM', REPOSITORY_ROOT / '.github/ci/publish_upstream.py')
)
CORE_COMMIT = '2' * 40


def metadata(series, upstream_commit, core_commit=CORE_COMMIT):
    return json.dumps(upstream_profile(series, upstream_commit, core_commit=core_commit))


def publisher_module():
    return load_module('publish_upstream', PUBLISHER.resolve())


@pytest.fixture
def publication_repository(tmp_path):
    repository = tmp_path / 'repository'
    init_repository(repository)
    initial = commit(repository, {'dns/bind/bind.conf': 'bind-v1\n'}, 'upstream 26.1')
    git(repository, 'checkout', '-b', 'release/bind-rp/26.1')
    source_release = commit(
        repository,
        {'.resolver-plugins/upstream.json': metadata('26.1', initial)},
        'release 26.1',
    )
    git(repository, 'checkout', '-b', 'upstream-26.7', initial)
    upstream_commit = commit(
        repository,
        {'dns/bind/bind.conf': 'bind-v2\n'},
        'upstream 26.7',
    )
    git(
        repository,
        'update-ref',
        'refs/remotes/upstream/stable/26.7',
        upstream_commit,
    )
    target_branch = 'release/bind-rp/26.7'
    git(repository, 'checkout', '-b', target_branch)
    target_commit = commit(
        repository,
        {'.resolver-plugins/upstream.json': metadata('26.7', upstream_commit)},
        'bootstrap resolver plugin release',
    )
    sync_branch = f'sync/bootstrap/26.7/{upstream_commit[:12]}'
    git(repository, 'checkout', '-b', sync_branch)
    sync_commit = commit(
        repository,
        {'tools/resolver-overlay.txt': 'resolver overlay\n'},
        'bootstrap resolver plugin overlay',
    )
    git(repository, 'checkout', 'master')
    plan = {
        'action': 'bootstrap-review',
        'series': '26.7',
        'upstream_ref': 'upstream/stable/26.7',
        'upstream_commit': upstream_commit,
        'source_release': 'release/bind-rp/26.1',
        'target_release': target_branch,
        'sync_branch': sync_branch,
        'tools_tag': '26.7.1',
        'freebsd_release': '15.1',
        'bind_changed': True,
        'reason': 'new series has an upstream BIND change',
    }
    return {
        'repository': repository,
        'plan': plan,
        'source_release': source_release,
        'target_commit': target_commit,
        'sync_commit': sync_commit,
    }


class FakeGitHub:
    def __init__(self, *, eligible=('reviewer',), refs=None, pulls=None):
        self.eligible = set(eligible)
        self.refs = dict(refs or {})
        self.pulls = list(pulls or [])
        self.created_refs = []
        self.published_commits = []

    def check_assignee(self, repository, reviewer):
        if reviewer not in self.eligible:
            raise ValueError('reviewer is not assignable')

    def publish_commit(self, local_repository, repository, commit_sha):
        self.published_commits.append(commit_sha)

    def ref_sha(self, repository, branch):
        return self.refs.get(branch)

    def create_ref(self, repository, branch, commit_sha):
        if branch in self.refs:
            raise ValueError('reference already exists')
        self.refs[branch] = commit_sha
        self.created_refs.append(branch)

    def pulls_for(self, repository, head, base):
        return [
            pull for pull in self.pulls
            if pull['head'] == head and pull['base'] == base
        ]

    def create_pull(self, repository, head, base, title, body):
        pull = {
            'number': len(self.pulls) + 1,
            'head': head,
            'base': base,
            'state': 'open',
            'assignees': [],
            'title': title,
            'body': body,
        }
        self.pulls.append(pull)
        return pull

    def assign_pull(self, repository, number, reviewer):
        pull = next(pull for pull in self.pulls if pull['number'] == number)
        if reviewer not in pull['assignees']:
            pull['assignees'].append(reviewer)


def remote_github(repository, refs):
    """Make fetched Git refs agree with the remote API fixture."""
    for branch, sha in refs.items():
        git(repository, 'update-ref', f'refs/remotes/origin/{branch}', sha)
    return FakeGitHub(refs=refs)


def bootstrap_pair(repository, plan, files, prefix):
    git(repository, 'checkout', '-B', f'{prefix}-target', plan['upstream_commit'])
    target = commit(repository, files, 'bootstrap resolver plugin release')
    git(repository, 'checkout', '-B', f'{prefix}-sync')
    sync = commit(repository, {'tools/resolver-overlay.txt': 'resolver overlay\n'},
                  'bootstrap resolver plugin overlay')
    git(repository, 'checkout', 'master')
    return target, sync


def test_review_preflights_assignability_before_publishing_refs(publication_repository):
    module = publisher_module()
    github = FakeGitHub(eligible=())

    with pytest.raises(ValueError, match='assignable'):
        module.publish_plan(
            publication_repository['repository'],
            publication_repository['plan'],
            'owner/plugins',
            'reviewer',
            github,
        )

    assert github.refs == {}
    assert github.published_commits == []


def test_review_publication_preserves_targets_and_recovers_assigned_prs(
    publication_repository,
):
    module = publisher_module()
    github = FakeGitHub()
    plan = publication_repository['plan']

    module.publish_plan(
        publication_repository['repository'], plan, 'owner/plugins', 'reviewer', github
    )

    assert github.created_refs == [plan['sync_branch'], plan['target_release']]
    assert github.refs[plan['sync_branch']] == publication_repository['sync_commit']
    assert github.refs[plan['target_release']] == publication_repository['target_commit']
    assert len(github.pulls) == 1
    assert github.pulls[0]['assignees'] == ['reviewer']
    source = json.loads(git(publication_repository['repository'], 'show',
                            f"{plan['source_release']}:.resolver-plugins/upstream.json"))['upstream_commit']
    assert f"https://github.com/opnsense/plugins/compare/{source}...{plan['upstream_commit']}" in github.pulls[0]['body']

    expected_refs = dict(github.refs)
    github.pulls.clear()
    github.created_refs.clear()
    module.publish_plan(publication_repository['repository'], plan, 'owner/plugins', 'reviewer', github)
    assert github.created_refs == []
    assert github.refs == expected_refs

    assert len(github.pulls) == 1
    assert github.pulls[0]['assignees'] == ['reviewer']

    existing_pull = github.pulls[0]
    existing_pull['assignees'] = []
    module.publish_plan(publication_repository['repository'], plan, 'owner/plugins', 'reviewer', github)
    assert github.pulls == [existing_pull]
    assert existing_pull['assignees'] == ['reviewer']
    assert github.created_refs == []
    assert github.refs == expected_refs

    repository = publication_repository['repository']
    git(repository, 'checkout', 'upstream-26.7')
    upstream = commit(repository, {'dns/bind/bind.conf': 'bind-v3\n'}, 'upstream update')
    git(repository, 'update-ref', 'refs/remotes/upstream/stable/26.7', upstream)
    sync = f'sync/bind/26.7/{upstream[:12]}'
    git(repository, 'checkout', '-b', sync, publication_repository['sync_commit'])
    updated_sync = commit(repository, {
        '.resolver-plugins/upstream.json': metadata('26.7', upstream),
        'dns/bind/bind.conf': 'bind-v3\n',
    }, 'review updated upstream')
    update = dict(plan, action='update-review', source_release=plan['target_release'],
                  upstream_commit=upstream, sync_branch=sync)
    github.published_commits.clear()
    module.publish_plan(repository, update, 'owner/plugins', 'reviewer', github)
    assert github.created_refs == [sync]
    assert github.published_commits == [updated_sync]
    assert github.refs == dict(expected_refs, **{sync: updated_sync})
    assert len(github.pulls) == 2
    assert (github.pulls[-1]['head'], github.pulls[-1]['base'], github.pulls[-1]['assignees']) == (
        sync, plan['target_release'], ['reviewer'])


def test_retry_refuses_to_replace_a_different_existing_ref(publication_repository):
    module = publisher_module()
    plan = publication_repository['plan']
    github = FakeGitHub(refs={plan['sync_branch']: 'f' * 40})

    with pytest.raises(ValueError, match='different commit'):
        module.publish_plan(
            publication_repository['repository'], plan, 'owner/plugins', 'reviewer', github
        )

    assert github.refs[plan['sync_branch']] == 'f' * 40
    assert github.pulls == []


def test_publish_preflights_generated_tools_profile_before_remote_writes(
    publication_repository,
):
    module = publisher_module()
    plan = dict(publication_repository['plan'])
    plan['tools_tag'] = '26.7.2'
    github = FakeGitHub()

    with pytest.raises(ValueError, match='metadata'):
        module.publish_plan(
            publication_repository['repository'],
            plan,
            'owner/plugins',
            'reviewer',
            github,
        )

    assert github.refs == {}
    assert github.published_commits == []
    assert github.pulls == []


def test_recovery_rejects_existing_refs_with_malformed_tools_metadata(
    publication_repository,
):
    module = publisher_module()
    repository = publication_repository['repository']
    plan = publication_repository['plan']
    malformed = json.loads(metadata(plan['series'], plan['upstream_commit']))
    malformed['tools_tag'] = '26.7.r1'
    target, sync = bootstrap_pair(repository, plan,
        {'.resolver-plugins/upstream.json': json.dumps(malformed)}, 'malformed')
    github = remote_github(repository, {plan['sync_branch']: sync, plan['target_release']: target})

    handled = module.recover_pending_reviews(
        repository, 'owner/plugins', 'reviewer', github
    )

    assert handled is False
    assert github.pulls == []
    assert github.created_refs == []


@pytest.mark.parametrize('target_present', [False, True])
def test_recovery_uses_original_refs_and_assigns_missing_pr_after_core_changes(
    publication_repository, target_present,
):
    module = publisher_module()
    repository = publication_repository['repository']
    plan = publication_repository['plan']
    original_target = publication_repository['target_commit']
    original_sync = publication_repository['sync_commit']
    git(repository, 'branch', '-D', plan['sync_branch'])
    git(repository, 'branch', '-D', plan['target_release'])

    bootstrap_pair(repository, plan, {
        '.resolver-plugins/upstream.json': metadata(plan['series'], plan['upstream_commit'], 'f' * 40)
    }, 'retry')

    refs = {plan['sync_branch']: original_sync}
    if target_present:
        refs[plan['target_release']] = original_target
    github = remote_github(repository, refs)

    handled = module.recover_pending_reviews(
        repository, 'owner/plugins', 'reviewer', github
    )

    assert handled is True
    assert github.refs[plan['sync_branch']] == original_sync
    assert github.refs[plan['target_release']] == original_target
    assert github.created_refs == ([] if target_present else [plan['target_release']])
    assert github.published_commits == []
    assert len(github.pulls) == 1
    assert github.pulls[0]['head'] == plan['sync_branch']
    assert github.pulls[0]['base'] == plan['target_release']
    assert github.pulls[0]['assignees'] == ['reviewer']


@pytest.mark.parametrize("defect", ["nonpristine-parent", "invalid-core-metadata"])
def test_recovery_rejects_sync_only_bootstrap_with_invalid_parent(publication_repository, defect):
    module = publisher_module()
    repository = publication_repository['repository']
    plan = publication_repository['plan']
    git(repository, 'branch', '-D', plan['sync_branch'], plan['target_release'])
    profile = json.loads(metadata(plan['series'], plan['upstream_commit']))
    files = {}
    if defect == 'nonpristine-parent':
        files['unexpected-release-file'] = 'not a pristine baseline\n'
    else:
        profile['core_archive_url'] = 'https://github.com/opnsense/core/archive/' + '0' * 40 + '.tar.gz'
    files['.resolver-plugins/upstream.json'] = json.dumps(profile)
    _, sync = bootstrap_pair(repository, plan, files, 'invalid')
    github = remote_github(repository, {plan['sync_branch']: sync})

    handled = module.recover_pending_reviews(repository, 'owner/plugins', 'reviewer', github)

    assert handled is False
    assert github.refs == {plan['sync_branch']: sync}
    assert github.created_refs == []
    assert github.published_commits == []
    assert github.pulls == []


def test_recovery_rejects_sync_only_bootstrap_outside_current_upstream_ref(
    publication_repository,
):
    module = publisher_module()
    repository = publication_repository['repository']
    plan = publication_repository['plan']
    sync_commit = publication_repository['sync_commit']
    previous_upstream = git(repository, 'rev-parse', f"{plan['upstream_commit']}^")
    git(
        repository,
        'update-ref',
        'refs/remotes/upstream/stable/26.7',
        previous_upstream,
    )
    git(
        repository,
        'update-ref',
        f"refs/remotes/origin/{plan['sync_branch']}",
        sync_commit,
    )
    git(repository, 'branch', '-D', plan['sync_branch'])
    git(repository, 'branch', '-D', plan['target_release'])
    github = FakeGitHub(refs={plan['sync_branch']: sync_commit})

    handled = module.recover_pending_reviews(
        repository, 'owner/plugins', 'reviewer', github
    )

    assert handled is False
    assert github.created_refs == []
    assert github.pulls == []


@pytest.mark.parametrize('mismatch', [None, 'blobs', 'trees', 'commits'])
def test_github_reproduces_exact_local_objects_before_accepting_commit(tmp_path, monkeypatch, mismatch):
    module = publisher_module()
    date = '2026-01-02T03:04:05+00:00'
    monkeypatch.setenv('GIT_AUTHOR_DATE', date)
    monkeypatch.setenv('GIT_COMMITTER_DATE', date)
    repository = init_repository(tmp_path / 'git')
    parent = commit(repository, {'removed': 'old\n', 'updated': 'before\n'}, 'before')
    git(repository, 'rm', 'removed')
    files = {'added with trailing space ': 'added\n', 'updated': 'after\n'}
    head = commit(repository, files, 'publish fixture')
    blobs = {name: git(repository, 'rev-parse', f'{head}:{name}') for name in files}
    entries = [{'path': name, 'mode': '100644', 'type': 'blob', 'sha': digest}
               for name, digest in blobs.items()]
    entries.insert(1, {'path': 'removed', 'sha': None})
    tree = git(repository, 'rev-parse', f'{head}^{{tree}}')
    identity = {'name': 'CI fixture', 'email': 'ci@example.invalid', 'date': date}
    expected = [
        ('blobs', {'content': base64.b64encode(content.encode()).decode(), 'encoding': 'base64'}, blobs[name])
        for name, content in files.items()
    ] + [
        ('trees', {'base_tree': git(repository, 'rev-parse', f'{parent}^{{tree}}'), 'tree': entries}, tree),
        ('commits', {'message': 'publish fixture\n', 'tree': tree, 'parents': [parent],
                     'author': identity, 'committer': identity}, head),
    ]
    calls = []
    def api(method, endpoint, payload):
        kind, data, digest = expected[len(calls)]
        calls.append(kind)
        assert (method, endpoint, payload) == ('POST', f'repos/example/plugins/git/{kind}', data)
        return {'sha': 'f' * 40 if kind == mismatch else digest}
    client = module.GitHub()
    monkeypatch.setattr(client, 'call', api)
    if mismatch:
        with pytest.raises(ValueError, match=f'reproduce (?:the )?local {mismatch[:-1]}'):
            client.publish_commit(repository, 'example/plugins', head)
        stop = next(i for i, item in enumerate(expected) if item[0] == mismatch) + 1
    else:
        client.publish_commit(repository, 'example/plugins', head)
        stop = len(expected)
    assert calls == [item[0] for item in expected[:stop]]


@pytest.mark.parametrize('exact_matches', [0, 1, 2])
def test_github_ref_lookup_requires_one_exact_branch(monkeypatch, exact_matches):
    module = publisher_module()
    branch = 'release/bind-rp/26.7'
    exact = {'ref': f'refs/heads/{branch}', 'object': {'sha': 'a' * 40}}
    response = [{'ref': f'refs/heads/{branch}-other', 'object': {'sha': 'b' * 40}}] + [exact] * exact_matches
    client = module.GitHub()
    call = Mock(return_value=response)
    monkeypatch.setattr(client, 'call', call)
    if exact_matches == 2:
        with pytest.raises(ValueError, match='ambiguous reference'):
            client.ref_sha('example/plugins', branch)
    else:
        assert client.ref_sha('example/plugins', branch) == ('a' * 40 if exact_matches else None)
    call.assert_called_once_with('GET', f'repos/example/plugins/git/matching-refs/heads/{branch}')
