from git_fixtures import *
from module_fixtures import load_module

from workflow_fixtures import assert_permissions, assert_action_release_tags, assert_checkout_credentials, job_text, workflow_jobs, workflow_steps, step_run, trigger_paths

import pytest


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[3]


@pytest.mark.parametrize('helper', [
    'shared/release_channel.py', 'shared/package_catalogue.py',
    'ha_dhcp/dhcp_interface_ha_channel.py', 'ha_dhcp/publish-dhcp-interface-ha.py',
])
def test_release_entrypoints_resolve_cross_directory_imports(tmp_path, helper):
    result = subprocess.run([sys.executable, '-I', REPOSITORY_ROOT / '.github/ci' / helper, '--help'],
                            cwd=tmp_path, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('changed', [None, 'creator', 'reader'])
def test_control_bundle_preflight_rejects_changed_records(tmp_path, monkeypatch, changed):
    """The HA role bundle retains its own producer; observe admission, not digest diagnostics."""
    from package_fixtures import (PLUGIN_PACKAGE, make_ports_recipe, make_tools_repository, write_resolved_target, write_json)
    target_pkg = load_module('workflow_target_pkg', 'shared/target_pkg.py', register=True)
    trusted = tmp_path / 'artifacts/trusted'
    trusted.mkdir(parents=True)
    upstream = write_json(trusted / 'upstream.json', upstream_profile('26.7'))
    digests = {}
    for role in ('creator', 'reader'):
        record = write_resolved_target(trusted / f'{role}-target.json', upstream_profile('26.7'), role=role)
        digests[role] = hashlib.sha256(record.read_bytes()).hexdigest()
        if changed == role:
            data = json.loads(record.read_text())
            data['package_creator']['version'] = '9.9.9'
            write_json(record, data)
        target_pkg.load_target(record, '26.7', upstream=upstream, role=role)
    ports, approval, _ = make_ports_recipe(tmp_path / 'ports')
    repository = init_repository(tmp_path)
    control = commit(repository, {'dns/bind/Makefile': PLUGIN_PACKAGE + approval}, 'reviewed HA control')
    tools = make_tools_repository(tmp_path / 'tools', upstream_profile('26.7'))
    ha_inputs = load_module('ha_workflow_inputs', 'ha_dhcp/dhcp_interface_ha_release.py')
    resolve = ha_inputs.bind920_profile.resolve_profile
    with monkeypatch.context() as boundary:
        boundary.chdir(repository)
        boundary.setattr(ha_inputs.bind920_profile, 'resolve_profile', lambda record: resolve(record, repository=ports))
        bind_digest = ha_inputs.freeze_bind_inputs(trusted, upstream_profile('26.7'), tools, control)
    shutil.copytree(REPOSITORY_ROOT / '.github/ci', tmp_path / '.github/ci',
                    ignore=shutil.ignore_patterns('__pycache__'))
    signer = job_text((REPOSITORY_ROOT / '.github/workflows/ha-dhcp-interface-release.yml').read_text(), 'sign')
    script = next(step_run(step) for step in workflow_steps(signer) if 'sha256sum --check' in step_run(step))
    for role, digest in digests.items():
        script = script.replace(action_expression(f'needs.profile.outputs.{role}_target_sha256'), digest)
    script = script.replace(action_expression('github.workflow_sha'), control)
    script = script.replace(action_expression('needs.profile.outputs.bind_inputs_sha256'), bind_digest)
    sentinel = tmp_path / 'privileged-output'
    result = subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', script + '\n: > "$Q_PRIVILEGED_SENTINEL"\n'],
        cwd=tmp_path, env=dict(os.environ, SERIES='26.7', Q_PRIVILEGED_SENTINEL=str(sentinel)),
        capture_output=True, text=True)
    assert (result.returncode == 0) is (changed is None), result.stderr
    assert sentinel.exists() is (changed is None)


