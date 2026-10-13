#!/usr/bin/env python3
"""Trusted BIND component/provenance boundaries with simulated native providers."""
from module_fixtures import *

from package_fixtures import (
    BIND_PROFILE as PROFILE, bind_records, creator_identity, make_bind_component,
    write_resolved_target, write_build_metadata, write_json, PLUGIN_PACKAGE,
    make_ports_recipe, make_tools_repository, fixture_version_comparison, plugin_manifest,
)

release_channel = load_module('release_channel', 'shared/release_channel.py')


class StageProvenanceTest(unittest.TestCase):
    def test_component_stage_preserves_owned_output_and_original_reuse(self):
        from contextlib import ExitStack
        from git_fixtures import init_repository, commit, upstream_profile, git
        from package_fixtures import make_bind_component
        from test_release_channel_archive import release_api_store, release_channel as publication
        components = load_module('stage_components', 'bind/shared/components.py')
        from shared import package_catalogue
        for kind in ('bind_runtime', 'bind_plugin'):
            with self.subTest(component=kind), tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary)
                history=init_repository(root / 'git')
                profile=upstream_profile('26.7')
                ports, approval, pin=make_ports_recipe(root / 'ports')
                tools=make_tools_repository(root / 'tools',profile)
                tracked={'.resolver-plugins/upstream.json':json.dumps(profile),
                         'dns/bind/Makefile':PLUGIN_PACKAGE,
                         '.github/ci/bind/runtime/build-bind920.sh':'runtime fixture recipe',
                         '.github/ci/bind/plugin/build-os-bind-rp.sh':'plugin fixture recipe'}
                older=commit(history,tracked,'reviewed controller')
                selected=commit(history,{'dns/bind/Makefile':PLUGIN_PACKAGE.replace('>= 9.20.26','>= 9.20.27')+approval},'selected controller')
                source_definition=root / 'source-package.mk'
                source_definition.write_bytes(subprocess.check_output(['git','-C',str(history),'show',f'{older}:dns/bind/Makefile']))
                origin=root / 'origin.git'
                git(root,'clone','--bare',history,origin)
                git(origin,'update-ref','refs/heads/master',older)
                git(history,'remote','add','origin',origin.as_uri())
                runtime=make_bind_component('bind_runtime',root / 'runtime',series='26.7',creator=creator_identity(),
                    source_commit=older,control_commit=selected,profile=profile,version='9.20.26_2',bind_profile=pin)
                template=make_bind_component(kind,root / 'template',series='26.7',creator=creator_identity(),
                    source_commit=older,control_commit=selected,profile=profile,
                    version='9.20.26_2' if kind=='bind_runtime' else '26.7_1',bind_profile=pin)
                creator=write_resolved_target(root / 'creator.json',profile)
                reader=write_resolved_target(root / 'reader.json',profile,role='reader')
                key=root / 'key'; key.write_bytes(b'fixture private key')
                original_run,original_output=subprocess.run,subprocess.check_output
                signatures=[]
                def run(command,**kwargs):
                    if command[:3]==['gh','release','list']:
                        return subprocess.CompletedProcess(command,0,stdout='[]')
                    if command[:2]==['pkg-fixture','repo']:
                        signatures.append(command)
                        for name in components.CATALOGUE_ASSETS:
                            (Path(command[2]) / name).write_bytes(f'catalogue {len(signatures)}'.encode())
                        return subprocess.CompletedProcess(command,0)
                    return original_run(command,**kwargs)
                def output(command,**kwargs):
                    if command[1:3]==['version','-t']:
                        return fixture_version_comparison(*command[-2:])+'\n'
                    return components.PUBLIC_KEY.read_bytes() if command[0]=='openssl' else original_output(command,**kwargs)
                def query(path,command):
                    data=json.loads(path.read_text())
                    return tuple(data[field] for field in ('name','version','origin','abi')), (
                        {('bind-tools','dns/bind-tools',data['version'])} if data['name']=='bind920' else {
                            (name,edge['origin'],edge['version']) for name,edge in data.get('deps',{}).items()})
                previous=Path.cwd()
                try:
                    os.chdir(history)
                    resolver=components.bind920_profile.resolve_profile
                    with ExitStack() as providers:
                        for provider in (patch.object(components.bind920_profile,'resolve_profile',side_effect=lambda value:resolver(value,repository=ports)),
                            patch.object(components.subprocess,'run',side_effect=run),
                            patch.object(components.subprocess,'check_output',side_effect=output),
                            patch.object(components.releases,'snapshot_release',return_value=SimpleNamespace(existed=True,draft=False,directory=runtime)),
                            patch.object(components.releases,'query_package',side_effect=query),
                            patch.object(components.releases,'read_package_manifest',side_effect=lambda archive,command:json.loads(archive.read_text())),
                            patch.object(components.package_checksums,'verify_archive',side_effect=lambda command,archive:list(json.loads(archive.read_text()).get('files',{}).items())),
                            patch.object(components,'_select_role'),
                            patch.object(package_catalogue,'verify_packages'), patch.object(package_catalogue,'verify_repository')):
                            providers.enter_context(provider)
                        trusted=root / 'trusted'
                        components.prepare_selection(trusted,component=kind,series='26.7',mode='production',
                            control_commit=selected,profile_commit=older,source_commit=older if kind=='bind_plugin' else None,
                            upstream=history / '.resolver-plugins/upstream.json',runtime_approval=history / 'dns/bind/Makefile',
                            package_definition=source_definition if kind=='bind_plugin' else None,tools_repository=tools,
                            recipe=history / ('.github/ci/bind/runtime/build-bind920.sh' if kind=='bind_runtime' else '.github/ci/bind/plugin/build-os-bind-rp.sh'),
                            repository='example/distribution',intake_directory=root / 'intake',creator_target=creator,reader_target=reader)
                        selection=json.loads((trusted / 'selection.json').read_text())
                        raw=root / 'producer';raw.mkdir()
                        manifest=json.loads((template / 'channel.json').read_text())
                        names=set(manifest['packages']) | ({'bind920-provenance.json'} if kind=='bind_runtime' else {'build-metadata.txt','plugin-build-inputs.json'})
                        for name in names: shutil.copyfile(template / name,raw / name)
                        if kind=='bind_runtime':
                            inputs=json.loads((trusted / 'producer-inputs.json').read_text())
                            write_json(raw / 'bind920-provenance.json',components.bind920_profile.build_provenance(
                                pin,'26.7','15.1','x86_64',creator_identity(),bind_records(),recipe_sha256=inputs['build_recipe_sha256']))
                        else:
                            shutil.copyfile(trusted / 'producer-inputs.json',raw / 'plugin-build-inputs.json')
                        expected={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in raw.iterdir()}
                        staged=root / 'component'
                        record=components.stage_component(raw,selection,trusted,staged,key,pkg_command='pkg-fixture')
                        current={p.name:p.read_bytes() for p in (staged / 'current').iterdir()}
                        self.assertEqual(current,{p.name:p.read_bytes() for p in (staged / 'snapshot').iterdir()})
                        self.assertEqual({name:digest for name,digest in expected.items() if name.endswith('.pkg')},record['packages'])
                        self.assertTrue(components.CATALOGUE_ASSETS <= set(current))
                        self.assertEqual(selected,record['control_commit'])
                        repository=root / 'repository'
                        shutil.copytree(staged,repository / 'component')
                        retained=runtime if kind=='bind_plugin' else make_bind_component('bind_plugin',root / 'retained-plugin',
                            series='26.7',creator=creator_identity(),source_commit=older,control_commit=older,
                            profile=profile,version='26.7_1')
                        directories={kind:repository / 'component/current',
                            ('bind_runtime' if kind=='bind_plugin' else 'bind_plugin'):retained}
                        receipts={name:components.component_receipt(directory,name,'26.7') for name,directory in directories.items()}
                        receipts['dhcp']=None
                        with patch.object(package_catalogue,'ROOT',history),patch.object(package_catalogue,'PKG','pkg-fixture'),patch.object(package_catalogue.bind,'select_role'), \
                             patch.object(package_catalogue.target_pkg,'verify_target_pkg'):
                            package_catalogue.stage_bind_stack(directories['bind_runtime'],directories['bind_plugin'],repository / 'stack/current',
                                series='26.7',key=key,creator_target=trusted / 'creator-target.json',upstream=trusted / 'upstream.json',
                                approved_profile=trusted / 'bind920-profile.json',runtime_approval=trusted / 'runtime-approval.mk')
                            shutil.copytree(repository / 'stack/current',repository / 'stack/snapshot')
                            package_catalogue.stage(directories['bind_runtime'],directories['bind_plugin'],None,repository / 'combined',
                                series='26.7',key=key,creator_target=trusted / 'creator-target.json',upstream=trusted / 'upstream.json',
                                approved_profile=trusted / 'bind920-profile.json',runtime_approval=trusted / 'runtime-approval.mk')
                        digest=components.write_composition(repository,component=kind,series='26.7',initialized=False,components=receipts)
                        peer='bind_runtime' if kind=='bind_plugin' else 'bind_plugin'
                        remote={tag:dict(assets={p.name:p.read_bytes() for p in retained.iterdir()},draft=False,
                                isImmutable=False,name=tag,body='original metadata',isPrerelease=False,
                                created_at='2026-01-01T00:00:00Z')
                            for tag in (receipts[peer]['current_tag'],receipts[peer]['snapshot_tag'])}
                        feed=[b'prior valid feed']
                        def publish_feed(*args,**kwargs):
                            if kind=='bind_runtime': raise RuntimeError('Pages compare-and-swap failed')
                            feed[0]=b'qualified feed'
                        with patch.object(components,'releases',publication),patch.object(package_catalogue,'releases',publication), \
                             patch.object(package_catalogue.bind,'releases',publication),patch.object(components,'_initialized',return_value=False), \
                             patch.object(publication,'publish_abi_channel',side_effect=publish_feed),patch.object(publication,'mark_latest_package_channel'), \
                             release_api_store(remote,dict(files={}),[None],run):
                            arguments=dict(component=kind,series='26.7',selection_sha256=publication.sha256(trusted / 'selection.json'),
                                composition_sha256=digest)
                            with self.assertRaisesRegex(ValueError,'not merged'):
                                components.publish_component('example/distribution',repository,trusted,root / 'unmerged',**arguments)
                            self.assertEqual({receipts[peer]['current_tag'],receipts[peer]['snapshot_tag']},set(remote))
                            git(origin,'update-ref','refs/heads/master',selected)
                            if kind=='bind_runtime':
                                with self.assertRaisesRegex(RuntimeError,'Pages compare-and-swap'):
                                    components.publish_component('example/distribution',repository,trusted,root / 'publish',**arguments)
                                self.assertEqual(b'prior valid feed',feed[0])
                                failure=json.loads((root / 'publish/failure.json').read_text())
                                self.assertEqual('pages',failure['phase'])
                                self.assertTrue(failure['qualified_releases_may_be_ahead'])
                            else:
                                components.publish_component('example/distribution',repository,trusted,root / 'publish',**arguments)
                                self.assertEqual(b'qualified feed',feed[0])
                            own=receipts[kind]
                            self.assertEqual(current,remote[own['current_tag']]['assets'])
                            self.assertEqual(current,remote[own['snapshot_tag']]['assets'])
                            self.assertFalse(remote[own['current_tag']]['draft'])
                            self.assertEqual({p.name:p.read_bytes() for p in (repository / 'stack/current').iterdir()},remote['pkg-26.7']['assets'])
                            self.assertEqual(receipts[peer]['assets'],{name:hashlib.sha256(content).hexdigest()
                                for name,content in remote[receipts[peer]['current_tag']]['assets'].items()})
                        signing_calls=len(signatures)
                        if kind=='bind_plugin':
                            receipt=components.component_receipt(staged / 'current',kind,'26.7')
                            write_json(trusted / 'cached-component.json',receipt)
                            selection['producer_action']='reuse'
                            components.seal_selection(trusted,selection)
                            selection=json.loads((trusted / 'selection.json').read_text())
                            with patch.object(components.releases,'snapshot_release',side_effect=lambda repository,tag,output:SimpleNamespace(existed=True,draft=False,directory=runtime if 'bind-runtime' in tag else staged / 'current')):
                                components.stage_component(raw,selection,trusted,root / 'reused',None,pkg_command='pkg-fixture')
                            self.assertEqual(current,{p.name:p.read_bytes() for p in (root / 'reused/current').iterdir()})
                            self.assertEqual(signing_calls,len(signatures))
                        selection['mode']='development'
                        selection['producer_action']='build'
                        (trusted / 'cached-component.json').unlink(missing_ok=True)
                        components.seal_selection(trusted,selection)
                        selection=json.loads((trusted / 'selection.json').read_text())
                        if kind=='bind_plugin':
                            package=raw / 'os-bind-rp-26.7_1.pkg'
                            development=json.loads(package.read_text())
                            development.update(plugin_manifest(source_definition.read_text(),development=True,
                                built_runtime_version=development['deps']['bind920']['version']))
                            write_json(raw / 'os-bind-rp-devel-26.7_1.pkg',development)
                            package.unlink()
                        preview=root / 'preview-repository'
                        components.stage_preview(raw,selection,trusted,preview / 'preview',pkg_command='pkg-fixture')
                        verification=load_module('bind_verification','bind/shared/verification.py')
                        with patch.dict(os.environ,RP_SELECTION_SHA256=components.releases.sha256(trusted / 'selection.json')):
                            result=verification.preflight(preview,trusted,kind,'26.7','local')
                        self.assertEqual('development',result['selection']['mode'])
                        self.assertIsNone(result['composition'])
                        self.assertEqual({'bind-tools-9.20.26_2.pkg','bind920-9.20.26_2.pkg'} |
                            ({'os-bind-rp-devel-26.7_1.pkg'} if kind=='bind_plugin' else set()),
                            {Path(path).name for path in result['repositories'][0]['packages']})
                        self.assertFalse((preview / 'preview/channel.json').exists())
                        self.assertTrue(result['repositories'][0]['unsigned'])
                finally: os.chdir(previous)

    def test_original_recipe_validates_after_the_selected_wrapper_changes(self):
        creator = creator_identity('FreeBSD:14:amd64')
        saved_inputs = dict(schema=4, series='26.1', freebsd_release='14.3',
            architecture='x86_64', bind_profile=PROFILE, build_recipe_sha256='e' * 64,
            package_creator=creator)
        original_hash = hashlib.sha256(json.dumps(saved_inputs, sort_keys=True,
            separators=(',', ':')).encode()).hexdigest()
        provenance = release_channel.bind920_profile.build_provenance(
            PROFILE, '26.1', '14.3', 'x86_64', creator, bind_records(),
            recipe_sha256=saved_inputs['build_recipe_sha256'])
        self.assertEqual(original_hash, provenance['fingerprint'])
        with tempfile.TemporaryDirectory() as temporary:
            wrapper = Path(temporary) / 'build-bind920.sh'
            wrapper.write_bytes(b'changed selected build wrapper')
            selected_hash = hashlib.sha256(wrapper.read_bytes()).hexdigest()
            with patch.object(release_channel.bind920_profile, 'BUILD_RECIPE_PATH', wrapper):
                self.assertEqual(bind_records(), release_channel.validate_bind_provenance(
                    provenance, PROFILE, '26.1', '14.3', creator))
                fresh = dict(saved_inputs, build_recipe_sha256=selected_hash)
                fresh_hash = hashlib.sha256(json.dumps(fresh, sort_keys=True,
                    separators=(',', ':')).encode()).hexdigest()
                self.assertEqual(fresh_hash, release_channel.bind920_profile.compatibility_fingerprint(
                    PROFILE, '26.1', '14.3', 'x86_64', creator, recipe_sha256=selected_hash))
                self.assertNotEqual(original_hash, fresh_hash)

    def test_channel_rejects_untrusted_bind_provenance(self):
        creator = creator_identity('FreeBSD:14:amd64')
        for fault, error in [('fingerprint', 'fingerprint'), ('freebsd', 'trusted profile')]:
            with self.subTest(fault=fault):
                provenance = release_channel.bind920_profile.build_provenance(
                    PROFILE, '26.1', '14.4' if fault == 'freebsd' else '14.3',
                    'x86_64', creator, bind_records(), recipe_sha256='e' * 64)
                if fault == 'fingerprint':
                    provenance['fingerprint'] = '0' * 64
                with self.assertRaisesRegex(ValueError, error):
                    release_channel.validate_bind_provenance(
                        provenance, PROFILE, '26.1', '14.3', creator)

    def test_runtime_component_uses_the_pair_named_by_original_provenance(self):
        with tempfile.TemporaryDirectory() as temporary:
            from git_fixtures import upstream_profile
            components = load_module('provenance_components', 'bind/shared/components.py')
            directory = make_bind_component('bind_runtime', Path(temporary) / 'runtime',
                series='26.7', creator=creator_identity(), source_commit='a' * 40,
                control_commit='c' * 40, profile=upstream_profile('26.7'), version='9.20.27')
            expected = {name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
                        for name in ('bind-tools-9.20.27.pkg', 'bind920-9.20.27.pkg')}
            record = components.validate_component(directory, 'bind_runtime', '26.7')
            self.assertEqual(expected, record['packages'])

    def test_original_plugin_metadata_matches_source_profile_and_creator(self):
        with tempfile.TemporaryDirectory() as temporary:
            from git_fixtures import upstream_profile
            components = load_module('metadata_components', 'bind/shared/components.py')
            profile = upstream_profile('26.7')
            directory = make_bind_component('bind_plugin', Path(temporary) / 'plugin',
                series='26.7', creator=creator_identity(), source_commit='a' * 40,
                control_commit='c' * 40, profile=profile, version='26.7_1')
            metadata = directory / 'build-metadata.txt'
            original = metadata.read_bytes()
            manifest = components.validate_component(directory, 'bind_plugin', '26.7')
            self.assertEqual(hashlib.sha256(original).hexdigest(), manifest['assets'][metadata.name])
            self.assertEqual('a' * 40, manifest['source_commit'])
            for changes in [
                dict(source_commit='b' * 40, upstream_commit='9' * 40),
                dict(freebsd_release='15.2', tools_tag='26.7.2', creator_release='26.7.2'),
                dict(creator_ports_commit='5' * 40, creator_recipe_path='opnsense/pkg'),
            ]:
                with self.subTest(changes=changes):
                    write_build_metadata(metadata, bind920='9.20.26_2', **changes)
                    manifest['assets'][metadata.name] = hashlib.sha256(metadata.read_bytes()).hexdigest()
                    write_json(directory / 'channel.json', manifest)
                    with self.assertRaises(ValueError):
                        components.validate_component(directory, 'bind_plugin', '26.7')



if __name__ == '__main__':
    unittest.main()
