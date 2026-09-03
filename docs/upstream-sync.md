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
