"""Shared-feed preservation, signature boundaries and publication concurrency."""
from common_imports import *

import pytest

CI = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CI))
from shared import package_catalogue as catalogue
from ha_fixtures import make_ha_channel
from git_fixtures import upstream_profile
from package_fixtures import (PLUGIN_PACKAGE, BIND_PROFILE, creator_identity, make_bind_component,
                              make_ports_recipe, make_tools_repository, runtime_approval, fixture_version_comparison,
                              write_resolved_target, write_json, plugin_manifest, BIND_COLLISION_PATH)
from bind.shared import components as bind_components


def directory_bytes(directory):
    return {path.name:path.read_bytes() for path in directory.iterdir()}


def restored_release(record):
    # Rollback restores content and notes; new uploads receive new native IDs.
    return {name:value for name,value in record.items() if name!='asset_ids'}


def byte_hashes(assets):
    return {name:hashlib.sha256(data).hexdigest() for name,data in assets.items()}


def assert_original_component(assets, component, original):
    data=json.loads(assets['channel.json'])
    record=data['components'][component]
    assert record['assets']==byte_hashes(original)
    for name,content in original.items():
        if name in record['packages']:
            assert assets.get(name)==content
        elif name not in {'data.pkg','data.tzst','packagesite.pkg','packagesite.tzst'}:
            assert assets.get(component+'-'+name)==content


