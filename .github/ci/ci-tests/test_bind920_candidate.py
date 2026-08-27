#!/usr/bin/env python3
"""Local regression coverage for BIND update candidate handling."""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "bind920_candidate.py"
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
CANDIDATE_WORKFLOW = REPOSITORY_ROOT / ".github/workflows/bind920-candidate.yml"
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
render_updated_profile = bind920_candidate.render_updated_profile


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

    def test_assessment_classifies_continuation_dependency_drift_as_risky(self) -> None:
        """Changes inside continued dependency assignments must not look routine."""
        result = assess_candidate(
            "9.20.26",
            "9.20.27",
            "Maintenance release.",
            "@@ -1,3 +1,3 @@\n LIB_DEPENDS= liba.so:devel/a \\\n- libb.so:devel/b\n+ libc.so:devel/c\n",
        )
        self.assertEqual("risky", result.classification)
        self.assertIn("dependency change", result.signals)

    def test_assessment_keeps_secondary_dependency_signal_for_security_candidate(self) -> None:
        """Security updates with dependency drift must surface both review concerns."""
        result = assess_candidate(
            "9.20.26",
            "9.20.27",
            "Security fix: CVE-2026-1234.",
            "+LIB_DEPENDS+= libnew.so:security/newlib\n",
        )
        self.assertEqual("security", result.classification)
        self.assertIn("CVE-2026-1234", result.signals)
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

    def test_render_updated_profile_preserves_key_order_and_updates_candidate_values(self) -> None:
        """Profile rewrites must be stable and limited to candidate values."""
        current = Bind920Profile("repo", "old", "oldmake", "olddist", "9.20.26", 1)
        candidate = CandidateProfile("repo", "new", "newmake", "newdist", "9.20.27", 0, "main")
        rendered = render_updated_profile(current, candidate)
        self.assertIn('"ports_commit": "new"', rendered)
        self.assertIn('"distversion": "9.20.27"', rendered)
        self.assertIn('"portrevision": 0', rendered)
        self.assertEqual(
            [
                "ports_repository",
                "ports_commit",
                "makefile_sha256",
                "distinfo_sha256",
                "distversion",
                "portrevision",
            ],
            list(json.loads(rendered).keys()),
        )

    def test_update_profile_cli_hashes_candidate_files(self) -> None:
        """The CLI must derive checksums from the candidate recipe files."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            current = directory / "bind920.json"
            makefile = directory / "Makefile"
            distinfo = directory / "distinfo"
            current.write_text(
                json.dumps(
                    {
                        "ports_repository": "https://github.com/freebsd/freebsd-ports.git",
                        "ports_commit": "0" * 40,
                        "makefile_sha256": "1" * 64,
                        "distinfo_sha256": "2" * 64,
                        "distversion": "9.20.26",
                        "portrevision": 1,
                    }
                ),
                encoding="utf-8",
            )
            makefile.write_text("PORTNAME= bind920\nDISTVERSION= 9.20.27\n", encoding="utf-8")
            distinfo.write_text(
                "TIMESTAMP = 1\nSHA256 (bind-9.20.27.tar.xz) = abc123\nSIZE (bind-9.20.27.tar.xz) = 1\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(MODULE_PATH),
                    "update-profile",
                    "--current",
                    str(current),
                    "--ports-repository",
                    "https://github.com/freebsd/freebsd-ports.git",
                    "--ports-commit",
                    "f" * 40,
                    "--makefile",
                    str(makefile),
                    "--distinfo",
                    str(distinfo),
                    "--output",
                    str(current),
                ],
                capture_output=True,
                text=True,
            )

            self.assertEqual("", result.stderr)
            self.assertEqual(0, result.returncode)
            updated = json.loads(current.read_text(encoding="utf-8"))
            self.assertEqual("9.20.27", updated["distversion"])
            self.assertEqual(0, updated["portrevision"])
            self.assertEqual("f" * 40, updated["ports_commit"])
            self.assertEqual(bind920_candidate.sha256_file(makefile), updated["makefile_sha256"])
            self.assertEqual(bind920_candidate.sha256_file(distinfo), updated["distinfo_sha256"])

    def test_candidate_from_files_rejects_distinfo_version_mismatch(self) -> None:
        """A profile PR must not pair one DISTVERSION with another distfile."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            makefile = directory / "Makefile"
            distinfo = directory / "distinfo"
            makefile.write_text("PORTNAME= bind920\nDISTVERSION= 9.20.27\n", encoding="utf-8")
            distinfo.write_text(
                "TIMESTAMP = 1\nSHA256 (bind-9.20.26.tar.xz) = abc123\nSIZE (bind-9.20.26.tar.xz) = 1\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "distinfo"):
                bind920_candidate.candidate_from_files(
                    "https://github.com/freebsd/freebsd-ports.git",
                    "f" * 40,
                    makefile,
                    distinfo,
                    "main",
                )

    def test_assess_cli_writes_markdown_summary(self) -> None:
        """The workflow should get stable PR text without embedding assessment logic."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            changelog = directory / "changelog.txt"
            diff = directory / "ports.diff"
            output = directory / "assessment.md"
            changelog.write_text("Resolver crash fixed.\n", encoding="utf-8")
            diff.write_text("", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(MODULE_PATH),
                    "assess",
                    "--old-version",
                    "9.20.26",
                    "--new-version",
                    "9.20.27",
                    "--changelog",
                    str(changelog),
                    "--ports-diff",
                    str(diff),
                    "--output",
                    str(output),
                ],
                capture_output=True,
                text=True,
            )

            self.assertEqual("", result.stderr)
            self.assertEqual(0, result.returncode)
            self.assertIn("classification: critical-bugfix", output.read_text(encoding="utf-8"))


class Bind920CandidateWorkflowTest(unittest.TestCase):
    def workflow_text(self) -> str:
        self.assertTrue(CANDIDATE_WORKFLOW.is_file(), "candidate workflow is missing")
        return CANDIDATE_WORKFLOW.read_text(encoding="utf-8")

    def test_candidate_workflow_uses_only_stdlib_tests_before_github_fetches(self) -> None:
        """The candidate workflow must not add package-registry egress."""
        workflow = self.workflow_text()
        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn("pip install", workflow)
        self.assertNotIn("python -m pytest", workflow)
        self.assertIn("python .github/ci/ci-tests/test_bind920_candidate.py", workflow)

    def test_candidate_workflow_never_publishes_packages(self) -> None:
        """Candidate review PRs must not cross the publication boundary."""
        workflow = self.workflow_text()
        self.assertNotIn("release upload", workflow)
        self.assertNotIn("release_channel.py publish", workflow)
        self.assertIn("Publication: not performed by this workflow", workflow)

    def test_candidate_workflow_uses_pinned_actions(self) -> None:
        """Workflow actions must stay pinned to immutable SHAs."""
        workflow = self.workflow_text()
        references = re.findall(r"^\s+(?:-\s+)?uses:\s+([^\s#]+)", workflow, re.MULTILINE)
        self.assertTrue(references)
        self.assertTrue(all(re.fullmatch(r"[^@\s]+@[0-9a-f]{40}", reference) for reference in references))

    def test_candidate_workflow_checks_empty_index_before_commit(self) -> None:
        """Only an actual empty candidate diff may skip PR branch publication."""
        workflow = self.workflow_text()
        self.assertIn("git diff --cached --quiet", workflow)
        self.assertNotIn("git commit -m \"ci(bind): update bind920 to ${version}_${revision}\" || exit 0", workflow)


if __name__ == "__main__":
    unittest.main()
