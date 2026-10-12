# Fork model

## Purpose

Resolver Plugins maintains this fork to develop a small number of BIND
features outside the official OPNsense plugin release process while retaining
the ability to incorporate upstream OPNsense plugin updates.

The package produced by this fork is named `os-bind-rp`. It is intentionally
distinct from OPNsense's official `os-bind` package and declares a package
conflict with it. A system must have one or the other installed, never both.

The package build requires OPNsense `26.1.11_10` or newer. That floor includes
the BIND version carrying the named DoT-related fix relied on by this fork.
Resolver Plugins owns the `bind920` package pin used by `os-bind-rp` and may
advance it independently of OPNsense after review. Candidate automation may
prepare evidence and PRs, but signed package publication remains a separate
maintainer-controlled boundary.

## Branch responsibilities

`master` is the control plane. It holds shared CI scripts, GitHub Actions
workflows, documentation, and the canonical BIND behavior suite in
`dns/bind/tests`. Pull requests run that suite against every active release
source discovered from `release/bind-rp/<series>` branches, so a new supported
series needs no workflow-matrix edit. It should stay aligned with upstream
OPNsense plugins except for the fork's deliberate control-plane and
package-identity changes.

`release/bind-rp/<series>` branches are the per-OPNsense-series build sources.
Their `.resolver-plugins/upstream.json` records immutable provenance for that
series. Treat a release branch as a stable, reviewed input to a package build;
do not force-push or casually rewrite it. Each release branch carries a thin
`.github/workflows/bind-tests.yml` caller that invokes the canonical read-only
test workflow from `master`; new release bootstraps inherit it through the
workflow overlay.

The synchronizer creates `sync/bind/<series>/<commit>` and
`sync/bootstrap/<series>/<commit>` branches when human review is necessary.
They are generated review inputs, not places for unrelated development.

## Current distribution boundary

The signed package repository is active and approved. Generated current and
rollback channels are published as GitHub Releases in
`resolver-plugins/repository`; human-facing source releases remain in
`resolver-plugins/plugins` and contain only the plugin package and build
metadata. A release-branch build remains a reviewed build input; it does not
independently authorize publication or a change to the installation contract.

The runtime and plugin have independent producers, compatibility fingerprints
and signed component histories. Runtime changes do not rebuild an unchanged
plugin; plugin changes consume an already published approved runtime and do not
build or dispatch it. Automatic runtime releases require an approved pin change
on `master`; helper, policy and plugin changes do not start that pipeline.
For a new series, dispatch runtime publication directly. For a new series or
runtime pin, publish the runtime first and retry the plugin if its required
component is not yet available.

Composition preserves original component archives, build records and creator
audit references, including the plugin's historical build-time runtime. Current
reader authentication and the new catalogue writer are separate qualification
records. Complete current and rollback BIND stacks remain self-contained; the
shared feed retains every registered peer, including HA on 26.7. All publishers
share one queued release lock and recheck their frozen peer receipts before
mutation. Only the plugin workflow creates the plugin's source release.

Do not alter signing, GitHub Release publication, package-repository
publication, tokens, secrets, or the end-user installation contract without
explicit maintainer authorization.
