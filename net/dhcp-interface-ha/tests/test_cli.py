import argparse
from pathlib import Path
import signal
import sys
import tempfile
import unittest
from unittest.mock import patch
import syslog

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/opnsense/scripts/dhcp_interface_ha'))
import dhcp_interface_ha as cli


class CliTests(unittest.TestCase):
    def test_mutation_does_not_accept_dry_run_role_or_carrier_overrides(self):
        with self.assertRaises(SystemExit):
            cli.build_parser().parse_args(['reconcile', '--enabled', '--carrier', 'hn1'])

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
        class Wake:
            def __init__(self):
                self.waits = 0
                self.handlers = {}

            def clear(self):
                pass

            def set(self):
                pass

            def wait(self, _timeout):
                self.waits += 1
                if self.waits == 4:
                    self.handlers[signal.SIGTERM](signal.SIGTERM, None)

        class DaemonController:
            def __init__(self):
                self.results = [
                    {
                        'state': 'ACTIVE', 'reason_code': 'global_master_attached',
                        'global_role': 'MASTER', 'actual_attachment': 'ATTACHED',
                        'managed_interface': 'wan', 'carrier': {'name': 'hn1'},
                        'ipv4_addresses': ['192.0.2.10'],
                    },
                    OSError('temporary observation failure'),
                    {
                        'state': 'ACTIVE', 'reason_code': 'global_master_attached',
                        'global_role': 'MASTER', 'actual_attachment': 'ATTACHED',
                        'managed_interface': 'wan', 'carrier': {'name': 'hn1'},
                        'ipv4_addresses': [],
                    },
                    {
                        'state': 'STANDBY', 'reason_code': 'global_backup',
                        'global_role': 'BACKUP', 'actual_attachment': 'FENCED',
                        'managed_interface': 'wan', 'carrier': {'name': 'hn1'},
                        'ipv4_addresses': [],
                    },
                ]
                self.stopped = False
                self.failures = []
                self.recoveries = []

            def reconcile(self, daemon=False):
                self.assert_daemon = daemon
                result = self.results.pop(0)
                if isinstance(result, Exception):
                    raise result
                return result

            def report_daemon_failure(self, operation, error):
                self.failures.append(operation)

            def report_daemon_recovery(self, operation):
                self.recoveries.append(operation)

            def fence(self, stop=False):
                self.stopped = stop

        wake = Wake()
        controller = DaemonController()
        events = []
        handlers = wake.handlers

        def register(signum, handler):
            handlers[signum] = handler

        with patch.object(cli.threading, 'Event', return_value=wake), \
                patch.object(cli.signal, 'signal', side_effect=register), \
                patch.object(cli, 'run'), \
                patch.object(cli, 'emit_event', side_effect=lambda code, severity, sink, **fields:
                             sink(severity, 'event=' + code + ' ' + ' '.join(
                                 f'{key}={value}' for key, value in fields.items()))):
            cli.serve(controller, lambda severity, message: events.append((severity, message)))

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
        self.assertEqual(controller.failures, [])

    def test_daemon_reconcile_and_native_refresh_failures_have_separate_suppression(self):
        class Wake:
            def __init__(self):
                self.waits = 0
                self.handlers = {}

            def clear(self):
                pass

            def set(self):
                pass

            def wait(self, _timeout):
                self.waits += 1
                if self.waits == 3:
                    self.handlers[signal.SIGTERM](signal.SIGTERM, None)

        with tempfile.TemporaryDirectory() as temporary:
            events = []
            controller = cli.Controller(
                Path(temporary) / 'config.xml', Path(temporary) / 'runtime',
                event_sink=lambda severity, message: events.append((severity, message)),
            )
            wake = Wake()
            statuses = [
                {
                    'state': 'ACTIVE', 'reason_code': 'global_master_attached',
                    'global_role': 'MASTER', 'actual_attachment': 'ATTACHED',
                    'managed_interface': 'wan', 'carrier': {'name': 'hn1'},
                    'ipv4_addresses': [],
                },
                OSError('reconcile command failed'),
                {
                    'state': 'ACTIVE', 'reason_code': 'global_master_attached',
                    'global_role': 'MASTER', 'actual_attachment': 'ATTACHED',
                    'managed_interface': 'wan', 'carrier': {'name': 'hn1'},
                    'ipv4_addresses': [],
                },
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

            handlers = wake.handlers

            def register(signum, handler):
                handlers[signum] = handler

            with patch.object(cli.threading, 'Event', return_value=wake), \
                    patch.object(cli.signal, 'signal', side_effect=register), \
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
