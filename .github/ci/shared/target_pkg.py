#!/usr/bin/env python3
"""Install and verify an immutable per-series pkg package creator."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
TARGET_FIELDS = {
    "name",
    "version",
    "origin",
    "abi",
    "filename",
    "sha256",
    "pkg_static_sha256",
}


class TargetPackageError(ValueError):
    """The pinned package creator cannot be selected or verified exactly."""


@dataclass(frozen=True)
class PackageIdentity:
    name: str
    version: str
    origin: str
    abi: str


@dataclass(frozen=True)
class TargetPackage:
    series: str
    identity: PackageIdentity
    filename: str
    sha256: str
    pkg_static_sha256: str

    def record(self) -> dict[str, str]:
        """Return the canonical flat record shared with provenance."""
        return {
            "name": self.identity.name,
            "version": self.identity.version,
            "origin": self.identity.origin,
            "abi": self.identity.abi,
            "filename": self.filename,
            "sha256": self.sha256,
            "pkg_static_sha256": self.pkg_static_sha256,
        }


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def package_content_sha256(archive: Path) -> str:
    """Hash the installation-relevant state extracted from a package archive."""
    with tempfile.TemporaryDirectory() as temporary_directory:
        root = Path(temporary_directory)
        run(["tar", "-xf", str(archive), "-C", str(root)])
        digest = hashlib.sha256()
        hardlinks: dict[tuple[int, int], int] = {}
        for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
            relative = path.relative_to(root).as_posix().encode()
            attributes = path.lstat()
            hardlink = 0
            if path.is_symlink():
                kind = b"link"
                content = os.readlink(path).encode()
            elif path.is_file():
                kind = b"file"
                content = path.read_bytes()
                if attributes.st_nlink > 1:
                    inode = (attributes.st_dev, attributes.st_ino)
                    hardlink = hardlinks.setdefault(inode, len(hardlinks) + 1)
            elif path.is_dir():
                kind = b"directory"
                content = b""
            else:
                raise TargetPackageError(f"target pkg contains unsupported entry {relative.decode()}")
            metadata = (
                f"{stat.S_IMODE(attributes.st_mode):o}|{attributes.st_uid}|"
                f"{attributes.st_gid}|{getattr(attributes, 'st_flags', 0)}|{hardlink}"
            ).encode()
            for value in (kind, relative, metadata, content):
                digest.update(len(value).to_bytes(8, "big"))
                digest.update(value)
        return digest.hexdigest()


def load_content_sha256(metadata: Path, series: str) -> str:
    """Load the canonical extracted-content digest used for safe archive repacks."""
    try:
        document = json.loads(metadata.read_text(encoding="utf-8"))
        records = document["series"]
        record = records[series]
        digest = record["content_sha256"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise TargetPackageError(f"target pkg content metadata does not define {series}") from error
    if (
        not isinstance(document, dict)
        or set(document) != {"schema", "series"}
        or document["schema"] != 2
        or not isinstance(records, dict)
        or set(records) != {"26.1", "26.7"}
        or not isinstance(record, dict)
        or set(record) != {"baseline_archive_sha256", "content_sha256"}
        or not isinstance(record["baseline_archive_sha256"], str)
        or SHA256_PATTERN.fullmatch(record["baseline_archive_sha256"]) is None
        or not isinstance(digest, str)
        or SHA256_PATTERN.fullmatch(digest) is None
    ):
        raise TargetPackageError(f"target pkg content metadata is invalid for {series}")
    return digest


def changed_archive_series(before: Path, after: Path) -> str | None:
    """Return the sole series whose outer hash changed without other target changes."""
    changed: list[str] = []
    for series in ("26.1", "26.7"):
        previous = load_target(before, series).record()
        current = load_target(after, series).record()
        previous_sha256 = previous.pop("sha256")
        current_sha256 = current.pop("sha256")
        if previous != current:
            return None
        if previous_sha256 != current_sha256:
            changed.append(series)
    return changed[0] if len(changed) == 1 else None


def load_target(metadata: Path, series: str) -> TargetPackage:
    """Load one exact target record from strict immutable metadata."""
    try:
        document = json.loads(metadata.read_text(encoding="utf-8"))
        records = document["series"]
        record = records[series]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise TargetPackageError(f"target pkg metadata does not define {series}") from error
    if (
        not isinstance(document, dict)
        or set(document) != {"schema", "series"}
        or document["schema"] != 1
        or not isinstance(records, dict)
        or set(records) != {"26.1", "26.7"}
        or not isinstance(record, dict)
        or set(record) != TARGET_FIELDS
        or not all(isinstance(record[field], str) and record[field] for field in TARGET_FIELDS)
        or record["name"] != "pkg"
        or record["origin"] != "ports-mgmt/pkg"
        or Path(record["filename"]).name != record["filename"]
        or SHA256_PATTERN.fullmatch(record["sha256"]) is None
        or SHA256_PATTERN.fullmatch(record["pkg_static_sha256"]) is None
    ):
        raise TargetPackageError(f"target pkg metadata is invalid for {series}")
    identity = PackageIdentity(
        record["name"], record["version"], record["origin"], record["abi"]
    )
    return TargetPackage(
        series,
        identity,
        record["filename"],
        record["sha256"],
        record["pkg_static_sha256"],
    )


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, capture_output=True, text=True)


def query_identity(pkg_command: str, arguments: list[str]) -> PackageIdentity:
    result = run([pkg_command, *arguments, "%n|%v|%o|%q"])
    lines = result.stdout.strip().splitlines()
    if len(lines) != 1:
        raise TargetPackageError("cannot read one target pkg identity")
    fields = lines[0].split("|")
    if len(fields) != 4 or not all(fields):
        raise TargetPackageError("target pkg identity is malformed")
    return PackageIdentity(*fields)


def downloaded_archive(directory: Path, filename: str) -> Path:
    candidates = (directory / filename, directory / "All" / filename)
    matches = tuple(candidate for candidate in candidates if candidate.is_file())
    if len(matches) != 1:
        raise TargetPackageError(f"pkg did not fetch exactly one {filename}")
    return matches[0]


def fetch_target_pkg(target: TargetPackage, pkg_command: str, repository: str, output: Path) -> Path:
    run(
        [
            pkg_command,
            "fetch",
            "-y",
            "-r",
            repository,
            "-o",
            str(output),
            target.filename.removesuffix(".pkg"),
        ]
    )
    return downloaded_archive(output, target.filename)


def verify_target_pkg(
    target: TargetPackage,
    pkg_command: str,
    *,
    pkg_static_path: Path = Path("/usr/local/sbin/pkg-static"),
) -> None:
    """Require the installed, locked creator and its static parser to be exact."""
    installed = query_identity(
        pkg_command, ["query", "-e", "%n = pkg"]
    )
    if installed != target.identity:
        raise TargetPackageError("installed pkg identity differs from the pinned creator")
    locked = run([pkg_command, "lock", "-l"]).stdout.splitlines()
    if f"pkg-{target.identity.version}" not in locked:
        raise TargetPackageError("target pkg is not locked")
    if not pkg_static_path.is_file() or sha256(pkg_static_path) != target.pkg_static_sha256:
        raise TargetPackageError("pkg-static SHA-256 differs from the pinned creator")
    static_version = run([str(pkg_static_path), "-v"]).stdout.strip()
    if static_version != target.identity.version.split("_", 1)[0]:
        raise TargetPackageError("pkg-static version differs from the pinned creator")


def select_target_pkg(
    metadata: Path,
    series: str,
    pkg_command: str,
    repository: str = "OPNsense",
    *,
    pkg_static_path: Path = Path("/usr/local/sbin/pkg-static"),
) -> TargetPackage:
    """Fetch, hash, install, lock, and verify exact target package-manager bytes."""
    target = load_target(metadata, series)
    with tempfile.TemporaryDirectory() as temporary_directory:
        downloads = Path(temporary_directory)
        archive = fetch_target_pkg(target, pkg_command, repository, downloads)
        if sha256(archive) != target.sha256:
            raise TargetPackageError("target pkg archive SHA-256 does not match metadata")
        archive_identity = query_identity(pkg_command, ["query", "-F", str(archive)])
        if archive_identity != target.identity:
            raise TargetPackageError("target pkg archive identity does not match metadata")
        run([pkg_command, "add", "-f", str(archive)])
    run([pkg_command, "lock", "-y", "pkg"])
    verify_target_pkg(target, pkg_command, pkg_static_path=pkg_static_path)
    return target


def refresh_archive_sha256(
    metadata: Path,
    content_metadata: Path,
    series: str,
    pkg_command: str,
    repository: str,
    output: Path,
) -> str:
    """Refresh only an archive digest whose identity and extracted contents are unchanged."""
    target = load_target(metadata, series)
    expected_content = load_content_sha256(content_metadata, series)
    with tempfile.TemporaryDirectory() as temporary_directory:
        downloads = Path(temporary_directory)
        archive = fetch_target_pkg(target, pkg_command, repository, downloads)
        if query_identity(pkg_command, ["query", "-F", str(archive)]) != target.identity:
            raise TargetPackageError("target pkg archive identity does not match metadata")
        if package_content_sha256(archive) != expected_content:
            raise TargetPackageError("target pkg extracted contents differ from the pinned package")
        archive_sha256 = sha256(archive)
    document = json.loads(metadata.read_text(encoding="utf-8"))
    document["series"][series]["sha256"] = archive_sha256
    output.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return archive_sha256


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("install", "verify"):
        command = commands.add_parser(name)
        command.add_argument("metadata", type=Path)
        command.add_argument("series")
        command.add_argument("--pkg-command", default="pkg")
        command.add_argument("--pkg-static", type=Path, default=Path("/usr/local/sbin/pkg-static"))
        command.add_argument("--repository", default="OPNsense")
    field = commands.add_parser("field")
    field.add_argument("metadata", type=Path)
    field.add_argument("series")
    field.add_argument("field", choices=sorted(TARGET_FIELDS))
    refresh = commands.add_parser("refresh")
    refresh.add_argument("metadata", type=Path)
    refresh.add_argument("content_metadata", type=Path)
    refresh.add_argument("series")
    refresh.add_argument("--pkg-command", default="pkg")
    refresh.add_argument("--repository", default="OPNsense")
    refresh.add_argument("--output", type=Path, required=True)
    changed = commands.add_parser("changed-series")
    changed.add_argument("before", type=Path)
    changed.add_argument("after", type=Path)
    arguments = parser.parse_args()
    try:
        if arguments.command == "refresh":
            print(
                refresh_archive_sha256(
                    arguments.metadata,
                    arguments.content_metadata,
                    arguments.series,
                    arguments.pkg_command,
                    arguments.repository,
                    arguments.output,
                )
            )
        elif arguments.command == "install":
            target = select_target_pkg(
                arguments.metadata,
                arguments.series,
                arguments.pkg_command,
                arguments.repository,
                pkg_static_path=arguments.pkg_static,
            )
            print(json.dumps(target.record(), sort_keys=True, separators=(",", ":")))
        elif arguments.command == "verify":
            target = load_target(arguments.metadata, arguments.series)
            verify_target_pkg(
                target, arguments.pkg_command, pkg_static_path=arguments.pkg_static
            )
        elif arguments.command == "field":
            target = load_target(arguments.metadata, arguments.series)
            print(target.record()[arguments.field])
        elif arguments.command == "changed-series":
            series = changed_archive_series(arguments.before, arguments.after)
            if series is not None:
                print(series)
    except (TargetPackageError, OSError, subprocess.CalledProcessError) as error:
        print(f"target pkg selection failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
