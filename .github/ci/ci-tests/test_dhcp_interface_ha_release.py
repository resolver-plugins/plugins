"""Protect source identity, verification gates and immutable release retries."""
from module_fixtures import *

import pytest

ROOT = CI.parents[1]
sys.path.insert(0, str(CI))
from ha_dhcp import dhcp_interface_ha_channel as channel
from ha_dhcp import dhcp_interface_ha_release as selection
from bind.shared import selection as routing
from git_fixtures import init_repository, commit, git, upstream_profile
from ha_fixtures import make_ha_channel
from package_fixtures import write_resolved_target, creator_identity
from workflow_fixtures import workflow_jobs, workflow_steps, trigger_paths

publisher = load_module("publish_dhcpha", "ha_dhcp/publish-dhcp-interface-ha.py")
COMMIT = "a" * 40

@pytest.fixture
def release_history(tmp_path, monkeypatch):
    init_repository(tmp_path)
    monkeypatch.chdir(tmp_path)

    def advance():
        git(tmp_path, 'commit', '--allow-empty', '-qm', 'fixture')
        return git(tmp_path, 'rev-parse', 'HEAD')

    return advance


@pytest.mark.parametrize('event_name,changed,base,series', [
    ('push', ['net/dhcp-interface-ha/tests/test_runtime.py'], None, None),
    ('push', ['.github/ci/ha_dhcp/build-dhcp-interface-ha.sh'], None, None),
    ('pull_request_target', ['net/dhcp-interface-ha/src/controller.py'], 'release/dhcp-interface-ha/26.1', '26.1'),
    ('pull_request_target', ['net/dhcp-interface-ha/README.md'], 'release/dhcp-interface-ha/26.7', None),
    ('workflow_dispatch', [], None, '26.1'),
])
def test_registered_series_routing_uses_reviewed_source_and_master_control(tmp_path, event_name, changed, base, series):
    refs = [f'refs/heads/release/dhcp-interface-ha/{value}' for value in ('26.1', '26.7')]
    event = dict(name=event_name, ref='refs/heads/master', sha=COMMIT, workflow_sha=COMMIT,
        before='b' * 40, merged=True, base_ref=base, merge_commit_sha='c' * 40,
        inputs=dict(mode=None, series='26.1', pull_number=None), pr_base_ref=None)
    paths = {'event.json': event, 'refs.json': refs, 'paths.json': changed}
    for name, record in paths.items():
        (tmp_path / name).write_text(json.dumps(record))
    output = tmp_path / 'output'
    subprocess.run([sys.executable, '-I', str(CI / 'bind/shared/selection.py'), 'select-event',
        '--component', 'dhcp_interface_ha', '--event-file', str(tmp_path / 'event.json'),
        '--refs-file', str(tmp_path / 'refs.json'), '--changed-paths-file', str(tmp_path / 'paths.json'),
        '--output', str(tmp_path / 'selected.json'), '--github-output', str(output)], check=True)
    selected = json.loads((tmp_path / 'selected.json').read_text())
    assert selected['release'] is (series is not None)
    if series:
        expected = 'c' * 40 if event_name == 'pull_request_target' else f'refs/heads/release/dhcp-interface-ha/{series}'
        assert (selected['series'], selected['source_ref'], selected['profile_ref'], selected['control_ref']) == (
            series, expected, expected, COMMIT)
        assert f'source_ref={expected}' in output.read_text()
    event['ref'] = 'refs/heads/feature'
    if event_name != 'pull_request_target':
        with pytest.raises(ValueError, match='master'):
            routing.select_event('dhcp_interface_ha', event, refs, ['net/dhcp-interface-ha/Makefile'])


