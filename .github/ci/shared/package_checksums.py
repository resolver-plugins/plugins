#!/usr/bin/env python3
"""Verify that a target pkg parser retains every archive file checksum."""

from __future__ import annotations

import argparse
import io
import json
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path


class PackageChecksumError(ValueError):
    """A package archive has no complete target-readable file checksum set."""


# pkg type 1 is SHA256 hex; type 2 is BLAKE2b encoded with z-base32.
CHECKSUM_PATTERN = re.compile(
    r"(?:1\$)?[0-9a-fA-F]{64}|2\$[ybndrfg8ejkmcpqxot1uwisza345h769]{103}"
)


def use_scalar_file_checksums(archive: Path) -> None:
    """Preserve native digests in the scalar layout accepted by older readers.

    pkg 2.3.1 drops object-valued sums whose length is not 64. Archive headers
    already carry ownership and modes; preserve header attributes, payload and digests.
    """
    with tarfile.open(archive) as source:
        if sum(member.name == '+MANIFEST' for member in source.getmembers()) != 1:
            raise PackageChecksumError('package must contain one full manifest')
        manifest = json.load(source.extractfile('+MANIFEST'))
        files = manifest.get('files')
        if not isinstance(files, dict) or not files:
            raise PackageChecksumError('package has no file checksum map')
        if not any(isinstance(value, dict) for value in files.values()):
            return
        sums = {path: value.get('sum') if isinstance(value, dict) else value
                for path, value in files.items()}
        if any(not path or not isinstance(value, str) or CHECKSUM_PATTERN.fullmatch(value) is None
               for path, value in sums.items()):
            raise PackageChecksumError('package has an invalid native file checksum')
        manifest['files'] = sums
        data = json.dumps(manifest, separators=(',', ':')).encode()
        with tempfile.TemporaryDirectory(prefix='pkg-manifest-', dir=archive.parent) as temporary:
            output = Path(temporary) / archive.name
            with tarfile.open(output, 'w:xz') as target:
                for member in source:
                    if member.name == '+MANIFEST':
                        member.size = len(data)
                        member.pax_headers.pop('size', None)
                        target.addfile(member, io.BytesIO(data))
                    else:
                        target.addfile(member, source.extractfile(member) if member.isfile() else None)
            output.replace(archive)


def archive_file_checksums(
    pkg_command: str, archive: Path
) -> tuple[tuple[str, str], ...]:
    """Return immutable file/checksum rows emitted by the selected pkg parser."""
    result = subprocess.run(
        [pkg_command, "query", "-F", str(archive), "%Fp|%Fs"],
        check=True,
        text=True,
        capture_output=True,
    )
    rows = []
    for line in result.stdout.splitlines():
        fields = line.split("|", 1)
        if len(fields) != 2:
            raise PackageChecksumError(f"malformed file checksum metadata: {archive.name}")
        rows.append((fields[0], fields[1]))
    return tuple(rows)


def verify_archive(
    pkg_command: str, archive: Path
) -> tuple[tuple[str, str], ...]:
    """Require a non-empty, non-null checksum for every packaged file."""
    rows = archive_file_checksums(pkg_command, archive)
    if not rows or any(not path or checksum in {"", "(null)"} for path, checksum in rows):
        raise PackageChecksumError(
            f"package has incomplete target-readable file checksums: {archive.name}"
        )
    if any(CHECKSUM_PATTERN.fullmatch(checksum) is None for _, checksum in rows):
        raise PackageChecksumError(
            f"package has an unrecognized target-readable checksum: {archive.name}"
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pkg-command", default="pkg")
    parser.add_argument("--scalar-file-checksums", action="store_true")
    parser.add_argument("archives", nargs="+", type=Path)
    arguments = parser.parse_args()
    try:
        for archive in arguments.archives:
            if arguments.scalar_file_checksums:
                use_scalar_file_checksums(archive)
            verify_archive(arguments.pkg_command, archive)
    except (PackageChecksumError, OSError, subprocess.CalledProcessError) as error:
        print(f"package checksum verification failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
