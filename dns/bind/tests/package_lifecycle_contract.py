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
    return (tuple(version), revision) >= threshold


def current_release_requires_lifecycle(bind_root, version, revision):
    metadata = bind_root.parents[1] / ".resolver-plugins/upstream.json"
    series = json.loads(metadata.read_text())["series"]
    return lifecycle_required(series, version, revision)