@pytest.mark.parametrize('fault', [None, 'source-file', 'control'])
def test_source_selection_materializes_exact_package_and_framework_preserving_canonical_controls(tmp_path, fault):
    repository = init_repository(tmp_path / 'repository')
    package = 'PLUGIN_NAME= dhcp-interface-ha\nPLUGIN_VERSION= 26.7\nPLUGIN_REVISION= 1\n'
    source = commit(repository, {selection.MAKEFILE: package,
        'net/dhcp-interface-ha/src/controller.py': 'reviewed source\n',
        'Mk/plugins.mk': 'reviewed framework\n', 'Mk/defaults.mk': '', 'Mk/common.mk': '',
        'Templates/version': 'reviewed template\n'}, 'reviewed HA release')
    control = commit(repository, {selection.MAKEFILE: package.replace('26.7', '0.2'),
        'net/dhcp-interface-ha/src/controller.py': 'stale control source\n',
        'net/dhcp-interface-ha/tests/test_runtime.py': 'canonical tests\n',
        'Mk/devel.mk': 'stale framework marker\n', '.github/ci/control.py': 'trusted controller\n'}, 'master control')
    trusted = tmp_path / 'trusted'
    def command(operation, *args):
        return [sys.executable, '-I', str(CI / 'ha_dhcp/dhcp_interface_ha_release.py'), operation,
            '--directory', str(trusted), '--series', '26.7', '--source-commit', source,
            '--control-commit', control, *args]
    digest = subprocess.check_output(command('source-inputs'), cwd=repository, text=True).strip()
    if fault == 'source-file':
        (trusted / 'plugin-package.mk').write_text(package.replace('1', '2'))
    elif fault == 'control':
        subprocess.run(['git', '-C', str(repository), 'checkout', '--detach', source], check=True, capture_output=True)
    observed = (selection.MAKEFILE, 'net/dhcp-interface-ha/src/controller.py', 'Mk/plugins.mk', '.github/ci/control.py')
    before = {name: (repository / name).read_bytes() if (repository / name).exists() else None for name in observed}
    result = subprocess.run(command('materialize', '--repository-root', str(repository), '--sha256', digest),
                            cwd=repository, text=True, capture_output=True)
    assert (result.returncode == 0) is (fault is None), result.stderr
    if fault:
        assert 'differs' in result.stderr
        assert before == {name: (repository / name).read_bytes() if (repository / name).exists() else None for name in observed}
    else:
        assert (repository / selection.MAKEFILE).read_text() == package
        assert (repository / 'net/dhcp-interface-ha/src/controller.py').read_text() == 'reviewed source\n'
        assert (repository / 'net/dhcp-interface-ha/tests/test_runtime.py').read_text() == 'canonical tests\n'
        assert (repository / '.github/ci/control.py').read_text() == 'trusted controller\n'
        assert (repository / 'Templates/version').read_text() == 'reviewed template\n'
        assert not (repository / 'Mk/devel.mk').exists()
        assert selection.validate_source(trusted, '26.7', source, control, digest, repository)['plugin_version'] == '26.7_1'
        version_file = tmp_path / 'version.json'
        version_file.write_text(json.dumps(dict(product_hash=source)))
        archive = tmp_path / 'os-dhcp-interface-ha-26.7_1.pkg'
        with tarfile.open(archive, 'w') as package:
            package.add(version_file, arcname='usr/local/opnsense/version/dhcp-interface-ha')
        check = command('verify-package', '--package', str(archive))
        subprocess.run(check, check=True)
        check[check.index('--source-commit') + 1] = control
        rejected = subprocess.run(check, capture_output=True, text=True)
        assert rejected.returncode != 0 and 'version hash differs' in rejected.stderr


