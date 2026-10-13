# Building os-bind-rp

## Build inputs

Build a release branch in the FreeBSD release declared by that branch's
`.resolver-plugins/upstream.json`. Release profiles live only on release branches;
builds using the `master` control plane extract the selected release commit's
profile before validation. The profile contains exactly these immutable inputs:

- `series` and `upstream_branch`: the OPNsense stable series being built.
- `upstream_commit`: the OPNsense plugins source commit.
- `tools_tag`: the matching numeric `opnsense/tools` release tag.
- `freebsd_release`: the value of `OS?=` in
  `config/<series>/build.conf` at that `tools_tag`.
- `core_commit`: the exact OPNsense core Git commit used to configure the
  package repository. The build checks out and verifies this commit directly;
  it does not trust the bytes of a GitHub-generated archive.
- `core_archive_url` and `core_archive_sha256`: legacy provenance fields kept
  in existing release profiles. They are not the build-time trust anchor.

Do not substitute a moving branch, a current tools checkout, or an unverified
core commit for these values. `.github/ci/shared/metadata_profile.py` rejects profiles
that do not meet the required schema and provenance checks.

The control commit's `dns/bind/Makefile` approves the runtime with
`RP_BIND_VERSION` and `RP_BIND_PORTS_COMMIT`. The controller fetches that exact
FreeBSD Ports object and generates `bind920-profile.json` from its Makefile and
distinfo Git blobs, including their SHA-256 hashes and declared version/revision.
The recipe must already declare the approved native version; the builder does
not rewrite PORTREVISION. Matching master and tools revision declarations are
supported; conflicting revisions stop input selection. Each plugin's release-source Makefile independently
owns its BIND minimum, official-plugin conflict and OPNsense floor.

The controller also verifies the source profile against its exact numeric tools
tag and records the resolved commit and build.conf hash in `tools-profile.json`.
There is no maintained OPNsense/FreeBSD compatibility map. A tools environment
change requires a reviewed source-profile update; builds never substitute latest.

GitHub Actions implementations use floating major release tags, such as
`actions/checkout@v6`, so compatible action updates can arrive automatically.
Package source, build input, signing and archive provenance checks still use
their recorded immutable identities.

Shell scripts and workflow shell steps call standalone Python commands. Keep
Python logic in `.py` files rather than inline strings or shell heredocs.

## Local build

The [runtime release workflow](../.github/workflows/bind-runtime-release.yml)
produces the pinned `bind-tools`/`bind920` pair. The
[plugin release workflow](../.github/workflows/bind-plugin-release.yml) produces
`os-bind-rp` against an already published, authenticated runtime pair. Neither
workflow builds or dispatches its peer. Both keep CI scripts, approvals and the
committed key at an immutable `master` control commit. Plugin materialization
replaces the selected source commit's entire `dns/bind`, `Mk`, `Scripts` and
`Templates` trees, removing source-absent framework paths. This matters for
legacy release branches, which intentionally do not carry control-plane code,
and prevents a leftover development marker from changing the production name.

Automatic runtime production starts only when the approved
`RP_BIND_VERSION` / `RP_BIND_PORTS_COMMIT` approval changes on `master`. It selects the newest
numeric `release/bind-rp/<series>` branch. Plugin source, its helpers and the
`Mk`, `Scripts` and `Templates` framework trigger only the plugin workflow.
Shared BIND helpers, catalogue, package-manager, installer and
key changes can also trigger the plugin workflow; they do not start runtime
production. Release helpers are grouped in `.github/ci/bind/`,
`.github/ci/ha_dhcp/`, and `.github/ci/shared/`; regression tests remain in
`.github/ci/ci-tests/`. PR tests retain broader validation filters, including
runtime helpers and workflow changes. HA-only changes, test-only edits and
documentation do not trigger BIND publication.

Merging a plugin-affecting pull request into a release-source branch automatically
runs plugin production for that series, pinned to the merge commit. It does not
start the runtime workflow. The workflow and publication helpers still come from
the trusted `master` control plane. Closing a pull request without merging does
not build a release. Manual dispatch remains available for an explicit series
or development build; use it to publish the approved runtime for another series.

For a local unsigned build, start in a disposable clone of `master` and fetch
the selected source once. Preserve its upstream profile separately from the
control checkout:

```sh
series=26.7
control_commit=$(git rev-parse HEAD)
release_ref="refs/heads/release/bind-rp/$series"
git fetch --no-tags origin "$release_ref:refs/remotes/origin/build-source"
source_commit=$(git rev-parse refs/remotes/origin/build-source)
mkdir -p artifacts
git show "$source_commit:.resolver-plugins/upstream.json" > artifacts/source-upstream.json
git show "$control_commit:dns/bind/Makefile" > artifacts/runtime-approval.mk
git show "$source_commit:dns/bind/Makefile" > artifacts/plugin-package.mk
tools_tag=$(python3 .github/ci/shared/metadata_profile.py artifacts/source-upstream.json "$series" tools_tag)
git init --bare artifacts/tools
git -C artifacts/tools fetch --depth=1 https://github.com/opnsense/tools.git "refs/tags/$tools_tag:refs/tags/$tools_tag"
```

The controller resolves creator and minimum-reader roles once and seals the
upstream/tools evidence, raw runtime approval, generated recipe, raw plugin
source definition, exact wrapper hash, producer inputs and any
published component receipts in a separate trusted directory. Consumers receive
that artifact's immutable ID and the external SHA-256 of `selection.json`.
Every wrapper requires `RP_TRUSTED_DIRECTORY` and `RP_SELECTION_SHA256` before
package-manager or build operations; the old environment defaults are insufficient.
Native VM jobs also trust their exact checkout directory for Git access by the
VM's root user, so Git can verify the selected source objects despite the host
runner's ownership of the checkout.

Every selected runtime run builds a fresh pair, verifying the sealed exact Ports
recipe and building `bind-tools` followed by `bind920`. It never searches old
fingerprints to skip the build. Documentation is excluded to avoid building the
Sphinx documentation toolchain. To retry a failed publication, rerun only the
failed jobs in the original Actions run; do not rebuild or resign its archives.

