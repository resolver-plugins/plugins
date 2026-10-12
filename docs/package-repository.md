# Package repository

`os-bind-rp` is published at an ABI-plus-OPNsense-series GitHub Pages path
backed by verified, signed channel bytes from `resolver-plugins/repository`.
The same feed also offers experimental HA DHCP Interface on OPNsense 26.7 after
its first publication. The distribution repository retains independent signed
component Releases for the BIND runtime, BIND plugin and HA DHCP, plus complete
BIND stack rollback snapshots;
source releases contain the plugin archive and build metadata.

## Channels

Clients use one current repository URL per installed OPNsense series:

```text
https://resolver-plugins.github.io/repository/pkg/${ABI}/<series>/latest
```

For example, OPNsense 26.7 on FreeBSD 15 uses:

```text
https://resolver-plugins.github.io/repository/pkg/FreeBSD:15:amd64/26.7/latest
```

Every current feed includes `meta.conf`, signed catalogue data, `resolver-plugins.pub`,
the pinned `bind-tools`/`bind920` package pair, and the current `os-bind-rp-*.pkg`.
After HA DHCP publication, the 26.7 feed also contains `os-dhcp-interface-ha`.
OPNsense 26.1 continues to offer BIND only. Existing client URLs and trust keys stay
the same; the additional plugin is installed only when explicitly selected.

The runtime, plugin and HA production workflows assemble the feed through the
shared catalogue helpers. Each writer retains its peers' exact published
archives, verifies input catalogue signatures, package identities, ABI,
provenance and checksums with the selected minimum reader, then signs one
combined catalogue. All three share the `package-release` concurrency group
with cancellation disabled and the full native queue enabled. Queue order is
not a dependency guarantee. They recheck sealed component receipts before
publication and the atomic `gh-pages` update.
Once a component is present, a missing Release cannot silently remove it from
the feed. A stale staged aggregate must be rebuilt against current components.

