from common_imports import *

from workflow_fixtures import assert_permissions, assert_pinned_actions, assert_checkout_credentials, job_text, workflow_jobs

import pytest


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[3]
WORKFLOW = REPOSITORY_ROOT / '.github/workflows/bind-package-release.yml'


def workflow_text() -> str:
    return WORKFLOW.read_text(encoding='utf-8')


def test_workflow_selects_an_immutable_release_source():
    workflow = workflow_text()
    assert 'workflow_dispatch:' in workflow
    assert 'refs/heads/release/bind-rp/$series' in workflow
    assert 'refs/pull/$INPUT_PULL_NUMBER/head' in workflow
    assert 'if [[ "$pr_base" == master ]]' in workflow
    assert 'elif [[ "$pr_base" == "release/bind-rp/$series" ]]' in workflow
    assert 'git checkout "$SOURCE_COMMIT" -- .resolver-plugins/upstream.json Mk dns/bind' in workflow


def test_merged_release_source_pr_publishes_its_exact_merge_commit():
    workflow = workflow_text()
    trigger = workflow.split('  pull_request_target:', 1)[1].split('  workflow_dispatch:', 1)[0]
    select = job_text(workflow, 'select')

    assert "pull_request_target:\n    types: [closed]\n    branches:\n      - 'release/bind-rp/**'" in workflow
    for path in ("'.resolver-plugins/**'", "'dns/bind/**'", "'Mk/**'"):
        assert path in trigger
    assert "if: github.event_name != 'pull_request_target' || github.event.pull_request.merged == true" in select
    assert 'source_ref="$PR_MERGE_COMMIT"' in select
    assert 'control_ref="$GITHUB_WORKFLOW_SHA"' in select
    assert 'github.event.pull_request.head.sha' not in workflow


def test_package_affecting_master_pushes_publish_the_newest_release_series():
    workflow = workflow_text()

    assert 'push:\n    branches: [master]' in workflow
    select = job_text(workflow, 'select')
    assert 'git/matching-refs/heads/release/bind-rp/' in select
    assert 'sort -V' in select


@pytest.mark.parametrize('paths,expected', [
    (['net/dhcp-interface-ha/Makefile', 'net/dhcp-interface-ha/src/runtime.py'], False),
    (['.github/ci/ha_dhcp/build-dhcp-interface-ha.sh', '.github/ci/ha_dhcp/verify-dhcp-interface-ha.sh',
      '.github/ci/ha_dhcp/publish-dhcp-interface-ha.py', '.github/ci/ha_dhcp/dhcp_interface_ha_release.py',
      '.github/ci/ha_dhcp/dhcp_interface_ha_channel.py', '.github/ci/ha_dhcp/new_helper.py',
      '.github/workflows/ha-dhcp-interface-release.yml'], False),
    (['.github/ci/ci-tests/test_package_release_workflow.py', '.github/ci/ci-tests/ha_fixtures.py',
      'dns/bind/tests/test_watcher.py', 'dns/bind/tests/fixtures/example.py'], False),
    (['README.md', 'docs/building.md', '.resolver-plugins/overlay-paths.txt',
      '.github/ci/sync_upstream.py', '.github/ci/bind920_candidate.py', 'scripts/install-repository.sh'], False),
    (['dns/bind/Makefile', 'dns/bind/pkg-descr', 'dns/bind/+POST_INSTALL.pre',
      'dns/bind/src/etc/rc.d/named'], True),
    (['.github/ci/bind/build-bind920.sh', '.github/ci/bind/build-os-bind-rp.sh',
      '.github/ci/bind/bind920_profile.py', '.github/ci/bind/bind_compatibility.py',
      '.github/ci/bind/reuse_bind920.py', '.github/ci/bind/verify-bind-runtime.sh',
      '.github/ci/bind/new_helper.py'], True),
    (['.github/ci/shared/package_catalogue.py', '.github/ci/shared/release_channel.py',
      '.github/ci/shared/target_pkg.py', '.github/ci/shared/package_checksums.py',
      '.github/ci/shared/metadata_profile.py', '.github/ci/shared/setup-opnsense-repository.sh',
      '.github/ci/shared/tools/new_helper.py'], True),
    (['.resolver-plugins/bind920.json', '.resolver-plugins/bind-compatibility.json',
      '.resolver-plugins/upstream.json', '.resolver-plugins/target-pkg.json',
      '.resolver-plugins/target-pkg-content.json'], True),
    (['Mk/plugins.mk', 'Scripts/version.sh', 'Templates/example.in',
      'docs/package-repository/resolver-plugins.pub', 'scripts/install-os-bind-rp.sh',
      '.github/workflows/bind-package-release.yml'], True),
])
def test_master_build_filters_include_bind_inputs_and_exclude_ha_and_tests(paths, expected):
    trigger = workflow_text().split('  push:', 1)[1].split('  pull_request_target:', 1)[0]
    patterns = re.findall(r"^      - '([^']+)'$", trigger, re.MULTILINE)
    for path in paths:
        selected = False
        # These filters use literal paths and trailing /**, with ordered exclusions.
        for pattern in patterns:
            if fnmatchcase(path, pattern.removeprefix('!')):
                selected = not pattern.startswith('!')
        assert selected is expected, path


