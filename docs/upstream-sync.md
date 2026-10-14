# Upstream synchronization

## Workflow triggers

`Synchronize os-bind-rp upstream` runs daily at 04:17 UTC and may also be run
manually from GitHub Actions on `master`. It fetches OPNsense plugin stable
branches, the fork's release branches, and `opnsense/tools`. It then validates
the current release metadata and discovers one review decision.

This workflow tracks OPNsense plugin source and numeric tools environment
changes. Runtime approval remains the separate pair of RP_BIND fields in
`dns/bind/Makefile`; synchronization does not advance that approval.

## Synchronization app setup

Review publication and interrupted-publication recovery authenticate with a
private GitHub App installed only on `resolver-plugins/plugins`. Use a separate
app from the [package publisher](package-repository.md), whose approved scope is
`Contents: write` on `resolver-plugins/repository`. The synchronization app needs
these repository permissions:

| Permission | Access |
| --- | --- |
| Contents | Read and write |
| Pull requests | Read and write |
| Workflows | Read and write |
| Metadata | Read-only, granted automatically |

An organization owner or authorized app manager completes registration:

1. Open the [prefilled registration form](https://github.com/organizations/resolver-plugins/settings/apps/new?name=Resolver%20Plugins%20Upstream%20Sync&description=Create%20reviewed%20upstream%20synchronization%20proposals%20for%20resolver-plugins%2Fplugins&url=https%3A%2F%2Fgithub.com%2Fresolver-plugins%2Fplugins&public=false&webhook_active=false&request_oauth_on_install=false&contents=write&pull_requests=write&workflows=write).
   Check the permissions above, keep webhooks inactive, and select installation
   access only for the app owner. Create the app; user OAuth authorization and
   callback URLs are unnecessary for this installation-token workflow.
2. Select **Install App**, choose `resolver-plugins`, and select **Only select
   repositories** with `plugins` as the sole repository.
3. In the app's **General** settings, copy its **Client ID** into the repository
   Actions variable `RP_SYNC_APP_CLIENT_ID`. Open **Credentials** → **Key pairs**
   → **New key**, then store the complete downloaded PEM contents in the
   repository Actions secret `RP_SYNC_APP_PRIVATE_KEY`. Keep the private key out
   of source and logs. GitHub's [private-key guide](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/managing-private-keys-for-github-apps)
   describes the current key-generation interface.

`RP_SYNC_REVIEWER` remains the login assigned to generated review PRs.
`actions/create-github-app-token@v3` requests the three write permissions above
for the executing repository and revokes its short-lived token at job cleanup.
Only the recovery and publication commands receive that token. Checkout uses a
read-only `GITHUB_TOKEN` with credential persistence disabled.

The Workflows permission is required because proposals include the generated
`.github/workflows/bind-tests.yml` caller. It is unavailable through the
`GITHUB_TOKEN` workflow `permissions` key. App authentication also allows PR
creation independently of the repository's **Allow GitHub Actions to create
and approve pull requests** setting. See GitHub's
[token action](https://github.com/actions/create-github-app-token) and
[app registration guide](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/registering-a-github-app).

## Synchronizer outcomes

| Planner action | Meaning | Automation result |
| --- | --- | --- |
| `noop` | BIND source and tools environment are unchanged, with no newer stable series. | Finish without creating a ref, PR, or artifact. |
| `update-review` | The current series has a BIND tree or tools/FreeBSD environment change. | Create or recover a `sync/bind/...` review PR. No artifact is built before review. |
| `bootstrap-review` | A newer OPNsense series exists, whether or not its BIND tree differs. | Create a `sync/bootstrap/...` review PR for the new release source. No package artifact is built before review. |

Review the generated PR and merge it only after the fork-specific behavior has
been verified. The synchronizer never silently advances a release branch for a
new OPNsense series, even when its BIND tree is unchanged.

The retained `bootstrap-build` compatibility path produces only an unsigned
runtime artifact named `bind-runtime-bootstrap-<series>`. It restores CI, raw approval and key bytes from the trusted workflow commit, seals development inputs
for the selected immutable profile, and passes that external selection digest
to the runtime wrapper. It does not build a plugin or create a stable channel.

## Provenance and recovery

Discovery freezes the control and source SHAs, observed stable tip, numeric tools
tag/commit and upstream-derived FreeBSD release. The workflow resolves the core
commit/archive audit once, then `seal-plan` produces a complete schema-2 plan.
Apply and publish consume that plan without rediscovering inputs. Generated refs
are `sync/<bind|bootstrap>/<series>/<upstream-prefix>-<input-hash-prefix>`;
the hash covers the entire sealed plan except its derived ref.

An environment-only proposal parents the existing release head, keeps its plugin
and framework trees and original upstream baseline, and updates only the profile,
generated proposal audit and thin read-only test caller. Source updates carry
fork diffs only from `dns/bind` (excluding canonical tests) and `Mk/plugins.mk`.
CI, policy and tool directories are not blanket overlays. Temporary clones
prepare commits without resetting the user's checkout or creating worktrees.

The review commit's generated `.resolver-plugins/sync-plan.json` records its exact
sealed inputs for interrupted-publication recovery. Recovery checks that audit,
ref, metadata and source relationships before reconciling the PR. An existing
ref with different bytes is a conflict, never an overwrite. Before activating
this schema, finish outstanding reviews created by the previous schema using
its normal recovery path; historical refs remain untouched.

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
and its `Mk`, `Scripts` and `Templates` build framework. Shared helpers,
catalogue and package-manager changes can select the plugin;
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
`built_against_runtime` record. The retained plugin's source requirement and current reader authentication
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
contains only the two updated `dns/bind/Makefile` approval fields and a deterministic
assessment classifying the change as `security`, `risky`, `critical-bugfix`,
or `routine`.

Routine candidates may be closed or deferred. Security and critical-bugfix
candidates should be reviewed promptly, but they still require the normal
review, build, signing, and publication flow. The candidate workflow never
publishes packages and never changes a release branch directly.
