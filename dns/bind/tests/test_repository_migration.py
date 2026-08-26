# Copyright (C) 2026 Bryan Wiegand <inbox@kw-ventures.com>
# All rights reserved.

import os
import pathlib
import subprocess

import pytest


BIND_ROOT = pathlib.Path(__file__).resolve().parents[1]
HOOK = BIND_ROOT / "+POST_INSTALL.pre"
ABI_URL = "https://resolver-plugins.github.io/repository/pkg/${ABI}/latest"
PUBLIC_KEY = "/usr/local/etc/pkg/keys/resolver-plugins.pub"


def repository_config(
    url,
    *,
    mirror_type="none",
    signature_type="pubkey",
    key=PUBLIC_KEY,
    enabled="yes",
):
    return (
        "resolver-plugins: {\n"
        f'  url: "{url}",\n'
        f'  mirror_type: "{mirror_type}",\n'
        f'  signature_type: "{signature_type}",\n'
        f'  pubkey: "{key}",\n'
        f"  enabled: {enabled}\n"
        "}\n"
    )


def run_hook(config):
    return subprocess.run(
        ["/bin/sh", str(HOOK)],
        env=os.environ | {"OS_BIND_RP_REPOSITORY_CONFIG": str(config)},
        text=True,
        capture_output=True,
        check=False,
    )


def test_repository_migration_hook_source_exists():
    assert HOOK.is_file()


@pytest.mark.parametrize("series", ("26.1", "26.7"))
def test_exact_legacy_repository_is_atomically_migrated(tmp_path, series):
    if not HOOK.is_file():
        pytest.skip("migration hook is not implemented")
    config = tmp_path / "resolver-plugins.conf"
    config.write_text(
        repository_config(
            f"https://github.com/resolver-plugins/repository/releases/download/pkg-{series}"
        ),
        encoding="utf-8",
    )
    config.chmod(0o640)
    original = config.stat()

    result = run_hook(config)

    assert result.returncode == 0, result.stderr
    assert config.read_text(encoding="utf-8") == repository_config(ABI_URL)
    migrated = config.stat()
    assert migrated.st_ino != original.st_ino
    assert migrated.st_mode == original.st_mode
    assert migrated.st_uid == original.st_uid
    assert migrated.st_gid == original.st_gid
    assert not list(tmp_path.glob(".resolver-plugins.conf.*"))
    assert "manual migration" not in result.stderr


@pytest.mark.parametrize(
    "contents",
    (
        repository_config("https://packages.example.test/custom"),
        repository_config(
            "https://github.com/resolver-plugins/repository/releases/download/pkg-26.1",
            key="/usr/local/etc/pkg/keys/custom.pub",
        ),
        repository_config(
            "https://github.com/resolver-plugins/repository/releases/download/pkg-26.1",
            enabled="no",
        ),
        repository_config(
            "https://github.com/resolver-plugins/repository/releases/download/pkg-26.1",
            mirror_type="srv",
        ),
        repository_config(
            "https://github.com/resolver-plugins/repository/releases/download/pkg-26.1",
            signature_type="none",
        ),
    ),
    ids=(
        "custom-url",
        "alternate-key",
        "disabled",
        "different-mirror",
        "alternate-signature",
    ),
)
def test_custom_repository_is_byte_identical_and_warns(tmp_path, contents):
    if not HOOK.is_file():
        pytest.skip("migration hook is not implemented")
    config = tmp_path / "resolver-plugins.conf"
    original = contents.encode()
    config.write_bytes(original)

    result = run_hook(config)

    assert result.returncode == 0
    assert config.read_bytes() == original
    assert "manual migration" in result.stderr
    assert ABI_URL in result.stderr


def test_symlink_is_not_followed_and_warns(tmp_path):
    if not HOOK.is_file():
        pytest.skip("migration hook is not implemented")
    target = tmp_path / "custom.conf"
    original = repository_config(
        "https://github.com/resolver-plugins/repository/releases/download/pkg-26.1"
    ).encode()
    target.write_bytes(original)
    config = tmp_path / "resolver-plugins.conf"
    config.symlink_to(target.name)

    result = run_hook(config)

    assert result.returncode == 0
    assert config.is_symlink()
    assert os.readlink(config) == target.name
    assert target.read_bytes() == original
    assert "manual migration" in result.stderr


def test_non_regular_path_is_untouched_and_warns(tmp_path):
    if not HOOK.is_file():
        pytest.skip("migration hook is not implemented")
    config = tmp_path / "resolver-plugins.conf"
    config.mkdir()
    marker = config / "custom"
    marker.write_bytes(b"leave me alone\n")

    result = run_hook(config)

    assert result.returncode == 0
    assert config.is_dir()
    assert marker.read_bytes() == b"leave me alone\n"
    assert "manual migration" in result.stderr
