// Run with: node net/dhcp-interface-ha/tests/ui/test_configure_lagg.js
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const viewPath = path.join(__dirname, '../../src/opnsense/mvc/app/views/OPNsense/DhcpInterfaceHa/index.volt');
const view = fs.readFileSync(viewPath, 'utf8');
const script = view.match(/<script>([\s\S]*?)<\/script>/)[1].replace(/{{[^}]*}}/g, 'true');
assert.doesNotThrow(() => new vm.Script(script), 'the Volt page JavaScript parses after template values are substituted');

function between(start, end) {
    const first = view.indexOf(start);
    const last = view.indexOf(end, first + start.length);
    assert.notEqual(first, -1, 'missing source marker: ' + start);
    assert.notEqual(last, -1, 'missing source marker: ' + end);
    return view.slice(first, last);
}

function progressContext(values) {
    const context = vm.createContext(Object.assign({
        saveProgress: null, saveProgressTimer: null, settingsBusy: false, configureBusy: false, carrierPreviewPending: false,
        crypto: {getRandomValues: bytes => bytes.fill(1)}, Uint8Array,
        setTimeout: () => 1, clearTimeout: () => {}
    }, values));
    vm.runInContext(between('    function renderSaveProgress()', '    function currentTabVisible()'), context);
    return context;
}

function domFixture() {
    const nodes = Object.create(null);
    const inputs = [{props: {disabled: true}}, {props: {disabled: false}}];

    function nodeFor(key) {
        if (typeof key === 'object' && key !== null) {
            return key;
        }
        return nodes[key] ||= {props: {}, value: '', text: '', visible: true, children: []};
    }

    function access(object, key) {
        return function (...values) {
            if (!values.length) return object[key];
            object[key] = values[0];
            return this;
        };
    }

    function wrap(key) {
        const node = nodeFor(key);
        return {
            node,
            on(events, selector, handler) {
                node.handler = handler || selector;
                return this;
            },
            selectpicker() { return this; },
            val: access(node, 'value'),
            text: access(node, 'text'),
            prop(name, ...values) { return access(node.props, name).apply(this, values); },
            toggle(value) {
                node.visible = value;
                return this;
            },
            show() { return this.toggle(true); },
            hide() { return this.toggle(false); },
            empty() {
                node.children = [];
                return this;
            },
            append(value) {
                node.children.push(value);
                return this;
            },
            addClass(value) {
                node.className = value;
                return this;
            },
            removeClass() {
                node.className = '';
                return this;
            }
        };
    }

    const inputCollection = {
        prop(name, value) {
            if (arguments.length > 1) {
                inputs.forEach(input => { input.props[name] = value; });
            }
            return this;
        },
        map(callback) {
            return {get: () => inputs.map((input, index) => callback.call(input, index, input))};
        },
        each(callback) {
            inputs.forEach((input, index) => callback.call(input, index, input));
            return this;
        }
    };

    function $(selector) {
        if (selector === '#frm_Settings :input') {
            return inputCollection;
        }
        return wrap(selector);
    }

    function setField(id, value, props = {}) {
        const node = nodeFor('field:' + id);
        node.value = value;
        node.props = Object.assign({}, node.props, props);
    }

    return {$, setField, inputs, nodes,
        field: id => wrap('field:' + id),
        getText: selector => nodeFor(selector).text,
        getValue: selector => nodeFor(selector).value};
}

function detachedStatus() {
    return {
        setup: {pending_assignment: {available: true, state: 'clear'}},
        attachment: {
            actual: 'FENCED',
            desired: 'FENCED',
            members: [],
            owned: true,
            device: {exists: true, protocol: 'failover'}
        }
    };
}

function statusContext() {
    return {statusGeneration: 0, selectionGeneration: 0, statusInFlight: false,
        statusRefreshPending: false, statusRequestPromise: null, statusTimer: null,
        statusData: null, statusObservedAt: 0};
}

