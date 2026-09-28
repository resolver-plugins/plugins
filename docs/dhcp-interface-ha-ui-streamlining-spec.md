# DHCP Interface HA: streamlined setup, diagnostics and logging

Status: implementation contract for experimental 0.2_9, 2026-09-27. The companion
[implementation plan](dhcp-interface-ha-ui-streamlining-plan.md) records actual
source verification and outstanding native acceptance checks. This specification
is not evidence that either appliance has been updated.

Reviewed for implementation handoff on 2026-09-27. The contracts below resolve
setup retry, diagnostic classification, native apply and logging ambiguities.
Native integration checks in the plan are still required before release.

This specification supersedes the older UI repair contract where it prescribes
manual carrier selection, routine manual preparation, three separate
Settings/Status/Diagnostics tabs, or user repair of plugin-owned configuration.
The [controller design](dhcp-interface-ha.md) remains authoritative for CARP,
fencing, shared identity, locking and supported interface types. Historical
deployment records remain evidence, not instructions to restore old behavior.

## Problem statement

The plugin now automates carrier discovery, device preparation, assignment
migration and shared-MAC receive filtering. The UI still presents much of that
work as manual prerequisites. Important setup actions are below a long checklist,
healthy states generate excessive detail, and some messages imply that the user
misconfigured something the plugin is responsible for maintaining.

Logging currently emphasizes exceptions. It does not provide a coherent history
of setup, CARP-driven attachment, automatic repair and recovery. A future agent
cannot reliably infer the responsible component or appropriate remedy from an
English error message alone.

## Solution

Use three tabs: **Settings**, **Diagnostics**, and **Log**. Settings combines a
compact live summary with the three normal controls: Enable, Interface and
Shared MAC. **Save & Apply** completes supported local setup while disabled and
remains the single settings action afterward. The carrier is discovered and
displayed, never requested as a second normal interface choice.

The plugin maintains its own invariants. Diagnostics describe observations,
automatic recovery and any remaining reason the operation cannot proceed. They
request user action only for an actual choice, permission boundary, conflicting
native configuration or external prerequisite. Log uses the native OPNsense log
infrastructure and viewer, with meaningful events and bounded repetition.

### Ownership and recovery contract

“Owned” means the plugin can establish identity and exclusive authority from
current configuration and runtime evidence. A familiar device name alone does
not establish ownership. Local ownership does not establish peer readiness.

| Condition or resource | Responsible component | Expected behavior | When user action is justified |
|---|---|---|---|
| Logical interface choice and shared MAC | User intent | Validate the submitted choice; never guess a different connection or replace a saved MAC on load | Missing/invalid choice or an intentional identity change |
| Carrier mapping during initial setup | Plugin setup | Capture the selected interface's original eligible device and persist it before relinking | Original device is unknown and cannot be recovered from authoritative local configuration |
| Owned LAGG creation, protocol, MAC preparation, attachment and fencing | Plugin controller | Establish and maintain the supported topology under the existing transition lock | A foreign device, conflicting owner, or unavailable authority prevents a safe operation |
| Shared-MAC receive mode | Plugin controller | Set and verify promiscuous reception and repair drift automatically | No manual checkbox is a normal prerequisite; failure requires investigation or a guarded retry |
| Moving the selected native assignment to the owned LAGG | Plugin setup using native assignment machinery | Perform the confirmed setup action and verify the result | Confirmation of connection interruption, conflicting edits, or unavailable native permissions/API |
| CARP roles, maintenance, VIP configuration and preemption | Native OPNsense | Observe and obey; standby and maintenance are normal | Missing native CARP setup, conflicting VIP placement, or an intentional operator change |
| Native DHCP configuration, lease acquisition, routes, gateways and firewall rules | Native OPNsense | Report evidence accurately and invoke only already-supported lifecycle operations | Unsupported address mode or other conflicting native settings; demonstrated native/network fault |
| Hypervisor permissions, network attachment, link and upstream DHCP server | Environment | Report observations without inventing a cause | Evidence identifies an external prerequisite or the next external investigation is necessary |
| Native XMLRPC destination, credentials and direction | Native HA configuration/user intent | Preserve the configured direction; optionally add this plugin to the existing sender selection | No destination exists or the administrator must choose synchronization policy |

