# Phase 07: Client series upgrade

Status: planned
Before: the new CI is built (phases 01–06). `master`'s `dns/bind` holds the
published 26.7_9 BIND plugin (commit `8c640b214`). Its Python worker
`bindRepositoryReconcile.py` (274 lines) requires this project's `bind920` and
`bind-tools` packages, parses pkg's output and re-checks identities at several
points; the managed repository configuration carries `priority: 20`; HA has no
upgrade hooks; fresh installs use `scripts/install-os-bind-rp.sh` (861 lines).
After: on the plan branch (`master` after 09.3), both plugins carry their clients across an OPNsense series
or FreeBSD base upgrade with short POSIX `sh` workers that rely on `pkg` for
signatures, ABI and dependencies, including BIND; the managed configuration has
no priority line; fresh install is `install-repository.sh` plus `pkg install`.

Rules: this phase changes plugin code, not CI, and is the last build phase
because CI is the primary work. Keep the code minimal and let `pkg` do what it already does: check
the catalogue signature, select packages for the running ABI, resolve
dependencies and refuse what it cannot install. Nothing is run: review by
reading the diff and `git diff --check`. Commit once per phase with
`[skip ci]`; nothing is pushed. The release branches are not touched; the new
pipeline carries these changes into them after activation (phase 09).

Shared references: design, "Client series upgrade" table
([design](../ci-rebuild-design.md)); specification, "Series upgrade" and
"BIND runtime" ([spec](../ci-rebuild-spec.md)). The spec's sequence: (1) the
pre-upgrade hook marks reconciliation pending and returns; (2) the repository
provider points recognized managed configurations at the target feed; (3) after
boot the start hook launches reconciliation, which "refreshes the signed feed,
validates identity, ABI and series, checks the transaction plan and installs
the target plugin. Pending clears only on success; a failure stays pending and
logged without blocking boot or firmware. Keep the required BIND template and
service refresh."

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

Worker contract (both plugins), shared by 07.2 and 07.4:

The worker is POSIX `sh` with `set -u` (not `-e`); a `fail()` function appends
`<date> <reason>` to the plugin's log and exits 1, and every external command
that can fail is followed by `|| fail "<reason>"`.

1. Exit 0 at once when the plugin's pending marker is absent.
2. series = `opnsense-version -a`; check 1: it matches `^[0-9]+\.[0-9]+$`.
3. Run the plugin's provider, then `pkg-static update -f -r resolver-plugins`
   (pkg verifies the signed catalogue).
4. candidate = `pkg-static rquery -r resolver-plugins '%o %v %q' <plugin>`;
   check 2: the origin is the plugin's, the version starts with `<series>_` and
   the ABI equals `pkg-static config ABI`.
5. plan = `pkg-static install -n <plugin>-<version>`, accepting exit status 0
   or 1; check 3: refuse a plan containing `REMOVED:` or `DOWNGRADED:`.
6. `pkg-static install -y <plugin>-<version>`.
7. Plugin-specific refresh, if any (07.2), before the marker is touched.
8. Remove the marker only when `pkg-static query '%v' <plugin>` equals the
   candidate version.

A failure leaves the marker and the log line. The start hook runs the worker
in the background through `daemon`, so boot never waits: up to three attempts,
30 seconds apart, each under `lockf -t 0 <lock>`.

## Stages

| Stage | Goal | Model | Effort | Order | Status |
| --- | --- | --- | --- | --- | --- |
| 07.1 | BIND provider writes the configuration without priority | haiku | low | first, parallel with 07.2 and 07.5 | planned |
| 07.2 | BIND worker rewritten in sh on pkg | sonnet | medium | first, parallel with 07.1 and 07.5 | planned |
| 07.3 | HA upgrade hooks and provider | sonnet | low | after 07.1 and 07.2 | planned |
| 07.4 | HA worker | sonnet | low | after 07.2, parallel with 07.3 | planned |
| 07.5 | Fresh install without runtime packages | haiku | low | first, parallel with 07.1 and 07.2 | planned |

## 07.1 BIND provider without priority

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
Done when:       The diff is those two lines.
Out of scope:    The URL list, warnings, marker handling, dns/bind/tests.
```

## 07.2 BIND worker on pkg

```
Goal:            BIND's reconcile worker is a short POSIX sh script that meets
                 the worker contract and refreshes BIND when the transaction
                 changed it.
