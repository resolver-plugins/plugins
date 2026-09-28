<script>
$(document).ready(function() {
    const api = "/api/dhcpinterfaceha";
    const canWriteSettings = {{ canWriteSettings ? 'true' : 'false' }};
    const canConfigureInterface = {{ canConfigureInterface ? 'true' : 'false' }};
    const canRunRecovery = {{ canRunRecovery ? 'true' : 'false' }};
    const canGenerateMac = {{ canGenerateMac ? 'true' : 'false' }};
    const configureAllowedInterfaces = $("#configureAllowedInterfaces [data-interface]").map(function() {
        return $(this).data("interface");
    }).get();
    let selectionGeneration = 0;
    let formGeneration = 0;
    let statusGeneration = 0;
    let statusTimer = null;
    let staleTimer = null;
    let statusInFlight = false;
    let statusRequestPromise = null;
    let statusRefreshPending = false;
    let statusData = null;
    let statusObservedAt = 0;
    let managedDescription = "";
    let localCarrier = "";
    let previewDevice = "";
    let savedEnabled = false;
    let savedMapping = {managed: "", carrier: ""};
    let configureBusy = false;
    let configureRetryReady = false;
    let configureRetryGeneration = -1;
    let configureOutcomeBlocked = false;
    let pendingConfigureOutcome = null;
    let settingsBusy = false;
    let populatingSettings = false;

    function field(id) {
        return $('[id="' + id + '"]');
    }

    const interfaceCell = field("dhcphalocal.managed_interface").closest("td");
    if (interfaceCell.length) {
        interfaceCell.append($("#configureBlock").detach());
    }
    const macCell = field("dhcphashared.shared_mac").closest("td");
    if (macCell.length) {
        macCell.append($("#macHelpers").detach());
    }

    function usableMac(value) {
        return /^(?:[0-9a-f]{2}:){5}[0-9a-f]{2}$/i.test(value)
            && value !== "00:00:00:00:00:00"
            && !(parseInt(value.slice(0, 2), 16) & 1);
    }

    function selectedOption(options) {
        if (!options || typeof options !== "object" || Array.isArray(options)) {
            return "";
        }
        for (const key of Object.keys(options)) {
            if (options[key] && typeof options[key] === "object" && String(options[key].selected) === "1") {
                return key;
            }
        }
        return "";
    }

    function valueOf(value) {
        if (value && typeof value === "object" && Object.prototype.hasOwnProperty.call(value, "value")) {
            return String(value.value);
        }
        return String(value == null ? "" : value);
    }

    function getJson(url, timeout) {
        return $.ajax({type: "GET", url: url, dataType: "json", timeout: timeout || 15000});
    }

    function post(url, data, timeout) {
        return $.ajax({
            type: "POST", url: url, data: JSON.stringify(data || {}),
            dataType: "json", contentType: "application/json", timeout: timeout || 30000
        });
    }

    function currentTabVisible() {
        return document.visibilityState !== "hidden"
            && ($("#settingsTab").hasClass("active") || $("#diagnosticsTab").hasClass("active"));
    }

    function invalidateStatus() {
        statusGeneration++;
        if (statusInFlight) {
            statusRefreshPending = true;
        }
    }

    function verifiedDetached(data) {
        const attachment = data && data.attachment;
        const device = attachment && attachment.device;
        return !!attachment
            && ["FENCED", "UNMANAGED"].includes(attachment.actual)
            && Array.isArray(attachment.members)
            && attachment.members.length === 0
            && (!device || device.exists === false || attachment.owned === true);
    }

    function statusIsFresh() {
        return statusData !== null && statusObservedAt > 0 && Date.now() - statusObservedAt < 15000;
    }

    function isConfiguredForForm() {
        if (!statusIsFresh()) {
            return false;
        }
        const managed = field("dhcphalocal.managed_interface").val() || "";
        const assignment = (statusData.readiness || []).find(function(item) {
            return item.code === "managed_assignment";
        });
        const ownership = (statusData.readiness || []).find(function(item) {
            return item.code === "device_ownership";
        });
        const topology = (statusData.readiness || []).find(function(item) {
            return item.code === "device_topology";
        });
        return managed !== ""
            && managed === savedMapping.managed
            && statusData.managed && statusData.managed.identifier === managed
            && statusData.managed.device === "dhcpha0lagg"
            && savedMapping.carrier !== ""
            && statusData.attachment && statusData.attachment.owned === true
            && statusData.attachment.device && statusData.attachment.device.exists === true
            && statusData.attachment.device.protocol === "failover"
            && ownership && ownership.status === "pass"
            && topology && topology.status === "pass"
            && statusData.attachment.carrier
            && statusData.attachment.carrier.selected === savedMapping.carrier
            && assignment && assignment.status === "pass";
    }

    function safelyRetryableSetup(status, managed, carrier) {
        const pending = status && status.setup && status.setup.pending_assignment;
        if (!statusIsFresh() || !status || status.result === "unavailable" || !carrier || !verifiedDetached(status)
            || !status.managed || status.managed.identifier !== managed
            || ![carrier, "dhcpha0lagg"].includes(status.managed.device)
            || !pending || pending.available !== true
            || !["clear", "selected_relink"].includes(pending.state)) {
            return false;
        }
        const attachment = status.attachment || {};
        const device = attachment.device || {};
        return device.exists === false || (device.exists === true
            && attachment.owned === true && device.protocol === "failover");
    }

    function mappedDeviceNeedsRecovery(managed) {
        return statusIsFresh() && managed === savedMapping.managed && savedMapping.carrier !== ""
            && statusData.managed && statusData.managed.identifier === managed
            && statusData.managed.device === "dhcpha0lagg"
            && statusData.attachment && statusData.attachment.device
            && statusData.attachment.device.exists === false;
    }

    function pendingAssignmentAllowsConfigure(retryReady) {
        const pending = statusData && statusData.setup && statusData.setup.pending_assignment;
        return !!pending && pending.available === true
            && (pending.state === "clear" || (retryReady && pending.state === "selected_relink"));
    }

    function loadCarrierPreview(interfaceId, savedCarrier) {
        const request = ++selectionGeneration;
        invalidateStatus();
        localCarrier = savedCarrier || "";
        previewDevice = "";
        if (!interfaceId) {
            managedDescription = "";
            updateActions();
            return;
        }
        getJson(api + "/status/carriers?interface=" + encodeURIComponent(interfaceId), 15000)
            .done(function(data) {
                if (request !== selectionGeneration || data.interface !== interfaceId) {
                    return;
                }
                const managed = data.managed || {};
                previewDevice = managed.current_device || "";
                managedDescription = managed.description || interfaceId;
                $("#carrierLoadError").toggle(!!(data.errors && Object.keys(data.errors).length))
                    .text("Carrier inventory has incomplete observations. Refresh status before Save & Apply.");
                updateActions();
            })
            .fail(function() {
                if (request !== selectionGeneration) {
                    return;
                }
                previewDevice = "";
                $("#carrierLoadError").text("Interface assignment inventory is unavailable. The saved selection and MAC field are unchanged.").show();
                updateActions();
            });
    }

    function updateAddressDetail(data) {
        const connection = data.connection || {};
        const ipv4 = connection.ipv4 || {};
        const address = ipv4.address;
        const state = data.controller && data.controller.state;
        if (address && address.address) {
            return address.address + (address.prefix == null ? "" : "/" + address.prefix);
        }
        if (state === "ACTIVE" && ipv4.available === true) {
            return "Waiting for DHCP";
        }
        if (ipv4.available === false) {
            return "IPv4 address unavailable";
        }
        if (verifiedDetached(data)) {
            return "Not expected while the interface is disconnected";
        }
        return "IPv4 address unavailable";
    }

    function setSummary(state, cssClass) {
        $("#summaryState").text(state).removeClass().addClass("label " + cssClass);
    }

    function renderSummary(data) {
        const summary = data.summary || {};
        const controller = data.controller || {};
        const carp = data.carp || {};
        const actual = data.attachment && data.attachment.actual;
        const detached = verifiedDetached(data);
        const role = carp.role || "role unavailable";
        const address = updateAddressDetail(data);
        const reasonCode = controller.reason_code || "";
        const attachmentUnsafe = actual === "UNVERIFIED" || reasonCode === "attachment_unverified"
            || (actual === "ATTACHED" && (!data.attachment || data.attachment.owned !== true
                || controller.state === "DISABLED"
                || ["attachment_while_disabled_or_unmanaged", "attachment_when_carp_ineligible"].includes(reasonCode)));
        const managed = data.managed && data.managed.identifier;
        if (summary.state === "not_configured" && !managed) {
            setSummary("Not configured", "label-default");
        } else if (summary.state === "status_unavailable" || data.result === "unavailable") {
            setSummary("Status unavailable", "label-default");
        } else if (summary.state === "not_configured") {
            setSummary("Not configured", "label-default");
        } else if (attachmentUnsafe) {
            setSummary("Needs attention", "label-danger");
        } else if (controller.state === "DISABLED" && detached) {
            setSummary(summary.state === "ready" && data.managed && data.managed.device === "dhcpha0lagg"
                ? "Ready to enable · Disabled" : "Disabled · Interface disconnected",
                "label-default");
        } else if (carp.maintenance === true && detached) {
            setSummary("Maintenance · Interface disconnected", "label-warning");
        } else if ((controller.state === "STANDBY" || role === "BACKUP") && detached) {
            setSummary("Standby · Interface intentionally disconnected", "label-warning");
        } else if (controller.state === "ACTIVE" && role === "MASTER" && actual === "ATTACHED"
            && data.attachment && data.attachment.owned === true) {
            setSummary("Active · CARP MASTER · " + address, "label-success");
        } else if (summary.state === "needs_attention") {
            setSummary("Needs attention", "label-danger");
        } else if (summary.state === "ready") {
            setSummary("Unable to proceed", "label-warning");
        } else {
            setSummary("Status unavailable", "label-default");
        }
        clearTimeout(staleTimer);
        const remainingFreshMs = Math.max(0, 15000 - (Date.now() - statusObservedAt));
        if (remainingFreshMs === 0) {
            markStatusStale();
        } else {
            staleTimer = setTimeout(markStatusStale, remainingFreshMs);
        }
    }

    function renderStatusUnavailable(message) {
        statusData = null;
        statusObservedAt = 0;
        clearTimeout(staleTimer);
        setSummary("Status unavailable", "label-default");
        $("#detailRole, #detailAttachment, #detailAddress, #detailController, "
            + "#detailCarrier, #detailDevice, #detailDhcp, #detailGateway, #detailHA")
            .text("Unavailable");
        updateActions();
    }

    function renderStatus(data) {
        if (!data || data.result === "unavailable") {
            renderStatusUnavailable((data && data.errors && data.errors.required) || "Required status data is unavailable. The state is unknown.");
            return;
        }
        statusData = data;
        renderSummary(data);
        const attachment = data.attachment || {};
        const device = attachment.device || {};
        const carrier = attachment.carrier || {};
        const carp = data.carp || {};
        const connection = data.connection || {};
        const gateway = connection.gateway || {};
        const ha = data.ha || {};
        const pfsync = ha.pfsync || {};
        const xmlrpc = ha.xmlrpc || {};
        $("#detailRole").text((carp.role || "Unknown") + " · maintenance " + String(carp.maintenance));
        $("#detailAttachment").text((attachment.actual || "Unknown") + " · desired " + (attachment.desired || "Unknown")
            + " · ownership " + String(attachment.owned));
        $("#detailAddress").text(updateAddressDetail(data));
        $("#detailController").text(controllerText(data.controller));
        $("#detailCarrier").text((carrier.selected || "Not configured") + " · link "
            + (carrier.link_up === true ? "up" : (carrier.link_up === false ? "down" : "unknown"))
            + " · MAC " + (carrier.mac || "unknown") + " · receive " + JSON.stringify(attachment.receive_mode || "unknown"));
        $("#detailDevice").text((device.name || "dhcpha0lagg") + " · " + (device.protocol || "unknown")
            + " · members " + (Array.isArray(attachment.members) ? attachment.members.join(", ") : "unknown")
            + " · promiscuous " + String(device.promiscuous));
        $("#detailDhcp").text((connection.dhcp && connection.dhcp.available)
            ? (connection.dhcp.state || "Available")
            : ((connection.dhcp && connection.dhcp.reason) || "Native DHCP lease details unavailable."));
        let gatewayText = gateway.name || "Not configured";
        if (gateway.name) {
            gatewayText += " · " + (gateway.address || "address unavailable") + " · " + (gateway.status || "status unknown");
        }
        $("#detailGateway").text(gatewayText);
        $("#detailHA").text("pfsync: " + (pfsync.configured_interface || "not configured")
            + " · peer " + (pfsync.configured_peer || "not configured")
            + " · XMLRPC destination " + (xmlrpc.sender_configured ? "configured" : "not configured")
            + " · this plugin " + (xmlrpc.plugin_settings_sync === true ? "included" : (xmlrpc.plugin_settings_sync === false ? "not included" : "unknown"))
            + " · peer readiness unverified");
        updateActions();
    }

    function controllerText(controller) {
        controller = controller || {};
        return (controller.running === null ? "Unknown" : (controller.running ? "Running" : "Stopped"))
            + " · " + (controller.state || "Unknown") + " · " + (controller.reason || "reason unavailable");
    }

    function markStatusStale() {
        if (!statusData || statusObservedAt === 0) {
            return;
        }
        const age = Math.max(0, Math.floor((Date.now() - statusObservedAt) / 1000));
        if (age < 15) {
            staleTimer = setTimeout(markStatusStale, Math.max(1000, 15000 - (Date.now() - statusObservedAt)));
            return;
        }
        const current = $("#summaryState").text().replace(/ · STALE$/, "");
        $("#summaryState").text(current + " · STALE").removeClass().addClass("label label-warning");
        updateActions();
    }

    function detachedEvidenceFresh() {
        return statusIsFresh() && verifiedDetached(statusData);
    }

    function updateActions() {
        const idle = !configureBusy && !settingsBusy;
        const managed = field("dhcphalocal.managed_interface").val() || "";
        const disabledSelection = managed === "";
        if (disabledSelection) {
            field("dhcphashared.enabled").prop("checked", false);
        }
        field("dhcphashared.enabled").prop("disabled", disabledSelection || !canWriteSettings || configureBusy || settingsBusy || configureOutcomeBlocked);
        const configured = isConfiguredForForm();
        const configureAllowed = canConfigureInterface && configureAllowedInterfaces.includes(managed);
        const retryReady = configureRetryReady && configureRetryGeneration === formGeneration && managed !== "";
        const pending = statusData && statusData.setup && statusData.setup.pending_assignment;
        const needsReadback = configureOutcomeBlocked || (!savedEnabled && !retryReady && statusIsFresh()
            && pending && pending.available === true && pending.state === "selected_relink");
        $("#configureBlock").toggle(idle && ((managed !== "" && !configured) || needsReadback));
        $("#recheckConfigure").toggle(idle && needsReadback)
            .prop("disabled", configureBusy || settingsBusy);
        $("#configureOutcomeNote").toggle(idle && needsReadback);
        $("#configurePermissionNote").toggle(idle && managed !== "" && !configured && (!canConfigureInterface || !configureAllowed))
            .text(canConfigureInterface
                ? "Your account needs native assignment write access for this interface."
                : "Your account needs native interface assignment and apply permissions to configure this interface.");
        $("#configureSavedStateNote").toggle(idle && managed !== "" && !configured && savedEnabled)
            .text("The saved plugin is still enabled. Save Disable and verify detachment before changing the assignment.");
        $("#configureEvidenceNote").toggle(idle && managed !== "" && !configured && !savedEnabled && !detachedEvidenceFresh())
            .text("Save & Apply requires fresh status confirming the disabled, detached adapter before changing the assignment.");
        $("#configureCarrierNote").toggle(idle && managed !== "" && previewDevice === "dhcpha0lagg" && !savedMapping.carrier)
            .text("This assignment already uses dhcpha0lagg, but its original carrier is unknown. Save is blocked; do not guess.");
        const identityChangeBlocked = savedEnabled && !disabledSelection && managed !== savedMapping.managed;
        $("#saveSettings").show()
            .prop("disabled", !canWriteSettings || configureBusy || settingsBusy || configureOutcomeBlocked || identityChangeBlocked);
        $("#saveIdentityNote").toggle(identityChangeBlocked)
            .text("Save Disable with the current interface first, then wait for fresh detached status before changing identity.");
        $("#retryApply").prop("disabled", !canRunRecovery || configureBusy || settingsBusy || configureOutcomeBlocked);
        $("#generateMac").prop("disabled", !canGenerateMac || configureBusy || settingsBusy || configureOutcomeBlocked);
        if (!canWriteSettings) {
            $("#frm_Settings :input").prop("disabled", true);
        }
    }

    function requestStatusOnce() {
        if (statusRequestPromise) {
            return statusRequestPromise;
        }
        statusInFlight = true;
        const generation = statusGeneration;
        const selectedGeneration = selectionGeneration;
        const startedAt = Date.now();
        const request = getJson(api + "/status/environment", 20000);
        statusRequestPromise = new Promise(function(resolve, reject) {
            request
            .done(function(data) {
                if (generation !== statusGeneration || selectedGeneration !== selectionGeneration) {
                    statusRefreshPending = true;
                    resolve(null);
                    return;
                }
                statusObservedAt = startedAt;
                renderStatus(data);
                resolve(data);
            })
            .fail(function() {
                if (generation !== statusGeneration || selectedGeneration !== selectionGeneration) {
                    statusRefreshPending = true;
                    reject(new Error("Status response became stale."));
                    return;
                }
                renderStatusUnavailable("Status request failed. Current required observations are unknown; refresh before relying on them.");
                reject(new Error("Status request failed."));
            })
            .always(function() {
                statusInFlight = false;
                statusRequestPromise = null;
                if (statusRefreshPending) {
                    statusRefreshPending = false;
                    refreshStatus();
                    return;
                }
                clearTimeout(statusTimer);
                if (currentTabVisible()) {
                    statusTimer = setTimeout(refreshStatus, 5000);
                }
            });
        });
        return statusRequestPromise;
    }

    async function requestFreshStatusForReadback() {
        try {
            const data = await requestStatusOnce();
            if (data !== null) {
                return data;
            }
        } catch (error) {
            if (!statusRequestPromise) {
                throw error;
            }
        }
        if (statusRequestPromise) {
            return await statusRequestPromise;
        }
        return await requestStatusOnce();
    }

    function refreshStatus() {
        if (!currentTabVisible()) {
            return;
        }
        if (statusInFlight) {
            statusRefreshPending = true;
            return;
        }
        requestStatusOnce().catch(function() {});
    }

    function loadSettings() {
        getJson(api + "/settings/get", 10000)
            .done(function(data) {
                if (!data.dhcphashared || !data.dhcphalocal) {
                    $("#settingsResult").text("Settings could not be loaded. Refresh the page or check system logs.");
                    return;
                }
                const managed = selectedOption(data.dhcphalocal.managed_interface) || data.managed_interface_value || "";
                const savedCarrier = valueOf(data.dhcphalocal.carrier);
                managedDescription = (data.managed_choices && data.managed_choices[managed] && data.managed_choices[managed].description) || managed;
                savedMapping = {managed: managed, carrier: savedCarrier};
                savedEnabled = valueOf(data.dhcphashared.enabled) === "1";
                // Native setFormData emits change for each field. Wait until
                // the interface is populated before enforcing Disabled rules.
                populatingSettings = true;
                try {
                    setFormData("frm_Settings", data);
                } finally {
                    populatingSettings = false;
                }
                $("#revision").val(data.revision || "");
                $("#failbackDelay").val(valueOf(data.dhcphashared.failback_delay) || "0");
                $("#storedFailbackValue").text($("#failbackDelay").val());
                $("#failbackWarning").toggle(Number($("#failbackDelay").val()) !== 0);
                field("dhcphalocal.managed_interface").selectpicker("refresh");
                loadCarrierPreview(managed, savedCarrier);
                updateActions();
                refreshStatus();
            })
            .fail(function() {
                $("#settingsResult").text("Settings could not be loaded. Check that the UI service and plugin API are available.");
                renderStatusUnavailable("Settings are unavailable; live saved configuration cannot be summarized.");
            });
    }

    function saveSettings() {
        if (!canWriteSettings || settingsBusy || configureBusy || configureOutcomeBlocked) {
            return;
        }
        const managed = field("dhcphalocal.managed_interface").val() || "";
        if (savedEnabled && managed !== "" && managed !== savedMapping.managed) {
            $("#settingsResult").text("Save Disable with the current interface before changing the local identity.");
            return;
        }
        const button = $("#saveSettings");
        settingsBusy = true;
        button.prop("disabled", true);
        const payload = getFormData("frm_Settings");
        payload.dhcphalocal.carrier = managed === savedMapping.managed ? localCarrier : "";
        payload.revision = $("#revision").val();
        payload.dhcphashared.failback_delay = $("#failbackDelay").val();
        if (managed === "") {
            payload.dhcphashared.enabled = "0";
            payload.dhcphalocal.carrier = "";
        }
        $("#settingsResult, #setupResult").text("");
        updateActions();
        $("#retryApply").hide();
        invalidateStatus();
        post(api + "/settings/set", payload, managed === "" ? 70000 : 35000)
            .done(function(data) {
                handleFormValidation("frm_Settings", data.validations);
                if (data.result === "unchanged") {
                    refreshStatus();
                } else if (data.result === "staged" && data.saved === true) {
                    $("#revision").val(data.revision || $("#revision").val());
                    $("#settingsResult").text(data.error || "HA DHCP Interface was disabled, but its interface selection was retained. Refresh status and save Disabled again.");
                    loadSettings();
                } else if (data.result === "saved" && data.saved === true) {
                    savedMapping = {managed: payload.dhcphalocal.managed_interface || "", carrier: payload.dhcphalocal.carrier || ""};
                    savedEnabled = valueOf(payload.dhcphashared.enabled) === "1";
                    $("#revision").val(data.revision || $("#revision").val());
                    const status = data.status || {};
                    $("#settingsResult").text(data.applied === true
                        ? ""
                        : (data.error || "Settings saved; controller apply needs attention."));
                    $("#retryApply").toggle(data.applied !== true);
                    configureRetryReady = false;
                    if (savedMapping.managed === "") {
                        loadCarrierPreview("", "");
                    }
                    refreshStatus();
                } else {
                    $("#settingsResult").text(data.error || (data.result === "conflict"
                        ? "Settings changed elsewhere. Reload before saving."
                        : "Settings were not saved."));
                    refreshStatus();
                }
            })
            .fail(function() {
                $("#settingsResult").text("Save result is unknown. Your form values are preserved; checking the saved revision before retrying.");
                getJson(api + "/settings/get", 10000).done(function(saved) {
                    if (saved.revision && saved.revision !== payload.revision) {
                        $("#settingsResult").text("The saved revision changed while the request timed out. Your form still contains the submitted values; reload and compare before saving again.");
                    } else {
                        $("#settingsResult").text("The saved revision could not be confirmed. Your form values are preserved; reload settings before retrying.");
                    }
                }).fail(function() {
                    $("#settingsResult").text("Save result is unknown and the saved revision could not be read. Do not resubmit until settings can be checked.");
                });
            })
            .always(function() {
                settingsBusy = false;
                button.prop("disabled", false);
                updateActions();
            });
    }

    function sameMac(left, right) {
        return valueOf(left).toLowerCase() === valueOf(right).toLowerCase();
    }

    function blockConfigureOutcome(managed, payload, formGenerationAtSubmit, expectedCarrier, backendVerified) {
        configureOutcomeBlocked = true;
        pendingConfigureOutcome = {
            managed: managed,
            payload: payload,
            formGeneration: formGenerationAtSubmit,
            expectedCarrier: expectedCarrier,
            backendVerified: backendVerified === true
        };
    }

    async function readConfigureOutcome(managed, payload, expectedFormGeneration, expectedCarrier, backendVerified) {
        $("#setupResult").text("");
        configureRetryReady = false;
        configureRetryGeneration = -1;
        try {
            const [settings, status] = await Promise.all([
                getJson(api + "/settings/get", 15000),
                requestFreshStatusForReadback()
            ]);
            const savedManaged = selectedOption(settings.dhcphalocal && settings.dhcphalocal.managed_interface)
                || settings.managed_interface_value || "";
            const savedCarrier = valueOf(settings.dhcphalocal && settings.dhcphalocal.carrier);
            const savedEnabledValue = valueOf(settings.dhcphashared && settings.dhcphashared.enabled);
            if (expectedFormGeneration !== formGeneration) {
                $("#setupResult").text("Readback completed after the form changed, so the saved revision was not advanced. Recheck after reviewing the saved settings, or reload this page to discard the unsaved values and load current settings.");
                return;
            }
            const settingsMatch = !!settings.revision && savedManaged === managed
                && savedCarrier === expectedCarrier
                && (savedEnabledValue === "0" || savedEnabledValue === valueOf(payload.dhcphashared.enabled))
                && (valueOf(payload.dhcphashared.shared_mac) === ""
                    ? usableMac(valueOf(settings.dhcphashared && settings.dhcphashared.shared_mac))
                    : sameMac(settings.dhcphashared && settings.dhcphashared.shared_mac, payload.dhcphashared.shared_mac))
                && valueOf(settings.dhcphashared && settings.dhcphashared.failback_delay) === valueOf(payload.dhcphashared.failback_delay);
            if (settingsMatch) {
                $("#revision").val(settings.revision);
                savedMapping = {managed: savedManaged, carrier: savedCarrier};
                savedEnabled = savedEnabledValue === "1";
                if ((field("dhcphalocal.managed_interface").val() || "") === managed) {
                    localCarrier = savedMapping.carrier;
                    field("dhcphashared.enabled").prop("checked", savedEnabled);
                    field("dhcphashared.shared_mac").val(valueOf(settings.dhcphashared.shared_mac));
                }
            }
            const attachment = status.attachment || {};
            const pending = status.setup && status.setup.pending_assignment;
            const mappingVerified = status.managed && status.managed.identifier === managed
                && status.managed.device === "dhcpha0lagg"
                && savedCarrier !== ""
                && attachment.carrier && attachment.carrier.selected === savedCarrier
                && attachment.device && attachment.device.exists === true
                && attachment.device.protocol === "failover"
                && attachment.owned === true && (savedEnabledValue === "1" || verifiedDetached(status))
                && statusIsFresh()
                && pending && pending.available === true && pending.state === "clear";
            if (settingsMatch) {
                previewDevice = (status.managed && status.managed.device) || "";
                managedDescription = (status.managed && status.managed.description) || managedDescription;
            }
            if (mappingVerified && settingsMatch) {
                configureRetryReady = false;
                configureOutcomeBlocked = false;
                pendingConfigureOutcome = null;
                $("#setupResult").text(savedEnabledValue === valueOf(payload.dhcphashared.enabled)
                    ? "" : "Interface configured, but Enable was not applied. Review the marked settings and save again.");
            } else if (settingsMatch && statusIsFresh() && status.result !== "unavailable"
                && status.managed && status.managed.identifier === managed) {
                configureRetryReady = !backendVerified && safelyRetryableSetup(status, managed, savedCarrier);
                configureRetryGeneration = formGeneration;
                if (configureRetryReady) {
                    configureOutcomeBlocked = false;
                    pendingConfigureOutcome = null;
                }
                $("#setupResult").text(backendVerified
                    ? "Save & Apply reported verified setup, but current readback does not confirm the full mapping. Current status needs attention; refresh Diagnostics before enabling."
                    : (configureRetryReady
                        ? "Fresh readback confirms a safe disabled setup state. Review the result, then retry Save & Apply to resume guarded setup."
                        : "Fresh readback found a conflicting or unverified device state. Save retry is blocked; inspect Diagnostics before continuing."));
            } else {
                $("#setupResult").text(backendVerified
                    ? "Save & Apply reported success, but readback differs or is unavailable. Refresh Diagnostics before relying on the current state."
                    : "Readback could not confirm a safe retry. Saved settings, assignment or runtime state differs or is unavailable; inspect Diagnostics before continuing.");
            }
        } catch (error) {
            $("#setupResult").text(backendVerified
                ? "Save & Apply verified setup, but current saved settings and assignment could not be read. Use Recheck outcome or reload this page after copying any unsaved values."
                : "Save result remains unknown because saved settings and assignment status could not both be read. Use Recheck outcome; if it remains blocked, reload this page after copying any unsaved values.");
        }
        updateActions();
        refreshStatus();
    }

    async function recheckConfigureOutcome() {
        if (configureBusy || settingsBusy) {
            return;
        }
        if (!pendingConfigureOutcome) {
            // A page reload loses request history, but the native pending queue
            // can still identify a saved setup intent. Verify it before retry.
            const managed = field("dhcphalocal.managed_interface").val() || "";
            const queue = statusData && statusData.setup && statusData.setup.pending_assignment;
            if (!statusIsFresh() || !queue || queue.available !== true || queue.state !== "selected_relink"
                || savedEnabled || managed !== savedMapping.managed || !savedMapping.carrier) {
                $("#setupResult").text("The saved setup intent is unavailable. Reload settings and review Diagnostics before resuming.");
                return;
            }
            const payload = getFormData("frm_Settings");
            payload.dhcphashared.enabled = "0";
            payload.dhcphashared.failback_delay = $("#failbackDelay").val();
            blockConfigureOutcome(managed, payload, formGeneration, savedMapping.carrier, false);
        }
        const pending = pendingConfigureOutcome;
        configureBusy = true;
        updateActions();
        try {
            await readConfigureOutcome(
                pending.managed,
                pending.payload,
                pending.formGeneration,
                pending.expectedCarrier,
                pending.backendVerified
            );
        } finally {
            configureBusy = false;
            updateActions();
        }
    }

    async function configureSelectedLagg() {
        if (configureBusy || settingsBusy) {
            return;
        }
        if (configureOutcomeBlocked) {
            $("#setupResult").text("Save remains blocked until Recheck outcome confirms the saved settings and current assignment. Reload this page if you need to discard changed form values.");
            return;
        }
        const managed = field("dhcphalocal.managed_interface").val() || "";
        const retryReady = configureRetryReady && configureRetryGeneration === formGeneration;
        if (!managed || savedEnabled) {
            $("#setupResult").text("Save and verify the disabled state before configuring the native assignment.");
            return;
        }
        if (!canConfigureInterface || !configureAllowedInterfaces.includes(managed)) {
            $("#setupResult").text("Native interface assignment write permission is required for this interface.");
            return;
        }
        if (!statusIsFresh() || !verifiedDetached(statusData)) {
            $("#setupResult").text("Fresh status must verify the saved disabled state and detached adapter before configuring.");
            refreshStatus();
            return;
        }
        if (!pendingAssignmentAllowsConfigure(retryReady)) {
            const pending = statusData && statusData.setup && statusData.setup.pending_assignment;
            $("#setupResult").text(!pending || pending.available !== true || pending.state === "unknown"
                ? "The native assignment queue could not be verified. Refresh status and inspect Diagnostics before Save & Apply."
                : (pending.state === "conflict"
                    ? "The native assignment queue contains another pending change. Resolve it in Interface Assignments before Save & Apply."
                    : "A pending relink is present. Recheck the save outcome before resuming setup."));
            return;
        }
        if (!retryReady && !mappedDeviceNeedsRecovery(managed)
            && (!previewDevice || previewDevice === "dhcpha0lagg")) {
            $("#setupResult").text("The original native device is unavailable or ambiguous. Refresh the assignment before continuing.");
            return;
        }

        configureBusy = true;
        configureRetryReady = false;
        configureRetryGeneration = -1;
        const expectedFormGeneration = formGeneration;
        const expectedCarrier = managed === savedMapping.managed && localCarrier
            ? localCarrier : (previewDevice === "dhcpha0lagg" ? savedMapping.carrier : previewDevice);
        const buttons = $("#recheckConfigure, #saveSettings, #retryApply, #generateMac, #resetFailback").prop("disabled", true);
        const inputs = $("#frm_Settings :input");
        const disabledStates = inputs.map(function() { return $(this).prop("disabled"); }).get();
        inputs.prop("disabled", true);
        const payload = getFormData("frm_Settings");
        payload.dhcphalocal.carrier = managed === savedMapping.managed ? localCarrier : "";
        payload.dhcphashared.failback_delay = $("#failbackDelay").val();
        payload.revision = $("#revision").val();
        handleFormValidation("frm_Settings", []);
        $("#settingsResult, #setupResult").text("");
        updateActions();
        invalidateStatus();
        try {
            const result = await post(api + "/settings/configure", payload, 120000);
            handleFormValidation("frm_Settings", result.validations);
            const complete = result.result === "saved" && result.saved === true && result.applied === true
                && result.assignment_verified === true && result.setup_stage === "verified";
            if (complete) {
                $("#setupResult").text("");
                blockConfigureOutcome(managed, payload, expectedFormGeneration, expectedCarrier, true);
                await readConfigureOutcome(managed, payload, expectedFormGeneration, expectedCarrier, true);
                refreshStatus();
            } else {
                $("#setupResult").text(result.error || "Save & Apply could not complete the interface configuration.");
                if (result.saved === true || result.saved === null || result.applied === null || result.assignment_verified === null) {
                    blockConfigureOutcome(managed, payload, expectedFormGeneration, expectedCarrier, false);
                    await readConfigureOutcome(managed, payload, expectedFormGeneration, expectedCarrier);
                    if (result.error && $("#setupResult").text()) {
                        $("#setupResult").text(result.error);
                    }
                } else {
                    refreshStatus();
                }
            }
        } catch (error) {
            blockConfigureOutcome(managed, payload, expectedFormGeneration, expectedCarrier, false);
            await readConfigureOutcome(managed, payload, expectedFormGeneration, expectedCarrier);
        } finally {
            inputs.each(function(index) { $(this).prop("disabled", disabledStates[index]); });
            configureBusy = false;
            buttons.prop("disabled", false);
            updateActions();
        }
    }

    function runRecovery(button, url, successText) {
        if (!canRunRecovery || configureBusy || settingsBusy || configureOutcomeBlocked) {
            return;
        }
        button.prop("disabled", true);
        invalidateStatus();
        post(api + url, {}, 20000).done(function(data) {
            const status = data.status || {};
            const reason = status.reason;
            const applied = data.applied === true || data.prepared === true;
            button.text(applied ? successText : (data.error || "The guarded recovery action did not complete."));
            if (reason && data.applied === true) {
                button.text(successText + " · " + reason);
            }
            refreshStatus();
        }).fail(function() {
            $("#settingsResult").text("Recovery result is unknown. Refresh status before retrying.");
            renderStatusUnavailable("Recovery result is unknown. Refresh status before relying on the previous state.");
        }).always(function() {
            button.prop("disabled", false);
            updateActions();
        });
    }

    function showTab(hash) {
        if (hash === "#status") {
            hash = "#settings";
        }
        $("#maintabs a[href='" + hash + "']").tab("show");
    }

    function saveFromForm() {
        const managed = field("dhcphalocal.managed_interface").val() || "";
        const setupRequired = managed !== "" && !savedEnabled && (
            managed !== savedMapping.managed || !savedMapping.carrier
            || (previewDevice && previewDevice !== "dhcpha0lagg")
            || mappedDeviceNeedsRecovery(managed)
        );
        if (setupRequired) {
            configureSelectedLagg();
        } else {
            saveSettings();
        }
    }

    $("#saveSettings").on("click", saveFromForm);
    $("#recheckConfigure").on("click", recheckConfigureOutcome);
    $("#retryApply").on("click", function() {
        runRecovery($(this), "/service/apply", "Guarded reconciliation completed.");
    });
    $("#refreshDiagnostics").on("click", function() {
        invalidateStatus();
        renderStatusUnavailable("Refreshing required observations...");
        refreshStatus();
    });
    $("#generateMac").on("click", function() {
        if (!canGenerateMac || configureBusy || settingsBusy) {
            return;
        }
        post(api + "/status/generate_mac", {}, 10000).done(function(data) {
            if (data.mac) {
                field("dhcphashared.shared_mac").val(data.mac).trigger("change");
            }
        }).fail(function() {
            $("#settingsResult").text("MAC generation failed. Existing form values are unchanged.");
        });
    });
    $("#resetFailback").on("click", function() {
        $("#failbackDelay").val("0");
        $("#failbackWarning").hide();
        $("#settingsResult").text("Reset selected in the unsaved form. Return to Settings and choose Save & Apply to store it.");
        showTab("#settings");
        updateActions();
    });
    field("dhcphalocal.managed_interface").on("changed.bs.select change", function() {
        if (populatingSettings) { return; }
        const managed = $(this).val() || "";
        formGeneration++;
        configureRetryReady = false;
        const carrier = managed === savedMapping.managed ? savedMapping.carrier : "";
        managedDescription = managed;
        loadCarrierPreview(managed, carrier);
        updateActions();
    });
    $("#frm_Settings").on("input change", ":input", function() {
        if (populatingSettings) { return; }
        formGeneration++;
        configureRetryReady = false;
        updateActions();
    });
    $("#maintabs a[data-toggle='tab']").on("shown.bs.tab", function(event) {
        const hash = $(event.target).attr("href");
        if (hash) {
            history.replaceState(null, "", hash);
        }
        clearTimeout(statusTimer);
        if (currentTabVisible()) {
            refreshStatus();
        }
    });
    $(document).on("visibilitychange", function() {
        clearTimeout(statusTimer);
        if (document.visibilityState === "visible") {
            markStatusStale();
            refreshStatus();
        }
    });

    if (!canWriteSettings) {
        $("#frm_Settings :input").prop("disabled", true);
    }
    $("#logTab").toggle({{ canViewLogs ? 'true' : 'false' }});
    loadSettings();
    const requestedTab = window.location.hash;
    if (["#settings", "#status", "#diagnostics"].includes(requestedTab)) {
        showTab(requestedTab);
    }
});
</script>

