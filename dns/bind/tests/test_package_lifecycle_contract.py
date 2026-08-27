import pytest

try:
    from .package_lifecycle_contract import lifecycle_required
except ImportError:
    from package_lifecycle_contract import lifecycle_required


@pytest.mark.parametrize(
    ("series", "version", "revision", "required"),
    [
        ("26.1", (1, 36), 10, False),
        ("26.1", (1, 36), 11, True),
        ("26.7", (1, 36), 3, False),
        ("26.7", (1, 36), 4, True),
        ("26.1", (26, 1), 0, False),
        ("26.1", (26, 1), 1, True),
        ("26.7", (26, 7), 0, False),
        ("26.7", (26, 7), 1, True),
        ("26.1", (1, 37), 0, True),
    ],
)
def test_lifecycle_activation_thresholds(series, version, revision, required):
    assert lifecycle_required(series, version, revision) is required


def test_known_series_package_for_another_release_is_an_error():
    with pytest.raises(ValueError, match="series package version 26.7 does not match 26.1"):
        lifecycle_required("26.1", (26, 7), 1)


def test_unknown_release_series_cannot_silently_skip_lifecycle_tests():
    with pytest.raises(ValueError, match="unsupported BIND release series"):
        lifecycle_required("27.1", (1, 36), 1)
