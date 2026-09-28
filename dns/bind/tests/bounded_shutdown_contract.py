import json


BOUNDED_SHUTDOWN_THRESHOLDS = {
    "26.1": None,
    "26.7": ((26, 7), 6),
}


def current_release_requires_bounded_shutdown(bind_root):
    metadata = bind_root.parents[1] / ".resolver-plugins/upstream.json"
    series = json.loads(metadata.read_text())["series"]
    try:
        threshold = BOUNDED_SHUTDOWN_THRESHOLDS[series]
    except KeyError as error:
        raise ValueError(f"unsupported BIND release series: {series}") from error
    if threshold is None:
        return False

    values = {}
    for line in (bind_root / "Makefile").read_text().splitlines():
        if line.startswith(("PLUGIN_VERSION=", "PLUGIN_REVISION=")):
            key, value = line.split("=", 1)
            values[key] = value.strip()
    version = tuple(int(part) for part in values["PLUGIN_VERSION"].split("."))
    revision = int(values["PLUGIN_REVISION"])
    return (version, revision) >= threshold