@pytest.fixture
def publication_environment(tmp_path,monkeypatch):
    """Real decisions and local Git; only GitHub/native process leaves are fake."""
    from copy import deepcopy
    from urllib.parse import unquote,urlparse
    from git_fixtures import init_repository,commit,git
    from test_release_channel_archive import release_api_store
    from bind.shared import retention
    from ha_dhcp import dhcp_interface_ha_channel as ha_channel
    history=init_repository(tmp_path / 'control')
    profile=upstream_profile('26.7')
    recipes={}
    def approved_recipe(version):
        if version not in recipes:
            recipes[version]=make_ports_recipe(tmp_path / ('ports-'+version),version)
        return recipes[version]
    _,approval,_=approved_recipe('9.20.26_2')
    tools=make_tools_repository(tmp_path / 'tools',profile)
    resolve_profile=bind_components.bind920_profile.resolve_profile
    monkeypatch.setattr(bind_components.bind920_profile,'resolve_profile',lambda selected:
        resolve_profile(selected,repository=recipes[selected['version']][0]))
    first=commit(history,{'.resolver-plugins/upstream.json':json.dumps(profile),
        'dns/bind/Makefile':PLUGIN_PACKAGE+approval,
        '.github/ci/bind/runtime/build-bind920.sh':'reviewed runtime recipe',
        '.github/ci/bind/plugin/build-os-bind-rp.sh':'reviewed plugin recipe'},'reviewed initial control')
    origin=tmp_path / 'origin.git';git(tmp_path,'clone','--bare',history,origin)
    git(history,'remote','add','origin',origin.as_uri())
    monkeypatch.chdir(history)
    monkeypatch.setattr(bind_components,'ROOT',history)
    monkeypatch.setattr(catalogue,'ROOT',history)
    pkg=tmp_path / 'pkg-fixture';pkg.write_text('#!/bin/sh\nexit 99\n');pkg.chmod(0o755)
    (tmp_path / 'key').write_bytes(b'opaque native signing leaf input')
    monkeypatch.setattr(catalogue,'PKG',str(pkg))
    temporary=tmp_path / 'temporary';temporary.mkdir();monkeypatch.setenv('TMPDIR',str(temporary))
    subprocess_original=subprocess.run
    reader=creator_identity()
    selected=[reader];installed=[reader];locked=[True];serial=[0];catalogues=[0]
    remote={};pages=dict(files={'pages-health':b'resolver-plugins-pages-ok',
        'pkg/FreeBSD:14:amd64/26.1/latest/sibling.pkg':b'unchanged sibling'})
    failure=[None]
    def response(command,kwargs,stdout=''):
        return subprocess.CompletedProcess(command,0,stdout if kwargs.get('text') else stdout.encode(),'')
    def archive_identity(path):
        if path.name.startswith('os-dhcp-interface-ha-'):
            return dict(name='os-dhcp-interface-ha',version=path.name.removeprefix('os-dhcp-interface-ha-').removesuffix('.pkg'),
                        origin='opnsense/os-dhcp-interface-ha',abi='FreeBSD:15:amd64')
        return json.loads(path.read_text())
    def identity_line(identity,separator='|'):
        return separator.join(identity[field] for field in ('name','version','origin','abi'))+'\n'
    def native_run(command,**kwargs):
        if command[0]=='git': return subprocess_original(command,**kwargs)
        if command[0]=='sh' and Path(command[1]).name=='setup-opnsense-repository.sh':
            selected[0]=json.loads(Path(kwargs['env']['RP_TARGET_PKG_METADATA']).read_text())['package_creator']
            return response(command,kwargs)
        if command[0]=='openssl': return response(command,kwargs,bind_components.PUBLIC_KEY.read_text())
        if command[0]!=str(pkg): raise AssertionError(command)
        args=list(command[1:]);options={}
        while args and args[0] in {'-o','-R'}:
            flag,value=args[:2];args=args[2:]
            if flag=='-o':
                key,_,value=value.partition('=');options[key]=value
        operation=args[0]
        if operation=='-v': return response(command,kwargs,installed[0]['version'].split('_')[0]+'\n')
        if operation=='version': return response(command,kwargs,fixture_version_comparison(*args[-2:])+'\n')
        if operation=='repo':
            catalogues[0]+=1
            output=Path(args[1])
            for name in bind_components.CATALOGUE_ASSETS-{'data.tzst','packagesite.tzst'}:
                (output / name).write_bytes(f'simulated signed {name} catalogue {catalogues[0]}'.encode())
            for name in ('data','packagesite'):
                (output / (name+'.tzst')).symlink_to(name+'.pkg')
            return response(command,kwargs)
        if operation in {'lock','unlock'}:
            if '-l' in args: return response(command,kwargs,'pkg-'+installed[0]['version']+'\n' if locked[0] else '')
            locked[0]=operation=='lock';return response(command,kwargs)
        if operation=='add':
            installed[0]=archive_identity(Path(args[-1]));return response(command,kwargs)
        if operation=='which': return response(command,kwargs,'pkg-'+installed[0]['version']+'\n')
        if operation=='check': return response(command,kwargs)
        if operation=='info':
            return response(command,kwargs,Path(args[args.index('-F')+1]).read_text())
        if operation=='query':
            if '%k' in args: return response(command,kwargs,'1\n' if locked[0] else '0\n')
            identity=archive_identity(Path(args[args.index('-F')+1])) if '-F' in args else installed[0]
            if args[-1]=='%Fp|%Fs':
                files=identity.get('files',{'/usr/local/fixture/'+identity['name']:'1'*64})
                return response(command,kwargs,''.join(path+'|'+checksum+'\n' for path,checksum in files.items()))
            if args[-1].startswith('%dn'):
                separator='\t' if '\t' in args[-1] else '|'
                edges=[('bind-tools','dns/bind-tools',identity['version'])] if identity['name']=='bind920' else [
                    (name,edge['origin'],edge['version']) for name,edge in identity.get('deps',{}).items()]
                return response(command,kwargs,''.join(separator.join(edge)+'\n' for edge in edges))
            return response(command,kwargs,identity_line(identity,'\t' if '\t' in args[-1] else '|'))
        if operation in {'update','rquery','fetch'}:
            repository=None
            if 'REPOS_DIR' in options:
                configuration=next(Path(options['REPOS_DIR']).glob('*.conf')).read_text()
                url=json.loads(configuration.split('url: ',1)[1].split(',',1)[0])
                repository=Path(unquote(urlparse(url).path))
            if operation=='update': return response(command,kwargs)
            packages=[path for path in repository.glob('*.pkg') if path.name not in {'data.pkg','packagesite.pkg'}] if repository else []
            if operation=='rquery':
                return response(command,kwargs,''.join(identity_line(archive_identity(path)) for path in packages) if repository else identity_line(selected[0]))
            output=Path(args[args.index('-o')+1]) / 'All';output.mkdir(parents=True)
            if repository:
                for path in packages: shutil.copyfile(path,output / path.name)
            else:
                write_json(output / ('pkg-'+selected[0]['version']+'.pkg'),selected[0])
            return response(command,kwargs)
        raise AssertionError(command)
    def control(version,label):
        git(history,'checkout','master')
        _,approval,_=approved_recipe(version)
        selected_commit=commit(history,{'dns/bind/Makefile':PLUGIN_PACKAGE+approval,
            'reviewed-change':label},label)
        git(origin,'fetch',history.as_uri(),'master:master')
        return selected_commit
    def write_source_definition(run_root,source):
        path=run_root / 'source-package.mk'
        path.write_bytes(subprocess.check_output(['git','-C',str(history),'show',source+':dns/bind/Makefile']))
        return path
    def select(component,version,*,source=None,creator=None):
        serial[0]+=1
        run_root=tmp_path / f'run-{serial[0]}';run_root.mkdir()
        current=git(history,'rev-parse','HEAD')
        maker=creator or creator_identity(version='2.6.0')
        role_directory=run_root / 'roles';role_directory.mkdir()
        creator_path=write_resolved_target(role_directory / 'creator-target.json',profile,creator=maker)
        reader_path=write_resolved_target(role_directory / 'reader-target.json',profile,role='reader',creator=reader)
        recipe=history / ('.github/ci/bind/runtime/build-bind920.sh' if component=='bind_runtime' else '.github/ci/bind/plugin/build-os-bind-rp.sh')
        result=bind_components.prepare_selection(run_root / 'trusted',component=component,series='26.7',mode='production',
            control_commit=current,profile_commit=source or current,source_commit=source if component=='bind_plugin' else None,
            upstream=history / '.resolver-plugins/upstream.json',runtime_approval=history / 'dns/bind/Makefile',
            package_definition=write_source_definition(run_root,source) if component=='bind_plugin' else None,
            tools_repository=tools,recipe=recipe,repository='example.invalid/distribution',
            intake_directory=run_root / 'intake',creator_target=creator_path,reader_target=reader_path)
        return SimpleNamespace(root=run_root,control=current,source=source,component=component,version=version,creator=maker,
            selection=json.loads((run_root / 'trusted/selection.json').read_text()),selection_sha256=result['selection_sha256'])
    def producer(run):
        raw=run.root / 'producer';raw.mkdir()
        if run.selection['producer_action']=='reuse':
            original=run.root / 'intake/cached_component'
        else:
            receipt=json.loads((run.root / 'trusted/runtime-selection.json').read_text()) if run.component=='bind_plugin' else None
            original=make_bind_component(run.component,run.root / 'built',series='26.7',creator=run.creator,
                source_commit=run.source or run.control,control_commit=run.control,profile=profile,version=run.version,
                recipe_sha256=hashlib.sha256((history / ('.github/ci/bind/runtime/build-bind920.sh' if run.component=='bind_runtime' else '.github/ci/bind/plugin/build-os-bind-rp.sh')).read_bytes()).hexdigest(),
                runtime_receipt=receipt,package_definition=run.root / 'trusted/plugin-package.mk' if run.component=='bind_plugin' else None,
                bind_profile=json.loads((run.root / 'trusted/bind920-profile.json').read_text()))
        names=json.loads((original / 'channel.json').read_text())['packages']
        for name in set(names) | ({'bind920-provenance.json'} if run.component=='bind_runtime' else {'build-metadata.txt','plugin-build-inputs.json'}):
            shutil.copyfile(original / name,raw / name)
        return raw
    def stage_run(run):
        git(history,'checkout','--detach',run.control)
        run.composition_sha256=bind_components.stage_repository(producer(run),run.selection,run.root / 'trusted',run.root / 'repository',
            tmp_path / 'key',pkg_command=str(pkg))
        return run
    def publish_run(run):
        git(history,'checkout','--detach',run.control)
        run.publish_attempt=getattr(run,'publish_attempt',0)+1
        recovery=run.root / ('recovery' if run.publish_attempt==1 else f'recovery-{run.publish_attempt}')
        bind_components.publish_component('example.invalid/distribution',run.root / 'repository',run.root / 'trusted',recovery,
            component=run.component,series='26.7',selection_sha256=run.selection_sha256,composition_sha256=run.composition_sha256)
    def publish_ha(version,creator):
        serial[0]+=1
        directory=tmp_path / f'ha-{serial[0]}';directory.mkdir()
        source=git(history,'rev-parse','HEAD')
        own=make_ha_channel(directory / 'current',version=version,source=source,profile=source,creator=creator)
        runtime=directory / 'runtime';plugin=directory / 'plugin'
        catalogue.fetch('example.invalid/distribution','bind_runtime','26.7',runtime)
        catalogue.fetch('example.invalid/distribution','bind_plugin','26.7',plugin)
        upstream=write_json(directory / 'upstream.json',profile)
        creator_target=write_resolved_target(directory / 'creator-target.json',profile,creator=creator)
        write_resolved_target(directory / 'reader-target.json',profile,role='reader',creator=reader)
        result=catalogue.stage(runtime,plugin,own,directory / 'combined',series='26.7',key=tmp_path / 'key',
            creator_target=creator_target,upstream=upstream,approved_profile=write_json(directory / 'bind920-profile.json',
                json.loads((runtime / 'bind920-profile.json').read_text())),
            runtime_approval=history / 'dns/bind/Makefile',owner='dhcp',composition_output=directory / 'composition-inputs.json',repository='example.invalid/distribution')
        composition=bind_components.load_composition(directory,component='dhcp',series='26.7',composition_sha256=result['composition_sha256'])
        bind_components.check_composition_peers('example.invalid/distribution',composition)
        ha_channel.promote('example.invalid/distribution',own,directory / 'recovery')
        bind_components.check_composition_peers('example.invalid/distribution',composition,owned_must_match=True)
        catalogue.publish('example.invalid/distribution',directory,directory / 'pages-recovery',component='dhcp',series='26.7',composition_sha256=result['composition_sha256'])
        return directory
    def snapshot(): return deepcopy((remote,pages))
    def feed():
        prefix='pkg/FreeBSD:15:amd64/26.7/latest/'
        return {name.removeprefix(prefix):data for name,data in pages['files'].items() if name.startswith(prefix)}
    with release_api_store(remote,pages,failure,native_run):
        yield SimpleNamespace(root=tmp_path,history=history,first=first,remote=remote,pages=pages,failure=failure,
            control=control,select=select,producer=producer,stage=stage_run,publish=publish_run,ha=publish_ha,snapshot=snapshot,feed=feed,
            pkg=str(pkg),retention=retention)


