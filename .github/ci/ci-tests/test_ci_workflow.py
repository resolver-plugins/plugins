from common_imports import *

from workflow_fixtures import (WORKFLOWS, assert_permissions, assert_checkout_credentials,
                               assert_action_release_tags, job_text, workflow_jobs, workflow_steps,
                               step_run, trigger_paths)


def test_pr_workflow_runs_the_full_suite_without_publication_authority():
    workflow = (WORKFLOWS / 'ci-tests.yml').read_text()
    header = workflow.split('jobs:\n', 1)[0]
    assert re.search(r'^  pull_request:$', header, re.MULTILINE)
    assert not re.search(r'^  pull_request_target:', header, re.MULTILINE)
    patterns = trigger_paths(workflow, 'pull_request')
    for path in ('.github/ci/example.py', '.github/workflows/example.yml',
                 'dns/bind/Makefile', 'net/dhcp-interface-ha/Makefile', 'Mk/plugins.mk',
                 'Scripts/version.sh', 'Templates/plugin.in', 'scripts/install-os-bind-rp.sh',
                 'scripts/install-os-bind-rp-prerequisites.py',
                 'docs/package-repository/resolver-plugins.pub'):
        selected = False
        for pattern in patterns:
            if fnmatchcase(path, pattern.removeprefix('!')):
                selected = not pattern.startswith('!')
        assert selected, path
    assert_permissions(workflow, {'contents': 'read'})
    expressions = '\n'.join(re.findall(r'\$\{\{(.*?)\}\}', workflow, re.DOTALL))
    assert not re.search(r'\bsecrets\b', expressions)
    assert not re.search(r'^\s+secrets:', workflow, re.MULTILINE)
    assert_action_release_tags(workflow)
    for job in workflow_jobs(workflow).values():
        assert_permissions(job, None, indent=4)
        if 'uses: actions/checkout@' in job:
            assert_checkout_credentials(job)
    job = job_text(workflow, 'test')
    suites = [step for step in workflow_steps(job)
              if any((arguments := shlex.split(line, comments=True)) and arguments[0] in ('python', 'python3')
                     and arguments[1:3] == ['-m', 'pytest'] and '.github/ci/ci-tests' in arguments
                     for line in step_run(step).splitlines())]
    assert len(suites) == 1
    for scope in (job.split('    steps:\n', 1)[0], suites[0]):
        for field, accepted in [('if', ('true', 'success()')), ('continue-on-error', ('false',))]:
            declaration = re.search(r'^\s+' + field + r': ([^\n]+)$', scope, re.MULTILINE)
            assert declaration is None or declaration[1] in accepted, field
