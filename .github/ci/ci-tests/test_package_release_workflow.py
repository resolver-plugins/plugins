from common_imports import *

from workflow_fixtures import assert_permissions, action_references, assert_pinned_actions, assert_checkout_credentials, job_text, workflow_jobs

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
    assert 'gh api "repos/$GITHUB_REPOSITORY/pulls/$INPUT_PULL_NUMBER" --jq .base.ref' in workflow
    assert 'if [[ "$pr_base" == master ]]' in workflow
    assert 'elif [[ "$pr_base" == "release/bind-rp/$series" ]]' in workflow
    assert 'git fetch --no-tags origin "$SOURCE_REF:refs/remotes/origin/package-source"' in workflow
    assert 'source_commit=$(git rev-parse refs/remotes/origin/package-source)' in workflow
    assert 'git checkout "$SOURCE_COMMIT" -- .resolver-plugins/upstream.json Mk dns/bind' in workflow


def test_merged_release_source_pr_publishes_its_exact_merge_commit():
    workflow = workflow_text()
    trigger = workflow.split('  pull_request_target:', 1)[1].split('  workflow_dispatch:', 1)[0]
    select = job_text(workflow, 'select')

    assert "pull_request_target:\n    types: [closed]\n    branches:\n      - 'release/bind-rp/**'" in workflow
    for path in ("'.resolver-plugins/**'", "'dns/bind/**'", "'Mk/**'"):
        assert path in trigger
    assert "if: github.event_name != 'pull_request_target' || github.event.pull_request.merged == true" in select
    assert '[[ "$PR_MERGED" == true ]]' in select
    assert '[[ "$PR_BASE_REF" =~ ^release/bind-rp/([0-9]+\\.[0-9]+)$ ]]' in select
    assert 'source_ref="$PR_MERGE_COMMIT"' in select
    assert 'control_ref="$GITHUB_WORKFLOW_SHA"' in select
    assert 'github.event.pull_request.head.sha' not in workflow


def test_package_affecting_master_pushes_publish_the_newest_release_series():
    workflow = workflow_text()

    assert 'push:\n    branches: [master]' in workflow
    select = job_text(workflow, 'select')
    assert 'EVENT_NAME: ${{ github.event_name }}' in select
    assert 'git/matching-refs/heads/release/bind-rp/' in select
    assert "sed -nE 's#^refs/heads/release/bind-rp/([0-9]+\\.[0-9]+)$#\\1#p'" in select
    assert 'sort -V' in select
    assert 'mode=production' in select
    assert "'26.7'" not in select


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
    assert 'usage:' in result.stdout


def test_merging_a_repack_recovery_rebuilds_its_exact_series():
    select = workflow_text().split('  select:', 1)[1].split('  profile:', 1)[0]

    assert 'ref: ${{ github.workflow_sha }}' in select
    assert 'fetch-depth: 0' in select
    assert 'BEFORE_SHA: ${{ github.event.before }}' in select
    assert 'changed=$(git diff --name-only "$BEFORE_SHA" "$GITHUB_SHA")' in select
    assert '[ "$changed" = .resolver-plugins/target-pkg.json ]' in select
    assert 'git show "$BEFORE_SHA:.resolver-plugins/target-pkg.json"' in select
    assert 'target_pkg.py changed-series "$before" "$after"' in select
    assert 'series=$recovered_series' in select


def test_production_runs_only_from_the_master_control_plane():
    workflow = workflow_text()
    select = job_text(workflow, 'select')
    profile = job_text(workflow, 'profile')
    test = job_text(workflow, 'test')
    bind = job_text(workflow, 'bind')
    build = job_text(workflow, 'build')
    assert 'GITHUB_REF: ${{ github.ref }}' in select
    assert '[[ "$GITHUB_REF" == refs/heads/master ]]' in select
    assert 'control_ref=$GITHUB_SHA' in select
    assert 'ref: ${{ needs.select.outputs.control_ref }}' in profile
    for job in (test, bind, build):
        assert 'ref: ${{ needs.profile.outputs.control_commit }}' in job
    assert '  group: package-release\n' in workflow
    assert 'cancel-in-progress: false' in workflow


def test_workflow_validates_metadata_before_selecting_the_freebsd_vm():
    workflow = workflow_text()
    validator_index = workflow.index('python3 .github/ci/shared/metadata_profile.py')
    vm_index = workflow.index('vmactions/freebsd-vm@')
    assert validator_index < vm_index
    assert 'release: ${{ needs.profile.outputs.freebsd_release }}' in workflow
    assert 'RP_UPSTREAM_METADATA=.resolver-plugins/upstream.json' in workflow
    assert '.github/ci/bind/build-os-bind-rp.sh "$series" "$output"' in workflow


