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


def bind_sync_repositories(tmp_path):
    """Source/control/tools objects for the actual discovery-to-review CLI."""
    tools = init_repository(tmp_path / 'tools')
    for series, releases in (('26.1', [('26.1', '14.2'), ('26.1.11', '14.3')]),
                             ('26.7', [('26.7', '15.0'), ('26.7.1', '15.1')])):
        for tag, release in releases:
            commit(tools, {f'config/{series}/build.conf': f'OS?= {release} # upstream\n'}, tag)
            git(tools, 'tag', tag)
        commit(tools, {f'config/{series}/build.conf': 'OS?=99.9\n'}, 'prerelease')
        for suffix in ('b', 'r1'):
            git(tools, 'tag', f'{series}.{suffix}')
    repository = init_repository(tmp_path / 'repository')
    initial = commit(repository, {'dns/bind/bind.conf': 'bind-v1\n',
                                 'Mk/plugins.mk': 'native framework\n',
                                 'README': 'upstream\n'}, 'upstream 26.1')
    git(repository, 'update-ref', 'refs/remotes/upstream/stable/26.1', initial)
    stable_26_7 = commit(repository, {'README': 'unrelated 26.7\n'}, 'upstream 26.7')
    git(repository, 'update-ref', 'refs/remotes/upstream/stable/26.7', stable_26_7)
    stable_27_1 = commit(repository, {'dns/bind/bind.conf': 'bind-v2\n'}, 'upstream BIND change')
    git(repository, 'update-ref', 'refs/remotes/upstream/stable/27.1', stable_27_1)
    git(repository, 'checkout', '-B', 'release/bind-rp/26.1', initial)
    source_commit = commit(repository, {
        'dns/bind/fork.conf': 'resolver overlay\n',
        'Mk/plugins.mk': 'native framework\nfork dependency formula\n',
        'dns/bind/tests/canonical-only.py': 'do not carry tests\n',
        'tools/not-an-overlay.txt': 'do not carry source CI\n',
    }, 'reviewed release 26.1')
    git(repository, 'checkout', 'master')
    return dict(repository=repository, tools=tools, initial=initial,
                stable_26_7=stable_26_7, stable_27_1=stable_27_1,
                source_commit=source_commit)


def discover_sync(fixture, planner):
    result = subprocess.run([
        'python3', str(planner), 'discover', '--repository', str(fixture['repository']),
        '--upstream', 'upstream', '--release-prefix', 'release/bind-rp/',
        '--tools-repository', str(fixture['tools']),
        '--control-commit', git(fixture['repository'], 'rev-parse', 'HEAD'),
    ], check=True, text=True, capture_output=True)
    return json.loads(result.stdout)


def seal_sync(discovery, tmp_path, planner, *, core_commit='2' * 40):
    discovery_path = tmp_path / 'discovery.json'
    plan_path = tmp_path / 'plan.json'
    discovery_path.write_text(json.dumps(discovery))
    subprocess.run([
        'python3', str(planner), 'seal-plan', '--discovery', str(discovery_path),
        '--core-commit', core_commit,
        '--core-archive-url', f'https://github.com/opnsense/core/archive/{core_commit}.tar.gz',
        '--core-archive-sha256', 'a' * 64, '--output', str(plan_path),
    ], check=True, text=True, capture_output=True)
    return json.loads(plan_path.read_text())


def apply_sync(fixture, plan, tmp_path, planner, environment=None):
    plan_path = tmp_path / 'plan.json'
    plan_path.write_text(json.dumps(plan))
    return subprocess.run([
        'python3', str(planner), 'apply', '--repository', str(fixture['repository']),
        '--plan', str(plan_path), '--tools-repository', str(fixture['tools']),
    ], text=True, capture_output=True, check=False,
       env={**os.environ, **(environment or {})})
