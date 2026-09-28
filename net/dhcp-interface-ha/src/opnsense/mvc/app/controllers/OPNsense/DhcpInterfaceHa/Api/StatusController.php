<?php

namespace OPNsense\DhcpInterfaceHa\Api;

use OPNsense\Base\ApiControllerBase;
use OPNsense\Core\Backend;
use OPNsense\Core\Config;
use OPNsense\Core\FileObject;
use OPNsense\Core\Hasync;
use OPNsense\DhcpInterfaceHa\Local;
use OPNsense\DhcpInterfaceHa\Shared;
use OPNsense\Routing\Gateways;

class StatusController extends ApiControllerBase
{
    private const OBSERVATION_BUDGET = 15.0;

    public function carriersAction()
    {
        if (!$this->request->isGet()) {
            return ['error' => gettext('GET required.')];
        }

        $config = Config::getInstance()->object();
        $local = new Local();
        $requested = (string)$this->request->getQuery('interface', null, (string)$local->managed_interface);
        if (!preg_match('/^[A-Za-z][A-Za-z0-9_.-]{0,14}$/', $requested) || empty($config->interfaces->$requested)) {
            return ['error' => gettext('Select an existing logical interface.'), 'interface' => $requested];
        }

        $backend = new Backend();
        $deadline = microtime(true) + 10;
        $errors = [];
        $devices = $this->collect($backend, 'interface list assign-opts', $deadline, $errors, 'assign_options');
        $ifconfig = $this->collect($backend, 'interface list ifconfig', $deadline, $errors, 'ifconfig');
        if (!$this->isInterfaceMap($devices)) {
            $errors['assign_options'] = gettext('Interface assignment inventory has an invalid shape.');
            $devices = [];
        }
        if (!$this->isInterfaceMap($ifconfig)) {
            $errors['ifconfig'] = gettext('Runtime interface inventory has an invalid shape.');
            $ifconfig = [];
        }
        $blocked = Local::blockedCarrierDevices($requested);
        $items = [];
        $managed = $config->interfaces->$requested;
        $managedDevice = (string)$managed->if;

        foreach ($devices as $name => $details) {
            if ($name === 'dhcpha0lagg') {
                continue;
            }
            $reason = Local::carrierRuntimeEligibility((string)$name, $devices, $ifconfig);
            if (isset($blocked[$name])) {
                $reason = $blocked[$name];
            }
            if (($details['optgroup'] ?? '') !== 'hardware' && $reason === null) {
                $reason = gettext('is not a physical Ethernet adapter');
            }
            if ($reason !== null) {
                $blocked[$name] = $reason;
            }
            $attachmentReason = $reason;
            if ($attachmentReason === null && $name === $managedDevice && $managedDevice !== 'dhcpha0lagg') {
                $attachmentReason = gettext('reassign the managed logical interface to dhcpha0lagg before attachment');
            } elseif ($attachmentReason === null && (!empty($ifconfig[$name]['ipv4']) || !empty($ifconfig[$name]['ipv6']))) {
                $attachmentReason = gettext('remove configured IP addresses from the local carrier');
            }
            $recordEligible = $reason === null;
            $attachEligible = $recordEligible && $attachmentReason === null;
            $items[$name] = [
                'name' => (string)$name,
                'label' => (string)($details['value'] ?? $name),
                'description' => (string)($ifconfig[$name]['description'] ?? $ifconfig[$name]['descr'] ?? $details['description'] ?? ''),
                'type' => (string)($details['optgroup'] ?? 'hardware'),
                'media_status' => (string)($ifconfig[$name]['status'] ?? gettext('Unknown')),
                'eligible' => $recordEligible,
                'record_eligible' => $recordEligible,
                'attachment_eligible' => $attachEligible,
                'reason' => $reason,
                'attachment_reason' => $attachmentReason,
            ];
        }

        $savedCarrier = trim((string)$local->carrier);
        if ($savedCarrier !== '' && !isset($items[$savedCarrier])) {
            $reason = $blocked[$savedCarrier] ?? Local::carrierRuntimeEligibility($savedCarrier, $devices, $ifconfig);
            if ($reason === null && empty($ifconfig[$savedCarrier])) {
                $reason = gettext('is unavailable in the current interface inventory');
            }
            $attachmentReason = $reason;
            if ($attachmentReason === null && $savedCarrier === $managedDevice && $managedDevice !== 'dhcpha0lagg') {
                $attachmentReason = gettext('reassign the managed logical interface to dhcpha0lagg before attachment');
            } elseif ($attachmentReason === null && (!empty($ifconfig[$savedCarrier]['ipv4']) || !empty($ifconfig[$savedCarrier]['ipv6']))) {
                $attachmentReason = gettext('remove configured IP addresses from the local carrier');
            }
            $items[$savedCarrier] = [
                'name' => $savedCarrier,
                'label' => sprintf(gettext('%s (saved selection)'), $savedCarrier),
                'description' => (string)($ifconfig[$savedCarrier]['description'] ?? $ifconfig[$savedCarrier]['descr'] ?? ''),
                'type' => (string)(!empty($ifconfig[$savedCarrier]['vlan']) ? 'vlan' : 'hardware'),
                'media_status' => (string)($ifconfig[$savedCarrier]['status'] ?? gettext('Unavailable')),
                'eligible' => $reason === null,
                'record_eligible' => $reason === null,
                'attachment_eligible' => $reason === null && $attachmentReason === null,
                'reason' => $reason,
                'attachment_reason' => $attachmentReason,
            ];
            if ($reason !== null) {
                $blocked[$savedCarrier] = $reason;
            }
        }
        ksort($items, SORT_NATURAL);

        $deviceName = $managedDevice;
        $spoof = strtolower(trim((string)$managed->spoofmac));
        $observedMac = strtolower((string)($ifconfig[$deviceName]['macaddr'] ?? ''));
        $mac = $spoof !== '' ? $spoof : $observedMac;
        if (!self::isUsableMac($mac)) {
            $mac = '';
        }
        return [
            'interface' => $requested,
            'items' => array_values($items),
            'blocked' => $blocked,
            'errors' => $errors,
            'managed' => [
                'identifier' => $requested,
                'description' => !empty((string)$managed->descr) ? (string)$managed->descr : strtoupper($requested),
                'current_device' => $deviceName,
                'ipv4_type' => (string)$managed->ipaddr,
                'native_spoof_mac' => $spoof,
                'effective_mac_suggestion' => $mac,
                'mac_source' => $mac === '' ? 'unavailable' : ($spoof !== '' ? 'native_spoof_mac' : 'observed_backing_device'),
            ],
        ];
    }

