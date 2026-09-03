from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
USER_GUIDES = (
    ROOT / "README.md",
    ROOT / "docs/fork-model.md",
    ROOT / "docs/package-repository.md",
    ROOT / "docs/package-channel-distribution-design.md",
    ROOT / "docs/building.md",
)


def test_user_guides_use_the_abi_aware_current_package_channel():
    text = "\n".join(path.read_text(encoding="utf-8") for path in USER_GUIDES)
    assert "https://resolver-plugins.github.io/repository/pkg/${ABI}/<series>/latest" in text
    assert "pkg/FreeBSD:15:amd64/26.7/latest" in text
    assert "https://resolver-plugins.github.io/repository/pkg/${ABI}/latest" not in text
    assert "resolver-plugins/plugins/releases/download/pkg-" not in text
    assert "pkg-<series>-bind920" not in text
    assert "pkg-$series-bind920" not in text


def test_maintainer_guide_documents_cross_repository_publication_setup():
    text = (ROOT / "docs/package-repository.md").read_text(encoding="utf-8")
    assert "RP_DISTRIBUTION_APP_ID" in text
    assert "RP_DISTRIBUTION_APP_PRIVATE_KEY" in text
    assert "RP_DISTRIBUTION_REPOSITORY_TOKEN" not in text
    assert "Contents: write" in text
    assert "webhooks disabled" in text
    assert "installed only on\n`resolver-plugins/repository`" in text
    assert "master" in text and "workflow_dispatch" in text


def test_package_guides_document_manifest_compatibility_and_recovery_contracts():
    building = (ROOT / "docs/building.md").read_text(encoding="utf-8")
    repository = (ROOT / "docs/package-repository.md").read_text(encoding="utf-8")
    design = (ROOT / "docs/package-channel-distribution-design.md").read_text(
        encoding="utf-8"
    )
    combined = "\n".join((building, repository, design))

    for contract in (
        "pkg_creator",
        "package_checksums.py",
        "official `os-bind`",
        "non-null",
        "configuration backup",
        "target package manager",
    ):
        assert contract in combined
    assert "does not upgrade the host package manager" in repository
    assert "does not enable BIND or change its user configuration" in repository
    assert "stops BIND" in repository
    assert "restarts BIND only when it was running" in repository
    assert "upgrade the backup node first" in repository
    assert "RP_STATE_DIRECTORY" in repository


def test_package_guides_document_series_versions_and_nonblocking_reconciliation():
    repository = (ROOT / "docs/package-repository.md").read_text(encoding="utf-8")
    design = (ROOT / "docs/package-channel-distribution-design.md").read_text(
        encoding="utf-8"
    )
    combined = "\n".join((repository, design))

    assert "os-bind-rp-26.1_1" in combined
    assert "os-bind-rp-26.7_1" in combined
    assert "BIND packages keep upstream versions" in combined
    assert "ResolverPlugins.sh" in combined
    assert "bindRepositoryReconcile.py" in combined
    assert "upgrade hook only marks reconciliation pending" in combined
    assert "start hook launches" in combined
    assert "does not abort OPNsense upgrade or boot" in combined
