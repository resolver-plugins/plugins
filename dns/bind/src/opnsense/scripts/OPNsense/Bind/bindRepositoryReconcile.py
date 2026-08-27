#!/usr/local/bin/python3
"""Safely reconcile os-bind-rp after an OPNsense series upgrade."""

from __future__ import annotations

import argparse
import fcntl
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path


PUBLIC_KEY = "/usr/local/etc/pkg/keys/resolver-plugins.pub"
REPOSITORY_BASE = "https://resolver-plugins.github.io/repository/pkg"
ALLOWED = {
    "os-bind-rp": "opnsense/os-bind-rp",
    "bind920": "dns/bind920",
    "bind-tools": "dns/bind-tools",
}


class ReconcileError(Exception):
    pass


def env_path(name: str, default: str) -> Path:
    return Path(os.environ.get(name, default))


def log(message: str) -> None:
    path = env_path("OS_BIND_RP_LOG", "/var/log/os-bind-rp-reconcile.log")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(message + "\n")


def run(command: list[str], *, allow_status: set[int] | None = None) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    allowed = {0} if allow_status is None else allow_status
    if result.returncode not in allowed:
        raise ReconcileError(result.stderr.strip() or f"command failed: {' '.join(command)}")
    return result


def opnsense_series() -> str:
    command = shlex.split(
        os.environ.get("OS_BIND_RP_OPNSENSE_VERSION_COMMAND", "/usr/local/sbin/opnsense-version")
    )
    series = run([*command, "-a"]).stdout.strip()
    if re.fullmatch(r"[0-9]+\.[0-9]+", series) is None:
        raise ReconcileError(f"invalid OPNsense product ABI: {series or 'empty'}")
    return series


def expected_repository(series: str) -> str:
    return (
        "resolver-plugins: {\n"
        f'  url: "{REPOSITORY_BASE}/${{ABI}}/{series}/latest",\n'
        '  mirror_type: "none",\n'
        '  signature_type: "pubkey",\n'
        f'  pubkey: "{PUBLIC_KEY}",\n'
        "  enabled: yes\n"
        "}\n"
    )


