#!/usr/bin/env python3
"""Publish validated synchronization commits through GitHub's create-only APIs."""

import argparse
import base64
import json
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode


from shared.metadata_profile import validate_profile
from sync_upstream import (
    AUDIT_PATH, CALLER_PATH, METADATA_PATH, SYNC_PATTERN, THIN_CALLER,
    metadata_for, source_metadata, validate_frozen_inputs, validate_plan,
)


SERIES_PATTERN = re.compile(r'^\d+\.\d+$')
IDENTITY_PATTERN = re.compile(r'^(.*) <([^>]*)> ([0-9]+) ([+-])([0-9]{2})([0-9]{2})$')


def local_git(repository: Path, *arguments: str, binary: bool = False) -> str | bytes:
    result = subprocess.run(
        ['git', '-C', str(repository), *arguments],
        check=False,
        capture_output=True,
    )
    if result.returncode:
        raise ValueError(result.stderr.decode(errors='replace').strip() or 'Git command failed')
    return result.stdout if binary else result.stdout.decode().strip()


def local_commit(repository: Path, branch: str) -> str:
    commit = local_git(repository, 'rev-parse', f'refs/heads/{branch}^{{commit}}')
    if not isinstance(commit, str) or not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError(f'missing local branch: {branch}')
    return commit


def metadata_at(repository: Path, revision: str) -> dict[str, str]:
    try:
        metadata = json.loads(local_git(repository, 'show', f'{revision}:{METADATA_PATH}'))
    except (ValueError, json.JSONDecodeError):
        raise ValueError(f'missing synchronization metadata at {revision}') from None
    if not isinstance(metadata, dict):
        raise ValueError(f'missing synchronization metadata at {revision}')
    required = ('series', 'upstream_commit')
    if any(not isinstance(metadata.get(field), str) or not metadata[field] for field in required):
        raise ValueError(f'missing synchronization metadata at {revision}')
    if not valid_release_metadata(
        metadata,
        metadata['series'],
        metadata['upstream_commit'],
    ):
        raise ValueError(f'missing synchronization metadata at {revision}')
    return metadata


def valid_release_metadata(metadata: dict[str, str], series: str, upstream_commit: str) -> bool:
    try:
        profile = validate_profile(metadata, series)
    except ValueError:
        return False
    return profile['upstream_commit'] == upstream_commit


def audit_at(repository: Path, revision: str) -> dict:
    try:
        plan = json.loads(local_git(repository, 'show', f'{revision}:{AUDIT_PATH}'))
    except (ValueError, json.JSONDecodeError):
        raise ValueError(f'missing synchronization audit at {revision}') from None
    validate_plan(plan)
    return plan


def validate_generated_state(repository: Path, plan: dict, target_commit: str | None,
                             sync_commit: str | None) -> None:
    expected_metadata = metadata_for(plan)
    for commit in (target_commit, sync_commit):
        if commit and metadata_at(repository, commit) != expected_metadata:
            raise ValueError('generated metadata does not match sealed publication plan')
    if target_commit and plan['action'] == 'bootstrap-review':
        if local_git(repository, 'rev-list', '--parents', '-n', '1', target_commit).split() != [target_commit, plan['upstream_commit']]:
            raise ValueError('bootstrap release does not parent the selected upstream commit')
        changed = local_git(repository, 'diff-tree', '--no-commit-id', '--name-only', '-r',
                            plan['upstream_commit'], target_commit).splitlines()
        if changed != [METADATA_PATH]:
            raise ValueError('bootstrap base is not pristine')
    if sync_commit is None:
        return
    if audit_at(repository, sync_commit) != plan:
        raise ValueError('generated recovery audit does not match sealed publication plan')
    caller = local_git(repository, 'show', f'{sync_commit}:{CALLER_PATH}', binary=True)
    if caller != THIN_CALLER.encode():
        raise ValueError('generated review caller does not match the trusted template')
    environment_only = plan['action'] == 'update-review' and not plan['bind_changed']
    parent = (target_commit if plan['action'] == 'bootstrap-review'
              else plan['source_commit'] if environment_only else plan['upstream_commit'])
    if local_git(repository, 'rev-list', '--parents', '-n', '1', sync_commit).split() != [sync_commit, parent]:
        raise ValueError('review commit parent does not match sealed publication plan')
    changed = local_git(repository, 'diff-tree', '--no-commit-id', '--name-only', '-r',
                        parent, sync_commit).splitlines()
    generated = {METADATA_PATH, AUDIT_PATH, CALLER_PATH}
    for path in changed:
        overlay = (path == 'Mk/plugins.mk' or path.startswith('dns/bind/'))
        tests = path == 'dns/bind/tests' or path.startswith('dns/bind/tests/')
        if path not in generated and (environment_only or not overlay or tests):
            raise ValueError('review includes changes outside the bounded fork overlay')


