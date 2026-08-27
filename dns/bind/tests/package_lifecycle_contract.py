import json


LIFECYCLE_THRESHOLDS = {
    "26.1": ((1, 36), 11),
    "26.7": ((1, 36), 4),
}


def lifecycle_required(series, version, revision):
    try:
        threshold = LIFECYCLE_THRESHOLDS[series]
    except KeyError as error:
        raise ValueError(f"unsupported BIND release series: {series}") from error
    series_version = tuple(int(part) for part in series.split("."))
    known_series_versions = {
        tuple(int(part) for part in supported.split("."))
        for supported in LIFECYCLE_THRESHOLDS
    }
    if tuple(version) in known_series_versions:
        if tuple(version) != series_version:
            raise ValueError(
                f"series package version {'.'.join(str(part) for part in version)} "
                f"does not match {series}"
            )
        return revision >= 1
    return (tuple(version), revision) >= threshold


def current_release_requires_lifecycle(bind_root, version, revision):
    metadata = bind_root.parents[1] / ".resolver-plugins/upstream.json"
    series = json.loads(metadata.read_text())["series"]
    return lifecycle_required(series, version, revision)