    public function generateMacAction()
    {
        if (!$this->request->isPost()) {
            return ['error' => gettext('POST required.')];
        }
        $this->throwReadOnly();
        $bytes = chr(0x02) . random_bytes(5);
        return ['mac' => implode(':', str_split(bin2hex($bytes), 2))];
    }

    public function environmentAction()
    {
        if (!$this->request->isGet()) {
            return ['result' => 'unavailable', 'error' => gettext('GET required.')];
        }

        $collectedAt = gmdate('c');
        $started = microtime(true);
        $deadline = $started + self::OBSERVATION_BUDGET;
        $errors = [];
        $backend = new Backend();
        $runtime = $this->collect($backend, 'dhcp_interface_ha status', $deadline, $errors, 'controller', true);
        $carpRuntime = $this->collect($backend, 'interface show carp', $deadline, $errors, 'carp');
        $addresses = $this->collect($backend, 'interface address', $deadline, $errors, 'addresses');
        $gatewayStatuses = $this->collect($backend, 'interface gateways status', $deadline, $errors, 'gateways');
        $pfsyncRuntime = $this->collect($backend, 'filter list pfsync json', $deadline, $errors, 'pfsync');
        if ($addresses !== null && !self::isAddressMap($addresses)) {
            $errors['addresses'] = gettext('Interface address observation has an invalid shape.');
            $addresses = null;
        }

        $config = Config::getInstance()->object();
        $shared = new Shared();
        $local = new Local();
        $hasync = new Hasync();
        $managedName = trim((string)$local->managed_interface);
        $managed = !empty($config->interfaces->$managedName) ? $config->interfaces->$managedName : null;
        if ($runtime !== null && !self::hasRuntimeStatusShape($runtime)) {
            $errors['controller'] = gettext('Controller status returned an invalid shape.');
            $runtime = null;
        }
        $deviceAssignments = [];
        foreach ($config->interfaces->children() as $logicalName => $interface) {
            if ((string)$interface->if === 'dhcpha0lagg') {
                $deviceAssignments[] = [
                    'identifier' => (string)$logicalName,
                    'description' => !empty((string)$interface->descr) ? (string)$interface->descr : strtoupper((string)$logicalName),
                ];
            }
        }
        $hasRequired = $runtime !== null;
        if (!$hasRequired) {
            $errors['required'] = gettext('Controller status or managed interface configuration is unavailable.');
        }

        $address = null;
        $addressSource = 'interface address';
        if (is_array($addresses) && $managed !== null) {
            foreach ($addresses[$managedName] ?? [] as $candidate) {
                if (filter_var($candidate['address'] ?? null, FILTER_VALIDATE_IP, FILTER_FLAG_IPV4)) {
                    $address = ['address' => (string)$candidate['address'], 'prefix' => (int)$candidate['bits']];
                    break;
                }
            }
        }

        $gateways = new Gateways();
        $gatewayName = $managed !== null ? trim((string)$managed->gateway) : '';
        $gatewayAddress = null;
        if ($managed !== null) {
            try {
                $gatewayName = $gateways->getInterfaceGateway($managedName, 'inet', false, 'name') ?: $gatewayName;
                $gatewayAddress = $gateways->getInterfaceGateway($managedName, 'inet');
            } catch (\Throwable $exception) {
                $errors['gateway_config'] = gettext('Native gateway configuration is unavailable.');
            }
        }
        $gateway = $this->findGatewayStatus($gatewayStatuses, $gatewayName);
        $monitorConfigured = $this->gatewayMonitorConfigured($config, $gatewayName);
        $gatewayState = !$monitorConfigured
            ? 'not_monitored'
            : (is_array($gateway) ? strtolower((string)($gateway['status'] ?? 'unknown')) : 'unknown');
        if ($gatewayState === 'none') {
            $gatewayState = 'unknown';
        }
        $gatewayResult = [
            'name' => $gatewayName,
            'address' => $gatewayAddress,
            'source' => 'native gateway configuration',
            'monitor_configured' => $monitorConfigured,
            'status' => $gatewayState,
            'status_source' => is_array($gatewayStatuses) ? 'interface gateways status' : null,
        ];
        $syncItems = array_filter(array_map('trim', explode(',', (string)$hasync->syncitems)));
        $sharedMac = strtolower(trim((string)$shared->shared_mac));
        $localCarrier = trim((string)$local->carrier);
        $readiness = $this->readiness($config, $shared, $local, $managedName, $managed, $runtime);
        $senderConfigured = trim((string)$hasync->synchronizetoip) !== '';
        $runtimeDevice = is_array($runtime) ? ($runtime['dhcpha'] ?? []) : [];
        $deviceDetached = is_array($runtime)
            && in_array($runtime['actual_attachment'] ?? '', ['FENCED', 'UNMANAGED'], true)
            && is_array($runtimeDevice['lagg_members'] ?? null)
            && $runtimeDevice['lagg_members'] === []
            && (empty($runtimeDevice['exists']) || !empty($runtime['owned']));
        $removalAllowed = $deviceAssignments === [] && $deviceDetached;
        $managedDevice = $managed !== null ? (string)$managed->if : '';
        $managedDescription = $managed !== null && !empty((string)$managed->descr)
            ? (string)$managed->descr
            : strtoupper($managedName);
        $platformVersion = $this->platformVersion();
        $optionalUnavailable = isset($errors['carp']) || isset($errors['addresses']) || isset($errors['gateways']) || isset($errors['gateway_config']) || isset($errors['pfsync']);
        $result = !$hasRequired ? 'unavailable' : ($optionalUnavailable ? 'partial' : 'ok');

        return [
            'collected_at' => $collectedAt,
            'result' => $result,
            'setup' => [
                'pending_assignment' => $this->pendingAssignmentState($managedName, $managed, $localCarrier),
            ],
            'local' => [
                'hostname' => trim((string)$config->system->hostname) ?: gethostname(),
                'platform' => php_uname('s') . ' ' . php_uname('r'),
                'opnsense_version' => $platformVersion,
                'plugin_version' => null,
            ],
            'managed' => [
                'identifier' => $managedName,
                'description' => $managedDescription,
                'device' => $managedDevice,
                'ipv4_type' => $managed !== null ? (string)$managed->ipaddr : null,
                'ipv6_type' => $managed !== null ? (string)$managed->ipaddrv6 : null,
                'enabled' => $managed !== null && !empty((string)$managed->enable),
                'native_spoof_mac' => $managed !== null ? (string)$managed->spoofmac : null,
            ],
            'controller' => $runtime === null ? [
                'running' => null,
                'stopped' => null,
                'state' => 'UNKNOWN',
                'reason_code' => 'required_observation_unavailable',
                'reason' => gettext('Controller status is unavailable.'),
            ] : [
                'running' => $runtime['controller_running'] ?? null,
                'stopped' => $runtime['stopped'] ?? null,
                'state' => (string)($runtime['state'] ?? 'UNKNOWN'),
                'reason_code' => (string)($runtime['reason_code'] ?? 'required_observation_unavailable'),
                'reason' => (string)($runtime['reason'] ?? gettext('Controller state is unknown.')),
            ],
            'attachment' => $runtime === null ? [
                'desired' => null, 'actual' => null, 'owned' => null,
                'configured_shared_mac' => $sharedMac,
                'carrier' => null, 'members' => null, 'device' => null,
            ] : [
                'desired' => $runtime['desired_attachment'] ?? null,
                'actual' => $runtime['actual_attachment'] ?? null,
                'owned' => $runtime['owned'] ?? null,
                'configured_shared_mac' => $sharedMac,
                'carrier' => [
                    'selected' => $localCarrier,
                    'exists' => $runtime['carrier']['exists'] ?? null,
                    'link_up' => $runtime['carrier']['link_up'] ?? null,
                    'mac' => $runtime['carrier']['mac'] ?? null,
                    'mtu' => $runtime['carrier']['mtu'] ?? null,
                ],
                'receive_mode' => $this->receiveMode($runtime),
                'members' => $runtime['dhcpha']['lagg_members'] ?? null,
                'device' => [
                    'name' => 'dhcpha0lagg',
                    'exists' => $runtime['dhcpha']['exists'] ?? null,
                    'protocol' => $runtime['dhcpha']['lagg_protocol'] ?? null,
                    'mac' => $runtime['dhcpha']['mac'] ?? null,
                    'mtu' => $runtime['dhcpha']['mtu'] ?? null,
                    'promiscuous' => $runtime['dhcpha']['promiscuous'] ?? null,
                ],
            ],
            'carp' => $runtime === null ? null : [
                'role' => $runtime['global_role'] ?? 'INDETERMINATE',
                'allowed' => $runtime['carp_allowed'] ?? null,
                'maintenance' => $runtime['carp_maintenance'] ?? null,
                'demotion' => is_array($carpRuntime) && is_numeric($carpRuntime['demotion'] ?? null) ? (int)$carpRuntime['demotion'] : null,
                'demotion_source' => 'interface show carp',
                'preemption_enabled' => empty((string)$hasync->disablepreempt),
                'expected_instances' => $runtime['expected_carp_instances'] ?? null,
                'live_instances' => $runtime['live_carp_instances'] ?? null,
                'aligned' => $runtime['carp_aligned'] ?? null,
            ],
            'connection' => [
                'ipv4' => ['available' => is_array($addresses), 'address' => $address, 'source' => $addressSource],
                'dhcp' => [
                    'available' => false,
                    'state' => 'unavailable',
                    'source' => null,
                    'reason' => gettext('Native DHCP lease details are not exposed by this read-only observation.'),
                ],
                'gateway' => $gatewayResult,
                'link_status' => $runtime['carrier']['link_up'] ?? null,
            ],
            'ha' => [
                'pfsync' => [
                    'configured_interface' => (string)$hasync->pfsyncinterface,
                    'configured_peer' => (string)$hasync->pfsyncpeerip,
                    'version' => (string)$hasync->pfsyncversion,
                    'defer' => !empty((string)$hasync->pfsyncdefer),
                    'runtime_available' => is_array($pfsyncRuntime),
                    'runtime_source' => 'filter list pfsync json',
                    'runtime' => $this->safePfsync($pfsyncRuntime),
                ],
                'xmlrpc' => [
                    'target' => (string)$hasync->synchronizetoip,
                    'sender_configured' => $senderConfigured,
                    'source' => 'native HA configuration',
                    'plugin_settings_sync' => in_array('dhcp-interface-ha', $syncItems, true),
                    'sync_items_configured' => !empty($syncItems),
                ],
                'peer_readiness' => 'unverified',
                'peer_reason' => gettext('Peer plugin readiness is not verified from this node. Check the other node directly.'),
            ],
            'removal' => [
                'allowed' => $removalAllowed,
                'assignments' => $deviceAssignments,
                'detached' => $deviceDetached,
                'reason' => $removalAllowed
                    ? gettext('No logical assignment uses dhcpha0lagg and the plugin path is verified detached.')
                    : gettext('Reassign every logical interface away from dhcpha0lagg and verify its owned device has no members before removing the package.'),
            ],
            'readiness' => $readiness,
            'summary' => $this->readinessSummary($managedName, $managed, $localCarrier, $shared, $runtime, $readiness),
            'errors' => $errors,
        ];
    }