def api_date(identity: str) -> dict[str, str]:
    match = IDENTITY_PATTERN.fullmatch(identity)
    if not match:
        raise ValueError('unsupported local commit identity')
    name, email, timestamp, sign, hours, minutes = match.groups()
    offset = timedelta(hours=int(hours), minutes=int(minutes))
    if sign == '-':
        offset = -offset
    date = datetime.fromtimestamp(int(timestamp), timezone(offset)).isoformat(timespec='seconds')
    return {'name': name, 'email': email, 'date': date}


def commit_data(repository: Path, commit_sha: str) -> dict[str, Any]:
    raw_bytes = local_git(repository, 'cat-file', 'commit', commit_sha, binary=True)
    if not isinstance(raw_bytes, bytes):
        raise ValueError('cannot inspect local commit')
    raw = raw_bytes.decode()
    headers, separator, message = raw.partition('\n\n')
    if not separator:
        raise ValueError('cannot inspect local commit')
    values: dict[str, list[str]] = {}
    for line in headers.splitlines():
        key, _, value = line.partition(' ')
        values.setdefault(key, []).append(value)
    if len(values.get('tree', [])) != 1 or len(values.get('parent', [])) != 1:
        raise ValueError('synchronization commits must have exactly one parent')
    if len(values.get('author', [])) != 1 or len(values.get('committer', [])) != 1:
        raise ValueError('cannot inspect local commit identity')
    return {
        'tree': values['tree'][0],
        'parent_tree': local_git(repository, 'rev-parse', f"{values['parent'][0]}^{{tree}}"),
        'parent': values['parent'][0],
        'author': api_date(values['author'][0]),
        'committer': api_date(values['committer'][0]),
        'message': message,
    }


