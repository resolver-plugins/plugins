# Package repository

`os-bind-rp` is published at an ABI-aware GitHub Pages path backed by verified,
signed channel bytes from `resolver-plugins/repository`. The distribution
repository also retains GitHub Release channels for transition and rollback;
source releases contain only the plugin archive and build metadata.

## Channels

Clients use one current repository URL:

```text
https://resolver-plugins.github.io/repository/pkg/${ABI}/latest
```

`pkg` expands `${ABI}` to select `FreeBSD:14:amd64` for OPNsense 26.1 or
`FreeBSD:15:amd64` for OPNsense 26.7. Every published ABI path is
self-contained. The distribution repository also keeps transition and up to
five immutable rollback Releases per supported OPNsense series:

| Purpose | Display title | Release tag | Default state |
| --- | --- | --- | --- |
| Legacy transition channel | `<series>-latest` | `pkg-<series>` | legacy clients pending migration only |
| Plugin rollback snapshot and its BIND baseline | `<series>-archive-<version>` | `pkg-<series>-os-bind-rp-<version>` | enabled only while rolling back |

Display titles are concise labels only. GitHub requires one repository-wide
`Latest` release, so publication assigns that badge to the transition channel
for the highest numeric OPNsense series. Archive releases never receive it.
These series-specific Release URLs are not used by new client configuration.

The ABI current channel and every rollback snapshot contain exactly one
`os-bind-rp` package, the matching `bind920`/`bind-tools` pair, BIND
provenance, `channel.json`, and the signed catalogue. `pkg` catalogues expose
one selected version per package name, so rollback temporarily selects a
retained snapshot URL from the same distribution repository. Publication
retains the five newest snapshots and reuses a compatible BIND pair instead
of rebuilding it for every plugin release.

All channels include the signed `pkg` catalogue and `resolver-plugins.pub`.
Clients verify both using that public key.

## Host operation

Configure the ABI-aware current plugin channel:

```sh
repo_url='https://resolver-plugins.github.io/repository/pkg/${ABI}/latest'
fetch_url="https://resolver-plugins.github.io/repository/pkg/$(pkg config ABI)/latest"
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
  enabled: yes
}
EOF
pkg update -r resolver-plugins
scripts/install-os-bind-rp.sh
```

The package post-install hook migrates only the exact documented legacy form
above when its URL ends in `releases/download/pkg-26.1` or
`releases/download/pkg-26.7`. It preserves the file's mode and owner and
atomically replaces only that known shape. A custom URL, alternate key,
disabled repository, different mirror mode, symlink, or non-regular path is
left byte-for-byte unchanged and produces a `manual migration` warning.

Systems already upgraded from 26.1 to 26.7 before receiving the migration
package cannot be repaired retrospectively. On those systems, back up and
review `/usr/local/etc/pkg/repos/resolver-plugins.conf`, change only its URL to
the literal `${ABI}` URL above, then run:

```sh
pkg update -f -r resolver-plugins
```

### Supported installer transition

Use `scripts/install-os-bind-rp.sh` for the official `os-bind` to
`os-bind-rp` transition. Before mutation, it fetches exact candidate archives,
requires non-null per-file checksums, records their SHA-256 values, creates a
local isolated repository, and dry-runs the frozen transaction. It locks the
installed `pkg` package for the transaction, attempts to restore its original
lock state on every exit, and reports failure if restoration does not succeed.
The installer does not upgrade the host package manager.
It does not enable BIND or change its user configuration. Beginning with
`os-bind-rp` 1.36_11, once that version or newer is published for the selected
OPNsense series, the package lifecycle detects whether BIND is running and, if
so, freezes each enabled dynamic primary and reverse zone, stops BIND, and
preserves the effective zone masters before the OPNsense package framework
regenerates managed templates and zone files. It atomically restores those
effective masters—including records received through RNDC updates—before
restarting BIND. It restarts BIND only when it was running before the
transaction. A package transaction fails if zone preservation or the original
running state cannot be restored and confirmed.

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
checks exact Resolver package origins, verifies ownership of archive-listed
paths, rejects null installed-file checksums, and runs a scoped
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
URL, for example `pkg-$series-os-bind-rp-1.36_2`. Dry-run and then install the
only plugin package exposed by that snapshot:

```sh
snapshot="pkg-$series-os-bind-rp-1.36_2"
cat > /usr/local/etc/pkg/repos/resolver-plugins-rollback.conf <<EOF
resolver-plugins-rollback: {
  url: "https://github.com/resolver-plugins/repository/releases/download/$snapshot",
  mirror_type: "none",
  signature_type: "pubkey",
  pubkey: "/usr/local/etc/pkg/keys/resolver-plugins.pub",
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

Development builds use pre-release tags such as `pr-123-26.7`. They are for
review testing only and are neither signed nor promoted into a stable channel.
Every development release and its Git tag are removed when the pull request
closes, whether it is merged or not. An in-flight development publisher checks
the pull request before and after upload so closure cannot leave a stale build.

## Publication

The `Publish os-bind-rp package release` workflow builds from the selected
`release/bind-rp/<series>` source branch. It first reuses a matching BIND pair
from the current distribution channel or builds the pinned pair on a verified
cache miss. The plugin is built against that exact pair.

Production publication is an explicit `workflow_dispatch` from the `master`
branch after the release-source change has been reviewed and merged. Select
`production` and the target series; a production dispatch from any other ref
is rejected. Release branches supply immutable build inputs only and never run
publication helpers. Runs are serialized per series so two promotions cannot
replace or restore the same current channel concurrently.

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

The production signer resolves and checks out a specific `master`
control-plane SHA, verifies the finished artifact's source commit, and receives
no release-source helper code with `RP_PKG_SIGNING_KEY`. It validates BIND
provenance and every security-relevant build field against trusted release
metadata, then stages identical
self-contained current and immutable rollback repositories. It generates the
signed catalogue once and copies those exact bytes to both publication paths.
The derived public key must match the key committed in this repository before
either path can proceed.

Before replacing mutable Release assets, publication downloads every prior
asset—packages, catalogues, metadata, provenance, and public key—to local
recovery storage, validates its audit checksums and expected channel structure,
and confirms the remote release did not change during preflight. Every uploaded
asset is downloaded again and compared byte-for-byte. If an upload or verification fails,
it restores each affected Release from those preserved bytes. The snapshot
and current channel have their published asset sets checked after upload;
pruning to the newest five snapshots happens only after promotion succeeds.
On a full workflow retry, an existing immutable snapshot is reused without an
upload. The signer downloads and validates that snapshot against its series,
plugin version, release-source commit, and the committed public key, then uses
those exact bytes for both staged paths. An existing current channel must also
be byte-identical; different bytes fail before any channel is changed,
preventing an older retry from moving current backward.
When the target snapshot is absent and current differs, the staged release
source must be a strict descendant of current's recorded source commit. This
allows a new promotion while rejecting stale runs even after snapshot pruning.

After promotion, a fresh FreeBSD VM configures the pinned OPNsense repository,
installs its matching core package, and runs `scripts/install-os-bind-rp.sh`
against the published ABI-aware URL. It verifies the installed
`bind-tools`, `bind920`, and `os-bind-rp` identities against the staged package
archives and requires the public `channel.json` to match the staged bytes. The
immutable source release is created only after this exact public-channel
installation succeeds, using the same verified plugin archive and build
metadata from the signed channel artifact. Retrying that source release is a
no-op when both assets are byte-identical; an existing tag with different
bytes is rejected.

Do not publish a stable channel manually from a workstation. A successful
workflow run is the release record and source of the signed catalogue.

## Verification

Before production publication, the workflow uses a disposable FreeBSD VM with
the matching official OPNsense core package to add the staged signed current
and snapshot repositories, install all three current packages, and
force-install the plugin through the snapshot path. The two staged paths
contain the same new version, so this gate proves snapshot catalogue
installability rather than a transition to an older version. After publication,
when an older retained snapshot exists, verify the actual version transition
from its public URL and confirm package identities with `pkg rquery`.

Development, staged, and published installation gates all finish by checking a
minimal isolated BIND configuration, starting and restarting BIND through its
managed service script, and querying an authoritative canary name. This catches
broken executables, linked libraries, service integration, and basic DNS
response failures before promotion.

If a signing-key rotation is required, replace `RP_PKG_SIGNING_KEY`, commit
the replacement public key, and republish every channel for every supported
series. Announce the new fingerprint; existing clients must update their key
before they can verify the replacement catalogues.
