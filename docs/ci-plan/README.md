# CI rebuild plan

Builds the [design](../ci-rebuild-design.md) for the
[specification](../ci-rebuild-spec.md). Planned with `plan-in-phases`, built with
`implement-in-phases`.

Status: design and plan approved 2026-10-19. Stage 1.0 is built; stage 1.1
is next.

## Rules for every stage

The specification's "Simplicity and limits", "Failed jobs stay failed" and
"Testing decisions" sections apply to every stage. In addition:

- Review is reading the diff plus `git diff --check`; nothing is run.
- "Immutable" describes code style only. Releases are "versioned" or "current";
  upstream commits are "pinned".
- Commits are local, one per approved layer, grouped by concern (`[skip ci]` on
  `master`). Local scratch such as `.github/ci-local/` and `docs/superpowers/`
  never enters a plan commit.
- Nothing is pushed, published or configured remotely without an explicit request.

## Phases

| # | Phase | Delivers | Needs | Build status |
| --- | --- | --- | --- | --- |
| 1 | [Client series upgrade](01-client-series-upgrade.md) | `master` caught up; BIND worker and fresh install on native dependency resolution; HA upgrade hooks | none | in progress: 1.0 built |
| 2 | [Build one target](02-build-one-target.md) | Entries with a production request; `prepare`, `build`, `qualify` | none | not started |
| 3 | [Publication](03-publication.md) | `publish`, `sync-source` | 2 | not started |
| 4 | [Index and retention](04-index-and-retention.md) | `repository-index.yml` called after `publish` | 3 | not started |
| 5 | [PR builds and promotion](05-pr-builds-and-promotion.md) | PR status, candidate request, promotion | 2, 3 | not started |
| 6 | [Automatic delivery](06-automatic-delivery.md) | `discover` and the daily schedule | 2 | not started |
| 7 | [Cutover](07-cutover.md) | Old CI retired, docs, branch rules, reconciliation, activation | 1–6 | not started |

Plugins are developed on `master`; release branches are written only by the
pipeline. Phase 1 brings `master`'s plugin directories up to date, then makes the
client changes there. Phases 2–6 build the new CI on `master`, replacing the entry
workflows in place; nothing reaches GitHub until phase 7.

## Decisions

- Client series upgrade is phase 1, over building it after the CI, because it is
  independent and must reach clients before the next series (2026-10-19).
- Source preparation is built whole in phase 2, over adding upstream merging in
  phase 6, so no stage reopens another phase's code (2026-10-19).
- All phase documents are written before implementation; stage handoffs are
  written just in time at each phase start (2026-10-19).
