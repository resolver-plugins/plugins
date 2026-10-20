# Phase 01: CI structure

Status: planned
Before: `.github/workflows/` holds the old CI, including the old
`bind-plugin-release.yml`, `ha-dhcp-interface-release.yml` and
`repository-index.yml`; none of the new CI exists yet.
After: the four workflows of the design exist with their final triggers,
inputs, outputs, job graph, conditions, concurrency, permissions, credential
steps and artifact names. Every `run:` body is a stub that fails, so nothing can
build or publish; phases 02–06 replace the stubs. The other old workflows stay
until phase 08.

Rules: the specification's "Simplicity and limits", "Failed jobs stay failed"
and "Testing decisions" apply ([spec](../ci-rebuild-spec.md)). Nothing is run:
review by reading the diff and `git diff --check`. Stop and ask before any
inline or CI script exceeds 30 physical lines. Add no script
files: every operation is a workflow step using `git`, `gh`, `jq`, `make` or
`pkg`. A script file needs the spec's written justification ("Script files are
the last resort"), reviewed until no simpler alternative remains and approved
by the user before it is created. Commit once per phase with
`[skip ci]`; nothing is pushed or dispatched.

Shared references: design sections "Components", "Entry inputs", "Entry jobs",
"Lifecycle", "Artifacts between jobs", "Index"
([design](../ci-rebuild-design.md)).

Every job that has steps runs on `ubuntu-latest`.

Stub convention used by every stage: each `run:` step keeps its `name`, `id`,
`if` and `env` and has the body

```yaml
        run: echo "::error::Not implemented (stage NN.M)" && exit 1
```

naming the stage that fills it. Every workflow sets

```yaml
defaults:
  run:
    shell: bash
```

so steps run with `-eo pipefail`.

## Stages

| Stage | Goal | Model | Effort | Order | Status |
| --- | --- | --- | --- | --- | --- |
| 01.1 | `plugin-target.yml` declared | sonnet | high | first, parallel with 01.2 and 01.3 | planned |
| 01.2 | `repository-index.yml` declared | sonnet | medium | parallel with 01.1 and 01.3 | planned |
| 01.3 | Both entry workflows declared | sonnet | medium | parallel with 01.1 and 01.2 | planned |

## 01.1 Lifecycle workflow

```
Goal:            .github/workflows/plugin-target.yml exists as the reusable
                 lifecycle with its final interface and job graph and stubbed
                 step bodies.
Context:         Design, "Lifecycle": jobs prepare, build, qualify, publish,
                 sync-source and index run in order, each needing the previous
                 one's success; candidates stop after qualify; promotion skips
                 build and qualify; index and sync-source both need only
                 publish. GitHub skips a job when any ancestor was skipped unless
                 its `if` uses a status function, so publish, sync-source and
                 index use explicit result conditions.
Contracts:       See "Lifecycle interface" below; copy names exactly.
Files:           .github/workflows/plugin-target.yml (new)
Work:            Write the file as specified below, steps in the listed order.
External inputs: none (structure only).
Done when:       Every input, secret, job, output, condition, step id, artifact
                 name and credential step below is present; every run body is a
                 stub naming its stage.
Out of scope:    Any real step body.
```

### Lifecycle interface

`on.workflow_call.inputs`: `component`, `source_directory`, `branch_prefix`,
`current_tag`, `source_tag`, `series`, `purpose`, `source_sha` (string,
required); `follow_upstream`, `prerelease` (boolean, required); `pr` (string,
default `''`). `purpose` is `production`, `candidate` or `promote`.
`current_tag` and `source_tag` contain the literal `{series}`.

`on.workflow_call.secrets`: `RP_DISTRIBUTION_APP_PRIVATE_KEY`,
`RP_SYNC_APP_PRIVATE_KEY`, `RP_PKG_SIGNING_KEY`, each `required: false`
(candidates receive none).

Workflow `permissions`: `contents: read`, `actions: read`.

**Job `prepare`** — name `Prepare ${{ inputs.component }} ${{ inputs.series }}
(${{ inputs.purpose }})`, `ubuntu-latest`, `timeout-minutes: 15`.
Env: `GH_TOKEN: ${{ github.token }}`, `SERIES`, `SOURCE_DIRECTORY`,
`BRANCH_PREFIX`, `SOURCE_SHA`, `PURPOSE` from the inputs.
Outputs:

| Output | Value |
| --- | --- |
| `operation` | `steps.decide.outputs.operation \|\| steps.candidate.outputs.operation` |
| `reason` | `steps.decide.outputs.reason \|\| steps.candidate.outputs.reason` |
| `freebsd_release`, `tools_tag`, `core_commit`, `plugins_commit`, `upstream_package_commit` | `steps.environment.outputs.<same>` |
| `abi` | `steps.environment.outputs.abi \|\| steps.candidate.outputs.abi` |
| `version` | `steps.candidate.outputs.version \|\| steps.current.outputs.version` |
| `previous_tag` | `steps.candidate.outputs.previous_tag \|\| steps.current.outputs.previous_tag` |
| `source_base` | `steps.base.outputs.source_base \|\| steps.candidate.outputs.source_base` |
| `prepared_sha` | `steps.version.outputs.prepared_sha \|\| steps.candidate.outputs.prepared_sha` |
| `artifact_run_id` | `steps.candidate.outputs.artifact_run_id \|\| github.run_id` |

Steps (`id`: name, condition, extra env, filled by):

1. `actions/checkout@v6` with `fetch-depth: 0`, if `inputs.purpose != 'promote'`.
2. `active`: "Check the active series" — 02.1.
3. `environment`: "Resolve the upstream environment", if purpose != promote — 02.1.
4. `current`: "Read the current Release", env `CURRENT_TAG: ${{ inputs.current_tag }}` — 02.2.
5. `candidate`: "Find the candidate", if purpose == promote, env
   `NAME: qualified-${{ inputs.component }}-${{ inputs.series }}-${{ inputs.source_sha }}`,
   `CURRENT_VERSION: ${{ steps.current.outputs.current_version }}` — 02.4.
6. `base`: "Select the release branch base", if purpose != promote — 02.3.
7. `merge`: "Merge upstream stable/${{ inputs.series }}", if
   `inputs.purpose != 'promote' && (steps.base.outputs.new_series == 'true' || (inputs.follow_upstream && steps.environment.outputs.upstream_package_commit != steps.current.outputs.recorded_upstream))` — 02.3.
8. `carry`: "Carry master changes", if purpose != promote — 02.3.
9. `decide`: "Decide the operation", if purpose != promote, env `CARRIED`
   (carry.carried), `SOURCE_BASE` (base.source_base), `RECORDED_BASE`,
   `RECORDED_COMMIT` (current.recorded_base, current.recorded_commit),
   `CURRENT_VERSION` (current.current_version) — 02.3.
10. `version`: "Set the version and bundle the prepared source", if
    `steps.decide.outputs.operation == 'build'`, env `VERSION`
    (current.version), `SOURCE_BASE` — 02.3.
11. `actions/upload-artifact@v4`, if operation == build: name
    `source-${{ inputs.component }}-${{ inputs.series }}-${{ github.run_id }}`,
    path `${{ runner.temp }}/source/source.bundle`, `retention-days: 1`.

**Job `build`** — name `Build ${{ inputs.component }} ${{ needs.prepare.outputs.version }}`,
`needs: prepare`, `if: needs.prepare.outputs.operation == 'build'`,
`timeout-minutes: 90`. Env `SERIES`, `SOURCE_DIRECTORY`,
`PREPARED_SHA` (prepare.prepared_sha), `CORE_COMMIT` (prepare.core_commit).
Steps:

1. `actions/checkout@v6` with `fetch-depth: 0`, `persist-credentials: false`.
2. `actions/download-artifact@v4`: name `source-<component>-<series>-<run_id>`
   as above, path `${{ runner.temp }}/source`.
3. "Check out the prepared source" — 03.2.
4. "Configure the OPNsense package repository" — 03.1.
5. "Build in FreeBSD ${{ needs.prepare.outputs.freebsd_release }}":
   `vmactions/freebsd-vm@v1` with `release` (prepare.freebsd_release),
   `arch: x86_64`, `usesh: true`, `copyback: true`, `disable-cache: true`,
   `envs: SERIES SOURCE_DIRECTORY PREPARED_SHA`, `run:` stub — 03.2.
6. "Write the build record", env `ABI`, `FREEBSD_RELEASE`, `SOURCE_BASE`,
   `MASTER_COMMIT` (inputs.source_sha), `TOOLS_TAG`, `PLUGINS_COMMIT`,
   `UPSTREAM_PACKAGE_COMMIT`, `PREVIOUS_TAG` from prepare outputs, `RUN_URL`
   `${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}` — 03.2.
7. `actions/upload-artifact@v4`: name
   `build-${{ inputs.component }}-${{ inputs.series }}-${{ github.run_id }}`,
   paths `.build/*.pkg` and `.build/build-record.json`, `retention-days: 1`.

