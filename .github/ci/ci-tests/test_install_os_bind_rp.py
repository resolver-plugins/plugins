"""Regression coverage for the interactive os-bind-rp installer."""

from __future__ import annotations

from common_imports import *

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
INSTALLER = REPOSITORY_ROOT / "scripts" / "install-os-bind-rp.sh"
PUBLIC_KEY_SHA256 = "bd89d6f91807c71f8a744532c9ce2f97e9590f8858ac779bfb2f23c10804e07e"
FIXTURE_ROOT = REPOSITORY_ROOT / ".github" / "ci-local"


def plugin_candidate_for_opnsense_version(opnsense_version: str) -> str:
    if " 26.7." in opnsense_version:
        return "26.7_1"
    return "26.1_1"


def write_executable(path: Path, contents: str) -> None:
    path.write_text(contents, encoding="utf-8")
    path.chmod(0o755)


def installer_environment(
    tmp_path: Path,
    *,
    opnsense_version: str = "OPNsense 26.1.11_10 (amd64)",
    pkg_abi: str = "FreeBSD:14:amd64",
    bind920: str = "bind920|9.20.26_2|dns/bind920",
    bind_tools: str = "bind-tools|9.20.26_2|dns/bind-tools",
    os_bind: str = "",
    os_bind_rp: str = "",
    os_bind_rp_candidate_version: str | None = None,
    confirmation: str | None = None,
    key_sha256: str = PUBLIC_KEY_SHA256,
    archive_checksum: str = "2$" + "a" * 64,
    install_failure: bool = False,
    plugin_install_failure: bool = False,
    fetch_layout: str = "all",
    post_install_fault: str = "",
    pkg_locked: bool = False,
    unlock_failure: bool = False,
    dry_run_status: int = 1,
    dry_run_plan: str = "valid",
) -> tuple[dict[str, str], Path, Path]:
    log = tmp_path / "commands.log"
    tty = tmp_path / "tty"
    if confirmation is not None:
        tty.write_text(f"{confirmation}\n", encoding="utf-8")
    config = tmp_path / "config.xml"
    config.write_text("<opnsense><bind/></opnsense>\n", encoding="utf-8")
    config.chmod(0o640)
    opnsense_repository = tmp_path / "OPNsense.conf"
    opnsense_repository.write_text("OPNsense: { enabled: yes }\n", encoding="utf-8")
    lock_marker = tmp_path / "pkg-locked"
    if pkg_locked:
        lock_marker.touch()

    environment = os.environ.copy()
    environment.update(
        {
            "RP_PKG_REPOSITORY_DIR": str(tmp_path / "repos"),
            "RP_PKG_KEYS_DIR": str(tmp_path / "keys"),
            "RP_TEMPORARY_DIRECTORY": str(tmp_path / "temporary"),
            "RP_TTY_PATH": str(tty),
            "RP_TEST_BIND920": bind920,
            "RP_TEST_BIND_TOOLS": bind_tools,
            "RP_TEST_OS_BIND": os_bind,
            "RP_TEST_OS_BIND_RP": os_bind_rp,
            "RP_TEST_OS_BIND_RP_CANDIDATE_VERSION": (
                os_bind_rp_candidate_version
                or plugin_candidate_for_opnsense_version(opnsense_version)
            ),
            "RP_TEST_INSTALL_FAILURE": "yes" if install_failure else "no",
            "RP_TEST_PLUGIN_INSTALL_FAILURE": "yes" if plugin_install_failure else "no",
            "RP_TEST_FETCH_LAYOUT": fetch_layout,
            "RP_TEST_POST_INSTALL_FAULT": post_install_fault,
            "RP_TEST_DRY_RUN_STATUS": str(dry_run_status),
            "RP_TEST_DRY_RUN_PLAN": dry_run_plan,
            "RP_TEST_UNLOCK_FAILURE": "yes" if unlock_failure else "no",
            "RP_TEST_ARCHIVE_CHECKSUM": archive_checksum,
            "RP_TEST_KEY_SHA256": key_sha256,
            "RP_TEST_LOG": str(log),
            "RP_TEST_OPNSENSE_VERSION": opnsense_version,
            "RP_TEST_ABI": pkg_abi,
            "RP_TEST_FALLBACK_MARKER": str(tmp_path / "fallback-installed"),
            "RP_TEST_PLUGIN_MARKER": str(tmp_path / "plugin-installed"),
            "RP_TEST_OFFICIAL_REMOVED_MARKER": str(tmp_path / "official-removed"),
            "RP_TEST_LOCK_MARKER": str(lock_marker),
            "RP_CONFIG_FILE": str(config),
            "RP_BACKUP_ROOT": str(tmp_path / "backups"),
            "RP_OPNSENSE_REPOSITORY_CONFIG": str(opnsense_repository),
        }
    )
    return environment, log, tmp_path / "repos"