Context:         The current worker is bindRepositoryReconcile.py (274 lines of
                 Python): it requires bind920 and bind-tools candidates from this
                 project's feed, compares versions, parses the dry-run plan for
                 unexpected names, re-reads identities and implements its own
                 lock and retries. Spec: "The reconcile worker relies on native
                 dependency resolution for BIND". The start hook
                 dns/bind/src/etc/rc.syshook.d/start/50-bind-rp-repository
                 launches it with daemon and --retry options.
Contracts:       Worker contract above, with plugin os-bind-rp, origin
                 opnsense/os-bind-rp, marker
                 /var/db/os-bind-rp/repository-reconcile.pending, log
                 /var/log/os-bind-rp-reconcile.log, lock
                 /var/run/os-bind-rp-reconcile.lock, provider
                 /usr/local/opnsense/scripts/firmware/repos/ResolverPlugins.sh,
                 installed worker
                 /usr/local/opnsense/scripts/OPNsense/Bind/bindRepositoryReconcile.sh.
                 BIND-only additions: before step 5, refuse when official os-bind
                 is installed (pkg-static info -e os-bind); record
                 `pkg-static query '%v' bind920` before step 6; in step 7, when
                 it differs, run `configctl template reload OPNsense/Bind` and
                 `configctl bind restart`, each `|| fail`.
Files:           dns/bind/src/opnsense/scripts/OPNsense/Bind/bindRepositoryReconcile.sh (new, 0755)
                 dns/bind/src/opnsense/scripts/OPNsense/Bind/bindRepositoryReconcile.py (delete)
                 dns/bind/src/etc/rc.syshook.d/start/50-bind-rp-repository
                 dns/bind/pkg-descr
Work:            1. Write the sh worker: set -eu, a fail() function that logs
                    and exits 1, then the contract steps in order.
                 2. Start hook: drop the OS_BIND_RP_* overrides (fixed paths);
                    when the marker exists, run in the background `daemon sh -c
                    'for attempt in 1 2 3; do lockf -t 0 <lock> <worker> && exit
                    0; sleep 30; done; exit 1'` with output discarded; exit 0.
                 3. git rm the Python worker.
                 4. pkg-descr: add as the first bullet under "1.36": "* Take
                    BIND from any enabled repository when upgrading the plugin
                    after a series upgrade".
External inputs: opnsense-version, pkg rquery and dry-run output.
Done when:       The worker is POSIX sh under 60 lines with no bind920 or
                 bind-tools candidate lookup, no parsing beyond the contract's
                 checks 1–3 and the installed-version comparison, no own lock or
                 retry loop; the start hook holds the retries, pause and lockf;
                 the Python worker is gone.
Out of scope:    The provider, the upgrade hook, dns/bind/tests, the Makefile.
```

## 07.3 HA upgrade hooks and provider

```
Goal:            HA has the pre-upgrade marker hook, the start hook and its own
                 repository provider, independent of BIND's files.
Context:         Design, "Client series upgrade": HA's files are
                 etc/rc.syshook.d/upgrade/50-dhcp-interface-ha-repository,
                 opnsense/scripts/firmware/repos/ResolverPluginsHA.sh,
                 etc/rc.syshook.d/start/50-dhcp-interface-ha-repository, and the
                 worker; marker
                 /var/db/os-dhcp-interface-ha/repository-reconcile.pending.
                 BIND's files, after 07.1 and 07.2, are the model.
                 net/dhcp-interface-ha/src/etc/rc.syshook.d/start/ already holds
                 another hook, which stays.
Contracts:       Provider output and managed shapes as at the top of this file,
                 identical to BIND's, so either provider may run. Fixed paths;
                 no environment overrides.
Files:           net/dhcp-interface-ha/src/etc/rc.syshook.d/upgrade/50-dhcp-interface-ha-repository
                 net/dhcp-interface-ha/src/etc/rc.syshook.d/start/50-dhcp-interface-ha-repository
                 net/dhcp-interface-ha/src/opnsense/scripts/firmware/repos/ResolverPluginsHA.sh