@pytest.mark.parametrize('fault', [None, 'member', 'control'])
def test_shared_bind_inputs_bind_exact_git_recipe_tools_and_external_seal(tmp_path, monkeypatch, fault):
    from git_fixtures import init_repository, commit, git, upstream_profile
    from package_fixtures import PLUGIN_PACKAGE, make_ports_recipe, make_tools_repository, write_json
    profile = upstream_profile('26.7')
    ports, approval, expected_recipe = make_ports_recipe(tmp_path / 'ports')
    tools = make_tools_repository(tmp_path / 'tools', profile)
    repository = init_repository(tmp_path / 'control')
    original = PLUGIN_PACKAGE + approval
    control = commit(repository, {'dns/bind/Makefile': original}, 'reviewed BIND approval')
    upstream = write_json(tmp_path / 'upstream.json', profile)
    trusted = tmp_path / 'trusted'
    resolve = selection.bind920_profile.resolve_profile
    with monkeypatch.context() as boundary:
        boundary.chdir(repository)
        boundary.setattr(selection.bind920_profile, 'resolve_profile', lambda record: resolve(record, repository=ports))
        digest = selection.freeze_bind_inputs(trusted, profile, tools, control)
    assert (trusted / 'runtime-approval.mk').read_bytes() == original.encode()
    assert json.loads((trusted / 'bind920-profile.json').read_text()) == expected_recipe
    tools_blob = subprocess.check_output(['git', '-C', str(tools), 'show',
        profile['tools_tag'] + ':config/26.7/build.conf'])
    assert json.loads((trusted / 'tools-profile.json').read_text()) == dict(schema=1,
        series='26.7', tools_tag='26.7.1', tools_commit=git(tools, 'rev-parse', profile['tools_tag']),
        path='config/26.7/build.conf', build_conf_sha256=hashlib.sha256(tools_blob).hexdigest(),
        freebsd_release='15.1')
    assert json.loads((trusted / 'bind-inputs.json').read_text()) == {
        name: hashlib.sha256((trusted / name).read_bytes()).hexdigest()
        for name in ('runtime-approval.mk', 'bind920-profile.json', 'tools-profile.json')}
    if fault == 'member':
        record = json.loads((trusted / 'tools-profile.json').read_text())
        write_json(trusted / 'tools-profile.json', dict(record, tools_commit='f' * 40))
    elif fault == 'control':
        control = commit(repository, {'dns/bind/Makefile': original + '# later approval context\n'}, 'different control')
    sentinel = tmp_path / 'privileged-output'
    command = [sys.executable, '-I', str(CI / 'ha_dhcp/dhcp_interface_ha_release.py'),
        'validate-bind-inputs', '--series', '26.7', '--directory', str(trusted),
        '--upstream', str(upstream), '--control-commit', control, '--sha256', digest]
    result = subprocess.run(['sh', '-c', shlex.join(command) + ' && : > ' + shlex.quote(str(sentinel))],
        cwd=repository, capture_output=True, text=True)
    assert (result.returncode == 0) is (fault is None), result.stderr
    assert sentinel.exists() is (fault is None)


@pytest.fixture
def release(tmp_path, monkeypatch):
    (tmp_path / "build-metadata.txt").write_text(
        f"source_commit={COMMIT}\nseries=26.7\nrepository_snapshot=26.7.4\nplugin_version=0.2_30\n")
    for name in ("os-dhcp-interface-ha-0.2_30.pkg", "upstream.json", "SHA256SUMS"):
        (tmp_path / name).write_text("fixture\n")
    calls = Mock()
    for name in ("run_gh", "upload_release_assets", "snapshot_release", "snapshot_matches_directory"):
        monkeypatch.setattr(publisher, name, getattr(calls, name))
    calls.snapshot_release.side_effect = [SimpleNamespace(existed=False), SimpleNamespace(existed=True)]
    calls.snapshot_matches_directory.return_value = True
    return tmp_path, calls


def test_release_targets_exact_commit_and_verifies_draft_before_publication(release):
    directory, calls = release
    publisher.publish("example/plugins", directory, "26.7", COMMIT)
    create, promote = [c.args[0] for c in calls.run_gh.call_args_list]
    assert create[:3] == ["release", "create", "os-dhcp-interface-ha-26.7-0.2_30"]
    assert create[create.index("--title") + 1] == 'os-dhcp-interface-ha 26.7 0.2_30'
    assert create[create.index("--target") + 1] == COMMIT
    assert "--prerelease" in create and "--draft" in create
    assert promote == ['release', 'edit', 'os-dhcp-interface-ha-26.7-0.2_30',
                       '--repo', 'example/plugins', '--title', 'os-dhcp-interface-ha 26.7 0.2_30',
                       '--draft=false', '--latest=false']
    names = [c[0] for c in calls.mock_calls]
    assert names == ['snapshot_release', 'run_gh', 'upload_release_assets',
                     'snapshot_release', 'snapshot_matches_directory', 'run_gh']

    calls.reset_mock()
    calls.snapshot_release.side_effect = None
    calls.snapshot_release.return_value = SimpleNamespace(existed=True)
    publisher.publish('example/plugins', directory, '26.7', COMMIT)
    calls.run_gh.assert_called_once_with(promote)
    calls.upload_release_assets.assert_not_called()