**Job `qualify`** — name `Qualify ${{ inputs.component }} ${{ needs.prepare.outputs.version }}`,
`needs: [prepare, build]`, `timeout-minutes: 60`, env `SERIES`, `CORE_COMMIT`.
Steps:

1. Download the `build-…` artifact to `dist`.
2. Download the `source-…` artifact to `dist`.
3. "Configure the OPNsense package repository" — 03.1.
4. "Read the expected identity" — 03.3.
5. "Install on a clean FreeBSD ${{ needs.prepare.outputs.freebsd_release }}":
   `vmactions/freebsd-vm@v1`, same `with` as build but no `copyback`,
   `envs: EXPECTED` — 03.3.
6. `actions/upload-artifact@v4`: name
   `qualified-${{ inputs.component }}-${{ inputs.series }}-${{ inputs.source_sha }}`,
   paths `dist/*.pkg`, `dist/build-record.json`, `dist/source.bundle`,
   `retention-days: 30`. This is the only upload of a `qualified-` name.

**Job `publish`** — name `Publish ${{ inputs.component }} ${{ needs.prepare.outputs.version }}`,
`needs: [prepare, qualify]`,

```yaml
    if: >-
      !cancelled() && inputs.purpose != 'candidate' && needs.prepare.result == 'success' &&
      (needs.qualify.result == 'success' || needs.prepare.outputs.operation == 'promote')
```

`timeout-minutes: 15`. Outputs: `published:
${{ steps.versioned.outputs.created == 'true' || steps.current.outputs.advanced == 'true' }}`,
`current_tag: ${{ steps.current.outputs.current_tag }}`. Env `REPOSITORY:
resolver-plugins/repository`, `SERIES`, `VERSION`, `PREPARED_SHA`,
`CURRENT_TAG`, `PRERELEASE`. Steps:

1. Download the `qualified-…` artifact to `dist` with
   `run-id: ${{ needs.prepare.outputs.artifact_run_id }}` and
   `github-token: ${{ github.token }}`.
2. `actions/create-github-app-token@v2`, id `token`: `app-id:
   ${{ vars.RP_DISTRIBUTION_APP_ID }}`, `private-key:
   ${{ secrets.RP_DISTRIBUTION_APP_PRIVATE_KEY }}`, `owner: resolver-plugins`,
   `repositories: repository`, `permission-contents: write`.
3. `versioned`: "Publish the versioned Release", env `GH_TOKEN` (token) — 04.1.
4. `current`: "Advance the current Release", env `GH_TOKEN` (token) — 04.1.

**Job `sync-source`** — name `Sync ${{ inputs.component }} ${{ needs.prepare.outputs.version }} source`,
`needs: [prepare, publish]`,
`if: ${{ !cancelled() && needs.publish.result == 'success' }}`,
`timeout-minutes: 15`. Env `BRANCH: ${{ inputs.branch_prefix }}/${{ inputs.series }}`,
`SERIES`, `VERSION`, `PREPARED_SHA`, `SOURCE_TAG`, `PRERELEASE`. Steps:

1. `actions/create-github-app-token@v2`, id `token`: `client-id:
   ${{ vars.RP_SYNC_APP_CLIENT_ID }}`, `private-key:
   ${{ secrets.RP_SYNC_APP_PRIVATE_KEY }}`, `owner: resolver-plugins`,
   `repositories: plugins`, `permission-contents: write`.
2. `actions/checkout@v6` with `fetch-depth: 0` and that token.
3. Download the `qualified-…` artifact to `${{ runner.temp }}/dist` with the
   same `run-id` and `github-token`.
4. "Push the prepared commit and its tag" — 04.2.
5. "Create the source Release", env `GH_TOKEN` (token) — 04.2.

**Job `index`** — `needs: [prepare, publish]`,
`if: ${{ !cancelled() && needs.publish.outputs.published == 'true' }}`,
`concurrency` group `package-repository-index` with `cancel-in-progress: false`
and `queue: max` (as the existing index), so no publication's index is dropped,
`uses: ./.github/workflows/repository-index.yml` with `series`, `abi`
(prepare.abi), `component`, `current_tag` (publish.current_tag), `source_tag`
(the input template); secrets: the three secrets passed by name.

## 01.2 Index workflow

```
Goal:            .github/workflows/repository-index.yml is replaced by the
                 reusable feed writer with its final interface and job graph and
                 stubbed step bodies.
Context:         Design, "Index": jobs select, stage (FreeBSD VM), publish,
                 readback, retain; the signing key only in stage. The caller
                 holds the concurrency group package-repository-index (01.1).
                 The old file is replaced in place.
Contracts:       See "Index interface" below.
Files:           .github/workflows/repository-index.yml (replaced)
Work:            Replace the file with the specification below.
External inputs: none (structure only).
Done when:       Every input, secret, job, output, condition, step and artifact
                 name below is present; every run body is a stub.
Out of scope:    Real step bodies; repository-index-series.yml (phase 08).
```

