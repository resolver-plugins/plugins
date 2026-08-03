import importlib.util
import json
import pathlib
import subprocess

import pytest


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[3]
PUBLISHER = REPOSITORY_ROOT / 'tools/ci/publish_upstream.py'
CORE_COMMIT = '8cc69b21e0f4c2622fc8a62df2a15ba7cb1e731f'


def git(repository: pathlib.Path, *arguments: str) -> str:
    return subprocess.run(
        ['git', '-C', repository, *arguments],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()


def commit(repository: pathlib.Path, files: dict[str, str], message: str) -> str:
    for name, contents in files.items():
        destination = repository / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(contents, encoding='utf-8')
    git(repository, 'add', *files)
    git(repository, 'commit', '-m', message)
    return git(repository, 'rev-parse', 'HEAD')


def metadata(series: str, upstream_commit: str) -> str:
    return json.dumps(
        {
            'series': series,
            'upstream_branch': f'stable/{series}',
            'upstream_commit': upstream_commit,
            'freebsd_release': '15.1',
            'core_commit': CORE_COMMIT,
            'core_archive_url': (
                f'https://github.com/opnsense/core/archive/{CORE_COMMIT}.tar.gz'
            ),
            'core_archive_sha256': 'fixture-sha256',
        }
    )


def publisher_module():
    assert PUBLISHER.is_file(), 'GitHub API publisher is missing'
    spec = importlib.util.spec_from_file_location('publish_upstream', PUBLISHER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def publication_repository(tmp_path):
    repository = tmp_path / 'repository'
    git(tmp_path, 'init', repository)
    git(repository, 'config', 'user.name', 'Publisher tests')
    git(repository, 'config', 'user.email', 'publisher@example.invalid')
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


def test_bootstrap_review_creates_sync_ref_before_target_and_assigns_pr(
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
    assert 'https://github.com/opnsense/plugins/compare/' in github.pulls[0]['body']


def test_retry_accepts_exact_ref_and_creates_missing_pr(publication_repository):
    module = publisher_module()
    plan = publication_repository['plan']
    github = FakeGitHub(
        refs={
            plan['sync_branch']: publication_repository['sync_commit'],
            plan['target_release']: publication_repository['target_commit'],
        }
    )

    module.publish_plan(
        publication_repository['repository'], plan, 'owner/plugins', 'reviewer', github
    )

    assert github.created_refs == []
    assert len(github.pulls) == 1
    assert github.pulls[0]['assignees'] == ['reviewer']


def test_retry_assigns_existing_open_pr_without_creating_a_duplicate(
    publication_repository,
):
    module = publisher_module()
    plan = publication_repository['plan']
    existing_pull = {
        'number': 41,
        'head': plan['sync_branch'],
        'base': plan['target_release'],
        'state': 'open',
        'assignees': [],
    }
    github = FakeGitHub(
        refs={
            plan['sync_branch']: publication_repository['sync_commit'],
            plan['target_release']: publication_repository['target_commit'],
        },
        pulls=[existing_pull],
    )

    module.publish_plan(
        publication_repository['repository'], plan, 'owner/plugins', 'reviewer', github
    )

    assert github.pulls == [existing_pull]
    assert existing_pull['assignees'] == ['reviewer']


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


def test_recovery_creates_missing_pr_before_planning_again(publication_repository):
    module = publisher_module()
    repository = publication_repository['repository']
    plan = publication_repository['plan']
    git(
        repository,
        'update-ref',
        f"refs/remotes/origin/{plan['target_release']}",
        publication_repository['target_commit'],
    )
    git(
        repository,
        'update-ref',
        f"refs/remotes/origin/{plan['sync_branch']}",
        publication_repository['sync_commit'],
    )
    github = FakeGitHub(
        refs={
            plan['sync_branch']: publication_repository['sync_commit'],
            plan['target_release']: publication_repository['target_commit'],
        }
    )

    handled = module.recover_pending_reviews(
        repository, 'owner/plugins', 'reviewer', github
    )

    assert handled is True
    assert len(github.pulls) == 1
    assert github.pulls[0]['head'] == plan['sync_branch']
    assert github.pulls[0]['base'] == plan['target_release']
    assert github.pulls[0]['assignees'] == ['reviewer']
