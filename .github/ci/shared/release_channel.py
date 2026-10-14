#!/usr/bin/env python3
"""Shared package Release, Pages publication and recovery primitives."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bind.runtime import bind920_profile
from shared import target_pkg


SERIES_PATTERN = re.compile(r"[0-9]+\.[0-9]+")
PULL_REQUEST_PATTERN = re.compile(r"[1-9][0-9]*")
DEVELOPMENT_RELEASE_PATTERN = re.compile(r"pr-([1-9][0-9]*)-([0-9]+\.[0-9]+)(?:-bind-(?:runtime|plugin))?")
PACKAGE_VERSION_PATTERN = re.compile(r"[0-9][0-9A-Za-z._-]*")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
SERIES_PLUGIN_VERSION_PATTERN = re.compile(r"(?P<series>[0-9]+\.[0-9]+)_(?P<revision>[1-9][0-9]*)")
PACKAGE_ABI_PATTERN = re.compile(r"FreeBSD:[0-9]+:amd64")
BUILD_METADATA_FIELDS = {
    "series", "uname", "pkg_abi", "bind920", "bind_source", "opnsense",
    "opnsense_core_commit", "upstream_commit", "core_commit", "tools_tag",
    "freebsd_release", "source_commit",
    "pkg_creator", "creator_release", "creator_ports_commit", "creator_recipe_path",
}
TRUSTED_BUILD_FIELDS = {"series", "upstream_commit", "core_commit", "tools_tag", "freebsd_release"}


def channel_tag(series: str) -> str:
    """Return the fixed GitHub Release tag for a numeric OPNsense series."""
    if SERIES_PATTERN.fullmatch(series) is None:
        raise ValueError("invalid series")
    return f"pkg-{series}"


def validate_bind_fingerprint(fingerprint: object) -> str:
    """Return one canonical BIND compatibility fingerprint."""
    if not isinstance(fingerprint, str) or SHA256_PATTERN.fullmatch(fingerprint) is None:
        raise ValueError("invalid BIND fingerprint")
    return fingerprint


def validate_package_abi(abi: str) -> None:
    """Accept only package ABIs supported by the static package channel."""
    if not isinstance(abi, str) or PACKAGE_ABI_PATTERN.fullmatch(abi) is None:
        raise ValueError("invalid package ABI")


def series_abi_path(abi: str, series: str) -> str:
    """Return the ABI-and-series-indexed repository path for one channel."""
    validate_package_abi(abi)
    if not isinstance(series, str) or SERIES_PATTERN.fullmatch(series) is None:
        raise ValueError("invalid series")
    return f"pkg/{abi}/{series}/latest"


def package_release_title(tag: str) -> str:
    """Return the purpose-first display title for one package channel tag."""
    ha_channel = re.fullmatch(r"pkg-dhcp-interface-ha-(\d+\.\d+)(?:-([0-9]+(?:\.[0-9]+)*(?:_[0-9]+)?))?", tag)
    if ha_channel:
        series, version = ha_channel.groups()
        return f"os-dhcp-interface-ha {series} {version or 'latest'}"
    component = re.fullmatch(r"pkg-(\d+\.\d+)-bind-(runtime|plugin)(?:-(.+)-[0-9a-f]{64})?", tag)
    if component:
        series, kind, version = component.groups()
        return f"BIND {kind} {series} — {version or 'latest'}"
    value = tag.removeprefix("pkg-")
    if value == tag:
        raise ValueError("invalid package release tag")
    if SERIES_PATTERN.fullmatch(value) is not None:
        return f"{value}-latest"
    series, separator, version = value.partition("-os-bind-rp-")
    if (
        separator
        and SERIES_PATTERN.fullmatch(series) is not None
        and PACKAGE_VERSION_PATTERN.fullmatch(version) is not None
    ):
        return f"{series}-archive-{version}"
    raise ValueError("invalid package release tag")


def validate_bind_provenance(
    provenance: object,
    profile: object,
    series: str,
    freebsd_release: str,
    package_creator: object,
) -> dict[str, dict[str, str]]:
    """Accept only the BIND pair implied by trusted control-plane metadata."""
    if (not isinstance(provenance, dict)
            or type(provenance.get("schema")) is not int
            or provenance["schema"] not in (bind920_profile.PROVENANCE_SCHEMA, 5, 6)):
        raise ValueError("BIND provenance is invalid")
    try:
        expected = bind920_profile.build_provenance(
            profile,
            series,
            freebsd_release,
            "x86_64",
            package_creator,
            provenance["packages"],
            recipe_sha256=provenance["build_recipe_sha256"],
            build_inputs_sha256=provenance.get("build_inputs_sha256"),
            package=provenance.get("package"), tools_version=provenance.get("tools_version"),
        )
    except (KeyError, ValueError) as error:
        raise ValueError("BIND provenance is invalid") from error
    if provenance.get("fingerprint") != expected["fingerprint"]:
        raise ValueError("BIND provenance fingerprint does not match the trusted profile")
    if provenance != expected:
        raise ValueError("BIND provenance does not match the trusted profile")
    return expected["packages"]


def query_package(package: Path, pkg_command: str) -> tuple[tuple[str, str, str, str], set[tuple[str, str, str]]]:
    """Read the manifest identity and dependency edges from one package file."""
    identity = subprocess.run(
        [pkg_command, "query", "-F", str(package), "%n\t%v\t%o\t%q"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip().split("\t")
    if len(identity) != 4 or not all(identity):
        raise ValueError(f"cannot read package identity: {package.name}")
    dependencies = subprocess.run(
        [pkg_command, "query", "-F", str(package), "%dn\t%do\t%dv"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    edges = set()
    for dependency in dependencies:
        fields = dependency.split("\t")
        if len(fields) != 3 or not all(fields):
            raise ValueError(f"cannot read package dependency: {package.name}")
        edges.add(tuple(fields))
    return tuple(identity), edges


def read_package_manifest(package: Path, pkg_command: str) -> dict[str, object]:
    """Read the full archive manifest needed to validate solver formulas."""
    result = subprocess.run(
        [pkg_command, "info", "-F", str(package), "-R", "--raw-format", "json"],
        check=True,
        capture_output=True,
        text=True,
    )
    manifest = json.loads(result.stdout)
    if not isinstance(manifest, dict):
        raise ValueError(f"cannot read package manifest: {package.name}")
    if isinstance(manifest.get("files"), dict):
        manifest["files"] = {path: value.get("sum") if isinstance(value, dict) else value
                             for path, value in manifest["files"].items()}
    return manifest


def stage_selected_repository(
    packages: list[Path],
    output: Path,
    private_key: Path,
    pkg_command: str,
    metadata: list[Path],
) -> list[Path]:
    """Create one signed catalogue from already validated package inputs."""
    if not private_key.is_file():
        raise ValueError("private signing key does not exist")
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError("repository output directory must be empty")
    copied = [output / package.name for package in packages]
    for package, destination in zip(packages, copied, strict=True):
        shutil.copy2(package, destination)
    for source in metadata:
        shutil.copy2(source, output / source.name)
    subprocess.run([pkg_command, "repo", str(output), f"rsa:{private_key}"], check=True, stdout=sys.stderr)
    for alias, target in (("data.tzst", "data.pkg"), ("packagesite.tzst", "packagesite.pkg")):
        link, source = output / alias, output / target
        if link.is_symlink():
            if os.readlink(link) != target or source.is_symlink() or not source.is_file():
                raise ValueError("generated catalogue alias has an unexpected target")
            link.unlink()
            shutil.copy2(source, link)
    assets = sorted(path for path in output.iterdir() if path.is_file())
    if any(package not in assets for package in copied) or any(
        output / source.name not in assets for source in metadata
    ) or not any(path.name.startswith("meta") for path in assets):
        raise ValueError("pkg repo did not produce a repository catalog")
    return assets


def asset_order(directory: Path) -> list[Path]:
    """Order package assets before catalog assets, with meta last."""
    assets = sorted(path for path in directory.iterdir() if path.is_file())
    packages = [path for path in assets if path.suffix == ".pkg" and not path.name.startswith("data")]
    catalogs = [path for path in assets if path not in packages and not path.name.startswith("meta")]
    metadata = [path for path in assets if path.name.startswith("meta")]
    return packages + catalogs + metadata


GH_TIMEOUT_SECONDS = 300
GH_ATTEMPTS = 3


def run_gh(arguments: list[str], *, attempts: int = GH_ATTEMPTS) -> None:
    command = ["gh", *arguments]
    for attempt in range(1, attempts + 1):
        try:
            subprocess.run(command, check=True, timeout=GH_TIMEOUT_SECONDS)
            return
        except subprocess.TimeoutExpired:
            if attempt == attempts:
                raise


def download_release_asset(repository: str, tag: str, name: str, directory: Path) -> None:
    """Download one Release asset, discarding partial bytes before a retry."""
    downloaded = directory / name
    for attempt in range(1, GH_ATTEMPTS + 1):
        try:
            run_gh([
                "release", "download", tag, "--repo", repository, "--pattern", name,
                "--dir", str(directory),
            ])
            return
        except subprocess.CalledProcessError:
            downloaded.unlink(missing_ok=True)
            if attempt == GH_ATTEMPTS:
                raise


def upload_release_assets(repository: str, tag: str, assets: list[Path]) -> None:
    """Upload release assets one at a time to avoid partial multi-asset CLI failures."""
    for asset in assets:
        run_gh([
            "release", "upload", tag, str(asset),
            "--clobber", "--repo", repository,
        ])


def edit_package_release_title(repository: str, tag: str, prerelease: bool = False) -> None:
    """Converge a package Release on its purpose-first display title."""
    edit = [
        "release", "edit", tag, "--repo", repository,
        "--title", package_release_title(tag),
    ]
    if not prerelease:
        edit.append("--latest=false")
    run_gh(edit)


def sha256(path: Path) -> str:
    """Return the checksum used to verify a preserved Release asset."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def directory_checksums(directory: Path) -> dict[str, str]:
    """Checksum every flat Release asset in a staged or downloaded channel."""
    return {
        path.name: sha256(path)
        for path in directory.iterdir()
        if path.is_file()
    }


