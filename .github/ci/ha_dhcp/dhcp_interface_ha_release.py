#!/usr/bin/env python3
"""Select HA DHCP releases and authenticate their reviewed BIND peers."""
import argparse
import json
import re
import subprocess
import hashlib
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bind.runtime import bind920_profile
from bind.shared import components
from shared import metadata_profile, plugin_source, upstream_inputs, series_builds

MAKEFILE = "net/dhcp-interface-ha/Makefile"
BIND_INPUT_FILES = {"runtime-approval.mk", "bind920-profile.json", "tools-profile.json"}
SOURCE_FILES = {"plugin-package.mk": MAKEFILE}


def package_version(definition, series):
    versions = re.findall(r"^PLUGIN_VERSION=\s*([0-9]+\.[0-9]+)\s*$", definition, re.M)
    revisions = re.findall(r"^PLUGIN_REVISION=\s*([0-9]+)\s*$", definition, re.M)
    names = re.findall(r"^PLUGIN_NAME=\s*(\S+)\s*$", definition, re.M)
    if versions != [series] or len(revisions) != 1 or names != ["dhcp-interface-ha"]:
        raise ValueError("HA source requires the selected series and literal package name/revision")
    return series + (f"_{int(revisions[0])}" if int(revisions[0]) else "")


def freeze_source(directory, series, source_commit, control_commit, upstream=None,
                  series_build_inputs=None, tools_profile=None):
    directory.mkdir(parents=True, exist_ok=True)
    for name, path in SOURCE_FILES.items():
        (directory / name).write_bytes(subprocess.check_output(["git", "show", f"{source_commit}:{path}"]))
    files = set(SOURCE_FILES)
    if upstream is not None:
        profile = metadata_profile.load_profile(upstream, series)
        upstream_inputs.validate_source_profile(profile, source_commit, Path.cwd())
        (directory / "upstream.json").write_bytes(upstream.read_bytes())
        files.add("upstream.json")
    if series_build_inputs is not None:
        if upstream is None or tools_profile is None:
            raise ValueError("per-series HA selection requires upstream and exact tools evidence")
        for source, name in ((series_build_inputs, series_builds.INPUT_FILE), (tools_profile, "tools-profile.json")):
            (directory / name).write_bytes(source.read_bytes())
            files.add(name)
    record = dict(schema=1, series=series, source_commit=source_commit, profile_commit=source_commit,
                  control_commit=control_commit,
                  plugin_version=package_version((directory / "plugin-package.mk").read_text(), series),
                  files={name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
                         for name in files})
    (directory / "selection.json").write_text(json.dumps(record, sort_keys=True, indent=2) + "\n")
    return hashlib.sha256((directory / "selection.json").read_bytes()).hexdigest()