Work:            1. Upgrade hook (sh, 0755): mkdir -p the marker directory,
                    touch the marker, ignore failures, exit 0.
                 2. Start hook (sh, 0755): BIND's start hook from 07.2 with HA's
                    marker, lock /var/run/os-dhcp-interface-ha-reconcile.lock and
                    worker
                    /usr/local/opnsense/scripts/dhcp_interface_ha/repository_reconcile.sh.
                 3. Provider (sh, 0755): copy BIND's ResolverPlugins.sh with its
                    three path variables set to fixed paths (HA's marker),
                    mktemp "${repository_config%/*}/.${repository_config##*/}.XXXXXX"
                    instead of the directory/name case block, and mkdir -p
                    "${pending_marker%/*}" instead of the marker-directory test.
External inputs: opnsense-version output and the repository file, checked as
                 BIND's provider does.
Done when:       Three new executable files; no name shared with BIND.
Out of scope:    The worker (07.4), HA's Makefile and controller.
```

## 07.4 HA worker

```
Goal:            HA's reconcile worker meets the worker contract.
Context:         07.2's BIND worker is the model.
Contracts:       Worker contract above, with plugin os-dhcp-interface-ha,
                 origin opnsense/os-dhcp-interface-ha, marker
                 /var/db/os-dhcp-interface-ha/repository-reconcile.pending, log
                 /var/log/os-dhcp-interface-ha-reconcile.log, provider
                 /usr/local/opnsense/scripts/firmware/repos/ResolverPluginsHA.sh.
Files:           net/dhcp-interface-ha/src/opnsense/scripts/dhcp_interface_ha/repository_reconcile.sh (new, 0755)
                 net/dhcp-interface-ha/pkg-descr
Work:            1. Write the worker: BIND's without the os-bind refusal and the
                    BIND refresh, with HA's names.
                 2. pkg-descr: add above "0.1" a "26.7" section with "* Move the
                    package feed and upgrade the plugin automatically across
                    OPNsense series upgrades".
External inputs: as 07.2.
Done when:       POSIX sh under 50 lines, no BIND logic.
Out of scope:    The hooks (07.3), the controller.
```

## 07.5 Fresh install without runtime packages

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
Out of scope:    docs/package-repository.md (phase 08).
```

## Notes

- Client and install changes come after the CI phases, because the CI rebuild is
  the primary work (user decision, 2026-10-20).
- The workers rely on `pkg`: it checks signatures, resolves dependencies and
  refuses what it cannot install. The workers keep only the spec's checks
  (series, origin, version prefix and ABI of the candidate, a plan without
  removals or downgrades, the installed version) and move locking and retries
  to `lockf` and the start hook (user direction, 2026-10-20).
- The start hook pauses 30 seconds between attempts so a slow network at boot
  does not use up all three at once (2026-10-20).
- The workers are POSIX sh (`bindRepositoryReconcile.sh`,
  `repository_reconcile.sh`; the design's table is updated), because relying on
  pkg leaves thin orchestration that fits in a few lines of shell; Python is
  approved only for operations that cannot, after written justification (user
  decision, 2026-10-20).
- The workers no longer require the repository file's exact managed shape: the
  provider owns that file and rewrites only managed shapes (2026-10-20).
- Drop `priority: 20`, over keeping it, because it existed to prefer this
  project's runtime packages, which are retired (2026-10-19).
- 26.1 is left unchanged: it is retired and its clients cross into a feed that
  still has the runtime packages (2026-10-19).
- HA copies BIND's provider shape and managed-URL list, over sharing one file,
  so neither package owns a file the other needs (2026-10-19).
- The ABI comparison works: although plugin manifests declare
  `abi: "FreeBSD:*:*"`, `pkg create` records the build machine's ABI. On
  opnsense-ha-2 (OPNsense 26.7.5, pkg 2.8.4) the published 26.7 catalogue lists
  os-bind-rp 26.7_9 and os-dhcp-interface-ha 0.2_45 as `FreeBSD:15:amd64`, and
  the installed os-bind-rp reports the same (2026-10-20).
- `dns/bind/tests/test_repository_reconciliation.py` and
  `test_repository_migration.py` assert the old worker's behavior. Updating
  plugin tests is not part of this plan; the carry excludes tests (phase 02),
  so the maintainer can update them on `master` at any time (2026-10-20).
