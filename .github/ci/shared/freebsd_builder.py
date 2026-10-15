"""Transport a plugin build to an on-demand FreeBSD VM without signing credentials."""
from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile


BUILD_ENVIRONMENT = {
    'SERIES', 'SOURCE_COMMIT', 'PROFILE_COMMIT', 'CONTROL_COMMIT',
    'RP_TRUSTED_DIRECTORY', 'RP_SELECTION_SHA256',
    'RP_TARGET_PKG_METADATA', 'RP_READER_PKG_METADATA',
}


def output_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or len(path.parts) < 2 or path.parts[0] != 'artifacts':
        raise ValueError('Output must be a directory below artifacts/')
    return path


def extract_output(archive: Path, destination: Path, output: PurePosixPath) -> None:
    def checked(member, target):
        name = PurePosixPath(member.name)
        if '..' in name.parts or (name != output and output not in name.parents):
            raise ValueError('Guest archive contains a path outside the selected output')
        if not (member.isfile() or member.isdir()):
            raise ValueError('Guest outputs must contain only regular files and directories')
        return tarfile.data_filter(member, target)

    with tarfile.open(archive) as package:
        package.extractall(destination, filter=checked)


def run_build(*, series: str, release: str, environment: str, output: str, script: str,
              checkout: Path, controller: Path) -> None:
    if series not in {'26.1', '26.7'} or not re.fullmatch(r'\d+\.\d+', release):
        raise ValueError('Unsupported builder series or FreeBSD release')
    selected = output_path(output)
    target = checkout.joinpath(*selected.parts)
    if target.exists() or target.is_symlink():
        raise ValueError('Guest output destination must not already exist')
    for parent in target.parents:
        if parent == checkout:
            break
        if parent.is_symlink():
            raise ValueError('Output destination cannot traverse a symlink')
    names = environment.split()
    if set(names) - BUILD_ENVIRONMENT:
        raise ValueError('Only explicit build metadata may be forwarded to the guest')
    exports = [f'{name}={os.environ[name]}' for name in names]
    if not script.strip():
        raise ValueError('Build script is empty')

    def client(operation, *command, **kwargs):
        return subprocess.run([sys.executable, str(controller), operation, series, *command],
                              check=True, **kwargs)

    original_error = None
    try:
        client('reset')
        actual = client('exec', '--', 'freebsd-version', '-u', capture_output=True, text=True).stdout.strip()
        if actual != f'{release}-RELEASE' and not actual.startswith(f'{release}-RELEASE-p'):
            raise ValueError('Guest release does not match the sealed controller profile')
        remote = client('exec', '--', 'mktemp', '-d', '/var/tmp/rp-build.XXXXXXXX',
                        capture_output=True, text=True).stdout.strip()
        if not re.fullmatch(r'/var/tmp/rp-build\.[A-Za-z0-9]+', remote):
            raise ValueError('Guest returned an invalid workspace path')
        with tempfile.TemporaryDirectory(prefix='rp-freebsd-') as temporary:
            temporary = Path(temporary)
            build_script = temporary / 'rp-build.sh'
            build_script.write_text(script, encoding='utf-8')
            incoming = temporary / 'source.tar'
            with tarfile.open(incoming, 'w') as archive:
                archive.add(checkout, arcname='.')
                archive.add(build_script, arcname='rp-build.sh')
            with incoming.open('rb') as source:
                client('exec', '--', 'tar', '-C', remote, '-xf', '-', stdin=source)
            command = f'cd {shlex.quote(remote)} && exec /bin/sh rp-build.sh'
            client('exec', '--', 'env', *exports, '/bin/sh', '-c', command)
            outgoing = temporary / 'output.tar'
            with outgoing.open('wb') as result:
                client('exec', '--', 'tar', '-C', remote, '-cf', '-', str(selected), stdout=result)
            unpacked = temporary / 'returned'
            unpacked.mkdir()
            extract_output(outgoing, unpacked, selected)
            produced = unpacked.joinpath(*selected.parts)
            if not produced.is_dir():
                raise ValueError('Guest did not produce the selected artifact directory')
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(produced, target)
    except BaseException as error:
        original_error = error
        raise
    finally:
        try:
            client('clean')
        except BaseException:
            if original_error is None:
                raise
            print('FreeBSD cleanup failed; the runner completion hook will retry.', file=sys.stderr)


def main() -> None:
    run_build(series=os.environ['RP_VM_SERIES'], release=os.environ['RP_VM_RELEASE'],
              environment=os.environ['RP_VM_ENVS'], output=os.environ['RP_VM_OUTPUT'],
              script=os.environ['RP_VM_SCRIPT'], checkout=Path.cwd().resolve(),
              controller=Path.home() / 'builderctl.py')


if __name__ == '__main__':
    main()
