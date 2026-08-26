import pytest

from .package_lifecycle_contract import lifecycle_required


@pytest.mark.parametrize(
    ("series", "version", "revision", "required"),
    [
        ("26.1", (1, 36), 10, False),
        ("26.1", (1, 36), 11, True),
        ("26.7", (1, 36), 3, False),
        ("26.7", (1, 36), 4, True),
    ],
)
def test_lifecycle_activation_thresholds(series, version, revision, required):
    assert lifecycle_required(series, version, revision) is required


def test_unknown_release_series_cannot_silently_skip_lifecycle_tests():
    with pytest.raises(ValueError, match="unsupported BIND release series"):
        lifecycle_required("27.1", (1, 36), 1)
