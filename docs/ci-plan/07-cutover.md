# Phase 7: Cutover

Status: stages defined; handoffs not written. Not built.

The new CI replaces the old in one switch, with the working feed and old clients'
migration intact (specification, "Cutover"; design, "Retirement map", "Frozen").
Local stages come first. Every remote stage needs its own explicit request.

## Contracts used

- Retirement map and frozen material (design).
- Ruleset settings: on `master`, both plugins' required statuses
  `Package build / <source_directory>`, PRs required and up-to-date branches; on
  `release/**`, pushes only by the sync app.
- The phase 1 client changes are merged and published before the next series.

## Stages

### 7.1 Retire the old CI (local)

Role: `master` holds only the new CI.

- Inputs: the retirement map.
- Outputs: removed workflows, `.github/ci/**`, `packages/`, test callers and
  development-release cleanup; no reference left to removed files.
- Depends on: phases 2–6.
- Done when: `.github/workflows/` holds exactly the four design files and a
  search finds no caller of a removed file.

### 7.2 Maintainer documentation (local)

Role: the guides describe the new CI.

- Inputs: the design; current `README.md` and `docs/*.md`.
- Outputs: updated `README.md`, `docs/building.md`, `docs/package-repository.md`,
  `docs/upstream-sync.md`, `docs/dhcp-interface-ha.md`, `docs/fork-model.md`;
  stale guides removed.
- Depends on: 7.1.
- Done when: no guide describes a retired workflow, runtime build or fingerprint.

### 7.3 Reconcile release branches (remote, on request)

Role: each release branch matches its published source once, before activation.

- Inputs: each branch head and the source of its current published version.
- Outputs: branch heads that hold the published source plus pending reviewed
  work, without rewriting history (merge commits), and a first `Master-Commit`
  trailer naming the `master` commit of stage 1.0, so the first carry starts
  from it.
- External inputs: published source references.
- Depends on: phase 1 changes merged where intended.
- Done when: for each plugin and series, the published version's source is an
  ancestor of the branch head, confirmed by the user.

### 7.4 Branch rules (remote, on request)

Role: GitHub enforces the PR requirements the new CI assumes.

- Outputs: rulesets as in the contracts above.
- Depends on: 7.3.
- Done when: the user confirms the rulesets in GitHub settings.

### 7.5 Activate (remote, on request)

Role: the new CI goes live in one push of `master`.

- Outputs: `master` pushed; the first runs observed by the user.
- Depends on: 7.1–7.4.
- Done when: the user confirms one production run publishes and indexes.

### 7.6 Legacy Release cleanup (remote, on request)

Role: remove the old pipeline's fingerprinted and development Releases.

- Inputs: a list, prepared locally for review, of Releases to remove, keeping
  every frozen item and each target's current and one rollback.
- Outputs: the approved Releases removed.
- Depends on: 7.5.
- Done when: the user approves the list and the removals match it.

## Decisions

- Activation is one push after branch rules exist, over enabling workflows
  separately, so old and new never run against the same change (2026-10-19).
