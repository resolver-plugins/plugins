# Phase 09: Activate

Status: planned
Before: phases 01–08 are committed locally on the plan branch; GitHub still
runs the old CI from `master`, and the release branches have no
`Master-Commit` trailer.
After: the release branches hold their published source and the first
trailer; a first PR removed the old CI from `master` and a second PR brought
the new CI, client changes, guides and plan; branch rules enforce the PR and
release-branch model; a first production run per plugin has published and
indexed; the old pipeline's leftover Releases are cleaned up.

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
| 09.2 | PR 1: remove the old CI from master | sonnet | low | after 09.1, on request | planned |
| 09.3 | PR 2: the new CI and everything else | sonnet | medium | after 09.2, on request | planned |
| 09.4 | Branch rules | sonnet | medium | after 09.3, on request | planned |
| 09.5 | First production runs | sonnet | medium | after 09.4, on request | planned |
| 09.6 | Legacy Release cleanup | sonnet | medium | after 09.5, on request | planned |

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
                 release/dhcp-interface-ha/26.7. Published source: the
                 `source_commit` in each current Release's build-metadata.txt
                 (the old pipeline's source tags point at control-plane commits
                 on master, not at plugin source).
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

## 09.2 PR 1: remove the old CI from master

```
Goal:            master holds no old CI, before any new CI arrives.
Context:         Spec, "Cutover": retire the old production routes before
                 enabling the replacements; never run both against the same
                 change. On a PR to master, the old `pull_request` workflows
                 run from the PR's merge commit (where they are deleted) and the
                 old entries' `pull_request_target` triggers only cover release
                 branches; closing runs master's old pr-release-cleanup.yml
                 once, which has no development Releases to delete. Until
                 09.3 merges, master has no CI: nothing builds or publishes,
                 and clients keep the current feed.
Contracts:       Branch `ci-rebuild/retire-old-ci` from `origin/master` with one
                 commit "[skip ci] Retire the old CI" deleting exactly the files
                 08.1 deleted (the ten old workflows, `.github/ci/**`,
                 `packages/`); nothing else. PR to master, merged with a merge
                 commit.
Files:           none in the plan branch.
Work:            1. Create the branch and commit with git rm of 08.1's paths.
                 2. Show the push and `gh pr create` commands; push and open the
                    PR after the request; the user reviews and merges.
External inputs: the remote master.
Done when:       The PR is merged and origin/master has no old workflow,
                 `.github/ci/` or `packages/`.
Out of scope:    Guides (they change in 09.3), the new CI.
```

## 09.3 PR 2: the new CI and everything else

```
Goal:            master holds the new CI, the client changes, the guides and
                 this plan.
Context:         The plan branch already deletes the same files as PR 1, so
                 merging the updated master into it is conflict-free and keeps
                 history. The new workflows have no push trigger, so the merge
                 starts nothing; their PR jobs run only from master's copy, so
                 they do not run on this PR either.
Contracts:       Merge origin/master (after 09.2) into ci-rebuild/plan with a
                 merge commit; PR ci-rebuild/plan to master, merged with a merge
                 commit (never a squash, which would lose 8c640b214 for the
                 first trailer).
Files:           none beyond the merge.
Work:            1. git fetch; git merge origin/master into the plan branch.
                 2. Show the push and `gh pr create` commands; push and open the
                    PR after the request; the user reviews and merges.
External inputs: the remote master.
Done when:       The PR is merged by merge commit and 8c640b214 is an ancestor
                 of origin/master.
Out of scope:    Branch rules (09.4), dispatching runs (09.5).
```

## 09.4 Branch rules

```
Goal:            GitHub enforces the model the new CI assumes.
Context:         Spec, "PR builds and promotion": master requires the status on
                 the current head and up-to-date PRs; release branches accept
                 pushes only from the pipeline. Set after 09.3, because the
                 required statuses only exist once the new entries are on
                 master.
Contracts:       Ruleset on master: pull request required; required statuses
                 "Package build / dns/bind" and "Package build /
                 net/dhcp-interface-ha"; branches must be up to date. Ruleset
                 on release/**: updates and creation restricted to the sync
                 GitHub App; no deletion or force push.
Files:           none.
Work:            1. Read the existing rulesets (gh api
                    repos/resolver-plugins/plugins/rulesets).
                 2. Prepare the two ruleset JSON bodies and gh api commands; stop
                    for the request; apply.
External inputs: the existing rulesets.
Done when:       The user confirms both rulesets in the repository settings.
Out of scope:    Dispatching runs (09.5).
```

## 09.5 First production runs

```
Goal:            One production run per plugin publishes, syncs and indexes.
Context:         The first carry starts at the 09.1 trailer, so it brings phase
                 07's client changes into the 26.7 release branches.
Contracts:       `gh workflow run bind-plugin.yml` and `gh workflow run
                 ha-dhcp-interface-plugin.yml`, each without inputs (discovery).
Files:           none.
Work:            1. On request, dispatch both and report the run URLs.
                 2. Report each run's outcome job by job; a failure stays failed
                    and is reported, not repaired.
External inputs: the run results.
Done when:       The user confirms each plugin's run published, synced and
                 indexed.
Out of scope:    Release cleanup (09.6).
```

## 09.6 Legacy Release cleanup

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

- Activation is two PRs, the first removing the old CI and the second adding
  the new, with branch rules after the second, over one direct push after the
  rules, so old and new never run against the same change and each review is
  focused (user decision, 2026-10-20).
- The first trailer names master commit 8c640b214, so the first carry brings
  phase 07's client changes into the 26.7 branches (2026-10-20).
- `.github/workflows/` is removed from all three release branches by a new
  commit, over leaving retired 26.1 untouched, because the spec allows no
  workflow callers on release branches and 26.1's old `package-release.yml`
  would publish with the signing key on a merged PR; a removal commit is not a
  build, so the lock on 26.1 holds (user decision, 2026-10-20).
- 09.1 found all three branches already contain their published source
  (26.1_7 `31b351a47` and 26.7_9 `1c241209d` are the heads; HA 0.2_45
  `8e29ddd85` is an ancestor of the prepared 26.7_1 head), so only the workflow
  removal and trailer commits were needed (2026-10-20).
