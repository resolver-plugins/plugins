#!/bin/sh
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


# The watcher is meaningful only while named is running and a rendered mapping
# exists. Keeping this guard in the configd action makes GUI, CLI and boot
# service actions behave consistently.

WATCHER_CONFIG="/usr/local/etc/bind/dhcpwatcher.conf"
WATCHER="/usr/local/opnsense/scripts/OPNsense/Bind/dhcplease_watcher.py"
WATCHER_PIDFILE="/var/run/bind_dhcplease.pid"

if [ -r "$WATCHER_PIDFILE" ]; then
    watcher_pid=$(tr -cd '0-9' < "$WATCHER_PIDFILE")
    if [ -n "$watcher_pid" ] && kill -0 "$watcher_pid" 2>/dev/null; then
        exit 0
    fi
    rm -f "$WATCHER_PIDFILE"
fi

if ! /usr/local/etc/rc.d/named status >/dev/null 2>&1; then
    exit 0
fi

if [ ! -r "$WATCHER_CONFIG" ] || ! grep -q '^\[[0-9a-f-][0-9a-f-]*\]$' "$WATCHER_CONFIG"; then
    exit 0
fi

exec "$WATCHER"
