# How the fork works, and why

This document explains the design of the fork: how it follows upstream, how
packages are built and published, and why the CI is shaped as it is. Exact
names and formats are in the [reference](reference.md).

## Why a fork

Resolver Plugins develops BIND features and the HA DHCP Interface plugin
outside the official OPNsense plugin release process, while still following
upstream OPNsense plugin updates. Upstream OPNsense is the compatibility
baseline: fork changes stay small, reviewable and confined to the two plugin
directories, and unrelated upstream plugin changes are not taken.

The fork publishes its own packages from its own signed feed. It never opens
PRs or issues against the official OPNsense repositories.

## Two independent plugins

`os-bind-rp` and `os-ha-dhcp-interface` share the feed, the signing key and
the GitHub Apps, and nothing else. Each has its own entry workflow, release
branches, version counter, Releases and series-upgrade hooks. A pipeline for
one never dispatches, waits for, rebuilds or requires publication by the
other, so a problem in one plugin cannot hold up the other. Both can be
installed on one system because their hooks use distinct file names and
pending state.

The policy that distinguishes them lives only in each entry workflow's inputs.
The shared workflows contain no package names, so adding a third plugin means
adding an entry workflow, not branching the shared ones.

## Development and release branches

Development happens on `master`, as upstream does. Release branches are
build sources, written only by the pipeline, one per plugin and series.

When a change to a plugin directory merges, the pipeline carries it onto the
active series' release branch. The carry is the directory's diff since the
last carried `master` commit, which the branch records in a `Master-Commit`
trailer. A diff, rather than merging `master` into the release branch, is
used because the two follow different upstream lines: `master` tracks
upstream `master`, while a release branch tracks `stable/<series>`. Because
the diff compares trees, a squash-merged PR carries cleanly after its
candidate was built.

A run publishes before it pushes, so a release branch holds only published
versions. Release history is never rewritten.

Keeping `master` merged with upstream `master` is maintainer work outside the
pipeline.

## Versions and changelogs

A version starts with its series, so a newer series always offers a newer
package. `os-bind-rp` versions also name the upstream BIND plugin version they
are built on, `<series>.<upstream>_<n>`, so users can see which upstream
release they run. The upstream version keeps its dots because `pkg` compares
each dotted part as a number: `1.9`, `1.34` and `2.0` sort correctly, which
they would not as `19`, `134` and `20`. The local revision `<n>` starts at 1
for each upstream version and counts every other build. HA has no upstream
and stays `<series>_<n>`.

The fork's BIND changelog entries live in their own block above upstream's
changelog heading, grouped by the upstream version they apply to. Upstream
adds its next section right under its own heading; keeping the fork's entries
apart lets the automatic upstream merge apply cleanly.

## Following upstream

### Series

The active series is the newest final Community series that upstream has
fully published. Only it builds, in line with upstream's security-support
policy. When upstream publishes a newer series, the next daily discovery
builds both plugins for it with no CI edit: it creates the new release branch
from the previous series' branch merged with upstream `stable/<series>`, which
keeps ancestry and every published fix. The previous series is then retired:
it is locked against all builds, but keeps its feed, current Release and one
rollback for installed clients. Upstream decides retirement; the project keeps
no list.

### BIND changes

BIND follows upstream without a review gate. Daily discovery compares the
last upstream commit touching `dns/bind` with the one recorded in the current
build, and any difference builds a new revision, including documentation-only
changes and changes without an upstream version bump. HA follows no upstream
plugin.

A conflict between the upstream merge and the fork's changes fails that
plugin and series and publishes nothing. Fork features are never dropped to
make a merge succeed.

### Environment changes

A change to the FreeBSD base, tools configuration or dependencies within a
series starts no build. A qualified archive stays valid for its series until
the plugin source changes; it is never requalified, rebuilt or relabeled for
another series. Rebuilding for every upstream environment change would cost
runner time without changing what users receive.

## Why the CI looks like this

The CI was rebuilt in October 2026. The previous one had grown into thousands
of lines of orchestration, redundant checks and custom integrity machinery.
Pushes and merges started overlapping pipelines with indistinguishable names,
wasted runner time and at times got Actions blocked. The rebuild replaced it
with one direct flow: **discover upstream → select eligible work → build or
promote → publish → index the feed and sync the source**.

### Workflow features over scripts

Selection and ordering are expressed with GitHub Actions features (triggers,
`if:`, `needs`, matrices, `concurrency`, reusable workflows, artifacts and
outputs) and the work itself with `git`, `gh`, `jq`, `make` and `pkg`. Custom
code is the last resort, because every line of glue is code nobody else
maintains. That is why script files need a written justification, inline
scripts stop at 30 lines for discussion, and the custom glue stays in the low
hundreds of lines.

### Duplicate work is prevented by design

Each package change has exactly one production route. A merge cannot start
repeated builds through push, PR-closed, workflow-completion or
self-dispatch events, because CI entry points exist only on `master` and
release branches carry no workflows. Native concurrency per package and
series lets the newest pending request replace older ones before any VM
starts, while never cancelling a running publication. No duplicate scanner or
history inventory is needed.