def test_workflow_materializes_the_distribution_bind_pair_before_building_the_plugin():
    workflow = workflow_text()
    assert 'RP_BIND920_CHANNEL_URL: https://github.com/resolver-plugins/repository/releases/download/pkg-${{ needs.select.outputs.series }}' in workflow
    assert 'needs: [select, profile, test, bind]' in workflow
    build = job_text(workflow, 'build')
    assert 'RP_BIND920_FALLBACK=yes' in build
    assert 'pkg add "$output"/bind-tools-*.pkg "$output"/bind920-*.pkg' in build
    assert 'pkg query -F "$package" \'%dn\'' in build
    assert build.index('.github/ci/shared/setup-opnsense-repository.sh') < build.index(
        'pkg add "$output"/bind-tools-*.pkg'
    )


def test_failed_production_bind_job_can_only_propose_a_content_identical_pkg_repack():
    workflow = workflow_text()
    recovery = job_text(workflow, 'recover-target-pkg')
    validator = recovery
    proposer = job_text(workflow, 'propose-target-pkg')

    assert "needs.bind.result == 'failure'" in validator
    assert "needs.select.outputs.mode == 'production'" in validator
    assert '.resolver-plugins/target-pkg-content.json' in validator
    assert 'target_pkg.py refresh' in validator
    assert "needs.recover-target-pkg.result == 'success'" in proposer
    assert '[ "$changed" = .resolver-plugins/target-pkg.json ]' in proposer
    assert 'gh pr create' in proposer
    assert 'gh pr merge' not in validator + proposer


def test_workflow_uses_sha_pinned_actions_and_nonpersistent_checkout_credentials():
    workflow = workflow_text()
    references = action_references(workflow)
    assert_pinned_actions(workflow)
    for name, job in workflow_jobs(workflow).items():
        if 'uses: actions/checkout@' in job:
            assert_checkout_credentials(job, persistent=name == 'propose-target-pkg')
    assert 'actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803' in references
    assert 'vmactions/freebsd-vm@77ed28d336d03fe19a3f4f7266c1d2c4714dd79d' in references


def test_workflow_provisions_the_pinned_python_test_runtime():
    workflow = workflow_text()
    test_job = job_text(workflow, 'test')

    assert re.search(r'actions/setup-python@[0-9a-f]{40}', test_job)
    assert "python-version: '3.12.13'" in test_job
    assert "python -m pip install --disable-pip-version-check 'pytest==8.3.5'" in test_job


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
    assert 'needs: [select, profile, build]' in jobs['sign']
    assert 'needs: [select, profile, sign, verify]' in jobs['publish']
    assert 'needs: [select, profile, sign]' in jobs['verify']
    assert 'RP_PKG_SIGNING_KEY: ${{ secrets.RP_PKG_SIGNING_KEY }}' in jobs['sign']
    assert 'python3 .github/ci/shared/release_channel.py stage-channel' in jobs['sign']
    assert 'python3 .github/ci/shared/release_channel.py publish-channels' in jobs['publish']


def test_signer_uses_master_control_plane_and_self_contained_channel_layout():
    workflow = workflow_text()
    signer = job_text(workflow, 'sign')
    assert 'control_commit: ${{ steps.profile.outputs.control_commit }}' in workflow
    assert 'control_commit=$(git rev-parse HEAD)' in workflow
    assert 'ref: ${{ needs.profile.outputs.control_commit }}' in signer
    assert "--control-commit '${{ needs.profile.outputs.control_commit }}'" in signer
    assert 'cp -R "$output/repository/current" "$output/repository/snapshot"' in signer
    assert 'cmp -s docs/package-repository/resolver-plugins.pub "$output/resolver-plugins.pub"' in signer
    for command in ('validate-bind-provenance', 'validate-build-metadata', 'reuse-snapshot', 'stage-channel'):
        matches = re.findall(r'release_channel\.py ' + command + r' ([^\n]+)', signer.replace('\\\n', ''))
        assert len(matches) == 1, command
        arguments = shlex.split(matches[0])
        assert arguments[arguments.index('--target-pkg-metadata') + 1] == '.resolver-plugins/target-pkg.json'
        if command == 'validate-bind-provenance':
            assert arguments[arguments.index('--profile') + 1] == '.resolver-plugins/bind920.json'
            assert arguments[arguments.index('--freebsd-release') + 1] == '${{ needs.profile.outputs.freebsd_release }}'
            assert arguments[arguments.index('--series') + 1] == '$SERIES'
        if command == 'validate-build-metadata':
            assert arguments[arguments.index('--upstream') + 1] == '$output/trusted-upstream.json'
    assert 'id: reuse-snapshot' in signer
    assert 'reuse-snapshot --repository resolver-plugins/repository' in signer
    assert '--provenance "$output/bind920-provenance.json"' in signer
    reuse = signer.split('- name: Reuse existing immutable snapshot', 1)[1].split(
        '- name: Sign package repository', 1
    )[0]
    reuse_command = 'release_channel.py reuse-snapshot'
    assert reuse.index('validate-bind-provenance') < reuse.index(reuse_command)
    assert reuse.index('validate-build-metadata') < reuse.index(reuse_command)
    assert 'if [ "$REUSED" != true ]; then' in signer
    assert "REUSED: ${{ steps.reuse-snapshot.outputs.reused }}" in signer
    assert signer.index('            fi\n            python3 .github/ci/shared/package_catalogue.py stage') > signer.index('stage-channel')
    assert '--public-key docs/package-repository/resolver-plugins.pub' in signer
    assert 'repository/bind920' not in signer