def validate_source(directory, series, source_commit, control_commit, digest, repository):
    raw = (directory / "selection.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError("HA selection differs from the controller seal")
    selected = json.loads(raw)
    expected = {"schema", "series", "source_commit", "profile_commit", "control_commit", "plugin_version", "files"}
    if (set(selected) != expected or type(selected["schema"]) is not int or selected["schema"] != 1
            or selected["series"] != series or selected["source_commit"] != source_commit
            or selected["profile_commit"] != source_commit or selected["control_commit"] != control_commit
            or any(re.fullmatch(r"[0-9a-f]{40}", value or "") is None for value in (source_commit, control_commit))
            or set(selected["files"]) not in (set(SOURCE_FILES), set(SOURCE_FILES) | {"upstream.json"},
                set(SOURCE_FILES) | {"upstream.json", "tools-profile.json", series_builds.INPUT_FILE})):
        raise ValueError("HA source/control selection differs from the trusted workflow")
    command = ["git", "-C", str(repository)]
    if subprocess.check_output([*command, "rev-parse", "HEAD"], text=True).strip() != control_commit:
        raise ValueError("HA checkout differs from trusted control")
    for name, path in SOURCE_FILES.items():
        contents = (directory / name).read_bytes()
        if (hashlib.sha256(contents).hexdigest() != selected["files"][name]
                or contents != subprocess.check_output([*command, "show", f"{source_commit}:{path}"])):
            raise ValueError("HA source file differs from sealed Git provenance")
    if "upstream.json" in selected["files"]:
        upstream = directory / "upstream.json"
        if hashlib.sha256(upstream.read_bytes()).hexdigest() != selected["files"]["upstream.json"]:
            raise ValueError("HA upstream inputs differ from the controller seal")
        profile = metadata_profile.load_profile(upstream, series)
        upstream_inputs.validate_source_profile(profile, source_commit, repository)
    if series_builds.INPUT_FILE in selected["files"]:
        for name in (series_builds.INPUT_FILE, "tools-profile.json"):
            if hashlib.sha256((directory / name).read_bytes()).hexdigest() != selected["files"][name]:
                raise ValueError("HA series build inputs differ from the controller seal")
        tools = json.loads((directory / "tools-profile.json").read_text())
        metadata_profile.validate_tools_record(tools, profile)
        actual = json.loads((directory / series_builds.INPUT_FILE).read_text())
        if actual != series_builds.build_inputs(repository, "dhcp_interface_ha", series,
                                                source_commit, control_commit, profile, tools, actual.get("package_targets")):
            raise ValueError("HA per-series inputs differ from selected source/control Git objects")
        if actual.get("package_targets") is not None:
            for role in ('creator', 'reader'):
                if json.loads((directory / f'{role}-target.json').read_text()) != actual['package_targets'][role]:
                    raise ValueError('HA package targets differ from sealed per-series inputs')
    if package_version((directory / "plugin-package.mk").read_text(), series) != selected["plugin_version"]:
        raise ValueError("HA package version differs from source selection")
    return selected


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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["source-inputs", "validate-source", "materialize", "verify-package", "bind-inputs", "validate-bind-inputs",
                                           "authenticate-peers", "check-peers"])
    parser.add_argument("--series")
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--upstream", type=Path)
    parser.add_argument("--tools-repository", type=Path)
    parser.add_argument("--series-build-inputs", type=Path)
    parser.add_argument("--tools-profile", type=Path)
    parser.add_argument("--control-commit")
    parser.add_argument("--source-commit")
    parser.add_argument("--package", type=Path)
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
    if args.command != "verify-package" and (args.series is None
            or metadata_profile.SERIES_PATTERN.fullmatch(args.series) is None):
        parser.error("this command requires the selected --series")
    if args.command == "verify-package":
        if args.package is None or re.fullmatch(r"[0-9a-f]{40}", args.source_commit or "") is None:
            parser.error("package verification requires an archive and immutable source commit")
        plugin_source.verify_package_source(args.package, "dhcp-interface-ha", args.source_commit)
    elif args.command in {"source-inputs", "validate-source", "materialize"}:
        if args.directory is None or any(re.fullmatch(r"[0-9a-f]{40}", value or "") is None
               for value in (args.source_commit, args.control_commit)):
            parser.error("source commands require a trusted directory and immutable source/control commits")
        if args.command == "source-inputs":
            print(freeze_source(args.directory, args.series, args.source_commit, args.control_commit, args.upstream,
                               args.series_build_inputs, args.tools_profile))
        else:
            if args.repository_root is None or args.sha256 is None:
                parser.error("source validation requires repository root and external selection seal")
            validate_source(args.directory, args.series, args.source_commit, args.control_commit,
                            args.sha256, args.repository_root)
            if args.command == "materialize":
                plugin_source.materialize_source(args.repository_root, args.source_commit,
                    package_path="net/dhcp-interface-ha", preserve_tests=True)
    elif args.command == "authenticate-peers":
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


if __name__ == "__main__":
    main()
