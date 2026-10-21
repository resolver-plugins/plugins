# Copyright (C) 2026 Bryan Wiegand <inbox@kw-ventures.com>
# All rights reserved.

from pathlib import Path


BIND_ROOT = Path(__file__).resolve().parents[1]
STOP_SCRIPT = BIND_ROOT / "src/opnsense/scripts/OPNsense/Bind/bindStop.py"


def test_general_log_includes_informational_lifecycle_messages():
    view = (BIND_ROOT / "src/opnsense/mvc/app/views/OPNsense/Bind/logs.volt").read_text()
    stop = STOP_SCRIPT.read_text()

    assert "'default_log_severity':'Informational'" in view
    assert 'syslog.openlog("named")' in stop
