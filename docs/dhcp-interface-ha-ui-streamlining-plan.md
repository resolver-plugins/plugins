# HA DHCP Interface UI and logging implementation plan

Status: source implementation for experimental **0.2_28**, 2026-09-28, following
the user’s implementation request. Local behavior checks and native source/
syntax probes are recorded below. HA-2 deployments and UI corrections are
recorded in sections 10–16;
authenticated native acceptance remains a separate gate.

Reviewed for a Luna Max implementation handoff on 2026-09-27. Implement the
bounded contracts below; do not reopen settled product choices or treat the
native verification gates as permission to invent replacement infrastructure.

The specification defines the product contract. The [older UI repair plan](dhcp-interface-ha-ui-plan.md)
retains useful deployment evidence and regression history, but its manual
setup instructions are superseded for this increment. Preserve the existing
working tree, including prior unstaged/untracked work. No worktrees, upstream
OPNsense PRs, package publication or unrelated BIND/CI changes.

## 1. Implementation boundaries

| Boundary | Existing implementation / reference | Responsibility in this increment |
|---|---|---|
| Root controller | `net/dhcp-interface-ha/src/opnsense/scripts/dhcp_interface_ha/{core.py,runtime.py,dhcp_interface_ha.py}` | Preserve fencing, role/identity checks and automatic receive mode; report outcomes and log actual transitions/repairs. |
| Settings and actions | `net/dhcp-interface-ha/src/opnsense/mvc/app/controllers/OPNsense/DhcpInterfaceHa/Api/{SettingsController,ServiceController}.php` | Reuse revisioned save; add `settings/configure` and `settings/enable_sync` as specified, with verified outcomes and native privileges. |
| Observations | `net/dhcp-interface-ha/src/opnsense/mvc/app/controllers/OPNsense/DhcpInterfaceHa/Api/StatusController.php` | Structured responsibility/resolution, current evidence, sender-aware sync context and compact summary. No mutation on GET. |
| UI | `net/dhcp-interface-ha/src/opnsense/mvc/app/views/OPNsense/DhcpInterfaceHa/index.volt`, corresponding `forms/settings.xml` and `IndexController.php` | Settings summary, adjacent setup action, progressive diagnostics and Log navigation. |
| Native integration | Plugin `.inc`, configd actions, model ACL/Menu and native Syslog templates | Stable log identity, stream registration, least required log permissions, upgrade/start integration. |
| Logging prior art | `net/frr/src/opnsense/service/templates/OPNsense/Syslog/local/routing_frr.conf`, FRR Menu/ACL entries | Reuse the native filter/viewer pattern; ordinary syslog should not need a custom parser. |
| Existing checks | `net/dhcp-interface-ha/tests/`, including PHP fixtures, native probes and `ui/test_configure_lagg.js` | Extend real behavior tests instead of creating another test framework. |
| Focused CI | `.github/workflows/dhcp-interface-ha-tests.yml` | Include only any newly required focused checks; preserve unrelated existing edits and publication policy. |

Keep native package/service hooks in POSIX sh and nontrivial controller logic in
Python. Do not add abstractions whose only purpose is to make the code appear
more architectural. Named condition codes, narrow functions and explicit
side-effect boundaries are sufficient.

### S0 — Confirm native integration before restructuring the UI

- [x] Read the current implementation and working diff, then the spec's setup,
  condition and logging contracts. Do not implement from the historical plan.
- [x] Trace target `Interfaces/Api/AssignmentController` and `NetworkInterface`,
  `Core/Hasync`, `Core/ACL`, native diagnostics Log controller/view/API and Syslog
  template registration. Local core checkout may differ from the appliances;
  record the version used for each native check.
- [x] Establish the narrow native model/controller call path for the server-side
  Configure action. Reuse native assignment validation, staging and apply;
  check the authenticated caller against the native assignment endpoints in
  addition to plugin access. No stored API keys, internal HTTP call, direct
  config.xml relink, duplicate native apply implementation or upstream patch.
- [x] Confirm pending-queue inspection and lock behavior. Native apply consumes
  the shared queue, so implement the spec's preflight/recheck boundary and
  concurrent-edit warning, not an unachievable atomic selected-only guarantee.
- [x] Confirm native view inclusion, Informational default, stream registration
  and exact log read/search/export/live ACL patterns without `/clear` access.
  Check the real authenticated username helper for browser and API callers;
  do not assume session-only authentication in new permission checks.

Completion evidence: a short implementation note in this plan names the native
entry points and observed response/permission behavior. A disposable native
fixture verifies the bridge when an authorized test target is available. If the
target cannot support a boundary without core changes, report that specific
limitation before replacing the design; continue independent source work and
do not claim native acceptance from stubs. No feature deployment is needed to
perform source tracing.

## 2. S1 — Establish the condition and ownership contract

- [x] Inventory existing controller reasons and readiness checks. Classify each
  by responsibility, observed status and allowed resolution using the spec's
  ownership table. Keep current codes where their meaning remains correct.
- [x] Add the minimum structured response fields needed for this classification
  and outcomes. Keep a single backend interpretation of recovery eligibility;
  the browser renders it rather than deriving rules from prose.
- [x] Implement the spec's exact added fields (`responsibility`, `resolution`,
  `relevant`, nullable `attempt`) and all listed existing condition mappings.
  Add `receive_mode`; preserve existing status/severity enums. Fix MAC
  validation so missing collision inventory is unknown rather than coerced
  into an invalid-MAC result.
- [x] Rewrite guidance that asks users to maintain an owned LAGG, choose its
  carrier or enable its receive filter. Reserve user-action messages for real
  conflicts/choices; keep unknown observations distinct from failed checks.
- [x] Confirm that “repairing” and “repaired” are supported by runtime evidence.
  Keep setup retry and periodic controller repair distinct.
- [x] Add or extend one focused group of behavior cases for plugin-owned drift,
  missing evidence, native conflict and ownership conflict. Exercise real
  controller/Status API output, not only a table of expected labels.

Completion evidence: tests show the same failure cannot be described as a user
prerequisite in one path and an automatic repair in another. A failed repair
retains its cause/outcome and cannot be labeled safely fenced without readback.

## 3. S2 — Complete the guarded setup action

Depends on S0/S1. Preserve working carrier capture and receive-mode behavior.

- [x] Trace the current browser orchestration through prepare, Settings save,
  native assignment save and native apply. Document their actual return shapes,
  permissions, pending-edit handling and native lock semantics on the target.
- [x] Implement POST `settings/configure` with the spec's full-form/revision
  input and staged result contract. Factor only the shared settings validation/
  save code needed by `set` and `configure`; do not maintain two transaction
  implementations. Derive/verify the carrier on the server. Reuse the native
  bridge validated in S0; kernel operations stay in root/configd.
