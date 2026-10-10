"""Recipe selection and installed-state acceptance at local command boundaries."""
from module_fixtures import *
import pytest
from git_fixtures import init_repository, commit, git, upstream_profile, create_core_repository

MODULE_PATH = CI / 'shared/target_pkg.py'
target_pkg = load_module('target_pkg', MODULE_PATH, register=True)
FIXTURE_ROOT = CI.parent / 'ci-local'

def recipe_targets(tmp_path, series, *, zero_revision=False):
    ports = init_repository(tmp_path / 'ports')
    floor = '26.1.11' if series == '26.1' else '26.7'
    floor_version = '2.3.1_1' if series == '26.1' else '2.3.1'
    floor_commit = commit(ports, {'ports-mgmt/pkg/Makefile':
        'PORTNAME=pkg\nPORTVERSION=2.3.1\n' + ('PORTREVISION=1\n' if series == '26.1' else '')}, 'floor')
    git(ports, 'tag', floor)
    release, creator_commit, origin, version = floor, floor_commit, 'ports-mgmt/pkg', floor_version
    if series == '26.7':
        release = '26.7.2' if zero_revision else '26.7.1'
        git(ports, 'rm', '-r', 'ports-mgmt/pkg')
        creator_commit = commit(ports, {'opnsense/pkg/Makefile':
            ('PORTNAME = pkg # literal\nPORTVERSION=2.5.0\nPORTREVISION=0\nPORTEPOCH=1\n'
             if zero_revision else 'PORTNAME=pkg\nDISTVERSION=2.4.0\nPORTREVISION=2\nPORTEPOCH=1\n')
            + 'CATEGORIES=ports-mgmt\n'}, 'creator')
        git(ports, 'tag', release)
        origin, version = 'opnsense/pkg', '2.5.0,1' if zero_revision else '2.4.0_2,1'
    commit(ports, {f'{origin}/Makefile': 'PORTNAME=pkg\nDISTVERSION=9.9.9\n'}, 'decoy moving branch')
    git(ports, 'tag', f'{series}.99')
    profile = upstream_profile(series, tools_tag=release)
    upstream = tmp_path / 'upstream.json'
    upstream.write_text(json.dumps(profile))
    abi = 'FreeBSD:14:amd64' if series == '26.1' else 'FreeBSD:15:amd64'
    expected = {}
    for role, selected_release, selected_commit, selected_path, selected_version in [
        ('creator', release, creator_commit, origin, version),
        ('reader', floor, floor_commit, 'ports-mgmt/pkg', floor_version),
    ]:
        expected[role] = dict(schema=1, role=role, series=series, release=selected_release,
            recipe=dict(repository='https://github.com/opnsense/ports.git', commit=selected_commit,
                        path=selected_path),
            package_creator=dict(name='pkg', version=selected_version, origin=selected_path, abi=abi))
    return ports, upstream, expected


