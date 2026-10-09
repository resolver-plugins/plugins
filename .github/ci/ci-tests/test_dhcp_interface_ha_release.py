"""Protect source identity, verification gates and immutable release retries."""
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

CI = Path(__file__).resolve().parents[1]
ROOT = CI.parents[1]
sys.path.insert(0, str(CI))
import dhcp_interface_ha_channel as channel
import dhcp_interface_ha_release as selection
from ha_fixtures import make_ha_channel

spec = importlib.util.spec_from_file_location("publish_dhcpha", CI / "publish-dhcp-interface-ha.py")
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)
COMMIT = "a" * 40

@pytest.fixture
def release_history(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-q"], check=True)

    def commit(version=None, revision=0, comment=""):
        if version is not None:
            path = tmp_path / selection.MAKEFILE
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"PLUGIN_VERSION=\t{version}\nPLUGIN_REVISION=\t{revision}\n{comment}\n")
            subprocess.run(["git", "add", selection.MAKEFILE], check=True)
        subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                        "commit", "--allow-empty", "-qm", "fixture"], check=True)
        return selection.git("rev-parse", "HEAD")

    return commit


@pytest.mark.parametrize("previous,current,release", [
    (None, ("0.2", 43), True),
    (("0.2", 42), ("0.2", 43), True),
    (("0.2", 99), ("0.3", 0), True),
    (("0.2", 43), ("0.2", 43), False),
    (("0.2", 43), ("0.2.0", 43), False),
])
def test_push_selects_only_package_version_increases(release_history, previous, current, release):
    before = release_history(*previous) if previous else release_history()
    source = release_history(*current, comment="# metadata-only edit")
    assert selection.select("push", "refs/heads/master", source, before) is release


def test_manual_retry_and_new_branch_select_exact_source(release_history):
    source = release_history("0.2", 43)
    assert selection.select("workflow_dispatch", "refs/heads/master", source)
    assert selection.select("push", "refs/heads/master", source, "0" * 40)


def test_release_rejects_downgrade_and_unknown_history(release_history):
    before = release_history("0.2", 43)
    source = release_history("0.2", 42)
    with pytest.raises(ValueError, match="decreased"):
        selection.select("push", "refs/heads/master", source, before)
    with pytest.raises(subprocess.CalledProcessError):
        selection.select("push", "refs/heads/master", source, "f" * 40)


@pytest.mark.parametrize("event,ref", [
    ("push", "refs/heads/feature"),
    ("workflow_dispatch", "refs/tags/v0.2"),
    ("pull_request", "refs/heads/master"),
])
def test_release_rejects_unapproved_trigger(event, ref):
    with pytest.raises(ValueError, match="master"):
        selection.select(event, ref, COMMIT)


def test_release_cli_consumes_push_payload_and_emits_job_outputs(release_history, tmp_path):
    before = release_history()
    source = release_history("0.2", 43)
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"before": before}))
    env = dict(os.environ, GITHUB_EVENT_PATH=str(event), GITHUB_EVENT_NAME="push",
               GITHUB_REF="refs/heads/master", GITHUB_SHA=source)
    output = subprocess.check_output([sys.executable, CI / "dhcp_interface_ha_release.py", "select"],
                                     env=env, text=True)
    assert dict(line.split("=", 1) for line in output.splitlines()) == {
        "release": "true", "series": "26.7", "source_commit": source}


def test_release_rejects_missing_plugin_or_computed_version(release_history):
    source = release_history()
    with pytest.raises(ValueError, match="does not contain"):
        selection.select("workflow_dispatch", "refs/heads/master", source)
    source = release_history("${UNKNOWN}", 43)
    with pytest.raises(ValueError, match="literal numeric"):
        selection.select("workflow_dispatch", "refs/heads/master", source)


