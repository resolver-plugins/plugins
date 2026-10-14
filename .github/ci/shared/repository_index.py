#!/usr/bin/env python3
"""Refresh the shared signed index from already published package records."""
import argparse
import base64
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bind.shared import components as bind
from shared import metadata_profile, package_catalogue as catalogue, package_checksums
from shared import release_channel as releases, target_pkg, upstream_inputs

FILES = {"upstream.json", "tools-profile.json", "creator-target.json", "reader-target.json"}
ORDER = ("bind_tools", "bind920", "bind_plugin", "dhcp", "bind_runtime")


def selected_records(receipts):
    """Standalone runtime packages supersede their legacy pair individually."""
    records, seen = {}, set()
    for component in ORDER:
        receipt = receipts.get(component)
        if receipt is None:
            continue
        bind.validate_receipt(receipt, component, receipt["series"])
        selected = {}
        for name, digest in receipt["packages"].items():
            package = name.rsplit("-", 1)[0]
            if package not in seen:
                selected[name] = digest
                seen.add(package)
        if selected:
            records[component] = dict(catalogue.component_record(receipt), selected_packages=selected)
    return records


def validate_inputs(directory, digest, *, prefix="", expected_control=None):
    path = directory / "index-inputs.json"
    if path.is_symlink() or releases.sha256(path) != digest:
        raise ValueError("index inputs differ from controller seal")
    inputs = bind._json(path)
    if (set(inputs) != {"schema", "series", "control_commit", "source_commit", "components", "files"}
            or type(inputs["schema"]) is not int or inputs["schema"] != 1
            or set(inputs["components"]) != set(ORDER) or set(inputs["files"]) != FILES):
        raise ValueError("invalid index selection")
    bind._commit(inputs["control_commit"])
    bind._commit(inputs["source_commit"])
    if expected_control is not None and inputs["control_commit"] != expected_control:
        raise ValueError("index control differs from executing trusted workflow")
    for name, value in bind._hashes(inputs["files"]).items():
        if releases.sha256(directory / (prefix + name)) != value:
            raise ValueError("index roles/environment differ from controller selection")
    profile = metadata_profile.load_profile(directory / (prefix + "upstream.json"), inputs["series"])
    metadata_profile.validate_tools_record(bind._json(directory / (prefix + "tools-profile.json")), profile)
    for role in ("creator", "reader"):
        bind._role(bind._json(directory / (prefix + f"{role}-target.json")), profile, role)
    records = selected_records(inputs["components"])
    if not records or any(receipt is not None and (receipt["series"] != inputs["series"] or
            receipt["package_abi"] != f"FreeBSD:{profile['freebsd_release'].split('.')[0]}:amd64")
            for receipt in inputs["components"].values()):
        raise ValueError("index package series/ABI differs from selected environment")
    return inputs