def write_command_fixtures(directory: Path) -> None:
    write_executable(
        directory / "opnsense-version",
        "#!/bin/sh\nprintf '%s\\n' \"$RP_TEST_OPNSENSE_VERSION\"\n",
    )
    write_executable(
        directory / "fetch",
        "#!/bin/sh\n"
        "{ printf 'fetch'; for argument in \"$@\"; do printf ' %s' \"$argument\"; done; printf '\\n'; } >> \"$RP_TEST_LOG\"\n"
        "[ \"$1\" = -o ] || exit 64\n"
        "printf 'test public key\\n' > \"$2\"\n",
    )
    write_executable(
        directory / "sha256",
        "#!/bin/sh\n"
        "case \"$2\" in\n"
        "  */resolver-plugins.pub) printf '%s\\n' \"$RP_TEST_KEY_SHA256\";;\n"
        "  *) sha256sum \"$2\" | awk '{ print $1 }';;\n"
        "esac\n",
    )
    write_executable(
        directory / "pkg",
        r'''#!/usr/bin/env python3
import os
from pathlib import Path
import re
import sys


raw = sys.argv[1:]
with open(os.environ["RP_TEST_LOG"], "a", encoding="utf-8") as stream:
    stream.write("pkg " + " ".join(raw) + "\n")

args = list(raw)
while args[:1] == ["-o"]:
    del args[:2]
if not args:
    raise SystemExit(64)
command, args = args[0], args[1:]

candidates = {
    "bind920": ("9.20.26_2", "dns/bind920"),
    "bind-tools": ("9.20.26_2", "dns/bind-tools"),
    "os-bind-rp": (os.environ["RP_TEST_OS_BIND_RP_CANDIDATE_VERSION"], "opnsense/os-bind-rp"),
}


def marker(name):
    return Path(os.environ[name])


def version_key(value):
    return tuple(int(part) for part in re.findall(r"\d+", value))


def installed(name):
    if name == "bind920":
        if marker("RP_TEST_FALLBACK_MARKER").exists():
            return "bind920|9.20.26_2|dns/bind920"
        return os.environ.get("RP_TEST_BIND920", "")
    if name == "bind-tools":
        if marker("RP_TEST_FALLBACK_MARKER").exists():
            return "bind-tools|9.20.26_2|dns/bind-tools"
        return os.environ.get("RP_TEST_BIND_TOOLS", "")
    if name == "os-bind-rp":
        if marker("RP_TEST_PLUGIN_MARKER").exists():
            version = "0.0_1" if os.environ["RP_TEST_POST_INSTALL_FAULT"] == "identity" else os.environ["RP_TEST_OS_BIND_RP_CANDIDATE_VERSION"]
            return f"os-bind-rp|{version}|opnsense/os-bind-rp"
        return os.environ.get("RP_TEST_OS_BIND_RP", "")
    if name == "os-bind" and not marker("RP_TEST_OFFICIAL_REMOVED_MARKER").exists():
        return os.environ.get("RP_TEST_OS_BIND", "")
    if name == "pkg":
        return "pkg|2.3.1_1|ports-mgmt/pkg"
    return ""


if command == "version":
    left, right = args[-2:]
    comparison = (version_key(left) > version_key(right)) - (version_key(left) < version_key(right))
    print("<=>"[comparison + 1])
elif command == "config" and args == ["ABI"]:
    print(os.environ.get("RP_TEST_ABI", "FreeBSD:14:amd64"))
elif command == "update":
    pass
elif command == "rquery":
    expression = " ".join(args)
    for name, (version, origin) in candidates.items():
        if f"%n = {name}" in expression:
            print(f"{name}|{version}|{origin}")
elif command == "fetch":
    destination = Path(args[args.index("-o") + 1])
    if os.environ.get("RP_TEST_FETCH_LAYOUT", "all") == "all":
        destination /= "All"
    identity = args[-1]
    destination.mkdir(parents=True, exist_ok=True)
    (destination / f"{identity}.pkg").write_bytes(f"archive:{identity}\n".encode())
elif command == "repo":
    repository = Path(args[-1])
    (repository / "meta.conf").write_text("meta\n", encoding="utf-8")
    (repository / "packagesite.pkg").write_text("catalogue\n", encoding="utf-8")
elif command == "query" and "-F" in args:
    archive = Path(args[args.index("-F") + 1])
    identity = archive.name.removesuffix(".pkg")
    record = next(
        (
            f"{name}|{version}|{origin}"
            for name, (version, origin) in candidates.items()
            if identity == f"{name}-{version}"
        ),
        "",
    )
    if not record:
        record = next(
            (
                installed(name)
                for name in ("bind920", "bind-tools", "os-bind-rp", "os-bind")
                if installed(name) and identity == "-".join(installed(name).split("|")[:2])
            ),
            "",
        )
    name, version, origin = record.split("|")
    output_format = args[-1]
    if output_format == "%n|%v|%o":
        print(f"{name}|{version}|{origin}")
    elif output_format == "%Fp|%Fs":
        checksum = os.environ["RP_TEST_ARCHIVE_CHECKSUM"]
        print(f"/usr/local/{name}/one|{checksum}")
        print(f"/usr/local/{name}/two|{checksum}")
elif command == "query":
    expression = " ".join(args)
    for name in ("bind920", "bind-tools", "os-bind-rp", "os-bind", "pkg"):
        if f"%n = {name} " in f"{expression} ":
            value = installed(name)
            if value:
                if args[-1] == "%Fp|%Fs":
                    checksum = "(null)" if os.environ["RP_TEST_POST_INSTALL_FAULT"] == "checksum" else os.environ["RP_TEST_ARCHIVE_CHECKSUM"]
                    print(f"/usr/local/{name}/one|{checksum}")
                    print(f"/usr/local/{name}/two|{checksum}")
                else:
                    print(value)
elif command == "info":
    print("pkg-2.3.1_1")
    for name in ("bind920", "bind-tools", "os-bind-rp", "os-bind"):
        value = installed(name)
        if value:
            package, version, _ = value.split("|")
            print(f"{package}-{version}")
elif command == "create":
    destination = Path(args[args.index("-o") + 1])
    destination.mkdir(parents=True, exist_ok=True)
    for name in args[args.index("-o") + 2:]:
        value = installed(name)
        if value:
            package, version, _ = value.split("|")
            (destination / f"{package}-{version}.pkg").write_bytes(
                f"recovery:{package}-{version}\n".encode()
            )
elif command == "install":
    if "-n" in args:
        requested = "\n".join("\t" + arg for arg in args if re.search(r"-[0-9]", arg))
        version = os.environ["RP_TEST_OS_BIND_RP_CANDIDATE_VERSION"]
        valid = "The following package(s) will be affected:\nNew packages to be INSTALLED:\n" + requested
        current = "The most recent versions of packages are already installed"
        plans = {
            "valid": valid,
            "all_current": current,
            "repository_warning_noop": "pkg-static: Repository resolver-verified has a wrong packagesite, need to re-create database\n" + current,
            "outside_identity": f"notice: requested archive {args[-1]} was not selected\nNew packages to be INSTALLED:\n\tbind920-9.20.26_2",
            "missing": "New packages to be INSTALLED:\n\tbind920-9.20.26_2",
            "wrong_result_version": "Installed packages to be UPGRADED:\n\tos-bind-rp: 1.36_9 -> 1.36_100",
            "requested_only_on_old_side": f"Installed packages to be UPGRADED:\n\tos-bind-rp: {version} -> 26.1_2",
            "requested_removal": f"Installed packages to be REMOVED:\n\tos-bind-rp-{version}",
            "unknown_section": "Packages selected for CHANGE:\n" + requested,
            "pkg_colon": valid + "\nInstalled packages to be UPGRADED:\n\tpkg: 2.3.1_1 -> 2.4.0",
            "opnsense_colon": valid + "\nInstalled packages to be UPGRADED:\n\topnsense: 26.1.11_10 -> 26.1.12",
            "pkg_hyphen": valid + "\nNew packages to be INSTALLED:\n\tpkg-2.4.0",
            "unrelated_install": valid + "\nNew packages to be INSTALLED:\n\tpython311: 3.11.13",
            "unrelated_upgrade": valid + "\nInstalled packages to be UPGRADED:\n\tpython311: 3.11.12 -> 3.11.13",
            "unrelated_downgrade": valid + "\nInstalled packages to be DOWNGRADED:\n\tpython311: 3.11.13 -> 3.11.12",
            "unrelated_removal": valid + "\nInstalled packages to be REMOVED:\n\tpython311-3.11.13",
            "malformed_entry": valid + "\n\t???",
            "blank_continuation": valid + "\n\n\tpython311: 3.11.13",
            "empty_trailing_section": valid + "\nInstalled packages to be REMOVED:",
        }
        plan = os.environ["RP_TEST_DRY_RUN_PLAN"]
        if plan in {"outside_identity", "missing", "wrong_result_version", "requested_only_on_old_side", "requested_removal", "unknown_section"}:
            print("The following package(s) will be affected:")
        print(plans[plan])
        raise SystemExit(int(os.environ.get("RP_TEST_DRY_RUN_STATUS", "1")))
    else:
        if os.environ.get("RP_TEST_INSTALL_FAILURE") == "yes":
            raise SystemExit(1)
        identities = set(args)
        if "bind920-9.20.26_2" in identities and "bind-tools-9.20.26_2" in identities:
            marker("RP_TEST_FALLBACK_MARKER").touch()
        if f"os-bind-rp-{os.environ['RP_TEST_OS_BIND_RP_CANDIDATE_VERSION']}" in identities:
            if os.environ.get("RP_TEST_PLUGIN_INSTALL_FAILURE") == "yes":
                raise SystemExit(1)
            marker("RP_TEST_PLUGIN_MARKER").touch()
            if os.environ["RP_TEST_POST_INSTALL_FAULT"] != "official":
                marker("RP_TEST_OFFICIAL_REMOVED_MARKER").touch()
elif command == "lock":
    if "-l" in args:
        if marker("RP_TEST_LOCK_MARKER").exists():
            print("pkg-2.3.1_1")
        else:
            raise SystemExit(1)
    elif "-u" in args:
        marker("RP_TEST_LOCK_MARKER").unlink(missing_ok=True)
    elif "-y" in args:
        marker("RP_TEST_LOCK_MARKER").touch()
elif command == "unlock":
    if os.environ.get("RP_TEST_UNLOCK_FAILURE") == "yes":
        raise SystemExit(1)
    marker("RP_TEST_LOCK_MARKER").unlink(missing_ok=True)
elif command == "which":
    path = args[-1]
    name = path.split("/")[3]
    value = installed(name)
    if not value:
        raise SystemExit(1)
    package, version, _ = value.split("|")
    if os.environ["RP_TEST_POST_INSTALL_FAULT"] == "owner":
        package = "wrong-owner"
        version = "1"
    print(f"{package}-{version}")
elif command == "check":
    pass
else:
    raise SystemExit(64)
Path(os.environ["RP_TEST_LOG"]).with_name("installed-packages").write_text(
    "\n".join(filter(None, (installed(name) for name in ("bind920", "bind-tools", "os-bind-rp", "os-bind"))))
)
''',
    )
    for command in ("service", "configctl"):
        write_executable(directory / command, f'#!/bin/sh\necho "{command} $*" >> "$RP_TEST_LOG"\nexit 99\n')


