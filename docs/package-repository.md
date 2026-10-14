# Package repository

`os-bind-rp` is published at an ABI-plus-OPNsense-series GitHub Pages path
backed by verified, signed channel bytes from `resolver-plugins/repository`.
The same feed also offers experimental HA DHCP Interface on OPNsense 26.7 after
its first publication. The distribution repository retains independent signed
component Releases for `bind-tools`, `bind920`, BIND plugin and HA DHCP. Historical
paired runtime and complete BIND rollback snapshots remain available;
source releases contain the plugin archive and build metadata.
Distribution snapshots use byte-identity checks to prohibit replacing a published
archive. They use the shared snapshot publisher for BIND and HA; repository-wide
GitHub release locking stays disabled so current channels can be updated. The
source repository separately enables GitHub's immutable-release policy.

## Channels

Clients use one current repository URL per installed OPNsense series:

```text
https://resolver-plugins.github.io/repository/pkg/${ABI}/<series>/latest
```

For example, OPNsense 26.7 on FreeBSD 15 uses:

```text
https://resolver-plugins.github.io/repository/pkg/FreeBSD:15:amd64/26.7/latest
```

Every current feed includes `meta.conf`, signed catalogue data,
`resolver-plugins.pub`, and the packages currently published for that series.
The index can contain HA DHCP or a plugin without requiring other RP package
releases. Native installation still resolves each package's ordinary dependencies.

Package workflows build, qualify and publish only their own channel. Their
concurrency groups include the package and selected series. The separate
[repository-index workflow](../.github/workflows/repository-index.yml) runs after
successful owned releases, or by an authorized manual refresh. It authenticates
existing immutable package receipts, archives, original catalogues, native file
checksums and ABI before signing the shared catalogue. It never builds a package
or changes a package Release. Only index writers share the Pages publication lock.

New shared `channel.json` records use `kind=combined`, schema 3. They preserve
original component tags and asset hashes, and record the subset selected into the
index. A standalone tools or daemon release supersedes only its corresponding
archive from a historical runtime pair. No package bytes or original receipts
are rewritten. New BIND component manifests use schema 2; historical component
schema 1 and complete-stack schema 6 remain readable. New HA manifests retain
schema 3. Generated controller and signer seals travel in immutable Actions
artifacts; release-source `.resolver-plugins` files remain unnecessary.

`pkg` expands `${ABI}` to select `FreeBSD:14:amd64` for OPNsense 26.1 or
`FreeBSD:15:amd64` for OPNsense 26.7. The explicit series segment prevents a
future series sharing the same FreeBSD ABI from being offered to an older
system. Every published ABI-plus-series path is self-contained. The
distribution repository also keeps independent components and complete rollback
Releases per supported OPNsense series:

| Purpose | Release tag | Use |
| --- | --- | --- |
| Tools current | `pkg-<series>-bind-tools` | signed `bind-tools` only |
| Tools snapshot | `pkg-<series>-bind-tools-<version>-<fingerprint>` | immutable tools variant |
| Daemon current | `pkg-<series>-bind920` | signed `bind920` only |
| Daemon snapshot | `pkg-<series>-bind920-<version>-<fingerprint>` | immutable daemon variant |
| Plugin current | `pkg-<series>-bind-plugin` | signed `os-bind-rp` only |
| Plugin snapshot | `pkg-<series>-bind-plugin-<version>-<fingerprint>` | immutable plugin variant |
| HA current | `pkg-dhcp-interface-ha-<series>` | signed HA plugin only |
| HA snapshot | `pkg-dhcp-interface-ha-<series>-<version>-<fingerprint>` | immutable HA variant |

Historical `pkg-<series>-bind-runtime` pairs and `pkg-<series>` complete stacks,
including their immutable rollback tags, remain readable and installable.
Package publication no longer updates or prunes those shared histories. Owned
immutable variants are retained; a package run cannot delete another package's
history. Complete historical snapshots remain useful for coordinated rollback.
New component snapshots roll back one package while preserving its ordinary
native dependency requirements. The shared ABI URL remains the current client feed.

