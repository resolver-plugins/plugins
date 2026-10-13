"""Shared-feed preservation, signature boundaries and publication concurrency."""
from common_imports import *

import pytest

CI = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CI))
from shared import package_catalogue as catalogue
from ha_fixtures import make_ha_channel
from git_fixtures import upstream_profile
from package_fixtures import complete_bind_channel, creator_identity, write_resolved_target, write_json


@pytest.fixture
def channels(tmp_path, monkeypatch):
    target = creator_identity()
    bind, dhcp = tmp_path / 'bind', tmp_path / 'dhcp'
    complete_bind_channel(bind)
    (bind / 'resolver-plugins.pub').write_bytes(catalogue.PUBLIC_KEY.read_bytes())
    make_ha_channel(dhcp, version='0.2_43', source='f' * 40, profile='a' * 40)
    identities = {p.name: (name, version, origin, target['abi'])
                  for directory, name, version, origin in [
                      (bind, 'bind-tools', '9.20.26_1', 'dns/bind-tools'),
                      (bind, 'bind920', '9.20.26_1', 'dns/bind920'),
                      (bind, 'os-bind-rp', '26.7_1', 'opnsense/os-bind-rp'),
                      (dhcp, 'os-dhcp-interface-ha', '0.2_43', 'opnsense/os-dhcp-interface-ha')]
                  for p in [directory / f'{name}-{version}.pkg']}
    profile = upstream_profile('26.7')
    write_json(tmp_path / 'trusted-upstream.json', profile)
    write_resolved_target(tmp_path / 'creator-target.json', profile)

    def query(path, command):
        identity = identities[path.name]
        deps = {('bind-tools', 'dns/bind-tools', '9.20.26_1')} if identity[0] == 'bind920' else set()
        if identity[0] == 'os-bind-rp':
            deps = {('bind920', 'dns/bind920', '9.20.26_1')}
        return identity, deps

    def sign(packages, output, key, pkg, metadata):
        output.mkdir()
        for package in packages:
            shutil.copyfile(package, output / package.name)
        (output / 'meta.conf').write_text('combined fixture')
        (output / 'packagesite.pkg').write_text('combined signed catalogue')

    monkeypatch.setattr(catalogue.releases, 'query_package', query)
    monkeypatch.setattr(catalogue.releases, 'read_package_manifest', lambda *a: {'dep_formula': 'bind920 >= 9.20.26'})
    monkeypatch.setattr(catalogue.target_pkg, 'verify_target_pkg', Mock())
    monkeypatch.setattr(catalogue.package_checksums, 'verify_archive', Mock())
    monkeypatch.setattr(catalogue.subprocess, 'check_output', Mock(return_value=catalogue.PUBLIC_KEY.read_bytes()))
    monkeypatch.setattr(catalogue, 'verify_packages', Mock())
    monkeypatch.setattr(catalogue.releases, 'stage_selected_repository', Mock(side_effect=sign))
    return bind, dhcp, identities


def stage(channels, tmp_path):
    bind, dhcp, _ = channels
    output = tmp_path / 'combined'
    catalogue.stage(bind, dhcp, output, '26.7', tmp_path / 'key', tmp_path / 'creator-target.json', tmp_path / 'trusted-upstream.json')
    return output


