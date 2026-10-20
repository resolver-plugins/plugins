# Phase 06: Entries

Status: planned
Before: the lifecycle and index work (phases 02–05); both entry workflows are
declared with stub bodies (phase 01).
After: each plugin's entry runs daily discovery, accepts production and
candidate requests, writes PR statuses, and promotes merged candidates. The new
CI is complete on `master`; the old workflows still exist until phase 08.

Rules: the new CI lives only in new files (`bind-plugin.yml`,
`ha-dhcp-interface-plugin.yml`, `plugin-target.yml`, `package-index.yml`). No
old CI file (the old workflows, `.github/ci/**`, `packages/`) is edited,
renamed or reused; the old CI stays untouched as reference until stage 08.1
deletes it. The specification's "Simplicity and limits", "Failed jobs stay failed"
and "Testing decisions" apply ([spec](../ci-rebuild-spec.md)). Nothing is run:
review by reading the diff and `git diff --check`. Stop and ask before any
inline or CI script exceeds 30 physical lines. Add no script
files: every operation is a workflow step using `git`, `gh`, `jq`, `make` or
`pkg`. A script file needs the spec's written justification ("Script files are
the last resort"), reviewed until no simpler alternative remains and approved
by the user before it is created. Commit once per phase with
`[skip ci]`; nothing is pushed or dispatched.

Shared references: design, "Entry jobs" and "Upstream discovery"; spec,
"Delivery events", "Active series" and "PR builds and promotion". Job names,
conditions, env and outputs in
[01-ci-structure.md](01-ci-structure.md#entry-interface). Both entry files get
the same bodies; only the policy values (already in each file) differ, so each
stage edits both files the same way. Status context:
`Package build / $SOURCE_DIRECTORY`.

## Stages

| Stage | Goal | Model | Effort | Order | Status |
| --- | --- | --- | --- | --- | --- |
| 06.1 | PR status | haiku | low | first | planned |
| 06.2 | Discovery | sonnet | medium | after 06.1 | planned |
| 06.3 | Candidate and promotion requests | sonnet | medium | after 06.2 | planned |

## 06.1 PR status

```
Goal:            On a PR to master opened, updated or reopened, the status job
                 writes pending when the PR changes the plugin directory and
                 success "Not applicable" otherwise.
Context:         Spec: opening or updating a PR runs only a cheap status job;
                 each plugin reports its own required status; the status writer
                 runs no package code. Design: applicability from `gh pr view
                 --json files`.
Contracts:       pending description "Request a package build for this head";
                 success description "Not applicable"; written to HEAD_SHA.
Files:           .github/workflows/bind-plugin.yml,
                 .github/workflows/ha-dhcp-interface-plugin.yml (status body)
Work:            1. files = gh pr view "$PR" --repo "$GITHUB_REPOSITORY" --json
                    files --jq '.files[].path'.
                 2. grep -q "^$SOURCE_DIRECTORY/" decides the state.
                 3. gh api "repos/$GITHUB_REPOSITORY/statuses/$HEAD_SHA" -f
                    state -f context -f description.
External inputs: the event payload and the changed-files response.
Done when:       No checkout; body under 15 lines.
Out of scope:    Candidate status (06.3).
```

## 06.2 Discovery

```
Goal:            The discover job selects the active series when it needs a
                 production build, or the series a dispatch names.
Context:         Design, "Entry jobs": a target needs work when its current
                 Release is missing (new series), when follow_upstream is set and
                 the upstream package path changed since the current build
                 record, or when the dispatch asked for that series. Spec:
                 cheap selection happens before any VM; master changes are not
                 discovered (promotion and production requests carry them).
Contracts:       Outputs applies (true|false) and series. Reason in
                 $GITHUB_STEP_SUMMARY.
Files:           both entry workflows (discover body)
Work:            1. active series by the active series rule of 02.1 (usable
                    series only).
                 2. SERIES set: applies=true with that series, reason
                    "Production build of <series> requested" (prepare rejects a
                    non-active one).
                 3. Otherwise: current = CURRENT_TAG with {series} replaced by
                    the active series; record = its build-record.json (gh
                    release download --output -); "release not found" or "no
                    assets match" means {} (no record), any other error fails.
                 4. upstream = for FOLLOW_UPSTREAM=true, `gh api
                    "repos/opnsense/plugins/commits?sha=stable/$active&path=$SOURCE_DIRECTORY&per_page=1"
                    --jq '.[0].sha // empty'`, else empty.
                 5. No work when the record exists and upstream equals its
                    upstream_package_commit (HA: both empty): applies=false and
                    a summary line. Otherwise applies=true with the reason (no
                    record, or upstream changed).
External inputs: core tags, the current record, the upstream commits API.
Done when:       No hard-coded series; no VM or target when nothing needs
                 work. If the body needs more than 30 lines, stop and ask.
Out of scope:    Request handling (06.3).
```

## 06.3 Candidate and promotion requests

```
Goal:            The request job resolves a candidate request or a merged PR's
                 promotion, and candidate-status writes the result on the built
                 head.
Context:         Spec: the maintainer requests a build for a PR; it runs trusted
                 CI from master and sets the status to success on the head it
                 built; a build for an older head never satisfies a newer one.
                 At merge, promotion finds the candidate by the merged head.
                 Candidates, promotion and production target only the active
                 series.
Contracts:       request outputs: applies, purpose (candidate|promote), series
                 (active), source_sha, pr, group (candidate-<component>-<pr> or
                 plugin-<component>-<series>). candidate-status writes success
                 "Candidate built and qualified" or failure "Candidate build
                 <result>", with target_url the run URL.
Files:           both entry workflows (request and candidate-status bodies)
Work:            request:
                 1. active series by the active series rule of 02.1.
                 2. pull_request_target: if the PR's files do not include the
                    plugin directory, applies=false and exit 0; else promote
                    with MERGED_HEAD and group plugin-<component>-<active>.
                 3. Dispatch: head = gh pr view --json state,headRefOid
                    selecting OPEN; empty: ::error:: "PR #N is not open"; exit
                    1; else candidate with that head and group
                    candidate-<component>-<pr>.
                 4. Write the outputs with one printf.
                 candidate-status:
                 5. state from RESULT; gh api statuses on HEAD_SHA.
External inputs: the PR record and file list, core tags.
Done when:       Status always on the built head; promotion only for PRs that
                 changed the plugin.
Out of scope:    Branch rules requiring the status (phase 09).
```

## Notes

- PRs go to `master` and are carried into the active release branch, over PRs
  to release branches (2026-10-19).
- `gh pr view --json files` returns at most 100 files; a PR changing more files
  than that may be misjudged. Accepted as rare (2026-10-20).
