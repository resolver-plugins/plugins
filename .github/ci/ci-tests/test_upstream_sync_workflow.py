from common_imports import *

from workflow_fixtures import (action_references, assert_checkout_credentials,
                               assert_permissions, assert_pinned_actions,
                               job_text, workflow_jobs)


REPOSITORY_ROOT = pathlib.Path(
    os.environ.get('REPOSITORY_ROOT', pathlib.Path(__file__).resolve().parents[3])
)
WORKFLOW = REPOSITORY_ROOT / '.github/workflows/upstream-sync.yml'


def workflow_text() -> str:
    return WORKFLOW.read_text(encoding='utf-8')


def test_workflow_runs_daily_and_manually_with_exact_permissions():
    workflow = workflow_text()
    cron_expressions = re.findall(
        r'^\s*-\s+cron:\s*["\']([^"\']+)["\']\s*$', workflow, re.MULTILINE
    )

    assert re.search(r'^\s{2}schedule:\s*$', workflow, re.MULTILINE)
    assert re.search(r'^\s{2}workflow_dispatch:\s*$', workflow, re.MULTILINE)
    assert any(expression.split()[2:] == ['*', '*', '*'] for expression in cron_expressions)
    assert_permissions(workflow, {'contents': 'write', 'pull-requests': 'write'})
    jobs = workflow_jobs(workflow)
    for name, job in jobs.items():
        assert_permissions(job, {'contents': 'read'} if name == 'test' else None, indent=4)
        assert "    if: github.ref == 'refs/heads/master'\n" in job
    assert '    needs: test\n' in jobs['reconcile']


def test_workflow_fetches_control_inputs_and_plans_before_apply():
    workflow = workflow_text()
    assert 'refs/heads/release/bind-rp/*:refs/heads/release/bind-rp/*' in workflow
    assert 'https://github.com/opnsense/plugins.git' in workflow
    assert 'refs/heads/stable/*:refs/remotes/upstream/stable/*' in workflow
    assert 'https://github.com/opnsense/tools.git' in workflow
    assert '--tools-repository "$RUNNER_TEMP/opnsense-tools"' in workflow
    assert 'opnsense/changelog' not in workflow
    assert '--release-notes-directory' not in workflow
    assert "'tools_tag'," in workflow


def test_workflow_resolves_and_hashes_immutable_core_archive_before_apply():
    workflow = workflow_text()
    resolve_index = workflow.index('git ls-remote https://github.com/opnsense/core.git')
    download_index = workflow.index('https://github.com/opnsense/core/archive/$core_commit.tar.gz')
    hash_index = workflow.index('sha256sum')
    apply_index = workflow.index('.github/ci/sync_upstream.py apply')

    assert 'refs/heads/stable/$series' in workflow
    assert 'curl --fail --location' in workflow
    assert resolve_index < download_index < hash_index < apply_index
    assert '--core-commit "$core_commit"' in workflow
    assert '--core-archive-url "$core_archive_url"' in workflow
    assert '--core-archive-sha256 "$core_archive_sha256"' in workflow


def test_workflow_uses_api_only_credentials_for_recovery_and_publication():
    workflow = workflow_text()
    for job in workflow_jobs(workflow).values():
        assert_checkout_credentials(job)
    assert 'git push' not in workflow
    operations = []
    for step in re.split(r'^      - ', workflow, flags=re.MULTILINE):
        operation = re.findall(r'python3 \.github/ci/publish_upstream\.py (recover|publish)\b', step)
        operations.extend(operation)
        for binding, value in (('GH_TOKEN', 'github.token'), ('RP_SYNC_REVIEWER', 'vars.RP_SYNC_REVIEWER')):
            expected = f'${{{{ {value} }}}}'
            assert re.findall(r'^          ' + binding + r': (.+)$', step, re.MULTILINE) == (
                [expected] if operation else []
            )
            assert step.count(expected) == bool(operation)
        if operation:
            assert '--reviewer "$RP_SYNC_REVIEWER"' in step
    assert sorted(operations) == ['publish', 'recover']


def test_workflow_recovers_partial_review_state_before_planning_and_uses_api_publisher():
    workflow = workflow_text()
    recover_index = workflow.index('.github/ci/publish_upstream.py recover')
    plan_index = workflow.index('.github/ci/sync_upstream.py plan')
    apply_index = workflow.index('.github/ci/sync_upstream.py apply')
    publish_index = workflow.index('.github/ci/publish_upstream.py publish')

    assert recover_index < plan_index < apply_index < publish_index
    assert "steps.recovery.outputs.handled != 'true'" in workflow


def test_bootstrap_build_uses_the_planner_profile_and_expires():
    workflow = workflow_text()
    bootstrap = workflow.split('Build bootstrap in planner-selected FreeBSD release', 1)[1].split(
        'Upload bootstrap artifact', 1
    )[0]

    assert "steps.plan.outputs.action == 'bootstrap-build'" in workflow
    assert 'release: ${{ steps.plan.outputs.freebsd_release }}' in workflow
    assert 'set -eu' in bootstrap
    assert 'export IGNORE_OSVERSION=yes' in bootstrap
    assert 'pkg update -f' in bootstrap
    assert bootstrap.index('pkg install -y python3') < bootstrap.index('.github/ci/bind/build-bind920.sh')
    assert 'output="artifacts/$series"' in bootstrap
    assert 'source_commit="$(git rev-parse HEAD)"' in bootstrap
    assert 'RP_UPSTREAM_METADATA=.resolver-plugins/upstream.json' in bootstrap
    assert bootstrap.count('SOURCE_COMMIT="$source_commit"') == 2
    bind_index = bootstrap.index('.github/ci/bind/build-bind920.sh "$series" "$output"')
    plugin_index = bootstrap.index('.github/ci/bind/build-os-bind-rp.sh "$series" "$output"')
    assert bind_index < plugin_index
    assert 'actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02' in workflow
    assert 'retention-days: 7' in workflow


def test_workflow_pins_actions_and_has_no_publication_authority_or_commands():
    workflow = workflow_text()
    references = action_references(workflow)
    lowered = workflow.lower()

    assert_pinned_actions(workflow)
    assert any(ref.startswith('actions/checkout@') for ref in references)
    assert any(ref.startswith('vmactions/freebsd-vm@') for ref in references)
    assert 'secrets.' not in workflow
    assert not re.search(r'^\s*environment:', workflow, re.MULTILINE)
    for forbidden in (
        'gh release', 'create-release', 'pages:', 'id-token:', 'packages:',
        'pkg repo', 'docker push', 'npm publish', 'twine upload',
    ):
        assert forbidden not in lowered
