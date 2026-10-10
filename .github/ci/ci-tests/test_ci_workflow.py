from common_imports import *

from workflow_fixtures import assert_permissions, WORKFLOWS, assert_checkout_credentials, assert_pinned_actions, job_text


def test_pr_workflow_runs_the_full_suite_without_publication_authority():
    workflow = (WORKFLOWS / 'ci-tests.yml').read_text()
    trigger, jobs = workflow.split('jobs:\n', 1)
    assert '  pull_request:\n' in trigger
    assert 'pull_request_target:' not in trigger
    for path in ('.github/ci/**', '.github/workflows/**', '.resolver-plugins/**',
                 'dns/bind/**', 'net/dhcp-interface-ha/**', 'Mk/**', 'Scripts/**',
                 'Templates/**', 'scripts/**', 'docs/package-repository/**'):
        assert f"      - '{path}'" in trigger
    assert_permissions(workflow, {'contents': 'read'})
    assert 'permissions:' not in jobs
    assert 'secrets.' not in workflow
    assert_pinned_actions(workflow)
    test = job_text(workflow, 'test')
    assert_checkout_credentials(test)
    assert not re.search(r'^\s+(?:if|continue-on-error):', test, re.MULTILINE)
    assert 'python -m pytest -q .github/ci/ci-tests' in test
