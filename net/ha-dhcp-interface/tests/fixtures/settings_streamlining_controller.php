<?php

namespace { require __DIR__ . '/api_fixture.php'; }

namespace OPNsense\Core {
    class Config
    {
        private static $instance;
        private $xml; // Working copy; a failed save never changes the committed snapshot.
        public static $committed;
        public static $locked = false;
        public static $pending = [];
        public static $appliedReceiveModes = [];
        public static $saveCount = 0;

        public static function getInstance() { return self::$instance ??= new self(); }

        public function object()
        {
            return $this->xml ??= simplexml_load_string(self::$committed->asXML());
        }

        public function lock($reload = true)
        {
            if (!self::$locked && $reload) {
                $this->xml = null;
            }
            self::$locked = true;
        }

        public function unlock() { self::$locked = false; }

        public static function receiveModes()
        {
            $result = [];
            foreach (self::$committed->interfaces->children() as $name => $node) {
                $result[$name] = array_intersect_key(\fixtureValues($node), array_flip(['promisc', 'dhcpha_original_promisc']));
            }
            return $result;
        }

        public function save($metadata = null)
        {
            if (!self::$locked) {
                throw new \RuntimeException('save without config lock');
            }
            $description = $metadata['description'] ?? '';
            $kind = match (true) {
                str_contains($description, 'configuration sync') => 'sync',
                str_contains($description, 'receive mode') => 'receive',
                is_array($metadata) => 'plugin',
                default => 'native',
            };
            if (\FixtureState::$options['fail_save'] === $kind) {
                throw new \RuntimeException('injected config save failure');
            }
            self::$committed = simplexml_load_string($this->object()->asXML());
            self::$saveCount++;
            if ($kind === 'native') {
                \FixtureState::$options['needs_reconcile'] = true;
                if ((string)self::$committed->interfaces->wan->if === 'dhcpha0lagg') {
                    \FixtureState::$options['status_unavailable'] = \FixtureState::$options['status_unavailable']
                        || \FixtureState::$options['status_unavailable_after_apply'];
                    \FixtureState::$options['managed_mismatch'] = \FixtureState::$options['managed_mismatch']
                        || \FixtureState::$options['managed_mismatch_after_apply'];
                }
            }
        }
    }

    class ACL
    {
        public function isPageAccessible($user, $route)
        {
            \FixtureState::$aclChecks[] = $route;
            return !in_array($route, \FixtureState::$options['denied_routes'], true);
        }
    }

    class Syslog
    {
        private $identity;

        public function __construct($identity, $option = null, $facility = null)
        {
            $this->identity = $identity;
        }

        public function info($message)
        {
            \FixtureState::$logs[] = ['identity' => $this->identity, 'level' => 'info', 'message' => $message];
        }

        public function error($message)
        {
            \FixtureState::$logs[] = ['identity' => $this->identity, 'level' => 'error', 'message' => $message];
        }
    }

