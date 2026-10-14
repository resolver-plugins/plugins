#!/usr/bin/env python3
"""Inspect fetched OPNsense refs and plan a safe BIND synchronization."""

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from shared.metadata_profile import (
    COMMIT_PATTERN,
    FREEBSD_RELEASE_PATTERN,
    verify_tools_profile,
    tools_tag_matches_series,
    validate_core_archive,
    validate_profile,
)
from shared.upstream_inputs import source_baseline, tools_release_profile


SERIES_PATTERN = re.compile(r'^stable/(\d+)\.(\d+)$')
RELEASE_PATTERN = re.compile(r'^(\d+)\.(\d+)$')
AUDIT_TRAILER = 'Resolver-Sync-Plan: '
CALLER_PATH = '.github/workflows/bind-tests.yml'
OVERLAY_PATHS = ('dns/bind', 'Mk/plugins.mk', ':(exclude)dns/bind/tests')
DISCOVERY_FIELDS = {
    'action', 'series', 'upstream_ref', 'upstream_commit', 'source_release',
    'target_release', 'tools_tag', 'freebsd_release', 'bind_changed', 'reason',
    'control_commit', 'source_commit', 'upstream_head', 'tools_commit',
    'source_upstream_commit',
}
PLAN_FIELDS = DISCOVERY_FIELDS | {
    'schema', 'sync_branch', 'core_commit', 'core_archive_url', 'core_archive_sha256',
}
SYNC_PATTERN = re.compile(r'^sync/(bootstrap|bind)/(\d+\.\d+)/([0-9a-f]{12})-([0-9a-f]{12})$')
THIN_CALLER = """name: Test BIND release source

on:
  pull_request:
    paths:
      - 'dns/bind/**'
      - 'Mk/plugins.mk'
      - '.github/workflows/bind-tests.yml'

permissions:
  contents: read

jobs:
  test:
    permissions:
      contents: read
    uses: resolver-plugins/plugins/.github/workflows/bind-tests.yml@master
    with:
      pull_request_base: ${{ github.event.pull_request.base.ref }}
      pull_request_sha: ${{ github.event.pull_request.head.sha }}
"""


