#!/usr/bin/env python3
"""Local regression coverage for BIND update candidate handling."""

from __future__ import annotations

from module_fixtures import *

from package_fixtures import BIND_PROFILE, write_json
from workflow_fixtures import assert_pinned_actions


MODULE_PATH = CI / "bind920_candidate.py"
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
CANDIDATE_WORKFLOW = REPOSITORY_ROOT / ".github/workflows/bind920-candidate.yml"
bind920_candidate = load_module("bind920_candidate", MODULE_PATH, register=True)


Bind920Profile = bind920_candidate.Bind920Profile
CandidateProfile = bind920_candidate.CandidateProfile
assess_candidate = bind920_candidate.assess_candidate
candidate_is_newer = bind920_candidate.candidate_is_newer
parse_bind920_makefile = bind920_candidate.parse_bind920_makefile
render_commit_log_markdown = bind920_candidate.render_commit_log_markdown
upstream_bind_release_tag = bind920_candidate.upstream_bind_release_tag


class Bind920CandidateTest(unittest.TestCase):
    def test_parse_bind920_makefile_reads_distversion_and_portrevision(self) -> None:
        """A Ports recipe version bump must update both version inputs."""
        text = "PORTNAME= bind920\nDISTVERSION= 9.20.27\nPORTREVISION= 2\n"
        self.assertEqual(("9.20.27", 2), parse_bind920_makefile(text))


    def test_candidate_comparison_rejects_duplicates_and_downgrades(self):
        current = Bind920Profile("repo", "old", "m1", "d1", "9.20.26", 1)
        for version, revision, expected in [
            ('9.20.26', 1, False), ('9.20.26', 2, True), ('9.20.26', 0, False),
            ('9.20.27', 0, True), ('9.20.25', 9, False),
        ]:
            with self.subTest(version=version, revision=revision):
                candidate = CandidateProfile("repo", "new", "m2", "d2", version, revision, "main")
                self.assertEqual(expected, candidate_is_newer(current, candidate))


    def test_candidate_is_newer_rejects_wrong_bind_series(self) -> None:
        """The updater must not silently move os-bind-rp to another BIND series."""
        current = Bind920Profile("repo", "old", "m1", "d1", "9.20.26", 1)
        candidate = CandidateProfile("repo", "new", "m2", "d2", "9.21.0", 0, "main")
        with self.assertRaisesRegex(ValueError, "9.20"):
            candidate_is_newer(current, candidate)

    def test_assessment_classification_and_signals(self):
        for notes, diff, classification, signals in [
            ('Security fix: CVE-2026-1234 denial of service in resolver.', '', 'security', ['CVE-2026-1234']),
            ('Bug fixes include named crash and SERVFAIL regression.', '', 'critical-bugfix', ['crash']),
            ('Maintenance release.', '+LIB_DEPENDS+= libnew.so:security/newlib\n', 'risky', ['dependency change']),
            ('Maintenance release with documentation and minor bug fixes.', '', 'routine', []),
        ]:
            with self.subTest(classification=classification):
                result = assess_candidate('9.20.26', '9.20.27', notes, diff)
                self.assertEqual(classification, result.classification)
                for signal in signals:
                    self.assertIn(signal, result.signals)


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


    def test_upstream_bind_release_tag_strips_portrevision(self) -> None:
        """Upstream BIND tags must be based on BIND versions, not FreeBSD package revisions."""
        self.assertEqual("v9.20.26", upstream_bind_release_tag("9.20.26_2"))
        self.assertEqual("v9.20.27", upstream_bind_release_tag("9.20.27"))

    def test_render_commit_log_markdown_lists_subjects_or_empty_fallback(self):
        for commits, expected in [
            ('abc1234 Fix resolver crash\ndef5678 Improve DNSSEC validation\n',
             '- `abc1234` Fix resolver crash\n- `def5678` Improve DNSSEC validation\n'),
            ('\n', '- Could not resolve upstream BIND release tags.\n'),
        ]:
            with self.subTest(commits=commits):
                self.assertEqual('### Upstream BIND Changes\n\n' + expected, render_commit_log_markdown(
                    'Upstream BIND Changes', commits, 'Could not resolve upstream BIND release tags.'))


    def test_update_profile_cli_hashes_candidate_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            current = write_json(directory / 'bind920.json', BIND_PROFILE)
            makefile, distinfo = directory / 'Makefile', directory / 'distinfo'
            makefile.write_text('PORTNAME= bind920\nDISTVERSION= 9.20.27\n')
            distinfo.write_text('TIMESTAMP = 1\nSHA256 (bind-9.20.27.tar.xz) = abc123\nSIZE (bind-9.20.27.tar.xz) = 1\n')
            result = subprocess.run([
                sys.executable, str(MODULE_PATH), 'update-profile', '--current', str(current),
                '--ports-repository', BIND_PROFILE['ports_repository'], '--ports-commit', 'f' * 40,
                '--makefile', str(makefile), '--distinfo', str(distinfo), '--output', str(current),
            ], capture_output=True, text=True)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(list(BIND_PROFILE), list(json.loads(current.read_text())))
            self.assertEqual(dict(BIND_PROFILE, ports_commit='f' * 40, distversion='9.20.27', portrevision=0,
                                  makefile_sha256=hashlib.sha256(makefile.read_bytes()).hexdigest(),
                                  distinfo_sha256=hashlib.sha256(distinfo.read_bytes()).hexdigest()),
                             json.loads(current.read_text()))


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

    def test_assess_cli_classifies_notes_and_makefile_dependency_drift(self):
        for classification in ('critical-bugfix', 'risky'):
            with self.subTest(classification=classification), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                changelog, diff, output = directory / 'notes', directory / 'diff', directory / 'assessment.md'
                changelog.write_text('Resolver crash fixed.' if classification == 'critical-bugfix' else 'Maintenance release.')
                diff.write_text('')
                command = [sys.executable, str(MODULE_PATH), 'assess', '--old-version', '9.20.26',
                           '--new-version', '9.20.27', '--changelog', str(changelog),
                           '--ports-diff', str(diff), '--output', str(output)]
                if classification == 'risky':
                    # No diff context: only the complete Makefiles expose dependency drift.
                    old, new = directory / 'old.Makefile', directory / 'new.Makefile'
                    old.write_text('LIB_DEPENDS= liba.so:devel/a \\\n  libf.so:devel/f\n')
                    new.write_text(old.read_text().replace('libf.so:devel/f', 'libg.so:devel/g'))
                    command += ['--old-makefile', str(old), '--new-makefile', str(new)]
                result = subprocess.run(command, capture_output=True, text=True)
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertIn('classification: ' + classification, output.read_text())