@pytest.mark.parametrize('helper', [
    'bind/reuse_bind920.py', 'shared/release_channel.py', 'shared/package_catalogue.py',
    'ha_dhcp/dhcp_interface_ha_channel.py', 'ha_dhcp/publish-dhcp-interface-ha.py',
])
def test_release_entrypoints_resolve_cross_directory_imports(tmp_path, helper):
    result = subprocess.run([sys.executable, '-I', REPOSITORY_ROOT / '.github/ci' / helper, '--help'],
                            cwd=tmp_path, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_merging_a_repack_recovery_rebuilds_its_exact_series():
    select = workflow_text().split('  select:', 1)[1].split('  profile:', 1)[0]

    assert '[ "$changed" = .resolver-plugins/target-pkg.json ]' in select
    assert 'target_pkg.py changed-series "$before" "$after"' in select
    assert 'series=$recovered_series' in select


def test_production_runs_only_from_the_master_control_plane():
    workflow = workflow_text()
    select = job_text(workflow, 'select')
    profile = job_text(workflow, 'profile')
    test = job_text(workflow, 'test')
    bind = job_text(workflow, 'bind')
    build = job_text(workflow, 'build')
    assert '[[ "$GITHUB_REF" == refs/heads/master ]]' in select
    assert 'control_ref=$GITHUB_SHA' in select
    assert 'ref: ${{ needs.select.outputs.control_ref }}' in profile
    for job in (test, bind, build):
        assert 'ref: ${{ needs.profile.outputs.control_commit }}' in job


def test_workflow_selects_the_profile_freebsd_release():
    workflow = workflow_text()
    assert 'release: ${{ needs.profile.outputs.freebsd_release }}' in workflow


def test_workflow_builds_the_plugin_with_the_selected_distribution_channel():
    workflow = workflow_text()
    assert 'RP_BIND920_CHANNEL_URL: https://github.com/resolver-plugins/repository/releases/download/pkg-${{ needs.select.outputs.series }}' in workflow
    assert 'needs: [select, profile, test, bind]' in workflow


def test_failed_production_bind_job_can_only_propose_a_content_identical_pkg_repack():
    workflow = workflow_text()
    recovery = job_text(workflow, 'recover-target-pkg')
    validator = recovery
    proposer = job_text(workflow, 'propose-target-pkg')

    assert "needs.bind.result == 'failure'" in validator
    assert "needs.select.outputs.mode == 'production'" in validator
    assert '.resolver-plugins/target-pkg-content.json' in validator
    assert 'target_pkg.py refresh' in validator
    assert '[ "$changed" = .resolver-plugins/target-pkg.json ]' in proposer
    assert 'gh pr create' in proposer
    assert 'gh pr merge' not in validator + proposer


def test_workflow_uses_sha_pinned_actions_and_nonpersistent_checkout_credentials():
    workflow = workflow_text()
    assert_pinned_actions(workflow)
    for name, job in workflow_jobs(workflow).items():
        if 'uses: actions/checkout@' in job:
            assert_checkout_credentials(job, persistent=name == 'propose-target-pkg')


def test_production_signing_and_publication_are_separate_from_builds():
    workflow = workflow_text()
    jobs = workflow_jobs(workflow)
    assert 'secrets.' not in workflow.split('jobs:', 1)[0]
    assert_permissions(workflow, {'contents': 'read', 'pull-requests': 'read'})
    overrides = {
        'propose-target-pkg': {'contents': 'write', 'pull-requests': 'write'},
        'publish-development': {'contents': 'write', 'pull-requests': 'read'},
        'source-release': {'contents': 'write'},
    }
    for name in ('bind', 'recover-target-pkg', 'build', 'verify-development',
                 'sign', 'publish', 'verify', 'verify-published'):
        overrides[name] = {'contents': 'read'}
    for name, job in jobs.items():
        secrets = set(re.findall(r'secrets\.([A-Z_]+)', job))
        expected = {'sign': {'RP_PKG_SIGNING_KEY'}, 'publish': {'RP_DISTRIBUTION_APP_PRIVATE_KEY'}}
        assert secrets == expected.get(name, set()), name
        assert_permissions(job, overrides.get(name), indent=4)
    for name in ('sign', 'publish', 'verify', 'verify-published', 'source-release',
                 'verify-development', 'publish-development'):
        mode = 'development' if name.endswith('-development') else 'production'
        assert f"    if: needs.select.outputs.mode == '{mode}'\n" in jobs[name]
    assert 'needs: [select, profile, sign, verify]' in jobs['publish']


def test_signer_binds_the_control_commit_and_trusted_build_profiles():
    workflow = workflow_text()
    signer = job_text(workflow, 'sign')
    assert 'ref: ${{ needs.profile.outputs.control_commit }}' in signer
    assert "--control-commit '${{ needs.profile.outputs.control_commit }}'" in signer
    for command in ('validate-bind-provenance', 'validate-build-metadata', 'reuse-snapshot', 'stage-channel'):
        matches = re.findall(r'release_channel\.py ' + command + r' ([^\n]+)', signer.replace('\\\n', ''))
        arguments = shlex.split(matches[0])
        assert arguments[arguments.index('--target-pkg-metadata') + 1] == '.resolver-plugins/target-pkg.json'
        if command == 'validate-bind-provenance':
            assert arguments[arguments.index('--profile') + 1] == '.resolver-plugins/bind920.json'
            assert arguments[arguments.index('--freebsd-release') + 1] == '${{ needs.profile.outputs.freebsd_release }}'
            assert arguments[arguments.index('--series') + 1] == '$SERIES'
        if command == 'validate-build-metadata':
            assert arguments[arguments.index('--upstream') + 1] == '$output/trusted-upstream.json'


def test_publisher_mints_a_repository_scoped_github_app_token():
    workflow = workflow_text()
    publisher = job_text(workflow, 'publish')
    assert 'owner: resolver-plugins' in publisher
    assert 'repositories: repository' in publisher
    assert 'permission-contents: write' in publisher
    assert 'RP_DISTRIBUTION_REPOSITORY_TOKEN' not in workflow


def test_production_preflights_pages_then_publishes_the_abi_static_channel():
    workflow = workflow_text()
    publisher = job_text(workflow, 'publish')

    assert 'https://resolver-plugins.github.io/repository/pages-health' in publisher
    assert publisher.index('publish-channels') < publisher.index('package_catalogue.py publish')


def test_publication_waits_for_current_and_snapshot_installability_in_freebsd():
    workflow = workflow_text()
    verifier = job_text(workflow, 'verify')
    assert '"$root"/current/bind-tools-*.pkg' in verifier
    assert '"$root"/current/bind920-*.pkg' in verifier
    assert '"$root"/current/os-bind-rp-*.pkg' in verifier
    assert '"$root"/current/*.pkg' not in verifier
    assert 'url: "file://$PWD/$root/snapshot"' in verifier
    assert '/usr/local/sbin/pkg-static install -f -y -r resolver-plugins-rollback os-bind-rp' in verifier


def test_published_channel_is_installed_from_github_in_freebsd():
    workflow = workflow_text()
    verifier = job_text(workflow, 'verify-published')
    assert 'needs: [select, profile, publish]' in verifier
    assert 'repository_url="https://resolver-plugins.github.io/repository/pkg/\\${ABI}/$series/latest"' in verifier
    assert 'verify-abi-endpoint --url "$channel_url" --expected-channel "$root"' in verifier
    assert '[ "$channel_identities" = "$expected_identities" ]' in verifier
    assert verifier.index('pkg install -y -r OPNsense opnsense os-bind') < verifier.index(
        'RP_PKG_STATIC_COMMAND="$shim/observe-pkg-static" scripts/install-os-bind-rp.sh'
    )
    assert '[ "$channel_identity" = "$expected_identity" ]' in verifier
    assert '[ "$installed_identity" = "$channel_identity" ]' in verifier
    assert verifier.index('verify-abi-endpoint') < verifier.index('pkg update -f -r resolver-plugins')
    assert 'test "$(stat -f %Lp "$state_directory")" = 700' in verifier
    assert '[ "$(sha256 -q "$root/$identity.pkg")" = "$digest" ]' in verifier


def test_development_release_installs_from_a_temporary_freebsd_repository():
    workflow = workflow_text()
    verifier = job_text(workflow, 'verify-development')
    publisher = job_text(workflow, 'publish-development')
    assert 'needs: [select, profile, build]' in verifier
    assert 'needs: [select, build, verify-development]' in publisher
    assert 'pull_number: ${{ steps.select.outputs.pull_number }}' in workflow
    assert 'PULL_NUMBER: ${{ needs.select.outputs.pull_number }}' in publisher
    assert publisher.count(
        'gh api "repos/$GITHUB_REPOSITORY/pulls/$PULL_NUMBER" --jq .state'
    ) == 2
    assert publisher.count(
        'release_channel.py cleanup-tag --repository "$GITHUB_REPOSITORY" --tag "$TAG"'
    ) == 2
    assert publisher.index('pr_state=$(gh api') < publisher.index('gh release create "$TAG"')
    assert publisher.rindex('pr_state=$(gh api') > publisher.index('gh release upload "$TAG"')


def test_source_release_contains_only_plugin_and_build_metadata():
    workflow = workflow_text()
    source_release = workflow.split('  source-release:', 1)[1]
    assert 'needs: [select, profile, verify-published]' in source_release
    assert 'publish-immutable --repository "$GITHUB_REPOSITORY"' in source_release
    assert 'set -- "$output"/os-bind-rp-*.pkg' in source_release
    assert 'cp "$1" "$output/build-metadata.txt" "$source_output/"' in source_release
    assert 'bind920-*.pkg' not in source_release


@pytest.mark.parametrize('job', ['verify-development', 'verify', 'verify-published'])
def test_freebsd_install_gates_pin_verify_and_replace_official_packages(job):
    verifier = job_text(workflow_text(), job)
    official = 'pkg install -y -r OPNsense opnsense os-bind'
    checksums = 'package_checksums.py'
    runtime = '.github/ci/bind/verify-bind-runtime.sh'
    for required in (
        'target_pkg.py verify', '--pkg-command /usr/local/sbin/pkg-static',
        "[ -z \"$(pkg query -e '%n = os-bind' '%n'",
        "pkg query -e \"%n = $package\" '%Fp|%Fs'", '$2 == "(null)"',
    ):
        assert required in verifier
    assert verifier.index('.github/ci/shared/setup-opnsense-repository.sh') < verifier.index(
        'target_pkg.py install'
    ) < verifier.index(official) < verifier.index(checksums)
    assert verifier.index('pkg check -s bind-tools bind920 os-bind-rp') < verifier.index(runtime)
