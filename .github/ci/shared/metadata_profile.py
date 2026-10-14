#!/usr/bin/env python3
"""Read and validate upstream inputs frozen in a build's trusted artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess


REQUIRED_FIELDS = (
    "series",
    "upstream_branch",
    "upstream_commit",
    "tools_tag",
    "freebsd_release",
    "core_commit",
    "core_archive_url",
    "core_archive_sha256",
)
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
SERIES_PATTERN = re.compile(r"\d+\.\d+")
FREEBSD_RELEASE_PATTERN = re.compile(r"[1-9]\d*(?:\.\d+)?")
TOOLS_PROFILE_FIELDS = {
    'schema', 'series', 'tools_tag', 'tools_commit', 'path',
    'build_conf_sha256', 'freebsd_release',
}


def tools_tag_matches_series(tools_tag: str, series: str) -> bool:
    return re.fullmatch(
        rf"{re.escape(series)}(?:\.(?:0|[1-9]\d*))?",
        tools_tag,
    ) is not None


def freebsd_from_build_conf(text: str) -> str:
    """Read the single literal tools OS assignment, without evaluating make."""
    assignments = [
        match.group(1).split('#', 1)[0].strip()
        for line in text.splitlines()
        if (match := re.fullmatch(r'\s*OS\s*\?=\s*(.*)', line))
    ]
    if len(assignments) != 1 or FREEBSD_RELEASE_PATTERN.fullmatch(assignments[0]) is None:
        raise ValueError('missing or invalid tools OS assignment')
    return assignments[0]


def validate_tools_record(record: object, profile: dict[str, str]) -> dict:
    """Validate sealed tools evidence without re-resolving upstream state."""
    profile = validate_profile(profile, profile['series'])
    if not isinstance(record, dict) or set(record) != TOOLS_PROFILE_FIELDS or record['schema'] != 1:
        raise ValueError('tools evidence does not match the required schema')
    expected = {field: profile[field] for field in ('series', 'tools_tag', 'freebsd_release')}
    expected['path'] = f"config/{profile['series']}/build.conf"
    if (any(record[field] != value for field, value in expected.items())
            or not isinstance(record['tools_commit'], str)
            or COMMIT_PATTERN.fullmatch(record['tools_commit']) is None
            or not isinstance(record['build_conf_sha256'], str)
            or SHA256_PATTERN.fullmatch(record['build_conf_sha256']) is None):
        raise ValueError('tools evidence does not match the selected source profile')
    return record


def verify_tools_profile(profile: dict[str, str], tools_repository: Path) -> dict:
    """Bind generated provenance to the exact numeric tools tag and build.conf blob."""
    profile = validate_profile(profile, profile['series'])
    path = f"config/{profile['series']}/build.conf"
    try:
        commit = subprocess.run(
            ['git', '-C', str(tools_repository), 'rev-parse',
             f"refs/tags/{profile['tools_tag']}^{{commit}}"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        contents = subprocess.run(
            ['git', '-C', str(tools_repository), 'show', f'{commit}:{path}'],
            check=True, capture_output=True,
        ).stdout
        freebsd_release = freebsd_from_build_conf(contents.decode('utf-8'))
    except (OSError, subprocess.CalledProcessError, UnicodeDecodeError) as error:
        raise ValueError('cannot resolve the selected tools build profile') from error
    if COMMIT_PATTERN.fullmatch(commit) is None:
        raise ValueError('tools tag does not resolve to an immutable commit')
    if freebsd_release != profile['freebsd_release']:
        raise ValueError('tools OS assignment does not match selected FreeBSD release')
    return {
        'schema': 1,
        'series': profile['series'],
        'tools_tag': profile['tools_tag'],
        'tools_commit': commit,
        'path': path,
        'build_conf_sha256': hashlib.sha256(contents).hexdigest(),
        'freebsd_release': freebsd_release,
    }


def fail(message: str) -> None:
    raise SystemExit(message)


def validate_core_archive(
    core_commit: str,
    core_archive_url: str,
    core_archive_sha256: str,
) -> None:
    if COMMIT_PATTERN.fullmatch(core_commit) is None:
        raise ValueError("upstream metadata core_commit is not an immutable commit hash")
    if SHA256_PATTERN.fullmatch(core_archive_sha256) is None:
        raise ValueError(
            "upstream metadata core_archive_sha256 is not an immutable SHA-256 hash"
        )
    expected_url = (
        "https://github.com/opnsense/core/archive/"
        f"{core_commit}.tar.gz"
    )
    if core_archive_url != expected_url:
        raise ValueError("upstream metadata core archive URL is not immutable")


def validate_profile(metadata: object, series: str) -> dict[str, str]:
    if not isinstance(metadata, dict):
        raise ValueError("upstream metadata must be a JSON object")
    if set(metadata) != set(REQUIRED_FIELDS):
        raise ValueError("upstream metadata does not match the required schema")
    if SERIES_PATTERN.fullmatch(series) is None:
        raise ValueError(f"invalid upstream metadata series {series}")

    profile: dict[str, str] = {}
    for field in REQUIRED_FIELDS:
        value = metadata.get(field)
        if not isinstance(value, str) or not value:
            raise ValueError(f"upstream metadata has an invalid {field}")
        profile[field] = value
    if profile["series"] != series:
        raise ValueError(
            f"upstream metadata series {profile['series']} does not match {series}"
        )
    if profile["upstream_branch"] != f"stable/{series}":
        raise ValueError("upstream metadata branch does not match its series")
    if COMMIT_PATTERN.fullmatch(profile["upstream_commit"]) is None:
        raise ValueError(
            "upstream metadata upstream_commit is not an immutable commit hash"
        )
    if not tools_tag_matches_series(profile["tools_tag"], series):
        raise ValueError("upstream metadata tools_tag does not match its series")
    if FREEBSD_RELEASE_PATTERN.fullmatch(profile["freebsd_release"]) is None:
        raise ValueError("upstream metadata has an invalid freebsd_release")
    validate_core_archive(
        profile["core_commit"],
        profile["core_archive_url"],
        profile["core_archive_sha256"],
    )
    return profile


def load_profile(metadata_path: Path, series: str) -> dict[str, str]:
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot read upstream metadata: {error}")
    try:
        return validate_profile(metadata, series)
    except ValueError as error:
        fail(str(error))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("metadata_path", type=Path)
    parser.add_argument("series")
    parser.add_argument("field", choices=REQUIRED_FIELDS)
    arguments = parser.parse_args()
    print(load_profile(arguments.metadata_path, arguments.series)[arguments.field])


if __name__ == "__main__":
    main()