@pytest.mark.parametrize('component,changed', [
    ('bind_runtime', None), ('bind_plugin', None), ('bind_plugin', 'creator'),
    ('bind_plugin', 'reader'), ('bind_plugin', 'runtime'), ('bind_plugin', 'reader-file'),
    ('bind_plugin', 'control'), ('bind_plugin', 'profile-control'), ('bind_plugin', 'head'),
])
def test_sealed_component_bundle_preflight_precedes_privileged_output(tmp_path, monkeypatch, component, changed):
    components = load_module('sealed_components', 'bind/shared/components.py')
    from package_fixtures import (PLUGIN_PACKAGE, creator_identity, make_bind_component, make_ports_recipe,
                                  make_tools_repository, write_resolved_target, write_json)
    profile = upstream_profile('26.7')
    upstream = write_json(tmp_path / 'upstream.json', profile)
    ports, approval_text, pin = make_ports_recipe(tmp_path / 'ports')
    tools = make_tools_repository(tmp_path / 'tools', profile)
    approval = tmp_path / 'runtime-approval.mk'
    approval.write_text(PLUGIN_PACKAGE + approval_text)
    package = tmp_path / 'plugin-package.mk'
    package.write_text(PLUGIN_PACKAGE)
    recipe = tmp_path / 'build-wrapper.sh'
    recipe.write_text('selected build wrapper')
    creator = write_resolved_target(tmp_path / 'creator.json', profile)
    reader = write_resolved_target(tmp_path / 'reader.json', profile, role='reader')
    repository = init_repository(tmp_path / 'checkout')
    source = commit(repository, {
        '.resolver-plugins/upstream.json': upstream.read_text(),
        'dns/bind/Makefile': package.read_text(),
        '.github/ci/bind/runtime/build-bind920.sh': recipe.read_text(),
        '.github/ci/bind/plugin/build-os-bind-rp.sh': recipe.read_text(),
    }, 'selected release source without runtime approval')
    control = commit(repository, {'dns/bind/Makefile': approval.read_text()}, 'trusted control approval')
    runtime = make_bind_component('bind_runtime', tmp_path / 'runtime', series='26.7',
        creator=creator_identity(), source_commit='1' * 40, control_commit='2' * 40,
        profile=profile, version='9.20.26_2', bind_profile=pin)
    trusted = tmp_path / 'trusted'
    with monkeypatch.context() as boundary:
        native_run = components.subprocess.run
        boundary.setattr(components.subprocess, 'run', lambda command, **kw:
            SimpleNamespace(stdout='[]') if command[0] == 'gh' else native_run(command, **kw))
        resolve = components.bind920_profile.resolve_profile
        boundary.setattr(components.bind920_profile, 'resolve_profile', lambda record: resolve(record, repository=ports))
        boundary.setattr(components.releases, 'snapshot_release', lambda *a:
            SimpleNamespace(existed=True, draft=False, directory=runtime))
        result = components.prepare_selection(trusted, component=component, series='26.7', mode='production',
            control_commit=control, source_commit=source if component == 'bind_plugin' else None,
            profile_commit=source, upstream=upstream, runtime_approval=approval, tools_repository=tools,
            package_definition=package if component == 'bind_plugin' else None, recipe=recipe,
            repository='example/distribution', intake_directory=tmp_path / 'intake',
            creator_target=creator, reader_target=reader)
    assert result['producer_action'] == 'build'
    digest, profile_control = result['selection_sha256'], control
    if changed in ('creator', 'reader', 'reader-file'):
        role = 'reader' if changed == 'reader-file' else changed
        record = json.loads((trusted / f'{role}-target.json').read_text())
        record['package_creator']['version'] = '9.9.9'
        write_json(trusted / f'{role}-target.json', record)
        components.target_pkg.load_target(trusted / f'{role}-target.json', '26.7',
                                          upstream=trusted / 'upstream.json', role=role)
        if changed == 'creator':
            inputs = json.loads((trusted / 'producer-inputs.json').read_text())
            inputs['package_creator'] = record['package_creator']
            write_json(trusted / 'producer-inputs.json', inputs)
    elif changed == 'runtime':
        replacement = make_bind_component('bind_runtime', tmp_path / 'replacement', series='26.7',
            creator=creator_identity(version='2.4.0'), source_commit='5' * 40, control_commit='6' * 40,
            profile=profile, version='9.20.26_2', bind_profile=pin)
        write_json(trusted / 'runtime-selection.json', components.component_receipt(replacement, 'bind_runtime', '26.7'))
    if changed in ('creator', 'reader', 'runtime'):
        selection = json.loads((trusted / 'selection.json').read_text())
        if changed == 'creator':
            selection['expected_fingerprint'] = components.plugin_build_fingerprint(inputs)
        selection['files'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in trusted.iterdir() if p.name != 'selection.json'}
        write_json(trusted / 'selection.json', selection)
        components.load_selection(trusted, component=component, series='26.7',
                                  selection_sha256=hashlib.sha256((trusted / 'selection.json').read_bytes()).hexdigest())
    if changed in ('control', 'profile-control', 'head'):
        foreign = commit(repository, {'other.txt': 'another valid control'}, 'foreign control')
        if changed == 'control':
            selection = json.loads((trusted / 'selection.json').read_text())
            selection['control_commit'] = foreign
            profile_control = foreign
            digest = components.seal_selection(trusted, selection)
        elif changed == 'profile-control':
            profile_control = foreign
            git(repository, 'checkout', control)
    sentinel = tmp_path / 'privileged-output'
    command = [sys.executable, '-I', str(REPOSITORY_ROOT / '.github/ci/bind/shared/components.py'),
               'validate', '--component', component, '--series', '26.7', '--trusted-directory', str(trusted),
               '--selection-sha256', digest, '--expected-control-commit', control,
               '--profile-control-commit', profile_control]
    checked = subprocess.run(['sh', '-c', shlex.join(command) + ' && : > ' + shlex.quote(str(sentinel))],
                             cwd=repository, capture_output=True, text=True)
    assert (checked.returncode == 0) is (changed is None), checked.stderr
    assert sentinel.exists() is (changed is None)
    if changed is None:
        producer = tmp_path / 'producer'
        producer.mkdir()
        inputs = json.loads((trusted / 'producer-inputs.json').read_text())
        if component == 'bind_runtime':
            from package_fixtures import bind_records
            for name in bind_records():
                filename = bind_records()[name]['filename']
                shutil.copyfile(runtime / filename, producer / filename)
            write_json(producer / 'bind920-provenance.json', components.bind920_profile.build_provenance(
                pin, '26.7', '15.1', 'x86_64', creator_identity(), bind_records(),
                recipe_sha256=inputs['build_recipe_sha256']))
        else:
            fresh = make_bind_component('bind_plugin', tmp_path / 'fresh-plugin', series='26.7',
                creator=creator_identity(), source_commit=source, control_commit=control,
                profile=profile, version='26.7_1')
            for name in ('os-bind-rp-26.7_1.pkg', 'build-metadata.txt'):
                shutil.copyfile(fresh / name, producer / name)
            shutil.copyfile(trusted / 'producer-inputs.json', producer / 'plugin-build-inputs.json')
        original = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in producer.iterdir()}
        selection = components.load_selection(trusted, component=component, series='26.7',
                                             selection_sha256=result['selection_sha256'])
        candidate = components.validate_candidate(producer, selection, trusted, pkg_command=None)
        assert candidate['build_fingerprint'] == result['expected_fingerprint']
        assert candidate['control_commit'] == control
        assert candidate['packages'] == {name: digest for name, digest in original.items() if name.endswith('.pkg')}

