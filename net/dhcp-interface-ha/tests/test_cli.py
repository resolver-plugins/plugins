import argparse
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/opnsense/scripts/dhcp_interface_ha'))
import dhcp_interface_ha as cli


class CliTests(unittest.TestCase):
    def test_mutation_does_not_accept_dry_run_role_or_carrier_overrides(self):
        with self.assertRaises(SystemExit):
            cli.build_parser().parse_args(['reconcile', '--enabled', '--carrier', 'hn1'])

    def test_health_withholds_demotion_when_fencing_cannot_be_verified(self):
        with patch.object(cli, 'Controller') as factory, patch.object(cli.os, 'geteuid', return_value=0):
            factory.return_value.health.side_effect = RuntimeError('fence failed')
            self.assertEqual(cli.cmd_runtime(argparse.Namespace(command='health')), 0)
            factory.return_value.health.side_effect = None
            factory.return_value.health.return_value = False
            self.assertEqual(cli.cmd_runtime(argparse.Namespace(command='health')), 100)