def test_existing_release_accepts_only_identical_bytes(release):
    directory, calls = release
    calls.snapshot_release.side_effect = None
    calls.snapshot_release.return_value = SimpleNamespace(existed=True)
    calls.snapshot_matches_directory.return_value = False
    with pytest.raises(RuntimeError, match='different bytes'):
        publisher.publish('example/plugins', directory, '26.7', COMMIT)
    calls.run_gh.assert_not_called()
    calls.upload_release_assets.assert_not_called()


def test_failed_uploaded_verification_keeps_release_draft(release):
    directory, calls = release
    calls.snapshot_matches_directory.return_value = False
    with pytest.raises(RuntimeError, match="uploaded release"):
        publisher.publish("example/plugins", directory, "26.7", COMMIT)
    assert calls.run_gh.call_count == 1
    assert "--draft" in calls.run_gh.call_args.args[0]


def test_workflow_keeps_build_readonly_and_publishes_only_after_tests_and_verification(tmp_path, monkeypatch):
    test_workflow = (ROOT / '.github/workflows/ha-dhcp-interface-tests.yml').read_text()
    test_header = test_workflow.split('jobs:\n', 1)[0]
    assert not re.search(r'^  push:', test_header, re.MULTILINE)
    for event in ('pull_request', 'workflow_dispatch', 'workflow_call'):
        assert re.search(r'^  ' + event + ':', test_header, re.MULTILINE), event
    watched = ['.github/ci/ha_dhcp/build.sh', '.github/ci/shared/package_catalogue.py',
               '.github/ci/bind/shared/components.py', '.github/ci/bind/runtime/bind920_profile.py',
               '.github/ci/ci-tests/test_dhcp_interface_ha_release.py',
               '.github/workflows/bind-runtime-release.yml', '.github/workflows/bind-plugin-release.yml']
    patterns = trigger_paths(test_workflow, 'pull_request')
    for path in watched:
        assert any(fnmatchcase(path, pattern) for pattern in patterns), path
    workflow = (ROOT / '.github/workflows/ha-dhcp-interface-release.yml').read_text()
    header = workflow.split('jobs:\n', 1)[0]
    jobs = workflow_jobs(workflow)
    assert re.search(r'^  workflow_dispatch:', header, re.MULTILINE)
    assert re.search(r'^  push:\n    branches: \[master\]', header, re.MULTILINE)
    assert "branches: ['release/dhcp-interface-ha/**']" in header
    assert not re.search(r'^  pull_request:', header, re.MULTILINE)
    assert "if: needs.profile.outputs.release == 'true'" in workflow.split('  profile:')[0]
    assert 'release: ' + '$' + '{{ needs.profile.outputs.freebsd_release }}' in jobs['build']
    assert 'validate-source' in jobs['build']
    assert '--source-commit' in jobs['build'] and 'selection_sha256' in jobs['build']
    for name, required in {'build': {'profile', 'test'}, 'publish': {'profile', 'sign', 'verify'},
                           'verify-published': {'profile', 'sign', 'publish'},
                           'source-release': {'profile', 'sign', 'verify-published'}}.items():
        declared = re.search(r'^    needs: \[([^\n]+)\]$', jobs[name], re.MULTILINE)[1]
        assert required <= {value.strip() for value in declared.split(',')}, name
    for name, job in jobs.items():
        if name in ('select', 'profile', 'test'):
            continue
        expected = '$' + '{{ github.workflow_sha }}' if name in ('sign', 'publish', 'source-release') else (
            '$' + '{{ needs.profile.outputs.control_commit }}')
        for step in workflow_steps(job):
            if 'uses: actions/checkout@' in step:
                assert re.search(r'^          ref: ([^\n]+)$', step, re.MULTILINE)[1] == expected, name

    publish = jobs['publish']
    assert publish.index('package_catalogue.py validate') < publish.index('check-peers') < publish.index('Mint distribution repository token')
    assert publish.index('dhcp_interface_ha_channel.py promote') < publish.index('--owned-must-match') < publish.index('package_catalogue.py publish')
    composition = dict(component='dhcp', series='26.7', components={
        name: {'current_tag': name} for name in ('bind_runtime', 'bind_plugin', 'dhcp')})
    monkeypatch.setattr(selection.components, 'load_composition', Mock(return_value=composition))
    from shared import package_catalogue
    fetch = Mock(side_effect=lambda repository, component, *args: composition['components'][component])
    monkeypatch.setattr(package_catalogue, 'fetch', fetch)
    command = ['check-peers', '--series', '26.7', '--repository-root', str(tmp_path),
               '--composition-sha256', 'd' * 64, '--repository', 'example.invalid/distribution']
    for owned, expected in [(False, ['bind_runtime', 'bind_plugin']),
                            (True, ['bind_runtime', 'bind_plugin', 'dhcp'])]:
        fetch.reset_mock()
        selection.main(command + (['--owned-must-match'] if owned else []))
        assert [call.args[1] for call in fetch.call_args_list] == expected
    fetch.side_effect = lambda *args: {'current_tag': 'changed-peer'}
    with pytest.raises(RuntimeError, match='registered component changed'):
        selection.main(command)


