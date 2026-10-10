"""Inspect the repository's consistently indented workflow text without YAML dependencies."""
from common_imports import *


WORKFLOWS = Path(__file__).resolve().parents[2] / 'workflows'


def workflow_jobs(workflow):
    return dict(re.findall(r'^  ([\w-]+):\n(.*?)(?=^  [\w-]+:|\Z)',
                           workflow.split('jobs:\n', 1)[1], re.MULTILINE | re.DOTALL))


def job_text(workflow, name):
    return workflow_jobs(workflow)[name]


def action_references(workflow):
    return re.findall(r'^\s+(?:-\s+)?uses:\s+([^\s#]+)', workflow, re.MULTILINE)


def assert_pinned_actions(workflow):
    references = action_references(workflow)
    assert references
    assert all(re.fullmatch(r'[^@\s]+@[0-9a-f]{40}', ref) for ref in references)


def assert_checkout_credentials(job, *, persistent=False):
    steps = re.split(r'^      - ', job, flags=re.MULTILINE)
    checkouts = [step for step in steps if 'uses: actions/checkout@' in step]
    assert checkouts
    for step in checkouts:
        assert re.findall(r'^          persist-credentials: (\w+)$', step, re.MULTILINE) == [
            'true' if persistent else 'false'
        ]


def assert_permissions(text, expected, *, indent=0):
    padding = ' ' * indent
    blocks = re.findall(r'^' + padding + r'permissions:(?: \{\})?\n((?:' + padding +
                        r'  [^\n]*\n)*)', text, re.MULTILINE)
    headers = re.findall(r'^' + padding + r'permissions:[^\n]*$', text, re.MULTILINE)
    if expected is None:
        assert not headers
    else:
        assert len(headers) == len(blocks) == 1
        assert dict(line.strip().split(': ', 1) for line in blocks[0].splitlines()) == expected
