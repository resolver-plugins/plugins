# Upstream synchronization

## Workflow triggers

`Synchronize os-bind-rp upstream` runs daily at 04:17 UTC and may also be run
manually from GitHub Actions on `master`. It fetches OPNsense plugin stable
branches, the fork's release branches, and `opnsense/tools`. It then validates
the reviewed source ancestry and discovers one review decision. No file under
`.resolver-plugins` is required or consulted.

This workflow tracks OPNsense plugin source changes and new stable series.
Build controllers discover tools, FreeBSD and core inputs when needed; a tools
update alone does not create a metadata-refresh PR. Runtime approval remains the separate pair of RP_BIND fields in
`packages/bind-tools/Makefile` and `packages/bind920/Makefile`; synchronization
does not advance either approval or build runtime packages.

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
| `noop` | BIND source is unchanged, with no newer stable series. | Finish without creating a ref, PR, or artifact. |
| `update-review` | The current series has an upstream BIND tree change. | Create or recover a `sync/bind/...` review PR. No artifact is built before review. |
| `bootstrap-review` | A newer OPNsense series exists, whether or not its BIND tree differs. | Create a `sync/bootstrap/...` review PR for the new release source. No package artifact is built before review. |

Review the generated PR and merge it only after the fork-specific behavior has
been verified. The synchronizer never silently advances a release branch for a
new OPNsense series, even when its BIND tree is unchanged.

New-series discovery produces a source review. It does not build a temporary
runtime artifact or dispatch any package pipeline.

## Provenance and recovery

Discovery freezes the control and source SHAs, observed stable tip, numeric tools
tag/commit and upstream-derived FreeBSD release. The workflow resolves the core
commit/archive audit once, then `seal-plan` produces a complete schema-3 plan.
Apply and publish consume that plan without rediscovering inputs. Generated refs
are `sync/<bind|bootstrap>/<series>/<upstream-prefix>-<input-hash-prefix>`;
the hash covers the entire sealed plan except its derived ref.

Source updates carry fork diffs only from `dns/bind` (excluding canonical tests)
and `Mk/plugins.mk`, plus the thin read-only test caller.
CI, policy and tool directories are not blanket overlays. Temporary clones
prepare commits without resetting the user's checkout or creating worktrees.

The review commit's `Resolver-Sync-Plan` message trailer records its exact sealed
proposal inputs for interrupted-publication recovery. No plan or upstream JSON
is added to the generated source tree. Recovery checks the trailer, ref and
source relationships before reconciling the PR. An existing
ref with different bytes is a conflict, never an overwrite. Historical schema-2
refs and their JSONs remain untouched and are not recovery inputs for the new
schema. Existing metadata-only reviews can be closed without merging.
Merge source reviews with their upstream history so the next discovery can derive
the reviewed baseline. Build controllers independently discover and seal their
inputs at build time; the proposal's audit is not a maintained build profile.

## Operational response

For a blocked run, use the failure message to identify the invalid source
ancestry or unavailable upstream input. Check that the source history and upstream
refs were fully fetched; correct source problems through a reviewed release
branch change. Do not bypass the validator or edit a generated branch in
place. For an artifact-build failure, inspect the VM build logs and the
run's generated profile first, then reproduce the build using [Building](building.md).

## Runtime and plugin publication

The [tools workflow](../.github/workflows/bind-tools-release.yml) and
[daemon workflow](../.github/workflows/bind920-release.yml) build their own package
for a version-update PR and its approved merge, or an explicitly authorized
manual run. They have separate approvals and no schedule. Missing generated
metadata or a changed OPNsense environment alone does not select a build.

The [BIND plugin workflow](../.github/workflows/bind-plugin-release.yml) and
[HA workflow](../.github/workflows/ha-dhcp-interface-release.yml) compare only their
own reviewed code with the published source, before discovering build inputs.
A source merge confined to one series selects only that package and series.
Daily upstream BIND source reviews therefore reach only the BIND plugin workflow.
Unchanged code or runtime approvals skip production, including manual dispatch.

Every package signs and publishes only its own channel. BIND builds use a frozen
ordinary OPNsense dependency; HA does not fetch BIND. No release-order sequence
is required. The [repository index](../.github/workflows/repository-index.yml)
authenticates available published packages and refreshes the shared feed without
building or republishing them. Index failures are repaired by refreshing or
rerunning that index workflow, not by rebuilding a plugin or runtime package.

## BIND runtime candidate workflow

Use `Propose bind920 candidate` when evaluating whether Resolver Plugins
should advance the BIND runtime package independently of OPNsense's bundled
package. The workflow is manual-only and inspects FreeBSD Ports `dns/bind920`
at the selected ref, defaulting to `main`.

When the candidate is newer than the current pin, the workflow creates or
updates a `sync/bind920/<version>-<portrevision>` PR against `master`. The PR
contains only the two updated approval fields in the selected `packages/<name>/Makefile` and a deterministic
assessment classifying the change as `security`, `risky`, `critical-bugfix`,
or `routine`.

Routine candidates may be closed or deferred. Security and critical-bugfix
candidates should be reviewed promptly, but they still require the normal
review, build, signing, and publication flow. The candidate workflow never
publishes packages and never changes a release branch directly.
