import pathlib
import re


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[3]
WORKFLOW = REPOSITORY_ROOT / '.github/workflows/proof-build.yml'
PINNED_ACTION = re.compile(r'^[^@\s]+@[0-9a-f]{40}$')


def workflow_text() -> str:
    assert WORKFLOW.is_file(), 'generic release artifact workflow is missing'
    return WORKFLOW.read_text(encoding='utf-8')


def action_references(workflow: str) -> list[str]:
    return re.findall(r'^\s+(?:-\s+)?uses:\s+([^\s#]+)', workflow, re.MULTILINE)


def test_workflow_builds_release_branch_pushes_and_manual_dispatches():
    workflow = workflow_text()

    assert re.search(r'^on:\s*$', workflow, re.MULTILINE)
    assert re.search(r'^\s{2}push:\s*$', workflow, re.MULTILINE)
    assert re.search(r'^\s{6}-\s+["\']?release/bind-rp/\*\*["\']?\s*$', workflow, re.MULTILINE)
    assert re.search(r'^\s{2}workflow_dispatch:\s*$', workflow, re.MULTILINE)
    assert 'pull_request:' not in workflow
    assert 'schedule:' not in workflow


def test_workflow_reads_and_validates_branch_metadata_before_building():
    workflow = workflow_text()
    metadata_index = workflow.index('.resolver-plugins/upstream.json')
    vm_index = workflow.index('vmactions/freebsd-vm@')

    assert metadata_index < vm_index
    assert re.search(r'json\.loads?\(', workflow)
    assert "metadata['series']" in workflow
    assert "'core_commit'" in workflow
    assert "metadata['freebsd_release']" in workflow
    assert 'release/bind-rp/' in workflow
    assert 'needs.profile.outputs.series' in workflow
    assert 'needs.profile.outputs.freebsd_release' in workflow
    assert 'release: ${{ needs.profile.outputs.freebsd_release }}' in workflow
    assert 'RP_UPSTREAM_METADATA=.resolver-plugins/upstream.json' in workflow
    assert 'tools/ci/build-os-bind-rp.sh "$series" "artifacts/$series"' in workflow


def test_workflow_has_no_hardcoded_series_or_freebsd_profile_list():
    workflow = workflow_text()

    for hardcoded_value in ('26.1', '26.7', '14.3', '15.1'):
        assert hardcoded_value not in workflow


def test_workflow_uses_only_sha_pinned_actions_and_expiring_artifacts():
    workflow = workflow_text()
    references = action_references(workflow)

    assert workflow.count('persist-credentials: false') == 2
    assert references
    assert all(PINNED_ACTION.fullmatch(reference) for reference in references)
    assert 'actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803' in references
    assert 'vmactions/freebsd-vm@77ed28d336d03fe19a3f4f7266c1d2c4714dd79d' in references
    assert 'actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02' in references
    assert 'retention-days: 7' in workflow
    assert 'name: os-bind-rp-${{ needs.profile.outputs.series }}' in workflow
    assert 'path: artifacts/${{ needs.profile.outputs.series }}' in workflow


def test_workflow_has_no_publication_or_secret_bearing_steps():
    workflow = workflow_text()
    lowered = workflow.lower()

    assert 'contents: read' in workflow
    assert 'actions: read' in workflow
    assert workflow.count('permissions:') == 1
    assert 'secrets.' not in workflow
    assert not re.search(r'^\s*environment:', workflow, re.MULTILINE)
    for forbidden in (
        'gh release', 'create-release', 'pages:', 'id-token:', 'packages:',
        'pkg repo', 'docker push', 'npm publish', 'twine upload',
    ):
        assert forbidden not in lowered
