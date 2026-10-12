# Upstream synchronization

## Workflow triggers

`Synchronize os-bind-rp upstream` runs daily at 04:17 UTC and may also be run
manually from GitHub Actions on `master`. It fetches OPNsense plugin stable
branches, the fork's release branches, and `opnsense/tools`. It then validates
the current release metadata and plans one safe outcome.

This workflow tracks OPNsense plugin source changes. It does not decide when
to advance the separately pinned BIND runtime package in
`.resolver-plugins/bind920.json`.

## Synchronizer outcomes

| Planner action | Meaning | Automation result |
| --- | --- | --- |
| `noop` | Current upstream BIND tree is unchanged and no newer stable series exists. | Finish without creating a ref, PR, or artifact. |
| `update-review` | The current release series has an upstream BIND tree change. | Create or recover a `sync/bind/...` review PR. No artifact is built before review. |
| `bootstrap-review` | A newer OPNsense series exists, whether or not its BIND tree differs. | Create a `sync/bootstrap/...` review PR for the new release source. No package artifact is built before review. |

Review the generated PR and merge it only after the fork-specific behavior has
been verified. The synchronizer never silently advances a release branch for a
new OPNsense series, even when its BIND tree is unchanged.

The retained `bootstrap-build` compatibility path produces only an unsigned
runtime artifact named `bind-runtime-bootstrap-<series>`. It restores CI, pin,
policy and key bytes from the trusted workflow commit, seals development inputs
for the selected immutable profile, and passes that external selection digest
to the runtime wrapper. It does not build a plugin or create a stable channel.

## Provenance and recovery

Before it creates a branch, pull request, or assignment, the workflow checks
the immutable source profile and the planned upstream, tools, FreeBSD, and core
archive provenance. Invalid or mismatched provenance stops the run before any
GitHub publication action.

If a prior run was interrupted after it began creating a review publication,
the recovery step verifies the same provenance and reconciles the existing
generated branch and PR. It does not overwrite unrelated state.

## Operational response

For a blocked run, use the failure message to identify the invalid source
metadata or unavailable upstream input. Correct it through a reviewed release
branch change; do not bypass the validator or edit a generated branch in
place. For an artifact-build failure, inspect the VM build logs and the
metadata profile first, then reproduce the build using [Building](building.md).

## Runtime and plugin publication

The [runtime workflow](../.github/workflows/bind-runtime-release.yml) starts
automatically only for an approved BIND pin change on `master`. The
[plugin workflow](../.github/workflows/bind-plugin-release.yml) owns the plugin
and its `Mk`, `Scripts` and `Templates` build framework. Shared compatibility,
catalogue, package-manager and upstream-profile changes can select the plugin;
they do not select runtime production. Runtime helper and workflow changes run
PR tests without starting a runtime release. Documentation, tests, HA-only
changes and upstream discovery changes do not select BIND production.

A relevant `master` push selects the newest numeric registered release series.
A merged plugin-affecting PR into an exact `release/bind-rp/<series>` selects
only plugin production for that series and its merged immutable source/profile
commit; unmerged closure selects neither producer. Workflow code and privileged
helpers remain pinned to the trusted default-branch workflow commit. A manual
production dispatch requires `master` and an explicit registered series; use the
runtime workflow directly to publish the approved runtime for another series.
Development dispatch requires a PR and writes only its unsigned component preview.

For a new series or approved runtime pin, run runtime production first, then
plugin production. The plugin freezes an already published runtime receipt and
fails if it is absent or incompatible; it never builds or dispatches its peer.
The three writers, including HA, share the `package-release` lock with
cancellation disabled and the full native queue enabled. A queued plugin run
may reach selection before the runtime it needs, so retry it after runtime
publication succeeds. Queue order does not supply dependency ordering.

Runtime-only updates preserve an unchanged plugin's archive and original
`built_against_runtime` record. Current compatibility and reader authentication
are recorded separately when creating the complete stack and feed. Only the
plugin producer creates a human-facing plugin source release after public
installation succeeds. [Package repository](package-repository.md) documents
component tags, complete rollback stacks, reference-aware retention and retry
recovery.

## BIND runtime candidate workflow

Use `Propose bind920 candidate` when evaluating whether Resolver Plugins
should advance the BIND runtime package independently of OPNsense's bundled
package. The workflow is manual-only and inspects FreeBSD Ports `dns/bind920`
at the selected ref, defaulting to `main`.

When the candidate is newer than the current pin, the workflow creates or
updates a `sync/bind920/<version>-<portrevision>` PR against `master`. The PR
contains the updated `.resolver-plugins/bind920.json` and a deterministic
assessment classifying the change as `security`, `risky`, `critical-bugfix`,
or `routine`.

Routine candidates may be closed or deferred. Security and critical-bugfix
candidates should be reviewed promptly, but they still require the normal
review, build, signing, and publication flow. The candidate workflow never
publishes packages and never changes a release branch directly.