def test_builder_retains_target_parser_and_native_installation_gates(tmp_path, monkeypatch):
    builder = (CI / 'ha_dhcp/build-dhcp-interface-ha.sh').read_text()
    assert builder.index('package_checksums.py"') < builder.index('"$pkg_static" add "$package"')
    assert builder.index('"$pkg_static" check -s') < builder.index('cp "$package" "$output/"')
    assert builder.index('test_ui_routes.php') < builder.index('cp "$package" "$output/"')
    verifier = (CI / 'ha_dhcp/verify-dhcp-interface-ha.sh').read_text()
    signer = workflow_jobs((ROOT / '.github/workflows/ha-dhcp-interface-release.yml').read_text())['sign']
    assert builder.index('materialize --series') < builder.index('setup-opnsense-repository.sh')
    for script in (builder, verifier):
        assert 'unset RP_OPNSENSE_SNAPSHOT' in script
    native = next(step for step in workflow_steps(signer) if 'uses: vmactions/freebsd-vm@' in step)
    body = textwrap.dedent(re.search(r'^          run: \|\n((?: {12}[^\n]*\n|\n)*)', native, re.MULTILINE)[1])
    assert 'unset RP_OPNSENSE_SNAPSHOT' in body
    assert body.index('authenticate-peers') < body.index('base64 -d')
    from shared import package_catalogue
    calls = Mock()
    monkeypatch.setattr(selection.components, 'select_role', calls.select_role)
    monkeypatch.setattr(package_catalogue, 'authenticate_components', calls.authenticate)
    command = ['authenticate-peers', '--series', '26.7',
        '--runtime-channel', str(tmp_path / 'runtime'), '--plugin-channel', str(tmp_path / 'plugin'),
        '--upstream', str(tmp_path / 'upstream.json'), '--reader-target', str(tmp_path / 'reader.json'),
        '--creator-target', str(tmp_path / 'creator.json'), '--approved-profile', str(tmp_path / 'recipe.json'),
        '--runtime-approval', str(tmp_path / 'approval.mk')]
    selection.main(command)
    assert [call[0] for call in calls.mock_calls] == ['select_role', 'authenticate', 'select_role']
    assert [call.args[3] for call in calls.select_role.call_args_list] == ['reader', 'creator']
    calls.reset_mock()
    calls.authenticate.side_effect = ValueError('untrusted peer')
    with pytest.raises(ValueError, match='untrusted peer'):
        selection.main(command)
    assert [call[0] for call in calls.mock_calls] == ['select_role', 'authenticate']
    for line in verifier.splitlines():
        if 'target_pkg.py" install' in line or 'target_pkg.py" verify' in line:
            assert '"$RP_READER_PKG_METADATA"' in line
            assert '--role reader' in line
            assert '--upstream "$RP_UPSTREAM_METADATA"' in line
    assert 'RP_TARGET_PKG_METADATA=$RP_READER_PKG_METADATA "$ci/shared/setup-opnsense-repository.sh"' in verifier
    assert "printf 'repository_snapshot=latest\\n'" in builder

