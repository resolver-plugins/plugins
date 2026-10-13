#!/usr/bin/env python3
"""Select HA DHCP releases and authenticate their reviewed BIND peers."""
import argparse
import json
import os
import re
import subprocess
import hashlib
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bind.runtime import bind920_profile
from bind.shared import components
from shared import metadata_profile

MAKEFILE = "net/dhcp-interface-ha/Makefile"
BIND_INPUT_FILES = {"runtime-approval.mk", "bind920-profile.json", "tools-profile.json"}


def freeze_bind_inputs(directory, upstream, tools_repository, control_commit):
    directory.mkdir(parents=True, exist_ok=True)
    raw = subprocess.check_output(["git", "show", f"{control_commit}:dns/bind/Makefile"])
    approval = bind920_profile.approval_from_makefile(raw.decode())
    (directory / "runtime-approval.mk").write_bytes(raw)
    records = {"bind920-profile.json": bind920_profile.resolve_profile(approval),
               "tools-profile.json": metadata_profile.verify_tools_profile(upstream, tools_repository)}
    for name, record in records.items():
        (directory / name).write_text(json.dumps(record, sort_keys=True, indent=2) + "\n")
    hashes = {name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
              for name in sorted(BIND_INPUT_FILES)}
    sealed = (json.dumps(hashes, sort_keys=True, indent=2) + "\n").encode()
    (directory / "bind-inputs.json").write_bytes(sealed)
    return hashlib.sha256(sealed).hexdigest()


def validate_bind_inputs(directory, upstream, control_commit, digest):
    sealed = (directory / "bind-inputs.json").read_bytes()
    if hashlib.sha256(sealed).hexdigest() != digest:
        raise ValueError("HA shared BIND inputs differ from the controller seal")
    hashes = json.loads(sealed)
    if set(hashes) != BIND_INPUT_FILES or any(
            hashlib.sha256((directory / name).read_bytes()).hexdigest() != hashes[name]
            for name in BIND_INPUT_FILES):
        raise ValueError("HA shared BIND file map differs from its seal")
    raw = (directory / "runtime-approval.mk").read_bytes()
    if raw != subprocess.check_output(["git", "show", f"{control_commit}:dns/bind/Makefile"]):
        raise ValueError("HA runtime approval differs from trusted control Git blob")
    approval = bind920_profile.approval_from_makefile(raw.decode())
    recipe = bind920_profile.validate_profile(json.loads((directory / "bind920-profile.json").read_text()))
    if recipe["ports_commit"] != approval["ports_commit"] or bind920_profile.package_version(recipe) != approval["version"]:
        raise ValueError("HA runtime recipe differs from sealed approval")
    metadata_profile.validate_tools_record(json.loads((directory / "tools-profile.json").read_text()), upstream)


def authenticate_peers(runtime_channel, plugin_channel, *, series, upstream,
                       reader_target, creator_target, approved_profile, runtime_approval,
                       pkg_command):
    """Authenticate retained BIND peers with the reader before selecting the creator."""
    from shared import package_catalogue

    components.select_role(upstream, reader_target, series, "reader", pkg_command)
    package_catalogue.authenticate_components(runtime_channel, plugin_channel, None,
        series=series, reader_target=reader_target, upstream=upstream, pkg_command=pkg_command,
        approved_profile=approved_profile, runtime_approval=runtime_approval)
    components.select_role(upstream, creator_target, series, "creator", pkg_command)


def check_peers(repository_root, *, series, composition_sha256, repository,
                owned_must_match=False):
    composition = components.load_composition(repository_root, component="dhcp",
        series=series, composition_sha256=composition_sha256)
    components.check_composition_peers(repository, composition, owned_must_match=owned_must_match)


def repository_snapshot(series):
    if series != "26.7":
        raise ValueError("HA DHCP releases support only OPNsense 26.7")
    # Keep ordinary framework dependencies on the reviewed snapshot.
    return "26.7.4"


def git(*args):
    return subprocess.check_output(["git", *args], text=True).strip()


