from git_fixtures import *

import pytest


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[3]
METADATA_PROFILE = pathlib.Path(
    os.environ.get(
        'METADATA_PROFILE', REPOSITORY_ROOT / '.github/ci/shared/metadata_profile.py'
    )
)
UPSTREAM_COMMIT = 'a' * 40
CORE_COMMIT = 'b' * 40
CORE_ARCHIVE_SHA256 = 'c' * 64


@pytest.mark.parametrize(
    ('field', 'invalid_value'),
    (
        (None, None),
        ('core_commit', 'refs/heads/stable/26.1'),
        ('core_commit', CORE_COMMIT.upper()),
        ('upstream_commit', 'refs/heads/stable/26.1'),
        ('upstream_commit', UPSTREAM_COMMIT.upper()),
        ('core_archive_sha256', 'not-a-sha256'),
        ('core_archive_sha256', CORE_ARCHIVE_SHA256.upper()),
        ('upstream_branch', 'stable/26.7'),
        ('tools_tag', '26.7.1'),
        ('tools_tag', '26.1.r1'),
        ('freebsd_release', 'not-a-release'),
    ),
)
def test_cli_validates_strict_profile_fields(tmp_path, field, invalid_value):
    profile = upstream_profile(upstream_commit=UPSTREAM_COMMIT, core_commit=CORE_COMMIT,
                               archive_sha256=CORE_ARCHIVE_SHA256)
    if field:
        profile[field] = invalid_value
    if field == 'core_commit':
        profile['core_archive_url'] = (
            f'https://github.com/opnsense/core/archive/{invalid_value}.tar.gz'
        )
    metadata_path = tmp_path / 'upstream.json'
    metadata_path.write_text(json.dumps(profile))

    result = subprocess.run(
        ['python3', METADATA_PROFILE, metadata_path, '26.1', 'core_commit'],
        text=True,
        capture_output=True,
        check=False,
    )

    if field is None:
        assert result.returncode == 0, result.stderr
        assert result.stdout == CORE_COMMIT + '\n'
    else:
        assert result.returncode != 0
        assert ('branch' if field == 'upstream_branch' else field) in result.stderr
        assert result.stdout == ''