def release_workflow(component):
    filename = 'bind-runtime-release.yml' if component == 'bind_runtime' else 'bind-plugin-release.yml'
    return (REPOSITORY_ROOT / '.github/workflows' / filename).read_text()


def select_cli(directory, component, context, document, paths=None, *, pr_base=None, files=None, repository=None):
    directory.mkdir(parents=True, exist_ok=True)
    event, refs, output, github_output = (directory / name for name in ('event.json', 'refs.json', 'selected.json', 'github-output'))
    event.write_text(json.dumps(document))
    refs.write_text(json.dumps(['refs/heads/release/bind-rp/26.7', 'refs/heads/release/bind-rp/26.10',
                               'refs/heads/release/bind-rp/26.9', 'refs/heads/release/bind-rp/not-a-series']))
    environment = dict(os.environ, GITHUB_EVENT_NAME=context['name'], GITHUB_REF=context['ref'],
                       GITHUB_SHA=context['sha'], GITHUB_WORKFLOW_SHA=context['workflow_sha'],
                       GITHUB_REPOSITORY='example/plugins')
    arguments = [sys.executable, '-I', str(REPOSITORY_ROOT / '.github/ci/bind/shared/selection.py'),
                 'select-event', '--component', component, '--event-file', str(event), '--refs-file', str(refs),
                 '--output', str(output), '--github-output', str(github_output)]
    if paths is not None:
        changed = directory / 'paths.json'
        changed.write_text(json.dumps(paths))
        arguments.extend(['--changed-paths-file', str(changed)])
    if pr_base is not None or files is not None:
        gh = directory / 'gh'
        gh.write_text('#!/bin/sh\nset -eu\ncase "$*" in\n'
            '  "api --paginate --slurp repos/example/plugins/pulls/51/files?per_page=100") cat "$Q_GH_FILES";;\n'
            '  "api repos/example/plugins/pulls/51 --jq .base.ref") printf "%s\\n" "$Q_PR_BASE";;\n'
            '  *) exit 64;;\nesac\n')
        gh.chmod(0o755)
        remote = directory / 'remote-files.json'
        remote.write_text(json.dumps(files))
        environment.update(PATH=str(directory) + os.pathsep + environment['PATH'],
                           Q_PR_BASE=pr_base or '', Q_GH_FILES=str(remote))
    completed = subprocess.run(arguments, cwd=repository, env=environment, text=True, capture_output=True)
    selected = json.loads(output.read_text()) if output.exists() else None
    serialized = dict(line.split('=', 1) for line in github_output.read_text().splitlines()) if github_output.exists() else {}
    return completed, selected, serialized


