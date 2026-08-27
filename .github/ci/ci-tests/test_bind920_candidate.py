#!/usr/bin/env python3
"""Local regression coverage for BIND update candidate handling."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "bind920_candidate.py"
SPEC = importlib.util.spec_from_file_location("bind920_candidate", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
bind920_candidate = importlib.util.module_from_spec(SPEC)
sys.modules["bind920_candidate"] = bind920_candidate
SPEC.loader.exec_module(bind920_candidate)


Bind920Profile = bind920_candidate.Bind920Profile
CandidateProfile = bind920_candidate.CandidateProfile
assess_candidate = bind920_candidate.assess_candidate
candidate_is_newer = bind920_candidate.candidate_is_newer
parse_bind920_makefile = bind920_candidate.parse_bind920_makefile
parse_distinfo_hash = bind920_candidate.parse_distinfo_hash


class Bind920CandidateTest(unittest.TestCase):
    def test_parse_bind920_makefile_reads_distversion_and_portrevision(self) -> None:
        """A Ports recipe version bump must update both version inputs."""
        text = "PORTNAME= bind920\nDISTVERSION= 9.20.27\nPORTREVISION= 2\n"
        self.assertEqual(("9.20.27", 2), parse_bind920_makefile(text))

    def test_parse_bind920_makefile_defaults_missing_portrevision_to_zero(self) -> None:
        """A fresh Ports version without PORTREVISION must become revision zero."""
        text = "PORTNAME= bind920\nDISTVERSION= 9.20.27\n"
        self.assertEqual(("9.20.27", 0), parse_bind920_makefile(text))

    def test_parse_distinfo_hash_reads_bind_tarball_sha256(self) -> None:
        """The candidate profile must hash the BIND distfile, not unrelated entries."""
        text = "TIMESTAMP = 1\nSHA256 (bind-9.20.27.tar.xz) = abc123\nSIZE (bind-9.20.27.tar.xz) = 1\n"
        self.assertEqual("abc123", parse_distinfo_hash(text))

    def test_candidate_is_newer_rejects_same_distversion_and_portrevision(self) -> None:
        """An unchanged candidate must not open a review PR."""
        current = Bind920Profile("repo", "old", "m1", "d1", "9.20.26", 1)
        candidate = CandidateProfile("repo", "new", "m2", "d2", "9.20.26", 1, "main")
        self.assertFalse(candidate_is_newer(current, candidate))

    def test_candidate_is_newer_accepts_higher_patch_version(self) -> None:
        """A newer BIND 9.20 patch release must be proposed for review."""
        current = Bind920Profile("repo", "old", "m1", "d1", "9.20.26", 1)
        candidate = CandidateProfile("repo", "new", "m2", "d2", "9.20.27", 0, "main")
        self.assertTrue(candidate_is_newer(current, candidate))

    def test_candidate_is_newer_rejects_wrong_bind_series(self) -> None:
        """The updater must not silently move os-bind-rp to another BIND series."""
        current = Bind920Profile("repo", "old", "m1", "d1", "9.20.26", 1)
        candidate = CandidateProfile("repo", "new", "m2", "d2", "9.21.0", 0, "main")
        with self.assertRaisesRegex(ValueError, "9.20"):
            candidate_is_newer(current, candidate)

    def test_assessment_classifies_security_signal(self) -> None:
        """CVE-bearing updates must be visible as security review candidates."""
        result = assess_candidate(
            "9.20.26",
            "9.20.27",
            "Security fix: CVE-2026-1234 denial of service in resolver.",
            "",
        )
        self.assertEqual("security", result.classification)
        self.assertIn("CVE-2026-1234", result.signals)

    def test_assessment_classifies_crash_or_servfail_as_critical_bugfix(self) -> None:
        """Operationally important resolver fixes must not look like routine bumps."""
        result = assess_candidate(
            "9.20.26",
            "9.20.27",
            "Bug fixes include named crash and SERVFAIL regression.",
            "",
        )
        self.assertEqual("critical-bugfix", result.classification)

    def test_assessment_classifies_dependency_drift_as_risky(self) -> None:
        """Ports dependency changes need explicit maintainer attention."""
        result = assess_candidate(
            "9.20.26",
            "9.20.27",
            "Maintenance release.",
            "+LIB_DEPENDS+= libnew.so:security/newlib\n",
        )
        self.assertEqual("risky", result.classification)
        self.assertIn("dependency change", result.signals)

    def test_assessment_classifies_plain_patch_as_routine(self) -> None:
        """A patch release without policy signals can be deferred as routine."""
        result = assess_candidate(
            "9.20.26",
            "9.20.27",
            "Maintenance release with documentation and minor bug fixes.",
            "",
        )
        self.assertEqual("routine", result.classification)

    def test_assessment_summary_is_stable(self) -> None:
        """PR assessment text must be predictable across repeated workflow runs."""
        result = assess_candidate(
            "9.20.26",
            "9.20.27",
            "Bug fixes include resolver crash.",
            "",
        )
        self.assertEqual(
            "BIND 9.20.26 to 9.20.27 is classified as critical-bugfix. "
            "Signals: crash, resolver. Maintainer review is required before publication.",
            result.summary,
        )


if __name__ == "__main__":
    unittest.main()
