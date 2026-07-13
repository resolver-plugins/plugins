#!/usr/bin/env python3

"""
    Copyright (c) 2026 Bryan Wiegand <inbox@kw-ventures.com>
    All rights reserved.

    Redistribution and use in source and binary forms, with or without
    modification, are permitted provided that the following conditions are met:

    1. Redistributions of source code must retain the above copyright notice,
       this list of conditions and the following disclaimer.

    2. Redistributions in binary form must reproduce the above copyright
       notice, this list of conditions and the following disclaimer in the
       documentation and/or other materials provided with the distribution.

    THIS SOFTWARE IS PROVIDED ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES,
    INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY
    AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
    AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY,
    OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
    SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
    INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
    CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
    ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
    POSSIBILITY OF SUCH DAMAGE.

    ---------------------------------------------------------------------------
    Watch DHCP leases and push/remove DNS records in BIND via nsupdate.

    Follows the same daemon pattern as the Unbound watcher
    (scripts/unbound/unbound_watcher.py in core).
"""

import syslog
import signal
import time
import os
import sys
import tempfile
import subprocess
import ipaddress
import json
import configparser
import argparse
import fcntl

sys.path.insert(0, "/usr/local/opnsense/site-python")
from daemonize import Daemonize
import watchers.dhcpd
sys.path.insert(0, "/usr/local/opnsense/scripts/kea/lib")
from kea_ctrl import KeaCtrl


# Global shutdown flag for signal handling
shutdown_flag = False
shutdown_inflight = None


def handle_sigterm(signum, frame):
    """Graceful shutdown on SIGTERM."""
    global shutdown_flag
    shutdown_flag = True
    syslog.syslog(syslog.LOG_NOTICE, "received SIGTERM, shutting down")


# ---------------------------------------------------------------------------
# Lease normalization
# ---------------------------------------------------------------------------

def normalize_isc_lease(lease, suffix):
    """Normalize an ISC DHCP lease into our unified representation.

    :param lease: dict from DHCPDLease.parse_lease
    :param suffix: hostname suffix from watcher mapping
    :return: dict or None if lease should be skipped
    """
    if 'ends' not in lease or lease['ends'] <= time.time():
        return None
    if 'binding' in lease and lease['binding'] in ('free', 'abandoned', 'backup'):
        return None
    if 'client-hostname' not in lease or not lease['client-hostname']:
        return None
    if 'address' not in lease:
        return None

    try:
        addr = ipaddress.ip_address(lease['address'])
    except ValueError:
        return None

    hostname = lease['client-hostname'].rstrip('.')
    mac = lease.get('hardware', {}).get('mac-address', '').lower()

    return {
        'address': addr,
        'hostname': hostname,
        'mac': mac,
        'ends': lease['ends'],
        'state': lease.get('binding', 'active'),
        'source': 'isc-dhcp',
        'type': None,
    }


def normalize_kea_lease(lease, source):
    """Normalize a Kea DHCP lease into our unified representation.

    :param lease: dict from KeaCtrl lease results
    :param source: 'kea-dhcp4' or 'kea-dhcp6'
    :return: dict or None if lease should be skipped
    """
    # Filter out PD leases before normalization
    if lease.get('type') == 'IA_PD':
        return None

    if 'ip-address' not in lease:
        return None

    try:
        addr = ipaddress.ip_address(lease['ip-address'])
    except ValueError:
        return None

    hostname = lease.get('hostname', '').rstrip('.')
    if not hostname:
        return None

    mac = lease.get('hw-address', '').lower()
    ends = lease.get('cltt', 0) + lease.get('valid-lft', 0)
    if ends <= time.time():
        return None

    state = lease.get('state', 0)

    return {
        'address': addr,
        'hostname': hostname,
        'mac': mac,
        'ends': ends,
        'state': str(state),
        'source': source,
        'type': lease.get('type'),
    }


# ---------------------------------------------------------------------------
# FQDN construction
# ---------------------------------------------------------------------------

def build_fqdn(hostname, suffix):
    """Build a fully-qualified domain name from a hostname and suffix.

    Handles the case where DHCP already returns a FQDN containing the suffix
    to avoid double-suffix like ``laptop.home.arpa..home.arpa``.
    """
    if hostname.endswith('.' + suffix):
        return hostname + '.'
    return hostname + '.' + suffix + '.'


