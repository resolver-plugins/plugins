# os-bind-rp Repository Guide for Coding Agents

This repository is an OPNsense plugins fork maintained by Resolver Plugins.
Treat the upstream OPNsense source as the compatibility baseline and keep this
fork's changes narrow and reviewable.

## Design at a glance

The fork exists to maintain `os-bind-rp`, a community-maintained BIND plugin
that can follow upstream OPNsense plugin releases while carrying a small,
reviewed feature set. `os-bind-rp` intentionally replaces official `os-bind`:
the packages conflict and must never be installed together.

`master` is the control plane. It contains documentation and CI code that
discovers compatible upstream releases. Each `release/bind-rp/<series>` branch
is a reviewed build source for one OPNsense series. Controllers discover upstream
inputs when needed and freeze their immutable identities in each run's trusted
artifacts; release branches do not require `.resolver-plugins` files.

Automation is deliberately conservative. An upstream BIND change becomes a
review PR; merging that source review makes the affected plugin series eligible
for a build. Each package has an independent build and publication lifecycle. Signed,
self-contained current and rollback channels are generated in
`resolver-plugins/repository`; source releases remain narrow and human-facing.

## Repository map

- `dns/bind/`: the `os-bind-rp` plugin package definition and fork-specific
  plugin changes.
- `.github/ci/`: upstream discovery and validation, OPNsense repository setup, package build,
  synchronization planning, and safe GitHub publication helpers.
- `.github/workflows/`: the daily/manual synchronizer and signed package
  publication workflows.
- `docs/`: maintainer reference material. Start with
  [docs/README.md](docs/README.md) before changing build, synchronization, or
  package-related code.

## Non-negotiable rules

- DO NOT open pr's on the official Opnsense Repos
- `os-bind-rp` is a replacement for official `os-bind`, not a companion
  package. Keep `PLUGIN_NAME=bind-rp` and `PLUGIN_CONFLICTS=bind` intact unless
  the maintainer explicitly changes the package policy.
- Preserve the documented minimum OPNsense version of `26.1.11_10`. It is the
  minimum needed for the BIND/DoT fix used by this fork.
- Keep fork-specific plugin changes small and isolated under `dns/bind`.
  Do not take unrelated upstream plugin changes into this fork.
- `master` contains the CI control plane. Reviewed per-release source belongs
  on `release/bind-rp/<series>` branches. Preserve upstream ancestry when merging
  generated source reviews so discovery can derive their baseline. Never rewrite
  those branches or generated `sync/bind/*` and `sync/bootstrap/*` refs.
- Discover upstream inputs once per controller run and seal the resolved commits
  and recipe hashes before consumers run. A generated profile must use a matching
  `opnsense/tools` numeric tag and the `OS?=` value from `config/<series>/build.conf`.
  Consumers must use the sealed inputs without resolving moving refs again.
- Keep `os-bind-rp`, `os-dhcp-interface-ha`, `bind-tools`, and `bind920`
  independent, with separate package pipelines and signed release channels. A package's pipeline must not dispatch, rebuild, block on, or
  require publication by another package's pipeline. Shared CI helpers and the
  repository index may consume existing package records without coupling their
  build or publication lifecycles. Preserve native installation dependencies.
- Build `os-dhcp-interface-ha` only when its own reviewed package code changes.
  Build `os-bind-rp` only when its own reviewed package code changes, including
  changes brought in by the daily upstream synchronizer. CI, documentation,
  metadata, toolchain, OPNsense environment, runtime approval, and other package
  changes alone must not trigger either plugin's rebuild.
- Build BIND runtime packages for a maintainer-requested BIND version-update PR
  and its approved publication, or an explicitly authorized manual runtime run.
  Runtime builds have no daily schedule and do not follow plugin or OPNsense
  environment changes. Each runtime package owns its approval under
  `packages/<name>/Makefile`; its version PR builds unsigned previews, and its
  reviewed merge selects only that package's production builds. Building an
  internal native prerequisite must not publish it or invoke its package pipeline.
- Package workflows publish only their own current channel and immutable variants.
  The separate repository-index workflow consumes existing signed packages,
  tolerates absent unrelated packages, and owns the shared catalogue/Pages write
  lock. An index refresh must not trigger a package build; package publication
  must not require an index refresh or another package's current channel.
- Apply eligibility independently to each package and registered OPNsense series.
  A code change confined to one series must not rebuild its peers. Discover and
  seal current build inputs only after that package and series are eligible.
  Missing generated input metadata alone is not a rebuild trigger. Preserve
  historical signed snapshots when publishing an authorized new build variant
  under an unchanged reviewed plugin version.
- Do not weaken provenance checks, pin checks, package fingerprint checks, or
  the explicit `os-bind` conflict to make a build pass.
- Keep durable CI helper and workflow regression tests in
  `.github/ci/ci-tests/`; use local Git and command fixtures rather than live
  services. Keep the canonical BIND behavior suite in `dns/bind/tests/` on
  `master`; its PR workflow materializes and tests every active
  `release/bind-rp/<series>` source. Use the ignored `.github/ci-local/`
  directory only for CI discovery reports and investigation harnesses;
  never stage or commit anything under it.
- The signed package repository is an approved system. Do not alter its
  GitHub Release publication, signing boundary, tokens, secrets, or end-user
  installation contract without explicit maintainer authorization.
- Verify CI changes with the focused local checks in
  [docs/building.md](docs/building.md), and run the affected workflow manually
  only when authorized. Report the workflow URL and its actual outcome.
- Discovery and troubleshooting tests may remain temporary while investigating
  uncertainty. Keep them outside the final diff. Create temporary Git fixtures
  and disposable clones under a unique `/tmp` directory with automatic cleanup
  (`TemporaryDirectory` or a shell exit trap). Keep CI reports and harnesses in
  `.github/ci-local/`, but do not retain generated repositories there. Remove
  generated repositories before finishing; if failure evidence must remain,
  report its path and cleanup command explicitly. Promote a test into the
  repository only when it protects a distinct observable behavior, invariant,
  known regression, security or compatibility risk, integration boundary, or
  machine-enforced contract.
- Do not retain a test merely because it was useful during development, raises
  coverage, or exercises another example of an already-protected equivalence
  class. Do not add tests that merely mirror ordinary Markdown wording.
  Preserve uncertain tests until their value can be investigated.
- Follow the repository's existing language and runtime constraints. Use POSIX
  `sh` for existing package or service hooks and thin orchestration that must
  run in the base OPNsense/FreeBSD environment. Prefer Python for non-trivial
  parsing, branching, state handling, or data transformation when Python is
  guaranteed or explicitly provisioned. Do not introduce Bash-specific syntax
  or rewrite working scripts solely because of line count.
- Prefer small pure functions for transformations and keep state changes,
  filesystem access, network access, and other side effects explicit at
  boundaries. Follow existing framework conventions; do not introduce
  functional abstractions solely for stylistic purity.

## Documentation updates

Update the relevant maintainer guide in the same change when behavior or a
workflow contract changes. Keep the root README user-oriented; operational
detail belongs under `docs/`. The focused references are:

- [Fork model](docs/fork-model.md) for package and branch policy.
- [Building](docs/building.md) for local build inputs and verification.
- [Upstream synchronization](docs/upstream-sync.md) for CI decisions and
  operational recovery.
