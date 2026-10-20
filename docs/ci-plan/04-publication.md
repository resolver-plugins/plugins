# Phase 04: Publication

Status: planned
Before: a request produces the `qualified-` artifact (phase 03); publish and
sync-source are stubs.
After: production and promotion publish the versioned and current
distribution Releases and land the prepared source on the release branch with
its tag and source Release. Every step is safe to rerun. The index is still a
stub.

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

Shared references: design, "Publication" and "`sync-source`"; spec, "Signing
and publication", "Source and distribution stay in step" and "Failed jobs stay
failed" ("GitHub's rerun must work"). Step ids and env in
[01-ci-structure.md](01-ci-structure.md#lifecycle-interface). The qualified
artifact holds `<name>-<version>.pkg`, `build-record.json` and `source.bundle`
(phase 03).

## Stages

| Stage | Goal | Model | Effort | Order | Status |
| --- | --- | --- | --- | --- | --- |
| 04.1 | Versioned and current distribution Releases | sonnet | high | first | planned |
| 04.2 | Source sync, tag and source Release | sonnet | medium | after 04.1 | planned |

## 04.1 Distribution Releases

```
Goal:            Steps `versioned` and `current` publish the versioned Release
                 completely and advance the current Release only for a newer
                 revision.
Context:         Design, "Publication": create the versioned Release as a draft,
                 upload the archive and build-record.json, publish; a rerun
                 deletes a leftover draft of the same tag first; an already
                 published identical tag completes as done. Current advances only
                 when the version is newer (replace its assets); equal is done;
                 older leaves it. HA keeps the prerelease flag. The job runs
                 under the lifecycle's concurrency group.
Contracts:       Tags: versioned <current tag>-<version>, current <current tag>
                 (CURRENT_TAG with {series} replaced). Outputs: versioned
                 created=true when it published; current current_tag (always)
                 and advanced=true when it created or advanced current. After
                 advancing, current holds only the archive and
                 build-record.json.
Files:           .github/workflows/plugin-target.yml (bodies of `versioned` and
                 `current`)
Work:            versioned:
                 1. draft = `gh release view "$tag" --json isDraft --jq
                    .isDraft` (stderr to a file); on failure only "release not
                    found" means missing (draft empty); other errors fail.
                 2. draft=false: download its build-record.json; if its
                    source_commit is not $PREPARED_SHA, ::error:: "<tag> is
                    already published from another source; rerun the run that
                    published it" and exit 1; else note it in the summary and
                    exit 0.
                 3. draft=true: gh release delete it.
                 4. gh release create the tag with dist/*.pkg and
                    dist/build-record.json, --draft, title the tag, notes
                    "<version> for OPNsense <series>", --prerelease=$PRERELEASE;
                    gh release edit --draft=false; output created=true.
                 current:
                 5. Output current_tag. If gh release view of it fails, create it
                    with both files and the prerelease flag, output
                    advanced=true, exit 0.
                 6. revision = the -<series>_<n>.pkg asset's n, or 0 when current
                    holds no such archive (HA's 0.2_45). If ${VERSION##*_} -le
                    revision: note "does not advance" in the summary; exit 0.
                 7. gh release upload both files --clobber; delete every other
                    asset (`{ grep -vx -e "$archive" -e build-record.json ||
                    true; }` so an empty list does not fail under pipefail);
                    output advanced=true.
External inputs: existing Releases (another run may have changed them).
Done when:       Draft, upload, publish order; leftover draft replaced;
                 published tag of the same source done and of another source
                 failing; current created, advanced, or left; no signing; no
                 Release listing.
Out of scope:    The index (phase 05).
```

## 04.2 Source sync

```
Goal:            The sync-source job fast-forwards the release branch to the
                 prepared commit, tags it and creates the source Release.
Context:         Design, "`sync-source`": only the pipeline writes release
                 branches, under the target lock, and every write is a
                 publication, so the push is a fast-forward and Git rejects it
                 if the branch moved. A rerun whose branch already holds the
                 commit only ensures the tag and Release. The sync app is the
                 only allowed writer after cutover.
Contracts:       Branch $BRANCH (created for a new series); tag
                 <source tag>-<version> (SOURCE_TAG with {series} replaced) at
                 PREPARED_SHA; source Release in resolver-plugins/plugins with
                 the archive and build-record.json, title the tag, notes "Source
                 of <version> for OPNsense <series>", prerelease flag.
Files:           .github/workflows/plugin-target.yml (sync-source bodies)
Work:            Push:
                 1. git fetch -q "$RUNNER_TEMP/dist/source.bundle" HEAD.
                 2. If git merge-base --is-ancestor $PREPARED_SHA origin/$BRANCH,
                    note it in the summary; else git push origin
                    "$PREPARED_SHA:refs/heads/$BRANCH" (no force).
                 3. git push origin "$PREPARED_SHA:refs/tags/<tag>" (a no-op
                    when it exists there; rejected if it points elsewhere).
                 Source Release:
                 4. The versioned pattern of 04.1 steps 1, 3 and 4 against the
                    checked-out repository: done when published, delete a leftover
                    draft, create as draft with --verify-tag, then publish.
External inputs: the release branch head and the existing tag and Release.
Done when:       No force push; the already-contained case skips the push; the
                 tag points at the prepared commit; the Release is rerun-safe.
Out of scope:    Branch rules (phase 09).
```

## Notes

- The source tag and Release are made by `sync-source`, over `publish`, because
  the tag must point at the commit that lands on the branch (2026-10-19).
- A versioned tag already published from a different `source_commit` fails
  instead of completing, so a later run cannot claim another build's version
  (2026-10-20).
