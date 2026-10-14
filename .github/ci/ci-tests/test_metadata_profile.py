from git_fixtures import *
from module_fixtures import *

import pytest


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[3]
METADATA_PROFILE = pathlib.Path(
    os.environ.get(
        'METADATA_PROFILE', REPOSITORY_ROOT / '.github/ci/shared/metadata_profile.py'
    )
)
UPSTREAM_COMMIT = 'a' * 40
CORE_COMMIT = 'b' * 40
CORE_ARCHIVE_SHA256 = 'c' * 64


@pytest.mark.parametrize(
    ('field', 'invalid_value'),
    (
        (None, None),
        ('core_commit', 'refs/heads/stable/26.1'),
        ('upstream_commit', 'refs/heads/stable/26.1'),
        ('upstream_branch', 'stable/26.7'),
        ('tools_tag', '26.7.1'),
        ('tools_tag', '26.1.r1'),
    ),
)
def test_cli_validates_strict_profile_fields(tmp_path, field, invalid_value):
    profile = upstream_profile(upstream_commit=UPSTREAM_COMMIT, core_commit=CORE_COMMIT,
                               archive_sha256=CORE_ARCHIVE_SHA256)
    if field:
        profile[field] = invalid_value
    if field == 'core_commit':
        profile['core_archive_url'] = (
            f'https://github.com/opnsense/core/archive/{invalid_value}.tar.gz'
        )
    metadata_path = tmp_path / 'upstream.json'
    metadata_path.write_text(json.dumps(profile))

    result = subprocess.run(
        ['python3', METADATA_PROFILE, metadata_path, '26.1', 'core_commit'],
        text=True,
        capture_output=True,
        check=False,
    )

    if field is None:
        assert result.returncode == 0, result.stderr
        assert result.stdout == CORE_COMMIT + '\n'
    else:
        assert result.returncode != 0
        assert result.stdout == ''


def test_tools_evidence_uses_selected_historical_tag_and_exact_blob(tmp_path):
    module = load_module('metadata_profile', METADATA_PROFILE)
    tools = init_repository(tmp_path / 'tools')
    contents = '# selected upstream recipe\n  OS ?= 14.2  # supported literal\n'
    selected = commit(tools, {'config/26.1/build.conf': contents}, 'tools 26.1')
    git(tools, 'tag', '26.1')
    commit(tools, {'config/26.1/build.conf': 'OS?=14.3\n'}, 'newer tools')
    git(tools, 'tag', '26.1.11')
    profile = upstream_profile(tools_tag='26.1', freebsd_release='14.2')

    record = module.verify_tools_profile(profile, tools)

    assert record == dict(schema=1, series='26.1', tools_tag='26.1', tools_commit=selected,
                         path='config/26.1/build.conf',
                         build_conf_sha256=hashlib.sha256(contents.encode()).hexdigest(),
                         freebsd_release='14.2')
    assert module.validate_tools_record(record, profile) == record
    with pytest.raises(ValueError, match='OS assignment does not match'):
        module.verify_tools_profile(dict(profile, freebsd_release='14.3'), tools)
    commit(tools, {'config/26.1/build.conf': 'OS?=14.3\nOS?=14.4\n'}, 'ambiguous upstream OS')
    git(tools, 'tag', '26.1.12')
    with pytest.raises(ValueError, match='OS assignment'):
        module.verify_tools_profile(dict(profile, tools_tag='26.1.12', freebsd_release='14.3'), tools)


def test_build_inputs_are_discovered_without_source_json_and_frozen_per_run(tmp_path, monkeypatch):
    inputs = load_module('upstream_inputs', 'shared/upstream_inputs.py')
    fixture = bind_sync_repositories(tmp_path)
    repository, tools = fixture['repository'], fixture['tools']
    git(repository, 'branch', 'stable/26.1', fixture['initial'])
    core = init_repository(tmp_path / 'core')
    core_commit = commit(core, {'README': 'core source\n'}, 'official core')
    git(core, 'branch', 'stable/26.1', core_commit)
    archives = []
    def archive_hash(url):
        archives.append(url)
        return 'a' * 64
    monkeypatch.setattr(inputs, 'core_archive_sha256', archive_hash)
    selected = []
    for patch, release in ((12, '14.4'), (13, '14.5')):
        tag = f'26.1.{patch}'
        tools_commit = commit(tools, {'config/26.1/build.conf': f'OS?= {release}\n'}, tag)
        git(tools, 'tag', tag)
        git(core, 'branch', '-f', 'stable/26.1', core_commit)
        resolved_tools = tmp_path / f'resolved-tools-{patch}'
        profile = inputs.discover_inputs(repository, fixture['source_commit'], '26.1', resolved_tools,
            plugins_url=str(repository), tools_url=str(tools), core_url=str(core))
        assert profile == upstream_profile('26.1', fixture['initial'], core_commit=core_commit,
                                          tools_tag=tag, freebsd_release=release)
        proof = inputs.metadata_profile.verify_tools_profile(profile, resolved_tools)
        assert proof['tools_commit'] == tools_commit
        selected.append((profile, proof))
        core_commit = commit(core, {'README': f'new core {patch}\n'}, 'later official core')
    assert selected[0][0]['core_commit'] != selected[1][0]['core_commit']
    assert selected[0][1]['freebsd_release'] == '14.4'
    assert inputs.validate_source_profile(selected[0][0], fixture['source_commit'], repository) == selected[0][0]
    assert archives == [profile['core_archive_url'] for profile, _proof in selected]
    assert git(repository, 'ls-tree', fixture['source_commit'], '--', '.resolver-plugins') == ''


def test_environment_variants_reject_older_core_and_tools_inputs(tmp_path):
    inputs = load_module('variant_upstream_inputs', 'shared/upstream_inputs.py')
    core = init_repository(tmp_path / 'core')
    old_commit = commit(core, {'README': 'old\n'}, 'old core')
    new_commit = commit(core, {'README': 'new\n'}, 'new core')
    old = upstream_profile(core_commit=old_commit)
    new = upstream_profile(core_commit=new_commit)
    assert inputs.environment_can_advance(old, new, core_url=str(core))
    assert not inputs.environment_can_advance(new, old, core_url=str(core))
    assert not inputs.environment_can_advance(dict(old, tools_tag='26.1.12'), old, core_url=str(core))
