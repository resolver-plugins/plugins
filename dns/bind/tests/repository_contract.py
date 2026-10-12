"""Managed repository shapes across the reviewed release-source rollout."""
import re
from pathlib import Path

PUBLIC_KEY = "/usr/local/etc/pkg/keys/resolver-plugins.pub"
MAKEFILE = Path(__file__).resolve().parents[1] / "Makefile"
PRIORITY_REVISIONS = {"26.1": 6, "26.7": 8}


def source_version():
    text = MAKEFILE.read_text(encoding="utf-8")
    version = re.search(r"^PLUGIN_VERSION=\s*([^\s]+)", text, re.MULTILINE)
    revision = re.search(r"^PLUGIN_REVISION=\s*([0-9]+)", text, re.MULTILINE)
    return (version[1], int(revision[1])) if version and revision else ("", 0)


SERIES, REVISION = source_version()
SERIES_VERSIONED = SERIES in PRIORITY_REVISIONS and REVISION > 0
PRIORITY_REQUIRED = SERIES_VERSIONED and REVISION >= PRIORITY_REVISIONS[SERIES]


def repository_config(url, *, mirror_type="none", signature_type="pubkey",
                      key=PUBLIC_KEY, enabled="yes",
                      priority=20 if PRIORITY_REQUIRED else None):
    return (
        "resolver-plugins: {\n"
        f'  url: "{url}",\n'
        f'  mirror_type: "{mirror_type}",\n'
        f'  signature_type: "{signature_type}",\n'
        f'  pubkey: "{key}",\n'
        + (f"  priority: {priority},\n" if priority is not None else "")
        + f"  enabled: {enabled}\n"
        "}\n"
    )


def series_url(series):
    return f"https://resolver-plugins.github.io/repository/pkg/${{ABI}}/{series}/latest"
