# Copyright (C) 2026 Bryan Wiegand <inbox@kw-ventures.com>
# All rights reserved.

import fcntl
import os
import pathlib
import re
import subprocess
import sys
import textwrap
import time

import pytest


BIND_ROOT = pathlib.Path(__file__).resolve().parents[1]
MAKEFILE = BIND_ROOT / "Makefile"
WORKER = BIND_ROOT / "src/opnsense/scripts/OPNsense/Bind/bindRepositoryReconcile.py"
UPGRADE_HOOK = BIND_ROOT / "src/etc/rc.syshook.d/upgrade/50-bind-rp-repository"
START_HOOK = BIND_ROOT / "src/etc/rc.syshook.d/start/50-bind-rp-repository"
PROVIDER = BIND_ROOT / "src/opnsense/scripts/firmware/repos/ResolverPlugins.sh"
PUBLIC_KEY = "/usr/local/etc/pkg/keys/resolver-plugins.pub"


def plugin_source_is_series_versioned():
    text = MAKEFILE.read_text(encoding="utf-8")
    version = re.search(r"^PLUGIN_VERSION=\s*([^\s]+)", text, re.MULTILINE)
    revision = re.search(r"^PLUGIN_REVISION=\s*([^\s]+)", text, re.MULTILINE)
    if version is None or revision is None:
        return False
    return version.group(1) in {"26.1", "26.7"} and (
        re.fullmatch(r"[1-9][0-9]*", revision.group(1)) is not None
    )


pytestmark = pytest.mark.skipif(
    not plugin_source_is_series_versioned(),
    reason="repository reconciliation contract applies to series-versioned release sources",
)


def repository_config(
    url,
    *,
    mirror_type="none",
    signature_type="pubkey",
    key=PUBLIC_KEY,
    enabled="yes",
):
    return (
        "resolver-plugins: {\n"
        f'  url: "{url}",\n'
        f'  mirror_type: "{mirror_type}",\n'
        f'  signature_type: "{signature_type}",\n'
        f'  pubkey: "{key}",\n'
        f"  enabled: {enabled}\n"
        "}\n"
    )


def series_url(series):
    return f"https://resolver-plugins.github.io/repository/pkg/${{ABI}}/{series}/latest"


def write_executable(path, contents):
    path.write_text(contents, encoding="utf-8")
    path.chmod(0o755)


def write_opnsense_version(path, series="26.7"):
    write_executable(
        path,
        "#!/bin/sh\n"
        "[ \"$1\" = -a ] || exit 64\n"
        f"printf '%s\\n' '{series}'\n",
    )


def write_provider(path):
    write_executable(path, "#!/bin/sh\nprintf 'provider %s\\n' \"$@\" >> \"$OS_BIND_RP_TEST_LOG\"\n")