def test_independent_event_routing_uses_exact_source_and_control(tmp_path):
    push = dict(name='push', ref='refs/heads/master', sha='a' * 40, workflow_sha='b' * 40)
    merge = dict(push, name='pull_request_target')
    dispatch = dict(push, name='workflow_dispatch')
    merged = dict(number=51, pull_request=dict(merged=True, base=dict(ref='release/bind-rp/26.7'), merge_commit_sha='c' * 40))
    scenarios = [
        (push, {'before': 'd' * 40}, ['.github/ci/shared/target_pkg.py'], None, False, True, 'production', '26.10', 'refs/heads/release/bind-rp/26.10', 'a' * 40),
        (push, {'before': 'd' * 40}, ['.resolver-plugins/upstream.json'], None, False, False, 'production', None, None, None),
        (push, {'before': 'd' * 40}, ['dns/bind/tests/test_watcher.py'], None, False, False, 'production', None, None, None),
        (merge, merged, ['.resolver-plugins/upstream.json'], None, False, True, 'production', '26.7', 'c' * 40, 'b' * 40),
        (merge, merged, ['docs/building.md'], None, False, False, 'production', None, None, None),
        (merge, dict(merged, pull_request=dict(merged['pull_request'], merged=False)), [], None, False, False, 'production', None, None, None),
        (dispatch, {'inputs': dict(mode='production', series='26.7')}, [], None, True, True, 'production', '26.7', 'refs/heads/release/bind-rp/26.7', 'a' * 40),
        (dispatch, {'inputs': dict(mode='development', series='26.7', pull_number='51')}, [], 'master', True, True, 'development', '26.7', 'refs/heads/release/bind-rp/26.7', 'a' * 40),
        (dispatch, {'inputs': dict(mode='development', series='26.7', pull_number='51')}, [], 'release/bind-rp/26.7', True, True, 'development', '26.7', 'refs/pull/51/head', 'a' * 40),
    ]
    for index, (context, document, paths, pr_base, runtime, plugin, mode, series, profile, control) in enumerate(scenarios):
        for component, release in [('bind_runtime', runtime), ('bind_plugin', plugin)]:
            completed, selected, serialized = select_cli(tmp_path / f'{index}-{component}', component,
                                                        context, document, paths, pr_base=pr_base)
            assert completed.returncode == 0, completed.stderr
            expected = dict(mode=mode, series=series if release else None,
                            source_ref=profile if release and component == 'bind_plugin' else None,
                            profile_ref=profile if release else None, control_ref=control if release else None,
                            prerelease_tag=f'pr-51-26.7-{component.replace("_", "-")}' if mode == 'development' else None,
                            pull_number='51' if mode == 'development' else None, release=release)
            assert selected == expected
            assert serialized == {key: str(value).lower() if isinstance(value, bool) else value or ''
                                  for key, value in expected.items()}


@pytest.mark.parametrize('change,expected', [
    ('initialize', (False, False)), ('runtime', (True, False)),
    ('plugin', (False, True)), ('both', (True, True)), ('unchanged', (False, False)),
])
def test_git_package_delta_routes_approval_and_plugin_independently(tmp_path, change, expected):
    from package_fixtures import PLUGIN_PACKAGE, runtime_approval
    repository = init_repository(tmp_path / 'history')
    before_definition = PLUGIN_PACKAGE + ('' if change == 'initialize' else runtime_approval())
    before = commit(repository, {'dns/bind/Makefile': before_definition}, 'before reviewed package change')
    after_definition = PLUGIN_PACKAGE + runtime_approval()
    if change in ('runtime', 'both'):
        after_definition = PLUGIN_PACKAGE + runtime_approval('9.20.27', '2' * 40)
    if change in ('plugin', 'both'):
        after_definition = after_definition.replace('>= 9.20.26', '>= 9.20.27')
    after = commit(repository, {'dns/bind/Makefile': after_definition, 'docs/note': 'reviewed note'}, 'after reviewed change')
    context = dict(name='push', ref='refs/heads/master', sha=after, workflow_sha=after)
    for component, release in zip(('bind_runtime', 'bind_plugin'), expected):
        paths = trigger_paths(release_workflow(component), 'push')
        admitted = False
        for pattern in paths:
            if fnmatchcase('dns/bind/Makefile', pattern.removeprefix('!')):
                admitted = not pattern.startswith('!')
        assert admitted
        completed, selected, serialized = select_cli(tmp_path / component, component, context,
            {'before': before}, repository=repository)
        assert completed.returncode == 0, completed.stderr
        profile = 'refs/heads/release/bind-rp/26.10' if release else None
        wanted = dict(mode='production', series='26.10' if release else None, release=release,
            source_ref=profile if component == 'bind_plugin' else None, profile_ref=profile,
            control_ref=after if release else None, prerelease_tag=None, pull_number=None)
        assert selected == wanted
        assert serialized == {key: str(value).lower() if isinstance(value, bool) else value or ''
                              for key, value in wanted.items()}


def test_closed_pr_collection_includes_every_page_and_renamed_inputs(tmp_path):
    context = dict(name='pull_request_target', ref='refs/heads/master', sha='a' * 40, workflow_sha='b' * 40)
    document = dict(number=51, pull_request=dict(merged=True, base=dict(ref='release/bind-rp/26.7'),
                                               merge_commit_sha='c' * 40))
    pages = [[dict(filename='docs/unrelated.md')],
             [dict(filename='docs/renamed-plugin.py', previous_filename='dns/bind/src/etc/rc.d/named'),
              dict(filename='docs/building.md')]]
    completed, selected, _ = select_cli(tmp_path, 'bind_plugin', context, document, files=pages)
    assert completed.returncode == 0, completed.stderr
    assert selected == dict(mode='production', series='26.7',
                            source_ref='c' * 40,
                            profile_ref='c' * 40, control_ref='b' * 40,
                            prerelease_tag=None, pull_number=None, release=True)


@pytest.mark.parametrize('component', ['bind_runtime', 'bind_plugin'])
def test_production_dispatch_cannot_select_release_branch_control(tmp_path, component):
    context = dict(name='workflow_dispatch', ref='refs/heads/release/bind-rp/26.7',
                   sha='a' * 40, workflow_sha='b' * 40)
    completed, selected, serialized = select_cli(tmp_path, component, context,
                                                {'inputs': dict(mode='production', series='26.7')}, [])
    assert completed.returncode != 0
    assert selected is None and serialized == {}


