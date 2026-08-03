import os
import pathlib
import subprocess


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[3]
BUILD_SCRIPT = REPOSITORY_ROOT / 'tools/ci/build-os-bind-rp.sh'


def test_build_wrapper_creates_package_and_metadata_for_26_1(tmp_path):
    environment = os.environ.copy()
    environment['MAKE_COMMAND'] = str(
        REPOSITORY_ROOT / 'tools/ci/tests/make-package-fixture.sh'
    )
    environment['PKG_COMMAND'] = str(
        REPOSITORY_ROOT / 'tools/ci/tests/pkg-build-fixture.sh'
    )
    environment['PKG_SWITCH_COMMAND'] = str(
        REPOSITORY_ROOT / 'tools/ci/tests/pkg-switch-fixture.sh'
    )
    environment['GIT_COMMAND'] = str(
        REPOSITORY_ROOT / 'tools/ci/tests/git-opnsense-core-fixture.sh'
    )
    environment['PKG_REPOS_DIR'] = str(tmp_path / 'repos')
    environment['PKG_FINGERPRINTS_DIR'] = str(tmp_path / 'fingerprints' / 'OPNsense')

    assert BUILD_SCRIPT.is_file(), 'non-publishing build wrapper is missing'
    result = subprocess.run(
        [BUILD_SCRIPT, '26.1', str(tmp_path)],
        cwd=REPOSITORY_ROOT,
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )

    assert result.returncode == 0, result.stderr
    assert (tmp_path / 'os-bind-rp-1.36_3.pkg').is_file()
    assert (tmp_path / 'repos' / 'OPNsense.conf').is_file()
    metadata = (tmp_path / 'build-metadata.txt').read_text()
    assert 'series=26.1\n' in metadata
    assert 'pkg_abi=FreeBSD:14:amd64\n' in metadata
    assert 'bind920=9.20.24\n' in metadata
    assert 'opnsense=26.1.11_10\n' in metadata
    assert 'switch_test=passed\n' in metadata
    assert 'opnsense_core_commit=fixture-opnsense-core-commit\n' in metadata