# ---------------------------------------------------------------------------
# nsupdate execution
# ---------------------------------------------------------------------------

def run_nsupdate(mapping, commands):
    """Execute nsupdate with TSIG authentication via a temporary key file.

    :param mapping: dict with tsigkey_name, tsigkey_algo, tsigkey_secret,
                    nsupdate_address, nsupdate_port
    :param commands: list of nsupdate command strings to pipe to stdin
    :return: True on success, False on failure
    """
    keyfile = None
    try:
        keyfile = tempfile.NamedTemporaryFile(
            mode='w', prefix='nsupdate-key-', delete=False
        )
        os.chmod(keyfile.name, 0o600)
        keyfile.write('key "{}" {{\n'.format(mapping['tsigkey_name']))
        keyfile.write('    algorithm {};\n'.format(mapping['tsigkey_algo']))
        keyfile.write('    secret "{}";\n'.format(mapping['tsigkey_secret']))
        keyfile.write('};\n')
        keyfile.close()

        nsupdate_input = ''
        nsupdate_input += 'server {} {}\n'.format(
            mapping['nsupdate_address'], mapping['nsupdate_port']
        )
        for cmd in commands:
            nsupdate_input += cmd + '\n'
        nsupdate_input += 'send\n'

        result = subprocess.run(
            ['/usr/bin/nsupdate', '-k', keyfile.name],
            input=nsupdate_input,
            text=True,
            capture_output=True,
            timeout=10,
        )

        if result.returncode != 0:
            stderr = result.stderr.lower() if result.stderr else ''
            if 'refused' in stderr:
                syslog.syslog(
                    syslog.LOG_ERR,
                    'nsupdate REFUSED: key=%s zone=%s',
                    mapping['tsigkey_name'], mapping['hostname_suffix']
                )
            elif 'notauth' in stderr:
                syslog.syslog(
                    syslog.LOG_WARNING,
                    'nsupdate NOTAUTH: key=%s zone=%s (zone may not exist or '
                    'key lacks permissions)',
                    mapping['tsigkey_name'], mapping['hostname_suffix']
                )
            elif 'servfail' in stderr:
                syslog.syslog(
                    syslog.LOG_ERR,
                    'nsupdate SERVFAIL: key=%s zone=%s',
                    mapping['tsigkey_name'], mapping['hostname_suffix']
                )
            elif 'connection refused' in stderr:
                syslog.syslog(
                    syslog.LOG_ERR,
                    'nsupdate connection refused: server=%s:%s',
                    mapping['nsupdate_address'], mapping['nsupdate_port']
                )
            elif 'timed out' in stderr:
                syslog.syslog(
                    syslog.LOG_ERR,
                    'nsupdate timed out: server=%s:%s',
                    mapping['nsupdate_address'], mapping['nsupdate_port']
                )
            else:
                syslog.syslog(
                    syslog.LOG_ERR,
                    'nsupdate failed (rc={}): {}'.format(
                        result.returncode, result.stderr.strip()
                    )
                )
            return False
        return True

    except subprocess.TimeoutExpired:
        syslog.syslog(
            syslog.LOG_ERR,
            'nsupdate timed out (timeout=10s): server=%s:%s',
            mapping['nsupdate_address'], mapping['nsupdate_port']
        )
        return False
    except Exception as e:
        syslog.syslog(
            syslog.LOG_ERR,
            'nsupdate exception: {}'.format(e)
        )
        return False
    finally:
        if keyfile and os.path.exists(keyfile.name):
            try:
                os.unlink(keyfile.name)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# DNS record generation
# ---------------------------------------------------------------------------