    class Backend
    {
        public function configdRun($event, $detach = false, $timeout = 120, $connectTimeout = 10)
        {
            if (Config::$locked && !($event === 'filter reload skip_alias' && $detach)) {
                throw new \RuntimeException('configd called under config lock');
            }
            \FixtureState::$events[] = $event;
            if (!empty(\FixtureRequest::$post['progress_id'])) {
                $method = \FixtureRequest::$method;
                \FixtureRequest::$method = 'GET';
                \FixtureState::$progressReads[] = [
                    'event' => $event,
                    'progress' => (new \OPNsense\DhcpInterfaceHa\Api\SettingsController())->progressAction(\FixtureRequest::$post['progress_id']),
                ];
                \FixtureRequest::$method = $method;
            }
            if ($event === 'dhcp_interface_ha route_status') {
                return json_encode(['observed_default' => \FixtureState::$options['default_route']]);
            }
            if ($event === 'dhcp_interface_ha status') {
                if (\FixtureState::$options['status_unavailable']) {
                    throw new \RuntimeException('status unavailable');
                }
                return self::statusJson();
            }
            if ($event === '!interface list assign-opts') {
                \FixtureState::$options['assignment_cache_stale'] = false;
                $event = 'interface list assign-opts';
            }
            if ($event === 'interface list assign-opts') {
                return json_encode([
                    'hn1' => ['value' => 'hn1', 'optgroup' => 'hardware'],
                    'hn2' => ['value' => 'hn2', 'optgroup' => 'hardware'],
                    // Native list_assign_options.php advertises plugin devices
                    // before runtime preparation, so the model may cache this.
                    'dhcpha0lagg' => ['value' => 'dhcpha0lagg', 'optgroup' => 'virtual'],
                ]);
            }
            if ($event === 'interface list ifconfig') {
                $wan = ['is_physical' => true, 'macaddr' => '02:11:22:33:44:01'];
                if (\FixtureState::$options['lease_on_original'] && (string)Config::$committed->interfaces->wan->if === 'hn1') {
                    $wan['ipv4'] = ['192.0.2.15/24'];
                }
                $devices = [
                    'hn1' => $wan,
                    'hn2' => ['is_physical' => true, 'macaddr' => '02:11:22:33:44:02'],
                ];
                if (\FixtureState::$options['device_present']) {
                    // Native inventory omits laggport when the device has no members.
                    $devices['dhcpha0lagg'] = ['laggproto' => 'failover'];
                    if (\FixtureState::$options['inventory_members'] !== null) {
                        $devices['dhcpha0lagg']['laggport'] = \FixtureState::$options['inventory_members'];
                    }
                }
                return json_encode($devices);
            }
            if ($event === 'dhcp_interface_ha prepare_setup') {
                \FixtureState::$options['device_present'] = true;
                return '{"prepared":true,"detached":true,"owned":true,"device":"dhcpha0lagg"}';
            }
            if ($event === 'dhcp_interface_ha apply') {
                \FixtureState::$options['needs_reconcile'] = false;
                \FixtureState::$options['status_unavailable'] = \FixtureState::$options['status_unavailable'] || \FixtureState::$options['controller_apply'] === 'no_status';
                return match (\FixtureState::$options['controller_apply']) {
                    'failed' => '{"error":"controller apply failed"}',
                    'empty' => '',
                    'timeout', 'no_status' => throw new \RuntimeException('apply timed out'),
                    default => self::statusJson(),
                };
            }
            if ($event === 'interface apply') {
                Config::$appliedReceiveModes[] = Config::receiveModes();
                if (\FixtureState::$options['native_apply'] === 'failed') {
                    return 'FAILED';
                }
                if (\FixtureState::$options['native_apply'] === 'timeout') {
                    throw new \RuntimeException('interface apply timed out');
                }
                return 'OK';
            }
            return '{}';
        }

        private static function statusJson()
        {
            return json_encode([
                'state' => (string)Config::$committed->OPNsense->DhcpInterfaceHaShared->enabled === '1' ? \FixtureState::$options['runtime_state'] : 'DISABLED',
                'reason' => \FixtureState::$options['runtime_state'] === 'FAULT' ? 'selected carrier has no link' : 'fixture controller state',
                'actual_attachment' => (\FixtureState::$options['needs_reconcile'] && (string)Config::$committed->interfaces->wan->if === 'dhcpha0lagg') ? 'UNVERIFIED' : \FixtureState::$options['attachment'],
                'desired_attachment' => 'FENCED',
                'enabled' => (string)Config::$committed->OPNsense->DhcpInterfaceHaShared->enabled === '1',
                'owned' => \FixtureState::$options['device_present'],
                'managed_by_dhcpha' => \FixtureState::$options['device_present']
                    && (string)Config::$committed->interfaces->wan->if === 'dhcpha0lagg'
                    && !\FixtureState::$options['managed_mismatch'],
                'carrier_capable' => true,
                'carrier' => ['name' => 'hn1', 'exists' => true, 'link_up' => true, 'mac' => '02:11:22:33:44:01'],
                'dhcpha' => [
                    'name' => 'dhcpha0lagg',
                    'exists' => \FixtureState::$options['device_present'],
                    'lagg_protocol' => 'failover',
                    'lagg_members' => \FixtureState::$options['attachment'] === 'ATTACHED' ? ['hn1'] : [],
                ],
                'shared_mac_collisions' => [],
                'carp_aligned' => true,
            ]);
        }
    }

    class Hasync extends \FixtureModel
    {
        protected const ROOT = 'hasync';
        protected const DEFAULTS = ['synchronizetoip' => '', 'syncitems' => ''];
        protected const INVALID = 'invalid_sync';
        protected const ERROR = ['hasync.syncitems', 'invalid sync selection'];
    }
}