The shared `channel.json` uses `kind=combined`, schema 2, and records the component
tags, original asset hashes, selected package hashes and aggregate asset hashes.
Component metadata is preserved as `bind_runtime-*`, `bind_plugin-*` and
`dhcp-*` sidecars. BIND component manifests use schema 1; complete BIND stack
manifests use schema 6. HA retains its schema-2 manifest and existing tags.
The external `composition-inputs.json` seal lives beside the catalogues in the
signer artifact and its SHA-256 travels as a job output. It is not a catalogue
asset. See [HA DHCP Interface](#ha-dhcp-interface) for its publication workflow.

`pkg` expands `${ABI}` to select `FreeBSD:14:amd64` for OPNsense 26.1 or
`FreeBSD:15:amd64` for OPNsense 26.7. The explicit series segment prevents a
future series sharing the same FreeBSD ABI from being offered to an older
system. Every published ABI-plus-series path is self-contained. The
distribution repository also keeps independent components and complete rollback
Releases per supported OPNsense series:

| Purpose | Release tag | Use |
| --- | --- | --- |
| Runtime current | `pkg-<series>-bind-runtime` | producer intake; signed pair only |
| Runtime snapshot | `pkg-<series>-bind-runtime-<bind_version>-<runtime_fingerprint>` | immutable runtime identity |
| Plugin current | `pkg-<series>-bind-plugin` | producer intake; signed plugin only |
| Plugin snapshot | `pkg-<series>-bind-plugin-<plugin_version>-<plugin_fingerprint>` | immutable plugin identity |
| Complete BIND current | `pkg-<series>` | existing transition clients |
| Complete BIND rollback | `pkg-<series>-os-bind-rp-<plugin_version>-bind-<runtime_fingerprint>-plugin-<plugin_fingerprint>` | explicit self-contained rollback |

Display titles are concise labels only. GitHub requires one repository-wide
`Latest` release, so publication assigns that badge to the transition channel
for the highest numeric OPNsense series. Archive releases never receive it.
These series-specific Release URLs are not used by new client configuration.

The complete BIND current channel and every new BIND rollback snapshot contain exactly one
series-versioned plugin package such as `os-bind-rp-26.1_1` or
`os-bind-rp-26.7_1`, the matching `bind920`/`bind-tools` pair, BIND
provenance, `channel.json`, and the signed catalogue. `pkg` catalogues expose
one selected version per package name, so rollback temporarily selects a
retained snapshot URL from the same distribution repository. Runtime-only and
plugin-only component snapshots are CI inputs, not complete plugin rollback
repositories. Reusing a component preserves its archives, metadata and original
creator audit bytes; composition records the new catalogue writer separately.

Retention keeps the five newest complete BIND stack snapshots, including
historical opaque snapshots, and protects the current pair and every component
referenced by a retained stack. It then keeps five additional unreferenced
plugin snapshots and protects every original `built_against_runtime` snapshot
referenced by a retained plugin, followed by five additional unreferenced
runtime snapshots. BIND pruning runs only after public verification, and for the
plugin writer after source-release success.

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

After configuring the repository, use `pkg install os-bind-rp`. Native package
dependencies select BIND and its tools. Fresh installs must select this feed's
approved pair; an already installed compatible OPNsense pair may remain because
pkg preserves repository affinity. Qualification records the actual provider
and installed file maps. A below-minimum BIND or core state must upgrade or
refuse the plugin, never install an unsupported state. The reviewed source
package emits its official-plugin conflict and core dependency, and its native
pre-install hook checks the actual core version without mutating packages.

The custom installer is retained only until the replacement native acceptance
cases pass on both supported series. Before mutation, that retained harness
fetches exact candidate archives,
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
ownership of paths listed in archives installed by the transaction, rejects null
installed-file checksums, and runs a scoped
`pkg check -s`. The operator must still validate the generated configuration,
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

Development builds use pre-release tags such as `pr-123-26.7-bind-runtime` and
`pr-123-26.7-bind-plugin`. They are for
review testing only and are neither signed nor promoted into a stable channel.
Every development release and its Git tag are removed when the pull request
closes, whether it is merged or not. An in-flight development publisher checks
the pull request before and after upload so closure cannot leave a stale build.

## Publication

The [runtime workflow](../.github/workflows/bind-runtime-release.yml) builds
a fresh approved runtime pair on every selected run. The
[plugin workflow](../.github/workflows/bind-plugin-release.yml) builds or reuses
only `os-bind-rp` from the selected `release/bind-rp/<series>` source. Its
controller requires an already published runtime matching the approved pin and
source package's minimum, freezes the complete receipt, and authenticates that
pair before building. It never builds or dispatches the runtime workflow.
For a new series or pin, publish the runtime first and retry the plugin if it
was selected before that publication completed. A runtime bootstrap can publish
its isolated pair before a plugin exists; it cannot expose an incomplete
combined feed. Once a complete feed exists, every writer retains all registered
peers or fails.

Automatic runtime production starts only when the approved `RP_BIND_VERSION` / `RP_BIND_PORTS_COMMIT` pair
changes on `master`, targeting the newest numeric `release/bind-rp/<series>`
branch. Plugin source, its helpers and workflow, and the `Mk`, `Scripts` and
`Templates` framework trigger only the plugin workflow. Shared BIND helpers, installer and committed key can
also trigger the plugin, without starting runtime production. Relevant merges
into a release-source branch start only the plugin workflow for that series.
Runtime helper and workflow changes run PR tests without starting a runtime
release. HA-only helpers, tests, documentation and discovery tooling do not
trigger BIND publication. `workflow_dispatch` remains available for development
builds and explicit production series selection; use the runtime
workflow directly to publish the approved pair for another series. A production
dispatch from any ref other than `master` is rejected. Release branches supply
immutable build inputs only and never run publication helpers. Publication runs
share one global lock so automatic and manual promotions cannot replace or
restore the same current channel concurrently.

The `Propose bind920 candidate` workflow may open PRs that update only the
two runtime approval fields in the plugin package Makefile. Those PRs provide review evidence and CI status and do not
alter a stable package channel while open. Merging a package-affecting candidate
to `master` starts runtime production for the newest numeric release branch;
maintainers can still dispatch another series explicitly.

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
credentials. Producer artifacts contain only their owned raw archives and
original build records. The signer independently fetches reused components and
the plugin's frozen runtime, authenticates them with the sealed reader role,
and checks their exact receipts before decoding the signing key. Composition
separately authenticates retained peers and checks approved compatibility before
generating its signed catalogues. It preserves original provenance, stages the
owned component and complete BIND stack, and seals all retained composition
inputs outside the catalogues. Each catalogue is generated once for its current
and immutable publication paths.
The derived public key must match the key committed in this repository before
either path can proceed.

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
not a valid cache hit. Stale selected peers fail before promotion and must be
recomposed. If the Pages update fails, the previous feed remains intact while
completed Release snapshots remain available. Recover using the exact original
owned signed component and then-current peer receipts; a new build is not a
publication retry. Recovery artifacts preserve prior mutable bytes.
Historical complete snapshots remain unchanged and installable through `pkg`.

For a partial publication failure, use `gh run rerun RUN_ID --failed` within
GitHub's 30-day rerun window. Runtime controller, producer, signed repository and
recovery artifacts retain those original immutable IDs and external seals for
30 days. Rerunning failed jobs uses the original qualified signed tree; a whole
build rerun or same-pin manual dispatch can produce different bytes and is not
this recovery operation. Expired artifacts or changed peers stop promotion.
Never overwrite an immutable snapshot to make a rebuilt package fit.

After promotion, a fresh FreeBSD VM configures the selected OPNsense core and
minimum pkg reader, then runs repository setup and native
`pkg install -y os-bind-rp` with all three BIND/plugin packages initially absent. Dependencies
are not preinstalled for this checkpoint. It records the native dry run,
transaction, providers and file maps, compares the plugin with the qualified
archive, checks source-defined floors/conflicts, and runs the existing named,
rndc and DNS canaries. Separate clean cases exercise official-plugin replacement
with an eligible upstream pair and below-minimum runtime/core refusal or upgrade.
The public `channel.json` must match the qualified bytes. The
immutable source release is created only by the plugin workflow, after this
verified public-channel installation succeeds. Its tag is
`os-bind-rp-<series>-<plugin_version>-plugin-<plugin_fingerprint>` and its narrow
asset set contains the original plugin archive and
build/provenance records, including the raw source `plugin-package.mk`, from the
signed component. A runtime-only publication
does not mint a source release. Retrying a source release with the exact same
assets is a no-op; an existing tag with different bytes is rejected.

Do not publish a stable channel manually from a workstation. A successful
workflow run is the release record and source of the signed catalogue.

## Verification

Before production publication, the shared BIND verifier uses a disposable FreeBSD VM with
the matching official OPNsense core package to add the staged signed current
and snapshot repositories, install all three current packages, and
force-install the complete plugin/runtime pair through the snapshot path. A
runtime bootstrap verifies only its owned signed pair. Before publishing a new
complete stack, native qualification checks and installs the staged current and
its exact paired snapshot. After publication, a fresh public gate authenticates
those published repositories against the qualified bytes, runs the ordinary native
installation checkpoint against the current feed, and checks installed identities and file
checksums. Historical snapshots remain available for explicit administrator rollback.

Development, staged, and published installation gates all finish by checking a
minimal isolated BIND configuration, starting and restarting BIND through its
managed service script, and querying an authoritative canary name. This catches
broken executables, linked libraries, service integration, and basic DNS
response failures before promotion.

The published installation gate also checks the installer's mode-0700 durable
state directory and the configuration backup's original bytes and mode. It keeps
the caller-owned temporary archives, compares their recorded hashes with both
the fetched and staged packages, and observes the isolated repository dry run
before the installer's live transaction. Archive ownership checks cover packages
installed from those archives. Retained BIND packages must keep their original
identities, and all three installed BIND/plugin packages pass file checksum checks.
These observations qualify only the selected series in a successful native run;
both 26.1 and 26.7 need successful runs before retiring the corresponding local
official-replacement regression test.
These stock FreeBSD VM checks cover packages, catalogues and BIND runtime;
appliance UI and PHP plugin migrations require separate appliance qualification.

If a signing-key rotation is required, replace `RP_PKG_SIGNING_KEY`, commit
the replacement public key, and republish every channel for every supported
series. Announce the new fingerprint; existing clients must update their key
before they can verify the replacement catalogues.

## HA DHCP Interface

The experimental HA DHCP Interface plugin joins the shared 26.7 feed after its
first publication. It uses the same signing key and publisher App, while retaining
independent signed current and rollback Release channels in the distribution
repository. Its signer fetches the published runtime and plugin separately and
authenticates both with its selected minimum reader before creator or key use.
It preserves HA's own producer, metadata, snapshot identity and source prerelease,
and signs only the combined feed alongside its own channel. An externally sealed
three-component composition is checked before native installation and before
and after HA promotion; HA never promotes BIND component or stack tags.
Existing Resolver Plugins clients keep the same
feed URL and public key.

The [HA DHCP release workflow](../.github/workflows/ha-dhcp-interface-release.yml)
runs automatically when the plugin's package version increases on `master`;
manual dispatch from `master` supports recovery. It publishes experimental
`os-dhcp-interface-ha` packages for 26.7 / amd64 after staged and public
installation checks. Publication does not deploy to appliances.
