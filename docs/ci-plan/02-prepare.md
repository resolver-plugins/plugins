# Phase 02: Prepare

Status: planned
Before: `plugin-target.yml` is declared (phase 01) with stubbed steps.
After: the `prepare` job works: it checks the active series, resolves the
upstream environment, reads the current Release and record, prepares and
bundles the source for production and candidates (or skips), and finds the
candidate for a promotion. Later jobs are still stubs.

Rules: the new CI lives only in new files (`bind-plugin.yml`,
`ha-dhcp-interface-plugin.yml`, `plugin-target.yml`, `package-index.yml`). No
old CI file (the old workflows, `.github/ci/**`, `packages/`) is edited,
renamed or reused; the old CI stays untouched as reference until stage 08.1
deletes it. The specification's "Simplicity and limits", "Failed jobs stay failed"
and "Testing decisions" apply ([spec](../ci-rebuild-spec.md)). Nothing is run:
review by reading the diff and `git diff --check`. Stop and ask before any
inline or CI script exceeds 30 physical lines (comments and blanks count). Add no script
files: every operation is a workflow step using `git`, `gh`, `jq`, `make` or
`pkg`. A script file needs the spec's written justification ("Script files are
the last resort"), reviewed until no simpler alternative remains and approved
by the user before it is created.
Commit once per phase with `[skip ci]`; nothing is pushed or dispatched.