def version_at(commit):
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("release selection requires an exact source commit")
    git("cat-file", "-e", f"{commit}^{{commit}}")
    if not git("ls-tree", "--name-only", commit, "--", MAKEFILE):
        return None
    source = git("show", f"{commit}:{MAKEFILE}")
    versions = re.findall(r"^PLUGIN_VERSION=\s*([0-9]+(?:\.[0-9]+)*)\s*$", source, re.M)
    revisions = re.findall(r"^PLUGIN_REVISION=\s*([0-9]+)\s*$", source, re.M)
    if len(versions) != 1 or len(revisions) != 1:
        raise ValueError("release requires literal numeric PLUGIN_VERSION and PLUGIN_REVISION")
    # pkg treats trailing zero version components and revision zero as equal.
    components = [int(part) for part in versions[0].split(".")]
    while len(components) > 1 and components[-1] == 0:
        components.pop()
    return tuple(components), int(revisions[0])


def select(event, ref, source, before=None):
    if ref != "refs/heads/master" or event not in {"push", "workflow_dispatch"}:
        raise ValueError("release requires a master push or manual master dispatch")
    current = version_at(source)
    if current is None:
        raise ValueError("selected source does not contain the HA DHCP plugin")
    if event == "workflow_dispatch":
        return True
    previous = None if before == "0" * 40 else version_at(before or "")
    if previous is not None and current < previous:
        raise ValueError("plugin version decreased; release requires a version increase")
    return current != previous


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["select", "snapshot", "bind-inputs", "validate-bind-inputs",
                                           "authenticate-peers", "check-peers"])
    parser.add_argument("--series", default="26.7")
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--upstream", type=Path)
    parser.add_argument("--tools-repository", type=Path)
    parser.add_argument("--control-commit")
    parser.add_argument("--sha256")
    parser.add_argument("--runtime-channel", type=Path)
    parser.add_argument("--plugin-channel", type=Path)
    parser.add_argument("--reader-target", type=Path)
    parser.add_argument("--creator-target", type=Path)
    parser.add_argument("--approved-profile", type=Path)
    parser.add_argument("--runtime-approval", type=Path)
    parser.add_argument("--pkg-command", default="/usr/local/sbin/pkg-static")
    parser.add_argument("--repository-root", type=Path)
    parser.add_argument("--composition-sha256")
    parser.add_argument("--repository", default="resolver-plugins/repository")
    parser.add_argument("--owned-must-match", action="store_true")
    args = parser.parse_args(argv)
    snapshot = repository_snapshot(args.series)
    if args.command == "authenticate-peers":
        if any(value is None for value in (args.runtime_channel, args.plugin_channel, args.upstream,
               args.reader_target, args.creator_target, args.approved_profile, args.runtime_approval)):
            parser.error("authenticate-peers requires both channels, upstream, both roles, approved profile and runtime approval")
        authenticate_peers(args.runtime_channel, args.plugin_channel, series=args.series,
            upstream=args.upstream, reader_target=args.reader_target, creator_target=args.creator_target,
            approved_profile=args.approved_profile, runtime_approval=args.runtime_approval,
            pkg_command=args.pkg_command)
    elif args.command == "check-peers":
        if args.repository_root is None or args.composition_sha256 is None:
            parser.error("check-peers requires repository root and composition SHA-256")
        check_peers(args.repository_root, series=args.series, composition_sha256=args.composition_sha256,
            repository=args.repository, owned_must_match=args.owned_must_match)
    elif args.command in {"bind-inputs", "validate-bind-inputs"}:
        if args.directory is None or args.upstream is None or not re.fullmatch(r"[0-9a-f]{40}", args.control_commit or ""):
            parser.error("shared BIND input commands require directory, upstream and immutable control commit")
        upstream = metadata_profile.load_profile(args.upstream, args.series)
        if args.command == "bind-inputs":
            if args.tools_repository is None:
                parser.error("bind-inputs requires tools repository")
            print(freeze_bind_inputs(args.directory, upstream, args.tools_repository, args.control_commit))
        else:
            validate_bind_inputs(args.directory, upstream, args.control_commit, args.sha256)
    elif args.command == "snapshot":
        print(snapshot)
    else:
        with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as stream:
            payload = json.load(stream)
        source = os.environ["GITHUB_SHA"]
        release = select(os.environ["GITHUB_EVENT_NAME"], os.environ["GITHUB_REF"],
                         source, payload.get("before"))
        print(f"release={str(release).lower()}\nseries={args.series}\nsource_commit={source}")


if __name__ == "__main__":
    main()