### Cheap decisions first

Deciding whether there is work costs a few API calls; building costs FreeBSD
VMs. Every decision, including whether a PR needs a build, is made on Ubuntu
before any VM starts.

### Failed jobs stay failed

There are no recovery jobs, retry loops or salvage of partial work. A failed
job stops its dependents and reports the command's own error. Instead, every
step is safe to rerun: GitHub's "Re-run failed jobs" finishes an interrupted
publication from the job's inputs, for example by replacing a leftover draft
Release. Recovery code is the kind of complexity the rebuild removed.

### Build once, publish that archive

An archive is built and qualified once, and that exact archive is published,
served and recorded. Its name, version and target agree everywhere and it is
never renamed or repackaged. The direction of truth is **build result →
package Releases → source branch and signed feed**: the release branch and the
feed follow what was actually published.

Upstream packages such as `pkg` are identified by version, never by archive
hash, because OPNsense has republished builds under an unchanged version.

One readable `build-record.json` travels with each archive. It replaces the
sealed manifest chains of the previous CI; the packages and signed catalogues
carry normal integrity.

### Candidates and promotion

Builds are expensive, so draft PRs are not built: a PR iterates as a draft,
and marking it ready for review builds a candidate of its head. Later pushes
build again and cancel a running older candidate, so a forgotten request
cannot leave a PR waiting and quick pushes do not pile up. PRs from forks are
built only on the maintainer's request, because their code is not trusted.
The candidate is built with trusted CI from `master` and carries the
final name and version. Merging promotes that candidate without rebuilding,
so the archive tested on an appliance is the one that ships.

A newer head needs a new candidate, and `master` requires up-to-date PRs. If
a candidate expired or another publication overtook its version, promotion
fails and the maintainer requests a production build, rather than the
pipeline checking versions and falling back on its own. That removes a whole
decision path.

### Trust boundaries

Code from the package source never runs where credentials are. Build and
qualification jobs, and all candidates, have no secrets. Signing and
publication run in separate jobs with credentials scoped to one destination:
the signing key exists only inside the disposable index VM, and each GitHub
App can write to only one repository. Requesting a candidate therefore never
authorizes publication. The PR status job runs on `pull_request_target`
without checking out PR code.

### Checks in CI

CI runs no tests, linters or validators by default: each one costs runner time
and maintenance, and the old CI collected many. A check is proposed with its
purpose and cost and added only after the maintainer approves it. The
production checks are package qualification before publication and the public
feed readback after it. Plugin suites run locally.

The first approved check is the changelog rule: a PR that changes a plugin's
code must change its `pkg-descr`. The changelog is the only change record
users see, and the check costs a few lines in the PR status job, which already
reads the PR's file list. CI and documentation changes are reviewed by
reading the diff, and committed with `[skip ci]` instead of dispatching
Actions to validate them.

## The signed feed

Clients read one feed per ABI and series. The series segment keeps a newer
series' package from being offered to an older system with the same FreeBSD
ABI. `${ABI}` stays literal in client configuration so that `pkg` expands it
for the base actually running.

Only the index writes the feed. It takes a shared lock, rereads the current
Releases, signs complete catalogues and replaces the feed directory in one
commit, so a delayed run cannot restore older selections and a failed run
leaves the working feed in place. Package publication never waits for the
index; a failed index stops retention but never rolls packages back.

Retention keeps the current release and one rollback per plugin and series.
It finds the release to drop through the recorded `previous_tag`, rather than
by scanning Releases.

## Series upgrades on clients

A client crossing to a new series, and possibly a new FreeBSD base, must end
up on the new feed with a newer plugin, without manual repair. Three parts
make that work together: the feed path per ABI and series, versions that start
with the series and so always increase, and hooks installed by the plugin
itself.

The hooks do no network or package work inside the firmware transaction. The
upgrade only marks the plugin pending; OPNsense's own repository step points
the managed repository file at the new feed; after boot, a worker installs the
plugin built for the new series. The worker refuses any plan that would remove
or downgrade a package, and a failure never blocks the upgrade or the boot.

The hooks must already be installed before the upgrade: a package published
for the new series cannot add them to a system that is already running it.
Custom repository files are never rewritten.

## BIND runtime

The fork once built its own `bind920` and `bind-tools` to avoid a regression
in OPNsense 26.1's BIND. OPNsense 26.7 ships a fixed, newer BIND, so those
builds were retired on 2026-10-19 and the plugin's minimum OPNsense became
`26.7` on 2026-10-21. `os-bind-rp` declares a minimum BIND version and `pkg`
installs any provider that meets it: OPNsense's own. If no provider meets the
minimum, installation fails natively; the CI never compiles a private
dependency.

