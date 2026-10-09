from git_fixtures import *

from package_fixtures import package_creator, write_target_metadata


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[3]
UPSTREAM_COMMIT = '1' * 40
FREEBSD_RELEASE = '14.3'
TARGET_ARCHIVE_BYTES = b'fixture target package archive\n'
TARGET_STATIC_BYTES = (pathlib.Path(__file__).with_name('pkg-static-fixture.sh')).read_bytes()


def configure_target_pkg_fixture(
    environment: dict[str, str], directory: pathlib.Path, executable_directory: pathlib.Path
) -> None:
    metadata = directory / 'target-pkg.json'
    write_target_metadata(metadata, package_creator(
        sha256=hashlib.sha256(TARGET_ARCHIVE_BYTES).hexdigest(),
        pkg_static_sha256=hashlib.sha256(TARGET_STATIC_BYTES).hexdigest(),
    ))
    environment['RP_TARGET_PKG_METADATA'] = str(metadata)
    environment['RP_PKG_STATIC_COMMAND'] = str(executable_directory / 'pkg-static')
    environment['PKG_STATIC_PATH'] = str(executable_directory / 'pkg-static')
    environment['PKG_LOCK_MARKER'] = str(directory / 'pkg.locked')
    environment['PKG_STATIC_CALL_LOG'] = str(directory / 'pkg-static-calls.log')


def materialize_build_repository(request) -> pathlib.Path:
    local_tests = REPOSITORY_ROOT / '.github/ci-local'
    local_tests.mkdir(exist_ok=True)
    build_repository = pathlib.Path(
        tempfile.mkdtemp(prefix='build-os-bind-rp-', dir=local_tests)
    )
    request.addfinalizer(lambda: shutil.rmtree(build_repository, ignore_errors=True))
    for name in ('.github/ci', 'dns/bind', '.resolver-plugins'):
        shutil.copytree(REPOSITORY_ROOT / name, build_repository / name,
                        ignore=shutil.ignore_patterns('ci-local', 'work', '__pycache__', '.pytest_cache'))
    return build_repository


def set_plugin_version(build_repository: pathlib.Path, version: str, revision: str) -> None:
    makefile = build_repository / 'dns/bind/Makefile'
    lines = []
    for line in makefile.read_text(encoding='utf-8').splitlines(keepends=True):
        if line.startswith('PLUGIN_VERSION='):
            lines.append(f'PLUGIN_VERSION=\t\t{version}\n')
        elif line.startswith('PLUGIN_REVISION='):
            lines.append(f'PLUGIN_REVISION=\t{revision}\n')
        else:
            lines.append(line)
    makefile.write_text(''.join(lines), encoding='utf-8')


def build_environment(
    tmp_path: pathlib.Path, build_repository: pathlib.Path, core: pathlib.Path
) -> dict[str, str]:
    core_commit = create_core_repository(core)
    environment = os.environ.copy()
    environment.pop('RP_OPNSENSE_SNAPSHOT', None)
    environment.pop('SOURCE_COMMIT', None)
    environment['MAKE_COMMAND'] = str(
        build_repository / '.github/ci/ci-tests/make-package-fixture.sh'
    )
    environment['PKG_COMMAND'] = str(
        build_repository / '.github/ci/ci-tests/pkg-build-fixture.sh'
    )
    environment['PYTHON_COMMAND'] = 'python3'
    environment['GIT_CONFIG_GLOBAL'] = str(tmp_path / 'gitconfig')
    environment['OPNSENSE_CORE_REPOSITORY'] = str(core)
    environment['PKG_REPOS_DIR'] = str(tmp_path / 'repos')
    environment['PKG_FINGERPRINTS_DIR'] = str(tmp_path / 'fingerprints' / 'OPNsense')
    metadata_path = tmp_path / 'upstream.json'
    write_upstream_metadata(metadata_path, core_commit)
    environment['RP_UPSTREAM_METADATA'] = str(metadata_path)
    package_call_log = tmp_path / 'pkg-calls.log'
    environment['PKG_CALL_LOG'] = str(package_call_log)
    configure_target_pkg_fixture(environment, tmp_path, build_repository)
    return environment


