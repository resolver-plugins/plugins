"""Materialize reviewed sources and verify their packaged source identity."""
import json
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile


def verify_package_source(archive, plugin_name, source_commit):
    # FreeBSD tar understands native pkg compression; target Python need not.
    entries = subprocess.check_output(["tar", "-tf", str(archive)], text=True).splitlines()
    path = f"usr/local/opnsense/version/{plugin_name}"
    matches = [entry for entry in entries if entry in {path, "/" + path, "./" + path}]
    if len(matches) != 1:
        raise ValueError("fresh plugin archive lacks one packaged version file")
    version = json.loads(subprocess.check_output(["tar", "-xOf", str(archive), matches[0]], text=True))
    if not isinstance(version, dict) or version.get("product_hash") != source_commit:
        raise ValueError("fresh packaged version hash differs from selected source")


def materialize_source(repository, source_commit, *, package_path="dns/bind", preserve_tests=False):
    """Extract only exact source-owned paths, leaving control code/policy intact."""
    source_paths = (package_path, "Mk", "Scripts", "Templates")
    source_required = (f"{package_path}/Makefile", "Mk/plugins.mk", "Mk/defaults.mk", "Mk/common.mk")
    if re.fullmatch(r"[0-9a-f]{40}", source_commit) is None:
        raise ValueError("plugin source must be a full immutable commit")
    command = ["git", "-C", str(repository)]
    resolved = subprocess.check_output([*command, "rev-parse", f"{source_commit}^{{commit}}"], text=True).strip()
    if resolved != source_commit:
        raise ValueError("plugin source object differs from selection")
    for path in source_required:
        if subprocess.check_output([*command, "cat-file", "-t", f"{source_commit}:{path}"], text=True).strip() != "blob":
            raise ValueError(f"required plugin source input is missing: {path}")
    present = []
    for path in source_paths:
        result = subprocess.run([*command, "cat-file", "-t", f"{source_commit}:{path}"], text=True, capture_output=True)
        if result.returncode == 0:
            if result.stdout.strip() != "tree":
                raise ValueError(f"plugin source input is not a directory: {path}")
            present.append(path)
    with tempfile.TemporaryDirectory() as temporary:
        extracted = Path(temporary)
        archive = extracted / "source.tar"
        with archive.open("wb") as stream:
            subprocess.run([*command, "archive", source_commit, "--", *present], check=True, stdout=stream)
        with tarfile.open(archive) as source:
            source.extractall(extracted, filter="data")
        canonical_tests = repository / package_path / "tests"
        saved_tests = extracted / "canonical-tests"
        if preserve_tests and canonical_tests.is_dir():
            shutil.copytree(canonical_tests, saved_tests)
        # Prepare every source tree before replacing any checkout tree.
        for path in source_paths:
            destination = repository / path
            if destination.is_symlink() or destination.is_file():
                destination.unlink()
            elif destination.exists():
                shutil.rmtree(destination)
            if path in present:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(extracted / path, destination, symlinks=True)
        if preserve_tests:
            shutil.rmtree(canonical_tests, ignore_errors=True)
            if saved_tests.exists():
                shutil.copytree(saved_tests, canonical_tests)
