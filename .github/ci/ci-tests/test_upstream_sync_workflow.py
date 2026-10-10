from common_imports import *

from workflow_fixtures import (assert_checkout_credentials,
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


def test_workflow_uses_approved_upstream_sources_and_complete_stable_discovery():
    workflow = workflow_text()
    assert 'https://github.com/opnsense/plugins.git' in workflow
    assert 'refs/heads/stable/*:refs/remotes/upstream/stable/*' in workflow
    assert 'https://github.com/opnsense/tools.git' in workflow


def test_workflow_fetches_core_for_the_reviewed_series_with_checked_transport():
    workflow = workflow_text()

    assert 'refs/heads/stable/$series' in workflow
    assert 'curl --fail --location' in workflow


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
    assert sorted(operations) == ['publish', 'recover']


def test_workflow_recovers_partial_review_state_before_planning_and_uses_api_publisher():
    workflow = workflow_text()
    recover_index = workflow.index('.github/ci/publish_upstream.py recover')
    plan_index = workflow.index('.github/ci/sync_upstream.py plan')
    apply_index = workflow.index('.github/ci/sync_upstream.py apply')
    publish_index = workflow.index('.github/ci/publish_upstream.py publish')

    assert recover_index < plan_index < apply_index < publish_index
    assert "steps.recovery.outputs.handled != 'true'" in workflow


def test_workflow_pins_actions_and_has_no_publication_authority_or_commands():
    workflow = workflow_text()
    lowered = workflow.lower()

    assert_pinned_actions(workflow)
    assert 'secrets.' not in workflow
    assert not re.search(r'^\s*environment:', workflow, re.MULTILINE)
    for forbidden in (
        'gh release', 'create-release', 'pages:', 'id-token:', 'packages:',
        'pkg repo',
    ):
        assert forbidden not in lowered