def write_pkg(
    path,
    *,
    update_fails=False,
    plan="upgrade",
    candidate_plugin="26.7_1",
    installed_bind_version="9.20.26_2",
):
    plan_text = {
        "upgrade": textwrap.dedent(
            f"""\
            The following 1 package(s) will be affected (of 0 checked):

            Installed packages to be UPGRADED:
              os-bind-rp: 26.1_1 -> {candidate_plugin}
            """
        ),
        "unrelated": textwrap.dedent(
            """\
            The following 1 package(s) will be affected (of 0 checked):

            Installed packages to be UPGRADED:
              curl: 8.0 -> 8.1
            """
        ),
        "remove": textwrap.dedent(
            """\
            The following 1 package(s) will be affected (of 0 checked):

            Installed packages to be REMOVED:
              os-bind-rp: 26.1_1
            """
        ),
        "requested": None,
        "empty": "",
    }[plan]
    write_executable(
        path,
        "#!/usr/bin/env python3\n"
        "import os, pathlib, re, sys\n"
        "raw = sys.argv[1:]\n"
        "with open(os.environ['OS_BIND_RP_TEST_LOG'], 'a', encoding='utf-8') as stream:\n"
        "    stream.write('pkg ' + ' '.join(raw) + '\\n')\n"
        "args = list(raw)\n"
        "while args[:1] == ['-o']:\n"
        "    del args[:2]\n"
        "command = args[0] if args else ''\n"
        "arguments = args[1:]\n"
        "installed = pathlib.Path(os.environ['OS_BIND_RP_INSTALLED_MARKER']).exists()\n"
        "bind_updated = pathlib.Path(os.environ['OS_BIND_RP_BIND_MARKER']).exists()\n"
        f"candidate_plugin = {candidate_plugin!r}\n"
        f"installed_bind_version = {installed_bind_version!r}\n"
        f"update_fails = {update_fails!r}\n"
        f"plan_text = {plan_text!r}\n"
        "def version_key(value):\n"
        "    return tuple(int(part) for part in re.findall(r'\\d+', value))\n"
        "if command == 'config' and arguments == ['ABI']:\n"
        "    print('FreeBSD:15:amd64')\n"
        "elif command == 'update':\n"
        "    raise SystemExit(1 if update_fails else 0)\n"
        "elif command == 'rquery':\n"
        "    text = ' '.join(arguments)\n"
        "    if '%n = os-bind-rp' in text:\n"
        "        print('os-bind-rp|' + candidate_plugin + '|opnsense/os-bind-rp|FreeBSD:15:amd64')\n"
        "    elif '%n = bind920' in text:\n"
        "        print('bind920|9.20.26_2|dns/bind920|FreeBSD:15:amd64')\n"
        "    elif '%n = bind-tools' in text:\n"
        "        print('bind-tools|9.20.26_2|dns/bind-tools|FreeBSD:15:amd64')\n"
        "elif command == 'query':\n"
        "    text = ' '.join(arguments)\n"
        "    if '%n = os-bind-rp' in text:\n"
        "        print(('os-bind-rp|' + candidate_plugin if installed else 'os-bind-rp|26.1_1') + '|opnsense/os-bind-rp|FreeBSD:15:amd64')\n"
        "    elif '%n = os-bind' in text:\n"
        "        pass\n"
        "    elif '%n = bind920' in text:\n"
        "        print('bind920|' + ('9.20.26_2' if bind_updated else installed_bind_version) + '|dns/bind920|FreeBSD:15:amd64')\n"
        "    elif '%n = bind-tools' in text:\n"
        "        print('bind-tools|' + ('9.20.26_2' if bind_updated else installed_bind_version) + '|dns/bind-tools|FreeBSD:15:amd64')\n"
        "elif command == 'version':\n"
        "    left, right = arguments[-2:]\n"
        "    comparison = (version_key(left) > version_key(right)) - (version_key(left) < version_key(right))\n"
        "    print('<=>'[comparison + 1])\n"
        "elif command == 'install' and '-n' in arguments:\n"
        "    if plan_text is None:\n"
        "        print('The following package(s) will be affected:')\n"
        "        print('Installed packages to be UPGRADED:')\n"
        "        for identity in arguments:\n"
        "            if re.search(r'-[0-9]', identity):\n"
        "                print('  ' + identity)\n"
        "    elif plan_text:\n"
        "        print(plan_text)\n"
        "    raise SystemExit(1)\n"
        "elif command == 'install':\n"
        "    if 'bind920-9.20.26_2' in arguments and 'bind-tools-9.20.26_2' in arguments:\n"
        "        pathlib.Path(os.environ['OS_BIND_RP_BIND_MARKER']).touch()\n"
        "    pathlib.Path(os.environ['OS_BIND_RP_INSTALLED_MARKER']).touch()\n"
        "else:\n"
        "    raise SystemExit(64)\n",
    )