def require_managed_repository(path: Path, series: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise ReconcileError("repository configuration is not a managed regular file")
    if path.read_text(encoding="utf-8") != expected_repository(series):
        raise ReconcileError("repository configuration is not the exact managed Resolver Plugins shape")


def package_command(static: bool = False) -> list[str]:
    if static:
        return shlex.split(os.environ.get("OS_BIND_RP_PKG_STATIC_COMMAND", "/usr/local/sbin/pkg-static"))
    return shlex.split(os.environ.get("OS_BIND_RP_PKG_COMMAND", "/usr/local/sbin/pkg-static"))


def configctl_command() -> list[str]:
    return shlex.split(os.environ.get("OS_BIND_RP_CONFIGCTL_COMMAND", "/usr/local/sbin/configctl"))


def pkg_config_abi() -> str:
    abi = run([*package_command(), "config", "ABI"]).stdout.strip()
    if re.fullmatch(r"FreeBSD:[0-9]+:amd64", abi) is None:
        raise ReconcileError(f"invalid package ABI: {abi or 'empty'}")
    return abi


def record(command: list[str], package: str, remote: bool = False) -> tuple[str, str, str, str] | None:
    args = [*command]
    if remote:
        args += ["rquery", "-r", "resolver-plugins", "-e", f"%n = {package}", "%n|%v|%o|%q"]
    else:
        args += ["query", "-e", f"%n = {package}", "%n|%v|%o|%q"]
    result = run(args, allow_status={0, 1})
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        return None
    if len(lines) != 1:
        raise ReconcileError(f"ambiguous package record for {package}")
    fields = lines[0].split("|")
    if len(fields) != 4 or not all(fields):
        raise ReconcileError(f"invalid package record for {package}")
    name, version, origin, abi = fields
    if name != package:
        raise ReconcileError(f"unexpected package name for {package}: {name}")
    return name, version, origin, abi


def require_candidate(package: str, abi: str, series: str) -> tuple[str, str, str, str]:
    candidate = record(package_command(), package, remote=True)
    if candidate is None:
        raise ReconcileError(f"missing Resolver Plugins candidate: {package}")
    name, version, origin, candidate_abi = candidate
    if origin != ALLOWED[package] or candidate_abi != abi:
        raise ReconcileError(f"invalid Resolver Plugins candidate: {package}")
    if package == "os-bind-rp":
        if re.fullmatch(r"[0-9]+\.[0-9]+_[1-9][0-9]*", version) is None:
            raise ReconcileError("invalid os-bind-rp candidate version")
        candidate_series = version.split("_", 1)[0]
        if candidate_series != series:
            raise ReconcileError(
                f"os-bind-rp candidate series {candidate_series} does not match {series}"
            )
    return candidate


def require_installed(package: str, expected: tuple[str, str, str, str]) -> None:
    actual = record(package_command(), package)
    if actual != expected:
        raise ReconcileError(f"installed identity mismatch for {package}: {actual or 'missing'}")


def package_version_less_than(installed: str, candidate: str) -> bool:
    result = run([*package_command(), "version", "-t", installed, candidate])
    comparison = result.stdout.strip()
    if comparison not in {"<", "=", ">"}:
        raise ReconcileError(f"could not compare package versions: {installed} and {candidate}")
    return comparison == "<"


def restart_bind_service() -> None:
    run([*configctl_command(), "template", "reload", "OPNsense/Bind"])
    run([*configctl_command(), "bind", "restart"])


def validate_dry_run(output: str, requested_identities: list[str]) -> None:
    if "will be affected" not in output and "already installed" not in output:
        raise ReconcileError("dry-run package plan is not recognized")
    if "REMOVED:" in output or "DOWNGRADED:" in output:
        raise ReconcileError("dry-run package plan contains a removal or downgrade")
    names = re.findall(r"^\s*([A-Za-z0-9][A-Za-z0-9+_.-]*)(?::|-[0-9])", output, re.MULTILINE)
    for name in names:
        if name not in ALLOWED:
            raise ReconcileError(f"dry-run package plan changes unexpected package: {name}")
    for requested_identity in requested_identities:
        requested_name, requested_version = requested_identity.rsplit("-", 1)
        satisfied_by_arrow = re.search(
            rf"^\s*{re.escape(requested_name)}:\s+\S+\s+->\s+{re.escape(requested_version)}(?:\s|$)",
            output,
            re.MULTILINE,
        )
        if requested_identity not in output and satisfied_by_arrow is None:
            raise ReconcileError(f"dry-run package plan omitted {requested_identity}")


def reconcile_once() -> None:
    marker = env_path("OS_BIND_RP_PENDING_MARKER", "/var/db/os-bind-rp/repository-reconcile.pending")
    if not marker.exists():
        return
    series = opnsense_series()
    repository = env_path("OS_BIND_RP_REPOSITORY_CONFIG", "/usr/local/etc/pkg/repos/resolver-plugins.conf")
    require_managed_repository(repository, series)
    provider = shlex.split(
        os.environ.get(
            "OS_BIND_RP_PROVIDER_COMMAND",
            "/usr/local/opnsense/scripts/firmware/repos/ResolverPlugins.sh",
        )
    )
    run(provider)
    abi = pkg_config_abi()
    update = [
        *package_command(),
        "-o",
        "FETCH_TIMEOUT=15",
        "-o",
        "FETCH_RETRY=1",
        "update",
        "-f",
        "-r",
        "resolver-plugins",
    ]
    run(update)
    bind920 = require_candidate("bind920", abi, series)
    bind_tools = require_candidate("bind-tools", abi, series)
    plugin = require_candidate("os-bind-rp", abi, series)
    plugin_identity = f"os-bind-rp-{plugin[1]}"
    official = record(package_command(), "os-bind")
    if official is not None:
        raise ReconcileError("official os-bind is installed")
    bind_update_required = False
    for package, expected in (("bind920", bind920), ("bind-tools", bind_tools)):
        installed = record(package_command(), package)
        if installed is not None and installed[2] != expected[2]:
            raise ReconcileError(f"invalid installed identity for {package}")
        if installed is None or package_version_less_than(installed[1], expected[1]):
            bind_update_required = True
    requested_identities = []
    if bind_update_required:
        requested_identities.extend([f"bind920-{bind920[1]}", f"bind-tools-{bind_tools[1]}"])
    requested_identities.append(plugin_identity)
    dry_run = run(
        [
            *package_command(static=True),
            "install",
            "-n",
            "-r",
            "resolver-plugins",
            *requested_identities,
        ],
        allow_status={0, 1},
    )
    validate_dry_run(dry_run.stdout + dry_run.stderr, requested_identities)
    run([*package_command(static=True), "install", "-y", "-r", "resolver-plugins", *requested_identities])
    if bind_update_required:
        require_installed("bind920", bind920)
        require_installed("bind-tools", bind_tools)
        restart_bind_service()
    require_installed("os-bind-rp", plugin)
    marker.unlink(missing_ok=True)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--retry", action="store_true")
    parser.add_argument("--retry-attempts", type=int, default=int(os.environ.get("OS_BIND_RP_RETRY_ATTEMPTS", "3")))
    parser.add_argument("--retry-delay", type=float, default=float(os.environ.get("OS_BIND_RP_RETRY_DELAY", "0")))
    args = parser.parse_args(argv)
    attempts = max(1, args.retry_attempts if args.retry else 1)
    lock_path = env_path("OS_BIND_RP_LOCK_FILE", "/var/run/os-bind-rp-reconcile.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log("repository reconciliation lock is already held")
            return 1
        for attempt in range(1, attempts + 1):
            try:
                reconcile_once()
                return 0
            except ReconcileError as error:
                log(f"repository reconciliation failed on attempt {attempt}: {error}")
                if attempt < attempts and args.retry_delay > 0:
                    time.sleep(args.retry_delay)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
