import pathlib


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[3]
WORKFLOW = REPOSITORY_ROOT / '.github/workflows/proof-build.yml'


def test_workflow_is_manual_pinned_26_1_artifact_build():
    assert WORKFLOW.is_file(), 'manual 26.1 artifact workflow is missing'
    workflow = WORKFLOW.read_text()

    assert 'name: Build os-bind-rp 26.1' in workflow
    assert 'workflow_dispatch:' in workflow
    assert 'inputs:' not in workflow
    assert 'push:' not in workflow
    assert 'pull_request:' not in workflow
    assert 'schedule:' not in workflow
    assert 'workflow_call:' not in workflow
    assert 'issues:' not in workflow
    assert 'contents: read' in workflow
    assert 'actions: read' in workflow
    assert workflow.count('permissions:') == 1
    assert 'pages: write' not in workflow
    assert 'id-token: write' not in workflow
    assert 'environment:' not in workflow
    assert '\n    env:' not in workflow
    assert 'secrets.' not in workflow
    assert 'jobs:\n  build-26-1:' in workflow
    assert workflow.count('\n  build-') == 1
    assert 'timeout-minutes: 45' in workflow
    assert 'actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803' in workflow
    assert 'vmactions/freebsd-vm@77ed28d336d03fe19a3f4f7266c1d2c4714dd79d' in workflow
    assert 'actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02' in workflow
    assert 'release: "14.3"' in workflow
    assert 'release: "15.1"' not in workflow
    assert '26.7' not in workflow
    assert 'arch: x86_64' in workflow
    assert 'disable-cache: true' in workflow
    assert 'pkg install -y git' not in workflow
    assert 'pkg install -y python3 py311-pytest' not in workflow
    assert (
        "SOURCE_COMMIT='${{ github.sha }}' "
        'tools/ci/build-os-bind-rp.sh 26.1 artifacts/26.1'
    ) in workflow
    assert workflow.count('tools/ci/build-os-bind-rp.sh') == 1
    assert 'name: os-bind-rp-26.1' in workflow
    assert 'path: artifacts/26.1' in workflow
    assert 'retention-days: 7' in workflow
