# Phase 08: Retire the old CI and document the new one

Status: built, awaiting user review (stages done: 08.1–08.4)
Before: the new CI (phases 01–06) and the client changes (phase 07) are built
beside the old workflows, helpers and runtime package definitions; the guides describe the old CI.
After: `.github/workflows/` holds exactly `bind-plugin.yml`,
`ha-dhcp-interface-plugin.yml`, `plugin-target.yml` and `package-index.yml`; `.github/ci/` and `packages/` are gone; the guides and
`AGENTS.md` describe the new CI. Everything is still local.

Rules: the new CI lives only in new files (`bind-plugin.yml`,
`ha-dhcp-interface-plugin.yml`, `plugin-target.yml`, `package-index.yml`). No
old CI file (the old workflows, `.github/ci/**`, `packages/`) is edited,
renamed or reused; the old CI stays untouched as reference until stage 08.1
deletes it. The specification's "Simplicity and limits", "Testing decisions" and
"Cutover" apply ([spec](../ci-rebuild-spec.md)). Nothing is run: review by
reading the diff and `git diff --check`. Commit once per phase with
`[skip ci]`; nothing is pushed or dispatched. `.github/ci-local/` is ignored
scratch and is never staged. `docs/superpowers/` is the user's scratch and is
not edited.

Shared references: design, "Retirement map" and "Frozen, never written by the
new CI" ([design](../ci-rebuild-design.md)); the built workflows. Documentation
describes what the workflows actually do; read them before writing.

## Stages

| Stage | Goal | Model | Effort | Order | Status |
| --- | --- | --- | --- | --- | --- |
| 08.1 | Remove the retired CI | haiku | low | first | done |
| 08.2 | Maintainer guides | sonnet | medium | after 08.1, parallel with 08.3 and 08.4 | done |
| 08.3 | User and package guides | sonnet | medium | after 08.1, parallel with 08.2 and 08.4 | done |
| 08.4 | Agent guide | sonnet | low | after 08.1, parallel with 08.2 and 08.3 | done |

## 08.1 Remove the retired CI

```
Goal:            Only the four design workflows remain, and nothing refers to a
                 removed CI file from a workflow.
Context:         Design, "Retirement map": every old workflow, packages/ and
                 .github/ci/** are retired; the new CI was built in new files
                 beside them, so nothing old was replaced in place. Mk/devel.mk
                 is upstream framework code and stays.
Contracts:       none.
Files:           git rm: .github/workflows/bind-plugin-release.yml,
                 ha-dhcp-interface-release.yml, repository-index.yml,
                 bind-runtime-package.yml,
                 bind-tools-release.yml, bind920-candidate.yml,
                 bind920-release.yml, pr-release-cleanup.yml,
                 repository-index-series.yml, upstream-sync.yml (all under
                 .github/workflows/), .github/ci/ (all tracked files), packages/
Work:            1. git rm -r the listed paths.
                 2. Search .github/workflows for the removed names,
                    ".github/ci/" and "packages/"; there must be no match.
External inputs: none.
Done when:       ls .github/workflows shows exactly the four files; the search
                 finds nothing.
Out of scope:    Documentation (08.2–08.4).
```

## 08.2 Maintainer guides

```
Goal:            docs/README.md, docs/building.md, docs/upstream-sync.md and
                 docs/fork-model.md describe the new CI and fork model.
Context:         The current files describe sealed controllers, runtime
                 packages, review PRs and fingerprints. The spec and design are
                 the source of truth; keep each guide short and operational.
Contracts:       Link targets used by other guides: docs/fork-model.md#branches,
                 docs/building.md (no anchor).
Files:           docs/README.md, docs/building.md, docs/upstream-sync.md,
                 docs/fork-model.md
Work:            1. building.md, "Building and releasing": a table of the four
                    workflows; requests (schedule/dispatch discovery, PR status,
                    `gh workflow run <entry> -f pr=N` candidates, promotion at
                    merge, `-f series=X.Y` production) with the gh commands;
                    the lifecycle jobs in order with what each does; versions
                    <series>_<n>; failures (stays failed, "Re-run failed jobs",
                    promotion failure means request production, conflicts fail
                    the target); local build `make -C dns/bind PLUGIN_DEVEL=
                    PLUGIN_ABI=<series> package`; rules for changing CI (built
                    from scratch, workflow first, script files only after the
                    written justification and approval, 30 lines, no tests in
                    CI, [skip ci], nothing hard-coded).
                 2. upstream-sync.md, "Upstream changes": new series (previous
                    branch plus upstream merge, <series>_1); upstream BIND
                    changes (discovery compares the upstream path commit with
                    the record, every change taken); conflicts; environment
                    changes start no build.
                 3. fork-model.md: purpose; package table; branches table
                    (master development, release/* pipeline-only, upstream);
                    the Master-Commit carry; active series and retirement;
                    independence of the two plugins. Heading "## Branches".
                 4. docs/README.md: one line per guide, plus the spec, design
                    and this plan directory.
External inputs: none.
Done when:       No guide describes a retired workflow, helper, seal, runtime
                 build or fingerprinted snapshot; each request path is described
                 once.
Out of scope:    package-repository.md and README.md (08.3), AGENTS.md (08.4),
                 docs/ci-*.md and this plan.
```