function configureFixture(options = {}) {
    const dom = domFixture();
    dom.setField('dhcphalocal.managed_interface', 'opt7');
    dom.setField('dhcphashared.enabled', '', {checked: options.enabledDraft === undefined ? true : options.enabledDraft});
    dom.setField('dhcphashared.shared_mac', '02:00:00:00:00:01');
    dom.$('#revision').val('rev1');
    dom.$('#failbackDelay').val('0');
    const calls = [];
    const readbacks = [];
    const context = progressContext(Object.assign({}, dom, statusContext(), {
        api: '/api/dhcpinterfaceha',
        canConfigureInterface: true,
        canWriteSettings: true,
        configureAllowedInterfaces: options.allowed === false ? [] : ['opt7'],
        configureRetryReady: false,
        configureRetryGeneration: -1,
        configureOutcomeBlocked: false,
        pendingConfigureOutcome: null,
        formGeneration: 0,
        localCarrier: 'em0',
        previewDevice: 'em0',
        managedDescription: 'LAN',
        savedEnabled: options.savedEnabled === true,
        savedMapping: {managed: 'opt7', carrier: 'em0'},
        canRunRecovery: false,
        canGenerateMac: false,
        statusData: detachedStatus(),
        statusObservedAt: Date.now(),
        staleTimer: null,
        window: {confirm: () => assert.fail('Save must not open a confirmation dialog')},
        handleFormValidation: () => {},
        invalidateStatus: () => {},
        refreshStatus: () => {},
        updateActions: () => {},
        isConfiguredForForm: () => false,
        getFormData: () => ({
            dhcphashared: {
                enabled: dom.field('dhcphashared.enabled').prop('checked') ? '1' : '0',
                shared_mac: dom.field('dhcphashared.shared_mac').val(),
                failback_delay: '0'
            },
            dhcphalocal: {managed_interface: dom.field('dhcphalocal.managed_interface').val(), carrier: ''},
            revision: dom.getValue('#revision')
        }),
        post: (url, payload) => {
            calls.push({url, payload: JSON.parse(JSON.stringify(payload))});
            if (options.post) {
                return options.post(url, payload);
            }
            if (options.timeout) {
                return Promise.reject(new Error('timeout'));
            }
            return Promise.resolve(options.result || {
                result: 'saved', saved: true, applied: true, assignment_verified: true,
                setup_stage: 'verified', revision: 'rev2'
            });
        },
        readConfigureOutcome: async (...args) => {
            readbacks.push(args);
        }
    }));
    vm.runInContext(between('    function verifiedDetached(', '    function isConfiguredForForm('), context);
    vm.runInContext(between('    function mappedDeviceNeedsRecovery(', '    function loadCarrierPreview('), context);
    vm.runInContext(between('    function updateActions()', '    function requestStatusOnce('), context);
    vm.runInContext(between('    function blockConfigureOutcome(', '    async function readConfigureOutcome('), context);
    vm.runInContext(between('    async function configureSelectedLagg()', '    function runRecovery('), context);
    return {context, dom, calls, readbacks};
}

function settingsReadback(overrides = {}) {
    return Object.assign({
        revision: 'rev2',
        managed_interface_value: 'opt7',
        dhcphalocal: {
            managed_interface: {opt7: {selected: '1'}},
            carrier: {value: 'em0'}
        },
        dhcphashared: {
            enabled: {value: '0'},
            shared_mac: {value: '02:00:00:00:00:01'},
            failback_delay: {value: '0'}
        }
    }, overrides);
}

function setupReadbackStatus(pendingState = 'clear', deviceName = 'dhcpha0lagg') {
    const status = detachedStatus();
    status.result = 'ok';
    status.managed = {identifier: 'opt7', description: 'LAN', device: deviceName};
    status.setup.pending_assignment.state = pendingState;
    status.attachment.carrier = {selected: 'em0', exists: true, mac: '02:11:22:33:44:55'};
    status.attachment.device.name = 'dhcpha0lagg';
    return status;
}

function readbackFixture(settings, status, options = {}) {
    const {context, dom} = configureFixture();
    dom.setField('dhcphashared.shared_mac', options.currentMac || '02:00:00:00:00:01');
    let settingsReads = 0;
    let statusReads = 0;
    Object.assign(context, {
        statusData: null,
        formGeneration: options.formGeneration || 0,
        configureOutcomeBlocked: true,
        savedMapping: {managed: 'opt7', carrier: 'old0'},
        savedEnabled: true,
        localCarrier: 'old0',
        previewDevice: 'old0',
        getJson: async () => { settingsReads++; return settings; },
        requestFreshStatusForReadback: async () => {
            statusReads++;
            context.statusData = status;
            context.statusObservedAt = options.observedAt || Date.now();
            return status;
        },
    });
    vm.runInContext(between('    function usableMac(', '    function getJson('), context);
    vm.runInContext(between('    function safelyRetryableSetup(', '    function mappedDeviceNeedsRecovery('), context);
    vm.runInContext(between('    function sameMac(', '    function blockConfigureOutcome('), context);
    vm.runInContext(between('    function blockConfigureOutcome(', '    async function configureSelectedLagg('), context);
    return {context, dom, readCounts: () => ({settingsReads, statusReads}),
        readOutcome(payload, generation = 0, backendVerified = false) {
            Object.assign(context, {testPayload: payload, testGeneration: generation, testBackendVerified: backendVerified});
            return vm.runInContext('readConfigureOutcome("opt7", testPayload, testGeneration, "em0", testBackendVerified)', context);
        }};
}

