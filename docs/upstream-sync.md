# Upstream changes

The pipeline follows OPNsense upstream without review PRs or sync branches.
Discovery and `prepare` (see [building](building.md)) read everything from
upstream on each run.

## New series

The active series is the highest final `X.Y` or `X.Y.Z` tag in `opnsense/core`
whose matching `opnsense/tools` tag has `config/<series>/build.conf`. When that
series has no current Release for a plugin, the daily discovery builds it:

1. `prepare` creates the plugin's release branch base from the previous series'
   release branch (the highest earlier `release/<plugin>/<series>`).
2. It merges upstream `stable/<series>` into that base.
3. It carries the plugin directory's changes from `master` and sets the version
   to `<series>_1`.
4. The usual build, qualification and publication follow, and `sync-source`
   creates the new release branch.

No CI edit is needed. The previous series stops building and keeps its feed,
current Release and one rollback.

## Upstream BIND changes

BIND follows upstream. Daily discovery compares the last commit on
`opnsense/plugins` `stable/<series>` that touched `dns/bind` with
`upstream_package_commit` in the current build record. A difference builds BIND
with the next revision. Every such change is taken, including documentation-only
changes and changes without an upstream version bump. Unrelated upstream plugin
changes do not select BIND. HA does not follow upstream.

Keeping `master` merged with upstream `master` is maintainer work, outside the
pipeline.

## Conflicts

The upstream merge and the `master` carry may conflict. A conflict confined to
the `PLUGIN_VERSION` and `PLUGIN_REVISION` lines of the plugin Makefile keeps
the release side, because the version commit rewrites both. Any other conflict
fails `prepare`, names the cause in the log and publishes nothing. The target
stays failed until a maintainer resolves the cause and requests a production
build.

## Environment changes

Changes to the FreeBSD base, tools configuration, dependencies or ports within
a series start no build. A qualified archive stays in use until the plugin
source changes. Discovery compares only the upstream package path and the
build record.
