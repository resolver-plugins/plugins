#!/usr/bin/env python3
"""Event routing and complete changed-file discovery for package producers."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from bind.runtime import bind920_profile

KINDS = ("bind_runtime", "bind_plugin", "dhcp_interface_ha")
RELEASE_PREFIX = {"bind_runtime": "release/bind-rp/", "bind_plugin": "release/bind-rp/",
                  "dhcp_interface_ha": "release/dhcp-interface-ha/"}
EVENT_FIELDS = {"name", "ref", "sha", "workflow_sha", "before", "merged", "base_ref",
                "merge_commit_sha", "inputs", "pr_base_ref"}
OUTPUT_FIELDS = ("mode", "series", "source_ref", "profile_ref", "control_ref",
                 "prerelease_tag", "pull_number", "release")
SHARED_PATHS = (".github/ci/bind/shared/", ".github/ci/shared/")
PACKAGE_DEFINITION = "dns/bind/Makefile"
APPROVAL_LINE = re.compile(r"^\s*RP_BIND_(?:VERSION|PORTS_COMMIT)\s*[?+:!]?=")
SHARED_FILES = {"docs/package-repository/resolver-plugins.pub", "scripts/install-os-bind-rp.sh",
                "scripts/install-os-bind-rp-prerequisites.py"}


def watches(component, path):
    if component == "dhcp_interface_ha":
        if path == "net/dhcp-interface-ha/README.md" or path.startswith(("net/dhcp-interface-ha/tests/", ".github/ci/ci-tests/")):
            return False
        return (path == "docs/package-repository/resolver-plugins.pub"
                or path == ".github/workflows/ha-dhcp-interface-release.yml"
                or path.startswith(("net/dhcp-interface-ha/", "Mk/", "Scripts/", "Templates/",
                                    ".github/ci/ha_dhcp/", *SHARED_PATHS)))
    if component == "bind_runtime":
        return path == PACKAGE_DEFINITION
    if path.startswith(("dns/bind/tests/", ".github/ci/ci-tests/")):
        return False
    if path in SHARED_FILES or path.startswith(SHARED_PATHS):
        return True
    return path.startswith(("dns/bind/", "Mk/", "Scripts/", "Templates/", ".github/ci/bind/plugin/")) or path == ".github/workflows/bind-plugin-release.yml"


def _commit(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise ValueError("event requires a full immutable commit")
    return value


def package_changes(before, after):
    """Separate runtime approvals from the plugin-visible package definition."""
    if not isinstance(before, str) or not isinstance(after, str):
        raise ValueError("package changes require immutable before/after definitions")
    previous = bind920_profile.approval_from_makefile(before, allow_absent=True)
    current = bind920_profile.approval_from_makefile(after)
    runtime_changed = previous is not None and previous != current
    if runtime_changed:
        def version(value):
            base, _, revision = value.partition("_")
            return (*map(int, base.split(".")), int(revision or "0"))
        if version(current["version"]) <= version(previous["version"]):
            raise ValueError("a changed runtime approval requires a newer native package version")
    plugin_lines = lambda text: [line for line in text.splitlines() if not APPROVAL_LINE.match(line)]
    return runtime_changed, plugin_lines(before) != plugin_lines(after)


def select_event(component, event, refs, changed_paths, *, before_package=None, after_package=None):
    if component not in KINDS or not isinstance(event, dict) or set(event) != EVENT_FIELDS:
        raise ValueError("invalid component event")
    if (not isinstance(event["inputs"], dict) or set(event["inputs"]) != {"mode", "series", "pull_number"}
            or not isinstance(refs, list) or not all(isinstance(ref, str) for ref in refs)
            or not isinstance(changed_paths, list) or not all(isinstance(path, str) for path in changed_paths)):
        raise ValueError("invalid registered refs or event inputs")
    selected = dict.fromkeys(OUTPUT_FIELDS)
    selected.update(mode="production", release=False)
    prefix = RELEASE_PREFIX[component]
    series_refs = {match[1]: ref for ref in refs
                   if (match := re.fullmatch("refs/heads/" + re.escape(prefix) + r"([0-9]+\.[0-9]+)", ref))}
    name = event["name"]
    if name == "push":
        if event["ref"] != "refs/heads/master":
            raise ValueError("production pushes require merged master control")
        relevant = [path for path in changed_paths if watches(component, path)]
        if component != "dhcp_interface_ha" and PACKAGE_DEFINITION in relevant:
            runtime_changed, plugin_changed = package_changes(before_package, after_package)
            if not (runtime_changed if component == "bind_runtime" else plugin_changed):
                relevant.remove(PACKAGE_DEFINITION)
        if not relevant:
            return selected
        if not series_refs:
            raise ValueError("no registered numeric release series")
        series = max(series_refs, key=lambda value: tuple(map(int, value.split("."))))
        profile = series_refs[series]
        control = _commit(event["sha"])
    elif name == "pull_request_target":
        if type(event["merged"]) is not bool:
            raise ValueError("invalid release-source merge state")
        if not event["merged"] or component == "bind_runtime":
            return selected
        match = re.fullmatch(re.escape(prefix) + r"([0-9]+\.[0-9]+)", event["base_ref"] or "")
        if match is None or match[1] not in series_refs:
            raise ValueError("unsupported release-source merge base")
        if not any(watches(component, path) for path in changed_paths):
            return selected
        series = match[1]
        profile, control = _commit(event["merge_commit_sha"]), _commit(event["workflow_sha"])
    elif name == "workflow_dispatch":
        inputs = event["inputs"]
        mode, series = inputs["mode"], inputs["series"]
        if component == "dhcp_interface_ha" and mode is None:
            mode = "production"
        if component == "dhcp_interface_ha" and mode != "production":
            raise ValueError("HA dispatch requires production mode")
        if mode not in ("production", "development") or series not in series_refs:
            raise ValueError("unsupported dispatch mode or registered series")
        control = _commit(event["sha"])
        profile = series_refs[series]
        selected["mode"] = mode
        if mode == "production":
            if event["ref"] != "refs/heads/master":
                raise ValueError("production dispatch requires merged master control")
        else:
            pull = inputs["pull_number"]
            if not isinstance(pull, str) or re.fullmatch(r"[1-9][0-9]*", pull) is None:
                raise ValueError("development dispatch requires a pull request number")
            if event["pr_base_ref"] == f"{prefix}{series}":
                profile = f"refs/pull/{pull}/head"
            elif event["pr_base_ref"] != "master":
                raise ValueError("pull request base does not supply selected release source")
            selected.update(pull_number=pull, prerelease_tag=f"pr-{pull}-{series}-{component.replace('_', '-')}")
    else:
        raise ValueError("unsupported package producer event")
    selected.update(series=series, profile_ref=profile, control_ref=control, release=True)
    if component != "bind_runtime":
        selected["source_ref"] = profile
    return selected


def normalize_event(document, *, pr_base_ref=None):
    if isinstance(document, dict) and set(document) == EVENT_FIELDS:
        return document
    pull = document.get("pull_request") or {}
    supplied = document.get("inputs") or {}
    inputs = {key: supplied.get(key) for key in ("mode", "series", "pull_number")}
    inputs["pull_number"] = str(inputs["pull_number"]) if inputs["pull_number"] is not None else None
    return dict(name=os.environ.get("GITHUB_EVENT_NAME"), ref=os.environ.get("GITHUB_REF"),
                sha=os.environ.get("GITHUB_SHA"), workflow_sha=os.environ.get("GITHUB_WORKFLOW_SHA"),
                before=document.get("before"), merged=pull.get("merged"),
                base_ref=(pull.get("base") or {}).get("ref"), merge_commit_sha=pull.get("merge_commit_sha"),
                inputs=inputs, pr_base_ref=pr_base_ref)


def discover_changed_paths(event, document):
    """Read the complete event delta; a rebase merge can span several commits."""
    if event["name"] == "push":
        after, before = _commit(event["sha"]), _commit(event["before"])
        for commit in (after, before):
            if commit == "0" * 40:
                continue
            if subprocess.run(["git", "cat-file", "-e", f"{commit}^{{commit}}"],
                              capture_output=True).returncode:
                subprocess.run(["git", "fetch", "--no-tags", "origin", commit], check=True)
        command = (["git", "diff-tree", "--root", "--no-commit-id", "--name-only", "-r", "-z", after]
                   if before == "0" * 40 else ["git", "diff", "--name-only", "-z", before, after, "--"])
        return [path for path in subprocess.check_output(command).decode().split("\0") if path]
    if event["name"] != "pull_request_target" or event["merged"] is False:
        return []
    _commit(event["merge_commit_sha"])
    number = document.get("number", (document.get("pull_request") or {}).get("number"))
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    if (type(number) is not int or number < 1
            or re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) is None):
        raise ValueError("merged PR path discovery requires trusted repository and PR number")
    pages = json.loads(subprocess.check_output(["gh", "api", "--paginate", "--slurp",
        f"repos/{repository}/pulls/{number}/files?per_page=100"], text=True))
    return [name for page in pages for record in page
            for name in (record["filename"], record.get("previous_filename")) if name is not None]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    matrix = commands.add_parser("release-matrix")
    matrix.add_argument("--component", choices=KINDS, default="bind_plugin")
    matrix.add_argument("--refs-file", type=Path, required=True)
    matrix.add_argument("--output", type=Path, required=True)
    event_parser = commands.add_parser("select-event")
    event_parser.add_argument("--component", choices=KINDS, required=True)
    event_parser.add_argument("--event-file", type=Path, required=True)
    event_parser.add_argument("--refs-file", type=Path, required=True)
    event_parser.add_argument("--changed-paths-file", type=Path, help="explicit paths; otherwise discover the complete event delta")
    event_parser.add_argument("--output", type=Path, required=True, help="JSON selection output")
    event_parser.add_argument("--github-output", type=Path, help="append fixed Actions outputs")
    event_parser.add_argument("--pr-base-ref")
    args = parser.parse_args()
    if args.command == "release-matrix":
        series = []
        for line in args.refs_file.read_text().splitlines():
            _object_id, ref = line.split("\t", maxsplit=1)
            candidate = ref.removeprefix("refs/heads/" + RELEASE_PREFIX[args.component])
            if re.fullmatch(r"\d+\.\d+", candidate) is None:
                raise ValueError(f"invalid release branch name: {ref}")
            series.append(candidate)
        with args.output.open("a") as stream:
            print(f"series={json.dumps(sorted(set(series)))}", file=stream)
        return
    document = json.loads(args.event_file.read_text())
    event = normalize_event(document, pr_base_ref=args.pr_base_ref)
    if (event["name"] == "workflow_dispatch" and event["inputs"]["mode"] == "development"
            and event["pr_base_ref"] is None):
        pull = event["inputs"]["pull_number"]
        repository = os.environ.get("GITHUB_REPOSITORY", "")
        if (not isinstance(pull, str) or re.fullmatch(r"[1-9][0-9]*", pull) is None
                or re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) is None):
            parser.error("development PR lookup requires trusted repository context and pull number")
        event["pr_base_ref"] = subprocess.check_output(
            ["gh", "api", f"repos/{repository}/pulls/{pull}", "--jq", ".base.ref"], text=True).strip()
    changed = (json.loads(args.changed_paths_file.read_text()) if args.changed_paths_file is not None
               else discover_changed_paths(event, document))
    package = {}
    if event["name"] == "push" and PACKAGE_DEFINITION in changed:
        for key, commit in (("before_package", event["before"]), ("after_package", event["sha"])):
            package[key] = ("" if _commit(commit) == "0" * 40 else subprocess.check_output(
                ["git", "show", f"{commit}:{PACKAGE_DEFINITION}"], text=True))
    selected = select_event(args.component, event, json.loads(args.refs_file.read_text()), changed, **package)
    args.output.write_text(json.dumps(selected, sort_keys=True, indent=2) + "\n")
    if args.github_output is not None:
        with args.github_output.open("a") as stream:
            for key in OUTPUT_FIELDS:
                value = selected[key]
                stream.write(f"{key}={str(value).lower() if isinstance(value, bool) else value or ''}\n")


if __name__ == "__main__":
    main()
