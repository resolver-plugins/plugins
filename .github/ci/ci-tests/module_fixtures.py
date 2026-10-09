"""Load CI scripts for tests using only the standard library."""
from common_imports import *


CI = Path(__file__).resolve().parents[1]


def load_module(name, path, *, register=False):
    """Return a fresh module; register dataclass modules before execution."""
    spec = importlib.util.spec_from_file_location(name, CI / path)
    assert spec is not None and spec.loader is not None, path
    module = importlib.util.module_from_spec(spec)
    if register:
        sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
