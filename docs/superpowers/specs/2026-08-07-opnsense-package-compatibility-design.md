# OPNsense Package Compatibility and Safe Replacement Design

## Status

Approved for implementation on 2026-08-07.

## Problem

The published Resolver Plugins packages install incorrectly on the supported
OPNsense 26.1 target even though their repository signatures and archive
checksums are valid. The build environment emits per-file checksums in the
newer `2$...` representation. OPNsense 26.1.11_10 ships `pkg` 2.3.1, whose
manifest parser discards those values. Installed files from `bind920`,
`bind-tools`, and `os-bind-rp` therefore have null package-database checksums.

The null value becomes destructive during the supported replacement from
official `os-bind` to `os-bind-rp`. After removing the conflicting official
package and extracting replacement files, `pkg` 2.3.1 attempts to preserve an
existing unowned file, dereferences the null checksum, and exits with
`SIGSEGV`. This leaves the official package removed, the replacement package
unregistered, and extracted files unowned.

The existing installer and CI do not prevent this state. The installer asks
`pkg` to perform the replacement without first proving that the target can
interpret the candidate manifests. CI verifies clean installation in a newer
FreeBSD builder environment but does not exercise a real official `os-bind`
replacement with the target OPNsense `pkg` implementation.

## Goals

- Produce `bind920`, `bind-tools`, and `os-bind-rp` archives whose per-file
  checksums are recognized by the supported target package manager.
- Make the installer fail before package mutation when a candidate archive is
  incompatible with the target package manager.
- Preserve a timestamped OPNsense configuration backup and enough package
  state to make a failed replacement diagnosable and recoverable.
- Add durable automated coverage for package-manifest compatibility and the
  official `os-bind` to `os-bind-rp` replacement.
- Prove the complete upgrade on HA-2 while HA-1 remains the healthy serving
  peer, including validation after reboot.
- Preserve signed-repository, immutable-source, conflict, and minimum-version
  policies.

## Non-goals

- Upgrading or replacing OPNsense's system `pkg` package.
- Weakening package signatures, repository provenance, or the explicit
  `os-bind` conflict.
- Publishing a stable channel from a workstation.
- General transactional recovery for arbitrary OPNsense package failures.
- Changing BIND configuration or HA synchronization behavior.

## Chosen approach

Build packages with a package creator compatible with the target OPNsense
series, and verify the result using the target package manager before signing
or publication. The workflow will treat the target-recognized per-file
checksum representation as an output contract rather than assuming that
matching FreeBSD ABI alone makes builder output compatible.

The installer will fetch the resolved candidate archives before installation
and inspect them through the local target `pkg`. It will reject any archive
whose file list is empty or contains a null or unrecognized checksum. This
preflight prevents the known partial replacement before `pkg` is allowed to
remove official `os-bind`.

Alternatives were rejected for the following reasons:

- Upgrading client `pkg` would modify an OPNsense-managed core package and
  make the plugin channel responsible for system package-manager policy.
- Rewriting manifests after the build would introduce custom repacking and
  signing behavior that duplicates `pkg create` and increases supply-chain
  risk.

## Build and publication contract

The BIND and plugin build jobs will explicitly select the OPNsense-compatible
package creator after configuring the pinned OPNsense repository. The build
metadata will record the creator version used for each archive.

Before an archive can enter a signed staged repository, a durable verifier
will inspect every archive with the target package manager and require:

- a non-empty file list;
- one recognized, non-null checksum per regular packaged file;
- the expected package name, version, ABI, and dependency identity; and
- no change to the selected release-source or BIND provenance.

The same verifier will run against all three packages after repository
installation. A package revision must be bumped for every rebuilt affected
archive so published immutable versions cannot refer to incompatible and
corrected bytes under the same identity.

Production continues to use the trusted GitHub Actions signing boundary. The
HA-2 canary uses a host-local repository signed by a one-use local key whose
private material is never published or added to the source tree. This is
necessary because ordinary PR development releases are intentionally
unsigned. The canary installer copy uses the local repository URL and the
one-use public-key fingerprint; the committed installer retains the production
URL and key. Once accepted, production promotion must use the same verified
build inputs and compatibility gates; no stable channel is published manually
during the canary.

## Installer contract

