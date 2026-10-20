# CI rebuild specification

Status: requirements, rewritten 2026-10-19. Supersedes earlier CI plans and the
2026-10-15 specification where they conflict. The replacement workflows are not
implemented. The [design](ci-rebuild-design.md) describes how these requirements
are met; the [upstream research](ci-upstream-research.md) records evidence.

## Problem

Pushes and PR merges have produced overlapping pipelines with indistinguishable
names, unnecessary runner costs and periods when GitHub blocked Actions. The
current CI holds thousands of lines of orchestration, redundant checks, retired
pathways and custom integrity machinery that make it hard to understand.

The replacement must deliver compatible OPNsense plugin packages automatically
and keep the installed-client upgrade path working: when OPNsense crosses a
series or FreeBSD base boundary, clients must move to the right signed feed and
upgrade the installed plugin without manual repository repair.

## Solution

A direct flow: **discover upstream → select eligible work → build or promote →
publish → index the feed and sync source**. Use upstream build tools, GitHub
Actions features, GitHub Releases, Git and `pkg`, with as little glue as possible.

Each plugin owns its build and publication lifecycle. One separate index workflow
serves already published packages through the existing signed feed. A PR build
requested by the maintainer produces an unsigned production candidate; merging
requires it, and merging promotes it without rebuilding.

Core determines the newest final Community series and its upstream inputs. New
series and upstream BIND plugin changes are delivered automatically.

## Terms

- **Series:** an OPNsense release line such as `26.7`, taken from core.
- **ABI:** FreeBSD's package ABI such as `FreeBSD:15:amd64`; distinct from series.
- **Target:** one series and its ABI for one package.
- **Candidate:** an unsigned, qualified build for one PR head and target.
- **Promotion:** signing and publishing an existing candidate without rebuilding.
- **Versioned Release:** the distribution Release for one package version.
- **Current Release:** the per-package, per-series Release that clients' feed
  selection follows (the existing `pkg-<series>-bind-plugin` style tags).
- **Pinned ref:** an upstream commit selected for one run and recorded.
- **Build record:** the one readable JSON record that travels with an archive.

## Packages

| Package | Name | Origin | Source |
| --- | --- | --- | --- |
| BIND plugin | `os-bind-rp` | `opnsense/os-bind-rp` | `dns/bind` on `release/bind-rp/<series>` |
| HA DHCP interface plugin | `os-dhcp-interface-ha` | `opnsense/os-dhcp-interface-ha` | `net/dhcp-interface-ha` on `release/dhcp-interface-ha/<series>` |