@pytest.fixture
def signed_channel(tmp_path):
    return make_ha_channel(tmp_path / 'channel')


@pytest.fixture
def incoming_channel(tmp_path):
    return make_ha_channel(tmp_path / 'incoming', version='26.7_1', profile=COMMIT, snapshot='latest')


@pytest.fixture
def package_definition(tmp_path):
    path = tmp_path / 'plugin-package.mk'
    path.write_text('PLUGIN_NAME= dhcp-interface-ha\nPLUGIN_VERSION= 26.7\nPLUGIN_REVISION= 1\n')
    return path


@pytest.fixture
def signing_target(incoming_channel):
    upstream = json.loads((incoming_channel / 'upstream.json').read_text())
    target_path = write_resolved_target(incoming_channel.parent / 'creator-target.json', upstream)
    return target_path


def test_signed_channel_rejects_tampered_assets_and_foreign_key(signed_channel):
    manifest = signed_channel / 'channel.json'
    original = manifest.read_bytes()
    key = signed_channel / "resolver-plugins.pub"
    key.write_text("foreign key")
    data = json.loads(original)
    data['assets'][key.name] = hashlib.sha256(key.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="untrusted"):
        channel.validate(signed_channel)
    key.write_bytes(channel.PUBLIC_KEY.read_bytes())
    manifest.write_bytes(original)
    (signed_channel / "os-dhcp-interface-ha-0.2_30.pkg").write_text("changed")
    with pytest.raises(ValueError, match="checksums"):
        channel.validate(signed_channel)


def test_signer_rejects_unreviewed_snapshot_without_invalidating_older_metadata(incoming_channel, signing_target, tmp_path, monkeypatch, package_definition):
    build = incoming_channel / "build-metadata.txt"
    build.write_text(build.read_text().replace("repository_snapshot=latest", "repository_snapshot=26.7.3"))
    # Older current channels remain readable when a later source selects a new snapshot.
    assert channel.metadata(incoming_channel, "26.7", COMMIT, COMMIT)["repository_snapshot"] == "26.7.3"
    sign = Mock()
    monkeypatch.setattr(channel.releases, "stage_selected_repository", sign)
    with pytest.raises(ValueError, match="provenance"):
        channel.stage(incoming_channel, tmp_path / "signed", "26.7", COMMIT, COMMIT,
                      incoming_channel / "upstream.json", tmp_path / "key", signing_target, package_definition)
    sign.assert_not_called()


def test_signed_retry_reuses_exact_snapshot_and_rejects_source_mismatch(incoming_channel, signing_target, tmp_path, monkeypatch, package_definition):
    remote = tmp_path / 'remote'
    shutil.copytree(incoming_channel, remote)
    snapshot = SimpleNamespace(existed=True, directory=remote)
    monkeypatch.setattr(channel.releases, 'snapshot_release', Mock(return_value=snapshot))
    output = tmp_path / 'reuse'
    inputs = incoming_channel / 'upstream.json', signing_target, package_definition
    assert channel.reuse('example/repository', incoming_channel, output, '26.7', COMMIT, COMMIT, *inputs)
    assert channel.releases.directory_checksums(output) == channel.releases.directory_checksums(remote)
    channel.validate(output, target_metadata=signing_target, trusted_upstream=inputs[0])
    build = incoming_channel / 'build-metadata.txt'
    original = build.read_text()
    build.write_text(original.replace(COMMIT, 'c' * 40))
    with pytest.raises(ValueError, match='different source/profile'):
        channel.reuse('example/repository', incoming_channel, tmp_path / 'reject', '26.7', 'c' * 40, 'c' * 40, *inputs)
    build.write_text(original.replace('pkg_creator=2.3.1_1', 'pkg_creator=2.4.0'))
    with pytest.raises(ValueError, match='provenance'):
        channel.reuse('example/repository', incoming_channel, tmp_path / 'foreign', '26.7', COMMIT, COMMIT, *inputs)
    assert not (tmp_path / 'foreign').exists()


