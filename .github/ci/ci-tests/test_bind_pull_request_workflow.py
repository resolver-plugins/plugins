from common_imports import *
from git_fixtures import *

from workflow_fixtures import assert_permissions, assert_action_release_tags, assert_checkout_credentials, job_text, workflow_jobs, workflow_steps, step_run


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[3]
WORKFLOW = REPOSITORY_ROOT / '.github/workflows/bind-tests.yml'


def workflow_text() -> str:
    return WORKFLOW.read_text(encoding='utf-8')


def test_workflow_runs_only_for_relevant_pull_request_changes():
    workflow = workflow_text()

    assert 'pull_request:' in workflow
    assert 'workflow_call:' in workflow
    assert 'pull_request_target:' not in workflow
    assert "- 'dns/bind/**'" in workflow
    assert "- '.github/ci/**'" in workflow
    assert "- '.github/workflows/bind-tests.yml'" in workflow


def test_workflow_discovers_release_branches_and_runs_canonical_tests(tmp_path):
    workflow = workflow_text()

    assert "refs/heads/release/bind-rp/*" in workflow
    assert "fromJSON(needs.discover.outputs.series)" in workflow
    assert 'refs/heads/release/bind-rp/$SERIES' in workflow
    assert 'git checkout "$source_commit" -- .resolver-plugins/upstream.json dns/bind/Makefile dns/bind/src' in workflow
    assert 'python3 -m pytest -q dns/bind/tests' in workflow

    origin = init_repository(tmp_path / 'origin')
    source = commit(origin, {'dns/bind/Makefile': 'PLUGIN_NAME= bind-rp\n'}, 'release source')
    for series in ('26.1', '26.7'):
        git(origin, 'branch', 'release/bind-rp/' + series, source)
    helpers = {path.relative_to(REPOSITORY_ROOT).as_posix(): path.read_text()
               for path in (REPOSITORY_ROOT / '.github/ci').rglob('*')
               if path.suffix in {'.py', '.sh'} and 'ci-tests' not in path.parts}
    commit(origin, helpers, 'master control helpers')
    checkout = tmp_path / 'checkout'
    git(tmp_path, 'clone', '--quiet', '--branch', 'release/bind-rp/26.1', str(origin), str(checkout))
    assert not (checkout / '.github/ci').exists()
    runner = tmp_path / 'runner'
    runner.mkdir()
    output = runner / 'outputs'
    output.write_text('previous=retained\n')
    step = next(step for step in workflow_steps(job_text(workflow, 'discover'))
                if 'name: Discover supported release branches' in step)
    result = subprocess.run(['bash', '-c', step_run(step)], cwd=checkout,
        env={**os.environ, 'PR_BASE': 'release/bind-rp/26.1', 'RUNNER_TEMP': str(runner),
             'GITHUB_OUTPUT': str(output)}, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert output.read_text() == 'previous=retained\nseries=["26.1", "26.7"]\n'


def test_release_source_pull_requests_test_their_proposed_source():
    workflow = workflow_text()

    assert 'if [[ "$PR_BASE" != "release/bind-rp/$SERIES" ]]' in workflow
    assert 'ref: ${{ inputs.pull_request_sha || github.sha }}' in workflow
    assert 'PR_BASE: ${{ inputs.pull_request_base || github.event.pull_request.base.ref }}' in workflow
    test_job = job_text(workflow, 'test')
    assert 'if [[ "$PR_BASE" == release/bind-rp/* ]]' in test_job
    assert 'refs/heads/master:refs/remotes/origin/canonical-tests' in test_job


def test_release_source_pull_requests_materialize_master_ci_helpers():
    workflow = workflow_text()
    helper_job = job_text(workflow, 'ci-helpers')

    assert 'PR_BASE: ${{ inputs.pull_request_base || github.event.pull_request.base.ref }}' in helper_job
    assert 'if [[ "$PR_BASE" == release/bind-rp/* ]]' in helper_job
    assert 'refs/heads/master:refs/remotes/origin/control-plane' in helper_job
    assert '.github/ci \\' in helper_job


def test_workflow_requires_pkg_descr_for_publishable_bind_changes():
    workflow = workflow_text()
    changes_job = job_text(workflow, 'changes')

    assert 'CALLER_SHA: ${{ inputs.pull_request_sha }}' in changes_job
    assert 'if [ -n "$CALLER_SHA" ]; then' in changes_job
    assert 'refs/heads/master:refs/remotes/origin/control-plane' in changes_job
    assert 'check-bind-pkg-descr.sh' in changes_job


def test_workflow_has_read_only_permissions_and_action_release_tags():
    workflow = workflow_text()

    assert_permissions(workflow, {'contents': 'read'})
    assert 'secrets.' not in workflow
    assert_action_release_tags(workflow)
    for job in workflow_jobs(workflow).values():
        assert_permissions(job, None, indent=4)
        if 'uses: actions/checkout@' in job:
            assert_checkout_credentials(job)
