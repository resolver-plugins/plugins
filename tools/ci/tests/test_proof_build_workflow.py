import pathlib


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[3]
WORKFLOW = REPOSITORY_ROOT / '.github/workflows/proof-build.yml'


def test_proof_workflow_is_manual_pinned_and_non_publishing():
    assert WORKFLOW.is_file(), 'non-publishing proof workflow is missing'
    workflow = WORKFLOW.read_text()

    assert 'workflow_dispatch:' in workflow
    assert 'contents: read' in workflow
    assert 'pages: write' not in workflow
    assert 'id-token: write' not in workflow
    assert 'environment:' not in workflow
    assert 'secrets.' not in workflow
    assert 'actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803' in workflow
    assert 'vmactions/freebsd-vm@77ed28d336d03fe19a3f4f7266c1d2c4714dd79d' in workflow
    assert 'actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02' in workflow
    assert 'release: "14.3"' in workflow
    assert 'release: "15.1"' in workflow
    assert 'disable-cache: true' in workflow
    assert 'tools/ci/build-os-bind-rp.sh 26.1 artifacts/26.1' in workflow
    assert 'tools/ci/build-os-bind-rp.sh 26.7 artifacts/26.7' in workflow
