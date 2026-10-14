#!/usr/bin/env python3
"""Exact plugin source, frozen native build inputs and source Release assets."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from bind.shared import components
from shared import package_catalogue, package_checksums, target_pkg
from shared.plugin_source import materialize_source, verify_package_source
from shared import release_channel as releases

SOURCE_ASSETS = ("build-metadata.txt", "plugin-build-inputs.json", "upstream.json",
                 "creator-target.json", "plugin-package.mk", "channel.json")


def fetch_intake(repository, trusted, selection, output):
    """Fetch only controller-frozen snapshots; current is never consulted here."""
    output.mkdir(parents=True, exist_ok=True)
    if selection["schema"] == 1:
        receipt = json.loads((trusted / "runtime-selection.json").read_text())
        components.fetch_frozen_component(repository, receipt, output / "bind_runtime")
    if selection["producer_action"] == "reuse":
        cached = json.loads((trusted / "cached-component.json").read_text())
        components.fetch_frozen_component(repository, cached, output / "cached_component")


def installed_runtime(directory, trusted, series, pkg_command):
    if json.loads((trusted / "selection.json").read_text())["schema"] == 2:
        requirement = components.plugin_requirement_from_makefile((trusted / "plugin-package.mk").read_text())
        creator = json.loads((trusted / "creator-target.json").read_text())["package_creator"]
        identities = {name: target_pkg.query_identity(pkg_command, ["query", "-e", f"%n = {name}"])
                      for name in ("bind920", "bind-tools")}
        dependency = json.loads((trusted / "bind-build-dependency.json").read_text())
        if identities["bind920"].version != dependency["version"]:
            raise ValueError("ordinary build dependency differs from sealed upstream version")
        for name, identity in identities.items():
            if (identity.name != name or identity.origin != f"dns/{name}" or identity.abi != creator["abi"]
                    or components.compare_native_versions(pkg_command, identity.version, requirement["minimum_version"]) == "<"):
                raise ValueError("ordinary OPNsense dependency differs from source minimum or target ABI")
        edges = subprocess.check_output([pkg_command, "query", "-e", "%n = bind920", "%dn|%do|%dv"], text=True).splitlines()
        if f'bind-tools|dns/bind-tools|{identities["bind-tools"].version}' not in edges:
            raise ValueError("installed ordinary runtime dependency differs")
        subprocess.run([pkg_command, "check", "-s", "bind920", "bind-tools"], check=True, stdout=sys.stderr)
        return identities["bind920"].version
    receipt = components.component_receipt(directory, "bind_runtime", series)
    if receipt != json.loads((trusted / "runtime-selection.json").read_text()):
        raise ValueError("installed build input differs from frozen runtime receipt")
    version = None
    names = []
    for filename in sorted(receipt["packages"]):
        archive = directory / filename
        expected = target_pkg.query_identity(pkg_command, ["query", "-F", str(archive)])
        actual = target_pkg.query_identity(pkg_command, ["query", "-e", f"%n = {expected.name}"])
        if actual != expected:
            raise ValueError(f"installed {expected.name} identity differs from frozen archive")
        archive_files = package_checksums.verify_archive(pkg_command, archive)
        installed = subprocess.check_output([pkg_command, "query", "-e", f"%n = {expected.name}", "%Fp|%Fs"], text=True)
        installed_files = tuple(tuple(row.split("|", 1)) for row in installed.splitlines())
        if sorted(installed_files) != sorted(archive_files):
            raise ValueError(f"installed {expected.name} file checksums differ from frozen archive")
        names.append(expected.name)
        if expected.name == "bind920":
            version = expected.version
    subprocess.run([pkg_command, "check", "-s", *names], check=True, stdout=sys.stderr)
    if version is None:
        raise ValueError("frozen pair has no bind920 version")
    return version


def copy_cached_producer(directory, output, selection, trusted, pkg_command):
    if selection["producer_action"] != "reuse" or selection["mode"] != "production":
        raise ValueError("plugin cache copy requires a sealed production reuse action")
    manifest = components.validate_candidate(directory, selection, trusted, pkg_command=pkg_command)
    packages = [directory / name for name in manifest["packages"]]
    package_catalogue.verify_packages(packages, directory.resolve().as_uri(),
        trusted / "reader-target.json", trusted / "upstream.json", pkg_command=pkg_command)
    output.mkdir(parents=True)
    for name in (*manifest["packages"], "build-metadata.txt", "plugin-build-inputs.json"):
        shutil.copyfile(directory / name, output / name)
    components.validate_producer_copy(output, directory, "bind_plugin", selection["series"])


def validate_output(directory, selection, trusted, pkg_command):
    if selection["producer_action"] != "build":
        raise ValueError("fresh plugin validation requires sealed build action")
    if selection["mode"] == "production":
        manifest = components.validate_candidate(directory, selection, trusted, pkg_command=pkg_command)
        archive = directory / next(iter(manifest["packages"]))
    else:
        files = {path.name for path in directory.iterdir()}
        archives = [path for path in directory.glob("os-bind-rp-devel-*.pkg")]
        if (len(archives) != 1 or files != {archives[0].name, "build-metadata.txt", "plugin-build-inputs.json"}
                or json.loads((directory / "plugin-build-inputs.json").read_text())
                   != json.loads((trusted / "producer-inputs.json").read_text())):
            raise ValueError("development output differs from exact unsigned plugin files")
        archive = archives[0]
        identity = target_pkg.query_identity(pkg_command, ["query", "-F", str(archive)])
        inputs = json.loads((trusted / "producer-inputs.json").read_text())
        if (identity.name != "os-bind-rp-devel" or identity.origin != "opnsense/os-bind-rp-devel"
                or identity.abi != inputs["package_abi"] or archive.name != f"{identity.name}-{identity.version}.pkg"):
            raise ValueError("development plugin identity differs from selected source")
        _, dependencies = releases.query_package(archive, pkg_command)
        record = releases.read_package_manifest(archive, pkg_command)
        requirement = components.plugin_requirement_from_makefile((trusted / "plugin-package.mk").read_text())
        if selection["schema"] == 2:
            built_version = components._metadata((directory / "build-metadata.txt").read_text())["bind920"]
        else:
            runtime = components.validate_receipt(json.loads((trusted / "runtime-selection.json").read_text()),
                                                  "bind_runtime", selection["series"])
            built_version = next(name.removeprefix("bind920-").removesuffix(".pkg")
                                 for name in runtime["packages"] if name.startswith("bind920-"))
        upstream = json.loads((trusted / "upstream.json").read_text())
        _, source = components._role(json.loads((trusted / "creator-target.json").read_text()), upstream, "creator")
        components.validate_plugin_metadata(components._metadata((directory / "build-metadata.txt").read_text()),
                                            upstream, inputs, source, built_version,
                                            "opnsense" if selection["schema"] == 2 else "resolver")
        components.validate_plugin_archive(record, dependencies, requirement,
                                           built_runtime_version=built_version, development=True)
        components._validate_plugin_file_map(record, package_checksums.verify_archive(pkg_command, archive))
    verify_package_source(archive, "bind-rp", selection["source_commit"])


def prepare_source_assets(component, output, series, selection):
    manifest = components.validate_component(component, "bind_plugin", series)
    if selection["mode"] != "production" or manifest["build_fingerprint"] != selection["expected_fingerprint"]:
        raise ValueError("source Release requires qualified production plugin identity")
    output.mkdir(parents=True)
    for name in (*manifest["packages"], *SOURCE_ASSETS):
        shutil.copyfile(component / name, output / name)
    if (component / "bind-build-dependency.json").is_file():
        shutil.copyfile(component / "bind-build-dependency.json", output / "bind-build-dependency.json")
    if (component / "series-build-inputs.json").is_file():
        shutil.copyfile(component / "series-build-inputs.json", output / "series-build-inputs.json")
    return f"os-bind-rp-{series}-{manifest['plugin_version']}-plugin-{manifest['build_fingerprint']}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("materialize", "intake", "installed-runtime", "reuse", "validate-output", "source-assets", "source-minimum"):
        command = commands.add_parser(name)
        command.add_argument("--series", required=True)
        command.add_argument("--trusted-directory", type=Path, required=True)
        command.add_argument("--selection-sha256", required=True)
        if name == "materialize":
            command.add_argument("--repository-root", type=Path, required=True)
            command.add_argument("--source-commit", required=True)
        elif name == "intake":
            command.add_argument("--repository", required=True)
            command.add_argument("--output-directory", type=Path, required=True)
        elif name == "source-assets":
            command.add_argument("--repository-root", type=Path, required=True)
            command.add_argument("--composition-sha256", required=True)
            command.add_argument("--output-directory", type=Path, required=True)
        elif name == "source-minimum":
            command.add_argument("--repository-root", type=Path, required=True)
        else:
            command.add_argument("--directory", type=Path, required=True)
            if name == "reuse":
                command.add_argument("--output-directory", type=Path, required=True)
            command.add_argument("--pkg-command", required=True)
    args = parser.parse_args()
    selection = components.load_selection(args.trusted_directory, component="bind_plugin", series=args.series,
                                          selection_sha256=args.selection_sha256)
    if args.command == "materialize":
        if selection["source_commit"] != args.source_commit:
            raise ValueError("materialized source differs from controller")
        inputs = json.loads((args.trusted_directory / "producer-inputs.json").read_text())
        if inputs["build_recipe_sha256"] != releases.sha256(args.repository_root / ".github/ci/bind/plugin/build-os-bind-rp.sh"):
            raise ValueError("plugin wrapper differs from trusted build input")
        components.validate_selected_git(selection, args.trusted_directory)
        materialize_source(args.repository_root, args.source_commit)
        definition = args.repository_root / "dns/bind/Makefile"
        trusted_definition = args.trusted_directory / "plugin-package.mk"
        if definition.read_bytes() != trusted_definition.read_bytes():
            raise ValueError("materialized plugin definition differs from trusted source")
        components.validate_plugin_source(args.repository_root,
            components.plugin_requirement_from_makefile(trusted_definition.read_text()))
    elif args.command == "intake":
        fetch_intake(args.repository, args.trusted_directory, selection, args.output_directory)
    elif args.command == "installed-runtime":
        print(installed_runtime(args.directory, args.trusted_directory, args.series, args.pkg_command))
    elif args.command == "source-minimum":
        requirement = components.plugin_requirement_from_makefile((args.trusted_directory / "plugin-package.mk").read_text())
        components.validate_plugin_source(args.repository_root, requirement)
        print(requirement["minimum_opnsense_version"])
    elif args.command == "reuse":
        copy_cached_producer(args.directory, args.output_directory, selection, args.trusted_directory, args.pkg_command)
    elif args.command == "validate-output":
        validate_output(args.directory, selection, args.trusted_directory, args.pkg_command)
    else:
        composition = components.load_composition(args.repository_root, component="bind_plugin", series=args.series,
                                                composition_sha256=args.composition_sha256)
        components.validate_repository_root(args.repository_root, selection, args.trusted_directory, composition)
        print(prepare_source_assets(args.repository_root / "component/current", args.output_directory, args.series, selection))


if __name__ == "__main__":
    main()