def run_installer(tmp_path: Path, **kwargs: object) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    environment, log, repositories = installer_environment(tmp_path, **kwargs)
    FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=FIXTURE_ROOT) as fixture_directory:
        fixtures = Path(fixture_directory)
        write_command_fixtures(fixtures)
        environment["PATH"] = f"{fixtures}:{environment['PATH']}"
        environment["RP_PKG_STATIC_COMMAND"] = str(fixtures / "pkg")
        result = subprocess.run(
            ["/bin/sh", INSTALLER], text=True, capture_output=True, check=False, env=environment
        )
    return result, log, repositories


@pytest.mark.parametrize(
    ("opnsense_version", "pkg_abi", "series", "installed_plugin", "checksum", "layout", "dry_run_status"),
    (
        ("OPNsense 26.1.11_10 (amd64)", "FreeBSD:14:amd64", "26.1", "",
         "1$" + "b" * 64, "direct", 0),
        ("OPNsense 26.7.1_1 (amd64)", "FreeBSD:15:amd64", "26.7", "",
         "2$" + "c" * 64, "all", 1),
        ("OPNsense 26.1.11_10 (amd64)", "FreeBSD:14:amd64", "26.1", "os-bind-rp|1.36_9|opnsense/os-bind-rp",
         "2$" + "c" * 64, "all", 1),
    ),
)
def test_installs_current_plugin_for_the_detected_series_without_service_changes(
    tmp_path: Path, opnsense_version: str, pkg_abi: str, series: str, installed_plugin: str,
    checksum: str, layout: str, dry_run_status: int
) -> None:
    result, log, repositories = run_installer(
        tmp_path, opnsense_version=opnsense_version, pkg_abi=pkg_abi, os_bind_rp=installed_plugin,
        archive_checksum=checksum, fetch_layout=layout, dry_run_status=dry_run_status
    )

    assert result.returncode == 0, result.stderr
    repository = (repositories / "resolver-plugins.conf").read_text(encoding="utf-8")
    assert (
        f'url: "https://resolver-plugins.github.io/repository/pkg/${{ABI}}/{series}/latest"'
        in repository
    )
    calls = log.read_text(encoding="utf-8")
    assert (
        f"https://resolver-plugins.github.io/repository/pkg/{pkg_abi}/{series}/latest/"
        "resolver-plugins.pub" in calls
    )
    assert (
        f"https://resolver-plugins.github.io/repository/pkg/${{ABI}}/{series}/latest/"
        "resolver-plugins.pub" not in calls
    )
    assert f"os-bind-rp-{series}_1" in calls
    assert "configctl" not in calls
    assert "service" not in calls
    assert "Do you wish to update BIND?" not in result.stderr
    assert "bind920-9.20.26_2 bind-tools-9.20.26_2" not in calls
    assert (tmp_path / "plugin-installed").exists()