function deferredAjax(onChange) {
    let done = () => {};
    let fail = () => {};
    let always = () => {};
    return {
        done(callback) { done = callback; return this; },
        fail(callback) { fail = callback; return this; },
        always(callback) { always = callback; return this; },
        resolve(value) {
            onChange(-1);
            done(value);
            always();
        },
        reject(error) {
            onChange(-1);
            fail(error);
            always();
        }
    };
}

async function testSaveWaitsForInterfaceLookup() {
    const fixture = configureFixture();
    const request = deferredAjax(() => {});
    fixture.context.getJson = () => request;
    vm.runInContext(between('    function loadCarrierPreview(', '    function updateAddressDetail('), fixture.context);
    vm.runInContext('loadCarrierPreview("opt7", ""); updateActions()', fixture.context);
    assert.equal(fixture.dom.nodes['#saveSettings'].props.disabled, true,
        'Save must wait for the selected interface lookup instead of reporting an ambiguous device');
    await vm.runInContext('configureSelectedLagg()', fixture.context);
    assert.equal(fixture.calls.length, 0);
    assert.equal(fixture.dom.getText('#setupResult'), '', 'a pending lookup is not an error');
    request.resolve({interface: 'opt7', managed: {current_device: 'ix0', description: 'WAN'}, errors: {}});
    assert.equal(fixture.dom.nodes['#saveSettings'].props.disabled, false);
    assert.equal(fixture.context.previewDevice, 'ix0');
    await vm.runInContext('configureSelectedLagg()', fixture.context);
    assert.equal(fixture.calls.length, 1, 'the first available Save submits configuration');

    const failed = configureFixture();
    const failedRequest = deferredAjax(() => {});
    failed.context.getJson = () => failedRequest;
    vm.runInContext(between('    function loadCarrierPreview(', '    function updateAddressDetail('), failed.context);
    vm.runInContext('loadCarrierPreview("opt7", "")', failed.context);
    failedRequest.reject(new Error('timeout'));
    assert.equal(failed.context.carrierPreviewPending, false, 'a failed lookup does not leave the page loading forever');
    await vm.runInContext('configureSelectedLagg()', failed.context);
    assert.equal(failed.calls.length, 0);
    assert.match(failed.dom.getText('#setupResult'), /no configuration was submitted/);
}

