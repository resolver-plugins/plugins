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

"""Lease validation and DNS command construction."""

import ipaddress
import re
import syslog
import time


HOSTNAME_PATTERN = re.compile(r"(?!-)[A-Z0-9-]*(?<!-)$", re.IGNORECASE)


def is_valid_hostname(hostname):
    return bool(hostname) and all(
        part and HOSTNAME_PATTERN.match(part)
        for part in hostname.split('.')
    )


def normalize_isc_lease(lease, source='isc-dhcp'):
    if 'ends' not in lease or lease['ends'] <= time.time():
        return None
    if lease.get('binding') in ('free', 'abandoned', 'backup'):
        return None
    if 'address' not in lease:
        return None
    try:
        address = ipaddress.ip_address(lease['address'])
    except ValueError:
        return None
    hostname = lease.get('client-hostname', '')
    if not hostname:
        return None
    hostname = hostname.rstrip('.')
    if not is_valid_hostname(hostname):
        syslog.syslog(
            syslog.LOG_WARNING,
            'dhcpd lease: {} is not a valid hostname, ignoring'.format(hostname),
        )
        return None
    return {
        'address': address, 'hostname': hostname,
        'mac': lease.get('hardware', {}).get('mac-address', '').lower(),
        'ends': lease['ends'], 'source': source,
    }


def normalize_kea_lease(lease, source):
    if lease.get('type') == 'IA_PD':
        return None
    if 'ip-address' not in lease:
        return None
    try:
        address = ipaddress.ip_address(lease['ip-address'])
    except ValueError:
        return None
    hostname = lease.get('hostname', '').rstrip('.')
    if not hostname:
        return None
    if not is_valid_hostname(hostname):
        syslog.syslog(
            syslog.LOG_WARNING,
            'kea lease: {} is not a valid hostname, ignoring'.format(hostname),
        )
        return None
    ends = lease.get('cltt', 0) + lease.get('valid-lft', 0)
    if ends <= time.time():
        return None
    return {
        'address': address, 'hostname': hostname,
        'mac': lease.get('hw-address', '').lower(), 'ends': ends,
        'source': source,
    }


def build_fqdn(hostname, suffix):
    if hostname.endswith('.' + suffix):
        return hostname + '.'
    return hostname + '.' + suffix + '.'


def forward_commands(action, address, fqdn):
    fqdn_name = fqdn.rstrip('.')
    if isinstance(address, ipaddress.IPv4Address):
        return ['update {} {}. 300 A {}'.format(action, fqdn_name, address)]
    if isinstance(address, ipaddress.IPv6Address):
        return ['update {} {}. 300 AAAA {}'.format(action, fqdn_name, address)]
    return []


def reverse_commands(action, address, fqdn):
    fqdn_name = fqdn.rstrip('.')
    return ['update {} {}. 300 PTR {}.'.format(action, address.reverse_pointer, fqdn_name)]


def select_reverse_zone(address, reverse_zones):
    """Return the most-specific configured reverse zone containing address."""
    return max(
        (zone for zone in reverse_zones if address in zone['network']),
        key=lambda zone: zone['network'].prefixlen,
        default=None,
    )
