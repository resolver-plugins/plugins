#!/usr/bin/env python3
"""Signed HA DHCP Interface channels; BIND channels are never selected here."""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ha_dhcp.dhcp_interface_ha_release import repository_snapshot
from shared import metadata_profile, package_checksums, target_pkg
from shared import release_channel as releases

ROOT = Path(__file__).resolve().parents[3]
PUBLIC_KEY = ROOT / "docs/package-repository/resolver-plugins.pub"
TARGET = ROOT / ".resolver-plugins/target-pkg.json"
PKG = "/usr/local/sbin/pkg-static"
NAME = "os-dhcp-interface-ha"


def metadata(directory, series, source, profile):
    data = dict(line.split("=", 1) for line in
                (directory / "build-metadata.txt").read_text().splitlines())
    if (series != "26.7" or not re.fullmatch(r"[0-9a-f]{40}", source)
            or not re.fullmatch(r"[0-9a-f]{40}", profile)
            or data["source_commit"] != source or data["profile_commit"] != profile
            or data["series"] != series
            or not re.fullmatch(re.escape(series) + r"\.[0-9]+", data.get("repository_snapshot", ""))
            or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*(?:_[0-9]+)?", data["plugin_version"])):
        raise ValueError("build metadata does not match the selected source/profile")
    return data


def tags(series, version):
    return f"pkg-dhcp-interface-ha-{series}", f"pkg-dhcp-interface-ha-{series}-{version}"


def validate(directory):
    data = json.loads((directory / "channel.json").read_text())
    build = metadata(directory, data["series"], data["source_commit"], data["profile_commit"])
    target = target_pkg.load_target(TARGET, data["series"])
    if (data["schema"] != 1 or data["plugin_version"] != build["plugin_version"]
            or data["package_creator"] != target.record()
            or (directory / "resolver-plugins.pub").read_bytes() != PUBLIC_KEY.read_bytes()):
        raise ValueError("untrusted HA DHCP Interface channel identity")
    actual = releases.directory_checksums(directory)
    actual.pop("channel.json")
    if actual != data["assets"]:
        raise ValueError("channel asset checksums differ")
    required = {f"{NAME}-{data['plugin_version']}.pkg", "build-metadata.txt", "upstream.json",
                "resolver-plugins.pub", "meta.conf"}
    if (not required.issubset(actual) or
            {name for name in actual if name.endswith('.pkg') and name not in {'data.pkg', 'packagesite.pkg'}} !=
            {f"{NAME}-{data['plugin_version']}.pkg"}):
        raise ValueError("unexpected channel package set")
    return data


def stage(directory, output, series, source, profile, trusted_upstream, key):
    build = metadata(directory, series, source, profile)
    upstream = metadata_profile.load_profile(trusted_upstream, series)
    target = target_pkg.load_target(TARGET, series)
    if ((directory / "upstream.json").read_bytes() != trusted_upstream.read_bytes()
            or build["repository_snapshot"] != repository_snapshot(series)
            or build["core_commit"] != upstream["core_commit"]
            or build["freebsd_release"] != upstream["freebsd_release"]
            or build["pkg_abi"] != target.identity.abi
            or build["pkg_creator"] != target.identity.version
            or build["pkg_creator_sha256"] != target.sha256):
        raise ValueError("build provenance differs from trusted pins")
    version = subprocess.check_output(
        ["make", "-C", str(ROOT / "net/dhcp-interface-ha"), "-V", "PLUGIN_PKGVERSION"], text=True).strip()
    if version != build["plugin_version"]:
        raise ValueError("package version differs from selected source")
    package = directory / f"{NAME}-{version}.pkg"
    identity, _ = releases.query_package(package, PKG)
    if identity != (NAME, version, f"opnsense/{NAME}", target.identity.abi):
        raise ValueError("package identity differs from selected target")
    target_pkg.verify_target_pkg(target, "pkg")
    package_checksums.verify_archive(PKG, package)
    public = subprocess.check_output(["openssl", "pkey", "-in", str(key), "-pubout"])
    if public != PUBLIC_KEY.read_bytes():
        raise ValueError("signing key does not match committed public key")
    releases.stage_selected_repository([package], output, key, PKG,
                                       [directory / "build-metadata.txt", trusted_upstream])
    shutil.copyfile(PUBLIC_KEY, output / "resolver-plugins.pub")
    channel = dict(schema=1, series=series, source_commit=source, profile_commit=profile,
                   plugin_version=version, package_creator=target.record(),
                   assets=releases.directory_checksums(output))
    (output / "channel.json").write_text(json.dumps(channel, sort_keys=True, indent=2) + "\n")
    validate(output)


def reuse(repository, directory, output, series, source, profile):
    build = metadata(directory, series, source, profile)
    _, archive = tags(series, build["plugin_version"])
    with tempfile.TemporaryDirectory() as temporary:
        snapshot = releases.snapshot_release(repository, archive, Path(temporary))
        if not snapshot.existed:
            return False
        data = validate(snapshot.directory)
        if data["source_commit"] != source or data["profile_commit"] != profile:
            raise ValueError("existing immutable version has different source/profile; bump the plugin revision")
        shutil.copytree(snapshot.directory, output)
    return True


def promote(repository, directory, recovery):
    data = validate(directory)
    current_tag, archive_tag = tags(data["series"], data["plugin_version"])
    recovery.mkdir(parents=True, exist_ok=False)
    current = releases.snapshot_release(repository, current_tag, recovery)
    archive = releases.snapshot_release(repository, archive_tag, recovery)
    if archive.existed and not releases.snapshot_matches_directory(archive, directory):
        raise RuntimeError("immutable snapshot has different bytes")
    if current.existed:
        old = validate(current.directory)
        if releases.snapshot_matches_directory(current, directory):
            if not archive.existed:
                releases.publish_immutable_release(repository, archive_tag, directory,
                                                   releases.package_release_title(archive_tag))
            return
        if old["source_commit"] == data["source_commit"] or not releases.commit_is_ancestor_or_equal(
                old["source_commit"], data["source_commit"]):
            raise RuntimeError("stale promotion cannot replace current channel")
    # Recheck remote bytes immediately before mutation, retaining recovery copies.
    with tempfile.TemporaryDirectory() as temporary:
        fresh = releases.snapshot_release(repository, current_tag, Path(temporary))
        if not releases.release_snapshots_match(current, fresh):
            raise RuntimeError("current channel changed during preflight")
    releases.publish_immutable_release(repository, archive_tag, directory,
                                       releases.package_release_title(archive_tag))
    try:
        releases.publish(repository, current_tag, directory, True)
    except Exception:
        releases.restore_release(repository, current)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["stage", "reuse", "promote", "validate"])
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--series")
    parser.add_argument("--source-commit")
    parser.add_argument("--profile-commit")
    parser.add_argument("--trusted-upstream", type=Path)
    parser.add_argument("--key", type=Path)
    parser.add_argument("--repository", default="resolver-plugins/repository")
    parser.add_argument("--recovery", type=Path)
    args = parser.parse_args()
    if args.command == "stage":
        stage(args.directory, args.output, args.series, args.source_commit, args.profile_commit,
              args.trusted_upstream, args.key)
    elif args.command == "reuse":
        print(str(reuse(args.repository, args.directory, args.output, args.series,
                        args.source_commit, args.profile_commit)).lower())
    elif args.command == "promote":
        promote(args.repository, args.directory, args.recovery)
    else:
        validate(args.directory)
