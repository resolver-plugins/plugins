import os
import re


LIFECYCLE_THRESHOLDS = {
    "26.1": ((1, 36), 11),
    "26.7": ((1, 36), 4),
}


def lifecycle_required(series, version, revision):
    series_version = tuple(int(part) for part in series.split("."))
    if version[0] >= 20:
        if tuple(version) != series_version:
            raise ValueError(
                f"series package version {'.'.join(str(part) for part in version)} "
                f"does not match {series}"
            )
        return revision >= 1
    try:
        threshold = LIFECYCLE_THRESHOLDS[series]
    except KeyError as error:
        raise ValueError(f"unsupported BIND release series: {series}") from error
    return (tuple(version), revision) >= threshold


def current_release_series(bind_root):
    series = os.environ.get("RP_TEST_SERIES")
    if not series:
        versions = re.findall(r"^PLUGIN_VERSION=\s*([0-9]+\.[0-9]+)\s*$",
                              (bind_root / "Makefile").read_text(), re.M)
        if len(versions) == 1 and int(versions[0].split('.')[0]) >= 20:
            series = versions[0]
    if not series or re.fullmatch(r"[0-9]+\.[0-9]+", series) is None:
        raise ValueError("set RP_TEST_SERIES to the discovered release series for legacy BIND source")
    return series


def current_release_requires_lifecycle(bind_root, version, revision):
    return lifecycle_required(current_release_series(bind_root), version, revision)
