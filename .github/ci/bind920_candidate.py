#!/usr/bin/env python3
"""Discover and assess FreeBSD Ports candidates for bundled BIND."""

from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path


BIND_SERIES = (9, 20)
DISTVERSION_PATTERN = re.compile(r"^DISTVERSION[?+]?=\s*([0-9]+\.[0-9]+\.[0-9]+)\s*$", re.MULTILINE)
PORTREVISION_PATTERN = re.compile(r"^PORTREVISION[?+]?=\s*([0-9]+)\s*$", re.MULTILINE)
DISTINFO_SHA256_PATTERN = re.compile(r"^SHA256\s+\(bind-([0-9]+\.[0-9]+\.[0-9]+)\.tar\.[^)]+\)\s+=\s+(\S+)\s*$", re.MULTILINE)


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


def parse_bind920_makefile(text: str) -> tuple[str, int]:
    distversion_match = DISTVERSION_PATTERN.search(text)
    if distversion_match is None:
        raise ValueError("bind920 Makefile does not declare DISTVERSION")
    distversion = distversion_match.group(1)
    _version_tuple(distversion)
    portrevision_match = PORTREVISION_PATTERN.search(text)
    portrevision = int(portrevision_match.group(1)) if portrevision_match is not None else 0
    return distversion, portrevision


def parse_distinfo_hash(text: str) -> str:
    match = DISTINFO_SHA256_PATTERN.search(text)
    if match is None:
        raise ValueError("distinfo does not contain a BIND tarball SHA256")
    _version_tuple(match.group(1))
    return match.group(2)


def candidate_is_newer(current: Bind920Profile, candidate: CandidateProfile) -> bool:
    current_version = _version_tuple(current.distversion)
    candidate_version = _version_tuple(candidate.distversion)
    if candidate_version != current_version:
        return candidate_version > current_version
    return candidate.portrevision > current.portrevision