@pytest.mark.parametrize('component,transition', [('bind', False), ('dhcp', False), ('bind', True)])
def test_either_release_preserves_the_other_component_bytes(channels, tmp_path, monkeypatch, component, transition):
    creator = creator_identity(version='2.4.0', origin='opnsense/pkg') if transition else creator_identity()
    profile = upstream_profile('26.7')
    write_resolved_target(tmp_path / 'creator-target.json', profile, creator=creator)
    # Recreate only the fresh component; the retained component keeps its original bytes.
    fresh = channels[0 if component == 'bind' else 1]
    shutil.rmtree(fresh)
    if component == 'bind':
        complete_bind_channel(fresh, creator=creator)
        (fresh / 'resolver-plugins.pub').write_bytes(catalogue.PUBLIC_KEY.read_bytes())
    else:
        make_ha_channel(fresh, version='0.2_43', source='f' * 40, profile='a' * 40, creator=creator)
    output = stage(channels, tmp_path)
    data = catalogue.validate(output)
    assert set(data['components']) == {'bind', 'dhcp'}
    assert {name for record in data['components'].values() for name in record['packages']} == {
        'bind-tools-9.20.26_1.pkg', 'bind920-9.20.26_1.pkg',
        'os-bind-rp-26.7_1.pkg', 'os-dhcp-interface-ha-0.2_43.pkg'}
    for name, directory in zip(('bind', 'dhcp'), channels[:2]):
        assert data['components'][name]['assets'] == catalogue.releases.directory_checksums(directory)
        for filename in data['components'][name]['packages']:
            assert (output / filename).read_bytes() == (directory / filename).read_bytes()
        for filename in ('channel.json', 'build-metadata.txt', 'bind920-provenance.json' if name == 'bind' else 'upstream.json'):
            assert (output / f'{name}-{filename}').read_bytes() == (directory / filename).read_bytes()
    # Publication must match both the newly released and retained component.
    snapshots = dict(zip(('pkg-26.7', 'pkg-dhcp-interface-ha-26.7'), channels[:2]))
    monkeypatch.setattr(catalogue.releases, 'snapshot_release', Mock(side_effect=lambda repo, tag, root:
        SimpleNamespace(existed=True, draft=False, directory=snapshots[tag])))
    publish = Mock()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(catalogue.releases, 'publish_abi_channel', publish)
        catalogue.publish('example/distribution', output, tmp_path / 'recovery')
        publish.assert_called_once_with('example/distribution', output, tmp_path / 'recovery')
        # A newer retained component invalidates the staged aggregate on retry.
        directory = channels[0 if component == 'bind' else 1]
        (directory / 'build-metadata.txt').write_text('new source metadata')
        with pytest.raises(RuntimeError, match='component changed'):
            catalogue.publish('example/distribution', output, tmp_path / 'retry')
        assert publish.call_count == 1


def test_bind_only_bootstrap_cannot_omit_a_newly_published_dhcp_component(channels, tmp_path, monkeypatch):
    bind, dhcp, _ = channels
    output = tmp_path / 'combined'
    catalogue.stage(bind, tmp_path / 'absent', output, '26.7', tmp_path / 'key', tmp_path / 'creator-target.json', tmp_path / 'trusted-upstream.json')
    assert set(catalogue.validate(output)['components']) == {'bind'}
    monkeypatch.setattr(catalogue.releases, 'snapshot_release', lambda repo, tag, root:
                        SimpleNamespace(existed=True, draft=False, directory=bind if tag == 'pkg-26.7' else dhcp))
    publish = Mock()
    monkeypatch.setattr(catalogue.releases, 'publish_abi_channel', publish)
    with pytest.raises(RuntimeError, match='new component appeared'):
        catalogue.publish('example/distribution', output, tmp_path / 'recovery')
    publish.assert_not_called()


@pytest.mark.parametrize('fault,exception,error', [
    ('key', ValueError, 'key or package set differs'),
    ('series', ValueError, 'audit metadata is inconsistent'),
    ('abi', ValueError, 'package ABI differs'),
])
def test_untrusted_or_incompatible_component_is_rejected_before_combined_signing(channels, tmp_path, fault, exception, error):
    bind, dhcp, identities = channels
    if fault == 'key':
        (bind / 'resolver-plugins.pub').write_text('foreign key')
    elif fault == 'series':
        data = json.loads((bind / 'channel.json').read_text())
        data['series'] = '26.1'
        (bind / 'channel.json').write_text(json.dumps(data))
    elif fault == 'abi':
        name = 'os-dhcp-interface-ha-0.2_43.pkg'
        identities[name] = (*identities[name][:3], 'FreeBSD:14:amd64')
    with pytest.raises(exception, match=error):
        stage(channels, tmp_path)
    catalogue.releases.stage_selected_repository.assert_not_called()


