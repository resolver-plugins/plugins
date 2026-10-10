from git_fixtures import *

import pytest


REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[3]
GUARD = REPOSITORY_ROOT / ".github/ci/check-bind-pkg-descr.sh"


def initialize_repository(repository: pathlib.Path) -> str:
    init_repository(repository)
    initial = {
        "dns/bind/Makefile": "PLUGIN_VERSION= 1.0\nPLUGIN_REVISION= 1\nPLUGIN_DEPENDS= bind920\n",
        "dns/bind/pkg-descr": "Initial description\n",
        "dns/bind/src/service": "initial\n",
        "dns/bind/tests/test_service.py": "initial\n",
    }
    return commit(repository, initial, "initial")


def run_guard(repository: pathlib.Path, base: str, head: str) -> int:
    return subprocess.run(
        [str(GUARD), base, head],
        cwd=repository,
        capture_output=True,
        text=True,
    ).returncode


def check_case(repository: pathlib.Path, changes: dict[str, str]) -> int:
    base = initialize_repository(repository)
    head = commit(repository, changes, "change")
    return run_guard(repository, base, head)


@pytest.mark.parametrize(
    ("case", "changes", "expected"),
    [
        ("runtime", {"dns/bind/src/service": "changed\n"}, 1),
        ("hook", {"dns/bind/+POST_INSTALL.post": "changed\n"}, 1),
        (
            "documented",
            {
                "dns/bind/src/service": "changed\n",
                "dns/bind/pkg-descr": "Updated description\n",
            },
            0,
        ),
        (
            "dependency-formula",
            {
                "dns/bind/Makefile": (
                    "PLUGIN_VERSION= 1.0\nPLUGIN_REVISION= 1\nPLUGIN_DEPENDS= bind920\n"
                    "PLUGIN_DEPEND_FORMULA_DEPENDS= bind920\n"
                )
            },
            1,
        ),
        (
            "revision",
            {"dns/bind/Makefile": "PLUGIN_VERSION= 1.0\nPLUGIN_REVISION= 2\nPLUGIN_DEPENDS= bind920\n"},
            0,
        ),
        ("tests", {"dns/bind/tests/test_service.py": "changed\n"}, 0),
    ],
)
def test_guard_requires_pkg_descr_only_for_publishable_changes(tmp_path, case, changes, expected):
    assert check_case(tmp_path / case, changes) == expected


def test_guard_uses_merge_base_when_the_release_branch_advances(tmp_path):
    repository = tmp_path / "advanced-base"
    original_base = initialize_repository(repository)
    git(repository, "checkout", "-b", "feature")
    (repository / "dns/bind/src/service").write_text("changed\n", encoding="utf-8")
    git(repository, "commit", "-am", "runtime change")
    head = git(repository, "rev-parse", "HEAD")

    git(repository, "checkout", original_base)
    (repository / "dns/bind/pkg-descr").write_text("Base advanced\n", encoding="utf-8")
    git(repository, "commit", "-am", "advance base")
    advanced_base = git(repository, "rev-parse", "HEAD")

    assert original_base == git(repository, "merge-base", advanced_base, head)
    assert run_guard(repository, advanced_base, head) == 1


def test_guard_detects_runtime_files_moved_out_of_the_package(tmp_path):
    repository = tmp_path / "runtime-rename"
    base = initialize_repository(repository)
    git(repository, "mv", "dns/bind/src/service", "dns/bind/tests/moved_service")
    git(repository, "commit", "-m", "move runtime file")

    assert run_guard(repository, base, "HEAD") == 1
