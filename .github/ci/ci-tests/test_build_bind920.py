from common_imports import *


ROOT = Path(__file__).resolve().parents[3]
BUILD_SCRIPT = ROOT / ".github" / "ci" / "bind/build-bind920.sh"


def test_build_bootstraps_the_lmdb_abi_used_by_the_target_opnsense_repository():
    script = BUILD_SCRIPT.read_text(encoding="utf-8")

    install = re.search(r'"\$pkg_command" install -y autoconf.*?(?<!\\)\n', script, re.DOTALL)
    packages = shlex.split(install[0].replace("\\\n", ""))[3:]
    assert "lmdb0" in packages
    assert "lmdb" not in packages
