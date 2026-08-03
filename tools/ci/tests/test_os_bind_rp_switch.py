import os
import pathlib
import subprocess


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[3]
SWITCH_SCRIPT = REPOSITORY_ROOT / 'tools/ci/test-os-bind-rp-switch.sh'
PKG_FIXTURE = REPOSITORY_ROOT / 'tools/ci/tests/pkg-switch-fixture.sh'


def test_switch_script_rejects_coinstallation_then_completes_manual_swap(tmp_path):
    official_package = tmp_path / 'os-bind-1.34_3.pkg'
    rp_package = tmp_path / 'os-bind-rp-1.36_1.pkg'
    official_package.touch()
    rp_package.touch()
    environment = os.environ.copy()
    environment['PKG_COMMAND'] = str(PKG_FIXTURE)

    result = subprocess.run(
        ['sh', SWITCH_SCRIPT, str(official_package), str(rp_package)],
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )

    assert result.returncode == 0, result.stderr
    assert 'package switch verified' in result.stdout
