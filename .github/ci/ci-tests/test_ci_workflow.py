"""Protect the local-only regression-suite execution policy."""
from common_imports import *

from workflow_fixtures import WORKFLOWS, workflow_jobs, workflow_steps, step_run


def test_hosted_workflows_do_not_run_regression_suites_or_call_retired_test_workflows():
    retired = {'ci-tests.yml', 'bind-tests.yml', 'ha-dhcp-interface-tests.yml', 'wan-ha-dhcp-tests.yml'}
    assert not retired & {path.name for path in WORKFLOWS.glob('*.y*ml')}
    for path in WORKFLOWS.glob('*.y*ml'):
        workflow = path.read_text()
        assert not any('/' + name in workflow for name in retired), path.name
        for job in workflow_jobs(workflow).values():
            for step in workflow_steps(job):
                command = step_run(step)
                assert not re.search(r'\b(?:pytest|unittest|shellcheck)\b|ci-tests/test_|run-local-tests|(?:dns/bind|net/dhcp-interface-ha)/tests|\bphp -l\b', command), (path.name, command)
