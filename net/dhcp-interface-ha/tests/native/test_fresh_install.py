"""Read-only check after installing the package into an unconfigured OPNsense VM."""
import json
import subprocess
import sys
import time

sys.path.insert(0, '/usr/local/opnsense/scripts/dhcp_interface_ha')
from runtime import Controller, interface_snapshot, _ipv4_addresses

# Qualify the installed non-verbose reader against OPNsense's native parser.
# This single verbose read belongs to package verification, not daemon polling.
native = json.loads(subprocess.run(
    ['/usr/local/sbin/pluginctl', '-D'], text=True, capture_output=True,
    check=True, timeout=10,
).stdout)
light = Controller().inventory()
assert set(light) == set(native), 'interface inventory device set differs'
for name, expected in native.items():
    observed = light[name]
    assert interface_snapshot(name, light) == interface_snapshot(name, native), name
    assert observed['is_physical'] == expected['is_physical'], name
    for field in ('macaddr', 'macaddr_hw'):
        assert observed.get(field) == expected.get(field), (name, field)
    assert _ipv4_addresses(light, name) == _ipv4_addresses(native, name), name
    assert {x['ipaddr'].split('%', 1)[0] for x in observed['ipv6']} == {
        x['ipaddr'] for x in expected['ipv6']
    }, name
    assert {vhid: x['status'].upper() for vhid, x in observed['carp'].items()} == {
        vhid: x['status'].upper() for vhid, x in expected.get('carp', {}).items()
    }, name
    assert set(observed.get('groups', [])) == set(expected.get('groups', [])), name
    assert set(observed.get('members', {})) == set(expected.get('members', {})), name
    assert observed.get('vlan', {}).get('parent') == expected.get('vlan', {}).get('parent'), name
    for field in ('tunnel', 'vxlan'):
        assert bool(observed.get(field)) == bool(expected.get(field)), (name, field)
print('Non-verbose inventory: installed reader matches native HA safety fields.')

for attempt in range(10):
    result = subprocess.run(['configctl', 'dhcp_interface_ha', 'status'],
                            text=True, capture_output=True, check=True)
    status = json.loads(result.stdout)
    if status.get('controller_running'):
        break
    time.sleep(0.5)
assert status['controller_running'], status
assert status['state'] == 'SETUP_INCOMPLETE', status
assert status['enabled'] is False, status
assert not status['managed_interface'], status
assert not status['ipv4_addresses'], status
assert status['actual_attachment'] == 'UNMANAGED', status
print('Fresh installation: controller running, status readable, no interface configured or attached.')
