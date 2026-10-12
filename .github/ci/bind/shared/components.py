#!/usr/bin/env python3
"""Original BIND component identities, sealed selections and frozen intake."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from bind.runtime import bind920_profile
from shared import metadata_profile, package_checksums, release_channel as releases, target_pkg

ROOT = Path(__file__).resolve().parents[4]
PUBLIC_KEY = ROOT / "docs/package-repository/resolver-plugins.pub"
REPOSITORY = "resolver-plugins/repository"
KINDS = ("bind_runtime", "bind_plugin")
CATALOGUE_FILES = {"data.pkg", "packagesite.pkg"}
CATALOGUE_ASSETS = {"meta", "meta.conf", "data.pkg", "data.tzst", "packagesite.pkg", "packagesite.tzst"}
COMMON_FIELDS = {"schema", "kind", "series", "package_abi", "control_commit",
                 "build_fingerprint", "package_creator", "creator_source", "packages", "assets"}
PLUGIN_INPUT_FIELDS = {"schema", "source_commit", "series", "package_abi", "freebsd_release",
                       "package_creator", "build_recipe_sha256"}
RUNTIME_INPUT_FIELDS = {"schema", "series", "freebsd_release", "architecture", "bind_profile",
                        "build_recipe_sha256", "package_creator"}
RECEIPT_FIELDS = {"schema", "component", "series", "package_abi", "current_tag", "snapshot_tag",
                  "fingerprint", "assets", "packages"}
SELECTION_FIELDS = {"schema", "component", "series", "mode", "control_commit", "source_commit",
                    "profile_commit", "producer_action", "expected_fingerprint", "files"}
TRUSTED_FILES = {"upstream.json", "creator-target.json", "reader-target.json",
                 "runtime-approval.mk", "bind920-profile.json", "tools-profile.json", "producer-inputs.json"}
RUNTIME_SIDECARS = {"upstream.json", "creator-target.json", "bind920-profile.json",
                    "bind920-provenance.json"}
PLUGIN_SIDECARS = {"upstream.json", "creator-target.json", "plugin-build-inputs.json",
                   "build-metadata.txt", "plugin-package.mk"}


def plugin_requirement_from_makefile(text):
    """Read the literal package contract from its original release definition."""
    fields = {}
    for field in ("PLUGIN_NAME", "PLUGIN_DEPENDS", "PLUGIN_DEPEND_FORMULA",
                  "PLUGIN_DEPEND_FORMULA_DEPENDS", "PLUGIN_CONFLICTS", "PLUGIN_MANIFEST_DEPENDS"):
        declarations = [line for line in text.splitlines() if re.match(rf"\s*{field}\s*[?+:!]?=", line)]
        match = re.fullmatch(rf"\s*{field}\s*=\s*([^#]*?)(?:\s*#.*)?", declarations[0]) if len(declarations) == 1 else None
        if match is None:
            raise ValueError(f"missing or ambiguous literal {field}")
        fields[field] = match[1].strip()
    formula = re.fullmatch(r"bind920\s+>=\s+(9\.20\.[0-9]+(?:_[1-9][0-9]*)?)", fields["PLUGIN_DEPEND_FORMULA"])
    core = re.fullmatch(r'opnsense:\s*\{\s*version:\s*"([0-9]+(?:\.[0-9]+)*(?:_[0-9]+)?)",\s*origin:\s*"opnsense/opnsense"\s*\}',
                        fields["PLUGIN_MANIFEST_DEPENDS"])
    if (fields["PLUGIN_NAME"] != "bind-rp" or fields["PLUGIN_DEPENDS"].split() != ["bind920"]
            or fields["PLUGIN_DEPEND_FORMULA_DEPENDS"].split() != ["bind920"]
            or fields["PLUGIN_CONFLICTS"].split() != ["bind"] or formula is None or core is None):
        raise ValueError("unsupported plugin dependency, identity, conflict or core contract")
    return dict(name="os-" + fields["PLUGIN_NAME"], dependency="bind920", minimum_version=formula[1],
                dependency_formula=f"bind920 >= {formula[1]}", formula_dependencies=["bind920"],
                conflicts=["os-bind"], minimum_opnsense_version=core[1])


def compare_native_versions(pkg_command, left, right):
    result = subprocess.check_output([pkg_command, "version", "-t", left, right], text=True).strip()
    if result not in {"<", "=", ">"}:
        raise ValueError("native package version comparison failed")
    return result


def runtime_is_eligible(requirement, bind920, bind_tools, compare_versions):
    """Apply a source minimum to supported native runtime package identities."""
    return ((bind920[0], bind920[2]) == ("bind920", bind920_profile.PACKAGE_ORIGINS["bind920"])
            and (bind_tools[0], bind_tools[2]) == ("bind-tools", bind920_profile.PACKAGE_ORIGINS["bind-tools"])
            and compare_versions(bind920[1], requirement["minimum_version"]) in {"=", ">"})


def _kind(kind):
    if kind not in KINDS:
        raise ValueError("invalid BIND component")
    return kind.replace("_", "-")


def _commit(value):
    if not isinstance(value, str) or releases.COMMIT_PATTERN.fullmatch(value) is None:
        raise ValueError("invalid component commit")
    return value


def _hashes(value):
    if not isinstance(value, dict):
        raise ValueError("invalid component digest map")
    for name, digest in value.items():
        if not isinstance(name, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+,-]*", name) is None:
            raise ValueError("unsafe component asset name")
        releases.validate_bind_fingerprint(digest)
    return value


def _files(directory):
    if not directory.is_dir() or any(path.is_symlink() or not path.is_file()
                                     for path in directory.iterdir()):
        raise ValueError("component directory must contain flat regular files")
    return _hashes(releases.directory_checksums(directory))


def _json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _role(record, upstream, role):
    """Validate the existing role record without reading any filesystem."""
    if (not isinstance(record, dict)
            or set(record) != {"schema", "role", "series", "release", "recipe", "package_creator"}
            or type(record["schema"]) is not int or record["schema"] != 1
            or record["role"] != role or record["series"] != upstream["series"]
            or not isinstance(record["recipe"], dict)
            or set(record["recipe"]) != {"repository", "commit", "path"}):
        raise ValueError("invalid resolved pkg role record")
    creator = target_pkg.normalize_creator(record["package_creator"])
    source = target_pkg.validate_source(dict(record["recipe"], release=record["release"]),
                                       creator, upstream["series"])
    expected = upstream["tools_tag"] if role == "creator" else target_pkg.READER_RELEASES.get(upstream["series"])
    if (record["release"] != expected
            or creator["abi"] != f"FreeBSD:{upstream['freebsd_release'].split('.')[0]}:amd64"):
        raise ValueError("pkg role differs from original upstream")
    return creator, source


def component_tags(kind, series, version, fingerprint):
    label = _kind(kind)
    releases.channel_tag(series)
    releases.validate_bind_fingerprint(fingerprint)
    if not isinstance(version, str) or releases.PACKAGE_VERSION_PATTERN.fullmatch(version) is None:
        raise ValueError("invalid component version")
    if kind == "bind_runtime":
        compare_runtime_versions(version, version)
    else:
        match = releases.SERIES_PLUGIN_VERSION_PATTERN.fullmatch(version)
        if match is None or match["series"] != series:
            raise ValueError("plugin version differs from series")
    current = f"pkg-{series}-{label}"
    return current, f"{current}-{version}-{fingerprint}"


def compare_runtime_versions(left, right):
    def version(value):
        match = re.fullmatch(r"9\.20\.([0-9]+)(?:_([0-9]+))?", value)
        if match is None:
            raise ValueError("unsupported BIND runtime package version")
        return int(match[1]), int(match[2] or 0)
    left, right = version(left), version(right)
    return "<" if left < right else ">" if left > right else "="


def validate_plugin_inputs(inputs):
    if (not isinstance(inputs, dict) or set(inputs) != PLUGIN_INPUT_FIELDS
            or type(inputs["schema"]) is not int or inputs["schema"] != 1):
        raise ValueError("invalid plugin build inputs")
    _commit(inputs["source_commit"])
    releases.channel_tag(inputs["series"])
    creator = target_pkg.normalize_creator(inputs["package_creator"])
    releases.validate_package_abi(inputs["package_abi"])
    if (not isinstance(inputs["freebsd_release"], str)
            or bind920_profile.FREEBSD_RELEASE_PATTERN.fullmatch(inputs["freebsd_release"]) is None
            or inputs["package_abi"] != creator["abi"]
            or creator["abi"] != f"FreeBSD:{inputs['freebsd_release'].split('.')[0]}:amd64"):
        raise ValueError("plugin build ABI differs from release")
    releases.validate_bind_fingerprint(inputs["build_recipe_sha256"])
    return inputs


def plugin_build_fingerprint(inputs):
    inputs = validate_plugin_inputs(inputs)
    return hashlib.sha256(json.dumps(inputs, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _runtime_inputs(inputs):
    if not isinstance(inputs, dict) or set(inputs) != RUNTIME_INPUT_FIELDS:
        raise ValueError("invalid runtime producer inputs")
    canonical = bind920_profile.producer_inputs(
        inputs["bind_profile"], inputs["series"], inputs["freebsd_release"],
        inputs["architecture"], inputs["package_creator"], recipe_sha256=inputs["build_recipe_sha256"])
    if type(inputs["schema"]) is not int or inputs != canonical:
        raise ValueError("invalid runtime producer inputs")
    return inputs


def _fingerprint(kind, inputs):
    _kind(kind)
    if kind == "bind_plugin":
        return plugin_build_fingerprint(inputs)
    inputs = _runtime_inputs(inputs)
    return bind920_profile.compatibility_fingerprint(
        inputs["bind_profile"], inputs["series"], inputs["freebsd_release"],
        inputs["architecture"], inputs["package_creator"], recipe_sha256=inputs["build_recipe_sha256"])


def _metadata(text):
    if not isinstance(text, str):
        raise ValueError("invalid original plugin build metadata")
    fields = {}
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if not separator or not key or not value or key in fields:
            raise ValueError("invalid original plugin build metadata")
        fields[key] = value
    if set(fields) != releases.BUILD_METADATA_FIELDS:
        raise ValueError("invalid original plugin build metadata")
    return fields


def _runtime_reference(record, series):
    if not isinstance(record, dict) or set(record) != {"snapshot_tag", "fingerprint", "version", "packages"}:
        raise ValueError("invalid build-time runtime reference")
    _, snapshot = component_tags("bind_runtime", series, record["version"], record["fingerprint"])
    packages = _hashes(record["packages"])
    if (record["snapshot_tag"] != snapshot
            or set(packages) != {f"{name}-{record['version']}.pkg" for name in ("bind-tools", "bind920")}):
        raise ValueError("invalid build-time runtime reference")
    return record


def validate_plugin_metadata(metadata, upstream, inputs, creator_source, runtime_version):
    creator = inputs["package_creator"]
    expected = {field: upstream[field] for field in releases.TRUSTED_BUILD_FIELDS}
    expected.update(source_commit=inputs["source_commit"], bind920=runtime_version, bind_source="resolver",
                    pkg_abi=creator["abi"], pkg_creator=creator["version"],
                    creator_release=creator_source["release"], creator_ports_commit=creator_source["commit"],
                    creator_recipe_path=creator_source["path"], opnsense_core_commit=upstream["core_commit"])
    if any(metadata[field] != value for field, value in expected.items()):
        raise ValueError("plugin build metadata differs from original inputs")


def validate_component_record(manifest, sidecars, package_hashes, kind, series):
    label = _kind(kind)
    extra = {"profile_commit"} if kind == "bind_runtime" else {"source_commit", "plugin_version", "built_against_runtime"}
    if (not isinstance(manifest, dict) or set(manifest) != COMMON_FIELDS | extra
            or type(manifest["schema"]) is not int or manifest["schema"] != 1
            or manifest["kind"] != label or manifest["series"] != series):
        raise ValueError("invalid BIND component manifest")
    releases.channel_tag(series)
    _commit(manifest["control_commit"])
    releases.validate_bind_fingerprint(manifest["build_fingerprint"])
    _hashes(manifest["assets"])
    packages = _hashes(manifest["packages"])
    if packages != _hashes(package_hashes) or any(manifest["assets"].get(name) != digest
                                               for name, digest in packages.items()):
        raise ValueError("component package hashes differ from original manifest")
    names = RUNTIME_SIDECARS if kind == "bind_runtime" else PLUGIN_SIDECARS
    assets = set(manifest["assets"])
    if (not names <= assets
            or not assets <= names | {"resolver-plugins.pub"} | CATALOGUE_ASSETS | set(packages)):
        raise ValueError("component original sidecar or catalogue set differs")
    upstream = metadata_profile.validate_profile(sidecars["upstream.json"], series)
    creator, source = _role(sidecars["creator-target.json"], upstream, "creator")
    if (manifest["package_creator"] != creator or manifest["creator_source"] != source
            or manifest["package_abi"] != creator["abi"]):
        raise ValueError("component original creator or ABI differs")
    if kind == "bind_runtime":
        _commit(manifest["profile_commit"])
        profile = bind920_profile.validate_profile(sidecars["bind920-profile.json"])
        provenance = sidecars["bind920-provenance.json"]
        records = releases.validate_bind_provenance(provenance, profile, series, upstream["freebsd_release"], creator)
        if (manifest["build_fingerprint"] != provenance["fingerprint"]
                or set(packages) != {record["filename"] for record in records.values()}):
            raise ValueError("runtime component differs from original provenance")
    else:
        inputs = validate_plugin_inputs(sidecars["plugin-build-inputs.json"])
        metadata = _metadata(sidecars["build-metadata.txt"])
        requirement = plugin_requirement_from_makefile(sidecars["plugin-package.mk"])
        historical = _runtime_reference(manifest["built_against_runtime"], series)
        version = manifest["plugin_version"]
        component_tags(kind, series, version, manifest["build_fingerprint"])
        if (manifest["source_commit"] != _commit(inputs["source_commit"])
                or inputs["series"] != series or inputs["package_creator"] != creator
                or inputs["package_abi"] != creator["abi"]
                or inputs["freebsd_release"] != upstream["freebsd_release"]
                or manifest["build_fingerprint"] != plugin_build_fingerprint(inputs)
                or compare_runtime_versions(historical["version"], requirement["minimum_version"]) == "<"
                or set(packages) != {f"os-bind-rp-{version}.pkg"}):
            raise ValueError("plugin component differs from original build inputs")
        validate_plugin_metadata(metadata, upstream, inputs, source, historical["version"])
    return manifest


def _sidecars(directory, kind):
    names = RUNTIME_SIDECARS if kind == "bind_runtime" else PLUGIN_SIDECARS
    return {name: (directory / name).read_text(encoding="utf-8") if name.endswith((".txt", ".mk"))
            else _json(directory / name) for name in names}


def validate_plugin_archive(record, dependencies, requirement, *, development=False):
    conflicts = requirement["conflicts"]
    expected_conflicts = set(conflicts) | {name + "-devel" for name in conflicts}
    expected_conflicts.add(requirement["name"] if development else requirement["name"] + "-devel")
    core = record.get("deps", {}).get("opnsense")
    if (any(edge[0] in requirement["formula_dependencies"] for edge in dependencies)
            or record.get("dep_formula") != requirement["dependency_formula"]
            or not isinstance(record.get("conflicts"), list) or set(record["conflicts"]) != expected_conflicts
            or core != {"version": requirement["minimum_opnsense_version"], "origin": "opnsense/opnsense"}):
        raise ValueError("plugin archive dependency, conflict or core floor differs from source")


def _validate_archives(directory, manifest, sidecars, pkg_command):
    identities, dependencies = {}, {}
    for filename in manifest["packages"]:
        path = directory / filename
        identity, edges = releases.query_package(path, pkg_command)
        name, version, origin, abi = identity
        if manifest["kind"] == "bind-runtime":
            expected = sidecars["bind920-provenance.json"]["packages"].get(name)
            if expected is None or (name, version, origin, filename) != tuple(
                    expected[field] for field in ("name", "version", "origin", "filename")):
                raise ValueError("runtime archive identity differs from provenance")
        elif identity[:3] != ("os-bind-rp", manifest["plugin_version"], "opnsense/os-bind-rp"):
            raise ValueError("plugin archive identity differs from original manifest")
        if abi != manifest["package_abi"]:
            raise ValueError("archive ABI differs from component")
        identities[name], dependencies[name] = identity, edges
        package_checksums.verify_archive(pkg_command, path)
    if manifest["kind"] == "bind-runtime":
        if ("bind-tools", "dns/bind-tools", identities["bind-tools"][1]) not in dependencies["bind920"]:
            raise ValueError("runtime archive pair dependency differs")
    else:
        requirement = plugin_requirement_from_makefile(sidecars["plugin-package.mk"])
        archive = directory / next(iter(manifest["packages"]))
        record = releases.read_package_manifest(archive, pkg_command)
        validate_plugin_archive(record, dependencies["os-bind-rp"], requirement)


def validate_component(directory, kind, series, *, pkg_command=None):
    _kind(kind)
    actual = _files(directory)
    manifest = _json(directory / "channel.json")
    assets = dict(actual)
    assets.pop("channel.json")
    sidecar_names = RUNTIME_SIDECARS if kind == "bind_runtime" else PLUGIN_SIDECARS
    if (manifest.get("assets") != assets
            or not sidecar_names | {"resolver-plugins.pub", "meta.conf"} <= set(assets)
            or not CATALOGUE_FILES.intersection(assets)
            or (directory / "resolver-plugins.pub").read_bytes() != PUBLIC_KEY.read_bytes()):
        raise ValueError("component assets, original sidecars, catalogue or public key differ")
    payloads = {name: digest for name, digest in assets.items() if name.endswith(".pkg") and name not in CATALOGUE_FILES}
    sidecars = _sidecars(directory, kind)
    validate_component_record(manifest, sidecars, payloads, kind, series)
    if pkg_command is not None:
        _validate_archives(directory, manifest, sidecars, pkg_command)
    return manifest


def component_receipt(directory, kind, series):
    manifest = validate_component(directory, kind, series)
    version = (bind920_profile.package_version(_json(directory / "bind920-profile.json"))
               if kind == "bind_runtime" else manifest["plugin_version"])
    current, snapshot = component_tags(kind, series, version, manifest["build_fingerprint"])
    return dict(schema=1, component=kind, series=series, package_abi=manifest["package_abi"],
                current_tag=current, snapshot_tag=snapshot, fingerprint=manifest["build_fingerprint"],
                assets=_files(directory), packages=manifest["packages"])


def validate_receipt(receipt, kind, series):
    if kind != "dhcp":
        _kind(kind)
    if (not isinstance(receipt, dict) or set(receipt) != RECEIPT_FIELDS
            or type(receipt["schema"]) is not int or receipt["schema"] != 1
            or receipt["component"] != kind or receipt["series"] != series):
        raise ValueError("invalid component receipt")
    releases.validate_package_abi(receipt["package_abi"])
    assets, packages = _hashes(receipt["assets"]), _hashes(receipt["packages"])
    prefix = "bind920-" if kind == "bind_runtime" else "os-bind-rp-"
    if kind == "dhcp":
        prefix = "os-dhcp-interface-ha-"
    versions = [name[len(prefix):-4] for name in packages if name.startswith(prefix) and name.endswith(".pkg")]
    if len(versions) != 1:
        raise ValueError("invalid receipt package set")
    version = versions[0]
    if kind == "dhcp":
        from ha_dhcp import dhcp_interface_ha_channel
        if (series != "26.7" or receipt["fingerprint"] is not None
                or re.fullmatch(r"[0-9]+(?:\.[0-9]+)*(?:_[0-9]+)?", version) is None):
            raise ValueError("invalid HA receipt identity")
        current,snapshot=dhcp_interface_ha_channel.tags(series,version)
        names={f"os-dhcp-interface-ha-{version}.pkg"}
    else:
        current, snapshot = component_tags(kind, series, version, receipt["fingerprint"])
        names = {f"{name}-{version}.pkg" for name in ("bind-tools", "bind920")} if kind == "bind_runtime" else {f"os-bind-rp-{version}.pkg"}
    if (receipt["current_tag"] != current or receipt["snapshot_tag"] != snapshot
            or set(packages) != names or "channel.json" not in assets
            or any(assets.get(name) != digest for name, digest in packages.items())):
        raise ValueError("component receipt identity or bytes differ")
    return receipt


def load_selection(trusted_directory, *, component, series, selection_sha256):
    _kind(component)
    releases.validate_bind_fingerprint(selection_sha256)
    path = trusted_directory / "selection.json"
    if path.is_symlink() or releases.sha256(path) != selection_sha256:
        raise ValueError("trusted selection digest differs from controller")
    selection = _json(path)
    if (not isinstance(selection, dict) or set(selection) != SELECTION_FIELDS
            or type(selection["schema"]) is not int or selection["schema"] != 1
            or selection["component"] != component or selection["series"] != series
            or selection["mode"] not in ("production", "development")
            or selection["producer_action"] not in ("build", "reuse")):
        raise ValueError("invalid trusted selection")
    _commit(selection["control_commit"])
    _commit(selection["profile_commit"])
    if component == "bind_runtime":
        if selection["source_commit"] is not None or selection["producer_action"] != "build":
            raise ValueError("runtime selection requires a fresh build without plugin source")
    elif (_commit(selection["source_commit"]) != selection["profile_commit"]
          or selection["mode"] == "development" and selection["producer_action"] != "build"):
        raise ValueError("plugin source/profile or preview action differs")
    required = TRUSTED_FILES | ({"plugin-package.mk", "runtime-selection.json"} if component == "bind_plugin" else set())
    if selection["producer_action"] == "reuse":
        required |= {"cached-component.json"}
    actual = _files(trusted_directory)
    actual.pop("selection.json")
    if set(actual) != required or _hashes(selection["files"]) != actual:
        raise ValueError("trusted bundle files differ from controller selection")
    upstream = metadata_profile.validate_profile(_json(trusted_directory / "upstream.json"), series)
    creator, _ = _role(_json(trusted_directory / "creator-target.json"), upstream, "creator")
    _role(_json(trusted_directory / "reader-target.json"), upstream, "reader")
    profile = bind920_profile.load_profile(trusted_directory / "bind920-profile.json")
    approval = bind920_profile.approval_from_makefile((trusted_directory / "runtime-approval.mk").read_text())
    if profile["ports_commit"] != approval["ports_commit"] or bind920_profile.package_version(profile) != approval["version"]:
        raise ValueError("trusted recipe differs from runtime approval")
    metadata_profile.validate_tools_record(_json(trusted_directory / "tools-profile.json"), upstream)
    inputs = _json(trusted_directory / "producer-inputs.json")
    if (inputs.get("series") != series or inputs.get("freebsd_release") != upstream["freebsd_release"]
            or inputs.get("package_creator") != creator
            or _fingerprint(component, inputs) != selection["expected_fingerprint"]):
        raise ValueError("producer inputs differ from trusted selection")
    if component == "bind_runtime":
        if inputs["bind_profile"] != profile or inputs["architecture"] != "x86_64":
            raise ValueError("runtime inputs differ from trusted pin")
    else:
        requirement = plugin_requirement_from_makefile((trusted_directory / "plugin-package.mk").read_text())
        if (inputs["source_commit"] != selection["source_commit"]
                or compare_runtime_versions(approval["version"], requirement["minimum_version"]) == "<"):
            raise ValueError("plugin inputs differ from trusted source or runtime minimum")
        runtime = validate_receipt(_json(trusted_directory / "runtime-selection.json"), "bind_runtime", series)
        if runtime["package_abi"] != creator["abi"]:
            raise ValueError("frozen runtime ABI differs from selected plugin")
    if selection["producer_action"] == "reuse":
        cached = validate_receipt(_json(trusted_directory / "cached-component.json"), component, series)
        if cached["fingerprint"] != selection["expected_fingerprint"] or cached["package_abi"] != creator["abi"]:
            raise ValueError("cached component differs from producer selection")
    return selection


def seal_selection(trusted_directory, selection):
    """Controller-only boundary; consumers still require its external digest."""
    selection = dict(selection)
    selection["files"] = {name: digest for name, digest in _files(trusted_directory).items() if name != "selection.json"}
    path = trusted_directory / "selection.json"
    path.write_text(json.dumps(selection, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    digest = releases.sha256(path)
    load_selection(trusted_directory, component=selection["component"], series=selection["series"],
                   selection_sha256=digest)
    return digest


def fetch_component(repository, kind, series, output):
    current = f"pkg-{series}-{_kind(kind)}"
    releases.channel_tag(series)
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        observed = releases.snapshot_release(repository, current, root)
        if not observed.existed:
            return None
        if observed.draft:
            raise ValueError("registered component current is unpublished")
        receipt = component_receipt(observed.directory, kind, series)
        snapshot = releases.snapshot_release(repository, receipt["snapshot_tag"], root)
        if not snapshot.existed or snapshot.draft:
            raise ValueError("registered current lacks its published component snapshot")
        if component_receipt(snapshot.directory, kind, series) != receipt:
            raise ValueError("component current differs from its immutable snapshot")
        shutil.copytree(snapshot.directory, output)
        return receipt


def fetch_cached_component(repository, kind, series, fingerprint, output):
    if kind != "bind_plugin":
        raise ValueError("only the plugin has a build cache")
    releases.channel_tag(series)
    releases.validate_bind_fingerprint(fingerprint)
    result = subprocess.run(["gh", "release", "list", "--repo", repository, "--limit", "10000",
                             "--json", "tagName,isDraft"], check=True, capture_output=True, text=True)
    records = json.loads(result.stdout)
    if not isinstance(records, list) or any(not isinstance(record, dict)
            or not isinstance(record.get("tagName"), str) or type(record.get("isDraft")) is not bool
            for record in records):
        raise ValueError("invalid registered component release list")
    pattern = re.compile(rf"pkg-{re.escape(series)}-{_kind(kind)}-(.+)-{fingerprint}")
    matches = sorted({record["tagName"] for record in records if not record["isDraft"]
                      and pattern.fullmatch(record["tagName"])})
    if len(matches) > 1:
        raise ValueError("conflicting published component cache snapshots")
    if not matches:
        return None
    with tempfile.TemporaryDirectory() as temporary:
        snapshot = releases.snapshot_release(repository, matches[0], Path(temporary))
        if not snapshot.existed or snapshot.draft:
            raise ValueError("published cache snapshot disappeared or became unpublished")
        receipt = component_receipt(snapshot.directory, kind, series)
        if receipt["snapshot_tag"] != matches[0] or receipt["fingerprint"] != fingerprint:
            raise ValueError("cache snapshot identity differs from registered tag")
        shutil.copytree(snapshot.directory, output)
        return receipt


def fetch_frozen_component(repository, receipt, output):
    """Download only the immutable snapshot already frozen by the controller."""
    validate_receipt(receipt, receipt["component"], receipt["series"])
    with tempfile.TemporaryDirectory() as temporary:
        snapshot = releases.snapshot_release(repository, receipt["snapshot_tag"], Path(temporary))
        if (not snapshot.existed or snapshot.draft
                or component_receipt(snapshot.directory, receipt["component"], receipt["series"]) != receipt):
            raise ValueError("immutable intake differs from frozen controller receipt")
        shutil.copytree(snapshot.directory, output)
    return receipt


def select_producer_action(kind, expected_inputs, candidate_directory):
    fingerprint = _fingerprint(kind, expected_inputs)
    cached = None
    if kind == "bind_runtime":
        if candidate_directory is not None:
            raise ValueError("runtime production cannot reuse a build cache")
        return dict(producer_action="build", expected_fingerprint=fingerprint, cached_component=None)
    if candidate_directory is not None:
        cached = component_receipt(candidate_directory, kind, expected_inputs["series"])
        if cached["fingerprint"] != fingerprint:
            raise ValueError("selected cache differs from expected producer fingerprint")
    return dict(producer_action="reuse" if cached else "build", expected_fingerprint=fingerprint,
                cached_component=cached)


def prepare_selection(trusted_directory, *, component, series, mode, control_commit,
                      source_commit, profile_commit, upstream, runtime_approval, tools_repository, recipe,
                      repository, intake_directory, package_definition=None, creator_target=None, reader_target=None):
    """Read-only controller boundary shared by both producer workflows."""
    _kind(component)
    _commit(control_commit)
    _commit(profile_commit)
    if mode not in ("production", "development"):
        raise ValueError("invalid producer mode")
    if (component == "bind_runtime" and source_commit is not None
            or component == "bind_plugin" and _commit(source_commit) != profile_commit):
        raise ValueError("invalid selected source/profile identity")
    if (component == "bind_plugin") != (package_definition is not None):
        raise ValueError("only plugin selection requires its source package definition")
    upstream_record = metadata_profile.validate_profile(_json(upstream), series)
    approval = bind920_profile.approval_from_makefile(runtime_approval.read_text())
    approved = bind920_profile.resolve_profile(approval)
    tools = metadata_profile.verify_tools_profile(upstream_record, tools_repository)
    if component == "bind_plugin":
        requirement = plugin_requirement_from_makefile(package_definition.read_text())
        if compare_runtime_versions(approval["version"], requirement["minimum_version"]) == "<":
            raise ValueError("approved runtime is below source dependency minimum")
    trusted_directory.mkdir(parents=True, exist_ok=True)
    if any(trusted_directory.iterdir()):
        raise ValueError("trusted controller output must be empty")
    for source, name in ((upstream, "upstream.json"), (runtime_approval, "runtime-approval.mk")):
        shutil.copyfile(source, trusted_directory / name)
    if package_definition is not None:
        shutil.copyfile(package_definition, trusted_directory / "plugin-package.mk")
    for name, record in (("bind920-profile.json", approved), ("tools-profile.json", tools)):
        (trusted_directory / name).write_text(json.dumps(record, sort_keys=True, indent=2) + "\n")
    if (creator_target is None) != (reader_target is None):
        raise ValueError("supply both resolved pkg roles or resolve them once")
    if creator_target is None:
        target_pkg.resolve_targets(trusted_directory / "upstream.json", series, "x86_64", trusted_directory)
    else:
        shutil.copyfile(creator_target, trusted_directory / "creator-target.json")
        shutil.copyfile(reader_target, trusted_directory / "reader-target.json")
    creator, _ = _role(_json(trusted_directory / "creator-target.json"), upstream_record, "creator")
    _role(_json(trusted_directory / "reader-target.json"), upstream_record, "reader")
    recipe_hash = releases.sha256(recipe)
    if component == "bind_runtime":
        inputs = bind920_profile.producer_inputs(approved, series, upstream_record["freebsd_release"],
                                                "x86_64", creator, recipe_sha256=recipe_hash)
    else:
        inputs = validate_plugin_inputs(dict(schema=1, source_commit=source_commit, series=series,
            package_abi=creator["abi"], freebsd_release=upstream_record["freebsd_release"],
            package_creator=creator, build_recipe_sha256=recipe_hash))
        runtime_directory = intake_directory / "bind_runtime"
        runtime_directory.parent.mkdir(parents=True, exist_ok=True)
        receipt = fetch_component(repository, "bind_runtime", series, runtime_directory)
        if receipt is None:
            raise ValueError(f"{series} runtime {bind920_profile.package_version(approved)} is absent; publish runtime first and retry")
        if (_json(runtime_directory / "bind920-profile.json") != approved
                or _json(runtime_directory / "bind920-provenance.json")["freebsd_release"] != upstream_record["freebsd_release"]
                or receipt["package_abi"] != creator["abi"]):
            raise ValueError(f"{series} runtime differs from approved {bind920_profile.package_version(approved)} recipe; publish runtime first and retry")
        (trusted_directory / "runtime-selection.json").write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n")
    fingerprint = _fingerprint(component, inputs)
    cached = None
    cache_directory = intake_directory / "cached_component"
    if component == "bind_plugin" and mode == "production":
        cache_directory.parent.mkdir(parents=True, exist_ok=True)
        cached = fetch_cached_component(repository, component, series, fingerprint, cache_directory)
    action = select_producer_action(component, inputs, cache_directory if cached else None)
    (trusted_directory / "producer-inputs.json").write_text(json.dumps(inputs, sort_keys=True, indent=2) + "\n")
    if cached:
        (trusted_directory / "cached-component.json").write_text(json.dumps(cached, sort_keys=True, indent=2) + "\n")
    selection = dict(schema=1, component=component, series=series, mode=mode, control_commit=control_commit,
                     source_commit=source_commit, profile_commit=profile_commit,
                     producer_action=action["producer_action"], expected_fingerprint=fingerprint)
    digest = seal_selection(trusted_directory, selection)
    return dict(selection_sha256=digest, freebsd_release=upstream_record["freebsd_release"],
                producer_action=action["producer_action"], expected_fingerprint=fingerprint)


def validate_candidate(directory, selection, trusted_directory, *, pkg_command):
    component, series = selection["component"], selection["series"]
    # The caller supplies the preflight result, never a builder-selected record.
    if _json(trusted_directory / "selection.json") != selection:
        raise ValueError("candidate selection differs from preflight")
    if selection["mode"] == "development" and component == "bind_plugin":
        raise ValueError("development plugin is not a production component candidate")
    if selection["producer_action"] == "reuse":
        manifest = validate_component(directory, component, series, pkg_command=pkg_command)
        receipt = component_receipt(directory, component, series)
        if receipt != _json(trusted_directory / "cached-component.json"):
            raise ValueError("reused component bytes differ from frozen cache")
        return manifest
    actual = _files(directory)
    upstream = _json(trusted_directory / "upstream.json")
    creator, source = _role(_json(trusted_directory / "creator-target.json"), upstream, "creator")
    sidecars = {"upstream.json": upstream, "creator-target.json": _json(trusted_directory / "creator-target.json")}
    manifest = dict(schema=1, kind=_kind(component), series=series, package_abi=creator["abi"],
                    control_commit=selection["control_commit"], build_fingerprint=selection["expected_fingerprint"],
                    package_creator=creator, creator_source=source)
    if component == "bind_runtime":
        profile = _json(trusted_directory / "bind920-profile.json")
        provenance = _json(directory / "bind920-provenance.json")
        sidecars.update({"bind920-profile.json": profile, "bind920-provenance.json": provenance})
        names = {f"{name}-{bind920_profile.package_version(profile)}.pkg" for name in ("bind-tools", "bind920")}
        required = names | {"bind920-provenance.json"}
        manifest["profile_commit"] = selection["profile_commit"]
        if provenance["build_recipe_sha256"] != _json(trusted_directory / "producer-inputs.json")["build_recipe_sha256"]:
            raise ValueError("fresh runtime recipe differs from selection")
    else:
        inputs = _json(directory / "plugin-build-inputs.json")
        if inputs != _json(trusted_directory / "producer-inputs.json"):
            raise ValueError("fresh plugin build inputs differ from selection")
        sidecars.update({"plugin-build-inputs.json": inputs, "build-metadata.txt": (directory / "build-metadata.txt").read_text(),
                         "plugin-package.mk": (trusted_directory / "plugin-package.mk").read_text()})
        names = {name for name in actual if name.endswith(".pkg")}
        if len(names) != 1:
            raise ValueError("fresh plugin output must contain one archive")
        version = next(iter(names)).removeprefix("os-bind-rp-").removesuffix(".pkg")
        required = names | {"plugin-build-inputs.json", "build-metadata.txt"}
        runtime = validate_receipt(_json(trusted_directory / "runtime-selection.json"), "bind_runtime", series)
        runtime_version = next(name.removeprefix("bind920-").removesuffix(".pkg") for name in runtime["packages"] if name.startswith("bind920-"))
        manifest.update(source_commit=selection["source_commit"], plugin_version=version,
                        built_against_runtime=dict(snapshot_tag=runtime["snapshot_tag"], fingerprint=runtime["fingerprint"],
                                                   version=runtime_version, packages=runtime["packages"]))
    if set(actual) != required:
        raise ValueError("producer output differs from exact owned files")
    manifest["packages"] = {name: actual[name] for name in sorted(names)}
    manifest["assets"] = dict(actual)
    for name in sidecars:
        if name not in manifest["assets"]:
            manifest["assets"][name] = releases.sha256(trusted_directory / name)
    validate_component_record(manifest, sidecars, manifest["packages"], component, series)
    if pkg_command is not None:
        _validate_archives(directory, manifest, sidecars, pkg_command)
    return manifest


def validate_producer_copy(producer, cached_directory, kind, series):
    """Check the raw owned upload against independently frozen original cache."""
    manifest = validate_component(cached_directory, kind, series)
    names = set(manifest["packages"]) | ({"bind920-provenance.json"} if kind == "bind_runtime"
                                        else {"build-metadata.txt", "plugin-build-inputs.json"})
    actual = _files(producer)
    if actual != {name: releases.sha256(cached_directory / name) for name in names}:
        raise ValueError("producer copy differs from original frozen component bytes")
    return manifest


def _ensure_commits(*commits):
    for commit in set(commits):
        _commit(commit)
        present = subprocess.run(["git", "cat-file", "-e", f"{commit}^{{commit}}"], capture_output=True)
        if present.returncode:
            subprocess.run(["git", "fetch", "--no-tags", "origin", commit], check=True, stdout=sys.stderr)


def _git_bytes(commit, path):
    return subprocess.check_output(["git", "show", f"{commit}:{path}"])


def validate_selected_git(selection, trusted_directory):
    """Signer checks selected Git inputs independently before private-key use."""
    _ensure_commits(selection["control_commit"], selection["profile_commit"])
    control = selection["control_commit"]
    if subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip() != control:
        raise ValueError("executing checkout differs from selected control")
    folder = "runtime" if selection["component"] == "bind_runtime" else "plugin"
    recipe = "build-bind920.sh" if folder == "runtime" else "build-os-bind-rp.sh"
    inputs = _json(trusted_directory / "producer-inputs.json")
    if hashlib.sha256(_git_bytes(control, f".github/ci/bind/{folder}/{recipe}")).hexdigest() != inputs["build_recipe_sha256"]:
        raise ValueError("selected wrapper differs from controller inputs")
    definitions = [("dns/bind/Makefile", "runtime-approval.mk", control),
                   (".resolver-plugins/upstream.json", "upstream.json", selection["profile_commit"])]
    if selection["component"] == "bind_plugin":
        definitions.append(("dns/bind/Makefile", "plugin-package.mk", selection["source_commit"]))
    for path, name, commit in definitions:
        if _git_bytes(commit, path) != (trusted_directory / name).read_bytes():
            raise ValueError("trusted input differs from selected Git object")


def validate_privileged_selection(selection, trusted_directory, *, expected_control_commit, profile_control_commit):
    """Bind privileged execution to the literal trusted workflow and profile output."""
    expected = _commit(expected_control_commit)
    if (selection["mode"] != "production" or selection["control_commit"] != expected
            or _commit(profile_control_commit) != expected):
        raise ValueError("production controller differs from executing trusted workflow")
    validate_selected_git(selection, trusted_directory)


def component_can_advance(current, staged, kind, *, compare_versions):
    before = _json(current / "channel.json")
    series = before["series"]
    validate_component(current, kind, series)
    after = validate_component(staged, kind, series)
    if _files(current) == _files(staged):
        return True
    return component_record_can_advance(before, after, kind, compare_versions=compare_versions)


def component_record_can_advance(before, after, kind, *, compare_versions):
    if before == after:
        return True
    fields = ("profile_commit", "control_commit") if kind == "bind_runtime" else ("source_commit", "control_commit")
    _ensure_commits(*(record[field] for record in (before, after) for field in fields))
    if not all(releases.commit_is_ancestor_or_equal(before[field], after[field]) for field in fields):
        return False
    if kind == "bind_runtime":
        def versions(record):
            return {name:next(filename.removeprefix(name+"-").removesuffix(".pkg") for filename in record["packages"]
                              if filename.startswith(name+"-")) for name in ("bind-tools","bind920")}
        old, new = versions(before), versions(after)
        if any(compare_versions(new[name], old[name]) == "<" for name in old):
            return False
    return before["build_fingerprint"] != after["build_fingerprint"] or any(before[field] != after[field] for field in fields)


def _select_role(trusted, series, role, pkg_command):
    return select_role(trusted / "upstream.json", trusted / f"{role}-target.json", series, role, pkg_command)


def select_role(upstream, metadata, series, role, pkg_command):
    """Concrete verified reader/creator selection for BIND and HA catalogue callers."""
    target_pkg.load_target(metadata, series, upstream=upstream, role=role)
    environment = dict(os.environ, RP_UPSTREAM_METADATA=str(upstream), RP_TARGET_PKG_METADATA=str(metadata))
    subprocess.run(["sh", str(ROOT / ".github/ci/shared/setup-opnsense-repository.sh"), series],
                   check=True, env=environment, stdout=sys.stderr)
    return target_pkg.select_target_pkg(metadata, series, pkg_command,
        upstream=upstream, role=role, pkg_static_path=Path(pkg_command))


def authenticate_candidate(producer, selection, trusted_directory, root, *, pkg_command):
    """Authenticate frozen/cached originals before the workflow decodes its key."""
    from shared import package_catalogue
    kind, series = selection["component"], selection["series"]
    if selection["mode"] != "production":
        raise ValueError("production component staging requires production selection")
    validate_selected_git(selection, trusted_directory)
    _select_role(trusted_directory, series, "reader", pkg_command)

    if kind == "bind_plugin":
        runtime = root / "runtime"
        receipt = _json(trusted_directory / "runtime-selection.json")
        fetch_frozen_component(REPOSITORY, receipt, runtime)
        if freeze_runtime(runtime, _json(trusted_directory / "bind920-profile.json"),
                          _json(trusted_directory / "upstream.json"), series,
                          requirement=plugin_requirement_from_makefile((trusted_directory / "plugin-package.mk").read_text()),
                          pkg_command=pkg_command) != receipt:
            raise ValueError("signer runtime differs from frozen plugin input")
    cached = None
    if selection["producer_action"] == "reuse":
        cached = root / "cache"
        fetch_frozen_component(REPOSITORY, _json(trusted_directory / "cached-component.json"), cached)
        manifest = validate_candidate(cached, selection, trusted_directory, pkg_command=pkg_command)
        package_catalogue.verify_packages([cached / name for name in manifest["packages"]], cached.resolve().as_uri(),
            trusted_directory / "reader-target.json", trusted_directory / "upstream.json", pkg_command=pkg_command)
        validate_producer_copy(producer, cached, kind, series)
    else:
        manifest = validate_candidate(producer, selection, trusted_directory, pkg_command=pkg_command)
    return cached, manifest


def stage_component(producer, selection, trusted_directory, output, key, *, pkg_command):
    """Authenticate original intake, then stage an exact independently owned component."""
    kind, series = selection["component"], selection["series"]
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        cached, manifest = authenticate_candidate(producer, selection, trusted_directory, root, pkg_command=pkg_command)
        _select_role(trusted_directory, series, "creator", pkg_command)
        staged = root / "component"
        current = staged / "current"
        if cached is not None:
            shutil.copytree(cached, current)
        else:
            if key is None or subprocess.check_output(["openssl", "pkey", "-in", str(key), "-pubout"]) != PUBLIC_KEY.read_bytes():
                raise ValueError("signing key differs from committed key")
            names = RUNTIME_SIDECARS if kind == "bind_runtime" else PLUGIN_SIDECARS
            metadata = [producer / name if (producer / name).is_file() else trusted_directory / name
                        for name in sorted(names)]
            releases.stage_selected_repository([producer / name for name in manifest["packages"]],
                                              current, key, pkg_command, metadata)
            shutil.copyfile(PUBLIC_KEY, current / "resolver-plugins.pub")
            manifest["assets"] = _files(current)
            (current / "channel.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
        validate_component(current, kind, series, pkg_command=pkg_command)
        shutil.copytree(current, staged / "snapshot")
        shutil.copytree(staged, output)
    return manifest


def stage_preview(producer, selection, trusted_directory, output, *, pkg_command):
    """Unsigned native qualification only, without production manifests or tags."""
    from shared import package_catalogue
    if selection["mode"] != "development" or selection["producer_action"] != "build":
        raise ValueError("unsigned preview requires a sealed development build")
    validate_selected_git(selection, trusted_directory)
    _select_role(trusted_directory, selection["series"], "reader", pkg_command)
    packages = [producer / name for name in _files(producer) if name.endswith(".pkg")]
    if selection["component"] == "bind_runtime":
        validate_candidate(producer, selection, trusted_directory, pkg_command=pkg_command)
    else:
        inputs = _json(producer / "plugin-build-inputs.json")
        if inputs != _json(trusted_directory / "producer-inputs.json") or len(packages) != 1:
            raise ValueError("preview producer differs from trusted inputs")
        identity, edges = releases.query_package(packages[0], pkg_command)
        version = identity[1]
        component_tags("bind_plugin", selection["series"], version, selection["expected_fingerprint"])
        if (identity != ("os-bind-rp-devel", version, "opnsense/os-bind-rp-devel", inputs["package_abi"])
                or packages[0].name != f"os-bind-rp-devel-{version}.pkg"):
            raise ValueError("preview archive identity differs")
        package_checksums.verify_archive(pkg_command, packages[0])
        runtime = output.parent / "preview-runtime"
        receipt = _json(trusted_directory / "runtime-selection.json")
        fetch_frozen_component(REPOSITORY, receipt, runtime)
        if freeze_runtime(runtime, _json(trusted_directory / "bind920-profile.json"),
                          _json(trusted_directory / "upstream.json"), selection["series"],
                          requirement=plugin_requirement_from_makefile((trusted_directory / "plugin-package.mk").read_text()),
                          pkg_command=pkg_command) != receipt:
            raise ValueError("preview runtime differs from frozen selection")
        metadata = _metadata((producer / "build-metadata.txt").read_text())
        upstream = _json(trusted_directory / "upstream.json")
        creator, source = _role(_json(trusted_directory / "creator-target.json"), upstream, "creator")
        version = next(name.removeprefix("bind920-").removesuffix(".pkg") for name in receipt["packages"] if name.startswith("bind920-"))
        validate_plugin_metadata(metadata, upstream, inputs, source, version)
        record = releases.read_package_manifest(packages[0], pkg_command)
        requirement = plugin_requirement_from_makefile((trusted_directory / "plugin-package.mk").read_text())
        validate_plugin_archive(record, edges, requirement, development=True)
        packages += [runtime / name for name in receipt["packages"]]
    _select_role(trusted_directory, selection["series"], "creator", pkg_command)
    output.mkdir(parents=True)
    for path in producer.iterdir():
        shutil.copyfile(path, output / path.name)
    for package in packages:
        if not (output / package.name).exists():
            shutil.copyfile(package, output / package.name)
    subprocess.run([pkg_command, "repo", str(output)], check=True, stdout=sys.stderr)
    return dict(component=selection["component"], mode="development", packages={package.name: releases.sha256(package) for package in packages})


def stack_tags(series, plugin_version, runtime_fingerprint, plugin_fingerprint):
    component_tags("bind_plugin",series,plugin_version,plugin_fingerprint)
    releases.validate_bind_fingerprint(runtime_fingerprint)
    return releases.channel_tag(series), f"pkg-{series}-os-bind-rp-{plugin_version}-bind-{runtime_fingerprint}-plugin-{plugin_fingerprint}"


def materialize_stack(repository, runtime_receipt, plugin_receipt, output):
    from shared import package_catalogue
    series=runtime_receipt["series"]
    validate_receipt(runtime_receipt,"bind_runtime",series)
    validate_receipt(plugin_receipt,"bind_plugin",series)
    version=next(name.removeprefix("os-bind-rp-").removesuffix(".pkg") for name in plugin_receipt["packages"])
    _,tag=stack_tags(series,version,runtime_receipt["fingerprint"],plugin_receipt["fingerprint"])
    with tempfile.TemporaryDirectory() as temporary:
        snapshot=releases.snapshot_release(repository,tag,Path(temporary))
        if not snapshot.existed:
            return False
        actual=_files(snapshot.directory)
        incomplete="channel.json" not in actual
        if not incomplete:
            data=_json(snapshot.directory / "channel.json")
            incomplete=bool(set(_hashes(data.get("assets"))) - set(actual))
        if snapshot.draft and incomplete:
            output.parent.mkdir(parents=True,exist_ok=True)
            shutil.copytree(snapshot.directory,output.parent / "incomplete-draft")
            (output.parent / "incomplete-draft.json").write_text(json.dumps(dict(tag=tag,draft=True,assets=actual),sort_keys=True)+"\n")
            return False
        data=package_catalogue.validate(snapshot.directory)
        expected={"bind_runtime":package_catalogue.component_record(runtime_receipt),
                  "bind_plugin":package_catalogue.component_record(plugin_receipt)}
        if data["kind"]!="bind-stack" or data["components"]!=expected:
            raise ValueError("existing paired snapshot has conflicting original receipts")
        shutil.copytree(snapshot.directory,output)
        return True


def _initialized(repository, series, abi):
    with tempfile.TemporaryDirectory() as temporary:
        stack=releases.snapshot_release(repository,releases.channel_tag(series),Path(temporary))
        if stack.existed:
            return True
    endpoint=f"repos/{repository}/contents/{releases.series_abi_path(abi,series)}/channel.json?ref=gh-pages"
    result=subprocess.run(["gh","api",endpoint],capture_output=True,text=True)
    if result.returncode:
        if "HTTP 404" in result.stderr:
            return False
        raise RuntimeError(result.stderr.strip() or "cannot determine registered feed presence")
    payload=json.loads(result.stdout)
    if not isinstance(payload,dict) or payload.get("type")!="file":
        raise ValueError("invalid registered feed presence")
    return True


def write_composition(repository_root, *, component, series, initialized, components):
    record=dict(schema=1,component=component,series=series,initialized=initialized,components=components)
    path=repository_root / "composition-inputs.json"
    path.write_text(json.dumps(record,sort_keys=True,indent=2)+"\n")
    digest=releases.sha256(path)
    load_composition(repository_root,component=component,series=series,composition_sha256=digest)
    return digest


def load_composition(repository_root, *, component, series, composition_sha256):
    releases.validate_bind_fingerprint(composition_sha256)
    path=repository_root / "composition-inputs.json"
    if path.is_symlink() or releases.sha256(path)!=composition_sha256:
        raise ValueError("composition digest differs from signer job")
    data=_json(path)
    if (not isinstance(data,dict) or set(data)!={"schema","component","series","initialized","components"}
            or type(data["schema"]) is not int or data["schema"]!=1
            or component not in (*KINDS,"dhcp") or data["component"]!=component or data["series"]!=series
            or type(data["initialized"]) is not bool or not isinstance(data["components"],dict)
            or set(data["components"])!={"bind_runtime","bind_plugin","dhcp"}):
        raise ValueError("invalid sealed composition")
    for kind,receipt in data["components"].items():
        if receipt is not None:
            validate_receipt(receipt,kind,series)
    if (data["components"]["bind_runtime"] is None or data["components"][component] is None
            or data["components"]["bind_plugin"] is None and (component!="bind_runtime" or data["initialized"])
            or series!="26.7" and data["components"]["dhcp"] is not None):
        raise ValueError("incomplete initialized composition")
    abi=data["components"]["bind_runtime"]["package_abi"]
    if any(receipt is not None and receipt["package_abi"]!=abi for receipt in data["components"].values()):
        raise ValueError("composition ABI differs")
    return data


def check_composition_peers(repository, composition, *, owned_must_match=False):
    from shared import package_catalogue
    with tempfile.TemporaryDirectory() as temporary:
        for kind,receipt in composition["components"].items():
            if kind==composition["component"] and not owned_must_match:
                continue
            observed=package_catalogue.fetch(repository,kind,composition["series"],Path(temporary) / kind)
            if observed!=receipt:
                raise RuntimeError("registered component changed after qualified composition")


def validate_repository_root(repository_root, selection, trusted_directory, composition):
    from shared import package_catalogue
    kind,series=selection["component"],selection["series"]
    current=repository_root / "component/current"
    manifest=validate_component(current,kind,series)
    receipt=component_receipt(current,kind,series)
    if (receipt!=composition["components"][kind]
            or _files(current)!=_files(repository_root / "component/snapshot")
            or receipt["fingerprint"]!=selection["expected_fingerprint"]):
        raise ValueError("owned component differs from sealed qualification")
    if selection["producer_action"]=="reuse":
        if receipt!=_json(trusted_directory / "cached-component.json"):
            raise ValueError("qualified cache differs from controller")
    else:
        if manifest["control_commit"]!=selection["control_commit"]:
            raise ValueError("fresh owned control differs")
        names=RUNTIME_SIDECARS-{"bind920-provenance.json"} if kind=="bind_runtime" else PLUGIN_SIDECARS-{"plugin-build-inputs.json","build-metadata.txt"}
        if any((current / name).read_bytes()!=(trusted_directory / name).read_bytes() for name in names):
            raise ValueError("fresh owned originals differ from controller")
        if kind=="bind_runtime" and manifest["profile_commit"]!=selection["profile_commit"]:
            raise ValueError("fresh profile audit differs")
        if kind=="bind_plugin":
            runtime=composition["components"]["bind_runtime"]
            original=manifest["built_against_runtime"]
            if (manifest["source_commit"]!=selection["source_commit"]
                    or _json(current / "plugin-build-inputs.json")!=_json(trusted_directory / "producer-inputs.json")
                    or original["snapshot_tag"]!=runtime["snapshot_tag"]
                    or original["fingerprint"]!=runtime["fingerprint"] or original["packages"]!=runtime["packages"]):
                raise ValueError("fresh plugin build-time runtime differs")
    if kind=="bind_plugin" and composition["components"]["bind_runtime"]!=_json(trusted_directory / "runtime-selection.json"):
        raise ValueError("plugin qualification substituted frozen runtime")
    if composition["components"]["bind_plugin"] is None:
        if (repository_root / "stack").exists() or (repository_root / "combined").exists():
            raise ValueError("runtime bootstrap must not expose incomplete feed")
        return manifest
    pair={key:package_catalogue.component_record(composition["components"][key]) for key in KINDS}
    stack=package_catalogue.validate(repository_root / "stack/current")
    if (stack["components"]!=pair
            or _files(repository_root / "stack/current")!=_files(repository_root / "stack/snapshot")):
        raise ValueError("paired stack differs from sealed component receipts")
    combined=package_catalogue.validate(repository_root / "combined")
    expected={key:package_catalogue.component_record(receipt) for key,receipt in composition["components"].items() if receipt is not None}
    if combined["components"]!=expected:
        raise ValueError("combined feed differs from sealed receipts")
    approved=_json(trusted_directory / "bind920-profile.json")
    if _json(repository_root / "stack/current/bind_runtime-bind920-profile.json")!=approved:
        raise ValueError("qualified runtime differs from approved control recipe")
    return manifest


def stage_repository(producer, selection, trusted, output, key, *, pkg_command):
    from shared import package_catalogue
    kind,series=selection["component"],selection["series"]
    stage_component(producer,selection,trusted,output / "component",key,pkg_command=pkg_command)
    own=component_receipt(output / "component/current",kind,series)
    initialized=_initialized(REPOSITORY,series,own["package_abi"])
    receipts=dict.fromkeys(("bind_runtime","bind_plugin","dhcp"))
    receipts[kind]=own
    with tempfile.TemporaryDirectory() as temporary:
        intake=Path(temporary)
        directories={kind:output / "component/current"}
        for peer in ("bind_runtime","bind_plugin","dhcp"):
            if peer==kind:
                continue
            directory=intake / peer
            if peer=="bind_runtime":
                selected=_json(trusted / "runtime-selection.json")
                fetch_frozen_component(REPOSITORY,selected,directory)
            else:
                selected=package_catalogue.fetch(REPOSITORY,peer,series,directory)
            receipts[peer]=selected
            if selected is not None:
                directories[peer]=directory
        if receipts["bind_plugin"] is None:
            if initialized:
                raise ValueError("initialized series cannot omit plugin peer")
        else:
            (output / "stack").mkdir(parents=True,exist_ok=True)
            stack=output / "stack/current"
            if not materialize_stack(REPOSITORY,receipts["bind_runtime"],receipts["bind_plugin"],stack):
                package_catalogue.stage_bind_stack(directories["bind_runtime"],directories["bind_plugin"],stack,
                    series=series,key=key,creator_target=trusted / "creator-target.json",upstream=trusted / "upstream.json",
                    approved_profile=trusted / "bind920-profile.json",runtime_approval=trusted / "runtime-approval.mk")
            shutil.copytree(stack,output / "stack/snapshot")
            package_catalogue.stage(directories["bind_runtime"],directories["bind_plugin"],directories.get("dhcp"),
                output / "combined",series=series,key=key,creator_target=trusted / "creator-target.json",upstream=trusted / "upstream.json",
                approved_profile=trusted / "bind920-profile.json",runtime_approval=trusted / "runtime-approval.mk")
        digest=write_composition(output,component=kind,series=series,initialized=initialized,components=receipts)
        composition=load_composition(output,component=kind,series=series,composition_sha256=digest)
        check_composition_peers(REPOSITORY,composition)
        validate_repository_root(output,selection,trusted,composition)
    return digest


def stack_can_advance(current, staged):
    from shared import package_catalogue
    before,after=package_catalogue.validate(current),package_catalogue.validate(staged)
    if before["kind"]!="bind-stack" or after["kind"]!="bind-stack" or before["series"]!=after["series"]:
        return False
    for kind in KINDS:
        if before["components"][kind]==after["components"][kind]:
            continue
        if not component_record_can_advance(_json(current / f"{kind}-channel.json"),
                _json(staged / f"{kind}-channel.json"),kind,compare_versions=compare_runtime_versions):
            return False
    return True


def publish_component(repository, repository_root, trusted_directory, recovery, *, component,
                      series, selection_sha256, composition_sha256):
    from shared import package_catalogue
    selection=load_selection(trusted_directory,component=component,series=series,selection_sha256=selection_sha256)
    if selection["mode"]!="production":
        raise ValueError("publication requires sealed production inputs")
    validate_selected_git(selection,trusted_directory)
    composition=load_composition(repository_root,component=component,series=series,composition_sha256=composition_sha256)
    owned=validate_repository_root(repository_root,selection,trusted_directory,composition)
    subprocess.run(["git","fetch","--no-tags","origin","master"],check=True,stdout=sys.stderr)
    master=_commit(subprocess.check_output(["git","rev-parse","FETCH_HEAD"],text=True).strip())
    _ensure_commits(owned["control_commit"])
    if not releases.commit_is_ancestor_or_equal(owned["control_commit"],master):
        raise ValueError("owned original control is not merged into registered master")
    if _initialized(repository,series,owned["package_abi"])!=composition["initialized"]:
        # The first complete publication can initialize Releases before Pages
        # succeeds. Continue only that exact qualified publication on retry.
        if composition["initialized"] or composition["components"]["bind_plugin"] is None:
            raise RuntimeError("registered initialization changed after qualification")
        check_composition_peers(repository,composition,owned_must_match=True)
        runtime,plugin=(composition["components"][kind] for kind in KINDS)
        version=next(name.removeprefix("os-bind-rp-").removesuffix(".pkg") for name in plugin["packages"])
        with tempfile.TemporaryDirectory() as temporary:
            for index,tag in enumerate(stack_tags(series,version,runtime["fingerprint"],plugin["fingerprint"])):
                published=releases.snapshot_release(repository,tag,Path(temporary) / str(index))
                if (not published.existed or published.draft
                        or releases.directory_checksums(published.directory)!=_files(repository_root / "stack/current")):
                    raise RuntimeError("registered initialization differs from the exact qualified stack")
    check_composition_peers(repository,composition)
    receipt=composition["components"][component]
    def validate_owned(directory):
        validate_component(directory,component,series)
        check_composition_peers(repository,composition)
    groups=[dict(current_tag=receipt["current_tag"],snapshot_tag=receipt["snapshot_tag"],
        directory=repository_root / "component/current",validate=validate_owned,
        can_advance=lambda before,after:component_can_advance(before,after,component,compare_versions=compare_runtime_versions))]
    if composition["components"]["bind_plugin"] is not None:
        runtime,plugin=(composition["components"][kind] for kind in KINDS)
        version=next(name.removeprefix("os-bind-rp-").removesuffix(".pkg") for name in plugin["packages"])
        current,snapshot=stack_tags(series,version,runtime["fingerprint"],plugin["fingerprint"])
        def validate_stack(directory):
            if package_catalogue.validate(directory)["kind"]!="bind-stack":
                raise ValueError("full stack publication requires paired stack")
            check_composition_peers(repository,composition)
        groups.append(dict(current_tag=current,snapshot_tag=snapshot,directory=repository_root / "stack/current",
                           validate=validate_stack,can_advance=stack_can_advance))
    recovery.mkdir(parents=True,exist_ok=False)
    (recovery / "qualified-inputs.json").write_text(json.dumps(dict(selection_sha256=selection_sha256,
        composition_sha256=composition_sha256,composition=composition),sort_keys=True,indent=2)+"\n")
    phase="releases"
    try:
        releases.publish_channels(repository,groups,recovery / "releases")
        phase="registered-readback"
        check_composition_peers(repository,composition,owned_must_match=True)
        if composition["components"]["bind_plugin"] is not None:
            phase="pages"
            package_catalogue.publish(repository,repository_root,recovery / "pages",component=component,series=series,
                                      composition_sha256=composition_sha256)
            phase="latest"
            releases.mark_latest_package_channel(repository)
    except Exception as error:
        (recovery / "failure.json").write_text(json.dumps(dict(phase=phase,error=str(error),
            qualified_releases_may_be_ahead=phase!="releases"),sort_keys=True,indent=2)+"\n")
        raise


def retention_plan(releases, current_components, current_stack, *, keep=5):
    from bind.shared import retention
    return retention.retention_plan(releases,current_components,current_stack,keep=keep)


def freeze_runtime(directory, approved_profile, upstream, series, *, requirement=None, pkg_command):
    manifest = validate_component(directory, "bind_runtime", series, pkg_command=pkg_command)
    profile = bind920_profile.validate_profile(approved_profile)
    upstream = metadata_profile.validate_profile(upstream, series)
    provenance = _json(directory / "bind920-provenance.json")
    if (_json(directory / "bind920-profile.json") != profile
            or provenance["freebsd_release"] != upstream["freebsd_release"]
            or manifest["package_abi"] != f"FreeBSD:{upstream['freebsd_release'].split('.')[0]}:amd64"
            or requirement is not None and compare_native_versions(
                pkg_command, bind920_profile.package_version(profile), requirement["minimum_version"]) == "<"):
        raise ValueError(f"{series} runtime does not match approved {bind920_profile.package_version(profile)} recipe; publish runtime first and retry")
    from shared import package_catalogue
    package_catalogue.verify_repository([directory / name for name in manifest["packages"]],
                                       directory.resolve().as_uri(), pkg_command=pkg_command)
    return component_receipt(directory, "bind_runtime", series)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    tags = commands.add_parser("tags")
    tags.add_argument("--component", choices=KINDS, required=True)
    tags.add_argument("--series", required=True)
    tags.add_argument("--version", required=True)
    tags.add_argument("--fingerprint", required=True)
    tags.add_argument("--output", type=Path)
    inputs = commands.add_parser("inputs")
    inputs.add_argument("--component", choices=KINDS, required=True)
    inputs.add_argument("--series", required=True)
    inputs.add_argument("--upstream", type=Path, required=True)
    inputs.add_argument("--creator-target", type=Path)
    inputs.add_argument("--reader-target", type=Path)
    recipe = inputs.add_mutually_exclusive_group(required=True)
    recipe.add_argument("--recipe-sha256")
    recipe.add_argument("--recipe", type=Path)
    inputs.add_argument("--profile", type=Path)
    inputs.add_argument("--runtime-approval", type=Path)
    inputs.add_argument("--package-definition", type=Path)
    inputs.add_argument("--tools-repository", type=Path)
    inputs.add_argument("--source-commit")
    inputs.add_argument("--output", type=Path)
    inputs.add_argument("--trusted-directory", type=Path)
    inputs.add_argument("--intake-directory", type=Path)
    inputs.add_argument("--repository", default="resolver-plugins/repository")
    inputs.add_argument("--mode", choices=("production", "development"), default="production")
    inputs.add_argument("--control-commit")
    inputs.add_argument("--profile-commit")
    select = commands.add_parser("select")
    select.add_argument("--component", choices=KINDS, required=True)
    select.add_argument("--inputs", type=Path, required=True)
    select.add_argument("--directory", type=Path)
    select.add_argument("--output", type=Path, required=True)
    for name in ("validate", "freeze-runtime", "authenticate"):
        command = commands.add_parser(name)
        command.add_argument("--component", choices=("bind_plugin",) if name == "freeze-runtime" else KINDS, required=True)
        command.add_argument("--series", required=True)
        command.add_argument("--trusted-directory", type=Path, required=True)
        command.add_argument("--selection-sha256", required=True)
        command.add_argument("--directory", type=Path, required=name in ("freeze-runtime", "authenticate"))
        command.add_argument("--pkg-command", required=name in ("freeze-runtime", "authenticate"))
        command.add_argument("--output", type=Path)
        if name == "validate":
            command.add_argument("--expected-control-commit")
            command.add_argument("--profile-control-commit")
            command.add_argument("--repository-root", type=Path)
            command.add_argument("--composition-sha256")
    stage = commands.add_parser("stage")
    stage.add_argument("--component", choices=KINDS, required=True)
    stage.add_argument("--series", required=True)
    stage.add_argument("--trusted-directory", type=Path, required=True)
    stage.add_argument("--selection-sha256", required=True)
    stage.add_argument("--directory", type=Path, required=True)
    stage.add_argument("--output", type=Path, required=True)
    stage.add_argument("--key", type=Path)
    stage.add_argument("--pkg-command", required=True)
    publish = commands.add_parser("publish")
    publish.add_argument("--component", choices=KINDS, required=True)
    publish.add_argument("--series", required=True)
    publish.add_argument("--trusted-directory", type=Path, required=True)
    publish.add_argument("--selection-sha256", required=True)
    publish.add_argument("--composition-sha256", required=True)
    publish.add_argument("--repository-root", type=Path, required=True)
    publish.add_argument("--repository", required=True)
    publish.add_argument("--recovery", type=Path, required=True)
    publish.add_argument("--retention-only", action="store_true")
    args = parser.parse_args()
    if args.command == "tags":
        current, snapshot = component_tags(args.component, args.series, args.version, args.fingerprint)
        result = dict(current_tag=current, snapshot_tag=snapshot)
    elif args.command == "inputs":
        if args.trusted_directory is not None:
            if any(value is None for value in (args.recipe, args.runtime_approval, args.tools_repository,
                                               args.intake_directory, args.control_commit, args.profile_commit)):
                parser.error("controller inputs require --recipe, --runtime-approval, --tools-repository, --intake-directory, --control-commit and --profile-commit")
            result = prepare_selection(args.trusted_directory, component=args.component, series=args.series,
                mode=args.mode, control_commit=args.control_commit, source_commit=args.source_commit,
                profile_commit=args.profile_commit, upstream=args.upstream, runtime_approval=args.runtime_approval,
                package_definition=args.package_definition, tools_repository=args.tools_repository,
                recipe=args.recipe, repository=args.repository,
                intake_directory=args.intake_directory, creator_target=args.creator_target, reader_target=args.reader_target)
            encoded = json.dumps(result, sort_keys=True, indent=2) + "\n"
            if args.output is not None:
                args.output.write_text(encoded, encoding="utf-8")
            else:
                print(encoded, end="")
            return
        if args.creator_target is None or args.recipe_sha256 is None:
            parser.error("raw producer inputs require --creator-target and --recipe-sha256")
        upstream = metadata_profile.validate_profile(_json(args.upstream), args.series)
        creator, _ = _role(_json(args.creator_target), upstream, "creator")
        if args.component == "bind_runtime":
            if args.profile is None:
                parser.error("runtime inputs require --profile")
            result = bind920_profile.producer_inputs(bind920_profile.load_profile(args.profile), args.series,
                upstream["freebsd_release"], "x86_64", creator, recipe_sha256=args.recipe_sha256)
        else:
            if args.package_definition is None or args.source_commit is None:
                parser.error("plugin inputs require --package-definition and --source-commit")
            plugin_requirement_from_makefile(args.package_definition.read_text())
            result = validate_plugin_inputs(dict(schema=1, source_commit=args.source_commit, series=args.series,
                package_abi=creator["abi"], freebsd_release=upstream["freebsd_release"], package_creator=creator,
                build_recipe_sha256=args.recipe_sha256))
    elif args.command == "select":
        result = select_producer_action(args.component, _json(args.inputs), args.directory)
    elif args.command == "publish":
        if args.retention_only:
            from bind.shared import retention
            retention.prune(args.repository,args.repository_root,args.trusted_directory,args.recovery,
                component=args.component,series=args.series,selection_sha256=args.selection_sha256,
                composition_sha256=args.composition_sha256)
        else:
            publish_component(args.repository,args.repository_root,args.trusted_directory,args.recovery,
                component=args.component,series=args.series,selection_sha256=args.selection_sha256,
                composition_sha256=args.composition_sha256)
        return
    elif args.command == "stage":
        selection = load_selection(args.trusted_directory, component=args.component, series=args.series,
                                   selection_sha256=args.selection_sha256)
        if args.output.exists():
            raise ValueError("stage repository output must be absent")
        with tempfile.TemporaryDirectory() as temporary:
            staged = Path(temporary) / "repository"
            composition_sha256=None
            if selection["mode"] == "development":
                if args.key is not None:
                    raise ValueError("development stage cannot receive a production key")
                stage_preview(args.directory, selection, args.trusted_directory, staged / "preview", pkg_command=args.pkg_command)
            else:
                composition_sha256=stage_repository(args.directory,selection,args.trusted_directory,staged,
                    args.key,pkg_command=args.pkg_command)
            shutil.copytree(staged, args.output)
        print(json.dumps(dict(composition_sha256=composition_sha256), sort_keys=True))
        return
    else:
        result = load_selection(args.trusted_directory, component=args.component, series=args.series,
                                selection_sha256=args.selection_sha256)
        if args.command == "validate":
            if (args.expected_control_commit is None) != (args.profile_control_commit is None):
                parser.error("privileged validation requires both control commitments")
            if args.expected_control_commit is not None:
                validate_privileged_selection(result, args.trusted_directory,
                    expected_control_commit=args.expected_control_commit, profile_control_commit=args.profile_control_commit)
            if (args.repository_root is None) != (args.composition_sha256 is None):
                parser.error("repository validation requires root and external composition digest")
            if args.repository_root is not None:
                composition = load_composition(args.repository_root, component=args.component, series=args.series,
                                               composition_sha256=args.composition_sha256)
                validate_repository_root(args.repository_root, result, args.trusted_directory, composition)
        if args.command == "authenticate":
            with tempfile.TemporaryDirectory() as temporary:
                _, result = authenticate_candidate(args.directory, result, args.trusted_directory, Path(temporary),
                                                   pkg_command=args.pkg_command)
        elif args.command == "freeze-runtime":
            target = target_pkg.load_target(args.trusted_directory / "reader-target.json", args.series,
                                           upstream=args.trusted_directory / "upstream.json", role="reader")
            target_pkg.verify_target_pkg(target, args.pkg_command, pkg_static_path=Path(args.pkg_command))
            result = freeze_runtime(args.directory, _json(args.trusted_directory / "bind920-profile.json"),
                                    _json(args.trusted_directory / "upstream.json"), args.series,
                                    requirement=plugin_requirement_from_makefile((args.trusted_directory / "plugin-package.mk").read_text()),
                                    pkg_command=args.pkg_command)
            if result != _json(args.trusted_directory / "runtime-selection.json"):
                raise ValueError("authenticated runtime differs from frozen controller receipt")
        elif args.directory is not None:
            result = validate_candidate(args.directory, result, args.trusted_directory, pkg_command=args.pkg_command)
    encoded = json.dumps(result, sort_keys=True, indent=2) + "\n"
    if args.output is not None:
        args.output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")


if __name__ == "__main__":
    main()
