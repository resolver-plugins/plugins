#!/usr/bin/env python3
"""Local regression coverage for BIND update candidate handling."""

from __future__ import annotations

from module_fixtures import *

from package_fixtures import PLUGIN_PACKAGE, make_ports_recipe
from workflow_fixtures import assert_pinned_actions


MODULE_PATH = CI / "bind920_candidate.py"
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
CANDIDATE_WORKFLOW = REPOSITORY_ROOT / ".github/workflows/bind920-candidate.yml"
bind920_candidate = load_module("bind920_candidate", MODULE_PATH, register=True)


Bind920Profile = bind920_candidate.Bind920Profile
CandidateProfile = bind920_candidate.CandidateProfile
assess_candidate = bind920_candidate.assess_candidate
candidate_is_newer = bind920_candidate.candidate_is_newer


class Bind920CandidateTest(unittest.TestCase):
    def test_candidate_comparison_rejects_duplicates_and_downgrades(self):
        current = Bind920Profile("repo", "old", "m1", "d1", "9.20.26", 1)
        for version, revision, expected in [
            ('9.20.26', 1, False), ('9.20.26', 2, True), ('9.20.26', 0, False),
            ('9.20.27', 0, True), ('9.20.25', 9, False),
        ]:
            with self.subTest(version=version, revision=revision):
                candidate = CandidateProfile("repo", "new", "m2", "d2", version, revision, "main")
                self.assertEqual(expected, candidate_is_newer(current, candidate))


    def test_assessment_classification_and_signals(self):
        for notes, diff, classification, signals in [
            ('Security fix: CVE-2026-1234 denial of service in resolver.', '', 'security', ['CVE-2026-1234']),
            ('Bug fixes include named crash and SERVFAIL regression.', '', 'critical-bugfix', ['crash']),
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


    def test_update_approval_cli_preserves_plugin_and_generates_exact_recipe(self):
        for package_version in ('9.20.27', '9.20.26_2'):
            with self.subTest(version=package_version), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                old_ports, old_approval, old_profile = make_ports_recipe(directory / 'old', '9.20.26_1')
                ports, approval, expected_profile = make_ports_recipe(directory / 'candidate', package_version)
                current = directory / 'plugin.Makefile'
                preserved = '# Runtime approval follows the package definition.\n' + PLUGIN_PACKAGE
                current.write_text(preserved + old_approval)
                result = subprocess.run([
                    sys.executable, str(MODULE_PATH), 'update-approval', '--makefile', str(current),
                    '--ports-commit', expected_profile['ports_commit'], '--version', package_version,
                ], capture_output=True, text=True)
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual(preserved + approval, current.read_text())
                generated = bind920_candidate.bind920_profile.resolve_profile(
                    bind920_candidate.bind920_profile.approval_from_makefile(current.read_text()), repository=ports)
                self.assertEqual(expected_profile, generated)
                old_input = directory / 'old-approval.mk'
                old_input.write_text(old_approval)
                self.assertEqual(old_profile, vars(bind920_candidate.current_from_approval(old_input, old_ports)))
                result = subprocess.run([sys.executable, str(MODULE_PATH), 'upstream-tag',
                    '--version', package_version], capture_output=True, text=True)
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual('v' + package_version.split('_')[0] + '\n', result.stdout)

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
                    old.write_text('CONFIGURE_ARGS+= --with-a \\\n  --with-b\nCONFIGURE_ARGS+= --enable-fixed\n')
                    new.write_text(old.read_text().replace('--with-b', '--with-c'))
                    command += ['--old-makefile', str(old), '--new-makefile', str(new)]
                result = subprocess.run(command, capture_output=True, text=True)
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertIn('classification: ' + classification, output.read_text())
                if classification == 'risky':
                    self.assertIn('dependency change', output.read_text())


class Bind920CandidateWorkflowTest(unittest.TestCase):
    def workflow_text(self) -> str:
        return CANDIDATE_WORKFLOW.read_text(encoding="utf-8")

    def test_candidate_workflow_runs_candidate_reuse_and_provenance_checks(self) -> None:
        workflow = self.workflow_text()
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("python .github/ci/ci-tests/test_bind920_candidate.py", workflow)
        self.assertIn("python .github/ci/ci-tests/test_bind920_reuse.py", workflow)
        self.assertIn("python .github/ci/ci-tests/test_release_channel_provenance.py", workflow)

    def test_candidate_workflow_never_publishes_packages(self) -> None:
        """Candidate review PRs must not cross the publication boundary."""
        workflow = self.workflow_text()
        self.assertNotIn("release upload", workflow)
        self.assertNotIn("release_channel.py publish", workflow)
        assert_pinned_actions(workflow)

    def test_candidate_workflow_includes_ports_commit_subjects_in_pr_body(self) -> None:
        """Review PRs must show the FreeBSD Ports commits behind the candidate."""
        workflow = self.workflow_text()
        body = workflow.split('} > "$RUNNER_TEMP/pr-body.md"', 1)[0].rsplit('          {\n', 1)[1]
        self.assertIn('cat "$RUNNER_TEMP/ports-changes.md"', body)

if __name__ == "__main__":
    unittest.main()
