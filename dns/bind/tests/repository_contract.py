"""Managed repository shapes written and recognized by the provider."""

PUBLIC_KEY = "/usr/local/etc/pkg/keys/resolver-plugins.pub"


def repository_config(url, *, mirror_type="none", signature_type="pubkey",
                      key=PUBLIC_KEY, enabled="yes",
                      priority=None):
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