An absent plugin-owned invariant after completed setup is **configuration drift
or a plugin failure to maintain its contract**, not automatically a user error.
Attempt the permitted repair. A repair failure is an operational fault, not proof
that its root cause is a programming defect. Permission failure, an unavailable
backend and a command that succeeds without the expected readback must remain
distinguishable. Observation failure is **unknown**, not a failed prerequisite.

This increment preserves the existing repair capabilities: creating a missing
owned path, reconciling attachment/MAC and restoring receive mode. It does not
add automatic native assignment migration, protocol replacement or topology
cleanup to the periodic loop. An existing device with wrong protocol or
unexpected members still follows the controller's current refusal/fencing
rules. Responsibility describes who must investigate a failure; it does not
grant permission for a new repair operation.

For agents: if the shared receive filter disappears, repair the controller or
its integration and protect the behavior with a regression test. Do not add a
manual “enable promiscuous mode” setup requirement to make the test pass. The
same rule applies to other plugin-owned invariants. Conversely, do not “fix” a
foreign-device conflict by weakening ownership checks or altering that device.

## User stories

1. As an administrator, I want to see whether this node is active, standby,
   disabled, in maintenance, recovering or unable to proceed without scanning a
   checklist.
2. As an administrator, I want attachment and DHCP address availability shown
   separately so an active carrier is not mistaken for a working connection.
3. As an administrator, I want to select one logical interface and have its
   carrier recorded automatically so I cannot accidentally select an unrelated
   adapter.
4. As an administrator, I want the setup action beside that selection so I do
   not have to search below diagnostics to complete setup.
5. As an administrator, I want a clear interruption confirmation before native
   reassignment, followed by verification of the actual result.
6. As an administrator, I want successful setup to leave the plugin disabled
   until I explicitly enable it.
7. As an administrator, I want the configured MAC preserved across refreshes,
   repairs and interface removal unless I explicitly change it.
8. As an administrator, I want automatic repairs to occur without being told to
   manually maintain plugin-owned settings.
9. As an administrator, I want a failed repair to state what was attempted,
   what remains safe or unverified, and the next useful action.
10. As an administrator, I want stale or unavailable observations labeled as
    such instead of seeing a false failure or an old green status.
11. As an administrator, I want standby's detached carrier and absent address
    treated as expected behavior.
12. As an administrator, I want only current actionable issues expanded, while
    retaining access to every check and its evidence.
13. As an administrator, I want normal CARP controls to remain the only way to
    request maintenance or role changes.
14. As an administrator, I want to include the plugin in an already-configured
    native sync sender without leaving the plugin page or changing other sync
    selections.
15. As an administrator, I want the receiving node to avoid presenting an
    outbound-sync selection as a required setup step.
16. As an administrator, I want to inspect local events and failures through a
    Log tab with native filtering, timestamps and severity.
17. As an administrator, I want successful repairs and recoveries logged so I
    can understand what happened after a transient issue disappears.
18. As an administrator, I want repeated failures to remain visible without
    flooding the log every reconciliation cycle.
19. As an administrator, I want a failed or timed-out setup to retain the
    captured carrier and distinguish saved configuration from applied state.
20. As an administrator, I want read-only inspection and log access to make no
    configuration or runtime changes.
21. As an administrator, I want selecting None to retain the established
    clear-on-Save behavior and preserve the shared MAC.
22. As a future agent, I want stable condition codes, explicit responsibility
    and observable recovery outcomes so I can choose between fixing a defect,
    retrying a failed operation and requesting missing user intent.
23. As a future agent, I want code and behavioral tests to express the safety
    boundaries so a UI wording change cannot silently change recovery policy.
24. As a maintainer, I want native logging and small additions to existing
    boundaries instead of a separate logging service or recovery framework.

## Implementation decisions

### 1. Page layout and status meaning

- **Settings** is the default tab and includes the operational summary; the
  separate Status tab is removed. Existing status links/bookmarks should land on
  the summary rather than a dead tab.
- The summary shows this node, operational role/state, selected interface,
  observed IPv4 address or DHCP wait state, and the configured shared MAC.
- Combine existing observations for presentation without replacing the
  controller state machine. Examples: **Active · CARP MASTER · 10.250.100.100**,
  **Active · Waiting for DHCP**, **Standby · Interface intentionally
  disconnected**, and **Maintenance · Interface disconnected**.