- [x] Read committed assignment state and inspect pending native edits. Reject
  unrelated pending assignment changes; do not rely on a warning to justify
  applying them. Recheck intent and configuration before changing the mapping
  and just before apply. Accept a selected-interface pending relink only as a
  verified continuation of the same saved intent. Do not erase pending edits.
- [x] Preserve the order: saved disabled state and detachment verified; owned
  device prepared; carrier captured/saved; selected assignment moved through
  native machinery; final mapping verified. Do not change shared MAC on load or
  silently enable the plugin.
- [x] Return explicit save/apply/verification outcomes using current conventions.
  On native failure, keep the captured carrier. On timeout, obtain readback
  before allowing a retry that would replay a mutation. Do not invent a
  transaction rollback across independent native/plugin operations.
- [x] Make confirmed retry resume the known state safely. Unknown original
  carrier, foreign ownership, conflicting intent or missing privileges remains
  blocked with specific guidance.
- [x] Replace the browser's multi-request setup mutation chain with this action.
  Canceling confirmation sends no mutation. Disable competing buttons while a
  request is pending; after timeout, refresh evidence before offering Retry.
  Do not start a browser retry loop or assume canceling HTTP canceled configd.
- [x] Extend the existing setup-flow and API fixtures to cover partial success,
  stale revision, unrelated pending edits, already-configured retry and read-only
  denial. Preserve the None/reset and identity-change fencing tests.

Completion evidence: one Configure interface action saves the disabled form,
captures the right device and verifies migration without manual LAGG work.
Failed/retried operations preserve the original carrier and reject observed
unrelated pending edits. Record the native concurrent-edit limitation explicitly;
do not claim atomic isolation. Native appliance checks remain required for actual
apply/ACL semantics beyond the stub tests.

## 4. S3 — Add authoritative events and native log integration

Start native capability investigation alongside S1; complete after S2 provides
observable setup outcomes. No live remote access is required for source work.

- [x] Establish a stable syslog program identity shared by controller/action
  events. Add the native Syslog filter, normal template registration and viewer
  access, following existing plugin conventions: identity `dhcp-interface-ha`,
  filter `local/dhcpinterfaceha_core.conf`, stream `dhcpinterfaceha/core`.
  A thin plugin Log wrapper includes the native view and common tab navigation,
  sets Informational as the default and leaves service controls empty. Preserve
  native filters and retention; do not copy the grid or expose log clearing to
  log-only users.
- [x] Emit transition/repair events at the operation boundary that already knows
  their result. Trace daemon, explicit apply, prepare/setup, CARP wakeup, boot
  preparation and shutdown. Follow the spec's producer/event-code table:
  operation events and later daemon observations are distinct; nested exception
  handlers must not all log the same failure.
- [x] Cover setup, configuration, lifecycle, role/attachment changes, failed
  operations and recovery. Prefer one informative event for a verified
  transition over a cascade of “starting/done” messages.
- [x] Use the existing service loop for state/address observation; reuse native
  interface inventory. Do not make browser polling responsible for durable
  event history. Log an observed address rather than a claimed DHCP ACK.
- [x] Suppress identical steady-state failures, with at most one reminder per
  minute; emit recovery and changed causes promptly. Bound suppression state to
  the small fixed set of active operations/conditions. A daemon restart logs
  its observed starting state, not invented past transitions. Suppression is
  per long-lived process with a monotonic clock; no cross-process ledger.
- [x] Keep syslog write errors outside the critical attachment/fencing outcome.
  Filter/redact context and prevent multiline/injected payloads. No arbitrary
  config or browser request content in logs.
- [x] Test emission and suppression through real operations with a mocked syslog
  sink/clock. Cover success, failure, recovery, unchanged polling and a logging
  failure during fencing. Test registration/rendering natively as a separate
  acceptance step.

Completion evidence: a chronological local log explains a setup and role
transition, a failed repair and its recovery. An unchanged loop does not flood
the stream. The native viewer can read/filter it with correct permissions; log
access cannot write configuration or invoke actions.

## 5. S4 — Simplify Settings and Diagnostics

Depends on S1/S2; integrate the Log tab from S3.

- [x] Use Settings, Diagnostics and Log navigation. Move Status content into a
  compact Settings summary and detailed Diagnostics. Preserve old status hash
  navigation by mapping it to the summary.
- [x] Keep Enable, Interface and Shared MAC as normal fields. Show carrier as
  read-only mapping; keep MAC helper actions adjacent and retain None semantics.
- [x] Place Configure interface beside the selection. Once migrated, replace it
  with Interface configured and make Save & Apply the primary editing action.
  Keep incomplete disabled draft saving as a clearly secondary action.
- [x] Follow the spec's action-state matrix and summary precedence. Keep the
  live saved state separate from unsaved previews. Show Not configured for a
  disabled None state; distinguish Interface configured from Ready to enable.
  Preserve MAC helper behavior and expose an explicit legacy failback reset in
  Diagnostics if the saved value is nonzero.
- [x] Show only current relevant issues by default, distinguishing automatic
  work, unavailable observations and user decisions. Retain Show all checks.
- [x] Move manual prepare/reconcile, raw topology/role details, removal guidance
  and snapshot download into Diagnostics. Remove duplicated warnings and
  always-visible unsupported failback explanation; keep a real nonzero legacy
  value visible as a specific corrective condition.
- [x] Poll only while the view is visible, keep at most one status request in
  flight, ignore stale preview responses and preserve unsaved form changes.
  Use five-second visible polling and 15-second freshness; failed required
  observations become unknown immediately. Viewing tabs must never trigger
  setup, apply, sync or repair writes.
- [x] Keep summary states honest: ACTIVE with no observed address says Waiting
  for DHCP; standby/maintenance detached state is normal; stale/partial data
  cannot imply fresh success. Required issue information must not rely on color.
- [x] Ensure read-only users can inspect supported views and logs; controls are
  appropriately unavailable and the server independently enforces authorization.

Completion evidence: an authenticated browser user can find and complete setup
without opening Diagnostics. All detailed evidence and existing recovery
functionality remain reachable. Test actual handlers for setup outcomes and
stale responses; do not retain tests that only count labels or mirror markup.

## 6. S5 — Add the existing-sender sync control

Depends on S1/S4. This is a local configuration-selection action, not a push.

- [x] Read native sync target/selection and derive sender context independently
  of current CARP role. No configured destination means neutral guidance,
  not a required outbound-sync checkbox on a presumed receiver.
- [x] Implement idempotent POST `settings/enable_sync` with no client-provided
  selections, preserving native HA authorization. Under the native config lock,
  reload the Hasync model, recheck the target and merge only `dhcp-interface-ha`
  membership. Validate and save if changed. Follow the spec's response contract;
  already selected is successful without another write.