def prepare(repository, directory, series, control):
    if subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip() != bind._commit(control):
        raise ValueError("index must execute its selected control checkout")
    releases.channel_tag(series)
    directory.mkdir(parents=True)
    receipts = {}
    originals = directory / "originals"
    originals.mkdir()
    for component in ORDER:
        if component == "bind_runtime" and receipts.get("bind_tools") and receipts.get("bind920"):
            receipts[component] = None
            continue
        receipts[component] = catalogue.fetch(repository, component, series, originals / component)
    records = selected_records(receipts)
    if not records:
        return dict(changed=False, selection_sha256="", freebsd_release="")
    first = next(receipt for receipt in receipts.values() if receipt is not None)
    endpoint = f"repos/{repository}/contents/{releases.series_abi_path(first['package_abi'], series)}/channel.json?ref=gh-pages"
    response = subprocess.run(["gh", "api", endpoint], capture_output=True, text=True)
    if response.returncode == 0:
        current = json.loads(base64.b64decode(json.loads(response.stdout)["content"]))
        registered = {kind: dict(record, selected_packages=record.get("selected_packages", record["packages"]))
                      for kind, record in current.get("components", {}).items()}
        # A missing mutable pointer must not withdraw a previously indexed package.
        # Retain its exact immutable receipt; a never-published peer stays optional.
        for component, record in registered.items():
            if receipts.get(component) is not None or component == "bind_runtime" and receipts.get("bind_tools") and receipts.get("bind920"):
                continue
            fingerprint = record["snapshot_tag"].rsplit("-", 1)[-1]
            fingerprint = fingerprint if releases.SHA256_PATTERN.fullmatch(fingerprint) else None
            retained = dict(schema=1, component=component, series=series, package_abi=current["package_abi"],
                            current_tag=record["current_tag"], snapshot_tag=record["snapshot_tag"], fingerprint=fingerprint,
                            assets=record["assets"], packages=record["packages"])
            bind.validate_receipt(retained, component, series)
            with tempfile.TemporaryDirectory(prefix="index-retained-") as temporary:
                snapshot = releases.snapshot_release(repository, retained["snapshot_tag"], Path(temporary))
                if (not snapshot.existed or snapshot.draft or catalogue.receipt(snapshot.directory, component, series) != retained):
                    raise ValueError("previously indexed immutable package is unavailable")
                shutil.copytree(snapshot.directory, originals / component)
            receipts[component] = retained
        records = selected_records(receipts)
        if registered == records:
            return dict(changed=False, selection_sha256="", freebsd_release="")
    elif "HTTP 404" not in response.stderr:
        raise RuntimeError(response.stderr.strip() or "cannot inspect registered index")
    manifest = bind._json(originals / first["component"] / "channel.json")
    source = manifest.get("source_commit", manifest.get("profile_commit"))
    bind._ensure_commits(source)
    with tempfile.TemporaryDirectory(prefix="index-discovery-") as temporary:
        tools_repository = Path(temporary) / "tools"
        profile = upstream_inputs.discover_inputs(Path.cwd(), source, series, tools_repository)
        tools = metadata_profile.verify_tools_profile(profile, tools_repository)
        (directory / "upstream.json").write_text(json.dumps(profile, sort_keys=True, indent=2) + "\n")
        (directory / "tools-profile.json").write_text(json.dumps(tools, sort_keys=True, indent=2) + "\n")
        target_pkg.resolve_targets(directory / "upstream.json", series, "x86_64", directory,
                                   tools_repository=tools_repository)
    inputs = dict(schema=1, series=series, control_commit=control, source_commit=source, components=receipts,
                  files={name: releases.sha256(directory / name) for name in sorted(FILES)})
    path = directory / "index-inputs.json"
    path.write_text(json.dumps(inputs, sort_keys=True, indent=2) + "\n")
    digest = releases.sha256(path)
    validate_inputs(directory, digest, expected_control=control)
    return dict(changed=True, selection_sha256=digest, freebsd_release=profile["freebsd_release"])


def authenticate(directory, digest, pkg_command):
    inputs = validate_inputs(directory, digest)
    if subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip() != inputs["control_commit"]:
        raise ValueError("index signer checkout differs from controller")
    bind.select_role(directory / "upstream.json", directory / "reader-target.json", inputs["series"], "reader", pkg_command)
    for component in selected_records(inputs["components"]):
        receipt = inputs["components"][component]
        original = directory / "originals" / component
        if catalogue.receipt(original, component, inputs["series"]) != receipt:
            raise ValueError("retained original package differs from frozen immutable receipt")
        if component in bind.KINDS:
            bind.validate_component(original, component, inputs["series"], pkg_command=pkg_command)
        packages = catalogue.component_packages(original, component, inputs["series"])
        for package in packages:
            if releases.query_package(package, pkg_command)[0][3] != receipt["package_abi"]:
                raise ValueError("index archive ABI differs from original receipt")
            package_checksums.verify_archive(pkg_command, package)
        catalogue.verify_packages(packages, original.resolve().as_uri(), directory / "reader-target.json",
                                  directory / "upstream.json", pkg_command=pkg_command)
    return inputs


