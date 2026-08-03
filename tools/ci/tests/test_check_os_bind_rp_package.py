import os
import pathlib
import subprocess
import tarfile


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[3]
CHECK_SCRIPT = REPOSITORY_ROOT / 'tools/ci/check-os-bind-rp-package.sh'


def create_package(path: pathlib.Path, manifest: str) -> None:
    manifest_path = path.parent / '+MANIFEST'
    manifest_path.write_text(manifest)

    with tarfile.open(path, 'w') as package:
        package.add(manifest_path, arcname='+MANIFEST')


def test_accepts_manifest_with_required_identity_conflict_and_bind_version(tmp_path):
    package = tmp_path / 'os-bind-rp-1.36_1.pkg'
    create_package(
        package,
        '\n'.join(
            [
                'name: os-bind-rp',
                'version: "1.36_1"',
                'conflicts: [ "os-bind" ]',
                'deps: {',
                '  bind920: { version: "9.20.26", origin: "dns/bind920" }',
                '}',
                '',
            ]
        ),
    )
    environment = os.environ.copy()
    environment['PKG_COMMAND'] = str(
        REPOSITORY_ROOT / 'tools/ci/tests/pkg-version-equal.sh'
    )

    assert CHECK_SCRIPT.is_file(), 'package-inspection script is missing'
    result = subprocess.run(
        [CHECK_SCRIPT, str(package)],
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )

    assert result.returncode == 0, result.stderr


def test_rejects_manifest_with_bind_below_dot_minimum(tmp_path):
    package = tmp_path / 'os-bind-rp-1.36_1.pkg'
    create_package(
        package,
        '\n'.join(
            [
                'name: os-bind-rp',
                'version: "1.36_1"',
                'conflicts: [ "os-bind" ]',
                'deps: {',
                '  bind920: { version: "9.20.25", origin: "dns/bind920" }',
                '}',
                '',
            ]
        ),
    )
    environment = os.environ.copy()
    environment['PKG_COMMAND'] = str(
        REPOSITORY_ROOT / 'tools/ci/tests/pkg-version-less.sh'
    )

    result = subprocess.run(
        [CHECK_SCRIPT, str(package)],
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )

    assert result.returncode != 0
    assert 'below the required 9.20.26' in result.stderr