def test_runtime_release_advances_pair_and_preserves_plugin_and_ha(publication_environment):
    from copy import deepcopy
    environment=publication_environment
    r0=environment.stage(environment.select('bind_runtime','9.20.26_2',creator=creator_identity()))
    environment.publish(r0)
    source=environment.control('9.20.26_2','reviewed initial plugin')
    p0=environment.stage(environment.select('bind_plugin','26.7_1',source=source,
        creator=creator_identity(version='2.4.0',origin='opnsense/pkg')))
    environment.publish(p0)
    environment.control('9.20.26_2','reviewed initial HA')
    environment.ha('0.2_43',creator_identity(version='2.5.0'))
    runtime0=deepcopy(environment.remote['pkg-26.7-bind-runtime']['assets'])
    plugin0=deepcopy(environment.remote['pkg-26.7-bind-plugin']['assets'])
    ha0=deepcopy(environment.remote['pkg-dhcp-interface-ha-26.7']['assets'])
    initial_stack=deepcopy(environment.remote['pkg-26.7']['assets'])
    initial_feed=environment.feed()
    assert json.loads(plugin0['channel.json'])['package_creator']['version']=='2.4.0'
    assert json.loads(ha0['channel.json'])['package_creator']['version']=='2.5.0'
    original_runtime=json.loads(plugin0['channel.json'])['built_against_runtime']
    assert original_runtime['packages']=={name:hashlib.sha256(runtime0[name]).hexdigest() for name in ('bind-tools-9.20.26_2.pkg','bind920-9.20.26_2.pkg')}
    assert_original_component(initial_stack,'bind_plugin',plugin0)
    assert_original_component(initial_feed,'dhcp',ha0)
    first_advance=None
    for number in range(1,13):
        version=f'9.20.{26+number}_2'
        environment.control(version,f'reviewed runtime {number}')
        run=environment.stage(environment.select('bind_runtime',version))
        candidate=directory_bytes(run.root / 'repository/component/current')
        fingerprint=json.loads(candidate['channel.json'])['build_fingerprint']
        tag=f'pkg-26.7-bind-runtime-{version}-{fingerprint}'
        environment.publish(run)
        assert environment.remote['pkg-26.7-bind-runtime']['assets']==candidate
        assert environment.remote[tag]['assets']==candidate
        stack=environment.remote['pkg-26.7']['assets']
        data=json.loads(stack['channel.json'])
        assert set(data['packages'])=={f'bind-tools-{version}.pkg',f'bind920-{version}.pkg','os-bind-rp-26.7_1.pkg'}
        assert_original_component(stack,'bind_runtime',candidate)
        assert_original_component(stack,'bind_plugin',plugin0)
        assert_original_component(environment.feed(),'bind_runtime',candidate)
        assert_original_component(environment.feed(),'bind_plugin',plugin0)
        assert_original_component(environment.feed(),'dhcp',ha0)
        assert json.loads(environment.remote['pkg-26.7-bind-plugin']['assets']['channel.json'])['built_against_runtime']==original_runtime
        if first_advance is None: first_advance=tag
    assert environment.feed()!=initial_feed
    sentinels={'os-bind-rp-26.7-26.7_1-plugin-'+'1'*64,'pr-51-26.7-bind-runtime','pkg-dhcp-interface-ha-26.7-0.2_43',
        'pkg-26.1-bind-runtime-9.20.26_2-'+'2'*64}
    prototype=deepcopy(environment.remote['pkg-26.7-bind-runtime'])
    for tag in sentinels-{'pkg-dhcp-interface-ha-26.7-0.2_43'}:
        environment.remote[tag]=deepcopy(prototype)
    for number in range(6):
        record=deepcopy(prototype);record['assets']={'legacy-self-contained.pkg':b'opaque bytes'}
        record['created_at']=f'2026-02-{number+1:02}T00:00:00Z'
        environment.remote[f'pkg-26.7-os-bind-rp-1.36_{number+1}']=record
    environment.retention.prune('example.invalid/distribution',run.root / 'repository',run.root / 'trusted',environment.root / 'retention',
        component='bind_runtime',series='26.7',selection_sha256=run.selection_sha256,composition_sha256=run.composition_sha256)
    assert original_runtime['snapshot_tag'] in environment.remote
    assert environment.remote[original_runtime['snapshot_tag']]['assets']==runtime0
    assert first_advance not in environment.remote
    assert sentinels <= set(environment.remote)
    assert 'pkg-26.7-os-bind-rp-1.36_1' not in environment.remote
    assert len([tag for tag in environment.remote if re.fullmatch(r'pkg-26\.7-os-bind-rp-1\.36_[1-6]',tag)])==5
    bind_before={tag:deepcopy(environment.remote[tag]) for tag in ('pkg-26.7-bind-runtime','pkg-26.7-bind-plugin','pkg-26.7')}
    environment.control(version,'reviewed HA advance')
    ha=environment.ha('0.2_44',creator_identity(version='2.5.0'))
    assert bind_before=={tag:environment.remote[tag] for tag in bind_before}
    assert environment.remote['pkg-dhcp-interface-ha-26.7']['assets']==directory_bytes(ha / 'current')
    assert_original_component(environment.feed(),'bind_runtime',bind_before['pkg-26.7-bind-runtime']['assets'])
    assert_original_component(environment.feed(),'bind_plugin',plugin0)
    assert_original_component(environment.feed(),'dhcp',directory_bytes(ha / 'current'))
    assert environment.pages['files']['pages-health']==b'resolver-plugins-pages-ok'
    assert environment.pages['files']['pkg/FreeBSD:14:amd64/26.1/latest/sibling.pkg']==b'unchanged sibling'


