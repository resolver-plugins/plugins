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


"""Stop BIND and remove dynamic-update journals before zone regeneration."""

import os
import re
import signal
import subprocess
import syslog
import time
import xml.etree.ElementTree as ElementTree
from pathlib import Path


ENV = os.environ
WATCHER_PIDFILE = Path(ENV.get("BIND_STOP_WATCHER_PIDFILE", "/var/run/bind_dhcplease.pid"))
WATCHER_CONFIG = Path(ENV.get("BIND_STOP_WATCHER_CONFIG", "/usr/local/etc/bind/dhcpwatcher.conf"))
ZONE_DIR = Path(ENV.get("BIND_STOP_ZONE_DIR", "/usr/local/etc/namedb/primary"))
STATE_FILE = Path(ENV.get("BIND_STOP_STATE_FILE", "/var/cache/bind/dhcplease_state.json"))
CONFIG = Path(ENV.get("BIND_STOP_CONFIG", "/conf/config.xml"))
NAMED_RC = ENV.get("BIND_STOP_NAMED_RC", "/usr/local/etc/rc.d/named")
NAMED_PIDFILE = Path(ENV.get("BIND_STOP_NAMED_PIDFILE", "/var/run/named/pid"))
RNDC = ENV.get("BIND_STOP_RNDC", "/usr/local/sbin/rndc")
GRACE_TIMEOUT = float(ENV.get("BIND_STOP_GRACE_TIMEOUT", "30"))
FORCE_TIMEOUT = float(ENV.get("BIND_STOP_FORCE_TIMEOUT", "5"))
ZONE_NAME = re.compile(r"[A-Za-z0-9.-]+$")


def log(priority, message):
    syslog.openlog("named")
    syslog.syslog(priority, message)


