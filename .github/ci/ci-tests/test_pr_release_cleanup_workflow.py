from common_imports import *

from workflow_fixtures import assert_permissions, assert_pinned_actions, assert_checkout_credentials, job_text


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[3]
WORKFLOW = REPOSITORY_ROOT / ".github/workflows/pr-release-cleanup.yml"


def workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_pull_request_release_cleanup_runs_on_every_close():
    workflow = workflow_text()
    assert "pull_request_target:" in workflow
    assert "types: [closed]" in workflow
    assert "if: github.event.pull_request.merged" not in workflow
    assert "PULL_NUMBER: ${{ github.event.pull_request.number }}" in workflow
    assert "cleanup-pull-request" in workflow


def test_pull_request_release_cleanup_uses_only_trusted_code():
    workflow = workflow_text()
    assert_pinned_actions(workflow)
    assert "ref: ${{ github.workflow_sha }}" in workflow
    assert_checkout_credentials(job_text(workflow, "cleanup"))
    for untrusted_ref in (
        "pull_request.head",
        "github.head_ref",
        "refs/pull/",
    ):
        assert untrusted_ref not in workflow
    cleanup = job_text(workflow, 'cleanup')
    assert_permissions(workflow, {})
    assert_permissions(cleanup, {'contents': 'write'}, indent=4)