def test_rejects_plugin_candidate_that_does_not_match_the_detected_series(
    tmp_path: Path,
) -> None:
    result, log, _ = run_installer(
        tmp_path,
        opnsense_version="OPNsense 26.7.1_1 (amd64)",
        pkg_abi="FreeBSD:15:amd64",
        os_bind_rp_candidate_version="26.1_1",
    )

    assert result.returncode != 0
    calls = log.read_text(encoding="utf-8")
    assert " fetch " not in calls
    assert " install " not in calls


def test_rejects_26_1_before_the_required_core_floor(tmp_path: Path) -> None:
    result, log, _ = run_installer(tmp_path, opnsense_version="OPNsense 26.1.10 (amd64)")

    assert result.returncode != 0
    calls = log.read_text(encoding="utf-8")
    assert "fetch " not in calls
    assert " update " not in calls
    assert " install " not in calls


def test_preserves_a_trusted_key_when_the_replacement_fails_verification(tmp_path: Path) -> None:
    key = tmp_path / "keys" / "resolver-plugins.pub"
    key.parent.mkdir()
    key.write_text("existing trusted key\n", encoding="utf-8")

    result, log, _ = run_installer(tmp_path, key_sha256="0" * 64)

    assert result.returncode != 0
    assert key.read_text(encoding="utf-8") == "existing trusted key\n"
    calls = log.read_text(encoding="utf-8")
    assert " update " not in calls
    assert " install " not in calls


