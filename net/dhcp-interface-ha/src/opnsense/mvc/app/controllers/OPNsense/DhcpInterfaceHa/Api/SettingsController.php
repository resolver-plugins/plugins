<?php

namespace OPNsense\DhcpInterfaceHa\Api;

use OPNsense\Base\ApiControllerBase;
use OPNsense\Core\ACL;
use OPNsense\Core\Backend;
use OPNsense\Core\Config;
use OPNsense\Core\Hasync;
use OPNsense\Core\Syslog;
use OPNsense\DhcpInterfaceHa\Local;
use OPNsense\DhcpInterfaceHa\NativeReceiveMode;
use OPNsense\DhcpInterfaceHa\Shared;

class SettingsController extends ApiControllerBase
{
    private const SHARED_FIELDS = ['enabled', 'shared_mac', 'failback_delay'];
    private const LOCAL_FIELDS = ['managed_interface', 'carrier'];
    private $eventLogger = null;

    private $saveProgress = null;

    // One bounded, short-lived transcript per account. A request ID prevents
    // overlapping tabs from displaying another save. It is observation only:
    // cache failures must never change configuration or authorize retries.
    private function progressPath()
    {
        return sys_get_temp_dir() . '/dhcpha-save-' . hash('sha256', $this->getUserName()) . '.json';
    }

    public function progressAction($id = '')
    {
        if (!$this->request->isGet() || !is_string($id) || !preg_match('/^[a-f0-9]{32}$/D', $id)) {
            return ['state' => 'unavailable'];
        }
        $path = $this->progressPath();
        $data = is_file($path) && !is_link($path)
            ? json_decode((string)@file_get_contents($path, false, null, 0, 65536), true) : null;
        if (!is_array($data) || ($data['id'] ?? '') !== $id || ($data['updated'] ?? 0) < time() - 900) {
            return ['state' => 'unavailable'];
        }
        return $data;
    }

    private function publishProgress()
    {
        if (empty($this->saveProgress['id'])) {
            return;
        }
        $temporary = false;
        try {
            $this->saveProgress['updated'] = time();
            // tempnam creates a private 0600 file; rename gives readers a complete
            // snapshot and never follows a pre-existing target symlink.
            $temporary = @tempnam(sys_get_temp_dir(), 'dhcpha-save-');
            if ($temporary !== false && @file_put_contents($temporary, json_encode($this->saveProgress)) !== false) {
                @rename($temporary, $this->progressPath());
            }
        } catch (\Throwable $exception) {
            // The ordinary API response still carries the complete transcript.
        } finally {
            if ($temporary !== false && is_file($temporary)) {
                @unlink($temporary);
            }
        }
    }

    private function progressStep($message)
    {
        if ($this->saveProgress === null) {
            return;
        }
        $last = count($this->saveProgress['steps']) - 1;
        if ($last >= 0) {
            $this->saveProgress['steps'][$last]['state'] = 'success';
        }
        $this->saveProgress['steps'][] = ['state' => 'running', 'message' => $message];
        $this->publishProgress();
    }

    private function finishProgress($state, $message)
    {
        $last = count($this->saveProgress['steps']) - 1;
        $this->saveProgress['steps'][$last]['state'] = $state;
        if ($message !== '') {
            $this->saveProgress['steps'][$last]['message'] .= ' — ' . mb_substr($message, 0, 1600);
        }
        $this->saveProgress['state'] = $state;
        $this->publishProgress();
    }

    private function withSaveProgress($operation)
    {
        if (!$this->request->isPost()) {
            return ['result' => 'failed', 'saved' => false, 'applied' => false, 'error' => gettext('POST required.')];
        }
        $this->throwReadOnly();
        $id = $this->request->getPost('progress_id', null, '');
        if (!is_string($id) || ($id !== '' && !preg_match('/^[a-f0-9]{32}$/D', $id))) {
            return ['result' => 'failed', 'saved' => false, 'applied' => false, 'error' => gettext('Invalid save request ID. Reload this page and retry.')];
        }
        $this->saveProgress = ['id' => $id, 'state' => 'running', 'steps' => []];
        $this->progressStep(gettext('Check settings and current interface state'));
        try {
            $result = $operation();
        } catch (\Throwable $exception) {
            $this->finishProgress('unknown', gettext('The request stopped unexpectedly. Recheck the saved settings before retrying; see the system log for details.'));
            throw $exception;
        }
        $success = ($result['result'] ?? '') === 'unchanged'
            || (($result['result'] ?? '') === 'saved' && ($result['applied'] ?? null) === true);
        $state = $success ? 'success' : 'failed';
        $saveUnknown = array_key_exists('saved', $result) && $result['saved'] === null;
        $assignmentUnknown = array_key_exists('assignment_verified', $result) && $result['assignment_verified'] === null;
        $applyUnknown = !empty($result['saved']) && (($result['applied'] ?? null) === null || $assignmentUnknown);
        if (!$success && ($saveUnknown || $applyUnknown)) {
            $state = 'unknown';
        }
        $message = $result['error'] ?? '';
        if (!empty($result['validations'])) {
            $message .= ' ' . implode(' ', array_unique(array_values($result['validations'])));
        }
        if (($result['result'] ?? '') === 'unchanged') {
            $message = gettext('Nothing changed; no configuration or interface actions were needed.');
        } elseif ($success) {
            $this->progressStep(gettext('Check interface readiness'));
            $status = $result['status'] ?? [];
            $runtimeState = $status['state'] ?? 'UNKNOWN';
            $message = $status['reason'] ?? gettext('Current runtime status is unavailable.');
            if ($runtimeState === 'FAULT') {
                $state = 'failed';
                $message .= ' ' . gettext('Settings were saved, but the interface is not ready. Review the plugin Log before retrying.');
            } elseif (!empty($result['cleared']) && ($status['enabled'] ?? null) === false
                && ($status['actual_attachment'] ?? '') === 'UNMANAGED') {
                $message = gettext('Plugin disabled and interface configuration cleared. The shared MAC was retained.');
            } elseif (in_array($runtimeState, ['UNKNOWN', 'SETUP_INCOMPLETE'], true)) {
                $state = 'unknown';
                $message .= ' ' . gettext('Settings were saved; current readiness could not be confirmed.');
            }
        }
        $this->finishProgress($state, trim($message));
        $result['progress'] = $this->saveProgress;
        return $result;
    }

    public function setAction()
    {
        return $this->withSaveProgress(fn() => $this->setSettings());
    }

    public function configureAction()
    {
        return $this->withSaveProgress(fn() => $this->configureInterface());
    }

    public function getAction()
    {
        if (!$this->request->isGet()) {
            return ['error' => gettext('GET required.')];
        }

        $shared = new Shared();
        $local = new Local();
        $config = Config::getInstance()->object();
        $sharedNodes = $shared->getNodes();
        $localNodes = $local->getNodes();
        $managedName = trim((string)$local->managed_interface);
        $localNodes['managed_interface'] = [
            '' => ['value' => gettext('Disabled'), 'selected' => $managedName === '' ? 1 : 0],
        ];
        $managedChoices = [];
        $interfaces = $config->interfaces ?? null;
        if ($interfaces !== null) {
            foreach ($interfaces->children() as $name => $interface) {
                $description = !empty((string)$interface->descr) ? (string)$interface->descr : strtoupper($name);
                $reason = self::managedInterfaceReason($config, $name, $interface);
                $label = sprintf('%s (%s)', $description, $name);
                if ($reason !== null) {
                    $label .= sprintf(gettext(' — setup requirement: %s'), $reason);
                }
                $localNodes['managed_interface'][$name] = [
                    'value' => $label,
                    'selected' => $name === $managedName ? 1 : 0,
                    'data' => ['reason' => $reason ?? '', 'eligible' => $reason === null ? '1' : '0'],
                ];
                $managedChoices[$name] = [
                    'description' => $description,
                    'identifier' => (string)$name,
                    'reason' => $reason,
                    'eligible' => $reason === null,
                ];
            }
        }
        if ($managedName !== '' && empty($config->interfaces->$managedName)) {
            $localNodes['managed_interface'][$managedName] = [
                'value' => sprintf(gettext('%s (saved selection unavailable)'), $managedName),
                'selected' => 1,
                'data' => ['reason' => gettext('interface no longer exists'), 'eligible' => '0'],
            ];
            $managedChoices[$managedName] = [
                'description' => $managedName,
                'identifier' => $managedName,
                'reason' => gettext('interface no longer exists'),
                'eligible' => false,
            ];
        }
        return [
            'dhcphashared' => $sharedNodes,
            'dhcphalocal' => $localNodes,
            'managed_interface_value' => $managedName,
            'managed_choices' => $managedChoices,
            'local_hostname' => gethostname(),
            'revision' => self::revision($shared, $local),
        ];
    }

    private function setSettings()
    {
        $sharedInput = $this->request->getPost('dhcphashared');
        $localInput = $this->request->getPost('dhcphalocal');
        $revisionInput = $this->request->getPost('revision', null, '');
        $revision = is_string($revisionInput) ? $revisionInput : '';
        if (!self::hasFields($sharedInput, self::SHARED_FIELDS) || !self::hasFields($localInput, self::LOCAL_FIELDS) || $revision === '') {
            return [
                'result' => 'failed',
                'saved' => false,
                'applied' => null,
                'error' => gettext('Submit both complete settings sections and their revision.'),
            ];
        }
        if (trim((string)$localInput['managed_interface']) === '') {
            return $this->disableAndClear($sharedInput, $revision);
        }

        $saved = $this->saveSettings($sharedInput, $localInput, $revision, unknownSaveOutcome: false, skipUnchanged: true);
        if ($saved['result'] !== 'saved') {
            return $saved;
        }

        if (!empty($saved['receive_mode_only'])) {
            $saved['applied'] = true;
            $saved['status'] = self::readRuntimeStatus();
            $this->logEvent('info', 'receive_mode_configured', ['outcome' => 'saved']);
            return $saved;
        }

        if ($saved['changed']) {
            $this->logEvent('info', 'settings_saved', [
                'operation' => 'settings_set',
                'outcome' => 'saved',
            ]);
        }
        $this->progressStep(gettext('Apply saved settings to the controller'));
        $apply = self::applySavedSettings();
        $response = [
            'result' => 'saved',
            'saved' => true,
            'applied' => $apply['applied'],
            'error' => $apply['error'],
            'revision' => $saved['revision'],
        ];
        if (!empty($apply['status'])) {
            $response['status'] = $apply['status'];
        }
        return $response;
    }

