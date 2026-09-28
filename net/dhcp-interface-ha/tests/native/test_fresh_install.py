"""Read-only check after installing the package into an unconfigured OPNsense VM."""
import json
import subprocess
import time

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
