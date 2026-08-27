#!/usr/bin/env python3
"""Discover and assess FreeBSD Ports candidates for bundled BIND."""

from __future__ import annotations

import dataclasses
import argparse
import hashlib
import json
import re
from pathlib import Path


BIND_SERIES = (9, 20)
DISTVERSION_PATTERN = re.compile(r"^DISTVERSION[?+]?=\s*([0-9]+\.[0-9]+\.[0-9]+)\s*$", re.MULTILINE)
PORTREVISION_PATTERN = re.compile(r"^PORTREVISION[?+]?=\s*([0-9]+)\s*$", re.MULTILINE)
DISTINFO_SHA256_PATTERN = re.compile(r"^SHA256\s+\(bind-([0-9]+\.[0-9]+\.[0-9]+)\.tar\.[^)]+\)\s+=\s+(\S+)\s*$", re.MULTILINE)
CVE_PATTERN = re.compile(r"CVE-[0-9]{4}-[0-9A-Za-z-]+", re.IGNORECASE)
DEPENDENCY_FIELDS = ("LIB_DEPENDS", "RUN_DEPENDS", "USES", "OPTIONS_DEFAULT", "CONFIGURE_ARGS")
SECURITY_TERMS = ("security", "vulnerability", "advisory", "VU#")
CRITICAL_TERMS = (
    "crash",
    "assertion",
    "SERVFAIL",
    "DNSSEC validation",
    "DoT",
    "TLS",
    "resolver",
    "cache corruption",
    "data loss",
)


@dataclasses.dataclass(frozen=True)
class Bind920Profile:
    ports_repository: str
    ports_commit: str
    makefile_sha256: str
    distinfo_sha256: str
    distversion: str
    portrevision: int


@dataclasses.dataclass(frozen=True)
class CandidateProfile(Bind920Profile):
    source_ref: str


@dataclasses.dataclass(frozen=True)
class Assessment:
    classification: str
    signals: list[str]
    summary: str


def _version_tuple(version: str) -> tuple[int, int, int]:
    try:
        parsed = tuple(int(part) for part in version.split("."))
    except ValueError as error:
        raise ValueError(f"invalid BIND version: {version}") from error
    if len(parsed) != 3 or parsed[:2] != BIND_SERIES:
        raise ValueError("candidate must be in the BIND 9.20 series")
    return parsed


def load_current_profile(path: Path) -> Bind920Profile:
    data = json.loads(path.read_text(encoding="utf-8"))
    return Bind920Profile(
        ports_repository=str(data["ports_repository"]),
        ports_commit=str(data["ports_commit"]),
        makefile_sha256=str(data["makefile_sha256"]),
        distinfo_sha256=str(data["distinfo_sha256"]),
        distversion=str(data["distversion"]),
        portrevision=int(data["portrevision"]),
    )


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_bind920_makefile(text: str) -> tuple[str, int]:
    distversion_match = DISTVERSION_PATTERN.search(text)
    if distversion_match is None:
        raise ValueError("bind920 Makefile does not declare DISTVERSION")
    distversion = distversion_match.group(1)
    _version_tuple(distversion)
    portrevision_match = PORTREVISION_PATTERN.search(text)
    portrevision = int(portrevision_match.group(1)) if portrevision_match is not None else 0
    return distversion, portrevision


def _parse_distinfo(text: str) -> tuple[str, str]:
    match = DISTINFO_SHA256_PATTERN.search(text)
    if match is None:
        raise ValueError("distinfo does not contain a BIND tarball SHA256")
    distversion = match.group(1)
    _version_tuple(distversion)
    return distversion, match.group(2)


def parse_distinfo_hash(text: str) -> str:
    return _parse_distinfo(text)[1]


def candidate_is_newer(current: Bind920Profile, candidate: CandidateProfile) -> bool:
    current_version = _version_tuple(current.distversion)
    candidate_version = _version_tuple(candidate.distversion)
    if candidate_version != current_version:
        return candidate_version > current_version
    return candidate.portrevision > current.portrevision


