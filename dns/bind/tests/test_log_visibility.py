# Copyright (C) 2026 Bryan Wiegand <inbox@kw-ventures.com>
# All rights reserved.

from pathlib import Path

import pytest

from .bounded_shutdown_contract import current_release_requires_bounded_shutdown


BIND_ROOT = Path(__file__).resolve().parents[1]
STOP_SCRIPT = BIND_ROOT / "src/opnsense/scripts/OPNsense/Bind/bindStop.py"

if not current_release_requires_bounded_shutdown(BIND_ROOT):
    pytestmark = pytest.mark.skip(reason="release predates bounded BIND shutdown")


def test_general_log_includes_informational_lifecycle_messages():
    view = (BIND_ROOT / "src/opnsense/mvc/app/views/OPNsense/Bind/logs.volt").read_text()
    stop = STOP_SCRIPT.read_text()

    assert "'default_log_severity':'Informational'" in view
    assert 'syslog.openlog("named")' in stop
