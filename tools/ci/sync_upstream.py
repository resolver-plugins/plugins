#!/usr/bin/env python3
"""Inspect fetched OPNsense refs and plan a safe BIND synchronization."""

import argparse
import json
import re
import subprocess
from pathlib import Path


SERIES_PATTERN = re.compile(r'^stable/(\d+)\.(\d+)$')
RELEASE_PATTERN = re.compile(r'^(\d+)\.(\d+)$')
FREEBSD_PATTERN = re.compile(r'\bFreeBSD(?:\s+base)?\s+(\d+(?:\.\d+)?)\b', re.I)
REQUIRED_METADATA_FIELDS = (
    'series',
    'upstream_branch',
    'upstream_commit',
    'freebsd_release',
    'core_archive_url',
    'core_archive_sha256',
)


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
        if match:
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


def load_metadata(
    repository: Path,
    release: str,
    metadata_path: str,
    source_series: str,
    upstream: str,
) -> dict:
    try:
        metadata = json.loads(run_git(repository, 'show', f'{release}:{metadata_path}'))
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        raise ValueError('missing or invalid source metadata') from None
    if not isinstance(metadata, dict):
        raise ValueError('missing or invalid source metadata')
    if any(not isinstance(metadata.get(field), str) or not metadata[field] for field in REQUIRED_METADATA_FIELDS):
        raise ValueError('missing or invalid source metadata')
    expected_branch = f'stable/{source_series}'
    if metadata['series'] != source_series or metadata['upstream_branch'] != expected_branch:
        raise ValueError('missing or invalid source metadata')
    run_git(
        repository,
        'merge-base',
        '--is-ancestor',
        metadata['upstream_commit'],
        f'{upstream}/{expected_branch}',
    )
    return metadata


def bind_tree(repository: Path, revision: str) -> str:
    return run_git(repository, 'rev-parse', f'{revision}:dns/bind')


def declared_freebsd_release(release_notes_directory: str | None, series: str) -> str | None:
    if not release_notes_directory:
        return None
    directory = Path(release_notes_directory)
    if not directory.is_dir():
        return None
    candidates = sorted(
        path for path in directory.rglob('*')
        if path.is_file() and (path.name == series or path.stem == series)
    )
    if not candidates:
        return None
    lines = candidates[0].read_text(encoding='utf-8').splitlines()
    introduction = []
    for index, line in enumerate(lines):
        if (
            index > 1
            and index + 1 < len(lines)
            and line.strip()
            and re.fullmatch(r'[=\-~^"`:#*+]+', lines[index + 1].strip())
        ):
            break
        introduction.append(line)
    match = FREEBSD_PATTERN.search('\n'.join(introduction))
    return match.group(1) if match else None


def decision(
    action: str,
    series: str | None,
    upstream_commit: str | None,
    source_release: str | None,
    target_release: str | None,
    freebsd_release: str | None,
    bind_changed: bool,
    reason: str,
) -> dict:
    upstream_ref = f'upstream/stable/{series}' if series else None
    sync_branch = None
    if action == 'update-review':
        sync_branch = f'sync/bind/{series}/{upstream_commit[:12]}'
    elif action == 'bootstrap-review':
        sync_branch = f'sync/bootstrap/{series}/{upstream_commit[:12]}'
    return {
        'action': action,
        'series': series,
        'upstream_ref': upstream_ref,
        'upstream_commit': upstream_commit,
        'source_release': source_release,
        'target_release': target_release,
        'sync_branch': sync_branch,
        'freebsd_release': freebsd_release,
        'bind_changed': bind_changed,
        'reason': reason,
    }


def plan(arguments: argparse.Namespace) -> dict:
    repository = Path(arguments.repository)
    stable = stable_refs(repository, arguments.upstream)
    releases = release_refs(repository, arguments.release_prefix)
    available = sorted(stable, key=series_key)
    if not available or not releases:
        return decision('blocked', None, None, None, None, None, False, 'no release source')

    latest_release = max(releases, key=series_key)
    source_release = f'{arguments.release_prefix}{latest_release}'
    source_upstream_commit = stable.get(latest_release)
    try:
        metadata = load_metadata(
            repository,
            source_release,
            arguments.metadata_path,
            latest_release,
            arguments.upstream,
        )
        source_bind_tree = bind_tree(repository, metadata['upstream_commit'])
    except (ValueError, subprocess.CalledProcessError):
        return decision(
            'blocked', latest_release, source_upstream_commit, source_release, source_release,
            None, False, 'missing or invalid source metadata',
        )

    if source_upstream_commit:
        try:
            current_bind_tree = bind_tree(repository, source_upstream_commit)
        except subprocess.CalledProcessError:
            current_bind_tree = None
        if current_bind_tree and source_bind_tree != current_bind_tree:
            freebsd_release = (
                declared_freebsd_release(arguments.release_notes_directory, latest_release)
                or metadata['freebsd_release']
            )
            return decision(
                'update-review', latest_release, source_upstream_commit, source_release,
                source_release, freebsd_release, True, 'upstream BIND tree changed',
            )

    target_series = next(
        (series for series in available if series_key(series) > series_key(latest_release)), None
    )
    if target_series is None:
        return decision(
            'noop', latest_release, source_upstream_commit, source_release, source_release,
            metadata['freebsd_release'], False, 'upstream BIND tree is unchanged',
        )
    target_release = f'{arguments.release_prefix}{target_series}'
    upstream_commit = stable[target_series]
    try:
        bind_changed = source_bind_tree != bind_tree(repository, upstream_commit)
    except subprocess.CalledProcessError:
        return decision(
            'blocked', target_series, upstream_commit, source_release, target_release,
            None, False, 'upstream BIND tree is unavailable',
        )
    freebsd_release = (
        declared_freebsd_release(arguments.release_notes_directory, target_series)
        or metadata['freebsd_release']
    )
    if bind_changed:
        return decision(
            'bootstrap-review', target_series, upstream_commit, source_release,
            target_release, freebsd_release, True, 'new series has an upstream BIND change',
        )
    return decision(
        'bootstrap-build', target_series, upstream_commit, source_release,
        target_release, freebsd_release, False, 'new series has an unchanged BIND tree',
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest='command', required=True)
    plan_parser = commands.add_parser('plan')
    plan_parser.add_argument('--repository', required=True)
    plan_parser.add_argument('--upstream', required=True)
    plan_parser.add_argument('--release-prefix', required=True)
    plan_parser.add_argument('--metadata-path', required=True)
    plan_parser.add_argument('--release-notes-directory')
    arguments = parser.parse_args()
    if arguments.command == 'plan':
        print(json.dumps(plan(arguments), sort_keys=True))


if __name__ == '__main__':
    main()