def test_forward_retry_after_rollback_retains_bytes_and_ancestry_guards(tmp_path, monkeypatch, release_history):
    old_commit, new_commit = release_history(), release_history()
    signed_channel = make_ha_channel(tmp_path / 'channel', source=new_commit)
    old_dir = make_ha_channel(tmp_path / 'old', source=old_commit, version='0.2_29')
    old = SimpleNamespace(existed=True, directory=old_dir)
    archive = SimpleNamespace(existed=False)
    reads = Mock(side_effect=lambda repository, tag, recovery:
                 old if tag == 'pkg-dhcp-interface-ha-26.7' else archive)
    monkeypatch.setattr(channel.releases, 'snapshot_release', reads)
    monkeypatch.setattr(channel.releases, 'snapshot_matches_directory', lambda snapshot, directory:
                        snapshot.existed and channel.releases.directory_checksums(snapshot.directory)
                        == channel.releases.directory_checksums(directory))
    monkeypatch.setattr(channel.releases, 'release_snapshots_match', lambda a, b: True)
    def publish_archive(*args):
        assert args[1] == 'pkg-dhcp-interface-ha-26.7-0.2_30'
        archive.existed = True
        archive.directory = signed_channel
    monkeypatch.setattr(channel.releases, 'publish_snapshot', publish_archive)
    publish = Mock(side_effect=RuntimeError('upload failed'))
    restore = Mock()
    monkeypatch.setattr(channel.releases, 'publish', publish)
    monkeypatch.setattr(channel.releases, 'restore_release', restore)
    with pytest.raises(RuntimeError, match='upload failed'):
        channel.promote('example/repository', signed_channel, tmp_path / 'first')
    restore.assert_called_once_with('example/repository', old)
    publish.side_effect = None
    channel.promote('example/repository', signed_channel, tmp_path / 'retry')
    assert publish.call_count == 2
    assert all(call.args[1] == 'pkg-dhcp-interface-ha-26.7' for call in publish.call_args_list)
    assert all(call.args[1].startswith('pkg-dhcp-interface-ha-') for call in reads.call_args_list)
    # A matching archive cannot authorize an old-source retry over a newer current.
    old.directory, archive.directory = signed_channel, old_dir
    with pytest.raises(RuntimeError, match='stale promotion'):
        channel.promote('example/repository', old_dir, tmp_path / 'stale')
    assert publish.call_count == 2
    # Nor can ancestry authorize replacing an immutable snapshot's bytes.
    old.directory = old_dir
    with pytest.raises(RuntimeError, match='different bytes'):
        channel.promote('example/repository', signed_channel, tmp_path / 'changed')
    assert publish.call_count == 2


