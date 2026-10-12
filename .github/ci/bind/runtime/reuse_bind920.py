#!/usr/bin/env python3
"""Authenticate the frozen runtime cache and copy only its owned producer files."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from bind.shared import components
from shared import package_catalogue


def reuse(
    component_directory: Path,
    trusted_directory: Path,
    series: str,
    output: Path,
    *,
    selection_sha256: str,
    pkg_command: str,
) -> None:
    """Keep original package/provenance bytes; failed intake never triggers a build."""
    selection = components.load_selection(
        trusted_directory, component="bind_runtime", series=series,
        selection_sha256=selection_sha256,
    )
    if selection["producer_action"] != "reuse":
        raise ValueError("runtime reuse requires the sealed reuse action")
    manifest = components.validate_candidate(
        component_directory, selection, trusted_directory, pkg_command=None,
    )
    package_catalogue.verify_packages(
        [component_directory / name for name in manifest["packages"]],
        component_directory.resolve().as_uri(),
        trusted_directory / "reader-target.json", trusted_directory / "upstream.json",
        pkg_command=pkg_command,
    )
    components.validate_candidate(
        component_directory, selection, trusted_directory, pkg_command=pkg_command,
    )
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError("runtime producer output must be empty")
    for name in (*manifest["packages"], "bind920-provenance.json"):
        shutil.copyfile(component_directory / name, output / name)
    components.validate_producer_copy(output, component_directory, "bind_runtime", series)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("series")
    parser.add_argument("output", type=Path)
    parser.add_argument("--component-directory", type=Path, required=True)
    parser.add_argument("--trusted-directory", type=Path, required=True)
    parser.add_argument("--selection-sha256", required=True)
    parser.add_argument("--pkg-command", default="/usr/local/sbin/pkg-static")
    arguments = parser.parse_args()
    try:
        reuse(arguments.component_directory, arguments.trusted_directory, arguments.series,
              arguments.output, selection_sha256=arguments.selection_sha256,
              pkg_command=arguments.pkg_command)
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        print(f"BIND frozen reuse failed: {error}", file=sys.stderr)
        return 1
    print("Reused the authenticated frozen BIND runtime pair")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