def run_git(repository: Path, *arguments: str) -> str:
    """Run a read-only Git command and return its stripped standard output."""
    return subprocess.run(
        ['git', '-C', str(repository), *arguments],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()


def series_key(series: str) -> tuple[int, int]:
    match = RELEASE_PATTERN.fullmatch(series)
    if not match:
        raise ValueError(f'invalid series: {series}')
    return int(match.group(1)), int(match.group(2))


def stable_refs(repository: Path, upstream: str) -> dict[str, str]:
    refs = run_git(
        repository,
        'for-each-ref',
        '--format=%(refname:short) %(objectname)',
        f'refs/remotes/{upstream}',
    )
    result = {}
    for line in refs.splitlines():
        reference, commit = line.split(maxsplit=1)
        prefix = f'{upstream}/'
        if not reference.startswith(prefix):
            continue
        upstream_branch = reference[len(prefix):]
        match = SERIES_PATTERN.fullmatch(upstream_branch)
        if match and COMMIT_PATTERN.fullmatch(commit):
            result[f'{match.group(1)}.{match.group(2)}'] = commit
    return result


def release_refs(repository: Path, release_prefix: str) -> dict[str, str]:
    refs = run_git(
        repository,
        'for-each-ref',
        '--format=%(refname:short) %(objectname)',
        'refs/heads',
    )
    releases = {}
    for line in refs.splitlines():
        reference, commit = line.split(maxsplit=1)
        if not reference.startswith(release_prefix):
            continue
        series = reference[len(release_prefix):]
        if RELEASE_PATTERN.fullmatch(series):
            releases[series] = commit
    return releases


def bind_tree(repository: Path, revision: str) -> str:
    return run_git(repository, 'rev-parse', f'{revision}:dns/bind')


def decision(action: str, reason: str, **values) -> dict:
    result = dict.fromkeys(DISCOVERY_FIELDS)
    result.update(action=action, reason=reason, bind_changed=False)
    result.update(values)
    return result


def discover(arguments: argparse.Namespace) -> dict:
    repository = Path(arguments.repository)
    control_commit = arguments.control_commit
    if (COMMIT_PATTERN.fullmatch(control_commit) is None
            or run_git(repository, 'rev-parse', 'HEAD') != control_commit):
        raise ValueError('discovery control commit does not match checkout HEAD')
    stable = stable_refs(repository, arguments.upstream)
    releases = release_refs(repository, arguments.release_prefix)
    available = sorted(stable, key=series_key)
    if not available or not releases:
        return decision('blocked', 'no release source', control_commit=control_commit)

    latest_release = max(releases, key=series_key)
    source_release = f'{arguments.release_prefix}{latest_release}'
    source_commit = releases[latest_release]
    observed_head = stable.get(latest_release)
    values = dict(control_commit=control_commit, source_commit=source_commit,
                  source_release=source_release, target_release=source_release,
                  series=latest_release, upstream_head=observed_head,
                  upstream_ref=f'{arguments.upstream}/stable/{latest_release}')
    try:
        baseline = source_baseline(repository, source_commit,
                                   f'{arguments.upstream}/stable/{latest_release}')
        source_bind_tree = bind_tree(repository, baseline)
        tools_tag, tools_commit, freebsd_release = tools_release_profile(
            Path(arguments.tools_repository), latest_release)
        if observed_head is None:
            raise ValueError('missing upstream release ref')
        current_bind_tree = bind_tree(repository, observed_head)
    except (ValueError, subprocess.CalledProcessError):
        return decision('blocked', 'missing source ancestry or upstream tools release', **values)
    values.update(upstream_commit=baseline, source_upstream_commit=baseline, tools_tag=tools_tag,
                  tools_commit=tools_commit, freebsd_release=freebsd_release)
    if source_bind_tree != current_bind_tree:
        values.update(upstream_commit=observed_head, bind_changed=True)
        return decision('update-review', 'upstream BIND tree changed', **values)

    target_series = next((series for series in available
                          if series_key(series) > series_key(latest_release)), None)
    if target_series is None:
        return decision('noop', 'upstream BIND tree is unchanged; build inputs are discovered per run', **values)
    values.update(series=target_series, target_release=f'{arguments.release_prefix}{target_series}',
                  upstream_ref=f'{arguments.upstream}/stable/{target_series}',
                  upstream_head=stable[target_series], upstream_commit=stable[target_series])
    try:
        tools_tag, tools_commit, freebsd_release = tools_release_profile(
            Path(arguments.tools_repository), target_series)
        bind_changed = source_bind_tree != bind_tree(repository, stable[target_series])
    except (ValueError, subprocess.CalledProcessError):
        return decision('blocked', 'missing or invalid target release profile', **values)
    values.update(tools_tag=tools_tag, tools_commit=tools_commit, freebsd_release=freebsd_release,
                  bind_changed=bind_changed)
    reason = ('new series has an upstream BIND change' if bind_changed
              else 'new series has an unchanged BIND tree')
    return decision('bootstrap-review', reason, **values)


def sync_branch_for(plan_data: dict[str, Any]) -> str | None:
    action = plan_data['action']
    if action not in {'update-review', 'bootstrap-review'}:
        return None
    kind = 'bind' if action == 'update-review' else 'bootstrap'
    inputs = {key: value for key, value in plan_data.items() if key != 'sync_branch'}
    input_id = hashlib.sha256(json.dumps(inputs, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return f"sync/{kind}/{plan_data['series']}/{plan_data['upstream_commit'][:12]}-{input_id[:12]}"


def validate_plan(plan_data: dict[str, Any]) -> tuple[str, str, str, str | None]:
    if not isinstance(plan_data, dict) or set(plan_data) != PLAN_FIELDS or plan_data['schema'] != 3:
        raise ValueError('missing or invalid sealed plan')
    action = plan_data['action']
    series = plan_data['series']
    if (action not in {'bootstrap-build', 'bootstrap-review', 'update-review'}
            or not isinstance(series, str) or RELEASE_PATTERN.fullmatch(series) is None):
        raise ValueError('missing or invalid sealed plan')
    for field in ('control_commit', 'source_commit', 'upstream_head', 'upstream_commit', 'tools_commit',
                  'source_upstream_commit'):
        if not isinstance(plan_data[field], str) or COMMIT_PATTERN.fullmatch(plan_data[field]) is None:
            raise ValueError('missing or invalid sealed plan')
    if (plan_data['upstream_ref'] != f'upstream/stable/{series}'
            or plan_data['target_release'] != f'release/bind-rp/{series}'
            or not isinstance(plan_data['source_release'], str)
            or re.fullmatch(r'release/bind-rp/\d+\.\d+', plan_data['source_release']) is None
            or not isinstance(plan_data['reason'], str) or not plan_data['reason']
            or not isinstance(plan_data['bind_changed'], bool)
            or not isinstance(plan_data['tools_tag'], str)
            or not tools_tag_matches_series(plan_data['tools_tag'], series)
            or not isinstance(plan_data['freebsd_release'], str)
            or FREEBSD_RELEASE_PATTERN.fullmatch(plan_data['freebsd_release']) is None):
        raise ValueError('missing or invalid sealed plan')
    if action == 'update-review':
        if (plan_data['source_release'] != plan_data['target_release']
                or not plan_data['bind_changed']):
            raise ValueError('missing or invalid sealed plan')
    elif series_key(plan_data['source_release'].rsplit('/', 1)[-1]) >= series_key(series):
        raise ValueError('missing or invalid sealed plan')
    if plan_data['bind_changed'] and plan_data['upstream_commit'] != plan_data['upstream_head']:
        raise ValueError('missing or invalid sealed plan')
    validate_core_archive(plan_data['core_commit'], plan_data['core_archive_url'],
                          plan_data['core_archive_sha256'])
    if plan_data['sync_branch'] != sync_branch_for(plan_data):
        raise ValueError('sealed plan ref does not match its input identity')
    return action, series, plan_data['target_release'], plan_data['sync_branch']


def seal_plan(discovery: dict, core_commit: str, core_archive_url: str,
              core_archive_sha256: str) -> dict:
    if not isinstance(discovery, dict) or set(discovery) != DISCOVERY_FIELDS:
        raise ValueError('missing or invalid discovery')
    plan_data = dict(discovery, schema=3, core_commit=core_commit,
                     core_archive_url=core_archive_url, core_archive_sha256=core_archive_sha256)
    plan_data['sync_branch'] = sync_branch_for(plan_data)
    validate_plan(plan_data)
    return plan_data


def git_result(
    repository: Path,
    *arguments: str,
    input_data: bytes | None = None,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Run Git, keeping binary patch input intact when provided."""
    return subprocess.run(
        ['git', '-C', str(repository), *arguments],
        input=input_data,
        capture_output=True,
        check=False,
        env=environment,
    )


def require_git(
    repository: Path,
    *arguments: str,
    input_data: bytes | None = None,
    environment: dict[str, str] | None = None,
) -> str:
    result = git_result(
        repository,
        *arguments,
        input_data=input_data,
        environment=environment,
    )
    if result.returncode:
        raise ValueError(result.stderr.decode(errors='replace').strip() or 'Git command failed')
    return result.stdout.decode().strip()


def ref_exists(repository: Path, branch: str) -> bool:
    result = git_result(repository, 'show-ref', '--verify', '--quiet', f'refs/heads/{branch}')
    if result.returncode not in (0, 1):
        raise ValueError('unable to inspect local branches')
    return result.returncode == 0


def read_apply_plan(plan_path: str) -> dict[str, Any]:
    try:
        plan_data = json.loads(Path(plan_path).read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        raise ValueError('missing or invalid sealed plan') from None
    validate_plan(plan_data)
    return plan_data


def metadata_for(plan_data: dict[str, Any]) -> dict[str, str]:
    profile = {field: plan_data[field] for field in
               ('series', 'upstream_commit', 'tools_tag', 'freebsd_release', 'core_commit',
                'core_archive_url', 'core_archive_sha256')}
    profile['upstream_branch'] = f"stable/{plan_data['series']}"
    return validate_profile(profile, plan_data['series'])


def validate_frozen_inputs(repository: Path, plan_data: dict[str, Any],
                           tools_repository: Path, *, require_control_head: bool = True) -> dict:
    validate_plan(plan_data)
    if require_control_head and require_git(repository, 'rev-parse', 'HEAD') != plan_data['control_commit']:
        raise ValueError('control checkout does not match sealed plan')
    require_git(repository, 'cat-file', '-e', f"{plan_data['control_commit']}^{{commit}}")
    if require_git(repository, 'rev-parse', f"refs/heads/{plan_data['source_release']}^{{commit}}") != plan_data['source_commit']:
        raise ValueError('source release ref does not match sealed plan')
    if require_git(repository, 'rev-parse', f"{plan_data['upstream_ref']}^{{commit}}") != plan_data['upstream_head']:
        raise ValueError('upstream ref does not match sealed plan')
    source_series = plan_data['source_release'].rsplit('/', 1)[-1]
    baseline = source_baseline(repository, plan_data['source_commit'], f'upstream/stable/{source_series}')
    if baseline != plan_data['source_upstream_commit']:
        raise ValueError('source upstream ancestry does not match sealed plan')
    source_tree = bind_tree(repository, baseline)
    observed_tree = bind_tree(repository, plan_data['upstream_head'])
    if (source_tree != observed_tree) != plan_data['bind_changed']:
        raise ValueError('BIND tree decision does not match sealed inputs')
    expected_baseline = (plan_data['upstream_head']
                         if plan_data['bind_changed'] or plan_data['action'].startswith('bootstrap')
                         else baseline)
    if plan_data['upstream_commit'] != expected_baseline:
        raise ValueError('upstream baseline does not match sealed inputs')
    proof = verify_tools_profile(metadata_for(plan_data), tools_repository)
    if proof['tools_commit'] != plan_data['tools_commit']:
        raise ValueError('tools tag does not match sealed plan commit')
    return {'upstream_commit': baseline}


def commit_clone(clone: Path, paths: list[str], message: str,
                 plan_data: dict | None = None) -> None:
    conflicts = unmerged_paths(clone)
    if conflicts:
        raise ValueError(f'overlay patch conflicts: {", ".join(conflicts)}')
    generated = []
    if plan_data is not None:
        caller_path = clone / CALLER_PATH
        caller_path.parent.mkdir(parents=True, exist_ok=True)
        caller_path.write_text(THIN_CALLER, encoding='utf-8')
        generated.append(CALLER_PATH)
        message += '\n\n' + AUDIT_TRAILER + json.dumps(plan_data, sort_keys=True, separators=(',', ':'))
    if generated or paths:
        require_git(clone, 'add', '--all', '--', *generated, *paths)
    parent_timestamp = require_git(clone, 'show', '-s', '--format=%ct', 'HEAD')
    environment = os.environ.copy()
    environment.update(GIT_AUTHOR_DATE=f'@{parent_timestamp} +0000',
                       GIT_COMMITTER_DATE=f'@{parent_timestamp} +0000')
    require_git(clone, 'commit', '--allow-empty', '-m', message, environment=environment)


def with_clone(repository: Path, revision: str, callback) -> Any:
    with tempfile.TemporaryDirectory(prefix='sync-upstream-') as directory:
        clone = Path(directory) / 'repository'
        subprocess.run(['git', 'clone', '--quiet', '--no-checkout', '--no-hardlinks',
                        str(repository.resolve()), str(clone)], check=True, capture_output=True)
        for field in ('user.name', 'user.email'):
            require_git(clone, 'config', field, require_git(repository, 'config', field))
        require_git(clone, 'checkout', '--detach', revision)
        commits = callback(clone)
        exported = commits if isinstance(commits, tuple) else (commits,)
        for commit in exported:
            require_git(repository, 'fetch', '--no-tags', '--no-write-fetch-head', str(clone), commit)
        return commits


def unmerged_paths(worktree: Path) -> list[str]:
    result = git_result(worktree, 'diff', '--name-only', '--diff-filter=U', '-z')
    if result.returncode:
        raise ValueError('unable to inspect overlay conflicts')
    return [os.fsdecode(path) for path in result.stdout.split(b'\0') if path]


def apply_overlay(worktree: Path, patch: bytes) -> None:
    result = git_result(worktree, 'apply', '--3way', '--binary', input_data=patch)
    conflicts = unmerged_paths(worktree)
    if conflicts:
        raise ValueError(f'overlay patch conflicts: {", ".join(conflicts)}')
    if result.returncode:
        raise ValueError('overlay patch does not apply')
    require_git(worktree, 'reset')


def create_branches(repository: Path, branches: dict[str, str], plan_data: dict) -> None:
    commands = [f"verify HEAD {plan_data['control_commit']}",
                f"verify refs/heads/{plan_data['source_release']} {plan_data['source_commit']}",
                f"verify refs/remotes/{plan_data['upstream_ref']} {plan_data['upstream_head']}"]
    for branch, commit in branches.items():
        if ref_exists(repository, branch):
            if require_git(repository, 'rev-parse', f'refs/heads/{branch}') != commit:
                raise ValueError(f'generated branch already exists with a different commit: {branch}')
            commands.append(f'verify refs/heads/{branch} {commit}')
        else:
            commands.append(f'create refs/heads/{branch} {commit}')
    require_git(repository, 'update-ref', '--stdin', input_data=('\n'.join(commands) + '\n').encode())


def apply(arguments: argparse.Namespace) -> None:
    repository = Path(arguments.repository)
    plan_data = read_apply_plan(arguments.plan)
    tools_repository = Path(arguments.tools_repository)
    metadata = validate_frozen_inputs(repository, plan_data, tools_repository)
    action, _, target, sync_branch = validate_plan(plan_data)
    result = git_result(repository, 'diff', '--binary',
                        f"{metadata['upstream_commit']}..{plan_data['source_commit']}",
                        '--', *OVERLAY_PATHS)
    if result.returncode:
        raise ValueError('unable to create overlay patch')
    patch = result.stdout
    base = plan_data['upstream_commit']

    def build_commits(clone: Path):
        if action == 'bootstrap-review':
            commit_clone(clone, [], 'bootstrap resolver plugin release')
            target_commit = require_git(clone, 'rev-parse', 'HEAD')
            if patch:
                apply_overlay(clone, patch)
            commit_clone(clone, list(OVERLAY_PATHS), 'bootstrap resolver plugin overlay', plan_data)
            return target_commit, require_git(clone, 'rev-parse', 'HEAD')
        if patch:
            apply_overlay(clone, patch)
        message = 'bootstrap resolver plugin release' if action == 'bootstrap-build' else 'sync BIND overlay'
        commit_clone(clone, list(OVERLAY_PATHS), message, plan_data if sync_branch else None)
        return require_git(clone, 'rev-parse', 'HEAD')

    commits = with_clone(repository, base, build_commits)
    validate_frozen_inputs(repository, plan_data, tools_repository)
    if action == 'bootstrap-review':
        target_commit, sync_commit = commits
        branches = {target: target_commit, sync_branch: sync_commit}
    else:
        branches = {target if action == 'bootstrap-build' else sync_branch: commits}
    create_branches(repository, branches, plan_data)


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest='command', required=True)
    discover_parser = commands.add_parser('discover')
    discover_parser.add_argument('--repository', required=True)
    discover_parser.add_argument('--upstream', required=True)
    discover_parser.add_argument('--release-prefix', required=True)
    discover_parser.add_argument('--tools-repository', required=True)
    discover_parser.add_argument('--control-commit', required=True)
    outputs_parser = commands.add_parser('github-outputs')
    outputs_parser.add_argument('--discovery', required=True)
    outputs_parser.add_argument('--output', required=True)
    seal_parser = commands.add_parser('seal-plan')
    seal_parser.add_argument('--discovery', required=True)
    seal_parser.add_argument('--core-commit', required=True)
    seal_parser.add_argument('--core-archive-url', required=True)
    seal_parser.add_argument('--core-archive-sha256', required=True)
    seal_parser.add_argument('--output', required=True)
    profile_parser = commands.add_parser('profile')
    profile_parser.add_argument('--plan', required=True)
    profile_parser.add_argument('--output', required=True)
    apply_parser = commands.add_parser('apply')
    apply_parser.add_argument('--repository', required=True)
    apply_parser.add_argument('--plan', required=True)
    apply_parser.add_argument('--tools-repository', required=True)
    arguments = parser.parse_args()
    try:
        if arguments.command == 'discover':
            print(json.dumps(discover(arguments), sort_keys=True))
        elif arguments.command == 'github-outputs':
            discovery = json.loads(Path(arguments.discovery).read_text())
            if discovery.get('action') == 'blocked':
                raise ValueError(f"upstream synchronization is blocked: {discovery.get('reason')}")
            with Path(arguments.output).open('a') as stream:
                for field in ('action', 'series', 'upstream_commit', 'source_release',
                              'target_release', 'tools_tag', 'freebsd_release'):
                    value = discovery.get(field)
                    print(f'{field}={value if value is not None else ""}', file=stream)
        elif arguments.command == 'seal-plan':
            discovery = json.loads(Path(arguments.discovery).read_text(encoding='utf-8'))
            sealed = seal_plan(discovery, arguments.core_commit, arguments.core_archive_url,
                               arguments.core_archive_sha256)
            Path(arguments.output).write_text(json.dumps(sealed, indent=2, sort_keys=True) + '\n')
        elif arguments.command == 'profile':
            Path(arguments.output).write_text(json.dumps(metadata_for(read_apply_plan(arguments.plan)),
                                                        indent=2, sort_keys=True) + '\n')
        else:
            apply(arguments)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.error(str(error))


if __name__ == '__main__':
    main()
