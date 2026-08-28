from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[3]
BUILD_SCRIPT = ROOT / ".github" / "ci" / "build-bind920.sh"


def test_build_bootstraps_the_lmdb_abi_used_by_the_target_opnsense_repository():
    script = BUILD_SCRIPT.read_text(encoding="utf-8")

    assert re.search(r"\blmdb0\b", script)
    assert not re.search(r"\blmdb\b", script)