`pkg` upgrades a package only from the repository it came from, so a system
that installed the fork's 9.20.27 stayed on it while OPNsense moved on. The
26.7 feed no longer serves it, and the plugin's
[how-to](../plugins/bind/how-to.md#move-bind-to-opnsenses-package) moves such
a system to OPNsense's package. The runtime build workflow and its scripts
were removed in commit `f15edf9a0`; start there if a private BIND is ever
needed again.

## Decisions

| Date | Decision | Over | Because |
| --- | --- | --- | --- |
| 2026-10-21 | Raise the BIND plugin's minimum OPNsense to `26.7` and drop the runtime from the 26.7 feed | Keeping `26.1.11_10` and the frozen runtime | No build targets 26.1, and the frozen runtime held systems back from OPNsense's BIND updates |
| 2026-10-21 | BIND versions are `<series>.<upstream>_<n>`, dots kept | `<series>_<n>` | Users see the upstream base, and dotted parts order correctly in `pkg` |
| 2026-10-21 | "Newer" compares whole versions | Comparing the revision alone | Versions within a target no longer share one base |
| 2026-10-21 | The fork's changelog sits in its own block above upstream's heading | Sections under upstream's heading | Upstream's next section conflicted with the fork's in the automatic merge |
| 2026-10-21 | PRs need a build only for packaged files | Any file under the plugin directory | Unpackaged files published identical revisions |
| 2026-10-21 | CI checks are added after maintainer approval | No checks at all | Lets a cheap, useful check such as the changelog rule in |
| 2026-10-20 | Rebuild the CI from scratch; admit script files only after every direct alternative fails | Adapting the old helpers | The old CI duplicated workflow features in thousands of lines of scripts |
| 2026-10-19 | An expired or overtaken candidate fails promotion; the maintainer requests a new build | In-pipeline version checks and fallbacks | Removes a whole decision path |
| 2026-10-19 | Retire the BIND runtime builds | Keeping them | OPNsense ships a fixed, newer BIND; the plugin's minimum covers the gap |
| 2026-10-19 | The BIND worker relies on native dependency resolution | Carrying runtime packages into new feeds | OPNsense's BIND satisfies the minimum |
| 2026-10-19 | HA uses `<series>_<revision>` | Keeping `0.2_n` | Series upgrade depends on the version naming its series |
| 2026-10-19 | Retention follows `previous_tag` | Scanning Releases | One targeted lookup |
| 2026-10-19 | Build and index with the series' latest-release `pkg` | The first release's `pkg`, or checksum rewrites | Upstream defines `pkg` per release and clients follow the latest |
| 2026-10-19 | Retire the fresh-install script for `install-repository.sh` plus `pkg install` | Keeping it | Its purpose was the runtime BIND update |
| 2026-10-19 | Develop on `master` and carry merged changes automatically | PRs to release branches | Matches upstream's `master` and `stable/<series>` model |
| 2026-10-19 | Create a new series' branch from the previous one merged with upstream `stable/<series>` | Branching from `master` | Keeps ancestry and every published fix |
| 2026-10-19 | Retired series are locked; upstream decides retirement | Allowing manual builds | Retired series stop consuming runners |
| 2026-10-19 | Order versions by revision number (superseded 2026-10-21) | `pkg version` in a VM | Every version in a target was `<series>_<n>` |
| 2026-10-19 | PHP and Python come from the installed OPNsense core | Passing tools `build.conf` values | The plugins framework reads them natively |
| 2026-10-19 | Package policy lives in the entry inputs | A `ci.mk` per package | The entries must know the branch before reading it |
| 2026-10-19 | Distribution Releases hold the archive and build record only | A signed per-Release catalogue | Clients read only the Pages feed |
| 2026-10-19 | The lifecycle calls the index after `publish` | A `workflow_run` trigger | The caller knows a publication happened |
| 2026-10-19 | Prepared source travels as a bundle and lands after publication by fast-forward | Pushing before building | The branch only ever holds published versions |
| 2026-10-19 | VMs use the `pkg` from the series' OPNsense repository, recorded by version | A separately installed `pkg` | It is the `pkg` upstream defines for the release |
| 2026-10-19 | Changes reach a release branch as the directory's diff, tracked by `Master-Commit` | Merging `master` into release branches | `master` and release branches follow different upstream lines |
| 2026-10-19 | PR applicability comes from the PR's changed files | The base branch | `master` holds CI, docs and both plugins |
| 2026-10-17 | A qualified archive stays valid across environment changes within its series | A reuse-time dry run | Avoids requalification without changed content |
| 2026-10-21 | Ready-for-review PRs from this repository build candidates automatically; drafts and forks do not | Manual requests per head | Manual requests were forgotten; drafts keep iteration cheap |
| 2026-10-16 | PR builds are requested manually per head, with up-to-date branches required | Automatic PR builds | Builds are expensive |

## Known gaps

- BIND upgrade features must be preserved from the published release sources:
  the plugin copy on `master` lacks some installed files, and release branch
  heads can differ from published sources.
- Unfamiliar future upstream formats or an unavailable builder fail the
  affected target clearly; discovery cannot make incompatible inputs
  compatible.
