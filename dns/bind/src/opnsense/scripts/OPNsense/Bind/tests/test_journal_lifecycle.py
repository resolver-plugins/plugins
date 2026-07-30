# Copyright (C) 2026 Bryan Wiegand <inbox@kw-ventures.com>
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE AUTHOR AND CONTRIBUTORS ``AS IS AND ANY
# EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE AUTHOR OR CONTRIBUTORS BE LIABLE FOR ANY
# DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
# (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
# ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

import os
import pathlib
import subprocess
import tempfile
import unittest


class JournalLifecycleTest(unittest.TestCase):
    def test_stop_clears_journals_for_watcher_and_reverse_zones(self):
        bind_root = pathlib.Path(__file__).resolve().parents[6]
        stop_script = bind_root / "src/opnsense/scripts/OPNsense/Bind/bindStop.py"

        with tempfile.TemporaryDirectory(dir=bind_root) as directory:
            temporary = pathlib.Path(directory)
            zone_dir = temporary / "primary"
            zone_dir.mkdir()
            watcher_config = temporary / "dhcpwatcher.conf"
            watcher_config.write_text(
                "[mapping]\n"
                "hostname_suffix = watcher.example\n\n"
                "[reverse-zones]\n"
                "192.168.2.0/24 = 2.168.192.in-addr.arpa\n"
                "fd29:abc0:a250:ab10::/64 = 0.1.b.a.0.5.2.a.0.c.b.a.9.2.d.f.ip6.arpa\n"
                "fd29:abc0:a250:ab20::/64 = 0.2.b.a.0.5.2.a.0.c.b.a.9.2.d.f.ip6.arpa\n"
            )
            config = temporary / "config.xml"
            config.write_text(
                """<opnsense>
                    <bind>
                      <domain><domains>
                        <domain uuid=\"forward\"><domainname>forward.example</domainname></domain>
                        <domain uuid=\"reverse\"><type>reverse</type><domainname>1.168.192.in-addr.arpa</domainname></domain>
                      </domains></domain>
                      <watcher><mappings>
                        <mapping><hostname_suffix>forward</hostname_suffix></mapping>
                      </mappings></watcher>
                    </bind>
                  </opnsense>"""
            )
            for zone in (
                    "forward.example", "watcher.example", "1.168.192.in-addr.arpa",
                    "2.168.192.in-addr.arpa", "0.1.b.a.0.5.2.a.0.c.b.a.9.2.d.f.ip6.arpa",
                    "0.2.b.a.0.5.2.a.0.c.b.a.9.2.d.f.ip6.arpa"):
                for suffix in (".jnl", ".jnw", ".jbk"):
                    (zone_dir / f"{zone}.db{suffix}").write_text("")
            state = temporary / "state.json"
            state.write_text("{}")
            events = temporary / "events"
            named = temporary / "named"
            named.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$TEST_EVENTS\"\n")
            named.chmod(0o755)
            pgrep = temporary / "pgrep"
            pgrep.write_text("#!/bin/sh\nexit 1\n")
            pgrep.chmod(0o755)

            result = subprocess.run(
                [stop_script],
                env=os.environ | {
                    "BIND_STOP_CONFIG": str(config),
                    "BIND_STOP_NAMED_RC": str(named),
                    "BIND_STOP_PGREP": str(pgrep),
                    "BIND_STOP_WATCHER_CONFIG": str(watcher_config),
                    "BIND_STOP_ZONE_DIR": str(zone_dir),
                    "BIND_STOP_STATE_FILE": str(state),
                    "TEST_EVENTS": str(events),
                },
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(events.read_text(), "stop\n")
            self.assertFalse(state.exists())
            for zone in (
                    "forward.example", "watcher.example", "1.168.192.in-addr.arpa",
                    "2.168.192.in-addr.arpa", "0.1.b.a.0.5.2.a.0.c.b.a.9.2.d.f.ip6.arpa",
                    "0.2.b.a.0.5.2.a.0.c.b.a.9.2.d.f.ip6.arpa"):
                for suffix in (".jnl", ".jnw", ".jbk"):
                    self.assertFalse((zone_dir / f"{zone}.db{suffix}").exists())

    def test_stop_failure_preserves_journals_and_state(self):
        bind_root = pathlib.Path(__file__).resolve().parents[6]
        stop_script = bind_root / "src/opnsense/scripts/OPNsense/Bind/bindStop.py"

        with tempfile.TemporaryDirectory(dir=bind_root) as directory:
            temporary = pathlib.Path(directory)
            zone_dir = temporary / "primary"
            zone_dir.mkdir()
            watcher_config = temporary / "dhcpwatcher.conf"
            watcher_config.write_text("[mapping]\nhostname_suffix = watcher.example\n")
            state = temporary / "state.json"
            state.write_text("{}")
            journal = zone_dir / "watcher.example.db.jnl"
            journal.write_text("")
            named = temporary / "named"
            named.write_text("#!/bin/sh\nexit 1\n")
            named.chmod(0o755)
            pgrep = temporary / "pgrep"
            pgrep.write_text("#!/bin/sh\nexit 0\n")
            pgrep.chmod(0o755)
            pkill = temporary / "pkill"
            pkill.write_text("#!/bin/sh\nexit 0\n")
            pkill.chmod(0o755)

            result = subprocess.run(
                [stop_script],
                env=os.environ | {
                    "BIND_STOP_NAMED_RC": str(named),
                    "BIND_STOP_PGREP": str(pgrep),
                    "BIND_STOP_PKILL": str(pkill),
                    "BIND_STOP_NAMED_STOP_ATTEMPTS": "1",
                    "BIND_STOP_NAMED_STOP_INTERVAL": "0",
                    "BIND_STOP_WATCHER_CONFIG": str(watcher_config),
                    "BIND_STOP_ZONE_DIR": str(zone_dir),
                    "BIND_STOP_STATE_FILE": str(state),
                },
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertTrue(state.exists())
            self.assertTrue(journal.exists())

    def test_stop_cleans_after_rc_failure_when_named_has_exited(self):
        """An rc.d status failure must not block cleanup after named exits."""
        bind_root = pathlib.Path(__file__).resolve().parents[6]
        stop_script = bind_root / "src/opnsense/scripts/OPNsense/Bind/bindStop.py"

        with tempfile.TemporaryDirectory(dir=bind_root) as directory:
            temporary = pathlib.Path(directory)
            zone_dir = temporary / "primary"
            zone_dir.mkdir()
            watcher_config = temporary / "dhcpwatcher.conf"
            watcher_config.write_text("[mapping]\nhostname_suffix = watcher.example\n")
            state = temporary / "state.json"
            state.write_text("{}")
            journal = zone_dir / "watcher.example.db.jnl"
            journal.write_text("")
            running = temporary / "named-running"
            running.touch()
            named = temporary / "named"
            named.write_text("#!/bin/sh\nrm -f \"$TEST_RUNNING\"\nexit 1\n")
            named.chmod(0o755)
            pgrep = temporary / "pgrep"
            pgrep.write_text("#!/bin/sh\ntest -e \"$TEST_RUNNING\"\n")
            pgrep.chmod(0o755)
            pkill = temporary / "pkill"
            pkill.write_text("#!/bin/sh\necho unexpected-pkill >> \"$TEST_EVENTS\"\nexit 1\n")
            pkill.chmod(0o755)
            events = temporary / "events"

            result = subprocess.run(
                [stop_script],
                env=os.environ | {
                    "BIND_STOP_NAMED_RC": str(named),
                    "BIND_STOP_PGREP": str(pgrep),
                    "BIND_STOP_PKILL": str(pkill),
                    "BIND_STOP_NAMED_STOP_ATTEMPTS": "1",
                    "BIND_STOP_NAMED_STOP_INTERVAL": "0",
                    "BIND_STOP_WATCHER_CONFIG": str(watcher_config),
                    "BIND_STOP_ZONE_DIR": str(zone_dir),
                    "BIND_STOP_STATE_FILE": str(state),
                    "TEST_RUNNING": str(running),
                    "TEST_EVENTS": str(events),
                },
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(running.exists())
            self.assertFalse(state.exists())
            self.assertFalse(journal.exists())
            self.assertFalse(events.exists())

    def test_stop_force_kills_named_before_cleanup(self):
        """A named process that ignores rc.d stop is killed before cleanup."""
        bind_root = pathlib.Path(__file__).resolve().parents[6]
        stop_script = bind_root / "src/opnsense/scripts/OPNsense/Bind/bindStop.py"

        with tempfile.TemporaryDirectory(dir=bind_root) as directory:
            temporary = pathlib.Path(directory)
            zone_dir = temporary / "primary"
            zone_dir.mkdir()
            watcher_config = temporary / "dhcpwatcher.conf"
            watcher_config.write_text("[mapping]\nhostname_suffix = watcher.example\n")
            state = temporary / "state.json"
            state.write_text("{}")
            journal = zone_dir / "watcher.example.db.jnl"
            journal.write_text("")
            running = temporary / "named-running"
            running.touch()
            named = temporary / "named"
            named.write_text("#!/bin/sh\nexit 1\n")
            named.chmod(0o755)
            pgrep = temporary / "pgrep"
            pgrep.write_text("#!/bin/sh\ntest -e \"$TEST_RUNNING\"\n")
            pgrep.chmod(0o755)
            pkill = temporary / "pkill"
            pkill.write_text("#!/bin/sh\nrm -f \"$TEST_RUNNING\"\necho pkill >> \"$TEST_EVENTS\"\n")
            pkill.chmod(0o755)
            events = temporary / "events"

            result = subprocess.run(
                [stop_script],
                env=os.environ | {
                    "BIND_STOP_NAMED_RC": str(named),
                    "BIND_STOP_PGREP": str(pgrep),
                    "BIND_STOP_PKILL": str(pkill),
                    "BIND_STOP_NAMED_STOP_ATTEMPTS": "1",
                    "BIND_STOP_NAMED_STOP_INTERVAL": "0",
                    "BIND_STOP_WATCHER_CONFIG": str(watcher_config),
                    "BIND_STOP_ZONE_DIR": str(zone_dir),
                    "BIND_STOP_STATE_FILE": str(state),
                    "TEST_RUNNING": str(running),
                    "TEST_EVENTS": str(events),
                },
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(running.exists())
            self.assertFalse(state.exists())
            self.assertFalse(journal.exists())
            self.assertEqual(events.read_text(), "pkill\n")


if __name__ == "__main__":
    unittest.main()