def worker_environment(
    tmp_path,
    *,
    repository_text=None,
    pkg_plan="upgrade",
    update_fails=False,
    installed_bind_version="9.20.26_2",
):
    config = tmp_path / "resolver-plugins.conf"
    if repository_text is None:
        repository_text = repository_config(series_url("26.7"))
    config.write_text(repository_text, encoding="utf-8")
    marker = tmp_path / "repository-reconcile.pending"
    marker.touch()
    log = tmp_path / "commands.log"
    bind_marker = tmp_path / "bind-updated"
    opnsense_version = tmp_path / "opnsense-version"
    provider = tmp_path / "ResolverPlugins.sh"
    pkg = tmp_path / "pkg"
    installed_marker = tmp_path / "installed"
    write_opnsense_version(opnsense_version)
    write_provider(provider)
    candidate_plugin = os.environ.get("OS_BIND_RP_TEST_CANDIDATE_PLUGIN", "26.7_1")
    write_pkg(
        pkg,
        update_fails=update_fails,
        plan=pkg_plan,
        candidate_plugin=candidate_plugin,
        installed_bind_version=installed_bind_version,
    )
    environment = os.environ | {
        "OS_BIND_RP_REPOSITORY_CONFIG": str(config),
        "OS_BIND_RP_PENDING_MARKER": str(marker),
        "OS_BIND_RP_LOG": str(tmp_path / "worker.log"),
        "OS_BIND_RP_LOCK_FILE": str(tmp_path / "reconcile.lock"),
        "OS_BIND_RP_OPNSENSE_VERSION_COMMAND": f"/bin/sh {opnsense_version}",
        "OS_BIND_RP_PROVIDER_COMMAND": f"/bin/sh {provider}",
        "OS_BIND_RP_PKG_COMMAND": f"{sys.executable} {pkg}",
        "OS_BIND_RP_PKG_STATIC_COMMAND": f"{sys.executable} {pkg}",
        "OS_BIND_RP_TEST_LOG": str(log),
        "OS_BIND_RP_INSTALLED_MARKER": str(installed_marker),
        "OS_BIND_RP_BIND_MARKER": str(bind_marker),
    }
    return environment, config, marker, log


def run_worker(tmp_path, *arguments, **kwargs):
    environment, config, marker, log = worker_environment(tmp_path, **kwargs)
    result = subprocess.run(
        [sys.executable, str(WORKER), *arguments],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=5,
    )
    return result, config, marker, log, pathlib.Path(environment["OS_BIND_RP_LOG"])


def test_worker_source_exists_and_is_executable():
    assert WORKER.is_file()
    assert os.access(WORKER, os.X_OK)


@pytest.mark.parametrize(
    "repository_text",
    (
        repository_config("https://packages.example.test/custom"),
        repository_config(series_url("26.7"), key="/usr/local/etc/pkg/keys/custom.pub"),
        repository_config(series_url("26.7"), enabled="no"),
        repository_config(series_url("26.7"), mirror_type="srv"),
        repository_config(series_url("26.7"), signature_type="none"),
    ),
)
def test_worker_refuses_unmanaged_repository_before_package_calls(tmp_path, repository_text):
    result, _, marker, log, worker_log = run_worker(tmp_path, repository_text=repository_text)

    assert result.returncode != 0
    assert marker.exists()
    assert not log.exists()
    assert "repository" in worker_log.read_text(encoding="utf-8")


def test_worker_refuses_symlink_repository_before_package_calls(tmp_path):
    environment, config, marker, log = worker_environment(tmp_path)
    target = tmp_path / "target.conf"
    target.write_text(config.read_text(encoding="utf-8"), encoding="utf-8")
    config.unlink()
    config.symlink_to(target.name)

    result = subprocess.run(
        [sys.executable, str(WORKER)],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=5,
    )

    assert result.returncode != 0
    assert marker.exists()
    assert not log.exists()