def test_plugin_release_uses_frozen_runtime_and_preserves_runtime_and_ha(publication_environment):
    from copy import deepcopy
    environment=publication_environment
    maker=creator_identity(version='2.4.0',origin='opnsense/pkg')
    a=environment.stage(environment.select('bind_runtime','9.20.26_2',creator=creator_identity()))
    environment.publish(a)
    source0=environment.control('9.20.26_2','reviewed original plugin')
    p0=environment.stage(environment.select('bind_plugin','26.7_1',source=source0,creator=maker))
    environment.publish(p0)
    source1=environment.control('9.20.26_2','reviewed plugin advance')
    selected_a=environment.stage(environment.select('bind_plugin','26.7_2',source=source1,creator=maker))
    frozen=json.loads((selected_a.root / 'trusted/runtime-selection.json').read_text())
    original_a=deepcopy(environment.remote['pkg-26.7-bind-runtime']['assets'])
    assert frozen['assets']==byte_hashes(original_a)
    environment.control('9.20.27_2','reviewed runtime B')
    b=environment.stage(environment.select('bind_runtime','9.20.27_2'))
    environment.publish(b)
    before=environment.snapshot()
    intake=environment.root / 'frozen-A'
    bind_components.fetch_frozen_component('example.invalid/distribution',frozen,intake)
    assert directory_bytes(intake)==original_a
    assert directory_bytes(intake)!=environment.remote['pkg-26.7-bind-runtime']['assets']
    bind_components.select_role(selected_a.root / 'trusted/upstream.json',selected_a.root / 'trusted/reader-target.json','26.7','reader',environment.pkg)
    authenticated=bind_components.freeze_runtime(intake,json.loads((selected_a.root / 'trusted/bind920-profile.json').read_text()),
        json.loads((selected_a.root / 'trusted/upstream.json').read_text()),'26.7',
        requirement=bind_components.plugin_requirement_from_makefile((selected_a.root / 'trusted/plugin-package.mk').read_text()),
        pkg_command=environment.pkg)
    assert authenticated==frozen
    with pytest.raises(RuntimeError): environment.publish(selected_a)
    assert before==environment.snapshot()
    environment.control('9.20.27_2','qualify plugin against B')
    selected_b=environment.stage(environment.select('bind_plugin','26.7_2',source=source1,creator=maker))
    assert json.loads((selected_b.root / 'repository/composition-inputs.json').read_text())['components']['dhcp'] is None
    runtime_b=deepcopy(environment.remote['pkg-26.7-bind-runtime']['assets'])
    environment.control('9.20.27_2','reviewed first HA')
    environment.ha('0.2_43',creator_identity(version='2.5.0'))
    before_ha=environment.snapshot()
    with pytest.raises(RuntimeError): environment.publish(selected_b)
    assert before_ha==environment.snapshot()
    assert environment.remote['pkg-26.7-bind-runtime']['assets']==runtime_b
    environment.control('9.20.27_2','fresh plugin qualification with HA')
    fresh=environment.stage(environment.select('bind_plugin','26.7_2',source=source1,creator=maker))
    ha=deepcopy(environment.remote['pkg-dhcp-interface-ha-26.7']['assets'])
    candidate=directory_bytes(fresh.root / 'repository/component/current')
    environment.publish(fresh)
    assert environment.remote['pkg-26.7-bind-plugin']['assets']==candidate
    original=json.loads(candidate['channel.json'])
    assert original['source_commit']==source1
    assert original['plugin_version']=='26.7_2'
    build_runtime=original['built_against_runtime']
    assert build_runtime['packages']=={name:hashlib.sha256(runtime_b[name]).hexdigest() for name in ('bind-tools-9.20.27_2.pkg','bind920-9.20.27_2.pkg')}
    assert environment.remote['pkg-26.7-bind-runtime']['assets']==runtime_b
    assert environment.remote['pkg-dhcp-interface-ha-26.7']['assets']==ha
    assert_original_component(environment.feed(),'bind_runtime',runtime_b)
    assert_original_component(environment.feed(),'dhcp',ha)
    environment.control('9.20.28_2','reviewed runtime C')
    c=environment.stage(environment.select('bind_runtime','9.20.28_2'))
    environment.publish(c)
    runtime_c=deepcopy(environment.remote['pkg-26.7-bind-runtime']['assets'])
    reuse=environment.select('bind_plugin','26.7_2',source=source1,creator=maker)
    assert reuse.selection['producer_action']=='reuse'
    assert reuse.selection['expected_fingerprint']==original['build_fingerprint']
    environment.stage(reuse)
    environment.publish(reuse)
    assert environment.remote['pkg-26.7-bind-plugin']['assets']==candidate
    assert json.loads(environment.remote['pkg-26.7-bind-plugin']['assets']['channel.json'])['built_against_runtime']==build_runtime
    qualified=json.loads(environment.remote['pkg-26.7']['assets']['channel.json'])['components']['bind_runtime']
    assert qualified['packages']=={name:hashlib.sha256(runtime_c[name]).hexdigest() for name in ('bind-tools-9.20.28_2.pkg','bind920-9.20.28_2.pkg')}
    assert qualified['snapshot_tag']!=build_runtime['snapshot_tag']
    assert_original_component(environment.remote['pkg-26.7']['assets'],'bind_plugin',candidate)
    assert_original_component(environment.feed(),'bind_runtime',runtime_c)
    assert_original_component(environment.feed(),'bind_plugin',candidate)
    assert_original_component(environment.feed(),'dhcp',ha)


