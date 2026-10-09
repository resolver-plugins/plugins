from common_imports import *

from workflow_fixtures import assert_permissions, action_references, assert_pinned_actions, assert_checkout_credentials, job_text, workflow_jobs


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
    assert "- '.resolver-plugins/target-pkg.json'" in workflow
    assert "- '.resolver-plugins/target-pkg-content.json'" in workflow
    assert "- '.github/workflows/bind-tests.yml'" in workflow


def test_workflow_discovers_release_branches_and_runs_canonical_tests():
    workflow = workflow_text()

    assert "refs/heads/release/bind-rp/*" in workflow
    assert "fromJSON(needs.discover.outputs.series)" in workflow
    assert 'refs/heads/release/bind-rp/$SERIES' in workflow
    assert 'git checkout "$source_commit" -- .resolver-plugins/upstream.json dns/bind/Makefile dns/bind/src' in workflow
    assert 'git cat-file -e "$source_commit:dns/bind/$fragment"' in workflow
    assert 'git show "$source_commit:dns/bind/$fragment" > "dns/bind/$fragment"' in workflow
    assert 'python3 -m pytest -q dns/bind/tests' in workflow


def test_release_source_pull_requests_test_their_proposed_source():
    workflow = workflow_text()

    assert 'git checkout refs/remotes/origin/canonical-tests -- \\' in workflow
    assert '.github/ci/shared/metadata_profile.py' in workflow
    assert 'if [[ "$PR_BASE" != "release/bind-rp/$SERIES" ]]' in workflow
    assert 'pull_request_base:' in workflow
    assert 'pull_request_sha:' in workflow
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
    assert '.github/workflows/bind-tests.yml' in helper_job
    assert '.github/workflows/bind920-candidate.yml' in helper_job
    assert '.resolver-plugins/bind920.json' in helper_job


def test_workflow_requires_pkg_descr_for_publishable_bind_changes():
    workflow = workflow_text()
    changes_job = job_text(workflow, 'changes')

    assert 'CALLER_SHA: ${{ inputs.pull_request_sha }}' in changes_job
    assert 'if [ -n "$CALLER_SHA" ]; then' in changes_job
    assert 'refs/heads/$PR_BASE:refs/remotes/origin/pr-base' in changes_job
    assert 'refs/heads/master:refs/remotes/origin/control-plane' in changes_job
    assert 'check-bind-pkg-descr.sh' in changes_job


def test_workflow_has_read_only_permissions_and_pinned_actions():
    workflow = workflow_text()
    references = action_references(workflow)

    assert_permissions(workflow, {'contents': 'read'})
    assert 'secrets.' not in workflow
    assert_pinned_actions(workflow)
    for job in workflow_jobs(workflow).values():
        assert_permissions(job, None, indent=4)
        if 'uses: actions/checkout@' in job:
            assert_checkout_credentials(job)
