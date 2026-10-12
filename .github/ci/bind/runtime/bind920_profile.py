#!/usr/bin/env python3
"""Resolve package-owned BIND approval into exact FreeBSD Ports build input."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path


FIELDS = (
    "ports_repository",
    "ports_commit",
    "makefile_sha256",
    "distinfo_sha256",
    "distversion",
    "portrevision",
)
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
DISTVERSION_PATTERN = re.compile(r"9\.20\.[0-9]+")
SERIES_PATTERN = re.compile(r"[0-9]+\.[0-9]+")
FREEBSD_RELEASE_PATTERN = re.compile(r"[0-9]+\.[0-9]+")
ARCHITECTURE_PATTERN = re.compile(r"[a-z0-9_]+")
PROVENANCE_SCHEMA = 4
BUILD_RECIPE_PATH = Path(__file__).with_name("build-bind920.sh")
PACKAGE_ORIGINS = {
    "bind-tools": "dns/bind-tools",
    "bind920": "dns/bind920",
}
PORTS_REPOSITORY = "https://github.com/freebsd/freebsd-ports.git"


def approval_from_makefile(text: str, *, allow_absent: bool = False) -> dict[str, str] | None:
    """Read only the two literal runtime approval assignments."""
    values = {}
    for field in ("RP_BIND_VERSION", "RP_BIND_PORTS_COMMIT"):
        declarations = [line for line in text.splitlines()
                        if re.match(rf"\s*{field}\s*(?:[?+:!]?=)", line)]
        if len(declarations) > 1:
            raise ValueError(f"ambiguous {field} approval")
        if declarations:
            match = re.fullmatch(rf"\s*{field}\s*=\s*([^#]*?)(?:\s*#.*)?", declarations[0])
            if match is None:
                raise ValueError(f"{field} must be a literal assignment")
            values[field] = match[1].strip()
    if not values and allow_absent:
        return None
    if (set(values) != {"RP_BIND_VERSION", "RP_BIND_PORTS_COMMIT"}
            or re.fullmatch(r"9\.20\.[0-9]+(?:_[1-9][0-9]*)?", values["RP_BIND_VERSION"]) is None
            or COMMIT_PATTERN.fullmatch(values["RP_BIND_PORTS_COMMIT"]) is None):
        raise ValueError("missing or invalid runtime approval")
    return {"version": values["RP_BIND_VERSION"], "ports_commit": values["RP_BIND_PORTS_COMMIT"]}


def profile_from_recipe(approval: dict[str, str], resolved_commit: str,
                        makefile_bytes: bytes, distinfo_bytes: bytes) -> dict[str, str | int]:
    """Hash exact Git blobs and require the recipe's declared native version."""
    if resolved_commit != approval["ports_commit"] or COMMIT_PATTERN.fullmatch(resolved_commit) is None:
        raise ValueError("resolved Ports object differs from runtime approval")
    text = makefile_bytes.decode("utf-8")
    declarations = {}
    for field in ("DISTVERSION", "PORTREVISION"):
        lines = [line for line in text.splitlines() if re.match(rf"\s*{field}\s*[?+:!]?=", line)]
        if len(lines) > 1 or field == "DISTVERSION" and not lines:
            raise ValueError(f"missing or ambiguous recipe {field}")
        match = re.fullmatch(rf"\s*{field}\s*=\s*([^#]*?)(?:\s*#.*)?", lines[0]) if lines else None
        if lines and match is None:
            raise ValueError(f"recipe {field} must be literal")
        declarations[field] = match[1].strip() if match else "0"
    if re.fullmatch(r"[0-9]+", declarations["PORTREVISION"]) is None:
        raise ValueError("invalid recipe PORTREVISION")
    profile = validate_profile(dict(
        ports_repository=PORTS_REPOSITORY, ports_commit=resolved_commit,
        makefile_sha256=hashlib.sha256(makefile_bytes).hexdigest(),
        distinfo_sha256=hashlib.sha256(distinfo_bytes).hexdigest(),
        distversion=declarations["DISTVERSION"], portrevision=int(declarations["PORTREVISION"])))
    if package_version(profile) != approval["version"]:
        raise ValueError("Ports recipe version differs from runtime approval")
    return profile


def resolve_profile(approval: dict[str, str], *, repository: Path | None = None) -> dict[str, str | int]:
    """Resolve the approved immutable recipe once at the controller boundary."""
    def read(directory):
        command = ["git", "-C", str(directory)]
        commit = subprocess.check_output([*command, "rev-parse", f"{approval['ports_commit']}^{{commit}}"], text=True).strip()
        return profile_from_recipe(approval, commit,
            subprocess.check_output([*command, "show", f"{commit}:dns/bind920/Makefile"]),
            subprocess.check_output([*command, "show", f"{commit}:dns/bind920/distinfo"]))
    if repository is not None:
        return read(repository)
    with tempfile.TemporaryDirectory() as temporary:
        subprocess.run(["git", "init", "--quiet", temporary], check=True)
        subprocess.run(["git", "-C", temporary, "fetch", "--quiet", "--depth=1", PORTS_REPOSITORY,
                        approval["ports_commit"]], check=True, stdout=sys.stderr)
        return read(Path(temporary))