namespace OPNsense\DhcpInterfaceHa {
    class Shared extends \FixtureModel
    {
        protected const ROOT = 'DhcpInterfaceHaShared';
        protected const DEFAULTS = ['enabled' => '0', 'shared_mac' => '', 'failback_delay' => '0'];
        protected const ERROR = ['DhcpInterfaceHa.Shared.shared_mac', 'invalid settings'];
    }

    class Local extends \FixtureModel
    {
        protected const ROOT = 'DhcpInterfaceHaLocal';
        protected const DEFAULTS = ['managed_interface' => '', 'carrier' => '',
            'standby_enabled' => null, 'standby_interface' => null, 'standby_vip' => null];
        protected const ERROR = ['DhcpInterfaceHa.Local.managed_interface', 'invalid settings'];

        public static function blockedCarrierDevices($managedInterface) { return []; }

        public static function carrierRuntimeEligibility($carrier, array $devices, array $ifconfig)
        {
            return isset($devices[$carrier]) && isset($ifconfig[$carrier]) ? null : 'missing carrier';
        }
    }
}
namespace OPNsense\Interfaces {
    class NetworkInterface
    {
        private $selected = [];

        public function get_if_todo()
        {
            return \OPNsense\Core\Config::$pending;
        }

        public function setNodes(array $nodes)
        {
            $this->selected = $nodes['interface'] ?? [];
        }

        public function performValidation($full = false)
        {
            foreach ($this->selected as $props) {
                if ($props['if'] === 'hn1' && \FixtureState::$options['assignment_cache_stale']) {
                    return [new \FixtureMessage('interface.wan.if', 'Option not in list')];
                }
            }
            return [];
        }

        public function serializeToConfig($full = false, $disableValidation = false)
        {
            foreach ($this->selected as $name => $props) {
                \OPNsense\Core\Config::$pending[$name] = [
                    'pending_action' => 'relink',
                    'pending_if' => (string)$props['if'],
                ];
            }
        }

        public function flush_todo()
        {
            \OPNsense\Core\Config::$pending = [];
        }
    }
}

namespace OPNsense\Interfaces\Api {
    class AssignmentController extends \OPNsense\Base\ApiControllerBase
    {
        private $model;

        protected function getModel()
        {
            return $this->model ?? ($this->model = new \OPNsense\Interfaces\NetworkInterface());
        }

        public function reconfigureAction()
        {
            if (!$this->request->isPost()) {
                return ['status' => 'failed'];
            }
            $backend = new \OPNsense\Core\Backend();
            if (\FixtureState::$options['native_apply'] === 'failed') {
                return ['status' => 'failed'];
            }
            if (\FixtureState::$options['native_apply'] === 'timeout') {
                $backend->configdRun('interface apply');
                throw new \RuntimeException('native apply timed out');
            }
            if (trim($backend->configdRun('interface apply')) !== 'OK') {
                return ['status' => 'failed'];
            }
            \OPNsense\Core\Config::getInstance()->lock();
            foreach ($this->getModel()->get_if_todo() as $name => $props) {
                if (($props['pending_action'] ?? '') === 'relink') {
                    \OPNsense\Core\Config::getInstance()->object()->interfaces->$name->if = $props['pending_if'];
                }
            }
            \OPNsense\Core\Config::getInstance()->save();
            $this->getModel()->flush_todo();
            $backend->configdRun('filter reload skip_alias', true);
            return ['status' => 'ok'];
        }
    }
}

namespace {
    use OPNsense\Core\Config;

    function fixtureValues($node)
    {
        $values = [];
        foreach ($node->children() as $key => $value) {
            $values[$key] = (string)$value;
        }
        return $values;
    }

    function fixtureFields($node, array $values)
    {
        foreach ($values as $key => $value) {
            $node->$key = $value;
        }
    }

    class FixtureMessage
    {
        public function __construct(private $field, private $message) {}
        public function getField() { return $this->field; }
        public function getMessage() { return $this->message; }
    }

    abstract class FixtureModel
    {
        protected const INVALID = 'invalid_settings';
        private $values; // Each candidate model stays isolated until serializeToConfig().