def gh_api(endpoint: str, *, method: str = "GET", payload: object | None = None) -> object:
    """Call one Git Data API endpoint with the workflow's scoped App token."""
    command = ["gh", "api"]
    if method != "GET":
        command.extend(("--method", method))
    input_text = None
    if payload is not None:
        command.extend(("--input", "-"))
        input_text = json.dumps(payload, separators=(",", ":"))
    command.append(endpoint)
    result = subprocess.run(command, text=True, capture_output=True, input=input_text)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"GitHub API request failed: {endpoint}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"GitHub API returned invalid JSON: {endpoint}") from error


def _object_sha(payload: object, *path: str) -> str:
    value = payload
    for component in path:
        if not isinstance(value, dict):
            raise RuntimeError("GitHub API response is missing an object SHA")
        value = value.get(component)
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise RuntimeError("GitHub API response is missing an object SHA")
    return value


def publish_abi_channel(repository: str, directory: Path, recovery: Path) -> None:
    """Replace one ABI subtree and compare-and-advance the gh-pages branch."""
    validate_static_channel(directory)
    try:
        channel = json.loads((directory / "channel.json").read_text(encoding="utf-8"))
        target = series_abi_path(channel["package_abi"], channel["series"])
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        raise ValueError("static channel audit metadata is invalid") from error
    assets = sorted(directory.iterdir(), key=lambda path: path.name)
    if not assets or any(not path.is_file() for path in assets):
        raise ValueError("static channel must contain only flat assets")

    ref_endpoint = f"repos/{repository}/git/ref/heads/gh-pages"
    update_ref_endpoint = f"repos/{repository}/git/refs/heads/gh-pages"
    old_head = _object_sha(gh_api(ref_endpoint), "object", "sha")
    old_tree = _object_sha(
        gh_api(f"repos/{repository}/git/commits/{old_head}"), "tree", "sha"
    )
    tree_payload = gh_api(f"repos/{repository}/git/trees/{old_tree}?recursive=1")
    if not isinstance(tree_payload, dict) or tree_payload.get("truncated") is not False:
        raise RuntimeError("cannot read the complete gh-pages tree")
    entries = tree_payload.get("tree")
    if not isinstance(entries, list):
        raise RuntimeError("cannot read the gh-pages tree")

    prefix = target + "/"
    # A missing component release must never silently remove it from the feed.
    for entry in entries:
        if isinstance(entry, dict) and entry.get("path") == prefix + "channel.json":
            blob = gh_api(f"repos/{repository}/git/blobs/{entry['sha']}")
            prior = json.loads(base64.b64decode(blob["content"]))
            if prior.get("kind") == "combined" and not set(prior["components"]).issubset(channel.get("components", {})):
                raise RuntimeError("static publication would remove an existing component")
    replacement_paths = {prefix + asset.name for asset in assets}
    changes: list[dict[str, object]] = []
    for entry in entries:
        if (
            isinstance(entry, dict)
            and isinstance(entry.get("path"), str)
            and entry["path"].startswith(prefix)
            and entry["path"] not in replacement_paths
            and entry.get("type") == "blob"
        ):
            changes.append(
                {"path": entry["path"], "mode": "100644", "type": "blob", "sha": None}
            )
    for asset in assets:
        blob = gh_api(
            f"repos/{repository}/git/blobs",
            method="POST",
            payload={
                "content": base64.b64encode(asset.read_bytes()).decode("ascii"),
                "encoding": "base64",
            },
        )
        changes.append(
            {
                "path": prefix + asset.name,
                "mode": "100644",
                "type": "blob",
                "sha": _object_sha(blob, "sha"),
            }
        )
    new_tree = _object_sha(
        gh_api(
            f"repos/{repository}/git/trees",
            method="POST",
            payload={"base_tree": old_tree, "tree": changes},
        ),
        "sha",
    )
    new_commit = _object_sha(
        gh_api(
            f"repos/{repository}/git/commits",
            method="POST",
            payload={
                "message": f"Publish package channel {channel['package_abi']} {channel['series']}",
                "tree": new_tree,
                "parents": [old_head],
            },
        ),
        "sha",
    )
    recovery.mkdir(parents=True, exist_ok=True)
    (recovery / "gh-pages-head.txt").write_text(old_head + "\n", encoding="ascii")
    current_head = _object_sha(gh_api(ref_endpoint), "object", "sha")
    if current_head != old_head:
        raise RuntimeError("gh-pages head changed during static publication")
    gh_api(update_ref_endpoint, method="PATCH", payload={"sha": new_commit, "force": False})


