"""Execute lifecycle hooks with local command fixtures; never contact appliances."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

PLUGIN = Path(__file__).resolve().parents[1]


class HookTests(unittest.TestCase):
    def test_device_preparation_uses_supported_core_command_helper(self):
        # OPNsense 26.7 provides mwexecf(), not the removed mwexec(). Invoke
        # the real boot callback with only the supported command/log boundary.
        fixture = r'''
            $commands = [];
            $messages = [];
            function mwexecf($format, $args = [], $mute = false) {
                $GLOBALS['commands'][] = $format;
                return (int)getenv('PREPARE_RESULT');
            }
            function log_msg($message, $level) {
                $GLOBALS['messages'][] = $message;
            }
            require $argv[1];
            $result = dhcp_interface_ha_prepare_device($argv[2]);
            echo json_encode([$result, $commands, $messages]);
        '''
        hook = PLUGIN / 'src/etc/inc/plugins.inc.d/dhcp_interface_ha.inc'
        command = '/usr/local/bin/python3 /usr/local/opnsense/scripts/dhcp_interface_ha/dhcp_interface_ha.py prepare'
        for device, status in (('dhcpha0lagg', 0), ('dhcpha0lagg', 1), ('dhcpha0lagg', 127), ('unrelated0', 0)):
            with self.subTest(device=device, controller_exit=status):
                result = subprocess.run(
                    ['php', '-r', fixture, str(hook), device],
                    env={**os.environ, 'PREPARE_RESULT': str(status)},
                    text=True, capture_output=True,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                prepared, commands, messages = json.loads(result.stdout)
                selected = device == 'dhcpha0lagg'
                self.assertEqual(prepared, device if selected and status == 0 else None)
                self.assertEqual(commands, [command] if selected else [])
                self.assertEqual(bool(messages), selected and status in (126, 127))

    def test_health_hook_demotes_only_on_verified_incapacity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake = root / 'controller.py'
            fake.write_text('import os, sys\nsys.exit(int(os.environ["HEALTH_RESULT"]))\n')
            logger = root / 'logger'
            logger.write_text('#!/bin/sh\nexit 0\n')
            logger.chmod(0o755)
            script = (PLUGIN / 'src/etc/rc.carp_service_status.d/dhcp-interface-ha').read_text()
            # Substitute only the executable boundary, retaining the real hook.
            script = script.replace('/usr/local/bin/python3', shlex.quote(sys.executable) + ' ' + shlex.quote(str(fake)))
            for status, expected in ((0, 0), (100, 1), (1, 0), (127, 0)):
                with self.subTest(controller_exit=status):
                    result = subprocess.run(['/bin/sh'], input=script, text=True, capture_output=True,
                                            env={**os.environ, 'PATH': directory, 'HEALTH_RESULT': str(status)})
                    self.assertEqual(result.returncode, expected, result.stderr)

    def test_deinstall_assignment_guard_and_upgrade_exception(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'conf').mkdir()
            (root / 'conf/config.xml').write_text('<opnsense><interfaces><wan><if>dhcpha0lagg</if></wan></interfaces></opnsense>')
            env = {**os.environ, 'PKG_ROOTDIR': directory, 'PKG_UPGRADE': ''}
            command = ['sh', str(PLUGIN / '+PRE_DEINSTALL.pre')]
            self.assertNotEqual(subprocess.run(command, env=env, capture_output=True).returncode, 0)
            env['PKG_UPGRADE'] = 'yes'
            self.assertEqual(subprocess.run(command, env=env, capture_output=True).returncode, 0)
