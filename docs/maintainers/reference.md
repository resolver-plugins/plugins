# Maintainer reference

Exact facts about the fork's packages, branches, CI, Releases and signed feed.
For the reasons behind them, see the [explanation](explanation.md); for
procedures, the [how-to guides](how-to.md).

## Packages

| Package | Origin | Source | Conflicts |
| --- | --- | --- | --- |
| `os-bind-rp` | `opnsense/os-bind-rp` | `dns/bind` | `os-bind` (official) |
| `os-ha-dhcp-interface` | `opnsense/os-ha-dhcp-interface` | `net/ha-dhcp-interface` | `os-dhcp-interface-ha`, `os-wan-ha-dhcp` |

- Names and origins are fixed across series; renaming is a maintainer decision.
- Archives are named `<name>-<version>.pkg`.
- `os-bind-rp` keeps `PLUGIN_NAME=bind-rp` and `PLUGIN_CONFLICTS=bind`, a
  minimum OPNsense of `26.1.11_10` and the minimum BIND in
  `PLUGIN_DEPEND_FORMULA` (`bind920 >= 9.20.26`).
- `os-ha-dhcp-interface` was published as `os-dhcp-interface-ha` up to
  `26.7_3`; that name's branch and Releases are frozen.
- Plugin details: [plugin documentation](../plugins/README.md).

## Branches

| Branch | Role | Written by |
| --- | --- | --- |
| `master` | Development of both plugins, the CI and the docs | PRs |
| `release/bind-rp/<series>` | Build source of `os-bind-rp` for a series | pipeline only |
| `release/ha-dhcp-interface/<series>` | Build source of `os-ha-dhcp-interface` for a series | pipeline only |
| `release/dhcp-interface-ha/<series>` | Former HA name | frozen |
| `ci/self-hosted-runners` | CI as last run on the self-hosted runner | reference only |
| upstream `master`, `stable/<series>` | `opnsense/plugins` | read by the pipeline |

Release branches carry no workflows and are never rewritten. Each version
commit sets `PLUGIN_VERSION` and `PLUGIN_REVISION` and has the trailer
`Master-Commit: <sha>`, the last `master` commit carried.

`master` requires the `Package build / <directory>` status on the current PR
head and an up-to-date branch.

## Versions

| `version_scheme` | Format | Example | Used by |
| --- | --- | --- | --- |
| `series` | `<series>_<n>` | `26.7_1` | `os-ha-dhcp-interface` |
| `series-upstream` | `<series>.<upstream>_<n>` | `26.7.1.34_1` | `os-bind-rp` |

| Item | Value |
| --- | --- |
| `<upstream>` | `PLUGIN_VERSION` of `dns/bind/Makefile` on upstream `stable/<series>`, without upstream's `PLUGIN_REVISION`; must be a dotted number |
| `<n>` | `1` when there is no current Release or the part before `_` differs from the current version's; otherwise the current `<n>` plus one |
| Fixed | In `prepare`, before building; an archive is never renamed |
| Ordering | Compare the part before `_` as dotted numbers (`sort -V`), then `<n>`; `26.7.1.34_1` is newer than `26.7_11` |

The build record keeps the upstream version in `upstream_version`.

## Workflows

All in `.github/workflows/`. Nothing dispatches another workflow.

| File | Role | Triggers |
| --- | --- | --- |
| `bind-plugin.yml` | Entry for `os-bind-rp` | daily schedule, `workflow_dispatch`, `pull_request_target` on `master` |
| `ha-dhcp-interface-plugin.yml` | Entry for `os-ha-dhcp-interface` | same |
| `plugin-target.yml` | Lifecycle for one plugin and one series | `workflow_call` |
| `package-index.yml` | Sole feed writer and retention | `workflow_call` |

The repository Actions policy "Allow the CI workflows' events" (id 8127,
Settings → Actions → Rules) allows `schedule`, `workflow_dispatch`,
`pull_request_target` and `workflow_call`. It replaces GitHub's default policy, which
blocks `pull_request_target` in public repositories from November 2, 2026. A
new trigger must also be added to the policy.

### Entry inputs

Each entry passes its policy to `plugin-target.yml`; no other file names a
package.

