#!/usr/bin/env python3
"""Route a runtime package's own version review to its registered series."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from bind.shared import selection


def eligible(before, after):
    return selection.package_changes(before, after)[0]


def definition(commit, path):
    if not commit or commit == "0" * 40:
        return ""
    selection._commit(commit)
    if subprocess.run(["git", "cat-file", "-e", f"{commit}:{path}"], capture_output=True).returncode:
        return ""
    return subprocess.check_output(["git", "show", f"{commit}:{path}"], text=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--component", choices=("bind_tools", "bind920"), required=True)
    parser.add_argument("--event-file", type=Path, required=True)
    parser.add_argument("--github-output", type=Path, required=True)
    args = parser.parse_args()
    event = json.loads(args.event_file.read_text())
    kind = os.environ["GITHUB_EVENT_NAME"]
    mode, pull, requested = "production", "", None
    if kind == "workflow_dispatch":
        mode = event["inputs"]["mode"]
        pull = event["inputs"].get("pull_number", "")
        requested = event["inputs"]["series"]
        changed = True
    else:
        path = selection.APPROVAL_PATHS[args.component]
        if kind == "pull_request":
            pr = event["pull_request"]
            before, after = pr["base"]["sha"], pr["head"]["sha"]
            mode, pull = "development", str(pr["number"])
        elif kind == "push" and os.environ["GITHUB_REF"] == "refs/heads/master":
            before, after = event["before"], os.environ["GITHUB_SHA"]
        else:
            raise ValueError("unsupported runtime package event")
        changed = eligible(definition(before, path), definition(after, path))
    matrix = []
    if changed:
        pages = json.loads(subprocess.check_output(["gh", "api", "--paginate", "--slurp",
            f'repos/{os.environ["GITHUB_REPOSITORY"]}/git/matching-refs/heads/release/bind-rp/'], text=True))
        import re
        series = sorted({match[1] for page in pages for record in page
                         if (match := re.fullmatch(r"refs/heads/release/bind-rp/(\d+\.\d+)", record["ref"]))},
                        key=lambda value: tuple(map(int, value.split("."))))
        if requested is not None and requested not in series:
            raise ValueError("runtime package requires a registered numeric compatibility series")
        matrix = [dict(series=value) for value in series if requested is None or value == requested]
        if not matrix:
            raise ValueError("no registered runtime compatibility series")
    with args.github_output.open("a") as stream:
        print("matrix=" + json.dumps(dict(include=matrix), separators=(",", ":")), file=stream)
        print("mode=" + (mode if matrix else ""), file=stream)
        print("pull_number=" + pull, file=stream)


if __name__ == "__main__":
    main()
