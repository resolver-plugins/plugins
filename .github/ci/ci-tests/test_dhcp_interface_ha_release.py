"""Protect source identity, verification gates and immutable release retries."""
import importlib.util
from pathlib import Path
import re
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

CI = Path(__file__).resolve().parents[1]
ROOT = CI.parents[1]
sys.path.insert(0, str(CI))
spec = importlib.util.spec_from_file_location("publish_dhcpha", CI / "publish-dhcp-interface-ha.py")
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)
COMMIT = "a" * 40


@pytest.fixture
def release(tmp_path, monkeypatch):
    (tmp_path / "build-metadata.txt").write_text(
        f"source_commit={COMMIT}\nseries=26.7\nplugin_version=0.2_30\n")
    for name in ("os-dhcp-interface-ha-devel-0.2_30.pkg", "upstream.json", "SHA256SUMS"):
        (tmp_path / name).write_text("fixture\n")
    calls = Mock()
    for name in ("run_gh", "upload_release_assets", "snapshot_release", "snapshot_matches_directory"):
        monkeypatch.setattr(publisher, name, getattr(calls, name))
    calls.snapshot_release.side_effect = [SimpleNamespace(existed=False), SimpleNamespace(existed=True)]
    calls.snapshot_matches_directory.return_value = True
    return tmp_path, calls


def test_release_targets_exact_commit_and_verifies_draft_before_publication(release):
    directory, calls = release
    publisher.publish("example/plugins", directory, "26.7", COMMIT)
    create, promote = [c.args[0] for c in calls.run_gh.call_args_list]
    assert create[:3] == ["release", "create", "dhcp-interface-ha-26.7-0.2_30"]
    assert create[create.index("--target") + 1] == COMMIT
    assert "--prerelease" in create and "--draft" in create
    assert "--draft=false" in promote
    names = [c[0] for c in calls.mock_calls]
    assert names.index("upload_release_assets") < names.index("snapshot_matches_directory") < len(names) - 1
    assert names[-1] == "run_gh"


@pytest.mark.parametrize("matching", [True, False])
def test_existing_release_accepts_only_identical_bytes(release, matching):
    directory, calls = release
    calls.snapshot_release.side_effect = None
    calls.snapshot_release.return_value = SimpleNamespace(existed=True)
    calls.snapshot_matches_directory.return_value = matching
    if matching:
        publisher.publish("example/plugins", directory, "26.7", COMMIT)
        # A completed draft can be promoted on retry without uploading again.
        assert "--draft=false" in calls.run_gh.call_args.args[0]
    else:
        with pytest.raises(RuntimeError, match="different bytes"):
            publisher.publish("example/plugins", directory, "26.7", COMMIT)
        calls.run_gh.assert_not_called()
    calls.upload_release_assets.assert_not_called()


def test_failed_uploaded_verification_keeps_release_draft(release):
    directory, calls = release
    calls.snapshot_matches_directory.return_value = False
    with pytest.raises(RuntimeError, match="uploaded release"):
        publisher.publish("example/plugins", directory, "26.7", COMMIT)
    assert calls.run_gh.call_count == 1
    assert "--draft" in calls.run_gh.call_args.args[0]


@pytest.mark.parametrize("mismatch", ["source", "assets"])
def test_publication_rejects_wrong_source_or_extra_assets(release, mismatch):
    directory, calls = release
    if mismatch == "assets":
        (directory / "unexpected.pkg").write_text("extra")
    with pytest.raises(ValueError):
        publisher.publish("example/plugins", directory, "26.7", "b" * 40 if mismatch == "source" else COMMIT)
    calls.snapshot_release.assert_not_called()


def test_workflow_keeps_build_readonly_and_publishes_only_after_tests_and_verification():
    workflow = (ROOT / ".github/workflows/dhcp-interface-ha-release.yml").read_text()
    build = workflow.split("  build:")[1].split("  sign:")[0]
    publish = workflow.split("  publish:")[1].split("  verify-published:")[0]
    source = workflow.split("  source-release:")[1]
    sign = workflow.split("  sign:")[1].split("  verify:")[0]
    assert "  workflow_dispatch:" in workflow
    assert "  push:" not in workflow and "  pull_request:" not in workflow
    assert "needs: [profile, test]" in build
    assert "needs: [profile, sign, verify]" in publish
    assert "needs: [profile, verify-published]" in source
    assert "contents: write" not in workflow.split("  publish:")[0]
    assert "contents: write" in source
    assert "RP_PKG_SIGNING_KEY" not in build + publish + source
    assert "RP_PKG_SIGNING_KEY" in sign
    assert "repositories: repository" in publish
    assert 'test "$GITHUB_REF" = refs/heads/master' in workflow
    assert "ref: ${{ needs.profile.outputs.source_commit }}" in build
    assert "ref: ${{ needs.profile.outputs.source_commit }}" in publish
    assert "release: ${{ needs.profile.outputs.freebsd_release }}" in build
    assert 'git show "$PROFILE_COMMIT:.resolver-plugins/upstream.json"' in build
    assert "sha256sum --check SHA256SUMS" in sign
    assert "dhcp_interface_ha_channel.py promote" in publish
    actions = re.findall(r"uses: ([^\s]+)", workflow)
    assert all(a.startswith("./") or re.fullmatch(r"[^@]+@[0-9a-f]{40}", a) for a in actions)
    assert workflow.count("persist-credentials: false") == workflow.count("uses: actions/checkout@")


