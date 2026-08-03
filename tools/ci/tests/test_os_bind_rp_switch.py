import io
import os
import pathlib
import subprocess
import tarfile


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[3]
SWITCH_SCRIPT = REPOSITORY_ROOT / 'tools/ci/test-os-bind-rp-switch.sh'
PKG_FIXTURE = REPOSITORY_ROOT / 'tools/ci/tests/pkg-switch-fixture.sh'


def create_package(path: pathlib.Path, name: str) -> None:
    manifest = (
        '{"name":"%s","version":"1","origin":"fixture/fixture"}' % name
    ).encode()
    with tarfile.open(path, 'w') as archive:
        entry = tarfile.TarInfo('+MANIFEST')
        entry.size = len(manifest)
        archive.addfile(entry, io.BytesIO(manifest))


def test_switch_script_rejects_coinstallation_then_completes_manual_swap(tmp_path):
    official_package = tmp_path / 'os-bind-1.34_3.pkg'
    bind_package = tmp_path / 'bind920-9.20.24.pkg'
    core_package = tmp_path / 'opnsense-26.1.11_10.pkg'
    rp_package = tmp_path / 'os-bind-rp-1.36_1.pkg'
    create_package(official_package, 'os-bind')
    create_package(bind_package, 'bind920')
    create_package(core_package, 'opnsense')
    create_package(rp_package, 'os-bind-rp')
    environment = os.environ.copy()
    environment['PKG_COMMAND'] = str(PKG_FIXTURE)

    result = subprocess.run(
        [
            'sh',
            SWITCH_SCRIPT,
            str(official_package),
            str(bind_package),
            str(core_package),
            str(rp_package),
        ],
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )

    assert result.returncode == 0, result.stderr
    assert 'package switch verified' in result.stdout
