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

function domFixture() {
    const nodes = Object.create(null);
    const fields = Object.create(null);
    const inputs = [{props: {disabled: true}}, {props: {disabled: false}}];

    function nodeFor(key) {
        if (typeof key === 'object' && key !== null) {
            return key;
        }
        if (!nodes[key]) {
            nodes[key] = {props: {}, value: '', text: '', visible: true, children: []};
        }
        return nodes[key];
    }

    function wrap(key) {
        const node = nodeFor(key);
        return {
            node,
            val(value) {
                if (arguments.length) {
                    node.value = value;
                    return this;
                }
                return node.value;
            },
            prop(name, value) {
                if (arguments.length > 1) {
                    node.props[name] = value;
                    return this;
                }
                return node.props[name];
            },
            text(value) {
                if (arguments.length) {
                    node.text = value;
                    return this;
                }
                return node.text;
            },
            toggle(value) {
                node.visible = value;
                return this;
            },
            show() {
                node.visible = true;
                return this;
            },
            hide() {
                node.visible = false;
                return this;
            },
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

    function field(id) {
        return wrap('field:' + id);
    }

    function setField(id, value, props = {}) {
        const node = nodeFor('field:' + id);
        node.value = value;
        node.props = Object.assign({}, node.props, props);
    }

    function getText(selector) {
        return nodeFor(selector).text;
    }

    function getValue(selector) {
        return nodeFor(selector).value;
    }

    return {$, field, setField, getText, getValue, inputs, nodes, fields};
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

function configureFixture(options = {}) {
    const dom = domFixture();
    dom.setField('dhcphalocal.managed_interface', 'opt7');
    dom.setField('dhcphashared.enabled', '', {checked: options.enabledDraft === undefined ? true : options.enabledDraft});
    dom.setField('dhcphashared.shared_mac', options.mac || '02:00:00:00:00:01');
    dom.nodes['#revision'] = {props: {}, value: 'rev1', text: '', visible: true, children: []};
    dom.nodes['#failbackDelay'] = {props: {}, value: '0', text: '', visible: true, children: []};
    const calls = [];
    const readbacks = [];
    const context = vm.createContext(Object.assign({}, dom, {
        api: '/api/dhcpinterfaceha',
        canConfigureInterface: options.canConfigureInterface !== false,
        canEnableSync: false,
        canWriteSettings: true,
        configureAllowedInterfaces: options.allowed === false ? [] : ['opt7'],
        configureBusy: false,
        settingsBusy: false,
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
        statusGeneration: 0,
        statusInFlight: false,
        selectionGeneration: 0,
        statusRefreshPending: false,
        statusRequestPromise: null,
        statusTimer: null,
        staleTimer: null,
        window: {confirm: () => options.confirm !== false},
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
            if (options.readback) {
                return options.readback(...args);
            }
        }
    }));
    vm.runInContext(between('    function verifiedDetached(', '    function statusIsFresh('), context);
    vm.runInContext(between('    function statusIsFresh(', '    function isConfiguredForForm('), context);
    vm.runInContext(between('    function mappedDeviceNeedsRecovery(', '    function updateMapping('), context);
    vm.runInContext(between('    function detachedEvidenceFresh()', '    function requestStatusOnce('), context);
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
    return {
        result: 'ok',
        managed: {identifier: 'opt7', description: 'LAN', device: deviceName},
        attachment: {
            actual: 'FENCED',
            desired: 'FENCED',
            members: [],
            owned: true,
            carrier: {selected: 'em0', exists: true, mac: '02:11:22:33:44:55'},
            device: {name: 'dhcpha0lagg', exists: true, protocol: 'failover'}
        },
        setup: {pending_assignment: {available: true, state: pendingState}}
    };
}

function readbackFixture(settings, status, options = {}) {
    const dom = domFixture();
    dom.setField('dhcphalocal.managed_interface', options.managed || 'opt7');
    dom.setField('dhcphashared.enabled', '', {checked: options.enabledDraft === undefined ? true : options.enabledDraft});
    dom.setField('dhcphashared.shared_mac', options.currentMac || '02:00:00:00:00:01');
    dom.nodes['#revision'] = {props: {}, value: 'rev1', text: '', visible: true, children: []};
    const updates = [];
    let settingsReads = 0;
    let statusReads = 0;
    const context = vm.createContext(Object.assign({}, dom, {
        api: '/api/dhcpinterfaceha',
        statusData: null,
        statusObservedAt: Date.now(),
        statusTimer: null,
        staleTimer: null,
        configureBusy: false,
        settingsBusy: false,
        formGeneration: options.formGeneration || 0,
        configureRetryReady: false,
        configureRetryGeneration: -1,
        configureOutcomeBlocked: true,
        pendingConfigureOutcome: null,
        savedMapping: {managed: 'opt7', carrier: 'old0'},
        savedEnabled: true,
        localCarrier: 'old0',
        previewDevice: 'old0',
        managedDescription: 'LAN',
        getJson: async () => { settingsReads++; return settings; },
        requestFreshStatusForReadback: async () => {
            statusReads++;
            context.statusData = status;
            context.statusObservedAt = options.observedAt || Date.now();
            return status;
        },
        selectedOption: optionsList => {
            if (!optionsList || typeof optionsList !== 'object') {
                return '';
            }
            return Object.keys(optionsList).find(key => optionsList[key]
                && typeof optionsList[key] === 'object'
                && String(optionsList[key].selected) === '1') || '';
        },
        valueOf: value => value && typeof value === 'object' && Object.prototype.hasOwnProperty.call(value, 'value')
            ? String(value.value) : String(value == null ? '' : value),
        sameMac: (left, right) => String(left && left.value !== undefined ? left.value : left).toLowerCase()
            === String(right && right.value !== undefined ? right.value : right).toLowerCase(),
        verifiedDetached: data => !!data && !!data.attachment
            && ['FENCED', 'UNMANAGED'].includes(data.attachment.actual)
            && Array.isArray(data.attachment.members) && data.attachment.members.length === 0
            && (data.attachment.device.exists === false || data.attachment.owned === true),
        updateMapping: () => updates.push('mapping'),
        updateMacSuggestion: () => updates.push('mac'),
        updateActions: () => {},
        refreshStatus: () => {}
    }));
    vm.runInContext(between('    function statusIsFresh(', '    function isConfiguredForForm('), context);
    vm.runInContext(between('    function safelyRetryableSetup(', '    function mappedDeviceNeedsRecovery('), context);
    vm.runInContext(between('    function blockConfigureOutcome(', '    async function configureSelectedLagg('), context);
    return {context, dom, updates, readCounts: () => ({settingsReads, statusReads})};
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

async function testConfigureHandler() {
    const cancelled = configureFixture({confirm: false});
    await vm.runInContext('configureSelectedLagg()', cancelled.context);
    assert.equal(cancelled.calls.length, 0, 'cancel sends no mutation');

    const disabledSaved = configureFixture({savedEnabled: true, enabledDraft: false});
    await vm.runInContext('configureSelectedLagg()', disabledSaved.context);
    assert.equal(disabledSaved.calls.length, 0, 'an unchecked draft cannot bypass saved Enable state');

    const denied = configureFixture({allowed: false});
    await vm.runInContext('configureSelectedLagg()', denied.context);
    assert.equal(denied.calls.length, 0, 'missing per-interface native assignment permission blocks Configure');

    const unknownOutcome = configureFixture();
    unknownOutcome.context.configureOutcomeBlocked = true;
    unknownOutcome.context.pendingConfigureOutcome = {managed: 'opt7'};
    vm.runInContext('updateActions()', unknownOutcome.context);
    assert.equal(unknownOutcome.dom.nodes['#configureLagg'].props.disabled, true,
        'an uncertain prior Configure disables the primary action despite fresh original-device evidence');
    assert.equal(unknownOutcome.dom.nodes['#recheckConfigure'].visible, true,
        'uncertain Configure exposes a read-only Recheck action');
    await vm.runInContext('configureSelectedLagg()', unknownOutcome.context);
    assert.equal(unknownOutcome.calls.length, 0,
        'the actual Configure handler cannot bypass the uncertain-outcome latch with a fresh original-device preview');

    for (const pendingState of ['conflict', 'unknown', 'selected_relink']) {
        const queueBlocked = configureFixture();
        queueBlocked.context.statusData.setup.pending_assignment = {
            available: pendingState !== 'unknown', state: pendingState
        };
        vm.runInContext('updateActions()', queueBlocked.context);
        assert.equal(queueBlocked.dom.nodes['#configureLagg'].props.disabled, true);
        assert.equal(queueBlocked.dom.nodes['#recheckConfigure'].visible, pendingState === 'selected_relink',
            'a pending saved relink exposes readback even after request history is lost');
        await vm.runInContext('configureSelectedLagg()', queueBlocked.context);
        assert.equal(queueBlocked.calls.length, 0,
            pendingState + ' pending assignment state blocks ordinary Configure without verified retry readback');
    }

    let finishPost;
    const duplicate = configureFixture({
        post: () => new Promise(resolve => { finishPost = resolve; })
    });
    const first = vm.runInContext('configureSelectedLagg()', duplicate.context);
    await vm.runInContext('configureSelectedLagg()', duplicate.context);
    assert.equal(duplicate.calls.length, 1, 'a second click while Configure is in flight is ignored');
    const submitted = duplicate.calls[0];
    assert.equal(submitted.url, '/api/dhcpinterfaceha/settings/configure');
    assert.deepEqual(submitted.payload, {
        dhcphashared: {enabled: '0', shared_mac: '02:00:00:00:00:01', failback_delay: '0'},
        dhcphalocal: {managed_interface: 'opt7', carrier: 'em0'},
        revision: 'rev1'
    }, 'Configure submits both complete roots and the current revision, forcing Enable off');
    finishPost({result: 'saved', saved: true, applied: true, assignment_verified: true, setup_stage: 'verified', revision: 'rev2'});
    await first;
    assert.equal(duplicate.dom.field('dhcphashared.enabled').prop('checked'), false,
        'a saved Configure result updates the visible Enable checkbox to match the forced disabled value');
    assert.equal(duplicate.readbacks.length, 1);

    const partial = configureFixture({
        result: {result: 'failed', saved: true, applied: false, assignment_verified: false, setup_stage: 'settings_saved'}
    });
    await vm.runInContext('configureSelectedLagg()', partial.context);
    assert.equal(partial.calls.length, 1);
    assert.equal(partial.readbacks.length, 1, 'known partial save refreshes saved revision and assignment readback');
    assert.equal(partial.dom.field('dhcphashared.enabled').prop('checked'), false);

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

    const timedOut = configureFixture({timeout: true});
    await vm.runInContext('configureSelectedLagg()', timedOut.context);
    assert.equal(timedOut.calls.length, 1, 'timeout does not blindly replay Configure');
    assert.equal(timedOut.readbacks.length, 1, 'timeout performs read-only settings and status readback');
}

async function testConfigureReadbackGuards() {
    const submitted = {
        dhcphashared: {enabled: '0', shared_mac: '02:00:00:00:00:01', failback_delay: '0'},
        dhcphalocal: {managed_interface: 'opt7', carrier: 'em0'},
        revision: 'rev1'
    };
    const complete = readbackFixture(settingsReadback(), setupReadbackStatus('clear'));
    complete.context.testPayload = submitted;
    await vm.runInContext('readConfigureOutcome("opt7", testPayload, 0, "em0", false)', complete.context);
    assert.equal(complete.dom.getValue('#revision'), 'rev2', 'matching saved intent advances the revision for a safe retry');
    assert.equal(complete.context.savedMapping.carrier, 'em0');
    assert.equal(complete.context.savedEnabled, false);
    assert.equal(complete.context.configureRetryReady, false, 'verified mapping does not offer Retry');
    assert.equal(complete.context.configureOutcomeBlocked, false,
        'complete fresh settings and assignment readback clears the uncertain-outcome latch');
    assert.match(complete.dom.getText('#setupResult'), /Readback verifies/);

    const retryable = readbackFixture(settingsReadback(), setupReadbackStatus('selected_relink', 'dhcpha0lagg'));
    retryable.context.testPayload = submitted;
    await vm.runInContext('readConfigureOutcome("opt7", testPayload, 0, "em0", false)', retryable.context);
    assert.equal(retryable.context.configureRetryReady, true,
        'a fresh detached owned device plus selected-relink pending state can resume guarded setup');
    assert.equal(retryable.context.configureOutcomeBlocked, false,
        'verified safe retry readback releases the latch and offers the guarded retry');

    for (const pendingState of ['conflict', 'unknown']) {
        const blocked = readbackFixture(settingsReadback(), setupReadbackStatus(pendingState, 'dhcpha0lagg'));
        blocked.context.testPayload = submitted;
        await vm.runInContext('readConfigureOutcome("opt7", testPayload, 0, "em0", false)', blocked.context);
        assert.equal(blocked.context.configureRetryReady, false, pendingState + ' pending queue state blocks Retry');
        assert.equal(blocked.context.configureOutcomeBlocked, true,
            pendingState + ' pending queue state keeps Configure blocked pending a complete readback');
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

    const anotherWriter = readbackFixture(settingsReadback({
        revision: 'rev-other',
        dhcphashared: {
            enabled: {value: '0'},
            shared_mac: {value: '02:00:00:00:00:99'},
            failback_delay: {value: '0'}
        }
    }), setupReadbackStatus('clear'));
    anotherWriter.context.testPayload = submitted;
    await vm.runInContext('readConfigureOutcome("opt7", testPayload, 0, "em0", false)', anotherWriter.context);
    assert.equal(anotherWriter.dom.getValue('#revision'), 'rev1',
        'a differing readback does not advance the form revision past another writer');
    assert.equal(anotherWriter.context.configureRetryReady, false);

    const changedCarrier = settingsReadback({
        revision: 'rev-other',
        dhcphalocal: {
            managed_interface: {opt7: {selected: '1'}},
            carrier: {value: 'em9'}
        }
    });
    const carrierWriter = readbackFixture(changedCarrier, setupReadbackStatus('clear'));
    carrierWriter.context.testPayload = submitted;
    await vm.runInContext('readConfigureOutcome("opt7", testPayload, 0, "em0", false)', carrierWriter.context);
    assert.equal(carrierWriter.dom.getValue('#revision'), 'rev1',
        'a readback with a different derived carrier cannot advance the submitted revision');

    const dirty = readbackFixture(settingsReadback(), setupReadbackStatus('clear'), {
        formGeneration: 2,
        currentMac: '02:00:00:00:00:99'
    });
    dirty.context.testPayload = submitted;
    await vm.runInContext('readConfigureOutcome("opt7", testPayload, 1, "em0", false)', dirty.context);
    assert.equal(dirty.dom.getValue('#revision'), 'rev1', 'a response for an older form generation cannot advance revision');
    assert.equal(dirty.dom.field('dhcphashared.shared_mac').val(), '02:00:00:00:00:99',
        'readback preserves values edited after submission');
    assert.equal(dirty.context.configureRetryReady, false);

    const stale = readbackFixture(settingsReadback(), setupReadbackStatus('clear'), {observedAt: Date.now() - 16000});
    stale.context.testPayload = submitted;
    await vm.runInContext('readConfigureOutcome("opt7", testPayload, 0, "em0", false)', stale.context);
    assert.equal(stale.context.configureRetryReady, false, 'stale assignment evidence cannot claim a safe retry');
}

function testConfiguredBadgeRequiresVerifiedOwnership() {
    const dom = domFixture();
    dom.setField('dhcphalocal.managed_interface', 'opt7');
    const context = vm.createContext(Object.assign({}, dom, {
        statusData: null,
        statusObservedAt: Date.now(),
        savedMapping: {managed: 'opt7', carrier: 'em0'}
    }));
    vm.runInContext(between('    function statusIsFresh(', '    function isConfiguredForForm('), context);
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
        'a native mapping alone cannot show Interface configured for a foreign device');
    context.statusData.attachment.owned = true;
    context.statusData.attachment.device.exists = false;
    assert.equal(vm.runInContext('isConfiguredForForm()', context), false,
        'a native mapping alone cannot show Interface configured when the owned device is absent');
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
    const context = vm.createContext({
        Date: {now: () => clock.now},
        Error,
        api: '/api/dhcpinterfaceha',
        statusGeneration: 0,
        selectionGeneration: 0,
        statusInFlight: false,
        statusRequestPromise: null,
        statusRefreshPending: false,
        statusData: null,
        statusObservedAt: 0,
        statusTimer: null,
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
    });
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

async function testOldCarrierAndMacSuggestions() {
    const requests = [];
    const dom = domFixture();
    const context = vm.createContext(Object.assign({}, dom, {
        api: '/api/dhcpinterfaceha',
        selectionGeneration: 0,
        statusGeneration: 0,
        statusInFlight: false,
        statusRefreshPending: false,
        localCarrier: '',
        previewDevice: '',
        managedDescription: '',
        statusData: null,
        statusObservedAt: 0,
        statusGeneration: 0,
        statusRequestPromise: null,
        statusTimer: null,
        staleTimer: null,
        savedMapping: {managed: 'opt7', carrier: 'em0'},
        canWriteSettings: true,
        configureBusy: false,
        settingsBusy: false,
        statusIsFresh: () => false,
        updateMacSuggestion: data => { context.lastSuggestion = data.interface; },
        updateMapping: () => {},
        updateActions: () => {},
        getJson: () => {
            const request = deferredAjax(() => {});
            requests.push(request);
            return request;
        }
    }));
    vm.runInContext(between('    function invalidateStatus(', '    function verifiedDetached('), context);
    vm.runInContext(between('    function loadCarrierPreview(', '    function checkLabel('), context);
    vm.runInContext('loadCarrierPreview("opt7", "em0"); loadCarrierPreview("opt8", "");', context);
    requests[1].resolve({interface: 'opt8', managed: {current_device: 'em1', description: 'WAN'}, errors: {}});
    requests[0].resolve({interface: 'opt7', managed: {current_device: 'em0', description: 'LAN'}, errors: {}});
    assert.equal(context.managedDescription, 'WAN');
    assert.equal(context.previewDevice, 'em1');
    assert.equal(context.lastSuggestion, 'opt8', 'a late preview for the old interface cannot replace the current preview');

    const macDom = domFixture();
    macDom.setField('dhcphalocal.managed_interface', 'opt7');
    const macContext = vm.createContext(Object.assign({}, macDom, {
        savedMapping: {managed: 'opt7', carrier: 'em0'},
        statusData: null,
        statusObservedAt: 0,
        canWriteSettings: true,
        configureBusy: false,
        settingsBusy: false
    }));
    vm.runInContext(between('    function usableMac(', '    function selectedOption('), macContext);
    vm.runInContext(between('    function statusIsFresh(', '    function isConfiguredForForm('), macContext);
    vm.runInContext(between('    function updateMacSuggestion(', '    function loadCarrierPreview('), macContext);
    const laggMac = '02:aa:bb:cc:dd:ee';
    vm.runInContext('updateMacSuggestion({interface:"opt7", managed:{current_device:"dhcpha0lagg", effective_mac_suggestion:"' + laggMac + '"}})', macContext);
    assert.equal(macDom.getText('#currentMacSuggestion'), 'Unavailable',
        'a LAGG address is never used as a carrier MAC before fresh saved-carrier readback');
    macContext.statusData = {
        managed: {identifier: 'opt7'},
        attachment: {carrier: {selected: 'em0', exists: true, mac: '02:11:22:33:44:55'}}
    };
    macContext.statusObservedAt = Date.now();
    vm.runInContext('updateMacSuggestion({interface:"opt7", managed:{current_device:"dhcpha0lagg", effective_mac_suggestion:"' + laggMac + '"}})', macContext);
    assert.equal(macDom.getText('#currentMacSuggestion'), '02:11:22:33:44:55',
        'a fresh observation uses the saved original carrier MAC');
    macDom.setField('dhcphalocal.managed_interface', 'opt8');
    vm.runInContext('updateMacSuggestion({interface:"opt7", managed:{current_device:"dhcpha0lagg", effective_mac_suggestion:"' + laggMac + '"}})', macContext);
    assert.equal(macDom.getText('#currentMacSuggestion'), '02:11:22:33:44:55',
        'a status response for the saved old selection cannot overwrite a dirty current selection preview');
}

function testSummaryAndAddressMeaning() {
    const dom = domFixture();
    const context = vm.createContext(Object.assign({}, dom, {
        statusObservedAt: Date.now(),
        staleTimer: null,
        clearTimeout: () => {},
        setTimeout: () => 1,
        markStatusStale: () => {},
        statusData: null
    }));
    vm.runInContext(between('    function verifiedDetached(', '    function isConfiguredForForm('), context);
    vm.runInContext(between('    function updateAddressDetail(', '    function renderStatusUnavailable('), context);

    const addressBase = {
        attachment: {actual: 'FENCED', members: [], owned: true, device: {exists: true}},
        controller: {state: 'STANDBY'},
        connection: {ipv4: {available: true, address: null}}
    };
    assert.equal(vm.runInContext('updateAddressDetail(addressFixture)', Object.assign(context, {addressFixture: addressBase})),
        'Not expected while the interface is disconnected', 'verified standby detachment explains the absent address');
    addressBase.controller.state = 'ACTIVE';
    assert.equal(vm.runInContext('updateAddressDetail(addressFixture)', context), 'Waiting for DHCP',
        'only an available address lookup on an active node reports DHCP wait');
    addressBase.connection.ipv4.available = false;
    assert.equal(vm.runInContext('updateAddressDetail(addressFixture)', context), 'IPv4 address unavailable',
        'failed address observation stays unknown rather than claiming DHCP wait');

    function summaryFixture(state, controllerState, actual, role, reasonCode) {
        return {
            summary: {state, reason_code: reasonCode || ''},
            readiness: [{code: reasonCode || '', message: 'Observed local condition.'}],
            controller: {state: controllerState, reason_code: reasonCode || '', reason: 'controller evidence'},
            carp: {role, maintenance: false},
            managed: {identifier: 'opt7', description: 'LAN', device: actual === 'ATTACHED' ? 'dhcpha0lagg' : 'em0'},
            attachment: {
                actual, desired: actual, members: [],
                owned: actual !== 'UNVERIFIED',
                device: {exists: true},
                configured_shared_mac: '02:00:00:00:00:01'
            },
            connection: {ipv4: {available: true, address: null}},
            local: {hostname: 'fw1'},
            collected_at: '2026-09-27T00:00:00Z',
            result: 'ok'
        };
    }

    const disabled = summaryFixture('needs_attention', 'DISABLED', 'FENCED', 'MASTER', 'failback_policy');
    vm.runInContext('renderSummary(summaryFixture)', Object.assign(context, {summaryFixture: disabled}));
    assert.equal(dom.getText('#summaryState'), 'Disabled · Interface disconnected',
        'a verified disabled state takes precedence over an unrelated readiness issue');

    const active = summaryFixture('ready', 'ACTIVE', 'ATTACHED', 'MASTER');
    vm.runInContext('renderSummary(summaryFixture)', Object.assign(context, {summaryFixture: active}));
    assert.equal(dom.getText('#summaryState'), 'Active · CARP MASTER · Waiting for DHCP');

    const unsafe = summaryFixture('needs_attention', 'DISABLED', 'UNVERIFIED', 'MASTER', 'attachment_unverified');
    vm.runInContext('renderSummary(summaryFixture)', Object.assign(context, {summaryFixture: unsafe}));
    assert.equal(dom.getText('#summaryState'), 'Needs attention',
        'unverified attachment/fencing takes precedence over a normal disabled label');

    const setup = summaryFixture('not_configured', 'DISABLED', 'FENCED', 'MASTER', 'interface_setup_required');
    vm.runInContext('renderSummary(summaryFixture)', Object.assign(context, {summaryFixture: setup}));
    assert.match(dom.getText('#summaryDetails'), /Configure the selected interface/);
    setup.managed.identifier = '';
    vm.runInContext('renderSummary(summaryFixture)', Object.assign(context, {summaryFixture: setup}));
    assert.match(dom.getText('#summaryDetails'), /Select a managed logical interface/);
}

function testIssueFirstDiagnostics() {
    const dom = domFixture();
    dom.$('#showAllChecks').prop('checked', false);
    const context = vm.createContext(Object.assign({}, dom, {
        checkLabel: status => status === 'pass' ? 'Passing' : (status === 'fail' ? 'Needs attention' : 'Unknown'),
        actionControl: () => null,
        nativeLinks: {},
        showTab: () => {}
    }));
    vm.runInContext(between('    function renderReadiness(', '    function renderSourceErrors('), context);
    const records = [
        {code: 'blocking', status: 'fail', relevant: true, message: 'block'},
        {code: 'healthy', status: 'pass', relevant: true, message: 'pass'},
        {code: 'context', status: 'fail', relevant: false, message: 'context'},
        {code: 'unknown', status: 'unknown', relevant: true, message: 'unknown'}
    ];
    context.readinessFixture = records;
    vm.runInContext('renderReadiness(readinessFixture)', context);
    assert.equal(dom.nodes['#diagnosticsReadinessList'].children.length, 2,
        'default Diagnostics shows only non-passing relevant checks');
    dom.$('#showAllChecks').prop('checked', true);
    vm.runInContext('renderReadiness(readinessFixture)', context);
    assert.equal(dom.nodes['#diagnosticsReadinessList'].children.length, 4,
        'Show all checks restores every check including passing and irrelevant context');
}

function testSenderGatesSyncAction() {
    const dom = domFixture();
    const context = vm.createContext(Object.assign({}, dom, {canEnableSync: true}));
    vm.runInContext(between('    function updateSyncStatus(', '    function markStatusStale('), context);
    vm.runInContext('updateSyncStatus({sender_configured:false, plugin_settings_sync:false})', context);
    assert.equal(dom.nodes['#enableSyncBlock'].visible, true);
    assert.equal(dom.nodes['#syncNoSender'].visible, true);
    assert.equal(dom.nodes['#enableSync'].visible, false,
        'a receiver without an outbound destination sees neutral context, never an include action');
    vm.runInContext('updateSyncStatus({sender_configured:true, plugin_settings_sync:false})', context);
    assert.equal(dom.nodes['#syncMissing'].visible, true);
    assert.equal(dom.nodes['#enableSync'].visible, true);
    vm.runInContext('updateSyncStatus({sender_configured:true, plugin_settings_sync:true})', context);
    assert.equal(dom.nodes['#syncSelected'].visible, true);
    assert.equal(dom.nodes['#enableSync'].visible, false,
        'already-included membership is reported without a repeat action');
}

(async () => {
    await testConfigureHandler();
    await testConfigureReadbackGuards();
    await testSharedStatusReadbackAndFreshness();
    await testOldCarrierAndMacSuggestions();
    testConfiguredBadgeRequiresVerifiedOwnership();
    testSummaryAndAddressMeaning();
    testIssueFirstDiagnostics();
    testSenderGatesSyncAction();
    console.log('DHCP Interface HA UI behavior checks passed');
})().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