    /**
     * Disable with the saved identity first, then clear it only after apply and
     * the ordinary identity-change validation verify a detached device.
     */
    private function disableAndClear(array $sharedInput, $revision)
    {
        $currentShared = new Shared();
        $currentLocal = new Local();
        $managedName = trim((string)$currentLocal->managed_interface);
        $carrier = trim((string)$currentLocal->carrier);
        $targetShared = [
            'enabled' => '0',
            'shared_mac' => (string)$sharedInput['shared_mac'],
            'failback_delay' => '0',
        ];
        $targetLocal = ['managed_interface' => '', 'carrier' => ''];

        if (empty((string)$currentShared->enabled) && $managedName === '' && $carrier === '') {
            $unchanged = $this->saveSettings($targetShared, $targetLocal, $revision, unknownSaveOutcome: false, validateOnly: true, skipUnchanged: true);
            if ($unchanged['result'] !== 'valid') {
                return $unchanged;
            }
        }

        if (!empty((string)$currentShared->enabled)) {
            $this->progressStep(gettext('Disable HA DHCP Interface and detach its carrier'));
            $current = self::canonical($currentShared, $currentLocal);
            $disableShared = $current['dhcphashared'];
            $disableShared['enabled'] = '0';
            $disabled = $this->saveSettings(
                $disableShared,
                $current['dhcphalocal'],
                $revision,
                unknownSaveOutcome: false
            );
            if ($disabled['result'] !== 'saved') {
                return $disabled;
            }
            if ($disabled['changed']) {
                $this->logEvent('info', 'settings_saved', [
                    'operation' => 'settings_disable_for_clear',
                    'outcome' => 'saved',
                ]);
            }
            $apply = self::applySavedSettings();
            if ($apply['applied'] !== true) {
                $response = [
                    'result' => 'staged',
                    'saved' => true,
                    'applied' => $apply['applied'],
                    'cleared' => false,
                    'revision' => $disabled['revision'],
                    'error' => gettext('HA DHCP Interface was disabled, but its interface selection was retained because detachment could not be verified. Refresh status, then save Disabled again.'),
                ];
                if (!empty($apply['status'])) {
                    $response['status'] = $apply['status'];
                }
                return $response;
            }
            $revision = $disabled['revision'];
        }

        if ($managedName !== '' && $carrier !== '') {
            $this->progressStep(gettext('Restore the original interface assignment'));
            $restored = $this->restoreNativeAssignment($managedName, $carrier, $revision);
            if (empty($restored['restored'])) {
                return [
                    'result' => 'staged',
                    'saved' => true,
                    'applied' => $restored['applied'] ?? false,
                    'cleared' => false,
                    'revision' => $revision,
                    'error' => $restored['error'],
                ];
            }
            $this->logEvent('info', 'assignment_restored', [
                'operation' => 'settings_clear',
                'interface' => $managedName,
                'carrier' => $carrier,
                'outcome' => 'verified',
            ]);
        }

        $this->progressStep(gettext('Clear plugin configuration and retain the shared MAC'));
        $cleared = $this->saveSettings($targetShared, $targetLocal, $revision, unknownSaveOutcome: false);
        if ($cleared['result'] !== 'saved') {
            if (!empty((string)$currentShared->enabled)) {
                $cleared['result'] = 'staged';
                $cleared['saved'] = true;
                $cleared['cleared'] = false;
                $cleared['revision'] = $revision;
                $cleared['error'] = gettext('HA DHCP Interface was disabled, but its interface selection was retained because detached state could not be verified. Refresh status, then save Disabled again.');
            }
            return $cleared;
        }
        if ($cleared['changed']) {
            $this->logEvent('info', 'settings_saved', [
                'operation' => 'settings_clear',
                'outcome' => 'saved',
            ]);
        }
        $this->progressStep(gettext('Apply saved settings to the controller'));
        $apply = self::applySavedSettings();
        $response = [
            'result' => 'saved',
            'saved' => true,
            'applied' => $apply['applied'],
            'cleared' => true,
            'error' => $apply['error'],
            'revision' => $cleared['revision'],
        ];
        if (!empty($apply['status'])) {
            $response['status'] = $apply['status'];
        }
        return $response;
    }

    /** Restore the logical interface through OPNsense's native assignment path. */
    private function restoreNativeAssignment($managedName, $carrier, $revision)
    {
        if (!preg_match('/^[A-Za-z][A-Za-z0-9_.-]{0,14}$/', $managedName)
            || !preg_match('/^[A-Za-z][A-Za-z0-9_.-]{0,14}$/', $carrier)) {
            return ['restored' => false, 'applied' => false, 'error' => gettext('The saved interface identity is invalid; plugin settings were retained.')];
        }
        if (!$this->nativeRouteAllowed('/api/interfaces/assignment/set_item/' . $managedName)
            || !$this->nativeRouteAllowed('/api/interfaces/assignment/reconfigure')) {
            return ['restored' => false, 'applied' => false, 'error' => gettext('Native interface assignment write permission is required to remove this configuration.')];
        }
        // Native assign-opts is cached for 30 seconds. Disabling releases our
        // carrier reservation; refresh that cache before native validation.
        try {
            $options = json_decode((new Backend())->configdRun('!interface list assign-opts', false, 10, 2), true);
            if (!self::isInterfaceMap($options)) {
                return ['restored' => false, 'applied' => false, 'error' => gettext('Fresh native assignment options are unavailable; plugin settings were retained.')];
            }
        } catch (\Throwable $exception) {
            return ['restored' => false, 'applied' => false, 'error' => gettext('Native assignment options could not be refreshed; plugin settings were retained.')];
        }
        $config = Config::getInstance();
        $config->lock(true);
        try {
            $shared = new Shared();
            $local = new Local();
            $nativeConfig = $config->object();
            $assignments = self::assignmentMap($nativeConfig);
            $bridge = new \OPNsense\DhcpInterfaceHa\SetupAssignmentBridge();
            $pending = $bridge->pendingChanges();
            if (!hash_equals(self::revision($shared, $local), $revision)
                || !empty((string)$shared->enabled)
                || (string)$local->managed_interface !== $managedName
                || trim((string)$local->carrier) !== $carrier) {
                return ['restored' => false, 'applied' => false, 'error' => gettext('Saved settings changed during removal; reload before retrying.')];
            }
            if (!is_array($pending) || !self::isAssignmentMap($assignments)) {
                return ['restored' => false, 'applied' => false, 'error' => gettext('Native assignment state cannot be inspected safely; plugin settings were retained.')];
            }
            $currentDevice = $assignments[$managedName] ?? null;
            if ($currentDevice === $carrier && $pending === []) {
                return ['restored' => true, 'applied' => true, 'error' => null];
            }
            if ($currentDevice !== 'dhcpha0lagg') {
                return ['restored' => false, 'applied' => false, 'error' => gettext('The managed interface assignment changed; plugin settings were retained for review.')];
            }
            foreach ($assignments as $name => $device) {
                if ($name !== $managedName && $device === $carrier) {
                    return ['restored' => false, 'applied' => false, 'error' => gettext('The saved carrier is assigned elsewhere; plugin settings were retained.')];
                }
            }
            if ($pending !== [] && !self::isSelectedPendingRelink($pending, $managedName, $carrier)) {
                return ['restored' => false, 'applied' => false, 'error' => gettext('Other native assignment changes are pending; apply or discard them before retrying.')];
            }
            if ($pending === []) {
                $staged = $bridge->stageRelink($managedName, $carrier);
                if (empty($staged['staged'])) {
                    return [
                        'restored' => false,
                        'applied' => false,
                        'error' => gettext('OPNsense rejected the original interface assignment. Plugin settings were retained.')
                            . ' ' . implode(' ', array_values($staged['validations'] ?? [])),
                    ];
                }
                $pending = $bridge->pendingChanges();
            }
            if (!self::isSelectedPendingRelink($pending, $managedName, $carrier)) {
                return ['restored' => false, 'applied' => false, 'error' => gettext('The native restoration queue could not be verified; plugin settings were retained.')];
            }
            // Restore before native apply configures the original carrier. A
            // failed apply leaves HA disabled and can safely retry this relink.
            if (NativeReceiveMode::update($nativeConfig->interfaces->$managedName, false)) {
                $config->save(['description' => gettext('HA DHCP Interface restore native receive mode')]);
            }
        } catch (\Throwable $exception) {
            return ['restored' => false, 'applied' => false, 'error' => gettext('Native assignment restoration could not be staged; plugin settings were retained.')];
        } finally {
            $config->unlock();
        }

        $config->lock(true);
        try {
            $shared = new Shared();
            $local = new Local();
            $assignments = self::assignmentMap($config->object());
            $pending = (new \OPNsense\DhcpInterfaceHa\SetupAssignmentBridge())->pendingChanges();
            if (!hash_equals(self::revision($shared, $local), $revision)
                || !empty((string)$shared->enabled)
                || (string)$local->managed_interface !== $managedName
                || trim((string)$local->carrier) !== $carrier
                || ($assignments[$managedName] ?? null) !== 'dhcpha0lagg'
                || !self::isSelectedPendingRelink($pending, $managedName, $carrier)) {
                return ['restored' => false, 'applied' => false, 'error' => gettext('Native assignments changed before restoration apply; plugin settings were retained.')];
            }
        } catch (\Throwable $exception) {
            return ['restored' => false, 'applied' => false, 'error' => gettext('Native restoration intent could not be rechecked; plugin settings were retained.')];
        } finally {
            $config->unlock();
        }

        $applied = false;
        try {
            $result = (new \OPNsense\DhcpInterfaceHa\SetupAssignmentBridge())->applyStagedChanges($this->request);
            $applied = ($result['status'] ?? '') === 'ok';
        } catch (\Throwable $exception) {
            $applied = null;
        }

        $verified = false;
        $config->lock(true);
        try {
            $shared = new Shared();
            $local = new Local();
            $assignments = self::assignmentMap($config->object());
            $pending = (new \OPNsense\DhcpInterfaceHa\SetupAssignmentBridge())->pendingChanges();
            $verified = hash_equals(self::revision($shared, $local), $revision)
                && empty((string)$shared->enabled)
                && (string)$local->managed_interface === $managedName
                && trim((string)$local->carrier) === $carrier
                && ($assignments[$managedName] ?? null) === $carrier
                && $pending === [];
        } catch (\Throwable $exception) {
            $verified = false;
        } finally {
            $config->unlock();
        }
        return $verified
            ? ['restored' => true, 'applied' => true, 'error' => null]
            : [
                'restored' => false,
                'applied' => $applied,
                'error' => $applied === null
                    ? gettext('Native assignment restoration result is unknown; plugin settings were retained for safe retry.')
                    : gettext('Native assignment restoration was not verified; plugin settings were retained for safe retry.'),
            ];
    }

