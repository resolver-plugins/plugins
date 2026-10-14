#!/usr/bin/env python3
"""Discover upstream build inputs once, then freeze them for this run."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared import metadata_profile


def git(repository, *arguments):
    return subprocess.check_output(['git', '-C', str(repository), *arguments], text=True).strip()


def source_baseline(repository, source_commit, upstream_ref):
    """Derive the reviewed upstream baseline from Git ancestry, without sidecars."""
    if metadata_profile.COMMIT_PATTERN.fullmatch(source_commit) is None:
        raise ValueError('source must be a full immutable commit')
    bases = git(repository, 'merge-base', '--all', source_commit, upstream_ref).splitlines()
    if len(bases) != 1 or metadata_profile.COMMIT_PATTERN.fullmatch(bases[0]) is None:
        raise ValueError('source requires one unambiguous upstream baseline and full Git history')
    return bases[0]


def latest_tools_tag(tags, series):
    if metadata_profile.SERIES_PATTERN.fullmatch(series) is None:
        raise ValueError('invalid upstream series')
    candidates = [tag for tag in tags if metadata_profile.tools_tag_matches_series(tag, series)]
    if not candidates:
        raise ValueError(f'missing numeric tools tag for {series}')
    return max(candidates, key=lambda tag: int(tag[len(series) + 1:]) if tag != series else -1)


def tools_release_profile(tools_repository, series):
    tag = latest_tools_tag(git(tools_repository, 'tag', '--list').splitlines(), series)
    commit = git(tools_repository, 'rev-parse', f'refs/tags/{tag}^{{commit}}')
    contents = git(tools_repository, 'show', f'{commit}:config/{series}/build.conf')
    return tag, commit, metadata_profile.freebsd_from_build_conf(contents)


def validate_source_profile(profile, source_commit, repository):
    """Check sealed, generated provenance against the exact selected source."""
    profile = metadata_profile.validate_profile(profile, profile['series'])
    if (metadata_profile.COMMIT_PATTERN.fullmatch(source_commit) is None
            or git(repository, 'rev-parse', f'{source_commit}^{{commit}}') != source_commit):
        raise ValueError('source object differs from selection')
    result = subprocess.run(['git', '-C', str(repository), 'merge-base', '--is-ancestor',
                             profile['upstream_commit'], source_commit], capture_output=True)
    if result.returncode:
        raise ValueError('discovered upstream baseline differs from selected source ancestry')
    return profile


def environment_can_advance(before, after, *, core_url='https://github.com/opnsense/core.git'):
    """Permit new environment variants while rejecting an older sealed retry."""
    before = metadata_profile.validate_profile(before, before['series'])
    after = metadata_profile.validate_profile(after, after['series'])
    if before['series'] != after['series']:
        return False
    if tuple(map(int, after['tools_tag'].split('.'))) < tuple(map(int, before['tools_tag'].split('.'))):
        return False
    if before['core_commit'] == after['core_commit']:
        return True
    with tempfile.TemporaryDirectory(prefix='series-core-history-') as temporary:
        subprocess.run(['git', 'init', '--quiet', '--bare', temporary], check=True)
        subprocess.run(['git', '-C', temporary, 'fetch', '--quiet', '--no-tags', '--filter=tree:0',
                        core_url, before['core_commit'], after['core_commit']], check=True)
        result = subprocess.run(['git', '-C', temporary, 'merge-base', '--is-ancestor',
                                 before['core_commit'], after['core_commit']], capture_output=True)
        if result.returncode not in (0, 1):
            raise ValueError('cannot verify immutable core environment ancestry')
        return result.returncode == 0


def core_archive_sha256(url):
    digest = hashlib.sha256()
    with urlopen(url, timeout=60) as response:
        while chunk := response.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def discover_inputs(repository, source_commit, series, tools_repository, *,
                    plugins_url='https://github.com/opnsense/plugins.git',
                    tools_url='https://github.com/opnsense/tools.git',
                    core_url='https://github.com/opnsense/core.git'):
    """Controller boundary: resolve moving official refs before any build starts."""
    if metadata_profile.SERIES_PATTERN.fullmatch(series) is None:
        raise ValueError('invalid upstream series')
    if metadata_profile.COMMIT_PATTERN.fullmatch(source_commit) is None:
        raise ValueError('source must be a full immutable commit')
    tools_repository.mkdir(parents=True, exist_ok=True)
    if any(tools_repository.iterdir()):
        raise ValueError('tools discovery output must be empty')
    ref = f'refs/remotes/upstream/stable/{series}'
    subprocess.run(['git', '-C', str(repository), 'fetch', '--no-tags', plugins_url,
                    f'+refs/heads/stable/{series}:{ref}'], check=True, stdout=sys.stderr)
    baseline = source_baseline(repository, source_commit, ref)
    tags = subprocess.check_output(['git', 'ls-remote', '--tags', '--refs', tools_url], text=True)
    tag = latest_tools_tag([line.split()[1].removeprefix('refs/tags/')
                           for line in tags.splitlines()], series)
    subprocess.run(['git', 'init', '--bare', str(tools_repository)], check=True, stdout=sys.stderr)
    subprocess.run(['git', '-C', str(tools_repository), 'fetch', '--no-tags', '--depth=1',
                    tools_url, f'refs/tags/{tag}:refs/tags/{tag}'], check=True, stdout=sys.stderr)
    tag, _tools_commit, release = tools_release_profile(tools_repository, series)
    core_ref = f'refs/heads/stable/{series}'
    lines = subprocess.check_output(['git', 'ls-remote', core_url, core_ref], text=True).splitlines()
    if len(lines) != 1 or lines[0].split()[1:] != [core_ref]:
        raise ValueError(f'cannot resolve exact core stable/{series} commit')
    core_commit = lines[0].split()[0]
    if metadata_profile.COMMIT_PATTERN.fullmatch(core_commit) is None:
        raise ValueError('core stable ref is not an immutable commit')
    archive_url = f'https://github.com/opnsense/core/archive/{core_commit}.tar.gz'
    profile = dict(series=series, upstream_branch=f'stable/{series}', upstream_commit=baseline,
                   tools_tag=tag, freebsd_release=release, core_commit=core_commit,
                   core_archive_url=archive_url, core_archive_sha256=core_archive_sha256(archive_url))
    profile = validate_source_profile(profile, source_commit, repository)
    metadata_profile.verify_tools_profile(profile, tools_repository)
    return profile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', type=Path, required=True)
    parser.add_argument('--source-commit', required=True)
    parser.add_argument('--series', required=True)
    parser.add_argument('--tools-repository', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        profile = discover_inputs(args.repository, args.source_commit, args.series, args.tools_repository)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(profile, indent=2, sort_keys=True) + '\n')
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.error(str(error))


if __name__ == '__main__':
    main()
