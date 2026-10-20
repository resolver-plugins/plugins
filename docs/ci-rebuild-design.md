# CI rebuild design

Structure and contracts for the [specification](ci-rebuild-spec.md). Internals of
each part belong to its implementation stage in the [plan](ci-plan/).

## Overview

```
bind-plugin-release.yml ─┐                       ┌─ prepare  (Ubuntu)
                          ├─ plugin-target.yml ───┤  build    (FreeBSD VM)
ha-dhcp-interface-        │   one plugin, one     │  qualify  (clean FreeBSD VM)
  release.yml ───────────┘   series              │  publish  (Ubuntu)
                                                  ├─ sync-source (Ubuntu)
                                                  └─ index: repository-index.yml
                                                       stage (FreeBSD VM), publish,
                                                       readback, retain
```

Each plugin has one entry workflow on `master`. Entries hold the plugin's policy
as inputs and call one reusable lifecycle per target. The lifecycle calls the
index after a successful publication. Nothing dispatches another workflow.

## Components

| File (`.github/workflows/`) | Role | Triggers |
| --- | --- | --- |
| `bind-plugin-release.yml` | BIND entry | schedule (daily), `workflow_dispatch`, `pull_request_target` on `master` |
| `ha-dhcp-interface-release.yml` | HA entry | same |
| `plugin-target.yml` | Lifecycle for one plugin and series | `workflow_call` only |
| `repository-index.yml` | Sole feed writer and retention | `workflow_call` only |

The entries keep their current file names, so cutover replaces them in place.
Everything else under `.github/workflows/` and `.github/ci/` is retired.

### Entry inputs (the package policy)

Each entry passes these to `plugin-target.yml`; no other file names a package.

| Input | BIND | HA |
| --- | --- | --- |
| `component` | `bind_plugin` | `dhcp` |
| `source_directory` | `dns/bind` | `net/dhcp-interface-ha` |
| `branch_prefix` | `release/bind-rp` | `release/dhcp-interface-ha` |
| `current_tag` (with `{series}`) | `pkg-{series}-bind-plugin` | `pkg-dhcp-interface-ha-{series}` |
| `source_tag` (with `{series}`) | `os-bind-rp-{series}` | `dhcp-interface-ha-{series}` |
| `follow_upstream` | `true` | `false` |
| `prerelease` | `false` | `true` (while experimental) |

`component` matches the existing feed `channel.json` keys. Versioned tags append
`-<version>` to `current_tag` and `source_tag`, for example
`pkg-26.7-bind-plugin-26.7_10` and `os-bind-rp-26.7-26.7_10`.

### Entry jobs

| Event | Jobs |
| --- | --- |
| schedule, or `workflow_dispatch` without `pr` | `discover` (Ubuntu) → `target` (purpose `production`, the active series when it needs work) |
| `workflow_dispatch` with `pr` | `target` (purpose `candidate`) → `status` (success on the built head) |
| `pull_request_target` opened, synchronize, reopened | `status` (pending, or success "not applicable") |
| `pull_request_target` closed and merged, changing `source_directory` | `target` (purpose `promote`) |

- `discover` resolves the active series (below). A target needs work when its
  current Release is missing (new series), when `follow_upstream` is set and the
  upstream package path changed since the current build record, or when the
  dispatch explicitly asked for that series.
- `status` writes the commit status `Package build / <source_directory>` with
  `GITHUB_TOKEN` (`statuses: write`) and runs no package code. A PR needs the
  build when its changed files (`gh pr view --json files`) include
  `source_directory`; otherwise the status is success "not applicable".
- Candidates, promotion and production target only the active series; a request
  for a retired series fails with that reason.
- Concurrency on the `target` call job: `plugin-<component>-<series>` for
  production and promotion (one active lifecycle, newest pending request wins);
  `candidate-<component>-<pr>` for candidates.

## Lifecycle (`plugin-target.yml`)