def build_nsupdate_commands(action, address, fqdn):
    """Build nsupdate commands for A/AAAA and PTR records.

    :param action: 'add' or 'delete'
    :param address: ipaddress object
    :param fqdn: fully-qualified domain name (trailing dot)
    :return: list of nsupdate command strings
    """
    reverse = address.reverse_pointer
    commands = []

    if isinstance(address, ipaddress.IPv4Address):
        commands.append('update {} {}. 300 A {}'.format(action, fqdn, address))
    elif isinstance(address, ipaddress.IPv6Address):
        commands.append('update {} {}. 300 AAAA {}'.format(action, fqdn, address))

    commands.append('update {} {}. 300 PTR {}.'.format(action, reverse, fqdn))
    return commands


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def load_config(path='/usr/local/etc/bind/dhcpwatcher.conf'):
    """Load watcher mappings from the template-generated config file.

    :param path: path to dhcpwatcher.conf
    :return: dict of mapping_uuid -> mapping dict
    """
    mappings = {}
    if not os.path.isfile(path):
        syslog.syslog(syslog.LOG_NOTICE, 'config file not found: {}'.format(path))
        return mappings

    cnf = configparser.ConfigParser()
    cnf.read(path)
    for section in cnf.sections():
        mappings[section] = {
            'dhcp_source': cnf.get(section, 'dhcp_source'),
            'hostname_suffix': cnf.get(section, 'hostname_suffix'),
            'tsigkey_name': cnf.get(section, 'tsigkey_name'),
            'tsigkey_algo': cnf.get(section, 'tsigkey_algo'),
            'tsigkey_secret': cnf.get(section, 'tsigkey_secret'),
            'nsupdate_address': cnf.get(section, 'nsupdate_address'),
            'nsupdate_port': cnf.get(section, 'nsupdate_port'),
        }
    return mappings


def config_mtime(path='/usr/local/etc/bind/dhcpwatcher.conf'):
    """Return the mtime of the config file, or 0 if absent."""
    try:
        return os.stat(path).st_mtime
    except OSError:
        return 0


# ---------------------------------------------------------------------------
# State file management
# ---------------------------------------------------------------------------

STATE_FILE = '/var/cache/bind/dhcplease_state.json'


