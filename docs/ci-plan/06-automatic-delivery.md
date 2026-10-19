# Phase 6: Automatic delivery

Status: stages defined; handoff not written. Not built.

A daily run finds the active series and starts production work where it is
needed: a new series, or an upstream BIND change (specification, "Active series",
"Delivery events"; design, "Entry jobs", "Upstream discovery").

## Contracts used

- `discover` outputs a matrix of `series` values for its plugin; the entry calls
  `plugin-target.yml` with `purpose: production` for each.
- Phase 2's `prepare` already handles the new-series branch and the upstream
  merge, and skips unchanged targets.
- Entry inputs `follow_upstream` and `current_tag`.

## Stages

### 6.1 Discover and schedule

Role: each plugin's daily run selects the targets that need work.

- Inputs: `opnsense/core` and `opnsense/tools` tags; the active series' current
  Release and record; for `follow_upstream`, the upstream `stable/<series>` log of
  `source_directory`; an optional `series` from `workflow_dispatch`.
- Outputs: a matrix with the active series when its current Release is missing
  or the upstream package changed, or the requested series; an empty matrix
  otherwise, with the reason in the job summary.
- Contracts: active series and usability as in the design; no target for
  prerelease tags or retired series; a `workflow_dispatch` without `series`
  behaves like the schedule. `master` changes are not discovered here: merges
  promote (phase 5), and a production request carries `master`'s head.
- External inputs: upstream tags and logs; the current Release and record.
- Depends on: phase 2.
- Done when: the diff shows the schedule, discovery from upstream only, no
  hard-coded series, and an empty matrix that starts no VM.

## Decisions

- One stage, over separate series and upstream stages, because both are the same
  selection read from the same sources (2026-10-19).
