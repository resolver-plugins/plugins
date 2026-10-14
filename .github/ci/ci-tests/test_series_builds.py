"""Real Git and local upstream fixtures for independent per-series build selection."""
from git_fixtures import *
from module_fixtures import load_module
from package_fixtures import write_resolved_target
import pytest

series_builds = load_module('series_builds', 'shared/series_builds.py')
from ha_fixtures import make_ha_channel


@pytest.fixture
def series_repository(tmp_path):
    repository = init_repository(tmp_path / 'repository')
    base = commit(repository, {
        'dns/bind/Makefile': 'PLUGIN_NAME= bind-rp\nPLUGIN_VERSION= 1.36\n',
        'dns/bind/src/plugin.py': 'bind source\n',
        'net/dhcp-interface-ha/Makefile': 'PLUGIN_NAME= dhcp-interface-ha\nPLUGIN_VERSION= 0.2\nPLUGIN_REVISION= 1\n',
        'net/dhcp-interface-ha/src/plugin.py': 'HA source\n',
        'Mk/plugins.mk': 'shared source framework\n',
        '.github/ci/bind/runtime/build-bind920.sh': 'runtime recipe\n',
        '.github/ci/bind/plugin/build-os-bind-rp.sh': 'plugin recipe\n',
        '.github/ci/ha_dhcp/build-dhcp-interface-ha.sh': 'HA recipe\n',
    }, 'upstream source and controller recipes')
    refs = []
    for series in ('26.1', '26.7'):
        git(repository, 'checkout', '-B', f'source-{series}', base)
        source = commit(repository, {
            'dns/bind/Makefile': f'PLUGIN_NAME= bind-rp\nPLUGIN_VERSION= {series}\n',
            'net/dhcp-interface-ha/Makefile': f'PLUGIN_NAME= dhcp-interface-ha\nPLUGIN_VERSION= {series}\nPLUGIN_REVISION= 1\n',
        }, 'reviewed source')
        for prefix in ('release/bind-rp/', 'release/dhcp-interface-ha/'):
            ref = prefix + series
            git(repository, 'branch', ref, source)
            refs.append('refs/heads/' + ref)
    git(repository, 'checkout', 'master')
    control = commit(repository, {'dns/bind/Makefile':
        'RP_BIND_VERSION= 9.20.26_2\nRP_BIND_PORTS_COMMIT= ' + '3' * 40 + '\n'}, 'runtime approval')
    git(repository, 'remote', 'add', 'origin', str(repository))
    tools = init_repository(tmp_path / 'tools')
    for series, tag, release in (('26.1', '26.1.11', '14.3'), ('26.7', '26.7.1', '15.1')):
        commit(tools, {f'config/{series}/build.conf': f'OS?= {release}\n'}, tag)
        git(tools, 'tag', tag)
    def discover(root, source, series, output):
        shutil.copytree(tools, output)
        tag, _commit, release = series_builds.upstream_inputs.tools_release_profile(output, series)
        return upstream_profile(series, base, tools_tag=tag, freebsd_release=release)
    def resolve(upstream, series, architecture, output):
        profile = json.loads(upstream.read_text())
        for role in ('creator', 'reader'):
            write_resolved_target(output / f'{role}-target.json', profile, role=role,
                creator=dict(name='pkg', version='2.3.1_1', origin='ports-mgmt/pkg',
                             abi=f"FreeBSD:{profile['freebsd_release'].split('.')[0]}:amd64"))
    event = dict(name='schedule', ref='refs/heads/master', sha=control, workflow_sha=control,
                 before=None, merged=None, base_ref=None, merge_commit_sha=None,
                 inputs=dict(mode=None, series=None, pull_number=None), pr_base_ref=None)
    return SimpleNamespace(repository=repository, tools=tools, refs=refs, event=event, discover=discover, resolve=resolve)


