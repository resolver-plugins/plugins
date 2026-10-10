#!/usr/bin/env python3
"""Regression coverage for self-contained package channels and rollback snapshots."""

from __future__ import annotations

from module_fixtures import *

from package_fixtures import bind_channel_inputs, bind_records, package_creator, write_json, write_target_metadata


release_channel = load_module("release_channel", "shared/release_channel.py")


def target_creator_record(digest: str = "a" * 64) -> dict[str, str]:
    return package_creator(sha256=digest, pkg_static_sha256=digest)


def bind_provenance_record(fingerprint: str = "f" * 64) -> dict[str, object]:
    return {
        "architecture": "x86_64",
        "fingerprint": fingerprint,
        "freebsd_release": "15.1",
    }


def make_assets(directory, assets):
    directory.mkdir(parents=True)
    for name, contents in assets.items():
        (directory / name).write_bytes(contents)
    return directory


def make_snapshot(directory, tag, assets=None, *, existed=True, draft=False, immutable=False):
    if assets is not None:
        make_assets(directory, assets)
    manifest = directory.with_name(directory.name + '-checksums.json')
    if existed:
        write_json(manifest, {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in directory.iterdir() if p.is_file()})
    return release_channel.ReleaseSnapshot(tag, existed, directory, manifest,
                                           draft=draft, immutable=immutable)


class ChannelTagTest(unittest.TestCase):
    def test_channel_tags_are_series_scoped(self) -> None:
        """Current and immutable snapshot channels must never share a tag."""
        fingerprint = "f" * 64
        self.assertEqual("pkg-26.7", release_channel.channel_tag("26.7"))
        self.assertEqual(
            f"pkg-26.7-os-bind-rp-1.36_2-bind-{fingerprint}",
            release_channel.snapshot_channel_tag("26.7", "1.36_2", fingerprint),
        )
        self.assertEqual(
            f'os-bind-rp-26.7-1.36_7-bind-{fingerprint}',
            release_channel.source_release_tag('26.7', '1.36_7', fingerprint),
        )


class GitHubCliTest(unittest.TestCase):

    def test_release_asset_download_retries_after_removing_partial_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            downloaded = directory / "meta.conf"
            calls: list[list[str]] = []

            def fake_run_gh(arguments: list[str]) -> None:
                calls.append(arguments)
                if len(calls) == 1:
                    downloaded.write_bytes(b"partial")
                    raise subprocess.CalledProcessError(1, ["gh", *arguments])
                self.assertFalse(downloaded.exists(), "partial download survived before retry")
                downloaded.write_bytes(b"complete")

            with patch.object(release_channel, "run_gh", side_effect=fake_run_gh):
                release_channel.download_release_asset(
                    "resolver-plugins/repository", "pkg-26.7", "meta.conf", directory
                )

            self.assertEqual(b"complete", downloaded.read_bytes())
            self.assertEqual(2, len(calls))


class PullRequestReleaseCleanupTest(unittest.TestCase):
    def test_pull_request_release_cleanup_does_not_hide_tag_api_failure(self) -> None:
        def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            if command[:2] == ["gh", "release"]:
                return subprocess.CompletedProcess(command, 0, "", "")
            return subprocess.CompletedProcess(command, 1, "", "gh: Server Error (HTTP 500)")

        with patch.object(release_channel.subprocess, "run", side_effect=fake_run):
            with self.assertRaisesRegex(RuntimeError, "cannot delete development tag"):
                release_channel.cleanup_development_release(
                    "resolver-plugins/plugins", "pr-51-26.7"
                )

    def test_pull_request_cleanup_refuses_a_production_tag_before_mutation(self) -> None:
        with patch.object(release_channel.subprocess, "run") as run:
            with self.assertRaisesRegex(ValueError, "invalid development release tag"):
                release_channel.cleanup_development_release(
                    "resolver-plugins/plugins", "pkg-26.7"
                )

        run.assert_not_called()

    def test_pull_request_release_cleanup_lists_and_deletes_exact_matches(self) -> None:
        commands: list[list[str]] = []

        def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            if command[:3] == ['gh', 'api', '--paginate']:
                if '/releases?' in command[-1]:
                    payload = [[{'tag_name': tag} for tag in (
                        'pr-51-26.7', 'pr-51-26.1', 'pr-510-26.7', 'pr-51-26.7-extra',
                        'pkg-26.7', 'os-bind-rp-26.7-1.36_2',
                    )]]
                else:
                    payload = [[{'ref': ref} for ref in (
                        'refs/tags/pr-51-27.1', 'refs/tags/pr-51-26.7',
                        'refs/tags/pr-510-26.7', 'refs/tags/pkg-26.7',
                    )]]
                return subprocess.CompletedProcess(command, 0, json.dumps(payload), '')
            commands.append(command)
            if command[:2] == ['gh', 'release'] and command[3] == 'pr-51-27.1':
                return subprocess.CompletedProcess(command, 1, '', 'release not found')
            if command[:3] == ['gh', 'api', '--method'] and not command[-1].endswith('pr-51-27.1'):
                return subprocess.CompletedProcess(command, 1, '', 'gh: Not Found (HTTP 404)')
            return subprocess.CompletedProcess(command, 0, '', '')

        with patch.object(release_channel.subprocess, 'run', side_effect=fake_run):
            release_channel.cleanup_pull_request_releases('resolver-plugins/plugins', '51')

        expected = []
        for tag in ('pr-51-26.1', 'pr-51-26.7', 'pr-51-27.1'):
            expected.extend([
                ['gh', 'release', 'delete', tag, '--yes', '--repo', 'resolver-plugins/plugins'],
                ['gh', 'api', '--method', 'DELETE', f'repos/resolver-plugins/plugins/git/refs/tags/{tag}'],
            ])
        self.assertEqual(expected, commands)