    public function snapshotAction()
    {
        if (!$this->request->isGet()) {
            return ['error' => gettext('GET required.')];
        }
        return $this->environmentAction();
    }

    private function collect(Backend $backend, $event, &$deadline, array &$errors, $name, $required = false)
    {
        $remaining = $deadline - microtime(true);
        // Backend::configdRun uses a two-second stream poll; reserve it from
        // the shared deadline and bound configd socket connection to one second.
        if ($remaining < 3) {
            $errors[$name] = gettext('Observation deadline expired.');
            return null;
        }
        $timeout = min(3, max(1, (int)floor($remaining - 2)));
        try {
            $raw = $backend->configdRun($event, false, $timeout, 1);
            $result = json_decode($raw, true);
            if ($raw === '' || !is_array($result)) {
                $errors[$name] = gettext('Observation source returned unavailable or invalid data.');
                return null;
            }
            if ($required && isset($result['error'])) {
                $errors[$name] = gettext('Required controller observation failed.');
                return null;
            }
            return $result;
        } catch (\Throwable $exception) {
            $errors[$name] = gettext('Observation source could not be reached.');
            return null;
        }
    }

    private function readiness($config, Shared $shared, Local $local, $managedName, $managed, $runtime)
    {
        $enabled = !empty((string)$shared->enabled);
        $carrier = trim((string)$local->carrier);
        $managedDevice = $managed !== null ? (string)$managed->if : '';
        $managedExists = $managed !== null;
        $hasManagedSelection = trim((string)$managedName) !== '';
        $assigned = $managedExists && $managedDevice === 'dhcpha0lagg';
        $assignmentCount = 0;
        foreach ($config->interfaces->children() as $interface) {
            if ((string)$interface->if === 'dhcpha0lagg') {
                $assignmentCount++;
            }
        }
        $ipv4Ok = $managedExists && !empty((string)$managed->enable) && (string)$managed->ipaddr === 'dhcp';
        $ipv6Ok = $managedExists && in_array(strtolower(trim((string)$managed->ipaddrv6)), ['', 'none'], true);
        $spoofOk = $managedExists && empty((string)$managed->spoofmac);
        $hardwareOk = $managedExists && empty((string)$managed->hw_settings_overwrite)
            && empty((string)$managed->media) && empty((string)$managed->mediaopt);
        $carpVips = 0;
        $managedCarp = false;
        foreach ($config->virtualip->vip ?? [] as $vip) {
            if ((string)$vip->mode === 'carp' && empty((string)$vip->disabled)) {
                $carpVips++;
                $managedCarp = $managedCarp || (string)$vip->interface === $managedName;
            }
        }
        $device = is_array($runtime) ? ($runtime['dhcpha'] ?? []) : [];
        $carrierSafe = is_array($runtime) ? !empty($runtime['carrier_safe']) : null;
        $carrierCapable = is_array($runtime) ? ($runtime['carrier_capable'] ?? null) : null;
        $sharedMac = strtolower(trim((string)$shared->shared_mac));
        $macSyntaxValid = self::isUsableMac($sharedMac);
        $collisionInventoryAvailable = is_array($runtime)
            && array_key_exists('shared_mac_collisions', $runtime)
            && self::isCollisionInventory($runtime['shared_mac_collisions']);
        $macCollisionFree = $collisionInventoryAvailable
            ? empty($runtime['shared_mac_collisions'])
            : null;
        $validMac = !$macSyntaxValid ? false : $macCollisionFree;
        $members = $device['lagg_members'] ?? null;
        $deviceExists = is_array($runtime) && !empty($device['exists']);
        $deviceTopology = $deviceExists ? (
            ($device['lagg_protocol'] ?? '') === 'failover'
            && is_array($members)
            && ($members === [] || $members === [$carrier])
        ) : null;
        $hasync = new Hasync();
        $senderConfigured = trim((string)$hasync->synchronizetoip) !== '';
        $pluginSyncEnabled = $this->pluginSyncEnabled($hasync);
        $assignmentCountMatches = $assigned && $assignmentCount === 1;
        $initialAssignment = $hasManagedSelection
            && !$assigned
            && $assignmentCount === 0
            && $managedDevice !== ''
            && $managedDevice !== 'dhcpha0lagg'
            && $carrier === '';
        $capturedOriginalAssignment = $hasManagedSelection
            && !$assigned
            && $assignmentCount === 0
            && $carrier !== ''
            && $managedDevice === $carrier;
        $assignmentConflict = $hasManagedSelection
            && !$assignmentCountMatches
            && !$initialAssignment
            && !$capturedOriginalAssignment;
        $carrierSelected = $carrier !== '' && $carrier !== 'dhcpha0lagg';
        $receiveMode = $this->receiveMode($runtime);
        $receivePassed = $receiveMode['status'] === 'pass'
            ? true
            : ($receiveMode['status'] === 'fail' ? false : null);
        $controllerRunning = is_array($runtime) && !empty($runtime['controller_running']);
        $controllerCanRepair = $enabled && $controllerRunning && empty($runtime['stopped']) && !empty($runtime['owned']);
        $missingDeviceCanBeRecreated = $enabled && $controllerRunning && empty($runtime['stopped']) && !$deviceExists
            && !empty($runtime['managed_by_dhcpha']);
        $failbackConfigured = (string)$shared->failback_delay === '0';
        $pfsyncConfigured = trim((string)$hasync->pfsyncinterface) !== '';

        return [
            $this->check(
                'managed_interface', 'local', 'blocker', $managedExists, 'assignment',
                $managedExists
                    ? gettext('The selected logical interface exists in native configuration.')
                    : ($hasManagedSelection
                        ? gettext('The selected logical interface is unavailable in native configuration.')
                        : ($enabled
                            ? gettext('Enablement is shared, but this node still needs its own logical interface selected and configured.')
                            : gettext('No managed logical interface is configured; None is a valid disabled state.'))),
                action: $hasManagedSelection || $enabled ? 'settings' : null, responsibility: 'user', resolution: $hasManagedSelection || $enabled ? 'user_action' : 'none', relevant: $hasManagedSelection || $enabled
            ),
            $this->check(
                'managed_assignment', 'local', 'blocker', $assignmentCountMatches, 'migration',
                $assignmentCountMatches
                    ? gettext('Only the selected logical interface uses dhcpha0lagg.')
                    : ($initialAssignment || $capturedOriginalAssignment
                        ? gettext('Configure interface to move the selected logical interface through native assignment machinery.')
                        : gettext('The native assignment does not match the saved HA DHCP Interface mapping; inspect the assignment before continuing.')),
                action: $initialAssignment || $capturedOriginalAssignment ? 'configure' : ($assignmentConflict ? 'interface_assignments' : null),
                responsibility: 'plugin',
                resolution: $initialAssignment || $capturedOriginalAssignment ? 'user_action' : ($assignmentConflict ? 'investigate' : 'none'),
                relevant: $hasManagedSelection
            ),
            $this->check('managed_ipv4', 'local', 'blocker', $managedExists ? $ipv4Ok : null, 'native_interface', gettext('The managed interface uses enabled IPv4 DHCP.'), action: 'interface_assignments', responsibility: 'native', resolution: 'user_action', relevant: $hasManagedSelection && $managedExists),
            $this->check('managed_ipv6', 'local', 'blocker', $managedExists ? $ipv6Ok : null, 'native_interface', gettext('The managed interface has no unsupported IPv6 configuration.'), action: 'interface_assignments', responsibility: 'native', resolution: 'user_action', relevant: $hasManagedSelection && $managedExists),
            $this->check(
                'carrier_selection', 'local', 'blocker', $carrierSelected, 'adapter',
                $carrierSelected
                    ? gettext('The saved local carrier is available for verification.')
                    : ($initialAssignment
                        ? gettext('The original carrier will be captured by Configure interface; do not select or maintain the LAGG manually.')
                        : gettext('The saved carrier mapping is unavailable; its original device cannot be inferred safely.')),
                action: $initialAssignment ? 'configure' : ($carrierSelected ? null : 'settings'),
                responsibility: 'plugin', resolution: $initialAssignment ? 'user_action' : ($carrierSelected ? 'none' : 'investigate'),
                relevant: $hasManagedSelection
            ),
            $this->check(
                'carrier_capability', 'local', 'blocker', $carrierSelected && is_array($runtime) ? $carrierCapable : null, 'adapter',
                gettext('The saved carrier is a supported physical Ethernet adapter.'),
                action: $carrierSelected ? 'settings' : null, responsibility: 'environment', resolution: 'investigate', relevant: $hasManagedSelection && $carrierSelected
            ),
            $this->check(
                'carrier_exclusive', 'local', 'blocker', $capturedOriginalAssignment ? true : ($carrierSelected && is_array($runtime) ? $carrierSafe : null), 'adapter',
                $capturedOriginalAssignment
                    ? gettext('The original carrier remains on its logical interface until Configure interface completes the confirmed migration.')
                    : gettext('The carrier is reserved exclusively for HA DHCP Interface.'),
                action: $capturedOriginalAssignment ? 'configure' : 'interface_assignments',
                responsibility: 'native', resolution: 'user_action', relevant: $hasManagedSelection && $carrierSelected
            ),
            $this->check(
                'device_ownership', 'local', 'blocker', is_array($runtime) ? ($runtime['owned'] ?? null) : null, 'device',
                !is_array($runtime)
                    ? gettext('Controller ownership observation is unavailable.')
                    : ($deviceExists && empty($runtime['owned'])
                        ? gettext('A device named dhcpha0lagg exists but its plugin ownership is unverified; preserve it and investigate.')
                        : ($deviceExists
                            ? gettext('DHCP HA device ownership is verified.')
                            : ($missingDeviceCanBeRecreated
                                ? gettext('The owned device is absent; automatic safe recreation is expected, refresh status to verify.')
                                : gettext('The plugin device is absent and will be prepared through Configure interface or a guarded recovery action.')))),
                action: $deviceExists ? null : (($controllerCanRepair || $missingDeviceCanBeRecreated) ? null : ($hasManagedSelection && !$carrierSelected ? 'configure' : 'prepare')),
                responsibility: 'plugin',
                resolution: $deviceExists && empty($runtime['owned']) ? 'investigate' : ($deviceExists ? 'none' : (($controllerCanRepair || $missingDeviceCanBeRecreated) ? 'automatic' : ($hasManagedSelection && !$carrierSelected ? 'user_action' : 'retry'))),
                relevant: $hasManagedSelection
                    && !$initialAssignment
                    && !$capturedOriginalAssignment
                    && ($deviceExists || $enabled || $carrier !== '')
            ),
            $this->check(
                'device_topology', 'local', 'blocker', $deviceTopology, 'device',
                gettext('The owned DHCP HA device has failover protocol and no unexpected members.'),
                action: null, responsibility: 'plugin', resolution: 'investigate', relevant: $hasManagedSelection && $deviceExists
            ),
            $this->check(
                'shared_mac', 'local', 'blocker', $validMac, 'identity',
                !$macSyntaxValid
                    ? gettext('Shared MAC must be a usable unicast address.')
                    : ($macCollisionFree === null
                        ? gettext('Shared MAC collision inventory is unavailable; refresh status before changing identity.')
                        : ($macCollisionFree
                            ? gettext('Shared MAC is a usable unicast address with no observed collision.')
                            : gettext('Shared MAC conflicts with another observed interface; investigate before changing identity.'))),
                action: !$macSyntaxValid ? 'settings' : null,
                responsibility: !$macSyntaxValid ? 'user' : ($macCollisionFree === true ? 'user' : 'environment'),
                resolution: !$macSyntaxValid ? 'user_action' : ($macCollisionFree === false ? 'investigate' : 'none'),
                relevant: $hasManagedSelection || $enabled
            ),
            $this->check('native_spoof_mac', 'local', 'blocker', $managedExists ? $spoofOk : null, 'native_interface', gettext('Native spoof MAC must be clear on the managed interface.'), action: 'interface_assignments', responsibility: 'native', resolution: 'user_action', relevant: $hasManagedSelection && $managedExists),
            $this->check('hardware_media', 'local', 'blocker', $managedExists ? $hardwareOk : null, 'native_interface', gettext('Unsupported native hardware and media overrides must be clear.'), action: 'interface_assignments', responsibility: 'native', resolution: 'user_action', relevant: $hasManagedSelection && $managedExists),
            $this->check('managed_carp_vips', 'local', 'blocker', $managedExists ? !$managedCarp : null, 'native_carp', gettext('The managed logical interface has no CARP VIP.'), action: 'carp', responsibility: 'native', resolution: 'user_action', relevant: $hasManagedSelection && $managedExists),
            $this->check('carp_inventory', 'local', 'blocker', is_array($runtime) ? ($runtime['carp_aligned'] ?? null) : null, 'native_carp', gettext('Configured CARP instances match live instances.'), action: 'carp', responsibility: 'native', resolution: 'investigate', relevant: $hasManagedSelection),
            $this->check('failback_policy', 'local', 'blocker', $failbackConfigured, 'policy', gettext('Delayed failback must be reset to zero through Save & Apply.'), action: $failbackConfigured ? null : 'reset_failback', responsibility: 'user', resolution: 'user_action', relevant: $hasManagedSelection),
            $this->check('pfsync_context', 'local', 'info', $pfsyncConfigured, 'native_ha', $pfsyncConfigured ? gettext('pfsync is configured as optional session-state context.') : gettext('pfsync is optional and is not configured.'), action: null, responsibility: 'native', resolution: 'none', relevant: false),
            $this->check(
                'xmlrpc_selection', 'local', 'warning', !$senderConfigured || $pluginSyncEnabled, 'native_ha',
                !$senderConfigured
                    ? gettext('No native XMLRPC sender is configured; plugin synchronization selection is optional.')
                    : ($pluginSyncEnabled
                        ? gettext('Shared plugin settings are selected for this configured XMLRPC sender.')
                        : gettext('Include HA DHCP Interface in the configured native XMLRPC sender selection.')),
                action: $senderConfigured && !$pluginSyncEnabled ? 'enable_sync' : null,
                responsibility: 'user', resolution: $senderConfigured && !$pluginSyncEnabled ? 'user_action' : 'none',
                relevant: $senderConfigured && !$pluginSyncEnabled
            ),
            $this->check('peer_readiness', 'peer', 'info', 'unknown', 'peer_check', gettext('Peer plugin readiness is not verified from this node.'), action: null, responsibility: 'environment', resolution: 'none', relevant: false),
            $this->check(
                'receive_mode', 'local', 'blocker', $receivePassed, 'device',
                $receiveMode['required'] === false
                    ? gettext('Shared-MAC receive mode is not required while the carrier is intentionally detached.')
                    : ($receivePassed === true
                        ? gettext('Both attached interfaces receive frames for the shared MAC.')
                        : ($receivePassed === false
                            ? ($controllerCanRepair
                                ? gettext('Shared-MAC receive mode is missing; automatic repair is expected, refresh status to verify.')
                                : gettext('Shared-MAC receive mode is missing; inspect ownership and controller evidence before recovery.'))
                            : gettext('Shared-MAC receive mode cannot be verified from the current observation.'))),
                action: null,
                responsibility: 'plugin',
                resolution: $receivePassed === false ? ($controllerCanRepair ? 'automatic' : 'investigate') : 'none',
                relevant: $receiveMode['required'] === true || ($hasManagedSelection && $enabled && !is_array($runtime))
            ),
        ];
    }