def test_publisher_mints_a_repository_scoped_github_app_token():
    workflow = workflow_text()
    publisher = job_text(workflow, 'publish')
    assert 'fetch-depth: 0' in publisher
    assert 'actions/create-github-app-token@fee1f7d63c2ff003460e3d139729b119787bc349' in publisher
    assert 'app-id: ${{ vars.RP_DISTRIBUTION_APP_ID }}' in publisher
    assert 'private-key: ${{ secrets.RP_DISTRIBUTION_APP_PRIVATE_KEY }}' in publisher
    assert 'owner: resolver-plugins' in publisher
    assert 'repositories: repository' in publisher
    assert 'permission-contents: write' in publisher
    assert 'RP_DISTRIBUTION_REPOSITORY_TOKEN' not in workflow
    assert publisher.index('prune-snapshots') < publisher.index('mark-latest-package-channel')
    assert '--recovery "$RUNNER_TEMP/recovery"' in workflow


def test_production_preflights_pages_then_publishes_the_abi_static_channel():
    workflow = workflow_text()
    publisher = job_text(workflow, 'publish')

    assert 'https://resolver-plugins.github.io/repository/pages-health' in publisher
    assert '[ "$attempt" -ge 10 ]' in publisher
    assert 'sleep 15' in publisher
    assert 'package_catalogue.py publish --repository resolver-plugins/repository --directory "$root/combined"' in publisher
    assert publisher.index('publish-channels') < publisher.index('package_catalogue.py publish')
    assert 'GH_TOKEN: ${{ steps.distribution-token.outputs.token }}' in publisher


def test_public_verification_compares_all_static_bytes_before_installation():
    workflow = workflow_text()
    verifier = job_text(workflow, 'verify-published')

    assert 'https://resolver-plugins.github.io/repository/pkg/\\${ABI}/$series/latest' in verifier
    assert verifier.index('verify-abi-endpoint') < verifier.index(
        'pkg update -f -r resolver-plugins'
    )


def test_public_verification_uses_the_installed_freebsd_ca_bundle():
    workflow = workflow_text()
    verifier = job_text(workflow, 'verify-published')

    assert 'SSL_CERT_FILE=/usr/local/share/certs/ca-root-nss.crt' in verifier
    assert verifier.index('test -r "$SSL_CERT_FILE"') < verifier.index('verify-abi-endpoint')


def test_publication_waits_for_current_and_snapshot_installability_in_freebsd():
    workflow = workflow_text()
    publisher = job_text(workflow, 'publish')
    verifier = job_text(workflow, 'verify')
    assert '"$root"/current/bind-tools-*.pkg' in verifier
    assert '"$root"/current/bind920-*.pkg' in verifier
    assert '"$root"/current/os-bind-rp-*.pkg' in verifier
    assert '"$root"/current/*.pkg' not in verifier
    assert '/usr/local/sbin/pkg-static query -F "$package" \'%dn\'' in verifier
    assert verifier.index('.github/ci/shared/setup-opnsense-repository.sh') < verifier.index(
        'pkg install -y -r OPNsense opnsense os-bind'
    ) < verifier.index(
        '/usr/local/sbin/pkg-static install -y -r resolver-plugins bind-tools bind920 os-bind-rp'
    )
    assert 'url: "file://$PWD/$root/snapshot"' in verifier
    assert '/usr/local/sbin/pkg-static install -f -y -r resolver-plugins-rollback os-bind-rp' in verifier


