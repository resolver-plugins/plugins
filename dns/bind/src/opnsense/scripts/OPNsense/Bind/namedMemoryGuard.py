#!/usr/local/bin/python3
# Copyright (C) 2026 Bryan Wiegand <inbox@kw-ventures.com>
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE AUTHOR AND CONTRIBUTORS ``AS IS AND ANY
# EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE AUTHOR OR CONTRIBUTORS BE LIABLE FOR ANY
# DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
# (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
# ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.


"""Protect firewall memory while BIND loads a DNSBL response-policy zone."""

import os
import re
import subprocess
import sys


ENV = os.environ
DNSBL_FILE = ENV.get("NAMED_GUARD_DNSBL_FILE", "/usr/local/etc/namedb/dnsbl.inc")
RC_CONF = ENV.get("NAMED_GUARD_RC_CONF", "/etc/rc.conf.d/named")
DEFAULT_MIN_FREE_MB = ENV.get("NAMED_GUARD_DEFAULT_MIN_FREE_MB", "300")
TIMEOUT_SECONDS = ENV.get("NAMED_GUARD_TIMEOUT_SECONDS", "90")
SAMPLE_SECONDS = ENV.get("NAMED_GUARD_SAMPLE_SECONDS", "0.1")
GETCONF = ENV.get("NAMED_GUARD_GETCONF", "getconf")
LOGGER = ENV.get("NAMED_GUARD_LOGGER", "logger")
PGREP = ENV.get("NAMED_GUARD_PGREP", "pgrep")
PS = ENV.get("NAMED_GUARD_PS", "ps")
RECOVER = ENV.get(
    "NAMED_GUARD_RECOVER",
    "/usr/local/opnsense/scripts/OPNsense/Bind/dnsblMemoryRecovery.sh",
)
SLEEP = ENV.get("NAMED_GUARD_SLEEP", "sleep")
SYSCTL = ENV.get("NAMED_GUARD_SYSCTL", "sysctl")
KILL = ENV.get("NAMED_GUARD_KILL", "/bin/kill")
STATUS = ENV.get(
    "NAMED_GUARD_STATUS",
    "/usr/local/opnsense/scripts/OPNsense/Bind/dnsblStatus.py",
)


def command_output(*command):
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except OSError:
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def command(*args):
    try:
        return subprocess.run(args, check=False).returncode
    except OSError:
        return 1


def read_rc_conf():
    try:
        with open(RC_CONF, encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return ""


def dnsbl_enabled():
    return ENV.get("NAMED_GUARD_ENABLED") == "1" or bool(
        re.search(r'^named_dnsbl="[^"]', read_rc_conf(), re.MULTILINE)
    )


def minimum_free_kb():
    configured = ENV.get("NAMED_GUARD_MIN_FREE_KB")
    if configured is not None:
        return int(configured) if configured.isdigit() else None

    match = re.search(r'^named_memory_guard_mb="([0-9]+)"$', read_rc_conf(), re.MULTILINE)
    memory_guard_mb = match.group(1) if match else DEFAULT_MIN_FREE_MB
    return int(memory_guard_mb) * 1024 if memory_guard_mb.isdigit() else None


def stop_named(pid, free_kb, minimum_kb):
    rss_kb = command_output(PS, "-o", "rss=", "-p", pid) or "unknown"
    command(
        STATUS,
        "guard_recovered",
        "Memory Guard stopped DNSBL/RPZ loading and is restarting BIND without DNSBL.",
    )
    command(
        LOGGER,
        "-p",
        "daemon.crit",
        "-t",
        "named",
        "DNSBL startup memory guard stopped named "
        f"(pid {pid}): {free_kb} KiB free, below the {minimum_kb} KiB minimum; "
        f"RSS {rss_kb} KiB.",
    )
    command(KILL, "-TERM", pid)
    command(SLEEP, "1")
    if command_output(PGREP, "-o", "named") is not None:
        command(KILL, "-KILL", pid)
    command(RECOVER, pid)


def main():
    if not os.path.isfile(DNSBL_FILE) or os.path.getsize(DNSBL_FILE) == 0 or not dnsbl_enabled():
        command(STATUS, "disabled", "DNSBL/RPZ is disabled.")
        return 0

    minimum_kb = minimum_free_kb()
    if minimum_kb is None or minimum_kb == 0:
        return 0

    try:
        page_size = int(command_output(GETCONF, "PAGESIZE") or "")
        samples = int(float(TIMEOUT_SECONDS) * 10)
    except ValueError:
        return 0

    expected_pid = sys.argv[1] if len(sys.argv) > 1 else ""
    minimum_pages = minimum_kb * 1024 // page_size
    for _ in range(samples):
        pid = command_output(PGREP, "-o", "named")
        if not pid:
            command(SLEEP, SAMPLE_SECONDS)
            continue
        if expected_pid and pid != expected_pid:
            return 0

        try:
            free_pages = int(command_output(SYSCTL, "-n", "vm.stats.vm.v_free_count") or "")
        except ValueError:
            break
        if free_pages < minimum_pages:
            stop_named(pid, free_pages * page_size // 1024, minimum_kb)
            return 1
        command(SLEEP, SAMPLE_SECONDS)

    command(STATUS, "dnsbl_active", "BIND loaded DNSBL/RPZ successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