def load_state():
    """Load persisted lease state, keyed by (source, ip_address)."""
    if not os.path.isfile(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        syslog.syslog(syslog.LOG_WARNING, 'failed to read state file, starting fresh')
        return {}


def save_state(state):
    """Persist lease state to disk."""
    os.makedirs('/var/cache/bind', exist_ok=True)
    tmp = STATE_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(state, f)
    os.rename(tmp, STATE_FILE)


# ---------------------------------------------------------------------------
# Daemonized watcher
# ---------------------------------------------------------------------------

def run_watcher(dhcp_source=None, hostname_suffix=None):
    """Entry point for the daemonized watcher."""
    # Build shared config watchers set
    active_mappings = {}

    def check_config_and_pid():
        """Check health of the daemon and reload config if needed."""
        # Check if named is alive; exit if not running for 2 consecutive checks
        nonlocal pid_misses
        if not os.path.exists('/var/run/named/pid'):
            pid_misses += 1
            if pid_misses >= 2:
                syslog.syslog(syslog.LOG_NOTICE, 'named not running, exiting')
                return False
        else:
            pid_misses = 0
        return True

    def check_config_for_source(source):
        """Check if the dhcpwatcher.conf has changed and reload if needed."""
        return source not in active_mappings or not active_mappings[source]

    pid_misses = 0

    # Initial config load
    mappings = load_config()
    if not mappings:
        syslog.syslog(syslog.LOG_NOTICE, 'no enabled watcher mappings, sleeping')
        while not shutdown_flag:
            time.sleep(10)
        return

    active_mappings = mappings

    # Reconcile per-source from config
    sources_needed = set()
    for uuid, mapping in mappings.items():
        sources_needed.add(mapping['dhcp_source'])

    # Read previous state and reconcile
    state = load_state()
    cached_leases = {}  # key: (source, str(ip)) -> lease dict
    config_last_mtime = config_mtime()
    last_cleanup = time.time()

    # Initialize ISC DHCP watcher if configured
    isc_watcher = None
    if 'isc-dhcp' in sources_needed:
        try:
            isc_watcher = watchers.dhcpd.DHCPDLease('/var/dhcpd/var/db/dhcpd.leases')
            syslog.syslog(syslog.LOG_NOTICE, 'watching ISC DHCP leases at /var/dhcpd/var/db/dhcpd.leases')
        except IOError:
            syslog.syslog(syslog.LOG_WARNING, 'cannot open ISC DHCP lease file')

    # Kea poll timers (10 second intervals)
    kea4_last_poll = time.time()
    kea6_last_poll = time.time()
    KEA_POLL_INTERVAL = 10

    # Startup reconciliation: process all active leases from each source
    syslog.syslog(syslog.LOG_NOTICE, 'starting lease reconciliation')

    # ISC: catch up on all existing leases
    if isc_watcher:
        for lease in isc_watcher.watch():
            normalized = normalize_isc_lease(lease, '')
            if normalized:
                key = (normalized['source'], str(normalized['address']))
                cached_leases[key] = normalized

    # Kea: fetch current state
    kea4_leases = {}
    kea6_leases = {}
    if 'kea-dhcp4' in sources_needed:
        try:
            result = KeaCtrl.send_command('lease4-get-all', {}, 'dhcp4')
            for lease in result.get('arguments', {}).get('leases', []):
                normalized = normalize_kea_lease(lease, 'kea-dhcp4')
                if normalized:
                    key = ('kea-dhcp4', str(normalized['address']))
                    kea4_leases[key] = normalized
                    cached_leases[key] = normalized
        except Exception as e:
            syslog.syslog(syslog.LOG_WARNING, 'kea4 startup fetch failed: {}'.format(e))

    if 'kea-dhcp6' in sources_needed:
        try:
            result = KeaCtrl.send_command('lease6-get-all', {}, 'dhcp6')
            for lease in result.get('arguments', {}).get('leases', []):
                normalized = normalize_kea_lease(lease, 'kea-dhcp6')
                if normalized:
                    key = ('kea-dhcp6', str(normalized['address']))
                    kea6_leases[key] = normalized
                    cached_leases[key] = normalized
        except Exception as e:
            syslog.syslog(syslog.LOG_WARNING, 'kea6 startup fetch failed: {}'.format(e))

    # Reconcile state file vs active leases: remove stale records
    stale_keys = []
    for state_key in list(state.keys()):
        if state_key not in cached_leases:
            # Check if expired
            entry = state[state_key]
            if 'ends' in entry and entry['ends'] < time.time():
                stale_keys.append(state_key)
            # If absent from leases and not in cache, schedule for removal
            elif state_key not in cached_leases:
                stale_keys.append(state_key)

    if stale_keys:
        syslog.syslog(syslog.LOG_NOTICE, 'cleaning {} stale records from state'.format(len(stale_keys)))
        for sk in stale_keys:
            del state[sk]
        save_state(state)

    # Push all current leases to BIND
    push_count = 0
    for (source, ip_str), lease in cached_leases.items():
        state_key = '{},{}'.format(source, ip_str)
        if state_key in state:
            # Already in BIND from a previous run
            continue
        # Find matching mapping for this lease source
        for uuid, mapping in active_mappings.items():
            if mapping['dhcp_source'] == source:
                fqdn = build_fqdn(lease['hostname'], mapping['hostname_suffix'])
                cmds = build_nsupdate_commands('add', lease['address'], fqdn)
                if run_nsupdate(mapping, cmds):
                    push_count += 1
                # Write state entry
                state[state_key] = {
                    'address': ip_str,
                    'hostname': lease['hostname'],
                    'suffix': mapping['hostname_suffix'],
                    'ends': lease['ends'],
                    'mac': lease['mac'],
                    'source': source,
                    'mapping_uuid': uuid,
                }
    if push_count:
        syslog.syslog(syslog.LOG_NOTICE, 'startup: pushed {} records'.format(push_count))
        save_state(state)

    # Main loop
    syslog.syslog(syslog.LOG_NOTICE, 'entering main loop')
    while not shutdown_flag:
        changed = False

        # Process ISC DHCP leases
        if isc_watcher:
            for lease in isc_watcher.watch():
                normalized = normalize_isc_lease(lease, '')
                if normalized:
                    key = (normalized['source'], str(normalized['address']))
                    state_key = '{},{}'.format(normalized['source'], str(normalized['address']))

                    # Check if this lease is new or changed
                    if key not in cached_leases:
                        cached_leases[key] = normalized
                        for uuid, mapping in active_mappings.items():
                            if mapping['dhcp_source'] == 'isc-dhcp':
                                fqdn = build_fqdn(normalized['hostname'], mapping['hostname_suffix'])
                                cmds = build_nsupdate_commands('add', normalized['address'], fqdn)
                                if run_nsupdate(mapping, cmds):
                                    syslog.syslog(
                                        syslog.LOG_NOTICE,
                                        'added {} -> {} (ISC)'.format(fqdn, normalized['address'])
                                    )
                                    # Remove any previous PTR for this address
                                    for old_key in list(state.keys()):
                                        if old_key.endswith(',' + str(normalized['address'])):
                                            old_entry = state[old_key]
                                            old_fqdn = build_fqdn(old_entry['hostname'], old_entry.get('suffix', ''))
                                            del_cmds = build_nsupdate_commands('delete', normalized['address'], old_fqdn)
                                            run_nsupdate(mapping, del_cmds)
                                            del state[old_key]
                                state[state_key] = {
                                    'address': str(normalized['address']),
                                    'hostname': normalized['hostname'],
                                    'suffix': mapping['hostname_suffix'],
                                    'ends': normalized['ends'],
                                    'mac': normalized['mac'],
                                    'source': 'isc-dhcp',
                                    'mapping_uuid': uuid,
                                }
                                changed = True

        # Process Kea DHCPv4 leases
        if 'kea-dhcp4' in sources_needed and time.time() - kea4_last_poll >= KEA_POLL_INTERVAL:
            kea4_last_poll = time.time()
            try:
                result = KeaCtrl.send_command('lease4-get-all', {}, 'dhcp4')
                new_leases = {}
                for lease in result.get('arguments', {}).get('leases', []):
                    normalized = normalize_kea_lease(lease, 'kea-dhcp4')
                    if normalized:
                        key = ('kea-dhcp4', str(normalized['address']))
                        new_leases[key] = normalized

                # Diff: new/changed and removed
                for key, normalized in new_leases.items():
                    state_key = '{},{}'.format(normalized['source'], str(normalized['address']))
                    if key not in cached_leases:
                        cached_leases[key] = normalized
                        for uuid, mapping in active_mappings.items():
                            if mapping['dhcp_source'] == 'kea-dhcp4':
                                fqdn = build_fqdn(normalized['hostname'], mapping['hostname_suffix'])
                                cmds = build_nsupdate_commands('add', normalized['address'], fqdn)
                                if run_nsupdate(mapping, cmds):
                                    syslog.syslog(
                                        syslog.LOG_NOTICE,
                                        'added {} -> {} (Kea4)'.format(fqdn, normalized['address'])
                                    )
                                state[state_key] = {
                                    'address': str(normalized['address']),
                                    'hostname': normalized['hostname'],
                                    'suffix': mapping['hostname_suffix'],
                                    'ends': normalized['ends'],
                                    'mac': normalized['mac'],
                                    'source': 'kea-dhcp4',
                                    'mapping_uuid': uuid,
                                }
                                changed = True

                # Remove leases that disappeared from Kea
                for key in list(cached_leases.keys()):
                    if key[0] == 'kea-dhcp4' and key not in new_leases:
                        lease = cached_leases[key]
                        state_key = '{},{}'.format(key[0], key[1])
                        if state_key in state:
                            entry = state[state_key]
                            for uuid, mapping in active_mappings.items():
                                if mapping['dhcp_source'] == 'kea-dhcp4':
                                    fqdn = build_fqdn(lease['hostname'], mapping['hostname_suffix'])
                                    cmds = build_nsupdate_commands('delete', lease['address'], fqdn)
                                    run_nsupdate(mapping, cmds)
                                    syslog.syslog(
                                        syslog.LOG_NOTICE,
                                        'removed {} -> {} (Kea4)'.format(fqdn, lease['address'])
                                    )
                        del cached_leases[key]
                        if state_key in state:
                            del state[state_key]
                        changed = True
            except Exception as e:
                syslog.syslog(syslog.LOG_WARNING, 'kea4 poll failed: {}'.format(e))

        # Process Kea DHCPv6 leases
        if 'kea-dhcp6' in sources_needed and time.time() - kea6_last_poll >= KEA_POLL_INTERVAL:
            kea6_last_poll = time.time()
            try:
                result = KeaCtrl.send_command('lease6-get-all', {}, 'dhcp6')
                new_leases = {}
                for lease in result.get('arguments', {}).get('leases', []):
                    normalized = normalize_kea_lease(lease, 'kea-dhcp6')
                    if normalized:
                        key = ('kea-dhcp6', str(normalized['address']))
                        new_leases[key] = normalized

                for key, normalized in new_leases.items():
                    state_key = '{},{}'.format(normalized['source'], str(normalized['address']))
                    if key not in cached_leases:
                        cached_leases[key] = normalized
                        for uuid, mapping in active_mappings.items():
                            if mapping['dhcp_source'] == 'kea-dhcp6':
                                fqdn = build_fqdn(normalized['hostname'], mapping['hostname_suffix'])
                                cmds = build_nsupdate_commands('add', normalized['address'], fqdn)
                                if run_nsupdate(mapping, cmds):
                                    syslog.syslog(
                                        syslog.LOG_NOTICE,
                                        'added {} -> {} (Kea6)'.format(fqdn, normalized['address'])
                                    )
                                state[state_key] = {
                                    'address': str(normalized['address']),
                                    'hostname': normalized['hostname'],
                                    'suffix': mapping['hostname_suffix'],
                                    'ends': normalized['ends'],
                                    'mac': normalized['mac'],
                                    'source': 'kea-dhcp6',
                                    'mapping_uuid': uuid,
                                }
                                changed = True

                for key in list(cached_leases.keys()):
                    if key[0] == 'kea-dhcp6' and key not in new_leases:
                        lease = cached_leases[key]
                        state_key = '{},{}'.format(key[0], key[1])
                        if state_key in state:
                            entry = state[state_key]
                            for uuid, mapping in active_mappings.items():
                                if mapping['dhcp_source'] == 'kea-dhcp6':
                                    fqdn = build_fqdn(lease['hostname'], mapping['hostname_suffix'])
                                    cmds = build_nsupdate_commands('delete', lease['address'], fqdn)
                                    run_nsupdate(mapping, cmds)
                                    syslog.syslog(
                                        syslog.LOG_NOTICE,
                                        'removed {} -> {} (Kea6)'.format(fqdn, lease['address'])
                                    )
                        del cached_leases[key]
                        if state_key in state:
                            del state[state_key]
                        changed = True
            except Exception as e:
                syslog.syslog(syslog.LOG_WARNING, 'kea6 poll failed: {}'.format(e))

        # Periodic cleanup: expired leases
        if time.time() - last_cleanup > 60:
            last_cleanup = time.time()

            # Health check
            if not check_config_and_pid():
                return

            # Reload config if changed
            new_mtime = config_mtime()
            if new_mtime != config_last_mtime:
                syslog.syslog(syslog.LOG_NOTICE, 'config file changed, reloading')
                active_mappings = load_config()
                config_last_mtime = new_mtime
                sources_needed = set()
                for uuid, mapping in active_mappings.items():
                    sources_needed.add(mapping['dhcp_source'])

            # Clean expired
            for key in list(cached_leases.keys()):
                lease = cached_leases[key]
                if lease['ends'] < time.time():
                    state_key = '{},{}'.format(lease['source'], key[1])
                    if state_key in state:
                        for uuid, mapping in active_mappings.items():
                            if mapping['dhcp_source'] == lease['source']:
                                fqdn = build_fqdn(lease['hostname'], mapping['hostname_suffix'])
                                cmds = build_nsupdate_commands('delete', lease['address'], fqdn)
                                run_nsupdate(mapping, cmds)
                                syslog.syslog(
                                    syslog.LOG_NOTICE,
                                    'expired {} -> {} ({})'.format(
                                        fqdn, lease['address'], lease['source']
                                    )
                                )
                    del cached_leases[key]
                    if state_key in state:
                        del state[state_key]
                    changed = True

        # Persist state after any change
        if changed:
            save_state(state)

        time.sleep(1)

    syslog.syslog(syslog.LOG_NOTICE, 'watcher exited cleanly')


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--foreground', action='store_true', default=False,
        help='run in foreground (do not daemonize)'
    )
    parser.add_argument(
        '--pid', default='/var/run/bind_dhcplease.pid',
        help='pid file location'
    )
    args = parser.parse_args()

    syslog.openlog('bind-dhcplease', facility=syslog.LOG_LOCAL4)

    if args.foreground:
        signal.signal(signal.SIGTERM, handle_sigterm)
        run_watcher()
    else:
        syslog.syslog(syslog.LOG_NOTICE, 'daemonizing bind dhcpd watcher')
        signal.signal(signal.SIGTERM, handle_sigterm)
        daemon = Daemonize(
            app='bind-dhcplease',
            pid=args.pid,
            action=run_watcher,
        )
        daemon.start()