def test_published_channel_is_installed_from_github_in_freebsd():
    workflow = workflow_text()
    verifier = job_text(workflow, 'verify-published')
    source_release = workflow.split('  source-release:', 1)[1]
    assert 'needs: [select, profile, publish]' in verifier
    assert 'name: os-bind-rp-production-repository-${{ needs.select.outputs.series }}' in verifier
    assert 'repository_url="https://resolver-plugins.github.io/repository/pkg/\\${ABI}/$series/latest"' in verifier
    assert '.github/ci/shared/setup-opnsense-repository.sh "$series"' in verifier
    assert '"$root"/bind-tools-*.pkg' in verifier
    assert '"$root"/bind920-*.pkg' in verifier
    assert '"$root"/os-bind-rp-*.pkg' in verifier
    assert '"$root"/*.pkg' not in verifier
    assert 'verify-abi-endpoint --url "$channel_url" --expected-channel "$root"' in verifier
    assert '/usr/local/sbin/pkg-static query -F "$archive" \'%n|%v|%o\'' in verifier
    assert '[ "$channel_identities" = "$expected_identities" ]' in verifier
    assert '[ "$attempt" -ge 20 ]' in verifier
    assert 'sleep 30' in verifier
    assert 'pkg rquery -r resolver-plugins -e "%n = $package" \'%dn\'' in verifier
    assert verifier.index('pkg install -y -r OPNsense opnsense os-bind') < verifier.index(
        'RP_PKG_STATIC_COMMAND=/usr/local/sbin/pkg-static scripts/install-os-bind-rp.sh'
    )
    assert 'dns/bind-tools' in verifier
    assert 'dns/bind920' in verifier
    assert 'opnsense/os-bind-rp' in verifier
    assert '[ "$channel_identity" = "$expected_identity" ]' in verifier
    assert '[ "$installed_identity" = "$channel_identity" ]' in verifier


def test_development_release_installs_from_a_temporary_freebsd_repository():
    workflow = workflow_text()
    verifier = job_text(workflow, 'verify-development')
    publisher = job_text(workflow, 'publish-development')
    assert 'needs: [select, profile, build]' in verifier
    assert '/usr/local/sbin/pkg-static repo "$output"' in verifier
    assert 'signature_type: "none"' in verifier
    assert '/usr/local/sbin/pkg-static update -r resolver-plugins-development' in verifier
    assert verifier.index('pkg install -y -r OPNsense opnsense os-bind') < verifier.index(
        '/usr/local/sbin/pkg-static install -y -r resolver-plugins-development bind-tools bind920 os-bind-rp'
    )
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
    assert 'name: os-bind-rp-production-repository-${{ needs.select.outputs.series }}' in source_release
    assert 'output="artifacts/$SERIES/repository/current"' in source_release
    assert 'source_output="$RUNNER_TEMP/source-release"' in source_release
    assert 'publish-immutable --repository "$GITHUB_REPOSITORY"' in source_release
    assert 'gh release view "$tag"' not in source_release
    assert 'gh release create "$tag"' not in source_release
    assert 'set -- "$output"/os-bind-rp-*.pkg' in source_release
    assert 'source-release-tag "$SERIES" "$version" --provenance "$output/bind920-provenance.json"' in source_release
    assert 'cp "$1" "$output/build-metadata.txt" "$source_output/"' in source_release
    assert 'bind920-*.pkg' not in source_release
    assert 'os-bind-rp-build-production-' not in source_release


def test_immutable_package_snapshot_is_scoped_to_the_bind_build():
    workflow = workflow_text()
    publisher = job_text(workflow, 'publish')

    assert 'snapshot-tag "$SERIES" "$version" --provenance "$root/current/bind920-provenance.json"' in publisher


@pytest.mark.parametrize('job,install,dependencies', [
    ('verify-development', '/usr/local/sbin/pkg-static install -y -r resolver-plugins-development bind-tools bind920 os-bind-rp', '$dependencies'),
    ('verify', '/usr/local/sbin/pkg-static install -y -r resolver-plugins bind-tools bind920 os-bind-rp', '$dependencies'),
    ('verify-published', 'RP_PKG_STATIC_COMMAND=/usr/local/sbin/pkg-static scripts/install-os-bind-rp.sh', '"$dependency"'),
])
def test_freebsd_install_gates_pin_verify_and_replace_official_packages(job, install, dependencies):
    verifier = job_text(workflow_text(), job)
    official = 'pkg install -y -r OPNsense opnsense os-bind'
    config = "printf '%s\\n' '<opnsense/>' > /conf/config.xml"
    checksums = 'package_checksums.py'
    runtime = '.github/ci/bind/verify-bind-runtime.sh'
    for required in (
        'target_pkg.py verify', '--pkg-command /usr/local/sbin/pkg-static',
        'install -d -m 0750 /conf', 'if [ ! -e /conf/config.xml ]; then',
        'chmod 0640 /conf/config.xml',
        "[ -z \"$(pkg query -e '%n = os-bind' '%n'",
        "pkg query -e \"%n = $package\" '%Fp|%Fs'", '$2 == "(null)"',
        'pkg install -y -r OPNsense ' + dependencies,
    ):
        assert required in verifier
    assert ' OR ' not in verifier
    assert verifier.index('target_pkg.py install') < verifier.index(official) < verifier.index(checksums)
    assert verifier.index(official) < verifier.index(config) < verifier.index(install)
    assert verifier.index('pkg check -s bind-tools bind920 os-bind-rp') < verifier.index(runtime)
