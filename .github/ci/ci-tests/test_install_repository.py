"""Exercise repository bootstrap with local commands and no package mutations."""
from common_imports import *

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize('series,abi,fault', [
    ('26.1', 'FreeBSD:14:amd64', ''),
    ('26.7', 'FreeBSD:15:amd64', ''),
    ('26.7', 'FreeBSD:15:amd64', 'key'),
    ('26.7', 'FreeBSD:15:amd64', 'fetch'),
    ('26.7', 'FreeBSD:15:amd64', 'update'),
])
def test_repository_setup_verifies_key_and_only_refreshes_catalogue(tmp_path, series, abi, fault):
    key = tmp_path / 'keys/resolver-plugins.pub'
    config = tmp_path / 'repos/resolver-plugins.conf'
    for path in (key, config):
        path.parent.mkdir()
        path.write_text('existing trusted configuration')
    candidate = tmp_path / 'candidate.pub'
    trusted = (ROOT / 'docs/package-repository/resolver-plugins.pub').read_bytes()
    candidate.write_bytes(b'foreign key' if fault == 'key' else trusted)
    log = tmp_path / 'commands.log'
    commands = {
        'opnsense-version': '[ "$*" = -a ] || exit 64\nprintf "%s\\n" "$RP_TEST_SERIES"',
        'sha256': 'sha256sum "$2" | cut -d " " -f 1',
        'fetch': '''printf 'fetch %s\n' "$*" >> "$RP_TEST_LOG"
[ "$1" = -o ] || exit 64
cp "$RP_TEST_KEY" "$2"
[ "$RP_TEST_FAULT" != fetch ]''',
        'pkg': '''printf 'pkg %s\n' "$*" >> "$RP_TEST_LOG"
case "$*" in
    'config ABI') printf '%s\n' "$RP_TEST_ABI";;
    "-o REPOS_DIR=$RP_PKG_REPOSITORY_DIR update -r resolver-plugins") [ "$RP_TEST_FAULT" != update ];;
    *) exit 64;;
esac''',
    }
    fixture_root = ROOT / '.github/ci-local'
    fixture_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=fixture_root) as directory:
        for name, body in commands.items():
            command = Path(directory) / name
            command.write_text('#!/bin/sh\nset -eu\n' + body + '\n')
            command.chmod(0o755)
        env = dict(os.environ, PATH=f'{directory}:{os.environ["PATH"]}',
                   RP_PKG_REPOSITORY_DIR=str(config.parent), RP_PKG_KEYS_DIR=str(key.parent),
                   RP_TEST_SERIES=series, RP_TEST_ABI=abi, RP_TEST_FAULT=fault,
                   RP_TEST_KEY=str(candidate), RP_TEST_LOG=str(log))
        result = subprocess.run(['sh', ROOT / 'scripts/install-repository.sh'],
                                env=env, text=True, capture_output=True)
    calls = log.read_text().splitlines()
    assert calls[0] == 'pkg config ABI'
    assert calls[1].endswith(f'https://resolver-plugins.github.io/repository/pkg/{abi}/{series}/latest/resolver-plugins.pub')
    assert (result.returncode == 0) == (not fault), result.stderr
    if fault in ('key', 'fetch'):
        assert len(calls) == 2
        assert key.read_text() == config.read_text() == 'existing trusted configuration'
        if fault == 'key':
            assert 'fingerprint verification failed' in result.stderr
    else:
        assert calls[2:] == [f'pkg -o REPOS_DIR={config.parent} update -r resolver-plugins']
        assert key.read_bytes() == trusted
        assert config.read_text() == (
            'resolver-plugins: {\n'
            f'  url: "https://resolver-plugins.github.io/repository/pkg/${{ABI}}/{series}/latest",\n'
            '  mirror_type: "none",\n  signature_type: "pubkey",\n'
            f'  pubkey: "{key}",\n  enabled: yes\n}}\n'
        )