@pytest.mark.parametrize('series,zero_revision', [('26.1', False), ('26.7', False), ('26.7', True)])
def test_resolves_release_recipe_targets(tmp_path, monkeypatch, series, zero_revision):
    ports, upstream, expected = recipe_targets(tmp_path, series, zero_revision=zero_revision)
    monkeypatch.setenv('OPNSENSE_PORTS_REPOSITORY', str(ports))
    monkeypatch.delenv('GIT_COMMAND', raising=False)
    output = tmp_path / 'targets'
    result = subprocess.run([sys.executable, str(MODULE_PATH), 'resolve', '--upstream', str(upstream),
        '--series', series, '--architecture', 'x86_64', '--output-directory', str(output)],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert {role: json.loads((output / f'{role}-target.json').read_text())
            for role in ('creator', 'reader')} == expected


@contextmanager
def pkg_fixture():
    """Stateful simulated archives, installed files and current manifest integrity."""
    FIXTURE_ROOT.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=FIXTURE_ROOT) as temporary:
        root = Path(temporary)
        command = root / 'pkg'
        command.write_text('''#!/usr/bin/env python3
import hashlib, json, sys
from pathlib import Path
root = Path(__file__).parent
args = sys.argv[1:]
with (root / 'calls').open('a') as log: log.write(' '.join(args) + '\\n')
operation = args[0]
if (root / 'fault').exists() and (root / 'fault').read_text() == operation:
    sys.exit('fixture ' + operation + ' failure')
installed = root / 'installed.json'
locked = root / 'locked'
static = root / 'pkg-static'
if operation == 'update': pass
elif operation in ('rquery', 'fetch'):
    record = json.loads((root / 'feeds.json').read_text())[args[args.index('-r') + 1]]
    if operation == 'rquery': print('|'.join(record['identity']))
    else:
        assert args[-1] == 'pkg-' + record['identity'][1]
        destination = Path(args[args.index('-o') + 1]) / 'All'
        destination.mkdir()
        (destination / (args[-1] + '.pkg')).write_text(json.dumps(record))
elif operation == 'add':
    assert not locked.exists(), 'replacement attempted while locked'
    record = json.loads(Path(args[-1]).read_text())
    static.write_text(record['static']); static.chmod(0o755)
    record['manifest'] = hashlib.sha256(static.read_bytes()).hexdigest()
    installed.write_text(json.dumps(record))
elif operation == 'query':
    record = json.loads(Path(args[args.index('-F') + 1]).read_text()) if '-F' in args else json.loads(installed.read_text())
    print('|'.join(record['identity']))
elif operation == 'lock':
    if '-y' in args: locked.touch()
    elif locked.exists(): print('pkg-' + json.loads(installed.read_text())['identity'][1])
elif operation == 'unlock': locked.unlink()
elif operation == 'which':
    assert Path(args[-1]) == static and static.is_file()
    print('pkg-' + json.loads(installed.read_text())['identity'][1])
elif operation == 'check':
    assert hashlib.sha256(static.read_bytes()).hexdigest() == json.loads(installed.read_text())['manifest'], 'manifest integrity'
else: raise AssertionError(args)
''')
        command.chmod(0o755)
        yield root, command


def publication(target, marker):
    identity = target['package_creator']
    base = identity['version'].split(',')[0].split('_')[0]
    return dict(identity=[identity[k] for k in ('name', 'version', 'origin', 'abi')],
                static=f"#!/bin/sh\n# {marker}\nprintf '%s\\n' '{base}'\n")


def install(role, targets, upstream, series, command, root):
    return target_pkg.select_target_pkg(targets / f'{role}-target.json', series, str(command),
        upstream=upstream, role=role, pkg_static_path=root / 'pkg-static')


