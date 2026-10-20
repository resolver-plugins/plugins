# Phase 09: Activate

Status: planned
Before: phases 01–08 are committed locally on the plan branch; GitHub still
runs the old CI from `master`, and the release branches have no
`Master-Commit` trailer.
After: the release branches hold their published source and the first
trailer, branch rules enforce the PR and release-branch model, `master` runs
the new CI, a first production run has published and indexed, and the old
pipeline's leftover Releases are cleaned up.

Rules: the new CI lives only in new files (`bind-plugin.yml`,
`ha-dhcp-interface-plugin.yml`, `plugin-target.yml`, `package-index.yml`). No
old CI file (the old workflows, `.github/ci/**`, `packages/`) is edited,
renamed or reused; the old CI stays untouched as reference until stage 08.1
deletes it. Every stage here changes GitHub. **Each stage needs its own explicit
user request.** Before that request, the agent may only read (git fetch, gh
api GET) and prepare the exact commands; it then stops and shows them. Never
force-push or rewrite history. The specification's "Cutover" section applies
([spec](../ci-rebuild-spec.md)).

Shared references: design, "Frozen, never written by the new CI" and
"`sync-source`" ([design](../ci-rebuild-design.md)); the built workflows.

## Stages

| Stage | Goal | Model | Effort | Order | Status |
| --- | --- | --- | --- | --- | --- |
| 09.1 | Reconcile release branches | opus | high | first, on request | planned |
| 09.2 | Branch rules | sonnet | medium | after 09.1, on request | planned |
| 09.3 | Activate master | sonnet | medium | after 09.2, on request | planned |
| 09.4 | Legacy Release cleanup | sonnet | medium | after 09.3, on request | planned |

## 09.1 Reconcile release branches

```
Goal:            Each existing release branch contains its published source,
                 carries no workflow files, and the active series' branches end
                 with a first Master-Commit trailer.
Context:         Spec, "Branches": before cutover, reconcile each existing plugin
                 branch once with its actual published source, keeping fork
                 features, upgrade hooks and pending reviewed changes, without
                 rewriting history; CI entry points live only on master. Design:
                 the carry starts from the newest Master-Commit trailer on the
                 release branch. The master commit 8c640b214 ("Bring master's
                 BIND plugin up to date with 26.7_9", phase 07's starting point)
                 is the point both
                 plugins' directories last matched their release branches, so it
                 is the first carried commit; it must stay reachable on master
                 (merge the plan branch without squashing, 09.3).
Contracts:       Branches release/bind-rp/26.1, release/bind-rp/26.7,
                 release/dhcp-interface-ha/26.7. Published source: the commit of
                 each current Release's newest source tag in
                 resolver-plugins/plugins (26.7_9 for BIND 26.7 is 1c241209d).
                 Trailer commit (active series only): an empty commit "Start
                 carrying plugin changes from master" with trailer
                 "Master-Commit: 8c640b214…" (full sha).
Files:           none in this repository's working tree; remote branches only.
Work:            1. For each branch, find its published source commit and check
                    git merge-base --is-ancestor <source> <branch>. Report the
                    result.
                 2. Where it is not an ancestor, prepare a merge of the
                    published source into the branch (merge commit).
                 3. On all three branches, prepare a commit "Remove workflow
                    files from the release branch" deleting .github/workflows/
                    (26.7: the test callers bind-tests.yml and
                    ha-dhcp-interface-tests.yml; 26.1: bind-tests.yml and the
                    old package-release.yml, which holds RP_PKG_SIGNING_KEY and
                    contents: write behind pull_request_target).
                 4. On the 26.7 branches, prepare the trailer commit.
                 5. Show the user the commits and the push commands; push only
                    after the request, with the maintainer's credentials (the
                    sync-app rule does not exist yet).
External inputs: the remote branches, tags and Releases.
Done when:       The user confirms, for each branch, that the published source
                 is an ancestor of the head and that the 26.7 heads carry the
                 trailer.
Out of scope:    26.1 trailer (retired series are locked and never carried).
```

## 09.2 Branch rules

```
Goal:            GitHub enforces the model the new CI assumes.
Context:         Spec, "PR builds and promotion": master requires the status on
                 the current head and up-to-date PRs; release branches accept
                 pushes only from the pipeline.
Contracts:       Ruleset on master: pull request required; required statuses
                 "Package build / dns/bind" and "Package build /
                 net/dhcp-interface-ha"; branches must be up to date; repository
                 admins may bypass (needed by 09.3). Ruleset on release/**:
                 updates and creation restricted to the sync GitHub App; no
                 deletion or force push.
Files:           none.
Work:            1. Read the existing rulesets (gh api
                    repos/resolver-plugins/plugins/rulesets).
                 2. Prepare the two ruleset JSON bodies and gh api commands; stop
                    for the request; apply.
External inputs: the existing rulesets.
Done when:       The user confirms both rulesets in the repository settings.
Out of scope:    Activation (09.3).
```

## 09.3 Activate master

```
Goal:            master runs the new CI after one push, and one production run
                 publishes and indexes.
Context:         Spec, "Cutover": retire the old production routes before
                 enabling the replacements; never run both against the same
                 change. The plan branch removes the old workflows and adds the
                 new ones in the same history, so one push switches over.
Contracts:       master advances to the plan branch by fast-forward or a merge
                 commit (never a squash, which would lose 8c640b214); pushed by
                 an admin under the ruleset bypass.
Files:           none.
Work:            1. Confirm master has not moved since the plan branch's base,
                    or merge master into the plan branch locally first.
                 2. Show the push command; push after the request.
                 3. On request, dispatch `gh workflow run bind-plugin.yml`
                    and the HA entry, and report the run URLs and outcomes.
External inputs: the remote master.
Done when:       The user confirms a production run published, synced and
                 indexed for each plugin, carrying phase 07's client changes
                 into the 26.7 release branches.
Out of scope:    Release cleanup (09.4).
```

## 09.4 Legacy Release cleanup

```
Goal:            The old pipeline's fingerprinted and development Releases are
                 removed after review.
Context:         Design, "Frozen": the legacy feeds, runtime Releases
                 pkg-<series>-bind-runtime and their versioned ones stay;
                 fingerprinted versioned Releases from the old pipeline get one
                 manual cleanup keeping each target's current and one rollback.
Contracts:       A list file in the job temp directory (not committed) of tags
                 to delete in resolver-plugins/repository and
                 resolver-plugins/plugins, each with its reason.
Files:           none committed.
Work:            1. List Releases in both repositories (gh release list
                    --limit 1000).
                 2. Select fingerprinted versioned plugin Releases older than
                    each target's current and one rollback, and pr-*
                    development Releases; never runtime, legacy feed or current
                    tags.
                 3. Show the list; delete only the approved entries.
External inputs: the Release listings.
Done when:       The user approves the list and the deletions match it.
Out of scope:    Frozen material.
```

## Notes

- Activation is one push after the branch rules exist, over enabling workflows
  separately, so old and new never run against the same change (2026-10-19).
- The first trailer names master commit 8c640b214, so the first carry brings
  phase 07's client changes into the 26.7 branches (2026-10-20).
- `.github/workflows/` is removed from all three release branches by a new
  commit, over leaving retired 26.1 untouched, because the spec allows no
  workflow callers on release branches and 26.1's old `package-release.yml`
  would publish with the signing key on a merged PR; a removal commit is not a
  build, so the lock on 26.1 holds (user decision, 2026-10-20).
