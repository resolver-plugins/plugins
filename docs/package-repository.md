# Package repository

Resolver Plugins publishes two OPNsense plugins, `os-bind-rp` and
`os-dhcp-interface-ha`, as signed `pkg` archives. Clients read one signed feed
per OPNsense series from GitHub Pages. The archives themselves are GitHub
Releases in `resolver-plugins/repository`. How the Releases are built and
published is described in [Building](building.md).

## Feed

Each installed OPNsense series has one feed URL:

```text
https://resolver-plugins.github.io/repository/pkg/${ABI}/<series>/latest
```

`pkg` expands `${ABI}` to the running base, `FreeBSD:14:amd64` for OPNsense 26.1
or `FreeBSD:15:amd64` for OPNsense 26.7. For example, OPNsense 26.7 uses:

```text
https://resolver-plugins.github.io/repository/pkg/FreeBSD:15:amd64/26.7/latest
```

The series segment keeps a newer series from being offered to an older system
that shares its FreeBSD ABI. Client configuration keeps `${ABI}` literal.

A feed directory contains:

- the current `.pkg` archive of each plugin published for that series;
- the signed catalogue that `pkg repo` writes for those archives;
- `resolver-plugins.pub`, the public signing key;
- `channel.json`, which lists each component's current Release tag, version and
  archive names.

The index refreshes a series' feed after a plugin publishes a new current
archive. It signs the catalogue with the package-signing key, replaces the
feed directory on the `gh-pages` branch of `resolver-plugins/repository` in one
commit, and compares the public files with the staged ones before it finishes.
A plugin that has not published for a series is simply absent from that
series' feed, and one plugin's publication never rebuilds or changes the
other's archive.

Frozen legacy content stays as it is and is never written by the current
workflows:

- the Pages feed `pkg/${ABI}/latest` and the Release feeds `pkg-26.1` and
  `pkg-26.7`, which older clients still read until they migrate;
- the runtime Releases `pkg-<series>-bind-runtime` and their versioned Releases;
- the older fingerprinted versioned Releases.