def test_bootstrap_and_partial_publication_retry_preserve_immutable_state(publication_environment):
    from copy import deepcopy
    from git_fixtures import git
    environment=publication_environment
    initial_pages=deepcopy(environment.pages['files'])
    r0=environment.stage(environment.select('bind_runtime','9.20.26_2',creator=creator_identity()))
    own0=directory_bytes(r0.root / 'repository/component/current')
    environment.publish(r0)
    fingerprint0=json.loads(own0['channel.json'])['build_fingerprint']
    assert set(environment.remote)=={'pkg-26.7-bind-runtime',f'pkg-26.7-bind-runtime-9.20.26_2-{fingerprint0}'}
    assert environment.pages['files']==initial_pages
    assert environment.feed()=={}
    source0=environment.control('9.20.26_2','reviewed bootstrap plugin')
    maker=creator_identity(version='2.4.0')
    p0=environment.stage(environment.select('bind_plugin','26.7_1',source=source0,creator=maker))
    environment.failure[0]=('pages',None)
    with pytest.raises(RuntimeError): environment.publish(p0)
    assert environment.pages['files']==initial_pages
    original_stack=deepcopy(environment.remote['pkg-26.7'])
    completed_bootstrap={tag:deepcopy(state) for tag,state in environment.remote.items()
        if tag not in {'pkg-26.7','pkg-26.7-bind-runtime','pkg-26.7-bind-plugin'}}
    # A foreign registered catalogue with the same pair is not this publication.
    foreign=environment.remote['pkg-26.7']['assets']
    foreign['meta.conf']=b'another qualified catalogue'
    foreign_manifest=json.loads(foreign['channel.json'])
    foreign_manifest['assets']['meta.conf']=hashlib.sha256(foreign['meta.conf']).hexdigest()
    foreign['channel.json']=json.dumps(foreign_manifest).encode()
    foreign_before=environment.snapshot()
    with pytest.raises(RuntimeError,match='exact qualified stack'): environment.publish(p0)
    assert environment.snapshot()==foreign_before
    environment.remote['pkg-26.7']=original_stack
    environment.publish(p0)
    assert completed_bootstrap=={tag:environment.remote[tag] for tag in completed_bootstrap}
    plugin0=deepcopy(environment.remote['pkg-26.7-bind-plugin']['assets'])
    plugin_fingerprint=json.loads(plugin0['channel.json'])['build_fingerprint']
    assert set(json.loads(environment.remote['pkg-26.7']['assets']['channel.json'])['packages'])=={
        'bind-tools-9.20.26_2.pkg','bind920-9.20.26_2.pkg','os-bind-rp-26.7_1.pkg'}
    assert_original_component(environment.feed(),'bind_runtime',own0)
    assert_original_component(environment.feed(),'bind_plugin',plugin0)
    for tag in ('pkg-26.7-bind-runtime','pkg-26.7'):
        environment.remote[tag]['name']='Saved current '+tag
        environment.remote[tag]['body']='Original notes\nwith a second line\n'
    environment.remote['pkg-26.7-bind-runtime']['isPrerelease']=True
    environment.control('9.20.27_2','reviewed runtime for current rollback')
    r1=environment.stage(environment.select('bind_runtime','9.20.27_2'))
    own1=directory_bytes(r1.root / 'repository/component/current')
    fingerprint1=json.loads(own1['channel.json'])['build_fingerprint']
    runtime1=f'pkg-26.7-bind-runtime-9.20.27_2-{fingerprint1}'
    paired1=f'pkg-26.7-os-bind-rp-26.7_1-bind-{fingerprint1}-plugin-{plugin_fingerprint}'
    current_before={tag:deepcopy(restored_release(environment.remote[tag])) for tag in ('pkg-26.7-bind-runtime','pkg-26.7-bind-plugin','pkg-26.7')}
    feed_before=environment.feed()
    environment.failure[0]=('upload','pkg-26.7')
    with pytest.raises(subprocess.CalledProcessError): environment.publish(r1)
    assert current_before=={tag:restored_release(environment.remote[tag]) for tag in current_before}
    assert environment.feed()==feed_before
    assert environment.remote[runtime1]['assets']==own1 and not environment.remote[runtime1]['draft']
    assert environment.remote[paired1]['assets']==directory_bytes(r1.root / 'repository/stack/current')
    assert not environment.remote[paired1]['draft']
    recovery=json.loads((r1.root / 'recovery/releases/restoration.json').read_text())
    assert recovery['restore_errors']==[]
    completed={tag:deepcopy(environment.remote[tag]) for tag in (runtime1,paired1)}
    assert all(state['asset_ids'] for state in completed.values())
    # Full state equality below catches even byte-identical immutable reuploads.
    signed_before={path.relative_to(r1.root / 'repository').as_posix():path.read_bytes()
        for path in (r1.root / 'repository').rglob('*') if path.is_file()}
    environment.publish(r1)
    assert signed_before=={path.relative_to(r1.root / 'repository').as_posix():path.read_bytes()
        for path in (r1.root / 'repository').rglob('*') if path.is_file()}
    assert completed=={tag:environment.remote[tag] for tag in completed}
    assert environment.remote['pkg-26.7-bind-runtime']['assets']==own1
    assert environment.remote['pkg-26.7']['assets']==completed[paired1]['assets']
    environment.control('9.20.28_2','reviewed runtime for draft retry')
    r2=environment.stage(environment.select('bind_runtime','9.20.28_2'))
    own2=directory_bytes(r2.root / 'repository/component/current')
    fingerprint2=json.loads(own2['channel.json'])['build_fingerprint']
    runtime2=f'pkg-26.7-bind-runtime-9.20.28_2-{fingerprint2}'
    paired2=f'pkg-26.7-os-bind-rp-26.7_1-bind-{fingerprint2}-plugin-{plugin_fingerprint}'
    current_before={tag:deepcopy(restored_release(environment.remote[tag])) for tag in ('pkg-26.7-bind-runtime','pkg-26.7')}
    environment.failure[0]=('upload',paired2)
    with pytest.raises(subprocess.CalledProcessError): environment.publish(r2)
    assert current_before=={tag:restored_release(environment.remote[tag]) for tag in current_before}
    assert environment.remote[runtime2]['assets']==own2 and not environment.remote[runtime2]['draft']
    partial=deepcopy(environment.remote[paired2])
    assert partial['draft'] and 'channel.json' not in partial['assets']
    assert (r2.root / 'recovery/releases/unfinished-snapshots.json').is_file()
    published_before={tag:deepcopy(state) for tag,state in environment.remote.items()
        if tag.startswith('pkg-26.7-') and tag not in {'pkg-26.7-bind-runtime','pkg-26.7-bind-plugin'} and not state['draft']}
    environment.publish(r2)
    assert not environment.remote[paired2]['draft']
    assert environment.remote[paired2]['assets']==directory_bytes(r2.root / 'repository/stack/current')
    assert published_before=={tag:environment.remote[tag] for tag in published_before}
    environment.control('9.20.29_2','reviewed runtime for Pages retry')
    r3=environment.stage(environment.select('bind_runtime','9.20.29_2'))
    own3=directory_bytes(r3.root / 'repository/component/current')
    feed_before=environment.feed()
    head_before=environment.pages['head']
    environment.failure[0]=('pages',None)
    with pytest.raises(RuntimeError): environment.publish(r3)
    assert environment.feed()==feed_before
    assert environment.pages['head']==head_before
    assert environment.remote['pkg-26.7-bind-runtime']['assets']==own3
    assert environment.remote['pkg-26.7']['assets']==directory_bytes(r3.root / 'repository/stack/current')
    assert json.loads((r3.root / 'recovery/failure.json').read_text())['phase']=='pages'
    immutable_before={tag:deepcopy(state) for tag,state in environment.remote.items()
        if tag.startswith('pkg-26.7-') and tag not in {'pkg-26.7-bind-runtime','pkg-26.7-bind-plugin'} and not state['draft']}
    environment.publish(r3)
    assert immutable_before=={tag:environment.remote[tag] for tag in immutable_before}
    assert environment.feed()!=feed_before
    source1=environment.control('9.20.29_2','reviewed peer plugin advance')
    p1=environment.stage(environment.select('bind_plugin','26.7_2',source=source1,creator=maker))
    environment.publish(p1)
    plugin1=deepcopy(environment.remote['pkg-26.7-bind-plugin']['assets'])
    peer_advanced=environment.snapshot()
    with pytest.raises(RuntimeError): environment.publish(r3)
    assert environment.snapshot()==peer_advanced
    assert environment.remote['pkg-26.7-bind-runtime']['assets']==own3
    assert environment.remote['pkg-26.7-bind-plugin']['assets']==plugin1
    assert_original_component(environment.remote['pkg-26.7']['assets'],'bind_runtime',own3)
    assert_original_component(environment.remote['pkg-26.7']['assets'],'bind_plugin',plugin1)
    assert_original_component(environment.feed(),'bind_runtime',own3)
    assert_original_component(environment.feed(),'bind_plugin',plugin1)
    assert environment.feed()!=feed_before
    git(environment.history,'checkout','--detach',r1.control)
    backward=environment.stage(environment.select('bind_runtime','9.20.27_2'))
    before=environment.snapshot()
    with pytest.raises(RuntimeError): environment.publish(backward)
    assert before==environment.snapshot()