def candidate_from_files(
    ports_repository: str,
    ports_commit: str,
    makefile: Path,
    distinfo: Path,
    source_ref: str,
) -> CandidateProfile:
    distversion, portrevision = parse_bind920_makefile(makefile.read_text(encoding="utf-8"))
    distinfo_version, _distinfo_hash = _parse_distinfo(distinfo.read_text(encoding="utf-8"))
    if distinfo_version != distversion:
        raise ValueError("distinfo BIND version does not match Makefile DISTVERSION")
    return CandidateProfile(
        ports_repository=ports_repository,
        ports_commit=ports_commit,
        makefile_sha256=sha256_file(makefile),
        distinfo_sha256=sha256_file(distinfo),
        distversion=distversion,
        portrevision=portrevision,
        source_ref=source_ref,
    )


def render_updated_profile(current: Bind920Profile, candidate: CandidateProfile) -> str:
    if not candidate_is_newer(current, candidate):
        raise ValueError("candidate is not newer than the current BIND profile")
    data = {
        "ports_repository": candidate.ports_repository,
        "ports_commit": candidate.ports_commit,
        "makefile_sha256": candidate.makefile_sha256,
        "distinfo_sha256": candidate.distinfo_sha256,
        "distversion": candidate.distversion,
        "portrevision": candidate.portrevision,
    }
    return json.dumps(data, indent=2) + "\n"


def _contains_term(text: str, term: str) -> bool:
    if term.isupper() or term == "DoT":
        return term in text
    return term.lower() in text.lower()


def _unique_signals(signals: list[str]) -> list[str]:
    unique: list[str] = []
    for signal in signals:
        if signal not in unique:
            unique.append(signal)
    return unique


def _has_dependency_drift(ports_diff_text: str) -> bool:
    in_dependency_hunk = False
    for line in ports_diff_text.splitlines():
        content = line[1:] if line.startswith(("+", "-", " ")) else line
        if any(field in content for field in DEPENDENCY_FIELDS):
            in_dependency_hunk = True
        if line.startswith("@@"):
            in_dependency_hunk = False
            continue
        if in_dependency_hunk and line.startswith(("+", "-")):
            return True
    return False


def _logical_makefile_assignments(text: str) -> dict[str, str]:
    assignments: dict[str, str] = {}
    current_field = ""
    current_value: list[str] = []

    def flush() -> None:
        if current_field:
            assignments[current_field] = "\n".join(current_value)

    for line in text.splitlines():
        match = re.match(rf"^({'|'.join(DEPENDENCY_FIELDS)})[?+:!]?=\s*(.*)$", line)
        if match:
            flush()
            current_field = match.group(1)
            current_value = [match.group(2).rstrip("\\").rstrip()]
            if not line.rstrip().endswith("\\"):
                flush()
                current_field = ""
                current_value = []
            continue
        if current_field:
            current_value.append(line.rstrip("\\").rstrip())
            if not line.rstrip().endswith("\\"):
                flush()
                current_field = ""
                current_value = []
    flush()
    return assignments


def _has_makefile_dependency_drift(old_makefile_text: str, new_makefile_text: str) -> bool:
    return _logical_makefile_assignments(old_makefile_text) != _logical_makefile_assignments(new_makefile_text)


