#!/usr/bin/env python3
"""Select HA DHCP releases and their reviewed OPNsense build repository."""
import argparse
import json
import os
import re
import subprocess

MAKEFILE = "net/dhcp-interface-ha/Makefile"


def repository_snapshot(series):
    if series != "26.7":
        raise ValueError("HA DHCP releases support only OPNsense 26.7")
    # This signed catalogue contains the exact target-pkg.json archive pin.
    return "26.7.4"


def git(*args):
    return subprocess.check_output(["git", *args], text=True).strip()


def version_at(commit):
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("release selection requires an exact source commit")
    git("cat-file", "-e", f"{commit}^{{commit}}")
    if not git("ls-tree", "--name-only", commit, "--", MAKEFILE):
        return None
    source = git("show", f"{commit}:{MAKEFILE}")
    versions = re.findall(r"^PLUGIN_VERSION=\s*([0-9]+(?:\.[0-9]+)*)\s*$", source, re.M)
    revisions = re.findall(r"^PLUGIN_REVISION=\s*([0-9]+)\s*$", source, re.M)
    if len(versions) != 1 or len(revisions) != 1:
        raise ValueError("release requires literal numeric PLUGIN_VERSION and PLUGIN_REVISION")
    # pkg treats trailing zero version components and revision zero as equal.
    components = [int(part) for part in versions[0].split(".")]
    while len(components) > 1 and components[-1] == 0:
        components.pop()
    return tuple(components), int(revisions[0])


def select(event, ref, source, before=None):
    if ref != "refs/heads/master" or event not in {"push", "workflow_dispatch"}:
        raise ValueError("release requires a master push or manual master dispatch")
    current = version_at(source)
    if current is None:
        raise ValueError("selected source does not contain the HA DHCP plugin")
    if event == "workflow_dispatch":
        return True
    previous = None if before == "0" * 40 else version_at(before or "")
    if previous is not None and current < previous:
        raise ValueError("plugin version decreased; release requires a version increase")
    return current != previous


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["select", "snapshot"])
    parser.add_argument("--series", default="26.7")
    args = parser.parse_args()
    snapshot = repository_snapshot(args.series)
    if args.command == "snapshot":
        print(snapshot)
    else:
        with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as stream:
            payload = json.load(stream)
        source = os.environ["GITHUB_SHA"]
        release = select(os.environ["GITHUB_EVENT_NAME"], os.environ["GITHUB_REF"],
                         source, payload.get("before"))
        print(f"release={str(release).lower()}\nseries={args.series}\nsource_commit={source}")