@pytest.mark.parametrize('path,push,merge', [
    ('.github/ci/bind/runtime/build-bind920.sh', (False, False), (False, False)),
    ('.github/ci/bind/plugin/build-os-bind-rp.sh', (False, True), (False, False)),
    ('dns/bind/Makefile', (True, True), (False, True)),
    ('dns/bind/src/etc/rc.d/named', (False, True), (False, True)),
    ('Mk/plugins.mk', (False, True), (False, True)),
    ('Scripts/version.sh', (False, True), (False, True)),
    ('Templates/plugin.in', (False, True), (False, True)),
    ('.github/ci/bind/shared/components.py', (False, True), (False, False)),
    ('.github/ci/shared/new-helper.py', (False, True), (False, False)),
    ('.resolver-plugins/upstream.json', (False, False), (False, True)),
    ('docs/package-repository/resolver-plugins.pub', (False, True), (False, False)),
    ('scripts/install-os-bind-rp.sh', (False, True), (False, False)),
    ('.github/workflows/bind-runtime-release.yml', (False, False), (False, False)),
    ('.github/workflows/bind-plugin-release.yml', (False, True), (False, False)),
    ('dns/bind/tests/test_watcher.py', (False, False), (False, False)),
    ('.github/ci/ci-tests/test_package_release_workflow.py', (False, False), (False, False)),
    ('docs/building.md', (False, False), (False, False)),
    ('net/dhcp-interface-ha/Makefile', (False, False), (False, False)),
    ('.github/ci/ha_dhcp/new-helper.py', (False, False), (False, False)),
    ('.github/workflows/ha-dhcp-interface-release.yml', (False, False), (False, False)),
    ('.github/ci/sync_upstream.py', (False, False), (False, False)),
    ('.github/ci/bind/new-helper.py', (False, False), (False, False)),
])
def test_actual_filters_route_independent_input_classes(path, push, merge):
    selection = load_module('independent_workflow_selection', 'bind/shared/selection.py')
    for component, expected_push, expected_merge in zip(('bind_runtime', 'bind_plugin'), push, merge):
        workflow = release_workflow(component)
        for event, expected in [('push', expected_push), ('pull_request_target', expected_merge)]:
            selected = False
            for pattern in trigger_paths(workflow, event):
                if fnmatchcase(path, pattern.removeprefix('!')):
                    selected = not pattern.startswith('!')
            assert selected is expected, (component, event, path)
        assert selection.watches(component, path) is expected_push

def action_expression(value):
    return '$' + '{{ ' + value + ' }}'


@pytest.mark.parametrize('filename', ['bind-runtime-release.yml', 'bind-plugin-release.yml', 'ha-dhcp-interface-release.yml'])
def test_release_authority_is_limited_to_reviewed_jobs(filename):
    workflow = (REPOSITORY_ROOT / '.github/workflows' / filename).read_text()
    header = workflow.split('jobs:\n', 1)[0]
    assert not re.search(r'\bsecrets\b', '\n'.join(re.findall(r'\$\{\{(.*?)\}\}', header, re.DOTALL)))
    global_permissions = {'contents': 'read'} | ({} if filename.startswith('ha-') else {'pull-requests': 'read'})
    assert_permissions(workflow, global_permissions)
    concurrency = re.search(r'^concurrency:\n((?:  [^\n]*\n)*)', header, re.MULTILINE)[1]
    assert dict(line.strip().split(': ', 1) for line in concurrency.splitlines()) == {
        'group': 'package-release', 'cancel-in-progress': 'false', 'queue': 'max'}
    assert_action_release_tags(workflow)
    for name, job in workflow_jobs(workflow).items():
        expected_secrets = {'RP_PKG_SIGNING_KEY'} if name == 'sign' else (
            {'RP_DISTRIBUTION_APP_PRIVATE_KEY'} if name in ('publish', 'retention') else set())
        expressions = '\n'.join(re.findall(r'\$\{\{(.*?)\}\}', job, re.DOTALL))
        references = re.findall(r'\bsecrets\.([A-Za-z_]\w*)', expressions)
        assert not re.search(r'^\s+secrets:', job, re.MULTILINE), name
        assert len(re.findall(r'\bsecrets\b', expressions)) == len(references), name
        assert set(references) == expected_secrets, name
        if filename.startswith('ha-'):
            permissions = {'contents': 'write'} if name == 'source-release' else None
        elif name == 'publish-development':
            permissions = {'contents': 'write', 'pull-requests': 'read'}
        elif name == 'source-release':
            permissions = {'contents': 'write'}
        else:
            permissions = {'contents': 'read'} if name in (
                'producer', 'sign', 'verify', 'publish', 'verify-published', 'retention', 'verify-development') else None
        assert_permissions(job, permissions, indent=4)
        if 'uses: actions/checkout@' in job:
            assert_checkout_credentials(job)
        for step in workflow_steps(job):
            if 'uses: actions/create-github-app-token@' in step:
                fields = dict(re.findall(r'^          ([\w-]+): ([^\n]+)$', step, re.MULTILINE))
                assert fields['owner'] == 'resolver-plugins'
                assert fields['repositories'] == 'repository'
                assert {key: value for key, value in fields.items() if key.startswith('permission-')} == {'permission-contents': 'write'}


