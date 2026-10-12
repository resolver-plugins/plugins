#!/usr/bin/env python3
"""Sealed BIND qualification metadata and independent native observations."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from bind.shared import components as bind
from shared import package_catalogue as catalogue, package_checksums, release_channel as releases, target_pkg

PKG = "/usr/local/sbin/pkg-static"


def preview_packages(root, selection, trusted):
    directory = root / "preview"
    actual = bind._files(directory)
    profile = bind._json(trusted / "bind920-profile.json")
    version = bind.bind920_profile.package_version(profile)
    runtime = {f"{name}-{version}.pkg" for name in ("bind-tools", "bind920")}
    if selection["component"] == "bind_runtime":
        owned = runtime | {"bind920-provenance.json"}
        with tempfile.TemporaryDirectory() as temporary:
            raw = Path(temporary)
            for name in owned:
                shutil.copyfile(directory / name, raw / name)
            bind.validate_candidate(raw, selection, trusted, pkg_command=None)
        packages = runtime
    else:
        inputs = bind._json(directory / "plugin-build-inputs.json")
        if inputs != bind._json(trusted / "producer-inputs.json"):
            raise ValueError("preview inputs differ from sealed producer inputs")
        metadata = bind._metadata((directory / "build-metadata.txt").read_text())
        if metadata["source_commit"] != selection["source_commit"] or metadata["series"] != selection["series"]:
            raise ValueError("preview source differs from sealed source")
        archives = [name for name in actual if name.startswith("os-bind-rp-devel-") and name.endswith(".pkg")]
        if len(archives) != 1:
            raise ValueError("preview must contain one development plugin archive")
        version = archives[0].removeprefix("os-bind-rp-devel-").removesuffix(".pkg")
        bind.component_tags("bind_plugin", selection["series"], version, selection["expected_fingerprint"])
        plugin = {f"os-bind-rp-devel-{version}.pkg"}
        owned = plugin | {"plugin-build-inputs.json", "build-metadata.txt"}
        frozen = bind._json(trusted / "runtime-selection.json")
        if set(frozen["packages"]) != runtime or any(actual.get(name) != digest for name, digest in frozen["packages"].items()):
            raise ValueError("preview runtime differs from frozen receipt")
        packages = runtime | plugin
    if (set(actual) - bind.CATALOGUE_ASSETS != owned | packages
            or "meta.conf" not in actual or not bind.CATALOGUE_FILES.intersection(actual)):
        raise ValueError("preview output differs from unsigned qualification files")
    return [directory / name for name in sorted(packages)]


def preflight(root, trusted, component, series, mode):
    selection = bind.load_selection(trusted, component=component, series=series,
                                    selection_sha256=os.environ.get("RP_SELECTION_SHA256"))
    bind.validate_selected_git(selection, trusted)
    entries = []
    if selection["mode"] == "development":
        if mode != "local":
            raise ValueError("development qualification is local only")
        packages = preview_packages(root, selection, trusted)
        entries.append(dict(directory=str((root / "preview").resolve()),
                            packages=[str(path.resolve()) for path in packages], unsigned=True, tag=None))
        composition = None
    else:
        composition = bind.load_composition(root, component=component, series=series,
            composition_sha256=os.environ.get("RP_COMPOSITION_SHA256"))
        bind.validate_repository_root(root, selection, trusted, composition)
        owned = composition["components"][component]
        for name, tag in (("current", owned["current_tag"]), ("snapshot", owned["snapshot_tag"])):
            directory = root / "component" / name
            entries.append(dict(directory=str(directory.resolve()),
                packages=[str((directory / name).resolve()) for name in sorted(owned["packages"])], unsigned=False, tag=tag))
        if composition["components"]["bind_plugin"] is not None:
            plugin = composition["components"]["bind_plugin"]
            version = next(name.removeprefix("os-bind-rp-").removesuffix(".pkg") for name in plugin["packages"])
            current, snapshot = bind.stack_tags(series, version, composition["components"]["bind_runtime"]["fingerprint"], plugin["fingerprint"])
            for path, tag in (("stack/current", current), ("stack/snapshot", snapshot), ("combined", None)):
                directory = root / path
                record = catalogue.validate(directory)
                packages = sorted(name for record in record["components"].values() for name in record["packages"])
                entries.append(dict(directory=str(directory.resolve()), packages=[str((directory / name).resolve()) for name in packages], unsigned=False, tag=tag))
    abi = bind._json(trusted / "reader-target.json")["package_creator"]["abi"]
    for entry in entries:
        entry["url"] = ("https://github.com/resolver-plugins/repository/releases/download/" + entry["tag"]
            if mode == "public" and entry["tag"] else
            "https://resolver-plugins.github.io/repository/" + releases.series_abi_path(abi, series)
            if mode == "public" else Path(entry["directory"]).as_uri())
    return dict(selection=selection, composition=composition, repositories=entries)


def save_evidence(directory, name, value):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / (name + ".json")).write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")


def authenticate(plan, trusted, evidence, mode):
    series = plan["selection"]["series"]
    target = target_pkg.load_target(trusted / "reader-target.json", series, upstream=trusted / "upstream.json", role="reader")
    target_pkg.verify_target_pkg(target, PKG, pkg_static_path=Path(PKG))
    observations, dependencies = [], set()
    for entry in plan["repositories"]:
        directory = Path(entry["directory"])
        packages = [Path(path) for path in entry["packages"]]
        if mode == "public":
            for attempt in range(20):
                try:
                    releases.verify_abi_endpoint(entry["url"], directory)
                    break
                except (OSError, RuntimeError):
                    if attempt == 19:
                        raise
                    time.sleep(30)
        identities = []
        runtime_edges = None
        for archive in packages:
            identity, edges = releases.query_package(archive, PKG)
            name, version, origin, abi = identity
            if (name not in {"bind-tools", "bind920", "os-bind-rp", "os-bind-rp-devel", "os-dhcp-interface-ha"}
                    or archive.name != f"{name}-{version}.pkg" or abi != target.identity.abi
                    or origin != ("dns/" if name in {"bind-tools", "bind920"} else "opnsense/") + name):
                raise ValueError("archive identity differs from qualified payload")
            if name == "bind920":
                runtime_edges = (version, edges)
            if name.startswith("os-bind-rp"):
                record = releases.read_package_manifest(archive, PKG)
                policy_path = trusted / "bind-compatibility.json" if entry["unsigned"] else directory / (
                    "bind-compatibility.json" if (directory / "bind-compatibility.json").exists() else "bind_plugin-bind-compatibility.json")
                policy = bind._json(policy_path)
                if (record.get("dep_formula") != f"bind920 >= {policy['minimum_version']}"
                        or "os-bind" not in record.get("conflicts", []) or any(edge[0] == "bind920" for edge in edges)):
                    raise ValueError("archive dependency or replacement policy differs")
            checksums = package_checksums.verify_archive(PKG, archive)
            identities.append(dict(archive=archive.name, sha256=releases.sha256(archive), identity=identity, files=checksums))
            dependencies.update(edge[0] for edge in edges)
        if runtime_edges is not None and ("bind-tools", "dns/bind-tools", runtime_edges[0]) not in runtime_edges[1]:
            raise ValueError("archive runtime pair dependency differs")
        catalogue.verify_repository(packages, entry["url"], pkg_command=PKG, unsigned=entry["unsigned"])
        observations.append(dict(directory=entry["directory"], url=entry["url"], unsigned=entry["unsigned"], archives=identities))
    dependencies -= {"pkg", "bind-tools", "bind920", "os-bind", "os-bind-rp", "os-bind-rp-devel", "os-dhcp-interface-ha"}
    if any(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_+.-]*", name) is None for name in dependencies):
        raise ValueError("unsafe external package dependency")
    save_evidence(evidence, "catalogues", dict(reader=target.record(), repositories=observations))
    (evidence / "dependencies.txt").write_text("".join(name + "\n" for name in sorted(dependencies)))


def installed(packages, evidence, label, *, exact=True, policy=None):
    observations = []
    names = []
    for archive in packages:
        expected, _ = releases.query_package(archive, PKG)
        name = expected[0]
        actual = subprocess.check_output([PKG, "query", "-e", f"%n = {name}", "%n|%v|%o|%q"], text=True).strip().split("|")
        if (len(actual) != 4 or (actual[0], actual[2], actual[3]) != (expected[0], expected[2], expected[3])
                or tuple(actual) != expected and (exact or name not in {"bind-tools", "bind920"})):
            raise ValueError("installed identity differs from exact archive")
        rows = subprocess.check_output([PKG, "query", "-e", f"%n = {name}", "%Fp|%Fs"], text=True).splitlines()
        checksums = [tuple(row.split("|", 1)) for row in rows]
        if not checksums or any(len(row) != 2 or not row[0] or package_checksums.CHECKSUM_PATTERN.fullmatch(row[1]) is None for row in checksums):
            raise ValueError("installed file checksums are incomplete")
        eligible_runtime = not exact and name in {"bind-tools", "bind920"}
        if not eligible_runtime and sorted(checksums) != sorted(package_checksums.verify_archive(PKG, archive)):
            raise ValueError("installed file set differs from exact archive")
        for path, _ in checksums:
            owner = subprocess.check_output([PKG, "which", "-q", path], text=True).strip()
            if owner != f"{actual[0]}-{actual[1]}":
                raise ValueError("installed file owner differs from package")
        names.append(name)
        observations.append(dict(staged=expected, installed=actual, files=checksums,
                                 validation="eligible-runtime" if eligible_runtime else "exact-archive"))
    if not exact:
        records = {row["installed"][0]: row["installed"] for row in observations}
        def compare(left, right):
            return subprocess.check_output([PKG, "version", "-t", left, right], text=True).strip()
        if not bind.bind_compatibility.is_eligible(policy, tuple(records["bind920"][:3]), tuple(records["bind-tools"][:3]), compare):
            raise ValueError("installed upstream runtime is ineligible")
        edges = subprocess.check_output([PKG, "query", "-e", "%n = bind920", "%dn|%do|%dv"], text=True).splitlines()
        if f"bind-tools|dns/bind-tools|{records['bind-tools'][1]}" not in edges:
            raise ValueError("installed upstream runtime pair dependency differs")
    if any(name.startswith("os-bind-rp") for name in names):
        official = subprocess.run([PKG, "query", "-e", "%n = os-bind", "%n"], capture_output=True, text=True)
        if official.returncode not in {0, 1} or official.stdout.strip():
            raise ValueError("official os-bind remains installed")
    subprocess.run([PKG, "check", "-s", *names], check=True, stdout=sys.stderr)
    save_evidence(evidence, label, observations)


def prepare_installer(plan, trusted, evidence):
    directory = Path(next(entry["directory"] for entry in plan["repositories"]
                          if Path(entry["directory"]).name == "combined"))
    policy = bind._json(trusted / "bind-compatibility.json")
    retained = {}
    for name in ("bind-tools", "bind920"):
        result = subprocess.run([PKG, "query", "-e", f"%n = {name}", "%n|%v|%o|%q"],
                                capture_output=True, text=True)
        if result.returncode not in (0, 1):
            raise RuntimeError("cannot read pre-installer runtime")
        fields = result.stdout.strip().split("|")
        archive = next(directory.glob(name + "-*.pkg"))
        expected, _ = releases.query_package(archive, PKG)
        if (len(fields) == 4 and (fields[0], fields[2], fields[3]) == (expected[0], expected[2], expected[3])
                and subprocess.check_output([PKG, "version", "-t", fields[1], expected[1]], text=True).strip() in {"=", ">"}):
            files = subprocess.check_output([PKG, "query", "-e", f"%n = {name}", "%Fp|%Fs"], text=True).splitlines()
            retained[name] = dict(identity=fields, files=sorted(row.split("|", 1) for row in files))
    def compare(left, right):
        return subprocess.check_output([PKG, "version", "-t", left, right], text=True).strip()
    if len(retained) != 2 or not bind.bind_compatibility.is_eligible(
            policy, tuple(retained["bind920"]["identity"][:3]), tuple(retained["bind-tools"]["identity"][:3]), compare):
        retained = {}
    config = Path("/conf/config.xml")
    save_evidence(evidence, "installer-before", dict(config_sha256=releases.sha256(config),
        config_mode=stat.S_IMODE(config.stat().st_mode), retained_runtime=retained))
    shim = evidence / "installer-bin"
    shim.mkdir()
    version = "26.1.11_10" if plan["selection"]["series"] == "26.1" else plan["selection"]["series"]
    for name, body in {
        "opnsense-version": "#!/bin/sh\nprintf '%s\\n' 'OPNsense " + version + "'\n",
        "observe-pkg-static": "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"${RP_VERIFY_INSTALLER_LOG:?}\"\nexec /usr/local/sbin/pkg-static \"$@\"\n",
    }.items():
        path = shim / name
        path.write_text(body)
        path.chmod(0o755)
    (evidence / "installer-approval").write_text("yes\n")


def audit_installer(plan, evidence):
    before = bind._json(evidence / "installer-before.json")
    state, transaction = evidence / "installer-state", evidence / "installer-transaction"
    backup = state / "config.xml.bak"
    if (stat.S_IMODE(state.stat().st_mode) != 0o700
            or releases.sha256(backup) != before["config_sha256"]
            or stat.S_IMODE(backup.stat().st_mode) != before["config_mode"]
            or not (state / "pkg-install.dry-run.txt").read_text().strip()):
        raise ValueError("installer recovery state or configuration backup differs")
    entry = next(entry for entry in plan["repositories"] if Path(entry["directory"]).name == "combined")
    expected = {Path(path).name: releases.sha256(Path(path)) for path in entry["packages"]
                if not Path(path).name.startswith("os-dhcp-interface-ha-")}
    actual = {}
    for row in (state / "candidate-sha256.txt").read_text().splitlines():
        fields = row.split("|")
        if len(fields) != 3:
            raise ValueError("invalid installer archive evidence")
        identity, digest, archive = fields
        path = Path(archive)
        name = identity + ".pkg"
        if (name in actual or name not in expected
                or not path.resolve().is_relative_to((transaction / "verified-repository").resolve())
                or releases.sha256(path) != digest or expected[name] != digest):
            raise ValueError("installer archive differs from frozen staged payload")
        actual[name] = digest
    if actual != expected:
        raise ValueError("installer archive evidence omits a BIND package")
    prefix = "-o REPOS_DIR=" + str(transaction) + "/isolated-repos install "
    planned, installed_plugin = False, False
    for command in (evidence / "installer-pkg.log").read_text().splitlines():
        if command.startswith(prefix + "-n -r resolver-verified "):
            planned = True
        if re.search(r"(?:^| )install -y ", command):
            if not planned or not command.startswith(prefix + "-y -r resolver-verified "):
                raise ValueError("installer transaction bypassed isolated dry run")
            installed_plugin |= " os-bind-rp-" in command
    if not installed_plugin:
        raise ValueError("installer log omits the planned plugin transaction")
    observations = bind._json(evidence / "public-installer.json")
    if len(observations) != 3 or {row["installed"][0] for row in observations} != {"bind-tools", "bind920", "os-bind-rp"}:
        raise ValueError("installer evidence omits an installed BIND package")
    for row in observations:
        name = row["installed"][0]
        retained = before["retained_runtime"].get(name)
        expected_identity = retained["identity"] if retained else row["staged"]
        if row["installed"] != expected_identity or retained and sorted(row["files"]) != retained["files"]:
            raise ValueError("installer changed the expected original runtime identity or file set")
    save_evidence(evidence, "installer-audit", dict(state_mode="0700", backup="original bytes and mode",
        archives=actual, isolated_dry_run_before_install=True, retained_runtime=before["retained_runtime"]))


def source_binding(plan, evidence):
    selection = plan["selection"]
    if selection["component"] != "bind_plugin" or selection["producer_action"] != "build":
        return
    product = json.loads(Path("/usr/local/opnsense/version/bind-rp").read_text())
    if product.get("product_hash") != selection["source_commit"]:
        raise ValueError("installed plugin product_hash differs from selected source")
    save_evidence(evidence, "source-binding", dict(product_hash=product["product_hash"], source_commit=selection["source_commit"]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("preflight", "authenticate", "installed", "source", "canary", "installer-before", "installer-after"))
    parser.add_argument("--component", required=True, choices=bind.KINDS)
    parser.add_argument("--series", required=True)
    parser.add_argument("--mode", required=True, choices=("local", "public"))
    parser.add_argument("--repository-root", required=True, type=Path)
    parser.add_argument("--trusted-directory", required=True, type=Path)
    parser.add_argument("--evidence-directory", required=True, type=Path)
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--label", default="installed")
    parser.add_argument("--allow-upstream", action="store_true")
    args = parser.parse_args()
    if args.evidence_directory.resolve().is_relative_to(args.repository_root.resolve()):
        raise ValueError("verification evidence must remain outside the sealed repository")
    plan = preflight(args.repository_root, args.trusted_directory, args.component, args.series, args.mode)
    if args.command == "preflight":
        save_evidence(args.evidence_directory, "preflight", plan)
    elif args.command == "authenticate":
        authenticate(plan, args.trusted_directory, args.evidence_directory, args.mode)
    elif args.command == "installed":
        entry = next(entry for entry in plan["repositories"] if Path(entry["directory"]) == args.directory.resolve())
        packages = [Path(path) for path in entry["packages"] if not args.allow_upstream or not Path(path).name.startswith("os-dhcp-interface-ha-")]
        installed(packages, args.evidence_directory, args.label,
                  exact=not args.allow_upstream, policy=bind._json(args.trusted_directory / "bind-compatibility.json"))
    elif args.command in {"installer-before", "installer-after"}:
        if args.mode != "public" or plan["composition"] is None or plan["composition"]["components"]["bind_plugin"] is None:
            raise ValueError("installer evidence requires a complete public feed")
        if args.command == "installer-before":
            prepare_installer(plan, args.trusted_directory, args.evidence_directory.resolve())
        else:
            audit_installer(plan, args.evidence_directory.resolve())
    elif args.command == "source":
        source_binding(plan, args.evidence_directory)
    else:
        save_evidence(args.evidence_directory, args.label, dict(managed_named="passed", drill="192.0.2.53", cleanup="passed"))


if __name__ == "__main__":
    main()
