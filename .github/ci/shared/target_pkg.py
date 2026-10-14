#!/usr/bin/env python3
"""Resolve release recipes and authenticate, install and verify their pkg identity."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import metadata_profile

PORTS_REPOSITORY = 'https://github.com/opnsense/ports.git'
TOOLS_REPOSITORY = 'https://github.com/opnsense/tools.git'
RECIPE_PATHS = ('opnsense/pkg', 'ports-mgmt/pkg')
IDENTITY_FIELDS = {'name', 'version', 'origin', 'abi'}
VERSION_PATTERN = re.compile(r'[0-9]+(?:\.[0-9]+)*(?:_[0-9]+)?(?:,[0-9]+)?')


class TargetPackageError(ValueError):
    """The release-derived package cannot be selected or verified."""


def reader_release(series):
    if (not isinstance(series, str) or metadata_profile.SERIES_PATTERN.fullmatch(series) is None
            or tuple(map(int, series.split('.'))) < (26, 1)):
        raise TargetPackageError('series predates the supported OPNsense floor')
    return '26.1.11' if series == '26.1' else series


@dataclass(frozen=True)
class PackageIdentity:
    name: str
    version: str
    origin: str
    abi: str


@dataclass(frozen=True)
class TargetPackage:
    series: str
    identity: PackageIdentity
    role: str
    release: str
    ports_commit: str
    recipe_path: str

    @property
    def filename(self) -> str:
        return f'pkg-{self.identity.version}.pkg'

    def record(self) -> dict[str, str]:
        return asdict(self.identity)

    def source_record(self) -> dict[str, str]:
        return dict(release=self.release, repository=PORTS_REPOSITORY,
                    commit=self.ports_commit, path=self.recipe_path)

    def document(self) -> dict:
        return dict(schema=1, role=self.role, series=self.series, release=self.release,
                    recipe=dict(repository=PORTS_REPOSITORY, commit=self.ports_commit,
                                path=self.recipe_path), package_creator=self.record())


def normalize_creator(record: object) -> dict[str, str]:
    if (not isinstance(record, dict) or set(record) != IDENTITY_FIELDS
            or not all(isinstance(v, str) and v for v in record.values())
            or record['name'] != 'pkg' or record['origin'] not in RECIPE_PATHS
            or VERSION_PATTERN.fullmatch(record['version']) is None
            or re.fullmatch(r'FreeBSD:[1-9][0-9]*:amd64', record['abi']) is None):
        raise TargetPackageError('invalid package creator identity')
    return {field: record[field] for field in ('name', 'version', 'origin', 'abi')}


def validate_source(source: object, creator: dict, series: str) -> dict:
    if (not isinstance(source, dict) or set(source) != {'release', 'repository', 'commit', 'path'}
            or not all(isinstance(v, str) for v in source.values())
            or not metadata_profile.tools_tag_matches_series(source['release'], series)
            or source['repository'] != PORTS_REPOSITORY
            or metadata_profile.COMMIT_PATTERN.fullmatch(source['commit']) is None
            or source['path'] != creator['origin']):
        raise TargetPackageError('invalid creator recipe source')
    return source


def validate_target_profile(target: TargetPackage, upstream: Path, role: str | None = None) -> None:
    profile = metadata_profile.load_profile(upstream, target.series)
    expected_role = role or target.role
    expected_release = (profile['tools_tag'] if expected_role == 'creator'
                        else reader_release(target.series))
    if (target.role != expected_role or expected_release is None
            or target.release != expected_release
            or target.identity.abi != f"FreeBSD:{profile['freebsd_release'].split('.')[0]}:amd64"):
        raise TargetPackageError('target role/release/ABI differs from trusted upstream profile')


def load_target(metadata: Path, series: str, *, upstream: Path | None = None,
                role: str | None = None) -> TargetPackage:
    document = json.loads(metadata.read_text())
    if (not isinstance(document, dict)
            or set(document) != {'schema', 'role', 'series', 'release', 'recipe', 'package_creator'}
            or type(document['schema']) is not int or document['schema'] != 1
            or document['series'] != series
            or document['role'] not in ('creator', 'reader')
            or not isinstance(document['recipe'], dict)
            or set(document['recipe']) != {'repository', 'commit', 'path'}):
        raise TargetPackageError('invalid resolved pkg target record')
    creator = normalize_creator(document['package_creator'])
    source = validate_source(dict(document['recipe'], release=document['release']), creator, series)
    target = TargetPackage(series, PackageIdentity(**creator), document['role'],
                           source['release'], source['commit'], source['path'])
    if role and upstream is None:
        raise TargetPackageError('expected role requires an upstream profile')
    if upstream is not None:
        validate_target_profile(target, upstream, role)
    return target


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, capture_output=True, text=True)


def recipe_version(recipe: str) -> str:
    values, depth = {}, 0
    for raw in recipe.splitlines():
        line = raw.split('#', 1)[0].strip()
        if re.match(r'\.if(?:\s|def|ndef)', line):
            depth += 1
        elif line.startswith('.endif'):
            depth -= 1
        match = re.match(r'(PORTNAME|PORTVERSION|DISTVERSION|PORTREVISION|PORTEPOCH|PKGNAMEPREFIX|PKGNAMESUFFIX)\s*([?:+!]?=)\s*(.*)$', line)
        if match:
            key, operator, value = match.groups()
            if depth or key in values or operator != '=':
                raise TargetPackageError('unsupported conditional/duplicate/expression recipe declaration')
            values[key] = value
    versions = [values[k] for k in ('DISTVERSION', 'PORTVERSION') if k in values]
    if (values.get('PORTNAME') != 'pkg' or len(versions) != 1
            or re.fullmatch(r'[0-9]+(?:\.[0-9]+)*', versions[0]) is None
            or any(values.get(k, '') for k in ('PKGNAMEPREFIX', 'PKGNAMESUFFIX'))
            or any(re.fullmatch(r'[0-9]+', values.get(k, '0')) is None
                   for k in ('PORTREVISION', 'PORTEPOCH'))):
        raise TargetPackageError('unsupported pkg recipe; literal version/revision/epoch required')
    revision, epoch = int(values.get('PORTREVISION', '0')), int(values.get('PORTEPOCH', '0'))
    return versions[0] + (f'_{revision}' if revision else '') + (f',{epoch}' if epoch else '')


def resolve_targets(upstream: Path, series: str, architecture: str, output: Path,
                    *, tools_repository: Path | None = None) -> None:
    profile = metadata_profile.load_profile(upstream, series)
    if architecture != 'x86_64':
        raise TargetPackageError('unsupported series/architecture for pkg resolution')
    git = os.environ.get('GIT_COMMAND', 'git')
    with tempfile.TemporaryDirectory() as directory:
        repository = Path(directory) / 'ports.git'
        run([git, 'init', '--bare', str(repository)])
        command = [git, '-C', str(repository)]
        run([*command, 'remote', 'add', 'ports', os.environ.get('OPNSENSE_PORTS_REPOSITORY', PORTS_REPOSITORY)])
        run([*command, 'config', 'remote.ports.promisor', 'true'])
        run([*command, 'config', 'remote.ports.partialclonefilter', 'blob:none'])
        resolved, targets = {}, []
        for role, release in [('creator', profile['tools_tag']), ('reader', reader_release(series))]:
            if release not in resolved:
                run([*command, 'fetch', '--depth=1', '--filter=blob:none', '--no-tags', 'ports', f'refs/tags/{release}'])
                commit = run([*command, 'rev-parse', 'FETCH_HEAD^{commit}']).stdout.strip()
                entries = run([*command, 'ls-tree', commit, '--', *RECIPE_PATHS]).stdout.splitlines()
                paths = [entry.split('\t', 1)[1] for entry in entries]
                if not paths or any(path not in RECIPE_PATHS for path in paths):
                    raise TargetPackageError(f'{release}: missing or ambiguous pkg recipe')
                if len(paths) == 1:
                    path = paths[0]
                else:
                    # OPNsense can retain the standard port beside its own override.
                    # Follow the literal build list at this role's matching tools tag.
                    tools_commit = None
                    if tools_repository is not None:
                        if role == 'creator':
                            proof = metadata_profile.verify_tools_profile(profile, tools_repository)
                            tools_commit = proof['tools_commit']
                        else:
                            result = subprocess.run([git, '-C', str(tools_repository), 'rev-parse',
                                '--verify', f'refs/tags/{release}^{{commit}}'], capture_output=True, text=True)
                            if result.returncode == 0:
                                tools_commit = result.stdout.strip()
                        tools_command = [git, '-C', str(tools_repository)]
                    if tools_commit is None:
                        tools = Path(directory) / f'tools-{release}.git'
                        run([git, 'init', '--bare', str(tools)])
                        tools_command = [git, '-C', str(tools)]
                        run([*tools_command, 'fetch', '--depth=1', '--filter=blob:none', '--no-tags',
                             os.environ.get('OPNSENSE_TOOLS_REPOSITORY', TOOLS_REPOSITORY), f'refs/tags/{release}'])
                        tools_commit = run([*tools_command, 'rev-parse', 'FETCH_HEAD^{commit}']).stdout.strip()
                    if metadata_profile.COMMIT_PATTERN.fullmatch(tools_commit) is None:
                        raise TargetPackageError('pkg origin requires an immutable tools commit')
                    build_list = run([*tools_command, 'show', f'{tools_commit}:config/{series}/ports.conf']).stdout
                    declared = {line.split('#', 1)[0].strip() for line in build_list.splitlines()} & set(RECIPE_PATHS)
                    if len(declared) != 1 or not declared.issubset(paths):
                        raise TargetPackageError(f'{release}: missing or ambiguous pkg origin in official tools build list')
                    path = declared.pop()
                version = recipe_version(run([*command, 'show', f'{commit}:{path}/Makefile']).stdout)
                resolved[release] = commit, path, version
            commit, path, version = resolved[release]
            target = TargetPackage(series, PackageIdentity('pkg', version, path,
                                   f"FreeBSD:{profile['freebsd_release'].split('.')[0]}:amd64"),
                                   role, release, commit, path)
            validate_target_profile(target, upstream, role)
            targets.append(target)
    output.mkdir(parents=True, exist_ok=True)
    for target in targets:
        (output / f'{target.role}-target.json').write_text(json.dumps(target.document(), indent=2) + '\n')


def query_identity(pkg_command: str, arguments: list[str]) -> PackageIdentity:
    lines = run([pkg_command, *arguments, '%n|%v|%o|%q']).stdout.strip().splitlines()
    if len(lines) != 1 or len(lines[0].split('|')) != 4 or not all(lines[0].split('|')):
        raise TargetPackageError('cannot read one pkg identity')
    return PackageIdentity(*lines[0].split('|'))


def downloaded_archive(directory: Path, filename: str) -> Path:
    matches = [path for path in (directory / filename, directory / 'All' / filename) if path.is_file()]
    if len(matches) != 1:
        raise TargetPackageError(f'pkg did not fetch exactly one {filename}')
    return matches[0]


def fetch_target_pkg(target: TargetPackage, pkg_command: str, repository: str, output: Path) -> Path:
    for selected in (repository, 'OPNsense-creator'):
        command = [pkg_command]
        if selected == 'OPNsense-creator':
            command += ['-R', str(Path(os.environ.get('PKG_REPOS_DIR', '/usr/local/etc/pkg/repos')) / 'target-pkg')]
        run([*command, 'update', '-f', '-r', selected])
        rows = run([*command, 'rquery', '-U', '-r', selected, '-e', '%n = pkg', '%n|%v|%o|%q']).stdout.splitlines()
        if any(len(row.split('|')) != 4 or not all(row.split('|')) for row in rows):
            raise TargetPackageError('malformed authenticated pkg catalogue identity')
        if '|'.join(asdict(target.identity).values()) in rows:
            run([*command, 'fetch', '-U', '-y', '-r', selected, '-o', str(output),
                 target.filename.removesuffix('.pkg')])
            return downloaded_archive(output, target.filename)
    raise TargetPackageError(f'{target.role} release {target.release}: {target.record()} absent from {selected}')


def verify_target_pkg(target: TargetPackage, pkg_command: str, *,
                      pkg_static_path: Path = Path('/usr/local/sbin/pkg-static')) -> None:
    if query_identity(pkg_command, ['query', '-e', '%n = pkg']) != target.identity:
        raise TargetPackageError('installed pkg identity differs from selected recipe')
    if f'pkg-{target.identity.version}' not in run([pkg_command, 'lock', '-l']).stdout.splitlines():
        raise TargetPackageError('target pkg is not locked')
    if not pkg_static_path.is_file() or not os.access(pkg_static_path, os.X_OK):
        raise TargetPackageError('pkg-static is not executable')
    if run([pkg_command, 'which', '-q', str(pkg_static_path)]).stdout.strip() != f'pkg-{target.identity.version}':
        raise TargetPackageError('pkg-static is not owned by selected pkg')
    if run([str(pkg_static_path), '-v']).stdout.strip() != target.identity.version.split(',')[0].split('_')[0]:
        raise TargetPackageError('pkg-static version differs from selected recipe')
    try:
        run([pkg_command, 'check', '-s', 'pkg'])
    except subprocess.CalledProcessError as error:
        raise TargetPackageError('pkg installed-manifest integrity check failed') from error


def select_target_pkg(metadata: Path, series: str, pkg_command: str, repository: str = 'OPNsense',
                      *, upstream: Path, role: str,
                      pkg_static_path: Path = Path('/usr/local/sbin/pkg-static')) -> TargetPackage:
    target = load_target(metadata, series, upstream=upstream, role=role)
    unlocked = False
    try:
        with tempfile.TemporaryDirectory() as directory:
            archive = fetch_target_pkg(target, pkg_command, repository, Path(directory))
            if query_identity(pkg_command, ['query', '-F', str(archive)]) != target.identity:
                raise TargetPackageError('authenticated archive identity differs from selected recipe')
            if run([pkg_command, 'query', '%k', 'pkg']).stdout.strip() == '1':
                run([pkg_command, 'unlock', '-y', 'pkg'])
                unlocked = True
            run([pkg_command, 'add', '-f', str(archive)])
        run([pkg_command, 'lock', '-y', 'pkg'])
        verify_target_pkg(target, pkg_command, pkg_static_path=pkg_static_path)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        lock_error = ''
        if unlocked:
            try:
                run([pkg_command, 'lock', '-y', 'pkg'])
            except (OSError, subprocess.CalledProcessError) as failure:
                lock_error = f'; relocking also failed: {failure}'
        detail = (error.stderr or str(error)) if isinstance(error, subprocess.CalledProcessError) else str(error)
        raise TargetPackageError(f'{target.role} release {target.release} {target.record()} via {repository}/OPNsense-creator: {detail}{lock_error}') from error
    return target


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest='command', required=True)
    resolve = commands.add_parser('resolve')
    resolve.add_argument('--upstream', required=True, type=Path)
    resolve.add_argument('--series', required=True)
    resolve.add_argument('--architecture', required=True)
    resolve.add_argument('--output-directory', required=True, type=Path)
    resolve.add_argument('--tools-repository', type=Path)
    for name in ('install', 'verify', 'field'):
        command = commands.add_parser(name)
        command.add_argument('metadata', type=Path)
        command.add_argument('series')
        command.add_argument('--upstream', required=name != 'field', type=Path)
        command.add_argument('--role', required=name != 'field', choices=('creator', 'reader'))
        if name == 'field':
            command.add_argument('field', choices=sorted(IDENTITY_FIELDS | {'filename', 'release', 'ports_commit', 'recipe_path'}))
        else:
            command.add_argument('--pkg-command', default='pkg')
            command.add_argument('--pkg-static', type=Path, default=Path('/usr/local/sbin/pkg-static'))
            command.add_argument('--repository', default='OPNsense')
    args = parser.parse_args()
    try:
        if args.command == 'resolve':
            resolve_targets(args.upstream, args.series, args.architecture, args.output_directory,
                            tools_repository=args.tools_repository)
        else:
            target = load_target(args.metadata, args.series, upstream=args.upstream, role=args.role)
            if args.command == 'install':
                select_target_pkg(args.metadata, args.series, args.pkg_command, args.repository,
                                  upstream=args.upstream, role=args.role, pkg_static_path=args.pkg_static)
                print(json.dumps(target.record(), sort_keys=True))
            elif args.command == 'verify':
                verify_target_pkg(target, args.pkg_command, pkg_static_path=args.pkg_static)
            else:
                print(target.record()[args.field] if args.field in IDENTITY_FIELDS else getattr(target, args.field))
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f'target pkg selection failed: {error}', file=sys.stderr)
        if isinstance(error, subprocess.CalledProcessError):
            print(error.stderr or error.stdout or '', file=sys.stderr, end='')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