@pytest.mark.parametrize("variant,fault", [
    (False, None), (False, "key"), (False, "identity"),
    (True, None), (True, "key"), (True, "identity"), (True, "inputs"), (True, "control"), (True, "downgrade"),
])
def test_signer_verifies_package_and_public_key_before_signing(incoming_channel, signing_target, tmp_path, monkeypatch, variant, fault, package_definition):
    target = channel.target_pkg.load_target(signing_target, "26.7")
    options = {}
    if variant:
        upstream = json.loads((incoming_channel / 'upstream.json').read_text())
        reader = write_resolved_target(tmp_path / 'reader-target.json', upstream, role='reader')
        inputs = dict(schema=2, component='dhcp_interface_ha', series='26.7',
            source_sha256='4' * 64, recipe_sha256='5' * 64, runtime_approval=None,
            environment=dict(tools_tag=upstream['tools_tag'], tools_commit='b' * 40,
                build_conf_sha256='c' * 64, freebsd_release=upstream['freebsd_release'],
                core_commit=upstream['core_commit']),
            package_targets=dict(creator=json.loads(signing_target.read_text()), reader=json.loads(reader.read_text())))
        file = incoming_channel / channel.series_builds.INPUT_FILE
        file.write_text(json.dumps(inputs))
        trusted = tmp_path / 'trusted' / file.name
        trusted.parent.mkdir()
        trusted.write_bytes(file.read_bytes())
        build = incoming_channel / 'build-metadata.txt'
        build.write_text(build.read_text() + 'control_commit=' + 'd' * 40 + '\n' +
            'build_fingerprint=' + channel.series_builds.fingerprint(inputs) + '\n')
        options = dict(trusted_inputs=trusted, control_commit='d' * 40)
        if fault == 'inputs':
            file.write_text(json.dumps(dict(inputs, recipe_sha256='6' * 64)))
        elif fault == 'control':
            options['control_commit'] = 'e' * 40
        elif fault == 'downgrade':
            build.write_text('\n'.join(line for line in build.read_text().splitlines()
                if not line.startswith(('control_commit=', 'build_fingerprint='))) + '\n')
    calls = Mock()
    calls.check_output.side_effect = [channel.PUBLIC_KEY.read_bytes() if fault != "key" else b"foreign key"]
    calls.query_package.return_value = (("foreign-package" if fault == "identity" else channel.NAME, "26.7_1", f"opnsense/{channel.NAME}", target.identity.abi), set())
    monkeypatch.setattr(channel.subprocess, "check_output", calls.check_output)
    monkeypatch.setattr(channel.releases, "query_package", calls.query_package)
    monkeypatch.setattr(channel.target_pkg, "verify_target_pkg", calls.verify_target_pkg)
    monkeypatch.setattr(channel.package_checksums, "verify_archive", calls.verify_archive)

    def fake_catalog(packages, output, key, pkg, metadata):
        output.mkdir()
        for path in packages + metadata:
            (output / path.name).write_bytes(path.read_bytes())
        (output / "meta.conf").write_text("fixture")
        (output / "packagesite.pkg").write_text("signed catalogue")
    calls.stage.side_effect = fake_catalog
    monkeypatch.setattr(channel.releases, "stage_selected_repository", calls.stage)
    args = (incoming_channel, tmp_path / "signed", "26.7", COMMIT, COMMIT,
            incoming_channel / "upstream.json", tmp_path / "private-key", signing_target, package_definition)
    if fault is None:
        channel.stage(*args, **options)
        assert channel.validate(tmp_path / "signed", target_metadata=signing_target, trusted_upstream=incoming_channel / "upstream.json")["plugin_version"] == "26.7_1"
        calls.verify_archive.assert_called_once_with(channel.PKG, incoming_channel / f"{channel.NAME}-26.7_1.pkg")
        calls.verify_target_pkg.assert_called_once_with(target, 'pkg')
        names = [c[0] for c in calls.mock_calls]
        assert names.index("verify_target_pkg") < names.index("stage")
        assert names.index("verify_archive") < names.index("stage")
    else:
        error = "controller" if fault in ('inputs', 'control', 'downgrade') else "package identity" if fault == "identity" else "signing key"
        with pytest.raises(ValueError, match=error):
            channel.stage(*args, **options)
        calls.stage.assert_not_called()


def test_identical_current_can_restore_a_missing_immutable_snapshot(signed_channel, tmp_path, monkeypatch):
    current = SimpleNamespace(existed=True, directory=signed_channel)
    monkeypatch.setattr(channel.releases, "snapshot_release", Mock(side_effect=[current, SimpleNamespace(existed=False)]))
    monkeypatch.setattr(channel.releases, "snapshot_matches_directory", lambda a, b: True)
    archive = Mock()
    update = Mock()
    monkeypatch.setattr(channel.releases, "publish_snapshot", archive)
    monkeypatch.setattr(channel.releases, "publish", update)
    channel.promote("example/repository", signed_channel, tmp_path / "recovery")
    assert archive.call_args.args[1] == "pkg-dhcp-interface-ha-26.7-0.2_30"
    update.assert_not_called()