class Bind920CandidateWorkflowTest(unittest.TestCase):
    def workflow_text(self) -> str:
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
        for name in ("test_bind920_candidate.py", "test_bind920_reuse.py", "test_release_channel_provenance.py"):
            self.assertLess(workflow.index("python .github/ci/ci-tests/" + name), workflow.index("git clone --filter=blob:none"))

    def test_bind_pull_request_workflow_covers_profile_only_candidates_without_pip(self) -> None:
        """Generated bind920 profile PRs need checks without adding package-registry egress."""
        workflow = (REPOSITORY_ROOT / ".github/workflows/bind-tests.yml").read_text(encoding="utf-8")
        helper_job = workflow.split("  ci-helpers:", 1)[1].split("  discover:", 1)[0]
        bind_job = workflow.split("  test:", 1)[1]

        self.assertIn("- '.resolver-plugins/bind920.json'", workflow)
        self.assertIn("python .github/ci/ci-tests/test_bind920_candidate.py", helper_job)
        self.assertIn("python .github/ci/ci-tests/test_bind920_reuse.py", helper_job)
        self.assertIn("python .github/ci/ci-tests/test_release_channel_provenance.py", helper_job)
        self.assertIn("python .github/ci/bind/bind920_profile.py .resolver-plugins/bind920.json package_version", helper_job)
        self.assertNotIn("pip install", helper_job)
        self.assertIn("needs.changes.outputs.bind_source == 'true'", bind_job)

    def test_candidate_workflow_never_publishes_packages(self) -> None:
        """Candidate review PRs must not cross the publication boundary."""
        workflow = self.workflow_text()
        self.assertNotIn("release upload", workflow)
        self.assertNotIn("release_channel.py publish", workflow)

    def test_candidate_workflow_includes_ports_commit_subjects_in_pr_body(self) -> None:
        """Review PRs must show the FreeBSD Ports commits behind the candidate."""
        workflow = self.workflow_text()
        self.assertIn('git -C "$RUNNER_TEMP/freebsd-ports" log --format=\'%h %s\' "$current_commit..$candidate_commit" -- dns/bind920 > "$RUNNER_TEMP/ports.log"', workflow)
        self.assertIn('printf \'ports_log=%s\\n\' "$RUNNER_TEMP/ports.log"', workflow)
        self.assertIn('PORTS_LOG: ${{ steps.ports.outputs.ports_log }}', workflow)
        commands = [shlex.split(command) for command in re.findall(
            r'^ +python3 \.github/ci/bind920_candidate\.py render-commit-log (.+)$',
            workflow.replace('\\\n', ''), re.MULTILINE)]
        ports = [args for args in commands if args[args.index('--commits') + 1] == '$PORTS_LOG']
        self.assertEqual(1, len(ports))
        self.assertEqual('$RUNNER_TEMP/ports-changes.md', ports[0][ports[0].index('--output') + 1])
        body = workflow.split('} > "$RUNNER_TEMP/pr-body.md"', 1)[0].rsplit('          {\n', 1)[1]
        self.assertIn('cat "$RUNNER_TEMP/ports-changes.md"', body)

    def test_candidate_workflow_includes_upstream_bind_commit_subjects_in_pr_body(self) -> None:
        """Review PRs must show upstream BIND commits between the old and new release tags."""
        workflow = self.workflow_text()
        self.assertIn("https://github.com/isc-projects/bind9.git", workflow)
        self.assertIn("upstream-tag", workflow)
        self.assertIn("git -C \"$RUNNER_TEMP/bind9\" log --format='%h %s' \"$old_tag..$new_tag\"", workflow)

    def test_candidate_workflow_uses_pinned_actions(self) -> None:
        """Workflow actions must stay pinned to immutable SHAs."""
        workflow = self.workflow_text()
        assert_pinned_actions(workflow)

    def test_candidate_workflow_checks_empty_index_before_commit(self) -> None:
        """Only an actual empty candidate diff may skip PR branch publication."""
        workflow = self.workflow_text()
        self.assertIn("git diff --cached --quiet", workflow)
        self.assertNotIn("git commit -m \"ci(bind): update bind920 to ${version}_${revision}\" || exit 0", workflow)

    def test_candidate_workflow_uses_package_version_for_human_output(self) -> None:
        """Reviewer-facing text must use the same version form as pkg artifacts."""
        workflow = self.workflow_text()
        self.assertIn('version="${{ steps.candidate.outputs.package_version }}"', workflow)
        self.assertIn("print(f\"old_version={old_package_version}\"", workflow)
        self.assertIn("print(f\"new_version={package_version}\"", workflow)
        self.assertIn('branch="sync/bind920/$distversion-$revision"', workflow)
        self.assertIn("GITHUB_STEP_SUMMARY", workflow)


if __name__ == "__main__":
    unittest.main()