async function testConfigureHandler() {
    const blockedCases = [
        {name: 'saved Enable', options: {savedEnabled: true, enabledDraft: false}},
        {name: 'native permission', options: {allowed: false}},
        {name: 'uncertain outcome', context: {configureOutcomeBlocked: true, pendingConfigureOutcome: {managed: 'opt7'}}, recheck: true},
        ...['conflict', 'unknown', 'selected_relink'].map(state => ({name: state, pending: state, recheck: state === 'selected_relink'}))
    ];
    for (const row of blockedCases) {
        const fixture = configureFixture(row.options);
        Object.assign(fixture.context, row.context);
        if (row.pending) {
            fixture.context.statusData.setup.pending_assignment = {available: row.pending !== 'unknown', state: row.pending};
        }
        vm.runInContext('updateActions()', fixture.context);
        if (row.recheck !== undefined) {
            assert.equal(fixture.dom.nodes['#recheckConfigure'].visible, row.recheck, row.name);
        }
        if (row.name === 'uncertain outcome') {
            assert.equal(fixture.dom.nodes['#saveSettings'].props.disabled, true, row.name);
        }
        await vm.runInContext('configureSelectedLagg()', fixture.context);
        assert.equal(fixture.calls.length, 0, row.name + ' blocks Configure');
    }

    const unchanged = configureFixture({savedEnabled: true});
    unchanged.context.statusData.setup.pending_assignment.state = 'selected_relink';
    vm.runInContext('updateActions()', unchanged.context);
    assert.equal(unchanged.dom.nodes['#configureQueueNote']?.visible ?? false, false,
        'a cached pending observation must not produce unsolicited relink advice for an enabled mapping');

    const saving = configureFixture();
    saving.context.configureBusy = true;
    saving.context.statusData.setup.pending_assignment.state = 'selected_relink';
    vm.runInContext('updateActions()', saving.context);
    assert.equal(saving.dom.nodes['#configureQueueNote']?.visible ?? false, false,
        'our own in-flight native relink is not an error or retry request');
    assert.equal(saving.dom.nodes['#recheckConfigure'].visible, false,
        'do not offer readback until the current save has finished');

    let finishPost;
    const duplicate = configureFixture({
        post: () => new Promise(resolve => { finishPost = resolve; })
    });
    const first = vm.runInContext('configureSelectedLagg()', duplicate.context);
    assert.deepEqual(duplicate.dom.inputs.map(input => input.props.disabled), [true, true]);
    await vm.runInContext('configureSelectedLagg()', duplicate.context);
    assert.equal(duplicate.calls.length, 1, 'a second click while Configure is in flight is ignored');
    const submitted = duplicate.calls[0];
    assert.equal(submitted.url, '/api/dhcpinterfaceha/settings/configure');
    assert.deepEqual(submitted.payload, {
        dhcphashared: {enabled: '1', shared_mac: '02:00:00:00:00:01', failback_delay: '0'},
        dhcphalocal: {managed_interface: 'opt7', carrier: 'em0'},
        revision: 'rev1',
        progress_id: '01'.repeat(16)
    }, 'Configure submits both complete roots and the current revision, honoring Enable');
    finishPost({result: 'saved', saved: true, applied: true, assignment_verified: true, setup_stage: 'verified', revision: 'rev2'});
    await first;
    assert.deepEqual(duplicate.dom.inputs.map(input => input.props.disabled), [true, false],
        'save completion restores previously disabled controls without enabling them');
    assert.equal(duplicate.dom.field('dhcphashared.enabled').prop('checked'), true,
        'successful setup preserves the requested Enable choice');
    assert.equal(duplicate.readbacks.length, 1);

    for (const options of [
        {result: {result: 'failed', saved: true, applied: false, assignment_verified: false, setup_stage: 'settings_saved'}},
        {timeout: true}
    ]) {
        const fixture = configureFixture(options);
        await vm.runInContext('configureSelectedLagg()', fixture.context);
        assert.equal(fixture.calls.length, 1, 'a partial or unknown outcome never replays Configure');
        assert.equal(fixture.readbacks.length, 1, 'read back settings and status after a partial or unknown outcome');
    }

    const mappedMissing = configureFixture();
    mappedMissing.context.previewDevice = 'dhcpha0lagg';
    mappedMissing.context.statusData = {
        managed: {identifier: 'opt7', device: 'dhcpha0lagg'},
        setup: {pending_assignment: {available: true, state: 'clear'}},
        attachment: {
            actual: 'FENCED', members: [], owned: false,
            device: {exists: false}
        }
    };
    await vm.runInContext('configureSelectedLagg()', mappedMissing.context);
    assert.equal(mappedMissing.calls.length, 1,
        'a known saved carrier and safely absent mapped device remains eligible for guarded recovery');
}

function testSaveDispatchesSetup() {
    const dom = domFixture();
    dom.setField('dhcphalocal.managed_interface', 'opt7');
    const calls = [];
    const context = progressContext(Object.assign({}, dom, {
        savedEnabled: false,
        savedMapping: {managed: '', carrier: ''},
        previewDevice: 'hn1',
        mappedDeviceNeedsRecovery: () => false,
        configureSelectedLagg: () => calls.push('setup'),
        saveSettings: () => calls.push('save')
    }));
    vm.runInContext(between('    function saveFromForm()', '    $("#saveSettings").on'), context);
    vm.runInContext('saveFromForm()', context);
    assert.deepEqual(calls, ['setup'], 'Save runs guarded setup for an unconfigured selection');

    context.savedMapping = {managed: 'opt7', carrier: 'hn1'};
    context.previewDevice = 'dhcpha0lagg';
    vm.runInContext('saveFromForm()', context);
    assert.deepEqual(calls, ['setup', 'save'], 'Save uses the ordinary settings path after setup');
    context.savedEnabled = true;
    context.previewDevice = 'hn1'; // stale preview must not re-run setup
    vm.runInContext('saveFromForm()', context);
    assert.deepEqual(calls, ['setup', 'save', 'save'], 'an enabled saved mapping never dispatches setup');
}

