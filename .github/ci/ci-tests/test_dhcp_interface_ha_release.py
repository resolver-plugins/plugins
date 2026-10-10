"""Protect source identity, verification gates and immutable release retries."""
from module_fixtures import *

import pytest

ROOT = CI.parents[1]
sys.path.insert(0, str(CI))
from ha_dhcp import dhcp_interface_ha_channel as channel
from ha_dhcp import dhcp_interface_ha_release as selection
from ha_fixtures import make_ha_channel
from workflow_fixtures import (assert_checkout_credentials, assert_permissions,
                               action_references, workflow_jobs)

publisher = load_module("publish_dhcpha", "ha_dhcp/publish-dhcp-interface-ha.py")
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


def test_release_rejects_downgrade(release_history):
    before = release_history("0.2", 43)
    source = release_history("0.2", 42)
    with pytest.raises(ValueError, match="decreased"):
        selection.select("push", "refs/heads/master", source, before)


def test_release_cli_consumes_push_payload_and_emits_job_outputs(release_history, tmp_path):
    before = release_history()
    source = release_history("0.2", 43)
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"before": before}))
    env = dict(os.environ, GITHUB_EVENT_PATH=str(event), GITHUB_EVENT_NAME="push",
               GITHUB_REF="refs/heads/master", GITHUB_SHA=source)
    output = subprocess.check_output([sys.executable, CI / "ha_dhcp/dhcp_interface_ha_release.py", "select"],
                                     env=env, text=True)
    assert dict(line.split("=", 1) for line in output.splitlines()) == {
        "release": "true", "series": "26.7", "source_commit": source}


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
    assert promote == ['release', 'edit', 'dhcp-interface-ha-26.7-0.2_30',
                       '--repo', 'example/plugins', '--draft=false', '--latest=false']
    names = [c[0] for c in calls.mock_calls]
    assert names == ['snapshot_release', 'run_gh', 'upload_release_assets',
                     'snapshot_release', 'snapshot_matches_directory', 'run_gh']

    calls.reset_mock()
    calls.snapshot_release.side_effect = None
    calls.snapshot_release.return_value = SimpleNamespace(existed=True)
    publisher.publish('example/plugins', directory, '26.7', COMMIT)
    calls.run_gh.assert_called_once_with(promote)
    calls.upload_release_assets.assert_not_called()


def test_existing_release_accepts_only_identical_bytes(release):
    directory, calls = release
    calls.snapshot_release.side_effect = None
    calls.snapshot_release.return_value = SimpleNamespace(existed=True)
    calls.snapshot_matches_directory.return_value = False
    with pytest.raises(RuntimeError, match='different bytes'):
        publisher.publish('example/plugins', directory, '26.7', COMMIT)
    calls.run_gh.assert_not_called()
    calls.upload_release_assets.assert_not_called()


def test_failed_uploaded_verification_keeps_release_draft(release):
    directory, calls = release
    calls.snapshot_matches_directory.return_value = False
    with pytest.raises(RuntimeError, match="uploaded release"):
        publisher.publish("example/plugins", directory, "26.7", COMMIT)
    assert calls.run_gh.call_count == 1
    assert "--draft" in calls.run_gh.call_args.args[0]


def test_workflow_keeps_build_readonly_and_publishes_only_after_tests_and_verification():
    test_workflow = (ROOT / ".github/workflows/ha-dhcp-interface-tests.yml").read_text()
    for event, next_event in (("push", "pull_request"), ("pull_request", "workflow_dispatch")):
        trigger = test_workflow.split(f"  {event}:\n")[1].split(f"  {next_event}:\n")[0]
        ci_paths = set(re.findall(r"^      - '(\.github/ci/[^']+)'$", trigger, re.MULTILINE))
        assert ci_paths == {'.github/ci/ha_dhcp/**', '.github/ci/shared/**', '.github/ci/ci-tests/**'}
    workflow = (ROOT / ".github/workflows/ha-dhcp-interface-release.yml").read_text()
    jobs = workflow_jobs(workflow)
    build, publish = (jobs[name] for name in ("build", "publish"))
    assert "  workflow_dispatch:" in workflow
    assert "  push:\n    branches: [master]\n    paths: ['net/dhcp-interface-ha/Makefile']" in workflow
    assert "  pull_request:" not in workflow
    assert "if: needs.profile.outputs.release == 'true'" in workflow.split("  profile:")[0]
    assert "release: ${{ needs.profile.outputs.freebsd_release }}" in build
    assert 'git show "$PROFILE_COMMIT:.resolver-plugins/upstream.json"' in build
    assert_permissions(workflow, {"contents": "read"})
    assert "secrets." not in workflow.split("jobs:\n", 1)[0]
    dependencies = {"build": "[profile, test]", "publish": "[profile, sign, verify]",
                    "source-release": "[profile, verify-published]"}
    secrets = {"sign": {"RP_PKG_SIGNING_KEY"}, "publish": {"RP_DISTRIBUTION_APP_PRIVATE_KEY"}}
    for name, job in jobs.items():
        assert_permissions(job, {"contents": "write"} if name == "source-release" else None, indent=4)
        assert set(re.findall(r"secrets\.([A-Z_]+)", job)) == secrets.get(name, set()), name
        if name in dependencies:
            assert f"    needs: {dependencies[name]}\n" in job
        if name != "test":
            assert_checkout_credentials(job)
        if name not in {"profile", "test"}:
            assert "ref: ${{ needs.profile.outputs.source_commit }}" in job
    for token_input in ("app-id: ${{ vars.RP_DISTRIBUTION_APP_ID }}",
                        "private-key: ${{ secrets.RP_DISTRIBUTION_APP_PRIVATE_KEY }}",
                        "owner: resolver-plugins", "repositories: repository", "permission-contents: write",
                        "GH_TOKEN: ${{ steps.distribution-token.outputs.token }}"):
        assert token_input in publish
    actions = action_references(workflow)
    assert all(a.startswith("./") or re.fullmatch(r"[^@]+@[0-9a-f]{40}", a) for a in actions)