class SelfContainedRepositoryStageTest(unittest.TestCase):
    def test_stage_channel_contains_the_plugin_bind_pair_and_audit_manifest(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            creator = package_creator()
            packages = bind_channel_inputs(root / 'packages')
            target_metadata = write_target_metadata(root / 'target-pkg.json')
            key = root / 'private.pem'
            key.touch()

            def fake_repo(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                Path(command[2]).joinpath("meta.conf").touch()
                return subprocess.CompletedProcess(command, 0)

            with (
                patch.object(
                    release_channel,
                    "validate_channel_package_manifests",
                    return_value="FreeBSD:15:amd64",
                ),
                patch.object(release_channel.subprocess, "run", side_effect=fake_repo),
            ):
                assets = release_channel.stage_channel_repository(
                    packages,
                    root / "channel",
                    key,
                    "pkg",
                    target_metadata,
                    "c" * 40,
                )

            names = {asset.name for asset in assets}
            self.assertEqual(
                {
                    "bind-tools-9.20.26_1.pkg",
                    "bind920-9.20.26_1.pkg",
                    "os-bind-rp-26.7_1.pkg",
                    "bind920-provenance.json",
                    "build-metadata.txt",
                    "channel.json",
                    "meta.conf",
                },
                names,
            )
            manifest = json.loads((root / "channel/channel.json").read_text(encoding="utf-8"))
            self.assertEqual(4, manifest["schema"])
            self.assertEqual("c" * 40, manifest["control_commit"])
            self.assertEqual("26.7", manifest["series"])
            self.assertEqual("FreeBSD:15:amd64", manifest["package_abi"])
            self.assertEqual("26.7_1", manifest["plugin_version"])
            self.assertEqual("a" * 40, manifest["source_commit"])
            self.assertEqual("f" * 64, manifest["bind"]["fingerprint"])
            self.assertEqual("15.1", manifest["build"]["freebsd_release"])
            self.assertEqual("26.7.1", manifest["build"]["tools_tag"])
            self.assertEqual(creator, manifest["package_creator"])
            self.assertEqual(
                hashlib.sha256((packages / "os-bind-rp-26.7_1.pkg").read_bytes()).hexdigest(),
                manifest["packages"]["os-bind-rp-26.7_1.pkg"],
            )
            (root / "channel/resolver-plugins.pub").write_text("public key", encoding="utf-8")
            (root / "channel/packagesite.pkg").touch()
            release_channel.validate_channel_directory(root / "channel")

            legacy_v3_manifest = dict(manifest, schema=3)
            legacy_v3_manifest.pop("control_commit")
            (root / "channel/channel.json").write_text(
                json.dumps(legacy_v3_manifest), encoding="utf-8"
            )
            release_channel.validate_channel_directory(root / "channel")

            legacy_v2_manifest = dict(legacy_v3_manifest, schema=2)
            legacy_v2_manifest.pop("package_abi")
            (root / "channel/channel.json").write_text(
                json.dumps(legacy_v2_manifest), encoding="utf-8"
            )
            release_channel.validate_channel_directory(root / "channel")

    def test_staging_rejects_a_common_abi_that_differs_from_the_trusted_target(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            packages = bind_channel_inputs(root / 'packages')
            target_metadata = write_target_metadata(root / 'target-pkg.json')
            key = root / 'private.pem'
            key.touch()
            with patch.object(
                release_channel,
                "validate_channel_package_manifests",
                return_value="FreeBSD:14:amd64",
            ), patch.object(release_channel, "stage_selected_repository") as sign:
                with self.assertRaisesRegex(ValueError, "trusted target package profile"):
                    release_channel.stage_channel_repository(
                        packages,
                        root / "channel",
                        key,
                        "pkg",
                        target_metadata,
                        "c" * 40,
                    )
                sign.assert_not_called()

    def test_asset_order_puts_repository_metadata_after_packages(self) -> None:
        """Publishing a self-contained channel uploads packages before catalog metadata."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            for name in (
                "bind-tools-9.20.26_1.pkg",
                "bind920-9.20.26_1.pkg",
                "os-bind-rp-1.36_2.pkg",
                "build-metadata.txt",
                "data.pkg",
                "meta.conf",
                "resolver-plugins.pub",
            ):
                (directory / name).touch()
            ordered = [path.name for path in release_channel.asset_order(directory)]
            self.assertEqual({p.name for p in directory.iterdir()}, set(ordered))
            self.assertLess(max(ordered.index(name) for name in (
                "bind-tools-9.20.26_1.pkg", "bind920-9.20.26_1.pkg", "os-bind-rp-1.36_2.pkg")),
                min(ordered.index(name) for name in ("build-metadata.txt", "data.pkg", "resolver-plugins.pub")))
            self.assertEqual("meta.conf", ordered[-1])


class AbiStaticPublicationTest(unittest.TestCase):
    def _channel(self, root: Path) -> Path:
        channel = root / "channel"
        channel.mkdir()
        (channel / "channel.json").write_text(
            json.dumps({"series": "26.7", "package_abi": "FreeBSD:15:amd64"}),
            encoding="utf-8",
        )
        (channel / "meta.conf").write_bytes(b"meta")
        (channel / "packagesite.pkg").write_bytes(b"catalogue")
        (channel / "os-bind-rp-1.36_7.pkg").write_bytes(b"package")
        return channel

    def test_publication_replaces_only_the_channel_declared_series_abi_path(self) -> None:
        """A static publish cannot remove sibling ABI or same-ABI series bytes."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            channel = self._channel(root)
            old_head = "1" * 40
            calls: list[tuple[list[str], object | None]] = []
            blob_number = 0

            def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
                nonlocal blob_number
                payload = json.loads(str(kwargs["input"])) if kwargs.get("input") else None
                calls.append((command, payload))
                endpoint = command[-1]
                method = command[command.index("--method") + 1] if "--method" in command else "GET"
                if endpoint.endswith("/git/ref/heads/gh-pages") and method == "GET":
                    response = {"object": {"sha": old_head}}
                elif endpoint.endswith(f"/git/commits/{old_head}"):
                    response = {"tree": {"sha": "2" * 40}}
                elif endpoint.endswith("/git/trees/" + "2" * 40 + "?recursive=1"):
                    response = {
                        "truncated": False,
                        "tree": [
                            {"path": "pages-health", "mode": "100644", "type": "blob", "sha": "3" * 40},
                            {"path": "pkg/FreeBSD:14:amd64/26.1/latest/meta.conf", "mode": "100644", "type": "blob", "sha": "4" * 40},
                            {"path": "pkg/FreeBSD:15:amd64/26.7/latest/obsolete.pkg", "mode": "100644", "type": "blob", "sha": "5" * 40},
                            {"path": "pkg/FreeBSD:15:amd64/27.1/latest/meta.conf", "mode": "100644", "type": "blob", "sha": "6" * 40},
                        ],
                    }
                elif endpoint.endswith("/git/blobs"):
                    blob_number += 1
                    response = {"sha": f"{blob_number + 5:x}" * 40}
                elif endpoint.endswith("/git/trees"):
                    response = {"sha": "a" * 40}
                elif endpoint.endswith("/git/commits"):
                    response = {"sha": "b" * 40}
                elif endpoint.endswith("/git/refs/heads/gh-pages") and method == "PATCH":
                    response = {"object": {"sha": "b" * 40}}
                else:  # pragma: no cover - makes unexpected API calls diagnostic
                    raise AssertionError(command)
                return subprocess.CompletedProcess(command, 0, json.dumps(response), "")

            with (
                patch.object(release_channel, "validate_channel_directory"),
                patch.object(release_channel.subprocess, "run", side_effect=fake_run),
            ):
                release_channel.publish_abi_channel(
                    "resolver-plugins/repository", channel, root / "recovery"
                )

            tree_payload = next(
                payload
                for command, payload in calls
                if command[-1].endswith("/git/trees") and payload is not None
            )
            self.assertEqual("2" * 40, tree_payload["base_tree"])
            changes = tree_payload["tree"]
            self.assertIn(
                {"path": "pkg/FreeBSD:15:amd64/26.7/latest/obsolete.pkg", "mode": "100644", "type": "blob", "sha": None},
                changes,
            )
            self.assertFalse(
                any(change["path"].startswith("pkg/FreeBSD:14:amd64/") for change in changes)
            )
            self.assertFalse(
                any(change["path"].startswith("pkg/FreeBSD:15:amd64/27.1/") for change in changes)
            )
            self.assertEqual(
                {f"pkg/FreeBSD:15:amd64/26.7/latest/{path.name}" for path in channel.iterdir()},
                {change["path"] for change in changes if change.get("sha") is not None},
            )
            self.assertEqual(old_head + "\n", (root / "recovery/gh-pages-head.txt").read_text())
            commit = next(payload for command, payload in calls
                          if command[-1].endswith('/git/commits') and payload is not None)
            self.assertEqual('a' * 40, commit['tree'])
            self.assertEqual([old_head], commit['parents'])
            updates = [payload for command, payload in calls if '--method' in command
                       and command[command.index('--method') + 1] == 'PATCH']
            self.assertEqual([{'sha': 'b' * 40, 'force': False}], updates)

    def test_publication_rejects_a_changed_head_before_ref_update(self) -> None:
        """A concurrent gh-pages publisher must win instead of losing its update."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            channel = self._channel(root)
            heads = iter(("1" * 40, "9" * 40))
            ref_updates: list[list[str]] = []

            def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                endpoint = command[-1]
                method = command[command.index("--method") + 1] if "--method" in command else "GET"
                if endpoint.endswith("/git/ref/heads/gh-pages") and method == "GET":
                    response = {"object": {"sha": next(heads)}}
                elif endpoint.endswith("/git/commits/" + "1" * 40):
                    response = {"tree": {"sha": "2" * 40}}
                elif endpoint.endswith("/git/trees/" + "2" * 40 + "?recursive=1"):
                    response = {"truncated": False, "tree": []}
                elif endpoint.endswith("/git/blobs"):
                    response = {"sha": "6" * 40}
                elif endpoint.endswith("/git/trees"):
                    response = {"sha": "7" * 40}
                elif endpoint.endswith("/git/commits"):
                    response = {"sha": "8" * 40}
                elif method == "PATCH":
                    ref_updates.append(command)
                    response = {}
                else:  # pragma: no cover
                    raise AssertionError(command)
                return subprocess.CompletedProcess(command, 0, json.dumps(response), "")

            with (
                patch.object(release_channel, "validate_channel_directory"),
                patch.object(release_channel.subprocess, "run", side_effect=fake_run),
                self.assertRaisesRegex(RuntimeError, "gh-pages head changed"),
            ):
                release_channel.publish_abi_channel(
                    "resolver-plugins/repository", channel, root / "recovery"
                )
            self.assertEqual([], ref_updates)


class PublicationRecoveryTest(unittest.TestCase):

    def test_immutable_release_retry_states(self):
        assets = {'os-bind-rp-1.36_2.pkg': b'plugin', 'build-metadata.txt': b'metadata'}
        repository, tag, title = 'resolver-plugins/plugins', 'os-bind-rp-26.7-1.36_2', 'os-bind-rp 26.7 1.36_2'
        for state in ('identical', 'different', 'mutable', 'absent', 'complete-draft', 'partial-draft'):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                staged = make_assets(root / 'staged', assets)
                remote_assets = assets if state not in ('different', 'partial-draft') else {'os-bind-rp-1.36_2.pkg': b'old'}
                existing = make_snapshot(root / 'remote', tag, remote_assets if state != 'absent' else None,
                                         existed=state != 'absent', draft=state.endswith('-draft'),
                                         immutable=state == 'identical')
                published = make_snapshot(staged, tag, immutable=True)
                with patch.object(release_channel, 'snapshot_release', side_effect=[existing, published]) as read, \
                     patch.object(release_channel, 'run_gh') as run:
                    if state in ('different', 'mutable'):
                        with self.assertRaisesRegex(RuntimeError, 'different bytes' if state == 'different' else 'not immutable'):
                            release_channel.publish_immutable_release(repository, tag, staged, title)
                    else:
                        release_channel.publish_immutable_release(repository, tag, staged, title)
                create = ['release', 'create', tag, str(staged / 'os-bind-rp-1.36_2.pkg'),
                          str(staged / 'build-metadata.txt'), '--repo', repository,
                          '--title', title, '--latest=false']
                expected = {
                    'identical': [], 'different': [], 'mutable': [], 'absent': [create],
                    'complete-draft': [['release', 'edit', tag, '--draft=false', '--latest=false', '--repo', repository]],
                    'partial-draft': [['release', 'delete', tag, '--yes', '--repo', repository], create],
                }[state]
                self.assertEqual(expected, [call.args[0] for call in run.call_args_list])


    def test_immutable_publication_rejects_invalid_readback(self):
        for fault, error in [('draft', 'remains a draft'), ('mutable', 'not immutable'), ('bytes', 'different bytes')]:
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                staged = make_assets(root / 'staged', {'asset.pkg': b'expected'})
                absent = make_snapshot(root / 'absent', 'source-release', existed=False)
                readback = make_snapshot(root / 'remote', absent.tag,
                                         {'asset.pkg': b'changed' if fault == 'bytes' else b'expected'},
                                         draft=fault == 'draft', immutable=fault != 'mutable')
                with patch.object(release_channel, 'snapshot_release', side_effect=[absent, readback]), \
                     patch.object(release_channel, 'run_gh'):
                    with self.assertRaisesRegex(RuntimeError, error):
                        release_channel.publish_immutable_release('example/plugins', absent.tag, staged, 'Fixture')

    def test_existing_snapshot_is_materialized_for_an_exact_release_retry(self) -> None:
        """A published version is reused instead of rebuilt under a new control commit."""
        for existed in (False, True):
            with self.subTest(snapshot_exists=existed), tempfile.TemporaryDirectory() as temporary_directory:
                root = Path(temporary_directory)
                remote = root / "remote"
                remote.mkdir()
                (remote / "channel.json").write_text(
                    json.dumps(
                        {
                            "bind": bind_provenance_record(),
                            "package_creator": target_creator_record(),
                            "series": "26.7",
                            "plugin_version": "1.36_2",
                            "source_commit": "a" * 40,
                        }
                    ),
                    encoding="utf-8",
                )
                (remote / "os-bind-rp-1.36_2.pkg").write_bytes(b"immutable")
                public_key = root / "resolver-plugins.pub"
                public_key.write_bytes(b"trusted key")
                (remote / public_key.name).write_bytes(public_key.read_bytes())
                fingerprint = bind_provenance_record()["fingerprint"]
                snapshot = release_channel.ReleaseSnapshot(
                    f"pkg-26.7-os-bind-rp-1.36_2-bind-{fingerprint}",
                    existed,
                    remote,
                    root / "manifest.json",
                )

                with (
                    patch.object(
                        release_channel, "snapshot_release", return_value=snapshot
                    ) as snapshot_release,
                    patch.object(release_channel, "validate_channel_directory") as validate,
                ):
                    reused = release_channel.materialize_existing_snapshot(
                        "resolver-plugins/repository",
                        "26.7",
                        "1.36_2",
                        "a" * 40,
                        root / "repository",
                        public_key,
                        target_creator_record(),
                        bind_provenance_record(),
                    )

                self.assertEqual(existed, reused)
                self.assertEqual(
                    (
                        "resolver-plugins/repository",
                        f"pkg-26.7-os-bind-rp-1.36_2-bind-{fingerprint}",
                    ),
                    snapshot_release.call_args.args[:2],
                )
                if not existed:
                    self.assertFalse((root / "repository").exists())
                    continue
                validate.assert_called_once_with(remote)
                for channel in ("current", "snapshot"):
                    self.assertEqual(
                        b"immutable",
                        (root / "repository" / channel / "os-bind-rp-1.36_2.pkg").read_bytes(),
                    )

    def test_snapshot_reuse_rejects_different_release_inputs(self):
        for field, changed, keyword in [
            ('source_commit', 'b' * 40, 'source'),
            ('bind', bind_provenance_record('b' * 64), 'bind'),
            ('package_creator', target_creator_record('b' * 64), 'creator'),
        ]:
            with self.subTest(input=keyword), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                manifest = dict(bind=bind_provenance_record(), package_creator=target_creator_record(),
                                series='26.7', plugin_version='1.36_2', source_commit='a' * 40)
                manifest[field] = changed
                snapshot = make_snapshot(root / 'remote', 'pkg-26.7-os-bind-rp-1.36_2', {
                    'channel.json': json.dumps(manifest).encode(), 'resolver-plugins.pub': b'trusted key'})
                public_key = root / 'resolver-plugins.pub'
                public_key.write_bytes(b'trusted key')
                with patch.object(release_channel, 'snapshot_release', return_value=snapshot), \
                     patch.object(release_channel, 'validate_channel_directory'):
                    with self.assertRaisesRegex(ValueError, 'does not match requested release'):
                        release_channel.materialize_existing_snapshot(
                            'resolver-plugins/repository', '26.7', '1.36_2', 'a' * 40,
                            root / 'repository', public_key, target_creator_record(), bind_provenance_record())
                self.assertFalse((root / 'repository').exists())


    def test_recovery_channel_rejects_an_audit_checksum_mismatch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = bind_channel_inputs(root / 'channel')
            write_json(directory / 'channel.json', dict(
                schema=1, series='26.7', plugin_version='26.7_1', source_commit='a' * 40,
                build={}, bind={}, packages={p.name: '0' * 64 for p in directory.glob('*.pkg')}))
            for name in ('resolver-plugins.pub', 'meta.conf', 'packagesite.pkg'):
                (directory / name).touch()
            with self.assertRaisesRegex(ValueError, 'checksum'):
                release_channel.validate_channel_directory(directory)

    def test_failed_promotion_restores_all_channels_from_preserved_bytes(self) -> None:
        """A failed later upload restores already changed releases without remote downloads."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            snapshot = make_assets(root / 'snapshot', {'os-bind-rp-1.36_1.pkg': b'snapshot-new'})
            latest = make_assets(root / 'latest', {'os-bind-rp-1.36_2.pkg': b'latest-new'})
            remote = {'pkg-26.7': {'old.pkg': b'pkg-26.7-old'}}
            operations = []

            def fake_snapshot(repository: str, tag: str, recovery: Path):
                if '-os-bind-rp-' in tag:
                    return make_snapshot(recovery / tag, tag, existed=False)
                return make_snapshot(recovery / tag, tag, {'old.pkg': f'{tag}-old'.encode()})

            def fake_publish(repository: str, tag: str, directory: Path, prerelease: bool) -> None:
                operations.append(tag)
                remote[tag] = {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()}
                if directory == latest:
                    raise RuntimeError("latest upload failed")

            def fake_delete(arguments, **kwargs):
                self.assertEqual(['gh', 'release', 'delete', 'pkg-26.7-os-bind-rp-1.36_2',
                                  '--yes', '--repo', 'resolver-plugins/plugins'], arguments)
                operations.append(arguments[3])
                remote.pop(arguments[3])
                return subprocess.CompletedProcess(arguments, 0)

            with (
                patch.object(release_channel, "snapshot_release", side_effect=fake_snapshot),
                patch.object(release_channel, "validate_channel_directory"),
                patch.object(
                    release_channel, "staged_source_descends_from_current", return_value=True
                ),
                patch.object(release_channel, "publish", side_effect=fake_publish),
                patch.object(release_channel.subprocess, "run", side_effect=fake_delete),
            ):
                with self.assertRaisesRegex(RuntimeError, "latest upload failed"):
                    release_channel.publish_channels(
                        "resolver-plugins/plugins",
                        [("pkg-26.7-os-bind-rp-1.36_2", snapshot), ("pkg-26.7", latest)],
                        root / "recovery",
                    )

            self.assertEqual({'pkg-26.7': {'old.pkg': b'pkg-26.7-old'}}, remote)
            self.assertEqual(['pkg-26.7', 'pkg-26.7-os-bind-rp-1.36_2'], operations[-2:])

    def test_immutable_retry_reuses_identical_bytes_and_rejects_changed_bytes(self):
        for changed in (False, True):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                staged = make_assets(root / 'staged', {'asset.pkg': b'new' if changed else b'immutable'})
                current = make_assets(root / 'current', {'asset.pkg': b'current'})
                immutable = make_snapshot(root / 'remote', 'pkg-26.7-os-bind-rp-1.36_2',
                                          {'asset.pkg': b'immutable'})
                absent = make_snapshot(root / 'missing', 'pkg-26.7', existed=False)
                published = []
                with (
                    patch.object(release_channel, 'snapshot_release',
                                 side_effect=lambda repository, tag, recovery: immutable if tag == immutable.tag else absent),
                    patch.object(release_channel, 'publish',
                                 side_effect=lambda repository, tag, directory, prerelease: published.append(tag)),
                    patch.object(release_channel, 'run_gh') as mutate,
                ):
                    with (self.assertRaisesRegex(RuntimeError, 'different bytes') if changed else nullcontext()):
                        release_channel.publish_channels('resolver-plugins/repository',
                                                         [(immutable.tag, staged), (absent.tag, current)],
                                                         root / 'recovery')
                self.assertEqual([] if changed else [absent.tag], published)
                if changed:
                    mutate.assert_not_called()

    def test_retry_only_updates_titles_when_snapshot_and_current_are_identical(self) -> None:
        """Retry updates titles only after unchanged real snapshot bytes pass preflight."""
        for changed in (False, True):
            with self.subTest(changed_preflight=changed), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                channels = []
                snapshots = []
                for tag in ('pkg-26.7-os-bind-rp-1.36_2', 'pkg-26.7'):
                    staged = make_assets(root / f'staged-{tag}', {'asset.pkg': tag.encode()})
                    channels.append((tag, staged))
                    snapshots.append(make_snapshot(root / f'remote-{tag}', tag, {'asset.pkg': tag.encode()}))
                preflight_current = (make_snapshot(root / 'changed-current', 'pkg-26.7', {'asset.pkg': b'changed'})
                                     if changed else snapshots[1])
                mutations: list[list[str]] = []
                with (
                    patch.object(release_channel, 'snapshot_release', side_effect=[*snapshots, snapshots[0], preflight_current]),
                    patch.object(release_channel, 'validate_channel_directory'),
                    patch.object(release_channel, 'publish') as publish,
                    patch.object(release_channel, 'run_gh', side_effect=mutations.append),
                ):
                    with (self.assertRaisesRegex(RuntimeError, 'changed during publication preflight')
                          if changed else nullcontext()):
                        release_channel.publish_channels('resolver-plugins/repository', channels, root / 'recovery')

                publish.assert_not_called()
                if changed:
                    self.assertEqual([], mutations)
                else:
                    self.assertEqual([['release', 'edit', tag] for tag, _ in channels],
                                     [command[:3] for command in mutations])

    def test_source_and_control_ancestry(self):
        def manifest(source, control=None):
            data = dict(schema=4 if control else 3, source_commit=source * 40)
            if control:
                data['control_commit'] = control * 40
            return data

        for name, current, staged, statuses, expected, pairs in [
            ('schema-migration', manifest('a'), manifest('a', 'c'), [], True, []),
            ('control-advance', manifest('a', 'b'), manifest('a', 'c'), [0], True, [('b', 'c')]),
            ('identical-inputs', manifest('a', 'b'), manifest('a', 'b'), [], False, []),
            ('control-rollback', manifest('a', 'd'), manifest('b', 'c'), [0, 1], False, [('a', 'b'), ('d', 'c')]),
            ('schema-downgrade', manifest('a', 'c'), manifest('b'), [], False, []),
        ]:
            with self.subTest(case=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                for directory, data in [('current', current), ('staged', staged)]:
                    make_assets(root / directory, {'channel.json': json.dumps(data).encode()})
                results = [subprocess.CompletedProcess(['git'], status) for status in statuses]
                with patch.object(release_channel.subprocess, 'run', side_effect=results) as run:
                    self.assertEqual(expected, release_channel.staged_source_descends_from_current(root / 'current', root / 'staged'))
                self.assertEqual([['git', 'merge-base', '--is-ancestor', old * 40, new * 40] for old, new in pairs],
                                 [call.args[0] for call in run.call_args_list])


    def test_stale_retry_cannot_replace_current_with_or_without_a_snapshot(self):
        for existed, error in [(False, 'stale package promotion'), (True, 'current channel has different bytes')]:
            with self.subTest(snapshot_exists=existed), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                staged_snapshot = make_assets(root / 'staged-snapshot', {'asset.pkg': b'snapshot-a'})
                staged_current = make_assets(root / 'staged-current', {'asset.pkg': b'current-a'})
                snapshot = make_snapshot(root / 'remote-snapshot', 'pkg-26.7-os-bind-rp-1.36_2',
                                         {'asset.pkg': b'snapshot-a'} if existed else None, existed=existed)
                current = make_snapshot(root / 'remote-current', 'pkg-26.7', {'asset.pkg': b'current-b'})
                def fetch(repository, tag, recovery):
                    return snapshot if tag == snapshot.tag else current
                with patch.object(release_channel, 'snapshot_release', side_effect=fetch), \
                     patch.object(release_channel, 'validate_channel_directory'), \
                     patch.object(release_channel, 'staged_source_descends_from_current', return_value=False), \
                     patch.object(release_channel, 'publish') as publish, \
                     patch.object(release_channel, 'run_gh') as mutate:
                    with self.assertRaisesRegex(RuntimeError, error):
                        release_channel.publish_channels('resolver-plugins/repository',
                            [(snapshot.tag, staged_snapshot), (current.tag, staged_current)], root / 'recovery')
                publish.assert_not_called()
                mutate.assert_not_called()


    def test_restore_absent_release_accepts_a_not_found_delete(self) -> None:
        """A failed release creation has no remote state to restore."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            snapshot = release_channel.ReleaseSnapshot("pkg-26.7-test", False, root, root / "missing.json")
            for status, error in ((0, ''), (1, 'release not found')):
                with self.subTest(error=error), patch.object(
                    release_channel.subprocess, 'run',
                    return_value=subprocess.CompletedProcess(['gh'], status, stderr=error),
                ) as run:
                    release_channel.restore_release('resolver-plugins/plugins', snapshot)
                    run.assert_called_once_with(
                        ['gh', 'release', 'delete', snapshot.tag, '--yes', '--repo', 'resolver-plugins/plugins'],
                        capture_output=True, text=True)


    def test_snapshot_pruning_keeps_the_newest_five_immutable_tags(self) -> None:
        """Only a successful promotion may remove the sixth-oldest snapshot."""
        fingerprint = "f" * 64
        releases = [
            {
                "tag_name": (
                    f"pkg-26.7-os-bind-rp-1.36_{number}-bind-{fingerprint}"
                    if number % 2 == 0
                    else f"pkg-26.7-os-bind-rp-1.36_{number}"
                ),
                "created_at": f"2026-01-0{number}T00:00:00Z",
            }
            for number in range(1, 7)
        ]
        # `gh api --paginate --slurp` returns one JSON array per fetched page.
        # Retention must therefore flatten all pages before selecting the oldest tag.
        result = subprocess.CompletedProcess(
            ["gh"], 0, stdout=json.dumps([releases[:3], releases[3:]])
        )
        deleted: list[list[str]] = []
        with (
            patch.object(release_channel.subprocess, "run", return_value=result),
            patch.object(release_channel, "run_gh", side_effect=deleted.append),
        ):
            release_channel.prune_snapshots("resolver-plugins/plugins", "26.7")

        self.assertEqual(
            [["release", "delete", "pkg-26.7-os-bind-rp-1.36_1", "--yes", "--repo", "resolver-plugins/plugins"]],
            deleted,
        )


class ChannelManifestValidationTest(unittest.TestCase):
    def test_manifest_contract(self):
        for fault, error in [
            ('valid', None), ('formula', 'dependency formula'), ('exact-edge', 'exact BIND'),
            ('version', 'plugin version does not match OPNsense series'),
            ('filename', 'filename does not match package identity'),
        ]:
            with self.subTest(fault=fault):
                identities = [
                    (('bind-tools', '9.20.26_1', 'dns/bind-tools', 'FreeBSD:15:amd64'), set()),
                    (('bind920', '9.20.26_1', 'dns/bind920', 'FreeBSD:15:amd64'),
                     {('bind-tools', 'dns/bind-tools', '9.20.26_1')}),
                    (('os-bind-rp', '26.7_1', 'opnsense/os-bind-rp', 'FreeBSD:15:amd64'), set()),
                ]
                if fault == 'exact-edge':
                    identities[2][1].add(('bind920', 'dns/bind920', '9.20.26_1'))
                if fault == 'version':
                    identities[2] = (('os-bind-rp', '1.36_2', 'opnsense/os-bind-rp', 'FreeBSD:15:amd64'), set())
                packages = [Path(f'/tmp/{name}-{version}.pkg') for (name, version, _, _), _ in identities]
                if fault == 'filename':
                    packages[2] = Path('/tmp/os-bind-rp-1.36_2.pkg')
                formula = 'bind920 = 9.20.26' if fault == 'formula' else 'bind920 >= 9.20.26'
                with patch.object(release_channel, 'read_bind_package_records', return_value=bind_records('9.20.26_1')), \
                     patch.object(release_channel, 'query_package', side_effect=identities), \
                     patch.object(release_channel, 'read_package_manifest', return_value={'dep_formula': formula}):
                    if error:
                        with self.assertRaisesRegex(ValueError, error):
                            release_channel.validate_channel_package_manifests(packages, Path('/tmp/provenance'), 'pkg', '26.7')
                    else:
                        self.assertEqual('FreeBSD:15:amd64', release_channel.validate_channel_package_manifests(
                            packages, Path('/tmp/provenance'), 'pkg', '26.7'))


class BootstrapRepositoryConfigTest(unittest.TestCase):
    def test_bootstrap_uses_the_abi_and_series_scoped_static_repository_url(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "resolver-plugins.conf"

            release_channel.write_bootstrap(
                output,
                "https://resolver-plugins.github.io/repository/",
                "26.7",
                "/usr/local/etc/pkg/keys/resolver-plugins.pub",
            )

            self.assertEqual(
                'resolver-plugins: {\n'
                '  url: "https://resolver-plugins.github.io/repository/pkg/${ABI}/26.7/latest",\n'
                '  mirror_type: "none",\n'
                '  signature_type: "pubkey",\n'
                '  pubkey: "/usr/local/etc/pkg/keys/resolver-plugins.pub",\n'
                '  enabled: yes\n'
                '}\n',
                output.read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