- “Ready” means local required setup checks pass. It does not mean peer
  readiness, internet reachability, DHCP success or session-preserving failover.
- Missing IPv4 on an attached eligible node is an observation to display, not
  an input to CARP election. A lease file alone does not prove a current address.
- Unavailable/stale observations retain their age and uncertainty. Never
  retain an unqualified green summary after a required observation fails.
- Keep a concise experimental marker. Put interruption warnings at the setup
  action, and explanatory detail in help/Diagnostics rather than repeated
  paragraphs across the main page.
- Use existing OPNsense form, tab, validation and status conventions. Add no
  frontend framework or separate wizard.

The live summary describes **saved configuration** and runtime observations.
Unsaved interface/MAC changes affect the form preview only. Use the existing
five-second visible-page polling interval with one request in flight; discard
responses for an older selection or request generation. A failed required
observation makes that summary component unknown immediately. Otherwise mark
an observation stale after 15 seconds without fresh evidence, including on
return from a hidden tab. Retained values must say “last observed” and show age.
Do not restart that freshness window on response receipt after slow collection;
conservative client request-start time may bound age without assuming aligned
browser/appliance wall clocks. Keep the server observation timestamp for display.

Apply this presentation precedence: verified unsafe or failed attachment/fencing;
unknown required evidence; disabled; native maintenance; normal standby;
active; otherwise unable to proceed with the controller's reason. Disabled,
standby and maintenance are normal only when detachment is verified. ACTIVE
requires verified attachment. Show “Waiting for DHCP” only when the address
observation succeeded and found no usable IPv4 address; failed address lookup
says “IPv4 address unavailable.” Do not add a DHCP wait timeout or infer a
DHCP-server fault in this increment.

### 2. Settings and the normal setup path

The main form has Enable, Interface and Shared MAC. Show the detected carrier
as read-only mapping text. The full internal LAGG topology is available in
Diagnostics. Put Use current MAC and Generate next to the MAC field; neither
changes saved state until a save action. Do not suggest an empty LAGG's zero MAC.
Use current MAC reads the verified original carrier before migration or the
saved carrier afterward. If that observation is unavailable/invalid, leave the
field unchanged and explain why; never replace it with zeros or stale data from
a previously selected interface. Generate remains an explicit user choice.

**Save & Apply** is the only settings action. While migration is required, it
saves the submitted plugin form with enablement off, captures the current native
device as the carrier and completes the native assignment. An empty Shared MAC
defaults to the freshly observed usable MAC of that carrier. Once configured,
the same button performs ordinary settings saves. Do not expose separate
Configure or Save draft actions.

| Saved state / selected form value | Available action and meaning |
|---|---|
| Disabled; Disabled | Save & Apply clears the local mapping, enablement and failback value, preserving MAC. |
| Disabled; eligible original native device | Save & Apply captures the carrier and completes guarded native setup. Setup forces Enable off even if checked in the submitted form. |
| Disabled; selected assignment already maps to the owned LAGG with a known carrier | Save & Apply performs an ordinary settings save. Remaining prerequisites appear separately. |
| Enabled; identity unchanged | Save & Apply performs ordinary edits or disables the service. |
| Enabled; interface/MAC changed | Require saving Disable with the old identity first, then fresh detached evidence before identity changes. |
| Enabled; Disabled selected | Save & Apply first disables and fences the saved identity, then clears the local mapping only after fresh detached evidence. A failed or unknown fence retains the mapping. |
| Foreign device, ambiguous carrier, stale observation or missing permission | Explain the specific blocker; Save & Apply never bypasses it. |

“Interface configured” proves committed native mapping, recorded carrier and
owned device identity, not all readiness checks or a DHCP address. “Ready to
enable” additionally requires every relevant local blocker to pass. Optional
pfsync/sync guidance and unverified peer readiness never gate enablement. If no
interface is selected and the saved plugin is disabled, show “Not configured”
instead of a page of missing prerequisites. Do not overwrite dirty form fields
after polling or a sync-selection action.

The action must:

1. Check write permissions, current saved disabled state, revision, selected
   assignment and eligible carrier. A cleared checkbox alone is not evidence
   that an enabled controller has been disabled and fenced.
2. Explain the selected connection interruption and obtain confirmation. Read
   canonical native assignment state, not merely a pending form choice.
