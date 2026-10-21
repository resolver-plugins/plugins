import argparse
from pathlib import Path
import signal
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import syslog

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/opnsense/scripts/dhcp_interface_ha'))
import dhcp_interface_ha as cli


class DaemonWake:
    """Stop the daemon through its real signal handler after a bounded number of polls."""
    def __init__(self, polls, observed=lambda: None):
        self.observed = observed
        self.polls = polls
        self.handlers = {}

    def clear(self):
        pass

    def set(self):
        pass

    def wait(self, _timeout):
        self.observed()
        self.polls -= 1
        if self.polls == 0:
            self.handlers[signal.SIGTERM](signal.SIGTERM, None)


def daemon_status(**overrides):
    return {'state': 'ACTIVE', 'reason_code': 'global_master_attached', 'global_role': 'MASTER',
            'actual_attachment': 'ATTACHED', 'managed_interface': 'wan', 'carrier': {'name': 'hn1'},
            'ipv4_addresses': [], **overrides}


class CliTests(unittest.TestCase):
    def test_runtime_rejects_each_forbidden_override(self):
        for flags in (['--enabled'], ['--carrier', 'hn1']):
            with self.subTest(flags=flags), self.assertRaises(SystemExit) as rejected:
                cli.build_parser().parse_args(['reconcile', *flags])
            self.assertEqual(rejected.exception.code, 2)

    def test_health_withholds_demotion_when_fencing_cannot_be_verified(self):
        with patch.object(cli, 'Controller') as factory, patch.object(cli.os, 'geteuid', return_value=0), \
                patch.object(cli.syslog, 'syslog') as log:
            factory.return_value.health.side_effect = RuntimeError('fence failed')
            self.assertEqual(cli.cmd_runtime(argparse.Namespace(command='health')), 0)
            factory.return_value.health.side_effect = None
            factory.return_value.health.return_value = False
            self.assertEqual(cli.cmd_runtime(argparse.Namespace(command='health')), 100)
            log.assert_not_called()

    def test_daemon_logs_only_observed_state_and_address_changes(self):
        statuses = [
            daemon_status(ipv4_addresses=['192.0.2.10']),
            OSError('temporary observation failure'),
            daemon_status(ipv4_addresses=[]),
            daemon_status(state='STANDBY', reason_code='global_backup',
                          global_role='BACKUP', actual_attachment='FENCED'),
        ]
        polls = []
        wake = DaemonWake(4, lambda: polls.append([message for _, message in events]))
        controller = Mock(reconcile=Mock(side_effect=statuses))
        events = []
        with patch.object(cli.threading, 'Event', return_value=wake), \
                patch.object(cli.signal, 'signal', side_effect=wake.handlers.__setitem__), \
                patch.object(cli, 'run'):
            cli.serve(controller, lambda severity, message: events.append((severity, message)))

        self.assertEqual(polls[1], polls[0], 'failed observation must not invent address loss')
        self.assertEqual(sum('outcome=lost' in message for message in polls[2]), 1)
        messages = [message for _, message in events]
        codes = [message.split()[0] for message in messages]
        self.assertEqual(codes.count('event=service_started'), 1)
        self.assertEqual(codes.count('event=service_stopped'), 1)
        self.assertEqual(codes.count('event=state_observed'), 1)
        self.assertEqual(codes.count('event=state_changed'), 1)
        self.assertEqual(codes.count('event=ipv4_changed'), 2)
        self.assertIn('outcome=observed', messages[2])
        self.assertIn('outcome=lost', messages[3])
        self.assertEqual(events[3][0], syslog.LOG_WARNING)
        controller.report_daemon_failure.assert_not_called()
        controller.fence.assert_called_once_with(stop=True)

    def test_daemon_reconcile_and_native_refresh_failures_have_separate_suppression(self):
        with tempfile.TemporaryDirectory() as temporary:
            events = []
            controller = cli.Controller(
                Path(temporary) / 'config.xml', Path(temporary) / 'runtime',
                event_sink=lambda severity, message: events.append((severity, message)),
            )
            wake = DaemonWake(3)
            statuses = [
                daemon_status(ipv4_addresses=[]),
                OSError('reconcile command failed'),
                daemon_status(ipv4_addresses=[]),
            ]

            def reconcile(daemon=False):
                self.assertTrue(daemon)
                status = statuses.pop(0)
                if isinstance(status, Exception):
                    controller._report_failure(
                        'reconcile', status, 'fenced', daemon=True,
                        context={'interface': 'wan', 'carrier': 'hn1'},
                    )
                    raise status
                controller._report_recovery(status)
                return status

            with patch.object(cli.threading, 'Event', return_value=wake), \
                    patch.object(cli.signal, 'signal', side_effect=wake.handlers.__setitem__), \
                    patch.object(controller, 'reconcile', side_effect=reconcile), \
                    patch.object(controller, 'fence'), \
                    patch.object(cli, 'run', side_effect=[
                        OSError('CARP refresh failed'),
                        OSError('CARP refresh failed'),
                        None,
                    ]):
                cli.serve(controller, lambda severity, message: events.append((severity, message)))

        failures = [message for _, message in events if message.startswith('event=operation_failed ')]
        recoveries = [message for _, message in events if message.startswith('event=operation_recovered ')]
        self.assertEqual(len(failures), 2)
        self.assertTrue(any('operation=reconcile ' in message for message in failures))
        self.assertTrue(any('operation=carp_status_refresh ' in message for message in failures))
        self.assertEqual(len(recoveries), 2)
        self.assertTrue(any('operation=reconcile ' in message for message in recoveries))
        self.assertTrue(any('operation=carp_status_refresh ' in message for message in recoveries))
