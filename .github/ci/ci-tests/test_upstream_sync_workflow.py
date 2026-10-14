from common_imports import *

from workflow_fixtures import (assert_checkout_credentials,
                               assert_permissions, assert_action_release_tags,
                               job_text, workflow_jobs, workflow_steps)


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
    assert_permissions(workflow, {'contents': 'read'})
    jobs = workflow_jobs(workflow)
    for name, job in jobs.items():
        assert_permissions(job, None, indent=4)
        assert "    if: github.ref == 'refs/heads/master'\n" in job
    assert set(jobs) == {'reconcile'}


def test_workflow_uses_approved_upstream_sources_and_complete_stable_discovery():
    workflow = workflow_text()
    assert 'https://github.com/opnsense/plugins.git' in workflow
    assert 'refs/heads/stable/*:refs/remotes/upstream/stable/*' in workflow
    assert 'https://github.com/opnsense/tools.git' in workflow
    assert 'https://github.com/opnsense/core.git' in workflow
    assert 'refs/heads/stable/$series' in workflow
    assert 'curl --fail --location' in workflow


def test_workflow_uses_api_only_credentials_for_recovery_and_publication():
    workflow = workflow_text()
    for job in workflow_jobs(workflow).values():
        assert_checkout_credentials(job)
    assert 'git push' not in workflow
    reconcile = job_text(workflow, 'reconcile')
    token_steps = [step for step in workflow_steps(reconcile)
                   if 'uses: actions/create-github-app-token@' in step]
    assert len(token_steps) == 1
    token_step = token_steps[0]
    assert re.findall(r'^        id: (.+)$', token_step, re.MULTILINE) == ['sync-app']
    assert dict(re.findall(r'^          ([a-z-]+): (.+)$', token_step, re.MULTILINE)) == {
        'client-id': '${{ vars.RP_SYNC_APP_CLIENT_ID }}',
        'private-key': '${{ secrets.RP_SYNC_APP_PRIVATE_KEY }}',
        'owner': '${{ github.repository_owner }}',
        'repositories': '${{ github.event.repository.name }}',
        'permission-contents': 'write',
        'permission-pull-requests': 'write',
        'permission-workflows': 'write',
    }
    assert reconcile.index('id: sync-app') < reconcile.index('publish_upstream.py recover')
    assert re.findall(r'\bsecrets\.([A-Za-z0-9_]+)', workflow) == ['RP_SYNC_APP_PRIVATE_KEY']
    operations = []
    for step in re.split(r'^      - ', workflow, flags=re.MULTILINE):
        operation = re.findall(r'python3 \.github/ci/publish_upstream\.py (recover|publish)\b', step)
        operations.extend(operation)
        for binding, value in (('GH_TOKEN', 'steps.sync-app.outputs.token'),
                               ('RP_SYNC_REVIEWER', 'vars.RP_SYNC_REVIEWER')):
            expected = f'${{{{ {value} }}}}'
            assert re.findall(r'^          ' + binding + r': (.+)$', step, re.MULTILINE) == (
                [expected] if operation else []
            )
            assert step.count(expected) == bool(operation)
    assert sorted(operations) == ['publish', 'recover']


def test_workflow_uses_one_frozen_discovery_and_sealed_plan_for_review_mutation():
    workflow = workflow_text()
    operations = [
        '.github/ci/publish_upstream.py recover',
        '.github/ci/sync_upstream.py discover',
        '.github/ci/sync_upstream.py seal-plan',
        '.github/ci/sync_upstream.py apply',
        '.github/ci/publish_upstream.py publish',
    ]
    assert [workflow.index(operation) for operation in operations] == sorted(
        workflow.index(operation) for operation in operations)
    assert workflow.count('.github/ci/sync_upstream.py discover') == 1
    assert '--control-commit "$(git rev-parse HEAD)"' in workflow
    assert '> "$RUNNER_TEMP/discovery.json"' in workflow
    assert '--discovery "$RUNNER_TEMP/discovery.json"' in workflow
    assert '--output "$RUNNER_TEMP/plan.json"' in workflow
    assert workflow.count('--plan "$RUNNER_TEMP/plan.json"') == 2
    for step in re.split(r'^      - ', workflow, flags=re.MULTILINE):
        if any(operation in step for operation in operations[2:]):
            assert "steps.recovery.outputs.handled != 'true'" in step
            assert "steps.plan.outputs.action != 'noop'" in step
        if any(operation in step for operation in (operations[0], operations[3], operations[4])):
            assert '--tools-repository "$RUNNER_TEMP/opnsense-tools"' in step


def test_workflow_uses_release_tags_and_has_no_package_publication_commands():
    workflow = workflow_text()
    lowered = workflow.lower()

    assert_action_release_tags(workflow)
    assert not re.search(r'^\s*environment:', workflow, re.MULTILINE)
    for forbidden in (
        'gh release', 'create-release', 'pages:', 'id-token:', 'packages:',
        'pkg repo',
    ):
        assert forbidden not in lowered
