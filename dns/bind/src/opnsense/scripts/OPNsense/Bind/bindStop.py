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

import configparser
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
NAMED_PGREP = ENV.get("BIND_STOP_PGREP", "/usr/bin/pgrep")
NAMED_PKILL = ENV.get("BIND_STOP_PKILL", "/usr/bin/pkill")
NAMED_STOP_ATTEMPTS = int(ENV.get("BIND_STOP_NAMED_STOP_ATTEMPTS", "50"))
NAMED_STOP_INTERVAL = float(ENV.get("BIND_STOP_NAMED_STOP_INTERVAL", "0.1"))
ZONE_NAME = re.compile(r"[A-Za-z0-9.-]+$")


def log(priority, message):
    syslog.openlog("bind")
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
        return root.find("./bind")
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


def watcher_zones():
    try:
        config = configparser.ConfigParser(interpolation=None, delimiters=("=",))
        with WATCHER_CONFIG.open(encoding="utf-8") as config_file:
            config.read_file(config_file)
    except (OSError, configparser.Error):
        return set()

    zones = set()
    for section in config.sections():
        if config.has_option(section, "hostname_suffix"):
            zones.add(config.get(section, "hostname_suffix").strip())
    if config.has_section("reverse-zones"):
        zones.update(value.strip() for value in config["reverse-zones"].values())
    return zones


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


def named_running():
    try:
        result = subprocess.run(
            [NAMED_PGREP, "-x", "named"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError as error:
        log(syslog.LOG_ERR, f"unable to check named status: {error}")
        return None
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    log(syslog.LOG_ERR, f"unable to check named status: pgrep exited {result.returncode}")
    return None


def wait_for_named_exit():
    for _ in range(max(NAMED_STOP_ATTEMPTS, 1)):
        running = named_running()
        if running is False:
            return True
        if running is None:
            return False
        time.sleep(NAMED_STOP_INTERVAL)
    return named_running() is False


def stop_named():
    try:
        result = subprocess.run([NAMED_RC, "stop"], check=False)
    except OSError as error:
        log(syslog.LOG_ERR, f"unable to stop named: {error}")
        return False
    if wait_for_named_exit():
        if result.returncode:
            log(syslog.LOG_WARNING, f"named stop exited {result.returncode}, but named stopped")
        return True

    log(syslog.LOG_WARNING, "named did not stop cleanly; terminating it before regenerating zones")
    try:
        result = subprocess.run([NAMED_PKILL, "-KILL", "-x", "named"], check=False)
    except OSError as error:
        log(syslog.LOG_ERR, f"unable to terminate named: {error}")
        return False
    if result.returncode not in (0, 1):
        log(syslog.LOG_ERR, f"named termination failed with exit status {result.returncode}")
        return False
    if wait_for_named_exit():
        return True

    log(syslog.LOG_ERR, "named is still running; preserving journals and watcher state")
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
