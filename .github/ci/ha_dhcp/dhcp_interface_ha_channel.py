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
PKG = "/usr/local/sbin/pkg-static"
NAME = "os-dhcp-interface-ha"


METADATA_FIELDS = {
    "series", "source_commit", "profile_commit", "repository_snapshot", "freebsd_release",
    "core_commit", "uname", "pkg_abi", "opnsense", "plugin_version", "pkg_creator",
    "creator_release", "creator_ports_commit", "creator_recipe_path",
}


def read_metadata(path):
    data = {}
    for line in path.read_text().splitlines():
        key, separator, value = line.partition("=")
        if not separator or not value or key in data:
            raise ValueError("invalid HA build metadata")
        data[key] = value
    if set(data) != METADATA_FIELDS:
        raise ValueError("invalid HA build metadata schema")
    return data


def metadata(directory, series, source, profile):
    data = read_metadata(directory / "build-metadata.txt")
    if any(data[key] != value for key, value in (
            ("series", series), ("source_commit", source), ("profile_commit", profile))):
        raise ValueError("build metadata does not match the selected source/profile")
    return data


def validate_component(data, build, upstream):
    fields = {"schema", "series", "source_commit", "profile_commit", "plugin_version", "package_creator", "assets", "creator_source"}
    if type(data.get("schema")) is not int or data["schema"] != 2 or set(data) != fields:
        raise ValueError("invalid HA channel schema")
    series = data["series"]
    if (series != "26.7" or any(not re.fullmatch(r"[0-9a-f]{40}", data[key]) for key in ("source_commit", "profile_commit"))
            or not re.fullmatch(re.escape(series) + r"\.[0-9]+", build["repository_snapshot"])
            or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*(?:_[0-9]+)?", data["plugin_version"])
            or not re.fullmatch(re.escape(series) + r"(?:\.[0-9]+)*(?:_[0-9]+)?", build["opnsense"])):
        raise ValueError("untrusted HA DHCP Interface channel identity")
    creator = target_pkg.normalize_creator(data["package_creator"])
    if (any(data[key] != build[key] for key in ("series", "source_commit", "profile_commit", "plugin_version"))
            or upstream["series"] != build["series"] or upstream["core_commit"] != build["core_commit"]
            or upstream["freebsd_release"] != build["freebsd_release"]
            or creator["abi"] != f"FreeBSD:{upstream['freebsd_release'].split('.')[0]}:amd64"
            or creator["abi"] != build["pkg_abi"] or creator["version"] != build["pkg_creator"]):
        raise ValueError("untrusted HA DHCP Interface channel identity")
    source = data["creator_source"]
    target_pkg.validate_source(source, creator, data["series"])
    if (source["release"] != upstream["tools_tag"] or source["release"] != build["creator_release"]
            or source["commit"] != build["creator_ports_commit"]
            or source["path"] != build["creator_recipe_path"]):
        raise ValueError("creator source differs from recorded metadata")
    return creator


def validate_incoming(directory, series, source, profile, trusted_upstream, target_metadata):
    build = metadata(directory, series, source, profile)
    upstream = metadata_profile.load_profile(trusted_upstream, series)
    target = target_pkg.load_target(target_metadata, series, upstream=trusted_upstream, role="creator")
    if ((directory / "upstream.json").read_bytes() != trusted_upstream.read_bytes()
            or build["repository_snapshot"] != repository_snapshot(series)
            or build["core_commit"] != upstream["core_commit"]
            or build["freebsd_release"] != upstream["freebsd_release"]
            or build["pkg_abi"] != target.identity.abi
            or build["pkg_creator"] != target.identity.version
            or build["creator_release"] != target.release
            or build["creator_ports_commit"] != target.ports_commit
            or build["creator_recipe_path"] != target.recipe_path):
        raise ValueError("build provenance differs from trusted pins")
    return build, target


def tags(series, version):
    return f"pkg-dhcp-interface-ha-{series}", f"pkg-dhcp-interface-ha-{series}-{version}"


def validate(directory, *, target_metadata=None, trusted_upstream=None):
    data = json.loads((directory / "channel.json").read_text())
    build = metadata(directory, data["series"], data["source_commit"], data["profile_commit"])
    upstream = metadata_profile.load_profile(directory / "upstream.json", data["series"])
    creator = validate_component(data, build, upstream)
    if target_metadata is not None:
        target = target_pkg.load_target(target_metadata, data["series"], upstream=trusted_upstream, role="creator")
        if creator != target.record():
            raise ValueError("channel creator differs from trusted creator")
    if (directory / "resolver-plugins.pub").read_bytes() != PUBLIC_KEY.read_bytes():
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


def stage(directory, output, series, source, profile, trusted_upstream, key, target_metadata):
    build, target = validate_incoming(directory, series, source, profile, trusted_upstream, target_metadata)
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
    channel = dict(schema=2, creator_source=target.source_record(), series=series, source_commit=source, profile_commit=profile,
                   plugin_version=version, package_creator=target.record(),
                   assets=releases.directory_checksums(output))
    (output / "channel.json").write_text(json.dumps(channel, sort_keys=True, indent=2) + "\n")
    validate(output)


def reuse(repository, directory, output, series, source, profile, trusted_upstream, target_metadata):
    build, target = validate_incoming(directory, series, source, profile, trusted_upstream, target_metadata)
    _, archive = tags(series, build["plugin_version"])
    with tempfile.TemporaryDirectory() as temporary:
        snapshot = releases.snapshot_release(repository, archive, Path(temporary))
        if not snapshot.existed:
            return False
        data = validate(snapshot.directory)
        if (data["source_commit"] != source or data["profile_commit"] != profile
                or target_pkg.normalize_creator(data["package_creator"]) != target.record()):
            raise ValueError("existing immutable version has different source/profile or creator; bump the plugin revision")
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
                releases.publish_snapshot(repository, archive_tag, directory)
            return
        if old["source_commit"] == data["source_commit"] or not releases.commit_is_ancestor_or_equal(
                old["source_commit"], data["source_commit"]):
            raise RuntimeError("stale promotion cannot replace current channel")
    # Recheck remote bytes immediately before mutation, retaining recovery copies.
    with tempfile.TemporaryDirectory() as temporary:
        fresh = releases.snapshot_release(repository, current_tag, Path(temporary))
        if not releases.release_snapshots_match(current, fresh):
            raise RuntimeError("current channel changed during preflight")
    releases.publish_snapshot(repository, archive_tag, directory)
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
    parser.add_argument("--target-pkg-metadata", type=Path)
    parser.add_argument("--repository", default="resolver-plugins/repository")
    parser.add_argument("--recovery", type=Path)
    args = parser.parse_args()
    if args.command == "stage":
        stage(args.directory, args.output, args.series, args.source_commit, args.profile_commit,
              args.trusted_upstream, args.key, args.target_pkg_metadata)
    elif args.command == "reuse":
        print(str(reuse(args.repository, args.directory, args.output, args.series,
                        args.source_commit, args.profile_commit, args.trusted_upstream, args.target_pkg_metadata)).lower())
    elif args.command == "promote":
        promote(args.repository, args.directory, args.recovery)
    else:
        validate(args.directory, target_metadata=args.target_pkg_metadata, trusted_upstream=args.trusted_upstream)
