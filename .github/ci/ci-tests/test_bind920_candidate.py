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
render_updated_profile = bind920_candidate.render_updated_profile
render_commit_log_markdown = bind920_candidate.render_commit_log_markdown
upstream_bind_release_tag = bind920_candidate.upstream_bind_release_tag


class Bind920CandidateTest(unittest.TestCase):
    def test_parse_bind920_makefile_reads_distversion_and_portrevision(self) -> None:
        """A Ports recipe version bump must update both version inputs."""
        text = "PORTNAME= bind920\nDISTVERSION= 9.20.27\nPORTREVISION= 2\n"
        self.assertEqual(("9.20.27", 2), parse_bind920_makefile(text))

    def test_parse_bind920_makefile_defaults_missing_portrevision_to_zero(self) -> None:
        """A fresh Ports version without PORTREVISION must become revision zero."""
        text = "PORTNAME= bind920\nDISTVERSION= 9.20.27\n"
        self.assertEqual(("9.20.27", 0), parse_bind920_makefile(text))

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

    def test_assessment_classifies_long_dependency_continuation_drift_as_risky(self) -> None:
        """Dependency changes must be detected even when a compact diff omits the assignment header."""
        old_makefile = """PORTNAME= bind920
DISTVERSION= 9.20.27
LIB_DEPENDS= liba.so:devel/a \\
  libb.so:devel/b \\
  libd.so:devel/d \\
  libe.so:devel/e \\
  libf.so:devel/f
"""
        new_makefile = old_makefile.replace("libf.so:devel/f", "libg.so:devel/g")
        compact_diff = "@@ -5,1 +5,1 @@\n-  libf.so:devel/f\n+  libg.so:devel/g\n"

        result = assess_candidate(
            "9.20.26",
            "9.20.27",
            "Maintenance release.",
            compact_diff,
            old_makefile_text=old_makefile,
            new_makefile_text=new_makefile,
        )

        self.assertEqual("risky", result.classification)
        self.assertIn("dependency change", result.signals)

    def test_assessment_compares_repeated_dependency_assignments(self) -> None:
        """Repeated dependency-like assignments must be compared as distinct logical blocks."""
        old_makefile = """PORTNAME= bind920
DISTVERSION= 9.20.27
CONFIGURE_ARGS+= --with-a \\
  --with-b
CONFIGURE_ARGS+= --enable-fixed
"""
        new_makefile = old_makefile.replace("--with-b", "--with-c")
        compact_diff = "@@ -4,1 +4,1 @@\n-  --with-b\n+  --with-c\n"

        result = assess_candidate(
            "9.20.26",
            "9.20.27",
            "Maintenance release.",
            compact_diff,
            old_makefile_text=old_makefile,
            new_makefile_text=new_makefile,
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

    def test_upstream_bind_release_tag_strips_portrevision(self) -> None:
        """Upstream BIND tags must be based on BIND versions, not FreeBSD package revisions."""
        self.assertEqual("v9.20.26", upstream_bind_release_tag("9.20.26_2"))
        self.assertEqual("v9.20.27", upstream_bind_release_tag("9.20.27"))

    def test_render_commit_log_markdown_lists_commit_subjects(self) -> None:
        """Candidate PRs should summarize upstream release commits without full commit bodies."""
        rendered = render_commit_log_markdown(
            "Upstream BIND Changes",
            "abc1234 Fix resolver crash\n"
            "def5678 Improve DNSSEC validation\n",
            "No upstream commits were found.",
        )

        self.assertEqual(
            "### Upstream BIND Changes\n\n"
            "- `abc1234` Fix resolver crash\n"
            "- `def5678` Improve DNSSEC validation\n",
            rendered,
        )

    def test_render_commit_log_markdown_uses_fallback_when_empty(self) -> None:
        """PR bodies should explain missing upstream history instead of showing a blank section."""
        rendered = render_commit_log_markdown(
            "Upstream BIND Changes",
            "\n",
            "Could not resolve upstream BIND release tags.",
        )

        self.assertEqual(
            "### Upstream BIND Changes\n\n"
            "- Could not resolve upstream BIND release tags.\n",
            rendered,
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

    def test_candidate_from_files_validates_distinfo_version_and_hashes_whole_files(self) -> None:
        """Candidate profiles must pair the Makefile version with the matching distinfo file."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            makefile = directory / "Makefile"
            distinfo = directory / "distinfo"
            makefile.write_text("PORTNAME= bind920\nDISTVERSION= 9.20.27\n", encoding="utf-8")
            distinfo.write_text(
                "TIMESTAMP = 1\nSHA256 (bind-9.20.27.tar.xz) = abc123\nSIZE (bind-9.20.27.tar.xz) = 1\n",
                encoding="utf-8",
            )

            candidate = bind920_candidate.candidate_from_files(
                "https://github.com/freebsd/freebsd-ports.git",
                "f" * 40,
                makefile,
                distinfo,
                "main",
            )

            self.assertEqual("9.20.27", candidate.distversion)
            self.assertEqual(bind920_candidate.sha256_file(makefile), candidate.makefile_sha256)
            self.assertEqual(bind920_candidate.sha256_file(distinfo), candidate.distinfo_sha256)

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

    def test_assess_cli_accepts_old_and_new_makefiles_for_dependency_drift(self) -> None:
        """Workflow assessment should not rely on diff context for dependency blocks."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            changelog = directory / "changelog.txt"
            diff = directory / "ports.diff"
            old_makefile = directory / "old.Makefile"
            new_makefile = directory / "new.Makefile"
            output = directory / "assessment.md"
            changelog.write_text("Maintenance release.\n", encoding="utf-8")
            diff.write_text("@@ -5,1 +5,1 @@\n-  libf.so:devel/f\n+  libg.so:devel/g\n", encoding="utf-8")
            old_makefile.write_text("LIB_DEPENDS= liba.so:devel/a \\\n  libf.so:devel/f\n", encoding="utf-8")
            new_makefile.write_text("LIB_DEPENDS= liba.so:devel/a \\\n  libg.so:devel/g\n", encoding="utf-8")

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
                    "--old-makefile",
                    str(old_makefile),
                    "--new-makefile",
                    str(new_makefile),
                    "--output",
                    str(output),
                ],
                capture_output=True,
                text=True,
            )

            self.assertEqual("", result.stderr)
            self.assertEqual(0, result.returncode)
            self.assertIn("classification: risky", output.read_text(encoding="utf-8"))


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
        self.assertIn("python .github/ci/ci-tests/test_bind920_reuse.py", workflow)
        self.assertIn("python .github/ci/ci-tests/test_release_channel_provenance.py", workflow)

    def test_bind_pull_request_workflow_covers_profile_only_candidates_without_pip(self) -> None:
        """Generated bind920 profile PRs need checks without adding package-registry egress."""
        workflow = (REPOSITORY_ROOT / ".github/workflows/bind-tests.yml").read_text(encoding="utf-8")
        helper_job = workflow.split("  ci-helpers:", 1)[1].split("  discover:", 1)[0]
        bind_job = workflow.split("  test:", 1)[1]

        self.assertIn("- '.resolver-plugins/bind920.json'", workflow)
        self.assertIn("python .github/ci/ci-tests/test_bind920_candidate.py", helper_job)
        self.assertIn("python .github/ci/ci-tests/test_bind920_reuse.py", helper_job)
        self.assertIn("python .github/ci/ci-tests/test_release_channel_provenance.py", helper_job)
        self.assertIn("python .github/ci/bind920_profile.py .resolver-plugins/bind920.json package_version", helper_job)
        self.assertNotIn("pip install", helper_job)
        self.assertIn("needs.changes.outputs.bind_source == 'true'", bind_job)

    def test_candidate_workflow_never_publishes_packages(self) -> None:
        """Candidate review PRs must not cross the publication boundary."""
        workflow = self.workflow_text()
        self.assertNotIn("release upload", workflow)
        self.assertNotIn("release_channel.py publish", workflow)
        self.assertIn("Publication: not performed by this workflow", workflow)

    def test_candidate_workflow_includes_ports_commit_subjects_in_pr_body(self) -> None:
        """Review PRs must show the FreeBSD Ports commits behind the candidate."""
        workflow = self.workflow_text()
        self.assertIn("render-commit-log", workflow)
        self.assertIn("--title \"FreeBSD Ports Changes\"", workflow)
        self.assertIn("No dns/bind920 commits found between the pinned and candidate Ports commits.", workflow)

    def test_candidate_workflow_includes_upstream_bind_commit_subjects_in_pr_body(self) -> None:
        """Review PRs must show upstream BIND commits between the old and new release tags."""
        workflow = self.workflow_text()
        self.assertIn("https://github.com/isc-projects/bind9.git", workflow)
        self.assertIn("old_distversion=", workflow)
        self.assertIn("--title \"Upstream BIND Changes\"", workflow)
        self.assertIn("upstream-tag", workflow)
        self.assertIn("git -C \"$RUNNER_TEMP/bind9\" log --format='%h %s' \"$old_tag..$new_tag\"", workflow)
        self.assertIn("Could not resolve upstream BIND release tags.", workflow)

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

    def test_candidate_workflow_uses_package_version_for_human_output(self) -> None:
        """Reviewer-facing text must use the same version form as pkg artifacts."""
        workflow = self.workflow_text()
        self.assertIn("package_version=", workflow)
        self.assertIn('version="${{ steps.candidate.outputs.package_version }}"', workflow)
        self.assertIn("old_package_version = ", workflow)
        self.assertIn("candidate_distversion=", workflow)
        self.assertIn("print(f\"old_version={old_package_version}\"", workflow)
        self.assertIn("print(f\"new_version={package_version}\"", workflow)
        self.assertIn('branch="sync/bind920/$distversion-$revision"', workflow)
        self.assertIn("GitHub Actions could not create the pull request automatically", workflow)
        self.assertIn("GITHUB_STEP_SUMMARY", workflow)
        self.assertNotIn("New BIND version: `%s_%s`", workflow)


if __name__ == "__main__":
    unittest.main()
