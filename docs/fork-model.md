# Fork model

## Purpose

Resolver Plugins maintains this fork to develop BIND features and the HA DHCP
Interface plugin outside the official OPNsense plugin release process while
following upstream OPNsense plugin updates.

| Package | Origin | Source |
| --- | --- | --- |
| `os-bind-rp` | `opnsense/os-bind-rp` | `dns/bind` |
| `os-dhcp-interface-ha` | `opnsense/os-dhcp-interface-ha` | `net/dhcp-interface-ha` |

`os-bind-rp` conflicts with OPNsense's `os-bind`; a system has one or the
other. `os-dhcp-interface-ha` replaces `wan-ha-dhcp`. Both require OPNsense
`26.1.11_10` or newer. BIND itself comes from OPNsense's packages and must meet
the minimum declared by `PLUGIN_DEPEND_FORMULA`. Names and origins stay fixed
across series.

## Branches

| Branch | Role |
| --- | --- |
| `master` | Development of both plugins, the CI and the documentation |
| `release/bind-rp/<series>` | Pipeline-written build source of `os-bind-rp` for a series |
| `release/dhcp-interface-ha/<series>` | Pipeline-written build source of `os-dhcp-interface-ha` for a series |
| upstream `master` and `stable/<series>` | `opnsense/plugins`, read by the pipeline |

Develop on `master` through PRs and never push to a release branch. Release
branches carry no workflows and are never rewritten.

Each release branch holds the upstream `stable/<series>` merge plus the fork's
plugin directory. When a change to the plugin directory reaches `master`, the
pipeline carries it onto the active series' release branch: the directory's diff
since the last carried `master` commit, recorded in the `Master-Commit` trailer
of the branch's version commit. A version commit sets `PLUGIN_VERSION` and
`PLUGIN_REVISION`, and a run publishes before it pushes, so a release branch
holds only published versions.

Tests live in each plugin's `tests` directory on `master` and run locally.

## Active series and retirement

Only the newest final Community series builds. A newer series retires its
predecessor from automatic, manual and PR builds. Retired series keep their
feed, current Release and one rollback for installed clients. Upstream decides
retirement; this project keeps no list.

## Independence

The two plugins have independent entry workflows, release branches, versions
(`<series>_<n>`), Releases and series-upgrade hooks. A pipeline for one never
dispatches, waits for or requires the other. Releases and the signed feed are
described in [package repository](package-repository.md).
