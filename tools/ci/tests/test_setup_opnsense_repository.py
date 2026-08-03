import os
import pathlib
import subprocess


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[3]
SETUP_SCRIPT = REPOSITORY_ROOT / 'tools/ci/setup-opnsense-repository.sh'


def test_installs_matching_opnsense_repository_configuration(tmp_path):
    repository_directory = tmp_path / 'repos'
    fingerprint_directory = tmp_path / 'fingerprints' / 'OPNsense'
    environment = os.environ.copy()
    environment['GIT_COMMAND'] = str(
        REPOSITORY_ROOT / 'tools/ci/tests/git-opnsense-core-fixture.sh'
    )
    environment['PKG_REPOS_DIR'] = str(repository_directory)
    environment['PKG_FINGERPRINTS_DIR'] = str(fingerprint_directory)

    assert SETUP_SCRIPT.is_file(), 'OPNsense repository setup script is missing'
    result = subprocess.run(
        [SETUP_SCRIPT, '26.1'],
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == 'fixture-opnsense-core-commit\n'
    repository_config = (repository_directory / 'OPNsense.conf').read_text()
    assert 'https://pkg.opnsense.org/${ABI}/26.1/latest' in repository_config
    assert (
        fingerprint_directory / 'trusted/pkg.opnsense.org.fixture'
    ).is_file()
