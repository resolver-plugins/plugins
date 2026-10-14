#!/usr/bin/env python3
"""Run repository regression suites locally against the current checkout."""
import argparse
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
HA = Path("net/dhcp-interface-ha")


def run(*command):
    print(shlex.join(map(str, command)), flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


def helpers():
    # Command fixtures must execute, rather than falling through to real host tools.
    with tempfile.TemporaryDirectory(prefix="resolver-test-exec-") as directory:
        probe = Path(directory) / "probe"
        probe.write_text("#!/bin/sh\nexit 0\n")
        probe.chmod(0o700)
        try:
            subprocess.run([str(probe)], check=True, capture_output=True)
        except OSError as error:
            raise SystemExit(f"Local command fixtures need an executable temporary filesystem: {error}") from None
    run(sys.executable, "-m", "compileall", "-q", ".github/ci")
    run(sys.executable, "-m", "pytest", "-q", ".github/ci/ci-tests")


def bind():
    if not (ROOT / "dns/bind/src").is_dir():
        raise SystemExit("Materialize reviewed BIND source in a disposable checkout first; see docs/building.md.")
    run(sys.executable, "-m", "pytest", "-q", "dns/bind/tests")


def ha():
    source = HA / "src"
    run(sys.executable, "-m", "compileall", "-q", source / "opnsense/scripts/dhcp_interface_ha")
    run(sys.executable, "-m", "unittest", "discover", "-s", HA / "tests", "-v")
    for name in ("test_configure_lagg.js", "test_log.js"):
        run("node", HA / "tests/ui" / name)
    for path in sorted((ROOT / source).rglob("*")):
        if path.suffix in (".php", ".inc") and path.is_file():
            run("php", "-l", path.relative_to(ROOT))
    hooks = [HA / name for name in ("+POST_INSTALL.post", "+PRE_DEINSTALL.pre", "+POST_DEINSTALL.post")]
    for name in ("rc.d", "rc.syshook.d", "rc.carp_service_status.d"):
        hooks.extend(path.relative_to(ROOT) for path in sorted((ROOT / source / "etc" / name).rglob("*")) if path.is_file())
    for hook in hooks:
        run("sh", "-n", hook)
    run("shellcheck", "-s", "sh", "-e", "SC1091", *hooks)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("suite", nargs="?", choices=("all", "helpers", "bind", "ha"), default="helpers")
    args = parser.parse_args()
    for name, suite in (("helpers", helpers), ("bind", bind), ("ha", ha)):
        if args.suite in ("all", name):
            suite()


if __name__ == "__main__":
    main()