3. Refuse observed unrelated pending native assignment edits. Also refuse a
   pending edit on the selected assignment unless it exactly matches a verified
   continuation of this setup. Recheck before staging and immediately before
   native apply; preserve and report partial progress.
4. Verify or prepare the owned detached device, capture and persist the carrier
   before relinking, and use the native assignment mechanism. Keep the existing
   shared/local configuration validation and revision checks.
5. Verify the new mapping and final local setup state. Leave enablement off.
   Distinguish “configuration saved,” “native apply completed,” and “result
   unknown”; only verified outcomes may be displayed as success.
6. On retry, inspect the already-saved mapping and observed state. Resume only
   when ownership, intent and the completed steps are unambiguous. Do not
   blindly replay a timed-out native mutation.

**Native apply limitation:** the target assignment API applies its shared
pending queue; it has no selected-interface-only apply or atomic revision
precondition. Preflight/rechecks prevent applying *observed* unrelated edits,
but cannot exclude an edit arriving after the final check. State in the setup
confirmation that native interface editing must not run concurrently. Do not
claim atomic isolation, clear other pending edits, hold the configuration lock
across a blocking configd call, or patch upstream core to simulate it. If an
unexpected mapping is observed afterward, report a conflict/partial result;
do not roll back someone else's configuration.

For timeout/partial-result readback, `status/environment` includes
`setup.pending_assignment` with `available` (boolean) and `state` (`clear`,
`selected_relink`, `conflict`, or `unknown`). `selected_relink` means the native
queue contains only this target and the saved local mapping explains its
carrier/committed assignment. This read is observational, not a reservation.
Readback success requires `clear`; a confirmed retry may use `clear` or
`selected_relink` with the other ownership, disabled-state and intent guards.
Unknown or conflicting pending state keeps Retry unavailable. The mutation
endpoint independently repeats its fresh queue checks.

An uncertain setup result keeps mutation controls blocked until full readback
establishes completion or a safe retry. **Recheck save outcome** performs
only reads; a normal status poll cannot clear this guard. After a page reload,
an observed matching pending relink also exposes Recheck so the saved intent can
be verified without lost browser request history or manual native apply.

#### Setup request and result contract

Use one POST `settings/configure` action for the confirmed operation, taking
the same complete `dhcphashared`, `dhcphalocal` and `revision` inputs as
`settings/set`. Reuse its validation/save implementation. The server derives
the carrier from committed native assignment evidence (or verifies an existing
saved mapping); a submitted carrier is never authority to select another device.
Reject stale revisions before any mutation. Both plugin write and native
assignment write privileges are required; read-only and CSRF protections apply.
The browser must no longer run its own parallel prepare/save/relink sequence.

Return the existing `result`, `saved`, `applied`, `error`, `revision`, validation
and status conventions, adding `setup_stage` and `assignment_verified`:

- `setup_stage`: last verified boundary, one of `none`, `prepared`,
  `settings_saved`, `assignment_staged`, `assignment_applied`, `verified`.
- `saved`: true when the requested disabled settings are verified saved, false
  when rejection/failure confirms no save occurred, null when the save outcome
  is unknown.
  `applied`: true/false/null for verified native apply success, known failure
  or unknown result. In this endpoint it describes **native assignment apply**;
  `settings/set` retains its existing controller-apply meaning. An already
  completed setup returns true after fresh verification without rerunning apply;
  preflight rejection returns false with `setup_stage=none`.
- `assignment_verified`: true/false/null for committed mapping plus owned,
  detached runtime readback. A successful command response alone is insufficient.
- `result`: `saved` only after complete verified setup; otherwise `failed` or
  `conflict` as appropriate, even when `saved` is true or null. Return the latest known
  plugin revision after a save, without claiming a revision was observed if it
  was not. Errors identify the failed boundary without exposing raw requests.

A transport timeout means the overall outcome is unknown regardless of the
last browser response. Refresh settings, committed/pending assignment and runtime
before offering confirmed Retry. Already-completed migration is verified without
reapplying; a saved carrier plus the unchanged original assignment may resume;
an exact pending relink to the owned device may be applied after all guards pass.
Conflicting pending edits, changed identity or unverified state block replay.
No background job system, persistent setup journal or automatic rollback is needed.