- [x] Preserve other sync selections, destination and credentials. Do not
  change plugin enablement or mappings, call native sync/restart, or save a
  duplicate plugin sync setting. Keep this action separate from normal form
  saving and make the “future native sync” effect explicit.
- [x] Show Included in configuration sync after readback; removal stays on the
  native HA page. This is an enable action, not a second persisted checkbox or
  another place to edit the complete sync list.
- [x] Log verified selection change/failure and show accurate readback. Test
  existing sender, absent destination, read-only caller and concurrent native
  selection edits. Test that no sync/restart backend action is called.

Completion evidence: the UI can include the plugin in a configured sender's
selection without changing anything else or contacting the peer.

## 7. S6 — Verification, documentation and deployment gate

- [x] Run the existing Python/PHP controller/API suite and setup UI checks,
  extended for the behaviors above. Record actual counts/outcomes at that time;
  baseline 0.2_8 has 72 Python-discovered tests plus UI flow checks.
- [x] Run affected PHP, JavaScript and shell syntax checks, native Volt
  compilation and `git diff --check`. If focused CI orchestration changes, use
  the relevant checks from [Building](building.md). Do not run publication or
  manually trigger workflows without authorization.
- [ ] Complete the acceptance matrix below in an authenticated native browser
  and supported test environment. Record which checks remain unrun, rather than
  describing compilation or mocked APIs as end-to-end validation.
- [x] Reconcile current maintainer documentation and user setup instructions
  with the implemented contract. Keep historical deployment facts intact and
  mark superseded instructions. Update the carrier/receive-mode ownership
  guidance without changing the existing controller safety contract.
- [x] Build a versioned package after source verification.
- [x] Stage concrete rollback package/config backups before any authorized deployment. Preserve
  node-local mappings, shared settings and operator-selected CARP maintenance.
- [ ] When deployment is authorized, verify installed file hashes, package
  checksums, service state, actual native log registration/viewer and unchanged
  configuration except for explicitly performed test actions. Account for
  compiled UI cache invalidation so the displayed page matches the package.
  No handoff, reboot, peer sync or live assignment change is implied by a UI
  deployment check; obtain the required task authorization for those tests.

### Native acceptance matrix

| Scenario | Required observable result |
|---|---|
| New disabled setup on a spare DHCP interface | One selection and confirmed Configure interface capture the original device and migrate; no carrier selector or manual receive-mode step; stays disabled. |
| Existing configured 0.2_8 node | Correct summary and stored mapping; opening/saving unrelated UI changes does not rerun migration. |
| Disabled while configured / identity edit while enabled | Disabled retains the MAC, restores the selected logical interface to its saved native carrier, then clears plugin settings; enabled identity edits retain the disable-and-fence guard. |
| Foreign device, missing original mapping, observed other pending assignments | Specific blocked condition; no guessed carrier, no foreign-device modification, no apply of observed unrelated edits. Document the native check/apply race. |
| Timeout after a save or native apply | Unknown result is shown honestly; readback precedes retry; captured carrier retained and no false “nothing changed.” |
| Active with and without an observed address | Attachment distinguished from DHCP address availability; no DHCP/Internet-driven role change. |
| Standby and operator maintenance | Expected detached state, no false DHCP failure or outbound-sync requirement. |
| Plugin-owned filter drift and failed repair | Automatic repair and recovery event; genuine failure visible with reason; no instruction to manually set the native checkbox. |
| Status unavailable / rapid selection changes | No stale success or wrong-interface preview; unsaved values preserved; no GET-side mutations. |
| Log normal/recovery/failure events | Correct native stream, timestamps/severity, useful interface context, native filtering, no unrelated logs or repeated five-second flood. |
| Restricted user | Authorized inspection works; setup/native assignment/sync writes denied without their respective privileges. |
| Sender selection update | Only this plugin's membership changes; concurrent selections preserved or conflict returned; no peer request or restart. |

### Focused regression cases by boundary

Extend existing tests; each row protects a distinct behavior, not a new suite.

| Boundary | Required cases |
|---|---|
| Configure API | Stale revision and unauthorized native writer rejected before mutation; committed carrier capture; failure at prepare/save/stage/apply/readback retains correct stage and saved state; runtime mismatch after native success is not success. |
| Retry and native integration | Already complete skips native apply; saved original mapping resumes; exact pending relink resumes; unrelated/mismatched pending changes block; no config lock held across configd. Native fixture proves behavior that PHP stubs cannot. |
| Status/readiness | Owned receive drift vs foreign device; no collision inventory; no interface selected; optional peer/pfsync context; sender and no-destination sync context; no invented repair attempt after a fresh GET. |
| UI handlers | Confirmation cancel; no double submit; timeout followed by readback; dirty values survive polling/sync update; saved enablement cannot be bypassed by unchecking draft; address absent vs lookup unavailable; 15-second staleness and old response rejection. |
| Event emission | One verified mutation event from actual root paths; nested failure logged once; unchanged daemon loop quiet; first failure/reminder before and at 60 seconds/recovery; changed safety outcome immediate; restart snapshot; observation failure is not address loss; logging failure cannot prevent fencing. |
| Native log integration | Fresh install and upgrade register stream; normal events visible with default severity; custom filter preference retained; tabs return correctly; log-only caller can search/export/live but cannot clear or mutate; global local logging disabled is reported accurately. |
| Sync action | Latest selection merged under lock; target removed before save; already selected no-op; plugin-only/read-only user denied; no backend sync/reconfigure call and no dirty-form reset. |

