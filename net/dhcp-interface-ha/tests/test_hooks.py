"""Execute lifecycle hooks with local command fixtures; never contact appliances."""
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

PLUGIN = Path(__file__).resolve().parents[1]


class HookTests(unittest.TestCase):
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