    /**
     * Save both plugin model roots using the revision, validation and lock
     * boundary shared by normal Save and Configure interface.
     */
    private function saveSettings(array $sharedInput, array $localInput, $revision, $unknownSaveOutcome, $validateOnly = false, $knownObservations = null, $skipUnchanged = false)
    {
        // Read candidates before locking only to decide whether runtime evidence
        // is needed. The revision check under the lock rejects any intervening write.
        $oldShared = new Shared();
        $oldLocal = new Local();
        $probeShared = new Shared();
        $probeLocal = new Local();
        try {
            $probeShared->setNodes($sharedInput);
            $probeLocal->setNodes($localInput);
        } catch (\Throwable $exception) {
            return ['result' => 'failed', 'saved' => false, 'applied' => null, 'error' => gettext('Invalid settings data.')];
        }
        $identityChanged = self::identityChanged($oldShared, $oldLocal, $probeShared, $probeLocal);
        $unchanged = hash_equals(self::revision($oldShared, $oldLocal), self::revision($probeShared, $probeLocal));
        $needsEvidence = (!$skipUnchanged || !$unchanged) && (!empty((string)$probeShared->enabled) || $identityChanged);
        $observations = $needsEvidence
            ? ($knownObservations ?? self::collectObservations())
            : ['collected_at' => microtime(true)];

        $config = Config::getInstance();
        $config->lock(true);
        $saveAttempted = false;
        $result = null;
        try {
            $currentShared = new Shared();
            $currentLocal = new Local();
            if (!hash_equals(self::revision($currentShared, $currentLocal), $revision)) {
                return [
                    'result' => 'conflict',
                    'saved' => false,
                    'applied' => null,
                    'error' => gettext('Settings changed since this page was loaded. Reload settings before saving.'),
                ];
            }

            $candidateShared = new Shared();
            $candidateLocal = new Local();
            $candidateShared->setNodes($sharedInput);
            $candidateLocal->setNodes($localInput);

            $managedName = trim((string)$candidateLocal->managed_interface);
            $previousName = trim((string)$currentLocal->managed_interface);
            if ($managedName !== '' && $previousName !== '' && $managedName !== $previousName
                && isset($config->object()->interfaces->$previousName->dhcpha_original_promisc)) {
                return ['result' => 'failed', 'saved' => false, 'applied' => null,
                    'error' => gettext('Select Disabled and save to restore the previous interface before choosing another.')];
            }
            $receiveName = $managedName ?: trim((string)$currentLocal->managed_interface);
            $nativeInterface = $config->object()->interfaces->$receiveName ?? null;
            $receiveChanged = false;
            $receiveCarrier = trim((string)($managedName !== '' ? $candidateLocal->carrier : $currentLocal->carrier));
            if ($receiveName !== '' && $receiveCarrier !== '' && $nativeInterface !== null
                && in_array((string)$nativeInterface->if, [$receiveCarrier, 'dhcpha0lagg'], true)) {
                $candidateInterface = simplexml_load_string($nativeInterface->asXML());
                $receiveChanged = NativeReceiveMode::update($candidateInterface, $managedName !== '');
            }
            if ($receiveChanged && !$this->nativeRouteAllowed('/api/interfaces/assignment/set_item/' . $receiveName)) {
                return ['result' => 'failed', 'saved' => false, 'applied' => null,
                    'error' => gettext('Native interface write permission is required to configure receive mode.')];
            }

            // An ordinary unchanged save is not a request to repair runtime state.
            // Check under the lock so a stale page cannot bypass conflict detection.
            if ($skipUnchanged && !$receiveChanged && hash_equals(self::revision($candidateShared, $candidateLocal), $revision)) {
                return ['result' => 'unchanged', 'saved' => false, 'applied' => null, 'revision' => $revision];
            }

            $validations = self::modelValidations($candidateShared, 'dhcphashared');
            $validations += self::modelValidations($candidateLocal, 'dhcphalocal');
            // A receive-mode-only correction changes no HA identity or enable
            // state and needs no runtime action or promotion evidence.
            if (!$unchanged || $needsEvidence) {
                $validations += self::crossValidations(
                    $currentShared,
                    $currentLocal,
                    $candidateShared,
                    $candidateLocal,
                    $config->object(),
                    $observations,
                    $needsEvidence
                );
            }
            if (!empty($validations)) {
                return [
                    'result' => 'failed',
                    'saved' => false,
                    'applied' => null,
                    'validations' => $validations,
                    'error' => gettext('Correct the marked settings; nothing was saved.'),
                ];
            }

            if ($validateOnly) {
                return ['result' => 'valid', 'validations' => []];
            }

            $oldRevision = self::revision($currentShared, $currentLocal);
            $newRevision = self::revision($candidateShared, $candidateLocal);
            if ($receiveChanged) {
                NativeReceiveMode::update($nativeInterface, $managedName !== '');
            }
            $candidateShared->serializeToConfig(false, true);
            $candidateLocal->serializeToConfig(false, true);
            $saveAttempted = true;
            $config->save(['description' => gettext('HA DHCP Interface settings')]);
            $result = [
                'result' => 'saved',
                'saved' => true,
                'revision' => $newRevision,
                'changed' => !hash_equals($oldRevision, $newRevision),
                'receive_mode_only' => $receiveChanged && hash_equals($oldRevision, $newRevision),
            ];
        } catch (\Throwable $exception) {
            $this->logEvent('error', 'settings_save_failed', [
                'operation' => 'settings_save',
                'outcome' => $saveAttempted ? 'unknown' : 'failed',
                'error' => get_class($exception),
            ]);
            $result = [
                'result' => 'failed',
                'saved' => $saveAttempted && $unknownSaveOutcome ? null : false,
                'applied' => null,
                'error' => gettext($saveAttempted ? 'Settings save result is unknown. Reload settings before retrying.' : 'Settings could not be saved. Check the system log.'),
            ];
        } finally {
            $config->unlock();
        }
        return $result ?? ['result' => 'failed', 'saved' => false, 'applied' => null, 'error' => gettext('Settings were not saved.')];
    }

