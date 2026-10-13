from git_fixtures import *

import pytest

from module_fixtures import *


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[3]
PUBLISHER = pathlib.Path(
    os.environ.get('PUBLISH_UPSTREAM', REPOSITORY_ROOT / '.github/ci/publish_upstream.py')
)
PLANNER = REPOSITORY_ROOT / '.github/ci/sync_upstream.py'
sys.path.insert(0, str(REPOSITORY_ROOT / '.github/ci'))


def publisher_module():
    return load_module('publish_upstream', PUBLISHER.resolve())


@pytest.fixture
def publication_repository(tmp_path):
    fixture = bind_sync_repositories(tmp_path)
    plan = seal_sync(discover_sync(fixture, PLANNER), tmp_path, PLANNER)
    result = apply_sync(fixture, plan, tmp_path, PLANNER)
    assert result.returncode == 0, result.stderr
    fixture.update(plan=plan, source_release=plan['source_commit'],
                   target_commit=git(fixture['repository'], 'rev-parse', plan['target_release']),
                   sync_commit=git(fixture['repository'], 'rev-parse', plan['sync_branch']),
                   tmp_path=tmp_path)
    return fixture


class FakeGitHub:
    def __init__(self, *, refs=None, pulls=None):
        self.refs = dict(refs or {})
        self.pulls = list(pulls or [])
        self.created_refs = []
        self.published_commits = []

    def check_assignee(self, repository, reviewer):
        pass

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


def publication_github(fixture, refs=None):
    return FakeGitHub(refs={fixture['plan']['source_release']: fixture['source_release'],
                            **(refs or {})})


def publish(module, fixture, github, plan=None):
    module.publish_plan(fixture['repository'], plan or fixture['plan'], 'owner/plugins',
                        'reviewer', github, fixture['tools'])


def test_review_publication_preserves_targets_and_recovers_assigned_prs(publication_repository):
    fixture = publication_repository
    module, github, plan = publisher_module(), publication_github(fixture), fixture['plan']
    publish(module, fixture, github)

    assert github.created_refs == [plan['sync_branch'], plan['target_release']]
    assert github.refs[plan['sync_branch']] == fixture['sync_commit']
    assert github.refs[plan['target_release']] == fixture['target_commit']
    assert len(github.pulls) == 1 and github.pulls[0]['assignees'] == ['reviewer']
    source = upstream_profile(upstream_commit=fixture['initial'])
    assert f"https://github.com/opnsense/plugins/compare/{source['upstream_commit']}...{plan['upstream_commit']}" in github.pulls[0]['body']
    assert f"Pinned core: `{plan['core_commit']}`" in github.pulls[0]['body']
    expected_refs = dict(github.refs)
    github.pulls.clear()
    github.created_refs.clear()
    publish(module, fixture, github)
    assert github.created_refs == [] and github.refs == expected_refs
    assert len(github.pulls) == 1 and github.pulls[0]['assignees'] == ['reviewer']
    github.pulls[0]['assignees'] = []
    publish(module, fixture, github)
    assert len(github.pulls) == 1 and github.pulls[0]['assignees'] == ['reviewer']
    assert github.created_refs == [] and github.refs == expected_refs

    # The maintainer merges the reviewed source, then upstream changes again.
    repository = fixture['repository']
    git(repository, 'update-ref', f"refs/heads/{plan['target_release']}", fixture['sync_commit'])
    github.refs[plan['target_release']] = fixture['sync_commit']
    git(repository, 'checkout', '-B', 'upstream-update', plan['upstream_commit'])
    upstream = commit(repository, {'dns/bind/bind.conf': 'bind-v3\n'}, 'upstream source update')
    git(repository, 'update-ref', 'refs/remotes/upstream/stable/26.7', upstream)
    git(repository, 'checkout', 'master')
    update = seal_sync(discover_sync(fixture, PLANNER), fixture['tmp_path'], PLANNER)
    result = apply_sync(fixture, update, fixture['tmp_path'], PLANNER)
    assert result.returncode == 0, result.stderr
    github.created_refs.clear()
    github.published_commits.clear()
    publish(module, fixture, github, update)
    updated_sync = git(repository, 'rev-parse', update['sync_branch'])
    assert github.created_refs == [update['sync_branch']]
    assert github.published_commits == [updated_sync]
    assert github.refs[plan['target_release']] == fixture['sync_commit']
    assert len(github.pulls) == 2
    assert (github.pulls[-1]['head'], github.pulls[-1]['base'], github.pulls[-1]['assignees']) == (
        update['sync_branch'], plan['target_release'], ['reviewer'])


def test_retry_refuses_to_replace_a_different_existing_ref(publication_repository):
    fixture = publication_repository
    module, plan = publisher_module(), fixture['plan']
    github = publication_github(fixture, {plan['sync_branch']: 'f' * 40})

    with pytest.raises(ValueError, match='different commit'):
        publish(module, fixture, github)

    assert github.refs[plan['sync_branch']] == 'f' * 40
    assert github.pulls == github.published_commits == []