<section class="page-content-main">
    <div class="alert alert-warning">
        {{ lang._('Experimental controller. Enabling this feature allows native CARP state to move the local adapter. Keep console or alternate management access available during interface setup.') }}
    </div>
    <div id="configureAllowedInterfaces" class="hidden">
        {% for interfaceId in configureAllowedInterfaces %}<span data-interface="{{ interfaceId }}"></span>{% endfor %}
    </div>

    <ul class="nav nav-tabs" data-tabs="tabs" id="maintabs">
        <li class="active" id="settingsTab"><a data-toggle="tab" href="#settings">{{ lang._('Settings') }}</a></li>
        <li id="diagnosticsTab"><a data-toggle="tab" href="#diagnostics">{{ lang._('Diagnostics') }}</a></li>
        {% if canViewLogs %}<li id="logTab"><a href="/ui/dhcpinterfaceha/index/log">{{ lang._('Log') }}</a></li>{% endif %}
    </ul>

    <div class="tab-content content-box">
        <div class="tab-pane fade in active" id="settings">
            <p aria-live="polite"><strong>{{ lang._('State') }}:</strong> <span id="summaryState" class="label label-default">{{ lang._('Loading') }}</span></p>

            {% if not canWriteSettings %}
            <div class="alert alert-info">{{ lang._('This account can view settings and diagnostics but cannot save or run plugin actions.') }}</div>
            {% endif %}
            {{ partial("layout_partials/base_form", ['fields': settings, 'id': 'frm_Settings']) }}
            <input type="hidden" id="revision" value="" />
            <input type="hidden" id="failbackDelay" value="0" />

            <div class="content-box">
                <p id="carrierLoadError" class="text-danger" style="display:none"></p>
                <p id="configurePermissionNote" class="text-warning" style="display:none"></p>
                <p id="configureSavedStateNote" class="text-warning" style="display:none"></p>
                <p id="configureEvidenceNote" class="text-warning" style="display:none"></p>
                <p id="configureCarrierNote" class="text-warning" style="display:none"></p>
                <p id="saveIdentityNote" class="text-warning" style="display:none"></p>
                <div id="configureBlock" style="display:none">
                    <button class="btn btn-default" id="recheckConfigure" type="button" style="display:none">{{ lang._('Recheck save outcome') }}</button>
                    <p id="configureOutcomeNote" class="help-block" style="display:none">{{ lang._('Save remains blocked until a read-only check confirms the saved settings and current assignment. Recheck outcome reads both again. If your form changed or readback remains unavailable, copy any unsaved values and reload this page to load current saved settings.') }}</p>
                    <p class="help-block">{{ lang._('Save & Apply configures the interface, then applies Enable. Setup may interrupt this interface. Use a separate management path and do not edit native assignments concurrently.') }}</p>
                </div>
                <div class="form-group" id="macHelpers">
                    {% if canGenerateMac %}<button class="btn btn-default" id="generateMac" type="button">{{ lang._('Generate') }}</button>{% endif %}
                    <p class="help-block">{{ lang._('Generate fills in a new shared MAC. Save & Apply stores it.') }}</p>
                </div>
                <p id="failbackWarning" class="text-warning" style="display:none">
                    {{ lang._('A nonzero legacy delayed failback value is saved:') }} <span id="storedFailbackValue"></span>.
                    <a href="#diagnostics" data-toggle="tab">{{ lang._('Review the corrective check in Diagnostics.') }}</a>
                </p>
                <button class="btn btn-primary" id="saveSettings" type="button">{{ lang._('Save & Apply') }}</button>
                <button class="btn btn-default" id="retryApply" type="button" style="display:none">{{ lang._('Retry Apply') }}</button>
                <p id="settingsResult" role="status"></p>
                <p id="setupResult" role="status"></p>
                <p id="serviceResult" role="status"></p>
            </div>
        </div>

        <div class="tab-pane fade" id="diagnostics">
            <h4>{{ lang._('Observed details') }}</h4>
            <dl class="dl-horizontal">
                <dt>{{ lang._('CARP role') }}</dt><dd id="detailRole">{{ lang._('Unknown') }}</dd>
                <dt>{{ lang._('Attachment') }}</dt><dd id="detailAttachment">{{ lang._('Unknown') }}</dd>
                <dt>{{ lang._('IPv4 address') }}</dt><dd id="detailAddress">{{ lang._('Unknown') }}</dd>
                <dt>{{ lang._('Controller') }}</dt><dd id="detailController">{{ lang._('Unknown') }}</dd>
                <dt>{{ lang._('Carrier and receive mode') }}</dt><dd id="detailCarrier">{{ lang._('Unknown') }}</dd>
                <dt>{{ lang._('DHCP HA device') }}</dt><dd id="detailDevice">{{ lang._('Unknown') }}</dd>
                <dt>{{ lang._('Native DHCP observation') }}</dt><dd id="detailDhcp">{{ lang._('Unknown') }}</dd>
                <dt>{{ lang._('IPv4 gateway') }}</dt><dd id="detailGateway">{{ lang._('Unknown') }}</dd>
                <dt>{{ lang._('HA configuration') }}</dt><dd id="detailHA">{{ lang._('Unknown') }}</dd>
            </dl>
            <h4>{{ lang._('Native configuration') }}</h4>
            <ul>
                <li><a href="/ui/interfaces/assign">{{ lang._('Interfaces: Assignments') }}</a></li>
                <li><a href="/ui/interfaces/vip">{{ lang._('Virtual IPs: CARP') }}</a></li>
                <li><a href="/ui/core/hasync">{{ lang._('System: High Availability') }}</a></li>
                <li><a href="/ui/core/hasync_status">{{ lang._('System: High Availability Status') }}</a></li>
                <li><a href="/ui/syslog">{{ lang._('System: Settings: Logging / Targets') }}</a></li>
                <li><a href="/ui/firmware/plugins">{{ lang._('Firmware: Plugins') }}</a></li>
            </ul>
            <button class="btn btn-default" id="refreshDiagnostics" type="button">{{ lang._('Refresh') }}</button>
        </div>
    </div>
</section>
