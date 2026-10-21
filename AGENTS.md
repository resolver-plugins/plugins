# Resolver Plugins Fork Guide for Coding Agents

This repository is an OPNsense plugins fork maintained by Resolver Plugins. It
carries two plugins, `os-bind-rp` (`dns/bind/`) and `os-ha-dhcp-interface`
(`net/ha-dhcp-interface/`). Treat the upstream OPNsense source as the
compatibility baseline and keep this fork's changes narrow and reviewable.
Start with [docs/README.md](docs/README.md) before changing build, upstream or
package-related code.

## Documentation

`docs/` holds three [Divio](https://docs.divio.com/documentation-system/)
sets, each with a tutorial, how-to guides, a reference and an explanation.
Use them as the record of how things are meant to behave before changing
them:

- `os-bind-rp`: [reference](docs/plugins/bind/reference.md) for settings,
  configd actions, files and hooks; [explanation](docs/plugins/bind/explanation.md)
  for design and limits.
- `os-ha-dhcp-interface`: [reference](docs/plugins/ha-dhcp-interface/reference.md)
  for settings, commands, hooks and the qualification record;
  [explanation](docs/plugins/ha-dhcp-interface/explanation.md) for the
  controller, fencing and standby routing.
- Maintaining the fork: [reference](docs/maintainers/reference.md) for
  branches, workflows, jobs, artifacts, Releases, the feed, credentials and the
  expected CI outcomes; [explanation](docs/maintainers/explanation.md) for the
  CI design, its decisions and known gaps.

## Non-negotiable rules

- Never open pull requests or issues, or push, against `opnsense/*`
  repositories. Target `resolver-plugins/*` only.
- `os-bind-rp` is a replacement for official `os-bind`, not a companion
  package; the two must never be installed together. Keep
  `PLUGIN_NAME=bind-rp` and `PLUGIN_CONFLICTS=bind` intact unless the
  maintainer explicitly changes the package policy.
- Preserve the documented minimum OPNsense version of `26.7` and the minimum
  BIND in `PLUGIN_DEPEND_FORMULA`. OPNsense's own BIND meets them; do not
  build or publish a private BIND.
- Keep fork-specific plugin changes small and isolated under `dns/bind` and
  `net/ha-dhcp-interface`. Do not take unrelated upstream plugin changes into
  this fork.
- Develop on `master` and never push to `release/*`; the build pipelines own
  those branches.
- Keep `os-bind-rp` and `os-ha-dhcp-interface` independent, with separate
  workflows and signed release channels. One plugin's pipeline must not
  dispatch, rebuild, block on or require publication by the other's. Preserve
  native installation dependencies.
- CI runs no tests, linters or validators by default. Propose any check with
  its purpose and cost, and add it only after the maintainer approves it. Run
  plugin suites (such as `dns/bind/tests/`) locally, and review CI and documentation changes by reading the diff and
  `git diff --check`. Publish and merge CI, test and documentation changes
  with `[skip ci]` commit annotations; do not dispatch Actions to validate
  them.
- The signed package repository is an approved system. Do not alter its
  GitHub Release publication, signing boundary, tokens, secrets, or end-user
  installation contract without explicit maintainer authorization.
- Run package workflows manually only when authorized for that operation;
  report the workflow URL and its actual outcome.

## Workflows

The [maintainer reference](docs/maintainers/reference.md) and
[explanation](docs/maintainers/explanation.md) describe the CI. Every change
must keep the [expected outcomes](docs/maintainers/reference.md#expected-outcomes). In short:

- Workflow first. Express selection and ordering with triggers and `paths`,
  `if:`, `needs`, matrices, `concurrency`, reusable workflows, artifacts and
  job outputs. Do the work with `git`, `gh`, `jq`, `make` and `pkg`.
- Discover series, ABIs and upstream refs; hard-code nothing that can be
  derived. A new OPNsense release must need no CI edit.
- Script files are the last resort, in any language. Before creating one,
  write down the operation, each simpler alternative and why it fails, and get
  the maintainer's approval. Stop and ask before any CI script or inline step
  grows beyond 30 physical lines; do not split or compress code to stay under
  it. Total custom glue stays in the low hundreds of lines.
- Add no recovery jobs; a failed job stays failed and stops dependent work.
- Name runs by package, target and purpose, and use `concurrency` so repeated
  events cannot start duplicate expensive jobs.
- Use the Ubuntu runner unless the work needs FreeBSD. CI runs on
  GitHub-hosted runners; switch to the self-hosted variant only on request
  (see [the how-to guide](docs/maintainers/how-to.md#switch-to-the-self-hosted-runner)).
- Validate external inputs once where they enter; trust artifacts from the
  same run.
- Keep candidate builds away from signing credentials; merging promotes the
  candidate without rebuilding.
- A PR that changes a plugin's code (`src/`, `Makefile` or `+*` scripts) must
  change its `pkg-descr`; the PR status enforces it.
- Keep package policy in the entry workflows' inputs; shared workflows name
  no package.
- Make every step safe to rerun: a rerun finishes the work from the job's
  inputs instead of failing on what an earlier attempt left.
- Build once and publish that archive. Never rename, repackage or relabel an
  archive for another series.

## Tests

- Keep CI discovery reports and investigation harnesses in the ignored
  `.github/ci-local/` directory; never stage or commit anything under it, and
  do not keep generated repositories there.
- Discovery and troubleshooting tests may stay temporary while investigating
  and must stay out of the final diff. Promote a test only when it protects a
  distinct observable behavior, invariant, known regression, security or
  compatibility risk, integration boundary, or machine-enforced contract. Do
  not keep a test for coverage alone, for another example of an
  already-protected case, or to mirror Markdown wording. Preserve uncertain
  tests until their value can be investigated.

## Code style

- Use POSIX `sh` for package and service hooks that run in the base
  OPNsense/FreeBSD environment; do not introduce Bash-specific syntax. CI code
  follows [Workflows](#workflows).
- Match existing OPNsense style and patterns. Plugin code follows the upstream
  MVC layout: model XML, API and UI controllers, `configd` actions, Volt
  templates and service templates. Copy the conventions of nearby upstream
  code rather than inventing new structure.
- Prefer immutable, functional-style code where the language and framework
  allow it: derive new values instead of mutating shared state, and keep side
  effects such as filesystem, network and service changes at clear boundaries.
  This is a preference, not a rule; use it where it reduces state bugs, not
  where it fights the framework.
- `make style` and `make lint` depend on an `opnsense/core` checkout next to
  this repository and do nothing without one. Check changed files directly
  instead: `php -l` and `phpcs --standard=PSR12` for PHP, `pycodestyle` and
  `python3 -m py_compile` for Python, and `shellcheck` for shell scripts.

## Local environment

Read `AGENTS-local.md` in the repository root when it exists. It is ignored by
Git and records the maintainer's environment, such as appliance SSH targets,
node roles and deployment procedure. Keep it current when you verify or change
those facts, keep secrets out of it, and never commit it or copy its contents
into tracked files.

## Documentation updates

- Update the affected documents in the same change when behavior or a
  workflow contract changes. Keep each document to its type: no instructions
  in a reference, no design discussion in a how-to guide.
- Keep the root README user-oriented; detail belongs under `docs/`, indexed by
  [docs/README.md](docs/README.md).
- Specs, designs, plans and todo lists are working documents, not
  documentation. Keep them in the ignored `docs/plans/` directory and never
  commit them. Before finishing the work, move lasting facts into the
  reference, the reasons and decisions into the explanation, and agent rules
  into this file.
