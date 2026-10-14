#!/usr/bin/env python3
"""Select changed plugin/series builds and seal their discovered inputs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bind.runtime import bind920_profile
from bind.shared import selection
from shared import metadata_profile, release_channel, upstream_inputs, target_pkg

INPUT_FILE = 'series-build-inputs.json'
WORKFLOWS = {'bind_runtime': 'bind-runtime-release.yml', 'bind_plugin': 'bind-plugin-release.yml',
             'dhcp_interface_ha': 'ha-dhcp-interface-release.yml'}
COMMON_RECIPES = ('.github/ci/shared/metadata_profile.py', '.github/ci/shared/target_pkg.py',
                  '.github/ci/shared/setup-opnsense-repository.sh', '.github/ci/shared/package_checksums.py')
RECIPES = {
    'bind_runtime': ('.github/ci/bind/runtime', *COMMON_RECIPES),
    'bind_plugin': ('.github/ci/bind/plugin', '.github/ci/shared/plugin_source.py', *COMMON_RECIPES),
    'dhcp_interface_ha': ('.github/ci/ha_dhcp/build-dhcp-interface-ha.sh',
                        '.github/ci/ha_dhcp/dhcp_interface_ha_release.py',
                        '.github/ci/shared/plugin_source.py', *COMMON_RECIPES),
}
PACKAGES = {'bind_plugin': 'dns/bind', 'dhcp_interface_ha': 'net/dhcp-interface-ha'}
ENVIRONMENT_FIELDS = {'tools_tag', 'tools_commit', 'build_conf_sha256', 'freebsd_release', 'core_commit'}


def fingerprint(record):
    validate_inputs(record)
    return hashlib.sha256(json.dumps(record, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def tree_digest(repository, commit, paths):
    entries = subprocess.check_output(['git', '-C', str(repository), 'ls-tree', '-r', '-z', commit, '--', *paths])
    filtered = []
    for entry in entries.split(b'\0'):
        if not entry:
            continue
        path = entry.split(b'\t', 1)[1].decode()
        if '/tests/' not in path and not path.endswith('/README.md'):
            filtered.append(entry)
    return hashlib.sha256(b'\0'.join(filtered)).hexdigest()


def build_inputs(repository, component, series, source_commit, control_commit, upstream, tools, targets=None):
    metadata_profile.validate_profile(upstream, series)
    metadata_profile.validate_tools_record(tools, upstream)
    record = dict(schema=1, component=component, series=series,
                  source_sha256=None if component == 'bind_runtime' else tree_digest(
                      repository, source_commit, (PACKAGES[component], 'Mk', 'Scripts', 'Templates')),
                  recipe_sha256=tree_digest(repository, control_commit, RECIPES[component]),
                  environment={field: (upstream if field == 'core_commit' else tools)[field]
                               for field in ENVIRONMENT_FIELDS}, runtime_approval=None)
    if component == 'bind_runtime':
        raw = upstream_inputs.git(repository, 'show', f'{control_commit}:dns/bind/Makefile')
        record['runtime_approval'] = bind920_profile.approval_from_makefile(raw)
    if targets is not None:
        record.update(schema=2, package_targets=targets)
    validate_inputs(record)
    return record


def validate_inputs(record):
    extra = {'package_targets'} if isinstance(record, dict) and record.get('schema') == 2 else set()
    if (not isinstance(record, dict)
            or set(record) != {'schema', 'component', 'series', 'source_sha256', 'recipe_sha256',
                              'environment', 'runtime_approval'} | extra
            or type(record['schema']) is not int or record['schema'] not in (1, 2)
            or record['component'] not in WORKFLOWS
            or not isinstance(record['series'], str)
            or metadata_profile.SERIES_PATTERN.fullmatch(record['series']) is None):
        raise ValueError('invalid per-series build inputs')
    for field in ('recipe_sha256', 'source_sha256'):
        value = record[field]
        if field == 'source_sha256' and record['component'] == 'bind_runtime' and value is None:
            continue
        if not isinstance(value, str) or metadata_profile.SHA256_PATTERN.fullmatch(value) is None:
            raise ValueError('invalid per-series source/recipe digest')
    environment = record['environment']
    if (not isinstance(environment, dict) or set(environment) != ENVIRONMENT_FIELDS
            or not all(isinstance(value, str) for value in environment.values())
            or not metadata_profile.tools_tag_matches_series(environment['tools_tag'], record['series'])
            or metadata_profile.FREEBSD_RELEASE_PATTERN.fullmatch(environment['freebsd_release']) is None
            or any(metadata_profile.COMMIT_PATTERN.fullmatch(environment[field]) is None
                   for field in ('tools_commit', 'core_commit'))
            or metadata_profile.SHA256_PATTERN.fullmatch(environment['build_conf_sha256']) is None):
        raise ValueError('invalid per-series upstream environment')
    approval = record['runtime_approval']
    if record['component'] == 'bind_runtime':
        if (not isinstance(approval, dict) or set(approval) != {'version', 'ports_commit'}
                or not isinstance(approval['version'], str)
                or re.fullmatch(r'\d+(?:\.\d+)*(?:_\d+)?', approval['version']) is None
                or not isinstance(approval['ports_commit'], str)
                or metadata_profile.COMMIT_PATTERN.fullmatch(approval['ports_commit']) is None):
            raise ValueError('invalid per-series runtime approval')
    elif approval is not None:
        raise ValueError('plugin inputs cannot select runtime production')
    if extra:
        from bind.shared.components import _role
        targets = record['package_targets']
        if not isinstance(targets, dict) or set(targets) != {'creator', 'reader'}:
            raise ValueError('invalid per-series package targets')
        profile = dict(series=record['series'], tools_tag=environment['tools_tag'], freebsd_release=environment['freebsd_release'])
        for role in ('creator', 'reader'):
            _role(targets[role], profile, role)
    return record


def validate_environment(record, upstream, tools=None):
    validate_inputs(record)
    for field in ('tools_tag', 'freebsd_release', 'core_commit'):
        if record['environment'][field] != upstream[field]:
            raise ValueError('series build environment differs from frozen upstream inputs')
    if tools is not None:
        for field in ENVIRONMENT_FIELDS - {'core_commit'}:
            if record['environment'][field] != tools[field]:
                raise ValueError('series build environment differs from exact tools evidence')


def current_identity(repository, component, series):
    tag = (f'pkg-dhcp-interface-ha-{series}' if component == 'dhcp_interface_ha'
           else f'pkg-{series}-{component.replace("_", "-")}')
    result = subprocess.run(['gh', 'release', 'view', tag, '--repo', repository, '--json', 'assets,isDraft'],
                            text=True, capture_output=True)
    if result.returncode:
        if 'not found' in result.stderr.lower():
            return None
        raise RuntimeError(result.stderr.strip() or 'cannot inspect current series build')
    payload = json.loads(result.stdout)
    if payload.get('isDraft') is not False:
        raise ValueError('current series component is unpublished')
    with tempfile.TemporaryDirectory(prefix='series-current-') as temporary:
        directory = Path(temporary)
        release_channel.download_release_asset(repository, tag, 'channel.json', directory)
        channel = json.loads((directory / 'channel.json').read_text())
        if channel.get('series') != series:
            raise ValueError('current package belongs to a different series')
        if component == 'bind_runtime':
            name = 'bind920-profile.json'
            release_channel.download_release_asset(repository, tag, name, directory)
            if channel['assets'].get(name) != release_channel.sha256(directory / name):
                raise ValueError('current runtime approval differs from published provenance')
            profile = bind920_profile.validate_profile(json.loads((directory / name).read_text()))
            return dict(runtime_approval=dict(version=bind920_profile.package_version(profile),
                                              ports_commit=profile['ports_commit']))
        source = channel.get('source_commit')
        if not isinstance(source, str) or metadata_profile.COMMIT_PATTERN.fullmatch(source) is None:
            raise ValueError('current plugin has no immutable source provenance')
        return dict(source_commit=source)


def candidate_events(component, event, refs, changed, **package):
    if event['name'] == 'schedule' and component != 'bind_plugin':
        return []
    prefix = 'refs/heads/' + selection.RELEASE_PREFIX[component]
    registered = sorted((ref for ref in refs if re.fullmatch(re.escape(prefix) + r'\d+\.\d+', ref)),
                        key=lambda ref: tuple(map(int, ref[len(prefix):].split('.'))), reverse=True)
    if event['name'] in ('workflow_dispatch', 'pull_request_target'):
        return [selection.select_event(component, event, refs, changed, **package)]
    candidates = []
    for ref in registered:
        context = event
        if event['name'] == 'schedule':
            context = dict(event, name='workflow_dispatch', inputs=dict(mode='production', series=ref[len(prefix):], pull_number=None))
        candidates.append(selection.select_event(component, context, [ref], changed, **package))
    return candidates


def select_builds(repository, component, event, refs, changed, distribution, *, discover=upstream_inputs.discover_inputs,
                  published=current_identity, resolve_targets=target_pkg.resolve_targets, **package):
    selected = []
    with tempfile.TemporaryDirectory(prefix='series-discovery-') as temporary:
        for index, candidate in enumerate(candidate_events(component, event, refs, changed, **package)):
            if not candidate['release']:
                continue
            ref = candidate['profile_ref']
            subprocess.run(['git', '-C', str(repository), 'fetch', '--no-tags', 'origin', ref],
                           check=True, stdout=sys.stderr)
            source = upstream_inputs.git(repository, 'rev-parse', 'FETCH_HEAD^{commit}')
            previous = published(distribution, component, candidate['series']) if candidate['mode'] == 'production' else None
            if previous is not None:
                if component == 'bind_runtime':
                    raw = upstream_inputs.git(repository, 'show', f'{candidate["control_ref"]}:dns/bind/Makefile')
                    unchanged = previous['runtime_approval'] == bind920_profile.approval_from_makefile(raw)
                    if not unchanged:
                        old = previous['runtime_approval']
                        selection.package_changes(f'RP_BIND_VERSION= {old["version"]}\nRP_BIND_PORTS_COMMIT= {old["ports_commit"]}\n', raw)
                else:
                    before = previous['source_commit']
                    if subprocess.run(['git', '-C', str(repository), 'cat-file', '-e', f'{before}^{{commit}}'],
                                      capture_output=True).returncode:
                        subprocess.run(['git', '-C', str(repository), 'fetch', '--no-tags', 'origin', before],
                                       check=True, stdout=sys.stderr)
                    paths = (PACKAGES[component],)
                    unchanged = tree_digest(repository, before, paths) == tree_digest(repository, source, paths)
                if unchanged:
                    print(f'{component} {candidate["series"]}: package source/approval unchanged; no build', file=sys.stderr)
                    continue
            tools_repository = Path(temporary) / str(index)
            profile = discover(repository, source, candidate['series'], tools_repository)
            tools = metadata_profile.verify_tools_profile(profile, tools_repository)
            targets_directory = Path(temporary) / f'targets-{index}'
            targets_directory.mkdir()
            profile_path = targets_directory / 'upstream.json'
            profile_path.write_text(json.dumps(profile))
            resolve_targets(profile_path, candidate['series'], 'x86_64', targets_directory,
                            tools_repository=tools_repository)
            targets = {role: json.loads((targets_directory / f'{role}-target.json').read_text())
                       for role in ('creator', 'reader')}
            inputs = build_inputs(repository, component, candidate['series'], source,
                                  candidate['control_ref'], profile, tools, targets)
            candidate.update(profile_ref=source)
            if component != 'bind_runtime':
                candidate['source_ref'] = source
            selected.append(dict(selection=candidate, upstream=profile, tools=tools, inputs=inputs, targets=targets))
    return selected


def write_discovery(directory, build):
    directory.mkdir(parents=True, exist_ok=True)
    if any(directory.iterdir()):
        raise ValueError('upstream discovery output must be empty')
    for name, record in (('upstream.json', build['upstream']), ('tools-profile.json', build['tools']), (INPUT_FILE, build['inputs'])):
        (directory / name).write_text(json.dumps(record, sort_keys=True, indent=2) + '\n')
    for role, record in build['targets'].items():
        (directory / f'{role}-target.json').write_text(json.dumps(record, sort_keys=True, indent=2) + '\n')
    candidate = build['selection']
    seal = dict(schema=1, component=build['inputs']['component'], series=candidate['series'],
                control_commit=candidate['control_ref'], source_commit=candidate['profile_ref'],
                files={path.name: release_channel.sha256(path) for path in directory.iterdir()})
    path = directory / 'discovery.json'
    path.write_text(json.dumps(seal, sort_keys=True, indent=2) + '\n')
    return release_channel.sha256(path)


def materialize_discovery(repository, directory, digest, component, series, source, control, tools_repository, output):
    path = directory / 'discovery.json'
    if release_channel.sha256(path) != digest:
        raise ValueError('upstream discovery differs from the controller seal')
    seal = json.loads(path.read_text())
    if (set(seal) != {'schema', 'component', 'series', 'control_commit', 'source_commit', 'files'}
            or seal['schema'] != 1 or seal['component'] != component or seal['series'] != series
            or seal['source_commit'] != source or seal['control_commit'] != control
            or set(seal['files']) != {'upstream.json', 'tools-profile.json', INPUT_FILE, 'creator-target.json', 'reader-target.json'}
            or any(release_channel.sha256(directory / name) != value for name, value in seal['files'].items())):
        raise ValueError('upstream discovery identities or files differ')
    profile = metadata_profile.load_profile(directory / 'upstream.json', series)
    upstream_inputs.validate_source_profile(profile, source, repository)
    tools = metadata_profile.validate_tools_record(json.loads((directory / 'tools-profile.json').read_text()), profile)
    inputs = json.loads((directory / INPUT_FILE).read_text())
    targets = {role: json.loads((directory / f'{role}-target.json').read_text()) for role in ('creator', 'reader')}
    if inputs != build_inputs(repository, component, series, source, control, profile, tools, targets):
        raise ValueError('series inputs differ from selected source/control Git objects')
    tools_repository.mkdir(parents=True, exist_ok=True)
    if any(tools_repository.iterdir()):
        raise ValueError('frozen tools output must be empty')
    subprocess.run(['git', 'init', '--quiet', '--bare', str(tools_repository)], check=True)
    subprocess.run(['git', '-C', str(tools_repository), 'fetch', '--no-tags', '--depth=1',
                    'https://github.com/opnsense/tools.git', tools['tools_commit']], check=True, stdout=sys.stderr)
    subprocess.run(['git', '-C', str(tools_repository), 'update-ref', f'refs/tags/{profile["tools_tag"]}', tools['tools_commit']], check=True)
    if metadata_profile.verify_tools_profile(profile, tools_repository) != tools:
        raise ValueError('frozen tools bytes differ from controller evidence')
    shutil.copyfile(directory / 'upstream.json', output)


def dispatch(repository, component, series):
    inputs = dict(series=series)
    if component != 'dhcp_interface_ha':
        inputs['mode'] = 'production'
    subprocess.run(['gh', 'api', '--method', 'POST', f'repos/{repository}/actions/workflows/{WORKFLOWS[component]}/dispatches',
                    '--input', '-'], input=json.dumps(dict(ref='master', inputs=inputs)), text=True, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('select-event', 'materialize-discovery', 'fingerprint'))
    parser.add_argument('--component', choices=tuple(WORKFLOWS), required=True)
    parser.add_argument('--repository-root', type=Path, default=Path.cwd())
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--event-file', type=Path)
    parser.add_argument('--refs-file', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--github-output', type=Path)
    parser.add_argument('--distribution-repository', default='resolver-plugins/repository')
    parser.add_argument('--github-repository', default=os.environ.get('GITHUB_REPOSITORY'))
    parser.add_argument('--dispatch', action='store_true')
    parser.add_argument('--sha256')
    parser.add_argument('--series')
    parser.add_argument('--source-commit')
    parser.add_argument('--control-commit')
    parser.add_argument('--tools-repository', type=Path)
    args = parser.parse_args()
    if args.command == 'fingerprint':
        record = json.loads((args.directory / INPUT_FILE).read_text())
        if record['component'] != args.component:
            parser.error('build inputs select a different component')
        print(fingerprint(record))
        return
    if args.command == 'materialize-discovery':
        materialize_discovery(args.repository_root, args.directory, args.sha256, args.component, args.series,
                              args.source_commit, args.control_commit, args.tools_repository, args.output)
        return
    document = json.loads(args.event_file.read_text())
    event = selection.normalize_event(document)
    if event['name'] == 'workflow_dispatch' and event['inputs']['mode'] == 'development':
        pull = event['inputs']['pull_number']
        if not pull or re.fullmatch(r'[1-9]\d*', pull) is None:
            parser.error('development discovery requires a pull request')
        event['pr_base_ref'] = subprocess.check_output(['gh', 'api', f'repos/{args.github_repository}/pulls/{pull}', '--jq', '.base.ref'], text=True).strip()
    changed = selection.discover_changed_paths(event, document)
    package = {}
    if event['name'] == 'push' and selection.PACKAGE_DEFINITION in changed:
        for name in ('before', 'after'):
            commit = event['before'] if name == 'before' else event['sha']
            package[name + '_package'] = '' if commit == '0' * 40 else upstream_inputs.git(args.repository_root, 'show', f'{commit}:{selection.PACKAGE_DEFINITION}')
    builds = select_builds(args.repository_root, args.component, event, json.loads(args.refs_file.read_text()), changed,
                           args.distribution_repository, **package)
    chosen = builds[0]['selection'] if builds else dict.fromkeys(selection.OUTPUT_FIELDS)
    if builds:
        if len(builds) > 1 and not args.dispatch:
            parser.error('multiple changed series require explicit dispatch authorization')
        for build in builds[1:]:
            dispatch(args.github_repository, args.component, build['selection']['series'])
        chosen['discovery_sha256'] = write_discovery(args.directory, builds[0])
    else:
        chosen.update(mode='production', release=False)
    args.output.write_text(json.dumps(chosen, indent=2, sort_keys=True) + '\n')
    with args.github_output.open('a') as stream:
        for name in (*selection.OUTPUT_FIELDS, 'discovery_sha256'):
            value = chosen.get(name)
            print(f'{name}={json.dumps(value) if isinstance(value, bool) else value or ""}', file=stream)


if __name__ == '__main__':
    main()