@pytest.fixture
def channels(tmp_path, monkeypatch):
    profile=upstream_profile('26.7')
    runtime=make_bind_component('bind_runtime',tmp_path / 'runtime',series='26.7',creator=creator_identity(),
        source_commit='a'*40,control_commit='b'*40,profile=profile,version='9.20.26_2')
    plugin=make_bind_component('bind_plugin',tmp_path / 'plugin',series='26.7',creator=creator_identity(version='2.4.0'),
        source_commit='c'*40,control_commit='d'*40,profile=profile,version='26.7_1')
    ha=make_ha_channel(tmp_path / 'dhcp',version='0.2_43',source='f'*40,profile='a'*40)
    identities={p.name:(name,version,origin,'FreeBSD:15:amd64') for directory,name,version,origin in [
        (runtime,'bind-tools','9.20.26_2','dns/bind-tools'),(runtime,'bind920','9.20.26_2','dns/bind920'),
        (plugin,'os-bind-rp','26.7_1','opnsense/os-bind-rp'),(ha,'os-dhcp-interface-ha','0.2_43','opnsense/os-dhcp-interface-ha')]
        for p in [directory / f'{name}-{version}.pkg']}
    write_json(tmp_path / 'trusted-upstream.json',profile)
    write_resolved_target(tmp_path / 'creator-target.json',profile)
    write_resolved_target(tmp_path / 'reader-target.json',profile,role='reader')
    write_json(tmp_path / 'approved-profile.json',BIND_PROFILE)
    (tmp_path / 'runtime-approval.mk').write_text(runtime_approval())
    def query(path,command):
        identity=identities[path.name]
        edges={('bind-tools','dns/bind-tools',identity[1])} if identity[0]=='bind920' else (
            {('opnsense','opnsense/opnsense','26.1.11_10')} if identity[0]=='os-bind-rp' else set())
        return identity,edges
    def sign(packages,output,key,pkg,metadata):
        output.mkdir()
        for package in packages: shutil.copyfile(package,output / package.name)
        for name in catalogue.CATALOGUE_ASSETS: (output / name).write_bytes(b'new signed catalogue')
    monkeypatch.setattr(catalogue.releases,'query_package',query)
    monkeypatch.setattr(catalogue.releases,'read_package_manifest',lambda *a:plugin_manifest())
    monkeypatch.setattr(catalogue.target_pkg,'verify_target_pkg',Mock())
    monkeypatch.setattr(catalogue.bind,'select_role',Mock())
    monkeypatch.setattr(catalogue.package_checksums,'verify_archive',lambda command,path:
        list(json.loads(path.read_text())['files'].items()) if path.name.startswith('os-bind-rp-') else [])
    def output(command,**kwargs):
        if command[1:3]==['version','-t']:
            return fixture_version_comparison(*command[-2:])+'\n'
        return catalogue.PUBLIC_KEY.read_bytes()
    monkeypatch.setattr(catalogue.subprocess,'check_output',Mock(side_effect=output))
    monkeypatch.setattr(catalogue,'verify_packages',Mock())
    monkeypatch.setattr(catalogue.releases,'stage_selected_repository',Mock(side_effect=sign))
    return runtime,plugin,ha,identities



def stage(channels,tmp_path):
    output=tmp_path / 'combined'
    catalogue.stage(*channels[:3],output,series='26.7',key=tmp_path / 'key',
        creator_target=tmp_path / 'creator-target.json',upstream=tmp_path / 'trusted-upstream.json',
        approved_profile=tmp_path / 'approved-profile.json',runtime_approval=tmp_path / 'runtime-approval.mk')
    return output


@pytest.mark.parametrize('fault',['key','series','abi'])
def test_untrusted_or_incompatible_component_is_rejected_before_combined_signing(channels,tmp_path,fault):
    runtime,plugin,ha,identities=channels
    if fault=='key':
        (runtime / 'resolver-plugins.pub').write_bytes(b'foreign key')
        data=json.loads((runtime / 'channel.json').read_text())
        data['assets']['resolver-plugins.pub']=hashlib.sha256(b'foreign key').hexdigest()
        write_json(runtime / 'channel.json',data)
    elif fault=='series':
        data=json.loads((runtime / 'channel.json').read_text());data['series']='26.1';write_json(runtime / 'channel.json',data)
    else:
        name='os-dhcp-interface-ha-0.2_43.pkg';identities[name]=(*identities[name][:3],'FreeBSD:14:amd64')
    with pytest.raises(ValueError): stage(channels,tmp_path)
    catalogue.releases.stage_selected_repository.assert_not_called()
    assert not (tmp_path / 'combined').exists()



