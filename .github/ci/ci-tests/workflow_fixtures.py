"""Inspect the repository's consistently indented workflow text without YAML dependencies."""
from common_imports import *


WORKFLOWS = Path(__file__).resolve().parents[2] / 'workflows'


def workflow_jobs(workflow):
    return dict(re.findall(r'^  ([\w-]+):\n(.*?)(?=^  [\w-]+:|\Z)',
                           workflow.split('jobs:\n', 1)[1], re.MULTILINE | re.DOTALL))


def job_text(workflow, name):
    return workflow_jobs(workflow)[name]


def workflow_steps(job):
    return re.split(r'^      - ', job, flags=re.MULTILINE)[1:]


def step_run(step):
    match = re.search(r'^(?: {8})?run: ([^\n]+)(?:\n|$)', step, re.MULTILINE)
    if match is None:
        return ''
    if match[1] != '|':
        return match[1] + '\n'
    body = re.match(r'(?: {10}[^\n]*\n|\n)*', step[match.end():])[0]
    return textwrap.dedent(body)


def trigger_paths(workflow, event):
    header = workflow.split('\njobs:', 1)[0]
    block = re.search(r'^  ' + re.escape(event) + r':\n(.*?)(?=^  [\w-]+:|\Z)',
                      header, re.MULTILINE | re.DOTALL)
    if block is None:
        return []
    paths = re.search(r'^    paths:\n((?:      [^\n]*\n)*)', block[1], re.MULTILINE)
    return re.findall(r"^      - '([^']+)'$", paths[1], re.MULTILINE) if paths else []


def action_references(workflow):
    return re.findall(r'^\s+(?:-\s+)?uses:\s+([^\s#]+)', workflow, re.MULTILINE)


def assert_pinned_actions(workflow):
    references = [ref for ref in action_references(workflow) if not ref.startswith('./')]
    assert references
    assert all(re.fullmatch(r'[^@\s]+@[0-9a-f]{40}', ref) for ref in references)


def assert_checkout_credentials(job, *, persistent=False):
    steps = workflow_steps(job)
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