The supported installer keeps its current OPNsense series floor, public-key
fingerprint check, and signed repository configuration. Before invoking an
installing package transaction it will:

1. Refresh only the Resolver Plugins repository needed for candidate
   resolution.
2. Resolve and fetch `bind-tools`, `bind920`, and `os-bind-rp` without
   installing them.
3. Verify each fetched archive through the local `pkg`, rejecting missing,
   empty, null-checksum, or unparseable file metadata.
4. Detect and report whether the transaction replaces official `os-bind` or
   upgrades an existing `os-bind-rp`.
5. Copy `/conf/config.xml` to a timestamped, mode-preserving backup and record
   the installed package identities.

The installer will then install the BIND pair and replacement plugin from the
selected Resolver Plugins repository. On success it will require the expected
package registrations and verify that archive-listed files are owned by the
installed packages. It will not change BIND configuration or silently remove
files after an error.

A preflight failure exits without changing installed packages. A package
transaction failure preserves the configuration backup, package-state record,
and downloaded archives, then prints their locations and bounded recovery
instructions. Cleanup may remove only installer-created temporary metadata
after success; it may not broadly delete plugin paths.

## Automated verification

Durable regression tests belong in `.github/ci/ci-tests/` and cover:

- target package-creator selection and metadata recording;
- rejection of an archive with null or unrecognized file checksums;
- acceptance of a target-compatible archive with a complete file list;
- preflight ordering before any mutating `pkg` invocation;
- official `os-bind` replacement detection and configuration backup;
- preservation of diagnostic artifacts on transaction failure;
- post-install package identity and file-ownership checks; and
- a target-native integration transition that starts with official `os-bind`,
  runs the real installer, and ends with only `os-bind-rp` registered.

The existing clean-install verification remains a separate path. The staged
repository verifier additionally runs `pkg check`, `named-checkconf`, a BIND
service restart, and a recursive DNS query where the environment supplies an
OPNsense runtime.

Temporary investigation harnesses, raw traces, and reproduction packages stay
under ignored local paths and are not committed. The checksum and replacement
tests are durable because they enforce supported release behavior and prevent
recurrence.

## HA-2 canary procedure

HA-1 remains unchanged and must pass package, BIND process, and recursive-query
health checks immediately before HA-2 mutation. HA-2 will have these rollback
materials captured locally before its baseline is changed:

- `/conf/config.xml` and relevant BIND configuration state;
- the package database and installed package inventory;
- locally created archives for its installed Resolver Plugins package set;
- resolver service state and representative query results; and
- the exact candidate archives and one-use public signing key.

The canary archives are recreated on HA-2 by its target `pkg` from the already
installed, healthy Resolver Plugins files before the official-package baseline
is established. They retain their test-local identities but receive new
target-compatible per-file manifests. This proves the compatibility property
without publishing different bytes under an existing immutable version. The
production workflow still requires revision-bumped, source-built packages.

HA-2 will then be returned temporarily to a healthy official `os-bind`
baseline using the configured OPNsense repository. The baseline must have
official `os-bind` registered, `os-bind-rp` absent, valid BIND configuration,
a running daemon, and successful representative recursive queries.

The revised installer will install the signed candidate through the exact
supported replacement path. Acceptance requires:

- `os-bind` absent and `os-bind-rp` registered at the candidate version;
- the intended `bind920` and `bind-tools` versions registered;
- recognized non-null checksums and correct ownership for every packaged file;
- a consistent package database and successful `pkg check`;
- successful `named-checkconf`, managed service restart, and recursive queries;
- no new crash, package, or BIND errors in scoped logs; and
- the same checks succeeding after an HA-2 reboot.

HA consistency and representative DNS responses will then be compared between
HA-1 and HA-2. If any acceptance gate fails, HA-2 is restored from its captured
configuration and locally preserved package archives. Recovery operations are
limited to HA-2 and exact package-owned or archive-listed paths; HA-1 remains
untouched throughout.

## Documentation

`docs/building.md` will describe the target package-creator contract and local
compatibility verifier. `docs/package-repository.md` will document installer
preflight, the official-package upgrade path, diagnostic artifact retention,
and the target-native release gate. User-facing text will remain concise and
will not expose maintainer-only recovery internals.