@pytest.mark.parametrize('component', ['bind_plugin', 'dhcp_interface_ha', 'bind_runtime'])
def test_only_the_series_with_changed_inputs_is_selected(series_repository, component):
    fixture = series_repository
    published = {}
    def choose():
        return series_builds.select_builds(fixture.repository, component, fixture.event, fixture.refs, [],
            'fixture/distribution', discover=fixture.discover,
            resolve_targets=fixture.resolve,
            published=lambda _repo, _component, series: published.get(series))
    first = choose()
    assert [item['selection']['series'] for item in first] == ['26.7', '26.1']
    published.update({item['selection']['series']: item['inputs'] for item in first})
    assert choose() == []
    commit(fixture.tools, {'config/26.7/build.conf': 'OS?= 15.2\n'}, '26.7 environment change')
    git(fixture.tools, 'tag', '26.7.2')
    changed = choose()
    assert [item['selection']['series'] for item in changed] == ['26.7']
    published['26.7'] = changed[0]['inputs']
    assert choose() == []
    if component != 'bind_runtime':
        source = series_builds.selection.RELEASE_PREFIX[component] + '26.1'
        git(fixture.repository, 'checkout', source)
        commit(fixture.repository, {'README.md': 'source documentation only\n',
                                   '.resolver-plugins/upstream.json': 'obsolete, ignored metadata\n'}, 'irrelevant source change')
        git(fixture.repository, 'checkout', 'master')
        assert choose() == []
        git(fixture.repository, 'checkout', source)
        commit(fixture.repository, {series_builds.PACKAGES[component] + '/src/plugin.py': 'changed plugin\n'}, 'reviewed plugin change')
        git(fixture.repository, 'checkout', 'master')
        assert [item['selection']['series'] for item in choose()] == ['26.1']


def test_merged_release_source_selects_only_its_own_series(series_repository):
    fixture = series_repository
    merged_source = git(fixture.repository, 'rev-parse', 'release/bind-rp/26.1')
    event = dict(fixture.event, name='pull_request_target', merged=True,
                 base_ref='release/bind-rp/26.1', merge_commit_sha=merged_source)
    selected = series_builds.select_builds(fixture.repository, 'bind_plugin', event, fixture.refs,
        ['dns/bind/src/plugin.py'], 'fixture/distribution', discover=fixture.discover,
        resolve_targets=fixture.resolve,
        published=lambda *_args: None)
    assert [item['selection']['series'] for item in selected] == ['26.1']
    assert selected[0]['selection']['source_ref'] == merged_source


def test_dispatch_is_scoped_to_own_producer_and_explicit_series(monkeypatch):
    calls = []
    monkeypatch.setattr(series_builds.subprocess, 'run', lambda *args, **kwargs: calls.append((args, kwargs)))
    series_builds.dispatch('fixture/plugins', 'dhcp_interface_ha', '26.1')
    command, kwargs = calls[0]
    assert command[0] == ['gh', 'api', '--method', 'POST',
        'repos/fixture/plugins/actions/workflows/ha-dhcp-interface-release.yml/dispatches', '--input', '-']
    assert json.loads(kwargs['input']) == dict(ref='master', inputs=dict(series='26.1'))


def test_ha_environment_variants_preserve_version_snapshots_and_reject_stale_retry(series_repository, tmp_path, monkeypatch):
    from ha_dhcp import dhcp_interface_ha_channel as channel
    from shared import package_catalogue
    fixture = series_repository
    def discover():
        return series_builds.select_builds(fixture.repository, 'dhcp_interface_ha', fixture.event, fixture.refs, [],
            'fixture/distribution', discover=fixture.discover, resolve_targets=fixture.resolve, published=lambda *_args: None)[0]
    old = discover()
    before = make_ha_channel(tmp_path / 'before', version='26.7_1', source=old['selection']['source_ref'],
        profile=old['selection']['source_ref'], control=old['selection']['control_ref'], snapshot='latest',
        inputs=old['inputs'], upstream_metadata=old['upstream'])
    commit(fixture.tools, {'config/26.7/build.conf': 'OS?= 15.1 # new numeric release\n'}, 'new 26.7 environment')
    git(fixture.tools, 'tag', '26.7.2')
    new = discover()
    after = make_ha_channel(tmp_path / 'after', version='26.7_1', source=new['selection']['source_ref'],
        profile=new['selection']['source_ref'], control=new['selection']['control_ref'], snapshot='latest',
        inputs=new['inputs'], upstream_metadata=new['upstream'])
    current = SimpleNamespace(existed=True, directory=before)
    old_bytes = {path.name: path.read_bytes() for path in before.iterdir()}
    old_receipt = package_catalogue.receipt(before, 'dhcp', '26.7')
    new_receipt = package_catalogue.receipt(after, 'dhcp', '26.7')
    assert old_receipt['current_tag'] == new_receipt['current_tag'] == 'pkg-dhcp-interface-ha-26.7'
    assert old_receipt['snapshot_tag'] != new_receipt['snapshot_tag']
    assert set(old_receipt['packages']) == set(new_receipt['packages']) == {'os-dhcp-interface-ha-26.7_1.pkg'}
    target = tmp_path / 'creator-target.json'
    target.write_text(json.dumps(new['targets']['creator']))
    package = tmp_path / 'plugin-package.mk'
    package.write_text(git(fixture.repository, 'show', new['selection']['source_ref'] + ':net/dhcp-interface-ha/Makefile') + '\n')
    monkeypatch.setattr(channel.releases, 'snapshot_release', lambda *_args: current)
    with pytest.raises(ValueError, match='inputs'):
        channel.reuse('fixture/distribution', after, tmp_path / 'wrong-variant', '26.7',
            new['selection']['source_ref'], new['selection']['source_ref'], after / 'upstream.json', target, package,
            trusted_inputs=after / series_builds.INPUT_FILE, control_commit=new['selection']['control_ref'])
    assert not (tmp_path / 'wrong-variant').exists()
    monkeypatch.chdir(fixture.repository)
    monkeypatch.setattr(channel.releases, 'snapshot_release', lambda _repo, tag, _dir:
        current if tag == 'pkg-dhcp-interface-ha-26.7' else SimpleNamespace(existed=False))
    monkeypatch.setattr(channel.releases, 'release_snapshots_match', lambda *_args: True)
    monkeypatch.setattr(channel.releases, 'snapshot_matches_directory', lambda *_args: False)
    publish = Mock()
    monkeypatch.setattr(channel.releases, 'publish_snapshot', publish.snapshot)
    monkeypatch.setattr(channel.releases, 'publish', publish.current)
    channel.promote('fixture/distribution', after, tmp_path / 'promotion')
    assert publish.snapshot.call_args.args[1] == new_receipt['snapshot_tag']
    assert publish.current.call_args.args[1] == new_receipt['current_tag']
    assert old_bytes == {path.name: path.read_bytes() for path in before.iterdir()}
    current.directory = after
    with pytest.raises(RuntimeError, match='stale upstream environment'):
        channel.promote('fixture/distribution', before, tmp_path / 'stale')
    assert publish.current.call_count == 1


