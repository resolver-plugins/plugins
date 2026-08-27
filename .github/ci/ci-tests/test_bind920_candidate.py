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


if __name__ == "__main__":
    unittest.main()
