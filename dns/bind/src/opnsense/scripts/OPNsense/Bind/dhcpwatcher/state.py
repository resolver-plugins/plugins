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

"""Persistent, mapping-aware state for DHCP records successfully in BIND."""

import json
import os
import syslog
import tempfile


class StateManager:
    """Persist only records whose last requested BIND operation succeeded."""

    def __init__(self, state_file):
        self.state_file = state_file

    @staticmethod
    def key(mapping_uuid, source, address):
        return '{}|{}|{}'.format(mapping_uuid, source, address)

    def load(self):
        if not os.path.isfile(self.state_file):
            return {}
        try:
            with open(self.state_file, 'r') as state_file:
                state = json.load(state_file)
            if not isinstance(state, dict):
                raise ValueError('state is not an object')
            # State from the original watcher was keyed only by source and IP.
            # It cannot safely identify the zone to clean up, so a service
            # reconfigure deliberately rebuilds dynamic records from leases.
            if any('|' not in key or 'mapping_uuid' not in entry for key, entry in state.items()):
                syslog.syslog(syslog.LOG_NOTICE, 'discarding legacy DHCP watcher state')
                return {}
            return state
        except (OSError, ValueError, json.JSONDecodeError) as error:
            syslog.syslog(syslog.LOG_WARNING, 'failed to read watcher state: {}'.format(error))
            return {}

    def save(self, state):
        """Atomically save state and return whether the write succeeded."""
        directory = os.path.dirname(self.state_file)
        temporary = None
        try:
            os.makedirs(directory, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix='.dhcplease_state.', dir=directory)
            with os.fdopen(fd, 'w') as state_file:
                json.dump(state, state_file, sort_keys=True)
                state_file.flush()
                os.fsync(state_file.fileno())
            os.replace(temporary, self.state_file)
            return True
        except OSError as error:
            syslog.syslog(syslog.LOG_ERR, 'failed to save watcher state: {}'.format(error))
            if temporary:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
            return False

    @classmethod
    def record(cls, mapping_uuid, mapping, lease, reverse_zone):
        return {
            'mapping_uuid': mapping_uuid,
            'address': str(lease['address']),
            'hostname': lease['hostname'],
            'suffix': mapping['hostname_suffix'],
            'ends': lease['ends'],
            'mac': lease.get('mac', ''),
            'source': lease['source'],
            'reverse_zone': reverse_zone or '',
        }

    @classmethod
    def lease_to_state(cls, lease, mapping_uuid, mapping, reverse_zone):
        """Return the persisted form of a successfully published lease."""
        return cls.record(mapping_uuid, mapping, lease, reverse_zone)