| Input | `bind-plugin.yml` | `ha-dhcp-interface-plugin.yml` |
| --- | --- | --- |
| `component` | `bind_plugin` | `dhcp` |
| `source_directory` | `dns/bind` | `net/ha-dhcp-interface` |
| `branch_prefix` | `release/bind-rp` | `release/ha-dhcp-interface` |
| `current_tag` | `pkg-{series}-bind-plugin` | `pkg-ha-dhcp-interface-{series}` |
| `source_tag` | `os-bind-rp-{series}` | `ha-dhcp-interface-{series}` |
| `follow_upstream` | `true` | `false` |
| `prerelease` | `false` | `true` |
| `version_scheme` | `series-upstream` | `series` |

`component` matches the feed's `channel.json` keys. Versioned tags append
`-<version>`, for example `pkg-26.7-bind-plugin-26.7_10` and
`os-bind-rp-26.7-26.7_10`.

### Requests

Replace `<entry>` with `bind-plugin.yml` or `ha-dhcp-interface-plugin.yml`.
Only the active series builds; a request for another series fails.

| Request | Started by | Jobs | Result |
| --- | --- | --- | --- |
| Discovery | daily schedule, or `gh workflow run <entry>` | `discover` → `target` (`production`) | Builds and publishes when the active series has no build record, or (BIND) upstream changed the package; otherwise writes the reason and stops |
| PR status | PR to `master` opened, updated or reopened | `status` | `Package build / <directory>`: success "Not applicable" when no packaged file changes; failure "Add a changelog entry to `<directory>/pkg-descr`" when code changes without `pkg-descr`; otherwise pending; builds nothing |
| Candidate | `gh workflow run <entry> -f pr=N` | `target` (`candidate`) → `status` | Unsigned, qualified candidate of the PR head; status success or failure on that head; publishes nothing |
| Promotion | merging a PR that changes packaged files | `target` (`promote`) | Publishes the candidate of the merged head without rebuilding |
| Production | `gh workflow run <entry> -f series=X.Y` | `target` (`production`) | Builds, qualifies and publishes the release branch plus `master`'s uncarried changes |

Packaged files are `<directory>/src/`, `Makefile`, `pkg-descr` and the `+*`
install scripts; code is the same without `pkg-descr`. A candidate request for
a PR whose code changes lack a `pkg-descr` change fails in `request` before
anything builds. The `status` job writes with `GITHUB_TOKEN`
(`statuses: write`) and never checks out or runs PR code. Run names show the PR number, candidate,
production series or event.

### Lifecycle (`plugin-target.yml`)

Inputs: the entry inputs, `series`, `purpose` (`candidate`, `production`,
`promote`), `source_sha` (PR head, or `master` head for production) and `pr`.

| Job | Runs on | Does | Credentials |
| --- | --- | --- | --- |
| `prepare` | Ubuntu | Read environment and current Release, decide, prepare source | read-only |
| `build` | FreeBSD VM | `make package`; writes `build-record.json` | none |
| `qualify` | clean FreeBSD VM | Install from a local repository; check package; compare name, origin, version and ABI with the record | none |
| `publish` | Ubuntu | Versioned and current distribution Releases | distribution app |
| `sync-source` | Ubuntu | Push the prepared commit, tag it, create the source Release | sync app |
| `index` | `package-index.yml` | Refresh the series feed, then retention | signing key, distribution app, sync app |

- Candidates stop after `qualify`. Promotion skips `build` and `qualify`.
- `sync-source` and `index` both need only `publish`; a failed source sync
  does not block the feed.
- FreeBSD work runs in a disposable `vmactions/freebsd-vm` VM of the series'
  FreeBSD release; nothing is kept between runs.

### `prepare`

Outputs:

| Output | Content |
| --- | --- |
| `operation` | `build`, `promote` or `skip`, with `reason` |
| `freebsd_release`, `abi`, `tools_tag`, `core_commit`, `plugins_commit`, `upstream_package_commit` | Upstream environment |
| `version`, `previous_tag` | Next version; versioned tag current points to |
| `prepared_sha` | Prepared commit, in artifact `source-<component>-<series>-<run_id>` |

- Production skips when the release branch head equals the current record's
  `source_base`, the upstream package is unchanged and there is nothing to
  carry.
- Promotion fails when the candidate artifact is missing or its version is not
  newer than current.

