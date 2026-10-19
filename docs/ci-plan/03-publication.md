# Phase 3: Publication

Status: stages defined; handoffs not written. Not built.

A qualified production archive becomes the versioned and current distribution
Releases, then lands on the release branch with its source tag and Release
(design, "Publication", "`sync-source`"). Both jobs are in `plugin-target.yml`
and must be safe to rerun with GitHub's rerun.

## Contracts used

- From phase 2: `prepare.version`, `prepare.previous_tag`, `prepare.prepared_sha`,
  the `qualified-` artifact (archive, record, source bundle), and the entry inputs
  `current_tag`, `source_tag`, `branch_prefix`, `prerelease`.
- Tag formats and Release contents (design, "Entry inputs", "Publication").
- Consumers: the index (phase 4) needs `publish` to output `published` and the
  current tag; promotion (phase 5) runs `publish` with the candidate's artifact;
  retention (phase 4) reads `previous_tag` from published records.

## Stages

### 3.1 Publish distribution Releases

Role: publish the versioned Release completely, then advance the current Release
when the version is newer.

- Inputs: the `qualified-` artifact; `version`; `current_tag`; `prerelease`.
- Outputs: versioned Release `<current_tag>-<version>`; current Release updated
  or left; outputs `published` (`true` when either changed) and `current_tag`.
- Contracts: draft, upload both files, publish; a leftover draft of the same tag
  is replaced on rerun; an already published identical tag is done. Current
  advances only for a newer revision, under the lifecycle lock. Credentials: the
  distribution app, created in this job only.
- External inputs: the existing Releases (state another run may have changed).
- Depends on: phase 2.
- Done when: the diff shows the draft-then-publish order, the rerun cases above,
  the newer/equal/older rule on current, and no signing.

### 3.2 Sync source

Role: put the published source on the release branch and record it as a source
tag and Release.

- Inputs: the `qualified-` artifact (archive, record, source bundle) and
  `prepared_sha`; `branch_prefix`, `series`, `source_tag`, `version`.
- Outputs: the branch advanced (or created for a new series) by a fast-forward
  push; tag `<source_tag>-<version>`; the source Release with both files.
- Contracts: fast-forward push of the prepared commit (merge ancestry kept); a
  rerun whose branch already holds it only ensures the tag and Release.
  Credentials: the sync app, the only allowed writer after cutover.
- External inputs: the current branch head (it may have moved).
- Depends on: 3.1 (`needs: publish`).
- Done when: the diff shows a push that cannot overwrite a moved branch, the
  tag at the pushed commit, and the rerun case.

## Decisions

- The source tag and Release are made by `sync-source`, over `publish`, because
  the tag must point at the commit that lands on the branch (2026-10-19).