Shared references: design, "`prepare` outputs", "Versions", "Upstream
discovery" ([design](../ci-rebuild-design.md)); the step ids, conditions,
step env and job outputs are declared in
[01-ci-structure.md](01-ci-structure.md#lifecycle-interface). Each stage replaces
only the stub bodies it names and writes the step outputs listed, with
`echo "key=value" >> "$GITHUB_OUTPUT"`. All four stages edit the same file, so
they run in sequence.

## Stages

| Stage | Goal | Model | Effort | Order | Status |
| --- | --- | --- | --- | --- | --- |
| 02.1 | Active series and upstream environment | sonnet | medium | first | planned |
| 02.2 | Current Release and record | sonnet | medium | after 02.1 | planned |
| 02.3 | Prepared source and decision | opus | high | after 02.2 | planned |
| 02.4 | Promotion lookup | sonnet | medium | after 02.3 | planned |

## 02.1 Active series and upstream environment

```
Goal:            Steps `active` and `environment` reject a non-active series and
                 output the upstream environment facts.
Context:         Spec, "Active series": only the newest final Community series
                 builds; "Upstream discovery": no hard-coded series or commits,
                 fail clearly on an unexpected format. Design, "Upstream
                 discovery": active series = highest numeric final tag (X.Y or
                 X.Y.Z) in opnsense/core, usable when the matching
                 opnsense/tools tag exists and has config/<series>/;
                 freebsd_release from the matching tools
                 tag's config/<series>/build.conf `OS`; abi FreeBSD:<major>:amd64;
                 core_commit and plugins_commit are the stable/<series> heads;
                 upstream_package_commit is the last upstream stable/<series>
                 commit touching source_directory.
Contracts:       environment outputs: freebsd_release, abi, tools_tag,
                 core_commit, plugins_commit, upstream_package_commit (empty for
                 HA, which upstream does not have). Upstream plugins
                 stable/<series> is fetched into
                 refs/remotes/upstream/stable/<series> for steps in 02.3.
Files:           .github/workflows/plugin-target.yml (bodies of `active` and
                 `environment`)
Work:            active (the "active series rule", reused by 06.2 and 06.3):
                 1. series list = the distinct X.Y of opnsense/core tags
                    matching ^[0-9]+\.[0-9]+(\.[0-9]+)?$ (git ls-remote --tags
                    --refs), newest first (sort -rV).
                 2. tools tags = opnsense/tools tags of the same form. active =
                    the first series that has a tools tag X.Y or X.Y.Z whose
                    config/<series>/build.conf exists (gh api of the newest such
                    tag; 404 means not usable yet). A newer series without it is
                    not yet usable and does not retire its predecessor; the
                    signed feed and builder show their availability when the
                    build runs.
                 3. If $SERIES != active: ::error:: "Series X is not the active
                    series Y; only the active series builds"; exit 1.
                 environment:
                 3. tools_tag = newest opnsense/tools tag matching
                    ^<series with dots escaped>(\.[0-9]+)?$ (sort -V, tail -n 1).
                 4. os = the X.Y of the line `OS?=X.Y` in `gh api -H 'Accept:
                    application/vnd.github.raw'
                    repos/opnsense/tools/contents/config/$SERIES/build.conf?ref=$tools_tag`.
                 5. core_commit = `git ls-remote https://github.com/opnsense/core.git
                    refs/heads/stable/$SERIES | cut -f1`.
                 6. If os or core_commit is empty: ::error:: naming the tools
                    tag and series; exit 1.
                 7. git fetch -q --no-tags https://github.com/opnsense/plugins.git
                    "+refs/heads/stable/$SERIES:refs/remotes/upstream/stable/$SERIES";
                    plugins_commit = its rev-parse; upstream_package_commit =
                    `git log -1 --format=%H upstream/stable/$SERIES --
                    "$SOURCE_DIRECTORY"`.
                 8. Write the six outputs (abi = FreeBSD:${os%%.*}:amd64).
External inputs: core and tools tags, build.conf, core stable head, upstream
                 plugins branch.
Done when:       No series, version or hash is hard-coded; failures name what
                 was missing. If a body needs more than 30 lines, stop and ask.
Out of scope:    Other steps.
```

## 02.2 Current Release and record

```
Goal:            Step `current` outputs the next version and what the current
                 Release and its build record say.
Context:         Design, "Versions": version is <series>_<n>, n = current
                 Release archive revision + 1, or 1 when the series has no
                 current Release. Design, "Data formats", build-record.json.
                 Releases created by the old pipeline have no
                 build-record.json; that means "build".
Contracts:       Outputs: version; when the current Release holds a
                 <series>_<n> archive also current_version (<series>_<n>); when
                 it has build-record.json also previous_tag
                 (<current tag>-<current_version>) and recorded_base,
                 recorded_commit, recorded_upstream (its source_base,
                 source_commit, upstream_package_commit). A current Release
                 whose archive is not <series>_<n> (HA's 0.2_45, which
                 26.7_1 succeeds) counts as no revision: version <series>_1.
                 Releases from the old pipeline have fingerprinted versioned
                 tags, so previous_tag stays empty without a record. The
                 current tag is CURRENT_TAG with {series} replaced.
Files:           .github/workflows/plugin-target.yml (body of `current`)
Work:            1. current=${CURRENT_TAG//'{series}'/$SERIES}.
                 2. assets = gh release view "$current" --repo
                    resolver-plugins/repository --json assets --jq
                    '.assets[].name', with stderr to a file; on failure, only
                    "release not found" means missing (write version=${SERIES}_1
                    and exit 0); any other error is printed and fails.
                 3. archives = the assets matching -<series escaped>_[0-9]+\.pkg$.
                    None: version=${SERIES}_1 and exit 0. More than one:
                    ::error:: and exit 1. revision = its number.
                 4. If build-record.json is an asset, download it to
                    $RUNNER_TEMP/current, write the three recorded_* outputs
                    with jq, and write previous_tag.
                 5. Write current_version and version (revision + 1).
External inputs: the current Release's assets and record.
Done when:       One targeted lookup, no listing; missing Release, foreign
                 version, old format and new format are covered. If the body
                 needs more than 30 lines, stop and ask.
Out of scope:    Promotion's version comparison (02.4).
```

## 02.3 Prepared source and decision

```
Goal:            Steps `base`, `merge`, `carry`, `decide` and `version` produce
                 the prepared commit and bundle, or decide to skip.
Context:         Design, "`prepare` outputs": the prepared commit starts from the
                 release branch head (for a new series, the previous series'
                 release branch head) and adds the upstream merge when the
                 series is new or upstream changed, the carry `git diff <last
                 carried>..<source_sha> -- <source_directory>` applied with git
                 apply --3way (<last carried> is the Master-Commit trailer of
                 the newest version commit on the release branch), and one
                 version commit with the trailer `Master-Commit: <source_sha>`.
                 A conflict fails the target. The `merge` step's condition is
                 already declared (01.1). Release branches set PLUGIN_VERSION
                 and PLUGIN_REVISION to <series>/<n>, while upstream and master
                 keep upstream's values (26.7/9 against 1.34/4 and 1.34/5 at
                 planning), so upstream revision bumps and master version
                 changes always conflict on those two lines; upstream bumped
                 the revision twice in July 2026. Tests are not packaged
                 (only src/ is) and the release branches lack most of them.
Contracts:       Version-line rule, used by merge and carry: after a conflict,
                 if the only unmerged path (git diff --name-only
                 --diff-filter=U) is "$SOURCE_DIRECTORY/Makefile" and every line
                 inside each <<<<<<< … >>>>>>> hunk of it, other than the markers
                 and =======, matches ^PLUGIN_(VERSION|REVISION)= (either side
                 may be empty), keep the release side of every hunk, git add the
                 file and continue; otherwise fail with git's message. The
                 version commit then rewrites both lines. Both steps run git
                 with -c merge.conflictStyle=merge so hunks have two sides.
                 base outputs new_series (true|false) and source_base (sha);
                 carry outputs carried (true|false); decide outputs operation
                 (build|skip) and reason; version outputs prepared_sha and
                 writes $RUNNER_TEMP/source/source.bundle containing HEAD with
                 prerequisite source_base. Commits use the identity
                 github-actions[bot] /
                 41898282+github-actions[bot]@users.noreply.github.com.
Files:           .github/workflows/plugin-target.yml (bodies of base, merge,
                 carry, decide, version)
Work:            base:
                 1. Set the git identity.
                 2. If refs/remotes/origin/$BRANCH_PREFIX/$SERIES exists, base is
                    it and new_series=false. Otherwise previous = the series
                    sorting just before $SERIES (sort -V) among the existing
                    origin/$BRANCH_PREFIX/<X.Y> branches; none: ::error:: and
                    exit 1; base is that branch, new_series=true.
                 3. git checkout --detach the base; output source_base.
                 merge:
                 4. git merge --no-edit -m "Merge upstream stable/$SERIES into
                    $BRANCH_PREFIX/$SERIES" upstream/stable/$SERIES. On failure,
                    apply the version-line rule (an awk over the Makefile that
                    exits non-zero on any other line in a hunk and otherwise
                    prints the file without markers and the upstream side), then
                    git commit --no-edit to complete the merge.
                 carry:
                 5. last = `git log -1 --grep='^Master-Commit: '
                    --format='%(trailers:key=Master-Commit,valueonly)' HEAD`;
                    empty: ::error:: "No Master-Commit trailer on the release
                    branch history"; exit 1.
                 6. git fetch -q --no-tags origin "$last" "$SOURCE_SHA" (both may
                    be PR heads that only refs/pull/* holds, after a squash
                    merge).
                 7. git diff --binary "$last" "$SOURCE_SHA" --
                    "$SOURCE_DIRECTORY" ":(exclude)$SOURCE_DIRECTORY/tests" >
                    $RUNNER_TEMP/carry.patch; if non-empty, git apply --3way it
                    (on failure, the version-line rule as in step 4, without a
                    commit) and output carried=true, else false.
                 decide:
                 8. skip only when PURPOSE is production, nothing was carried,
                    HEAD equals SOURCE_BASE, and SOURCE_BASE equals
                    RECORDED_COMMIT or RECORDED_BASE; reason "Nothing changed
                    since <current_version>". Otherwise build, reason "Build
                    <dir> <purpose> for <series> from <sha>". Write both outputs
                    and append the reason to $GITHUB_STEP_SUMMARY.
                 version:
                 9. sed -i -E the Makefile lines PLUGIN_VERSION= to ${VERSION%_*}
                    and PLUGIN_REVISION= to ${VERSION##*_}, keeping their
                    spacing.
                 10. git add -A -- "$SOURCE_DIRECTORY"; git commit -m "Set
                    <dir> to <version>" -m "Master-Commit: $SOURCE_SHA".
                 11. git bundle create $RUNNER_TEMP/source/source.bundle HEAD
                    "^$SOURCE_BASE"; output prepared_sha.
External inputs: the release branches (existence and trailer); upstream and
                 master content, whose conflicts git reports; the version-line
                 rule is the only conflict resolved automatically.
Done when:       Candidates never skip; the carry excludes tests; a conflict
                 confined to the two version lines continues and any other
                 conflict fails; the carry and version change land in one
                 commit with the trailer; any body over 30 lines is a stop.
Out of scope:    Promotion (02.4); pushing anything (phase 04).
```

## 02.4 Promotion lookup

```
Goal:            Step `candidate` finds the merged head's qualified artifact and
                 outputs what publication needs, or fails with a reason.
Context:         Spec, "PR builds and promotion": promotion finds the candidate
                 by the merged PR head; it fails clearly, and the maintainer
                 requests a production build, when the artifact expired or is
                 missing or its version is no longer newer than the current
                 Release. Design, "Artifacts between jobs": promotion looks it up
                 by name, the only cross-run lookup.
Contracts:       Outputs: version, previous_tag, prepared_sha (the record's
                 source_commit), source_base, abi (from the candidate's
                 build-record.json), artifact_run_id, operation=promote, reason
                 "Promote candidate <version> built from <sha>".
Files:           .github/workflows/plugin-target.yml (body of `candidate`)
Work:            1. run_id = `gh api
                    "repos/$GITHUB_REPOSITORY/actions/artifacts?name=$NAME" --jq
                    '[.artifacts[] | select(.expired | not)] | sort_by(.created_at)
                    | last | .workflow_run.id // empty'`; empty: ::error:: "No
                    unexpired $NAME; request a production build (gh workflow run
                    <entry> -f series=<series>)"; exit 1.
                 2. gh run download "$run_id" --name "$NAME" --dir
                    $RUNNER_TEMP/candidate.
                 3. If CURRENT_VERSION is set and the record's revision is not
                    greater than its revision: ::error:: "Candidate X is not newer
                    than current Y; request a production build (-f series=<series>)";
                    exit 1. An empty CURRENT_VERSION (no current, or a foreign
                    version) passes.
                 4. Write the outputs with jq and echo.
External inputs: the artifact listing and the candidate's record.
Done when:       One lookup by name, the two failure messages, no rebuild path.
Out of scope:    Downloading for publish (01.1 already declares run-id).
```

## Notes

- Production skips when the release branch head equals the record's
  `source_commit` (the normal state after `sync-source`) or `source_base` (a
  sync that did not land), over the design's `source_base` alone, which would
  rebuild every day after a successful sync (user decision, 2026-10-20).
- The carry and the version change share one commit; the design's order (merge,
  carry, version commit) is kept (2026-10-20).
- Conflicts confined to the `PLUGIN_VERSION` and `PLUGIN_REVISION` lines of the
  plugin's Makefile are resolved by keeping the release side, over failing the
  target or setting the version on the make command line, because the version
  commit replaces those lines and upstream bumps them with almost every change;
  every other conflict still fails (user decision, 2026-10-20).
- The carry excludes `<source_directory>/tests`, so tests stay on `master`
  only; they are not packaged and the release branches lack most of them (user
  decision, 2026-10-20).