def test_prompts_for_and_installs_the_fallback_when_bind_is_ineligible(tmp_path: Path) -> None:
    result, log, repositories = run_installer(
        tmp_path,
        bind920="bind920|9.20.25|dns/bind920",
        bind_tools="bind-tools|9.20.25|dns/bind-tools",
        confirmation="y",
    )

    assert result.returncode == 0, result.stderr
    assert "Do you wish to update BIND? [y/N]" in result.stderr
    assert not (repositories / "resolver-plugins-bind920.conf").exists()
    calls = log.read_text(encoding="utf-8")
    live_installs = [line for line in calls.splitlines() if " install -y " in line]
    assert "bind920-9.20.26_2 bind-tools-9.20.26_2" in live_installs[0]
    assert "os-bind-rp-26.1_1" in live_installs[1]
    assert set((tmp_path / "installed-packages").read_text().splitlines()) == {
        "bind920|9.20.26_2|dns/bind920", "bind-tools|9.20.26_2|dns/bind-tools",
        "os-bind-rp|26.1_1|opnsense/os-bind-rp",
    }


def test_declining_bind_fallback_leaves_the_plugin_uninstalled(tmp_path: Path) -> None:
    result, log, _ = run_installer(tmp_path, confirmation="n", bind920="bind920|9.20.25|dns/bind920")
    assert result.returncode != 0
    assert " install -y " not in log.read_text()
    assert not (tmp_path / "plugin-installed").exists()
    assert not (tmp_path / "backups").exists()