    private static function isUsableMac($mac)
    {
        return (bool)preg_match('/^(?:[0-9a-f]{2}:){5}[0-9a-f]{2}$/', $mac)
            && $mac !== '00:00:00:00:00:00'
            && !(hexdec(substr($mac, 0, 2)) & 1);
    }

    private static function isCollisionInventory($value)
    {
        if (!is_array($value)) {
            return false;
        }
        $index = 0;
        foreach ($value as $key => $device) {
            if ($key !== $index || !is_string($device)) {
                return false;
            }
            $index++;
        }
        return true;
    }

    private function check($code, $scope, $severity, $passed, $stage, $message, $action, $responsibility, $resolution, $relevant)
    {
        $status = $passed === null ? 'unknown' : ($passed === 'unknown' ? 'unknown' : ($passed ? 'pass' : 'fail'));
        $relevant = (bool)$relevant;
        if ($status !== 'fail' || !$relevant) {
            $action = null;
            $resolution = 'none';
        }
        return [
            'code' => $code,
            'scope' => $scope,
            'severity' => $severity,
            'status' => $status,
            'responsibility' => $responsibility,
            'resolution' => $resolution,
            'relevant' => $relevant,
            'attempt' => null,
            'stage' => $stage,
            'message' => $message,
            'action' => $action,
        ];
    }

