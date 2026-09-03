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
is a reviewed build source for one OPNsense series. The release branch records
immutable plugin, tools, FreeBSD, core archive, and checksum provenance in
`.resolver-plugins/upstream.json`.

Automation is deliberately conservative. An upstream BIND change becomes a
review PR; it does not silently advance a release branch. An unchanged BIND
tree for a new series can receive a temporary build artifact. Signed,
self-contained current and rollback channels are generated in
`resolver-plugins/repository`; source releases remain narrow and human-facing.

## Repository map

- `dns/bind/`: the `os-bind-rp` plugin package definition and fork-specific
  plugin changes.
- `.resolver-plugins/`: release build metadata and the synchronization overlay
  manifest.
- `.github/ci/`: metadata validation, OPNsense repository setup, package build,
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
- `master` contains the CI control plane. Per-release source and immutable
  build metadata belong on `release/bind-rp/<series>` branches. Never rewrite
  those branches or generated `sync/bind/*` and `sync/bootstrap/*` refs.
- Treat every field in `.resolver-plugins/upstream.json` as immutable build
  provenance. A profile must use a matching `opnsense/tools` numeric tag and
  the `OS?=` value from `config/<series>/build.conf`.
- Do not weaken provenance checks, pin checks, package fingerprint checks, or
  the explicit `os-bind` conflict to make a build pass.
- Keep durable CI helper and workflow regression tests in
  `.github/ci/ci-tests/`; use local Git and command fixtures rather than live
  services. Keep the canonical BIND behavior suite in `dns/bind/tests/` on
  `master`; its PR workflow materializes and tests every active
  `release/bind-rp/<series>` source. Use the ignored `.github/ci-local/`
  directory only for temporary CI discovery and investigation harnesses;
  never stage or commit anything under it.
- Keep agent-generated process documents under the ignored
  `docs/superpowers/` directory local-only. Never force-add or commit designs,
  specifications, implementation plans, or other process notes from that
  directory, even when a skill requests committing them; this repository rule
  takes precedence.
- The signed package repository is an approved system. Do not alter its
  GitHub Release publication, signing boundary, tokens, secrets, or end-user
  installation contract without explicit maintainer authorization.
- Verify CI changes with the focused local checks in
  [docs/building.md](docs/building.md), and run the affected workflow manually
  only when authorized. Report the workflow URL and its actual outcome.
- If a written implementation plan is created, have an independent agent
  review it before implementation. The plan must state the required behavior,
  the minimal sufficient architecture, and the current requirement served by
  every proposed abstraction, boundary, configuration option, retry, cache,
  state store, dependency, and durable test. Remove proposals with no
  demonstrated current requirement.
- Treat correctness, security, data-loss, compatibility, provenance, and
  public-contract findings as blocking regardless of the review tool's
  severity vocabulary. Resolve blocking findings and repeat review before
  implementation or merge.
- Before declaring a PR ready, run an independent code review for changes to
  executable code, tests, workflows, build or release behavior, provenance,
  security boundaries, persisted state, or public contracts. Purely editorial
  documentation changes may skip independent review only when they alter no
  command, procedure, policy, generated content, link contract, or
  machine-consumed content. Human PR review does not replace required agent
  review.
- Every code review must apply both the `code-simplifier` and
  `test-suite-simplifier` as read-only passes. Review implementation and
  architecture for unexplained accretion; review changed tests for distinct
  regression value, duplication, implementation coupling, inappropriate test
  level, and exploratory residue. Also perform a documentation-impact and
  accuracy check; update documentation only when the change affects its
  contract or accuracy.
- Discovery and troubleshooting tests may remain temporary while investigating
  uncertainty. Keep them outside the final diff, using a temporary directory
  or `.github/ci-local/` for CI investigations. Promote a test into the
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