    private function configureInterface()
    {

        $sharedInput = $this->request->getPost('dhcphashared');
        $localInput = $this->request->getPost('dhcphalocal');
        $revisionInput = $this->request->getPost('revision', null, '');
        $revision = is_string($revisionInput) ? $revisionInput : '';
        if (!self::hasFields($sharedInput, self::SHARED_FIELDS) || !self::hasFields($localInput, self::LOCAL_FIELDS) || $revision === '') {
            return self::setupResponse(
                result: 'failed',
                saved: false,
                applied: false,
                assignmentVerified: null,
                stage: 'none',
                error: gettext('Submit both complete settings sections and their revision.')
            );
        }

        $managedName = trim((string)$localInput['managed_interface']);
        if (!preg_match('/^[A-Za-z][A-Za-z0-9_.-]{0,14}$/', $managedName)) {
            return self::setupResponse(result: 'failed', saved: false, applied: false, assignmentVerified: false, stage: 'none', error: gettext('Select an existing logical interface before configuring it.'));
        }
        if (!$this->nativeRouteAllowed('/api/interfaces/assignment/set_item/' . $managedName)
            || !$this->nativeRouteAllowed('/api/interfaces/assignment/reconfigure')) {
            return self::setupResponse(
                result: 'failed',
                saved: false,
                applied: false,
                assignmentVerified: null,
                stage: 'none',
                error: gettext('Native interface assignment write permission is required to configure this interface.')
            );
        }

        // The form's carrier field is present for compatibility with the shared
        // settings payload, but only committed native assignment state can name it.
        $requestedEnabled = (string)$sharedInput['enabled'];
        $sharedInput['enabled'] = '0';
        $observations = self::collectObservations();
        $initial = self::inspectSetupState($managedName, $observations, $revision);
        if ($initial['result'] !== 'ok') {
            return self::setupResponse(
                result: $initial['result'],
                saved: false,
                applied: false,
                assignmentVerified: $initial['assignment_verified'],
                stage: 'none',
                error: $initial['error'],
                validations: $initial['validations'] ?? []
            );
        }

        $carrier = $initial['carrier'];
        if (trim((string)$sharedInput['shared_mac']) === '') {
            $sharedInput['shared_mac'] = $initial['default_mac'];
        }
        $localInput['carrier'] = $carrier;
        $stage = 'none';
        $preflight = $this->saveSettings(
            $sharedInput,
            $localInput,
            $revision,
            unknownSaveOutcome: true,
            validateOnly: true,
            knownObservations: $observations
        );
        if ($preflight['result'] !== 'valid') {
            return self::setupResponse(
                result: $preflight['result'],
                saved: false,
                applied: false,
                assignmentVerified: false,
                stage: 'none',
                error: $preflight['error'] ?? gettext('Correct the marked settings before configuring the interface.'),
                validations: $preflight['validations'] ?? []
            );
        }
        $this->progressStep(gettext('Prepare and verify the detached HA interface'));
        if (!$initial['device_present']) {
            $guard = self::checkSetupIntent($managedName, $carrier, $revision, $initial['committed_device'], $initial['assignment_map']);
            if ($guard !== null) {
                return self::setupResponse(result: 'conflict', saved: false, applied: false, assignmentVerified: false, stage: 'none', error: $guard);
            }
            $prepared = self::prepareDetachedDevice();
            if (!$prepared['prepared']) {
                $this->logEvent('error', 'setup_failed', [
                    'operation' => 'prepare_device',
                    'managed' => $managedName,
                    'outcome' => $prepared['outcome'],
                ]);
                return self::setupResponse(
                    result: 'failed',
                    saved: false,
                    applied: false,
                    assignmentVerified: false,
                    stage: 'none',
                    error: $prepared['error']
                );
            }
        }
        $stage = 'prepared';

        $this->progressStep(gettext('Save interface settings with HA temporarily disabled'));
        $saved = $this->saveSettings($sharedInput, $localInput, $revision, unknownSaveOutcome: true);
        if ($saved['result'] !== 'saved') {
            return self::setupResponse(
                result: $saved['result'],
                saved: $saved['saved'],
                applied: false,
                assignmentVerified: false,
                stage: $stage,
                error: $saved['error'],
                validations: $saved['validations'] ?? []
            );
        }
        $stage = 'settings_saved';
        if (!empty($saved['changed'])) {
            $this->logEvent('info', 'settings_saved', [
                'operation' => 'configure_interface',
                'managed' => $managedName,
                'carrier' => $carrier,
                'outcome' => 'saved_disabled',
            ]);
        }

        if (!empty($saved['changed'])) {
            $this->progressStep(gettext('Verify the carrier is safely detached'));
            $controllerApply = self::applySavedSettings();
            if ($controllerApply['applied'] !== true || !self::isDisabledDetachedStatus($controllerApply['status'] ?? null)) {
                $this->logEvent('error', 'setup_failed', [
                    'operation' => 'controller_apply',
                    'managed' => $managedName,
                    'outcome' => $controllerApply['applied'] === false ? 'failed' : 'unknown',
                ]);
                return self::setupResponse(
                    result: 'failed',
                    saved: true,
                    applied: false,
                    assignmentVerified: false,
                    stage: $stage,
                    error: $controllerApply['error'] ?? gettext('Saved settings, but disabled controller state could not be verified.'),
                    validations: [],
                    revision: $saved['revision'],
                    status: $controllerApply['status'] ?? null
                );
            }
        }

        $this->progressStep(gettext('Prepare the native interface assignment'));
        $stageResult = self::stageAssignment(
            $managedName,
            $carrier,
            $saved['revision'],
            $initial['committed_device'],
            $initial['assignment_map']
        );
        if (!in_array($stageResult['result'], ['already_mapped', 'complete', 'staged'], true)) {
            $this->logEvent('error', 'setup_failed', [
                'operation' => 'stage_assignment',
                'managed' => $managedName,
                'outcome' => $stageResult['result'],
            ]);
            return self::setupResponse(
                result: $stageResult['result'],
                saved: true,
                applied: false,
                assignmentVerified: false,
                stage: $stage,
                error: $stageResult['error'],
                validations: $stageResult['validations'] ?? [],
                revision: $saved['revision'],
                status: $stageResult['status'] ?? null
            );
        } else {
            $stage = 'assignment_staged';
        }

        $this->progressStep(gettext('Apply and verify the native interface assignment'));
        $apply = $stageResult['result'] === 'complete'
            ? ['applied' => true, 'error' => null]
            : self::applyStagedAssignment(
            $managedName,
            $carrier,
            $saved['revision'],
            $initial['committed_device'],
            $initial['assignment_map']
        );
        if ($apply['applied'] === true) {
            // Native interface apply can raise the original carrier. Reconcile
            // the disabled controller now, before verifying detached readiness.
            self::applySavedSettings();
        }
        $verification = self::verifySetup(
            $managedName,
            $carrier,
            $saved['revision'],
            $initial['assignment_map']
        );
        if ($verification['verified']) {
            // Setup stays disabled until native assignment and detachment are
            // verified. Then honor Enable through the normal validation/apply path.
            if ($requestedEnabled !== '0') {
                $this->progressStep(gettext('Enable HA DHCP Interface and apply the CARP role'));
                $sharedInput['enabled'] = $requestedEnabled;
                $enabled = $this->saveSettings($sharedInput, $localInput, $saved['revision'], unknownSaveOutcome: true);
                if ($enabled['result'] !== 'saved') {
                    return self::setupResponse(
                        result: $enabled['result'], saved: true, applied: false,
                        assignmentVerified: true, stage: 'verified',
                        error: $enabled['error'] ?? gettext('Interface setup completed, but Enable could not be saved.'),
                        validations: $enabled['validations'] ?? [], revision: $saved['revision']
                    );
                }
                $saved = $enabled;
                $apply = self::applySavedSettings();
                if ($apply['applied'] !== true) {
                    return self::setupResponse(
                        result: 'failed', saved: true, applied: $apply['applied'],
                        assignmentVerified: true, stage: 'verified', error: $apply['error'],
                        revision: $saved['revision'], status: $apply['status'] ?? null
                    );
                }
                $verification['status'] = $apply['status'] ?? null;
            }
            $this->logEvent('info', 'setup_completed', [
                'managed' => $managedName,
                'carrier' => $carrier,
                'outcome' => 'verified',
            ]);
            return self::setupResponse(result: 'saved', saved: true, applied: true, assignmentVerified: true, stage: 'verified', error: null, validations: [], revision: $saved['revision'], status: $verification['status']);
        }

        $applied = $apply['applied'];
        if ($verification['committed']) {
            $applied = true;
        }
        if ($applied === true || $verification['committed']) {
            $stage = 'assignment_applied';
        }
        $unknown = $applied === null || $verification['unknown'];
        $assignmentVerified = $verification['unknown'] ? null : false;
        $this->logEvent('error', 'setup_failed', [
            'operation' => 'native_assignment_apply',
            'managed' => $managedName,
            'carrier' => $carrier,
            'outcome' => $unknown ? 'unknown' : 'failed',
        ]);
        return self::setupResponse(
            result: $verification['conflict'] ? 'conflict' : 'failed',
            saved: true,
            applied: $applied,
            assignmentVerified: $assignmentVerified,
            stage: $stage,
            error: $verification['error'] ?? $apply['error'],
            validations: [],
            revision: $saved['revision'],
            status: $verification['status'] ?? null
        );
    }

    public function enable_syncAction()
    {
        if (!$this->request->isPost()) {
            return ['result' => 'failed', 'changed' => false, 'selected' => null, 'error' => gettext('POST required.')];
        }
        $this->throwReadOnly();
        if (!$this->nativeRouteAllowed('/api/core/hasync/set')) {
            return ['result' => 'failed', 'changed' => false, 'selected' => null, 'error' => gettext('Native configuration sync write permission is required.')];
        }

        $config = Config::getInstance();
        $changed = false;
        $saveAttempted = false;
        $saved = false;
        $destinationPresent = null;
        $selected = null;
        $error = null;
        $result = 'failed';
        $logFailure = false;
        $config->lock(true);
        try {
            // The native model is loaded after lock(true) reloads current config,
            // so concurrent HA selection edits are merged rather than overwritten.
            $hasync = new Hasync();
            $destinationPresent = trim((string)$hasync->synchronizetoip) !== '';
            $items = self::syncItems((string)$hasync->syncitems);
            $selected = in_array('dhcp-interface-ha', $items, true);
            if (!$destinationPresent) {
                $error = gettext('Configure a native XMLRPC synchronization destination on this node first.');
            } elseif ($selected) {
                $result = 'saved';
                $changed = false;
                $saved = true;
            } else {
                $rawItems = (string)$hasync->syncitems;
                $hasync->syncitems = trim($rawItems) === ''
                    ? 'dhcp-interface-ha'
                    : $rawItems . (substr($rawItems, -1) === ',' ? '' : ',') . 'dhcp-interface-ha';
                $validations = self::modelValidations($hasync, 'hasync');
                if (!empty($validations)) {
                    $error = reset($validations);
                } else {
                    $hasync->serializeToConfig(false, true);
                    $saveAttempted = true;
                    $config->save(['description' => gettext('Include HA DHCP Interface in configuration sync')]);
                    $saved = true;
                    $changed = true;
                    $result = 'saved';
                }
            }
        } catch (\Throwable $exception) {
            $error = gettext('Configuration sync selection could not be saved. Refresh sync status before retrying.');
            $changed = $saveAttempted ? null : false;
            $logFailure = $saveAttempted;
        } finally {
            $config->unlock();
        }

        if (!$saveAttempted) {
            return ['result' => $result, 'changed' => $changed, 'selected' => $selected, 'error' => $error];
        }

        // Reload from disk and verify the membership after releasing the write lock.
        try {
            $config->lock(true);
            $readback = new Hasync();
            $destinationPresent = trim((string)$readback->synchronizetoip) !== '';
            $selected = in_array('dhcp-interface-ha', self::syncItems((string)$readback->syncitems), true);
            $config->unlock();
            if ($destinationPresent && $selected) {
                $result = 'saved';
                $saved = true;
                // A successful save proves our selection change; after an
                // exception, readback proves inclusion but not who changed it.
                $error = null;
            } elseif ($saveAttempted && $changed === true) {
                $result = $destinationPresent ? 'failed' : 'conflict';
                $saved = false;
                $changed = false;
                $logFailure = true;
                $error = gettext('Configuration sync selection was not present after save. Review native HA settings before retrying.');
            } elseif (!$destinationPresent) {
                $result = 'failed';
                $saved = false;
                $changed = false;
                $error = gettext('Configure a native XMLRPC synchronization destination on this node first.');
            } elseif ($saved && !$selected) {
                $result = 'failed';
                $saved = false;
                $changed = false;
                $error = gettext('Configuration sync selection could not be verified. Refresh sync status before retrying.');
            }
        } catch (\Throwable $exception) {
            if ($saveAttempted) {
                $result = 'failed';
                $saved = null;
                $changed = null;
                $selected = null;
                $error = gettext('Configuration sync save result is unknown. Refresh sync status before retrying.');
                $logFailure = true;
            } else {
                $selected = null;
                $error = $error ?? gettext('Configuration sync status could not be read.');
            }
            $config->unlock();
        }

        if ($result === 'saved' && $changed === true) {
            $this->logEvent('info', 'sync_selection_changed', ['outcome' => 'selected']);
        } elseif ($logFailure && $result !== 'saved') {
            $this->logEvent('error', 'sync_selection_failed', [
                'outcome' => $changed === null ? 'unknown' : 'failed',
            ]);
        }
        return ['result' => $result, 'changed' => $changed, 'selected' => $selected, 'error' => $error];
    }

    private static function setupResponse(
        $result,
        $saved,
        $applied,
        $assignmentVerified,
        $stage,
        $error,
        array $validations = [],
        $revision = null,
        $status = null
    ) {
        $response = [
            'result' => $result,
            'saved' => $saved,
            'applied' => $applied,
            'error' => $error,
            'setup_stage' => $stage,
            'assignment_verified' => $assignmentVerified,
        ];
        if ($validations !== []) {
            $response['validations'] = $validations;
        }
        if ($revision !== null) {
            $response['revision'] = $revision;
        }
        if (is_array($status)) {
            $response['status'] = $status;
        }
        return $response;
    }