        public function __construct()
        {
            $this->values = array_replace(static::DEFAULTS,
                array_intersect_key(fixtureValues(Config::getInstance()->object()->OPNsense->{static::ROOT}), static::DEFAULTS));
        }

        public function __get($key) { return $this->values[$key] ?? null; }
        public function __isset($key) { return isset($this->values[$key]); }
        public function __set($key, $value) { $this->setNodes([$key => $value]); }
        public function getNodes() { return array_filter($this->values, fn($value) => $value !== null); }

        public function setNodes(array $nodes)
        {
            foreach (array_intersect_key($nodes, static::DEFAULTS) as $key => $value) {
                $this->values[$key] = (string)$value;
            }
        }

        public function performValidation($full = false)
        {
            return FixtureState::$options[static::INVALID] ? [new FixtureMessage(...static::ERROR)] : [];
        }

        public function serializeToConfig($full = false, $disableValidation = false)
        {
            fixtureFields(Config::getInstance()->object()->OPNsense->{static::ROOT}, $this->getNodes());
        }
    }

    class FixtureState
    {
        public static $events = [];
        public static $logs = [];
        public static $aclChecks = [];
        public static $progressReads = [];
        public static $options = [
            'runtime_state' => 'STANDBY', 'attachment' => 'FENCED', 'denied_routes' => [],
            'default_route' => ['gateway' => 'link#42', 'netif' => 'dhcpha0lagg'],
            'status_unavailable' => false, 'status_unavailable_after_apply' => false,
            'managed_mismatch' => false, 'managed_mismatch_after_apply' => false,
            'device_present' => true, 'needs_reconcile' => false, 'inventory_members' => null,
            'controller_apply' => 'success', 'native_apply' => 'success', 'fail_save' => null,
            'invalid_settings' => false, 'invalid_sync' => false,
        ];
    }

    require dirname($argv[2]) . '/NativeReceiveMode.php';
    require $argv[1];
    require $argv[2];
    require dirname($argv[1]) . '/ServiceController.php';