def test_worker_refreshes_only_resolver_repo_installs_plugin_and_removes_marker(tmp_path):
    result, _, marker, log, worker_log = run_worker(tmp_path)

    assert result.returncode == 0, worker_log.read_text(encoding="utf-8")
    assert not marker.exists()
    calls = log.read_text(encoding="utf-8").splitlines()
    assert any(
        call.startswith("pkg -o FETCH_TIMEOUT=15 -o FETCH_RETRY=1 update -f -r resolver-plugins")
        for call in calls
    )
    assert any("rquery -r resolver-plugins" in call and "%n = bind920" in call for call in calls)
    assert any("rquery -r resolver-plugins" in call and "%n = bind-tools" in call for call in calls)
    assert any("rquery -r resolver-plugins" in call and "%n = os-bind-rp" in call for call in calls)
    assert any("install -n -r resolver-plugins os-bind-rp-26.7_1" in call for call in calls)
    assert any("install -y -r resolver-plugins os-bind-rp-26.7_1" in call for call in calls)


def test_worker_updates_bind_pair_when_opnsense_upgrade_installed_an_older_pair(tmp_path):
    result, _, marker, log, worker_log = run_worker(
        tmp_path, installed_bind_version="9.20.24", pkg_plan="requested"
    )

    assert result.returncode == 0, worker_log.read_text(encoding="utf-8")
    assert not marker.exists()
    calls = log.read_text(encoding="utf-8").splitlines()
    assert any(
        "install -n -r resolver-plugins bind920-9.20.26_2 bind-tools-9.20.26_2 os-bind-rp-26.7_1"
        in call
        for call in calls
    )
    assert any(
        "install -y -r resolver-plugins bind920-9.20.26_2 bind-tools-9.20.26_2 os-bind-rp-26.7_1"
        in call
        for call in calls
    )


@pytest.mark.parametrize("pkg_plan", ("unrelated", "remove", "empty"))
def test_worker_rejects_unexpected_dry_run_plan_before_live_install(tmp_path, pkg_plan):
    result, _, marker, log, worker_log = run_worker(tmp_path, pkg_plan=pkg_plan)

    assert result.returncode != 0
    assert marker.exists()
    calls = log.read_text(encoding="utf-8").splitlines()
    assert any("install -n -r resolver-plugins" in call for call in calls)
    assert not any("install -y -r resolver-plugins" in call for call in calls)
    assert "dry" in worker_log.read_text(encoding="utf-8")


def test_worker_rejects_plugin_candidate_from_another_series_before_live_install(tmp_path, monkeypatch):
    monkeypatch.setenv("OS_BIND_RP_TEST_CANDIDATE_PLUGIN", "26.1_1")

    result, _, marker, log, worker_log = run_worker(tmp_path)

    assert result.returncode != 0
    assert marker.exists()
    calls = log.read_text(encoding="utf-8").splitlines()
    assert any("rquery -r resolver-plugins" in call and "%n = os-bind-rp" in call for call in calls)
    assert not any("install -y -r resolver-plugins" in call for call in calls)
    assert "series" in worker_log.read_text(encoding="utf-8")


def test_worker_retry_mode_has_bounded_attempt_count(tmp_path):
    result, _, marker, log, _ = run_worker(
        tmp_path, "--retry", update_fails=True
    )

    assert result.returncode != 0
    assert marker.exists()
    calls = log.read_text(encoding="utf-8").splitlines()
    assert sum(" update -f -r resolver-plugins" in call for call in calls) == 3


def test_worker_lock_contention_returns_promptly_without_package_calls(tmp_path):
    environment, _, marker, log = worker_environment(tmp_path)
    lock_file = pathlib.Path(environment["OS_BIND_RP_LOCK_FILE"])
    ready = tmp_path / "lock-ready"
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import fcntl, pathlib, sys, time; "
                "lock = open(sys.argv[1], 'w'); "
                "fcntl.flock(lock, fcntl.LOCK_EX); "
                "pathlib.Path(sys.argv[2]).touch(); "
                "time.sleep(5)"
            ),
            str(lock_file),
            str(ready),
        ]
    )
    try:
        for _ in range(50):
            if ready.exists():
                break
            time.sleep(0.02)
        assert ready.exists()
        start = time.monotonic()
        result = subprocess.run(
            [sys.executable, str(WORKER)],
            env=environment,
            text=True,
            capture_output=True,
            check=False,
            timeout=1,
        )
        elapsed = time.monotonic() - start
    finally:
        holder.terminate()
        holder.wait(timeout=2)

    assert result.returncode != 0
    assert elapsed < 1
    assert marker.exists()
    assert not log.exists()
    assert "lock" in pathlib.Path(environment["OS_BIND_RP_LOG"]).read_text(encoding="utf-8")


