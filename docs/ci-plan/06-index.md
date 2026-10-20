# Phase 06: Index and retention

Status: planned
Before: publication works (phase 05); `repository-index.yml` is declared with
stub bodies (phase 02), and its stage job's repository step is filled (04.1).
After: after a publication, the series feed is rebuilt from the current
Releases it serves, signed, published to Pages in one commit and read back, and
the release two versions back is removed.

Rules: the specification's "Simplicity and limits", "Failed jobs stay failed"
and "Testing decisions" apply ([spec](../ci-rebuild-spec.md)). Nothing is run:
review by reading the diff and `git diff --check`. Stop and ask before any
inline or CI script exceeds 30 physical lines. Commit once per phase with
`[skip ci]`; nothing is pushed or dispatched.

Shared references: design, "Index", "Data formats" (channel.json) and "Frozen,
never written by the new CI"; spec, "Index and retention". Job and step names in
[02-ci-structure.md](02-ci-structure.md#index-interface). The live feed's
`channel.json` is schema 2: `components.<name>` objects with `current_tag` and
`packages` as an object of archive name to hash; schema 4 writes `packages` as a
list. The 26.7 feed lists `bind_plugin`, `bind_runtime` (frozen,
`pkg-26.7-bind-runtime`) and `dhcp`. The public key is
`docs/package-repository/resolver-plugins.pub`; `RP_PKG_SIGNING_KEY` is the
base64 private key.

## Stages

| Stage | Goal | Model | Effort | Order | Status |
| --- | --- | --- | --- | --- | --- |
| 06.1 | Select the feed contents | sonnet | high | first | planned |
| 06.2 | Stage and sign | sonnet | medium | after 06.1 | planned |
| 06.3 | Publish to Pages and read back | sonnet | medium | after 06.2 | planned |
| 06.4 | Retention | sonnet | medium | after 06.3 | planned |

## 06.1 Select

```
Goal:            The select job writes the new channel.json from the feed's
                 current Releases plus the published one, and says whether the
                 archive set changed.
Context:         Spec: the index rereads the current published records after
                 taking the lock, so a delayed event cannot restore older
                 selections; it tolerates absent unrelated packages. Design:
                 select stops when nothing changed.
Contracts:       Output changed=true|false. $RUNNER_TEMP/channel/channel.json =
                 {"schema": 4, "series", "package_abi", "components":
                 {<name>: {"current_tag", "version" (from the Release's
                 build-record.json, else null), "packages": [<.pkg asset
                 names>]}}}. environment outputs freebsd_release and core_commit
                 from the input component's current Release record.
Files:           .github/workflows/repository-index.yml (bodies of `select` and
                 `environment`)
Work:            select:
                 1. Read $FEED/channel.json from gh-pages with gh api raw; on
                    failure, continue with {} only if the error says HTTP 404,
                    else print it and exit 1.
                 2. tags = component=tag lines from components.*.current_tag
                    with {$COMPONENT: $CURRENT_TAG} added (assign first, so a jq
                    error fails the step).
                 3. For each: assets from gh release view; a missing Release
                    ("release not found") is skipped, any other error fails;
                    version from build-record.json when present; add the entry.
                 4. old = sorted archive names of the existing feed (object keys
                    or list); new = sorted names of the new components.
                 5. Write channel.json; changed=$([ "$old" = "$new" ] && echo
                    false || echo true).
                 environment:
                 6. gh release download "$CURRENT_TAG" --pattern
                    build-record.json --output - | jq to the two outputs.
External inputs: the existing channel.json, current Releases and records.
Done when:       Frozen components keep their current_tag; nothing is written.
                 If the select body needs more than 30 lines, stop and ask.
Out of scope:    Signing (06.2).
```

## 06.2 Stage and sign

```
Goal:            The stage job downloads the archives, signs the catalogue in a
                 FreeBSD VM with the series' pkg and uploads the feed directory.
Context:         Design: stage downloads the archives and runs pkg repo with the
                 series' pkg and the signing key, adding resolver-plugins.pub and
                 channel.json. Spec: the key is exposed only to the signing job.
Contracts:       feed/ holds the archives, channel.json, resolver-plugins.pub and
                 pkg repo's catalogue files; uploaded as feed-<series>-<run_id>.
Files:           .github/workflows/repository-index.yml ("Download the current
                 archives" body, the VM run, and a new "Record the pkg version"
                 step after the VM)
Work:            Download:
                 1. For each components[].current_tag in feed/channel.json: gh
                    release download --pattern '*.pkg' --dir feed.
                 2. cp docs/package-repository/resolver-plugins.pub feed/.
                 VM (sh, set -eu):
                 3. Configure the OPNsense repository from .opnsense-pkg as in
                    phase 04; pkg-static install -fy -r OPNsense pkg.
                 4. umask 077; key=$(mktemp); trap removing it; printf '%s'
                    "$RP_PKG_SIGNING_KEY" | base64 -d > "$key"; pkg repo feed
                    "$key"; pkg -v > .pkg-version (outside feed/).
                 After the VM:
                 5. A step "Record the pkg version" appends "Signed with pkg
                    $(cat .pkg-version)" to $GITHUB_STEP_SUMMARY (spec: index with
                    the series' pkg and record its version).
External inputs: the Release archives (pkg repo reads their manifests).
Done when:       The key appears only in this VM step and is removed on exit;
                 the pkg version is in the job summary.
Out of scope:    Pages (06.3).
```

## 06.3 Publish and read back

```
Goal:            The publish job replaces the feed directory on gh-pages in one
                 commit, and readback confirms Pages serves the staged files.
Context:         Spec: publish complete signed catalogues in one Git update;
                 failed preparation leaves the working feed; verify through the
                 public feed. Design: readback compares the public files with
                 the staged ones, retrying briefly while Pages deploys.
Contracts:       Commit message "Index <FEED>"; author github-actions[bot];
                 public URL https://resolver-plugins.github.io/repository/<FEED>.
Files:           .github/workflows/repository-index.yml (publish and readback
                 bodies)
Work:            Publish (in pages/):
                 1. Set the git identity; rm -rf "$FEED"; mkdir -p; cp -R the
                    staged feed in; git add -A -- "$FEED".
                 2. Nothing staged (a rerun after a push): note it and exit 0.
                 3. git commit; git push origin HEAD:gh-pages (no force; a moved
                    gh-pages rejects it).
                 Readback:
                 4. Up to 20 attempts 30 s apart: compare every staged file with
                    curl -fsS --retry 3 of its public URL via cmp; exit 0 when all
                    match; finally ::error:: and exit 1.
External inputs: the public feed.
Done when:       Only $FEED changes on gh-pages; readback compares every staged
                 file.
Out of scope:    Frozen feeds and other series' directories.
```

## 06.4 Retention

```
Goal:            After a successful readback, retain deletes the release two
                 versions back: its distribution Release and tag and its source
                 Release, keeping the source tag.
Context:         Design: read the previous versioned Release's build-record.json;
                 its previous_tag names the release to drop; a missing Release
                 means nothing to delete. Spec: never touches another package,
                 series or frozen material.
Contracts:       Distribution token in GH_TOKEN (resolver-plugins/repository);
                 sync token in SYNC_TOKEN (resolver-plugins/plugins). Source
                 Release tag <SOURCE_TAG with {series}>-<version of the dropped
                 tag>, where the version is the dropped tag minus
                 "<CURRENT_TAG>-".
Files:           .github/workflows/repository-index.yml (retain body)
Work:            1. previous = previous_tag of CURRENT_TAG's record; dropped =
                    previous_tag of previous's record (a small shell function;
                    missing Release, asset or field yields empty).
                 2. Empty dropped, or gh release view of it fails: note "Nothing
                    to retire" and exit 0.
                 3. gh release delete "$dropped" --cleanup-tag --yes.
                 4. If the source Release exists (GH_TOKEN=$SYNC_TOKEN), delete
                    it without --cleanup-tag.
External inputs: the current and previous versioned Releases' records.
Done when:       Deletion driven only by two previous_tag reads, no listing of
                 Releases, missing cases end normally.
Out of scope:    Legacy Releases (phase 09).
```

## Notes

- Readback compares public files byte for byte, over a FreeBSD VM running
  `pkg update`, because signatures were made in `stage` (2026-10-19).