    private function receiveMode($runtime)
    {
        $carrier = is_array($runtime) ? ($runtime['carrier']['promiscuous'] ?? null) : null;
        $device = is_array($runtime) ? ($runtime['dhcpha']['promiscuous'] ?? null) : null;
        $activeEligible = is_array($runtime)
            && !empty($runtime['enabled'])
            && !empty($runtime['managed_by_dhcpha'])
            && !empty($runtime['owned'])
            && empty($runtime['stopped'])
            && ($runtime['desired_attachment'] ?? null) === 'ATTACHED'
            && ($runtime['global_role'] ?? null) === 'MASTER'
            && !empty($runtime['carp_allowed'])
            && empty($runtime['carp_maintenance'])
            && !empty($runtime['carp_aligned'])
            && !empty($runtime['dhcpha']['exists'])
            && ($runtime['dhcpha']['lagg_protocol'] ?? null) === 'failover'
            && is_array($runtime['dhcpha']['lagg_members'] ?? null)
            && ($runtime['dhcpha']['lagg_members'] ?? null) === [(string)($runtime['carrier']['name'] ?? '')];
        $required = is_array($runtime)
            && (($runtime['actual_attachment'] ?? null) === 'ATTACHED' || $activeEligible);
        if (!$required) {
            $status = 'pass';
        } elseif (!is_bool($carrier) || !is_bool($device)) {
            $status = 'unknown';
        } else {
            $status = $carrier && $device ? 'pass' : 'fail';
        }
        return [
            'required' => is_array($runtime) ? $required : null,
            'status' => is_array($runtime) ? $status : 'unknown',
            'carrier_promiscuous' => is_bool($carrier) ? $carrier : null,
            'device_promiscuous' => is_bool($device) ? $device : null,
        ];
    }