    private function nativeRouteAllowed($route)
    {
        $username = $this->getUserName();
        if (!is_string($username) || $username === '') {
            return false;
        }
        try {
            return (new ACL())->isPageAccessible($username, $route);
        } catch (\Throwable $exception) {
            return false;
        }
    }

    /**
     * Check committed assignment state and native pending changes without
     * treating a pending UI model as committed configuration.
     */
    private static function inspectSetupState($managedName, array $observations, $expectedRevision = null)
    {
        // NetworkInterface loads DeviceField options from configd on first
        // construction. Warm that native cache before taking Config's lock;
        // the locked instance below still re-reads current config and queue.
        try {
            (new \OPNsense\DhcpInterfaceHa\SetupAssignmentBridge())->pendingChanges();
        } catch (\Throwable $exception) {
            return [
                'result' => 'failed',
                'assignment_verified' => null,
                'error' => gettext('Native interface assignment state is unavailable. Refresh status before retrying.'),
            ];
        }
        $config = Config::getInstance();
        $config->lock(true);
        try {
            $shared = new Shared();
            $local = new Local();
            $currentRevision = self::revision($shared, $local);
            if ($expectedRevision !== null && !hash_equals($currentRevision, $expectedRevision)) {
                return [
                    'result' => 'conflict',
                    'assignment_verified' => false,
                    'error' => gettext('Settings changed since this page was loaded. Reload settings before configuring the interface.'),
                ];
            }
            if (!empty((string)$shared->enabled)) {
                return [
                    'result' => 'failed',
                    'assignment_verified' => false,
                    'error' => gettext('Disable and apply HA DHCP Interface before configuring the native assignment.'),
                    'validations' => ['dhcphashared.enabled' => gettext('Save the disabled state and verify fencing before setup.')],
                ];
            }

            $nativeConfig = $config->object();
            if (empty($nativeConfig->interfaces->$managedName)) {
                return [
                    'result' => 'failed',
                    'assignment_verified' => false,
                    'error' => gettext('The selected logical interface no longer exists.'),
                    'validations' => ['dhcphalocal.managed_interface' => gettext('Select an existing logical interface.')],
                ];
            }
            $managed = $nativeConfig->interfaces->$managedName;
            $reason = self::managedInterfaceReason($nativeConfig, $managedName, $managed);
            if ($reason !== null) {
                return [
                    'result' => 'failed',
                    'assignment_verified' => false,
                    'error' => sprintf(gettext('The selected logical interface is not eligible: %s.'), $reason),
                    'validations' => ['dhcphalocal.managed_interface' => $reason],
                ];
            }

            $committedDevice = trim((string)$managed->if);
            $assignmentMap = self::assignmentMap($nativeConfig);
            $bridge = new \OPNsense\DhcpInterfaceHa\SetupAssignmentBridge();
            $pending = $bridge->pendingChanges();
        } catch (\Throwable $exception) {
            return [
                'result' => 'failed',
                'assignment_verified' => null,
                'error' => gettext('Native interface assignment state is unavailable. Refresh status before retrying.'),
            ];
        } finally {
            $config->unlock();
        }

        if (!is_array($pending) || !self::isAssignmentMap($assignmentMap)) {
            return [
                'result' => 'failed',
                'assignment_verified' => null,
                'error' => gettext('Native interface assignment state could not be verified.'),
            ];
        }
        $carrier = $committedDevice;
        if ($committedDevice === 'dhcpha0lagg') {
            if ((string)$local->managed_interface !== $managedName || trim((string)$local->carrier) === '') {
                return [
                    'result' => 'conflict',
                    'assignment_verified' => null,
                    'error' => gettext('This interface already uses the plugin device, but its original carrier is not recorded. The carrier cannot be guessed.'),
                ];
            }
            $carrier = trim((string)$local->carrier);
        }
        if (!preg_match('/^[A-Za-z][A-Za-z0-9_.-]{0,14}$/', $carrier) || $carrier === 'dhcpha0lagg') {
            return [
                'result' => 'failed',
                'assignment_verified' => false,
                'error' => gettext('The committed assignment does not identify an eligible original Ethernet carrier.'),
            ];
        }

        $sources = $observations['sources'] ?? [];
        $devices = $sources['assign_options'] ?? null;
        $ifconfig = $sources['ifconfig'] ?? null;
        $status = $sources['status'] ?? null;
        if (!is_array($devices) || !is_array($ifconfig)
            || !self::isInterfaceMap($devices) || !self::isInterfaceMap($ifconfig)
            || microtime(true) - ($observations['collected_at'] ?? 0) > 15) {
            return [
                'result' => 'failed',
                'assignment_verified' => null,
                'error' => gettext('Fresh native interface inventory is unavailable; no assignment was changed.'),
            ];
        }
        $eligibility = Local::carrierRuntimeEligibility($carrier, $devices, $ifconfig);
        if ($eligibility !== null) {
            return [
                'result' => 'failed',
                'assignment_verified' => false,
                'error' => sprintf(gettext('The committed carrier cannot be used because it %s.'), $eligibility),
                'validations' => ['dhcphalocal.managed_interface' => $eligibility],
            ];
        }
        $blocked = Local::blockedCarrierDevices($managedName);
        if (isset($blocked[$carrier])) {
            return [
                'result' => 'failed',
                'assignment_verified' => false,
                'error' => sprintf(gettext('The committed carrier cannot be used because it %s.'), $blocked[$carrier]),
            ];
        }

        $targetAssignments = [];
        foreach ($assignmentMap as $name => $device) {
            if ($device === 'dhcpha0lagg') {
                $targetAssignments[] = $name;
            }
        }
        $alreadyMapped = $committedDevice === 'dhcpha0lagg';
        if ($alreadyMapped && $targetAssignments !== [$managedName]) {
            return [
                'result' => 'conflict',
                'assignment_verified' => false,
                'error' => gettext('The plugin device is assigned to more than the selected logical interface.'),
            ];
        }
        if (!$alreadyMapped && $targetAssignments !== []) {
            return [
                'result' => 'conflict',
                'assignment_verified' => false,
                'error' => gettext('The plugin device is already assigned to another logical interface.'),
            ];
        }
        if (!$alreadyMapped) {
            $carrierAssignments = [];
            foreach ($assignmentMap as $name => $device) {
                if ($device === $committedDevice) {
                    $carrierAssignments[] = $name;
                }
            }
            if ($carrierAssignments !== [$managedName]) {
                return [
                    'result' => 'conflict',
                    'assignment_verified' => false,
                    'error' => gettext('The committed carrier mapping is ambiguous across logical interfaces.'),
                ];
            }
        } elseif (!empty($ifconfig[$carrier]['ipv4']) || !empty($ifconfig[$carrier]['ipv6'])) {
            return [
                'result' => 'failed',
                'assignment_verified' => false,
                'error' => gettext('The saved original carrier has configured IP addresses and cannot be reused as the migrated carrier.'),
            ];
        }

        $pendingTarget = self::isSelectedPendingRelink($pending, $managedName);
        if ($pending !== [] && !$pendingTarget) {
            return [
                'result' => 'conflict',
                'assignment_verified' => false,
                'error' => gettext('Other native assignment changes are pending. Apply or discard them on Interfaces: Assignments before configuring this interface.'),
            ];
        }
        if ($pendingTarget && !$alreadyMapped
            && ((string)$local->managed_interface !== $managedName
                || trim((string)$local->carrier) !== $committedDevice)) {
            return [
                'result' => 'conflict',
                'assignment_verified' => false,
                'error' => gettext('A pending relink is not a verified continuation of the saved plugin setup.'),
            ];
        }

        $device = $ifconfig['dhcpha0lagg'] ?? null;
        if ($device === null) {
            if (self::hasRuntimeStatusShape($status)
                && (($status['actual_attachment'] ?? '') === 'ATTACHED'
                    || !empty($status['enabled'])
                    || !empty($status['dhcpha']['exists']))) {
                return [
                    'result' => 'conflict',
                    'assignment_verified' => false,
                    'error' => gettext('Runtime status conflicts with the absent plugin device. Refresh status before retrying.'),
                    'status' => $status,
                ];
            }
        } elseif (!is_array($device) || ($device['laggproto'] ?? '') !== 'failover'
            || !is_array($device['laggport'] ?? []) || ($device['laggport'] ?? []) !== []) {
            return [
                'result' => 'conflict',
                'assignment_verified' => false,
                'error' => gettext('Setup stopped before reassignment: the plugin could not verify ownership and detachment of dhcpha0lagg. Check the plugin Log, then recheck status.'),
                'status' => is_array($status) ? $status : null,
            ];
        } elseif (!self::isDisabledDetachedStatus($status)) {
            return [
                'result' => 'failed',
                'assignment_verified' => self::hasRuntimeStatusShape($status) ? false : null,
                'error' => gettext('Fresh controller status must verify the plugin device is owned, disabled and detached before setup.'),
                'status' => is_array($status) ? $status : null,
            ];
        }

        return [
            'result' => 'ok',
            'carrier' => $carrier,
            'default_mac' => strtolower(trim((string)($ifconfig[$carrier]['macaddr'] ?? ''))),
            'committed_device' => $committedDevice,
            'assignment_map' => $assignmentMap,
            'already_mapped' => $alreadyMapped,
            'pending_target' => $pendingTarget,
            'device_present' => $device !== null,
            'status' => is_array($status) ? $status : null,
        ];
    }

    private static function checkSetupIntent($managedName, $carrier, $revision, $committedDevice, array $expectedMap)
    {
        $config = Config::getInstance();
        $config->lock(true);
        try {
            $shared = new Shared();
            $local = new Local();
            if (!hash_equals(self::revision($shared, $local), $revision)
                || !empty((string)$shared->enabled)) {
                return gettext('Saved setup intent changed before device preparation. Reload settings before retrying.');
            }
            $nativeConfig = $config->object();
            $managed = $nativeConfig->interfaces->$managedName ?? null;
            if ($managed === null || (string)$managed->if !== $committedDevice
                || self::assignmentMap($nativeConfig) !== $expectedMap) {
                return gettext('Native assignment changed before device preparation. Review assignments before retrying.');
            }
            $bridge = new \OPNsense\DhcpInterfaceHa\SetupAssignmentBridge();
            $pending = $bridge->pendingChanges();
            if (!is_array($pending) || ($pending !== [] && !self::isSelectedPendingRelink($pending, $managedName))) {
                return gettext('Native assignment changes are pending. Apply or discard them before configuring this interface.');
            }
            if ($pending !== [] && ((string)$local->managed_interface !== $managedName
                || trim((string)$local->carrier) !== $carrier)) {
                return gettext('The pending relink does not match the saved plugin setup intent.');
            }
            return null;
        } catch (\Throwable $exception) {
            return gettext('Setup intent could not be rechecked before device preparation. Refresh status before retrying.');
        } finally {
            $config->unlock();
        }
    }