    // Cases differ only in initial state or one injected boundary failure.
    // Keep native save/apply effects in the fixture classes above.
    $case = $argv[3] ?? 'configure_success';
    $action = $argv[4] ?? 'configure';
    $cases = [
        'configure_readonly' => ['read_only' => true],
        'configure_native_acl' => ['denied_routes' => ['/api/interfaces/assignment/reconfigure']],
        'configure_invalid' => ['invalid_settings' => true],
        'configure_stale_revision' => ['changed_local' => ['carrier' => 'changed-after-read']],
        'configure_default_mac' => ['post_shared' => ['shared_mac' => '']],
        'configure_apply_failed' => ['native_apply' => 'failed'],
        'configure_timeout' => ['native_apply' => 'timeout'],
        'configure_native_save_unknown' => ['native_apply' => 'save_unknown', 'fail_save' => 'native'],
        'configure_runtime_readback_unknown' => ['status_unavailable_after_apply' => true],
        'configure_runtime_mismatch' => ['managed_mismatch_after_apply' => true],
        'configure_unrelated_pending' => [
            'pending' => ['lan' => ['pending_action' => 'relink', 'pending_if' => 'hn3']],
            'post_local' => ['carrier' => 'hn1'],
        ],
        'configure_exact_pending' => [
            'saved_local' => ['carrier' => 'hn1'], 'post_local' => ['carrier' => 'hn1'],
            'pending' => ['wan' => ['pending_action' => 'relink', 'pending_if' => 'dhcpha0lagg']],
        ],
        'configure_already_mapped' => [
            'saved_assignments' => ['wan' => 'dhcpha0lagg'],
            'saved_local' => ['carrier' => 'hn1'], 'post_local' => ['carrier' => 'hn1'],
        ],
        'configure_missing_device_retry' => [
            'saved_assignments' => ['wan' => 'dhcpha0lagg'],
            'saved_local' => ['carrier' => 'hn1'], 'device_present' => false,
        ],
        'teardown_apply_failed' => ['native_apply' => 'failed'],
        'teardown_unrelated_pending' => ['pending' => ['lan' => ['pending_action' => 'relink', 'pending_if' => 'hn3']]],
        'save_unchanged_stale' => ['revision' => 'stale'],
        'sync_native_acl' => ['denied_routes' => ['/api/core/hasync/set']],
        'sync_readonly' => ['read_only' => true],
        'sync_no_destination' => ['sync' => ['synchronizetoip' => '']],
        'sync_already_selected' => ['sync' => ['syncitems' => 'interfaces,firewall,dhcp-interface-ha']],
        'sync_save_unknown' => ['fail_save' => 'sync'],
        'sync_invalid' => ['invalid_sync' => true],
    ];
    $requestFlags = array_replace($cases[$case] ?? [], json_decode($argv[5] ?? '{}', true) ?: []);
    $teardown = str_starts_with($case, 'teardown_');
    $unchanged = str_starts_with($case, 'save_unchanged');
    $mapped = ($teardown || $unchanged) && $case !== 'save_unchanged_disabled';
    FixtureState::$options = array_replace(FixtureState::$options, $requestFlags, [
        'assignment_cache_stale' => $teardown,
        'lease_on_original' => in_array($case, ['configure_success', 'configure_timeout', 'configure_native_save_unknown'], true),
    ]);
    FixtureRequest::$readOnly = $requestFlags['read_only'] ?? false;
    Config::$pending = $requestFlags['pending'] ?? [];
    $shared = ['enabled' => $mapped && $case !== 'teardown_disabled' ? '1' : '0',
               'shared_mac' => '02:11:22:33:44:55', 'failback_delay' => '0'];
    $local = ['managed_interface' => $case === 'save_unchanged_disabled' ? '' : 'wan', 'carrier' => $mapped ? 'hn1' : ''];
    $assignments = array_replace(['wan' => $mapped ? 'dhcpha0lagg' : 'hn1', 'lan' => 'hn2'], $requestFlags['saved_assignments'] ?? []);
    Config::$committed = simplexml_load_string('<opnsense><interfaces/><virtualip><vip><mode>carp</mode>' .
        '<interface>lan</interface><disabled>0</disabled></vip></virtualip><OPNsense>' .
        '<DhcpInterfaceHaShared/><DhcpInterfaceHaLocal/><hasync/></OPNsense></opnsense>');
    foreach ($assignments as $name => $device) {
        fixtureFields(Config::$committed->interfaces->addChild($name), [
            'if' => $device, 'enable' => '1', 'descr' => strtoupper($name),
            'ipaddr' => $name === 'wan' ? 'dhcp' : 'static', 'ipaddrv6' => 'none', 'spoofmac' => '',
        ]);
    }
    $wan = Config::$committed->interfaces->wan;
    if ($mapped) {
        fixtureFields($wan, ['promisc' => '1', 'dhcpha_original_promisc' => json_encode([false, ''])]);
    }
    if (array_key_exists('original_promisc', $requestFlags)) {
        unset($wan->dhcpha_original_promisc);
        $wan->promisc = $requestFlags['original_promisc'];
    }
    if (isset($requestFlags['receive_backup'])) {
        $wan->dhcpha_original_promisc = $requestFlags['receive_backup'];
    }
    if (!empty($requestFlags['receive_missing'])) {
        unset($wan->promisc, $wan->dhcpha_original_promisc);
    }
    if (isset($requestFlags['standby'])) {
        Config::$committed->interfaces->lan->enable = '0';
        fixtureFields(Config::$committed->interfaces->addChild('lo0'), ['enable' => '1', 'if' => 'lo0', 'ipaddr' => '127.0.0.1']);
        fixtureFields(Config::$committed->interfaces->addChild('opt2'), [
            'enable' => '1', 'if' => 'vlan0.10', 'ipaddr' => '198.51.100.3', 'subnet' => '24']);
        fixtureFields(Config::$committed->virtualip->vip, ['interface' => 'opt2', 'subnet' => '198.51.100.1', 'vhid' => '10']);
    }
    foreach (['shared' => 'DhcpInterfaceHaShared', 'local' => 'DhcpInterfaceHaLocal'] as $name => $root) {
        fixtureFields(Config::$committed->OPNsense->$root, array_replace($$name, $requestFlags['saved_' . $name] ?? []));
    }
    fixtureFields(Config::$committed->OPNsense->hasync, array_replace(
        ['synchronizetoip' => '192.0.2.2', 'syncitems' => 'interfaces,firewall'], $requestFlags['sync'] ?? []));
    $controller = new \OPNsense\DhcpInterfaceHa\Api\SettingsController();
    $settings = $controller->getAction();
    foreach (['shared' => 'DhcpInterfaceHaShared', 'local' => 'DhcpInterfaceHaLocal'] as $name => $root) {
        fixtureFields(Config::$committed->OPNsense->$root, $requestFlags['changed_' . $name] ?? []);
    }
    \FixtureRequest::$method = 'POST';
    \FixtureRequest::$post = [
        'dhcphashared' => $unchanged ? fixtureValues(Config::$committed->OPNsense->DhcpInterfaceHaShared) : [
            'enabled' => $requestFlags['enabled'] ?? '0', 'shared_mac' => '02:11:22:33:44:55', 'failback_delay' => '0',
        ],
        'dhcphalocal' => $unchanged ? fixtureValues(Config::$committed->OPNsense->DhcpInterfaceHaLocal) : [
            'managed_interface' => $action === 'settings' ? '' : 'wan',
            'carrier' => $action === 'settings' ? '' : 'client-controlled-device',
        ],
        'revision' => $requestFlags['revision'] ?? $settings['revision'],
        'syncitems' => 'attacker-selected-list',
    ];
    foreach (['shared', 'local'] as $name) {
        \FixtureRequest::$post['dhcpha' . $name] = array_replace(\FixtureRequest::$post['dhcpha' . $name], $requestFlags['post_' . $name] ?? []);
    }
    if (isset($requestFlags['standby'])) {
        \FixtureRequest::$post['dhcphalocal'] += $requestFlags['standby'];
    }
    if (isset($requestFlags['progress_id'])) {
        \FixtureRequest::$username = 'fixture-admin-' . getmypid();
        \FixtureRequest::$post['progress_id'] = $requestFlags['progress_id'];
    }

