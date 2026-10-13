#!/usr/bin/env python3
"""Regression coverage for self-contained package channels and rollback snapshots."""

from __future__ import annotations

from module_fixtures import *

import pytest

from package_fixtures import creator_identity, write_json, write_resolved_target, make_bind_component, runtime_approval
from test_package_catalogue import publication_environment, directory_bytes, byte_hashes, assert_original_component
from bind.shared import components
from bind.plugin import plugin_release


release_channel = load_module("release_channel", "shared/release_channel.py")


def make_assets(directory, assets):
    directory.mkdir(parents=True)
    for name, contents in assets.items():
        (directory / name).write_bytes(contents)
    return directory


def make_snapshot(directory, tag, assets=None, *, existed=True, draft=False, immutable=False, metadata=None):
    if assets is not None:
        make_assets(directory, assets)
    manifest = directory.with_name(directory.name + '-checksums.json')
    if existed:
        write_json(manifest, {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in directory.iterdir() if p.is_file()})
        if metadata is not None:
            write_json(directory.parent / f"{tag}-release.json", metadata)
    return release_channel.ReleaseSnapshot(tag, existed, directory, manifest,
                                           draft=draft, immutable=immutable, metadata=metadata)


@contextmanager
def release_api_store(remote, pages, failure, native_run):
    """Persist GitHub wire outcomes while running all Release/Pages decisions."""
    objects={};sequence=[0];asset_sequence=[0]
    def upload_asset(state,name,contents):
        asset_sequence[0]+=1
        state['assets'][name]=contents
        state.setdefault('asset_ids',{})[name]=asset_sequence[0]
    for state in remote.values():
        for name,contents in list(state['assets'].items()):
            upload_asset(state,name,contents)
    def object_sha(kind,value):
        sequence[0]+=1
        digest=hashlib.sha1((kind+str(sequence[0])+repr(value)).encode()).hexdigest()
        objects[digest]=(kind,value)
        return digest
    initial_tree={name:object_sha('blob',data) for name,data in pages['files'].items()}
    tree=object_sha('tree',initial_tree)
    pages['head']=object_sha('commit',dict(tree=tree,parents=[]))
    def listed(tag,state):
        return dict(tag_name=tag,created_at=state['created_at'],draft=state['draft'],
                    prerelease=state['isPrerelease'])
    def result(command,kwargs,status=0,stdout='',stderr=''):
        if kwargs.get('check') and status:
            raise subprocess.CalledProcessError(status,command,output=stdout,stderr=stderr)
        if not kwargs.get('text'):
            stdout,stderr=stdout.encode(),stderr.encode()
        return subprocess.CompletedProcess(command,status,stdout,stderr)
    def run(command,**kwargs):
        if command[0]!='gh':
            return native_run(command,**kwargs)
        arguments=command[1:]
        if arguments[0]=='api':
            endpoint=arguments[-1]
            method=arguments[arguments.index('--method')+1] if '--method' in arguments else 'GET'
            payload=json.loads(kwargs['input']) if kwargs.get('input') else None
            if endpoint.endswith('/releases?per_page=100'):
                values=[listed(tag,state) for tag,state in remote.items()]
                response=[values[:len(values)//2],values[len(values)//2:]]
            elif '/contents/' in endpoint:
                name=endpoint.split('/contents/',1)[1].split('?',1)[0]
                if name not in pages['files']:
                    return result(command,kwargs,1,stderr='HTTP 404: no such content')
                response=dict(type='file')
            elif endpoint.endswith('/git/ref/heads/gh-pages'):
                response=dict(object=dict(sha=pages['head']))
            elif endpoint.endswith('/git/refs/heads/gh-pages') and method=='PATCH':
                if failure[0]==('pages',None):
                    failure[0]=None
                    return result(command,kwargs,1,stderr='Pages compare-and-swap rejected')
                commit=objects[payload['sha']][1]
                if payload['force'] is not False or commit['parents']!=[pages['head']]:
                    return result(command,kwargs,1,stderr='Pages head changed')
                pages['head']=payload['sha']
                pages['files']={name:objects[sha][1] for name,sha in objects[commit['tree']][1].items()}
                response=dict(object=dict(sha=pages['head']))
            elif '/git/commits/' in endpoint:
                response=objects[endpoint.rsplit('/',1)[-1]][1]
                response=dict(response,tree=dict(sha=response['tree']))
            elif '/git/trees/' in endpoint:
                value=objects[endpoint.rsplit('/',1)[-1].split('?',1)[0]][1]
                response=dict(truncated=False,tree=[dict(path=name,mode='100644',type='blob',sha=sha) for name,sha in value.items()])
            elif '/git/blobs/' in endpoint:
                response=dict(content=base64.b64encode(objects[endpoint.rsplit('/',1)[-1]][1]).decode())
            elif endpoint.endswith('/git/blobs') and method=='POST':
                response=dict(sha=object_sha('blob',base64.b64decode(payload['content'])))
            elif endpoint.endswith('/git/trees') and method=='POST':
                value=dict(objects[payload['base_tree']][1])
                for entry in payload['tree']:
                    if entry['sha'] is None: value.pop(entry['path'],None)
                    else: value[entry['path']]=entry['sha']
                response=dict(sha=object_sha('tree',value))
            elif endpoint.endswith('/git/commits') and method=='POST':
                response=dict(sha=object_sha('commit',payload))
            else:
                raise AssertionError(command)
            return result(command,kwargs,stdout=json.dumps(response))
        if arguments[:2]==['release','list']:
            return result(command,kwargs,stdout=json.dumps([dict(tagName=tag,isDraft=state['draft']) for tag,state in remote.items()]))
        operation,tag=arguments[1:3]
        if operation=='create':
            if tag in remote:
                return result(command,kwargs,1,stderr='release already exists')
            number=len(remote)+1
            remote[tag]=dict(assets={},asset_ids={},draft='--draft' in arguments,isImmutable=tag.startswith('pkg-dhcp-interface-ha-26.7-'),
                name=arguments[arguments.index('--title')+1],body='',isPrerelease='--prerelease' in arguments,
                created_at=f'2026-01-01T00:{number//60:02}:{number%60:02}Z')
            for value in arguments[3:arguments.index('--repo')]:
                if Path(value).is_file(): upload_asset(remote[tag],Path(value).name,Path(value).read_bytes())
        elif tag not in remote:
            return result(command,kwargs,1,stderr='release not found')
        elif operation=='view':
            state=remote[tag]
            assets=[dict(name=name,id=state['asset_ids'][name]) for name in state['assets']]
            if '--jq' in arguments:
                response='\n'.join(state['assets'])
            else:
                response=json.dumps(dict(assets=assets,isDraft=state['draft'],isImmutable=state['isImmutable'],
                    name=state['name'],body=state['body'],isPrerelease=state['isPrerelease']))
            return result(command,kwargs,stdout=response)
        elif operation=='download':
            name=arguments[arguments.index('--pattern')+1]
            if name not in remote[tag]['assets']:
                return result(command,kwargs,1,stderr='asset not found')
            directory=Path(arguments[arguments.index('--dir')+1]);directory.mkdir(parents=True,exist_ok=True)
            (directory / name).write_bytes(remote[tag]['assets'][name])
        elif operation=='upload':
            asset=Path(arguments[3]);upload_asset(remote[tag],asset.name,asset.read_bytes())
            if failure[0]==('upload',tag):
                failure[0]=None
                return result(command,kwargs,1,stderr='partial asset upload failed')
        elif operation=='delete-asset':
            remote[tag]['assets'].pop(arguments[3],None)
            remote[tag]['asset_ids'].pop(arguments[3],None)
        elif operation=='edit':
            for field,flag in (('name','--title'),('body','--notes-file')):
                if flag in arguments:
                    value=arguments[arguments.index(flag)+1]
                    remote[tag][field]=Path(value).read_text() if field=='body' else value
            for field,flag in (('draft','--draft='),('isPrerelease','--prerelease=')):
                for value in arguments:
                    if value.startswith(flag): remote[tag][field]=value.removeprefix(flag)=='true'
        elif operation=='delete':
            remote.pop(tag)
        else:
            raise AssertionError(command)
        return result(command,kwargs)
    with patch.object(subprocess,'run',side_effect=run):
        yield


class ChannelTagTest(unittest.TestCase):
    def test_channel_tags_are_series_scoped(self) -> None:
        """Independent component and paired tags carry their own fingerprints."""
        runtime_fingerprint,plugin_fingerprint='f'*64,'d'*64
        self.assertEqual(
            ('pkg-26.7-bind-runtime',f'pkg-26.7-bind-runtime-9.20.26_2-{runtime_fingerprint}'),
            components.component_tags('bind_runtime','26.7','9.20.26_2',runtime_fingerprint),
        )
        self.assertEqual(
            ('pkg-26.7-bind-plugin',f'pkg-26.7-bind-plugin-26.7_1-{plugin_fingerprint}'),
            components.component_tags('bind_plugin','26.7','26.7_1',plugin_fingerprint),
        )
        self.assertEqual(('pkg-26.7',f'pkg-26.7-os-bind-rp-26.7_1-bind-{runtime_fingerprint}-plugin-{plugin_fingerprint}'),
            components.stack_tags('26.7','26.7_1',runtime_fingerprint,plugin_fingerprint))
        from git_fixtures import upstream_profile
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            original=make_bind_component('bind_plugin',root / 'plugin',series='26.7',creator=creator_identity(),
                source_commit='a'*40,control_commit='c'*40,profile=upstream_profile('26.7'),version='26.7_1')
            inputs=json.loads((original / 'plugin-build-inputs.json').read_text())
            fingerprint=hashlib.sha256(json.dumps(inputs,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            selected=dict(mode='production',expected_fingerprint=fingerprint)
            tag=plugin_release.prepare_source_assets(original,root / 'source','26.7',selected)
            self.assertEqual(f'os-bind-rp-26.7-26.7_1-plugin-{fingerprint}',tag)
            names={'os-bind-rp-26.7_1.pkg','build-metadata.txt','plugin-build-inputs.json','upstream.json',
                   'creator-target.json','plugin-package.mk','channel.json'}
            self.assertEqual({name:(original / name).read_bytes() for name in names},directory_bytes(root / 'source'))


class GitHubCliTest(unittest.TestCase):

    def test_release_asset_download_retries_after_removing_partial_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            downloaded = directory / "meta.conf"
            calls: list[list[str]] = []

            def fake_run_gh(arguments: list[str]) -> None:
                calls.append(arguments)
                if len(calls) == 1:
                    downloaded.write_bytes(b"partial")
                    raise subprocess.CalledProcessError(1, ["gh", *arguments])
                self.assertFalse(downloaded.exists(), "partial download survived before retry")
                downloaded.write_bytes(b"complete")

            with patch.object(release_channel, "run_gh", side_effect=fake_run_gh):
                release_channel.download_release_asset(
                    "resolver-plugins/repository", "pkg-26.7", "meta.conf", directory
                )

            self.assertEqual(b"complete", downloaded.read_bytes())
            self.assertEqual(2, len(calls))


class PullRequestReleaseCleanupTest(unittest.TestCase):
    def test_pull_request_release_cleanup_does_not_hide_tag_api_failure(self) -> None:
        def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            if command[:2] == ["gh", "release"]:
                return subprocess.CompletedProcess(command, 0, "", "")
            return subprocess.CompletedProcess(command, 1, "", "gh: Server Error (HTTP 500)")

        with patch.object(release_channel.subprocess, "run", side_effect=fake_run):
            with self.assertRaisesRegex(RuntimeError, "cannot delete development tag"):
                release_channel.cleanup_development_release(
                    "resolver-plugins/plugins", "pr-51-26.7"
                )

    def test_pull_request_cleanup_refuses_a_production_tag_before_mutation(self) -> None:
        with patch.object(release_channel.subprocess, "run") as run:
            with self.assertRaisesRegex(ValueError, "invalid development release tag"):
                release_channel.cleanup_development_release(
                    "resolver-plugins/plugins", "pkg-26.7"
                )

        run.assert_not_called()

    def test_pull_request_release_cleanup_lists_and_deletes_exact_matches(self) -> None:
        commands: list[list[str]] = []
        retained={'pr-510-26.7-bind-runtime','pr-52-26.7-bind-plugin','pr-51-26.7-bind-runtime-extra',
            'pr-51-26.7-bind','pr-51-26.7-unknown','pkg-26.7','pkg-26.7-bind-runtime',
            'pkg-26.7-bind-plugin-26.7_1-'+'a'*64,'pkg-26.7-os-bind-rp-26.7_1-bind-'+'b'*64+'-plugin-'+'c'*64,
            'os-bind-rp-26.7-26.7_1-plugin-'+'d'*64,'pkg-dhcp-interface-ha-26.7-0.2_43',
            'pkg-26.1-bind-runtime-9.20.26_2-'+'e'*64}
        release_tags={'pr-51-26.7','pr-51-26.1','pr-51-26.7-bind-runtime','pr-51-26.7-bind-plugin'} | retained
        tag_refs=set(release_tags) | {'pr-51-27.1'}

        def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            if command[:3] == ['gh', 'api', '--paginate']:
                if '/releases?' in command[-1]:
                    payload = [[{'tag_name': tag} for tag in sorted(release_tags)]]
                else:
                    payload = [[{'ref': 'refs/tags/'+tag} for tag in sorted(tag_refs)]]
                return subprocess.CompletedProcess(command, 0, json.dumps(payload), '')
            commands.append(command)
            if command[:2] == ['gh', 'release'] and command[3] == 'pr-51-27.1':
                return subprocess.CompletedProcess(command, 1, '', 'release not found')
            if command[:2] == ['gh','release']:
                release_tags.discard(command[3])
            if command[:3] == ['gh','api','--method']:
                tag_refs.discard(command[-1].rsplit('/',1)[-1])
            if command[:3] == ['gh', 'api', '--method'] and not command[-1].endswith('pr-51-27.1'):
                return subprocess.CompletedProcess(command, 1, '', 'gh: Not Found (HTTP 404)')
            return subprocess.CompletedProcess(command, 0, '', '')

        with patch.object(release_channel.subprocess, 'run', side_effect=fake_run):
            release_channel.cleanup_pull_request_releases('resolver-plugins/plugins', '51')

        expected = []
        for tag in ('pr-51-26.1', 'pr-51-26.7', 'pr-51-26.7-bind-plugin', 'pr-51-26.7-bind-runtime', 'pr-51-27.1'):
            expected.extend([
                ['gh', 'release', 'delete', tag, '--yes', '--repo', 'resolver-plugins/plugins'],
                ['gh', 'api', '--method', 'DELETE', f'repos/resolver-plugins/plugins/git/refs/tags/{tag}'],
            ])
        self.assertEqual(expected, commands)
        self.assertEqual(retained,release_tags)
        self.assertEqual(retained,tag_refs)

class TestSelfContainedRepositoryStage:
    def test_stage_channel_contains_the_plugin_bind_pair_and_audit_manifest(self,publication_environment):
        environment=publication_environment
        creator=creator_identity()
        runtime=environment.stage(environment.select('bind_runtime','9.20.26_2',creator=creator))
        runtime_original=directory_bytes(runtime.root / 'repository/component/current')
        environment.publish(runtime)
        source=environment.control('9.20.26_2','reviewed plugin staging source')
        plugin=environment.select('bind_plugin','26.7_1',source=source,creator=creator)
        producer=environment.producer(plugin)
        original=directory_bytes(producer)
        inputs=json.loads(original['plugin-build-inputs.json'])
        fingerprint=hashlib.sha256(json.dumps(inputs,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        repository=plugin.root / 'repository'
        components.stage_repository(producer,plugin.selection,plugin.root / 'trusted',repository,
            environment.root / 'key',pkg_command=environment.pkg)
        own=directory_bytes(repository / 'component/current')
        assert set(own)=={'os-bind-rp-26.7_1.pkg','build-metadata.txt','plugin-build-inputs.json',
            'upstream.json','creator-target.json','plugin-package.mk','resolver-plugins.pub',
            'channel.json','meta','meta.conf','data.pkg','data.tzst','packagesite.pkg','packagesite.tzst'}
        for name,data in original.items(): assert own[name]==data
        manifest=json.loads(own['channel.json'])
        assert (manifest['schema'],manifest['kind'],manifest['series'],manifest['package_abi'],
                manifest['plugin_version'],manifest['source_commit'],manifest['control_commit'])==(
                1,'bind-plugin','26.7','FreeBSD:15:amd64','26.7_1',source,plugin.control)
        assert manifest['build_fingerprint']==fingerprint
        assert manifest['package_creator']==creator
        assert manifest['creator_source']==dict(release='26.7.1',repository='https://github.com/opnsense/ports.git',
            commit='4'*40,path='ports-mgmt/pkg')
        metadata=dict(line.split('=',1) for line in own['build-metadata.txt'].decode().splitlines())
        assert (metadata['freebsd_release'],metadata['tools_tag'])==('15.1','26.7.1')
        assert manifest['packages']=={'os-bind-rp-26.7_1.pkg':hashlib.sha256(original['os-bind-rp-26.7_1.pkg']).hexdigest()}
        assert own['resolver-plugins.pub']==components.PUBLIC_KEY.read_bytes()
        assert directory_bytes(repository / 'component/snapshot')==own
        stack=directory_bytes(repository / 'stack/current')
        assert set(json.loads(stack['channel.json'])['packages'])=={
            'bind-tools-9.20.26_2.pkg','bind920-9.20.26_2.pkg','os-bind-rp-26.7_1.pkg'}
        assert_original_component(stack,'bind_runtime',runtime_original)
        assert_original_component(stack,'bind_plugin',own)
        assert directory_bytes(repository / 'stack/snapshot')==stack
        for directory in ('component/current','component/snapshot','stack/current','stack/snapshot','combined'):
            for name in ('data','packagesite'):
                alias=repository / directory / (name+'.tzst')
                assert alias.is_file() and not alias.is_symlink()
                assert alias.read_bytes()==alias.with_suffix('.pkg').read_bytes()

    def test_staging_rejects_a_common_abi_that_differs_from_the_trusted_target(self,publication_environment):
        environment=publication_environment
        runtime=environment.select('bind_runtime','9.20.26_2')
        producer=environment.producer(runtime)
        archive=producer / 'bind920-9.20.26_2.pkg'
        identity=json.loads(archive.read_text());identity['abi']='FreeBSD:14:amd64'
        write_json(archive,identity)
        with pytest.raises(ValueError,match='archive ABI differs from component'):
            components.stage_repository(producer,runtime.selection,runtime.root / 'trusted',runtime.root / 'repository',
                environment.root / 'key',pkg_command=environment.pkg)
        assert not (runtime.root / 'repository').exists()
        assert not any(call.args[0][0]=='openssl' for call in subprocess.run.call_args_list)

    def test_asset_order_puts_repository_metadata_after_packages(self,tmp_path):
        """Paired-stack uploads expose their payloads before catalogue metadata."""
        packages=('bind-tools-9.20.26_2.pkg','bind920-9.20.26_2.pkg','os-bind-rp-26.7_1.pkg')
        metadata=('bind_runtime-bind920-provenance.json','bind_plugin-build-metadata.txt',
                  'data.pkg','meta.conf','resolver-plugins.pub','channel.json')
        for name in (*packages,*metadata): (tmp_path / name).touch()
        ordered=[path.name for path in release_channel.asset_order(tmp_path)]
        assert set(ordered)==set(packages) | set(metadata)
        assert max(ordered.index(name) for name in packages)<min(ordered.index(name) for name in metadata)
        assert ordered[-1]=='meta.conf'


class AbiStaticPublicationTest(unittest.TestCase):
    def _channel(self, root: Path) -> Path:
        channel = root / "channel"
        channel.mkdir()
        (channel / "channel.json").write_text(
            json.dumps({"series": "26.7", "package_abi": "FreeBSD:15:amd64"}),
            encoding="utf-8",
        )
        (channel / "meta.conf").write_bytes(b"meta")
        (channel / "packagesite.pkg").write_bytes(b"catalogue")
        (channel / "os-bind-rp-1.36_7.pkg").write_bytes(b"package")
        return channel

    def test_publication_replaces_only_the_channel_declared_series_abi_path(self) -> None:
        """A static publish cannot remove sibling ABI or same-ABI series bytes."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            channel = self._channel(root)
            old_head = "1" * 40
            calls: list[tuple[list[str], object | None]] = []
            blob_number = 0

            def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
                nonlocal blob_number
                payload = json.loads(str(kwargs["input"])) if kwargs.get("input") else None
                calls.append((command, payload))
                endpoint = command[-1]
                method = command[command.index("--method") + 1] if "--method" in command else "GET"
                if endpoint.endswith("/git/ref/heads/gh-pages") and method == "GET":
                    response = {"object": {"sha": old_head}}
                elif endpoint.endswith(f"/git/commits/{old_head}"):
                    response = {"tree": {"sha": "2" * 40}}
                elif endpoint.endswith("/git/trees/" + "2" * 40 + "?recursive=1"):
                    response = {
                        "truncated": False,
                        "tree": [
                            {"path": "pages-health", "mode": "100644", "type": "blob", "sha": "3" * 40},
                            {"path": "pkg/FreeBSD:14:amd64/26.1/latest/meta.conf", "mode": "100644", "type": "blob", "sha": "4" * 40},
                            {"path": "pkg/FreeBSD:15:amd64/26.7/latest/obsolete.pkg", "mode": "100644", "type": "blob", "sha": "5" * 40},
                            {"path": "pkg/FreeBSD:15:amd64/27.1/latest/meta.conf", "mode": "100644", "type": "blob", "sha": "6" * 40},
                        ],
                    }
                elif endpoint.endswith("/git/blobs"):
                    blob_number += 1
                    response = {"sha": f"{blob_number + 5:x}" * 40}
                elif endpoint.endswith("/git/trees"):
                    response = {"sha": "a" * 40}
                elif endpoint.endswith("/git/commits"):
                    response = {"sha": "b" * 40}
                elif endpoint.endswith("/git/refs/heads/gh-pages") and method == "PATCH":
                    response = {"object": {"sha": "b" * 40}}
                else:  # pragma: no cover - makes unexpected API calls diagnostic
                    raise AssertionError(command)
                return subprocess.CompletedProcess(command, 0, json.dumps(response), "")

            with (
                patch.object(release_channel, "validate_static_channel"),
                patch.object(release_channel.subprocess, "run", side_effect=fake_run),
            ):
                release_channel.publish_abi_channel(
                    "resolver-plugins/repository", channel, root / "recovery"
                )

            tree_payload = next(
                payload
                for command, payload in calls
                if command[-1].endswith("/git/trees") and payload is not None
            )
            self.assertEqual("2" * 40, tree_payload["base_tree"])
            changes = tree_payload["tree"]
            self.assertIn(
                {"path": "pkg/FreeBSD:15:amd64/26.7/latest/obsolete.pkg", "mode": "100644", "type": "blob", "sha": None},
                changes,
            )
            self.assertFalse(
                any(change["path"].startswith("pkg/FreeBSD:14:amd64/") for change in changes)
            )
            self.assertFalse(
                any(change["path"].startswith("pkg/FreeBSD:15:amd64/27.1/") for change in changes)
            )
            self.assertEqual(
                {f"pkg/FreeBSD:15:amd64/26.7/latest/{path.name}" for path in channel.iterdir()},
                {change["path"] for change in changes if change.get("sha") is not None},
            )
            self.assertEqual(old_head + "\n", (root / "recovery/gh-pages-head.txt").read_text())
            commit = next(payload for command, payload in calls
                          if command[-1].endswith('/git/commits') and payload is not None)
            self.assertEqual('a' * 40, commit['tree'])
            self.assertEqual([old_head], commit['parents'])
            updates = [payload for command, payload in calls if '--method' in command
                       and command[command.index('--method') + 1] == 'PATCH']
            self.assertEqual([{'sha': 'b' * 40, 'force': False}], updates)

    def test_publication_rejects_a_changed_head_before_ref_update(self) -> None:
        """A concurrent gh-pages publisher must win instead of losing its update."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            channel = self._channel(root)
            heads = iter(("1" * 40, "9" * 40))
            ref_updates: list[list[str]] = []

            def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                endpoint = command[-1]
                method = command[command.index("--method") + 1] if "--method" in command else "GET"
                if endpoint.endswith("/git/ref/heads/gh-pages") and method == "GET":
                    response = {"object": {"sha": next(heads)}}
                elif endpoint.endswith("/git/commits/" + "1" * 40):
                    response = {"tree": {"sha": "2" * 40}}
                elif endpoint.endswith("/git/trees/" + "2" * 40 + "?recursive=1"):
                    response = {"truncated": False, "tree": []}
                elif endpoint.endswith("/git/blobs"):
                    response = {"sha": "6" * 40}
                elif endpoint.endswith("/git/trees"):
                    response = {"sha": "7" * 40}
                elif endpoint.endswith("/git/commits"):
                    response = {"sha": "8" * 40}
                elif method == "PATCH":
                    ref_updates.append(command)
                    response = {}
                else:  # pragma: no cover
                    raise AssertionError(command)
                return subprocess.CompletedProcess(command, 0, json.dumps(response), "")

            with (
                patch.object(release_channel, "validate_static_channel"),
                patch.object(release_channel.subprocess, "run", side_effect=fake_run),
                self.assertRaisesRegex(RuntimeError, "gh-pages head changed"),
            ):
                release_channel.publish_abi_channel(
                    "resolver-plugins/repository", channel, root / "recovery"
                )
            self.assertEqual([], ref_updates)


class PublicationRecoveryTest(unittest.TestCase):

    def test_immutable_release_retry_states(self):
        assets = {'os-bind-rp-26.7_1.pkg': b'plugin', 'build-metadata.txt': b'metadata'}
        repository, tag, title = 'resolver-plugins/plugins', 'os-bind-rp-26.7-26.7_1-plugin-'+'d'*64, 'os-bind-rp 26.7 26.7_1'
        for state in ('identical', 'different', 'mutable', 'absent', 'complete-draft', 'partial-draft'):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                staged = make_assets(root / 'staged', assets)
                remote_assets = assets if state not in ('different', 'partial-draft') else {'os-bind-rp-26.7_1.pkg': b'old'}
                existing = make_snapshot(root / 'remote', tag, remote_assets if state != 'absent' else None,
                                         existed=state != 'absent', draft=state.endswith('-draft'),
                                         immutable=state == 'identical')
                published = make_snapshot(staged, tag, immutable=True)
                with patch.object(release_channel, 'snapshot_release', side_effect=[existing, published]) as read, \
                     patch.object(release_channel, 'run_gh') as run:
                    if state in ('different', 'mutable'):
                        with self.assertRaisesRegex(RuntimeError, 'different bytes' if state == 'different' else 'not immutable'):
                            release_channel.publish_immutable_release(repository, tag, staged, title)
                    else:
                        release_channel.publish_immutable_release(repository, tag, staged, title)
                create = ['release', 'create', tag, str(staged / 'os-bind-rp-26.7_1.pkg'),
                          str(staged / 'build-metadata.txt'), '--repo', repository,
                          '--title', title, '--latest=false']
                expected = {
                    'identical': [], 'different': [], 'mutable': [], 'absent': [create],
                    'complete-draft': [['release', 'edit', tag, '--draft=false', '--latest=false', '--repo', repository]],
                    'partial-draft': [['release', 'delete', tag, '--yes', '--repo', repository], create],
                }[state]
                self.assertEqual(expected, [call.args[0] for call in run.call_args_list])


    def test_immutable_publication_rejects_invalid_readback(self):
        for fault, error in [('draft', 'remains a draft'), ('mutable', 'not immutable'), ('bytes', 'different bytes')]:
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                staged = make_assets(root / 'staged', {'asset.pkg': b'expected'})
                absent = make_snapshot(root / 'absent', 'source-release', existed=False)
                readback = make_snapshot(root / 'remote', absent.tag,
                                         {'asset.pkg': b'changed' if fault == 'bytes' else b'expected'},
                                         draft=fault == 'draft', immutable=fault != 'mutable')
                with patch.object(release_channel, 'snapshot_release', side_effect=[absent, readback]), \
                     patch.object(release_channel, 'run_gh'):
                    with self.assertRaisesRegex(RuntimeError, error):
                        release_channel.publish_immutable_release('example/plugins', absent.tag, staged, 'Fixture')

    def test_published_component_snapshot_refuses_different_bytes(self):
        from copy import deepcopy
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            staged=make_assets(root / 'staged',{'asset.pkg':b'changed rebuild'})
            tag='pkg-26.7-bind-runtime-9.20.26_2-'+'f'*64
            remote={tag:dict(assets={'asset.pkg':b'original'},draft=False,isImmutable=False,
                name='Original runtime snapshot',body='Original notes',isPrerelease=False,created_at='2026-01-01T00:00:00Z')}
            groups=[dict(current_tag='pkg-26.7-bind-runtime',snapshot_tag=tag,directory=staged,
                validate=lambda directory:None,can_advance=lambda current,candidate:True)]
            with release_api_store(remote,dict(files={}),[None],lambda command,**kwargs:self.fail(str(command))):
                before=deepcopy(remote)
                with self.assertRaisesRegex(RuntimeError,'different bytes'):
                    release_channel.publish_channels('example.invalid/distribution',groups,root / 'recovery')
            self.assertEqual(before,remote)


    def test_restore_absent_release_accepts_a_not_found_delete(self) -> None:
        """A failed release creation has no remote state to restore."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            snapshot = release_channel.ReleaseSnapshot("pkg-26.7-test", False, root, root / "missing.json")
            for status, error in ((0, ''), (1, 'release not found')):
                with self.subTest(error=error), patch.object(
                    release_channel.subprocess, 'run',
                    return_value=subprocess.CompletedProcess(['gh'], status, stderr=error),
                ) as run:
                    release_channel.restore_release('resolver-plugins/plugins', snapshot)
                    run.assert_called_once_with(
                        ['gh', 'release', 'delete', snapshot.tag, '--yes', '--repo', 'resolver-plugins/plugins'],
                        capture_output=True, text=True)


    def test_snapshot_pruning_keeps_the_newest_five_immutable_tags(self) -> None:
        """Pagination preserves original runtime edges before bounded deletions."""
        from bind.shared import components, retention
        from shared import package_catalogue
        from git_fixtures import upstream_profile
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            profile=upstream_profile('26.7')
            runtime_dirs=[];plugin_dirs=[];remote={};listed=[]
            def register(directory,kind,number):
                receipt=components.component_receipt(directory,kind,'26.7')
                remote[receipt['snapshot_tag']]=directory
                listed.append(dict(tag_name=receipt['snapshot_tag'],draft=False,created_at=f'2026-01-{number+1:02}T00:00:00Z'))
                return receipt
            runtimes=[]
            for number in range(16):
                directory=make_bind_component('bind_runtime',root / f'runtime-{number}',series='26.7',creator=creator_identity(),
                    source_commit='a'*40,control_commit='a'*40,profile=profile,version=f'9.20.{26+number}_2')
                runtime_dirs.append(directory);runtimes.append(register(directory,'bind_runtime',number))
            plugins=[]
            for number in range(7):
                directory=make_bind_component('bind_plugin',root / f'plugin-{number}',series='26.7',creator=creator_identity(),
                    source_commit=f'{number+1:040x}',control_commit='a'*40,profile=profile,version=f'26.7_{number+1}')
                original=runtimes[0 if number==6 else number]
                version=f'9.20.{26+(0 if number==6 else number)}_2'
                manifest=json.loads((directory / 'channel.json').read_text())
                manifest['built_against_runtime']=dict(snapshot_tag=original['snapshot_tag'],fingerprint=original['fingerprint'],
                    version=version,packages=original['packages'])
                metadata=directory / 'build-metadata.txt'
                metadata.write_text(metadata.read_text().replace('bind920=9.20.26_2','bind920='+version))
                manifest['assets']['build-metadata.txt']=hashlib.sha256(metadata.read_bytes()).hexdigest()
                write_json(directory / 'channel.json',manifest)
                plugin_dirs.append(directory);plugins.append(register(directory,'bind_plugin',number))
            upstream=write_json(root / 'upstream.json',profile)
            creator=write_resolved_target(root / 'creator-target.json',profile)
            write_resolved_target(root / 'reader-target.json',profile,role='reader')
            stack=root / 'stack'
            approved=root / 'runtime-approval.mk'
            recipe=json.loads((runtime_dirs[-1] / 'bind920-profile.json').read_text())
            approved.write_text(runtime_approval('9.20.41_2',recipe['ports_commit']))
            pair=dict(bind_runtime=runtimes[-1],bind_plugin=plugins[-1])
            packages=[runtime_dirs[-1] / name for name in runtimes[-1]['packages']]+[plugin_dirs[-1] / name for name in plugins[-1]['packages']]
            def sign(paths,output,*args):
                output.mkdir()
                for path in paths: shutil.copyfile(path,output / path.name)
                for name in components.CATALOGUE_ASSETS: (output / name).write_bytes(b'signed fixture catalogue')
            with patch.object(package_catalogue,'authenticate_components',return_value=(packages,{kind:package_catalogue.component_record(receipt) for kind,receipt in pair.items()})), \
                 patch.object(package_catalogue.bind,'select_role'),patch.object(package_catalogue.target_pkg,'verify_target_pkg'), \
                 patch.object(package_catalogue.subprocess,'check_output',return_value=components.PUBLIC_KEY.read_bytes()), \
                 patch.object(package_catalogue.releases,'stage_selected_repository',side_effect=sign):
                current_stack=package_catalogue.stage_bind_stack(runtime_dirs[-1],plugin_dirs[-1],stack,series='26.7',key=root / 'key',creator_target=creator,upstream=upstream,
                    approved_profile=runtime_dirs[-1] / 'bind920-profile.json',runtime_approval=approved)
            current_pair=retention.paired_tag(current_stack,'26.7')
            remote[current_pair]=stack;listed.append(dict(tag_name=current_pair,draft=False,created_at='2026-01-01T00:00:00Z'))
            opaque=[]
            for number in range(1,7):
                tag=f'pkg-26.7-os-bind-rp-1.36_{number}'+('-bind-'+'f'*64 if number%2==0 else '')
                opaque.append(tag);remote[tag]=None;listed.append(dict(tag_name=tag,draft=False,created_at=f'2026-02-{number:02}T00:00:00Z'))
            sentinels={'pkg-26.7','pkg-26.7-bind-runtime','pkg-26.7-bind-plugin','pr-12-26.7',
                'os-bind-rp-26.7-26.7_7-plugin-'+'1'*64,'pkg-dhcp-interface-ha-26.7-0.2_43',
                'pkg-26.1-bind-runtime-9.20.26_2-'+'2'*64,'pkg-26.7-bind-runtime-invalid'}
            for tag in sentinels:
                remote[tag]=None;listed.append(dict(tag_name=tag,draft=False,created_at='2026-03-01T00:00:00Z'))
            def download(repository,tag,name,directory): shutil.copyfile(remote[tag] / name,directory / name)
            result=subprocess.CompletedProcess(['gh'],0,stdout=json.dumps([listed[:10],listed[10:]]))
            with patch.object(retention.subprocess,'run',return_value=result),patch.object(retention.releases,'download_release_asset',side_effect=download):
                records=retention.collect('example/distribution','26.7')
            before=set(remote)
            with self.assertRaisesRegex(ValueError,'original runtime'):
                components.retention_plan([record for record in records if record['tag']!=runtimes[0]['snapshot_tag']],pair,current_stack)
            self.assertEqual(before,set(remote))
            plan=components.retention_plan(records,pair,current_stack)
            expected={runtimes[number]['snapshot_tag'] for number in range(6,10)} | {plugins[0]['snapshot_tag'],opaque[0]}
            self.assertEqual(expected,set(plan))
            recovery=root / 'retention';recovery.mkdir()
            with patch.object(retention.releases,'run_gh',side_effect=lambda args:remote.pop(args[2])):
                retention.apply_plan('example/distribution',plan,recovery)
            self.assertEqual(before-expected,set(remote))
            self.assertTrue(sentinels <= set(remote))
            self.assertIn(runtimes[0]['snapshot_tag'],remote)
            self.assertIn(current_pair,remote)
            self.assertEqual(plan,json.loads((recovery / 'deleted.json').read_text()))


class ChannelManifestValidationTest(unittest.TestCase):
    def test_manifest_contract(self):
        from git_fixtures import upstream_profile
        for fault,error in [('valid',None),('formula','differs from source'),('exact-edge','differs from source'),
                            ('version','plugin version differs from series'),('filename','original build inputs')]:
            with self.subTest(fault=fault),tempfile.TemporaryDirectory() as temporary:
                directory=make_bind_component('bind_plugin',Path(temporary) / 'plugin',series='26.7',creator=creator_identity(),
                    source_commit='a'*40,control_commit='c'*40,profile=upstream_profile('26.7'),
                    version='1.36_2' if fault=='version' else '26.7_1')
                manifest=json.loads((directory / 'channel.json').read_text())
                if fault=='filename':
                    archive=directory / 'os-bind-rp-26.7_1.pkg';renamed=directory / 'os-bind-rp-1.36_2.pkg'
                    archive.rename(renamed)
                    manifest['packages']={renamed.name:hashlib.sha256(renamed.read_bytes()).hexdigest()}
                    manifest['assets']=byte_hashes({name:data for name,data in directory_bytes(directory).items() if name!='channel.json'})
                    write_json(directory / 'channel.json',manifest)
                identity=('os-bind-rp',manifest['plugin_version'],'opnsense/os-bind-rp','FreeBSD:15:amd64')
                edges={('bind920','dns/bind920','9.20.26_2')} if fault=='exact-edge' else set()
                formula='bind920 = 9.20.26' if fault=='formula' else 'bind920 >= 9.20.26'
                with patch.object(components.releases,'query_package',return_value=(identity,edges)), \
                     patch.object(components.package_checksums,'verify_archive'), \
                     patch.object(components.releases,'read_package_manifest',return_value=dict(dep_formula=formula,conflicts=['os-bind','os-bind-devel','os-bind-rp-devel'],
                        deps={'opnsense':{'version':'26.1.11_10','origin':'opnsense/opnsense'}})):
                    if error:
                        with self.assertRaisesRegex(ValueError,error):
                            components.validate_component(directory,'bind_plugin','26.7',pkg_command='pkg')
                    else:
                        self.assertEqual(manifest,components.validate_component(directory,'bind_plugin','26.7',pkg_command='pkg'))


class BootstrapRepositoryConfigTest(unittest.TestCase):
    def test_bootstrap_uses_the_abi_and_series_scoped_static_repository_url(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "resolver-plugins.conf"

            release_channel.write_bootstrap(
                output,
                "https://resolver-plugins.github.io/repository/",
                "26.7",
                "/usr/local/etc/pkg/keys/resolver-plugins.pub",
            )

            self.assertEqual(
                'resolver-plugins: {\n'
                '  url: "https://resolver-plugins.github.io/repository/pkg/${ABI}/26.7/latest",\n'
                '  mirror_type: "none",\n'
                '  signature_type: "pubkey",\n'
                '  pubkey: "/usr/local/etc/pkg/keys/resolver-plugins.pub",\n'
                '  enabled: yes\n'
                '}\n',
                output.read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
