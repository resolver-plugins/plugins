# os-bind-rp Repository Guide for Coding Agents

This repository is an OPNsense plugins fork maintained by Resolver Plugins.
Treat the upstream OPNsense source as the compatibility baseline and keep this
fork's changes narrow and reviewable.

## Design at a glance

The fork exists to maintain `os-bind-rp`, a community-maintained BIND plugin
that can follow upstream OPNsense plugin releases while carrying a small,
reviewed feature set, and `os-dhcp-interface-ha`. `os-bind-rp` intentionally
replaces official `os-bind`: the packages conflict and must never be installed
together.

`master` is where development and CI both live. Plugin changes are made and
reviewed on `master`. The `release/*` branches are written only by the build
pipelines; nobody pushes to them. Each plugin has its own workflow and its own
build and publication lifecycle, and one plugin's pipeline never depends on
the other's. Signed package channels are published to
`resolver-plugins/repository`; see
[Building and releasing](docs/building.md) and
[Package repository](docs/package-repository.md).

## Repository map

- `dns/bind/` and `net/dhcp-interface-ha/`: the two plugin directories, with
  the fork-specific plugin changes.
- `.github/workflows/`: `bind-plugin.yml`, `ha-dhcp-interface-plugin.yml`,
  `plugin-target.yml` and `package-index.yml`.
- `scripts/install-repository.sh`: the end-user repository installer.
- `docs/`: maintainer reference material. Start with
  [docs/README.md](docs/README.md) before changing build, upstream or
  package-related code.

## Non-negotiable rules

- DO NOT open pr's on the official Opnsense Repos
- `os-bind-rp` is a replacement for official `os-bind`, not a companion
  package. Keep `PLUGIN_NAME=bind-rp` and `PLUGIN_CONFLICTS=bind` intact unless
  the maintainer explicitly changes the package policy.
- Preserve the documented minimum OPNsense version of `26.1.11_10`. It is the
  minimum needed for the BIND/DoT fix used by this fork.
- Keep fork-specific plugin changes small and isolated under `dns/bind` and `net/dhcp-interface-ha`.
  Do not take unrelated upstream plugin changes into this fork.
- Develop on `master` and never push to `release/*`; the build pipelines own
  those branches.
- Keep `os-bind-rp` and `os-dhcp-interface-ha` independent, with separate
  workflows and signed release channels. One plugin's pipeline must not
  dispatch, rebuild, block on or require publication by the other's. Preserve
  native installation dependencies.
- Follow the CI spec ([spec](docs/ci-rebuild-spec.md)). Workflow first: express
  selection and ordering with workflow features and built-in tools (`git`, `gh`,
  `jq`, `make`, `pkg`). Hard-code nothing that can be derived. Add no recovery
  jobs; a failed job stays failed. Stop and ask before creating or growing any
  CI script or inline workflow script beyond 30 physical lines. A script file
  needs a written justification and the user's approval first.
- CI runs no test suites. Run plugin suites (such as `dns/bind/tests/`)
  locally only, and review CI and documentation changes by reading the diff and
  `git diff --check`. Publish and merge CI, test and documentation changes
  with `[skip ci]` commit annotations; do not dispatch Actions to validate
  them. Use the ignored `.github/ci-local/` directory only for CI discovery
  reports and investigation harnesses; never stage or commit anything under it.
- The signed package repository is an approved system. Do not alter its
  GitHub Release publication, signing boundary, tokens, secrets, or end-user
  installation contract without explicit maintainer authorization.
- Run package workflows manually only when authorized for that operation;
  report the workflow URL and its actual outcome. See
  [Building and releasing](docs/building.md).
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
- [Building and releasing](docs/building.md) for building, releasing and
  local verification.
- [Upstream changes](docs/upstream-sync.md) for how upstream changes arrive.
- [Package repository](docs/package-repository.md) for the signed repository.