def stage(directory, digest, output, key, pkg_command):
    inputs = authenticate(directory, digest, pkg_command)
    records = selected_records(inputs["components"])
    bind.select_role(directory / "upstream.json", directory / "creator-target.json", inputs["series"], "creator", pkg_command)
    if subprocess.check_output(["openssl", "pkey", "-in", str(key), "-pubout"]) != bind.PUBLIC_KEY.read_bytes():
        raise ValueError("index signing key differs from committed public key")
    packages = [directory / "originals" / component / name
                for component, record in records.items() for name in record["selected_packages"]]
    releases.stage_selected_repository(packages, output, key, pkg_command, [])
    shutil.copyfile(bind.PUBLIC_KEY, output / "resolver-plugins.pub")
    for component, record in records.items():
        original = directory / "originals" / component
        for name in record["assets"]:
            if name not in record["packages"] and name not in catalogue.CATALOGUE_ARCHIVES:
                shutil.copyfile(original / name, output / f"{component}-{name}")
    for name in FILES:
        shutil.copyfile(directory / name, output / ("catalogue-" + name))
    shutil.copyfile(directory / "index-inputs.json", output / "index-inputs.json")
    creator, source = bind._role(bind._json(directory / "creator-target.json"), bind._json(directory / "upstream.json"), "creator")
    data = dict(schema=3, kind="combined", series=inputs["series"], package_abi=creator["abi"],
                catalogue_creator=dict(package_creator=creator, creator_source=source), components=records,
                assets=bind._files(output))
    (output / "channel.json").write_text(json.dumps(data, sort_keys=True, indent=2) + "\n")
    catalogue.validate(output)


def publish(repository, directory, digest, control, recovery):
    inputs = validate_inputs(directory, digest, prefix="catalogue-", expected_control=control)
    if catalogue.validate(directory)["components"] != selected_records(inputs["components"]):
        raise ValueError("index differs from controller-frozen package selection")
    if subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip() != control:
        raise ValueError("index publisher differs from trusted control")
    with tempfile.TemporaryDirectory(prefix="index-readback-") as temporary:
        for component, expected in inputs["components"].items():
            if component == "bind_runtime" and expected is None and inputs["components"]["bind_tools"] and inputs["components"]["bind920"]:
                continue
            observed = catalogue.fetch(repository, component, inputs["series"], Path(temporary) / component)
            if observed is not None and observed != expected:
                raise RuntimeError("a package advanced while the index was qualified; refresh the index again")
    releases.publish_abi_channel(repository, directory, recovery)


def verify(directory, digest, pkg_command, url=None):
    inputs = validate_inputs(directory, digest, prefix="catalogue-")
    data = catalogue.validate(directory)
    upstream = directory / "catalogue-upstream.json"
    reader = directory / "catalogue-reader-target.json"
    bind.select_role(upstream, reader, inputs["series"], "reader", pkg_command)
    packages = [directory / name for record in data["components"].values() for name in record["selected_packages"]]
    for package in packages:
        package_checksums.verify_archive(pkg_command, package)
    if url:
        releases.verify_abi_endpoint(url, directory)
    catalogue.verify_packages(packages, url or directory.resolve().as_uri(), reader, upstream, pkg_command=pkg_command)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("select", "validate", "authenticate", "stage", "publish", "verify"))
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--repository", default=bind.REPOSITORY)
    parser.add_argument("--series")
    parser.add_argument("--control-commit")
    parser.add_argument("--sha256")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--key", type=Path)
    parser.add_argument("--recovery", type=Path)
    parser.add_argument("--pkg-command", default="/usr/local/sbin/pkg-static")
    parser.add_argument("--url")
    args = parser.parse_args()
    if args.command == "select":
        result = prepare(args.repository, args.directory, args.series, args.control_commit)
    elif args.command == "validate":
        result = validate_inputs(args.directory, args.sha256, prefix="catalogue-" if (args.directory / "channel.json").exists() else "",
                                 expected_control=args.control_commit)
    elif args.command == "authenticate":
        result = authenticate(args.directory, args.sha256, args.pkg_command)
    elif args.command == "stage":
        result = stage(args.directory, args.sha256, args.output, args.key, args.pkg_command)
    elif args.command == "verify":
        result = verify(args.directory, args.sha256, args.pkg_command, args.url)
    else:
        result = publish(args.repository, args.directory, args.sha256, args.control_commit, args.recovery)
    if result is not None:
        print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