@pytest.fixture
def release(tmp_path, monkeypatch):
    (tmp_path / "build-metadata.txt").write_text(
        f"source_commit={COMMIT}\nseries=26.7\nrepository_snapshot=26.7.4\nplugin_version=0.2_30\n")
    for name in ("os-dhcp-interface-ha-0.2_30.pkg", "upstream.json", "SHA256SUMS"):
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
    assert "  push:\n    branches: [master]\n    paths: ['net/dhcp-interface-ha/Makefile']" in workflow
    assert "  pull_request:" not in workflow
    assert "if: needs.profile.outputs.release == 'true'" in workflow.split("  profile:")[0]
    assert 'dhcp_interface_ha_release.py select --series "$SERIES" >> "$GITHUB_OUTPUT"' in workflow
    assert "${{ inputs.series }}" not in workflow
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
    checkouts = [step for step in re.split(r"(?m)^      - ", workflow)[1:]
                 if re.search(r"(?m)^(?:uses:|        uses:) actions/checkout@", step)]
    assert checkouts
    for step in checkouts:
        inputs = re.search(r"(?m)^        with:\n((?:          .*\n)*)", step)
        assert inputs and re.search(r"(?m)^          persist-credentials: false$", inputs[1])


def test_builder_retains_target_parser_and_native_installation_gates():
    builder = (CI / "build-dhcp-interface-ha.sh").read_text()
    assert builder.index('target_pkg.py" install') < builder.index('PLUGIN_HASH="$SOURCE_COMMIT" package')
    assert builder.index('package_checksums.py"') < builder.index('"$pkg_static" add "$package"')
    assert builder.index('"$pkg_static" check -s') < builder.index('cp "$package" "$output/"')
    assert builder.index('test_ui_routes.php') < builder.index('cp "$package" "$output/"')
    assert 'PLUGIN_DEVEL= PLUGIN_ABI="$series"' in builder
    assert 'rm -rf "$plugin/work"' in builder
    verifier = (CI / "verify-dhcp-interface-ha.sh").read_text()
    signer = (ROOT / ".github/workflows/dhcp-interface-ha-release.yml").read_text().split("  sign:")[1].split("  verify:")[0]
    for script in (builder, verifier, signer):
        assert script.index('dhcp_interface_ha_release.py') < script.index('setup-opnsense-repository.sh')
        assert 'export RP_OPNSENSE_SNAPSHOT' in script
    assert "printf 'repository_snapshot=%s\\n'" in builder

@pytest.fixture
def signed_channel(tmp_path):
    return make_ha_channel(tmp_path / 'channel')


def test_signed_channel_rejects_tampered_assets_and_foreign_key(signed_channel):
    assert channel.validate(signed_channel)["source_commit"] == COMMIT
    key = signed_channel / "resolver-plugins.pub"
    key.write_text("foreign key")
    with pytest.raises(ValueError, match="untrusted"):
        channel.validate(signed_channel)
    key.write_bytes(channel.PUBLIC_KEY.read_bytes())
    (signed_channel / "os-dhcp-interface-ha-0.2_30.pkg").write_text("changed")
    with pytest.raises(ValueError, match="checksums"):
        channel.validate(signed_channel)


def test_signer_rejects_unreviewed_snapshot_without_invalidating_older_metadata(signed_channel, tmp_path, monkeypatch):
    build = signed_channel / "build-metadata.txt"
    build.write_text(build.read_text().replace("repository_snapshot=26.7.4", "repository_snapshot=26.7.3"))
    # Older current channels remain readable when a later source selects a new snapshot.
    assert channel.metadata(signed_channel, "26.7", COMMIT, "b" * 40)["repository_snapshot"] == "26.7.3"
    monkeypatch.setattr(channel.metadata_profile, "load_profile", lambda *a: {})
    sign = Mock()
    monkeypatch.setattr(channel.releases, "stage_selected_repository", sign)
    with pytest.raises(ValueError, match="provenance"):
        channel.stage(signed_channel, tmp_path / "signed", "26.7", COMMIT, "b" * 40,
                      signed_channel / "upstream.json", tmp_path / "key")
    sign.assert_not_called()


def test_signed_retry_reuses_exact_snapshot_and_rejects_source_mismatch(signed_channel, tmp_path, monkeypatch):
    snapshot = SimpleNamespace(existed=True, directory=signed_channel)
    monkeypatch.setattr(channel.releases, "snapshot_release", Mock(return_value=snapshot))
    output = tmp_path / "reuse"
    assert channel.reuse("example/repository", signed_channel, output, "26.7", COMMIT, "b" * 40)
    assert channel.releases.directory_checksums(output) == channel.releases.directory_checksums(signed_channel)
    # The newly built metadata claims a different source for an existing version.
    (output / "build-metadata.txt").write_text(
        f"source_commit={'c' * 40}\nprofile_commit={'b' * 40}\nseries=26.7\nrepository_snapshot=26.7.4\nplugin_version=0.2_30\n")
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