class GitHub:
    """Small create-only GitHub API client backed by the authenticated gh CLI."""

    def call(
        self,
        method: str,
        endpoint: str,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        command = ['gh', 'api', '--method', method, endpoint]
        input_data = None
        if payload is not None:
            command.extend(['--input', '-'])
            input_data = json.dumps(payload).encode()
        result = subprocess.run(command, input=input_data, capture_output=True, check=False)
        if result.returncode:
            message = result.stderr.decode(errors='replace').strip()
            raise ValueError(message or f'GitHub API {method} {endpoint} failed')
        if not result.stdout.strip():
            return None
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            raise ValueError('GitHub API returned invalid JSON') from None

    def check_assignee(self, repository: str, reviewer: str) -> None:
        self.call('GET', f'repos/{repository}/assignees/{quote(reviewer, safe="")}')

    def publish_commit(self, local_repository: Path, repository: str, commit_sha: str) -> None:
        data = commit_data(local_repository, commit_sha)
        changed = local_git(
            local_repository,
            'diff-tree', '--no-commit-id', '--name-status', '-r', '-z', '--no-renames',
            data['parent'], commit_sha,
            binary=True,
        )
        if not isinstance(changed, bytes):
            raise ValueError('cannot inspect local commit changes')
        fields = changed.split(b'\0')
        if fields and fields[-1] == b'':
            fields.pop()
        if len(fields) % 2:
            raise ValueError('cannot inspect local commit changes')
        entries = []
        for index in range(0, len(fields), 2):
            status = fields[index].decode()
            path = fields[index + 1].decode()
            if status == 'D':
                entries.append({'path': path, 'sha': None})
                continue
            if status not in {'A', 'M', 'T'}:
                raise ValueError(f'unsupported local tree change: {status}')
            tree_line = local_git(local_repository, 'ls-tree', '-z', commit_sha, '--', path, binary=True)
            if (not isinstance(tree_line, bytes) or not tree_line.endswith(b'\0')
                    or b'\0' in tree_line[:-1] or b'\t' not in tree_line):
                raise ValueError(f'cannot inspect local tree entry: {path}')
            attributes, listed_path = tree_line[:-1].decode().split('\t', maxsplit=1)
            mode, object_type, object_sha = attributes.split()
            if listed_path != path:
                raise ValueError(f'cannot inspect local tree entry: {path}')
            if object_type == 'blob':
                contents = local_git(local_repository, 'cat-file', 'blob', object_sha, binary=True)
                if not isinstance(contents, bytes):
                    raise ValueError(f'cannot read local blob: {path}')
                created = self.call(
                    'POST',
                    f'repos/{repository}/git/blobs',
                    {
                        'content': base64.b64encode(contents).decode(),
                        'encoding': 'base64',
                    },
                )
                if not isinstance(created, dict) or created.get('sha') != object_sha:
                    raise ValueError(f'GitHub did not reproduce local blob: {path}')
            elif object_type != 'commit':
                raise ValueError(f'unsupported local tree object: {object_type}')
            entries.append(
                {'path': path, 'mode': mode, 'type': object_type, 'sha': object_sha}
            )
        created_tree = self.call(
            'POST',
            f'repos/{repository}/git/trees',
            {'base_tree': data['parent_tree'], 'tree': entries},
        )
        if not isinstance(created_tree, dict):
            raise ValueError('GitHub did not create the local tree')
        if created_tree.get('sha') != data['tree']:
            raise ValueError('GitHub did not reproduce the local tree')
        created_commit = self.call(
            'POST',
            f'repos/{repository}/git/commits',
            {
                'message': data['message'],
                'tree': data['tree'],
                'parents': [data['parent']],
                'author': data['author'],
                'committer': data['committer'],
            },
        )
        if not isinstance(created_commit, dict) or created_commit.get('sha') != commit_sha:
            raise ValueError('GitHub did not reproduce the local commit')

    def ref_sha(self, repository: str, branch: str) -> str | None:
        encoded_branch = quote(branch, safe='/')
        matches = self.call(
            'GET', f'repos/{repository}/git/matching-refs/heads/{encoded_branch}'
        )
        if not isinstance(matches, list):
            raise ValueError('GitHub returned invalid reference data')
        exact = [
            item for item in matches
            if isinstance(item, dict) and item.get('ref') == f'refs/heads/{branch}'
        ]
        if not exact:
            return None
        if len(exact) != 1 or not isinstance(exact[0].get('object'), dict):
            raise ValueError(f'GitHub returned ambiguous reference data for {branch}')
        sha = exact[0]['object'].get('sha')
        if not isinstance(sha, str):
            raise ValueError(f'GitHub returned invalid reference data for {branch}')
        return sha

    def create_ref(self, repository: str, branch: str, commit_sha: str) -> None:
        try:
            self.call(
                'POST',
                f'repos/{repository}/git/refs',
                {'ref': f'refs/heads/{branch}', 'sha': commit_sha},
            )
        except ValueError:
            if self.ref_sha(repository, branch) == commit_sha:
                return
            raise

    def pulls_for(self, repository: str, head: str, base: str) -> list[dict[str, Any]]:
        owner = repository.split('/', maxsplit=1)[0]
        query = urlencode({'state': 'all', 'head': f'{owner}:{head}', 'base': base})
        pulls = self.call('GET', f'repos/{repository}/pulls?{query}')
        if not isinstance(pulls, list):
            raise ValueError('GitHub returned invalid pull request data')
        normalized = []
        for pull in pulls:
            try:
                normalized.append(
                    {
                        'number': pull['number'],
                        'head': pull['head']['ref'],
                        'base': pull['base']['ref'],
                        'state': pull['state'],
                        'assignees': [assignee['login'] for assignee in pull['assignees']],
                    }
                )
            except (KeyError, TypeError):
                raise ValueError('GitHub returned invalid pull request data') from None
        return normalized

    def create_pull(
        self, repository: str, head: str, base: str, title: str, body: str
    ) -> dict[str, Any]:
        pull = self.call(
            'POST',
            f'repos/{repository}/pulls',
            {'head': head, 'base': base, 'title': title, 'body': body},
        )
        if not isinstance(pull, dict) or not isinstance(pull.get('number'), int):
            raise ValueError('GitHub returned invalid pull request data')
        return {
            'number': pull['number'],
            'head': head,
            'base': base,
            'state': pull.get('state', 'open'),
            'assignees': [item['login'] for item in pull.get('assignees', [])],
        }

    def assign_pull(self, repository: str, number: int, reviewer: str) -> None:
        self.call(
            'POST',
            f'repos/{repository}/issues/{number}/assignees',
            {'assignees': [reviewer]},
        )


def ensure_ref(
    github: GitHub,
    repository: str,
    branch: str,
    expected_commit: str,
) -> None:
    current = github.ref_sha(repository, branch)
    if current == expected_commit:
        return
    if current is not None:
        raise ValueError(f'refusing to replace {branch}: it points to a different commit')
    github.create_ref(repository, branch, expected_commit)
    if github.ref_sha(repository, branch) != expected_commit:
        raise ValueError(f'GitHub did not create the exact reference {branch}')


def ensure_existing_ref(
    github: GitHub,
    repository: str,
    branch: str,
    expected_commit: str,
) -> None:
    if github.ref_sha(repository, branch) != expected_commit:
        raise ValueError(f'remote reference {branch} does not match the fetched commit')


def review_text(plan: dict, source_profile: dict) -> tuple[str, str]:
    series = plan['series']
    comparison = ('https://github.com/opnsense/plugins/compare/'
                  f"{source_profile['upstream_commit']}...{plan['upstream_commit']}")
    source = 'BIND source changed' if plan['bind_changed'] else 'BIND source unchanged'
    environment = ('Build environment changed' if plan['environment_changed']
                   else 'Build environment unchanged')
    body = (f'{source}; {environment.lower()}. Review before merging.\n\n'
            f"Tools: `{source_profile['tools_tag']}` -> `{plan['tools_tag']}`; "
            f"FreeBSD: `{source_profile['freebsd_release']}` -> `{plan['freebsd_release']}`.\n"
            f"Pinned core: `{plan['core_commit']}`.\n"
            f'Upstream comparison: {comparison}')
    return f'Review upstream BIND synchronization for {series}', body


def ensure_pull(
    github: GitHub,
    repository: str,
    head: str,
    base: str,
    reviewer: str,
    title: str,
    body: str,
) -> dict[str, Any]:
    pulls = github.pulls_for(repository, head, base)
    pull = next((item for item in pulls if item.get('state') == 'open'), None)
    if pull is None:
        pull = github.create_pull(repository, head, base, title, body)
    if reviewer not in pull.get('assignees', []):
        github.assign_pull(repository, pull['number'], reviewer)
    return pull


def publish_plan(
    local_repository: Path,
    plan: dict[str, Any],
    github_repository: str,
    reviewer: str,
    github: GitHub,
    tools_repository: Path,
) -> None:
    action, _, target, sync = validate_plan(plan)
    source_profile = validate_frozen_inputs(local_repository, plan, tools_repository)
    target_commit = local_commit(local_repository, target) if action.startswith('bootstrap') else None
    sync_commit = local_commit(local_repository, sync) if sync else None
    validate_generated_state(local_repository, plan, target_commit, sync_commit)
    ensure_existing_ref(github, github_repository, plan['source_release'], plan['source_commit'])
    for branch, commit in ((target, target_commit), (sync, sync_commit)):
        if branch and commit:
            existing = github.ref_sha(github_repository, branch)
            if existing is not None and existing != commit:
                raise ValueError(f'refusing to replace {branch}: it points to a different commit')
    if sync:
        if not reviewer:
            raise ValueError('RP_SYNC_REVIEWER is required for review publication')
        github.check_assignee(github_repository, reviewer)
    if target_commit:
        github.publish_commit(local_repository, github_repository, target_commit)
    if sync_commit:
        github.publish_commit(local_repository, github_repository, sync_commit)
    if sync and sync_commit:
        ensure_ref(github, github_repository, sync, sync_commit)
    if target_commit:
        ensure_ref(github, github_repository, target, target_commit)
    if sync:
        title, body = review_text(plan, source_profile)
        ensure_pull(github, github_repository, sync, target, reviewer, title, body)


def recovery_candidates(repository: Path, tools_repository: Path) -> list[dict[str, Any]]:
    output = local_git(repository, 'for-each-ref', '--format=%(refname:strip=3) %(objectname)',
                       'refs/remotes/origin/sync')
    candidates = []
    for line in output.splitlines():
        branch, sync_commit = line.split(maxsplit=1)
        if SYNC_PATTERN.fullmatch(branch) is None:
            continue
        try:
            plan = audit_at(repository, sync_commit)
            if plan['sync_branch'] != branch:
                raise ValueError('proposal ref does not match its audit')
            source_profile = validate_frozen_inputs(repository, plan, tools_repository,
                                                    require_control_head=False)
        except ValueError:
            continue
        target = plan['target_release']
        try:
            target_commit = local_git(repository, 'rev-parse', f'refs/remotes/origin/{target}^{{commit}}')
            base_missing = False
        except ValueError:
            if plan['action'] != 'bootstrap-review':
                continue
            try:
                target_commit = local_git(repository, 'rev-parse', f'{sync_commit}^')
                base_missing = True
            except ValueError:
                continue
        try:
            validate_generated_state(repository, plan,
                                     target_commit if plan['action'] == 'bootstrap-review' else None,
                                     sync_commit)
            if plan['action'] == 'update-review' and target_commit != plan['source_commit']:
                continue
        except ValueError:
            continue
        candidates.append(dict(plan=plan, source_profile=source_profile, head=branch,
                               head_commit=sync_commit, base=target,
                               base_commit=target_commit, base_missing=base_missing))
    return candidates


def recover_pending_reviews(local_repository: Path, github_repository: str, reviewer: str,
                            github: GitHub, tools_repository: Path) -> bool:
    pending = []
    for candidate in recovery_candidates(local_repository, tools_repository):
        pulls = [] if candidate['base_missing'] else github.pulls_for(
            github_repository, candidate['head'], candidate['base'])
        if any(pull.get('state') == 'open' for pull in pulls) or not pulls:
            pending.append(candidate)
    if not pending:
        return False
    if not reviewer:
        raise ValueError('RP_SYNC_REVIEWER is required for review recovery')
    github.check_assignee(github_repository, reviewer)
    for candidate in pending:
        plan = candidate['plan']
        ensure_existing_ref(github, github_repository, plan['source_release'], plan['source_commit'])
        ensure_existing_ref(github, github_repository, candidate['head'], candidate['head_commit'])
        if candidate['base_missing']:
            ensure_ref(github, github_repository, candidate['base'], candidate['base_commit'])
        else:
            ensure_existing_ref(github, github_repository, candidate['base'], candidate['base_commit'])
        title, body = review_text(plan, candidate['source_profile'])
        ensure_pull(github, github_repository, candidate['head'], candidate['base'],
                    reviewer, title, body)
    return True


def read_plan(path: str) -> dict[str, Any]:
    try:
        plan = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        raise ValueError('missing or invalid publication plan') from None
    if not isinstance(plan, dict):
        raise ValueError('missing or invalid publication plan')
    return plan


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest='command', required=True)
    publish_parser = commands.add_parser('publish')
    publish_parser.add_argument('--repository', required=True)
    publish_parser.add_argument('--plan', required=True)
    publish_parser.add_argument('--tools-repository', required=True)
    publish_parser.add_argument('--github-repository', required=True)
    publish_parser.add_argument('--reviewer', default='')
    recover_parser = commands.add_parser('recover')
    recover_parser.add_argument('--repository', required=True)
    recover_parser.add_argument('--tools-repository', required=True)
    recover_parser.add_argument('--github-repository', required=True)
    recover_parser.add_argument('--reviewer', default='')
    recover_parser.add_argument('--output', required=True)
    arguments = parser.parse_args()
    github = GitHub()
    try:
        if arguments.command == 'publish':
            publish_plan(
                Path(arguments.repository),
                read_plan(arguments.plan),
                arguments.github_repository,
                arguments.reviewer,
                github,
                Path(arguments.tools_repository),
            )
        else:
            handled = recover_pending_reviews(
                Path(arguments.repository),
                arguments.github_repository,
                arguments.reviewer,
                github,
                Path(arguments.tools_repository),
            )
            with Path(arguments.output).open('a', encoding='utf-8') as output:
                print(f'handled={str(handled).lower()}', file=output)
    except ValueError as error:
        parser.error(str(error))


if __name__ == '__main__':
    main()
