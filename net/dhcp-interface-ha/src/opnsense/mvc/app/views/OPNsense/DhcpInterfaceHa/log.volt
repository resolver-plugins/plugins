<style>
/* The native log partial includes a clear button; this page is read-only. */
#flushlog {
    display: none !important;
}
</style>

<script>
function isPluginLogRequest(settings) {
    if (!settings || typeof settings.url !== "string") {
        return false;
    }
    try {
        return new URL(settings.url, window.location.href).pathname ===
            "/api/diagnostics/log/dhcpinterfaceha/core";
    } catch (error) {
        return false;
    }
}
$(document).ajaxError(function(event, xhr, settings) {
    if (isPluginLogRequest(settings)) {
        $("#logEmpty").addClass("hidden");
        $("#logAccessError").removeClass("hidden");
    }
}).ajaxSuccess(function(event, xhr, settings, data) {
    if (isPluginLogRequest(settings)) {
        const valid = data && Array.isArray(data.rows) && Number.isFinite(data.total);
        $("#logAccessError").toggleClass("hidden", Boolean(valid));
        $("#logEmpty").toggleClass("hidden", !valid || data.total !== 0);
    }
});
</script>

<section class="page-content-main">
    <ul class="nav nav-tabs" data-tabs="tabs" id="maintabs">
        <li><a href="/ui/dhcpinterfaceha">{{ lang._('Settings') }}</a></li>
        <li><a href="/ui/dhcpinterfaceha#diagnostics">{{ lang._('Diagnostics') }}</a></li>
        <li class="active"><a href="/ui/dhcpinterfaceha/log">{{ lang._('Log') }}</a></li>
    </ul>

    <div id="logAccessError" class="alert alert-danger{% if canViewLogs %} hidden{% endif %}" role="alert" aria-live="assertive">
        <strong>{{ lang._('Log access is unavailable.') }}</strong>
        {{ lang._('Check the account permissions, then retry the log request.') }}
        <a href="/ui/dhcpinterfaceha/log">{{ lang._('Retry') }}</a>
    </div>

    {% if canViewLogs %}
        <div id="logEmpty" class="alert alert-info hidden" role="status">
            {{ lang._('No events recorded for the current filters.') }}
        </div>
        {% if localLoggingEnabled === false %}
        <div class="alert alert-warning" role="status">
            {{ lang._('Local logging is disabled, so no new local DHCP Interface HA events are being stored.') }}
            <a href="/ui/diagnostics/log_settings">{{ lang._('Review native logging settings') }}</a>.
        </div>
        {% elseif localLoggingEnabled === null %}
        <div class="alert alert-info" role="status">
            {{ lang._('Local logging status is unavailable. Check the native logging settings.') }}
            <a href="/ui/diagnostics/log_settings">{{ lang._('Review native logging settings') }}</a>.
        </div>
        {% endif %}

        {{ partial("OPNsense/Diagnostics/log", [
            'module': 'dhcpinterfaceha',
            'scope': 'core',
            'service': '',
            'default_log_severity': 'Informational'
        ]) }}
    {% endif %}
</section>
