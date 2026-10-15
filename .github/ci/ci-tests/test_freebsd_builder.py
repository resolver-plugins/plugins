"""Local coverage for the homelab build transport and its credential boundary."""
import importlib.util
import io
import os
from pathlib import Path, PurePosixPath
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('freebsd_builder', ROOT / '.github/ci/shared/freebsd_builder.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class FreeBSDBuilderTest(unittest.TestCase):
    def test_guest_archive_cannot_replace_inputs_escape_or_create_links(self):
        selected = PurePosixPath('artifacts/26.7/producer')
        for name, kind in [('artifacts/trusted/selection.json', tarfile.REGTYPE),
                           ('artifacts/26.7/producer/../../escape', tarfile.REGTYPE),
                           ('artifacts/26.7/producer/link', tarfile.SYMTYPE)]:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                directory = Path(directory)
                package = directory / 'output.tar'
                with tarfile.open(package, 'w') as archive:
                    member = tarfile.TarInfo(name)
                    member.type = kind
                    member.linkname = '/etc/passwd' if kind == tarfile.SYMTYPE else ''
                    archive.addfile(member)
                with self.assertRaises(ValueError):
                    builder.extract_output(package, directory / 'returned', selected)

    def run_fixture(self, failure=None, release='15.1-RELEASE'):
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            checkout = Path(directory) / 'checkout'
            checkout.mkdir()
            (checkout / 'source.txt').write_text('reviewed source')

            def execute(command, **kwargs):
                operation, arguments = command[2], command[5:]
                calls.append((operation, arguments))
                if operation != 'exec':
                    return subprocess.CompletedProcess(command, 0)
                if arguments[:1] == ['freebsd-version']:
                    return subprocess.CompletedProcess(command, 0, release + '\n')
                if arguments[:1] == ['mktemp']:
                    return subprocess.CompletedProcess(command, 0, '/var/tmp/rp-build.fixture\n')
                if arguments[:1] == ['env'] and failure:
                    raise failure
                if arguments[:1] == ['tar'] and '-xf' in arguments:
                    with tarfile.open(fileobj=kwargs['stdin']) as archive:
                        self.assertEqual(archive.extractfile('./source.txt').read(), b'reviewed source')
                        self.assertIn('rp-build.sh', archive.getnames())
                if arguments[:1] == ['tar'] and '-cf' in arguments:
                    with tarfile.open(fileobj=kwargs['stdout'], mode='w') as archive:
                        item = tarfile.TarInfo('artifacts/26.7/producer/package.pkg')
                        item.size = 7
                        archive.addfile(item, io.BytesIO(b'package'))
                return subprocess.CompletedProcess(command, 0)

            with patch.object(builder.subprocess, 'run', side_effect=execute), patch.dict(os.environ, SERIES='26.7'):
                try:
                    builder.run_build(series='26.7', release='15.1', environment='SERIES',
                                      output='artifacts/26.7/producer', script='true\n',
                                      checkout=checkout, controller=Path('/fixture/builderctl.py'))
                except BaseException:
                    self.assertEqual(calls[-1][0], 'clean')
                    raise
            self.assertEqual((checkout / 'artifacts/26.7/producer/package.pkg').read_bytes(), b'package')
        return calls

    def test_success_transfers_source_and_output_then_cleans(self):
        calls = self.run_fixture()
        self.assertEqual(calls[0][0], 'reset')
        self.assertEqual(calls[-1][0], 'clean')
        exported = next(arguments for operation, arguments in calls if arguments[:1] == ['env'])
        self.assertEqual(exported[1], 'SERIES=26.7')

    def test_failure_and_cancellation_still_clean(self):
        for error in [subprocess.CalledProcessError(1, ['fixture']), KeyboardInterrupt()]:
            with self.subTest(error=type(error).__name__), self.assertRaises(type(error)):
                self.run_fixture(failure=error)

    def test_sealed_release_mismatch_still_cleans(self):
        with self.assertRaisesRegex(ValueError, 'sealed controller profile'):
            self.run_fixture(release='14.3-RELEASE')

    def test_signing_credentials_rejected_before_vm_start(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(builder.subprocess, 'run') as execute:
            with self.assertRaisesRegex(ValueError, 'build metadata'):
                builder.run_build(series='26.7', release='15.1', environment='RP_PKG_SIGNING_KEY',
                                  output='artifacts/26.7/producer', script='true',
                                  checkout=Path(directory), controller=Path('/fixture/builderctl.py'))
            execute.assert_not_called()


if __name__ == '__main__':
    unittest.main()