@pytest.mark.parametrize('fault,rehash,error', [
    ('bytes', False, 'assets'), ('missing', False, 'assets'),
    ('extra', False, 'assets'),
    ('bytes', True, 'package bytes differ from component provenance'),
    ('metadata', True, 'component metadata differs from original release'),
])
def test_combined_validation_rejects_package_or_provenance_drift(channels, tmp_path, fault, rehash, error):
    output = stage(channels, tmp_path)
    package = output / 'os-dhcp-interface-ha-0.2_43.pkg'
    if fault == 'bytes':
        package.write_text('tampered')
    elif fault == 'missing':
        package.unlink()
    elif fault == 'extra':
        (output / 'unexpected.pkg').write_text('extra')
    else:
        (output / 'dhcp-build-metadata.txt').write_text('wrong source')
    if rehash:
        manifest = output / 'channel.json'
        data = json.loads(manifest.read_text())
        data['assets'] = catalogue.releases.directory_checksums(output)
        data['assets'].pop('channel.json')
        manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError, match=error):
        catalogue.validate(output)


def test_static_publisher_rejects_component_removal_before_any_mutation(channels, tmp_path, monkeypatch):
    output = tmp_path / 'combined'
    catalogue.stage(channels[0], tmp_path / 'absent', output, '26.7', tmp_path / 'key', tmp_path / 'creator-target.json', tmp_path / 'trusted-upstream.json')
    def api(endpoint, **kwargs):
        assert not kwargs, 'no mutations are allowed before component preservation is checked'
        if '/git/ref/' in endpoint:
            return {'object': {'sha': '1' * 40}}
        if '/git/commits/' in endpoint:
            return {'tree': {'sha': '2' * 40}}
        if '/git/trees/' in endpoint:
            return {'truncated': False, 'tree': [{'path': 'pkg/FreeBSD:15:amd64/26.7/latest/channel.json', 'sha': '3' * 40}]}
        return {'content': base64.b64encode(json.dumps({'kind': 'combined', 'components': {'bind': {}, 'dhcp': {}}}).encode()).decode()}
    monkeypatch.setattr(catalogue.releases, 'gh_api', api)
    with pytest.raises(RuntimeError, match='remove an existing component'):
        catalogue.releases.publish_abi_channel('example/distribution', output, tmp_path / 'recovery')


def test_both_workflows_serialize_and_publish_the_shared_catalogue():
    root = CI.parents[1]
    for filename in ('bind-package-release.yml', 'ha-dhcp-interface-release.yml'):
        text = (root / '.github/workflows' / filename).read_text()
        assert 'group: package-release\n  cancel-in-progress: false' in text
        assert 'package_catalogue.py publish' in text


@pytest.fixture
def executable_directory():
    root = CI.parent / 'ci-local'
    root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='catalogue-', dir=root) as directory:
        yield Path(directory)