    private function readinessSummary($managedName, $managed, $carrier, Shared $shared, $runtime, array $readiness)
    {
        if (!is_array($runtime)) {
            return ['state' => 'status_unavailable', 'reason_code' => 'required_observation_unavailable'];
        }

        $device = $runtime['dhcpha'] ?? [];
        $actual = $runtime['actual_attachment'] ?? null;
        $owned = !empty($runtime['owned']);
        $unsafeAttachment = $actual === 'UNVERIFIED'
            || ($actual === 'ATTACHED' && (
                !$owned
                || empty((string)$shared->enabled)
                || !empty($runtime['carp_maintenance'])
                || empty($runtime['carp_allowed'])
                || ($runtime['global_role'] ?? '') !== 'MASTER'
            ));
        if ($unsafeAttachment) {
            return [
                'state' => 'needs_attention',
                'reason_code' => (string)($runtime['reason_code'] ?? 'attachment_unverified'),
            ];
        }

        $detached = in_array($actual, ['FENCED', 'UNMANAGED'], true)
            && is_array($device['lagg_members'] ?? null)
            && $device['lagg_members'] === []
            && (empty($device['exists']) || $owned);
        $enabled = !empty((string)$shared->enabled);
        $managedDevice = $managed !== null ? (string)$managed->if : '';
        $selected = trim((string)$managedName) !== '';
        $originalAssignmentStillInPlace = $managed !== null
            && $carrier !== ''
            && $managedDevice === $carrier
            && $managedDevice !== 'dhcpha0lagg';
        if (!$enabled && $detached && !$selected) {
            return [
                'state' => 'not_configured',
                'reason_code' => 'no_managed_interface',
            ];
        }

        $firstFailure = null;
        foreach ($readiness as $check) {
            if (($check['scope'] ?? '') !== 'local'
                || ($check['severity'] ?? '') !== 'blocker'
                || empty($check['relevant'])) {
                continue;
            }
            if (($check['status'] ?? '') === 'unknown') {
                return ['state' => 'status_unavailable', 'reason_code' => 'required_observation_unavailable'];
            }
            if (($check['status'] ?? '') === 'fail' && $firstFailure === null) {
                $firstFailure = (string)$check['code'];
            }
        }
        if (!$enabled && $detached
            && (($managed !== null && $carrier === '' && $managedDevice !== 'dhcpha0lagg') || $originalAssignmentStillInPlace)) {
            return ['state' => 'not_configured', 'reason_code' => 'interface_setup_required'];
        }
        if ($firstFailure !== null) {
            return ['state' => 'needs_attention', 'reason_code' => $firstFailure];
        }

        if (in_array($runtime['state'] ?? '', ['ACTIVE', 'STANDBY', 'FENCED', 'DISABLED'], true)) {
            return ['state' => 'ready', 'reason_code' => (string)($runtime['reason_code'] ?? 'local_checks_pass')];
        }
        return [
            'state' => 'needs_attention',
            'reason_code' => (string)($runtime['reason_code'] ?? 'controller_not_ready'),
        ];
    }