def test_discovery_seal_is_required_before_materializing_build_inputs(series_repository, tmp_path):
    fixture = series_repository
    chosen = series_builds.select_builds(fixture.repository, 'bind_plugin', fixture.event, fixture.refs, [],
        'fixture/distribution', discover=fixture.discover, resolve_targets=fixture.resolve, published=lambda *_args: None)[0]
    directory = tmp_path / 'discovery'
    digest = series_builds.write_discovery(directory, chosen)
    (directory / series_builds.INPUT_FILE).write_text('{}')
    with pytest.raises(ValueError, match='identities or files differ'):
        series_builds.materialize_discovery(fixture.repository, directory, digest, 'bind_plugin', '26.7',
            chosen['selection']['source_ref'], chosen['selection']['control_ref'], tmp_path / 'frozen-tools', tmp_path / 'upstream.json')
    assert not (tmp_path / 'frozen-tools').exists() and not (tmp_path / 'upstream.json').exists()


def test_materialization_uses_frozen_tools_after_upstream_advances(series_repository, tmp_path, monkeypatch):
    fixture = series_repository
    chosen = series_builds.select_builds(fixture.repository, 'bind_plugin', fixture.event, fixture.refs, [],
        'fixture/distribution', discover=fixture.discover, resolve_targets=fixture.resolve, published=lambda *_args: None)[0]
    directory = tmp_path / 'discovery'
    digest = series_builds.write_discovery(directory, chosen)
    commit(fixture.tools, {'config/26.7/build.conf': 'OS?= 15.2\n'}, 'later upstream environment')
    git(fixture.tools, 'tag', '26.7.2')
    run = series_builds.subprocess.run
    fetched = []
    def local_network(command, **kwargs):
        if command[:1] == ['git'] and 'fetch' in command:
            fetched.append(command[-1])
            assert command[-2] == 'https://github.com/opnsense/tools.git'
            command = [*command[:-2], str(fixture.tools), command[-1]]
        return run(command, **kwargs)
    monkeypatch.setattr(series_builds.subprocess, 'run', local_network)
    tools = tmp_path / 'frozen-tools'
    output = tmp_path / 'upstream.json'
    series_builds.materialize_discovery(fixture.repository, directory, digest, 'bind_plugin', '26.7',
        chosen['selection']['source_ref'], chosen['selection']['control_ref'], tools, output)
    assert fetched == [chosen['tools']['tools_commit']]
    assert json.loads(output.read_text()) == chosen['upstream']
    assert series_builds.metadata_profile.verify_tools_profile(chosen['upstream'], tools) == chosen['tools']
