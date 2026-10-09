// Exercise the wrapper's real AJAX handlers, without copying the native viewer.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const view = fs.readFileSync(path.join(__dirname,
    '../../src/opnsense/mvc/app/views/OPNsense/DhcpInterfaceHa/log.volt'), 'utf8');
const handlers = {};
const hidden = { '#logAccessError': true, '#logEmpty': true };
const document = {};
const events = {
    ajaxError(handler) { handlers.error = handler; return this; },
    ajaxSuccess(handler) { handlers.success = handler; return this; }
};
vm.runInNewContext(view.match(/<script>([\s\S]*?)<\/script>/)[1], {
    document, URL, window: { location: { href: 'https://firewall.example.invalid/ui/dhcpinterfaceha/index/log' } },
    $(selector) {
        if (selector === document) { return events; }
        return {
            addClass() { hidden[selector] = true; },
            removeClass() { hidden[selector] = false; },
            toggleClass(name, state) { hidden[selector] = state; }
        };
    }
});
const request = { url: '/api/diagnostics/log/dhcpinterfaceha/core' };
handlers.success(null, {}, request, { total: 0, rows: [] });
assert.deepEqual(hidden, { '#logAccessError': true, '#logEmpty': false });
handlers.error(null, {}, request);
assert.deepEqual(hidden, { '#logAccessError': false, '#logEmpty': true });
handlers.success(null, {}, request, { total: 1, rows: [{ message: 'event' }] });
assert.deepEqual(hidden, { '#logAccessError': true, '#logEmpty': true });
handlers.success(null, {}, request, { error: 'backend unavailable' });
assert.deepEqual(hidden, { '#logAccessError': false, '#logEmpty': true });
handlers.success(null, {}, { url: '/api/diagnostics/log/system/latest' }, { total: 0, rows: [] });
assert.deepEqual(hidden, { '#logAccessError': false, '#logEmpty': true });
console.log('Native Log wrapper response checks passed');