The normal setup flow does not ask the user to create the LAGG, select a carrier,
enable its receive mode, or make the assignment on another page. A backend/API
failure must not routinely be converted into “go configure it manually.”
If an already-migrated interface has no recoverable carrier mapping, state the
ambiguity and provide guarded recovery guidance; do not guess from driver name,
MAC prefix or another node's interface name.

Enablement still requires valid local setup. Identity changes still require a
saved disabled state and verified fencing. Disabled clears plugin settings on
save except the shared MAC. When the saved configuration is enabled, the same
request first saves and applies Disable with the old identity, then clears the
mapping only after fresh detached evidence. It does not delete native
assignments, rules or gateways or silently restore an original native
assignment. Describe that boundary at removal time.

### 3. Conditions that people and agents can interpret

Extend the existing structured readiness/status response narrowly. Retain stable
codes and existing local/peer scope, severity and pass/fail/unknown status.
Represent these additional distinctions explicitly, using ordinary fields or
small named values rather than a new policy engine:

| Field | Contract |
|---|---|
| `responsibility` | `plugin`, `native`, `environment`, or `user`; identifies who satisfies the condition, not who caused an incident |
| `resolution` | `none`, `automatic`, `retry`, `user_action`, or `investigate`; driven by current evidence and safety guards |
| `relevant` | Boolean; include in the default issues view only when true and status is not pass. Irrelevant checks remain in Show all checks with an explanation. |
| `attempt` | Null unless the responding operation actually observed an attempt. Otherwise include `operation` and `outcome` (`pending`, `repaired`, `failed`, `unknown`). “Repaired” requires readback; do not fabricate daemon history in a later GET. |
| Existing `action` | Stable allowed action identifier or null, never inferred from translated text. Mutating actions remain independently guarded on the server. |

Keep `status` limited to `pass`, `fail`, `unknown`, and keep severity values
`blocker`, `warning`, `info`; do not add “repairing” to either enum. Reuse the
enclosing observation timestamp, availability/error fields and evidence instead
of duplicating a snapshot in every check. `automatic` means an enabled controller
is expected to attempt an existing safe repair, not proof a command is currently
running. With no attempt evidence say “Automatic recovery expected; refresh
status,” not “Repairing…” or “Repair failed.” Successful checks use `none` and
null action; unavailable evidence uses `unknown` and never `user_action` solely
because a lookup failed.

Apply these classifications to **all** current readiness codes:

| Existing code(s) | Responsibility and non-passing interpretation |
|---|---|
| `managed_interface`, `shared_mac` | User intent when missing/invalid. Split MAC syntax from collision evidence: unavailable collision inventory is unknown, not invalid MAC. A confirmed conflict needs investigation before identity changes. |
| `managed_assignment`, `carrier_selection` | Plugin setup; initial setup awaits confirmed Configure, not manual LAGG/carrier edits. Unexpected post-setup drift requires guarded recovery/investigation. |
| `carrier_capability`, `carrier_exclusive` | Environment capability or native conflict respectively. Original carrier on the selected assignment is expected before migration; use Configure guidance. An unrelated assignment/VLAN/LAGG conflict requires a user decision. |
| `device_ownership`, `device_topology` | Plugin; absent device may be prepared/recreated through existing safe paths. Foreign identity/wrong protocol/unexpected topology never grants destructive repair. |
| `managed_ipv4`, `managed_ipv6`, `native_spoof_mac`, `hardware_media`, `managed_carp_vips` | Native configuration conflict; explain the incompatible value and native link. This increment does not silently rewrite address modes, overrides or VIPs. |
| `carp_inventory` | Native; unavailable observation is unknown. Observed mismatch blocks attachment and needs native investigation, not an automatic VIP edit. |
| `failback_policy` | User intent; unsupported nonzero legacy value offers an explicit reset to zero through normal Save & Apply. Do not silently drop a saved value on load. |
| `pfsync_context`, `peer_readiness` | Native / environment context, informational and not relevant to local readiness. Retain details without an always-expanded issue. |
| `xmlrpc_selection` | User sync policy; warning and relevant only for a configured sender missing plugin selection. No destination is neutral context, never a blocker. |

Add `receive_mode` for verified active attachment's carrier/LAGG receive flags.
It is plugin-owned and automatically repairable only within the existing runtime
guards. Missing flags while intentionally detached are not an issue. Do not
derive ownership or repair eligibility from a frontend table.

