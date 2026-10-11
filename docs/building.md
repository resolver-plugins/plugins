# Building os-bind-rp

## Build inputs

Build a release branch in the FreeBSD release declared by that branch's
`.resolver-plugins/upstream.json`. The profile contains exactly these immutable
inputs:

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

The BIND runtime package recipe is pinned separately in
`.resolver-plugins/bind920.json`. That profile records the FreeBSD Ports
repository, exact Ports commit, `dns/bind920` `Makefile` and `distinfo`
SHA-256 values, BIND `DISTVERSION`, and `PORTREVISION`. Resolver Plugins owns
this pin for `os-bind-rp`; OPNsense's bundled BIND package remains the
compatibility baseline, but this repository may carry a newer reviewed BIND
9.20 package when a security fix, critical bugfix, or maintainer-approved
routine update warrants it.

## Local build

The [BIND release workflow](../.github/workflows/bind-package-release.yml) is the
canonical build path. It keeps the CI scripts checked out from `master`,
fetches the selected immutable release
commit, then materializes only that commit's `dns/bind` source and
`.resolver-plugins/upstream.json` and `Mk` build framework. This matters for
legacy release branches, which intentionally do not carry the control-plane
scripts. In particular, the release `Mk` files prevent a development-branch
marker from adding an unintended `-devel` package suffix.

Package-affecting pushes to `master` automatically run production for the
newest numeric `release/bind-rp/<series>` branch. Release helpers are grouped in
`.github/ci/bind/`, `.github/ci/ha_dhcp/`, and `.github/ci/shared/`;
regression tests remain in `.github/ci/ci-tests/`. The BIND workflow watches the
whole `bind/` and `shared/` directories, so new helpers are covered automatically.
HA-only source, release helpers
and workflows, test-only edits, and documentation do not trigger a BIND build.
Shared catalogue, signing/publication, package-creator, and build-framework changes
still do. PR test workflows retain their own broader validation filters.

Merging a package-affecting pull request into a release-source branch automatically
runs production for that series, pinned to the merge commit. The workflow and
publication helpers still come from the trusted `master` control plane. Closing a pull request
without merging does not build a release. Manual dispatch remains available
for an explicit series or development build.

Reproduce that split in a disposable clone when building locally. Start
from `master`, fetch the selected release branch, and overlay only its release
inputs before entering the matching FreeBSD environment:

```sh
series=26.7
release_ref="refs/heads/release/bind-rp/$series"
git fetch --no-tags origin "$release_ref:refs/remotes/origin/build-source"
source_commit=$(git rev-parse refs/remotes/origin/build-source)
git checkout "$source_commit" -- .resolver-plugins/upstream.json Mk dns/bind
git cat-file -e "$source_commit:Mk/devel.mk" 2>/dev/null || rm -f Mk/devel.mk
```

The runner checks out the pinned OPNsense core commit and configures its
package repository and fingerprints. Before compiling, it checks the
self-contained `pkg-<series>` channel in `resolver-plugins/repository` for
`bind920-provenance.json`. If the complete BIND profile, series, FreeBSD
release, architecture, and target package-creator identity match, it downloads
the two BIND packages through the signed package channel and installs them in
the build VM. A first build or changed compatibility identity is a normal cache
miss and uses the exact FreeBSD Ports recipe pinned in
`.resolver-plugins/bind920.json`, verifies its hashes, and builds `bind-tools`
followed by `bind920` at the pinned BIND package version. Documentation is
excluded because it is not needed at runtime; this keeps the source build from
pulling in the large Sphinx documentation toolchain. The plugin build then
installs that exact pair before packaging `os-bind-rp`, so the current channel
and its rollback snapshot contain the BIND packages actually used by the build.
The plugin manifest records `dep_formula: "bind920 >= 9.20.26"`, not a locally
built BIND revision, and the builder verifies the OPNsense version floor.
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

Resolve both records once after materializing the selected release inputs:

```sh
python3 .github/ci/shared/target_pkg.py resolve \
  --upstream .resolver-plugins/upstream.json --series "$series" \
  --architecture x86_64 --output-directory artifacts/trusted
export RP_TARGET_PKG_METADATA=artifacts/trusted/creator-target.json
export RP_READER_PKG_METADATA=artifacts/trusted/reader-target.json
```

CI transfers only these two records in a separate, immutable same-run artifact.
Consumers download its artifact ID and verify both profile-job SHA256 outputs
before metadata helpers or VM execution. Builder output cannot select signing
policy. These digests protect our control records; they do not pin upstream
creator archive or binary bytes.

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
  --upstream .resolver-plugins/upstream.json --role creator
python3 .github/ci/shared/target_pkg.py verify "$RP_TARGET_PKG_METADATA" "$series" \
  --upstream .resolver-plugins/upstream.json --role creator