### Test commands available at the baseline

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s net/dhcp-interface-ha/tests -v
node net/dhcp-interface-ha/tests/ui/test_configure_lagg.js
node net/dhcp-interface-ha/tests/ui/test_log.js
git diff --check
```

These are starting points, not substitutes for the new log integration and
authenticated browser acceptance. Extend the existing CI invocation when a
distinct behavioral regression requires an additional check.

## 8. Decisions for future agents

- Fix an owned invariant at the responsible operation/controller boundary.
  Do not turn a known controller regression into a manual setup instruction.
- Derive remediation from structured condition evidence and guards. A fail
  label does not by itself mean user error; an exception does not prove a code
  defect; an unknown observation does not prove either.
- Preserve observability of partial success. A native assignment and plugin
  save are separate operations even when the UI exposes one action.
- Never repair a foreign device, infer peer state from a local role, or infer
  packet delivery from an attached carrier/lease file.
- Prefer native behavior and existing boundaries. There is no need for a new
  framework to express ownership, log state changes or filter the checklist.
- Keep proposed work, implemented behavior and verified appliance evidence
  separate in status reports. Checkmarks above record source implementation and
  focused checks; they do not imply appliance acceptance.

For the implementation handoff, work in order S0 → S1 → S2 → S3 → S4 → S5 → S6.
After each step, record changed boundaries and the actual focused check result;
check a task off only when its stated evidence exists. Source implementation can
be complete with named native checks still outstanding, but the release is not
qualified until those checks pass. No additional product interview is needed.

## 9. Implementation evidence — 2026-09-27

Implementation used focused Luna Max contexts with separate file ownership:
settings/setup/sync API, status classification, runtime events, UI behavior,
and native Log integration. Separate standards and specification reviews plus
coordinator integration review checked the combined result. Existing source was captured before editing so this increment can be
reviewed separately from the earlier uncommitted 0.2_8 changes.

Native source inspection used HA-2's OPNsense **26.7.3_11 amd64** files,
collected read-only:

- `Interfaces/Api/AssignmentController::reconfigureAction()` invokes native
  `interface apply`, commits the pending relinks/deletions and requests filter
  reload. Reusing it preserves native accounting; copying only the configd call
  would leave committed configuration wrong. It retains the Config lock until
  request cleanup; the enclosing setup adapter must release it before fresh
  readback, including after a native save exception.
- `Interfaces/NetworkInterface` merges `/tmp/.interfaces.todo` into its in-memory
  model. Committed carrier discovery must therefore read the native Config
  assignment. Fresh model validation/serialization stages the relink; the shared
  pending queue still has the final-check/apply concurrency limitation in the
  specification.
- Native `Interfaces/FieldTypes/DeviceField` caches assignment options within
  a request. `interfaces/list_assign_options.php` advertises registered plugin
  virtual-device names even before their runtime creation, so preparing the
  registered `dhcpha0lagg` does not require bypassing that cache or validation.
- Native `Core/ACL::isPageAccessible()` can check the current caller's native
  assignment/HA privileges. Use the API base's authenticated username helper,
  which supports both session and API authentication, plus read-only enforcement.
- Native `Diagnostics/log` is a reusable view with configurable module, scope,
  default severity and service controls. Its API's `clear` operation has a
  separate URL and must not be included in log-only ACL patterns.
- The native Syslog template discovers `local/*.conf` filters. Existing plugin
  package hooks reload plugin configuration/Syslog and the template, so the new
  stream needs no custom logging service or package restart hook. The new exact
  program filter passed HA-2 syslog-ng 4.12 syntax-only validation from stdin;
  this did not change or reload the running logging configuration.

These findings establish the source integration paths. They do not establish
native authenticated-browser, live assignment, log delivery or package acceptance.

Integration review also checked these edge cases against the actual boundaries:

- A selected original DHCP interface may already have an IPv4 lease before
  migration. That is expected on its exclusive native assignment; an address
  on an already reserved carrier remains a conflict.
- Disabled detached readback before native migration does not require the
  logical assignment to have moved already. Final setup verification does.
- Shared XMLRPC enablement can arrive on a node without its local mapping.
  Diagnostics must request that node's own interface selection, not silently
  treat the resulting incomplete setup as irrelevant.
- Native `pluginctl -D` reports IPv4 rows as `ipaddr` strings with `subnetbits`
  and `tunnel` metadata; an observed empty list is distinct from a failed read.
- Native log API module/scope order follows the registered directory:
  `/api/diagnostics/log/dhcpinterfaceha/core`, with separate `export` and `live`
  permissions. `core/dhcpinterfaceha` would read a different stream.

Focused verification completed on this source:

- Python discovery: **108 tests passed**, including actual PHP controller
  fixtures with stubbed native boundaries and real Python controller operations.
- Node fixtures execute the actual Settings handlers and Log AJAX handlers.
  They cover setup/readback, revision and pending-queue guards, stale/slow status,
  one request in flight, MAC selection, summary semantics, sender selection,
  and empty results versus log access/backend failures.
- PHP syntax, XML parsing, POSIX shell syntax and ShellCheck passed for the
  affected source/hooks. The focused CI workflow includes both UI fixtures; no
  workflow was dispatched and no release/publication workflow changed.
- Native Settings and Log Volt compilation and real native ACL URL-mask matching passed on
  HA-2 without installing files. Exact read/export/live patterns exclude clear,
  configuration writes and other log streams. This does not test a logged-in role.

Review corrections include preserving the form’s old revision after a conflicting
readback, refusing to use a zero/LAGG MAC as a carrier suggestion, measuring
status freshness from request start, and observing the native pending queue
without constructing its slow assignment model in the status request. Blocked
or stopped MASTER reconciliation now stays quiet when no repair occurred, and
logs the actual reason when it verifies a detach.

Unknown Configure outcomes block mutation controls until a read-only Recheck
confirms the complete settings/assignment evidence. An ordinary status refresh
cannot bypass that guard. A page reopened with a matching pending relink offers
the same readback path before enabling a confirmed retry; the UI regression
exercises both the button state and the actual handlers.

The native package build completed in temporary HA-2 staging
`/tmp/dhcpha-ui-0.2_9.rOzY8I`. The candidate is
`net/dhcp-interface-ha/work/pkg/os-dhcp-interface-ha-devel-0.2_9.pkg`, SHA-256
`526097435206aff8cda0575ed19cb2b706f534afa61b04572152fd37ce78f58c`.
All **25 packaged source files** match the reviewed source; the 26th package
entry is generated version metadata. No test files or Python caches are packaged.
The build log, source archive and per-file hashes are in the ignored plugin
`work/` directory (`build-0.2_9.log`, `build-0.2_9.json`). The build used working
source with `product_hash=unknown`; the recorded content hashes identify the
exact artifact without inventing a source commit. HA-2 still reported
`os-dhcp-interface-ha-devel-0.2_8` after the build.

At the source/build checkpoint, the native acceptance matrix was **unrun for
0.2_9**. No native assignment, peer sync, CARP transition, package installation
or running-service restart was performed during source implementation. The
subsequent authorized HA-2 deployment is recorded below. Before release qualification, exercise the assignment
bridge in an authorized disposable native setup, authenticated browser roles,
actual event delivery/filtering/rotation, and the specified live lifecycle cases.

## 10. HA-2 deployment — 2026-09-27

The user authorized deployment to HA-2. Installed the exact verified
`os-dhcp-interface-ha-devel-0.2_9.pkg` candidate above on OPNsense 26.7.3_11.
HA-1 was not changed. The prior 0.2_8 package and private configuration backup
are in `/root/dhcpha-upgrade-0.2_9.moxwkta8` on HA-2, alongside installation,
restart, status and native-log evidence. The rollback package SHA-256 is
`d56ebe639d52f62f912d8a62b0bd572878da9b6944ec0a007f2d296d4b8f5f1f`.

Deployment verification:

- All **25 installed source files** match the build manifest; native package
  checksum verification passes. Installed package reports **0.2_9**.
- The controller was safely stopped for installation and restarted. Before
  and after, it was enabled and **STANDBY / BACKUP / FENCED** with no IPv4
  address on the detached plugin adapter. This is the expected standby state.
- Configuration is byte-identical to its backup. Native CARP allow, demotion
  and maintenance readback are unchanged; no role or maintenance action was
  invoked. Local mapping, shared MAC and sync selection were preserved.
- Cleared all five compiled Volt templates and restarted the native web GUI.
  HTTPS responds **200**; installed Settings and Log templates compile.
- Native Syslog registered `dhcpinterfaceha/core`; the native log query returns
  actual new `dhcp-interface-ha` controller events, including the standby
  observation. This verifies event delivery and backend reading.
- A direct read-only invocation of the installed Status action using native
  OPNsense request/models reports `ready / global_backup`, with no relevant
  non-passing readiness checks. This is not an authenticated browser/ACL test.

No Configure operation, peer sync, failover or reboot was performed. Native
assignment/setup, authenticated browser and restricted-role behavior, log-viewer
interaction/retention, enabled boot and live paired-network qualification remain
outstanding. Deployment alone does not complete that acceptance matrix.

## 11. Log page route correction — HA-2 0.2_10, 2026-09-27

The user reported “page not found” when opening Log. The 0.2_9 link
`/ui/dhcpinterfaceha/log` addresses `LogController::indexAction`, which does
not exist. The actual implementation is `IndexController::logAction`, so its
native URL is `/ui/dhcpinterfaceha/index/log`. Corrected the menu, both views,
Retry link and exact wrapper ACL together. Native log API permissions are
unchanged. This was a plugin routing defect.

The previous direct-action fixtures and Volt syntax checks did not exercise URL
resolution. Added `tests/native/test_ui_routes.php`, which reads actual menu/tab
links and uses the installed native router to resolve their controller/actions
without dispatch or authentication changes. It reproduced the missing route on
0.2_9, passed for the candidate links, and passed after installation. Run it on
OPNsense with `php test_ui_routes.php` for installed files, or supply the
candidate plugin `src` directory to check its links against native controllers.

Both focused Log integration tests, both Node suites, XML parsing, PHP syntax
and diff checks pass. Built and installed `os-dhcp-interface-ha-devel-0.2_10`
on HA-2, package SHA-256
`b2b12800d14f2b482d138cffb10ccfee0836515feb2d45a730cc53878db18802`.
All 25 installed source hashes and package checksums pass. Compiled Volt cache
was cleared and the web GUI restarted; HTTPS responds. Unauthenticated HTTP
checks reach the login page and do not establish authenticated rendering; the
native router regression verifies the reported missing-page cause.

Private configuration backup, 0.2_9 rollback package and deployment logs are
in `/root/dhcpha-log-route-0.2_10.640t_upb` on HA-2. Configuration remains
byte-identical and the controller is running. HA-1 was not changed.

## 12. Log navigation cleanup — HA-2 0.2_11, 2026-09-27

Removed the separate Log child from the Services sidebar. The sidebar now has
one HA DHCP Interface entry, which opens Settings. Log remains available through
the Settings/Diagnostics/Log tabs within the plugin and keeps its dedicated ACL.

The candidate passed both Node UI suites and the native route check. Installed
`os-dhcp-interface-ha-devel-0.2_11` on HA-2, SHA-256
`23999e03e09d63095658bc90a974aa955ebc57efd5de1956c63820f56637385c`.
All 25 installed source hashes and package checksums pass. Installed menu XML
contains only `/ui/dhcpinterfaceha`, the Log tab still targets the verified
`/ui/dhcpinterfaceha/index/log` route, and HTTPS responds 200 after cache clearing
and web GUI restart. Configuration is byte-identical and the controller remains
running. The private 0.2.10 rollback package and deployment evidence are in
`/root/dhcpha-menu-0.2_11.jkmcpenj` on HA-2. HA-1 was not changed.

## 13. UI density cleanup — HA-2 0.2_12, 2026-09-27

Reduced Settings and Diagnostics to the controls and observations used in the
normal workflow. Settings no longer renders the local carrier node or mapping,
the configured-interface notice, observed timestamp, explanatory state sentence,
or configuration-sync box. Diagnostics no longer renders Current issues, status
checks, observation source errors or the CARP inventory. State remains the compact
operational summary; Diagnostics retains observed details, guarded recovery,
native links and snapshot download. The structured status/readiness data and
sync API remain available to safety guards and support tooling.

The candidate passed 108 Python tests, both Node UI suites, PHP lint, XML parsing,
diff checks and native Volt compilation. Installed
`os-dhcp-interface-ha-devel-0.2_12` on HA-2, package SHA-256
`4fccc28da66dcf4a4df4919ebd7c6d0b04397cf95c38899885373cf317b16ac7`.
All 25 installed source hashes and package checksums pass. The installed template
contains none of the removed sections, native Settings and Log routes resolve,
the Volt cache was cleared, the web GUI restarted and HTTPS responds 200.
Configuration remains byte-identical and the controller remains running. The
private 0.2.11 rollback package, configuration backup and deployment evidence are
in `/root/dhcpha-ui-cleanup-0.2_12.m6SIUL` on HA-2. HA-1 was not changed.

## 14. Settings form cleanup — HA-2 0.2_13, 2026-09-28

Removed the Shared connection, This node's connection and Shared network
identity collapsible section headers while retaining their form controls. The
MAC utilities now appear directly under Shared interface MAC without a separate
collapsible heading. Removed the page-level topology/XMLRPC explanation and the
save-action helper sentence. The MAC field now states only that the identity is
used by the active node, must match on both peers and is shared through native
XMLRPC configuration sync.

The candidate passed 108 Python tests, both Node UI suites, PHP lint, XML parsing,
diff checks and native Volt compilation. Installed
`os-dhcp-interface-ha-devel-0.2_13` on HA-2, package SHA-256
`9111653499814a34940c59c2798d6285dadc6eb5152e3c684c9ff9ae5fec91dd`.
All 25 installed source hashes and package checksums pass. The removed labels are
absent from the installed form and view, the Volt cache was cleared, the web GUI
restarted and HTTPS responds 200. Configuration remains byte-identical and the
controller remains running. The private 0.2.12 rollback package, configuration
backup and deployment evidence are in
`/root/dhcpha-settings-flat-0.2_13.K9Ywjj` on HA-2. HA-1 was not changed.

## 15. Guarded Disabled removal — HA-2 0.2.14, 2026-09-28

Renamed the empty managed-interface choice from None to Disabled. Selecting it
forces the unsaved Enable checkbox off, prevents re-enabling it and keeps Save &
Apply available even when an enabled interface is currently saved. Saving
Disabled clears the local managed interface, carrier and legacy failback value
while retaining the shared MAC.

An enabled installation uses a guarded two-phase server operation in one request:
save Disable with the existing identity, apply and fence it, then clear the local
mapping only after the ordinary fresh detached-state validation passes. Failed or
unknown apply leaves the disabled mapping intact and returns a staged result so
the UI reloads authoritative settings. Direct API callers cannot enable an empty
selection. Other interface or MAC identity changes retain their existing
disable-first guard.

The candidate passed 108 Python tests, both Node UI suites, PHP lint, XML parsing,
diff checks and native Volt compilation. The focused regressions reproduce the
previous disabled Save button, verify one-request enabled removal and prove a
failed fence retains the mapping. Installed
`os-dhcp-interface-ha-devel-0.2_14` on HA-2, package SHA-256
`c6b63241ece93bf8e6069281e0f132672879df1faab1215a0a46943641604230`.
All 25 installed source hashes and package checksums pass. The Volt cache was
cleared, the web GUI restarted and HTTPS responds 200. Installation did not run
the removal operation: configuration remains byte-identical and the controller
remains running. The private 0.2.13 rollback package, configuration backup and
deployment evidence are in `/root/dhcpha-disabled-0.2_14.9sAykh` on HA-2. HA-1
was not changed.

## 16. Unified Save and setup — HA-2 0.2.15, 2026-09-28

Removed the separate Configure interface and Save draft controls. Save & Apply
is always the single Settings action. For an unconfigured selection it invokes
the existing guarded setup endpoint, captures the committed native device as the
carrier, saves Enable off and completes native assignment migration. Once setup
is committed, the same button uses the ordinary settings endpoint. Uncertain
setup outcomes retain read-only Recheck and the existing mutation latch.

When Shared interface MAC is empty during initial setup, the server defaults it
to the freshly inventoried MAC of the selected eligible carrier before model and
cross-field validation. This behavior is server-side for browser and direct API
callers; explicit valid MAC values remain unchanged. Setup still confirms the
possible connectivity interruption, rejects stale/conflicting native assignment
state and never enables the service in the migration request.

The candidate passed 109 Python tests, both Node UI suites, PHP lint, XML parsing,
diff checks and native Volt compilation. Focused regressions verify that Save
dispatches setup only while migration is needed, that no Configure or draft
button remains, and that an empty MAC is saved as the selected carrier's observed
MAC. Installed `os-dhcp-interface-ha-devel-0.2_15` on HA-2, package SHA-256
`3a328e7d48534de94f535073710be839cd273abc69e6a52a2e83d31f2e1bdb8e`.
All 25 installed source hashes and package checksums pass. The Volt cache was
cleared, the web GUI restarted and HTTPS responds 200. Installation left the
then-current configuration byte-identical and the controller running. The
private 0.2.14 rollback package, configuration backup and deployment evidence
are in `/root/dhcpha-unified-save-0.2_15.ofViOK` on HA-2. HA-1 was not changed.

## 17. Verified native teardown — HA-2 0.2.16, 2026-09-28

Saving Disabled now treats removal as the reverse of setup. It disables and
fences the controller, stages the saved carrier through OPNsense's native
assignment model, rechecks the exact intent and pending queue, applies it, and
verifies the committed mapping and empty queue before clearing the plugin's
managed interface and carrier. The shared MAC remains saved as requested.

Failed, conflicting or unknown native restoration leaves the plugin disabled
with its managed interface and carrier retained, so the same operation can be
retried without guessing. Regression coverage reproduces the previous stranded
`dhcpha0lagg` assignment and verifies both successful restoration and retained
identity on native failure or unrelated pending edits.

The candidate passed 111 Python tests, both Node UI suites, PHP lint, XML
parsing and diff checks. Installed `os-dhcp-interface-ha-devel-0.2_16` on HA-2,
package SHA-256
`386db39fa49c1cc88c6a4e6da726efb095e23f9ec87b4cb0878364f05f463cc3`.
All 25 installed source hashes and package checksums pass. Installation left the
configuration byte-identical. The stranded assignment was then restored from
the pre-disable evidence using the same native assignment model: `opt7 → hn1`,
with an empty pending queue and cleared plugin-local identity. `hn1` obtained
DHCP address `10.250.100.109`. The web GUI cache was cleared, the service
restarted, native routes resolve and HTTPS responds 200. The 0.2.15 rollback
package, configuration backup and deployment evidence are in
`/root/dhcpha-native-teardown-0.2_16.iXEJS8` on HA-2. HA-1 was not changed.


### 0.2_18 — complete setup from Save & Apply

Native `interface list ifconfig` omits `laggport` for an empty LAGG. Setup and
prepare readback now treat that omission as empty while still requiring the
controller to verify ownership, disabled state and detachment. The fixture now
uses this native shape, covering both an existing and newly prepared device.
Setup records the carrier and applies/verifies the native assignment while
disabled, then validates and applies the submitted Enable choice. Failed native
migration cannot enable the plugin. Save has no confirmation popup or persistent
success message; validation and partial-failure messages remain visible.

Native verification also reproduced the post-assignment timing bug: native apply
raises the carrier, so immediate status was UNVERIFIED until the daemon fenced
it. Setup now explicitly reconciles after native apply and before readback.
Existing mappings and partial retries share the same completion/Enable path.

Validation: 113 Python tests, both Node UI suites, PHP lint and diff checks pass.
Installed 0.2_18 on HA-2 and verified all 25 installed source hashes plus package
integrity. Cleared the web UI cache and restarted the UI. Invoked the installed
settings controller with native Request/ACL/models/backend: a single configure
request from the cleared opt7→hn1 state returned saved=true, applied=true,
assignment_verified=true and setup_stage=verified. Final runtime: enabled=true,
opt7 managed, CARP BACKUP, STANDBY, owned detached device and eligible safe hn1.
This checks the native controller, not authenticated browser interaction.
Package SHA-256: `655d63667b52d72fb0b4235612b4dea6a0e28a3fcf61d3fa8d15bf0abbffbd2e`.
Evidence: `/root/dhcpha-save-0.2_17.41sdAs` (includes initial 0.2_17 investigation
and final `save-result-18.json`). HA-1 was not changed.


### 0.2_19 — release disabled carrier reservation

Disabled + Save previously stopped after disabling because the device hook
excluded the saved carrier even while disabled. Native DeviceField validation
therefore rejected restoring the assignment, retaining opt7 and reloading it in
the form. Reserve the carrier only while enabled; keep existing native validation
and the saved carrier until restoration is verified. A real hook regression
checks both enabled exclusion and disabled availability.

Verified 0.2_19 on HA-2: the installed settings action returned saved=true,
applied=true, cleared=true. Native opt7 was restored to hn1; managed_interface
and carrier are empty, enabled=0, shared MAC retained. settings/get selects only
the empty-value Disabled option. All 114 Python tests and both UI suites pass;
PHP lint, package integrity and all 25 installed source hashes pass. UI cache
cleared and web UI restarted. Evidence: `/root/dhcpha-fix-0.2_19.WTt7KG`.
Package SHA-256: `544475ad850ffaae5cdd578e43649f0e92ec80263527bbc1a9945105df9ca455`.


### 0.2_20 — minimal Settings state display

Replace the Current local state box with a single live `State: <status>` line
at the top of Settings. Remove its node/interface/MAC rows and refresh button;
automatic status updates and the Diagnostics refresh action remain.

UI behavior suite and diff checks pass. Deployed 0.2_20 to HA-2; package integrity
and all installed source hashes verified, UI cache cleared and web UI restarted.
Evidence: `/root/dhcpha-fix-0.2_20.8N2vzv`.


### 0.2_21 — remove redundant Diagnostics controls

Remove the Guarded recovery section, expandable pfsync runtime dump, package
removal instructions/readiness row, snapshot download button and disclosure.
Remove their unused rendering/event handlers. Keep observed details, native
configuration links and Refresh. Settings still supports Retry Apply after an
apply failure; its feedback appears in Settings. Backend guards remain intact.

Both UI suites and diff checks pass. Deployed 0.2_21 to HA-2; installed source
hashes and package integrity verified. Cleared the UI cache and restarted the
web UI. Evidence: `/root/dhcpha-fix-0.2_21.FwyGrE`.


### 0.2_22 — HA DHCP Interface display name

Rename the product throughout UI labels, menu, ACL descriptions, native sync
label, controller messages, package description and documentation. Preserve
package/config/API/service identifiers to avoid a configuration migration.

All 114 Python tests, both UI suites, PHP lint, XML parsing and diff checks pass.
Deployed 0.2_22 to HA-2; verified package integrity and all installed source
hashes. Cleared the UI cache and restarted the web UI. Evidence:
`/root/dhcpha-fix-0.2_22.TVf2KE`.


### 0.2_23 — General overview page

Add General above Settings in the sidebar, with a short explanation of the
purpose, native CARP handoff, shared MAC, initial setup and expected states.
Use a native MVC action/view and the existing Settings privilege. This page
performs no status requests or configuration changes.

PHP lint, XML parsing, focused metadata/log integration tests and diff checks
pass. Deployed 0.2_23 to HA-2; package integrity and all 26 installed source
hashes verified. Native router resolves General, Settings and Log. UI cache
cleared and web UI restarted. Evidence: `/root/dhcpha-fix-0.2_23.ECC4PQ`.


### HA-1 upgrade to 0.2_23

Upgraded HA-1 from 0.2_8 using the same verified package as HA-2. Retained the
old package and configuration under `/root/dhcpha-upgrade-0.2_23.cFUaKp`.
Configuration was byte-identical after installation and final verification.
Verified all 26 installed source hashes, package integrity, absence of obsolete
package files and unexpected files in plugin directories, and absence of legacy
WAN HA plugin files or obsolete plugin configuration fields. Cleared plugin
Python bytecode, compiled PHP/Volt templates and lighttpd compressed cache.
Restarted the controller and web UI; the replacement controller PID was 70958.
Native General, Settings and Log routes resolve; HTTPS responds successfully.
Final runtime was ACTIVE, CARP MASTER, hn0 attached to owned dhcpha0lagg,
shared MAC 00:15:5d:05:74:26, IPv4 10.250.100.100. Native assignment controller
matches HA-2; its older interface model differs only in new-interface identifier
handling, outside this plugin's existing-assignment relink path.


### 0.2_24 — remove physical MAC suggestion from Settings

Remove Current interface MAC and Use current interface MAC, plus their unused
rendering/copy logic. Keep the shared MAC field, Generate, and the existing
server-side default for initial setup. Retain the stale-interface preview test;
remove tests for the deleted suggestion control.

UI behavior checks and diff checks pass. Deployed 0.2_24 to both nodes; package
integrity and all installed source hashes verified. Cleared plugin compiled
UI templates and compressed caches, then restarted both web UIs. HA-1 evidence:
`/root/dhcpha-upgrade-0.2_24.CypXry`; HA-2:
`/root/dhcpha-fix-0.2_24.JNAeMN`.


### 0.2_25 — shared MAC help wording

Use the requested text: “This setting is sync from MASTER to BACKUP through
XMLRPC configuration sync.” This is a wording change only. XML parsing and
diff checks pass; packaged source hashes verified.

Deployed 0.2_25 to both nodes, verified installed source hashes/package integrity,
cleared UI caches and restarted web UIs. Evidence:
HA-1 `/root/dhcpha-upgrade-0.2_25.BMvW71`;
HA-2 `/root/dhcpha-fix-0.2_25.YwZVHg`.


### 0.2_26 — preserve Enable while loading Settings

Native setFormData emits change after each field. The Enable field precedes the
interface dropdown, so its initial change reached updateActions while the
interface still appeared Disabled and cleared the checkbox. Suppress user-edit
handlers only during synchronous form population; evaluate actions after the
full form is loaded. A regression replays native field/change order, verifies
saved Enable remains checked, and verifies a real Disabled selection still
clears it. Both UI suites and diff checks pass.

Deployed 0.2_26 to HA-1 and HA-2; package integrity and installed source hashes
verified, compiled plugin UI/compressed caches cleared and web UIs restarted.
HA-1 configuration remained byte-identical. Evidence:
HA-1 `/root/dhcpha-upgrade-0.2_26.Xaw7lI`;
HA-2 `/root/dhcpha-fix-0.2_26.xFbfaN`.


### 0.2_27 — quiet saves and fresh removal inventory

Native `interface list assign-opts` has a 30-second configd cache. A carrier
released by disabling could remain excluded from cached options, causing the
first restoration attempt to fail native validation. Refresh through configd's
native `!` prefix before creating the restoration model, outside the config lock.
Retain native validation and all intent/queue checks. The fixture reproduces a
cached excluded carrier and now verifies one-call enabled/disabled removal.

During Save & Apply, suppress setup advisories and readback controls so polling
the operation's own pending relink does not warn the user. Remove progress prose,
clear old result text at save start, omit internal stage labels from failures,
and retain the specific backend error when recovery readback remains incomplete.
A UI regression checks that in-flight relinking produces no retry warning.

Validation: 114 Python tests, both UI suites, PHP lint and diff checks pass.
Installed 0.2_27 on both nodes, verified source hashes/package integrity, cleared
UI caches and restarted web UIs. Native Backend forced assignment-option refresh
succeeded on both nodes. No live configuration transition was needed for this
verification. Evidence: HA-1 `/root/dhcpha-upgrade-0.2_27.p36u8l`;
HA-2 `/root/dhcpha-fix-0.2_27.HRnn8R`.


### 0.2_28 — no relink advisory on normal saves

Remove the automatic pending-relink text entirely; actual setup/save preflight
failures still explain a real unresolved queue. Normal settings saves retain the
existing interface preview rather than resetting it while another read starts.
Do not infer setup-readback controls from old pending observations for an enabled
saved interface. Explicit unresolved-operation guards still apply.

Regression checks verify that an enabled saved mapping routes to settings/set
even with a stale preview; the settings controller performs no native assignment
apply and retains an empty pending queue. UI coverage reproduces an old pending
observation after a normal save and verifies no unsolicited relink advisory.

Focused controller and UI suites pass. Deployed 0.2_28 to both nodes, verified
installed source hashes/package integrity, cleared UI caches and restarted web
UIs. Evidence: HA-1 `/root/dhcpha-upgrade-0.2_28.CAVIXz`;
HA-2 `/root/dhcpha-fix-0.2_28.Jru4Ik`.

### 0.2_29 — unchanged saves have no side effects

Ordinary settings saves compare canonical values under the configuration lock,
retaining revision conflict protection. Identical settings return `unchanged`
without configuration writes, settings events, runtime inventory collection or
controller apply. The UI accepts this quietly. Already-disabled, empty local
mappings also skip work. Initial setup, teardown and explicit Retry Apply retain
their existing guarded actions.

Regression coverage checks enabled and cleared configurations perform zero writes
and backend calls, and stale unchanged submissions still reject. The existing
apply-outcome fixture now submits an actual change. All 116 Python tests, both UI
checks, PHP lint and diff checks pass.

Deployed to both nodes with installed source hashes and package integrity verified.
Cleared compiled plugin templates and compressed UI caches, then restarted both
web UIs. HA-1 configuration hash remained unchanged across installation.
Evidence: HA-1 `/root/dhcpha-upgrade-0.2_29.7RPu0G`;
HA-2 `/root/dhcpha-fix-0.2_29.0j2BpV`.

### 0.2_30 — remove transient fresh-status advisory

Remove the automatic “Save & Apply requires fresh status” note and its unused
predicate. It flashed during page initialization before settings and observations
were loaded. Save-time freshness/detachment checks and their failure explanations
remain unchanged. Both UI behavior suites and diff checks pass.

Deployed 0.2_30 to both nodes, verified installed source hashes and package integrity,
cleared compiled plugin templates/compressed caches and restarted both web UIs.
Evidence: HA-1 `/root/dhcpha-upgrade-0.2_30.aOC78J`;
HA-2 `/root/dhcpha-fix-0.2_30.pRjAC7`.

### 0.2_31 — initialize controller on fresh package installation

Testing on reset HA-2 found that package installation registered the UI/models but
never started the controller: status failed with “interface transition state is
not initialized”, preventing initial setup. The boot hook alone did not cover
installation without reboot. A post-install hook now starts a missing controller,
skips offline package roots and preserves a running controller on upgrade.

A lifecycle regression check covers first install, running-service preservation,
offline roots and start failure. Both native build and signed-install workflow
gates now require readable initial status, a running controller and no configured
or attached interface. The runtime status reader remains read-only.

Native verification: removed the unconfigured 0.2_30 package, then installed
0.2_31 on reset HA-2. The package hook started the controller automatically.
Fresh-status and native menu/tab route checks passed; settings readback selected
Disabled with Enable off. The environment API reported SETUP_INCOMPLETE with no
observation errors, and the native log stream recorded service_started and the
initial state. Installed source hashes and package integrity match. UI caches
were cleared and the web UI restarted. Native interface assignments and HA-2's
maintenance state remained unchanged. HA-1 remained ACTIVE/MASTER at
10.250.100.100. Evidence: `/root/dhcpha-clean-install-0.2_31.DMU0Jj`.

117 plugin tests, 16 focused release-helper tests, shell lint and workflow lint
pass. The local test package is built from commit `2d5533de4`; SHA-256:
`892a4e2349f75ab5c6336aa8ad27b3fdc9c9ffc20e695142c0835c51fa3d6384`.

### 0.2_32 — fresh-install release without legacy model migration

Remove the custom M1_1_0 migration and its migration-only native fixture. The
plugin no longer copies a managed interface from the former Shared field into
Local. Native model version metadata and initialization remain, as do current
scope validation, setup/teardown and package lifecycle safety checks. Historical
migration evidence above describes development builds, not a supported upgrade
path for this release.

Validation: 117 plugin tests and both UI checks pass. Built and installed 0.2_32
on HA-2; package contents omit the migration. The old package left an unowned
M1_1_0.php behind, so its hash was verified against the prior source and the file
was moved out of the installed tree into the deployment evidence directory.
No legacy-file cleanup or migration logic was added to the new package. Installed
source hashes, package integrity and fresh-state checks pass; configuration is
unchanged and the web UI was restarted. HA-1 remains on 0.2_30.

HA-1 package replacement: backed up configuration and the previous package,
archived the obsolete M1_1_0.php, then installed the same verified 0.2_32 archive
used on HA-2 through pkg. No unowned plugin source files remain in the MVC or
runtime trees. Source hashes/package integrity and native menu/tab routes pass.
Configuration and controller PID remained unchanged; UI caches were cleared and
the web UI restarted. HA-1 remains ACTIVE/MASTER on opt7 at 10.250.100.100; three
source-address pings to 10.250.100.1 succeeded without loss. Evidence:
`/root/dhcpha-package-0.2_32.cyXOi7`. Both nodes now have 0.2_32 installed.

### 0.2_33 — physical-carrier investigation logging

HA-1 now uses ix0 directly for ISP WAN. The September 28 attempts first failed
native setup with residual carrier IP/CARP state; subsequent setup completed,
but enabling ended in `carrier_link_down`. Earlier hn carrier tests attached
successfully. Existing logs lack administrative UP and promiscuous flags, so
they do not establish why the physical driver reported no link. A possible
admin-down/link-down eligibility dependency remains a hypothesis, not a verified
driver defect or user configuration error.

Add change-only `interface_observed` events from the existing pre-reconcile
snapshot: names, enabled state, role, carrier/LAGG existence, administrative UP,
native link report, PROMISC, LAGG members, carrier address/CARP counts and reason.
Missing inventory fields remain unknown. The next reconcile captures resulting
changes. No new probes, interface commands or eligibility changes are introduced;
read-only status/health remain silent. The new regression covers an ix-named
carrier with no link, changing administrative state, residual addressing and
steady-state suppression. All 118 Python tests pass. Live reproduction requires
a coordinated WAN interruption with the administrator.
