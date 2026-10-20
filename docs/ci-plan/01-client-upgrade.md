# Phase 01: Client series upgrade

Status: planned
Before: `master`'s `dns/bind` holds the published 26.7_9 BIND plugin (commit
`8c640b214`, "Bring master's BIND plugin up to date with 26.7_9"). The BIND
worker still requires this project's `bind920` and `bind-tools` packages, the
managed repository configuration carries `priority: 20`, HA has no upgrade
hooks, and fresh installs use `scripts/install-os-bind-rp.sh`.
After: on `master`, both plugins carry their clients across an OPNsense series
or FreeBSD base upgrade with dependencies, including BIND, resolved natively;
the managed configuration has no priority line; fresh install is
`install-repository.sh` plus `pkg install`.

Rules: this phase changes plugin code, not CI. Nothing is run: review by
reading the diff and `git diff --check`. Commit once per phase with
`[skip ci]`; nothing is pushed. The release branches are not touched; the new
pipeline carries these changes into them later.

Shared references: design, "Client series upgrade" table
([design](../ci-rebuild-design.md)); specification, "Series upgrade" and
"BIND runtime" ([spec](../ci-rebuild-spec.md)).

The managed repository configuration every stage refers to is exactly:

```
resolver-plugins: {
  url: "https://resolver-plugins.github.io/repository/pkg/${ABI}/<series>/latest",
  mirror_type: "none",
  signature_type: "pubkey",
  pubkey: "/usr/local/etc/pkg/keys/resolver-plugins.pub",
  enabled: yes
}
```

The earlier shape is the same with `  priority: 20,` before `enabled`. Both
shapes count as managed.

## Stages

| Stage | Goal | Model | Effort | Order | Status |
| --- | --- | --- | --- | --- | --- |
| 01.1 | BIND provider writes the configuration without priority | haiku | low | first, parallel with 01.2 and 01.5 | planned |
| 01.2 | BIND worker resolves dependencies natively | sonnet | medium | first, parallel with 01.1 and 01.5 | planned |
| 01.3 | HA upgrade hooks and provider | sonnet | low | after 01.1 | planned |
| 01.4 | HA reconcile worker | sonnet | medium | after 01.2, parallel with 01.3 | planned |
| 01.5 | Fresh install without runtime packages | haiku | low | first, parallel with 01.1 and 01.2 | planned |

## 01.1 BIND provider without priority

```
Goal:            ResolverPlugins.sh writes the managed configuration without the
                 priority line and still treats both shapes as managed.
Context:         The provider runs during OPNsense boot, reads the series with
                 opnsense-version -a and rewrites only managed shapes. Its
                 repository_body() prints the priority line unless its second
                 argument is "none"; managed_body() compares with the default
                 body, then with the "none" body.
Contracts:       Output is the configuration above. A file with either shape at
                 the target URL, any pkg/${ABI}/<X.Y>/latest URL or the three
                 legacy URLs is managed. Every rewrite sets the pending marker,
                 including one that only drops the priority line (the worker
                 then finds the plugin current and clears it).
Files:           dns/bind/src/opnsense/scripts/firmware/repos/ResolverPlugins.sh
Work:            1. In repository_body(), print the priority line only when the
                    second argument is "priority":
                    [ "${2:-}" != priority ] || printf '%s\n' '  priority: 20,'
                 2. In managed_body(), pass "priority" instead of "none" to the
                    second repository_body call.
External inputs: none added.
Done when:       The diff is those two lines; the final write, the early
                 "already current" comparison and the managed checks all use the
                 body without priority first.
Out of scope:    The URL list, warnings, marker handling, the worker, and
                 dns/bind/tests (left stale on purpose; see Notes).
```

## 01.2 BIND worker on native dependency resolution