## 08.3 User and package guides

```
Goal:            docs/package-repository.md, README.md and the HA guides
                 describe the signed feed, Releases, host setup, series upgrades
                 and credentials as built.
Context:         Phase 07 built the client changes; phases 04–05 the Releases
                 and feed. The current package-repository.md describes runtime
                 channels, fingerprinted snapshots and the retired installer.
Contracts:       Keep the anchor docs/package-repository.md#ha-dhcp-interface
                 (linked from the HA guides) and README.md's
                 "Configure the package repository" heading.
Files:           docs/package-repository.md, README.md,
                 docs/dhcp-interface-ha.md (introduction only),
                 net/dhcp-interface-ha/README.md (two sentences)
Work:            1. package-repository.md sections: Feed (URL, ${ABI}, contents,
                    frozen legacy feeds and runtime packages); Releases (table of
                    current and versioned tags for both plugins, source Releases
                    and tags, the build record); Host setup
                    (install-repository.sh, fingerprint, pkg install, native
                    BIND); Series upgrades (the three steps for both plugins,
                    managed shapes, logs, a missed migration); BIND package
                    transactions (zone preservation, HA-pair order); Rollback (a
                    maintainer action using the previous versioned Release's
                    archive); Signing and credentials (RP_PKG_SIGNING_KEY,
                    distribution app variable and secret, sync app variable and
                    secret, scopes); "## HA DHCP Interface".
                 2. README.md: replace "The repository also supplies the BIND
                    runtime packages…" with BIND coming from OPNsense's packages
                    and the declared minimum.
                 3. dhcp-interface-ha.md: replace the introduction's release
                    paragraph with master development, release branch,
                    <series>_<n>, links to fork-model.md#branches and
                    building.md. Leave the dated verification record.
                 4. net/dhcp-interface-ha/README.md: the package-repository link
                    sentence covers the feed and series upgrades; the PHP test
                    note says the suite runs locally and CI runs no tests.
External inputs: none.
Done when:       No retired installer, runtime channel or snapshot remains in
                 these files; links resolve.
Out of scope:    Maintainer guides (08.2), AGENTS.md (08.4).
```

## 08.4 Agent guide

```
Goal:            The repository's AGENTS.md matches the new CI and branch model.
Context:         Its "Design at a glance", "Repository map", CI rules and
                 "Documentation updates" describe the control plane, sealed
                 inputs, runtime packages and .github/ci tests.
Contracts:       Keep: the no-PRs-upstream rule, os-bind replacement policy,
                 26.1.11_10 floor, narrow fork changes, signed repository
                 boundary, .github/ci-local rule, temporary fixture rule,
                 language and functional-style rules.
Files:           AGENTS.md
Work:            1. Rewrite "Design at a glance": master development and CI,
                    pipeline-only release branches, independent lifecycles.
                 2. Repository map: the two plugin directories, the four
                    workflows, scripts/install-repository.sh, docs/.
                 3. Replace the CI rules with: develop on master and never push
                    to release/*; plugins stay independent; follow the CI spec
                    (workflow first, nothing hard-coded, no recovery jobs, stop
                    before scripts over 30 lines, script files only after the
                    written justification and user approval); CI runs no test suites, plugin
                    suites run locally, review by diff, [skip ci].
                 4. Update "Documentation updates" to the new guide names.
External inputs: none.
Done when:       No mention of sealed inputs, runtime pipelines, .github/ci/ or
                 packages/; the kept rules are intact.
Out of scope:    Other guides.
```

## Notes

- `docs/plans/` is untracked and is not part of this phase (2026-10-20).
- Built 2026-10-20: 08.1 directly (git rm of the ten old workflows,
  `.github/ci/**` and `packages/`), 08.2–08.4 by parallel sonnet agents. The
  rollback procedure in `package-repository.md` was derived from the workflows
  (manual swap of the current Release's archive) and needs the user's review.
  `AGENTS.md` dropped the old "do not weaken provenance, pin or fingerprint
  checks" rule with the retired machinery; the `os-bind` conflict rule stays
  (2026-10-20).