The CI builds these two plugins. It no longer builds BIND runtimes; see
[BIND runtime](#bind-runtime).

## User stories

1. As a maintainer, I want duplicate expensive jobs blocked before they start,
   so repeated events and merges cannot multiply runner costs.
2. As a maintainer, I want each run named by package, target and purpose, so I
   can tell legitimate work from repeats.
3. As a maintainer, I want short workflows using built-in features, so CI stays
   understandable without another build framework.
4. As a maintainer, I want agents to stop before writing a CI script over 30
   lines, so added complexity is discussed first.
5. As a maintainer, I want series, environments and upstream commits discovered
   upstream, so a new OPNsense release needs no CI edit.
6. As a maintainer, I want release branches and their ancestry preserved, so
   upstream changes and the fork overlay stay traceable.
7. As a plugin user, I want new OPNsense series supported automatically, so
   packages are ready when I upgrade.
8. As a BIND user, I want upstream plugin changes incorporated automatically,
   without a review gate or an upstream version bump.
9. As a maintainer, I want environment changes alone not to rebuild packages.
10. As a maintainer, I want a PR's requested build promoted at merge, so the
    expensive build and qualification happen once.
11. As a maintainer, I want candidate builds kept away from signing credentials,
    so requesting a build does not authorize publication.
12. As a maintainer, I want every new PR head blocked from merging until its
    affected package has been built and qualified for that head.
13. As a package user, I want stable package names and origins, so dependency
    resolution and install commands keep working.
14. As a BIND user, I want the series-and-revision version policy kept, so the
    next series always offers a newer plugin.
15. As a package user, I want the feed laid out by ABI and series, so my
    machine gets packages for its actual base and series.
16. As an upgrading user of either plugin, I want firmware upgrades to move my
    feed and upgrade my plugin, independently of the other plugin.
17. As a package user, I want newer compatible dependencies kept, never
    downgraded.
18. As a maintainer, I want source branches, Releases and the signed feed to
    agree with what was actually built.
19. As a package user, I want only complete signed releases visible, and older
    delayed runs unable to replace current.
20. As a maintainer, I want failed jobs to stay failed and stop dependent work,
    with no recovery jobs, while GitHub's rerun still finishes the work.
21. As a plugin user, I want the current release and one rollback per series.
22. As a maintainer, I want automatic builds to follow upstream Community
    support, so retired series stop consuming runners.
23. As a maintainer, I want normal logs and one readable build record, so
    failures can be diagnosed without chains of custom manifests.

## Requirements

### Simplicity and limits

- **Workflow first.** Express selection and ordering with workflow features:
  triggers and `paths`, `if:`, `needs`, matrices, `concurrency`, reusable
  workflows, artifacts and job outputs. Use `git`, `gh`, `jq`, `make` and `pkg`
  for the operations themselves. Write custom code only for what these cannot
  express clearly.
- **Built from scratch.** The new CI reuses no code from the old CI (the old
  workflows and `.github/ci/**`). They stay in the tree only as reference until
  cutover deletes them.
- **Script files are the last resort.** A CI script file (any file a workflow
  runs other than the workflow itself) needs a written justification before it
  is created: the operation, each direct alternative (a workflow feature, an
  existing action, one native command, a short inline step) and why each fails.
  A reviewer rejects the justification while a simpler alternative remains, over
  as many iterations as it takes, and the maintainer approves each script file.
- Aim for a few hundred lines of custom CI glue in total, counting workflow
  scripts and helpers together. If the design grows toward thousands, simplify
  responsibilities.
- **Hard stop: 30 physical lines.** Before creating or growing any CI script or
  inline workflow script beyond 30 physical lines (comments and blank lines
  count), stop, explain the operation and why built-in commands or a shorter
  script cannot do it, and wait for the maintainer. Splitting one operation
  across steps or files, or compressing code, to stay under the limit is
  prohibited. Necessary functionality is not an exception.
- Use POSIX shell for base-system hooks and thin orchestration. Python is
  approved for an operation that cannot be done in a few lines of shell, where
  it is guaranteed or provisioned, after the same exploration and written
  justification as a script file.
- Run on the Ubuntu runner unless the work actually needs FreeBSD. Start a
  FreeBSD VM only for work that needs one; consecutive steps of one job share
  it. Installation qualification uses its own clean VM.
- Validate external inputs (event payloads, upstream data, earlier Releases)
  once where they enter. Artifacts handed between jobs of the same run are
  trusted.
- Carry forward required behavior and known compatibility repairs. Retire the
  old controllers, routers, source-review and bootstrap pathways, dispatch
  fan-out, sidecar formats and recovery machinery instead of porting them.

### Package identity

- Names and origins in the [package table](#packages) are fixed across series.
  Archives are named `<name>-<version>.pkg`; series, ABI and diagnostics stay
  out of the name. Renaming requires a maintainer decision.
- The table describes expected identities; it is not a registry. The native
  plugin builder supplies the actual name, origin and version. Workflows contain
  no package-name variables, branches, case lists or lookup tables. A package
  joins the shared workflow without a package-specific branch in it.
- Operations needed by both plugins live in reusable workflows called with
  explicit inputs by each plugin's own entry workflow, not copied into each entry
  and not behind a dispatcher.
- Fork-specific branch, channel and version policy belongs with each plugin's
  entry workflow on `master`. Keep the existing public branch and channel names.
- Keep `PLUGIN_NAME=bind-rp`, `PLUGIN_CONFLICTS=bind` (replacing the official
  `os-bind`), the minimum OPNsense `26.1.11_10` and the minimum BIND declared by
  `PLUGIN_DEPEND_FORMULA`. Keep HA's `wan-ha-dhcp` replacement conflict.
- The two plugins have independent lifecycles. A plugin pipeline never
  dispatches, waits for, rebuilds or requires publication by the other.

### Upstream discovery

- **No hard-coded series, fallback series, environment maps or upstream commit
  hashes** in workflows, helpers, checked-in configuration or approvals. Resolve
  them upstream per run and record what was used in the build record. Research
  notes and generated release metadata may contain concrete values.
- Start from core's final release tags and edition and series metadata. Follow
  the selected release to its matching plugins framework, tools configuration,
  Ports inputs and signed OPNsense package repository.
- The matching tools release supplies the FreeBSD base (its `OS` setting), make
  configuration, language and SSL defaults, port options and origin selections.
  Build in the actual matching FreeBSD environment; relabeling an archive's ABI
  is not a substitute.
- Name the target explicitly when invoking upstream tools, so host matching or
  latest-configuration defaults cannot pick another one. Dependencies come from
  the target's signed OPNsense repository. Architecture stays amd64.
- Build and index with the `pkg` that upstream ships for the series' latest
  release, taken from the series' signed OPNsense repository, and record its
  version. OPNsense has republished `pkg` builds under an unchanged version, so
  `pkg` and other upstream packages are identified by version, never by archive
  hash. Clients are expected to run the series' latest release; there is no
  checksum rewrite for older readers.
- An unavailable input or unfamiliar upstream format fails that package and
  target clearly; no fallback value is substituted.

#### Branches

| Role | Branch |
| --- | --- |
| Upstream core and plugins | `master` (development), `stable/<series>` (releases) |
| Fork plugin development and CI | `master` |
| Fork BIND releases | `release/bind-rp/<series>` |
| Fork HA releases | `release/dhcp-interface-ha/<series>` |

- Plugins are developed on `master`, as upstream does. Each release branch is
  written only by the pipeline: it carries the plugin directory's changes from
  `master`, merges upstream `stable/<series>` changes and sets the version.
  Builds use a pinned commit. Find BIND's incorporated upstream baseline through
  Git ancestry.
- A merged `master` change to a plugin's directory is carried automatically into
  the active series' release branch, then built and published. The carry is the
  directory's diff since the last carried `master` commit, which the release
  branch records; a conflict fails that target.
- Keeping `master` merged with upstream `master` is the maintainer's work, not
  the pipeline's.
- Before development moves to `master`, its plugin directories are brought up to
  date with the active release branches once.
- Preparing a new series creates its missing release branch automatically from
  the previous series' release branch merged with upstream `stable/<series>`.
- Never rewrite release history, and never open PRs against official OPNsense
  repositories.
- Before cutover, reconcile each existing plugin branch once with its actual
  published source, keeping fork features, upgrade hooks and pending reviewed
  changes, without rewriting history. Normal builds do not merge published
  source back into the branch.

### Active series

- Automatic plugin builds target only the newest final Community series, in line
  with upstream's security-support policy. A newer final series retires its
  predecessor from automatic builds.
- Order series numerically and use edition metadata. Prereleases, Business
  Edition, historical branches and later patch tags of older series do not add
  targets.
- A new series builds when its source, configuration, signed feed and builder
  are available.
- Retired series keep their feeds, current Release and rollback for installed
  clients, and are locked: no automatic, manual or PR builds target them.
- Upstream decides retirement; there is no list or flag in this project.

### Delivery events

Each plugin has one thin entry workflow and one production route. Series fan out
through a matrix, not by dispatching other workflows.

| Event | Work |
| --- | --- |
| New final Community series becomes usable | Prepare, build and publish each plugin for it |
| Upstream BIND plugin change on the active series | Incorporate it; build and publish BIND |
| Daily or manual discovery finds no change | Nothing |
| PR to `master` opened or updated | Cheap status only; a PR changing a plugin waits for a successful requested build of that head |
| Maintainer requests a PR build | Unsigned candidate of the active series with that head's changes carried |
| PR to `master` changing a plugin merged | Promote the candidate built for the merged head |
| Maintainer requests a production build | Build, qualify and publish the active release branch with `master`'s uncarried changes |
| Environment, ABI or dependency change within a series | Nothing; the qualified archive stays in use |
| CI, documentation or the other plugin's changes | Nothing |
| Successful publication | Index refresh |

- Every upstream BIND plugin change on the active series is incorporated, even
  without an upstream version bump or when it touches only documentation.
  Unrelated upstream plugin changes do not select BIND. Automatic delivery has
  no review gate.
- An integration conflict or incompatible input fails only that package and
  target, without dropping fork features or bypassing package checks.
- Cheap selection happens before fetching the build environment or starting a
  VM.
- A qualified archive stays valid when the toolchain, dependencies or FreeBSD
  base change within its series: no dry run, requalification or rebuild. It is
  never relabeled for another series. A new series gets its own build.

### Duplicate work is prohibited

This is a hard requirement, including manual reruns and agent-written automation.
It is met structurally, not with a duplicate scanner, history inventory or
reconciliation framework.

- One package change has one production route. A merge cannot trigger repeated
  builds through push, PR-closed, workflow completion or self-dispatch. CI entry
  points live only on `master`; release branches carry no workflow callers.
  Source synchronization never triggers a rebuild of its own result.
- Work is identified by package, source commit, target and recorded package
  identity. A merge commit or run number is not a package change.
- Native concurrency per package and target covers the whole lifecycle through
  publication. Superseded automatic requests coalesce before expensive work;
  queueing every duplicate is not enough. Distinct requested candidates are kept,
  active publication is never cancelled, and different packages and targets stay
  independent.
- Each lookup is targeted: promotion reads the merged head's candidate; jobs
  read their predecessors' artifacts; discovery compares the followed upstream
  path with the target's current build record.
- Index refresh runs only after an actual publication.

### Failed jobs stay failed

- **There are no recovery jobs. A failed job is a failed job.** It stops its
  dependent path; nothing starts to repair it. No partial-build saves,
  checkpoint searches, stage reconstruction, repair dispatches or salvage of a
  failed job's pieces.
- A documented negative result inside a job (for example, "nothing to publish")
  is a decision, not a failure. Operational errors fail normally with the
  command's own diagnostic. No custom retry loops, error classification,
  speculative fallbacks or recovery state.
- **GitHub's rerun must work.** Every step is safe to rerun: a rerun of a failed
  publication finishes it from the job's inputs (for example, replacing a
  leftover draft) instead of failing on what the first attempt left behind.
- Native timeouts and retry options may absorb transient network errors within a
  step.

### PR builds and promotion

- Opening or updating a package PR runs only a cheap status job; it never starts
  a build. Each plugin reports its own required status, `not applicable` (pass)
  when the PR does not change that plugin's paths, `pending` otherwise.
- The maintainer requests a build for a PR. It runs trusted CI from `master`,
  produces an unsigned candidate of the active series with the final name and
  version, qualifies it, and then sets the status to success on the head it
  built. A build for an older head never satisfies a newer one.
- `master` requires that status on the current head and requires PRs to be up to
  date with `master`, so an update needs a new build. Release branches accept
  pushes only from the pipeline. These rules are configured at cutover.
- The status writer is a small trusted job, separate from package-code execution
  and from signing and publication credentials. It does not discover affected
  packages; its caller supplies the context.
- At merge, promotion finds the candidate by the merged PR head and publishes its
  archive and qualification. No source comparison re-decides whether the build
  is current; the status requirement already enforced that.
- **Promotion fails clearly, and the maintainer requests a production build,
  when** the candidate's artifacts have expired or are missing, or its version is
  no longer newer than the current Release (another publication overtook it).
  Promotion never substitutes another head's candidate and never renames or
  repackages an archive.
- The final version is fixed before building.
- Candidates replace the old development-preview packages. An
  `os-bind-rp-devel` archive is a different identity and cannot be promoted.
  Candidates never enter production feeds.

### Versions

- Use the upstream framework's `PLUGIN_VERSION` and `PLUGIN_REVISION`.
- BIND keeps the `<series>_<revision>` policy: series from core, revision
  advanced for new package content, including upstream changes without a version
  bump. Record upstream BIND's own version separately. Never fall back to `1.x`
  from an upstream Makefile.
- HA follows the same `<series>_<revision>` policy; `26.7_1` succeeds the
  published `0.2_45`.
- New client-visible content needs a version or revision increase.
- Within one target every version is `<series>_<revision>`, so ordering is by
  revision number; this matches `pkg`'s ordering for that form.

### Series upgrade

This is a core requirement. The feed layout, package naming and versions and the
installed hooks together carry clients across a series or FreeBSD boundary, and
must be designed and implemented as one architecture.

- Feed: `https://resolver-plugins.github.io/repository/pkg/${ABI}/<series>/latest`.
  `${ABI}` stays literal in client configuration so `pkg` expands it for the
  running base. Follow upstream core's repository template and plugin ABI
  metadata.
- The increasing series version, the feed handover and the ABI and series path
  work together so the next series always offers a newer plugin.
- Keep the BIND upgrade sequence:
  1. The pre-upgrade hook marks reconciliation pending and returns, with no
     network or package work inside the firmware transaction.
  2. During firmware initialization, core calls the repository provider. It
     reads the target series with `opnsense-version -a` and points only
     recognized managed configurations at the target feed, keeping custom
     configurations, file ownership and mode, and public-key trust.
  3. After boot, the start hook launches reconciliation. `pkg-static` refreshes
     the signed feed, validates identity, ABI and series, checks the transaction
     plan and installs the target plugin. Pending clears only on success; a
     failure stays pending and logged without blocking boot or firmware. Keep the
     required BIND template and service refresh.
- HA needs its own working series and base upgrade path, independent of BIND,
  with distinct filenames and pending state so the two plugins install together
  without conflicts. This is part of the rebuild.
- An old series' migration-aware package must be installed before the upgrade;
  publishing a target package cannot add missing hooks retroactively.
- Native dependency handling keeps newer compatible installed dependencies,
  including OPNsense's BIND, and upgrades or refuses those below the declared
  minimum; never force a downgrade.
- The reconcile worker relies on native dependency resolution for BIND instead
  of requiring this project's runtime packages in the target feed.

### Signing and publication

- Build jobs and candidates receive no signing keys or write credentials.
  Separate trusted jobs sign and publish with credentials scoped to their
  destination and never run package-source code. Keep the existing secrets
  (`RP_PKG_SIGNING_KEY`, the distribution and sync GitHub Apps), Release
  hosting, trust key and installation contract.
- Before publication, qualify the actual archive once in a clean matching
  environment: identity, version, ABI, dependency and conflict behavior,
  integrity and file checksums. Reuse that qualification for every destination.
- Create each versioned Release as a draft, upload every asset, then publish it,
  so interrupted uploads stay unpublished.
- Under the package and series lock, compare the candidate's version with the
  current Release: newer publishes and advances current; equal with the same
  source completes as already published; older leaves current unchanged.
  Rollback is an explicit maintainer action.
- Finite job timeouts; bounded retries for transient network errors through
  native options. Keep normal Actions logs, short-lived extra diagnostics on
  failure and short-lived artifacts without redundant copies.

### Source and distribution stay in step

- Direction: **build result → package Releases → source and signed feed**.
  Source preparation and version updates are pipeline work, not manual steps.
- Build once; publish that archive and serve that archive. Name, version and
  target agree across archive, manifest, catalogue and Release metadata.
  Compiled output never enters source history.
- Source-release tags point at the built source snapshot; distribution Releases
  record that source reference.
- Sync updates only the owning package's release branch, keeping ancestry and the
  actual source reference. Before updating, it reads the current published record
  and uses a Git update that rejects an unexpected branch head. A rerun never
  overwrites newer released metadata or pending source work.
- One readable build record holds package and version, series, FreeBSD base and
  ABI, source and CI refs, upstream refs and versions, the `pkg` version,
  the previous versioned Release and the run URL. Only qualified archives are
  published, so publication itself shows qualification. Normal integrity stays in the packages and
  catalogues; sealed manifest chains, write-once histories and byte-identical
  rebuild policies are not requirements.

### Index and retention

- Only the index workflow writes the feed. It holds the shared feed lock and
  rereads the current published records after taking it, so a delayed event
  cannot restore older selections. It tolerates absent unrelated packages and
  never builds one.
- It prepares the complete signed catalogues locally and publishes them in one
  Git update; failed preparation leaves the working feed in place. It then
  verifies availability and signatures through the public feed.
- Package publication never waits for the index. A failed index or readback
  stays failed, stops retention and neither rebuilds nor rolls back packages.
- Retention keeps, per plugin and series, the current plus one previous
  production release, found through the build record's `previous_tag`. It runs
  only after a successful public readback, removes the older release's assets in
  both repositories consistently, never rewrites source history and never touches
  another package or series. Failed builds and candidates take no slot.
- Retired series keep working feeds. The legacy feeds that old clients migrate
  from (`pkg/${ABI}/latest` on Pages and the `pkg-<series>` Release feeds) stay
  frozen; nothing in the new CI writes or prunes them.

### BIND runtime

Decided 2026-10-19: the CI no longer builds `bind920` or `bind-tools`. They were
bundled to avoid a regression in OPNsense's BIND, and current OPNsense ships a
fixed, newer BIND.

- `os-bind-rp` declares its minimum BIND (`PLUGIN_DEPEND_FORMULA`). `pkg`
  installs any provider meeting it: OPNsense's BIND, or the existing runtime
  packages in this project's feed where OPNsense's is older.
- Existing published runtime Releases and their feed entries stay as they are
  for the series that have them, outside plugin retention.
- If no available provider meets the minimum, installation fails natively. CI
  never compiles a private dependency or falls back to building one.

### Cutover

- Retire the old production routes before enabling the replacements; never run
  both against the same change. Keep the working signed feed and old clients'
  migration needs intact throughout.
- Remove retired workflows, dispatch paths, helpers, input formats and stale
  documentation, including the runtime workflows and approval files. Keep only
  helpers with a live caller. A live client reader or a signed release still
  needed by clients is removed only after its last consumer is identified.
- Update maintainer guides to the final implementation. Commit CI and
  documentation changes with `[skip ci]`; never dispatch Actions to validate them.
- Done means: each eligible change has one production route, duplicate expensive
  work is blocked, promotion reuses the candidate, both repositories match the
  published packages, and retained feeds and client upgrades keep working.

## Testing decisions

**No test suites in CI, permanently.** No workflow, job, reusable workflow,
helper or release branch runs CI-helper, workflow, unit, regression, behavior or
lint suites, directly or indirectly. Remove existing ones at cutover.

**No tests for this work.** Do not write or run tests, lint, validators or
workflows to check CI or documentation changes. Static review and `git diff
--check` are allowed.

The production checks are package qualification before publication and public
feed readback after. Each runs once and its result is reused.

### Acceptance checklist

| Situation | Required result |
| --- | --- |
| Repeated events for the same package, source and target | One expensive run; repeats stop before a VM starts |
| Several merges queue up | The latest source is built; no backlog of obsolete builds |
| Package PR opened or updated | Merge blocked for that head until its requested build succeeds; no build starts automatically |
| Requested build for an older head finishes after a push | Its status stays on the older head; the new head stays blocked |
| CI or documentation-only PR | Status passes without a build |
| PR with a successful candidate merges | That archive and qualification are promoted |
| Candidate expired or overtaken at promotion | Promotion fails with a reason; no automatic rebuild |
| New final Community series becomes usable | Plugins build and publish without CI edits or a review gate |
| Upstream BIND change without a version bump | BIND builds with an advanced revision |
| Environment, CI, documentation or other-plugin change only | No build, probe or requalification |
| A job fails | It stays failed; dependents stop; no recovery job starts |
| Maintainer reruns a failed publication | The rerun completes it |
| Index or readback fails | Publication stands; retention stops |
| An older run reaches publication after a newer version | Current stays on the newer version |
| A rerun source sync meets a newer release or branch change | Newer metadata and pending work stay intact |
| A series is retired | No new automatic builds; current and rollback feeds stay |
| A client crosses a series or FreeBSD boundary | Feed handover and plugin upgrade happen automatically, for each plugin independently |
| OPNsense's BIND meets the plugin's minimum | `os-bind-rp` installs with it; no runtime package is required |

## Out of scope

- New plugin features beyond the HA series-upgrade work; unrelated upstream
  plugin changes; package or origin renames.
- Building BIND runtimes; Business Edition targets; new architectures;
  unrequested older-series HA backports.
- New signing keys, credentials, hosting or client installation contracts.
- Custom CI frameworks, per-file hashing, schedulers or transaction engines.
- Running the cutover, pushing branches or publishing packages without a
  separate explicit request.

## Decisions

- Build the new CI from scratch and admit script files only after every direct
  alternative is shown to fail, over adapting the old helpers, because the old
  CI grew into thousands of lines of scripts duplicating workflow features
  (2026-10-20).
- Promotion of an expired or overtaken candidate fails and the maintainer
  requests a new build, over in-pipeline version checks and Release fallbacks,
  because it removes a whole decision path (2026-10-19).
- Retire the BIND runtime builds over keeping them, because OPNsense now ships a
  fixed, newer BIND and the plugin's declared minimum covers the gap (2026-10-19).
- The BIND worker relies on native dependency resolution, over carrying runtime
  packages into new feeds for older workers, because OPNsense's BIND satisfies
  the minimum; clients pick up the new worker through normal plugin updates
  before crossing (2026-10-19).
- HA adopts `<series>_<revision>`, over keeping `0.2_n`, because series upgrade
  depends on the version naming its series (2026-10-19).
- Retention finds the previous release through a recorded `previous_tag`, over
  scanning Releases (2026-10-19).
- PR builds are requested manually per head, with up-to-date branches required,
  over automatic PR builds, because builds are expensive (2026-10-16).
- A qualified archive stays valid across environment changes within its series,
  over a reuse-time dry run (2026-10-17).
- Build and index with the series' latest-release `pkg`, over the first
  release's `pkg` or a checksum rewrite, because upstream defines it per release
  and clients follow the latest release (2026-10-19).
- Retire the fresh-install script in favor of `install-repository.sh` plus
  `pkg install`, because its purpose was the runtime BIND update (2026-10-19).
- Develop plugins on `master` and carry merged changes into the active release
  branch automatically, over PRs to release branches, matching upstream's
  `master` and `stable/<series>` model (2026-10-19).
- Create a new series' release branch from the previous one merged with upstream
  `stable/<series>`, over branching from `master`, because it keeps ancestry and
  every published fix (2026-10-19).
- Retired series are locked and upstream decides retirement, over allowing
  manual builds for them (2026-10-19).

## Known gaps

- Preserve BIND upgrade features from published release sources; the plugin copy
  on `master` lacks the installed files, and branch heads can differ from
  published sources.
- Unfamiliar future upstream formats or unavailable builders fail the affected
  target clearly; discovery cannot make incompatible inputs compatible.

References: [upstream support policy](https://github.com/opnsense/core/blob/master/SECURITY.md),
[upstream branch and configuration selection](https://github.com/opnsense/tools/blob/master/Makefile),
[core repository convention](https://github.com/opnsense/core/blob/stable/26.7/src/etc/pkg/repos/OPNsense.conf.shadow.in),
[plugin framework](https://github.com/opnsense/plugins/blob/stable/26.7/Mk/plugins.mk),
[BIND upgrade history](https://github.com/resolver-plugins/plugins/commit/f58e9978d5ab4e1b7d32c9af3a3e1629b5f92d40),
[Actions artifacts](https://docs.github.com/en/actions/tutorials/store-and-share-data).