    try {
        if ($action === 'get') {
            \FixtureRequest::$method = 'GET';
        }
        $response = match ($action) {
            'get' => $controller->getAction(),
            'sync' => $controller->enable_syncAction(),
            'settings' => $controller->setAction(),
            'service' => (new \OPNsense\DhcpInterfaceHa\Api\ServiceController())->applyAction(),
            default => $controller->configureAction(),
        };
    } catch (\FixtureReadOnly $exception) {
        $response = ['result' => 'denied', 'error' => $exception->getMessage()];
    }

    $progressChecks = [];
    if (isset($requestFlags['progress_id'])) {
        \FixtureRequest::$method = 'GET';
        $progressChecks['own'] = $controller->progressAction($requestFlags['progress_id']);
        $progressChecks['wrong_id'] = $controller->progressAction(str_repeat('0', 32));
        $user = \FixtureRequest::$username;
        \FixtureRequest::$username = $user . '-other';
        $progressChecks['other_user'] = $controller->progressAction($requestFlags['progress_id']);
        \FixtureRequest::$username = $user;
        $file = sys_get_temp_dir() . '/dhcpha-save-' . hash('sha256', $user) . '.json';
        if (is_file($file)) {
            $data = json_decode(file_get_contents($file), true);
            $data['updated'] = time() - 1000;
            file_put_contents($file, json_encode($data));
            $progressChecks['expired'] = $controller->progressAction($requestFlags['progress_id']);
            unlink($file);
        }
    }

    echo json_encode([
        'loaded_settings' => $settings,
        'progress_reads' => \FixtureState::$progressReads,
        'progress_checks' => $progressChecks,
        'response' => $response,
        'applied_receive_modes' => \OPNsense\Core\Config::$appliedReceiveModes,
        'receive_modes' => Config::receiveModes(),
        'save_count' => \OPNsense\Core\Config::$saveCount,
        'shared' => fixtureValues(Config::$committed->OPNsense->DhcpInterfaceHaShared),
        'local' => fixtureValues(Config::$committed->OPNsense->DhcpInterfaceHaLocal),
        'assignments' => array_map(fn($node) => (string)$node->if, iterator_to_array(Config::$committed->interfaces->children())),
        'pending' => \OPNsense\Core\Config::$pending,
        'sync' => fixtureValues(Config::$committed->OPNsense->hasync),
        'events' => \FixtureState::$events,
        'logs' => \FixtureState::$logs,
        'acl_checks' => \FixtureState::$aclChecks,
        'locked' => \OPNsense\Core\Config::$locked,
    ]);
}