```
Goal:            bindRepositoryReconcile.py installs the target-series os-bind-rp
                 with dependencies from every enabled repository and no longer
                 looks for bind920 or bind-tools in this project's feed.
Context:         Spec, "Series upgrade": "The reconcile worker relies on native
                 dependency resolution for BIND instead of requiring this
                 project's runtime packages in the target feed." The worker now
                 builds an ALLOWED map of three packages, requires candidates for
                 bind920 and bind-tools, compares versions with pkg version -t,
                 dry-runs and installs with -r resolver-plugins, and restarts
                 BIND when it requested a BIND update.
Contracts:       Unchanged: env overrides, lock, log, --retry and
                 --retry-attempts, pending marker, require_managed_repository
                 (accepts both shapes), the forced `update -f -r
                 resolver-plugins`, candidate lookup with `rquery -r
                 resolver-plugins`, the refusal of an installed official
                 os-bind, version pattern <series>_<n>, ABI and origin checks.
                 Changed: only os-bind-rp (origin opnsense/os-bind-rp) is a
                 candidate; dry run and install name only
                 os-bind-rp-<version> and pass no -r; BIND restarts when the
                 installed bind920 record differs before and after the install.
Files:           dns/bind/src/opnsense/scripts/OPNsense/Bind/bindRepositoryReconcile.py
                 dns/bind/pkg-descr
Work:            1. Replace ALLOWED with PLUGIN = "os-bind-rp" and
                    PLUGIN_ORIGIN = "opnsense/os-bind-rp".
                 2. require_candidate(abi, series): look up PLUGIN only; keep
                    the origin, ABI, version-pattern and series checks.
                 3. Delete package_version_less_than().
                 4. validate_dry_run(): keep the "recognized plan" and
                    REMOVED/DOWNGRADED checks and the requested-identity check;
                    delete the loop refusing package names outside ALLOWED.
                 5. reconcile_once(): after the update, take the plugin
                    candidate, refuse os-bind, record bind920 before
                    (record(package_command(), "bind920")), dry-run
                    `install -n <identity>` (allow status 0 and 1); when pkg
                    reports "The most recent versions of packages are already
                    installed", require the installed plugin to match and expect
                    no requests, otherwise expect the identity; run
                    `install -y <identity>`; restart BIND when the bind920
                    record changed; require the installed plugin; unlink the
                    marker.
                 6. pkg-descr: add as the first bullet under "1.36":
                    "* Take BIND from any enabled repository when upgrading the
                    plugin after a series upgrade"
External inputs: unchanged (opnsense-version, pkg config ABI, pkg query and
                 rquery, the dry-run output, the repository file).
Done when:       No bind920 or bind-tools candidate lookup, no version
                 comparison helper, no -r on the dry run or install, the restart
                 tied to a changed bind920 record, and the file shorter than
                 before (274 lines).
Out of scope:    The provider, the hooks, the Makefile, and dns/bind/tests
                 (left stale on purpose; see Notes).
```

## 01.3 HA upgrade hooks and provider

```
Goal:            HA has the pre-upgrade marker hook, the start hook and its own
                 repository provider, independent of BIND's files.
Context:         Design, "Client series upgrade": HA's files are
                 etc/rc.syshook.d/upgrade/50-dhcp-interface-ha-repository,
                 opnsense/scripts/firmware/repos/ResolverPluginsHA.sh,
                 etc/rc.syshook.d/start/50-dhcp-interface-ha-repository, worker
                 opnsense/scripts/dhcp_interface_ha/repository_reconcile.py,
                 marker /var/db/os-dhcp-interface-ha/repository-reconcile.pending.
                 BIND's equivalents in dns/bind/src are the model; 01.1 has
                 already changed BIND's provider. HA's package runs its Python
                 scripts as `/usr/local/bin/python3 <script>` (they are 0644).
Contracts:       Provider output and managed shapes as at the top of this file,
                 identical to BIND's, so either provider may run. Fixed paths;
                 no environment overrides (BIND's exist only for its tests).
Files:           net/dhcp-interface-ha/src/etc/rc.syshook.d/upgrade/50-dhcp-interface-ha-repository
                 net/dhcp-interface-ha/src/etc/rc.syshook.d/start/50-dhcp-interface-ha-repository
                 net/dhcp-interface-ha/src/opnsense/scripts/firmware/repos/ResolverPluginsHA.sh
Work:            1. Upgrade hook (sh, 0755): mkdir -p the marker directory,
                    touch the marker, ignore failures, exit 0. No network or
                    package work.
                 2. Start hook (sh, 0755): exit 0 unless the marker exists; run
                    `/usr/sbin/daemon /usr/local/bin/python3
                    /usr/local/opnsense/scripts/dhcp_interface_ha/repository_reconcile.py
                    --retry --retry-attempts 3` in the background with output
                    discarded; exit 0.
                 3. Provider (sh, 0755): copy BIND's ResolverPlugins.sh with
                    repository_config, pending_marker and
                    opnsense_version_command set to their fixed default paths
                    (HA's marker). Replace the directory/name case block with
                    mktemp "${repository_config%/*}/.${repository_config##*/}.XXXXXX"
                    and the marker-directory test with mkdir -p
                    "${pending_marker%/*}".
External inputs: opnsense-version output and the repository file, checked as
                 BIND's provider does.
Done when:       Three new executable files; the provider differs from BIND's
                 only in the fixed paths and the two simplifications; no name is
                 shared with BIND.
Out of scope:    The worker (01.4), HA's Makefile and controller.
```

## 01.4 HA reconcile worker

