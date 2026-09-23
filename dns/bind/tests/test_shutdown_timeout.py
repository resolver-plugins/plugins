# Copyright (C) 2026 Bryan Wiegand <inbox@kw-ventures.com>
# All rights reserved.

import os
import pathlib
import signal
import subprocess
import sys
import tempfile
import time

import pytest

from .bounded_shutdown_contract import current_release_requires_bounded_shutdown


BIND_ROOT = pathlib.Path(__file__).resolve().parents[1]
STOP_SCRIPT = BIND_ROOT / "src/opnsense/scripts/OPNsense/Bind/bindStop.py"

if not current_release_requires_bounded_shutdown(BIND_ROOT):
    pytestmark = pytest.mark.skip(reason="release predates bounded BIND shutdown")


@pytest.fixture
def executable_tmp_path():
    with tempfile.TemporaryDirectory(dir=BIND_ROOT) as directory:
        yield pathlib.Path(directory)


def write_executable(path, source):
    path.write_text(source)
    path.chmod(0o755)


def start_process(tmp_path, name, *, events=None, ignore_term=False):
    pidfile = tmp_path / f"{name}.pid"
    events = events or tmp_path / "events"
    if not events.exists():
        events.write_text("")
    supervisor = tmp_path / f"{name}-supervisor.py"
    supervisor.write_text(
        """import os
import pathlib
import signal
import sys

pidfile = pathlib.Path(sys.argv[1])
events = pathlib.Path(sys.argv[2])
name = sys.argv[3]
ignore_term = sys.argv[4] == "yes"
pid = os.fork()
if pid:
    _, status = os.waitpid(pid, 0)
    raise SystemExit(os.waitstatus_to_exitcode(status))

def stop(_signum, _frame):
    with events.open("a") as output:
        output.write(f"{name}-term\\n")
    raise SystemExit(0)

signal.signal(signal.SIGTERM, signal.SIG_IGN if ignore_term else stop)
pidfile.write_text(str(os.getpid()))
while True:
    signal.pause()
"""
    )
    process = subprocess.Popen(
        [sys.executable, supervisor, pidfile, events, name, "yes" if ignore_term else "no"]
    )
    for _ in range(100):
        if pidfile.exists():
            return process, int(pidfile.read_text()), pidfile, events
        time.sleep(0.01)
    process.kill()
    raise AssertionError("test named process did not start")


def stop_process(supervisor, child_pid, sig=signal.SIGKILL):
    if supervisor.poll() is not None:
        return
    try:
        os.kill(child_pid, sig)
    except ProcessLookupError:
        pass
    try:
        supervisor.wait(timeout=2)
    except subprocess.TimeoutExpired:
        supervisor.kill()
        supervisor.wait()


def run_stop(
    tmp_path,
    pidfile,
    events,
    *,
    watcher_pidfile=None,
    named_rc_source=None,
    rndc_source=None,
    extra_env=None,
):
    named_rc = tmp_path / "named-rc"
    write_executable(
        named_rc,
        named_rc_source or """#!/bin/sh
if [ "$1" = status ]; then
    kill -0 "$(cat "$TEST_PIDFILE")" 2>/dev/null
    exit $?
fi
printf 'rc-stop\\n' >> "$TEST_EVENTS"
exit 0
""",
    )
    rndc = tmp_path / "rndc"
    write_executable(
        rndc,
        rndc_source or "#!/bin/sh\nprintf 'rndc\\n' >> \"$TEST_EVENTS\"\n",
    )
    config = tmp_path / "config.xml"
    config.write_text("<opnsense><bind/></opnsense>")

    environment = os.environ | {
        "BIND_STOP_CONFIG": str(config),
        "BIND_STOP_FORCE_TIMEOUT": "0.2",
        "BIND_STOP_GRACE_TIMEOUT": "0.2",
        "BIND_STOP_NAMED_PIDFILE": str(pidfile),
        "BIND_STOP_NAMED_RC": str(named_rc),
        "BIND_STOP_RNDC": str(rndc),
        "BIND_STOP_STATE_FILE": str(tmp_path / "state.json"),
        "BIND_STOP_WATCHER_CONFIG": str(tmp_path / "watcher.conf"),
        "BIND_STOP_WATCHER_PIDFILE": str(watcher_pidfile or tmp_path / "watcher.pid"),
        "BIND_STOP_ZONE_DIR": str(tmp_path),
        "TEST_EVENTS": str(events),
        "TEST_PIDFILE": str(pidfile),
    }
    environment.update(extra_env or {})
    return subprocess.run(
        [sys.executable, STOP_SCRIPT],
        env=environment,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )


def test_graceful_timeout_escalates_to_term(executable_tmp_path):
    process, child_pid, pidfile, events = start_process(executable_tmp_path, "named")
    try:
        result = run_stop(executable_tmp_path, pidfile, events)

        assert result.returncode == 0, result.stderr
        process.wait(timeout=2)
        assert events.read_text().splitlines() == ["rndc", "named-term"]
    finally:
        stop_process(process, child_pid)


def test_term_timeout_escalates_to_kill(executable_tmp_path):
    process, child_pid, pidfile, events = start_process(
        executable_tmp_path, "named", ignore_term=True
    )
    try:
        result = run_stop(executable_tmp_path, pidfile, events)

        assert result.returncode == 0, result.stderr
        process.wait(timeout=2)
        assert events.read_text().splitlines() == ["rndc"]
    finally:
        stop_process(process, child_pid)


def test_rndc_timeout_is_bounded_and_escalates(executable_tmp_path):
    process, child_pid, pidfile, events = start_process(executable_tmp_path, "named")
    try:
        started = time.monotonic()
        result = run_stop(
            executable_tmp_path,
            pidfile,
            events,
            rndc_source=(
                "#!/bin/sh\n"
                "printf 'rndc\\n' >> \"$TEST_EVENTS\"\n"
                "while :; do :; done\n"
            ),
        )

        assert result.returncode == 0, result.stderr
        assert time.monotonic() - started < 2
        process.wait(timeout=2)
        assert events.read_text().splitlines() == ["rndc", "named-term"]
    finally:
        stop_process(process, child_pid)


def test_pid_change_prevents_signaling_replacement_process(executable_tmp_path):
    named, named_pid, pidfile, events = start_process(executable_tmp_path, "named")
    replacement, replacement_pid, replacement_pidfile, _ = start_process(
        executable_tmp_path,
        "replacement",
        events=events,
    )
    try:
        result = run_stop(
            executable_tmp_path,
            pidfile,
            events,
            rndc_source=(
                "#!/bin/sh\n"
                "printf 'rndc\\n' >> \"$TEST_EVENTS\"\n"
                "cp \"$TEST_REPLACEMENT_PIDFILE\" \"$TEST_PIDFILE\"\n"
            ),
            extra_env={"TEST_REPLACEMENT_PIDFILE": str(replacement_pidfile)},
        )

        assert result.returncode == 1
        assert named.poll() is None
        assert replacement.poll() is None
        assert events.read_text().splitlines() == ["rndc"]
    finally:
        stop_process(named, named_pid, signal.SIGTERM)
        stop_process(replacement, replacement_pid, signal.SIGTERM)


def test_watcher_stops_after_named_shutdown(executable_tmp_path):
    named, named_pid, pidfile, events = start_process(executable_tmp_path, "named")
    watcher, watcher_pid, watcher_pidfile, _ = start_process(
        executable_tmp_path, "watcher", events=events
    )
    try:
        result = run_stop(
            executable_tmp_path,
            pidfile,
            events,
            watcher_pidfile=watcher_pidfile,
        )

        assert result.returncode == 0, result.stderr
        named.wait(timeout=2)
        watcher.wait(timeout=2)
        assert events.read_text().splitlines() == [
            "rndc",
            "named-term",
            "watcher-term",
        ]
    finally:
        stop_process(named, named_pid)
        stop_process(watcher, watcher_pid)


def test_preflight_failure_leaves_watcher_running(executable_tmp_path):
    named, named_pid, pidfile, events = start_process(executable_tmp_path, "named")
    watcher, watcher_pid, watcher_pidfile, _ = start_process(
        executable_tmp_path, "watcher", events=events
    )
    try:
        result = run_stop(
            executable_tmp_path,
            pidfile,
            events,
            watcher_pidfile=watcher_pidfile,
            named_rc_source="#!/bin/sh\nexit 2\n",
        )

        assert result.returncode == 1
        assert named.poll() is None
        assert watcher.poll() is None
        assert events.read_text() == ""
    finally:
        stop_process(named, named_pid, signal.SIGTERM)
        stop_process(watcher, watcher_pid, signal.SIGTERM)