@pytest.mark.parametrize('target_present', [False, True])
def test_recovery_uses_exact_audit_and_original_refs_after_core_changes(publication_repository, target_present):
    fixture = publication_repository
    module, repository, plan = publisher_module(), fixture['repository'], fixture['plan']
    git(repository, 'branch', '-D', plan['sync_branch'], plan['target_release'])
    # A later control/source construction cannot replace the already published proposal.
    git(repository, 'checkout', '-B', 'retry-target', plan['upstream_commit'])
    commit(repository, {'.resolver-plugins/upstream.json': json.dumps(upstream_profile(
        plan['series'], plan['upstream_commit'], core_commit='f' * 40))}, 'later core input')
    git(repository, 'checkout', 'master')
    refs = {plan['source_release']: plan['source_commit'], plan['sync_branch']: fixture['sync_commit']}
    if target_present:
        refs[plan['target_release']] = fixture['target_commit']
    github = remote_github(repository, refs)

    handled = module.recover_pending_reviews(repository, 'owner/plugins', 'reviewer', github, fixture['tools'])

    assert handled is True
    assert github.refs[plan['sync_branch']] == fixture['sync_commit']
    assert github.refs[plan['target_release']] == fixture['target_commit']
    assert github.created_refs == ([] if target_present else [plan['target_release']])
    assert github.published_commits == []
    assert len(github.pulls) == 1
    assert (github.pulls[0]['head'], github.pulls[0]['base'], github.pulls[0]['assignees']) == (
        plan['sync_branch'], plan['target_release'], ['reviewer'])
    assert f"Pinned core: `{plan['core_commit']}`" in github.pulls[0]['body']


def test_recovery_rejects_sync_only_bootstrap_after_upstream_drift(publication_repository):
    fixture = publication_repository
    module, repository, plan = publisher_module(), fixture['repository'], fixture['plan']
    git(repository, 'checkout', '-B', 'upstream-update', plan['upstream_commit'])
    advanced = commit(repository, {'dns/bind/bind.conf': 'bind-v3\n'}, 'advance upstream')
    git(repository, 'checkout', 'master')
    git(repository, 'update-ref', 'refs/remotes/upstream/stable/26.7', advanced)
    git(repository, 'branch', '-D', plan['sync_branch'], plan['target_release'])
    github = remote_github(repository, {plan['source_release']: plan['source_commit'],
                                       plan['sync_branch']: fixture['sync_commit']})

    handled = module.recover_pending_reviews(repository, 'owner/plugins', 'reviewer', github, fixture['tools'])

    assert handled is False
    assert github.created_refs == github.pulls == []


def test_environment_review_publication_describes_retained_source_and_new_tools(tmp_path):
    fixture = bind_sync_repositories(tmp_path)
    repository, tools = fixture['repository'], fixture['tools']
    git(repository, 'update-ref', '-d', 'refs/remotes/upstream/stable/26.7')
    git(repository, 'update-ref', '-d', 'refs/remotes/upstream/stable/27.1')
    commit(tools, {'config/26.1/build.conf': 'OS?=14.4\n'}, 'tools environment update')
    git(tools, 'tag', '26.1.12')
    plan = seal_sync(discover_sync(fixture, PLANNER), tmp_path, PLANNER)
    result = apply_sync(fixture, plan, tmp_path, PLANNER)
    assert result.returncode == 0, result.stderr
    fixture.update(plan=plan, source_release=plan['source_commit'])
    github = publication_github(fixture)

    publish(publisher_module(), fixture, github)

    assert github.created_refs == [plan['sync_branch']]
    assert github.refs[plan['target_release']] == plan['source_commit']
    body = github.pulls[0]['body']
    assert 'BIND source unchanged; build environment changed' in body
    assert 'Tools: `26.1.11` -> `26.1.12`' in body
    assert 'FreeBSD: `14.3` -> `14.4`' in body


def test_github_reproduces_exact_local_objects_before_accepting_commit(tmp_path, monkeypatch):
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
        return {'sha': digest}
    client = module.GitHub()
    monkeypatch.setattr(client, 'call', api)
    client.publish_commit(repository, 'example/plugins', head)
    assert calls == [item[0] for item in expected]


@pytest.mark.parametrize('exact_matches', [0, 1])
def test_github_ref_lookup_requires_one_exact_branch(monkeypatch, exact_matches):
    module = publisher_module()
    branch = 'release/bind-rp/26.7'
    exact = {'ref': f'refs/heads/{branch}', 'object': {'sha': 'a' * 40}}
    response = [{'ref': f'refs/heads/{branch}-other', 'object': {'sha': 'b' * 40}}] + [exact] * exact_matches
    client = module.GitHub()
    call = Mock(return_value=response)
    monkeypatch.setattr(client, 'call', call)
    assert client.ref_sha('example/plugins', branch) == ('a' * 40 if exact_matches else None)
    call.assert_called_once_with('GET', f'repos/example/plugins/git/matching-refs/heads/{branch}')
