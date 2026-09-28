# HA DHCP Interface UI repair implementation plan

This is the historical 0.2 repair and deployment record. For upcoming work,
start with the [UI streamlining specification](dhcp-interface-ha-ui-streamlining-spec.md)
and [implementation plan](dhcp-interface-ha-ui-streamlining-plan.md). Those
documents supersede manual-setup and presentation instructions here; preserve
the recorded tests and deployment evidence rather than rerunning or undoing old
steps merely because they appear in this history.

Status: R0–R5 source changes are present; browser acceptance and native R6
qualification remain open. HA-1 and HA-2 now run 0.2_8, both enabled;
the latest recorded check has HA-1 in maintenance and HA-2 active with a DHCP
address. See the [deployment records](#ha-1-installation--2026-09-27).
The original repair contract is
[sections 21–22 of the design](dhcp-interface-ha.md#21-uiapi-contract-for-the-repair);
use the streamlining specification for the next increment.
Use this record to review the existing implementation and close the acceptance
checks; do not reimplement a step only because its original instructions remain
below. Code presence does not mean the acceptance step passed.

This document records the 0.2 UI repair and its 0.2_1 local-assignment follow-up.
The subsequent API-coordinated handoff increment was withdrawn; the restored
baseline retains this repair's UI, atomic save and native qualification requirements.

## 1. Outcome and scope

An administrator can configure one shared IPv4 DHCP connection on an existing
CARP pair, choose each node's logical assignment and local adapter, understand which node can carry
traffic, follow migration and removal steps, and distinguish known observations
from unavailable peer information. Settings, Status and Diagnostics use native
OPNsense UI conventions. Native CARP remains the only election authority.

This repair covers UI structure, transactional local saving, structured status,
guided setup/removal, read-only diagnostics, and the associated integration tests.
It does not implement delayed failback, VLAN execution, automatic remote peer
inspection, lease replication, automatic native interface reassignment or release
infrastructure generalization. These are explicit separate work items, not hidden
prerequisites to showing an honest and usable UI.

## 2. Baseline and known incident

The current source is under `net/dhcp-interface-ha/`. Earlier experimental names
were `os-wan-ha-dhcp`, `WanHaDhcp` and `wanha0lagg`. Preserve the current package,
namespace, stable device name and the old-package conflict. Do not implement a
second rename or automatically migrate old package settings in this repair.

On HA-2, OPNsense **26.7.3_11**, the boot log contained:

```text
Fatal error: Uncaught Error: Call to undefined function mwexec()
  in /usr/local/etc/inc/plugins.inc.d/dhcp_interface_ha.inc:67
#0 interfaces.inc: dhcp_interface_ha_prepare_device('dhcpha0lagg')
#1 rc.bootup: interfaces_configure(true)
```

This occurred even with an unassigned plugin device. Core prepares registered
unassigned devices near the end of `interfaces_configure()`. A disabled plugin
checkbox did not prevent execution. Uninstalling the plugin and rebooting restored
boot, SSH and HTTP 200 from the web UI.

The working source now calls `mwexecf()`. The actual PHP callback has a regression
test for successful/failed preparation and an unrelated device. All **54 tests**
passed at that earlier baseline. A harmless in-memory callback check on HA-2
confirmed the native helper exists; its command was replaced with `/usr/bin/true`.
That check did not create a device or qualify boot with the repair. HA-2 was later
updated to `os-dhcp-interface-ha-devel-0.2_1`, briefly to 0.3, and restored to
0.2_1 on 2026-09-27. It remains disabled with its owned device verified detached.
These package changes do not qualify enabled boot or paired behavior.

Do not discard the existing hook fix, its regression test, or the user's unrelated
`AGENTS.md` edits. Inspect the working diff first; do not assume these changes are
already committed. No worktrees, upstream OPNsense PRs or unrequested publishing.

## 3. Implementation record

The local source now contains the R0–R5 repair shape:

- **R0:** the boot callback calls supported `mwexecf()` and its PHP test covers
  success, failure and an unrelated device.
- **R1:** Settings GET returns both model roots and a revision; `settings/set`
  validates both roots under one config lock, writes once, unlocks, then applies.
  The independent Shared/Local write controllers are removed. Candidate model
  validation no longer performs backend discovery.
- **R2:** root status reduces CARP/attachment state; Status API normalizes
  allowlisted observations, readiness, source errors, carrier preview and
  removal readiness. DHCP lease details explicitly remain unavailable.
- **R3:** the source view contains one Settings form and Settings/Status/
  Diagnostics tabs with status, diagnostics, fixed native links and a snapshot.
- **R4:** a separate guarded `prepare_setup` root action, computed local
  checklist, migration interruption guidance, peer verification instructions
  and removal readback are present.
- **R5:** browser handlers serialize carrier previews, preserve submitted values,
  handle save/apply outcomes, refresh one status request at a time, and mark
  failed observations stale.
- **R6:** not run. No target OPNsense MVC rendering, browser session, package
  installation, boot, or appliance was used for this implementation pass.

The PHP settings harness executes the real controller source with stubbed core
models, Config and Backend classes. It checks one combined save, stale revision,
disabled drafts, detached identity edits, attached-path rejection, read-only
denial and command ordering. It does not exercise native model serialization,
the real config file/audit count, or native MVC routing. The PHP status harness
likewise uses core stubs. There is no browser runner or browser-handler smoke
test in this repository; actual form population, page request ordering,
validation rendering, expired-session recovery and tab/poll behavior remain
unqualified. A jsdom dependency could not be retrieved in this environment (npm
registry returned HTTP 403), so no untested dependency or harness is retained.
Do not mark A02–A05, A08, A10–A14 complete from syntax or stub-harness checks
alone.

## 4. Read these implementation boundaries

Paths in this table are relative to the repository unless marked core. Modify an
existing component where it already owns the behavior.

| Boundary | Current files and intended responsibility |
|---|---|
| Plugin boot integration | `net/dhcp-interface-ha/src/etc/inc/plugins.inc.d/dhcp_interface_ha.inc`: supported helper, device registration, shared-only XMLRPC registration. |
| Root controller | `net/dhcp-interface-ha/src/opnsense/scripts/dhcp_interface_ha/{core.py,runtime.py,dhcp_interface_ha.py}`: observations, eligibility, verified topology and mutations under the existing lock. |
| Configd | `net/dhcp-interface-ha/src/opnsense/service/conf/actions.d/actions_dhcp_interface_ha.conf`: fixed commands for status/apply and explicit setup preparation. |
| Models | `net/dhcp-interface-ha/src/opnsense/mvc/app/models/OPNsense/DhcpInterfaceHa/{Shared,Local}.{php,xml}`: retain separate mounts; validate candidate values together. |
| Settings API | Add `Api/SettingsController.php` beside the existing API controllers; replace independent write endpoints with the combined transaction. |
| Observation API | Existing `Api/StatusController.php`: normalize native observations and expose allowlisted status, readiness and carrier previews. Remove its independent role reduction. |
| Mutation API | Existing `Api/ServiceController.php`: authorized POST actions with explicit outcomes; no kernel mutation in PHP. |
| Page | Existing `IndexController.php`, `forms/*.xml`, and `views/OPNsense/DhcpInterfaceHa/index.volt`: native tabs, unified form, status, guidance, diagnostics. A small separate JS asset is reasonable if needed to test actual handlers. |
| Permissions/navigation | Existing model `ACL/ACL.xml` and `Menu/Menu.xml`; preserve menu location and apply normal read-only enforcement to every mutation. |
| Tests | Existing `net/dhcp-interface-ha/tests/`; tests must execute behavior, not grep for labels or API names. |
| CI | `.github/workflows/dhcp-interface-ha-tests.yml`: run new behavioral checks with explicit required runtimes. CI helper changes follow `docs/building.md` verification and remain separate from publication workflows. |

The local core reference reviewed for this plan was commit
`480f1916478d75f1c5d3c09e6b799871462565ce`. It is a reference, not a replacement for
the target's immutable build provenance. Verify functions and signatures against
the target series before use. Relevant core sources:

- `src/etc/inc/util.inc`: `mwexecf()` and supported command helpers.
- `src/etc/inc/interfaces.inc`: invocation of assigned and unassigned devices.
- `src/opnsense/mvc/app/controllers/OPNsense/Base/ApiMutableModelControllerBase.php`:
  native locking, validation, saving and permission conventions.
- `src/opnsense/mvc/app/library/OPNsense/Core/{Config,Backend}.php`: config lock
  lifetime and explicit backend timeouts. Do not hold the config lock across
  any configd/config-reading child call, including validation-time discovery.
- `src/opnsense/mvc/app/controllers/OPNsense/Interfaces/Api/OverviewController.php`:
  native address, gateway and Autoconf observations.
- `src/opnsense/scripts/routes/gateway_status.php`: unmonitored gateways can be
  returned with status `none`/Online; that is not a reachability measurement.
- `src/opnsense/mvc/app/controllers/OPNsense/Core/Api/HasyncStatusController.php`:
  native HA inspection. Its remote start/stop/restart actions perform sync and
  template reload; they MUST NOT be reused as read-only peer probes.
- `src/opnsense/mvc/app/views/layout_partials/base_form.volt`,
  `src/opnsense/www/js/{opnsense,opnsense_ui}.js`: actual rendering, AJAX, form
  mapping and validation behavior. PHP syntax lint cannot validate a Volt page.

## 5. Ordered implementation steps

Each step must leave its behavioral checks passing. Do not skip to cosmetic
layout work while preserving unsafe save semantics. Steps R1 and R2 provide the
API contracts consumed by R3–R5. R6 validates the assembled plugin.

### R0 — Preserve and validate the boot repair

1. Confirm the supported helper fix and reproduce the old fatal error by running
   the callback test against the old helper in a temporary copy, not by reverting
   or installing the broken package.
2. Keep success, nonzero preparation exit and unrelated-device behavior covered.
   Failed preparation logs a useful error and returns null; it must not terminate
   the PHP caller. Do not catch-and-hide an unknown function with a success result.
3. Verify native helper availability and invocation with the target PHP/core in
   an isolated process using a harmless command fixture. Normal PHP lint is also
   required, but is not the compatibility test.
4. Maintain the ownership, timeout and transition-lock checks in the Python
   prepare path. Do not weaken them to turn a failed callback green.

Exit: acceptance A01 passes. Installed-package boot remains R6 work.

### R1 — One validated local configuration transaction

Implement spec section 21.7 in `SettingsController` and the existing models:

1. One GET maps both form roots and a deterministic revision of their persisted
   scalar values. Reuse native select option representation. No disk mutation.
2. One POST accepts both roots. Enforce native write permission and collect any
   required runtime evidence before taking the config lock. Then lock/reload
   current models and reject a stale revision before any write. Validate against
   current locked native configuration plus that evidence. If enablement/identity
   change needs unavailable or older-than-15-second evidence, reject/recheck after
   unlocking. Do not call configd or a config-reading subprocess inside the lock.
3. Extract PHP cross-model checks into the smallest function that accepts both
   candidate models and observations. It must not construct an old persisted
   counterpart or invoke a backend during candidate/serialization validation.
   Retain normal field validators and the root controller's independent safety
   validation. PHP callbacks must consume already collected observations.
4. Permit disabled incomplete drafts and disable-only saves when local discovery
   is failing. Reject live identity changes until a separate disable/fence step
   succeeds and a fresh read verifies the owned path is detached or absent. A
   failed earlier fence is not authorization to change identity. Preserve an
   invalid existing value visibly until explicitly corrected.
5. Validate both candidates before serializing either; save both XML sections
   in one native config save/audit operation. Always release the lock, including
   rejection/error paths. Then invoke apply through the existing root boundary.
6. Return the exact saved/applied/validation/conflict response contract. Failed
   apply never causes a hidden config rollback. A later Retry Apply does not save
   the form a second time. Expose no independent shared/local write bypass.

Use the native pattern for any serialization validation bypass only after both
candidates have been completely validated; do not broadly disable model checks.
Keep the node-local carrier out of XMLRPC synchronization. XMLRPC remains a
native independent writer; the root controller must reject unsafe received
settings on the receiver.

Exit: A02–A05 and the save portions of A14 pass. Observe the saved file/audit
count and command trace, not only a controller's returned `saved` string.

### R2 — Structured observations and an explicit state reducer

1. Extend root status with observed CARP role even when disabled, expected/live
   instance alignment, structured local readiness, verified actual/desired
   attachment, and controller running/stopped observations. Use the same
   eligibility rules as reconciliation. Do not infer eligibility in Javascript.
2. Implement the ordered state table in spec 21.3 as a small pure transformation
   of the status observation. No new persisted state or independent election.
3. Normalize optional DHCP/address/gateway and local HA context with the native
   sources in section 3. Scope addresses/gateways to the managed interface;
   validate shapes before using them. Return availability and source instead of
   treating failed decoding as an empty, healthy result.
4. Enforce the 15-second total observation deadline and five-second per-call cap
   across root and PHP collection. Reuse collected inventory where possible. Stop
   optional work when the remaining budget expires. A lost configd connection
   must not cause multiple serial 120-second waits or accumulate PHP workers.
5. Generate structured readiness codes and fixed next-action identifiers. Include
   checks required by the spec and distinguish saved-draft progress from enabled
   safety failures. Expose unverified peer readiness and source-labeled native
   pfsync/XMLRPC facts; send no peer network requests.
6. Add a carrier preview for a requested existing logical interface. Return its
   identifier, assignment and MAC suggestion together with eligible/blocked
   carriers. Preview uses unsaved selection only for display and never for root
   controller mutation. Preserve saved missing/ineligible carriers explicitly.

Exit: A06–A10 pass. Healthy local observation is usable even when an optional
gateway or pfsync observation fails. Required-source failure is visibly Unknown.

### R3 — Native Settings, Status and Diagnostics tabs

1. Retain the existing menu and base route; implement the three anchors/default
   and native tab/form layout in spec 21.1. Use a single settings form with the
   `dhcphashared.*` and `dhcphalocal.*` field IDs. The managed-interface field
   is `dhcphalocal.managed_interface`; carrier is also local. Only enablement,
   MAC and stored failback delay belong in Shared.
2. Put editable connection settings first in Settings. Mark shared/local scope
   beside the relevant fields. Show the current and intended interface mapping.
3. Add MAC actions beside the MAC field. A generated/copied MAC is unsaved input.
   Native spoof settings are changed only on their native page. Load the new
   selected interface's MAC suggestion, not the old saved interface's value.
4. Remove the editable failback control. Present native preemption policy and
   the unavailable feature plainly. If stored delay is nonzero, show the value
   and an explicit reset-to-zero action before enabling; no on-load rewrite.
5. Render Status using the backend state/reason, verified attachment, controller,
   current address/gateway, and the separate HA evidence display. Use clear
   Standby and Unknown presentations. Render detailed checks in Diagnostics.
6. Add fixed native links, explicit Reconcile now, and an allowlisted diagnostic
   snapshot download. No automatic peer connection, raw config dump or new
   failover/force-attach controls.

Exit: A11, A12 and the rendering/security parts of A14 pass. Verify actual form
population and rendering; screenshots alone do not prove that save works.

### R4 — Guided setup and return to ordinary networking

1. Implement the computed checklist in spec 22.3. Every incomplete stage has an
   observation, a reason and a concrete native link or explicit plugin action.
   Native changes require Recheck setup after returning. Do not persist progress.
2. Expose `POST service/prepare` through a fixed configd action to a guarded root
   setup-preparation operation. Under the existing transition lock, read fresh
   configuration and reject enabled state, existing attached members or an
   unverified same-name device. Call the existing preparation implementation and
   verify the result is owned, failover and detached before reporting success.
3. Keep ordinary boot `prepare` valid for enabled configurations: boot must create
   the detached abstraction before native interface setup. Use a separate
   `prepare_setup` CLI/configd operation for the explicit UI guard; do not add a
   disabled-only guard to the boot entry point by mistake.
4. Explain the expected interruption before native reassignment or disabling a
   migrated interface. Identify the interface and preserve the instruction to
   use another management path/console when necessary. Never assume WAN is the
   only interface that can carry the administrator's connection.
5. Implement removal guidance with all-assignment checks, fencing readback and
   native Firmware/Plugins link. Preserve the package guard and show why simply
   disabling or deleting the package does not restore original assignments.
6. Link the manual peer verification step to native HA Status and explain what
   must be checked on the other node. Leave remote readiness explicitly unverified.

Exit: A10, A13–A15 pass. No GET/page load/preview changes configuration or devices.

### R5 — Complete asynchronous behavior and error recovery

1. Submit Settings with one POST; suppress duplicate clicks and show progress.
   Treat validation, stale-revision conflict, save failure, saved/apply failure
   and transport uncertainty as distinct outcomes. Preserve dirty fields.
2. After a transport timeout, read persisted settings before concluding whether
   saving happened; do not overwrite the user's input or blindly resubmit. Show
   changed saved values alongside unsaved input when reconciliation is needed.
3. Refresh status after apply outcomes. Poll only the visible Status tab, five
   seconds after completion, at most one request per page. Stop while hidden and
   refresh on return; no independent per-widget intervals.
4. Use the specified AJAX timeout and stale/unavailable presentation. Make
   backend error, HTML login response and malformed JSON visible. Never leave
   placeholders or a green stale status indefinitely after failure.
5. Ignore late carrier-preview responses for an earlier logical selection.
   Restored controls, inline validation, focus handling and text-only rendering
   must work for every request failure path and for read-only sessions.

Exit: A08 and A11–A14 pass using the real page handlers with controlled endpoint
responses, including delayed/out-of-order completion and an expired session.

### R6 — Native MVC, package and boot qualification

1. Run all focused local checks and real MVC/PHP/Volt rendering on the target
   framework. Stubbing every core class cannot establish compatibility. A local
   fixture can substitute configd command responses while retaining real model,
   controller and form behavior.
2. In a designated disposable OPNsense environment with console/snapshot recovery,
   exercise the installed package with disabled/unassigned settings, then disabled
   assigned test interface, and controlled preparation failures. Verify full boot
   completion, management SSH/HTTP response, no repeated fatal PHP errors, and
   detached device state. Do not select a production management adapter for this.
3. Render all tabs, save a disabled draft, perform explicit preparation/recheck,
   verify server-side read-only denial, and compare native configuration before
   and after observational requests. Test an enabled BACKUP on an isolated pair
   before claiming the Standby path is appliance qualified.
4. Verify uninstall rejection with an assignment and normal removal after native
   reassignment. Exercise upgrade preservation if lifecycle code is changed.
5. Record actual versions, exact source/package revision, commands, pass/fail and
   remaining limits in the design's evidence section. Update the plugin README
   to describe the implemented workflow; remove obsolete two-form save examples.

This plan authorizes implementation documentation, not a new installation/reboot
on HA-2 or a package publication. Follow the maintainer's explicit target/task
authorization for appliance changes. Produce a reviewable artifact and test
results first. Do not call isolated helper tests, Linux unit tests or HTTP 200
alone an installed-package boot pass.

Exit: A16 passes in the documented test environment. Gates A–D and full production
pair qualification remain separate; report them as outstanding when not run.

## 6. Required acceptance scenarios

These are behavioral scenarios, not a required one-test-per-row structure. Reuse
the highest useful existing seam. Each retained test must protect its stated
observable behavior. Do not add Markdown wording or substring-presence tests.

| ID | Setup/action | Required evidence |
|---|---|---|
| A01 | Execute real PHP boot callback with current helper API; preparation returns 0 then nonzero; call with unrelated device. | Correct return/log behavior, no fatal PHP, no command for unrelated device. The pre-fix callback fails this check. |
| A02 | Change local carrier and shared MAC together; inject invalid shared value, then valid values. | Rejection leaves both persisted sections/audit count unchanged. Success creates one config save containing both new values, then apply. No intermediate file with only one change. |
| A03 | Another writer changes saved plugin settings after GET; submit old revision. Inject persistence failure. | Stale revision rejected without overwrite/apply. Persistence failure not reported as saved. Config lock released on each path; never held during any configd/root-status discovery, validation or apply call. |
| A04 | Incomplete disabled setup with static interface/empty carrier/MAC; disable enabled settings with missing carrier or configd unavailable. | Draft can be recorded. Disable can persist; inability to verify fencing is clearly reported, never called safe. No hidden rollback. |
| A05 | Saved enabled; change interface/carrier/MAC, including simultaneous disable plus identity change. Then disable/fence separately and edit. | First request rejected without mutation; subsequent properly ordered change permitted. Root protection remains active after direct native/XMLRPC edits. |
| A06 | Feed the ordered state table with verified MASTER, BACKUP, maintenance, allow=0, INIT, missing VHID, unsafe attached BACKUP, unowned device and controller absent. | Exact state/reason from spec 21.3; no false Active/Standby. Disabled/unmigrated cases distinguished. Observed CARP role remains visible while disabled. |
| A07 | Active without address; Active with gateway down; gateway monitor disabled; BACKUP with retained address. | Correct connection labels. No carrier mutation/demotion caused by DHCP/gateway observations; retained standby address never implies upstream ownership. |
| A08 | Required backend fails; optional gateway fails; configd hangs; browser loses connection/session. | Unknown versus partial sections distinguished; server/browser deadlines hold; no request accumulation; stale data labeled and never green-current. |
| A09 | XMLRPC target set, synchronization selected, pfsync peer observed; repeat on secondary with no outbound target. | Sources shown separately, no invented remote plugin readiness/config equality, no peer traffic. Secondary not falsely rejected. |
| A10 | GET settings/status/carrier preview and download diagnostics repeatedly; inject missing device. | No config saves, interface commands, controller starts or peer calls. Missing device yields setup action, not hidden preparation. |
| A11 | Actual rendered form loads both roots; submit once/double-click; validation failure; apply failure after save; retry. | Correct populated controls; one transaction; progress recovers; values retained; saved/apply failure explained; Retry Apply does not resave. |
| A12 | Switch WAN to opt2; preview replies out of order; current saved carrier missing/reserved; generate/copy MAC. | Correct mapping/MAC source for opt2, late response ignored, unavailable saved value retained visibly, changes unsaved until explicit action. |
| A13 | Prepare while disabled, then race an enable or foreign/attached device before root lock/readback. | Only detached owned creation succeeds. Enabled/attached/foreign case rejected with no attach/adoption. Boot preparation still works for enabled configured device. |
| A14 | Read-only user calls mutation endpoints; names/messages contain HTML; export snapshot with secret-bearing input fixture. | Server denies mutations, UI text is escaped, snapshot excludes credentials/full config/unrelated details. No unsafe legacy setter route remains. |
| A15 | Change native assignments/settings between visits; another logical assignment still uses plugin device during removal guidance. | Checklist recomputed, interruption explained, remaining assignment blocks removal readiness; no automatic native edits or stale completed flag. |
| A17 | Same Shared config on nodes with `opt2`/`ix3` and `opt7`/`hn1`; legacy shared sync arrives after migration. | Each controller uses its own local assignment/carrier and the same MAC/device. Sync cannot retarget Local. Empty Local never falls back to Shared/WAN. Native rule/gateway references remain an explicit separate check. |
| A18 | Upgrade schema 1.0.0 in both model orders; repeat with existing, empty or unavailable selection, fresh install and later legacy XMLRPC. | Legacy local intent preserved once; existing local field wins; Shared drops the field; Local version gates subsequent import. Refresh/reject legacy API payloads. Use real MVC in-memory fixtures before deployment. |
| A16 | Install and reboot target package on disposable console-accessible OPNsense with disabled/unassigned and disabled/assigned test cases and preparation failure fixture. | Boot reaches normal completion; SSH/web UI accessible; plugin path detached; no fatal PHP; native page/API render works. Exact version/artifact evidence recorded. |

## 7. Verification and reporting

Run the existing suite and syntax checks as the baseline:

```sh
python3 -m compileall -q net/dhcp-interface-ha/src/opnsense/scripts/dhcp_interface_ha
python3 -m unittest discover -s net/dhcp-interface-ha/tests -v
find net/dhcp-interface-ha/src -type f \( -name '*.php' -o -name '*.inc' \) -exec php -l {} \;
git diff --check
```

Run shell syntax/ShellCheck for affected shell/package hooks as defined by the
existing workflow. Add the new behavioral UI/MVC test command to that workflow
and the README when implemented, explicitly provisioning its PHP/browser/runtime
requirements. A missing required framework/test dependency is an unexecuted gate,
not a skipped passing test. Use local Git/command/backend fixtures in CI; no live
HA appliance, credentials or external services in automated regression tests.

Prefer an existing browser/test runner if available. If none exists, a small
browser smoke harness with intercepted fixture API responses is justified by
A11–A14; keep it a development/test dependency and exercise the actual rendered
page/handlers. Test fixture rendering separately from real target Volt rendering
and describe the limits of each. Do not invent a production abstraction merely
to make unit tests easy.

At completion report: files and behavior changed; which A01–A16 checks ran and
their actual outcomes; native framework/appliance version and artifact; remaining
unqualified gates; whether any appliance was modified. Preserve unrelated work.
Do not claim the plugin is production-ready until the design's full acceptance
criteria and required package/release approvals are satisfied.

## 8. Local source-verification record — 2026-09-25

Environment: repository workspace on Linux, Python 3.13.14, PHP CLI 8.2.33 with
SimpleXML, Node 22.23.1 and ShellCheck 0.9.0. No OPNsense framework or browser
runner was available in this workspace.

| Check | Result |
|---|---|
| Python compile for controller scripts | Passed. |
| `python3 -m unittest discover -s net/dhcp-interface-ha/tests -v` | 63 tests passed. PHP controller/callback fixtures use local stubs; the runtime suite simulates interface commands. |
| PHP syntax lint for source and PHP fixtures | Passed for all 9 PHP/INC files. |
| POSIX shell syntax and ShellCheck for package/service hooks | Passed. |
| JavaScript syntax for the script extracted from the Volt view | Passed with Node. This does not prove native Volt rendering. |
| Five MVC XML files | Parsed successfully. |
| Workflow YAML parsing and `git diff --check` | Passed. The workflow was not run. |
| Browser-handler smoke test | Not run. No runner was installed; npm registry access for jsdom returned HTTP 403, so no new dependency or unverified harness remains. |
| Target OPNsense MVC, installed package, boot, HA pair | Not run. No appliance was modified. |

These results exercise portions of A01–A06, A13 and A14 through callback,
controller-stub and runtime fixtures. They do not complete those scenarios as
written, and they do not qualify A07–A12, A15 or A16. Native MVC rendering,
browser behavior and all appliance checks remain R6 work. The GitHub Actions
workflow now explicitly installs PHP CLI and XML support before running PHP
fixtures; it was not manually dispatched in this turn.


## 9. Independent local assignments — package 0.2_1

The managed logical assignment has moved from Shared to Local. The form, combined
API validation/revision, status/carrier preview and root runtime all read the local
field. NIC drivers, device numbers and OPNsense `optN` IDs may differ. Both sides
retain `dhcpha0lagg`, the shared MAC and connection to the intended segment.
See design section 5.2 for synchronization boundaries and upgrade requirements.

Historical implementation (removed in 0.2_32 for the fresh-install release):

Schema 1.1.0 migration preserves the legacy selection before Shared serialization,
works in either model order and does not overwrite an existing Local field.
It records Local's version so later legacy Shared XMLRPC cannot re-import the
peer's assignment. Runtime never uses the old shared field or an implicit WAN.
The UI labels both assignment and carrier as local, explains the independent
mappings, and rejects old form payloads instead of guessing their intended scope.

Verification added for A17/A18:

- Linux suite: 65 tests passed, including different local logical IDs/NIC drivers,
  immunity to a legacy shared assignment, missing-local fencing, local revision
  conflict, rejection of live assignment edits and rejection of legacy POST scope.
- Native migration fixture: ten cases passed on HA-2 using the actual OPNsense
  framework and synthetic in-memory configuration, with no save or interface
  commands. Covers both model orders and later legacy Shared sync in every case.
- The historical native migration fixture was removed with the migration code.


Two-node DHCP handoff and pfsync continuity with differing assignments remain
unqualified. Native XMLRPC rules, NAT and gateways still require correct logical
references on each node; this plugin does not translate those sections.


### HA-2 installation and verification — 2026-09-25

Target: `opnsense-ha-2.home.internal.bkwfamily.net`, OPNsense 26.7.3_11,
FreeBSD 15.1-RELEASE-p3 amd64. Installed `os-dhcp-interface-ha-devel-0.2_1`,
source archive SHA-256
`5765e6e8361552f1b3adc64ed84af6d2a3ccac6fc135f96c4ec56bcd68d0b9b7`
(`product_hash=5765e6e83615`). Package SHA-256:
`6b41c9c0ba21523ec0e1a8aac65bbcdc6aec8008734678bdc95b0308ac78ba1a`.
Built with native `make package` in temporary
staging and installed using `pkg install -y -U <package>`. No repository channel
or CI publication changed.

- Backups: `/root/dhcpha-upgrade-0.2.BdDFS3/` on HA-2 contains the pre-upgrade
  config and 0.1 package, the intermediate 0.2 config/package, and final 0.2_1
  package. A schema downgrade requires its matching config, reviewed against
  any subsequent administrator changes; do not blindly reinstall old code.
- Upgrade 0.1 → 0.2 migrated both models to 1.1.0 and preserved `opt7`/`hn1`,
  the shared MAC and disabled setting. The 0.2_1 update preserved those values.
  Native interface configuration matches the original backup exactly.
- `service dhcp_interface_ha onestatus` and `pkg check -s` passed. Root status
  reports DISABLED/FENCED, owned failover device with no members, BACKUP and no
  local error. No promotion or two-node failover was performed.
- Real installed Settings GET handler returns the selection under Local only.
  Real installed Status handler returns `result=ok`, `managed.identifier=opt7`,
  `device=dhcpha0lagg`, no IPv4 address, and no observation errors.
- Native XML form parsing reports the local field ID and label. Native Volt
  compilation passed. HTTP root returns 200; plugin path returns 302 to login.
  These CLI handler/form checks do not test session authorization or browser
  event handling. An authenticated browser workflow remains outstanding.
- The initial status check exposed native address placeholders (`address=null`,
  `bits=null`, family inet/inet6) for interfaces without addresses. Revision 1
  accepts those records and keeps malformed records unavailable. Fixture checks
  cover both the selected addressless interface and an unrelated addressless
  interface, so unrelated interfaces do not invalidate selected-interface data.
- No reboot was performed for this mapping change. The earlier repaired 0.1
  package was installed and rebooted successfully on HA-2 with disabled setup;
  that does not qualify a 0.2_1 boot or enabled pair behavior.

### Source and HA-2 rollback — 2026-09-27

The maintainer withdrew the API-coordinated handoff increment and requested
restoration of both source and HA-2. That increment was uncommitted. Resetting
to HEAD (`d5c0c67f2`) would also discard the earlier UI repair, boot fix and local
assignment work, so the exact pre-handoff source archive was restored instead:
`/tmp/dhcpha-localmapping-final.tar`, SHA-256
`5765e6e8361552f1b3adc64ed84af6d2a3ccac6fc135f96c4ec56bcd68d0b9b7`.
All 27 archived plugin files match, including all 22 production source files.
The restored package is `os-dhcp-interface-ha-devel-0.2_1`, product hash
`5765e6e83615`, with model schema 1.1.0.

Before rollback, HA-2 briefly ran 0.3 (`f34e2a72a16d`), disabled/fenced with
coordination off. Its 93 tests, 10 native migration cases, installed file checks,
read-only handlers and Volt compilation passed; it was not rebooted or pair
qualified. Its complete source, package and installation evidence remain in
`/root/dhcpha-upgrade-0.3.vBz066`. The withdrawn source, tests and design documents
were also saved locally under `/tmp/dhcpha-before-rollback-9061h7a1` before edits.

HA-2 rollback evidence and the fresh backup of 0.3 are in the root-private
directory `/root/dhcpha-rollback-0.2_1.DkechK`:

- `config-before.xml` and `os-dhcp-interface-ha-devel-0.3.pkg` preserve the
  appliance immediately before rollback.
- Installed the saved 0.2_1 package from
  `/root/dhcpha-upgrade-0.3.vBz066/rollback/` with `pkg install -y -f -U`.
  Its SHA-256 matches the original recorded package:
  `6b41c9c0ba21523ec0e1a8aac65bbcdc6aec8008734678bdc95b0308ac78ba1a`.
- Stopped the controller, installed the package, restored only the two plugin
  model sections from the pre-0.3 config under the native configuration lock,
  then restarted the controller. Non-plugin configuration is semantically
  unchanged apart from native revision metadata. `opt7`/`hn1`, the shared MAC,
  disabled setting and native CARP maintenance state are preserved.
- The package downgrade left four unowned 0.3 files: `handover.py`, `peer.py`,
  `PeerController.php` and `M1_2_0.php`. Each matched its original 0.3 source
  checksum before being moved to `removed-0.3/`, along with peer/handover
  bytecode. They are absent from the live installation.
- `pkg check -s` passes and all 22 installed source files match the restored
  source. Local and native suites pass 66 tests, retaining the apply-result
  regression in addition to the pre-handoff behavior coverage. The native MVC
  migration fixture passes all 10 cases. PHP and JavaScript syntax checks pass.
- Installed Settings/Status GET handlers and native Volt compilation pass
  without changing configuration. Status is `DISABLED`/`FENCED`, `BACKUP`,
  controller running, no local error, and the owned LAGG has no members.

API credentials, coordination settings, release requests and persistent release
holds are removed from the active implementation. The controller follows native
CARP. Ping-based settling and passive conflict observations remain proposals;
neither was added by this rollback. HA-1 was not changed and no reboot or enabled
paired handover was performed. Browser authorization, enabled boot and paired
network qualification remain open.

### None selection — 0.2_2 source change

The empty managed-interface option is now **None**. Selecting it clears the form's
managed interface and carrier, unchecks enablement, and resets failback delay to
zero while retaining the shared MAC. Save & Apply persists the reset. The API
normalizes the same fields for direct callers and retains the existing separate
disable/fence requirement before identity removal. Native assignments are not
deleted. MAC suggestions for the previous selection are cleared.

The local suite passes 67 tests, including reset persistence and rejection for
enabled, attached and unavailable-runtime cases. A temporary JavaScript handler
check verifies form clearing without saving or changing the MAC. PHP, form XML,
JavaScript syntax and diff checks pass. Subsequent deployment to both nodes is
recorded below; authenticated browser acceptance remains open.

### HA-1 installation — 2026-09-27

Installed `os-dhcp-interface-ha-devel-0.2_2` on
`opnsense-ha-1.home.internal.bkwfamily.net` (appliance hostname
`opnsense-ha-1.mgmt.internal.bkwfamily.net`), running OPNsense 26.7.1_1 amd64,
FreeBSD 15.1-RELEASE-p1 and PHP 8.5.8. This was a fresh installation: no earlier
HA DHCP Interface or WAN HA DHCP package, plugin configuration, device or
assignment existed. HA-1 was MASTER before installation and remains MASTER.

- Source archive SHA-256:
  `e73e97015687215d7b3e1d1d306f7e8ebf4f49e7c40c05d16e0fefeb48147aec`;
  product hash `e73e97015687`.
- Package SHA-256:
  `d496f63077e176a380ee8fcbba0c6f83d12bfd47d5936ddc2fc6f9f264a29d62`.
- Root-private backup/build/evidence directory:
  `/root/dhcpha-install-0.2_2.5qVJeX`. `config-before.xml` holds the pre-install
  configuration; `packages-before.txt` and `interfaces-before.json` record the
  previous package and runtime inventories. No previous plugin package existed.
- Built natively with the archived repository build scaffolding and explicit
  product hash, then installed with `pkg install -y -U`. The final package has
  23 fingerprinted files and no test-generated bytecode. `pkg check -s` passes,
  and all 22 installed source files match the source manifest. The package is
  retained at `build/pkg/os-dhcp-interface-ha-devel-0.2_2.pkg`.
- All 67 tests and 10 actual MVC migration cases pass on HA-1. Native PHP lint,
  installed Settings GET and native Volt compilation pass. Settings shows
  **None**, disabled enablement and no carrier. The read-only handler checks
  leave the configuration hash unchanged.
- Both models initialized to schema 1.1.0. The controller is running with
  `SETUP_INCOMPLETE` / `UNMANAGED`, as expected for an unconfigured installation.
  Status API reports `unavailable` with the missing managed-configuration reason.
  No carrier is attached. Non-plugin configuration matches the backup
  semantically apart from native revision metadata.
- HTTPS root returns 200 and the unauthenticated plugin page redirects with
  302 using normal certificate verification. Browser authorization and the
  interactive clear/save workflow were not exercised against live settings.

HA-2 was checked and still has 0.2_1; it was not upgraded during this deployment.
No plugin enablement, reboot or paired handover was performed.

### HA-2 update — 2026-09-27

Updated HA-2 from `os-dhcp-interface-ha-devel-0.2_1` to 0.2_2 using the exact
package built and tested on HA-1 above (product hash `e73e97015687`, package
SHA-256 `d496f63077e176a380ee8fcbba0c6f83d12bfd47d5936ddc2fc6f9f264a29d62`).
Before this update, the interface and carrier selections had already been
cleared. The plugin was disabled and unconfigured with no attached carrier,
and native CARP was BACKUP. Those current settings were preserved.

The root-private directory `/root/dhcpha-upgrade-0.2_2.XM3g4T` contains the
pre-update `config-before.xml`, rollback `os-dhcp-interface-ha-devel-0.2_1.pkg`,
installed 0.2_2 package, source manifest, installation log and before/after
status snapshots. The controller was stopped for installation and restarted.

`pkg check -s` passes, all 22 installed source files match the source manifest,
and `/conf/config.xml` is byte-for-byte unchanged. Controller and configd are
running. Status is `SETUP_INCOMPLETE` / `UNMANAGED`, disabled, BACKUP, with no
carrier attached. Settings GET shows **None** and an empty carrier; native Volt
compilation passes. HTTPS root returns 200 and the unauthenticated plugin page
redirects with 302 using normal certificate verification. No enablement, reboot
or paired handover was performed.

### HA-1 setup status-lock race — 0.2_3, 2026-09-27

While selecting `hn0` as the local carrier for `opt7 → dhcpha0lagg`, saves
intermittently rejected the shared MAC with the detached-device evidence error.
The live device was owned, down and empty. A read-only loop invoking the real
Settings controller's observation collection and validation reproduced the exact
message in 6 of 12 attempts without saving configuration.

The root status reader attempted its shared transition lock only once. Routine
controller reconciliation could hold the exclusive lock, causing status to exit
with an error; configd returned `Execute error`, which left the Settings API
without detachment evidence. Status now waits up to two seconds for the same
shared lock, within its existing five-second command budget. It still fails
closed when the lock remains busy and never reads a snapshot through a writer.
The detached/absent-device checks and identity-change restrictions are unchanged.

Two regression cases exercise a real contended lock: release during the first
retry followed by fresh state readback, and bounded failure without reading
through an outstanding transition. All 69 tests pass locally and on HA-1.
After installation, all 12 repeated native save-validation checks pass, including
reads overlapping normal controller work. No Settings POST or interface
reconfiguration was performed by the validation harness.

Deployed `os-dhcp-interface-ha-devel-0.2_3` to HA-1 only. Source archive SHA-256:
`d64aa0d9a1cb637d44f8aaab633dc1509899d8f3a065b9033ed2420ef15324c1`;
product hash `d64aa0d9a1cb`. Package SHA-256:
`7fcc64f3ba176d7332eb44696f1de43d1080f770906888a85992066610f0b566`.
The root-private `/root/dhcpha-status-fix-0.2_3.qWBOaO` directory retains the
pre-update config and 0.2_2 package, source archive/manifest, new package, logs
and successful native validation observations. `pkg check -s` and all 22 installed
source hashes pass. Configuration is byte-for-byte unchanged; HA-1 remains
MASTER with the plugin disabled/fenced and controller running. The logical
assignment is `opt7`; the carrier selection remains unsaved. HA-2 remains on
0.2_2. No reboot or enabled paired handover was performed.

### Empty-device MAC suggestion — 0.2_4, 2026-09-27

After HA-1's `opt7` assignment moved to the empty `dhcpha0lagg`, the carrier
preview reported its all-zero MAC as a copyable suggestion. The UI copied that
value over the unsaved shared-MAC field. Saved configuration still held the
chosen unicast MAC; this was a suggestion-validation bug, not a changed hardware
identity. The API now returns an unavailable suggestion for zero, multicast,
broadcast or malformed values. The UI disables the copy action while loading or
unavailable and independently validates the value before copying. Usable native
spoof values retain precedence over the observed backing-device MAC.

All 70 tests pass locally and natively on HA-1. Regression cases cover invalid
suggestions and spoof precedence; a temporary JavaScript check verifies that
invalid suggestions leave the MAC field unchanged. Installed carrier-preview
readback now reports the empty LAGG's suggestion as unavailable. Native Volt
compilation, package fingerprints and all 22 installed source hashes pass.

Deployed 0.2_4 to HA-1 only, retaining disabled/fenced state and MASTER role.
Configuration remained byte-for-byte unchanged. Source SHA-256:
`fac670d5057f4ebbfc70e871d9cc1b42d1ad21e5bc2d9477582c21414da7f3a1`;
product hash `fac670d5057f`. Package SHA-256:
`bf68aef0a518ea156bc22f6fcf836fb5b56d3126b4ff4ab6d4423cb897c933cf`.
Backups of configuration and 0.2_3, source/build artifacts, logs and status
snapshots remain in `/root/dhcpha-mac-fix-0.2_4.24WEec` on HA-1.

Read-only candidate validation confirmed `hn0` is eligible and attachment-ready.
The outstanding carrier and MAC-collision checklist failures reflected an
unsaved carrier selection. Both nodes must be configured with one shared MAC
before enabled pair testing; their saved MACs differed at diagnosis. XMLRPC
selection is a warning and peer readiness remains explicitly unverified.

### HA-2 native LAGG setup button — 0.2_5, 2026-09-27

Updated HA-2 from 0.2_2 to 0.2_5. This includes the status lock and MAC
suggestion fixes plus Configure LAGG on selected interface. No setup action
was invoked: config.xml is byte-identical to its pre-upgrade backup, and
the plugin remains disabled with empty local mapping (SETUP_INCOMPLETE /
UNMANAGED). The controller was restarted and is running.

All 70 tests passed natively; the UI flow checks passed locally. Package
checksums and all 22 installed source files match. The native Volt compiler
compiled the installed view successfully. Interactive browser operation and
the actual native reassignment remain for the user's setup.

Backup config, prior 0.2_2 package, source manifest, build/install/test logs,
compiled view and status readback are retained privately on HA-2 under
`/root/dhcpha-upgrade-0.2_5.JWHsbM`.

- Source archive SHA-256: `2d6219d15b56b64804015bb49a0a8be7a1e6ccc4af8191b39f92df867739f181`.
- Package SHA-256: `50267c488844341fe24edd1e7059dc7933c074ca61671770cf1b5a79dd8c6e20`.
- HA-1 remains on 0.2_4.

### Automatic carrier capture — HA-2 0.2_6, 2026-09-27

Configure LAGG now saves the original assigned device as carrier before
native reassignment, using the existing Settings transaction and revision
checks. The confirmed action also saves the form's shared MAC and remaining
settings, keeping the plugin disabled. Failed settings save/apply prevents
native reassignment; failed native apply retains the captured carrier. An
already migrated interface uses its known carrier, with manual selection
required only when unknown. UI regression checks cover capture, stale manual
choices, the already-migrated case, failure ordering and recovery.

Deployed 0.2_6 to HA-2; all 70 tests passed both locally and natively, the UI
flow tests and JavaScript syntax check passed locally, and installed Volt
compilation passed. Package checksums and all 22 installed source files
match. The daemon is running; config.xml remains byte-identical, with the
plugin disabled/unconfigured. The setup button was not invoked on the live
node. HA-1 remains on 0.2_4.

Backups of config and 0.2_5, source manifest, package, build/install/test logs,
compiled view and status readback are retained on HA-2 in
`/root/dhcpha-upgrade-0.2_6.yLFS00`.

- Source SHA-256: `c7fafdd1deaf4cfcec6e92dec7370493fde5e6e3e914a77bdaccbcb25e88beea`.
- Package SHA-256: `8e846d70ee1510a9cc63f1a18dafbdaa875d067551734e03c869e6b12591b07c`.

### Carrier selector removed — HA-2 0.2_7, 2026-09-27

Removed the local carrier field from the form. The preview detects the selected
assignment's device, retains the saved carrier after migration, and displays it
in the mapping. Configure LAGG still persists it before reassignment. If an
already migrated assignment has no saved carrier, the UI asks the user to
restore its original assignment while disabled and repeat setup. Selecting
another logical interface does not carry over the previous saved carrier.

Deployed to HA-2 with 70 native tests and local UI flow checks passing. All
22 source files and package checksums match; configuration is unchanged.
Cleared compiled Volt cache and restarted the web GUI; HTTPS returned 200
and the plugin controller is running. Backup config, prior 0.2_6 package,
source manifest and build/test/install logs are in
`/root/dhcpha-upgrade-0.2_7.Jl4FuQ`. HA-1 remains on 0.2_4.

- Source SHA-256: `be4a685fe05fb80a347db8956f66e34f68b1b583cd06552839f9e315e8ddd091`.
- Package SHA-256: `93db2a6ff2a8e988674306a5c0cde5ab8a83cfb39842e1a9a4c14c175da802ec`.

### HA-1 deployment — 0.2_7, 2026-09-27

Updated HA-1 from 0.2_4 to the identical 0.2_7 package verified on HA-2
(SHA-256 `93db2a6ff2a8e988674306a5c0cde5ab8a83cfb39842e1a9a4c14c175da802ec`).
All 22 installed source files match the manifest and package checksums pass.
Configuration is byte-identical; opt7/hn0 remains saved and the plugin is
DISABLED/FENCED. Controller is running. Cleared compiled UI templates and
restarted the web GUI; HTTPS returned 200 and installed Volt compilation
passed. No interface setup action was invoked.

Pre-update config and 0.2_4 package, installed package/manifest, installation
logs, status readback and compiled view are retained privately on HA-1 in
`/root/dhcpha-upgrade-0.2_7.1n6eki`. Both nodes now run 0.2_7.

### HA-2 DHCP receive-filter investigation — 2026-09-27

With both nodes enabled and HA-1 placed in native CARP maintenance by the
operator, HA-1 fenced and HA-2 became MASTER/ACTIVE with hn1 attached using
00:15:5d:05:74:26. HA-2 initially had no IPv4 address. Native DHCP configuration
matched HA-1. Non-promiscuous captures on HA-2 hn1 and dhcpha0lagg showed
repeated Discover packets without replies. The same requests were visible
on HA-1 ix0, proving transmission onto the test network. HA-2 hn1 receive
counters were zero.

A bounded promiscuous capture on hn1 immediately admitted a DHCP Offer/ACK
from 10.250.100.1 and the native client acquired 10.250.100.100. After the
capture ended, unicast ICMP probes were again absent from non-promiscuous
hn1 captures. A separate bounded `ifconfig dhcpha0lagg promisc` test propagated
to hn1 (`dev.hn.1.rxfilter: 20<PROMISC>`) and admitted those same probes.
The temporary flag was removed in a finally block; receive filter returned
to DIRECT/ALLMULTI/BROADCAST. No persistent config or plugin code was changed.

This isolates a receive-filter compatibility issue with the cloned MAC on
HA-2's Hyper-V adapter, not failed CARP attachment or absent outbound DHCP.
Native Interfaces > ha_test > Promiscuous mode is the available persistent
configuration mechanism; it has not yet been enabled. HA-2 retains the lease
acquired during diagnosis, but an assigned address alone does not establish
working reception. ICMP requests admitted during the test were not answered;
end-to-end firewall/traffic behavior is not qualified by this DHCP test.

### Shared-MAC receive filtering fixed — both nodes 0.2_8, 2026-09-27

The controller now sets promiscuous receive mode on the owned LAGG before
attachment and verifies propagation to the selected carrier before activation.
An otherwise valid active attachment repairs missing receive flags with only
`ifconfig dhcpha0lagg promisc`, preserving membership and DHCP state. Active
status requires both receive flags; failed verification fences. No NIC-name
heuristic or user option was added. Native config.xml is unchanged; the
controller reapplies its runtime requirement after native reconfiguration.

Regression checks first failed without the fix, then passed: filter setup
before activation, repair without detach, demotion with inherited flags
released, ignored filter commands preventing activation, and command failure
fencing. All 72 tests passed locally and on HA-2; UI checks also passed.

Deployed the same package on both nodes. Package checksums and all 22 installed
source files match, controllers run, and both configs are byte-identical to
backups. HA-1 remains BACKUP/FENCED in operator-selected CARP maintenance.
HA-2 is MASTER/ACTIVE with LAGG and hn1 promiscuous; native DHCP logged REBOOT
and acquired 10.250.100.100 at 14:09:50 -05:00. Clearing its receive flag in a
controlled repair test was corrected automatically in 3.2 seconds; the carrier
and IPv4 address remained in place. No promiscuous packet capture was needed
for these post-fix checks. Full traffic continuity/failback remains unqualified.

Backup configs, prior 0.2_7 packages, manifests, installation logs and status:
- HA-2: `/root/dhcpha-receive-fix-0.2_8.En4Gt2` (also source, build and tests).
- HA-1: `/root/dhcpha-receive-fix-0.2_8.HNeRxC`.
- Source SHA-256: `c9cfc932c48684404c2825060a963ef6ce5ef100c06ca2bf6258933ace7400dd`.
- Package SHA-256: `d56ebe639d52f62f912d8a62b0bd572878da9b6944ec0a007f2d296d4b8f5f1f`.