def test_forward_retry_after_rollback_retains_bytes_and_ancestry_guards(tmp_path, monkeypatch, release_history):
    old_commit, new_commit = release_history(), release_history()
    signed_channel = make_ha_channel(tmp_path / 'channel', source=new_commit)
    old_dir = make_ha_channel(tmp_path / 'old', source=old_commit, version='0.2_29')
    old = SimpleNamespace(existed=True, directory=old_dir)
    archive = SimpleNamespace(existed=False)
    reads = Mock(side_effect=lambda repository, tag, recovery:
                 old if tag == 'pkg-dhcp-interface-ha-26.7' else archive)
    monkeypatch.setattr(channel.releases, 'snapshot_release', reads)
    monkeypatch.setattr(channel.releases, 'snapshot_matches_directory', lambda snapshot, directory:
                        snapshot.existed and channel.releases.directory_checksums(snapshot.directory)
                        == channel.releases.directory_checksums(directory))
    monkeypatch.setattr(channel.releases, 'release_snapshots_match', lambda a, b: True)
    def publish_archive(*args):
        archive.existed = True
        archive.directory = signed_channel
    monkeypatch.setattr(channel.releases, 'publish_immutable_release', publish_archive)
    publish = Mock(side_effect=RuntimeError('upload failed'))
    restore = Mock()
    monkeypatch.setattr(channel.releases, 'publish', publish)
    monkeypatch.setattr(channel.releases, 'restore_release', restore)
    with pytest.raises(RuntimeError, match='upload failed'):
        channel.promote('example/repository', signed_channel, tmp_path / 'first')
    restore.assert_called_once_with('example/repository', old)
    publish.side_effect = None
    channel.promote('example/repository', signed_channel, tmp_path / 'retry')
    assert publish.call_count == 2
    # A matching archive cannot authorize an old-source retry over a newer current.
    old.directory, archive.directory = signed_channel, old_dir
    with pytest.raises(RuntimeError, match='stale promotion'):
        channel.promote('example/repository', old_dir, tmp_path / 'stale')
    assert publish.call_count == 2
    # Nor can ancestry authorize replacing an immutable snapshot's bytes.
    old.directory = old_dir
    with pytest.raises(RuntimeError, match='different bytes'):
        channel.promote('example/repository', signed_channel, tmp_path / 'changed')
    assert publish.call_count == 2


@pytest.mark.parametrize("fault", [None, "key", "identity"])
def test_signer_verifies_package_and_public_key_before_signing(signed_channel, tmp_path, monkeypatch, fault):
    target = channel.target_pkg.load_target(channel.TARGET, "26.7")
    profile = {"core_commit": "c" * 40, "freebsd_release": "15.1"}
    build_file = signed_channel / "build-metadata.txt"
    with build_file.open("a") as stream:
        stream.write(f"core_commit={profile['core_commit']}\nfreebsd_release=15.1\n"
                     f"pkg_abi={target.identity.abi}\npkg_creator={target.identity.version}\n"
                     f"pkg_creator_sha256={target.sha256}\n")
    monkeypatch.setattr(channel.metadata_profile, "load_profile", lambda *a: profile)
    calls = Mock()
    calls.check_output.side_effect = ["0.2_30\n", channel.PUBLIC_KEY.read_bytes() if fault != "key" else b"foreign key"]
    calls.query_package.return_value = (("foreign-package" if fault == "identity" else channel.NAME, "0.2_30", f"opnsense/{channel.NAME}", target.identity.abi), set())
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
    if fault is None:
        channel.stage(*args)
        assert channel.validate(tmp_path / "signed")["plugin_version"] == "0.2_30"
        calls.verify_archive.assert_called_once_with(channel.PKG, signed_channel / f"{channel.NAME}-0.2_30.pkg")
        names = [c[0] for c in calls.mock_calls]
        assert names.index("verify_target_pkg") < names.index("stage")
        assert names.index("verify_archive") < names.index("stage")
    else:
        with pytest.raises(ValueError, match="package identity" if fault == "identity" else "signing key"):
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
