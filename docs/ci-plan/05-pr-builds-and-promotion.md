# Phase 5: PR builds and promotion

Status: stages defined; handoffs not written. Not built.

Plugins are developed on `master`. A PR that changes a plugin's directory is
blocked until the maintainer requests a build of its current head; merging
promotes that build into the active series without rebuilding (specification,
"Branches", "PR builds and promotion"; design, "Entry jobs", "`prepare`
outputs").

## Contracts used

- Status context `Package build / <source_directory>`, written with
  `GITHUB_TOKEN` (`statuses: write`) by a job that runs no package code.
- Entry events: `pull_request_target` opened, synchronize, reopened and closed on
  `master`; `workflow_dispatch` with `pr`.
- Lifecycle purposes `candidate` and `promote`, always for the active series;
  `source_sha` is the PR head; the carry and `Master-Commit` trailer from
  phase 2; the `qualified-` artifact named by `source_sha`; `publish` and
  `sync-source` from phase 3.
- Concurrency group `candidate-<component>-<pr>` for candidates.
- Applicability by the PR's changed files under `source_directory`.

## Stages

### 5.1 PR status

Role: a PR to `master` that changes a plugin starts blocked for that plugin and
passes for the other.

- Inputs: the PR event (number, head sha); the PR's changed files.
- Outputs: per plugin, status `pending` on the head when its directory changed,
  otherwise success "not applicable".
- Contracts: no checkout of PR code; one `gh` query for the changed files.
- External inputs: the event payload and the changed-files response.
- Depends on: nothing.
- Done when: the diff shows one small job per entry writing its own status, with
  no other permissions.

### 5.2 Candidate build

Role: a maintainer's build request for a PR produces a qualified candidate of
the active series and marks that head as passing.

- Inputs: `workflow_dispatch` with `pr`; the PR's head sha (read with `gh`).
- Outputs: the `qualified-` artifact for the head; status `success` on that head
  after qualification, `failure` when the lifecycle fails.
- Contracts: trusted CI from `master`; `prepare` carries the PR's changes onto
  the active release branch head; the lifecycle stops after `qualify`; the status
  is written to the head that was built, not the PR's current head.
- External inputs: the PR record (head, state).
- Depends on: phase 2, 5.1.
- Done when: the diff shows the request path, the stop after `qualify`, and the
  status written to the built head.

### 5.3 Promotion on merge

Role: a merged PR's candidate is published without rebuilding.

- Inputs: `pull_request_target` closed with `merged` and changes under
  `source_directory`; the PR head sha; the `qualified-` artifact for it.
- Outputs: the same publication as a production build (phase 3); the release
  branch advanced to the candidate's prepared commit.
- Contracts: `prepare` returns `promote` when the artifact exists and its version
  is newer than current, and fails with a reason otherwise; the maintainer then
  requests a production build, which carries `master`'s head. `build` and
  `qualify` are skipped; `publish` and `sync-source` use the candidate's artifact.
- External inputs: the artifact listing and the current Release.
- Depends on: 5.2, phase 3.
- Done when: the diff shows one lookup by name, the two failure reasons, and no
  rebuild path.

## Decisions

- PRs go to `master` and are carried into the active release branch, over PRs
  to release branches (2026-10-19).