```
Goal:            repository_reconcile.py installs the target-series
                 os-dhcp-interface-ha after a series upgrade and clears HA's
                 marker only on success.
Context:         Design, "Client series upgrade", shared contract: workers
                 accept only their own plugin at <target series>_<n> with the
                 expected origin and ABI, reject removals and downgrades in the
                 dry-run plan and clear their marker only after success. 01.2's
                 BIND worker is the model.
Contracts:       PLUGIN os-dhcp-interface-ha, origin
                 opnsense/os-dhcp-interface-ha; pkg /usr/local/sbin/pkg-static;
                 provider /usr/local/opnsense/scripts/firmware/repos/ResolverPluginsHA.sh;
                 marker /var/db/os-dhcp-interface-ha/repository-reconcile.pending;
                 lock /var/run/os-dhcp-interface-ha-reconcile.lock;
                 log /var/log/os-dhcp-interface-ha-reconcile.log; arguments
                 --retry and --retry-attempts (default 3).
Files:           net/dhcp-interface-ha/src/opnsense/scripts/dhcp_interface_ha/repository_reconcile.py
                 net/dhcp-interface-ha/pkg-descr
Work:            1. Write the worker (mode 0644, shebang /usr/local/bin/python3)
                    with BIND's flow after 01.2: series from opnsense-version
                    -a; managed-file check (both shapes); run the provider; pkg
                    config ABI; `update -f -r resolver-plugins` with
                    FETCH_TIMEOUT=15 and FETCH_RETRY=1; candidate via rquery -r
                    resolver-plugins with origin, ABI, version and series
                    checks; dry run, "already installed" handling and
                    validation; install; installed identity check; unlink the
                    marker; non-blocking lock and per-attempt log lines.
                 2. Leave out BIND-only parts: os-bind refusal, BIND restart,
                    environment overrides, retry delay.
                 3. pkg-descr: add above "0.1" a "26.7" section with
                    "* Move the package feed and upgrade the plugin
                    automatically across OPNsense series upgrades".
External inputs: opnsense-version, pkg config ABI, pkg query and rquery, the
                 dry-run output, the repository file.
Done when:       The worker meets the shared contract with HA's names, has no
                 BIND logic, and is under 180 lines.
Out of scope:    The hooks and provider (01.3), the controller.
```

## 01.5 Fresh install without runtime packages

```
Goal:            A new installation configures the managed repository without
                 priority and installs os-bind-rp with pkg install.
Context:         Spec Decisions: "Retire the fresh-install script in favor of
                 install-repository.sh plus pkg install, because its purpose was
                 the runtime BIND update." The old installer's last step is a
                 plain pkg install of os-bind-rp, after which pkg's conflict
                 handling has removed official os-bind.
Contracts:       Configuration as at the top of this file; install-repository.sh
                 keeps its fingerprint check.
Files:           scripts/install-repository.sh
                 README.md (sections "Configure the package repository" and
                 "Install a plugin")
                 scripts/install-os-bind-rp.sh (delete)
                 scripts/install-os-bind-rp-prerequisites.py (delete)
Work:            1. Remove the `  priority: 20,` line from install-repository.sh
                    and from the README's manual setup.
                 2. Under the README's "### BIND" install, replace the runtime
                    and migration-installer paragraph and its curl block with:
                    "`pkg` takes BIND from any enabled repository that meets the
                    plugin's minimum version, normally OPNsense's own, and
                    replaces an installed official `os-bind`."
                 3. git rm the two scripts.
External inputs: none added.
Done when:       No priority line in either configuration, the README installs
                 BIND with `pkg install os-bind-rp` only, both scripts gone.
Out of scope:    docs/package-repository.md (phase 08) and old CI callers of the
                 removed scripts (removed in phase 08).
```

## Notes

- Drop `priority: 20`, over keeping it, because it existed to prefer this
  project's runtime packages, which are retired (2026-10-19).
- 26.1 is left unchanged: it is retired and its clients cross into a feed that
  still has the runtime packages (2026-10-19).
- HA copies BIND's provider shape and managed-URL list, over sharing one file,
  so neither package owns a file the other needs (2026-10-19).
- Open question for the user: plugin manifests declare `abi: "FreeBSD:*:*"`
  (Mk/plugins.mk). Both workers compare the candidate's `%q` with `pkg config
  ABI`; if pkg reports the wildcard, every series upgrade is refused. The
  existing BIND check is kept unchanged until this is confirmed on a client;
  decide before building 01.2 and 01.4 (2026-10-20).
- `dns/bind/tests/test_repository_reconciliation.py` and
  `test_repository_migration.py` still assert the old priority and runtime
  behavior. They stay unchanged in this phase: the release branches lack most of
  `dns/bind/tests`, so a tests change would make the first carry fail until the
  carry question in phase 03 is decided (2026-10-20).