def test_rejects_null_archive_checksums_before_any_package_install(tmp_path: Path) -> None:
    result, log, _ = run_installer(tmp_path, archive_checksum="(null)")

    assert result.returncode != 0
    assert " install " not in log.read_text(encoding="utf-8")
    assert not (tmp_path / "backups").exists()


def test_official_plugin_replacement_uses_verified_exact_archives_and_keeps_backup(
    tmp_path: Path,
) -> None:
    result, log, _ = run_installer(
        tmp_path,
        os_bind="os-bind|1.34_3|opnsense/os-bind",
    )

    assert result.returncode == 0, result.stderr
    backups = list((tmp_path / "backups").glob("os-bind-rp-install.*"))
    assert len(backups) == 1
    backup = backups[0]
    assert stat.S_IMODE(backup.stat().st_mode) == 0o700
    saved_config = backup / "config.xml.bak"
    assert saved_config.read_text(encoding="utf-8") == "<opnsense><bind/></opnsense>\n"
    assert stat.S_IMODE(saved_config.stat().st_mode) == 0o640

    calls = log.read_text(encoding="utf-8")
    assert calls.index(" fetch ") < calls.index(" repo ")
    assert calls.index(" install -n ") < calls.index(" install -y ")
    live_installs = [line for line in calls.splitlines() if " install -y " in line]
    assert live_installs
    assert all("REPOS_DIR=" in line and "isolated-repos" in line for line in live_installs)
    assert any("os-bind-rp-26.1_1" in line for line in live_installs)
    assert "-r resolver-plugins os-bind-rp" not in calls
    for package in ("bind-tools", "bind920", "os-bind-rp"):
        assert f"pkg query -e %n = {package} %Fp|%Fs" in calls


@pytest.mark.parametrize("fault", ["owner", "identity", "official", "checksum"])
def test_rejects_failed_post_install_verification(tmp_path: Path, fault: str) -> None:
    result, _, _ = run_installer(tmp_path, post_install_fault=fault,
                                os_bind="os-bind|1.34_3|opnsense/os-bind")
    assert result.returncode != 0
    assert (tmp_path / "plugin-installed").exists()
    backup, = (tmp_path / "backups").glob("os-bind-rp-install.*")
    assert stat.S_IMODE(backup.stat().st_mode) == 0o700
    assert (backup / "config.xml.bak").read_bytes() == b"<opnsense><bind/></opnsense>\n"
    assert stat.S_IMODE((backup / "config.xml.bak").stat().st_mode) == 0o640
    assert (backup / "recovery-packages/os-bind-1.34_3.pkg").read_bytes() == b"recovery:os-bind-1.34_3\n"
    assert str(backup) in result.stderr


def test_restores_the_original_pkg_lock_state_after_success_and_failure(tmp_path: Path) -> None:
    unlocked = tmp_path / "unlocked"
    unlocked.mkdir()
    result, _, _ = run_installer(unlocked)
    assert result.returncode == 0, result.stderr
    assert not (unlocked / "pkg-locked").exists()
    assert "pkg unlock -y pkg" in (unlocked / "commands.log").read_text(encoding="utf-8")

    locked = tmp_path / "locked"
    locked.mkdir()
    result, _, _ = run_installer(locked, pkg_locked=True, install_failure=True)
    assert result.returncode != 0
    assert (locked / "pkg-locked").exists()