def condition_allows(job, context):
    match = re.search(r'^    if: ([^\n]+)\n((?:      [^\n]*\n)*)', job, re.MULTILINE)
    condition = ' '.join(line.strip() for line in match[2].splitlines()) if match[1] in ('>-', '|') else match[1]
    for key, value in sorted(context.items(), key=lambda item: len(item[0]), reverse=True):
        condition = re.sub(r'\b' + re.escape(key) + r'\b', repr(value), condition)
    condition = re.sub(r'\btrue\b', 'True', condition).replace('&&', ' and ').replace('||', ' or ')
    return eval(condition, {'__builtins__': {}, 'startsWith': str.startswith,
                            'format': lambda pattern, *args: pattern.format(*args)})


@pytest.mark.parametrize('component', ['bind_runtime', 'bind_plugin'])
def test_privileged_event_guards_do_not_trust_selector_mode(component):
    workflow_name = 'bind-runtime-release.yml' if component == 'bind_runtime' else 'bind-plugin-release.yml'
    base = {'needs.select.outputs.mode': 'production', 'github.event_name': 'push',
            'github.ref': 'refs/heads/master', 'inputs.mode': None,
            'github.event.pull_request.merged': False, 'github.event.pull_request.base.ref': None,
            'github.workflow_ref': f'example/plugins/.github/workflows/{workflow_name}@refs/heads/master',
            'github.repository': 'example/plugins'}
    merged = dict(base, **{'github.event_name': 'pull_request_target', 'github.ref': 'refs/heads/release/bind-rp/26.7',
                          'github.event.pull_request.merged': True, 'github.event.pull_request.base.ref': 'release/bind-rp/26.7'})
    scenarios = [
        (base, True),
        (dict(base, **{'github.ref': 'refs/heads/feature'}), False),
        (dict(base, **{'needs.select.outputs.mode': 'development'}), False),
        (dict(base, **{'github.event_name': 'workflow_dispatch', 'inputs.mode': 'production'}), True),
        (dict(base, **{'github.event_name': 'workflow_dispatch', 'inputs.mode': 'development'}), False),
        (dict(base, **{'github.event_name': 'workflow_dispatch', 'inputs.mode': 'production', 'github.ref': 'refs/heads/feature'}), False),
        (merged, component == 'bind_plugin'),
        (dict(merged, **{'github.event.pull_request.merged': False}), False),
        (dict(merged, **{'github.event.pull_request.base.ref': 'master'}), False),
        (dict(merged, **{'github.workflow_ref': f'example/plugins/.github/workflows/{workflow_name}@refs/heads/feature'}), False),
        (dict(base, **{'github.event_name': 'pull_request'}), False),
    ]
    jobs = workflow_jobs(release_workflow(component))
    privileged = ['sign', 'publish', 'retention'] + (['source-release'] if component == 'bind_plugin' else [])
    for name in privileged:
        for context, allowed in scenarios:
            assert condition_allows(jobs[name], context) is allowed, (component, name, context)


@pytest.mark.parametrize('component', ['bind_runtime', 'bind_plugin'])
def test_privileged_jobs_bind_control_and_immutable_owner_artifacts(component):
    jobs = workflow_jobs(release_workflow(component))
    privileged = ['sign', 'publish', 'retention'] + (['source-release'] if component == 'bind_plugin' else [])
    for name, job in jobs.items():
        if component == 'bind_runtime':
            for step in workflow_steps(job):
                if 'uses: actions/upload-artifact@' in step:
                    assert re.search(r'^          retention-days: ([^\n]+)$', step, re.MULTILINE)[1] == '30', name
        downloads = [step for step in workflow_steps(job) if 'uses: actions/download-artifact@' in step]
        if downloads:
            expected = {(action_expression('needs.profile.outputs.trusted_artifact_id'), 'artifacts/trusted')}
            if name != 'producer':
                owner = 'producer' if name in ('sign', 'verify-development', 'publish-development') else 'sign'
                output = 'producer_artifact_id' if owner == 'producer' else 'repository_artifact_id'
                directory = 'producer' if owner == 'producer' else 'repository'
                if component == 'bind_plugin' and name == 'publish-development':
                    expected.clear()
                expected.add((action_expression(f'needs.{owner}.outputs.{output}'),
                              f'artifacts/{action_expression("needs.select.outputs.series")}/{directory}'))
            assert {(re.search(r'^          artifact-ids: ([^\n]+)$', step, re.MULTILINE)[1],
                     re.search(r'^          path: ([^\n]+)$', step, re.MULTILINE)[1]) for step in downloads} == expected, name
        if name not in privileged:
            continue
        steps = workflow_steps(job)
        checkouts = [step for step in steps if 'uses: actions/checkout@' in step]
        assert checkouts and all(re.search(r'^          ref: ([^\n]+)$', step, re.MULTILINE)[1] ==
                                 action_expression('github.workflow_sha') for step in checkouts)
        preflights = [(index, step) for index, step in enumerate(steps) if '--expected-control-commit' in step_run(step)]
        assert len(preflights) == 1, name
        index, preflight = preflights[0]
        for field, accepted in [('if', ('true', 'success()')), ('continue-on-error', ('false',))]:
            declaration = re.search(r'^\s+' + field + r': ([^\n]+)$', preflight, re.MULTILINE)
            assert declaration is None or declaration[1] in accepted, (name, field)
        script = step_run(preflight).replace('\\\n', '')
        arguments = shlex.split(re.search(r'components\.py validate ([^\n]+)', script)[1])
        required = {'--component': component, '--trusted-directory': 'artifacts/trusted',
                    '--selection-sha256': '$RP_SELECTION_SHA256',
                    '--expected-control-commit': '$RP_EXPECTED_CONTROL_COMMIT',
                    '--profile-control-commit': '$RP_PROFILE_CONTROL_COMMIT'}
        if name != 'sign':
            required['--composition-sha256'] = '$RP_COMPOSITION_SHA256'
            assert '--repository-root' in arguments
        for flag, value in required.items():
            assert arguments[arguments.index(flag) + 1] == value, (name, flag)
        fields = dict(re.findall(r'^\s+(RP_[A-Z0-9_]+): ([^\n]+)$',
                                 job.split('    steps:\n', 1)[0] + preflight, re.MULTILINE))
        assert fields['RP_EXPECTED_CONTROL_COMMIT'] == action_expression('github.workflow_sha')
        assert fields['RP_PROFILE_CONTROL_COMMIT'] == action_expression('needs.profile.outputs.control_commit')
        assert fields['RP_SELECTION_SHA256'] == action_expression('needs.profile.outputs.selection_sha256')
        if name != 'sign':
            assert fields['RP_COMPOSITION_SHA256'] == action_expression('needs.sign.outputs.composition_sha256')
        effects = [position for position, step in enumerate(steps)
                   if 'secrets.' in step or 'release_channel.py publish-immutable' in step]
        assert effects and index < min(effects), name
    assert ('source-release' in jobs) is (component == 'bind_plugin')

