# Phase 1: Client series upgrade

Status: plan approved 2026-10-19; stage 1.0 handoff written, not built. Stage 1.1's
handoff and its drafted interface layer (uncommitted, on the release-based branch
`ci-rebuild/bind-worker-26.7` in the main checkout) predate the move to `master`
development; the handoff is rewritten for `master` after 1.0, and the draft is
redone there.

Each plugin carries its own clients across an OPNsense series or FreeBSD base
boundary (design, "Client series upgrade"). BIND has a working path whose worker
still requires this project's runtime packages; HA has none. Plugins are
developed on `master` (specification, "Branches"), so all work is on `master`
after stage 1.0 brings its plugin directories up to date. The new pipeline
carries the changes into the 26.7 release branches; if clients need them sooner,
the user decides whether to carry them by hand under the old CI. Nothing is
pushed without an explicit request.

Only 26.7 changes: 26.1 clients cross into 26.7, whose feed still has the runtime
packages their current worker expects.

## Contracts

**Managed repository configuration.** Both plugins' providers write
`/usr/local/etc/pkg/repos/resolver-plugins.conf` with exactly this content for
the target series (from `opnsense-version -a`):

```
resolver-plugins: {
  url: "https://resolver-plugins.github.io/repository/pkg/${ABI}/<series>/latest",
  mirror_type: "none",
  signature_type: "pubkey",
  pubkey: "/usr/local/etc/pkg/keys/resolver-plugins.pub",
  enabled: yes
}
```

They rewrite the file only when it has one of the managed shapes: this content,
or the same with a `priority: 20,` line before `enabled`, at the current URL, any
`pkg/${ABI}/<X.Y>/latest` URL or the legacy URLs (`releases/download/pkg-26.1`,
`releases/download/pkg-26.7`, `pkg/${ABI}/latest`). Otherwise they leave it and
print the manual-migration warning. Apart from dropping the priority line, this
is BIND's current behavior; HA copies it.

**Worker acceptance.** A worker reconciles only its own plugin. The candidate in
the `resolver-plugins` repository must have the expected origin, the running ABI
(`pkg config ABI`) and version `<target series>_<n>`. Dependencies resolve
natively from every enabled repository. A dry-run plan with a removal or
downgrade is refused. The pending marker clears only after the installed identity
matches the candidate. Failures are logged and stay pending without blocking boot.

**Files and markers** as in the design's client table. BIND and HA never share a
file or marker, so both packages install together.

## Stages

### 1.0 Bring `master`'s plugin directories up to date

Role: `master` becomes the development source by taking each plugin's published
26.7 content from its release branch.

- Inputs: `origin/release/bind-rp/26.7` and `origin/release/dhcp-interface-ha/26.7`
  (their `dns/bind` and `net/dhcp-interface-ha` directories); `master`.
- Outputs: on `master`, both directories equal to the release branches' except
  the `Makefile` version lines (owned by the pipeline) and `master`'s own
  `tests/` directories, which stay.
- Contracts: the release branch content is what clients run; nothing on the
  release branches changes; the framework (`Mk/`) is not copied.