Responsibility, severity and resolution are independent. For example, a
plugin-owned setting being restored is informational/pending, a failed
restoration can be an error, and an unfamiliar device is a blocked ownership
conflict. None of these proves the user made an incorrect change.

Use one explicit interpretation of known conditions at the appropriate existing
boundary. Do not maintain competing frontend and backend classifications, or
derive actions by searching translated messages. Keep names and guards close to
the operations they describe. Comments explain why a boundary exists; tests
demonstrate the contract. Do not create a new persisted “health” or “diagnostic
policy” configuration schema for these observations.

Diagnostic observation remains read-only; recovery buttons are explicit POST
actions. Show **Repairing…** or **Recovering** only with an observed in-flight
attempt, not merely because the periodic controller is enabled. Do not promise
automatic retries for an operation that is actually awaiting confirmation.
Once repaired, the live issue clears and the event remains in Log.

Examples:

| Evidence | Presentation and agent interpretation |
|---|---|
| Receive filter missing on an owned active LAGG | Automatic repair responsibility; failure is investigated at the controller/native boundary, not resolved by adding a user prerequisite |
| Controller observes BACKUP with detached carrier | Expected standby; no repair or warning required |
| Status command timed out | Observation unavailable; refresh evidence, do not declare an interface defect |
| Setup backend rejected a permitted operation | Setup failed with its actual error and verified partial progress; retry only if preconditions still hold |
| Selected interface also carries CARP VIPs | Native configuration conflict; request an explicit interface/configuration decision, never remove VIPs automatically |
| DHCP address absent after attachment | Active, waiting for DHCP; expose native DHCP evidence, without claiming a server failure or changing CARP |
| Unowned device has the plugin's expected name | Stop setup/attachment; preserve the device and explain the ownership conflict |

### 4. Diagnostics and guarded recovery

Show only current non-passing, relevant conditions by default, with a concise
Not configured, Ready, Recovering, Status unavailable or Needs attention summary.
Ready requires fresh passing local blockers; unknown blockers cannot pass.
Automatic work, unknown observations and
user-action blockers should be visually distinguishable. **Show all checks**
retains the complete evidence, including successful checks. Do not show an
unknown peer-readiness note as a local failure.

Move detailed CARP instances, desired/actual attachment, controller process,
carrier/LAGG flags including receive mode, DHCP/gateway observations, pfsync,
native HA context, removal readiness and snapshot download here. Expose the
existing guarded prepare/reconcile operations as recovery tools rather than
normal setup requirements.

Automatic repair is limited to verified plugin ownership and existing operator
intent. It must recheck role, ownership and configuration at transition
boundaries. It must not alter CARP policy, choose another carrier/MAC, clear
unrelated addresses/VIPs, modify firewall policy, override read-only access or
operate on a foreign device. Repeated failures follow the existing controller
cadence, not a new browser retry loop. Read-only requests never trigger repair.

Preserve fail-closed attachment behavior. If fencing itself cannot be verified,
say so explicitly; do not claim the interface was safely disconnected.

### 5. Native configuration synchronization

Offer **Include this plugin in configuration sync** only when this node already
has a configured native XMLRPC destination. Determine sender configuration from
native settings, not its current CARP MASTER/BACKUP role.

The explicit control updates only this plugin's membership in the native sync
selection, preserving every other selection. Use a supported native model/API
and its permission, validation and concurrent-update rules. Do not add a second
stored plugin setting, alter credentials/destination, or submit a stale whole
selection list that can erase another administrator's changes.

Use an idempotent POST `settings/enable_sync` with no client-supplied selection
list. Check native HA write permission as well as plugin permission; reload the
native Hasync model under its configuration lock, recheck the destination, union
`dhcp-interface-ha` into the latest selection, validate and save only if changed.
Return `result`, `changed`, `selected` and `error`, then refresh sync context
without discarding unsaved plugin form fields. Already selected is successful
with `changed=false`. Use `result=saved` on verified inclusion, `failed` on known
failure or `conflict` for stale intent; `selected` is true/false/null for observed
membership or unavailable readback. Missing destination or permissions performs
no write. If save/readback times out, report unknown outcome and reread before
retry; `changed=null` represents an unknown save outcome, not `false`.
Show “Included in configuration sync” once selected; removal remains on the
native HA page. No plugin-owned disable toggle is added.