@pytest.mark.parametrize('fault,rehash,error', [
    ('bytes', False, 'assets'), ('missing', False, 'assets'),
    ('extra', False, 'assets'),
    ('bytes', True, 'package bytes differ from original provenance'),
    ('metadata', True, 'sidecar differs from original release'),
    ('key', True, 'key differs from committed public key'),
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
    elif fault == 'metadata':
        (output / 'dhcp-build-metadata.txt').write_text('wrong source')
    else:
        key = output / 'dhcp-resolver-plugins.pub'
        key.write_bytes(b'foreign retained key')
        original = output / 'dhcp-channel.json'
        inner = json.loads(original.read_text())
        inner['assets']['resolver-plugins.pub'] = hashlib.sha256(key.read_bytes()).hexdigest()
        write_json(original, inner)
        manifest = output / 'channel.json'
        data = json.loads(manifest.read_text())
        data['components']['dhcp']['assets']['resolver-plugins.pub'] = inner['assets']['resolver-plugins.pub']
        data['components']['dhcp']['assets']['channel.json'] = hashlib.sha256(original.read_bytes()).hexdigest()
        write_json(manifest, data)
    if rehash:
        manifest = output / 'channel.json'
        data = json.loads(manifest.read_text())
        data['assets'] = catalogue.releases.directory_checksums(output)
        data['assets'].pop('channel.json')
        manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError, match=error):
        catalogue.validate(output)


def test_static_publisher_rejects_component_removal_before_any_mutation(channels,tmp_path,monkeypatch):
    output=tmp_path / 'combined'
    catalogue.stage(channels[0],channels[1],None,output,series='26.7',key=tmp_path / 'key',
        creator_target=tmp_path / 'creator-target.json',upstream=tmp_path / 'trusted-upstream.json',
        approved_profile=tmp_path / 'approved-profile.json',runtime_approval=tmp_path / 'runtime-approval.mk')
    def api(endpoint,**kwargs):
        assert not kwargs,'no mutation before preservation guard'
        if '/git/ref/' in endpoint: return {'object':{'sha':'1'*40}}
        if '/git/commits/' in endpoint: return {'tree':{'sha':'2'*40}}
        if '/git/trees/' in endpoint: return {'truncated':False,'tree':[{'path':'pkg/FreeBSD:15:amd64/26.7/latest/channel.json','sha':'3'*40}]}
        return {'content':base64.b64encode(json.dumps({'kind':'combined','components':dict.fromkeys(catalogue.COMPONENTS)}).encode()).decode()}
    monkeypatch.setattr(catalogue.releases,'gh_api',api)
    with pytest.raises(RuntimeError,match='remove an existing component'):
        catalogue.releases.publish_abi_channel('example/distribution',output,tmp_path / 'recovery')



@pytest.fixture
def executable_directory():
    root = CI.parent / 'ci-local'
    root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='catalogue-', dir=root) as directory:
        yield Path(directory)


@pytest.mark.parametrize('fault,layout', [('', '.'),('', 'All'),('extra-package','All'),('archive-bytes','All'),('archive-abi','All')])
def test_repository_verification_observes_isolated_catalogue_and_exact_archives(tmp_path, executable_directory, monkeypatch, fault, layout):
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
        output = Path(args[args.index('-o', 7) + 1]) / LAYOUT
        output.mkdir(parents=True)
        for package in packages:
            shutil.copyfile(package, output / package.name)
        if fault == 'archive-bytes':
            (output / packages[-1].name).write_text('different bytes')
    else:
        raise AssertionError(args)