async function testConfigureReadbackGuards() {
    const submitted = {
        dhcphashared: {enabled: '0', shared_mac: '02:00:00:00:00:01', failback_delay: '0'},
        dhcphalocal: {managed_interface: 'opt7', carrier: 'em0'},
        revision: 'rev1'
    };
    for (const [name, pending, enabled, retry, blocked, requested = enabled] of [
        ['complete disabled', 'clear', false, false, false],
        ['complete enabled', 'clear', true, false, false],
        ['enable not applied', 'clear', false, false, false, true],
        ['safe relink retry', 'selected_relink', false, true, false],
        ['conflicting queue', 'conflict', false, false, true],
        ['unknown queue', 'unknown', false, false, true]
    ]) {
        const settings = settingsReadback();
        settings.dhcphashared.enabled.value = enabled ? '1' : '0';
        const status = setupReadbackStatus(pending);
        const payload = JSON.parse(JSON.stringify(submitted));
        payload.dhcphashared.enabled = requested ? '1' : '0';
        if (enabled) Object.assign(status.attachment, {actual: 'ATTACHED', members: ['em0']});
        const fixture = readbackFixture(settings, status);
        fixture.dom.field('dhcphashared.enabled').prop('checked', !enabled);
        await fixture.readOutcome(payload, 0, enabled);
        assert.equal(fixture.context.configureRetryReady, retry, name);
        assert.equal(fixture.context.configureOutcomeBlocked, blocked, name);
        if (pending === 'clear') {
            assert.equal(fixture.dom.getValue('#revision'), 'rev2', name);
            assert.equal(fixture.context.savedMapping.carrier, 'em0', name);
            assert.equal(fixture.context.savedEnabled, enabled, name);
            assert.equal(fixture.dom.field('dhcphashared.enabled').prop('checked'), enabled, name);
            if (requested === enabled) assert.equal(fixture.dom.getText('#setupResult'), '', name);
            else assert.match(fixture.dom.getText('#setupResult'), /Enable was not applied/, name);
        }
    }

    const recheck = readbackFixture(settingsReadback(), setupReadbackStatus('conflict'));
    recheck.context.pendingConfigureOutcome = {
        managed: 'opt7', payload: submitted, formGeneration: 0, expectedCarrier: 'em0', backendVerified: false
    };
    await vm.runInContext('recheckConfigureOutcome()', recheck.context);
    assert.deepEqual(recheck.readCounts(), {settingsReads: 1, statusReads: 1},
        'Recheck outcome performs one saved-settings and fresh-status readback');
    assert.equal(recheck.context.configureOutcomeBlocked, true,
        'read-only recheck preserves the latch while a queue conflict remains');

    const reopened = readbackFixture(settingsReadback(), setupReadbackStatus('selected_relink', 'em0'));
    reopened.context.configureOutcomeBlocked = false;
    reopened.context.savedEnabled = false;
    reopened.context.savedMapping = {managed: 'opt7', carrier: 'em0'};
    reopened.context.statusData = setupReadbackStatus('selected_relink', 'em0');
    reopened.context.getFormData = () => JSON.parse(JSON.stringify(submitted));
    reopened.dom.$('#failbackDelay').val('0');
    await vm.runInContext('recheckConfigureOutcome()', reopened.context);
    assert.deepEqual(reopened.readCounts(), {settingsReads: 1, statusReads: 1},
        'a reopened page verifies the saved intent through read-only readback');
    assert.equal(reopened.context.configureRetryReady, true,
        'a matching pending relink can resume after reloading the page');

    for (const [root, field, value] of [
        ['dhcphashared', 'shared_mac', '02:00:00:00:00:99'],
        ['dhcphalocal', 'carrier', 'em9']
    ]) {
        const changed = settingsReadback({revision: 'rev-other'});
        changed[root][field] = {value};
        const writer = readbackFixture(changed, setupReadbackStatus());
        await writer.readOutcome(submitted);
        assert.equal(writer.dom.getValue('#revision'), 'rev1',
            'a differing ' + field + ' cannot advance the revision past another writer');
        assert.equal(writer.context.configureRetryReady, false);
    }

    const dirty = readbackFixture(settingsReadback(), setupReadbackStatus('clear'), {
        formGeneration: 2,
        currentMac: '02:00:00:00:00:99'
    });
    await dirty.readOutcome(submitted, 1);
    assert.equal(dirty.dom.getValue('#revision'), 'rev1', 'a response for an older form generation cannot advance revision');
    assert.equal(dirty.dom.field('dhcphashared.shared_mac').val(), '02:00:00:00:00:99',
        'readback preserves values edited after submission');
    assert.equal(dirty.context.configureRetryReady, false);

    const stale = readbackFixture(settingsReadback(), setupReadbackStatus('selected_relink'), {observedAt: Date.now() - 16000});
    await stale.readOutcome(submitted);
    assert.equal(stale.context.configureRetryReady, false, 'stale assignment evidence cannot claim a safe retry');
    assert.equal(stale.context.configureOutcomeBlocked, true);
}

