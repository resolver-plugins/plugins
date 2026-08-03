import pathlib
import re


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[3]
WORKFLOW = REPOSITORY_ROOT / '.github/workflows/upstream-sync.yml'
PINNED_ACTION = re.compile(r'^[^@\s]+@[0-9a-f]{40}$')


def workflow_text() -> str:
    assert WORKFLOW.is_file(), 'daily upstream synchronization workflow is missing'
    return WORKFLOW.read_text(encoding='utf-8')


def top_level_mapping(workflow: str, key: str) -> list[str]:
    lines = workflow.splitlines()
    start = lines.index(f'{key}:') + 1
    block = []
    for line in lines[start:]:
        if line and not line.startswith((' ', '\t')):
            break
        if line.strip() and not line.lstrip().startswith('#'):
            block.append(line.strip())
    return block


def action_references(workflow: str) -> list[str]:
    return re.findall(r'^\s+(?:-\s+)?uses:\s+([^\s#]+)', workflow, re.MULTILINE)


def case_arm(workflow: str, action: str) -> str:
    match = re.search(
        rf'^\s+{re.escape(action)}\)\s*$\n(?P<body>.*?)^\s+;;\s*$',
        workflow,
        re.MULTILINE | re.DOTALL,
    )
    assert match, f'missing case arm for {action}'
    return match.group('body')


def test_workflow_runs_daily_and_manually_with_exact_permissions():
    workflow = workflow_text()
    cron_expressions = re.findall(
        r'^\s*-\s+cron:\s*["\']([^"\']+)["\']\s*$', workflow, re.MULTILINE
    )

    assert re.search(r'^\s{2}schedule:\s*$', workflow, re.MULTILINE)
    assert re.search(r'^\s{2}workflow_dispatch:\s*$', workflow, re.MULTILINE)
    assert any(expression.split()[2:] == ['*', '*', '*'] for expression in cron_expressions)
    assert top_level_mapping(workflow, 'permissions') == [
        'contents: write',
        'pull-requests: write',
    ]
    assert workflow.count('permissions:') == 1


def test_workflow_fetches_control_inputs_and_plans_before_apply():
    workflow = workflow_text()
    plan_index = workflow.index('tools/ci/sync_upstream.py plan')
    apply_index = workflow.index('tools/ci/sync_upstream.py apply')

    assert 'refs/heads/release/bind-rp/*:refs/heads/release/bind-rp/*' in workflow
    assert 'https://github.com/opnsense/plugins.git' in workflow
    assert 'refs/heads/stable/*:refs/remotes/upstream/stable/*' in workflow
    assert '--release-notes-directory' in workflow
    assert plan_index < apply_index
    assert 'plan.json' in workflow


def test_workflow_resolves_and_hashes_immutable_core_archive_before_apply():
    workflow = workflow_text()
    resolve_index = workflow.index('git ls-remote https://github.com/opnsense/core.git')
    download_index = workflow.index('https://github.com/opnsense/core/archive/$core_commit.tar.gz')
    hash_index = workflow.index('sha256sum')
    apply_index = workflow.index('tools/ci/sync_upstream.py apply')

    assert 'refs/heads/stable/$series' in workflow
    assert 'curl --fail --location' in workflow
    assert resolve_index < download_index < hash_index < apply_index
    assert '--core-archive-url "$core_archive_url"' in workflow
    assert '--core-archive-sha256 "$core_archive_sha256"' in workflow


def test_workflow_publishes_only_validated_plan_branches_and_review_prs():
    workflow = workflow_text()
    bootstrap_build = case_arm(workflow, 'bootstrap-build')
    bootstrap_review = case_arm(workflow, 'bootstrap-review')
    update_review = case_arm(workflow, 'update-review')

    target_push = 'git push origin "refs/heads/$target_release:refs/heads/$target_release"'
    sync_push = 'git push origin "refs/heads/$sync_branch:refs/heads/$sync_branch"'
    assert target_push in bootstrap_build
    assert sync_push not in bootstrap_build
    assert target_push in bootstrap_review
    assert sync_push in bootstrap_review
    assert target_push not in update_review
    assert sync_push in update_review
    assert workflow.count(target_push) == 2
    assert workflow.count(sync_push) == 2
    assert 'gh pr create' in workflow
    assert '--base "$target_release"' in workflow
    assert '--head "$sync_branch"' in workflow
    assert '--assignee "$RP_SYNC_REVIEWER"' in workflow
    assert 'RP_SYNC_REVIEWER: ${{ vars.RP_SYNC_REVIEWER }}' in workflow
    assert 'https://github.com/opnsense/plugins/compare/$source_commit...$upstream_commit' in workflow
    assert workflow.count('GH_TOKEN: ${{ github.token }}') == 1


def test_bootstrap_build_uses_the_planner_profile_and_expires():
    workflow = workflow_text()

    assert "steps.plan.outputs.action == 'bootstrap-build'" in workflow
    assert 'release: ${{ steps.plan.outputs.freebsd_release }}' in workflow
    assert 'tools/ci/build-os-bind-rp.sh "$series" "artifacts/$series"' in workflow
    assert 'actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02' in workflow
    assert 'retention-days: 7' in workflow


def test_workflow_pins_actions_and_has_no_publication_authority_or_commands():
    workflow = workflow_text()
    references = action_references(workflow)
    lowered = workflow.lower()

    assert references
    assert all(PINNED_ACTION.fullmatch(reference) for reference in references)
    assert 'actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803' in references
    assert 'vmactions/freebsd-vm@77ed28d336d03fe19a3f4f7266c1d2c4714dd79d' in references
    assert 'secrets.' not in workflow
    assert not re.search(r'^\s*environment:', workflow, re.MULTILINE)
    for forbidden in (
        'gh release', 'create-release', 'pages:', 'id-token:', 'packages:',
        'pkg repo', 'docker push', 'npm publish', 'twine upload',
    ):
        assert forbidden not in lowered