### Index interface

`on.workflow_call.inputs` (string, required): `series`, `abi`, `component`,
`current_tag` (resolved tag), `source_tag` (template with `{series}`).
Secrets (required): `RP_PKG_SIGNING_KEY`, `RP_DISTRIBUTION_APP_PRIVATE_KEY`,
`RP_SYNC_APP_PRIVATE_KEY`. Permissions `contents: read`. Workflow env:
`REPOSITORY: resolver-plugins/repository`,
`FEED: pkg/${{ inputs.abi }}/${{ inputs.series }}/latest`.

**Job `select`** — `timeout-minutes: 10`; outputs `changed`
(steps.select), `freebsd_release`, `core_commit` (steps.environment); env
`GH_TOKEN: ${{ github.token }}`, `SERIES`, `ABI`, `COMPONENT`, `CURRENT_TAG`.
Steps: `select` "Select the current Releases the feed serves" — 05.1;
`environment` "Read the build environment from the published record", if
`steps.select.outputs.changed == 'true'` — 05.1; upload-artifact (if changed)
name `channel-${{ inputs.series }}-${{ github.run_id }}`, path
`${{ runner.temp }}/channel/channel.json`, retention 1.

**Job `stage`** — `needs: select`, `if: needs.select.outputs.changed == 'true'`,
`timeout-minutes: 30`, env `GH_TOKEN`, `SERIES`, `CORE_COMMIT`
(select.core_commit). Steps: checkout with `persist-credentials: false` and
`sparse-checkout: docs/package-repository`; download `channel-…` to `feed`;
"Download the current archives" — 05.2; "Configure the OPNsense package
repository" — 03.1; "Sign the catalogue in FreeBSD …": `vmactions/freebsd-vm@v1`
with release (select.freebsd_release), `arch: x86_64`, `usesh`, `copyback`,
`disable-cache`, step env `RP_PKG_SIGNING_KEY: ${{ secrets.RP_PKG_SIGNING_KEY }}`,
`envs: RP_PKG_SIGNING_KEY`, run stub — 05.2; upload-artifact name
`feed-${{ inputs.series }}-${{ github.run_id }}`, path `feed`, retention 1.

**Job `publish`** — `needs: stage`, `timeout-minutes: 15`. Steps: distribution
app token (as in 01.1); checkout `repository: resolver-plugins/repository`,
`ref: gh-pages`, that token, `sparse-checkout: ${{ env.FEED }}`, `path: pages`;
download `feed-…` to `${{ runner.temp }}/feed`; "Replace the feed directory in
one commit" with `working-directory: pages` — 05.3.

**Job `readback`** — `needs: publish`, `timeout-minutes: 15`. Steps: download
`feed-…` to `feed`; "Compare the public feed with the staged files" — 05.3.

**Job `retain`** — `needs: readback`, `timeout-minutes: 10`, env `SERIES`,
`CURRENT_TAG`, `SOURCE_TAG`. Steps: distribution app token id `distribution`;
sync app token id `sync` (client-id, `repositories: plugins`,
`permission-contents: write`); "Delete the release two versions back" with env
`GH_TOKEN` (distribution token), `SYNC_TOKEN` (sync token) — 05.4.

## 01.3 Entry workflows

```
Goal:            bind-plugin-release.yml and ha-dhcp-interface-release.yml are
                 replaced by thin entries with their final events, jobs,
                 conditions and the one call to plugin-target.yml, with stubbed
                 step bodies.
Context:         Design, "Entry inputs" and "Entry jobs". Each entry resolves
                 production (discover), or candidate and promotion (request), and
                 makes one target call whose purpose, series, source commit and
                 concurrency group come from whichever job ran. Candidates get
                 empty secrets.
Contracts:       See "Entry interface" below; the two files differ only in the
                 policy values.
Files:           .github/workflows/bind-plugin-release.yml (replaced)
                 .github/workflows/ha-dhcp-interface-release.yml (replaced)
Work:            Write one shape and fill each file's policy values.
External inputs: none (structure only).
Done when:       Both files match the interface; the policy values match the
                 table; every run body is a stub.
Out of scope:    Real step bodies (phase 06).
```

### Entry interface

Policy values:

| Value | BIND | HA |
| --- | --- | --- |
| `name` (title) | `BIND plugin` | `HA DHCP Interface plugin` |
| `component` | `bind_plugin` | `dhcp` |
| `source_directory` | `dns/bind` | `net/dhcp-interface-ha` |
| `branch_prefix` | `release/bind-rp` | `release/dhcp-interface-ha` |
| `current_tag` | `pkg-{series}-bind-plugin` | `pkg-dhcp-interface-ha-{series}` |
| `source_tag` | `os-bind-rp-{series}` | `dhcp-interface-ha-{series}` |
| `follow_upstream` | `true` | `false` |
| `prerelease` | `false` | `true` |

`run-name`:

```yaml
run-name: >-
  <title> ${{ github.event_name == 'pull_request_target'
  && format('PR #{0} {1}', github.event.pull_request.number, github.event.action)
  || inputs.pr && format('candidate PR #{0}', inputs.pr)
  || format('production {0}', inputs.series || github.event_name) }}
```

`on`: `schedule` cron `'23 5 * * *'`; `workflow_dispatch` inputs `series`
("Series for a production build (default the active series)") and `pr` ("PR
number for a candidate build"), both string, default `''`;
`pull_request_target` types `[opened, synchronize, reopened, closed]`,
branches `[master]`. Workflow `permissions: {}`; workflow env
`SOURCE_DIRECTORY: <source_directory>`.

| Job | Condition | Permissions | Outputs | Env | Step (filled by) |
| --- | --- | --- | --- | --- | --- |
| `status` | `github.event_name == 'pull_request_target' && github.event.action != 'closed'` | `statuses: write`, `pull-requests: read` | — | `GH_TOKEN`, `PR` (event number), `HEAD_SHA` (event head sha) | "Write the package build status" (06.1) |
| `discover` | `github.event_name == 'schedule' \|\| (github.event_name == 'workflow_dispatch' && inputs.pr == '')` | none | `applies`, `series` (steps.discover) | `GH_TOKEN`, `SERIES` (inputs.series), `CURRENT_TAG`, `FOLLOW_UPSTREAM` (policy) | id `discover` "Discover work for the active series" (06.2) |
| `request` | `(github.event_name == 'workflow_dispatch' && inputs.pr != '') \|\| github.event.pull_request.merged` | `pull-requests: read` | `applies`, `purpose`, `series`, `source_sha`, `pr`, `group` (steps.request) | `GH_TOKEN`, `PR: ${{ inputs.pr \|\| github.event.pull_request.number }}`, `MERGED_HEAD` (event head sha) | id `request` "Resolve the request" (06.3) |
| `candidate-status` | `${{ !cancelled() && needs.request.outputs.purpose == 'candidate' }}`, needs `[request, target]` | `statuses: write` | — | `GH_TOKEN`, `HEAD_SHA` (request.source_sha), `RESULT` (needs.target.result) | "Write the package build status on the built head" (06.3) |

All step jobs run on `ubuntu-latest` with `timeout-minutes: 5`.

Job `target`: `needs: [discover, request]`,

```yaml
    if: >-
      !cancelled() && (needs.discover.outputs.applies == 'true' || needs.request.outputs.applies == 'true')
```

permissions `contents: read`, `actions: read`; concurrency group
`${{ needs.request.outputs.group || format('plugin-<component>-{0}', needs.discover.outputs.series) }}`;
`uses: ./.github/workflows/plugin-target.yml`; `with`: the policy values plus
`series: ${{ needs.discover.outputs.series || needs.request.outputs.series }}`,
`purpose: ${{ needs.request.outputs.purpose || 'production' }}`,
`source_sha: ${{ needs.request.outputs.source_sha || github.sha }}`,
`pr: ${{ needs.request.outputs.pr }}`; `secrets`: each of the three as
`${{ needs.request.outputs.purpose != 'candidate' && secrets.<NAME> || '' }}`.

## Notes

- `discover` outputs one series and `target` is a single call, over a matrix,
  because only the active series is ever a target and an empty matrix fails its
  job (user decision, 2026-10-20).
- One `request` job resolves candidates and promotions so the policy inputs
  appear in one call, over separate calls repeating them (2026-10-20).
- The index receives the `source_tag` template because retention deletes the
  source Release `<source_tag>-<version>` and may not scan Releases; the design's
  index inputs now include it (user decision, 2026-10-20).
- The index concurrency group sits on the calling job, which covers the whole
  called workflow, and keeps the existing `queue: max` (2026-10-20).
- The sync app gets only `contents: write`, as the existing workflows use;
  upstream `stable/*` has no workflow files and phase 09 removes them from the
  release branches (2026-10-20).