Plugin selection requires a published runtime that matches the approved pin
and the actual source package requirement. It freezes the full receipt before the VM,
authenticates and installs that exact pair, then builds or reuses the plugin.
It never falls back to building BIND. If the approved runtime is absent or
incompatible, publish it with the runtime workflow and retry the plugin run.
The plugin's recorded build-time runtime remains historical provenance; a later
compatible runtime can be qualified with the same original plugin bytes.
The plugin manifest records an ordinary `bind920` dependency at its original
authenticated build version, alongside `dep_formula: "bind920 >= 9.20.26"`.
The ordinary edge lets native pkg discover missing BIND and its tools. Its
recorded version remains build provenance; installed compatibility follows the
source minimum. The reviewed source also emits its core dependency and a
read-only pre-install guard for actual core/runtime versions and incompatible
installed plugin names. Qualification checks the exact source-generated guard
and both installation directions. Native manifest `conflicts` entries describe
installed database relationships and cannot express this prohibited-name policy.
The pinned reader prefers existing dependencies during `install` and does not
automatically enforce the formula's minimum. The guard uses its native stop
status to prevent registering an unsupported plugin; ordinary script failure
is insufficient. Successful command exit alone does not prove installation.
Official `os-bind` replacement uses the retained migration installer, which
checks prerequisites and upgrades an older pair before replacing the plugin.
Native shared-file resolution can remove official `os-bind` before running P's
guard, so the guard cannot provide safe migration preflight.
Archive checks require the exact declared formula, core floor and original
runtime edge. Installed `pkg info` omits dependency formulas and reports current
dependency versions. Qualification checks the installed core, BIND and tools
against the source minimums and verifies current ordinary edges directly. See the
[minimum reader's installed-package query](https://github.com/freebsd/pkg/blob/2.3.1/libpkg/pkgdb_query.c).
It clears only `dns/bind/work` before packaging; do not invoke the inherited
`make clean` target after materializing a release source, because that target
resets `dns/bind/src` to the control-plane checkout.

The disposable FreeBSD 14.3 GitHub Actions image may need
`IGNORE_OSVERSION=yes` to install current builder tools after the public
FreeBSD catalogue advances. Current FreeBSD Ports also requires
`ALLOW_UNSUPPORTED_SYSTEM=yes` for its end-of-life release; the BIND wrapper
scopes that flag, along with `BATCH=yes`, to the two Ports package builds. None
of these builder-only compatibility overrides alter the target ABI or the
OPNsense packages used by `os-bind-rp`.

The wrapper explicitly installs the pinned recipe's ordinary build and linked
library dependencies from the configured OPNsense repository, then invokes the
two BIND builds with `NO_DEPENDS=yes`. It therefore compiles only the pinned
BIND source instead of recursively rebuilding ordinary Ports dependencies,
while retaining OPNsense-compatible linked libraries.
The wrapper selects dependencies explicitly from OPNsense and repository setup
disables the default FreeBSD repositories, including the renamed Ports and
kernel-module feeds in FreeBSD 15.1. OPNsense 26.1 supplies LMDB as `lmdb`, while
26.7 supplies the same library as `lmdb0`. Ports records the installed library
provider in the output package dependencies.

The runner installs `python3` first when the clean FreeBSD environment does
not provide it; Python is required to validate the immutable metadata before
any OPNsense package repository configuration is used.

### Target package manager and manifest compatibility

The selected immutable upstream profile's numeric `tools_tag` selects the
creator recipe from that exact official `opnsense/ports` tag. One resolution
also selects the minimum reader recipe: `26.1.11` for 26.1, and `26.7` for 26.7.
Both use the profile's FreeBSD major and `amd64` ABI. The resolver accepts the
literal version, revision and epoch from either `opnsense/pkg` or
`ports-mgmt/pkg`; ambiguous or unsupported recipes stop the build.

The sealed controller resolves both records once. To inspect that resolution
separately when diagnosing the selected profile:

```sh
python3 .github/ci/shared/target_pkg.py resolve \
  --upstream artifacts/source-upstream.json --series "$series" \
  --architecture x86_64 --output-directory artifacts/trusted
export RP_TARGET_PKG_METADATA=artifacts/trusted/creator-target.json
export RP_READER_PKG_METADATA=artifacts/trusted/reader-target.json
```

BIND CI transfers these records within the sealed controller bundle, alongside
its exact build and source inputs. HA keeps its separate immutable role artifact
and checks its external role and shared BIND input seals. Consumers use artifact IDs and
external seals before metadata helpers or VM execution. Builder output cannot
select signing policy. These digests protect our control records; they do not
pin upstream creator archive or binary bytes.

Repository setup loads the pinned core's trusted and revoked fingerprints.
Selection first queries the authenticated ordinary OPNsense feed for the exact
recipe identity. A successful query with no match permits the isolated
`OPNsense-creator` feed at `<series>/MINT/<resolved-release>/latest`. Its enabled
configuration lives under `repos/target-pkg` and is loaded only by explicit
`pkg -R` selection commands. This lets older pkg versions refresh its database
without adding the archive to ordinary dependency installation. Update,
query and fetch errors stop selection; there is no raw-download or latest-version
fallback. The ordinary dependency snapshot remains independent, including HA's
reviewed `26.7.4` snapshot.

The selector force-installs and locks the exact name/version/origin/ABI, even
when an upstream reissue keeps that identity while changing bytes. It verifies
`pkg-static` ownership, executable version, and installed manifest integrity.
If replacement fails after unlocking, it attempts to relock the remaining
installed package while preserving the original failure diagnostic. This is
build/qualification policy, not an instruction to upgrade a firewall's package
manager.

For an explicit creator install and verification in the disposable build VM:

```sh
python3 .github/ci/shared/target_pkg.py install "$RP_TARGET_PKG_METADATA" "$series" \
  --upstream artifacts/source-upstream.json --role creator
python3 .github/ci/shared/target_pkg.py verify "$RP_TARGET_PKG_METADATA" "$series" \
  --upstream artifacts/source-upstream.json --role creator
```

Installation qualification uses the separate reader record with `--role reader`
and the same upstream profile. Set `RP_TARGET_PKG_METADATA="$RP_READER_PKG_METADATA"`
on repository setup for that VM, so its isolated feed selects the reader release.
Do not resolve tags again in consumer jobs.
Native BIND qualification prepares the OPNsense core with the current package
manager before selecting the minimum reader. It then uses that reader's verified
`pkg-static` for catalogues, official `os-bind` preparation, installation and
rollback operations. The ordinary public installation gate uses this same executable.
An already-booted FreeBSD qualification VM also needs the core's normal PHP
initialization before plugin package hooks run. When `/conf/config.xml` is absent,
the verifier copies the installed factory configuration, then runs
`rc.subr.d/php`, starts the real configd service and renders `OPNsense/WebGui`.
It requires PHP to resolve and load `config.inc` through the generated include
path. Existing customer configuration is preserved; this setup does not run the
appliance's network or firewall boot sequence.
Local qualification requests each built package's exact version and compares its
installed identity with the archive. Public native pkg qualification also checks
the supported behavior of keeping a newer compatible upstream BIND pair.
File checksum validation accepts SHA256 hex and BLAKE2b z-base32, as supported
by the [minimum reader's checksum implementation](https://github.com/freebsd/pkg/blob/2.3.1/libpkg/pkg_checksum.c).
Own installed packages require complete native file checksums and a successful
`pkg check -s`. The minimum reader can lose upstream object-valued BLAKE file
digests while installing them. For a retained upstream pair, migration records
those raw native rows separately and compares complete measured file maps from
genuine native recovery archives before and after replacement. Identity,
path-set and ownership checks remain mandatory. Native qualification also
independently measures the retained physical files; a successful `check -s`
with missing native digests is insufficient.
The runtime canary uses its own RNDC client configuration and key. An installed
plugin's default `rndc.conf` therefore cannot change the probe's control key;
installed and customer configuration files remain untouched by that probe.

New BIND provenance uses schema 4, independent runtime/plugin component channels
use schema 1, full BIND stack channels use schema 6, and combined catalogues use
schema 2. HA channels retain schema 2. `package_creator` contains name, full
version, origin and ABI.
`creator_source` records the numeric release, official repository, commit and
recipe path for audit. Flat build metadata records `pkg_creator`,
`creator_release`, `creator_ports_commit` and `creator_recipe_path`.
Runtime fingerprints cover its pin, compatibility tuple, creator identity and
runtime wrapper bytes. Plugin fingerprints cover the full plugin/framework
source SHA, tuple, creator identity and plugin wrapper bytes; runtime
identity is excluded. Source/control audit SHAs are excluded from runtime
identity. Current CI accepts the formats above and stops on other component
formats. Plugin reuse preserves every original byte, including earlier
creator audit references and the plugin's original build-time runtime.
Qualification records current reader authentication separately. Composed
catalogues preserve original component provenance and record their current
catalogue writer separately; all selected archives must share the exact ABI.
Creator byte reissues no longer need hash-update PRs.
Staging turns `pkg repo`'s two `.tzst` catalogue aliases into regular copies of
their corresponding `.pkg` files before recording the complete asset hashes.

Historical snapshots remain installable through `pkg`; they are not inputs to
current CI. Preserve their original metadata as the record of the actual build.

Before copying an artifact, each wrapper verifies that the target parser can
read a non-null checksum for every packaged file. The equivalent manual gate
is:

```sh
python3 .github/ci/shared/package_checksums.py \
  --pkg-command /usr/local/sbin/pkg-static path/to/package.pkg
```

Treat a missing, `(null)`, or malformed file checksum as an incompatible
artifact even when the archive and repository signatures are valid.

Prepare a sealed development runtime on a controller with Python provisioned
and access to the official role recipes:

```sh
python3 -I .github/ci/bind/shared/components.py inputs \
  --component bind_runtime --series "$series" --mode development \
  --upstream artifacts/source-upstream.json --runtime-approval artifacts/runtime-approval.mk \
  --tools-repository artifacts/tools \
  --recipe .github/ci/bind/runtime/build-bind920.sh \
  --control-commit "$control_commit" --profile-commit "$source_commit" \
  --trusted-directory artifacts/trusted/runtime --intake-directory "artifacts/$series/runtime-intake" \
  --output artifacts/runtime-controller.json
export RP_TRUSTED_DIRECTORY=artifacts/trusted/runtime
export RP_SELECTION_SHA256=$(python3 -I .github/ci/shared/workflow_data.py field artifacts/runtime-controller.json selection_sha256)
# In the profile's matching disposable FreeBSD environment:
.github/ci/bind/runtime/build-bind920.sh "$series" "artifacts/$series/runtime-producer"
```

For a plugin preview, independently seal its source and published runtime intake.
This requires an approved runtime already present in the distribution repository:

```sh
python3 -I .github/ci/bind/shared/components.py inputs \
  --component bind_plugin --series "$series" --mode development \
  --upstream artifacts/source-upstream.json --runtime-approval artifacts/runtime-approval.mk \
  --tools-repository artifacts/tools \
  --package-definition artifacts/plugin-package.mk \
  --recipe .github/ci/bind/plugin/build-os-bind-rp.sh \
  --control-commit "$control_commit" --profile-commit "$source_commit" --source-commit "$source_commit" \
  --trusted-directory artifacts/trusted/plugin --intake-directory "artifacts/$series/plugin-intake" \
  --repository resolver-plugins/repository --output artifacts/plugin-controller.json
export RP_TRUSTED_DIRECTORY=artifacts/trusted/plugin
export RP_SELECTION_SHA256=$(python3 -I .github/ci/shared/workflow_data.py field artifacts/plugin-controller.json selection_sha256)
RP_RUNTIME_DIRECTORY="artifacts/$series/plugin-intake/bind_runtime" RP_PLUGIN_DEVEL=yes \
SOURCE_COMMIT="$source_commit" \
.github/ci/bind/plugin/build-os-bind-rp.sh "$series" "artifacts/$series/plugin-producer"
```

The plugin preview has the explicit `os-bind-rp-devel` name. The package,
`plugin-build-inputs.json` and `build-metadata.txt` are written below its output directory.
The metadata records the source commit, BIND package version, OPNsense package
version, ABI, provenance values, and FreeBSD environment used for the build.

`.github/ci/shared/setup-opnsense-repository.sh <series>` is normally called by the
build runner. Use it directly only when diagnosing repository setup; it changes
the FreeBSD VM's package repository configuration.

The `Test BIND plugin` pull-request workflow discovers every active
`release/bind-rp/<series>` branch, materializes its `dns/bind/src` tree, and
runs the canonical `dns/bind/tests` suite from the pull request. It also runs
the CI helper tests for BIND profile changes, including package-owned
runtime approval candidate PRs. Do not add a static release
matrix: a newly created release branch is included automatically.

Local canonical checks require the same selected release-source materialization.
The control-plane `master` tree intentionally lacks `dns/bind/src`, so a raw
`pytest dns/bind/tests` there cannot collect the behavior suite. In a disposable
checkout, follow the workflow's `Materialize canonical tests and release BIND
source` step for each active release branch, retaining the tests from the
selected control commit, then run `python3 -m pytest -q dns/bind/tests` against
that exact source. Report each series and immutable source commit checked.

The workflow requires `dns/bind/pkg-descr` to change with publishable
plugin runtime, package-hook, or PLUGIN metadata changes. Runtime approval-only
changes do not modify the plugin package contract. Test-only changes and
`PLUGIN_REVISION`-only rebuilds do not require a changelog entry.

## BIND candidate updates

The `Propose bind920 candidate` workflow is manual-only. It inspects a
FreeBSD Ports ref, defaults to `main`, and compares `dns/bind920` with the
current `dns/bind/Makefile` approval. If it finds a newer BIND 9.20
candidate, it updates only the two approval fields on a `sync/bind920/<version>-<portrevision>`
branch and opens or updates a PR against `master`.

The workflow's assessment is deterministic. It classifies a candidate as:

- `security` when maintainer-supplied notes or security text contain CVE,
  vulnerability, advisory, or related security signals.
- `risky` when the Ports diff changes dependency or configuration inputs such
  as `LIB_DEPENDS`, `RUN_DEPENDS`, `USES`, `OPTIONS_DEFAULT`, or
  `CONFIGURE_ARGS`.
- `critical-bugfix` when notes mention resolver-impacting issues such as
  crash, assertion, SERVFAIL, DNSSEC validation, DoT, TLS, cache corruption,
  or data loss.
- `routine` when none of those signals are present.

The workflow may receive maintainer-supplied changelog/security notes through
its `changelog_text` input. It does not fetch ISC or VuXML pages directly,
because adding non-GitHub egress destinations requires explicit approval under
the workspace security policy. The generated PR is evidence for review only;
it does not publish packages.

## Before approving a build change

The `CI helper tests` pull-request workflow runs the full `.github/ci/ci-tests/`
suite when CI, workflow, package, framework, or installer inputs change. It uses
read-only repository permissions and local Git and command fixtures. Package
build and publication triggers are unchanged.

Use the profile extracted in the local build steps above to check metadata
before running a full VM build:

```sh
python3 .github/ci/shared/metadata_profile.py \
  artifacts/source-upstream.json "$series" freebsd_release
sh -n .github/ci/bind/runtime/build-bind920.sh .github/ci/bind/plugin/build-os-bind-rp.sh \
  .github/ci/bind/shared/verify-bind-release.sh .github/ci/ha_dhcp/verify-dhcp-interface-ha.sh \
  .github/ci/shared/setup-opnsense-repository.sh
python3 -m compileall -q .github/ci
pytest -q .github/ci/ci-tests
git diff --check
```

Also check the three suites used by the candidate workflow without pytest:

```sh
python3 .github/ci/ci-tests/test_bind920_candidate.py
python3 .github/ci/ci-tests/test_bind920_reuse.py
python3 .github/ci/ci-tests/test_release_channel_provenance.py
```

Inspect the resulting package and `build-metadata.txt` before treating a build
artifact as suitable for later package-repository publication.
