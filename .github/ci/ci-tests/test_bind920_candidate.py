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


    def test_update_profile_cli_hashes_candidate_files(self):
        for version, revision in [('9.20.27', 0), ('9.20.26', 2)]:
            with self.subTest(version=version, revision=revision), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                current = write_json(directory / 'bind920.json', dict(BIND_PROFILE, portrevision=1))
                makefile, distinfo = directory / 'Makefile', directory / 'distinfo'
                makefile.write_text(f'PORTNAME= bind920\nDISTVERSION= {version}\n' +
                                    (f'PORTREVISION= {revision}\n' if revision else ''))
                distinfo.write_text(f'TIMESTAMP = 1\nSHA256 (bind-{version}.tar.xz) = abc123\nSIZE (bind-{version}.tar.xz) = 1\n')
                result = subprocess.run([
                    sys.executable, str(MODULE_PATH), 'update-profile', '--current', str(current),
                    '--ports-repository', BIND_PROFILE['ports_repository'], '--ports-commit', 'f' * 40,
                    '--makefile', str(makefile), '--distinfo', str(distinfo), '--output', str(current),
                ], capture_output=True, text=True)
                self.assertEqual(0, result.returncode, result.stderr)
                profile = json.loads(current.read_text())
                self.assertEqual(dict(BIND_PROFILE, ports_commit='f' * 40, distversion=version, portrevision=revision,
                                      makefile_sha256=hashlib.sha256(makefile.read_bytes()).hexdigest(),
                                      distinfo_sha256=hashlib.sha256(distinfo.read_bytes()).hexdigest()), profile)
                package_version = profile['distversion'] + (f"_{profile['portrevision']}" if profile['portrevision'] else '')
                result = subprocess.run([
                    sys.executable, str(MODULE_PATH), 'upstream-tag', '--version', package_version,
                ], capture_output=True, text=True)
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual(f'v{version}\n', result.stdout)


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

    def test_bind_pull_request_workflow_checks_profile_only_candidates(self) -> None:
        workflow = (REPOSITORY_ROOT / ".github/workflows/bind-tests.yml").read_text(encoding="utf-8")
        helper_job = workflow.split("  ci-helpers:", 1)[1].split("  discover:", 1)[0]

        self.assertIn("- '.resolver-plugins/bind920.json'", workflow)
        self.assertIn("python .github/ci/ci-tests/test_bind920_candidate.py", helper_job)
        self.assertIn("python .github/ci/ci-tests/test_bind920_reuse.py", helper_job)
        self.assertIn("python .github/ci/ci-tests/test_release_channel_provenance.py", helper_job)

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