This action enables inclusion in future native synchronization. It does not
initiate a push, restart the peer, or promise plugin-only synchronization. The
main plugin save must not enable it silently.

Without an outbound destination, show neutral guidance to configure sync on
the sender. Do not label the node a verified receiver or claim that the peer
selected this plugin without evidence. Shared enablement, MAC and the existing
failback value are eligible for sync; interface/carrier mapping remains local.

### 6. Native Log tab and event contract

Use one stable plugin syslog identity and a native local syslog filter. Reuse the
OPNsense log viewer, access control, severity/timestamp handling and normal log
rotation/retention. Log must be discoverable as a peer tab with a way back to
Settings and Diagnostics. If the native viewer uses a separate route, retain
that native navigation instead of building a custom log store or iframe.

Use program identity `dhcp-interface-ha` and stream `dhcpinterfaceha/core`,
registered by the native filter convention. Use a small plugin Log page wrapper
around the existing native log view so Settings/Diagnostics/Log navigation
remains available. Default to **Informational and more severe**, retaining native
user filter preferences. The native default Warning would hide routine events.
Do not copy the log grid or add a custom parser. Render/register the filter using
the normal package/syslog template lifecycle, including upgrades, and use native
retention. Keep the wrapper's service controls empty so Log cannot implicitly
restart the controller.

If native local logging is disabled, explain that no local stream is being
stored and link to its native setting; do not change global logging policy.
An enabled but empty stream says “No events recorded.” Unavailable log access
is an error with Retry, not an empty history. Limit each emitted entry to one
line and 1,024 characters after formatting; truncate/sanitize exception context
without losing the event code and safety outcome.

Grant read/search/export/live access only to this stream through the native
permission mechanism. Native log search uses POST but is observational. Do not
grant a wildcard that also authorizes `/clear`, other streams or plugin writes
to a log-only user. Log clearing is not required in this increment; omit/deny it
for that role, including direct API requests. Verify route ACL mapping on the
target because the viewer and API authorization paths differ across releases.

Emit plain, readable messages with stable event codes and small relevant
context: operation, logical interface/carrier, reason and verified outcome.
Host, timestamp and severity come from native logging. Codes and structured
observations carry semantics; English wording is not a machine API. Bound and
escape untrusted text. Never include credentials, full configuration, raw
request bodies or packet payloads. Keep detailed DHCP protocol output in native
DHCP logs, linked from diagnostics when supported.

| Event | Default severity and trigger |
|---|---|
| Service started/stopped | Informational; one actual lifecycle event, not a status probe |
| Setup/configuration saved or applied | Informational; distinguish each completed boundary and keep partial failure visible |
| Active/standby/maintenance/disabled transition | Informational with old/new state and observed reason; normal standby is not a warning |
| Carrier attached/detached | Informational on verified change; combine related attachment facts within the operation |
| Automatic drift repair completed | Informational with the condition and repair result |
| Operation failed or attachment unverified | Error with known outcome, ownership/safety context and relevant reason code |
| Recovery after an error | Informational; identify the resolved condition |
| IPv4 address observed/acquired or lost | Informational or warning according to current role; log observations, not an invented DHCP exchange |

Events originate from the boundary that knows the outcome, never a browser
success callback. Use these producer responsibilities and stable codes:

| Producer | Events |
|---|---|
| Settings/setup/sync API | `settings_saved`, `settings_save_failed`, `setup_completed`, `setup_failed`, `sync_selection_changed`, `sync_selection_failed`; partial/unknown outcomes are included. Log operational failures, not each routine form validation error. No-op saves do not claim a change. |
| Root operation, regardless of daemon/apply/boot/CARP entry path | `device_prepared`, `attachment_changed`, `repair_completed`, `operation_failed`, `operation_recovered`; only verified changes or actual failed attempts. Log an exception once at the owning boundary, not again in each caller. |
| Long-lived daemon observation loop | `service_started`, `service_stopped`, `state_observed`, `state_changed`, `ipv4_changed`; stop is logged only on a shutdown path actually executed, not guaranteed after SIGKILL. |

Short-lived action processes and the daemon do not share in-memory suppression.
An action's verified `attachment_changed` and the daemon's later `state_changed`
are distinct facts and may both appear. Do not claim exactly-once delivery across
processes or add a shared ledger to achieve it. Within one reconcile operation,
combine related attachment facts in one event and suppress redundant wrapper
messages. Status/health polling and viewing logs emit no mutation events.