def verify_abi_endpoint(url: str, expected_channel: Path) -> None:
    """Require every expected static channel asset to match its public bytes."""
    validate_static_channel(expected_channel)
    assets = sorted(expected_channel.iterdir(), key=lambda path: path.name)
    if not assets or any(not path.is_file() for path in assets):
        raise ValueError("static channel must contain only flat assets")
    base_url = url.rstrip("/") + "/"
    for asset in assets:
        asset_url = urllib.parse.urljoin(base_url, urllib.parse.quote(asset.name))
        try:
            with urllib.request.urlopen(asset_url, timeout=30) as response:
                published = response.read()
        except (OSError, urllib.error.URLError) as error:
            raise RuntimeError(f"cannot fetch static channel asset: {asset.name}") from error
        if published != asset.read_bytes():
            raise RuntimeError(f"static channel asset has unexpected bytes: {asset.name}")


def validate_static_channel(directory: Path) -> None:
    data = json.loads((directory / "channel.json").read_text(encoding="utf-8"))
    if data.get("kind") in {"combined", "bind-stack"}:
        from shared import package_catalogue
        package_catalogue.validate(directory)
    elif data.get("kind") in {"bind-runtime", "bind-plugin", "bind-tools", "bind920"}:
        from bind.shared import components
        components.validate_component(directory, data["kind"].replace("-", "_"), data["series"])
    elif data.get("schema") in (2, 3) and "plugin_version" in data and "source_commit" in data:
        from ha_dhcp import dhcp_interface_ha_channel
        dhcp_interface_ha_channel.validate(directory)
    else:
        raise ValueError("unsupported static channel format")