    private static function prepareDetachedDevice()
    {
        $commandOutcome = 'unknown';
        try {
            $raw = (new Backend())->configdRun('dhcp_interface_ha prepare_setup', false, 15, 2);
            $result = json_decode($raw, true);
            if (is_array($result) && !empty($result['prepared']) && !empty($result['detached'])
                && !empty($result['owned']) && ($result['device'] ?? '') === 'dhcpha0lagg') {
                $commandOutcome = 'reported_prepared';
            } else {
                $commandOutcome = is_array($result) && isset($result['error']) ? 'failed' : 'unknown';
            }
        } catch (\Throwable $exception) {
            $commandOutcome = 'unknown';
        }

        $observations = self::collectObservations();
        $status = $observations['sources']['status'] ?? null;
        $ifconfig = $observations['sources']['ifconfig'] ?? null;
        if (self::isDisabledDetachedStatus($status)
            && is_array($ifconfig)
            && isset($ifconfig['dhcpha0lagg'])
            && ($ifconfig['dhcpha0lagg']['laggproto'] ?? '') === 'failover'
            && ($ifconfig['dhcpha0lagg']['laggport'] ?? []) === []) {
            return ['prepared' => true, 'outcome' => $commandOutcome === 'reported_prepared' ? 'verified' : 'verified_after_unknown', 'error' => null];
        }
        $outcome = $commandOutcome === 'failed' ? 'failed' : 'unknown';
        return [
            'prepared' => false,
            'outcome' => $outcome,
            'error' => $outcome === 'unknown'
                ? gettext('Device preparation result is unknown. Refresh status before retrying; no native assignment was changed.')
                : gettext('Device preparation could not be verified. Check system logs; no native assignment was changed.'),
        ];
    }

    private static function stageAssignment($managedName, $carrier, $revision, $committedDevice, array $expectedMap)
    {
        $status = self::readRuntimeStatus();
        if (!self::isDisabledDetachedStatus($status)) {
            return [
                'result' => 'failed',
                'error' => gettext('The plugin device must be freshly verified as owned, disabled and detached before assignment staging.'),
                'status' => $status,
            ];
        }

        $config = Config::getInstance();
        $config->lock(true);
        try {
            $shared = new Shared();
            $local = new Local();
            if (!hash_equals(self::revision($shared, $local), $revision)
                || !empty((string)$shared->enabled)
                || (string)$local->managed_interface !== $managedName
                || trim((string)$local->carrier) !== $carrier) {
                return [
                    'result' => 'conflict',
                    'error' => gettext('Saved settings changed before native assignment staging. Reload settings before retrying.'),
                ];
            }
            $nativeConfig = $config->object();
            $managed = $nativeConfig->interfaces->$managedName ?? null;
            $currentMap = self::assignmentMap($nativeConfig);
            if ($managed === null || $currentMap !== $expectedMap) {
                return [
                    'result' => 'conflict',
                    'error' => gettext('Native assignments changed before staging. Review the current assignments before retrying.'),
                ];
            }
            $currentDevice = (string)$managed->if;
            if (!in_array($currentDevice, [$committedDevice, 'dhcpha0lagg'], true)) {
                return ['result' => 'conflict', 'error' => gettext('The selected native assignment changed before staging.')];
            }
            $bridge = new \OPNsense\DhcpInterfaceHa\SetupAssignmentBridge();
            $pending = $bridge->pendingChanges();
            if (!is_array($pending)) {
                return ['result' => 'conflict', 'error' => gettext('Native pending assignments cannot be inspected safely.')];
            }
            if ($pending !== [] && !self::isSelectedPendingRelink($pending, $managedName)) {
                return ['result' => 'conflict', 'error' => gettext('Unrelated native assignment edits appeared before staging.')];
            }
            if ($pending !== [] && ((string)($pending[$managedName]['pending_if'] ?? '') !== 'dhcpha0lagg'
                || (string)$local->managed_interface !== $managedName
                || trim((string)$local->carrier) !== $carrier
                || ($currentDevice !== 'dhcpha0lagg' && $carrier !== $currentDevice))) {
                return ['result' => 'conflict', 'error' => gettext('The pending relink is not an exact continuation of this saved setup.')];
            }
            if ($currentDevice === 'dhcpha0lagg') {
                return $pending === []
                    ? ['result' => 'complete', 'error' => null]
                    : ['result' => 'already_mapped', 'error' => null];
            }
            if ($currentDevice !== $committedDevice || $carrier !== $currentDevice) {
                return ['result' => 'conflict', 'error' => gettext('The committed carrier no longer matches the saved setup intent.')];
            }
            if ($pending === []) {
                $staged = $bridge->stageRelink($managedName, 'dhcpha0lagg');
                if (empty($staged['staged'])) {
                    return [
                        'result' => 'failed',
                        'error' => gettext('Native assignment validation rejected the selected interface.'),
                        'validations' => $staged['validations'] ?? [],
                    ];
                }
                $pending = $bridge->pendingChanges();
            }
            if (!self::isSelectedPendingRelink($pending, $managedName)) {
                return ['result' => 'conflict', 'error' => gettext('The native selected-interface relink could not be verified after staging.')];
            }
            return ['result' => 'staged', 'error' => null];
        } catch (\Throwable $exception) {
            return [
                'result' => 'failed',
                'error' => gettext('Native assignment staging failed. Refresh assignment state before retrying.'),
            ];
        } finally {
            $config->unlock();
        }
    }

    private function applyStagedAssignment($managedName, $carrier, $revision, $committedDevice, array $expectedMap)
    {
        $guard = self::checkSetupBeforeApply($managedName, $carrier, $revision, $committedDevice, $expectedMap);
        if ($guard['result'] === 'committed') {
            // A concurrent native apply already committed this exact relink;
            // verify it below instead of replaying a queue-consuming action.
            return ['applied' => true, 'error' => null];
        }
        if ($guard['result'] !== 'ready') {
            return [
                'applied' => false,
                'conflict' => $guard['result'] === 'conflict',
                'error' => $guard['error'],
                'status' => $guard['status'] ?? null,
            ];
        }
        try {
            $bridge = new \OPNsense\DhcpInterfaceHa\SetupAssignmentBridge();
            $result = $bridge->applyStagedChanges($this->request);
            if (($result['status'] ?? '') === 'ok') {
                return ['applied' => true, 'unknown' => false, 'error' => null];
            }
            return [
                'applied' => false,
                'unknown' => false,
                'error' => gettext('Native interface apply did not complete. The saved carrier is retained; refresh assignment state before retrying.'),
            ];
        } catch (\Throwable $exception) {
            return [
                'applied' => null,
                'unknown' => true,
                'error' => gettext('Native interface apply result is unknown. The saved carrier is retained; refresh assignment state before retrying.'),
            ];
        }
    }

    private static function checkSetupBeforeApply($managedName, $carrier, $revision, $committedDevice, array $expectedMap)
    {
        // Keep slow runtime I/O outside Config's lock. The locked read below is
        // the final intent/queue check before the native queue-consuming apply.
        $status = self::readRuntimeStatus();
        $config = Config::getInstance();
        $config->lock(true);
        try {
            $shared = new Shared();
            $local = new Local();
            if (!hash_equals(self::revision($shared, $local), $revision)
                || !empty((string)$shared->enabled)
                || (string)$local->managed_interface !== $managedName
                || trim((string)$local->carrier) !== $carrier) {
                return [
                    'result' => 'conflict',
                    'error' => gettext('Saved setup intent changed immediately before native apply.'),
                    'status' => $status,
                ];
            }

            $nativeConfig = $config->object();
            $assignmentMap = self::assignmentMap($nativeConfig);
            $pending = (new \OPNsense\DhcpInterfaceHa\SetupAssignmentBridge())->pendingChanges();
            if (!is_array($pending) || !self::isAssignmentMap($assignmentMap)) {
                return [
                    'result' => 'conflict',
                    'error' => gettext('Native assignment state cannot be checked immediately before apply.'),
                    'status' => $status,
                ];
            }

            $expectedFinal = $expectedMap;
            $expectedFinal[$managedName] = 'dhcpha0lagg';
            ksort($expectedFinal);
            if ($assignmentMap === $expectedFinal && $pending === []) {
                return ['result' => 'committed', 'error' => null, 'status' => $status];
            }
            $currentDevice = (string)($nativeConfig->interfaces->$managedName->if ?? '');
            $pendingTarget = self::isSelectedPendingRelink($pending, $managedName);
            $matchesBefore = $assignmentMap === $expectedMap
                && $currentDevice === $committedDevice
                && ($committedDevice === 'dhcpha0lagg' || $carrier === $committedDevice)
                && $pendingTarget;
            $matchesAfter = $assignmentMap === $expectedFinal
                && $currentDevice === 'dhcpha0lagg'
                && $pendingTarget;
            if (!$matchesBefore && !$matchesAfter) {
                return [
                    'result' => 'conflict',
                    'error' => gettext('Native assignments changed immediately before apply. Review the current assignment queue before retrying.'),
                    'status' => $status,
                ];
            }
            if (!self::isDisabledDetachedStatus($status)) {
                return [
                    'result' => 'blocked',
                    'error' => gettext('Owned disabled and detached runtime state could not be freshly verified before native apply.'),
                    'status' => $status,
                ];
            }
            return ['result' => 'ready', 'error' => null, 'status' => $status];
        } catch (\Throwable $exception) {
            return [
                'result' => 'blocked',
                'error' => gettext('Setup intent could not be checked immediately before native apply.'),
                'status' => $status,
            ];
        } finally {
            $config->unlock();
        }
    }