Use the existing reconciliation loop for observed transitions and compact
in-memory duplicate suppression; do not create a second polling service or a
persistent event database. In the long-lived process, repeated identical failures log once,
then a bounded reminder no more than once per minute if still failing, followed
by a recovery entry. Changed reasons and critical loss of fencing must remain
visible. Restart may reset suppression and emits a fresh startup/state snapshot;
do not fabricate a transition from a state the new process did not observe.

Key suppression by stable condition/operation, affected interface and safety
outcome, not timestamps or raw exception text. Use a monotonic clock for the
60-second reminders. Each explicit short-lived action may record its own failed
attempt; no global throttle is promised. Successful polling does not emit a
recovery until evidence verifies the previously failing condition. Observation
failure preserves the last known address; it does not emit an address-loss event.
Use warning for a verified address loss while still active, informational for
expected removal on demotion/disable, and a fresh snapshot after daemon restart.

Report DHCP protocol failure only with native evidence. If only the interface
address is observed, use “IPv4 address observed” rather than “DHCP ACK received.”
Normal standby address removal is not a failed lease acquisition. Logging
failure must not prevent fencing or turn a failed operation into success.

### 7. Scope and compatibility of code changes

Build on the existing controller, combined Settings transaction, observation
API, guarded service actions and native UI. The server-side Configure action
must reuse native assignment behavior and preserve native assignment
permissions. Do not bypass native access controls by granting assignment power
to every user who can edit plugin settings. Kernel operations remain at the
existing root/configd boundary.

Preserve working 0.2_8 controller behavior, shared/local mounts, request
revision checks, owned-device identity and bounded backend calls. No persisted
schema change is expected. Add fields to existing status responses compatibly;
retain existing structured state/reason codes or explicitly map their successors.
No change to the signed repository, upstream core, release architecture or peer
protocol is part of this work.

## Testing decisions

Test observable behavior and safety boundaries, not Markdown wording, CSS
classes or one assertion per helper. Prefer existing seams: execute the actual
controller with the command boundary simulated, actual PHP controllers with
native boundaries stubbed, and actual UI handlers with mocked API outcomes.

Required behaviors include initial carrier capture and setup, completed setup
without a carrier selector, unknown/partial results and safe retry, rejection
of observed unrelated native edits and preservation of other configuration
under the documented native apply limitation, observation ownership
and resolution, automatic repair with no manual prerequisite, read-only access,
sender-only sync selection preserving concurrent edits, and state/event logging
without steady-state flooding. Extend existing scenarios where they already
cover the invariant; do not create duplicate suites for label changes.

Exercise syslog emission through an injected/standard-library mocked boundary
while running the real operation paths. Native integration must separately
prove that the emitted entries reach the correct log stream and viewer with
the intended permissions and rotation conventions. A fake syslog sink cannot
prove appliance registration works.

Authenticated browser acceptance on the supported OPNsense target is required
for layout, tab navigation, native log filtering, permission behavior, stale
observations, save/apply outcomes and the full configure action. Compilation,
source checksums and a 200/302 response are not browser acceptance. Live
handover and boot checks require a suitable test connection and explicit
authorization; do not perform them merely to finish this documentation task.

## Out of scope

- A new CARP election, peer API handoff, witness, lease replication or remote log
  aggregation.
- New supported carrier types, IPv6/PPPoE support, delayed failback or
  internet-reachability-based promotion.
- Automatic edits to CARP policy, firewall/NAT/gateways, hypervisor settings or
  an upstream DHCP server.
- A custom log backend, generic recovery engine, new UI framework or persistent
  diagnostic workflow state.
- Automatic peer installation, package publication or changes to the signed
  repository contract.

## Further notes

The user approved the direction: automated plugin-owned setup, guarded
recovery, quieter presentation, native logging, and code contracts interpretable
by future agents. There are no blocking product questions for this increment.
Verify native API/viewer capabilities during implementation and record actual
limitations; do not silently weaken the contract to accommodate a failing test.

Baseline evidence is experimental 0.2_8 on both appliances, with 72 controller
and API tests plus the existing UI setup-flow checks. HA-2 acquired an address
after the receive-filter correction; this does not qualify every routing,
firewall, reboot, failback or session-continuity scenario.