def test_reports_failure_when_the_original_pkg_lock_state_cannot_be_restored(
    tmp_path: Path,
) -> None:
    result, log, _ = run_installer(tmp_path, unlock_failure=True)

    assert result.returncode != 0
    assert "could not restore the original pkg lock state" in result.stderr
    assert "Diagnostic state retained at" in result.stderr
    assert (tmp_path / "pkg-locked").exists()
    assert "pkg unlock -y pkg" in log.read_text(encoding="utf-8")


@pytest.mark.parametrize("plan", ["all_current", "repository_warning_noop"])
def test_accepts_current_plan_for_an_exact_installed_identity(tmp_path: Path, plan: str) -> None:
    result, _, _ = run_installer(
        tmp_path,
        os_bind_rp="os-bind-rp|26.1_1|opnsense/os-bind-rp",
        dry_run_status=0,
        dry_run_plan=plan,
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("status,plan,installed_plugin", [
    (2, "valid", ""),
    (1, "missing", ""),
    (1, "pkg_colon", ""),
    (1, "opnsense_colon", ""),
    (1, "pkg_hyphen", ""),
    (1, "unrelated_install", ""),
    (1, "unrelated_upgrade", ""),
    (1, "unrelated_downgrade", ""),
    (1, "unrelated_removal", ""),
    (1, "unknown_section", ""),
    (1, "malformed_entry", ""),
    (1, "blank_continuation", ""),
    (1, "empty_trailing_section", ""),
    (1, "outside_identity", ""),
    (1, "wrong_result_version", ""),
    (1, "requested_only_on_old_side", ""),
    (1, "requested_removal", ""),
    (1, "requested_only_on_old_side", "os-bind-rp|26.1_1|opnsense/os-bind-rp"),
])
def test_dry_run_rejects_untrusted_structure_or_unapproved_mutations(
    tmp_path: Path, status: int, plan: str, installed_plugin: str
) -> None:
    result, log, _ = run_installer(tmp_path, dry_run_status=status, dry_run_plan=plan,
                                 os_bind_rp=installed_plugin)
    assert result.returncode != 0
    assert " install -y " not in log.read_text()


@pytest.mark.parametrize("failed_transaction", ["bind", "plugin"])
def test_failed_transaction_preserves_original_recovery_bytes_and_instructions(
    tmp_path: Path, failed_transaction: str
) -> None:
    result, log, _ = run_installer(
        tmp_path,
        bind920="bind920|9.20.25|dns/bind920",
        bind_tools="bind-tools|9.20.25|dns/bind-tools",
        confirmation="y",
        os_bind="os-bind|1.34_3|opnsense/os-bind",
        install_failure=failed_transaction == "bind",
        plugin_install_failure=failed_transaction == "plugin",
    )
    assert result.returncode != 0
    backup, = (tmp_path / "backups").glob("os-bind-rp-install.*")
    assert stat.S_IMODE(backup.stat().st_mode) == 0o700
    assert (backup / "config.xml.bak").read_bytes() == b"<opnsense><bind/></opnsense>\n"
    assert stat.S_IMODE((backup / "config.xml.bak").stat().st_mode) == 0o640
    recovery = backup / "recovery-packages"
    assert {path.name: path.read_bytes() for path in recovery.glob("*.pkg")
            if path.name != "packagesite.pkg"} == {
        "bind-tools-9.20.25.pkg": b"recovery:bind-tools-9.20.25\n",
        "bind920-9.20.25.pkg": b"recovery:bind920-9.20.25\n",
        "os-bind-1.34_3.pkg": b"recovery:os-bind-1.34_3\n",
    }
    assert str(backup) in result.stderr
    assert str(tmp_path / "temporary") in result.stderr
    assert list((tmp_path / "temporary").rglob("*.pkg"))
    assert str(recovery) in result.stderr
    assert "install -n -f -r resolver-recovery bind-tools-9.20.25 bind920-9.20.25 os-bind-1.34_3" in result.stderr
    assert (tmp_path / "fallback-installed").exists() is (failed_transaction == "plugin")
    assert not (tmp_path / "plugin-installed").exists()
    live_installs = [line for line in log.read_text().splitlines() if " install -y " in line]
    assert "bind920-9.20.26_2 bind-tools-9.20.26_2" in live_installs[0]
    if failed_transaction == "plugin":
        assert "os-bind-rp-26.1_1" in live_installs[1]
    else:
        assert len(live_installs) == 1