function testConfiguredStateRequiresVerifiedOwnership() {
    const {context} = configureFixture();
    vm.runInContext(between('    function isConfiguredForForm(', '    function safelyRetryableSetup('), context);
    context.statusData = {
        managed: {identifier: 'opt7', device: 'dhcpha0lagg'},
        attachment: {
            owned: true,
            carrier: {selected: 'em0'},
            device: {exists: true, protocol: 'failover'}
        },
        readiness: [
            {code: 'managed_assignment', status: 'pass'},
            {code: 'device_ownership', status: 'pass'},
            {code: 'device_topology', status: 'pass'}
        ]
    };
    assert.equal(vm.runInContext('isConfiguredForForm()', context), true);
    context.statusData.attachment.owned = false;
    assert.equal(vm.runInContext('isConfiguredForForm()', context), false,
        'a native mapping alone cannot be treated as configured for a foreign device');
    context.statusData.attachment.owned = true;
    context.statusData.attachment.device.exists = false;
    assert.equal(vm.runInContext('isConfiguredForForm()', context), false,
        'a native mapping alone cannot be treated as configured when the owned device is absent');
}

function testDisabledSelectionCanBeSaved() {
    const {context, dom} = configureFixture({savedEnabled: true});
    dom.setField('dhcphalocal.managed_interface', '');
    context.statusData = null;
    vm.runInContext('updateActions()', context);
    assert.equal(dom.field('dhcphashared.enabled').prop('checked'), false,
        'Disabled forces the unsaved Enable value off');
    assert.equal(dom.field('dhcphashared.enabled').prop('disabled'), true,
        'Enable cannot be selected while the managed interface is Disabled');
    assert.equal(dom.nodes['#saveSettings'].visible, true);
    assert.equal(dom.nodes['#saveSettings'].props.disabled, false,
        'Disabled remains saveable when an enabled interface was previously configured');
    assert.equal(dom.nodes['#saveIdentityNote'].visible, false,
        'the ordinary enabled identity-change warning does not block guarded removal');
}

function statusRequestSource() {
    return between('    function invalidateStatus(', '    function verifiedDetached(')
        + between('    function statusIsFresh(', '    function isConfiguredForForm(')
        + between('    function requestStatusOnce(', '    function loadSettings(');
}

function statusFixture(clock) {
    const requests = [];
    const rendered = [];
    let active = 0;
    let maximumActive = 0;
    let nextTimer = 1;
    const timers = [];
    const context = progressContext(Object.assign(statusContext(), {
        Date: {now: () => clock.now},
        Error,
        api: '/api/dhcpinterfaceha',
        currentTabVisible: () => true,
        setTimeout: (_callback, milliseconds) => {
            timers.push(milliseconds);
            return nextTimer++;
        },
        clearTimeout: () => {},
        renderStatus: data => {
            rendered.push(data);
            context.statusData = data;
            context.freshAtRender.push(context.statusIsFresh());
        },
        renderStatusUnavailable: () => {},
        getJson: () => {
            active++;
            maximumActive = Math.max(maximumActive, active);
            const request = deferredAjax(delta => { active += delta; });
            requests.push(request);
            return request;
        },
        freshAtRender: []
    }));
    vm.runInContext(statusRequestSource(), context);
    return {context, requests, rendered, timers, maximumActive: () => maximumActive};
}