def assess_candidate(
    old_version: str,
    new_version: str,
    changelog_text: str,
    ports_diff_text: str,
    security_text: str = "",
    old_makefile_text: str = "",
    new_makefile_text: str = "",
) -> Assessment:
    combined_notes = "\n".join((changelog_text, security_text))
    cves = [match.group(0).upper() for match in CVE_PATTERN.finditer(combined_notes)]
    security_signals = cves + [term for term in SECURITY_TERMS if _contains_term(combined_notes, term)]
    dependency_drift = _has_dependency_drift(ports_diff_text)
    if old_makefile_text or new_makefile_text:
        dependency_drift = dependency_drift or _has_makefile_dependency_drift(
            old_makefile_text,
            new_makefile_text,
        )
    dependency_signals = ["dependency change"] if dependency_drift else []
    patch_signals = [
        term
        for term in CRITICAL_TERMS
        if _contains_term(changelog_text, term)
    ]

    all_signals = _unique_signals(security_signals + dependency_signals + patch_signals)

    if security_signals:
        classification = "security"
    elif dependency_signals:
        classification = "risky"
    elif patch_signals:
        classification = "critical-bugfix"
    else:
        classification = "routine"

    signal_text = ", ".join(all_signals) if all_signals else "none"
    summary = (
        f"BIND {old_version} to {new_version} is classified as {classification}. "
        f"Signals: {signal_text}. Maintainer review is required before publication."
    )
    return Assessment(classification, all_signals, summary)


def update_profile(
    current_path: Path,
    ports_repository: str,
    ports_commit: str,
    makefile: Path,
    distinfo: Path,
    output: Path,
    source_ref: str,
) -> None:
    current = load_current_profile(current_path)
    candidate = candidate_from_files(ports_repository, ports_commit, makefile, distinfo, source_ref)
    output.write_text(render_updated_profile(current, candidate), encoding="utf-8")


def render_assessment_markdown(assessment: Assessment) -> str:
    signal_lines = "\n".join(f"- {signal}" for signal in assessment.signals) if assessment.signals else "- none"
    return (
        "## BIND Candidate Assessment\n\n"
        f"{assessment.summary}\n\n"
        f"- classification: {assessment.classification}\n"
        "- publication: manual maintainer action required\n\n"
        "### Signals\n\n"
        f"{signal_lines}\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    update_parser = subparsers.add_parser("update-profile")
    update_parser.add_argument("--current", type=Path, required=True)
    update_parser.add_argument("--ports-repository", required=True)
    update_parser.add_argument("--ports-commit", required=True)
    update_parser.add_argument("--makefile", type=Path, required=True)
    update_parser.add_argument("--distinfo", type=Path, required=True)
    update_parser.add_argument("--output", type=Path, required=True)
    update_parser.add_argument("--source-ref", default="")
    assess_parser = subparsers.add_parser("assess")
    assess_parser.add_argument("--old-version", required=True)
    assess_parser.add_argument("--new-version", required=True)
    assess_parser.add_argument("--changelog", type=Path, required=True)
    assess_parser.add_argument("--ports-diff", type=Path, required=True)
    assess_parser.add_argument("--security", type=Path)
    assess_parser.add_argument("--old-makefile", type=Path)
    assess_parser.add_argument("--new-makefile", type=Path)
    assess_parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    if arguments.command == "update-profile":
        update_profile(
            arguments.current,
            arguments.ports_repository,
            arguments.ports_commit,
            arguments.makefile,
            arguments.distinfo,
            arguments.output,
            arguments.source_ref or arguments.ports_commit,
        )
    elif arguments.command == "assess":
        security_text = ""
        if arguments.security is not None:
            security_text = arguments.security.read_text(encoding="utf-8")
        old_makefile_text = ""
        if arguments.old_makefile is not None:
            old_makefile_text = arguments.old_makefile.read_text(encoding="utf-8")
        new_makefile_text = ""
        if arguments.new_makefile is not None:
            new_makefile_text = arguments.new_makefile.read_text(encoding="utf-8")
        assessment = assess_candidate(
            arguments.old_version,
            arguments.new_version,
            arguments.changelog.read_text(encoding="utf-8"),
            arguments.ports_diff.read_text(encoding="utf-8"),
            security_text,
            old_makefile_text,
            new_makefile_text,
        )
        arguments.output.write_text(render_assessment_markdown(assessment), encoding="utf-8")


if __name__ == "__main__":
    main()