@pytest.mark.parametrize('fault', ['', 'extra-package', 'archive-bytes', 'archive-abi'])
def test_repository_verification_observes_isolated_catalogue_and_exact_archives(tmp_path, executable_directory, monkeypatch, fault):
    verification = tmp_path / 'verification'
    verification.mkdir()
    monkeypatch.setattr(catalogue.tempfile, 'TemporaryDirectory', lambda: nullcontext(str(verification)))
    profile = upstream_profile('26.7')
    upstream = write_json(tmp_path / 'upstream.json', profile)
    reader = write_resolved_target(tmp_path / 'reader-target.json', profile, role='reader')
    packages = []
    for name, abi in [('os-example', 'FreeBSD:15:amd64'), ('os-other', 'FreeBSD:14:amd64' if fault == 'archive-abi' else 'FreeBSD:15:amd64')]:
        packages.append(write_json(tmp_path / f'{name}-1.pkg', [name, '1', f'opnsense/{name}', abi]))
    command = executable_directory / 'pkg-fixture'
    command.write_text('''#!/usr/bin/env python3
import hashlib
import json
from pathlib import Path
import shutil
import sys
args = sys.argv[1:]
fault = FAULT
packages = [Path(path) for path in ARCHIVES]
root = Path(VERIFICATION_ROOT)
installed = json.loads((root / 'installed.json').read_text())
if args == ['-v']:
    print(installed['identity'][1].split('_')[0])
elif args[:2] == ['lock', '-l']:
    print(installed['owner'])
elif args[:2] == ['which', '-q']:
    assert args[2] == __file__
    print(installed['owner'])
elif args[:2] == ['check', '-s']:
    assert hashlib.sha256(Path(__file__).read_bytes()).hexdigest() == installed['manifest']
elif args[:2] == ['query', '-e']:
    print('|'.join(installed['identity']))
elif args[:2] == ['query', '-F']:
    if not args[-1].startswith('%dn'):
        print('\t'.join(json.loads(Path(args[2]).read_text())))
else:
    assert args[:6] == ['-o', f'REPOS_DIR={root / "repos"}', '-o', f'PKG_DBDIR={root / "db"}',
                        '-o', f'PKG_CACHEDIR={root / "cache"}']
    operation = args[6]
    with (root / 'commands.log').open('a') as stream:
        stream.write(operation + '\\n')
    if operation == 'update':
        config = (root / 'repos/catalogue.conf').read_text()
        assert 'signature_type: "pubkey"' in config
        assert 'url: "https://packages.example.invalid/feed"' in config
        key = Path(json.loads(config.split('pubkey: ', 1)[1].splitlines()[0]))
        assert key.read_bytes() == EXPECTED_KEY
    elif operation == 'rquery':
        assert '-U' in args and '-a' in args
        for package in packages:
            print('|'.join(json.loads(package.read_text())))
        if fault == 'extra-package':
            print('unexpected|1|unknown/unexpected|FreeBSD:15:amd64')
    elif operation == 'fetch':
        assert '-U' in args
        output = Path(args[args.index('-o', 7) + 1]) / 'All'
        output.mkdir(parents=True)
        for package in packages:
            shutil.copyfile(package, output / package.name)
        if fault == 'archive-bytes':
            (output / packages[-1].name).write_text('different bytes')
    else:
        raise AssertionError(args)
'''.replace('FAULT', repr(fault)).replace('ARCHIVES', repr([str(p) for p in packages]))
       .replace('EXPECTED_KEY', repr(catalogue.PUBLIC_KEY.read_bytes()))
       .replace('VERIFICATION_ROOT', repr(str(verification))))
    command.chmod(0o755)
    write_json(verification / 'installed.json', dict(identity=['pkg', '2.3.1_1', 'ports-mgmt/pkg', 'FreeBSD:15:amd64'],
        owner='pkg-2.3.1_1', manifest=hashlib.sha256(command.read_bytes()).hexdigest()))
    monkeypatch.setattr(catalogue, 'PKG', str(command))
    if not fault:
        catalogue.verify_packages(packages, 'https://packages.example.invalid/feed', reader, upstream)
        assert set((verification / 'commands.log').read_text().splitlines()) == {'update', 'rquery', 'fetch'}
        assert {p.name: p.read_bytes() for p in (verification / 'fetch/All').iterdir()} == {p.name: p.read_bytes() for p in packages}
    else:
        error = {'extra-package': 'identities', 'archive-bytes': 'archive differs', 'archive-abi': 'ABI differs'}[fault]
        with pytest.raises(ValueError, match=error):
            catalogue.verify_packages(packages, 'https://packages.example.invalid/feed', reader, upstream)


def test_fetch_requires_published_bind_and_accepts_absent_dhcp_only(channels, tmp_path, monkeypatch):
    snapshot = SimpleNamespace(existed=False)
    monkeypatch.setattr(catalogue.releases, 'snapshot_release', lambda *a: snapshot)
    with pytest.raises(ValueError, match='publish the BIND'):
        catalogue.fetch('example/distribution', 'bind', '26.7', tmp_path / 'missing-bind')
    catalogue.fetch('example/distribution', 'dhcp', '26.7', tmp_path / 'missing-dhcp')
    assert not (tmp_path / 'missing-dhcp').exists()
    snapshot.existed, snapshot.draft, snapshot.directory = True, True, channels[0]
    with pytest.raises(ValueError, match='unpublished'):
        catalogue.fetch('example/distribution', 'bind', '26.7', tmp_path / 'draft')
    snapshot.draft = False
    catalogue.fetch('example/distribution', 'bind', '26.7', tmp_path / 'downloaded')
    assert catalogue.releases.directory_checksums(tmp_path / 'downloaded') == catalogue.releases.directory_checksums(channels[0])
