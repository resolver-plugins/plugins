from common_imports import *

from workflow_fixtures import assert_permissions, assert_action_release_tags, assert_checkout_credentials, job_text, trigger_paths


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[3]
WORKFLOW = REPOSITORY_ROOT / ".github/workflows/pr-release-cleanup.yml"


def workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_pull_request_release_cleanup_runs_only_for_package_source_closes():
    workflow = workflow_text()
    assert "pull_request_target:" in workflow
    assert "types: [closed]" in workflow
    assert "if: github.event.pull_request.merged" not in workflow
    assert "PULL_NUMBER: ${{ github.event.pull_request.number }}" in workflow
    assert "cleanup-pull-request" in workflow
    patterns = trigger_paths(workflow, 'pull_request_target')
    for path, expected in [('dns/bind/Makefile', True), ('net/dhcp-interface-ha/Makefile', True),
                           ('packages/bind-tools/Makefile', True), ('packages/bind920/Makefile', True),
                           ('.github/ci/example.py', False), ('.github/workflows/example.yml', False),
                           ('docs/building.md', False), ('dns/bind/tests/test_watcher.py', False),
                           ('net/dhcp-interface-ha/tests/test_core.py', False)]:
        selected = False
        for pattern in patterns:
            if fnmatchcase(path, pattern.removeprefix('!')):
                selected = not pattern.startswith('!')
        assert selected is expected, path


def test_pull_request_release_cleanup_uses_only_trusted_code():
    workflow = workflow_text()
    assert_action_release_tags(workflow)
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