def test_builder_retains_target_parser_and_native_installation_gates():
    builder = (CI / "ha_dhcp/build-dhcp-interface-ha.sh").read_text()
    assert builder.index('package_checksums.py"') < builder.index('"$pkg_static" add "$package"')
    assert builder.index('"$pkg_static" check -s') < builder.index('cp "$package" "$output/"')
    assert builder.index('test_ui_routes.php') < builder.index('cp "$package" "$output/"')
    verifier = (CI / "ha_dhcp/verify-dhcp-interface-ha.sh").read_text()
    signer = (ROOT / ".github/workflows/ha-dhcp-interface-release.yml").read_text().split("  sign:")[1].split("  verify:")[0]
    for script in (builder, verifier, signer):
        assert script.index('dhcp_interface_ha_release.py') < script.index('setup-opnsense-repository.sh')
        assert 'export RP_OPNSENSE_SNAPSHOT' in script
    assert "printf 'repository_snapshot=%s\\n'" in builder

@pytest.fixture
def signed_channel(tmp_path):
    return make_ha_channel(tmp_path / 'channel')


@pytest.fixture
def signing_target(signed_channel, monkeypatch):
    target = channel.target_pkg.load_target(channel.TARGET, "26.7")
    profile = {"core_commit": "c" * 40, "freebsd_release": "15.1"}
    build_file = signed_channel / "build-metadata.txt"
    with build_file.open("a") as stream:
        stream.write(f"core_commit={profile['core_commit']}\nfreebsd_release=15.1\n"
                     f"pkg_abi={target.identity.abi}\npkg_creator={target.identity.version}\n"
                     f"pkg_creator_sha256={target.sha256}\n")
    monkeypatch.setattr(channel.metadata_profile, "load_profile", lambda *a: profile)
    return target


def test_signed_channel_rejects_tampered_assets_and_foreign_key(signed_channel):
    key = signed_channel / "resolver-plugins.pub"
    key.write_text("foreign key")
    with pytest.raises(ValueError, match="untrusted"):
        channel.validate(signed_channel)
    key.write_bytes(channel.PUBLIC_KEY.read_bytes())
    (signed_channel / "os-dhcp-interface-ha-0.2_30.pkg").write_text("changed")
    with pytest.raises(ValueError, match="checksums"):
        channel.validate(signed_channel)


def test_signer_rejects_unreviewed_snapshot_without_invalidating_older_metadata(signed_channel, signing_target, tmp_path, monkeypatch):
    build = signed_channel / "build-metadata.txt"
    build.write_text(build.read_text().replace("repository_snapshot=26.7.4", "repository_snapshot=26.7.3"))
    # Older current channels remain readable when a later source selects a new snapshot.
    assert channel.metadata(signed_channel, "26.7", COMMIT, "b" * 40)["repository_snapshot"] == "26.7.3"
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
        assert args[1] == 'pkg-dhcp-interface-ha-26.7-0.2_30'
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
    assert all(call.args[1] == 'pkg-dhcp-interface-ha-26.7' for call in publish.call_args_list)
    assert all(call.args[1].startswith('pkg-dhcp-interface-ha-') for call in reads.call_args_list)
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
def test_signer_verifies_package_and_public_key_before_signing(signed_channel, signing_target, tmp_path, monkeypatch, fault):
    target = signing_target
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
        calls.verify_target_pkg.assert_called_once_with(target, 'pkg')
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
