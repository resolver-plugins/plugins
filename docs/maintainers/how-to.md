# Maintainer how-to guides

Steps for routine maintainer tasks. Replace `<entry>` with `bind-plugin.yml`
or `ha-dhcp-interface-plugin.yml`. The [reference](reference.md) lists every
workflow, artifact and Release; the [explanation](explanation.md) covers why.

## Build a candidate for a PR

1. Open the PR against `master` as a draft and iterate on it; drafts do not
   build.
2. Mark it ready for review (`gh pr ready N`). The candidate builds, and
   every later push builds the new head. Keep the PR up to date with
   `master`.
3. For a draft or a fork PR, request the build instead:

   ```sh
   gh workflow run <entry> -f pr=N
   ```

4. Watch it:

   ```sh
   gh run list --workflow <entry>
   gh run watch <id>
   ```

On success, the PR head gets `Package build / <directory>` = success. Every
new head needs a new candidate.

## Test a candidate on an appliance

1. Download the qualified archive:

   ```sh
   gh run download <run> -n qualified-<component>-<series>-<head> -D <dir>
   ```

2. On the node, BACKUP first, install it from a temporary local repository:

   ```sh
   mkdir -p <tmp>/repo <tmp>/repos
   cp <archive> <tmp>/repo/
   pkg repo <tmp>/repo
   echo 'cand: { url: "file://<tmp>/repo", enabled: yes }' > <tmp>/repos/cand.conf
   pkg -o REPOS_DIR=<tmp>/repos install -r cand <package>
   ```

Merging publishes that archive unchanged, so a tested node already runs the
release. If the PR head changes or another publication takes the candidate's
version, reinstall the published package with `pkg install -f <package>`.

## Request a production build

```sh
gh workflow run <entry> -f series=X.Y
```

Only the active series is accepted. The run builds the release branch plus
`master`'s uncarried changes, or stops as `skip` when there is nothing to do.

## Recover a failed run

1. Read the failed job's log and fix the cause.
2. Use **Re-run failed jobs** on the run. Reruns finish an interrupted
   publication from the job's inputs.

For specific failures:

- **Promotion failed** because the candidate expired, is missing or was
  overtaken: request a production build.
- **Index or readback failed**: the publication stands and retention was
  skipped. Rerun the failed jobs.
- **Merge or carry conflict** in `prepare`: see the next guide.

## Resolve an upstream conflict

`prepare` fails when the upstream `stable/<series>` merge or the carry from
`master` conflicts outside the `PLUGIN_VERSION` and `PLUGIN_REVISION` lines.
Nothing is published and the target stays failed.

1. Read the conflict named in the `prepare` log.
2. Resolve its cause on `master` through a PR, for example by adapting the
   fork change to the upstream code.
3. Request a production build for the series.

## Build locally

On a FreeBSD host with the series' OPNsense installed:

```sh
make -C dns/bind PLUGIN_DEVEL= PLUGIN_ABI=<series> package
make -C net/ha-dhcp-interface PLUGIN_DEVEL= PLUGIN_ABI=<series> package
```

`PLUGIN_DEVEL=` is required; without it `Mk/devel.mk` builds the retired
`-devel` package, which conflicts with the release package and cannot be
promoted.

## Roll back a release

No workflow performs rollbacks.

1. Take the previous versioned Release of the plugin and series; it keeps its
   archive and `build-record.json`.
2. Replace the current Release's assets with that archive and record.
3. Let the next index run for the series refresh the feed.

To build the revision again, first delete the newer versioned Release and its
source Release: a versioned tag cannot be republished from a different
source. Systems that already installed the newer revision do not downgrade
through `pkg`; prefer a fix in a new revision.

## Move a client to a new series feed by hand

Use this when a system was upgraded without the migration hooks installed, or
its repository file is custom.

1. Back up `/usr/local/etc/pkg/repos/resolver-plugins.conf`.
2. Change only its URL to
   `https://resolver-plugins.github.io/repository/pkg/${ABI}/<series>/latest`,
   keeping `${ABI}` literal.
3. Refresh and install:

   ```sh
   pkg update -f -r resolver-plugins
   pkg install os-bind-rp
   pkg install os-ha-dhcp-interface
   ```

## Rotate a GitHub App key

1. Create a new private key in the App's credential settings.
2. Update the matching secret (`RP_DISTRIBUTION_APP_PRIVATE_KEY` or
   `RP_SYNC_APP_PRIVATE_KEY`).
3. Delete the old key in the App.

Never replace an App credential with a broader token.

## Add a workflow trigger

1. Add the trigger to the workflow.
2. Add the event to the Actions policy "Allow the CI workflows' events"
   (Settings → Actions → Rules); otherwise the event is blocked.

## Check a CI change

1. Follow the workflow rules in [`AGENTS.md`](../../AGENTS.md#workflows).
2. Compare the change with every row of
   [Expected outcomes](reference.md#expected-outcomes).
3. Review the diff and run `git diff --check`; do not dispatch Actions to
   validate it.
4. Commit with `[skip ci]`.

## Switch to the self-hosted runner

The branch `ci/self-hosted-runners` keeps the CI as it last ran on a
self-hosted runner. It differs from `master` by two commits, reverted on
`master` by `Revert "[skip ci] Fix the self-hosted FreeBSD builder in build
jobs"` and `Revert "[skip ci] Run every CI job on the self-hosted runner"`.
Those commits:

- run every job with `runs-on: [self-hosted, opnsense-dispatch]`;
- use `.github/actions/freebsd-builder` instead of `vmactions/freebsd-vm` in
  `build`, `qualify` and `stage`;
- pass the index signing key through the runner in a mode 0600 file before it
  reaches the builder, which changes the signing boundary.

The runner host must provide:

- a Linux runner registered to `resolver-plugins/plugins` with the label
  `opnsense-dispatch`, one job at a time, a job-start hook that empties the
  workspace, and a runner group restricted to this repository;
- `$HOME/builderctl.py` with `reset <series>`, `exec <series> -- <command>`
  and `clean <series>`;
- one persistent FreeBSD builder per series with OPNsense's build
  prerequisites in its baseline.

With the maintainer's approval of the signing-boundary change:

1. On a branch from `master`, revert the two revert commits, newest first,
   keeping workflow changes made since.
2. Add the active series to the release map in
   `.github/actions/freebsd-builder/action.yml` (for example `15.1` →
   `26.7`), and update this guide and the credentials table in the
   [reference](reference.md#credentials).
3. Merge with `[skip ci]`, then confirm with one candidate build.

Switch back by reverting those commits again.