def needs_jobs(job):
    value = re.search(r'^    needs: ([^\n]+)$', job, re.MULTILINE)[1]
    return {name.strip() for name in value.strip('[]').split(',')}


def vm_body(job):
    steps = [step for step in workflow_steps(job) if 'uses: vmactions/freebsd-vm@' in step]
    assert len(steps) == 1
    match = re.search(r'^          run: \|\n((?: {12}[^\n]*\n|\n)*)', steps[0], re.MULTILINE)
    return steps[0], textwrap.dedent(match[1])


@pytest.mark.parametrize('component', ['bind_runtime', 'bind_plugin'])
def test_native_qualification_is_mandatory_and_authentication_precedes_key_decode(component):
    jobs = workflow_jobs(release_workflow(component))
    for job, prerequisite in [('producer', 'test'), ('verify', 'sign'), ('publish', 'verify'),
                              ('verify-published', 'publish'), ('retention', 'verify-published'),
                              ('publish-development', 'verify-development')]:
        assert prerequisite in needs_jobs(jobs[job]), (component, job)
    if component == 'bind_plugin':
        assert 'verify-published' in needs_jobs(jobs['source-release'])
        assert 'source-release' in needs_jobs(jobs['retention'])
    for name in ('producer', 'sign', 'verify', 'verify-published', 'verify-development'):
        step, script = vm_body(jobs[name])
        assert re.search(r'^          release: ([^\n]+)$', step, re.MULTILINE)[1] == action_expression('needs.profile.outputs.freebsd_release')
        assert not re.search(r'^\s+continue-on-error: true$', jobs[name], re.MULTILINE)
        if name in ('verify', 'verify-published', 'verify-development'):
            arguments = shlex.split(re.search(r'verify-bind-release\.sh ([^\n]+)', script.replace('\\\n', ''))[1])
            assert arguments[arguments.index('--component') + 1] == component
            assert arguments[arguments.index('--mode') + 1] == ('public' if name == 'verify-published' else 'local')
            assert '--repository-root' in arguments and '--trusted-directory' in arguments and '--series' in arguments
            forwarded = re.search(r'^          envs: ([^\n]+)$', step, re.MULTILINE)[1].split()
            assert 'RP_SELECTION_SHA256' in forwarded
            if name != 'verify-development':
                assert 'RP_COMPOSITION_SHA256' in forwarded
    _, signer = vm_body(jobs['sign'])
    arguments = shlex.split(re.search(r'components\.py authenticate ([^\n]+)', signer.replace('\\\n', ''))[1])
    for flag, value in {'--component': component, '--selection-sha256': '$RP_SELECTION_SHA256',
                        '--trusted-directory': 'artifacts/trusted', '--pkg-command': '/usr/local/sbin/pkg-static',
                        '--directory': 'artifacts/$series/producer'}.items():
        assert arguments[arguments.index(flag) + 1] == value, flag
    assert signer.index('components.py authenticate') < signer.index('base64 -d')