async function testSharedStatusReadbackAndFreshness() {
    const clock = {now: 1000};
    const fixture = statusFixture(clock);
    const inFlight = vm.runInContext('requestStatusOnce()', fixture.context);
    fixture.context.selectionGeneration++;
    vm.runInContext('invalidateStatus()', fixture.context);
    const readback = vm.runInContext('requestFreshStatusForReadback()', fixture.context);
    assert.equal(fixture.requests.length, 1, 'readback joins the existing status request');
    const stale = {result: 'ok', marker: 'old selection'};
    fixture.requests[0].resolve(stale);
    await inFlight;
    assert.equal(fixture.requests.length, 2, 'stale response triggers one fresh read after the first request ends');
    assert.deepEqual(fixture.rendered, [], 'status for an older interface selection is discarded');
    const current = {result: 'ok', marker: 'current selection'};
    fixture.requests[1].resolve(current);
    assert.equal((await readback).marker, 'current selection');
    assert.deepEqual(fixture.rendered, [current]);
    assert.equal(fixture.maximumActive(), 1, 'polling and explicit readback never overlap status requests');
    assert.ok(fixture.timers.includes(5000), 'visible status refresh remains on the five-second interval');

    const delayedClock = {now: 1000};
    const delayed = statusFixture(delayedClock);
    const delayedRequest = vm.runInContext('requestStatusOnce()', delayed.context);
    delayedClock.now = 17000;
    delayed.requests[0].resolve({result: 'ok'});
    await delayedRequest;
    assert.equal(delayed.context.statusIsFresh(), false,
        'a response taking at least fifteen seconds is already stale when it arrives');
    assert.deepEqual(delayed.context.freshAtRender, [false]);
}

async function testOldCarrierPreview() {
    const requests = [];
    const {context} = configureFixture();
    Object.assign(context, {
        getJson: () => {
            const request = deferredAjax(() => {});
            requests.push(request);
            return request;
        }
    });
    vm.runInContext(between('    function invalidateStatus(', '    function verifiedDetached('), context);
    vm.runInContext(between('    function loadCarrierPreview(', '    function updateAddressDetail('), context);
    vm.runInContext('loadCarrierPreview("opt7", "em0"); loadCarrierPreview("opt8", "");', context);
    requests[1].resolve({interface: 'opt8', managed: {current_device: 'em1', description: 'WAN'}, errors: {}});
    requests[0].resolve({interface: 'opt7', managed: {current_device: 'em0', description: 'LAN'}, errors: {}});
    assert.equal(context.managedDescription, 'WAN');
    assert.equal(context.previewDevice, 'em1');
    vm.runInContext('loadCarrierPreview("opt7", ""); loadCarrierPreview("opt8", "");', context);
    requests[2].resolve({interface: 'opt7', managed: {current_device: 'em0'}, errors: {}});
    assert.equal(context.carrierPreviewPending, true, 'a stale lookup cannot unlock Save for the current selection');
    requests[3].resolve({interface: 'opt8', managed: {current_device: 'em1'}, errors: {}});
    assert.equal(context.carrierPreviewPending, false);
}

function testSummaryAndAddressMeaning() {
    const {context, dom} = configureFixture();
    context.markStatusStale = () => {};
    vm.runInContext(between('    function updateAddressDetail(', '    function renderStatusUnavailable('), context);

    for (const [state, available, expected] of [
        ['STANDBY', true, 'Not expected while the interface is disconnected'],
        ['ACTIVE', true, 'Waiting for DHCP'],
        ['ACTIVE', false, 'IPv4 address unavailable']
    ]) {
        context.addressFixture = {
            attachment: {actual: 'FENCED', members: [], owned: true, device: {exists: true}},
            controller: {state}, connection: {ipv4: {available, address: null}}
        };
        assert.equal(vm.runInContext('updateAddressDetail(addressFixture)', context), expected);
    }

    // A detached disabled interface is safe; an unverified one must still demand attention.
    for (const [actual, expected] of [
        ['FENCED', 'Disabled · Interface disconnected'], ['UNVERIFIED', 'Needs attention']
    ]) {
        const status = setupReadbackStatus();
        Object.assign(status, {
            summary: {state: 'needs_attention', reason_code: 'attachment_unverified'},
            controller: {state: 'DISABLED'}, carp: {role: 'MASTER'},
            connection: {ipv4: {available: true, address: null}}
        });
        status.attachment.actual = actual;
        context.renderSummary(status);
        assert.equal(dom.getText('#summaryState'), expected);
    }
}