Runtime packages (`bind920`, `bind-tools`) that a series' feed already lists
stay available in it, outside the plugins' retention. They are no longer built
or updated. BIND now normally comes from OPNsense's own packages (see
[Host setup](#host-setup)).

## Releases

Each plugin has a current Release and one versioned Release per revision in
`resolver-plugins/repository`, plus a matching source Release in
`resolver-plugins/plugins`. Versions are `<series>_<n>`, for example `26.7_3`.

| Plugin | Current Release | Versioned Release | Source Release and tag |
| --- | --- | --- | --- |
| `os-bind-rp` | `pkg-<series>-bind-plugin` | `pkg-<series>-bind-plugin-<series>_<n>` | `os-bind-rp-<series>-<series>_<n>` |
| `os-dhcp-interface-ha` (pre-release) | `pkg-dhcp-interface-ha-<series>` | `pkg-dhcp-interface-ha-<series>-<series>_<n>` | `dhcp-interface-ha-<series>-<series>_<n>` |

- The current Release holds only the newest archive and its `build-record.json`.
  It advances only when a new version is higher than the one it holds.
- A versioned Release holds the archive and build record of one version and is
  created once. Publishing a different source under an existing versioned tag
  fails.
- The source Release carries the same archive and record. Its tag points at the
  prepared commit on the plugin's release branch (`release/bind-rp/<series>` or
  `release/dhcp-interface-ha/<series>`; see
  [Fork model](fork-model.md#branches)).
- Retention keeps the current Release, the newest versioned Release and the one
  before it for each plugin and series. When the index finishes, it deletes the
  versioned Release two versions back, its tag and its source Release. The
  source tag stays.

`build-record.json` records the package name, origin, version and ABI; the
series and FreeBSD release; the release branch base, prepared commit and master
commit; the workflow commit; the `opnsense/tools`, `opnsense/core` and
`opnsense/plugins` commits used; the upstream package commit; the `pkg`
version; the previous versioned Release tag; and the run URL.

## Host setup

From an OPNsense root shell, configure the signed repository once for both
plugins:

```sh
curl -fsSL https://raw.githubusercontent.com/resolver-plugins/plugins/master/scripts/install-repository.sh | sh
```

[`scripts/install-repository.sh`](../scripts/install-repository.sh) reads the
series from `opnsense-version -a` and the ABI from `pkg config ABI`, downloads
`resolver-plugins.pub` from that series' feed and checks its SHA-256 fingerprint
before writing anything. It then writes the repository file
`/usr/local/etc/pkg/repos/resolver-plugins.conf`, installs the key in
`/usr/local/etc/pkg/keys/`, and runs `pkg update -r resolver-plugins`. It
installs no packages. The public key fingerprint is:

```text
bd89d6f91807c71f8a744532c9ce2f97e9590f8858ac779bfb2f23c10804e07e
```

The [README](../README.md#configure-the-package-repository) shows the same
setup by hand. Install a plugin with `pkg`; see
[Install a plugin](../README.md#install-a-plugin):

```sh
pkg install os-bind-rp
pkg install os-dhcp-interface-ha
```

`os-bind-rp` uses OPNsense's native BIND. It declares a minimum BIND version
(`bind920 >= 9.20.26`) and a minimum OPNsense version (`26.1.11_10`), and
`pkg` installs any enabled repository's `bind920` that meets the minimum,
normally OPNsense's own. A newer installed BIND is kept; a `bind920` below the
minimum is upgraded or the installation fails natively. The workflows never
build a private BIND. The plugin conflicts with the official `os-bind`, so
installing it replaces `os-bind`.

Installing a plugin does not enable BIND or change its configuration. Validate
the generated configuration, zones, authoritative answers and service state
after installing or upgrading.

## Series upgrades

A plugin installed from this feed moves with the system to the next OPNsense
series in three steps. Each plugin carries its own files, so the two work
independently and can be installed together.

| Role | `os-bind-rp` | `os-dhcp-interface-ha` |
| --- | --- | --- |
| Upgrade hook | `/usr/local/etc/rc.syshook.d/upgrade/50-bind-rp-repository` | `/usr/local/etc/rc.syshook.d/upgrade/50-dhcp-interface-ha-repository` |
| Repository provider | `/usr/local/opnsense/scripts/firmware/repos/ResolverPlugins.sh` | `/usr/local/opnsense/scripts/firmware/repos/ResolverPluginsHA.sh` |
| Start hook | `/usr/local/etc/rc.syshook.d/start/50-bind-rp-repository` | `/usr/local/etc/rc.syshook.d/start/50-dhcp-interface-ha-repository` |
| Reconcile worker | `/usr/local/opnsense/scripts/OPNsense/Bind/bindRepositoryReconcile.sh` | `/usr/local/opnsense/scripts/dhcp_interface_ha/repository_reconcile.sh` |
| Pending marker | `/var/db/os-bind-rp/repository-reconcile.pending` | `/var/db/os-dhcp-interface-ha/repository-reconcile.pending` |
| Log | `/var/log/os-bind-rp-reconcile.log` | `/var/log/os-dhcp-interface-ha-reconcile.log` |

1. The upgrade hook runs during the firmware upgrade. It only creates the
   pending marker and returns; it does no network or package work.
2. During firmware initialization, OPNsense runs the repository provider. It
   reads the new series with `opnsense-version -a` and points a recognized
   managed `resolver-plugins.conf` at the new feed, keeping the file's mode and
   owner and the public-key trust, and, when it changes the file, sets the pending marker.
3. After boot, the start hook launches the reconcile worker in the background.
   It makes up to three attempts, 30 seconds apart. The worker runs the
   provider again, refreshes the catalogue with `pkg-static update -f -r
   resolver-plugins`, and accepts only its own plugin at `<series>_<n>` with
   the expected origin and ABI. It dry-runs the transaction and refuses any
   plan that removes or downgrades packages, then installs the plugin and
   removes the marker. The BIND worker also refuses when the official `os-bind`
   is installed and, if the installed `bind920` changed, reloads the
   `OPNsense/Bind` templates and restarts BIND.

A failure at any point leaves the marker in place, writes a line to the log and
does not block the firmware upgrade or the boot. The next boot tries again.

Both providers write the same file with the same content, so either or both may
run. Managed shapes are exact: the legacy GitHub Release URLs
(`.../releases/download/pkg-26.1` and `pkg-26.7`), the older
`.../pkg/${ABI}/latest` URL, and a previous `.../pkg/${ABI}/<series>/latest`
URL, each with the Resolver Plugins key, `mirror_type: "none"` and optionally
`priority: 20`. A custom URL, a different key, a disabled repository, a
different mirror mode, a symlink or a missing or non-regular file is left
unchanged and the provider warns `manual migration`.

The migration files must be installed before the upgrade, because a package
published for the new series cannot add hooks to a system that is already
running it. If a system was upgraded without them, or its repository file is
custom, back up `/usr/local/etc/pkg/repos/resolver-plugins.conf`, change only
its URL to the literal `${ABI}` plus series URL shown in [Feed](#feed), and run:

```sh
pkg update -f -r resolver-plugins
pkg install os-bind-rp
pkg install os-dhcp-interface-ha
```

## BIND package transactions

Installing or upgrading `os-bind-rp` preserves dynamic zones. The
package scripts check the minimum OPNsense and BIND versions first. If BIND is
running, they freeze each enabled dynamic primary and reverse zone, stop BIND,
and keep the effective zone masters, including records received through RNDC
updates, before OPNsense regenerates the managed templates and zone files. They
then restore those masters and restart BIND, only if it was running before. The
transaction fails if the zone preservation or the original running state cannot
be restored and confirmed. A retained backup directory is named in the error.

The package action is distinct from the ordinary configure and restart path of
the running plugin.

On a high-availability pair, upgrade the backup node first. Confirm its CARP
role, generated configuration, dynamic and reverse zones, authoritative DNS
answers and BIND logs before upgrading the master. Do not move on while the
backup has an unloaded zone, a failed service-state restoration or new package
errors. Each node has a brief DNS interruption during its own transaction, so
keep the peer healthy meanwhile.

Back up the OPNsense configuration before changing plugin versions:

```sh
cp /conf/config.xml "/conf/config.xml.os-bind-rp.$(date +%Y%m%d%H%M%S).bak"
```

## Rollback

Rollback is a maintainer action; no workflow performs it. The previous
versioned Release of a plugin and series keeps its archive and
`build-record.json`. A maintainer restores it by replacing the assets of the
current Release with that archive and record. The feed follows the next index
run for the series. Delete the newer versioned Release and its source Release
first when the revision is to be built again, because a versioned tag cannot be
republished from a different source.

A system that already installed the newer revision does not downgrade through
`pkg`, so a rollback does not reach it. Prefer a fix in a new revision. Restore
the saved configuration if a failed upgrade left it inconsistent.

## Signing and credentials

The package workflows use these values from the `resolver-plugins/plugins`
Actions settings:

| Name | Kind | Holds |
| --- | --- | --- |
| `RP_PKG_SIGNING_KEY` | secret | the base64-encoded private package-signing key |
| `RP_DISTRIBUTION_APP_ID` | variable | the ID of the distribution GitHub App |
| `RP_DISTRIBUTION_APP_PRIVATE_KEY` | secret | the PEM private key of the distribution App |
| `RP_SYNC_APP_CLIENT_ID` | variable | the client ID of the sync GitHub App |
| `RP_SYNC_APP_PRIVATE_KEY` | secret | the PEM private key of the sync App |

- The signing key is decoded only inside the disposable FreeBSD VM of the index
  `stage` job, written to a temporary file with mode 0600 or tighter, used by
  `pkg repo` and removed. The committed public key is
  `docs/package-repository/resolver-plugins.pub`.
- The distribution App is installed only on `resolver-plugins/repository` with
  `Contents: write`. Its short-lived token creates and edits the Releases, writes
  the feed on `gh-pages` and deletes retired Releases.
- The sync App is installed only on `resolver-plugins/plugins` with
  `Contents: write`. Its short-lived token pushes the release branch and source
  tags, creates the source Releases and deletes retired ones.
- No personal access token is stored. Missing credentials or a suspended App
  installation fail the publishing job before anything is changed.
- Candidate builds (`-f pr=N`) receive none of these secrets and publish nothing.

Rotate an App key through the App's credential settings and update the matching
secret. Never replace an App credential with a broader token.

## HA DHCP Interface

`os-dhcp-interface-ha` is the experimental HA DHCP Interface plugin. It is
published in the same feed as `os-bind-rp` and uses the same signing key and
Apps, but it has its own lifecycle: its own entry workflow, Releases, release
branch (`release/dhcp-interface-ha/<series>`) and version counter. It follows
no upstream plugin, so upstream changes never select a build. Its
Releases are marked as GitHub pre-releases.

Versions are `<series>_<n>`, starting at `26.7_1`. They sort above the earlier
`0.2_*` revisions, so `pkg` upgrades those installs to the first series-based
build. Plugin behavior and limits are in the
[plugin guide](../net/dhcp-interface-ha/README.md) and the
[maintainer guide](dhcp-interface-ha.md). Publishing a package does not update
any appliance.