    private function pendingAssignmentState($managedName, $managed, $carrier)
    {
        try {
            // Native NetworkInterface uses this queue. Read it without building
            // its form model, which fetches device options through configd.
            // A busy writer or malformed snapshot is unknown, never "clear".
            $pending = is_file('/tmp/.interfaces.todo')
                ? (new FileObject('/tmp/.interfaces.todo', 'r', null, LOCK_SH | LOCK_NB))->readJson()
                : [];
            if (!is_array($pending)) {
                return ['available' => false, 'state' => 'unknown'];
            }
            if ($pending === []) {
                return ['available' => true, 'state' => 'clear'];
            }
            // A pending target alone is not retry authority: the saved local
            // mapping must explain its original carrier and committed device.
            $selectedRelink = $carrier !== '' && $managed !== null
                && in_array((string)$managed->if, [$carrier, 'dhcpha0lagg'], true)
                && count($pending) === 1
                && is_array($pending[$managedName] ?? null)
                && ($pending[$managedName]['pending_action'] ?? null) === 'relink'
                && ($pending[$managedName]['pending_if'] ?? null) === 'dhcpha0lagg';
            return ['available' => true, 'state' => $selectedRelink ? 'selected_relink' : 'conflict'];
        } catch (\Throwable $exception) {
            return ['available' => false, 'state' => 'unknown'];
        }
    }

