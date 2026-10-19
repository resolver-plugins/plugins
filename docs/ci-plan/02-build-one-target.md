# Phase 2: Build one target

Status: stages defined; handoffs not written. Not built.

A maintainer's production request for one plugin and series runs the lifecycle
up to a qualified archive (design, "Lifecycle"). Nothing is published; phase 3
continues from the `qualified-` artifact. Work is on `master`: the two entry
workflows are replaced in place and `plugin-target.yml` is new.

## Contracts used

- Entry inputs and entry jobs (design, "Entry inputs", "Entry jobs"). This phase
  adds only `workflow_dispatch` with a `series` input; phases 5 and 6 add the
  other events.
- Lifecycle inputs, `prepare` outputs, artifact names and `build-record.json`
  (design, "Lifecycle", "Artifacts between jobs", "Data formats").
- Upstream discovery facts and versions (design, "Upstream discovery",
  "Versions").
- Concurrency group `plugin-<component>-<series>` on the entry's call job.
- Consumers in later phases: `publish` (phase 3) reads `prepare.version`,
  `prepare.previous_tag` and the `qualified-` artifact; candidates (phase 5) use
  `purpose: candidate` and stop after `qualify`; promotion (phase 5) uses
  `operation: promote`; `discover` (phase 6) supplies matrix targets.

## Stages

### 2.1 Prepare a target

Role: from a production request, read the environment and the current Release,
decide the operation, and produce the prepared source.

- Inputs: entry inputs; `series` (the active series; a retired one fails with
  that reason); `source_sha` (`master`'s head for production); the release
  branch head (or the previous series' release branch head when missing);
  upstream `opnsense/core`, `opnsense/tools`, `opnsense/plugins`; the current
  Release and its `build-record.json` in `resolver-plugins/repository`.
- Outputs: `operation` and `reason`; environment facts; `version`;
  `previous_tag`; `prepared_sha`; the `source-` bundle artifact.
- Covers every source case in the design: nothing changed (skip), new series
  (previous series' branch plus upstream merge), upstream package change when
  `follow_upstream` is set (merge), the carry from `master` since the last
  `Master-Commit` trailer, and the version commit with its trailer.
- Also delivers: both entries' `workflow_dispatch` with `series`, the call to
  `plugin-target.yml` with the entry inputs and the concurrency group.
- External inputs: upstream tags, configuration and branch heads; the current
  Release and its record (an old-format Release without a record means "build").
- Depends on: nothing.
- Done when: the diff shows each output derived as the design states, no
  hard-coded series or hashes, every script within 30 lines, and a skip that
  stops before any VM job.

### 2.2 Build

Role: build the plugin archive from the prepared source in the series' FreeBSD
release.

- Inputs: the `source-` bundle; `freebsd_release`, `abi`, `series`, `version`;
  the OPNsense package repository for the series.
- Outputs: the `build-` artifact with `<name>-<version>.pkg` and
  `build-record.json`.
- Contracts: the VM points `pkg` at the series' OPNsense repository and uses its
  `pkg` (the latest release's), installs the series' OPNsense core, then runs `make package` with
  `PLUGIN_ABI=<series>` and `PLUGIN_HASH=<prepared_sha>`. The job holds no
  secrets and read-only permissions.
- External inputs: the OPNsense package repository (signed; `pkg` checks it).
- Depends on: 2.1.
- Done when: the diff shows one VM step building from the bundle, the record
  fields filled from the archive (`pkg query -F`) and `prepare` outputs, and no
  checksum rewrite.

### 2.3 Qualify

Role: install the archive once on a clean OPNsense of the target series and
publish the result as the `qualified-` artifact.

- Inputs: the `build-` artifact; `freebsd_release`, `series`.
- Outputs: the `qualified-<component>-<series>-<source_sha>` artifact (archive,
  record and the source bundle); job failure when installation fails.
- Contracts: clean VM with the series' OPNsense repository and its `pkg`; OPNsense
  core initialized enough for plugin install scripts; the archive installed with
  dependencies from the OPNsense repository; `pkg check -s` on the plugin;
  name, origin, version and ABI equal to the record. No plugin test suites.
- External inputs: the OPNsense package repository.
- Depends on: 2.2.
- Done when: the diff shows one clean VM installing and checking the archive and
  the only upload of the `qualified-` name.

## Decisions

- Build and qualify run in separate VMs, over one VM, because the specification
  requires a clean installation environment (2026-10-19).
- The build VM installs OPNsense core so the plugins framework reads PHP and
  Python versions natively, over passing them from tools configuration
  (2026-10-19).