The prepared commit starts from the release branch head (for a new series, the
previous series' release branch head) and adds:

1. `git merge upstream/stable/<series>`, when the series is new or upstream
   changed;
2. the carry: `git diff <Master-Commit>..<source_sha> -- <source_directory>`,
   applied with `git apply --3way`; nothing when empty;
3. one version commit with `Master-Commit: <source_sha>`.

A conflict fails the target, except one confined to the `PLUGIN_VERSION` and
`PLUGIN_REVISION` lines, which keeps the release side.

### Artifacts

| Artifact | Written by | Contents | Kept |
| --- | --- | --- | --- |
| `source-<component>-<series>-<run_id>` | `prepare` | Source bundle | 1 day |
| `build-<component>-<series>-<run_id>` | `build` | Archive, `build-record.json` | 1 day |
| `qualified-<component>-<series>-<source_sha>` | `qualify` | Archive, record, source bundle | 30 days |

Only `qualify` writes `qualified-`, so the artifact's existence means the
archive passed. Promotion looks it up by the merged PR head.

### Publication and source sync

- Versioned Release: created as a draft, assets uploaded, then published. A
  rerun deletes a leftover draft of the same tag; an already published
  identical tag completes as done.
- Current Release: under the target lock, advances only to a newer version;
  equal completes as done; older leaves it.
- HA Releases are pre-releases while `prerelease` is set.
- `sync-source` fast-forwards the release branch to the prepared commit (Git
  rejects the push if the branch moved), tags it `<source_tag>-<version>` and
  creates the source Release in `resolver-plugins/plugins`. A rerun whose
  branch already holds the commit only ensures the tag and Release.

### Concurrency

| Group | Scope |
| --- | --- |
| `plugin-<component>-<series>` | Production and promotion; newest pending request wins; a running publication is never cancelled |
| `candidate-<component>-<pr>` | Candidates per PR |
| `package-repository-index` | The whole index workflow |

### Index (`package-index.yml`)

Inputs: `series`, `abi`, `component`, `current_tag`, `source_tag`.

| Job | Runs on | Does |
| --- | --- | --- |
| `select` | Ubuntu | Read `channel.json` and each listed current Release plus the input one; stop if nothing changed |
| `stage` | FreeBSD VM | Download archives; `pkg repo` with the series' `pkg` and the signing key; add `resolver-plugins.pub` and `channel.json` |
| `publish` | Ubuntu | Replace `pkg/<abi>/<series>/latest/` on `gh-pages` in one commit |
| `readback` | Ubuntu | Compare the public feed with the staged files, retrying while Pages deploys |
| `retain` | Ubuntu | Delete the release two versions back |

Retention reads the previous versioned Release's `build-record.json`; its
`previous_tag` names the release to drop. It deletes that distribution
Release, its tag and its source Release, and keeps the source tag. It runs
only after a successful readback.

### Upstream discovery

| Value | Source |
| --- | --- |
| Active series | Highest final `X.Y` or `X.Y.Z` tag in `opnsense/core` whose matching `opnsense/tools` tag has `config/<series>/build.conf`; prerelease tags do not count |
| `freebsd_release` | `OS` in that `build.conf` |
| `abi` | `FreeBSD:<major>:amd64` |
| `core_commit`, `plugins_commit` | Heads of `stable/<series>` |
| `upstream_package_commit` | Last `upstream/stable/<series>` commit touching the plugin directory |
| VM package repository | `https://pkg.opnsense.org/${ABI}/<series>/latest`, per core's `OPNsense.conf.shadow.in` |
| PHP and Python | From the series' installed OPNsense core (`Mk/defaults.mk`); not passed |

Values are read with `git ls-remote`, `git fetch` and `gh api`, and an
unexpected format fails clearly. Builds name the target series explicitly, run in
the matching FreeBSD release, take dependencies from the series' signed
OPNsense repository and target amd64 only.

## Data formats

### `build-record.json`

Written once by `build`: `package`, `origin`, `version`, `series`, `abi`,
`freebsd_release`, `source_base` (release branch head used), `master_commit`,
`source_commit` (prepared), `control_commit` (workflow commit), `tools_tag`,
`core_commit`, `plugins_commit`, `upstream_package_commit`, `upstream_version`
(empty for `series`), `pkg_version`, `previous_tag`, `run_url`.

### `channel.json`

Written by the index:

```json
{"schema": 4, "series": "...", "package_abi": "...",
 "components": {"<component>": {"current_tag": "...", "version": "...", "packages": ["..."]}}}
```

The index reads `components.*.current_tag` (also present in schema 3) to know
which current Releases a feed serves, so the frozen `bind_runtime` entry stays
in the 26.1 and 26.7 feeds.

## Feed

```text
https://resolver-plugins.github.io/repository/pkg/${ABI}/<series>/latest
```

`${ABI}` stays literal in client configuration; `pkg` expands it, for example
to `FreeBSD:15:amd64` on OPNsense 26.7. A feed directory holds each plugin's
current archive for the series, the signed catalogue, `resolver-plugins.pub`
and `channel.json`. A plugin not yet published for a series is absent.

Frozen, never written by the current workflows:

- the Pages feed `pkg/${ABI}/latest` and the Release feeds `pkg-26.1` and
  `pkg-26.7`;
- runtime Releases `pkg-<series>-bind-runtime` and their versioned Releases;
  runtime packages (`bind920`, `bind-tools`) stay in the feeds that list them,
  outside retention;
- older fingerprinted versioned Releases.

## Releases

| Plugin | Current Release | Versioned Release | Source Release and tag |
| --- | --- | --- | --- |
| `os-bind-rp` | `pkg-<series>-bind-plugin` | `pkg-<series>-bind-plugin-<version>` | `os-bind-rp-<series>-<version>` |
| `os-ha-dhcp-interface` | `pkg-ha-dhcp-interface-<series>` | `pkg-ha-dhcp-interface-<series>-<version>` | `ha-dhcp-interface-<series>-<version>` |

- Distribution Releases are in `resolver-plugins/repository`; source Releases
  in `resolver-plugins/plugins`, tagging the prepared commit on the release
  branch.
- The current Release holds only the newest archive and its record.
- A versioned Release is created once; publishing a different source under an
  existing tag fails.
- Per plugin and series, retention keeps the current Release, the newest
  versioned Release and one before it.

## Credentials

Actions settings of `resolver-plugins/plugins`:

| Name | Kind | Holds |
| --- | --- | --- |
| `RP_PKG_SIGNING_KEY` | secret | Base64-encoded private package-signing key |
| `RP_DISTRIBUTION_APP_ID` | variable | Distribution GitHub App ID |
| `RP_DISTRIBUTION_APP_PRIVATE_KEY` | secret | Distribution App private key |
| `RP_SYNC_APP_CLIENT_ID` | variable | Sync GitHub App client ID |
| `RP_SYNC_APP_PRIVATE_KEY` | secret | Sync App private key |

| Credential | Scope | Used for |
| --- | --- | --- |
| Signing key | Decoded only in the index `stage` VM, mode 0600 temporary file, removed after `pkg repo` | Signing catalogues |
| Distribution App | Installed only on `resolver-plugins/repository`, `Contents: write` | Releases, `gh-pages` feed, retention |
| Sync App | Installed only on `resolver-plugins/plugins`, `Contents: write` | Release branches, source tags and Releases, retention |

No personal access token is stored. Candidates receive no secrets. The public
key is committed at `docs/package-repository/resolver-plugins.pub`; the index
copies it into each feed.

## Client installation

`scripts/install-repository.sh`:

1. reads the series (`opnsense-version -a`) and ABI (`pkg config ABI`);
2. downloads `resolver-plugins.pub` from that series' feed and checks its
   SHA-256 fingerprint
   `bd89d6f91807c71f8a744532c9ce2f97e9590f8858ac779bfb2f23c10804e07e`;
3. writes `/usr/local/etc/pkg/repos/resolver-plugins.conf` and the key in
   `/usr/local/etc/pkg/keys/`;
4. runs `pkg update -r resolver-plugins`. It installs no packages.

## Series upgrade

| Role | `os-bind-rp` | `os-ha-dhcp-interface` |
| --- | --- | --- |
| Upgrade hook | `/usr/local/etc/rc.syshook.d/upgrade/50-bind-rp-repository` | `/usr/local/etc/rc.syshook.d/upgrade/50-dhcp-interface-ha-repository` |
| Repository provider | `/usr/local/opnsense/scripts/firmware/repos/ResolverPlugins.sh` | `/usr/local/opnsense/scripts/firmware/repos/ResolverPluginsHA.sh` |
| Start hook | `/usr/local/etc/rc.syshook.d/start/50-bind-rp-repository` | `/usr/local/etc/rc.syshook.d/start/50-dhcp-interface-ha-repository` |
| Reconcile worker | `/usr/local/opnsense/scripts/OPNsense/Bind/bindRepositoryReconcile.sh` | `/usr/local/opnsense/scripts/dhcp_interface_ha/repository_reconcile.sh` |
| Pending marker | `/var/db/os-bind-rp/repository-reconcile.pending` | `/var/db/os-dhcp-interface-ha/repository-reconcile.pending` |
| Log | `/var/log/os-bind-rp-reconcile.log` | `/var/log/os-dhcp-interface-ha-reconcile.log` |

1. **Upgrade hook**, during the firmware upgrade: creates the pending marker;
   no network or package work.
2. **Repository provider**, during firmware initialization: reads the new
   series with `opnsense-version -a` and points a recognized managed
   `resolver-plugins.conf` at the new feed, keeping mode, owner and key; sets
   the marker when it changes the file.
3. **Start hook**, after boot: runs the worker in the background, up to three
   attempts 30 seconds apart. The worker runs the provider, refreshes with
   `pkg-static update -f -r resolver-plugins`, accepts only its own plugin for
   the new series (BIND: `<series>_<n>` or `<series>.<upstream>_<n>`; HA:
   `<series>_<n>`) with the expected origin and ABI, dry-runs and refuses plans
   that remove or downgrade packages, installs and removes the marker. The
   BIND worker also refuses while `os-bind` is installed and, if `bind920`
   changed, reloads the `OPNsense/Bind` templates and restarts BIND.

A failure leaves the marker, logs one line and blocks neither the upgrade nor
the boot; the next boot retries.

Managed shapes, which the providers may rewrite: the legacy Release URLs
(`.../releases/download/pkg-26.1` and `pkg-26.7`), `.../pkg/${ABI}/latest` and
a previous `.../pkg/${ABI}/<series>/latest`, each with the Resolver Plugins
key, `mirror_type: "none"` and optionally `priority: 20`. Anything else (custom
URL, other key, disabled, other mirror mode, symlink, missing or non-regular
file) is left unchanged with a `manual migration` warning. Both providers
write identical content.

## Expected outcomes

The behavior any CI change must preserve.

| Situation | Required result |
| --- | --- |
| Repeated events for the same package, source and target | One expensive run; repeats stop before a VM starts |
| Several merges queue up | The latest source is built; no backlog of obsolete builds |
| Package PR opened or updated | Merge blocked for that head until its requested build succeeds; no build starts automatically |
| Requested build for an older head finishes after a push | Its status stays on the older head; the new head stays blocked |
| CI or documentation-only PR | Status passes without a build |
| PR changes only unpackaged plugin files, such as tests | Status "Not applicable"; merging publishes nothing |
| PR changes plugin code without its `pkg-descr` | Status fails with the changelog message; a candidate request fails before building |
| PR with a successful candidate merges | That archive and qualification are promoted |
| Candidate expired or overtaken at promotion | Promotion fails with a reason; no automatic rebuild |
| New final Community series becomes usable | Plugins build and publish without CI edits or a review gate |
| Upstream BIND change without a version bump | BIND builds with `<n>` + 1 |
| Upstream BIND version changes, for example `1.34` → `1.35` | BIND builds `<series>.1.35_1` |
| First BIND build after `<series>_<n>` versions | `<series>.<upstream>_1`, newer than the last `<series>_<n>` |
| HA build | `<series>_<n>` |
| Environment, CI, documentation or other-plugin change only | No build, probe or requalification |
| A job fails | It stays failed; dependents stop; no recovery job starts |
| Maintainer reruns a failed publication | The rerun completes it |
| Index or readback fails | Publication stands; retention stops |
| An older run reaches publication after a newer version | Current stays on the newer version |
| A rerun source sync meets a newer release or branch change | Newer metadata and pending work stay intact |
| A series is retired | No new automatic builds; current and rollback feeds stay |
| A client crosses a series or FreeBSD boundary | Feed handover and plugin upgrade happen automatically, for each plugin independently |
| OPNsense's BIND meets the plugin's minimum | `os-bind-rp` installs with it; no runtime package is required |