@pytest.mark.parametrize('series', ['26.1', '26.7'])
def test_installs_recipe_targets_and_same_version_reissues(tmp_path, monkeypatch, series):
    ports, upstream, expected = recipe_targets(tmp_path, series)
    core = tmp_path / 'core'
    profile = upstream_profile(series, core_commit=create_core_repository(core), tools_tag=expected['creator']['release'])
    upstream.write_text(json.dumps(profile))
    monkeypatch.setenv('OPNSENSE_PORTS_REPOSITORY', str(ports))
    monkeypatch.delenv('GIT_COMMAND', raising=False)
    targets = tmp_path / 'targets'
    target_pkg.resolve_targets(upstream, series, 'x86_64', targets)
    with pkg_fixture() as (root, command):
        env = dict(os.environ, RP_UPSTREAM_METADATA=str(upstream), OPNSENSE_CORE_REPOSITORY=str(core),
                   PKG_REPOS_DIR=str(tmp_path / 'repos'), PKG_FINGERPRINTS_DIR=str(tmp_path / 'fingerprints'))
        env.pop('RP_OPNSENSE_SNAPSHOT', None)
        first, reissue = publication(expected['creator'], 'A'), publication(expected['creator'], 'B')
        feeds = root / 'feeds.json'
        feeds.write_text(json.dumps({'OPNsense': first, 'OPNsense-creator': reissue}))
        for role, record in [('creator', first), ('creator', reissue), ('reader', publication(expected['reader'], 'reader'))]:
            if record != first:
                feeds.write_text(json.dumps({'OPNsense': dict(first, identity=['pkg','9.9.9','opnsense/pkg',first['identity'][3]]),
                                            'OPNsense-creator': record}))
            subprocess.run([str(CI / 'shared/setup-opnsense-repository.sh'), series],
                env=dict(env, RP_TARGET_PKG_METADATA=str(targets / f'{role}-target.json')), capture_output=True, check=True)
            selected = install(role, targets, upstream, series, command, root)
            observed = json.loads((root / 'installed.json').read_text())
            assert observed['identity'] == record['identity']
            assert (root / 'pkg-static').read_text() == record['static']
            assert observed['manifest'] == hashlib.sha256((root / 'pkg-static').read_bytes()).hexdigest()
            assert (root / 'locked').is_file()
            assert target_pkg.run([str(command), 'which', '-q', str(root / 'pkg-static')]).stdout.strip() == 'pkg-' + record['identity'][1]
            target_pkg.verify_target_pkg(selected, str(command), pkg_static_path=root / 'pkg-static')


@pytest.mark.parametrize('fault', ['update', 'add'])
def test_failed_acquisition_preserves_installed_creator(tmp_path, monkeypatch, fault):
    ports, upstream, expected = recipe_targets(tmp_path, '26.1')
    monkeypatch.setenv('OPNSENSE_PORTS_REPOSITORY', str(ports))
    monkeypatch.delenv('GIT_COMMAND', raising=False)
    targets = tmp_path / 'targets'
    target_pkg.resolve_targets(upstream, '26.1', 'x86_64', targets)
    with pkg_fixture() as (root, command):
        a, b = publication(expected['creator'], 'A'), publication(expected['creator'], 'B')
        (root / 'feeds.json').write_text(json.dumps({'OPNsense': a, 'OPNsense-creator': b}))
        install('creator', targets, upstream, '26.1', command, root)
        before = {name: (root / name).read_bytes() for name in ('installed.json', 'pkg-static', 'locked')}
        (root / 'feeds.json').write_text(json.dumps({'OPNsense': b, 'OPNsense-creator': b}))
        (root / 'calls').write_text('')
        (root / 'fault').write_text(fault)
        with pytest.raises(target_pkg.TargetPackageError, match=f'fixture {fault} failure'):
            install('creator', targets, upstream, '26.1', command, root)
        assert {name: (root / name).read_bytes() for name in before} == before
        if fault == 'update':
            assert not any(operation in (root / 'calls').read_text() for operation in ('fetch', 'unlock', 'add', 'OPNsense-creator'))


def test_verify_rejects_changed_creator_state(tmp_path, monkeypatch):
    ports, upstream, expected = recipe_targets(tmp_path, '26.1')
    monkeypatch.setenv('OPNSENSE_PORTS_REPOSITORY', str(ports))
    monkeypatch.delenv('GIT_COMMAND', raising=False)
    targets = tmp_path / 'targets'
    target_pkg.resolve_targets(upstream, '26.1', 'x86_64', targets)
    with pkg_fixture() as (root, command):
        a = publication(expected['creator'], 'A')
        (root / 'feeds.json').write_text(json.dumps({'OPNsense': a, 'OPNsense-creator': a}))
        selected = install('creator', targets, upstream, '26.1', command, root)
        (root / 'pkg-static').write_text(publication(expected['creator'], 'uninstalled replacement')['static'])
        with pytest.raises(target_pkg.TargetPackageError, match='installed-manifest integrity'):
            target_pkg.verify_target_pkg(selected, str(command), pkg_static_path=root / 'pkg-static')