BIND packages keep upstream versions, for example `bind920-9.20.26_1` and
`bind-tools-9.20.26_1`; only `os-bind-rp` uses the OPNsense series as its
package version.

All channels include the signed `pkg` catalogue and `resolver-plugins.pub`.
Clients verify both using that public key.

The BIND packages deliberately retain the normal FreeBSD names and origins
(`bind920`/`dns/bind920` and `bind-tools`/`dns/bind-tools`), so they replace
OPNsense's older BIND packages. Resolver Plugins maintains this BIND package
pin independently when a reviewed security fix, critical bugfix, or
maintainer-approved routine update is needed. An official package with a higher
normal `pkg` version still wins package version ordering and can supersede the
replacement.

## Host operation

From an OPNsense root shell, configure the shared signed repository:

```sh
curl -fsSL https://raw.githubusercontent.com/resolver-plugins/plugins/master/scripts/install-repository.sh | sh
```

The repository installer detects the OPNsense series and package ABI, verifies
the public-key fingerprint before replacing any repository files, and refreshes
the signed catalogue. Package installation is a separate step; choose a plugin
from the [installation instructions](../README.md#install-a-plugin).

Alternatively, configure the ABI-aware current channel manually:

```sh
series="$(opnsense-version -a)"
repo_url="https://resolver-plugins.github.io/repository/pkg/\${ABI}/$series/latest"
fetch_url="https://resolver-plugins.github.io/repository/pkg/$(pkg config ABI)/$series/latest"
install -d -m 0755 /usr/local/etc/pkg/keys /usr/local/etc/pkg/repos
fetch -o /usr/local/etc/pkg/keys/resolver-plugins.pub "$fetch_url/resolver-plugins.pub"
test "$(sha256 -q /usr/local/etc/pkg/keys/resolver-plugins.pub)" = \
  bd89d6f91807c71f8a744532c9ce2f97e9590f8858ac779bfb2f23c10804e07e || exit 1
cat > /usr/local/etc/pkg/repos/resolver-plugins.conf <<EOF
resolver-plugins: {
  url: "$repo_url",
  mirror_type: "none",
  signature_type: "pubkey",
  pubkey: "/usr/local/etc/pkg/keys/resolver-plugins.pub",
  priority: 20,
  enabled: yes
}
EOF
pkg update -r resolver-plugins
```

The BIND package installs `ResolverPlugins.sh`, an OPNsense firmware repository
provider. It migrates only exact managed Resolver repository shapes: legacy
GitHub Release URLs, the older ABI-only URL, or a prior ABI-plus-series URL.
It reads `opnsense-version -a`, writes the literal `${ABI}` URL for that
series, preserves the file's mode and owner, and atomically replaces only that
known shape. A custom URL, alternate key, disabled repository, different
mirror mode, symlink, or non-regular path is left byte-for-byte unchanged and
produces a `manual migration` warning. Managed entries use repository-local
priority 20; the provider recognizes both the old body and the priority-aware
body. OPNsense repository settings and global solver policy are unchanged.
Native dependency selection can choose a newer compatible BIND pair from
OPNsense on a fresh installation. Qualification authenticates the selected pair
and checks its versions and installed files.

The package also installs `bindRepositoryReconcile.py`. The upgrade hook only
marks reconciliation pending and returns; it performs no network or package
operations. The start hook launches the worker in the background after boot.
The worker validates the exact managed repository, package ABI, candidate
identity, installed identity, and dry-run plan before any live package
mutation. A failure retains the pending marker and does not abort OPNsense upgrade or boot.

In short, the upgrade hook only marks reconciliation pending and the start
hook launches the validated retry after boot.

Systems already upgraded from 26.1 to 26.7 before receiving the migration
package cannot be repaired retrospectively. On those systems, back up and
review `/usr/local/etc/pkg/repos/resolver-plugins.conf`, change only its URL to
the literal `${ABI}` plus series URL above, then run:

```sh
pkg update -f -r resolver-plugins
```

### Native plugin installation

For a fresh installation, configure the repository and use
`pkg install os-bind-rp`. The plugin's ordinary dependency selects missing BIND,
which in turn selects its tools. An already installed compatible OPNsense pair
may remain. The recorded dependency version identifies the original build;
the source formula defines the minimum compatible BIND version. Qualification
records the actual provider and installed file maps. The pinned pkg reader can
retain an older installed dependency even when a compatible candidate exists.
The source-generated read-only guard prevents registering an unsupported
plugin, but command exit0 alone is not evidence that installation succeeded.

For official `os-bind` replacement or an installed BIND below the plugin's
minimum, use the retained migration installer:

```sh
curl -fsSL https://raw.githubusercontent.com/resolver-plugins/plugins/master/scripts/install-os-bind-rp.sh | sh
```

The installer reads core and BIND requirements from the authenticated plugin
archive, verifies the installed core, retains a compatible pair, and upgrades
an older pair before replacing the plugin. Native file ownership resolution
replaces official `os-bind` through the shared `bind.inc` payload. That removal
can happen before P's guard, making migration preflight necessary. Native
manifest `conflicts` entries are installed database relationships and do not
express the prohibited-name policy enforced by the source guard.

The installer fetches its standalone Python prerequisite reader from this
repository and checks its expected SHA-256 before execution. Keep the shell
installer and reader in the same commit; when changing the reader, update its
expected digest in the installer.

Before mutation, the installer fetches exact candidate archives,
requires non-null per-file checksums, records their SHA-256 values, creates a
local isolated repository, and dry-runs the frozen transaction. It locks the
installed `pkg` package for the transaction, attempts to restore its original
lock state on every exit, and reports failure if restoration does not succeed.
The installer does not upgrade the host package manager.
It does not enable BIND or change its user configuration. Package-transaction
zone preservation begins with `os-bind-rp` 1.36_11 on OPNsense 26.1,
1.36_4 on OPNsense 26.7, and all series-versioned packages beginning at
revision 1. The package lifecycle detects whether BIND is
running and, if so, freezes each enabled dynamic primary and reverse zone,
stops BIND, and preserves the effective zone masters before the OPNsense
package framework regenerates managed templates and zone files. It atomically
restores those effective masters—including records received through RNDC
updates—before restarting BIND. It restarts BIND only when it was running
before the transaction. A package transaction fails if zone preservation or
the original running state cannot be restored and confirmed.

That transaction boundary is distinct from the ordinary configure/restart
guard. During a normal plugin configure, the runtime stop helper accepts an
already-stopped service only after the rc script confirms `status=1`; a
running, indeterminate, or otherwise failed stop prevents template and zone
regeneration entirely.

Immediately before the first package install, the script creates a mode-0700
state directory below `/var/backups`, preserves a mode-retaining configuration
backup as `config.xml.bak`, and records package inventory and candidate
hashes. It also recreates the currently installed BIND/plugin packages in a
validated local recovery repository and proves an exact recovery dry run
before the first live package transaction. Set `RP_STATE_DIRECTORY` to choose
an explicit new directory or `RP_BACKUP_ROOT` to change only its parent. A
caller-supplied `RP_TEMPORARY_DIRECTORY` is never deleted. On failure, the script retains and
prints both the durable state directory and temporary verified archives for
diagnosis; on success, it removes only temporary storage that it created.
If a live transaction fails, the diagnostic names the recovery repository and
prints an exact dry-run command; stop BIND and review that dry run before an
approved recovery, then restore `config.xml.bak`, validate configuration, and
restart BIND. If the package lifecycle had stopped a running service, it
attempts to restore that service before the installer reports the failure;
confirm the actual service state before taking recovery action.

After replacement, the installer requires the official package to be absent,
checks exact Resolver package origins and retained BIND identities, verifies
ownership of paths listed in archives installed by the transaction, and requires
complete native checksums for newly installed own packages. A retained upstream
pair must preserve its complete native file records, identities and owners, plus
the measured full file maps from genuine native recovery archives created before
and after installation. These measured digests describe the existing files;
they do not replace missing supplier digests in the package database. A scoped
`pkg check -s` also runs. The operator must still validate the generated configuration,
all managed zones, authoritative answers, and the service state after package
installation.

On a high-availability pair, upgrade the backup node first. Confirm its CARP
role, generated configuration, dynamic and reverse zones, authoritative DNS
answers, and BIND logs before upgrading the master node. Do not advance to the
master while the backup node has an unloaded zone, a failed service-state
restoration, or new package/runtime errors. With lifecycle-enabled packages,
the package-managed stop and restart creates a brief DNS interruption on each
node, so keep the peer healthy throughout its partner's transaction.

### Rollback

Back up the OPNsense configuration before changing plugin versions. The
supported installer creates this configuration backup automatically; for a
manual rollback operation, create another explicit copy:

```sh
cp /conf/config.xml "/conf/config.xml.os-bind-rp.$(date +%Y%m%d%H%M%S).bak"
```

Configure `resolver-plugins-rollback` with the same key and the exact snapshot
URL. Snapshot tags include the plugin version and both full component fingerprints,
for example
`pkg-26.7-os-bind-rp-26.7_5-bind-<runtime-fingerprint>-plugin-<plugin-fingerprint>`.
Each fingerprint is 64 hexadecimal characters. Copy the complete tag from the
`resolver-plugins/repository` GitHub Releases page; the runtime fingerprint also
appears in the preserved `bind920-provenance.json`. Historical complete-stack
tags remain installable with their original bytes. Then dry-run and
install the only plugin package exposed by that snapshot:

```sh
snapshot="pkg-26.7-os-bind-rp-26.7_5-bind-<runtime-fingerprint>-plugin-<plugin-fingerprint>"
cat > /usr/local/etc/pkg/repos/resolver-plugins-rollback.conf <<EOF
resolver-plugins-rollback: {
  url: "https://github.com/resolver-plugins/repository/releases/download/$snapshot",
  mirror_type: "none",
  signature_type: "pubkey",
  pubkey: "/usr/local/etc/pkg/keys/resolver-plugins.pub",
  priority: 20,
  enabled: yes
}
EOF
pkg update -r resolver-plugins-rollback
pkg install -n -r resolver-plugins-rollback os-bind-rp
pkg install -f -r resolver-plugins-rollback os-bind-rp
pkg query -e '%n = os-bind-rp' '%n-%v'
configctl template reload OPNsense/Bind || true
configctl service restart bind || true
rm -f /usr/local/etc/pkg/repos/resolver-plugins-rollback.conf
```

If configuration, template generation, or service validation fails, restore
the saved configuration and run the exact latest-channel install command:

```sh
pkg install -f -r resolver-plugins os-bind-rp
```

The rollback snapshot is not a separate product feed; leave it disabled
outside an explicit rollback to keep ordinary upgrades on the ABI-aware URL.

Runtime version PRs build unsigned Actions artifacts for every registered
compatibility series. They receive no signing or publication credentials.
Plugin manual development builds retain their unsigned `pr-<number>-<series>-bind-plugin`
Release previews and closure cleanup; they never enter the stable repository.

## Publication

The [tools](../.github/workflows/bind-tools-release.yml) and
[daemon](../.github/workflows/bind920-release.yml) workflows each own their version
approval under `packages/<name>/Makefile`. Shared runtime job code is an invoked
helper, with no autonomous trigger. Each package's PR builds unsigned native
previews; merging its newer version approval selects only that package's changed
series. Neither runtime has a daily schedule. Initial approval-file creation and
missing new standalone tags do not cause migration rebuilds: selection can read
the historical pair's original approval.

The [BIND plugin](../.github/workflows/bind-plugin-release.yml) and
[HA DHCP](../.github/workflows/ha-dhcp-interface-release.yml) workflows select only
their own reviewed code changes, separately for each series. CI, metadata,
framework, environment, runtime approvals and other package changes alone do not
rebuild either plugin. Unchanged production inputs skip, including manual runs.
Eligible runs then discover and seal the complete current build environment.

BIND plugin controllers freeze `bind-build-dependency.json` from the official
OPNsense Ports object already selected for the creator's numeric tools tag.
The builder installs that exact ordinary BIND identity from the authenticated
OPNsense feed; signer metadata and archive dependency checks bind it to the sealed
controller record. It does not fetch RP runtime releases or approvals. HA builds
and publishes without fetching any BIND packages. Daemon builds create their
native tools prerequisite internally for qualification and never publish it.

The `RP_PKG_SIGNING_KEY` GitHub Actions secret contains the base64-encoded
private key. It is decoded only in the disposable FreeBSD VM, used by `pkg
repo`, and removed before the VM copyback. The committed public key is
`docs/package-repository/resolver-plugins.pub`; its SHA-256 fingerprint is
`bd89d6f91807c71f8a744532c9ce2f97e9590f8858ac779bfb2f23c10804e07e`.

The source repository must define this Actions variable and these Actions
secrets before production:

- `RP_PKG_SIGNING_KEY`: the base64-encoded private package-signing key. It is
  exposed only to the FreeBSD signing job.
- `RP_DISTRIBUTION_APP_ID`: the non-secret numeric ID of the organization-owned
  Resolver Plugins publisher GitHub App. Store it as an Actions repository
  variable.
- `RP_DISTRIBUTION_APP_PRIVATE_KEY`: the complete PEM private key for that App.
  Store it as an Actions repository secret and rotate it through the App's
  credential settings.

The publisher App is owned by `resolver-plugins`, has webhooks disabled, has
only `Contents: write`, and is installed only on
`resolver-plugins/repository`. The publication job exchanges its ID and
private key for a short-lived installation token; no personal access token is
stored. A missing App variable, missing private key, or suspended installation
fails the publication job before channel mutation. Never replace the App
credential with a broad source-repository or session credential.

Production privileged jobs check out the literal trusted workflow commit and
bind the controller's selected control identity to it before using keys or App
credentials. Producer artifacts contain their owned archives and original build records.
The daemon also carries an explicitly named internal tools archive for native
qualification; the signed owned channel excludes it. Its digest is sealed in the
signer artifact. Signers authenticate the selected reader and every owned native
checksum before decoding the key, then generate the owned catalogue with the
selected creator. A key must derive the committed public key.

The repository index has its own controller, isolated signing VM, native reader
qualification and App-authenticated publisher. Its frozen receipt map permits
missing unrelated packages and protects all selected originals. It rechecks that
map before a compare-and-advance Pages write. If an owned package advances while
the index is qualifying, refresh the index against the newer receipts; package
publication itself remains complete.

Before replacing mutable Release assets, publication downloads every prior
asset—packages, catalogues, metadata, provenance, and public key—to local
recovery storage, validates its audit checksums and expected channel structure,
and confirms the remote release did not change during preflight. Every uploaded
asset is downloaded again and compared byte-for-byte. If an upload or verification fails,
it restores each affected Release from those preserved bytes. The snapshot
and current channel have their published asset sets checked after upload.
Immutable snapshots already completed by an interrupted attempt are retained;
a retry reuses only a fully published, byte-validated snapshot. A matching
immutable snapshot can safely advance an older mutable current channel.
Partially populated drafts remain a distinct recoverable transaction state,
not a valid cache hit. Stale owned package selections fail before promotion. If the Pages update fails, the previous feed remains intact while
completed Release snapshots remain available. Recover an owned release using its exact original
signed component; a new build is not a
publication retry. Recovery artifacts preserve prior mutable bytes.
Historical complete snapshots remain unchanged and installable through `pkg`.

For a partial publication failure, use `gh run rerun RUN_ID --failed` within
GitHub's 30-day rerun window. Runtime controller, producer, signed repository and
recovery artifacts retain those original immutable IDs and external seals for
30 days. Rerunning failed jobs uses the original qualified signed tree; a whole
build rerun or same-pin manual dispatch can produce different bytes and is not
this recovery operation. Expired artifacts or an advanced owned current channel stop stale promotion.
Never overwrite an immutable snapshot to make a rebuilt package fit.

Before promotion, a clean FreeBSD VM qualifies the owned signed current and
snapshot catalogues with the selected minimum reader. It authenticates fetched
archive bytes, installs native dependencies, checks the exact owned identity,
file maps, ownership and `pkg check -s`, and checks the plugin's source binding
or BIND daemon canary. Tools qualification exercises the installed native clients.
After promotion the same checks use the package's public Release URLs, without
requiring the shared index to finish first. Plugin source releases follow the
successful public owned-channel check and preserve the original archive and build
records. Their immutable tags include the full build fingerprint. Runtime package
publication never creates a plugin source release.

Do not publish a stable channel manually from a workstation. A successful
workflow run is the release record and source of the signed catalogue.

## Verification

Run the focused checks in [Building](building.md) before changing release code.
The unprivileged helper suite protects ownership isolation, sealed provenance,
minimum-reader compatibility, immutable snapshots, source-only series selection,
and publication authority. Native package workflows require staged and public
owned-channel installation checks before their success can refresh the index.
The index separately verifies the signed aggregate through the minimum reader.

These VM checks cover packages and catalogues. Appliance acceptance still needs
real OPNsense service state, zone preservation, generated configuration and
HA behavior. Retain the native evidence for a release and report its actual run
outcome; local synthetic fixtures cannot establish native installation compatibility.

## HA DHCP Interface

The experimental HA DHCP Interface plugin joins the shared 26.7 feed after its
first publication. It uses the same signing key and publisher App, while retaining
independent signed current and rollback Release channels in the distribution
repository. Its signer validates only its own producer, controller inputs,
native checksums and public key. It publishes only the HA current and immutable
snapshot channels. Its native checks install from those own channels; the shared
index incorporates that publication independently.

It follows the BIND plugin's per-series release model: the source branch owns
the package version (`26.7_1` initially); trusted master CI discovers upstream
inputs and owns build and publication code. HA uses the same discovery and
per-series comparison as BIND, without a tracked upstream profile or BIND release
branch as its compatibility source.
Publication follows staged and public native installation checks and does not
deploy to appliances.

Bump `PLUGIN_REVISION` for new HA package behavior. Environment changes alone do
not select an HA rebuild. An explicitly authorized build variant retains its
reviewed version and uses a distinct immutable snapshot keyed by the build
fingerprint. Retries reuse that exact variant and never overwrite another
snapshot. Older tools or core inputs cannot replace a newer current channel.
Test-only and documentation changes do not start package publication. As with
BIND, ordinary dependencies come from the selected series' authenticated latest
feed; the exact numeric tools tag still selects and verifies the package creator.

Source release titles use `os-dhcp-interface-ha <series> <plugin_version>`,
and new source tags use `os-dhcp-interface-ha-<series>-<plugin_version>-<build_fingerprint>`.
Existing source tags retain their original names because published source
releases are immutable. Distribution titles use the same package name, with
`latest` for the current channel. The distribution lookup tags remain
`pkg-dhcp-interface-ha-<series>` and
`pkg-dhcp-interface-ha-<series>-<plugin_version>-<build_fingerprint>` for new snapshots.
Legacy snapshots retain the series/version form. The current feed lookup tag and
end-user package names stay the same.

For the initial cutover, register the prepared `release/dhcp-interface-ha/26.7`
source before merging the control-plane change. This lets the PR test matrix
exercise it and the master publication select it. Existing `0.2_*` source
releases, signed snapshots and current-channel receipts remain readable and are
not rewritten. `26.7_1` advances the existing package identity through `pkg`.