def validate_profile(profile: object) -> dict[str, str | int]:
    """Validate one pinned BIND profile object."""
    if not isinstance(profile, dict) or set(profile) != set(FIELDS):
        raise ValueError("BIND profile does not match the required schema")
    if profile["ports_repository"] != PORTS_REPOSITORY:
        raise ValueError("BIND profile has an unexpected ports repository")
    for field in ("ports_commit", "makefile_sha256", "distinfo_sha256", "distversion"):
        if not isinstance(profile[field], str):
            raise ValueError(f"BIND profile has an invalid {field}")
    if COMMIT_PATTERN.fullmatch(profile["ports_commit"]) is None:
        raise ValueError("BIND profile has an invalid ports_commit")
    for field in ("makefile_sha256", "distinfo_sha256"):
        if SHA256_PATTERN.fullmatch(profile[field]) is None:
            raise ValueError(f"BIND profile has an invalid {field}")
    if DISTVERSION_PATTERN.fullmatch(profile["distversion"]) is None:
        raise ValueError("BIND profile has an invalid distversion")
    if (
        not isinstance(profile["portrevision"], int)
        or isinstance(profile["portrevision"], bool)
        or profile["portrevision"] < 0
    ):
        raise ValueError("BIND profile has an invalid portrevision")
    return profile


def load_profile(path: Path) -> dict[str, str | int]:
    try:
        profile = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read BIND profile: {error}") from error
    return validate_profile(profile)


def package_version(profile: object) -> str:
    """Return the package version implied by a validated Ports profile."""
    profile = validate_profile(profile)
    if profile["portrevision"] == 0:
        return str(profile["distversion"])
    return f"{profile['distversion']}_{profile['portrevision']}"


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from shared import target_pkg


def validate_package_creator(package_creator: object) -> dict[str, str]:
    return target_pkg.normalize_creator(package_creator)


def build_recipe_sha256() -> str:
    """Identify the local build policy that produces the reusable BIND pair."""
    try:
        return hashlib.sha256(BUILD_RECIPE_PATH.read_bytes()).hexdigest()
    except OSError as error:
        raise ValueError(f"cannot read BIND build recipe: {error}") from error


def producer_inputs(
    profile: object,
    series: str,
    freebsd_release: str,
    architecture: str,
    package_creator: object,
    *,
    recipe_sha256: str,
) -> dict[str, object]:
    """Return the canonical schema-4 runtime build inputs."""
    profile = validate_profile(profile)
    if SERIES_PATTERN.fullmatch(series) is None:
        raise ValueError("invalid OPNsense series")
    if FREEBSD_RELEASE_PATTERN.fullmatch(freebsd_release) is None:
        raise ValueError("invalid FreeBSD release")
    if ARCHITECTURE_PATTERN.fullmatch(architecture) is None:
        raise ValueError("invalid target architecture")
    package_creator = validate_package_creator(package_creator)
    if not isinstance(recipe_sha256, str) or SHA256_PATTERN.fullmatch(recipe_sha256) is None:
        raise ValueError("invalid BIND build recipe SHA256")
    return {
        "schema": PROVENANCE_SCHEMA,
        "series": series,
        "freebsd_release": freebsd_release,
        "architecture": architecture,
        "bind_profile": profile,
        "build_recipe_sha256": recipe_sha256,
        "package_creator": package_creator,
    }


