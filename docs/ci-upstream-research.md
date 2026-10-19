# Upstream OPNsense build requirements

Research date: 2026-10-15. This note records upstream facts for the CI redesign;
it does not change a workflow or publication policy. Investigation used source
reads and read-only upstream metadata. No tests, builds or Actions were run.
Exact source references below make the findings auditable. They do not require
the replacement CI to freeze every input, maintain fingerprint variants or make
published build records write-once.

The agreed implementation contract is the
[CI rebuild specification](ci-rebuild-spec.md).

## Release discovery

Maintainer clarification: upstream `opnsense/core` is the authority for target
series. CI discovers core refs/releases and their series/edition metadata, then
uses the matching upstream tools/plugins/ports inputs to determine the complete
build environment, including its FreeBSD base. The concrete series and versions
in this note are observations, not values to hard-code in the replacement CI.

Core's `Mk/version.mk` declares `CORE_ABIS` and `CORE_TYPE`; its defaults select
the series ABI and corresponding stable branch. The explicit FreeBSD base comes
from the matching tools build configuration described below. Thus deriving the
environment from core means following its release identity across the upstream
repositories, rather than inventing a core field for the FreeBSD version.
[Core series and edition](https://github.com/opnsense/core/blob/stable/26.7/Mk/version.mk),
[core defaults](https://github.com/opnsense/core/blob/stable/26.7/Mk/defaults.mk).

OPNsense Community Edition uses a year/month series with an additional number
for minor updates. The project describes two major releases annually; its
current roadmap identifies 26.7 as released and 27.1 as planned. Calendar dates
therefore describe a schedule, rather than prove that a series is ready to
build. [Update schedule](https://docs.opnsense.org/manual/updates.html),
[roadmap](https://opnsense.org/roadmap/).

On the research date, upstream tools had final numeric tags through `26.1.11`
and `26.7.7`, alongside preview tags such as `26.7.b` and `26.7.r1`. Core and
plugins both had `stable/26.1` and `stable/26.7`, as well as numerous historical
stable branches. A stable branch's existence alone therefore cannot establish
release readiness or continued support. Sort final tags by numeric components,
and match the complete series prefix, rather than using lexical order or a
substring match. These are observations and a discovery recommendation, not an
upstream promise that future tag syntax will never change.
[Tools tags](https://api.github.com/repos/opnsense/tools/tags?per_page=100),
[core branches](https://api.github.com/repos/opnsense/core/branches?per_page=100),
[plugins branches](https://api.github.com/repos/opnsense/plugins/branches?per_page=100).

The checked fork refs include `release/bind-rp/26.1`,
`release/bind-rp/26.7` and `release/dhcp-interface-ha/26.7`. They already represent
per-package sources for corresponding upstream series. Upstream core/plugins
default to `stable/<ABI>` branches; tools selects both from its derived `ABI`.
The existing fork branch names can remain while their series and build inputs
are discovered upstream. The inspected BIND 26.1 source's merge base with
`upstream/stable/26.1` identifies its upstream baseline directly from Git history,
without a checked-in commit selection. Keep these resolved identities as build
records, not fixed CI pins. A new active series must not require a pre-existing
fork branch or another workflow entry point.
[Core branch selection](https://github.com/opnsense/core/blob/stable/26.7/Mk/defaults.mk),
[plugin branch selection](https://github.com/opnsense/plugins/blob/stable/26.7/Mk/defaults.mk),
[tools branch selection](https://github.com/opnsense/tools/blob/master/Makefile).

Tools `master` currently contains `config/26.7`, while the `26.1.11` tag retains
`config/26.1`. Scanning only the current tools configuration directory would
lose an older series. Read the configuration associated with each selected
release tag. [Tools tree at 26.7.7](https://github.com/opnsense/tools/tree/5625ce5fae2079b4133b7ebf29bb292f9576e9de/config),
[26.1 configuration](https://github.com/opnsense/tools/tree/31820a9d8716ad574b70d9421ac1407b8c088656/config/26.1).

Community Edition and Business Edition are distinct release streams. The
Business Edition documentation says that 26.4.2 is based on CE 26.1.11; the
initial 26.4 release was based on CE 26.1.6. Historical core branches also
include `stable/21.4` and `stable/21.10`. Numeric names alone are not a complete
edition classifier. For the current CE scope, discover published CE series and
their corresponding source branches; do not treat every numeric branch or
Business Edition version as another CE build target.
[CE releases](https://docs.opnsense.org/CE_releases.html),
[BE releases](https://docs.opnsense.org/BE_releases.html),
[26.4 release lineage](https://docs.opnsense.org/releases/BE_26.4.html).

Core's `SECURITY.md` provides an explicit support rule: security updates cover
only the latest published major branch of each edition. The CE release index
currently identifies `26.7.7`, hence series `26.7`, as latest. Under the approved
support window, derive the active series from the newest final CE core release
and matching edition/series metadata. A subsequent final CE series
supersedes its predecessor; prereleases and Business Edition releases do not.
This is a rule derived from upstream's published security policy, not a per-branch
EOL flag. An older-series tag published later must not supersede a numerically
newer CE series. Build readiness remains separate from release/support identity.
[Core support policy](https://github.com/opnsense/core/blob/master/SECURITY.md),
[latest CE release](https://docs.opnsense.org/CE_releases.html),
[core edition/series fields](https://github.com/opnsense/core/blob/stable/26.7/Mk/version.mk).

The maintainer has approved following that window for automatic plugin builds.
Retired series keep their current plugin release and one rollback available;
retirement does not remove their signed feeds or rewrite source history.

## Per-series environment

The relevant base is FreeBSD. The series is not a reliable substitute for its
base release or language defaults. The selected tools `build.conf` gives these
concrete differences:

| Attribute | Tools 26.1.11 | Tools 26.7.7 |
| --- | --- | --- |
| `OS` | `14.3` | `15.1` |
| `PHP` | `83` | `85` |
| `PYTHON` | `313` | `313` |
| `SSL` | `../opnsense/openssl` | `openssl35` |
| `LUA` / `PERL` / `RUBY` | `5.4` / `5.42` / `33` | `5.4` / `5.42` / `33` |

Sources: [26.1.11 build.conf](https://github.com/opnsense/tools/blob/31820a9d8716ad574b70d9421ac1407b8c088656/config/26.1/build.conf),
[26.7.7 build.conf](https://github.com/opnsense/tools/blob/5625ce5fae2079b4133b7ebf29bb292f9576e9de/config/26.7/build.conf).
The 26.7 release notes independently identify FreeBSD 15.1, OpenSSL 3.5,
Python 3.13 and PHP 8.5, and warn about third-party repositories during the OS
and OpenSSL transition. [26.7 release notes](https://docs.opnsense.org/releases/CE_26.7.html).

Upstream tools selects `config/<SETTINGS>` when `SETTINGS` is supplied. Otherwise
it searches for a configuration matching the host OS, then falls back to the
latest configuration. It sets core/plugins branches to `stable/${ABI}`, passes
PHP, series ABI and Python to their make environments, and rejects an unexpected
host OS unless explicitly overridden. The redesign should select the intended
series explicitly and provision a compatible target environment; a constant
FreeBSD 14.3 VM with a different manifest ABI is insufficient for compiled
FreeBSD 15 packages. [Tools Makefile](https://github.com/opnsense/tools/blob/5625ce5fae2079b4133b7ebf29bb292f9576e9de/Makefile).

For ports, upstream also consumes the series `make.conf`, including
`DEFAULT_VERSIONS`, global options and per-port options. Its generator substitutes
the language and SSL defaults from `build.conf`; the ports build installs that
generated configuration into its staged environment. In particular, 26.7
explicitly sets `GSSAPI_NONE` for both BIND ports. Base OS discovery by itself
does not reproduce the upstream build environment.
[26.7 make.conf](https://github.com/opnsense/tools/blob/c1f4bcd493064132bd3efa8e0ebc8143de49d4ea/config/26.7/make.conf),
[configuration generator](https://github.com/opnsense/tools/blob/5625ce5fae2079b4133b7ebf29bb292f9576e9de/build/make.conf.sh),
[ports builder](https://github.com/opnsense/tools/blob/5625ce5fae2079b4133b7ebf29bb292f9576e9de/build/ports.sh).

Keep three identifiers distinct: the OPNsense series such as `26.7`, the FreeBSD
release such as `15.1`, and the native package ABI such as
`FreeBSD:15:amd64`. Tools derives its source ABI from the source revision major
and target architecture. Plugin defaults separately use `PLUGIN_ABI` for the
OPNsense series, and can read it from `opnsense-version -a`.
[Tools ABI derivation](https://github.com/opnsense/tools/blob/5625ce5fae2079b4133b7ebf29bb292f9576e9de/build/common.sh),
[plugin defaults](https://github.com/opnsense/plugins/blob/484ea085f28fdf4f701a9e503ed33e117c1425cd/Mk/defaults.mk).
The public feed currently exposes `FreeBSD:14:amd64` and
`FreeBSD:15:amd64`; architecture support should be explicit and backed by a
matching feed and builder, rather than inferred from a repository's source
directories. [Public feed root](https://pkg.opnsense.org/).

## Native plugin versioning and framework

The upstream package version is `PLUGIN_VERSION` when `PLUGIN_REVISION` is zero
or empty, and `PLUGIN_VERSION` followed by `_` and the revision otherwise.
`Mk/plugins.mk` supplies that value to the package manifest. It does not require
`PLUGIN_VERSION` to equal the OPNsense series.
[Upstream version and manifest rules](https://github.com/opnsense/plugins/blob/484ea085f28fdf4f701a9e503ed33e117c1425cd/Mk/plugins.mk).

For example, the inspected stable branches define BIND as `1.34_3` on 26.1 and
`1.34_4` on 26.7. The generated plugin annotation `product_version` uses that
same package version; `product_abi` separately records the target OPNsense
series. [26.1 BIND definition](https://github.com/opnsense/plugins/blob/6f3937f938377464534ebebde66cc13d84186542/dns/bind/Makefile),
[26.7 BIND definition](https://github.com/opnsense/plugins/blob/484ea085f28fdf4f701a9e503ed33e117c1425cd/dns/bind/Makefile),
[version annotation template](https://github.com/opnsense/plugins/blob/484ea085f28fdf4f701a9e503ed33e117c1425cd/Templates/version).

The inspected versions of `Mk/plugins.mk` retain the same version contract; their
only differences are two shell equality comparisons. `Mk/defaults.mk` changes
the fallback series from 26.1 to 26.7. This observation does not justify copying
today's framework indefinitely: the selected upstream series should supply its
`Mk`, `Scripts` and `Templates`, with only the fork's necessary extensions
applied. Native packaging also queries the installed dependencies to construct
the manifest and invokes `pkg create`, so dependency identities belong to the
target environment rather than a hand-maintained list of manifest versions.
[26.1 framework](https://github.com/opnsense/plugins/blob/6f3937f938377464534ebebde66cc13d84186542/Mk/plugins.mk),
[26.7 native build orchestration](https://github.com/opnsense/tools/blob/5625ce5fae2079b4133b7ebf29bb292f9576e9de/build/plugins.sh).

The live fork's release-source commits use series-derived versions `26.1_7`
and `26.7_9`. These are deliberate fork upgrade policy expressed through the native
framework, rather than upstream BIND's choice of version value. The maintainer's
clarification and the history trace below supersede the earlier recommendation
to replace this policy with upstream-style `1.x` versions. Preserve the series
value and advance its revision for new package content; track the upstream BIND
version separately. Local branch names may refer to different commits with
different versions. Use the actual published source as the client baseline.
[Published 26.1 source](https://github.com/resolver-plugins/plugins/blob/31b351a4770f38536ba5614dff5f8c3b162ceda6/dns/bind/Makefile),
[published 26.7 source](https://github.com/resolver-plugins/plugins/blob/1c241209df8a178db076c1970339ef9a507c0d3c/dns/bind/Makefile).
The series version makes a target such as `26.7_1` newer than `26.1_7` even if
upstream BIND's own plugin version is unchanged. Native version ordering still
applies. Moving back to `1.x` would break that ordering and is not the new design.
[Native version comparison contract](https://github.com/freebsd/pkg/blob/2.8.4/docs/pkg-version.8).

## Existing fork series-upgrade path

The 26.1 change `f58e9978d` replaced `1.36_12` with `26.1_1` in the same commit
that added the upgrade/start syshooks, firmware repository provider and
reconciliation worker. Its 26.7 counterpart is `ca9e4b4ec`. The four installed
upgrade files are identical between inspected published source refs `31b351a47`
and `1c241209d`; they are absent from control-plane `master`'s `dns/bind/src`.
They are existing fork behavior to preserve in the source overlay.
[26.1 introduction](https://github.com/resolver-plugins/plugins/commit/f58e9978d5ab4e1b7d32c9af3a3e1629b5f92d40),
[26.7 introduction](https://github.com/resolver-plugins/plugins/commit/ca9e4b4ec).

The source and upstream call sites establish this sequence:

1. Core's firmware upgrade runs `rc.syshook upgrade` before reboot. BIND's
   `upgrade/50-bind-rp-repository` writes a pending marker and returns without
   network or package work. This avoids nesting a package transaction inside
   firmware migration.
2. Core boot initialization runs `system_firmware_configure`, which invokes
   executable firmware repository providers. `ResolverPlugins.sh` obtains the
   now-installed series through `opnsense-version -a` and rewrites only recognized
   managed configurations to `pkg/${ABI}/<series>/latest`, preserving the key and
   custom-configuration boundary. The native plugin framework also invokes
   installed repository providers from generated package post-install scripts.
3. Core runs start syshooks after boot/network initialization. BIND's
   `start/50-bind-rp-repository` launches `bindRepositoryReconcile.py` in the
   background when pending. The worker uses `pkg-static`, locks, validates the
   managed target feed, refreshes its signed catalogue, checks candidate origin/ABI
   and the plugin's series-version prefix, dry-runs the native transaction, then installs the
   target plugin. It clears pending only after successful identity checks;
   failures remain logged/pending, and retries are bounded.

The provider leaves `${ABI}` literal, so native `pkg` selects the running
FreeBSD base's repository. The explicit series selects the correct OPNsense
branch even when successive series share a FreeBSD ABI. The package's increasing
series version supplies upgrade ordering. These three parts work together; ABI
pathing alone does not retarget an old series URL or make an unchanged plugin
version newer.
[Core pre-upgrade call](https://github.com/opnsense/core/blob/stable/26.7/src/opnsense/scripts/firmware/upgrade.sh),
[boot provider call](https://github.com/opnsense/core/blob/stable/26.7/src/etc/rc.bootup),
[provider discovery](https://github.com/opnsense/core/blob/stable/26.7/src/etc/inc/system.inc),
[start syshook order](https://github.com/opnsense/core/blob/stable/26.7/src/etc/rc),
[syshook phases](https://docs.opnsense.org/development/backend/autorun.html),
[native provider packaging](../Mk/plugins.mk).

Automatic handover requires the migration-aware plugin to be installed before
the firmware upgrade. As the existing maintainer guide notes, publishing the
target package cannot retrospectively run missing hooks on an already-upgraded
client. Retain a working old-series current feed so clients can obtain the
migration-aware package before crossing the series/base boundary.
[Existing migration limitation](package-repository.md).

Later worker fixes restore an older/missing BIND runtime pair when needed and
refresh BIND templates/service after that runtime change. The current worker
still demands Resolver-feed candidates for both native runtime packages; retaining
client upgrade compatibility must account for that during cutover, without
turning it into a dependency between their CI producers. The inspected HA release
does not package these repository/upgrade hooks, so equivalent HA-only upgrade
behavior remains an implementation gap. This is a source/history investigation;
no tests, firmware upgrades or package transactions were run.
[Published provider](https://github.com/resolver-plugins/plugins/blob/31b351a4770f38536ba5614dff5f8c3b162ceda6/dns/bind/src/opnsense/scripts/firmware/repos/ResolverPlugins.sh),
[published worker](https://github.com/resolver-plugins/plugins/blob/31b351a4770f38536ba5614dff5f8c3b162ceda6/dns/bind/src/opnsense/scripts/OPNsense/Bind/bindRepositoryReconcile.py),
[runtime reconciliation fix](https://github.com/resolver-plugins/plugins/commit/da6c3820a),
[service refresh fix](https://github.com/resolver-plugins/plugins/commit/1734eeaf4).

## Package manager changes within a series

The package creator can change independently of the FreeBSD major or the
OPNsense major series:

| Release tag | Origin selected by tools `ports.conf` | Native pkg recipe version |
| --- | --- | --- |
| `26.1.11` | `ports-mgmt/pkg` | `2.3.1_1` |
| `26.7` | `ports-mgmt/pkg` | `2.3.1_1` |
| `26.7.7` | `opnsense/pkg` | `2.8.4_1` |

Sources: [26.1.11 build list](https://github.com/opnsense/tools/blob/31820a9d8716ad574b70d9421ac1407b8c088656/config/26.1/ports.conf),
[26.1.11 recipe](https://github.com/opnsense/ports/blob/a398345f51b79050581e78c5b2dad5094e9a891c/ports-mgmt/pkg/Makefile),
[base 26.7 build list](https://github.com/opnsense/tools/blob/c1f4bcd493064132bd3efa8e0ebc8143de49d4ea/config/26.7/ports.conf),
[base 26.7 recipe](https://github.com/opnsense/ports/blob/77a853b157a3afb62db0d28200894f072d0f7c95/ports-mgmt/pkg/Makefile),
[26.7.7 build list](https://github.com/opnsense/tools/blob/5625ce5fae2079b4133b7ebf29bb292f9576e9de/config/26.7/ports.conf),
[26.7.7 override recipe](https://github.com/opnsense/ports/blob/4811f42640bbfe1b3459d3cd955205a1f81bac88/opnsense/pkg/Makefile).

At 26.7.7, `ports-mgmt/pkg` also exists with version `2.8.4`; choosing the first
existing origin would miss the selected OPNsense override and its revision.
Evaluate the origin actually named by the selected tools build list.
[Other 26.7.7 recipe](https://github.com/opnsense/ports/blob/4811f42640bbfe1b3459d3cd955205a1f81bac88/ports-mgmt/pkg/Makefile).

There is a concrete archive compatibility issue behind the existing creator
and reader distinction. In pkg 2.3.1, an object-valued file checksum is retained
only when its length is 64, whereas scalar checksum entries do not have that
restriction. Pkg 2.8.4 removes the object checksum length check and emits file
attributes as objects. This can lose file integrity metadata when a newer
creator's archive is interpreted by an older client; accepting an archive is
not equivalent to preserving its file checksums.
[2.3.1 manifest parser](https://github.com/freebsd/pkg/blob/2.3.1/libpkg/pkg_manifest.c),
[2.8.4 parser and emitter](https://github.com/freebsd/pkg/blob/2.8.4/libpkg/pkg_manifest.c).
Pkg supports multiple native digest formats, including SHA256 and BLAKE2;
ordinary package integrity should follow pkg's formats rather than require
every checksum to be an unprefixed SHA256 string.
[Native checksum implementation](https://github.com/freebsd/pkg/blob/2.8.4/libpkg/pkg_checksum.c).

The inference for the redesign is to keep packages readable, with intact file
checksums, by the oldest OPNsense client the channel claims to support. This
requires knowing that support floor and its package manager. It does not
require a CI-specific hierarchy of seals or fingerprints.

## Core repository metadata and native dependencies

Core supplies its package feed template, trusted/revoked fingerprints and
series identity. The template combines `${ABI}` with `CORE_ABI` and uses
fingerprint signatures. Core's defaults and version metadata provide
architecture, language-dependent package names and the package-site default.
Read the selected series' files rather than copy one release's repository URL
or trust directory into a permanent compatibility table.
[Repository template](https://github.com/opnsense/core/blob/9ad30d73071d6a64ca5e9a71ccdb7a63803d53a7/src/etc/pkg/repos/OPNsense.conf.shadow.in),
[trust directory](https://github.com/opnsense/core/tree/9ad30d73071d6a64ca5e9a71ccdb7a63803d53a7/src/etc/pkg/fingerprints/OPNsense),
[core version metadata](https://github.com/opnsense/core/blob/9ad30d73071d6a64ca5e9a71ccdb7a63803d53a7/Mk/version.mk).

The checked core repository template uses
`%%CORE_PACKAGESITE%%/${ABI}/%%CORE_ABI%%/latest`. Core obtains `CORE_ABI` from
`opnsense-version -a` when available, otherwise from its declared `CORE_ABIS`.
The matching plugin framework obtains `PLUGIN_ABI` from the same version tool
and records it as `product_abi`. Following ABI-plus-series feed scoping therefore
matches upstream core/plugins behavior. The Resolver hosting prefix is the local
package-site base; it does not require a new compatibility convention.
[Core ABI selection](https://github.com/opnsense/core/blob/stable/26.7/Mk/defaults.mk),
[plugin ABI selection](https://github.com/opnsense/plugins/blob/stable/26.7/Mk/defaults.mk),
[plugin version annotations](https://github.com/opnsense/plugins/blob/stable/26.7/Templates/version).

Even disabled FreeBSD feed names changed: 26.1 disables `FreeBSD` and
`FreeBSD-kmods`, whereas 26.7 disables `FreeBSD-base`, `FreeBSD-ports` and
`FreeBSD-ports-kmods`. Using the selected core template avoids mixing another
release's ordinary dependencies into the target environment.
[26.1 disabled feeds](https://github.com/opnsense/core/blob/8cc69b21e0f4c2622fc8a62df2a15ba7cb1e731f/src/etc/pkg/repos/FreeBSD.conf.shadow),
[26.7 disabled feeds](https://github.com/opnsense/core/blob/9ad30d73071d6a64ca5e9a71ccdb7a63803d53a7/src/etc/pkg/repos/FreeBSD.conf.shadow).

BIND's native recipe changes with the environment too. Its default GSSAPI
choice depends on the FreeBSD major, and its LMDB provider changes from
`databases/lmdb` in the 26.1.11 recipe to `databases/lmdb0` in 26.7.7. The
daemon has a native dependency on `bind-tools`; the tools port is a slave using
the daemon's master recipe. Dependency and option discovery must therefore
honour native make evaluation and the series configuration. Independent
publication of the two packages does not remove their native dependency.
[26.1.11 BIND recipe](https://github.com/opnsense/ports/blob/a398345f51b79050581e78c5b2dad5094e9a891c/dns/bind920/Makefile),
[26.7.7 BIND recipe](https://github.com/opnsense/ports/blob/4811f42640bbfe1b3459d3cd955205a1f81bac88/dns/bind920/Makefile),
[tools slave recipe](https://github.com/opnsense/ports/blob/4811f42640bbfe1b3459d3cd955205a1f81bac88/dns/bind-tools/Makefile).

## Requirements inferred from the evidence

These are design conclusions, with automatic publication for new core series
and upstream BIND plugin updates reflecting the maintainer's instructions:

1. Discover the newest final CE series from upstream core refs and release
   metadata, following the approved upstream support window. Follow its core
   release to its matching plugins branch, tools release configuration and
   ports tag. Derive the FreeBSD base for that core target from this metadata;
   keep neither a hard-coded series list nor an OPNsense-to-FreeBSD version map,
   and require no manually created fork branch for discovery.
2. Treat a newly discovered series as ready when its required source,
   configuration, native package feed and supported builder are available.
   Verify feed availability through the native authenticated catalogue, rather
   than treating a directory listing or HTTP 200 as readiness.
3. Materialize the fork's narrow plugin changes with that series' upstream
   framework and metadata. A ready new series automatically builds and publishes
   its plugin packages without a source-review PR gate. Also check upstream BIND
   on the active core series: incorporate every plugin update automatically
   and build/publish the affected active series, with no review gate or upstream
   version-bump prerequisite. HA and runtime pipelines remain independent.
4. Let native make produce the package version, dependencies and annotations in
   the selected environment. Keep the OPNsense series and FreeBSD ABI as separate
   metadata while preserving BIND's series-derived package version and installed
   cross-series upgrade hooks. Keep runtime package approval and publication independent.
5. Record the refs actually used, target OS/architecture, relevant language/SSL
   defaults, package-manager identity and resulting package identity in one
   readable build record. Keep normal package signatures and checksums; do not
   reintroduce CI-wide immutability, sealed sidecar layers or fingerprint variants.

Existing local work already contains useful discovery and compatibility logic:
[upstream input discovery](../.github/ci/shared/upstream_inputs.py),
[package manager origin/version selection](../.github/ci/shared/target_pkg.py),
[manifest checksum compatibility](../.github/ci/shared/package_checksums.py), and
[repository setup](../.github/ci/shared/setup-opnsense-repository.sh).
Their current freezing, hashing and orchestration contracts are not requirements
for the redesign. BIND's series-based version and installed repository/upgrade
hooks are compatibility behavior to preserve, independently of those CI contracts.

No structured per-series upstream EOL registry was found in the inspected
sources. Use the approved latest-final-CE retirement rule above, derived from
core's published security-support policy, while retaining current plus one
rollback for retired series.
A future FreeBSD version also requires an available compatible builder;
discovery can report its absence automatically, but cannot make an unsupported
VM image or a conflicting fork patch compatible by changing metadata.
