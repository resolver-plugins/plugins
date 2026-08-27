#!/usr/local/bin/python3
# Copyright (C) 2026 Bryan Wiegand <inbox@kw-ventures.com>
# All rights reserved.

"""Preserve effective dynamic zones across package-managed template rendering."""

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import bindStop


RNDC = os.environ.get("BIND_STOP_RNDC", "/usr/local/sbin/rndc")
MANIFEST = "manifest.json"


def error(message):
    print(f"bind package zone preservation: {message}", file=sys.stderr)


def backup_directory(value):
    directory = Path(value)
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("backup path is not a directory")
    return directory


def zone_source(zone):
    return bindStop.ZONE_DIR / f"{zone}.db"


def regular_metadata(path):
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"zone master is not a regular file: {path}")
    return metadata


def digest(path):
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(block)
    return checksum.hexdigest()


def run_rndc(action, zone):
    try:
        result = subprocess.run([RNDC, action, zone], check=False)
    except OSError as exception:
        error(f"unable to run rndc {action} for {zone}: {exception}")
        return False
    if result.returncode:
        error(f"rndc {action} failed for {zone} with exit status {result.returncode}")
        return False
    return True


def thaw(zones):
    success = True
    for zone in reversed(zones):
        success = run_rndc("thaw", zone) and success
    return success


def write_manifest(directory, entries):
    descriptor, temporary = tempfile.mkstemp(prefix=".manifest.", dir=directory)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump({"version": 1, "zones": entries}, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, directory / MANIFEST)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def snapshot(directory, zones):
    entries = []
    for index, zone in enumerate(zones):
        source = zone_source(zone)
        metadata = regular_metadata(source)
        backup_name = f"zone-{index:04d}.db"
        destination = directory / backup_name
        with source.open("rb") as source_stream, destination.open("xb") as destination_stream:
            shutil.copyfileobj(source_stream, destination_stream)
            destination_stream.flush()
            os.fsync(destination_stream.fileno())
        entries.append({
            "backup": backup_name,
            "gid": metadata.st_gid,
            "mode": stat.S_IMODE(metadata.st_mode),
            "sha256": digest(destination),
            "uid": metadata.st_uid,
            "zone": zone,
        })
    write_manifest(directory, entries)


def load_manifest(directory):
    with (directory / MANIFEST).open("r", encoding="utf-8") as stream:
        manifest = json.load(stream)
    if set(manifest) != {"version", "zones"} or manifest["version"] != 1:
        raise ValueError("unsupported backup manifest")
    if not isinstance(manifest["zones"], list):
        raise ValueError("invalid backup zone list")
    expected = {"backup", "gid", "mode", "sha256", "uid", "zone"}
    seen = set()
    for entry in manifest["zones"]:
        if not isinstance(entry, dict) or set(entry) != expected:
            raise ValueError("invalid backup zone entry")
        zone = entry["zone"]
        if not isinstance(zone, str) or not bindStop.valid_zone(zone) or zone in seen:
            raise ValueError("invalid or duplicate backup zone")
        seen.add(zone)
        backup = directory / entry["backup"]
        regular_metadata(backup)
        if digest(backup) != entry["sha256"]:
            raise ValueError(f"backup checksum mismatch for {zone}")
        if not all(isinstance(entry[key], int) for key in ("uid", "gid", "mode")):
            raise ValueError(f"invalid backup metadata for {zone}")
    return manifest["zones"]


def prepare(directory):
    zones = sorted(bindStop.dynamic_model_zones(strict=True))
    for zone in zones:
        regular_metadata(zone_source(zone))

    frozen = []
    for zone in zones:
        if not run_rndc("freeze", zone):
            thaw(frozen)
            return 1
        frozen.append(zone)
    if not bindStop.stop_named():
        thaw(frozen)
        return 1

    bindStop.stop_watcher()
    snapshot(directory, zones)
    bindStop.clear_journals(zones)
    try:
        bindStop.STATE_FILE.unlink()
    except FileNotFoundError:
        pass
    return 0


def restore(directory):
    entries = load_manifest(directory)
    for entry in entries:
        source = directory / entry["backup"]
        target = zone_source(entry["zone"])
        descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=bindStop.ZONE_DIR)
        try:
            with source.open("rb") as source_stream, os.fdopen(descriptor, "wb") as target_stream:
                shutil.copyfileobj(source_stream, target_stream)
                target_stream.flush()
                os.fsync(target_stream.fileno())
                os.fchmod(target_stream.fileno(), entry["mode"])
                os.fchown(target_stream.fileno(), entry["uid"], entry["gid"])
            os.replace(temporary, target)
        except BaseException:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise
    return 0


def discard(directory):
    entries = load_manifest(directory)
    for entry in entries:
        (directory / entry["backup"]).unlink()
    (directory / MANIFEST).unlink()
    directory.rmdir()
    return 0


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in {"prepare", "restore", "discard"}:
        error("usage: bindPackageZones.py prepare|restore|discard backup-directory")
        return 2
    try:
        directory = backup_directory(sys.argv[2])
        return globals()[sys.argv[1]](directory)
    except (OSError, ValueError, json.JSONDecodeError) as exception:
        error(str(exception))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
