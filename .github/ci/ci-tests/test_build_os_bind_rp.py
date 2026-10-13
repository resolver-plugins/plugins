"""Plugin source/build boundaries; native pkg/signature evidence remains separate."""
from git_fixtures import *
from module_fixtures import load_module
from package_fixtures import (
    creator_identity, make_bind_component, make_ports_recipe,
    make_tools_repository, runtime_approval, write_json, write_resolved_target,
    PLUGIN_PRE_INSTALL,
)

plugin = load_module('plugin_release', 'bind/plugin/plugin_release.py')
components = plugin.components
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def native_command_fixture(path):
    # Synthetic archives/catalogue and installed DB; production validators run.
    path.write_text(textwrap.dedent(r'''
        #!/usr/bin/env python3
        import hashlib, json, os, pathlib, re, shutil, sys, tarfile
        from urllib.parse import unquote, urlparse
        args = sys.argv[1:]
        log = pathlib.Path(os.environ['PKG_CALL_LOG'])
        with log.open('a') as stream:
            stream.write(json.dumps(args) + '\n')
        state_path = pathlib.Path(os.environ['P_PKG_STATE'])
        state = json.loads(state_path.read_text()) if state_path.exists() else {}
        options = {}
        while args and args[0] in ('-o', '-R'):
            flag, value = args[:2]
            if flag == '-o':
                key, value = value.split('=', 1)
                options[key] = value
            args = args[2:]
        command = args.pop(0)
        trusted = pathlib.Path(os.environ['RP_TRUSTED_DIRECTORY'])
        target = json.loads((trusted / 'creator-target.json').read_text())['package_creator']
        def save():
            state_path.write_text(json.dumps(state))
        def archive(path):
            try:
                return json.loads(pathlib.Path(path).read_text())
            except (UnicodeDecodeError, json.JSONDecodeError):
                with tarfile.open(path) as package:
                    return json.loads(package.extractfile('+MANIFEST').read())
        def dependencies(record):
            if record['name'] == 'bind920':
                return [('bind-tools', 'dns/bind-tools', record['version'])]
            return [(name, edge['origin'], edge['version']) for name, edge in record.get('deps', {}).items()]
        def query(record, fmt):
            if fmt == '%Fp|%Fs':
                print('\n'.join(path + '|' + checksum for path, checksum in
                    record.get('files', {'/fixture/' + record['name']: '1$' + 'a' * 64}).items()))
            elif fmt in ('%dn', '%dn\t%do\t%dv'):
                print('\n'.join(dep[0] if fmt == '%dn' else '\t'.join(dep)
                    for dep in dependencies(record)), end='')
                if dependencies(record):
                    print()
            else:
                for key, value in [('n', record['name']), ('v', record['version']),
                                   ('o', record['origin']), ('q', record['abi'])]:
                    fmt = fmt.replace('%' + key, value)
                print(fmt)
        def catalogue():
            conf = pathlib.Path(options['REPOS_DIR']) / 'catalogue.conf'
            url = json.loads(re.search(r'url: ("[^"]+")', conf.read_text())[1])
            root = pathlib.Path(unquote(urlparse(url).path))
            return [p for p in root.glob('*.pkg') if p.name not in ('packagesite.pkg', 'data.pkg')]
        if command == '-v':
            print(target['version'].split('_')[0])
        elif command == 'update':
            pass
        elif command == 'config':
            print(target['abi'])
        elif command == 'version':
            def order(value):
                base, _, revision = value.partition('_')
                return tuple(map(int, base.split('.'))), int(revision or '0')
            left, right = map(order, args[-2:])
            print('<' if left < right else '>' if left > right else '=')
        elif command == 'lock':
            if '-l' in args:
                if state.get('locked'):
                    print('pkg-' + target['version'])
            else:
                state['locked'] = True
                save()
        elif command == 'unlock':
            state['locked'] = False
            save()
        elif command == 'which':
            print('pkg-' + target['version'])
        elif command == 'check':
            assert all(name in state for name in args[1:]), args
        elif command == 'install':
            pass
        elif command == 'add':
            for name in args[1:]:
                record = archive(name)
                state[record['name']] = record
            save()
        elif command == 'fetch':
            output = pathlib.Path(args[args.index('-o') + 1])
            output.mkdir(exist_ok=True)
            if args[args.index('-r') + 1] == 'catalogue':
                for source in catalogue():
                    shutil.copyfile(source, output / source.name)
            else:
                (output / ('pkg-' + target['version'] + '.pkg')).write_text(json.dumps(target))
        elif command == 'rquery':
            if args[args.index('-r') + 1] == 'catalogue':
                for source in catalogue():
                    query(archive(source), args[-1])
            elif '%n = pkg' in args:
                query(target, args[-1])
            else:
                print('26.1.11_10')
        elif command == 'info':
            record = archive(args[args.index('-F') + 1])
            print(json.dumps(record))
        elif command == 'query':
            if '%k' in args:
                print('1' if state.get('locked') else '0')
            elif '-F' in args:
                query(archive(args[args.index('-F') + 1]), args[-1])
            else:
                name = args[args.index('-e') + 1].removeprefix('%n = ')
                query(state[name], args[-1])
        else:
            raise AssertionError((command, args))
    ''').lstrip())
    path.chmod(0o755)
    return path