def compatibility_fingerprint(
    profile: object,
    series: str,
    freebsd_release: str,
    architecture: str,
    package_creator: object,
    *,
    recipe_sha256: str,
) -> str:
    """Hash explicit inputs without consulting the validator's checkout."""
    inputs = producer_inputs(profile, series, freebsd_release, architecture,
                             package_creator, recipe_sha256=recipe_sha256)
    encoded = json.dumps(inputs, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_provenance(
    profile: object,
    series: str,
    freebsd_release: str,
    architecture: str,
    package_creator: object,
    packages: object,
    *,
    recipe_sha256: str,
) -> dict[str, object]:
    """Describe the exact BIND pair available for one compatibility identity."""
    profile = validate_profile(profile)
    package_creator = validate_package_creator(package_creator)
    fingerprint = compatibility_fingerprint(
        profile, series, freebsd_release, architecture, package_creator,
        recipe_sha256=recipe_sha256,
    )
    if not isinstance(packages, dict) or set(packages) != set(PACKAGE_ORIGINS):
        raise ValueError("BIND provenance has an invalid package set")
    expected_version = package_version(profile)
    validated_packages: dict[str, dict[str, str]] = {}
    for package_name, origin in PACKAGE_ORIGINS.items():
        package = packages[package_name]
        if not isinstance(package, dict) or set(package) != {"name", "version", "origin", "filename"}:
            raise ValueError(f"{package_name} package has an invalid schema")
        expected = {
            "name": package_name,
            "version": expected_version,
            "origin": origin,
            "filename": f"{package_name}-{expected_version}.pkg",
        }
        if package != expected:
            raise ValueError(f"{package_name} package does not match the BIND profile")
        validated_packages[package_name] = package
    return {
        "schema": PROVENANCE_SCHEMA,
        "fingerprint": fingerprint,
        "series": series,
        "freebsd_release": freebsd_release,
        "architecture": architecture,
        "build_recipe_sha256": recipe_sha256,
        "package_creator": package_creator,
        "packages": validated_packages,
    }


def write_provenance(
    output: Path,
    profile: object,
    series: str,
    freebsd_release: str,
    architecture: str,
    package_creator: object,
    bind_tools: Path,
    bind920: Path,
    *,
    recipe_sha256: str | None = None,
) -> None:
    """Write canonical provenance for two finished BIND package archives."""
    if not bind_tools.is_file() or not bind920.is_file():
        raise ValueError("BIND package archive does not exist")
    packages = {
        "bind-tools": {
            "name": "bind-tools",
            "version": package_version(profile),
            "origin": "dns/bind-tools",
            "filename": bind_tools.name,
        },
        "bind920": {
            "name": "bind920",
            "version": package_version(profile),
            "origin": "dns/bind920",
            "filename": bind920.name,
        },
    }
    provenance = build_provenance(
        profile, series, freebsd_release, architecture, package_creator, packages,
        recipe_sha256=build_recipe_sha256() if recipe_sha256 is None else recipe_sha256,
    )
    output.write_text(json.dumps(provenance, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    if sys.argv[1:2] == ["generate"]:
        parser = argparse.ArgumentParser(description="Generate exact approved Ports recipe input")
        parser.add_argument("command", choices=("generate",))
        parser.add_argument("--runtime-approval", type=Path, required=True)
        parser.add_argument("--output", type=Path, required=True)
        arguments = parser.parse_args()
        profile = resolve_profile(approval_from_makefile(arguments.runtime_approval.read_text()))
        arguments.output.write_text(json.dumps(profile, sort_keys=True, indent=2) + "\n")
        return
    parser = argparse.ArgumentParser()
    parser.add_argument("metadata_path", type=Path)
    parser.add_argument("command", choices=(*FIELDS, "package_version", "fingerprint", "provenance"))
    parser.add_argument("series", nargs="?")
    parser.add_argument("freebsd_release", nargs="?")
    parser.add_argument("--architecture", default="x86_64")
    parser.add_argument("--package-creator")
    parser.add_argument("--recipe-sha256", help="explicit original recipe hash; defaults to this wrapper")
    parser.add_argument("--bind-tools", type=Path)
    parser.add_argument("--bind920", type=Path)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    profile = load_profile(arguments.metadata_path)
    if arguments.command in FIELDS:
        print(profile[arguments.command])
        return
    if arguments.command == "package_version":
        print(package_version(profile))
        return
    if arguments.series is None or arguments.freebsd_release is None:
        parser.error(f"{arguments.command} requires series and freebsd_release")
    if arguments.package_creator is None:
        parser.error(f"{arguments.command} requires --package-creator")
    try:
        package_creator = json.loads(arguments.package_creator)
    except json.JSONDecodeError as error:
        parser.error(f"invalid --package-creator JSON: {error}")
    if arguments.command == "fingerprint":
        print(
            compatibility_fingerprint(
                profile,
                arguments.series,
                arguments.freebsd_release,
                arguments.architecture,
                package_creator,
                recipe_sha256=(arguments.recipe_sha256 if arguments.recipe_sha256 is not None
                               else build_recipe_sha256()),
            )
        )
        return
    if arguments.bind_tools is None or arguments.bind920 is None or arguments.output is None:
        parser.error("provenance requires --bind-tools, --bind920, and --output")
    write_provenance(
        arguments.output,
        profile,
        arguments.series,
        arguments.freebsd_release,
        arguments.architecture,
        package_creator,
        arguments.bind_tools,
        arguments.bind920,
        recipe_sha256=arguments.recipe_sha256,
    )


if __name__ == "__main__":
    main()