    private static function verifySetup($managedName, $carrier, $revision, $expectedMap = null)
    {
        $config = Config::getInstance();
        $assignmentMap = null;
        $pending = null;
        $currentRevision = null;
        $config->lock(true);
        try {
            $shared = new Shared();
            $local = new Local();
            $currentRevision = self::revision($shared, $local);
            $nativeConfig = $config->object();
            $assignmentMap = self::assignmentMap($nativeConfig);
            $bridge = new \OPNsense\DhcpInterfaceHa\SetupAssignmentBridge();
            $pending = $bridge->pendingChanges();
        } catch (\Throwable $exception) {
            return [
                'verified' => false,
                'committed' => false,
                'unknown' => true,
                'conflict' => false,
                'status' => null,
                'error' => gettext('Native setup readback is unavailable. Refresh status before retrying.'),
            ];
        } finally {
            $config->unlock();
        }

        $committed = is_array($assignmentMap) && ($assignmentMap[$managedName] ?? null) === 'dhcpha0lagg';
        $status = self::readRuntimeStatus();
        $unknown = $assignmentMap === null || !is_array($pending) || $status === null
            || !self::hasRuntimeStatusShape($status);
        $conflict = false;
        if ($expectedMap !== null && is_array($assignmentMap)) {
            $expectedFinal = $expectedMap;
            $expectedFinal[$managedName] = 'dhcpha0lagg';
            ksort($expectedFinal);
            $stagedOriginal = $assignmentMap === $expectedMap
                && self::isSelectedPendingRelink($pending, $managedName);
            if ($assignmentMap !== $expectedFinal && !$stagedOriginal) {
                $conflict = true;
            }
        }
        if (!hash_equals((string)$revision, (string)$currentRevision)
            || (string)$local->managed_interface !== $managedName
            || trim((string)$local->carrier) !== $carrier
            || !empty((string)$shared->enabled)) {
            $conflict = true;
        }
        if ($pending !== [] && $pending !== null
            && !self::isSelectedPendingRelink($pending, $managedName)) {
            $conflict = true;
        }
        $verified = !$unknown && !$conflict && $pending === [] && $committed && self::isManagedDisabledDetachedStatus($status);
        $error = null;
        if (!$verified) {
            if ($unknown) {
                $error = gettext('Native assignment or runtime readback is unavailable. The result is unknown; refresh status before retrying.');
            } elseif ($conflict) {
                $error = gettext('Native assignments or saved setup intent changed during configuration. Review the current state before retrying.');
            } elseif (!$committed) {
                $error = gettext('The selected logical interface is not committed to dhcpha0lagg. The saved carrier is retained.');
            } else {
                $error = gettext('The assignment is committed, but owned disabled and detached runtime state could not be verified.');
            }
        }
        return [
            'verified' => $verified,
            'committed' => $committed,
            'unknown' => $unknown,
            'conflict' => $conflict,
            'status' => $status,
            'error' => $error,
        ];
    }

    private static function isDisabledDetachedStatus($status)
    {
        return self::hasRuntimeStatusShape($status)
            && ($status['enabled'] ?? null) === false
            && in_array($status['actual_attachment'] ?? '', ['FENCED', 'UNMANAGED'], true)
            && !empty($status['owned'])
            && !empty($status['dhcpha']['exists'])
            && ($status['dhcpha']['lagg_protocol'] ?? '') === 'failover'
            && ($status['dhcpha']['lagg_members'] ?? null) === [];
    }

    private static function isManagedDisabledDetachedStatus($status)
    {
        return self::isDisabledDetachedStatus($status) && !empty($status['managed_by_dhcpha']);
    }

    private static function readRuntimeStatus()
    {
        try {
            $status = json_decode((new Backend())->configdRun('dhcp_interface_ha status', false, 5, 1), true);
            return is_array($status) && !isset($status['error']) ? $status : null;
        } catch (\Throwable $exception) {
            return null;
        }
    }

    private static function assignmentMap($config)
    {
        $result = [];
        foreach ($config->interfaces->children() ?? [] as $name => $interface) {
            if (empty((string)$interface->virtual)) {
                $result[(string)$name] = (string)$interface->if;
            }
        }
        ksort($result);
        return $result;
    }

    private static function isAssignmentMap($value)
    {
        if (!is_array($value)) {
            return false;
        }
        foreach ($value as $name => $device) {
            if (!preg_match('/^[A-Za-z][A-Za-z0-9_.-]{0,14}$/', (string)$name) || !is_scalar($device)) {
                return false;
            }
        }
        return true;
    }

    private static function isSelectedPendingRelink($pending, $managedName, $target = 'dhcpha0lagg')
    {
        return is_array($pending)
            && count($pending) === 1
            && isset($pending[$managedName])
            && is_array($pending[$managedName])
            && ($pending[$managedName]['pending_action'] ?? null) === 'relink'
            && ($pending[$managedName]['pending_if'] ?? null) === $target;
    }

    private static function syncItems(string $value)
    {
        return array_map('trim', explode(',', $value));
    }

    private function logEvent($level, $code, array $context = [])
    {
        $parts = [$code];
        foreach ($context as $key => $value) {
            if (!preg_match('/^[a-z][a-z0-9_]{0,31}$/', (string)$key) || !is_scalar($value)) {
                continue;
            }
            $text = preg_replace('/[\x00-\x1f\x7f]+/', ' ', (string)$value);
            $parts[] = $key . '=' . substr($text, 0, 160);
        }
        $message = substr(implode(' ', $parts), 0, 1024);
        try {
            if ($this->eventLogger === null) {
                $this->eventLogger = new Syslog('dhcp-interface-ha', null, LOG_LOCAL4);
            }
            if ($level === 'error') {
                $this->eventLogger->error($message);
            } else {
                $this->eventLogger->info($message);
            }
        } catch (\Throwable $exception) {
            // Logging is observational and must not interfere with setup fencing.
        }
    }

    private static function hasFields($value, array $required)
    {
        if (!is_array($value) || count($value) !== count($required)) {
            return false;
        }
        foreach ($required as $field) {
            if (!array_key_exists($field, $value) || !is_scalar($value[$field])) {
                return false;
            }
        }
        return true;
    }

    private static function canonical(Shared $shared, Local $local)
    {
        return [
            'dhcphashared' => [
                'enabled' => (string)$shared->enabled,
                'shared_mac' => (string)$shared->shared_mac,
                'failback_delay' => (string)$shared->failback_delay,
            ],
            'dhcphalocal' => [
                'managed_interface' => (string)$local->managed_interface,
                'carrier' => (string)$local->carrier,
            ],
        ];
    }

    private static function revision(Shared $shared, Local $local)
    {
        return hash(
            'sha256',
            json_encode(self::canonical($shared, $local), JSON_UNESCAPED_SLASHES | JSON_INVALID_UTF8_SUBSTITUTE)
        );
    }

    private static function identityChanged(Shared $oldShared, Local $oldLocal, Shared $newShared, Local $newLocal)
    {
        return (string)$oldLocal->managed_interface !== (string)$newLocal->managed_interface
            || strtolower(trim((string)$oldShared->shared_mac)) !== strtolower(trim((string)$newShared->shared_mac))
            || trim((string)$oldLocal->carrier) !== trim((string)$newLocal->carrier);
    }

    private static function managedInterfaceReason($config, $name, $interface)
    {
        if (empty((string)$interface->enable)) {
            return gettext('enable the logical interface');
        }
        if ((string)$interface->ipaddr !== 'dhcp') {
            return gettext('configure enabled IPv4 DHCP');
        }
        if (!in_array(strtolower(trim((string)$interface->ipaddrv6)), ['', 'none'], true)) {
            return gettext('disable IPv6 for this experimental release');
        }
        if (!empty((string)$interface->spoofmac)) {
            return gettext('clear its native spoof MAC');
        }
        foreach ($config->virtualip->vip ?? [] as $vip) {
            if ((string)$vip->mode === 'carp' && empty((string)$vip->disabled) && (string)$vip->interface === (string)$name) {
                return gettext('move CARP VIPs to another logical interface');
            }
        }
        return null;
    }

    private static function modelValidations($model, $root)
    {
        $result = [];
        foreach ($model->performValidation(false) as $message) {
            $field = basename(str_replace('.', '/', $message->getField()));
            $result[$root . '.' . $field] = $message->getMessage();
        }
        return $result;
    }

    private static function collectObservations()
    {
        $backend = new Backend();
        $start = microtime(true);
        $sources = [];
        foreach ([
            'status' => 'dhcp_interface_ha status',
            'assign_options' => 'interface list assign-opts',
            'ifconfig' => 'interface list ifconfig',
        ] as $name => $event) {
            $remaining = 15 - (microtime(true) - $start);
            // Backend::configdRun uses a two-second stream poll. Reserve that
            // time inside the total budget and keep socket connection bounded.
            if ($remaining < 3) {
                $sources[$name] = null;
                continue;
            }
            $timeout = min(3, max(1, (int)floor($remaining - 2)));
            try {
                $raw = $backend->configdRun($event, false, $timeout, 1);
                $decoded = json_decode($raw, true);
                $sources[$name] = is_array($decoded) && $decoded !== [] ? $decoded : null;
            } catch (\Throwable $exception) {
                $sources[$name] = null;
            }
        }
        return ['collected_at' => $start, 'sources' => $sources];
    }