@pytest.mark.parametrize('component', ['bind_runtime', 'bind_plugin'])
def test_pages_readiness_precedes_distribution_mutation(component):
    steps = workflow_steps(job_text(release_workflow(component), 'publish'))
    health = [(index, step_run(step)) for index, step in enumerate(steps) if 'pages-health' in step_run(step)]
    assert len(health) == 1
    index, script = health[0]
    assert 'https://resolver-plugins.github.io/repository/pages-health' in script
    assert 'resolver-plugins-pages-ok' in script
    mutation = next(position for position, step in enumerate(steps) if 'create-github-app-token@' in step)
    assert index < mutation


@pytest.mark.parametrize('component', ['bind_runtime', 'bind_plugin'])
@pytest.mark.parametrize('before,after,existing', [('open', 'open', False), ('closed', 'closed', True), ('open', 'closed', False)])
def test_preview_publication_preserves_other_tags_and_removes_closed_pr_output(tmp_path, component, before, after, existing):
    """Exercise the actual publisher shell; GH persistence and sealed intake are independent boundaries."""
    tag = f'pr-51-26.7-{component.replace("_", "-")}'
    peer = 'bind-plugin' if component == 'bind_runtime' else 'bind-runtime'
    protected = {f'pr-51-26.7-{peer}', 'pr-52-26.7-bind-plugin', 'pkg-26.7-bind-runtime', 'os-bind-rp-26.7-source'}
    initial = protected | ({tag} if existing else set())
    state = tmp_path / 'remote.json'
    state.write_text(json.dumps(dict(states=[before, after], calls=0, releases=sorted(initial),
                                    tags=sorted(initial), assets={name: {'original': name} for name in initial})))
    commands = tmp_path / 'commands'
    commands.mkdir()
    gh = commands / 'gh'
    gh.write_text(textwrap.dedent("""\
        #!/usr/bin/env python3
        import json, os, sys
        from pathlib import Path
        path=Path(os.environ['Q_REMOTE_STORE'])
        state=json.loads(path.read_text())
        args=sys.argv[1:]
        status=0
        if args[:1]==['api'] and args[-1:]==['.state']:
            index=min(state['calls'],len(state['states'])-1)
            print(state['states'][index])
            state['calls']+=1
        elif args[:2]==['release','view']:
            status=0 if args[2] in state['releases'] else 1
        elif args[:2]==['release','create']:
            tag=args[2]
            if tag in state['releases']:
                status=1
            else:
                state['releases'].append(tag)
                state['tags'].append(tag)
                state['assets'][tag]={}
        elif args[:2]==['release','upload']:
            files=[]
            for value in args[3:]:
                if value.startswith('-'):
                    break
                files.append(Path(value))
            state['assets'][args[2]]={file.name:file.read_bytes().hex() for file in files}
        elif args[:2]==['release','delete']:
            tag=args[2]
            if tag in state['releases']:
                state['releases'].remove(tag)
                state['assets'].pop(tag)
        elif args[:3]==['api','--method','DELETE']:
            tag=args[3].split('/tags/',1)[1]
            if tag in state['tags']:
                state['tags'].remove(tag)
        else:
            raise SystemExit('unexpected local GH command')
        path.write_text(json.dumps(state))
        raise SystemExit(status)
        """))
    gh.chmod(0o755)
    python = commands / 'python3'
    python.write_text('#!/bin/sh\ncase "$*" in\n'
        '  *"components.py validate"*) exit 0;;\n'
        '  *) exec "$Q_REAL_PYTHON" "$@";;\nesac\n')
    python.chmod(0o755)
    producer = tmp_path / 'artifacts/26.7/producer'
    producer.mkdir(parents=True)
    names = ('bind-tools-9.20.26_2.pkg', 'bind920-9.20.26_2.pkg', 'bind920-provenance.json') if component == 'bind_runtime' else ('os-bind-rp-devel-26.7_1.pkg', 'build-metadata.txt')
    for name in names:
        (producer / name).write_bytes(name.encode())
    expected_payload = {name: (producer / name).read_bytes().hex() for name in names}
    job = job_text(release_workflow(component), 'publish-development')
    script = next(step_run(step) for step in workflow_steps(job) if 'gh release upload' in step_run(step))
    script = script.replace('.github/ci/shared/release_channel.py', str(REPOSITORY_ROOT / '.github/ci/shared/release_channel.py'))
    result = subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', script], cwd=tmp_path, text=True, capture_output=True,
        env=dict(os.environ, PATH=str(commands) + os.pathsep + os.environ['PATH'], Q_REAL_PYTHON=sys.executable,
                 Q_REMOTE_STORE=str(state), TAG=tag, SERIES='26.7', PULL_NUMBER='51',
                 INPUT_SERIES='26.7', INPUT_PULL_NUMBER='51', GITHUB_REPOSITORY='example/plugins',
                 RP_SELECTION_SHA256='a' * 64))
    assert result.returncode == 0, result.stderr
    remote = json.loads(state.read_text())
    expected_tags = protected | ({tag} if before == after == 'open' else set())
    assert set(remote['releases']) == expected_tags
    assert set(remote['tags']) == expected_tags
    expected_assets = {name: {'original': name} for name in protected}
    if tag in expected_tags:
        expected_assets[tag] = expected_payload
    assert remote['assets'] == expected_assets
