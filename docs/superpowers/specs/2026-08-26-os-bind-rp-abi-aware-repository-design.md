# ABI-aware os-bind-rp package repository

## Problem

The installed Resolver Plugins repository configuration currently points to a
series-specific GitHub Release URL (`pkg-26.1` or `pkg-26.7`). A major
OPNsense upgrade changes the FreeBSD package ABI before `pkg` refreshes the
third-party catalogue. A client left on `pkg-26.1` after upgrading to 26.7
therefore receives a `FreeBSD:14:amd64` catalogue while it requires
`FreeBSD:15:amd64`.

OPNsense invokes `opnsense-update -u` before its `upgrade` syshook. A plugin
syshook cannot fix the repository in time for that invocation.

## Decision

Use a single ABI-aware client URL:

```
https://resolver-plugins.github.io/repository/pkg/${ABI}/latest
```

`pkg` expands `${ABI}`. The initial supported mappings are:

| OPNsense series | ABI | repository path |
| --- | --- | --- |
| 26.1 | `FreeBSD:14:amd64` | `pkg/FreeBSD:14:amd64/latest` |
| 26.7 | `FreeBSD:15:amd64` | `pkg/FreeBSD:15:amd64/latest` |

The published path is keyed by ABI, not by OPNsense series. This allows a
client to select a compatible catalogue automatically after the system ABI
changes. GitHub Pages for the existing `resolver-plugins/repository`
distribution repository serves the static endpoint. A GitHub Release tag
cannot serve this URL because the ABI value cannot be transformed into the
existing `pkg-<series>` tag format.

## Client migration

The next production 26.1 package release migrates only the known,
plugin-managed legacy `resolver-plugins.conf` form to the ABI-aware URL. It
preserves the signing key, `mirror_type`, and all other repository settings.
It must not rewrite an unknown custom URL, disabled repository, alternate key,
or unrelated configuration; such a client receives a clear message with the
manual migration instructions instead.

New installations write the ABI-aware configuration from the outset. Already
upgraded 26.7 systems require a one-time manual correction because their
existing 26.1 package cannot run a migration retrospectively.

## Publication and trust boundary

The existing CI build and signing boundary remains intact. After `pkg repo`
has created and signed the catalogue, publication places the complete,
verified directory at the path for its declared package ABI. Publication must
reject a directory whose package manifests do not share one ABI, or whose ABI
does not equal the selected release profile.

The established series-specific GitHub Release channels remain available only
as short-lived compatibility and rollback endpoints until the ABI-addressed
endpoint has been proven and the supported clients have migrated. They are not
used by new installation instructions.

## Verification

- Unit tests cover ABI-to-publication-path validation and reject mixed ABI
  package sets.
- Tests cover exact legacy configuration conversion and non-modification of
  custom configuration.
- A disposable FreeBSD 14.3 client resolves the ABI-aware URL to the 26.1
  signed catalogue; a FreeBSD 15.1 client resolves the same URL to the 26.7
  catalogue.
- The package query output confirms each client sees only packages whose ABI
  equals its own.
- The existing signature, package-chain, provenance, and release-profile
  checks continue unchanged.

## Non-goals

- No OPNsense-core upgrade-flow modification or pre-upgrade hook.
- No runtime redirect, proxy, tunnel, or ABI inference service.
- No rewrite of user-managed repository definitions.