def test_builder_retains_target_parser_and_native_installation_gates():
    builder = (CI / "build-dhcp-interface-ha.sh").read_text()
    assert builder.index('target_pkg.py" install') < builder.index('PLUGIN_HASH="$SOURCE_COMMIT" package')
    assert builder.index('package_checksums.py"') < builder.index('"$pkg_static" add "$package"')
    assert builder.index('"$pkg_static" check -s') < builder.index('cp "$package" "$output/"')
    assert builder.index('test_ui_routes.php') < builder.index('cp "$package" "$output/"')
    assert '_PLUGIN_DEVEL=yes PLUGIN_ABI="$series"' in builder
    assert 'rm -rf "$plugin/work"' in builder

import json
import dhcp_interface_ha_channel as channel


@pytest.fixture
def signed_channel(tmp_path):
    directory = tmp_path / "channel"
    directory.mkdir()
    (directory / "build-metadata.txt").write_text(
        f"source_commit={COMMIT}\nprofile_commit={'b' * 40}\nseries=26.7\nplugin_version=0.2_30\n")
    for name in ("os-dhcp-interface-ha-devel-0.2_30.pkg", "upstream.json", "meta.conf", "packagesite.pkg"):
        (directory / name).write_text("fixture\n")
    (directory / "resolver-plugins.pub").write_bytes(channel.PUBLIC_KEY.read_bytes())
    data = dict(schema=1, source_commit=COMMIT, profile_commit="b" * 40, series="26.7",
                plugin_version="0.2_30", package_creator=channel.target_pkg.load_target(channel.TARGET, "26.7").record(),
                assets=channel.releases.directory_checksums(directory))
    (directory / "channel.json").write_text(json.dumps(data))
    return directory


def test_signed_channel_rejects_tampered_assets_and_foreign_key(signed_channel):
    assert channel.validate(signed_channel)["source_commit"] == COMMIT
    key = signed_channel / "resolver-plugins.pub"
    key.write_text("foreign key")
    with pytest.raises(ValueError, match="untrusted"):
        channel.validate(signed_channel)
    key.write_bytes(channel.PUBLIC_KEY.read_bytes())
    (signed_channel / "os-dhcp-interface-ha-devel-0.2_30.pkg").write_text("changed")
    with pytest.raises(ValueError, match="checksums"):
        channel.validate(signed_channel)


def test_signed_retry_reuses_exact_snapshot_and_rejects_source_mismatch(signed_channel, tmp_path, monkeypatch):
    snapshot = SimpleNamespace(existed=True, directory=signed_channel)
    monkeypatch.setattr(channel.releases, "snapshot_release", Mock(return_value=snapshot))
    output = tmp_path / "reuse"
    assert channel.reuse("example/repository", signed_channel, output, "26.7", COMMIT, "b" * 40)
    assert channel.releases.directory_checksums(output) == channel.releases.directory_checksums(signed_channel)
    # The newly built metadata claims a different source for an existing version.
    (output / "build-metadata.txt").write_text(
        f"source_commit={'c' * 40}\nprofile_commit={'b' * 40}\nseries=26.7\nplugin_version=0.2_30\n")
    with pytest.raises(ValueError, match="different source/profile"):
        channel.reuse("example/repository", output, tmp_path / "reject", "26.7", "c" * 40, "b" * 40)


def test_signed_promotion_preserves_old_channel_on_failure_and_uses_only_ha_tags(signed_channel, tmp_path, monkeypatch):
    old = SimpleNamespace(existed=False)
    reads = Mock(return_value=old)
    monkeypatch.setattr(channel.releases, "snapshot_release", reads)
    monkeypatch.setattr(channel.releases, "release_snapshots_match", lambda a, b: True)
    immutable = Mock()
    replace = Mock(side_effect=RuntimeError("upload failed"))
    restore = Mock()
    monkeypatch.setattr(channel.releases, "publish_immutable_release", immutable)
    monkeypatch.setattr(channel.releases, "publish", replace)
    monkeypatch.setattr(channel.releases, "restore_release", restore)
    with pytest.raises(RuntimeError, match="upload failed"):
        channel.promote("example/repository", signed_channel, tmp_path / "recovery")
    restore.assert_called_once_with("example/repository", old)
    assert immutable.call_args.args[1] == "pkg-dhcp-interface-ha-26.7-0.2_30"
    assert replace.call_args.args[1] == "pkg-dhcp-interface-ha-26.7"
    assert all(c.args[1].startswith("pkg-dhcp-interface-ha-") for c in reads.call_args_list)


