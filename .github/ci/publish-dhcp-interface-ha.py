#!/usr/bin/env python3
"""Publish a verified plugin build as an immutable experimental prerelease."""
import argparse
from pathlib import Path
import re
import tempfile

from release_channel import (
    asset_order, run_gh, snapshot_matches_directory, snapshot_release,
    upload_release_assets,
)


def publish(repository: str, directory: Path, series: str, source_commit: str) -> None:
    metadata = dict(line.split("=", 1) for line in
                    (directory / "build-metadata.txt").read_text().splitlines())
    version = metadata["plugin_version"]
    if (not re.fullmatch(r"\d+\.\d+", series)
            or not re.fullmatch(r"[0-9a-f]{40}", source_commit)
            or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*(?:_[0-9]+)?", version)
            or metadata["series"] != series
            or metadata["source_commit"] != source_commit):
        raise ValueError("build metadata does not match the selected source and series")
    expected = {f"os-dhcp-interface-ha-devel-{version}.pkg", "build-metadata.txt",
                "upstream.json", "SHA256SUMS"}
    if {path.name for path in directory.iterdir()} != expected:
        raise ValueError("unexpected release assets")
    tag = f"dhcp-interface-ha-{series}-{version}"
    with tempfile.TemporaryDirectory() as temporary:
        existing = snapshot_release(repository, tag, Path(temporary))
        if existing.existed:
            if not snapshot_matches_directory(existing, directory):
                raise RuntimeError(f"immutable GitHub Release has different bytes: {tag}")
            # Also finish a retry whose complete upload was left as a draft.
            run_gh(["release", "edit", tag, "--repo", repository, "--draft=false", "--latest=false"])
            return
    # Upload to a draft so a partial upload is never presented as a complete release.
    run_gh(["release", "create", tag, "--repo", repository, "--target", source_commit,
            "--title", f"HA DHCP Interface {version} — OPNsense {series}",
            "--prerelease", "--draft", "--latest=false", "--notes",
            "Experimental HA DHCP Interface package. See build-metadata.txt for build provenance."])
    upload_release_assets(repository, tag, asset_order(directory))
    with tempfile.TemporaryDirectory() as temporary:
        uploaded = snapshot_release(repository, tag, Path(temporary))
        if not snapshot_matches_directory(uploaded, directory):
            raise RuntimeError(f"uploaded release has different bytes: {tag}")
    run_gh(["release", "edit", tag, "--repo", repository, "--draft=false", "--latest=false"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--series", required=True)
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args()
    publish(args.repository, args.directory, args.series, args.source_commit)
