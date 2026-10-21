# Building and releasing

Four workflows in `.github/workflows/` build, qualify and publish the two
plugins. Pull requests are developed against `master`; release branches are
written only by the pipeline (see [fork model](fork-model.md#branches)).

| Workflow | Role | Triggers |
| --- | --- | --- |
| `bind-plugin.yml` | Entry for `os-bind-rp` (`dns/bind`) | daily schedule, `workflow_dispatch`, `pull_request_target` on `master` |
| `ha-dhcp-interface-plugin.yml` | Entry for `os-dhcp-interface-ha` (`net/dhcp-interface-ha`) | same |
| `plugin-target.yml` | Lifecycle for one plugin and one series | called by the entries |
| `package-index.yml` | Signed feed refresh and retention | called by the lifecycle |

Each entry holds its plugin's policy (directory, branch prefix, tag names,
whether it follows upstream) as inputs to `plugin-target.yml`. Nothing
dispatches another workflow, and the two plugins never wait for each other.

## Requests

Only the newest final Community series builds. A request for any other series
fails. Replace `<entry>` with `bind-plugin.yml` or `ha-dhcp-interface-plugin.yml`.

| Request | How | Result |
| --- | --- | --- |
| Discovery | the daily schedule, or `gh workflow run <entry>` | Builds and publishes the active series when it has no build record yet, or (BIND) when upstream changed its package. Otherwise it writes the reason to the run summary and stops. |
| PR status | opening or updating a PR to `master` | Sets `Package build / <directory>` to pending when the PR changes the plugin directory, otherwise to success "Not applicable". Nothing is built. |
| Candidate | `gh workflow run <entry> -f pr=N` | Builds and qualifies an unsigned candidate of the open PR's head, then sets the status on that head to success or failure. Nothing is published. |
| Promotion | merging a PR that changes the plugin directory | Publishes the candidate built for the merged head without rebuilding it. |
| Production | `gh workflow run <entry> -f series=X.Y` | Builds, qualifies and publishes the active series from the release branch plus `master`'s uncarried changes. |

Every new PR head needs a new candidate before it can merge. `master` requires
the status on the current head and an up-to-date branch. Watch a run with
`gh run list --workflow <entry>` and `gh run watch <id>`. Run names show the
purpose: PR number, candidate, production series or event.

## Lifecycle

`plugin-target.yml` runs these jobs in order. Each needs the previous one to
succeed.

1. `prepare` (Ubuntu) checks the series is active, reads the upstream
   environment (FreeBSD release, ABI, tools tag, core and plugins commits),
   reads the current Release and chooses the next version. It starts from the
   release branch head, merges upstream `stable/<series>` when the series is new
   or upstream changed, carries the plugin directory's changes from `master`,
   and adds one version commit. A production run with nothing to do stops here
   as `skip`. A promotion instead finds the candidate artifact for the merged
   head.
2. `build` (FreeBSD VM matching the series) runs `make package` on the prepared
   source and writes `build-record.json`. It has no credentials.
3. `qualify` (clean FreeBSD VM) installs the archive from a local repository,
   checks the package, and compares name, origin, version and ABI with the
   build record. It uploads the artifact
   `qualified-<component>-<series>-<head>` for 30 days. Candidates end here.
4. `publish` creates the versioned Release as a draft, uploads the archive and
   record, publishes it, then advances the current Release if the version is
   newer. It uses the distribution app.
5. `sync-source` pushes the prepared commit to the release branch, tags it and
   creates the source Release in this repository. It uses the sync app and does
   not block the feed.
6. `index` calls `package-index.yml` after a publication that advanced
   something: `select` reads the Releases the feed serves, `stage` signs the
   catalogue on a FreeBSD VM, `publish` replaces
   `pkg/<abi>/<series>/latest/` on `gh-pages` in one commit, `readback`
   compares the public feed with the staged files, and `retain` deletes the
   Release two versions back, keeping the current one and one rollback.

Promotion skips `build` and `qualify`. A concurrency group per plugin and series
(and per PR for candidates) lets the newest queued request replace older pending
ones; a running publication is never cancelled. The index has its own group
`package-repository-index`.

## Runners

Jobs run on GitHub-hosted `ubuntu-latest` runners. `build`, `qualify` and
`stage` run their FreeBSD work in a disposable `vmactions/freebsd-vm` VM of the
series' FreeBSD release, read from upstream in `prepare`; nothing is kept
between runs.

The branch `ci/self-hosted-runners` keeps the CI as it ran on the self-hosted
runner labelled `opnsense-dispatch` with persistent FreeBSD builders
(`.github/actions/freebsd-builder`, which maps FreeBSD releases to builders by
hand). It is a reference for a future self-hosted setup, not maintained with
`master`.

## Actions policy

The repository Actions policy "Allow the CI workflows' events" (id 8127, under
Settings → Actions → Rules) lists the events the four CI workflows may run on:
`schedule`, `workflow_dispatch`, `pull_request_target` and `workflow_call`. It
replaces GitHub's default policy that blocks `pull_request_target` in public
repositories from November 2, 2026. The PR status and promotion jobs need
`pull_request_target` and never check out or run PR code. A new trigger in one of
these workflows must also be added to the policy.

## Versions

Both plugins use `<series>_<n>`, for example `26.7_1`. `n` is the current
Release's revision plus one, or `1` for a new series. The version is fixed in
`prepare` before building and the archive is never renamed. Archives are named
`<name>-<version>.pkg`.

## Failures

A failed job stays failed and its dependents do not run. Nothing repairs it. Fix
the cause, then use "Re-run failed jobs" on the run; reruns finish an
interrupted publication from the job's inputs.

- Promotion fails when the candidate artifact expired or is missing, or when its
  version is no longer newer than the current Release. Request production with
  `gh workflow run <entry> -f series=X.Y`.
- A merge or carry conflict beyond the `PLUGIN_VERSION` and `PLUGIN_REVISION`
  lines fails the target. See [upstream synchronization](upstream-sync.md).
- A failed index or readback leaves the publication in place and skips
  retention. Rerun the failed jobs.

## Local build

On a FreeBSD host with the series' OPNsense installed:

```
make -C dns/bind PLUGIN_DEVEL= PLUGIN_ABI=<series> package
make -C net/dhcp-interface-ha PLUGIN_DEVEL= PLUGIN_ABI=<series> package
```

The plugin test suites also run locally. CI runs none.

`PLUGIN_DEVEL=` is required: `Mk/devel.mk` on `master` otherwise builds the
`-devel` package, a separate name and origin that conflicts with the release
package and cannot be promoted. `-devel` packages are retired.

## Testing on an appliance

Test a change on an OPNsense node with its PR's candidate, not a `-devel`
build. Request the candidate, then download the archive from the
`qualified-<component>-<series>-<head>` artifact:

```
gh workflow run <entry> -f pr=N
gh run download <run> -n qualified-<component>-<series>-<head> -D <dir>
```

On the node, install it from a temporary local repository, BACKUP node first:

```
mkdir -p <tmp>/repo <tmp>/repos
cp <archive> <tmp>/repo/
pkg repo <tmp>/repo
echo 'cand: { url: "file://<tmp>/repo", enabled: yes }' > <tmp>/repos/cand.conf
pkg -o REPOS_DIR=<tmp>/repos install -r cand <package>
```

Merging publishes that archive unchanged, so a tested node already runs the
release. The candidate carries the next release version before it is
published: if the PR head changes or another publication takes that version,
reinstall the published package on the tested node (`pkg install -f <package>`).

## Changing the CI

- The CI was built from scratch; do not reintroduce retired helpers.
- Use workflow features first: triggers, `if:`, `needs`, matrices, `concurrency`,
  reusable workflows, artifacts, outputs, and `git`, `gh`, `jq`, `make`, `pkg`.
- Add a script file only after a written justification shows every simpler
  alternative fails, and the maintainer approves it.
- Stop before any script or inline workflow script longer than 30 physical lines.
- Run no tests, linters or validators in CI.
- Hard-code no series, ABI, FreeBSD release, package name or upstream commit.
- Commit CI and documentation changes with `[skip ci]`.