def test_signed_promotion_rejects_replacing_current_with_stale_source(signed_channel, tmp_path, monkeypatch):
    snapshot = SimpleNamespace(existed=True, directory=signed_channel)
    monkeypatch.setattr(channel.releases, "snapshot_release", Mock(side_effect=[snapshot, SimpleNamespace(existed=False)]))
    monkeypatch.setattr(channel.releases, "snapshot_matches_directory", lambda a, b: False)
    upload = Mock()
    monkeypatch.setattr(channel.releases, "publish", upload)
    with pytest.raises(RuntimeError, match="stale promotion"):
        channel.promote("example/repository", signed_channel, tmp_path / "recovery")
    upload.assert_not_called()


def test_shared_release_titles_accept_isolated_ha_channels_without_changing_bind():
    assert channel.releases.package_release_title("pkg-dhcp-interface-ha-26.7") == "HA DHCP Interface 26.7 — latest"
    assert channel.releases.package_release_title("pkg-dhcp-interface-ha-26.7-0.2_30") == "HA DHCP Interface 26.7 — 0.2_30"
    assert channel.releases.package_release_title("pkg-26.7") == "26.7-latest"


@pytest.mark.parametrize("public_key_matches", [True, False])
def test_signer_checks_pins_package_identity_and_public_key_before_signing(signed_channel, tmp_path, monkeypatch, public_key_matches):
    target = channel.target_pkg.load_target(channel.TARGET, "26.7")
    profile = {"core_commit": "c" * 40, "freebsd_release": "15.1"}
    build_file = signed_channel / "build-metadata.txt"
    with build_file.open("a") as stream:
        stream.write(f"core_commit={profile['core_commit']}\nfreebsd_release=15.1\n"
                     f"pkg_abi={target.identity.abi}\npkg_creator={target.identity.version}\n"
                     f"pkg_creator_sha256={target.sha256}\n")
    monkeypatch.setattr(channel.metadata_profile, "load_profile", lambda *a: profile)
    calls = Mock()
    calls.check_output.side_effect = ["0.2_30\n", channel.PUBLIC_KEY.read_bytes() if public_key_matches else b"foreign key"]
    calls.query_package.return_value = ((channel.NAME, "0.2_30", f"opnsense/{channel.NAME}", target.identity.abi), set())
    monkeypatch.setattr(channel.subprocess, "check_output", calls.check_output)
    monkeypatch.setattr(channel.releases, "query_package", calls.query_package)
    monkeypatch.setattr(channel.target_pkg, "verify_target_pkg", calls.verify_target_pkg)
    monkeypatch.setattr(channel.package_checksums, "verify_archive", calls.verify_archive)

    def fake_catalog(packages, output, key, pkg, metadata):
        output.mkdir()
        for path in packages + metadata:
            (output / path.name).write_bytes(path.read_bytes())
        (output / "meta.conf").write_text("fixture")
        (output / "packagesite.pkg").write_text("signed catalogue")
    calls.stage.side_effect = fake_catalog
    monkeypatch.setattr(channel.releases, "stage_selected_repository", calls.stage)
    args = (signed_channel, tmp_path / "signed", "26.7", COMMIT, "b" * 40,
            signed_channel / "upstream.json", tmp_path / "private-key")
    if public_key_matches:
        channel.stage(*args)
        assert channel.validate(tmp_path / "signed")["plugin_version"] == "0.2_30"
        names = [c[0] for c in calls.mock_calls]
        assert names.index("verify_target_pkg") < names.index("stage")
        assert names.index("verify_archive") < names.index("stage")
    else:
        with pytest.raises(ValueError, match="signing key"):
            channel.stage(*args)
        calls.stage.assert_not_called()


def test_identical_current_can_restore_a_missing_immutable_snapshot(signed_channel, tmp_path, monkeypatch):
    current = SimpleNamespace(existed=True, directory=signed_channel)
    monkeypatch.setattr(channel.releases, "snapshot_release", Mock(side_effect=[current, SimpleNamespace(existed=False)]))
    monkeypatch.setattr(channel.releases, "snapshot_matches_directory", lambda a, b: True)
    archive = Mock()
    update = Mock()
    monkeypatch.setattr(channel.releases, "publish_immutable_release", archive)
    monkeypatch.setattr(channel.releases, "publish", update)
    channel.promote("example/repository", signed_channel, tmp_path / "recovery")
    assert archive.call_args.args[1] == "pkg-dhcp-interface-ha-26.7-0.2_30"
    update.assert_not_called()