```

Installation qualification uses the separate reader record with `--role reader`
and the same upstream profile. Set `RP_TARGET_PKG_METADATA="$RP_READER_PKG_METADATA"`
on repository setup for that VM, so its isolated feed selects the reader release.
Do not resolve tags again in consumer jobs.
Native BIND qualification prepares OPNsense and official `os-bind` with the
current OPNsense package manager before selecting the minimum reader. It then
uses that reader's verified `pkg-static` for catalogue, installation and rollback
operations. The shipped installer uses this same executable.
Local qualification requests each built package's exact version and compares its
installed identity with the archive. Public installer qualification also checks
the supported behavior of keeping a newer compatible upstream BIND pair.
File checksum validation accepts SHA256 hex and BLAKE2b z-base32, as supported
by the [minimum reader's checksum implementation](https://github.com/freebsd/pkg/blob/2.3.1/libpkg/pkg_checksum.c).
Installed file contents still require a successful `pkg check -s`.

New BIND provenance uses schema 4 and channels use schema 5; HA channels use
schema 2. `package_creator` contains name, full version, origin and ABI.
`creator_source` records the numeric release, official repository, commit and
recipe path for audit. Flat build metadata records `pkg_creator`,
`creator_release`, `creator_ports_commit` and `creator_recipe_path`.
BIND reuse fingerprints include creator identity and the existing BIND/build
compatibility inputs, excluding audit references and upstream creator bytes.
Normal CI reads only BIND provenance schema 4, BIND channel schema 5 and HA
channel schema 2. Other formats stop processing. Compatible current-format
immutable retries preserve every original byte, including earlier audit
references. Combined schema-1 catalogues
retain each component's own creator provenance, permitting creator upgrades or
origin relocation while requiring a common ABI, exact archives and authenticated
catalogue reads. Creator byte reissues no longer need hash-update PRs.

Before enabling normal release automation, perform the one-time BIND cutover
for both 26.1 and 26.7. Build fresh packages with `RP_BIND920_CHANNEL_URL` unset,
then sign and qualify them against their minimum readers. Temporary local
cutover tooling validates fresh build metadata and BIND provenance against the
selected trusted profiles and source/control revisions, validates the existing
current channels, preserves their exact recovery bytes and publishes the fresh
channels and shared feeds. Retire that tooling after both public feeds pass
verification. Historical snapshots remain
installable through `pkg`; they are not inputs to current CI. Do not rewrite
their metadata to describe a build that never occurred.

Before copying an artifact, each wrapper verifies that the target parser can
read a non-null checksum for every packaged file. The equivalent manual gate
is:

```sh
python3 .github/ci/shared/package_checksums.py \
  --pkg-command /usr/local/sbin/pkg-static path/to/package.pkg
```

Treat a missing, `(null)`, or malformed file checksum as an incompatible
artifact even when the archive and repository signatures are valid.

For example, after preparing the selected release source:

```sh
RP_UPSTREAM_METADATA=.resolver-plugins/upstream.json \
SOURCE_COMMIT="$source_commit" \
.github/ci/bind/build-os-bind-rp.sh "$series" "artifacts/$series"
```

If that command exits with status `3`, build or reuse the Resolver fallback
and invoke the plugin wrapper with `RP_BIND920_FALLBACK=yes`:

```sh
RP_UPSTREAM_METADATA=.resolver-plugins/upstream.json \
SOURCE_COMMIT="$source_commit" \
.github/ci/bind/build-bind920.sh "$series" "artifacts/$series"
RP_BIND920_FALLBACK=yes RP_UPSTREAM_METADATA=.resolver-plugins/upstream.json \
SOURCE_COMMIT="$source_commit" \
.github/ci/bind/build-os-bind-rp.sh "$series" "artifacts/$series"
```

The package and `build-metadata.txt` are written below the output directory.
The metadata records the source commit, BIND package version, OPNsense package
version, ABI, provenance values, and FreeBSD environment used for the build.

`.github/ci/shared/setup-opnsense-repository.sh <series>` is normally called by the
build runner. Use it directly only when diagnosing repository setup; it changes
the FreeBSD VM's package repository configuration.

The `Test BIND plugin` pull-request workflow discovers every active
`release/bind-rp/<series>` branch, materializes its `dns/bind/src` tree, and
runs the canonical `dns/bind/tests` suite from the pull request. It also runs
the CI helper tests for BIND profile changes, including generated
`.resolver-plugins/bind920.json` candidate PRs. Do not add a static release
matrix: a newly created release branch is included automatically.

The workflow also requires `dns/bind/pkg-descr` to change with publishable
BIND runtime, package-hook, or package-metadata changes. Test-only changes and
`PLUGIN_REVISION`-only rebuilds do not require a changelog entry.

## BIND candidate updates

The `Propose bind920 candidate` workflow is manual-only. It inspects a
FreeBSD Ports ref, defaults to `main`, and compares `dns/bind920` with the
current `.resolver-plugins/bind920.json` pin. If it finds a newer BIND 9.20
candidate, it updates the pin on a `sync/bind920/<version>-<portrevision>`
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

Check that the metadata is valid before running a full VM build:

```sh
python3 .github/ci/shared/metadata_profile.py \
  .resolver-plugins/upstream.json "$series" freebsd_release
sh -n .github/ci/bind/build-bind920.sh .github/ci/bind/build-os-bind-rp.sh \
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