class ReleaseSnapshot:
    """Verified local bytes required to restore one mutable GitHub Release."""

    def __init__(
        self,
        tag: str,
        existed: bool,
        directory: Path,
        manifest: Path,
        *,
        draft: bool = False,
        immutable: bool = False,
        metadata: dict | None = None,
    ) -> None:
        self.tag = tag
        self.existed = existed
        self.directory = directory
        self.manifest = manifest
        self.draft = draft
        self.immutable = immutable
        self.metadata = metadata


def release_snapshots_match(left: ReleaseSnapshot, right: ReleaseSnapshot) -> bool:
    """Return whether two observations contain the same remote Release bytes."""
    if (
        left.existed != right.existed
        or left.draft != right.draft
        or left.immutable != right.immutable
        or left.metadata != right.metadata
    ):
        return False
    if not left.existed:
        return True
    try:
        return json.loads(left.manifest.read_text(encoding="utf-8")) == json.loads(
            right.manifest.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return False


def snapshot_matches_directory(snapshot: ReleaseSnapshot, directory: Path) -> bool:
    """Return whether downloaded Release assets match all staged bytes."""
    if not snapshot.existed:
        return False
    try:
        checksums = json.loads(snapshot.manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return checksums == directory_checksums(directory)


def commit_is_ancestor_or_equal(current_commit: str, staged_commit: str) -> bool:
    """Return whether one validated commit is equal to or precedes another."""
    if current_commit == staged_commit:
        return True
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", current_commit, staged_commit],
        capture_output=True,
        text=True,
    )
    if result.returncode not in (0, 1):
        raise RuntimeError(
            result.stderr.strip() or "cannot compare package channel source history"
        )
    return result.returncode == 0


def snapshot_release(repository: str, tag: str, recovery: Path) -> ReleaseSnapshot:
    """Download and checksum every pre-promotion asset before changing a Release."""
    result = subprocess.run(
        [
            "gh", "release", "view", tag, "--repo", repository,
            "--json", "assets,isDraft,isImmutable,name,body,isPrerelease",
        ],
        capture_output=True,
        text=True,
    )
    directory = recovery / tag
    manifest = recovery / f"{tag}.json"
    if result.returncode:
        if "not found" in result.stderr.lower():
            return ReleaseSnapshot(tag, False, directory, manifest)
        raise RuntimeError(result.stderr.strip() or f"cannot inspect GitHub Release {tag}")
    payload = json.loads(result.stdout)
    assets = payload.get("assets")
    draft = payload.get("isDraft")
    immutable = payload.get("isImmutable")
    metadata = {field: payload.get(field) for field in ("name", "body", "isPrerelease")}
    if (
        not isinstance(assets, list)
        or not isinstance(draft, bool)
        or not isinstance(immutable, bool)
        or not isinstance(metadata["name"], str)
        or metadata["body"] is not None and not isinstance(metadata["body"], str)
        or not isinstance(metadata["isPrerelease"], bool)
        or not all(
            isinstance(asset, dict)
            and isinstance(asset.get("name"), str)
            and asset["name"]
            for asset in assets
        )
    ):
        raise RuntimeError(f"cannot read GitHub Release assets for {tag}")
    directory.mkdir(parents=True, exist_ok=False)
    checksums = {}
    for asset in assets:
        name = asset["name"]
        download_release_asset(repository, tag, name, directory)
        downloaded = directory / name
        if not downloaded.is_file():
            raise RuntimeError(f"cannot preserve GitHub Release asset {tag}/{name}")
        checksums[name] = sha256(downloaded)
    manifest.write_text(json.dumps(checksums, sort_keys=True) + "\n", encoding="utf-8")
    metadata["body"] = metadata["body"] or ""
    (recovery / f"{tag}-release.json").write_text(json.dumps(metadata, sort_keys=True) + "\n", encoding="utf-8")
    return ReleaseSnapshot(
        tag, True, directory, manifest, draft=draft, immutable=immutable, metadata=metadata
    )


def publish_immutable_release(
    repository: str, tag: str, directory: Path, title: str
) -> None:
    """Create an immutable Release, or accept an exact byte-for-byte retry."""
    with tempfile.TemporaryDirectory() as temporary_directory:
        existing = snapshot_release(repository, tag, Path(temporary_directory))
        if existing.existed and not existing.draft:
            if not snapshot_matches_directory(existing, directory):
                raise RuntimeError(f"immutable GitHub Release has different bytes: {tag}")
            if not existing.immutable:
                raise RuntimeError(f"GitHub Release is not immutable: {tag}")
            return
        create = not existing.existed
        if existing.existed:
            if snapshot_matches_directory(existing, directory):
                run_gh([
                    "release", "edit", tag, "--draft=false", "--latest=false",
                    "--repo", repository,
                ], attempts=1)
            else:
                run_gh([
                    "release", "delete", tag, "--yes", "--repo", repository,
                ], attempts=1)
                create = True
        if create:
            run_gh([
                "release", "create", tag,
                *(str(asset) for asset in asset_order(directory)),
                "--repo", repository, "--title", title, "--latest=false",
            ], attempts=1)

    with tempfile.TemporaryDirectory() as temporary_directory:
        published = snapshot_release(repository, tag, Path(temporary_directory))
        if published.draft:
            raise RuntimeError(f"GitHub Release remains a draft: {tag}")
        if not published.immutable:
            raise RuntimeError(f"GitHub Release is not immutable: {tag}")
        if not snapshot_matches_directory(published, directory):
            raise RuntimeError(f"published immutable GitHub Release has different bytes: {tag}")


def restore_release(repository: str, snapshot: ReleaseSnapshot) -> None:
    """Restore one channel exclusively from its verified local recovery bytes."""
    if not snapshot.existed:
        result = subprocess.run(
            ["gh", "release", "delete", snapshot.tag, "--yes", "--repo", repository],
            capture_output=True,
            text=True,
        )
        if result.returncode and "not found" not in result.stderr.lower():
            raise RuntimeError(result.stderr.strip() or f"cannot remove new Release {snapshot.tag}")
        return
    checksums = json.loads(snapshot.manifest.read_text(encoding="utf-8"))
    if not isinstance(checksums, dict) or {
        path.name for path in snapshot.directory.iterdir() if path.is_file()
    } != set(checksums) or any(
        not isinstance(digest, str) or sha256(snapshot.directory / name) != digest
        for name, digest in checksums.items()
    ):
        raise RuntimeError(f"recovery assets for {snapshot.tag} do not match their manifest")
    metadata = snapshot.metadata
    publish(repository, snapshot.tag, snapshot.directory,
            metadata["isPrerelease"] if metadata is not None else False, draft=snapshot.draft)
    if metadata is not None:
        notes = snapshot.manifest.with_name(f"{snapshot.tag}-notes.md")
        notes.write_text(metadata["body"], encoding="utf-8")
        run_gh(["release", "edit", snapshot.tag, "--repo", repository,
                "--title", metadata["name"], "--notes-file", str(notes),
                f"--prerelease={str(metadata['isPrerelease']).lower()}",
                f"--draft={str(snapshot.draft).lower()}"])
        with tempfile.TemporaryDirectory() as temporary:
            restored = snapshot_release(repository, snapshot.tag, Path(temporary))
            if not release_snapshots_match(snapshot, restored):
                raise RuntimeError(f"restored release metadata/bytes differ: {snapshot.tag}; saved at {snapshot.manifest.parent}")


def publish_snapshot(repository: str, tag: str, directory: Path) -> None:
    """Finalize exact draft bytes; published snapshots are never rewritten."""
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        before = snapshot_release(repository, tag, root / "before")
        if before.existed and not before.draft:
            if not snapshot_matches_directory(before, directory):
                raise RuntimeError(f"published snapshot has different bytes: {tag}")
            return
        if not snapshot_matches_directory(before, directory):
            publish(repository, tag, directory, False, draft=True)
        uploaded = snapshot_release(repository, tag, root / "uploaded")
        if not uploaded.draft or not snapshot_matches_directory(uploaded, directory):
            raise RuntimeError(f"snapshot draft has different bytes: {tag}")
        run_gh(["release", "edit", tag, "--draft=false", "--latest=false", "--repo", repository], attempts=1)
        completed = snapshot_release(repository, tag, root / "completed")
        if completed.draft or not snapshot_matches_directory(completed, directory):
            raise RuntimeError(f"completed snapshot has different bytes: {tag}")


def publish_channels(repository: str, groups: list[dict], recovery: Path) -> None:
    """Complete immutable snapshots, then recover all touched mutable currents."""
    fields = {"current_tag", "snapshot_tag", "directory", "validate", "can_advance"}
    if not groups or any(set(group) != fields or not callable(group["validate"])
                         or not callable(group["can_advance"]) for group in groups):
        raise ValueError("invalid trusted publication groups")
    tags = [group[key] for group in groups for key in ("current_tag", "snapshot_tag")]
    if len(tags) != len(set(tags)):
        raise ValueError("publication destinations must be distinct")
    recovery.mkdir(parents=True, exist_ok=False)
    snapshots = {tag: snapshot_release(repository, tag, recovery / "before") for tag in tags}
    for group in groups:
        directory = group["directory"]
        group["validate"](directory)
        current, snapshot = (snapshots[group[key]] for key in ("current_tag", "snapshot_tag"))
        if current.existed:
            if current.draft or current.immutable:
                raise RuntimeError(f"registered current must be mutable and published: {current.tag}")
            group["validate"](current.directory)
            if (not snapshot_matches_directory(current, directory)
                    and not group["can_advance"](current.directory, directory)):
                raise RuntimeError(f"stale package promotion cannot replace current: {current.tag}")
        if snapshot.existed and not snapshot.draft and not snapshot_matches_directory(snapshot, directory):
            raise RuntimeError(f"published snapshot has different bytes: {snapshot.tag}")
    preflight_root = recovery / "preflight"
    preflight_root.mkdir()
    preflight = {tag: snapshot_release(repository, tag, preflight_root) for tag in tags}
    if any(not release_snapshots_match(snapshots[tag], preflight[tag]) for tag in tags):
        raise RuntimeError("package channel changed during publication preflight")
    for group in groups:
        group["validate"](group["directory"])
    mutated = []
    try:
        for group in groups:
            group["validate"](group["directory"])
            publish_snapshot(repository, group["snapshot_tag"], group["directory"])
            completed = snapshot_release(repository, group["snapshot_tag"], recovery / "completed")
            if completed.draft or not snapshot_matches_directory(completed, group["directory"]):
                raise RuntimeError(f"completed snapshot readback differs: {completed.tag}")
        for group in groups:
            group["validate"](group["directory"])
            snapshot = snapshots[group["current_tag"]]
            if snapshot_matches_directory(snapshot, group["directory"]):
                continue
            mutated.append(snapshot)
            publish(repository, snapshot.tag, group["directory"], False)
            readback = snapshot_release(repository, snapshot.tag, recovery / "advanced")
            if readback.draft or not snapshot_matches_directory(readback, group["directory"]):
                raise RuntimeError(f"current readback differs: {snapshot.tag}")
    except Exception as failure:
        unfinished = {}
        for group in groups:
            try:
                state = snapshot_release(repository, group["snapshot_tag"], recovery / "unfinished")
                unfinished[state.tag] = {"existed": state.existed, "draft": state.draft}
            except Exception as error:
                unfinished[group["snapshot_tag"]] = {"read_error": str(error)}
        (recovery / "unfinished-snapshots.json").write_text(json.dumps(unfinished, indent=2) + "\n")
        restore_errors = []
        for snapshot in reversed(mutated):
            try:
                restore_release(repository, snapshot)
            except Exception as error:  # pragma: no cover - exercised against GitHub, not a fixture.
                restore_errors.append(f"{snapshot.tag}: {error}")
        (recovery / "restoration.json").write_text(json.dumps(dict(
            failure=str(failure), restored=[snapshot.tag for snapshot in reversed(mutated)],
            restore_errors=restore_errors), indent=2) + "\n")
        if restore_errors:
            raise RuntimeError(f"promotion failed: {failure}; recovery at {recovery} also failed: "
                               + "; ".join(restore_errors)) from failure
        raise


def mark_latest_package_channel(repository: str) -> None:
    """Assign GitHub's repository-wide Latest badge to the newest current series."""
    result = subprocess.run(
        ["gh", "api", "--paginate", "--slurp", f"repos/{repository}/releases?per_page=100"],
        check=True,
        capture_output=True,
        text=True,
    )
    pages = json.loads(result.stdout)
    if not isinstance(pages, list) or not all(isinstance(page, list) for page in pages):
        raise RuntimeError("cannot list package releases")
    current_channels = []
    for page in pages:
        for release in page:
            if not isinstance(release, dict) or release.get("draft") or release.get("prerelease"):
                continue
            tag = release.get("tag_name")
            series = tag.removeprefix("pkg-") if isinstance(tag, str) else ""
            if isinstance(tag, str) and tag.startswith("pkg-") and SERIES_PATTERN.fullmatch(series):
                current_channels.append((tuple(int(part) for part in series.split(".")), tag))
    if not current_channels:
        raise RuntimeError("cannot find a current package channel")
    _, tag = max(current_channels)
    run_gh(["release", "edit", tag, "--repo", repository, "--latest"])


def select_pull_request_release_tags(releases: object, pull_number: str) -> list[str]:
    """Select only complete development Release tags for one pull request."""
    if PULL_REQUEST_PATTERN.fullmatch(pull_number) is None:
        raise ValueError("invalid pull request number")
    if not isinstance(releases, list) or not all(
        isinstance(page, list) for page in releases
    ):
        raise RuntimeError("cannot list pull request releases")
    expected_pull = int(pull_number)
    selected = set()
    for page in releases:
        for release in page:
            if not isinstance(release, dict):
                continue
            tag = release.get("tag_name")
            match = DEVELOPMENT_RELEASE_PATTERN.fullmatch(tag) if isinstance(tag, str) else None
            if match is not None and int(match.group(1)) == expected_pull:
                selected.add(tag)
    return sorted(selected)


def select_pull_request_tag_refs(refs: object, pull_number: str) -> list[str]:
    """Select only complete development Git tag refs for one pull request."""
    if PULL_REQUEST_PATTERN.fullmatch(pull_number) is None:
        raise ValueError("invalid pull request number")
    if not isinstance(refs, list) or not all(isinstance(page, list) for page in refs):
        raise RuntimeError("cannot list pull request tags")
    expected_pull = int(pull_number)
    selected = set()
    prefix = "refs/tags/"
    for page in refs:
        for item in page:
            if not isinstance(item, dict):
                continue
            ref = item.get("ref")
            tag = ref.removeprefix(prefix) if isinstance(ref, str) and ref.startswith(prefix) else ""
            match = DEVELOPMENT_RELEASE_PATTERN.fullmatch(tag)
            if match is not None and int(match.group(1)) == expected_pull:
                selected.add(tag)
    return sorted(selected)


def cleanup_development_release(repository: str, tag: str) -> None:
    """Delete one exact development Release and its Git tag, if present."""
    if DEVELOPMENT_RELEASE_PATTERN.fullmatch(tag) is None:
        raise ValueError("invalid development release tag")
    result = subprocess.run(
        [
            "gh", "release", "delete", tag, "--yes", "--repo", repository,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode and "release not found" not in result.stderr.lower():
        raise RuntimeError(result.stderr.strip() or f"cannot delete development Release {tag}")
    tag_result = subprocess.run(
        [
            "gh", "api", "--method", "DELETE",
            f"repos/{repository}/git/refs/tags/{tag}",
        ],
        capture_output=True,
        text=True,
    )
    if tag_result.returncode and "(http 404)" not in tag_result.stderr.lower():
        raise RuntimeError(
            f"cannot delete development tag {tag}: "
            f"{tag_result.stderr.strip() or 'GitHub API request failed'}"
        )


def cleanup_pull_request_releases(repository: str, pull_number: str) -> None:
    """Delete every series-scoped development Release for one pull request."""
    if PULL_REQUEST_PATTERN.fullmatch(pull_number) is None:
        raise ValueError("invalid pull request number")
    result = subprocess.run(
        [
            "gh", "api", "--paginate", "--slurp",
            f"repos/{repository}/releases?per_page=100",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    try:
        releases = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("cannot list pull request releases") from error
    refs_result = subprocess.run(
        [
            "gh", "api", "--paginate", "--slurp",
            f"repos/{repository}/git/matching-refs/tags/pr-{pull_number}-",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    try:
        refs = json.loads(refs_result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("cannot list pull request tags") from error
    tags = set(select_pull_request_release_tags(releases, pull_number))
    tags.update(select_pull_request_tag_refs(refs, pull_number))
    for tag in sorted(tags):
        cleanup_development_release(repository, tag)


def publish(repository: str, tag: str, directory: Path, prerelease: bool, *, draft=False) -> None:
    """Create (if needed) and replace the assets of one GitHub Release."""
    title = package_release_title(tag)
    create = ["release", "create", tag, "--repo", repository, "--title", title]
    if prerelease:
        create.append("--prerelease")
    else:
        create.append("--latest=false")
    if draft:
        create.append("--draft")
    result = subprocess.run(["gh", *create], capture_output=True, text=True)
    if result.returncode and "already exists" not in result.stderr.lower():
        raise RuntimeError(result.stderr.strip() or "cannot create GitHub Release")
    if draft:
        with tempfile.TemporaryDirectory() as temporary:
            state = snapshot_release(repository, tag, Path(temporary))
            if not state.existed or not state.draft:
                raise RuntimeError(f"snapshot is no longer an unpublished draft: {tag}")
    edit_package_release_title(repository, tag, prerelease)
    assets = asset_order(directory)
    existing = subprocess.run(
        [
            "gh", "release", "view", tag, "--repo", repository,
            "--json", "assets", "--jq", ".assets[].name",
        ],
        capture_output=True,
        text=True,
    )
    if existing.returncode:
        raise RuntimeError(existing.stderr.strip() or "cannot list GitHub Release assets")
    asset_names = {asset.name for asset in assets}
    for asset_name in existing.stdout.splitlines():
        if asset_name not in asset_names:
            run_gh([
                "release", "delete-asset", tag, asset_name, "--yes",
                "--repo", repository,
            ])
    upload_release_assets(repository, tag, assets)
    result = subprocess.run(
        ["gh", "release", "view", tag, "--repo", repository, "--json", "assets"],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    published = payload.get("assets")
    if not isinstance(published, list) or {
        asset.get("name") for asset in published if isinstance(asset, dict)
    } != asset_names:
        raise RuntimeError(f"published GitHub Release {tag} has an unexpected asset set")
    with tempfile.TemporaryDirectory() as temporary_directory:
        verified = snapshot_release(repository, tag, Path(temporary_directory))
        if not snapshot_matches_directory(verified, directory):
            raise RuntimeError(f"published GitHub Release {tag} has unexpected asset bytes")


def write_bootstrap(output: Path, base_url: str, series: str, public_key_path: str) -> None:
    """Write the UCL configuration a client needs for a signed channel."""
    if SERIES_PATTERN.fullmatch(series) is None:
        raise ValueError("invalid series")
    url = f"{base_url.rstrip('/')}/pkg/${{ABI}}/{series}/latest"
    output.write_text(
        "resolver-plugins: {\n"
        f"  url: \"{url}\",\n"
        "  mirror_type: \"none\",\n"
        "  signature_type: \"pubkey\",\n"
        f"  pubkey: \"{public_key_path}\",\n"
        "  enabled: yes\n"
        "}\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    validate_provenance = commands.add_parser("validate-bind-provenance")
    validate_provenance.add_argument("--provenance", type=Path, required=True)
    validate_provenance.add_argument("--profile", type=Path, required=True)
    validate_provenance.add_argument("--series", required=True)
    validate_provenance.add_argument("--freebsd-release", required=True)
    validate_provenance.add_argument("--target-pkg-metadata", type=Path, required=True)
    publish_tag = commands.add_parser("publish-tag")
    publish_tag.add_argument("--repository", required=True)
    publish_tag.add_argument("--tag", required=True)
    publish_tag.add_argument("--directory", type=Path, required=True)
    publish_immutable = commands.add_parser("publish-immutable")
    publish_immutable.add_argument("--repository", required=True)
    publish_immutable.add_argument("--tag", required=True)
    publish_immutable.add_argument("--directory", type=Path, required=True)
    publish_immutable.add_argument("--title", required=True)
    publish_abi = commands.add_parser("publish-abi-channel")
    publish_abi.add_argument("--repository", required=True)
    publish_abi.add_argument("--directory", type=Path, required=True)
    publish_abi.add_argument("--recovery", type=Path, required=True)
    verify_abi = commands.add_parser("verify-abi-endpoint")
    verify_abi.add_argument("--url", required=True)
    verify_abi.add_argument("--expected-channel", type=Path, required=True)
    mark_latest = commands.add_parser("mark-latest-package-channel")
    mark_latest.add_argument("--repository", required=True)
    cleanup_tag = commands.add_parser("cleanup-tag")
    cleanup_tag.add_argument("--repository", required=True)
    cleanup_tag.add_argument("--tag", required=True)
    cleanup_pull = commands.add_parser("cleanup-pull-request")
    cleanup_pull.add_argument("--repository", required=True)
    cleanup_pull.add_argument("--pull-number", required=True)
    bootstrap = commands.add_parser("bootstrap")
    bootstrap.add_argument("--output", type=Path, required=True)
    bootstrap.add_argument("--base-url", required=True)
    bootstrap.add_argument("--series", required=True)
    bootstrap.add_argument("--public-key-path", required=True)
    arguments = parser.parse_args()
    if arguments.command == "validate-bind-provenance":
        provenance = json.loads(arguments.provenance.read_text(encoding="utf-8"))
        validate_bind_provenance(
            provenance, bind920_profile.load_profile(arguments.profile),
            arguments.series, arguments.freebsd_release,
            target_pkg.load_target(
                arguments.target_pkg_metadata, arguments.series
            ).record(),
        )
    elif arguments.command == "publish-tag":
        publish(arguments.repository, arguments.tag, arguments.directory, False)
    elif arguments.command == "publish-immutable":
        publish_immutable_release(
            arguments.repository, arguments.tag, arguments.directory, arguments.title
        )
    elif arguments.command == "publish-abi-channel":
        publish_abi_channel(arguments.repository, arguments.directory, arguments.recovery)
    elif arguments.command == "verify-abi-endpoint":
        verify_abi_endpoint(arguments.url, arguments.expected_channel)
    elif arguments.command == "mark-latest-package-channel":
        mark_latest_package_channel(arguments.repository)
    elif arguments.command == "cleanup-tag":
        cleanup_development_release(arguments.repository, arguments.tag)
    elif arguments.command == "cleanup-pull-request":
        cleanup_pull_request_releases(arguments.repository, arguments.pull_number)
    else:
        write_bootstrap(
            arguments.output, arguments.base_url, arguments.series, arguments.public_key_path
        )


if __name__ == "__main__":
    raise SystemExit(main())
