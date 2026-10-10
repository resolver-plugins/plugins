"""Small real Git repositories for CI tests; no network or host identity required."""
from common_imports import *


def git(directory, *arguments):
    return subprocess.run(['git', '-C', directory, *arguments], check=True,
                          text=True, capture_output=True).stdout.strip()


def init_repository(path):
    git(path.parent, 'init', '--initial-branch=master', path)
    git(path, 'config', 'user.name', 'CI fixture')
    git(path, 'config', 'user.email', 'ci@example.invalid')
    return path


def commit(directory, files, message):
    for name, contents in files.items():
        destination = directory / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(contents, encoding='utf-8')
    git(directory, 'add', *files)
    git(directory, 'commit', '-m', message)
    return git(directory, 'rev-parse', 'HEAD')


def upstream_profile(series='26.1', upstream_commit='1' * 40, *, core_commit='2' * 40,
                     freebsd_release=None, tools_tag=None, archive_sha256='a' * 64):
    return dict(series=series, upstream_branch=f'stable/{series}', upstream_commit=upstream_commit,
                tools_tag=tools_tag or {'26.1': '26.1.11', '26.7': '26.7.1'}[series],
                freebsd_release=freebsd_release or {'26.1': '14.3', '26.7': '15.1'}[series],
                core_commit=core_commit, core_archive_sha256=archive_sha256,
                core_archive_url=f'https://github.com/opnsense/core/archive/{core_commit}.tar.gz')


def write_upstream_metadata(path, core_commit, archive_sha256='a' * 64):
    path.write_text(json.dumps(upstream_profile(core_commit=core_commit, archive_sha256=archive_sha256)))


def create_core_repository(path):
    init_repository(path)
    return commit(path, {
        'src/etc/pkg/repos/OPNsense.conf.shadow.in':
            'OPNsense: {\n  url: "%%CORE_PACKAGESITE%%/${ABI}/%%CORE_ABI%%/latest",\n'
            '  signature_type: "fingerprints",\n  enabled: yes\n}\n',
        'src/etc/pkg/fingerprints/OPNsense/trusted/pkg.opnsense.org.fixture':
            'function: "sha256"\nfingerprint: "fixture"\n',
    }, 'fixture core')
