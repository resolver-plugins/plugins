import pathlib


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[3]
WORKFLOW = REPOSITORY_ROOT / '.github/workflows/proof-build.yml'


def test_workflow_is_manual_pinned_26_1_and_26_7_artifact_build():
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
    assert '\n  build-26-7:' in workflow
    assert workflow.count('\n  build-') == 2
    assert 'pkg install -y git' not in workflow
    assert 'pkg install -y python3 py311-pytest' not in workflow
    job_26_1, job_26_7 = workflow.split('\n  build-26-7:', 1)

    for job in (job_26_1, job_26_7):
        assert 'runs-on: ubuntu-24.04' in job
        assert 'timeout-minutes: 45' in job
        assert 'actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803' in job
        assert 'vmactions/freebsd-vm@77ed28d336d03fe19a3f4f7266c1d2c4714dd79d' in job
        assert 'arch: x86_64' in job
        assert 'disable-cache: true' in job
        assert 'copyback: true' in job
        assert 'actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02' in job
        assert 'retention-days: 7' in job
    assert 'release: "14.3"' in job_26_1
    assert 'release: "15.1"' in job_26_7
    assert 'name: Build in FreeBSD 14.3' in job_26_1
    assert 'name: Build in FreeBSD 15.1' in job_26_7
    assert (
        "SOURCE_COMMIT='${{ github.sha }}' "
        'tools/ci/build-os-bind-rp.sh 26.1 artifacts/26.1'
    ) in job_26_1
    assert workflow.count('tools/ci/build-os-bind-rp.sh') == 2
    assert 'name: os-bind-rp-26.1' in job_26_1
    assert 'path: artifacts/26.1' in job_26_1
    assert (
        "SOURCE_COMMIT='${{ github.sha }}' "
        'tools/ci/build-os-bind-rp.sh 26.7 artifacts/26.7'
    ) in job_26_7
    assert 'name: os-bind-rp-26.7' in job_26_7
    assert 'path: artifacts/26.7' in job_26_7
