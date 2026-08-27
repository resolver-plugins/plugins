# Copyright (C) 2026 Bryan Wiegand <inbox@kw-ventures.com>
# All rights reserved.

import os
import pathlib
import re
import subprocess

import pytest


BIND_ROOT = pathlib.Path(__file__).resolve().parents[1]
MAKEFILE = BIND_ROOT / "Makefile"
PROVIDER = BIND_ROOT / "src/opnsense/scripts/firmware/repos/ResolverPlugins.sh"
PUBLIC_KEY = "/usr/local/etc/pkg/keys/resolver-plugins.pub"
PENDING_MARKER = "repository-reconcile.pending"


def plugin_source_is_series_versioned():
    text = MAKEFILE.read_text(encoding="utf-8")
    version = re.search(r"^PLUGIN_VERSION=\s*([^\s]+)", text, re.MULTILINE)
    revision = re.search(r"^PLUGIN_REVISION=\s*([^\s]+)", text, re.MULTILINE)
    if version is None or revision is None:
        return False
    return version.group(1) in {"26.1", "26.7"} and (
        re.fullmatch(r"[1-9][0-9]*", revision.group(1)) is not None
    )


pytestmark = pytest.mark.skipif(
    not plugin_source_is_series_versioned(),
    reason="repository provider contract applies to series-versioned release sources",
)


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


def series_url(series):
    return f"https://resolver-plugins.github.io/repository/pkg/${{ABI}}/{series}/latest"


def write_opnsense_version(path, series):
    path.write_text(
        "#!/bin/sh\n"
        "[ \"$1\" = -a ] || exit 64\n"
        f"printf '%s\\n' '{series}'\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def run_provider(tmp_path, config, *, series="26.7"):
    opnsense_version = tmp_path / "opnsense-version"
    write_opnsense_version(opnsense_version, series)
    marker = tmp_path / PENDING_MARKER
    return subprocess.run(
        ["/bin/sh", str(PROVIDER)],
        env=os.environ
        | {
            "OS_BIND_RP_REPOSITORY_CONFIG": str(config),
            "OS_BIND_RP_PENDING_MARKER": str(marker),
            "OS_BIND_RP_OPNSENSE_VERSION_COMMAND": f"/bin/sh {opnsense_version}",
        },
        text=True,
        capture_output=True,
        check=False,
        timeout=5,
    ), marker


def test_repository_provider_source_exists_and_is_executable():
    assert PROVIDER.is_file()
    assert os.access(PROVIDER, os.X_OK)


@pytest.mark.parametrize(
    "old_url",
    (
        "https://github.com/resolver-plugins/repository/releases/download/pkg-26.1",
        "https://github.com/resolver-plugins/repository/releases/download/pkg-26.7",
        "https://resolver-plugins.github.io/repository/pkg/${ABI}/latest",
        "https://resolver-plugins.github.io/repository/pkg/${ABI}/26.1/latest",
    ),
)
def test_managed_repository_is_atomically_switched_to_current_series(tmp_path, old_url):
    config = tmp_path / "resolver-plugins.conf"
    config.write_text(repository_config(old_url), encoding="utf-8")
    config.chmod(0o640)
    original = config.stat()

    result, marker = run_provider(tmp_path, config, series="26.7")

    assert result.returncode == 0, result.stderr
    assert config.read_text(encoding="utf-8") == repository_config(series_url("26.7"))
    migrated = config.stat()
    assert migrated.st_ino != original.st_ino
    assert migrated.st_mode == original.st_mode
    assert migrated.st_uid == original.st_uid
    assert migrated.st_gid == original.st_gid
    assert marker.is_file()
    assert "manual migration" not in result.stderr


def test_current_series_repository_is_left_inode_identical_without_marker(tmp_path):
    config = tmp_path / "resolver-plugins.conf"
    original = repository_config(series_url("26.7")).encode()
    config.write_bytes(original)
    before = config.stat()

    result, marker = run_provider(tmp_path, config, series="26.7")

    assert result.returncode == 0
    assert result.stderr == ""
    assert config.read_bytes() == original
    assert config.stat().st_ino == before.st_ino
    assert not marker.exists()


def test_malformed_product_abi_is_refused_without_mutation(tmp_path):
    config = tmp_path / "resolver-plugins.conf"
    original = repository_config(series_url("26.1")).encode()
    config.write_bytes(original)

    result, marker = run_provider(tmp_path, config, series="26.7/../../latest")

    assert result.returncode == 0
    assert config.read_bytes() == original
    assert not marker.exists()
    assert "manual migration" in result.stderr


@pytest.mark.parametrize(
    "contents",
    (
        repository_config("https://packages.example.test/custom"),
        repository_config(series_url("26.1"), key="/usr/local/etc/pkg/keys/custom.pub"),
        repository_config(series_url("26.1"), enabled="no"),
        repository_config(series_url("26.1"), mirror_type="srv"),
        repository_config(series_url("26.1"), signature_type="none"),
    ),
)
def test_custom_repository_is_byte_and_inode_identical_without_marker(tmp_path, contents):
    config = tmp_path / "resolver-plugins.conf"
    original = contents.encode()
    config.write_bytes(original)
    before = config.stat()

    result, marker = run_provider(tmp_path, config)

    assert result.returncode == 0
    assert config.read_bytes() == original
    assert config.stat().st_ino == before.st_ino
    assert not marker.exists()
    assert "manual migration" in result.stderr


def test_symlink_is_not_followed_and_warns(tmp_path):
    target = tmp_path / "custom.conf"
    original = repository_config(series_url("26.1")).encode()
    target.write_bytes(original)
    config = tmp_path / "resolver-plugins.conf"
    config.symlink_to(target.name)

    result, marker = run_provider(tmp_path, config)

    assert result.returncode == 0
    assert config.is_symlink()
    assert os.readlink(config) == target.name
    assert target.read_bytes() == original
    assert not marker.exists()
    assert "manual migration" in result.stderr


def test_non_regular_path_is_untouched_and_warns(tmp_path):
    config = tmp_path / "resolver-plugins.conf"
    config.mkdir()
    marker_file = config / "custom"
    marker_file.write_bytes(b"leave me alone\n")

    result, marker = run_provider(tmp_path, config)

    assert result.returncode == 0
    assert config.is_dir()
    assert marker_file.read_bytes() == b"leave me alone\n"
    assert not marker.exists()
    assert "manual migration" in result.stderr
