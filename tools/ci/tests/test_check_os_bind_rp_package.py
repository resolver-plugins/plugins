import json
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


def test_accepts_manifest_with_core_version_floor_and_no_official_conflict(tmp_path):
    package = tmp_path / 'os-bind-rp-1.36_1.pkg'
    create_package(
        package,
        '\n'.join(
            [
                'name: os-bind-rp',
                'version: "1.36_1"',
                'deps: {',
                '  bind920: { version: "9.20.24", origin: "dns/bind920" }',
                '  opnsense: { version: "26.1.11_10", origin: "opnsense/opnsense" }',
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


def test_accepts_compact_json_manifest_from_pkg_create(tmp_path):
    package = tmp_path / 'os-bind-rp-1.36_1.pkg'
    create_package(
        package,
        json.dumps(
            {
                'name': 'os-bind-rp',
                'version': '1.36_1',
                'deps': {
                    'bind920': {
                        'version': '9.20.24',
                        'origin': 'dns/bind920',
                    },
                    'opnsense': {
                        'version': '26.1.11_10',
                        'origin': 'opnsense/opnsense',
                    },
                },
            },
            separators=(',', ':'),
        ),
    )
    environment = os.environ.copy()
    environment['PKG_COMMAND'] = str(
        REPOSITORY_ROOT / 'tools/ci/tests/pkg-version-equal.sh'
    )

    result = subprocess.run(
        [CHECK_SCRIPT, str(package)],
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )

    assert result.returncode == 0, result.stderr


def test_rejects_manifest_that_retains_an_official_plugin_conflict(tmp_path):
    package = tmp_path / 'os-bind-rp-1.36_1.pkg'
    create_package(
        package,
        '\n'.join(
            [
                'name: os-bind-rp',
                'version: "1.36_1"',
                'conflicts: [ "os-bind" ]',
                'deps: {',
                '  bind920: { version: "9.20.24", origin: "dns/bind920" }',
                '  opnsense: { version: "26.1.11_10", origin: "opnsense/opnsense" }',
                '}',
                '',
            ]
        ),
    )
    environment = os.environ.copy()
    result = subprocess.run(
        [CHECK_SCRIPT, str(package)],
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )

    assert result.returncode != 0
    assert 'must not declare an os-bind conflict' in result.stderr


def test_rejects_manifest_with_opnsense_below_required_core_version(tmp_path):
    package = tmp_path / 'os-bind-rp-1.36_1.pkg'
    create_package(
        package,
        '\n'.join(
            [
                'name: os-bind-rp',
                'version: "1.36_1"',
                'deps: {',
                '  bind920: { version: "9.20.24", origin: "dns/bind920" }',
                '  opnsense: { version: "26.1.11_9", origin: "opnsense/opnsense" }',
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
    assert 'below the required 26.1.11_10' in result.stderr