function testSettingsPopulationDoesNotActLikeUserEdits() {
    const fixture = configureFixture();
    const {context, dom} = fixture;
    dom.setField('dhcphalocal.managed_interface', '');
    dom.setField('dhcphashared.enabled', '', {checked: false});
    context.populatingSettings = false;
    let previews = 0;
    context.loadCarrierPreview = () => { previews++; };
    context.setFormData = () => {
        // Native setFormData fills fields in DOM order and emits change after
        // every field: Enable is populated before the interface dropdown.
        dom.field('dhcphashared.enabled').prop('checked', true);
        dom.nodes['#frm_Settings'].handler();
        dom.field('dhcphalocal.managed_interface').val('opt7');
        dom.nodes['field:dhcphalocal.managed_interface'].handler.call(dom.nodes['field:dhcphalocal.managed_interface']);
        dom.nodes['#frm_Settings'].handler();
    };
    context.getJson = () => ({done(callback) {
        callback({dhcphashared: {enabled: '1', shared_mac: '02:00:00:00:00:01', failback_delay: '0'},
            dhcphalocal: {managed_interface: {opt7: {selected: 1}}, carrier: 'em0'}, revision: 'rev2'});
        return {fail() {}};
    }});
    vm.runInContext(between('    function selectedOption(', '    function getJson('), context);
    vm.runInContext(between('    function loadSettings()', '    function saveSettings()'), context);
    vm.runInContext(between('    field("dhcphalocal.managed_interface").on(', '    $("#maintabs a'), context);
    vm.runInContext('loadSettings()', context);
    assert.equal(dom.field('dhcphashared.enabled').prop('checked'), true,
        'fresh load must show the saved enabled value after populating the interface');
    assert.equal(context.formGeneration, 0, 'loading fields is not a user edit');
    assert.equal(previews, 1, 'only load the final saved interface preview');
    dom.field('dhcphalocal.managed_interface').val('');
    dom.nodes['#frm_Settings'].handler();
    assert.equal(dom.field('dhcphashared.enabled').prop('checked'), false,
        'a real Disabled selection still clears Enable');
}

async function testSaveProgress() {
    const dom = domFixture();
    const timers = [];
    let reply;
    const context = progressContext(Object.assign({}, dom, {
        api: '/api/dhcpinterfaceha', payload: {},
        setTimeout: callback => { timers.push(callback); return timers.length; },
        getJson: () => Promise.resolve(reply)
    }));
    vm.runInContext('startSaveProgress(payload)', context);
    assert.match(dom.getValue('#saveProgressOutput'), /\[WORKING\]/);
    const id = context.payload.progress_id;
    reply = {id, state: 'running', steps: [
        {state: 'success', message: 'Prepare HA interface'},
        {state: 'running', message: 'Apply native assignment'}
    ]};
    await timers.shift()();
    assert.match(dom.getValue('#saveProgressOutput'), /\[OK\] Prepare HA interface/);
    assert.match(dom.getValue('#saveProgressOutput'), /\[WORKING\] Apply native assignment/);
    context.result = {progress: {id, state: 'failed', steps: [
        reply.steps[0], {state: 'failed', message: 'Apply native assignment — <error> no link'}
    ]}};
    vm.runInContext('finishSaveProgress(result); saveMessage("#setupResult", "Verification remains unavailable.")', context);
    assert.match(dom.getValue('#saveProgressOutput'), /\[FAIL\] Apply native assignment — <error> no link/);
    assert.match(dom.getValue('#saveProgressOutput'), /Verification remains unavailable/);
    assert.equal(dom.getText('#saveProgressSummary'), 'Save & Apply needs attention');
    assert.equal(dom.nodes['#saveProgressOutput'].children.length, 0, 'errors remain text, never HTML');
    // An already in-flight poll cannot overwrite the final result.
    await timers.shift()();
    assert.match(dom.getValue('#saveProgressOutput'), /\[FAIL\]/);
    vm.runInContext('startSaveProgress(payload); finishSaveProgress(null)', context);
    assert.match(dom.getValue('#saveProgressOutput'), /\[UNKNOWN\]/);
    assert.match(dom.getValue('#saveProgressOutput'), /may still be configuring/);
    assert.doesNotMatch(dom.getText('#saveProgressSummary'), /completed/);
}

(async () => {
    await testSaveProgress();
    await testSaveWaitsForInterfaceLookup();
    await testConfigureHandler();
    await testConfigureReadbackGuards();
    await testSharedStatusReadbackAndFreshness();
    await testOldCarrierPreview();
    testSaveDispatchesSetup();
    testConfiguredStateRequiresVerifiedOwnership();
    testDisabledSelectionCanBeSaved();
    testSummaryAndAddressMeaning();
    testSettingsPopulationDoesNotActLikeUserEdits();
    console.log('HA DHCP Interface UI behavior checks passed');
})().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