    private static function crossValidations(
        Shared $oldShared,
        Local $oldLocal,
        Shared $shared,
        Local $local,
        $config,
        array $observations,
        $needsEvidence
    ) {
        $errors = [];
        $wasEnabled = !empty((string)$oldShared->enabled);
        $enabled = !empty((string)$shared->enabled);
        $identityChanged = self::identityChanged($oldShared, $oldLocal, $shared, $local);
        $managedName = trim((string)$local->managed_interface);
        $carrier = trim((string)$local->carrier);

        if ($wasEnabled && $identityChanged) {
            foreach ([
                'dhcphalocal.managed_interface' => gettext('Disable and verify fencing, save, then change the managed interface.'),
                'dhcphashared.shared_mac' => gettext('Disable and verify fencing, save, then change the shared MAC.'),
                'dhcphalocal.carrier' => gettext('Disable and verify fencing, save, then change the local carrier.'),
            ] as $field => $message) {
                if (
                    ($field === 'dhcphalocal.managed_interface' && (string)$oldLocal->managed_interface !== (string)$local->managed_interface)
                    || ($field === 'dhcphashared.shared_mac' && strtolower(trim((string)$oldShared->shared_mac)) !== strtolower(trim((string)$shared->shared_mac)))
                    || ($field === 'dhcphalocal.carrier' && trim((string)$oldLocal->carrier) !== $carrier)
                ) {
                    $errors[$field] = $message;
                }
            }
            return $errors;
        }

        if ($identityChanged) {
            $status = $observations['sources']['status'] ?? null;
            $age = microtime(true) - ($observations['collected_at'] ?? 0);
            $statusValid = self::hasRuntimeStatusShape($status);
            $ifconfig = $observations['sources']['ifconfig'] ?? null;
            $ifconfigValid = self::isInterfaceMap($ifconfig);
            $deviceAbsent = $ifconfigValid && !array_key_exists('dhcpha0lagg', $ifconfig);
            $inventoryContradictsStatus = false;
            if ($statusValid && $ifconfigValid) {
                $inventoryHasDevice = array_key_exists('dhcpha0lagg', $ifconfig);
                $statusHasDevice = !empty($status['dhcpha']['exists']);
                $runtimeMembers = $ifconfig['dhcpha0lagg']['laggport'] ?? [];
                $inventoryContradictsStatus = $inventoryHasDevice !== $statusHasDevice
                    || ($inventoryHasDevice && (!is_array($runtimeMembers) || $runtimeMembers !== []));
            }
            $statusDetached = $statusValid
                && in_array($status['actual_attachment'] ?? '', ['FENCED', 'UNMANAGED'], true)
                && $status['dhcpha']['lagg_members'] === []
                && (empty($status['dhcpha']['exists']) || !empty($status['owned']))
                && !$inventoryContradictsStatus;
            $detached = $age <= 15 && ($statusValid ? $statusDetached : $deviceAbsent);
            if (!$detached) {
                $errors['dhcphashared.shared_mac'] = gettext('Fresh runtime inventory must show the plugin device absent, or controller status must verify it detached, before changing interface identity.');
            }
        }

        if (!$enabled) {
            return $errors;
        }

        if (!$needsEvidence || microtime(true) - ($observations['collected_at'] ?? 0) > 15) {
            $errors['dhcphashared.enabled'] = gettext('Fresh local runtime observations are required before enabling.');
            return $errors;
        }

        $sources = $observations['sources'] ?? [];
        $devices = $sources['assign_options'] ?? null;
        $ifconfig = $sources['ifconfig'] ?? null;
        $runtime = $sources['status'] ?? null;
        if (
            !is_array($devices)
            || !is_array($ifconfig)
            || !self::hasRuntimeStatusShape($runtime)
        ) {
            $errors['dhcphashared.enabled'] = gettext('Local runtime observations are unavailable; settings were not saved.');
            return $errors;
        }
        if (!self::isInterfaceMap($devices) || !self::isInterfaceMap($ifconfig)) {
            $errors['dhcphashared.enabled'] = gettext('Local runtime interface observations have an invalid shape; settings were not saved.');
            return $errors;
        }

        if (!preg_match('/^[A-Za-z][A-Za-z0-9_.-]{0,14}$/', $managedName) || empty($config->interfaces->$managedName)) {
            $errors['dhcphalocal.managed_interface'] = gettext('Select an existing logical OPNsense interface.');
            return $errors;
        }
        if (!$wasEnabled && ($runtime['actual_attachment'] ?? null) !== 'FENCED') {
            $errors['dhcphashared.enabled'] = gettext('Prepare and verify the owned device is detached before enabling.');
        }
        $managed = $config->interfaces->$managedName;
        if (empty((string)$managed->enable) || (string)$managed->ipaddr !== 'dhcp') {
            $errors['dhcphalocal.managed_interface'] = gettext('The managed interface must be enabled and configured for IPv4 DHCP.');
        }
        if (!in_array(strtolower(trim((string)$managed->ipaddrv6)), ['', 'none'], true)) {
            $errors['dhcphalocal.managed_interface'] = gettext('IPv6 is not supported on the managed interface.');
        }
        $assignments = [];
        foreach ($config->interfaces->children() as $name => $interface) {
            if ((string)$interface->if === 'dhcpha0lagg') {
                $assignments[] = $name;
            }
        }
        if ((string)$managed->if !== 'dhcpha0lagg' || $assignments !== [$managedName]) {
            $errors['dhcphalocal.managed_interface'] = gettext('Assign only the selected managed logical interface to dhcpha0lagg before enabling.');
        }
        if (!empty((string)$managed->spoofmac)) {
            $errors['dhcphashared.shared_mac'] = gettext('Clear the native interface spoof MAC before enabling.');
        }
        if (!empty((string)$managed->hw_settings_overwrite) || !empty((string)$managed->media) || !empty((string)$managed->mediaopt)) {
            $errors['dhcphalocal.managed_interface'] = gettext('Clear native hardware overrides and media settings before enabling.');
        }

        $carpCount = 0;
        foreach ($config->virtualip->vip ?? [] as $vip) {
            if ((string)$vip->mode === 'carp' && empty((string)$vip->disabled)) {
                $carpCount++;
                if ((string)$vip->interface === $managedName) {
                    $errors['dhcphalocal.managed_interface'] = gettext('Remove CARP VIPs from the managed DHCP interface.');
                }
            }
        }
        if ($carpCount === 0) {
            $errors['dhcphashared.enabled'] = gettext('Configure native OPNsense CARP before enabling HA DHCP Interface.');
        }

        if ($carrier === '' || $carrier === 'dhcpha0lagg') {
            $errors['dhcphalocal.carrier'] = gettext('Select an eligible local Ethernet carrier before enabling.');
        } else {
            $reason = Local::carrierRuntimeEligibility($carrier, $devices, $ifconfig);
            if ($reason !== null) {
                $errors['dhcphalocal.carrier'] = sprintf(gettext('The selected carrier cannot be used because it %s.'), $reason);
            }
            if (!empty($ifconfig[$carrier]['ipv4']) || !empty($ifconfig[$carrier]['ipv6'])) {
                $errors['dhcphalocal.carrier'] = gettext('The local carrier must not have configured IPv4 or IPv6 addresses.');
            }
            $blocked = Local::blockedCarrierDevices($managedName);
            if (isset($blocked[$carrier])) {
                $errors['dhcphalocal.carrier'] = sprintf(gettext('The selected carrier cannot be used because it %s.'), $blocked[$carrier]);
            }
        }

        $device = $ifconfig['dhcpha0lagg'] ?? [];
        $runtimeDevice = $runtime['dhcpha'] ?? [];
        if (!is_array($device) || !is_array($runtimeDevice)) {
            $device = [];
            $runtimeDevice = [];
        }
        if (
            empty($runtime['owned'])
            || ($device['laggproto'] ?? '') !== 'failover'
            || ($runtimeDevice['lagg_protocol'] ?? '') !== 'failover'
        ) {
            $errors['dhcphashared.enabled'] = gettext('Prepare and verify the plugin-owned failover device before enabling.');
        }
        $runtimeCarrier = (string)($runtime['carrier']['name'] ?? '');
        if ($runtimeCarrier === $carrier && ($runtime['carrier_capable'] ?? null) !== true) {
            $errors['dhcphalocal.carrier'] = gettext('The root controller cannot verify the selected carrier is a physical Ethernet adapter.');
        }
        if (($runtime['carp_aligned'] ?? null) !== true) {
            $errors['dhcphashared.enabled'] = gettext('Configured and live native CARP instances must match before enabling.');
        }

        $mac = strtolower(trim((string)$shared->shared_mac));
        foreach ($ifconfig as $name => $details) {
            if ($name === $carrier || $name === 'dhcpha0lagg') {
                continue;
            }
            if (in_array($mac, [strtolower((string)($details['macaddr'] ?? '')), strtolower((string)($details['macaddr_hw'] ?? ''))], true)) {
                $errors['dhcphashared.shared_mac'] = sprintf(gettext('The shared MAC duplicates local interface %s.'), $name);
                break;
            }
        }

        return $errors;
    }

    private static function isInterfaceMap($value)
    {
        if (!is_array($value) || $value === []) {
            return false;
        }
        foreach ($value as $name => $details) {
            if (!preg_match('/^[A-Za-z][A-Za-z0-9_.-]{0,14}$/', (string)$name) || !is_array($details)) {
                return false;
            }
        }
        return true;
    }

    private static function hasRuntimeStatusShape($value)
    {
        return is_array($value)
            && is_string($value['actual_attachment'] ?? null)
            && is_bool($value['owned'] ?? null)
            && is_array($value['carrier'] ?? null)
            && is_array($value['dhcpha'] ?? null)
            && is_bool($value['dhcpha']['exists'] ?? null)
            && is_array($value['dhcpha']['lagg_members'] ?? null)
            && (is_string($value['dhcpha']['lagg_protocol'] ?? null) || ($value['dhcpha']['lagg_protocol'] ?? null) === null)
            && is_array($value['shared_mac_collisions'] ?? null)
            && is_bool($value['carp_aligned'] ?? null);
    }

    private static function applySavedSettings()
    {
        $backend = new Backend();
        $applied = null;
        $error = gettext('Settings were saved, but the apply result is unknown. Check status before retrying.');
        $status = null;
        try {
            $raw = $backend->configdRun('dhcp_interface_ha apply', false, 15, 2);
            $apply = json_decode($raw, true);
            if (is_array($apply) && !isset($apply['error'])) {
                $applied = true;
                $error = null;
                $status = $apply;
            } elseif ($raw !== '') {
                $applied = false;
                $error = gettext('Settings were saved, but the controller could not apply them.')
                    . ' ' . (is_string($apply['error'] ?? null) ? $apply['error'] : gettext('No usable controller response was returned.'))
                    . ' ' . gettext('Review the plugin Log before using Retry Apply.');
            }
        } catch (\Throwable $exception) {
            // Readback below may explain the current state, but does not prove
            // whether this particular apply request completed.
        }
        if ($status === null) {
            try {
                $readback = json_decode($backend->configdRun('dhcp_interface_ha status', false, 5, 1), true);
                if (is_array($readback) && !isset($readback['error'])) {
                    $status = $readback;
                }
            } catch (\Throwable $exception) {
                // The saved configuration remains authoritative; status is unavailable.
            }
        }
        $result = ['applied' => $applied, 'error' => $error];
        if ($status !== null) {
            $result['status'] = $status;
        }
        return $result;
    }
}