Inputs: the entry inputs, plus `series`, `purpose` (`candidate`, `production`,
`promote`), `source_sha` (the `master` commit to carry up to: the PR head, or
`master`'s head for production) and `pr` (candidates). Secrets:
`RP_DISTRIBUTION_APP_PRIVATE_KEY`, `RP_SYNC_APP_PRIVATE_KEY`, `RP_PKG_SIGNING_KEY`.
Jobs run in order and each needs the previous one's success.

| Job | Runs on | Does | Credentials |
| --- | --- | --- | --- |
| `prepare` | Ubuntu | Read environment and current Release, decide, prepare source | read-only |
| `build` | FreeBSD VM | `make package` from the prepared source | none |
| `qualify` | clean FreeBSD VM | Install the archive on a fresh OPNsense of the series | none |
| `publish` | Ubuntu | Versioned and current distribution Releases | distribution app |
| `sync-source` | Ubuntu | Put the prepared commit on the release branch; tag it and create the source Release | sync app |
| `index` | `repository-index.yml` | Refresh the series feed, then retention | signing key, distribution app, sync app |

`index` and `sync-source` both need only `publish`, so a failed source sync does
not block the feed. Candidates stop after `qualify`; promotion skips `build` and
`qualify`.

### `prepare` outputs

- `operation`: `build`, `promote` or `skip` with a readable `reason`. Production
  skips when the release branch head equals the current record's `source_base`,
  the upstream package is unchanged and there is nothing to carry. Promotion fails clearly when the candidate
  artifact is missing or its version is not newer than current.
- Environment (from upstream, below): `freebsd_release`, `abi`, `tools_tag`,
  `core_commit`, `plugins_commit`, `upstream_package_commit`.
- `version` and `previous_tag` (the versioned tag current points to, or empty).
- Artifact `source-<component>-<series>-<run_id>`: a `git bundle` holding the
  prepared commit, whose sha is output `prepared_sha`.

The prepared commit starts from the release branch head (for a new series, the
previous series' release branch head) and adds, in order:

1. `git merge upstream/stable/<series>` when the series is new or upstream
   changed;
2. the carry: `git diff <last carried>..<source_sha> -- <source_directory>`
   applied with `git apply --3way`, where `<last carried>` is the
   `Master-Commit` trailer of the newest version commit on the release branch;
   nothing when the diff is empty;
3. one version commit setting `PLUGIN_VERSION` and `PLUGIN_REVISION`, with the
   trailer `Master-Commit: <source_sha>`.

A merge or apply conflict fails the target. The diff compares trees, so a
squash-merged PR carries cleanly after its candidate.

### Artifacts between jobs

| Artifact | Written by | Contents | Retention |
| --- | --- | --- | --- |
| `source-<component>-<series>-<run_id>` | `prepare` | source bundle | 1 day |
| `build-<component>-<series>-<run_id>` | `build` | `<name>-<version>.pkg`, `build-record.json` | 1 day |
| `qualified-<component>-<series>-<source_sha>` | `qualify` | the same two files plus the source bundle | 30 days |

Only `qualify` writes the `qualified-` name, so its existence means the archive
passed. It carries everything `publish` and `sync-source` need, so a promotion
days later does not depend on the short-lived artifacts. Promotion looks it up by
the merged PR head, the only cross-run lookup.

### Versions

`<series>_<n>` for both plugins. `prepare` reads the current Release's archive
version: `n` is its revision plus one, or `1` when the series has no current
Release. The build and the record use the prepared version; nothing renames an
archive afterward.

### Publication

- Versioned Release in `resolver-plugins/repository`: create as draft, upload the
  archive and `build-record.json`, publish. A rerun deletes a leftover draft of
  the same tag before creating it; an already published identical tag completes
  as done.
- Current Release: under the target lock, advance only when the version is newer
  than the current one (replace its assets); equal completes as done; older leaves
  it. HA Releases keep the prerelease flag while `prerelease` is set.

### `sync-source`

Fetch the bundle and push the prepared commit to the release branch (creating
it for a new series). Only the pipeline writes release branches, under the
target lock, and every write is a publication, so the branch head is still the
prepared commit's base: the push is a fast-forward, and Git rejects it if the
branch moved anyway. A promoted candidate whose base is no longer the head was
overtaken, which `prepare` already refuses. Then tag the pushed commit
`<source_tag>-<version>` and create the source Release in
`resolver-plugins/plugins` with the archive and `build-record.json`. A rerun
whose branch already holds the commit only ensures the tag and Release. The push
uses the sync app, which cutover makes the only allowed writer.

## Data formats

### `build-record.json` (written once by `build`)

`package`, `origin`, `version`, `series`, `abi`, `freebsd_release`,
`source_base` (release branch head used), `master_commit` (`source_sha`),
`source_commit` (prepared), `control_commit`,
`tools_tag`, `core_commit`, `plugins_commit`, `upstream_package_commit`,
`pkg_version`, `previous_tag`, `run_url`.

### Feed `channel.json` (written by the index)

`{"schema": 4, "series", "package_abi", "components": {<component>: {"current_tag",
"version", "packages": [<archive names>]}}}`. The index reads
`components.*.current_tag` from the existing file (schema 3 has the same field)
to know which current Releases a feed serves, so the frozen `bind_runtime` entry
stays in the 26.1 and 26.7 feeds.

## Upstream discovery (`discover` and `prepare`)

- Active series: highest numeric final tag (`X.Y` or `X.Y.Z`) in `opnsense/core`;
  series is `X.Y`. Prerelease tags (`.b`, `.r1`) do not count.
- Usable: the matching `opnsense/tools` tag exists and has `config/<series>/`.
- `freebsd_release` from that tag's `config/<series>/build.conf` `OS`; `abi` is
  `FreeBSD:<major>:amd64`.
- PHP and Python versions are not passed: the build VM installs the series'
  OPNsense core from its package repository, and the plugins framework reads
  them from it (`Mk/defaults.mk`). `make package` also installs the plugin's
  dependencies from that repository and records them in the manifest.
- `core_commit`, `plugins_commit`: heads of `stable/<series>` when prepared.
- `upstream_package_commit`: last `upstream/stable/<series>` commit touching
  `source_directory`.
- The OPNsense package repository for VMs comes from core's
  `OPNsense.conf.shadow.in` with `https://pkg.opnsense.org/${ABI}/<series>/latest`.

These are read with `git ls-remote`, `git fetch` and `gh api`, once in `prepare`
(and `discover`), and fail clearly on an unexpected format.

## Index (`repository-index.yml`)

Inputs: `series`, `abi`, `component`, `current_tag`, `source_tag` (the
template, so retention can name the source Release). Concurrency group
`package-repository-index` (existing name), whole workflow.

| Job | Runs on | Does |
| --- | --- | --- |
| `select` | Ubuntu | Read the feed's `channel.json` and each listed current Release, plus the input one; stop if nothing changed |
| `stage` | FreeBSD VM | Download the archives, `pkg repo` with the series' `pkg` and the signing key, add `resolver-plugins.pub` and `channel.json` |
| `publish` | Ubuntu | Replace `pkg/<abi>/<series>/latest/` on `gh-pages` in one commit |
| `readback` | Ubuntu | Fetch the public feed, compare with the staged files, retry briefly while Pages deploys |
| `retain` | Ubuntu | Delete the release two versions back (below) |

Retention: read the previous versioned Release's `build-record.json`; its
`previous_tag` names the release to drop. Delete that distribution Release and
tag and its source Release; keep the source tag. A missing Release means nothing
to delete.

## Client series upgrade

The feed path, the `<series>_<n>` versions and these installed files carry a
client across a series. Each plugin owns its own set.

| Role | BIND (`dns/bind/src/`, exists) | HA (`net/dhcp-interface-ha/src/`, new) |
| --- | --- | --- |
| Pre-upgrade marker | `etc/rc.syshook.d/upgrade/50-bind-rp-repository` | `etc/rc.syshook.d/upgrade/50-dhcp-interface-ha-repository` |
| Repository provider | `opnsense/scripts/firmware/repos/ResolverPlugins.sh` | `opnsense/scripts/firmware/repos/ResolverPluginsHA.sh` |
| Start hook | `etc/rc.syshook.d/start/50-bind-rp-repository` | `etc/rc.syshook.d/start/50-dhcp-interface-ha-repository` |
| Reconcile worker | `opnsense/scripts/OPNsense/Bind/bindRepositoryReconcile.py` | `opnsense/scripts/dhcp_interface_ha/repository_reconcile.py` |
| Pending marker | `/var/db/os-bind-rp/repository-reconcile.pending` | `/var/db/os-dhcp-interface-ha/repository-reconcile.pending` |

Shared contract: both providers write the same managed
`/usr/local/etc/pkg/repos/resolver-plugins.conf` with the same content and
recognize the same managed shapes and legacy URLs, so either or both may run.
Workers accept only their own plugin at `<target series>_<n>` with the expected
origin and ABI, reject removals and downgrades in the dry-run plan and clear
their marker only after success. Dependencies, including BIND, resolve natively
from all enabled repositories.

## Frozen, never written by the new CI

- Pages feed `pkg/${ABI}/latest` and Release feeds `pkg-26.1`, `pkg-26.7`.
- Runtime Releases `pkg-<series>-bind-runtime` (and their versioned ones).
- Fingerprinted versioned Releases from the old pipeline; cutover lists them for
  one manual cleanup, keeping each target's current and one rollback.

## Retirement map

| Existing | Disposition |
| --- | --- |
| `bind-plugin-release.yml`, `ha-dhcp-interface-release.yml` | Replaced in place by the new entries |
| `upstream-sync.yml` and its sync/bootstrap PR machinery | Replaced by `discover` and `prepare` |
| `bind-runtime-package.yml`, `bind920-release.yml`, `bind-tools-release.yml`, `bind920-candidate.yml`, `packages/` | Retired with the runtime builds |
| `repository-index.yml`, `repository-index-series.yml` | Replaced by the new `repository-index.yml` |
| `pr-release-cleanup.yml` and development Releases | Retired; candidates are artifacts |
| `.github/ci/**` (helpers, profiles, seals, tests) | Retired |

## Decisions

- Version ordering compares revision numbers, over running `pkg version` in a
  VM, because every version in a target is `<series>_<n>` (2026-10-19).
- PHP and Python come from the installed OPNsense core, over passing tools
  `build.conf` values to `make`, because the plugins framework already reads
  them natively (2026-10-19).
- Package policy lives in the entry inputs, over a `ci.mk` per package, because
  the entries exist per plugin and must know the branch before reading it
  (2026-10-19).
- Per-package Releases hold the archive and build record only, over a signed
  per-Release catalogue, because clients read only the Pages feed (2026-10-19).
- The index is called from the lifecycle after `publish`, over a `workflow_run`
  trigger, because the caller knows a publication happened (2026-10-19).
- Prepared source travels as a bundle artifact and lands after publication by
  fast-forward push, over pushing before building, so the branch only ever holds
  published versions (2026-10-19).
- VMs use the `pkg` from the series' OPNsense repository (its latest release),
  over a separately installed creator or reader `pkg`, because that is the `pkg`
  upstream defines for the release; it is recorded by version only, since
  OPNsense has republished `pkg` with an unchanged version (2026-10-19).
- Changes reach a release branch as the plugin directory's diff from `master`,
  tracked by a `Master-Commit` trailer, over merging `master` into release
  branches, because `master` follows upstream `master` while release branches
  follow `stable/<series>` (2026-10-19).
- PR applicability comes from the PR's changed files, over the base branch,
  because `master` holds CI, documentation and both plugins (2026-10-19).