def test_build_wrapper_creates_package_and_metadata_for_26_1(tmp_path, request):
    build_repository = materialize_build_repository(request)
    set_plugin_version(build_repository, '26.1', '1')
    build_script = build_repository / '.github/ci/bind/build-os-bind-rp.sh'
    core = tmp_path / 'core'
    environment = build_environment(tmp_path, build_repository, core)
    core_commit = git(core, 'rev-parse', 'HEAD')
    python_command = build_repository / 'python3-fixture'
    environment['PYTHON_COMMAND'] = str(python_command)
    package_call_log = pathlib.Path(environment['PKG_CALL_LOG'])

    result = subprocess.run(
        [build_script, '26.1', str(tmp_path)],
        cwd=build_repository,
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )

    assert result.returncode == 0, result.stderr
    assert python_command.is_file()
    assert (tmp_path / 'os-bind-rp-26.1_1.pkg').is_file()
    assert (tmp_path / 'repos' / 'OPNsense.conf').is_file()
    metadata = dict(line.split('=', 1) for line in (tmp_path / 'build-metadata.txt').read_text().splitlines())
    metadata.pop('uname')  # Host description varies; the package identity and build pins must match exactly.
    assert metadata == {
        'series': '26.1', 'pkg_abi': 'FreeBSD:14:amd64', 'bind920': '9.20.26',
        'bind_source': 'opnsense', 'opnsense': '26.1.11_10', 'opnsense_core_commit': core_commit,
        'upstream_commit': UPSTREAM_COMMIT, 'core_commit': core_commit, 'tools_tag': '26.1.11',
        'freebsd_release': FREEBSD_RELEASE, 'source_commit': 'unknown', 'pkg_creator': '2.3.1_1',
        'pkg_creator_sha256': hashlib.sha256(TARGET_ARCHIVE_BYTES).hexdigest(),
    }
    package_calls = package_call_log.read_text().splitlines()
    assert 'update -f' in package_calls
    assert 'install -y python3' in package_calls
    assert 'install -y git' in package_calls
    assert 'install -y bind920' in package_calls
    target_fetch = next(call for call in package_calls if call.startswith('fetch '))
    assert target_fetch.endswith('pkg-2.3.1_1')
    target_add_index = next(
        index for index, call in enumerate(package_calls)
        if call.startswith('add -f ') and 'pkg-2.3.1_1.pkg' in call
    )
    assert target_add_index < package_calls.index('install -y bind920')
    build_index = package_calls.index('make package')
    assert package_calls[build_index - 1] == 'lock -l'
    assert package_calls[build_index + 1:build_index + 3] == [
        'query -e %n = pkg %n|%v|%o|%q', 'lock -l',
    ]
    static_calls = pathlib.Path(environment['PKG_STATIC_CALL_LOG']).read_text().splitlines()
    assert any(
        call.startswith('query -F ') and call.endswith(' %Fp|%Fs')
        for call in static_calls
    )
    safe_directories = subprocess.run(
        ['git', 'config', '--global', '--get-all', 'safe.directory'],
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )
    assert safe_directories.returncode == 0, safe_directories.stderr
    assert build_repository.as_posix() in safe_directories.stdout.splitlines()


def test_build_wrapper_requires_plugin_version_to_match_release_series(tmp_path, request):
    build_repository = materialize_build_repository(request)
    environment = build_environment(tmp_path, build_repository, tmp_path / 'core')
    cases = (
        ('26.1', '12', True),
        ('26.7', '1', False),
        ('26.1', '0', False),
        ('1.36', '12', False),
        ('26.1', '1x', False),
    )

    for version, revision, allowed in cases:
        case_path = tmp_path / f'{version}_{revision}'
        case_path.mkdir()
        set_plugin_version(build_repository, version, revision)
        case_environment = dict(environment, PKG_CALL_LOG=str(case_path / 'pkg-calls.log'))
        configure_target_pkg_fixture(case_environment, case_path, build_repository)
        result = subprocess.run(
            [build_repository / '.github/ci/bind/build-os-bind-rp.sh',
             '26.1', str(case_path / 'artifacts')],
            cwd=build_repository,
            text=True,
            capture_output=True,
            check=False,
            env=case_environment,
        )
        artifact = case_path / 'artifacts' / f'os-bind-rp-{version}_{revision}.pkg'
        if allowed:
            assert result.returncode == 0, result.stderr
            assert artifact.is_file()
        else:
            assert result.returncode != 0
            assert not artifact.exists()
            assert 'plugin version' in result.stderr


def test_build_wrapper_requests_resolver_fallback_for_an_ineligible_opnsense_bind(tmp_path, request):
    build_repository = materialize_build_repository(request)
    build_script = build_repository / '.github/ci/bind/build-os-bind-rp.sh'
    environment = build_environment(tmp_path, build_repository, tmp_path / 'core')
    for overrides in (
        {'PKG_VERSION_COMPARISON': '<'},
        {'PKG_BIND_TOOLS_ORIGIN': 'resolver/bind-tools'},
    ):
        result = subprocess.run(
            [build_script, '26.1', str(tmp_path / 'artifacts')],
            cwd=build_repository,
            text=True,
            capture_output=True,
            check=False,
            env=dict(environment, **overrides),
        )
        assert result.returncode == 3, (overrides, result.stderr)
        assert 'make package' not in pathlib.Path(environment['PKG_CALL_LOG']).read_text().splitlines()
