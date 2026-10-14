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
Each runtime package owns its reviewed version and Ports pin in
`packages/bind-tools/Makefile` or `packages/bind920/Makefile`. The plugin uses an
ordinary authenticated OPNsense BIND build dependency, resolved from its frozen
upstream recipe independently of either runtime package release. Candidate automation may
prepare evidence and PRs, but signed package publication remains a separate
maintainer-controlled boundary.

## Branch responsibilities

`master` is the control plane. It holds shared CI scripts, GitHub Actions
workflows, documentation, and the canonical BIND behavior suite in
`dns/bind/tests`. Maintainers run that suite locally against the active release
sources under `release/bind-rp/<series>` before approving source changes.
It should stay aligned with upstream
OPNsense plugins except for the fork's deliberate control-plane and
package-identity changes.

`release/bind-rp/<series>` branches are the per-OPNsense-series build sources.
They need no `.resolver-plugins` files. Controllers discover build inputs from
official upstream refs and Git ancestry, then freeze their exact identities in
the run's artifacts and published receipts. Treat a release branch as a stable, reviewed input to a
package build; do not force-push or casually rewrite it. Test suites run locally;
new source reviews contain no generated test-workflow caller. Historical source
refs remain immutable, and their retired test workflows stay disabled in GitHub.
Preserve upstream ancestry by merging generated source reviews with their history;
discovery uses that ancestry to identify the reviewed upstream baseline.

The synchronizer creates `sync/bind/<series>/<upstream-prefix>-<input-hash-prefix>` and
`sync/bootstrap/<series>/<upstream-prefix>-<input-hash-prefix>` branches when human review is necessary.
They are generated review inputs, not places for unrelated development.

HA DHCP follows the same source/control split. Its reviewed build sources live on
`release/dhcp-interface-ha/<series>`. Its controllers use the same discovery helper
without requiring source JSON. Package versions use the series and a plugin revision,
starting with `26.7_1`. The package remains `os-dhcp-interface-ha`; its application
and package policy are independent of BIND.

The canonical HA suite lives in `net/dhcp-interface-ha/tests` on `master` and runs
locally against registered HA release sources. Builds materialize the exact reviewed package and
framework trees while keeping CI and tests from the control plane. Relevant
package code changes select only the series whose own code differs from its
published source. CI, framework and environment changes do not rebuild HA. Merging a package
change into a release source selects that series and exact merge commit. A manual
master dispatch selects a registered series for recovery. HA does not invoke the
BIND producers.

## Current distribution boundary

The signed package repository is active and approved. Generated current and
rollback channels are published as GitHub Releases in
`resolver-plugins/repository`; human-facing source releases remain in
`resolver-plugins/plugins` and contain only the plugin package and build
metadata. A release-branch build remains a reviewed build input; it does not
independently authorize publication or a change to the installation contract.

`bind-tools`, `bind920`, `os-bind-rp`, and `os-dhcp-interface-ha` have independent
builds, signed channels and immutable histories. Runtime version-update PRs
build unsigned previews; their approved merges select only the package whose
approval changed. Plugin source reviews select only their own changed series.
No package pipeline dispatches, waits for, or requires another package release.
The daily synchronizer creates plugin source reviews and never builds runtime packages.

The plugin's native BIND dependency is frozen from the matching upstream Ports
revision before building. A daemon build creates its tools prerequisite inside
that same run for native qualification; it publishes only the daemon archive.
Ordinary installation dependencies remain part of the native package contract.

A separate repository-index workflow authenticates already published packages,
preserves their original archives and metadata, and signs the shared catalogue.
Missing unrelated packages are allowed. It alone owns the shared Pages write
lock; index failure cannot undo or block an owned package publication. Existing
client URLs and trust keys remain in use. Legacy paired runtime and complete
BIND rollback releases are retained with their original bytes. Only plugin
workflows create their respective source releases.