'''.replace('FAULT', repr(fault)).replace('ARCHIVES', repr([str(p) for p in packages]))
       .replace('LAYOUT', repr(layout))
       .replace('EXPECTED_KEY', repr(catalogue.PUBLIC_KEY.read_bytes()))
       .replace('VERIFICATION_ROOT', repr(str(verification))))
    command.chmod(0o755)
    write_json(verification / 'installed.json', dict(identity=['pkg', '2.3.1_1', 'ports-mgmt/pkg', 'FreeBSD:15:amd64'],
        owner='pkg-2.3.1_1', manifest=hashlib.sha256(command.read_bytes()).hexdigest()))
    monkeypatch.setattr(catalogue, 'PKG', str(command))
    if not fault:
        catalogue.verify_packages(packages, 'https://packages.example.invalid/feed', reader, upstream)
        assert set((verification / 'commands.log').read_text().splitlines()) == {'update', 'rquery', 'fetch'}
        assert {p.name: p.read_bytes() for p in (verification / 'fetch' / layout).iterdir()} == {p.name: p.read_bytes() for p in packages}
    else:
        error = {'extra-package': 'identities', 'archive-bytes': 'archive differs', 'archive-abi': 'ABI differs'}[fault]
        with pytest.raises(ValueError, match=error):
            catalogue.verify_packages(packages, 'https://packages.example.invalid/feed', reader, upstream)


def test_fetch_rejects_an_unpublished_registered_runtime(channels,tmp_path,monkeypatch):
    snapshot=SimpleNamespace(existed=True,draft=True,directory=channels[0])
    monkeypatch.setattr(catalogue.releases,'snapshot_release',lambda *a:snapshot)
    with pytest.raises(ValueError,match='unpublished'):
        catalogue.fetch('example/distribution','bind_runtime','26.7',tmp_path / 'draft')
    assert not (tmp_path / 'draft').exists()


def test_published_corrupt_pair_is_refused_without_rebuilding(channels,tmp_path,monkeypatch):
    receipts=[catalogue.receipt(directory,kind,'26.7') for kind,directory in zip(bind_components.KINDS,channels[:2])]
    partial=tmp_path / 'partial';partial.mkdir();(partial / 'meta.conf').write_bytes(b'partial upload')
    snapshot=SimpleNamespace(existed=True,draft=False,directory=partial)
    monkeypatch.setattr(bind_components.releases,'snapshot_release',lambda *args:snapshot)
    with pytest.raises((OSError,ValueError)):
        bind_components.materialize_stack('example/distribution',*receipts,tmp_path / 'bad/current')
    assert not (tmp_path / 'bad/current').exists()


def test_installed_checkpoint_separates_exact_plugin_from_policy_eligible_runtime(tmp_path,monkeypatch):
    from bind.shared import verification
    identities={name:(name,version,origin,'FreeBSD:15:amd64') for name,version,origin in [
        ('bind-tools','9.20.26_2','dns/bind-tools'),('bind920','9.20.26_2','dns/bind920'),
        ('os-bind-rp','26.7_1','opnsense/os-bind-rp')]}
    packages=[tmp_path / f'{name}-{identity[1]}.pkg' for name,identity in identities.items()]
    expected=[('/usr/local/file','1'*64)]
    actual={name:[(f'/usr/local/{name}','2'*64 if name.startswith('bind') else '1'*64)] for name in identities}
    manifest=plugin_manifest()
    del manifest['dep_formula']
    actual['os-bind-rp']=list(manifest['files'].items())
    owners={path:f'{name}-{identities[name][1]}' for name,rows in actual.items() for path,_ in rows}
    core=('opnsense','26.7.3','opnsense/opnsense','FreeBSD:15:amd64')
    manifest['deps']['opnsense']['version']=core[1]
    anchor=tmp_path / 'bind.inc';anchor.write_bytes(b'fixture bind.inc\n')
    native_path=verification.Path
    monkeypatch.setattr(verification,'Path',lambda value:anchor if str(value)==BIND_COLLISION_PATH else native_path(value))
    monkeypatch.setattr(verification.releases,'query_package',lambda path,command:(identities[next(name for name in identities if path.name.startswith(name+'-'))],set()))
    monkeypatch.setattr(verification.package_checksums,'verify_archive',lambda command,path:
        list(manifest['files'].items()) if path.name.startswith('os-bind-rp-') else expected)
    def output(command,**kwargs):
        if command[1]=='version': return fixture_version_comparison(*command[-2:])+'\n'
        if command[1]=='which': return owners[command[-1]]+'\n'
        if command[1]=='info': return json.dumps(manifest)
        name=command[command.index('-e')+1].removeprefix('%n = ')
        if command[-1]=='%R': return 'OPNsense'
        if command[-1]=='%Fp|%Fs': return '\n'.join('|'.join(row) for row in actual[name])
        if command[-1]=='%dn|%do|%dv':
            assert name=='bind920'
            tools=identities['bind-tools']
            return f'{tools[0]}|{tools[2]}|{tools[1]}\n'
        return '|'.join(identities[name])
    monkeypatch.setattr(verification.subprocess,'check_output',output)
    def native_run(command,**kwargs):
        if command[1]=='query' and command[-1]=='%n|%v|%o|%q':
            name=command[command.index('-e')+1].removeprefix('%n = ')
            return SimpleNamespace(returncode=0,stdout='|'.join(core if name=='opnsense' else identities[name]))
        if command[1]=='query': return SimpleNamespace(returncode=1,stdout='')
        if command[1]=='check': return SimpleNamespace(returncode=0,stdout='')
        raise AssertionError(command)
    native=Mock(side_effect=native_run)
    monkeypatch.setattr(verification.subprocess,'run',native)
    requirement=bind_components.plugin_requirement_from_makefile(PLUGIN_PACKAGE)
    verification.installed(packages,tmp_path / 'evidence','public',exact=False,requirement=requirement)
    observed=json.loads((tmp_path / 'evidence/public.json').read_text())
    assert [row['validation'] for row in observed]==['eligible-runtime','eligible-runtime','exact-archive']
    assert observed[0]['files']==[['/usr/local/bind-tools','2'*64]]
    assert any(call.args[0]==[verification.PKG,'check','-s','bind-tools','bind920','os-bind-rp'] for call in native.call_args_list)
    with pytest.raises(ValueError,match='file set differs'):
        verification.installed(packages,tmp_path / 'evidence','local')
    actual['os-bind-rp']=[(BIND_COLLISION_PATH,'3'*64)]
    with pytest.raises(ValueError,match='file set differs'):
        verification.installed(packages,tmp_path / 'evidence','invalid-public',exact=False,requirement=requirement)
    assert not (tmp_path / 'evidence/local.json').exists() and not (tmp_path / 'evidence/invalid-public.json').exists()



def test_independent_components_preserve_original_identity_and_frozen_cache(tmp_path, monkeypatch):
    profile = upstream_profile('26.7')
    runtime = make_bind_component('bind_runtime', tmp_path / 'runtime', series='26.7',
        creator=creator_identity(), source_commit='1' * 40, control_commit='2' * 40,
        profile=profile, version='9.20.26_2')
    plugin = make_bind_component('bind_plugin', tmp_path / 'plugin', series='26.7',
        creator=creator_identity(version='2.4.0', origin='opnsense/pkg'), source_commit='3' * 40,
        control_commit='4' * 40, profile=profile, version='26.7_1')
    saved = {kind: {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in directory.iterdir()}
             for kind, directory in [('bind_runtime', runtime), ('bind_plugin', plugin)]}
    for kind, directory in [('bind_runtime', runtime), ('bind_plugin', plugin)]:
        original = bind_components.validate_component(directory, kind, '26.7')
        receipt = bind_components.component_receipt(directory, kind, '26.7')
        assert receipt['assets'] == saved[kind]
        assert original['assets'] == {name: digest for name, digest in saved[kind].items() if name != 'channel.json'}
        assert set(original['packages']) == ({'bind-tools-9.20.26_2.pkg', 'bind920-9.20.26_2.pkg'}
                                             if kind == 'bind_runtime' else {'os-bind-rp-26.7_1.pkg'})
    inputs = json.loads((plugin / 'plugin-build-inputs.json').read_text())
    fingerprint = hashlib.sha256(json.dumps(inputs, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    manifest = json.loads((plugin / 'channel.json').read_text())
    original_runtime = manifest['built_against_runtime']
    manifest['built_against_runtime'] = dict(original_runtime, fingerprint='a' * 64,
        snapshot_tag=f"pkg-26.7-bind-runtime-{original_runtime['version']}-{'a' * 64}")
    write_json(plugin / 'channel.json', manifest)
    assert bind_components.validate_component(plugin, 'bind_plugin', '26.7')['build_fingerprint'] == fingerprint
    for field, changed in [('source_commit', '5' * 40), ('package_creator', creator_identity(version='2.5.0', origin='opnsense/pkg')),
                           ('build_recipe_sha256', '5' * 64)]:
        assert bind_components.plugin_build_fingerprint(dict(inputs, **{field: changed})) != fingerprint
    write_json(plugin / 'channel.json', dict(manifest, built_against_runtime=original_runtime))
    frozen = bind_components.component_receipt(plugin, 'bind_plugin', '26.7')
    monkeypatch.setattr(bind_components.subprocess, 'run', lambda *a, **kw:
        SimpleNamespace(stdout=json.dumps([dict(tagName=frozen['snapshot_tag'], isDraft=False)])))
    monkeypatch.setattr(bind_components.releases, 'snapshot_release', lambda repo, tag, root:
        SimpleNamespace(existed=True, draft=False, directory=plugin))
    copied = tmp_path / 'cache'
    assert bind_components.fetch_cached_component('example/distribution', 'bind_plugin', '26.7',
                                                 fingerprint, copied) == frozen
    action = bind_components.select_producer_action('bind_plugin', inputs, copied)
    assert action['producer_action'] == 'reuse'
    assert catalogue.releases.directory_checksums(copied) == saved['bind_plugin']