def build_fixture(tmp_path):
    root = init_repository(tmp_path / 'build')
    # Isolate command/CI copies while shared providers are owned by other workers.
    shutil.copytree(REPOSITORY_ROOT / '.github/ci', root / '.github/ci',
                    ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache'))
    for name in ('dns/bind', 'Mk', 'Scripts', 'Templates', 'docs/package-repository'):
        shutil.copytree(REPOSITORY_ROOT / name, root / name, ignore=shutil.ignore_patterns('work'))
    core_commit = create_core_repository(tmp_path / 'core')
    upstream = upstream_profile(core_commit=core_commit)
    (root / '.resolver-plugins').mkdir()
    (root / '.resolver-plugins/upstream.json').write_text(json.dumps(upstream))
    makefile = root / 'dns/bind/Makefile'
    selected_definition = re.sub(r'^RP_BIND_.*\n', '', makefile.read_text(), flags=re.M)
    selected_definition = re.sub(r'^PLUGIN_VERSION=.*$', 'PLUGIN_VERSION=\t26.1',
        re.sub(r'^PLUGIN_REVISION=.*$', 'PLUGIN_REVISION=\t1', selected_definition, flags=re.M), flags=re.M)
    if 'PLUGIN_MANIFEST_DEPENDS=' not in selected_definition:
        selected_definition += '\nPLUGIN_MANIFEST_DEPENDS= opnsense: { version: "26.1.11_10", origin: "opnsense/opnsense" }\n'
    makefile.write_text(selected_definition)
    (root / 'dns/bind/+PRE_INSTALL.pre').write_text(PLUGIN_PRE_INSTALL)
    git(root, 'add', 'dns/bind', 'Mk', 'Scripts', 'Templates', '.resolver-plugins')
    git(root, 'commit', '-m', 'selected package inputs')
    source_commit = git(root, 'rev-parse', 'HEAD')
    ports, approval, pin = make_ports_recipe(tmp_path / 'ports')
    tools = make_tools_repository(tmp_path / 'tools', upstream)
    # Master control intentionally differs; source has no RP approval block.
    makefile.write_text(selected_definition.replace('PLUGIN_VERSION=\t26.1', 'PLUGIN_VERSION=\t1.99')
        .replace('>= 9.20.26', '>= 9.20.27') + approval)
    git(root, 'add', 'dns/bind/Makefile', '.github/ci/bind/plugin/build-os-bind-rp.sh')
    git(root, 'commit', '-m', 'immutable control approval')
    control_commit = git(root, 'rev-parse', 'HEAD')
    runtime = make_bind_component('bind_runtime', tmp_path / 'runtime', series='26.1',
        creator=creator_identity('FreeBSD:14:amd64'), source_commit='b' * 40,
        control_commit='c' * 40, profile=upstream, version='9.20.26_2', bind_profile=pin)
    role_root = tmp_path / 'roles'
    role_root.mkdir()
    creator = write_resolved_target(role_root / 'creator.json', upstream)
    reader = write_resolved_target(role_root / 'reader.json', upstream, role='reader')
    package = tmp_path / 'source-package.mk'
    package.write_text(selected_definition)
    trusted = tmp_path / 'trusted'
    def frozen_component(repository, kind, series, output):
        shutil.copytree(runtime, output)
        return components.component_receipt(runtime, kind, series)
    resolve = components.bind920_profile.resolve_profile
    with patch.object(components.bind920_profile, 'resolve_profile', side_effect=lambda record: resolve(record, repository=ports)), \
            patch.object(components, 'fetch_component', side_effect=frozen_component), \
            patch.object(components, 'fetch_cached_component', return_value=None):
        result = components.prepare_selection(trusted, component='bind_plugin', series='26.1', mode='production',
            control_commit=control_commit, profile_commit=source_commit, source_commit=source_commit,
            upstream=root / '.resolver-plugins/upstream.json', runtime_approval=makefile, package_definition=package,
            tools_repository=tools, recipe=root / '.github/ci/bind/plugin/build-os-bind-rp.sh',
            repository='example/distribution', intake_directory=tmp_path / 'intake',
            creator_target=creator, reader_target=reader)
    digest = result['selection_sha256']
    pkg = native_command_fixture(tmp_path / 'pkg-fixture')
    make = tmp_path / 'make-fixture'
    make.write_text(textwrap.dedent('''
        #!/usr/bin/env python3
        import hashlib, io, json, os, pathlib, sys, tarfile
        args = sys.argv[1:]
        sys.path.insert(0, str(pathlib.Path(args[1]).parents[1] / '.github/ci/ci-tests'))
        from package_fixtures import BIND_COLLISION_PATH, plugin_manifest
        assert args[0] == '-C' and args[-1] == 'package'
        assert '_PLUGIN_DEVEL=' in args
        assert 'PLUGIN_HASH=' + os.environ['SOURCE_COMMIT'] in args
        state = json.loads(pathlib.Path(os.environ['P_PKG_STATE']).read_text())
        assert state['locked'] and state['pkg']['version'] == '2.3.1_1'
        output = pathlib.Path(args[1]) / 'work/pkg'
        output.mkdir(parents=True)
        bind = pathlib.Path(args[1]) / 'src/etc/inc/plugins.inc.d/bind.inc'
        record = dict(name='os-bind-rp', version='26.1_1', origin='opnsense/os-bind-rp',
            abi='FreeBSD:14:amd64')
        record.update(plugin_manifest((pathlib.Path(args[1]) / 'Makefile').read_text(),
            built_runtime_version=state['bind920']['version'], payload=bind.read_bytes()))
        version = json.dumps(dict(product_hash=os.environ['SOURCE_COMMIT'])).encode()
        record['files']['/usr/local/opnsense/version/bind-rp'] = '1$' + hashlib.sha256(version).hexdigest()
        with tarfile.open(output / 'os-bind-rp-26.1_1.pkg', 'w') as package:
            for name, data in [('+MANIFEST', json.dumps(record).encode()),
                               (BIND_COLLISION_PATH.lstrip('/'), bind.read_bytes()),
                               ('usr/local/opnsense/version/bind-rp', version)]:
                entry = tarfile.TarInfo(name)
                entry.size = len(data)
                package.addfile(entry, io.BytesIO(data))
    ''').lstrip())
    make.chmod(0o755)
    environment = dict(os.environ, RP_TRUSTED_DIRECTORY=str(trusted), RP_RUNTIME_DIRECTORY=str(runtime),
        RP_SELECTION_SHA256=digest, SOURCE_COMMIT=source_commit, PKG_COMMAND=str(pkg), RP_PKG_STATIC_COMMAND=str(pkg),
        MAKE_COMMAND=str(make), PYTHON_COMMAND='python3', PKG_CALL_LOG=str(tmp_path / 'pkg-calls.jsonl'),
        P_PKG_STATE=str(tmp_path / 'pkg-state.json'), OPNSENSE_CORE_REPOSITORY=str(tmp_path / 'core'),
        GIT_CONFIG_GLOBAL=str(tmp_path / 'gitconfig'), PKG_REPOS_DIR=str(tmp_path / 'repos'),
        PKG_FINGERPRINTS_DIR=str(tmp_path / 'fingerprints'))
    environment.pop('RP_OPNSENSE_SNAPSHOT', None)
    environment.pop('RP_PLUGIN_DEVEL', None)
    return root, trusted, runtime, environment


def test_build_wrapper_creates_package_and_metadata_for_26_1(tmp_path):
    root, trusted, runtime, environment = build_fixture(tmp_path)
    output = tmp_path / 'producer'
    result = subprocess.run([root / '.github/ci/bind/plugin/build-os-bind-rp.sh', '26.1', output],
                            cwd=root, env=environment, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert {path.name for path in output.iterdir()} == {
        'os-bind-rp-26.1_1.pkg', 'build-metadata.txt', 'plugin-build-inputs.json'}
    metadata = dict(line.split('=', 1) for line in (output / 'build-metadata.txt').read_text().splitlines())
    metadata.pop('uname')
    upstream = json.loads((trusted / 'upstream.json').read_text())
    assert metadata == {
        'series': '26.1', 'pkg_abi': 'FreeBSD:14:amd64', 'bind920': '9.20.26_2',
        'bind_source': 'resolver', 'opnsense': '26.1.11_10', 'opnsense_core_commit': upstream['core_commit'],
        'upstream_commit': '1' * 40, 'core_commit': upstream['core_commit'], 'tools_tag': '26.1.11',
        'freebsd_release': '14.3', 'source_commit': environment['SOURCE_COMMIT'], 'pkg_creator': '2.3.1_1',
        'creator_release': '26.1.11', 'creator_ports_commit': '4' * 40, 'creator_recipe_path': 'ports-mgmt/pkg'}
    assert (root / 'dns/bind/Makefile').read_bytes() == (trusted / 'plugin-package.mk').read_bytes()
    assert b'RP_BIND_VERSION' not in (root / 'dns/bind/Makefile').read_bytes()
    assert b'PLUGIN_VERSION=\t1.99' in (trusted / 'runtime-approval.mk').read_bytes()
    assert '>= 9.20.27' in (trusted / 'runtime-approval.mk').read_text()
    assert (output / 'plugin-build-inputs.json').read_bytes() == (trusted / 'producer-inputs.json').read_bytes()
    state = json.loads(Path(environment['P_PKG_STATE']).read_text())
    assert state['locked'] and state['pkg']['version'] == '2.3.1_1'
    for archive in runtime.glob('bind*.pkg'):
        record = json.loads(archive.read_text())
        assert state[record['name']] == record
    calls = [json.loads(line) for line in Path(environment['PKG_CALL_LOG']).read_text().splitlines()]
    assert ['version', '-t', '9.20.26_2', '9.20.26'] in calls
    assert any(call[:2] == ['query', '-F'] and Path(call[2]).name == 'os-bind-rp-26.1_1.pkg'
               and call[-1] == '%Fp|%Fs' for call in calls)


def test_materialization_uses_exact_framework_and_removes_absent_control_paths(tmp_path):
    root = init_repository(tmp_path / 'source')
    upstream = tmp_path / 'upstream.json'
    upstream.write_text('{"selected":true}\n')
    source = commit(root, {
        '.resolver-plugins/upstream.json': upstream.read_text(),
        'dns/bind/Makefile': 'selected plugin\n', 'Mk/plugins.mk': 'selected framework\n',
        'Mk/defaults.mk': 'selected defaults\n', 'Mk/common.mk': 'selected common\n',
        'Templates/version': 'selected template\n',
    }, 'selected source')
    (root / 'Mk/devel.mk').write_text('stale development control\n')
    (root / 'Scripts').mkdir()
    (root / 'Scripts/stale.sh').write_text('stale script\n')
    (root / '.github/ci').mkdir(parents=True)
    (root / '.github/ci/control.py').write_text('trusted control\n')
    plugin.materialize_source(root, source, upstream)
    assert (root / 'Mk/plugins.mk').read_text() == 'selected framework\n'
    assert (root / 'Templates/version').read_text() == 'selected template\n'
    assert not (root / 'Mk/devel.mk').exists() and not (root / 'Scripts').exists()
    assert (root / '.github/ci/control.py').read_text() == 'trusted control\n'


def test_cached_plugin_copy_and_source_assets_preserve_original_build_record(tmp_path, monkeypatch):
    _, trusted, _, environment = build_fixture(tmp_path)
    upstream = json.loads((trusted / 'upstream.json').read_text())
    creator = creator_identity('FreeBSD:14:amd64')
    cached = make_bind_component('bind_plugin', tmp_path / 'cached', series='26.1', creator=creator,
        source_commit=environment['SOURCE_COMMIT'], control_commit='e' * 40, profile=upstream, version='26.1_1')
    original = json.loads((cached / 'channel.json').read_text())
    # Today's qualification pair is B; the cached build remains attributed to A.
    current = make_bind_component('bind_runtime', tmp_path / 'current', series='26.1', creator=creator,
        source_commit='b' * 40, control_commit='c' * 40, profile=upstream, version='9.20.27')
    shutil.copyfile(current / 'bind920-profile.json', trusted / 'bind920-profile.json')
    (trusted / 'runtime-approval.mk').write_text(runtime_approval('9.20.27'))
    shutil.copyfile(cached / 'plugin-build-inputs.json', trusted / 'producer-inputs.json')
    write_json(trusted / 'runtime-selection.json', components.component_receipt(current, 'bind_runtime', '26.1'))
    write_json(trusted / 'cached-component.json', components.component_receipt(cached, 'bind_plugin', '26.1'))
    selection = json.loads((trusted / 'selection.json').read_text())
    selection.update(producer_action='reuse', expected_fingerprint=original['build_fingerprint'])
    digest = components.seal_selection(trusted, selection)
    selected = components.load_selection(trusted, component='bind_plugin', series='26.1', selection_sha256=digest)
    for key in ('RP_TRUSTED_DIRECTORY', 'P_PKG_STATE', 'PKG_CALL_LOG'):
        monkeypatch.setenv(key, environment[key])
    write_json(Path(environment['P_PKG_STATE']), {'pkg': creator, 'locked': True})
    saved = {path.name: path.read_bytes() for path in cached.iterdir()}
    producer = tmp_path / 'reused'
    plugin.copy_cached_producer(cached, producer, selected, trusted, environment['PKG_COMMAND'])
    assert {path.name: path.read_bytes() for path in producer.iterdir()} == {
        name: saved[name] for name in ('os-bind-rp-26.1_1.pkg', 'build-metadata.txt', 'plugin-build-inputs.json')}
    source = tmp_path / 'source-assets'
    tag = plugin.prepare_source_assets(cached, source, '26.1', selected)
    assert {path.name: path.read_bytes() for path in source.iterdir()} == {
        name: saved[name] for name in ('os-bind-rp-26.1_1.pkg', 'build-metadata.txt', 'plugin-build-inputs.json',
                                     'upstream.json', 'creator-target.json', 'plugin-package.mk', 'channel.json')}
    inputs = json.loads(saved['plugin-build-inputs.json'])
    fingerprint = hashlib.sha256(json.dumps(inputs, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    assert tag == 'os-bind-rp-26.1-26.1_1-plugin-' + fingerprint