- External inputs: none (both are this project's branches).
- Depends on: nothing.
- Done when: `git diff` between `master` and each release branch, limited to the
  plugin directory and excluding `tests/` and the version lines, is empty.

Findings that set the handoff: `release/bind-rp/26.7` (`1c241209d`) is exactly
the source of the published `26.7_9` (its build record's `source_commit`).
Outside `tests/`, it adds 56 files and changes 26 under `dns/bind` relative to
`master`, deleting none; the `Makefile` also gains `PLUGIN_MANIFEST_DEPENDS`.
`net/dhcp-interface-ha` already matches its release branch except the version
lines and `tests/`, so HA needs no change.

```
Behavior:       master's dns/bind holds the published 26.7 BIND plugin content, so
                plugin development continues from what clients run.
Contracts:      Content from origin/release/bind-rp/26.7 (1c241209d) for every
                path under dns/bind except tests/; Makefile keeps master's
                PLUGIN_VERSION and PLUGIN_REVISION lines; Mk/ and other plugins
                untouched.
External inputs: none (this project's own branch).
Files:          dns/bind/+POST_INSTALL.post, dns/bind/+POST_INSTALL.pre,
                dns/bind/+PRE_INSTALL.pre, dns/bind/Makefile, dns/bind/pkg-descr,
                dns/bind/src/** (56 added, 26 changed)
Done when:      git diff master..1c241209d -- dns/bind, excluding dns/bind/tests
                and the two version lines, is empty; dns/bind/tests and
                net/dhcp-interface-ha are unchanged; one commit,
                "[skip ci] Bring master's BIND plugin up to date with 26.7_9".
Out of scope:   dns/bind/tests, net/dhcp-interface-ha, Mk/, release branches,
                any later phase 1 change.
Review:         per layer (one layer: content)
Open questions: none
```

### 1.1 BIND worker on native dependency resolution

Role: after a series upgrade, the BIND worker installs the target `os-bind-rp`
from this project's feed, with BIND and other dependencies taken from whichever
enabled repository satisfies them, and no longer requires `bind920` or
`bind-tools` in this project's feed.

```
Behavior:       On a pending reconcile, bindRepositoryReconcile.py installs the
                target-series os-bind-rp with dependencies resolved natively from
                all enabled repositories, and restarts BIND when the transaction
                changes bind920.
Contracts:      Worker acceptance and managed repository configuration (above);
                the provider writes the content without priority and still
                recognizes the priority shape as managed; pending marker, log,
                lock and retry interface unchanged.
External inputs: output of opnsense-version -a, pkg config ABI, pkg query and
                rquery, and the pkg dry-run plan; the repository configuration
                file. Keep the existing checks on these; add none.
Files:          (on master after 1.0)
                dns/bind/src/opnsense/scripts/OPNsense/Bind/bindRepositoryReconcile.py
                dns/bind/pkg-descr (one changelog line)
                dns/bind/src/opnsense/scripts/firmware/repos/ResolverPlugins.sh
Done when:      The diff shows: no candidate lookup for bind920 or bind-tools;
                the install and dry run no longer restrict to the
                resolver-plugins repository; the dry-run check accepts any
                additions and upgrades, refuses removals and downgrades, and
                requires the os-bind-rp target identity; BIND restarts only when
                bind920 changed; the marker clears only after the installed
                os-bind-rp matches; the worker accepts the configuration with or
                without the priority line; the provider writes it without, and
                treats an existing priority-20 file at the target URL as needing
                a rewrite. The worker is shorter than before.
Out of scope:   Makefile version (set by the pipeline), the other hooks, tests
                under dns/bind/tests, the release branches, HA.
Review:         per layer
Open questions: none
```

### 1.2 HA series-upgrade hooks

Role: the same three-step upgrade path for HA, independent of BIND.

- Inputs: firmware upgrade and boot events; the managed repository configuration;
  `opnsense-version -a`; the target feed.
- Outputs: the configuration pointed at the target feed; `os-dhcp-interface-ha`
  upgraded to `<target series>_<n>`; marker cleared on success.
- Contracts: managed repository configuration and worker acceptance (above); the
  HA files and marker in the design's client table; the package's existing Python.
- Files: the four new HA files under `net/dhcp-interface-ha/src/` and the
  package's `pkg-descr`. The branch already carries version `26.7_1`.
- Depends on 1.1 for the final managed configuration content.
- Done when: the diff shows the three-step path matching BIND's (marker only in
  the firmware transaction, provider rewrites only managed shapes, start hook
  launches the worker in the background), the worker meets worker acceptance for
  `os-dhcp-interface-ha`, and no file or marker name is shared with BIND.

### 1.3 Fresh install on native dependency resolution

Role: a new installation gets the managed repository configuration and the
plugin with dependencies resolved natively, so it works on series whose feed has
no runtime packages.

- Inputs: `opnsense-version -a`, `pkg config ABI`, the published public key.
- Outputs: the managed configuration (contract above, without priority); the
  public key installed after its fingerprint check; `os-bind-rp` installed.
- Contracts: managed repository configuration; the public-key fingerprint already
  in `scripts/install-repository.sh`.
- Files: `scripts/install-repository.sh` (drop the priority line), the install
  section of `README.md`; `scripts/install-os-bind-rp.sh` and
  `scripts/install-os-bind-rp-prerequisites.py` are removed.
- Done when: no install path requires `bind920` or `bind-tools` from this
  project's feed, and the README shows the resulting commands.
- The README installs with `install-repository.sh` then `pkg install os-bind-rp`;
  pkg's conflict handling replaces the official `os-bind`, as it does in the old
  installer's final step.

## Decisions

- Drop `priority: 20` from the managed configuration, over keeping it, because it
  existed to prefer this project's runtime packages, which are retired
  (2026-10-19).
- Leave 26.1 unchanged, because it is retired and its clients cross into a feed
  that still has runtime packages (2026-10-19).
- Bring `master` up to date before phase 1 work, over developing on the release
  branches, because plugins are developed on `master` (2026-10-19).
- HA copies BIND's provider shape and managed-URL list, over sharing one
  provider file, so neither package owns a file the other needs (2026-10-19).
