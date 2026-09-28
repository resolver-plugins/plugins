# HA DHCP Interface package releases

The **Publish HA DHCP Interface package release** workflow packages and publishes
`os-dhcp-interface-ha-devel` for OPNsense **26.7 / amd64**. It is manually dispatched
from `master`. Merge the plugin and workflow changes into `master` before the first
run. Other OPNsense series require separate qualification before adding them to
the workflow's supported choices.

## Run a release

1. Bump `PLUGIN_VERSION` or `PLUGIN_REVISION` in `net/dhcp-interface-ha/Makefile`
   when package contents change. Merge the reviewed source into `master`.
2. Open **Actions → Publish HA DHCP Interface package release → Run workflow**,
   choose `master` and series `26.7`.
3. Require the entire workflow to succeed, including installation from the public
   signed channel. Record the run URL as the release evidence.

The workflow uses the existing `RP_PKG_SIGNING_KEY` secret and the
`RP_DISTRIBUTION_APP_ID` variable / `RP_DISTRIBUTION_APP_PRIVATE_KEY` secret
[documented for the package repository](package-repository.md). No additional
credentials are required. The signing key is available only to the isolated
FreeBSD signing step; the publisher App token is scoped to
`resolver-plugins/repository`.

## Build and verification

The selected `master` commit supplies the plugin, build framework, and CI helpers.
The workflow resolves `release/bind-rp/26.7` once to an immutable commit and reuses
only its reviewed `.resolver-plugins/upstream.json` compatibility profile. This
selects FreeBSD and the pinned OPNsense core repository/fingerprints; it does not
build BIND or take BIND source into this plugin. Both commits are recorded.

Builds use the existing target-`pkg` pin and file-checksum validator. The native
package name is explicitly `os-dhcp-interface-ha-devel`, independent of the
checkout's development marker. The wrapper clears only the plugin's ignored
`work` directory; it never invokes upstream `make clean`.

The release must pass:

- The plugin controller, PHP, UI, hook and release-helper tests.
- A native package build, target-parser checksum validation, installation and
  menu/tab route resolution in a disposable FreeBSD VM.
- Validation of build identity against trusted source/profile and package-manager
  pins, and a signing-key/public-key match before creating the signed catalogue.
- Installation through the staged signed catalogue in a fresh VM.
- Installation through the public signed catalogue in another fresh VM, comparing
  package identity and installed file hashes with the exact staged archive.

These VM checks do not replace the two-firewall CARP/DHCP handoff qualification.
They neither connect to nor change HA-1 or HA-2.

## Distribution and installation

HA DHCP Interface has independent signed channels in the existing distribution
repository. The BIND feed and its current/rollback assets remain unchanged.

| Purpose | Repository | Release tag |
| --- | --- | --- |
| Current signed plugin feed | `resolver-plugins/repository` | `pkg-dhcp-interface-ha-26.7` |
| Immutable signed rollback feed | `resolver-plugins/repository` | `pkg-dhcp-interface-ha-26.7-<version>` |
| Human-facing experimental download | this source repository | `dhcp-interface-ha-26.7-<version>` |

All signed feeds include the plugin archive, signed catalogue, committed public
key, build metadata, upstream profile and `channel.json` asset hashes. The source
prerelease contains only the verified plugin archive, build metadata, upstream
profile and `SHA256SUMS`. It is published after the public installation check.

After the first successful publication, configure the separate feed on an
OPNsense 26.7 amd64 firewall. Install the committed
[Resolver Plugins public key](package-repository/resolver-plugins.pub) at
`/usr/local/etc/pkg/keys/resolver-plugins.pub`, using the trusted-key procedure and
fingerprint in the [package repository guide](package-repository.md). Create
`/usr/local/etc/pkg/repos/resolver-plugins-dhcpha.conf`:

```text
resolver-plugins-dhcpha: {
  url: "https://github.com/resolver-plugins/repository/releases/download/pkg-dhcp-interface-ha-26.7",
  mirror_type: "none",
  signature_type: "pubkey",
  pubkey: "/usr/local/etc/pkg/keys/resolver-plugins.pub",
  enabled: yes
}
```

Then install the plugin:

```sh
pkg update -f -r resolver-plugins-dhcpha
pkg install -y -r resolver-plugins-dhcpha os-dhcp-interface-ha-devel
```

For rollback, change the URL's final tag to the desired immutable version, refresh
that catalogue, and force-install the plugin from that repository. Review the
older version's configuration compatibility first. Retain the normal plugin
removal guards; do not uninstall a configured interface to perform an upgrade.

## Retry and recovery

An existing immutable version must belong to the same source/profile commits and
package-manager pin. A retry reuses those exact signed bytes, even if rebuilding
would produce a different archive timestamp. Different source at the same version
fails; bump the revision instead of overwriting the snapshot.

Publication downloads the previous current assets before mutation and rechecks
that they have not changed. The new immutable snapshot is published first. Failed
current publication restores the previous assets (or removes a newly created
current release), leaving the immutable snapshot available. Old-source retries
cannot move an advanced current feed backward. Rollback snapshots are retained;
this workflow does not prune them or alter BIND's retention policy.

If the post-publication installation check fails, the workflow fails and withholds
the source prerelease. The current signed feed has already been published; inspect
the failure before rerunning. It is not automatically rolled back after that stage.
A partial source-release upload remains a draft; remove that incomplete draft
before retrying. A complete draft can be promoted on retry without replacing its
assets.

## Local checks

```sh
sh -n .github/ci/build-dhcp-interface-ha.sh .github/ci/verify-dhcp-interface-ha.sh
shellcheck -s sh .github/ci/build-dhcp-interface-ha.sh .github/ci/verify-dhcp-interface-ha.sh
python3 -m py_compile .github/ci/*.py
python3 -m pytest -q .github/ci/ci-tests
python3 -m unittest discover -s net/dhcp-interface-ha/tests
node net/dhcp-interface-ha/tests/ui/test_configure_lagg.js
node net/dhcp-interface-ha/tests/ui/test_log.js
git diff --check
```

Run GitHub Actions only when authorized. Creating this workflow does not publish
or sign a package by itself.