    private function pluginSyncEnabled(Hasync $hasync)
    {
        return in_array('dhcp-interface-ha', array_filter(array_map('trim', explode(',', (string)$hasync->syncitems))), true);
    }

    private function safePfsync($data)
    {
        if (!is_array($data)) {
            return null;
        }
        $nodes = [];
        foreach (array_slice(is_array($data['nodes'] ?? null) ? $data['nodes'] : [], 0, 16) as $node) {
            if (is_array($node) && preg_match('/^[a-fA-F0-9]{1,16}$/', (string)($node['creatorid'] ?? ''))) {
                $nodes[] = [
                    'creatorid' => (string)$node['creatorid'],
                    'this_node' => !empty($node['this']),
                ];
            }
        }
        return ['hostid' => preg_match('/^[a-fA-F0-9]{1,16}$/', (string)($data['hostid'] ?? '')) ? (string)$data['hostid'] : null, 'nodes' => $nodes];
    }

    private function platformVersion()
    {
        $version = @file_get_contents('/usr/local/opnsense/version/core');
        $product = is_string($version) ? json_decode($version, true) : null;
        return is_array($product) ? ($product['CORE_PKGVERSION'] ?? null) : null;
    }

    private function findGatewayStatus($statuses, $name)
    {
        if (!is_array($statuses) || $name === '') {
            return null;
        }
        foreach ($statuses as $key => $status) {
            if (is_array($status) && (($status['name'] ?? $key) === $name)) {
                return $status;
            }
        }
        return null;
    }

    private function gatewayMonitorConfigured($config, $name)
    {
        if ($name === '') {
            return false;
        }
        foreach ($config->gateways->gateway_item ?? [] as $gateway) {
            if ((string)$gateway->name === $name) {
                return empty((string)$gateway->monitor_disable) && !empty((string)$gateway->monitor);
            }
        }
        return false;
    }

    private function isInterfaceMap($value)
    {
        if (!is_array($value)) {
            return false;
        }
        foreach ($value as $name => $details) {
            if (!preg_match('/^[A-Za-z][A-Za-z0-9_.-]{0,14}$/', (string)$name) || !is_array($details)) {
                return false;
            }
        }
        return true;
    }

    private static function isAddressMap($value)
    {
        if (!is_array($value)) {
            return false;
        }
        foreach ($value as $name => $addresses) {
            if (!preg_match('/^[A-Za-z][A-Za-z0-9_.-]{0,14}$/', (string)$name) || !is_array($addresses)) {
                return false;
            }
            foreach ($addresses as $address) {
                // Native interface.address emits one null placeholder per
                // family when an interface has no address (normal on BACKUP).
                if (
                    is_array($address)
                    && array_key_exists('address', $address) && $address['address'] === null
                    && array_key_exists('bits', $address) && $address['bits'] === null
                    && ($address['interface'] ?? null) === (string)$name
                    && in_array($address['family'] ?? null, ['inet', 'inet6'], true)
                ) {
                    continue;
                }
                if (
                    !is_array($address)
                    || !filter_var($address['address'] ?? null, FILTER_VALIDATE_IP)
                    || !is_numeric($address['bits'] ?? null)
                    || (int)$address['bits'] < 0
                    || (int)$address['bits'] > (strpos((string)$address['address'], ':') === false ? 32 : 128)
                ) {
                    return false;
                }
            }
        }
        return true;
    }

    private static function hasRuntimeStatusShape($value)
    {
        if (!is_array($value) || isset($value['error'])) {
            return false;
        }
        foreach (['state', 'reason_code', 'reason', 'actual_attachment', 'desired_attachment', 'global_role'] as $field) {
            if (!is_string($value[$field] ?? null)) {
                return false;
            }
        }
        foreach ([
            'controller_running', 'stopped', 'carp_allowed', 'carp_maintenance', 'carp_aligned',
            'enabled', 'managed_by_dhcpha', 'owned', 'carrier_safe', 'carrier_capable',
        ] as $field) {
            if (!is_bool($value[$field] ?? null)) {
                return false;
            }
        }
        foreach (['expected_carp_instances', 'live_carp_instances', 'carrier', 'dhcpha'] as $field) {
            if (!is_array($value[$field] ?? null)) {
                return false;
            }
        }
        return is_bool($value['carrier']['exists'] ?? null)
            && is_bool($value['carrier']['link_up'] ?? null)
            && is_array($value['dhcpha']['lagg_members'] ?? null)
            && array_key_exists('exists', $value['dhcpha'])
            && is_bool($value['dhcpha']['exists'])
            && (is_string($value['dhcpha']['lagg_protocol'] ?? null) || ($value['dhcpha']['lagg_protocol'] ?? null) === null)
            && is_string($value['dhcpha']['name'] ?? null);
    }
}