def stop_watcher():
    try:
        pid = int(WATCHER_PIDFILE.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        pid = None
    if pid:
        try:
            os.kill(pid, 0)
        except OSError:
            pass
        else:
            os.kill(pid, signal.SIGTERM)
            for _ in range(50):
                try:
                    os.kill(pid, 0)
                except OSError:
                    break
                time.sleep(0.1)
            else:
                log(
                    syslog.LOG_WARNING,
                    "DHCP watcher did not stop cleanly; terminating it before regenerating zones",
                )
                os.kill(pid, signal.SIGKILL)
    try:
        WATCHER_PIDFILE.unlink()
    except FileNotFoundError:
        pass


def bind_config(root):
    if root.tag.lower() == "opnsense":
        node = root.find("./bind")
        if node is not None:
            return node
    for path in ("./OPNsense/bind", "./opnsense/bind", ".//OPNsense/bind", ".//opnsense/bind"):
        node = root.find(path)
        if node is not None:
            return node
    return None


def model_zones():
    try:
        root = ElementTree.parse(CONFIG).getroot()
    except (OSError, ElementTree.ParseError):
        return set()
    plugin = bind_config(root)
    if plugin is None:
        return set()

    domains = {}
    zones = set()
    for domain in plugin.findall("./domain/domains/domain"):
        name = (domain.findtext("domainname") or "").strip()
        uuid = domain.attrib.get("uuid")
        if uuid:
            domains[uuid] = name
        if (domain.findtext("type") or "").strip() == "reverse":
            zones.add(name)
    for mapping in plugin.findall("./watcher/mappings/mapping"):
        name = domains.get((mapping.findtext("hostname_suffix") or "").strip())
        if name:
            zones.add(name)
    return zones


def dynamic_model_zones(strict=False):
    try:
        root = ElementTree.parse(CONFIG).getroot()
    except (OSError, ElementTree.ParseError):
        if strict:
            raise
        return set()
    plugin = bind_config(root)
    if plugin is None:
        return set()

    domains = {}
    zones = set()
    for domain in plugin.findall("./domain/domains/domain"):
        enabled = (domain.findtext("enabled") or "1").strip()
        name = (domain.findtext("domainname") or "").strip()
        zone_type = (domain.findtext("type") or "primary").strip()
        allow_update = (domain.findtext("allowrndcupdate") or "1").strip()
        uuid = domain.attrib.get("uuid")
        if uuid:
            domains[uuid] = (name, enabled, zone_type)
        if enabled == "1" and zone_type in {"primary", "reverse"} and allow_update == "1":
            zones.add(name)

    enabled_keys = {
        key.attrib.get("uuid")
        for key in plugin.findall("./tsig/keys/key")
        if (key.findtext("enabled") or "1").strip() == "1"
    }
    reverse_zones = {
        name for name, enabled, zone_type in domains.values()
        if enabled == "1" and zone_type == "reverse"
    }
    for mapping in plugin.findall("./watcher/mappings/mapping"):
        if (mapping.findtext("enabled") or "1").strip() != "1":
            continue
        if (mapping.findtext("tsigkey") or "").strip() not in enabled_keys:
            continue
        forward = domains.get((mapping.findtext("hostname_suffix") or "").strip())
        if forward and forward[1:] == ("1", "primary"):
            zones.add(forward[0])
        reverse_uuid = (mapping.findtext("reverse_zone") or "").strip()
        if reverse_uuid:
            reverse = domains.get(reverse_uuid)
            if reverse and reverse[1:] == ("1", "reverse"):
                zones.add(reverse[0])
        else:
            zones.update(reverse_zones)

    if strict and any(not valid_zone(zone) for zone in zones):
        raise ValueError("invalid dynamic zone name in configuration")
    return set(filter(valid_zone, zones))


def watcher_zones():
    try:
        lines = WATCHER_CONFIG.read_text(encoding="utf-8").splitlines()
    except OSError:
        return set()
    return {
        match.group(1).strip()
        for line in lines
        if (match := re.match(r"^\s*hostname_suffix\s*=\s*(.*)$", line))
    }


def valid_zone(name):
    return bool(name) and not name.startswith(".") and bool(ZONE_NAME.fullmatch(name))


def clear_journals(zones):
    for zone in sorted(filter(valid_zone, zones)):
        zone_path = ZONE_DIR / f"{zone}.db"
        journals = [zone_path.with_name(f"{zone_path.name}{suffix}") for suffix in (".jnl", ".jnw", ".jbk")]
        if any(path.exists() for path in journals):
            log(
                syslog.LOG_NOTICE,
                f"clearing dynamic update journal for {zone} before regenerating its zone file",
            )
            for path in journals:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass


def run(command, **kwargs):
    try:
        return subprocess.run(command, check=False, timeout=FORCE_TIMEOUT, **kwargs)
    except subprocess.TimeoutExpired:
        log(syslog.LOG_ERR, f"command timed out after {FORCE_TIMEOUT:g} seconds: {command[0]}")
    except OSError as error:
        log(syslog.LOG_ERR, f"unable to run {command[0]}: {error}")
    return None


def named_status():
    result = run(
        [NAMED_RC, "status"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return None if result is None else result.returncode


def named_pid():
    try:
        pid = int(NAMED_PIDFILE.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    return pid if pid > 1 else None


def process_exists(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def wait_for_exit(pid, timeout):
    deadline = time.monotonic() + timeout
    while process_exists(pid):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(0.1, remaining))
    return True


def signal_named(pid, sig):
    if not process_exists(pid):
        return True
    if named_pid() != pid or named_status() != 0:
        log(syslog.LOG_ERR, f"refusing to signal unverified named pid {pid}")
        return False
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        return True
    except OSError as error:
        log(syslog.LOG_ERR, f"unable to signal named pid {pid}: {error}")
        return False
    return True


def named_target():
    status = named_status()
    if status == 1:
        return status, None
    if status != 0:
        log(syslog.LOG_ERR, f"unable to verify named status (exit {status})")
        return None

    pid = named_pid()
    if pid is None:
        log(syslog.LOG_ERR, f"unable to read a valid named pid from {NAMED_PIDFILE}")
        return None
    return status, pid


def stop_named():
    target = named_target()
    if target is None:
        return False
    status, pid = target
    if status == 1:
        log(syslog.LOG_NOTICE, "named was already stopped")
        return True

    log(syslog.LOG_NOTICE, f"requesting graceful shutdown of named pid {pid}")
    result = run([RNDC, "stop"])
    if result is not None and result.returncode == 0:
        if wait_for_exit(pid, GRACE_TIMEOUT):
            log(syslog.LOG_NOTICE, f"named pid {pid} stopped gracefully")
            return True
    else:
        exit_status = "timeout" if result is None else f"exit {result.returncode}"
        log(syslog.LOG_WARNING, f"rndc stop failed ({exit_status}); escalating shutdown")

    log(syslog.LOG_ERR, f"named pid {pid} did not stop gracefully; sending SIGTERM")
    if not signal_named(pid, signal.SIGTERM):
        return False
    if wait_for_exit(pid, FORCE_TIMEOUT):
        log(syslog.LOG_WARNING, f"named pid {pid} stopped after SIGTERM")
        return True

    log(syslog.LOG_CRIT, f"named pid {pid} ignored SIGTERM; sending SIGKILL")
    if not signal_named(pid, signal.SIGKILL):
        return False
    if wait_for_exit(pid, FORCE_TIMEOUT):
        log(syslog.LOG_CRIT, f"named pid {pid} required SIGKILL to stop")
        return True

    log(
        syslog.LOG_ERR,
        f"named pid {pid} is still present after SIGKILL",
    )
    return False


def main():
    if not stop_named():
        return 1
    stop_watcher()
    clear_journals(watcher_zones() | model_zones())
    try:
        STATE_FILE.unlink()
    except FileNotFoundError:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
