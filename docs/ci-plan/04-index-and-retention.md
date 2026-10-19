# Phase 4: Index and retention

Status: stages defined; handoffs not written. Not built.

After a publication, the series feed is rebuilt from the current Releases it
serves, published to Pages, read back, and the release two versions back is
removed (design, "Index"). `repository-index.yml` is a reusable workflow called
from `plugin-target.yml` after `publish`.

## Contracts used

- Inputs `series`, `abi`, `component`, `current_tag`; concurrency group
  `package-repository-index` (design, "Index").
- Feed `channel.json` schema 4, reading `components.*.current_tag` from schema 3
  (design, "Data formats").
- From phase 3: `publish.published` and the published records with
  `previous_tag`.
- Frozen material it must never write or prune (design, "Frozen").
- Credentials: `RP_PKG_SIGNING_KEY` in `stage` only; the distribution app in
  `publish` and `retain`; the sync app in `retain` for source Releases.

## Stages

### 4.1 Refresh a series feed

Role: serve the current Releases of one series through the signed Pages feed.

- Inputs: the call inputs; the feed's existing `channel.json`; each listed
  current Release's archives; the series' OPNsense `pkg`.
- Outputs: `pkg/<abi>/<series>/latest/` on `gh-pages` replaced in one commit with
  the catalogue, archives, `resolver-plugins.pub` and `channel.json`; job failure
  if the public feed does not match after a short retry.
- Contracts: `select` stops when nothing changed; `stage` signs with `pkg repo`
  in a VM; `publish` pushes one fast-forward commit; `readback` compares the
  public files with the staged ones. The frozen `bind_runtime` entry stays listed.
- External inputs: the existing `channel.json`; current Releases; the public feed.
- Depends on: phase 3; called with `needs: publish` and
  `if: needs.publish.outputs.published == 'true'`.
- Done when: the diff shows the four jobs, the key only in `stage`, nothing
  written outside the one feed directory, and the call wiring in
  `plugin-target.yml`.

### 4.2 Retention

Role: keep current plus one rollback per plugin and series.

- Inputs: the published version's record (its `previous_tag`); that previous
  Release's record (its own `previous_tag` and source tag).
- Outputs: the release two versions back deleted from both repositories (source
  tag kept); nothing deleted when it is missing.
- Contracts: runs only after a successful `readback`; touches only the input
  component and series; never touches frozen material.
- External inputs: the previous Release's record.
- Depends on: 4.1.
- Done when: the diff shows deletion driven only by `previous_tag`, the missing
  case as a normal outcome, and no listing or scanning of Releases.

## Decisions

- Readback compares public files with the staged ones, over a FreeBSD VM running
  `pkg update`, because signatures were made in `stage` and a byte comparison
  shows the feed serves them (2026-10-19).