def test_upgrade_hook_marks_pending_and_never_runs_worker_inline(tmp_path):
    marker = tmp_path / "pending"
    worker_log = tmp_path / "worker-inline"
    worker = tmp_path / "worker"
    write_executable(worker, "#!/bin/sh\nprintf inline > \"$1\"\nsleep 5\n")

    start = time.monotonic()
    result = subprocess.run(
        ["/bin/sh", str(UPGRADE_HOOK)],
        env=os.environ
        | {
            "OS_BIND_RP_PENDING_MARKER": str(marker),
            "OS_BIND_RP_WORKER_COMMAND": f"{worker} {worker_log}",
        },
        text=True,
        capture_output=True,
        check=False,
        timeout=1,
    )
    elapsed = time.monotonic() - start

    assert result.returncode == 0
    assert elapsed < 1
    assert marker.exists()
    assert not worker_log.exists()


def test_start_hook_without_marker_does_not_launch_daemon(tmp_path):
    daemon_log = tmp_path / "daemon.log"
    daemon = tmp_path / "daemon"
    write_executable(daemon, "#!/bin/sh\nprintf daemon >> \"$OS_BIND_RP_DAEMON_LOG\"\n")

    result = subprocess.run(
        ["/bin/sh", str(START_HOOK)],
        env=os.environ
        | {
            "OS_BIND_RP_PENDING_MARKER": str(tmp_path / "missing"),
            "OS_BIND_RP_DAEMON_COMMAND": f"/bin/sh {daemon}",
            "OS_BIND_RP_DAEMON_LOG": str(daemon_log),
        },
        text=True,
        capture_output=True,
        check=False,
        timeout=1,
    )

    assert result.returncode == 0
    assert not daemon_log.exists()


def test_start_hook_launches_daemon_asynchronously_and_returns_zero(tmp_path):
    marker = tmp_path / "pending"
    marker.touch()
    daemon_log = tmp_path / "daemon.log"
    inline_worker_log = tmp_path / "inline-worker.log"
    daemon = tmp_path / "daemon"
    worker = tmp_path / "worker"
    write_executable(
        daemon,
        "#!/bin/sh\n"
        "printf '%s\\n' \"$*\" >> \"$OS_BIND_RP_DAEMON_LOG\"\n"
        "sleep 5\n",
    )
    write_executable(worker, "#!/bin/sh\nprintf inline >> \"$OS_BIND_RP_INLINE_WORKER_LOG\"\n")

    start = time.monotonic()
    result = subprocess.run(
        ["/bin/sh", str(START_HOOK)],
        env=os.environ
        | {
            "OS_BIND_RP_PENDING_MARKER": str(marker),
            "OS_BIND_RP_DAEMON_COMMAND": f"/bin/sh {daemon}",
            "OS_BIND_RP_WORKER_COMMAND": f"/bin/sh {worker}",
            "OS_BIND_RP_INLINE_WORKER_LOG": str(inline_worker_log),
            "OS_BIND_RP_DAEMON_LOG": str(daemon_log),
            "OS_BIND_RP_RETRY_ATTEMPTS": "3",
        },
        text=True,
        capture_output=True,
        check=False,
        timeout=1,
    )
    elapsed = time.monotonic() - start

    assert result.returncode == 0
    assert elapsed < 1
    for _ in range(50):
        if daemon_log.exists():
            break
        time.sleep(0.02)
    assert daemon_log.read_text(encoding="utf-8")
    assert "--retry" in daemon_log.read_text(encoding="utf-8")
    assert "--retry-attempts 3" in daemon_log.read_text(encoding="utf-8")
    assert not inline_worker_log.exists()
