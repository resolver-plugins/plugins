<?php

namespace OPNsense\Base {
    class ApiControllerBase
    {
        protected $request;

        public function __construct()
        {
            $this->request = new \FixtureRequest();
        }

        protected function throwReadOnly()
        {
            if (\FixtureRequest::$readOnly) {
                throw new \RuntimeException('read-only');
            }
        }

        public function getUserName()
        {
            return 'fixture-admin';
        }
    }
}

namespace OPNsense\Core {
    class Config
    {
        private static $instance;
        private $xml;
        public static $locked = false;
        public static $shared = [];
        public static $local = [];
        public static $assignments = ['wan' => 'hn1', 'lan' => 'hn2'];
        public static $pending = [];
        public static $sync = ['synchronizetoip' => '192.0.2.2', 'syncitems' => 'interfaces,firewall'];
        public static $pendingShared = null;
        public static $pendingLocal = null;
        public static $pendingSync = null;
        public static $saveCount = 0;
        public static $saveKind = null;
        public static $failSave = null;
        public static $nativeApply = 'success';

        public static function getInstance()
        {
            return self::$instance ?? (self::$instance = new self());
        }

        public function object()
        {
            if ($this->xml === null) {
                $this->xml = simplexml_load_string(
                    '<opnsense><interfaces>' .
                    '<wan><if>' . self::$assignments['wan'] . '</if><enable>1</enable><descr>WAN</descr>' .
                    '<ipaddr>dhcp</ipaddr><ipaddrv6>none</ipaddrv6><spoofmac></spoofmac></wan>' .
                    '<lan><if>' . self::$assignments['lan'] . '</if><enable>1</enable><descr>LAN</descr>' .
                    '<ipaddr>static</ipaddr><ipaddrv6>none</ipaddrv6><spoofmac></spoofmac></lan>' .
                    '</interfaces><virtualip><vip><mode>carp</mode><interface>lan</interface><disabled>0</disabled></vip></virtualip>' .
                    '<OPNsense><DhcpInterfaceHaShared/></OPNsense></opnsense>'
                );
            }
            return $this->xml;
        }

        public function lock($reload = true)
        {
            if (!self::$locked && $reload) {
                $this->xml = null;
            }
            self::$locked = true;
        }

        public function unlock()
        {
            self::$locked = false;
        }

        public function save($metadata = null)
        {
            if (!self::$locked) {
                throw new \RuntimeException('save without config lock');
            }
            if (is_array($metadata) && str_contains($metadata['description'] ?? '', 'configuration sync')) {
                self::$saveKind = 'sync';
            } elseif (is_array($metadata)) {
                self::$saveKind = 'plugin';
            } else {
                self::$saveKind = 'native';
            }
            if (self::$failSave === self::$saveKind) {
                throw new \RuntimeException('injected config save failure');
            }
            self::$saveCount++;
            if (self::$saveKind === 'plugin') {
                self::$shared = self::$pendingShared;
                self::$local = self::$pendingLocal;
                self::$pendingShared = null;
                self::$pendingLocal = null;
            } elseif (self::$saveKind === 'sync') {
                self::$sync = self::$pendingSync;
                self::$pendingSync = null;
            } else {
                foreach ($this->object()->interfaces->children() as $name => $node) {
                    self::$assignments[(string)$name] = (string)$node->if;
                    \FixtureRequest::$needsReconcile = true;
                }
                if (self::$assignments['wan'] === 'dhcpha0lagg') {
                    if (\FixtureRequest::$statusUnavailableAfterApply) {
                        \FixtureRequest::$statusUnavailable = true;
                    }
                    if (\FixtureRequest::$managedMismatchAfterApply) {
                        \FixtureRequest::$managedMismatch = true;
                    }
                }
            }
        }
    }

    class ACL
    {
        public function isPageAccessible($user, $route)
        {
            \FixtureRequest::$aclChecks[] = $route;
            return !in_array($route, \FixtureRequest::$deniedRoutes, true);
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
            \FixtureRequest::$logs[] = ['identity' => $this->identity, 'level' => 'info', 'message' => $message];
        }

        public function error($message)
        {
            \FixtureRequest::$logs[] = ['identity' => $this->identity, 'level' => 'error', 'message' => $message];
        }
    }

    class Backend
    {
        public function configdRun($event, $detach = false, $timeout = 120, $connectTimeout = 10)
        {
            if (Config::$locked && !($event === 'filter reload skip_alias' && $detach)) {
                throw new \RuntimeException('configd called under config lock');
            }
            \FixtureRequest::$events[] = $event;
            if ($event === 'dhcp_interface_ha status') {
                if (\FixtureRequest::$statusUnavailable) {
                    throw new \RuntimeException('status unavailable');
                }
                return self::statusJson();
            }
            if ($event === '!interface list assign-opts') {
                \FixtureRequest::$assignmentCacheStale = false;
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
                if (\FixtureRequest::$leaseOnOriginal && Config::$assignments['wan'] === 'hn1') {
                    $wan['ipv4'] = ['192.0.2.15/24'];
                }
                $devices = [
                    'hn1' => $wan,
                    'hn2' => ['is_physical' => true, 'macaddr' => '02:11:22:33:44:02'],
                ];
                if (\FixtureRequest::$devicePresent) {
                    // Native inventory omits laggport when the device has no members.
                    $devices['dhcpha0lagg'] = ['laggproto' => 'failover'];
                    if (\FixtureRequest::$inventoryMembers !== null) {
                        $devices['dhcpha0lagg']['laggport'] = \FixtureRequest::$inventoryMembers;
                    }
                }
                return json_encode($devices);
            }
            if ($event === 'dhcp_interface_ha prepare_setup') {
                \FixtureRequest::$devicePresent = true;
                return '{"prepared":true,"detached":true,"owned":true,"device":"dhcpha0lagg"}';
            }
            if ($event === 'dhcp_interface_ha apply') {
                \FixtureRequest::$needsReconcile = false;
                if (\FixtureRequest::$controllerApply === 'failed') {
                    return '{"error":"controller apply failed"}';
                }
                return self::statusJson();
            }
            if ($event === 'interface apply') {
                if (\OPNsense\Core\Config::$nativeApply === 'failed') {
                    return 'FAILED';
                }
                if (\OPNsense\Core\Config::$nativeApply === 'timeout') {
                    throw new \RuntimeException('interface apply timed out');
                }
                return 'OK';
            }
            return '{}';
        }

        private static function statusJson()
        {
            return json_encode([
                'actual_attachment' => (\FixtureRequest::$needsReconcile && Config::$assignments['wan'] === 'dhcpha0lagg') ? 'UNVERIFIED' : 'FENCED',
                'desired_attachment' => 'FENCED',
                'enabled' => Config::$shared['enabled'] === '1',
                'owned' => \FixtureRequest::$devicePresent,
                'managed_by_dhcpha' => \FixtureRequest::$devicePresent
                    && Config::$assignments['wan'] === 'dhcpha0lagg'
                    && !\FixtureRequest::$managedMismatch,
                'carrier_capable' => true,
                'carrier' => ['name' => 'hn1', 'exists' => true, 'link_up' => true, 'mac' => '02:11:22:33:44:01'],
                'dhcpha' => [
                    'name' => 'dhcpha0lagg',
                    'exists' => \FixtureRequest::$devicePresent,
                    'lagg_protocol' => 'failover',
                    'lagg_members' => [],
                ],
                'shared_mac_collisions' => [],
                'carp_aligned' => true,
            ]);
        }
    }

    class Hasync
    {
        public $synchronizetoip;
        private $syncitems;

        public function __construct()
        {
            $this->synchronizetoip = Config::$sync['synchronizetoip'];
            $this->syncitems = new \FixtureStringField(Config::$sync['syncitems']);
        }

        public function __get($name)
        {
            return $name === 'syncitems' ? $this->syncitems : null;
        }

        public function __set($name, $value)
        {
            if ($name === 'syncitems') {
                $this->syncitems = new \FixtureStringField($value);
            }
        }

        public function performValidation($full = false)
        {
            if (\FixtureRequest::$invalidSync) {
                return [new \FixtureMessage('hasync.syncitems', 'invalid sync selection')];
            }
            return [];
        }

        public function serializeToConfig($full = false, $disableValidation = false)
        {
            Config::$pendingSync = [
                'synchronizetoip' => $this->synchronizetoip,
                'syncitems' => (string)$this->syncitems,
            ];
        }
    }
}

namespace OPNsense\DhcpInterfaceHa {
    class Shared
    {
        public $enabled;
        public $shared_mac;
        public $failback_delay;

        public function __construct()
        {
            $this->enabled = (string)(\OPNsense\Core\Config::$shared['enabled'] ?? '0');
            $this->shared_mac = (string)(\OPNsense\Core\Config::$shared['shared_mac'] ?? '');
            $this->failback_delay = (string)(\OPNsense\Core\Config::$shared['failback_delay'] ?? '0');
        }

        public function getNodes()
        {
            return ['enabled' => $this->enabled, 'shared_mac' => $this->shared_mac, 'failback_delay' => $this->failback_delay];
        }

        public function setNodes(array $nodes)
        {
            foreach (['enabled', 'shared_mac', 'failback_delay'] as $key) {
                if (array_key_exists($key, $nodes)) {
                    $this->$key = (string)$nodes[$key];
                }
            }
        }

        public function performValidation($full = false)
        {
            if (\FixtureRequest::$invalidSettings) {
                return [new \FixtureMessage('DhcpInterfaceHa.Shared.shared_mac', 'invalid settings')];
            }
            return [];
        }

        public function serializeToConfig($full = false, $disableValidation = false)
        {
            \OPNsense\Core\Config::$pendingShared = $this->getNodes();
        }
    }

    class Local
    {
        public $managed_interface;
        public $carrier;

        public function __construct()
        {
            $this->managed_interface = (string)(\OPNsense\Core\Config::$local['managed_interface'] ?? '');
            $this->carrier = (string)(\OPNsense\Core\Config::$local['carrier'] ?? '');
        }

        public function getNodes()
        {
            return ['managed_interface' => $this->managed_interface, 'carrier' => $this->carrier];
        }

        public function setNodes(array $nodes)
        {
            foreach (['managed_interface', 'carrier'] as $key) {
                if (array_key_exists($key, $nodes)) {
                    $this->$key = (string)$nodes[$key];
                }
            }
        }

        public function performValidation($full = false)
        {
            if (\FixtureRequest::$invalidSettings) {
                return [new \FixtureMessage('DhcpInterfaceHa.Local.managed_interface', 'invalid settings')];
            }
            return [];
        }

        public function serializeToConfig($full = false, $disableValidation = false)
        {
            \OPNsense\Core\Config::$pendingLocal = $this->getNodes();
        }

        public static function blockedCarrierDevices($managedInterface)
        {
            return [];
        }

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
                if ($props['if'] === 'hn1' && \FixtureRequest::$assignmentCacheStale) {
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
    class AssignmentController
    {
        protected $request;
        private $model;

        public function __construct()
        {
            $this->request = new \FixtureRequest();
        }

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
            if (\OPNsense\Core\Config::$nativeApply === 'failed') {
                return ['status' => 'failed'];
            }
            if (\OPNsense\Core\Config::$nativeApply === 'timeout') {
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
    class FixtureMessage
    {
        private $field;
        private $message;

        public function __construct($field, $message)
        {
            $this->field = $field;
            $this->message = $message;
        }

        public function getField()
        {
            return $this->field;
        }

        public function getMessage()
        {
            return $this->message;
        }
    }

    class FixtureStringField
    {
        private $value;

        public function __construct($value)
        {
            $this->value = (string)$value;
        }

        public function __toString()
        {
            return $this->value;
        }
    }

    class FixtureRequest
    {
        public static $method = 'GET';
        public static $post = [];
        public static $readOnly = false;
        public static $events = [];
        public static $logs = [];
        public static $aclChecks = [];
        public static $deniedRoutes = [];
        public static $statusUnavailable = false;
        public static $statusUnavailableAfterApply = false;
        public static $managedMismatchAfterApply = false;
        public static $managedMismatch = false;
        public static $leaseOnOriginal = false;
        public static $devicePresent = true;
        public static $assignmentCacheStale = false;
        public static $needsReconcile = false;
        public static $inventoryMembers = null;
        public static $controllerApply = 'success';
        public static $nativeApply = 'success';
        public static $invalidSettings = false;
        public static $invalidSync = false;

        public function isGet()
        {
            return self::$method === 'GET';
        }

        public function isPost()
        {
            return self::$method === 'POST';
        }

        public function getPost($key, $filter = null, $default = null)
        {
            return self::$post[$key] ?? $default;
        }
    }

    require $argv[1];
    require $argv[2];

    $case = $argv[3] ?? 'configure_success';
    \FixtureRequest::$assignmentCacheStale = str_starts_with($case, 'teardown_');
    $action = $argv[4] ?? 'configure';
    $requestFlags = json_decode($argv[5] ?? '{}', true) ?: [];
    \FixtureRequest::$inventoryMembers = $requestFlags['inventory_members'] ?? null;
    \FixtureRequest::$leaseOnOriginal = in_array($case, ['configure_success', 'configure_timeout', 'configure_native_save_unknown'], true);
    \FixtureRequest::$devicePresent = $case !== 'configure_missing_device_retry';
    \FixtureRequest::$controllerApply = $requestFlags['controller_apply'] ?? 'success';
    \FixtureRequest::$nativeApply = $requestFlags['native_apply'] ?? 'success';
    \FixtureRequest::$readOnly = !empty($requestFlags['read_only']);
    \FixtureRequest::$deniedRoutes = $requestFlags['denied_routes'] ?? [];
    \FixtureRequest::$statusUnavailable = !empty($requestFlags['status_unavailable']);
    \FixtureRequest::$statusUnavailableAfterApply = $case === 'configure_runtime_readback_unknown';
    \FixtureRequest::$managedMismatchAfterApply = $case === 'configure_runtime_mismatch';
    \FixtureRequest::$invalidSettings = !empty($requestFlags['invalid_settings']);
    \FixtureRequest::$invalidSync = !empty($requestFlags['invalid_sync']);

    \OPNsense\Core\Config::$shared = [
        'enabled' => '0',
        'shared_mac' => '02:11:22:33:44:55',
        'failback_delay' => '0',
    ];
    \OPNsense\Core\Config::$local = ['managed_interface' => 'wan', 'carrier' => ''];
    \OPNsense\Core\Config::$assignments = [
        'wan' => $case === 'configure_already_mapped' ? 'dhcpha0lagg' : 'hn1',
        'lan' => 'hn2',
    ];
    \OPNsense\Core\Config::$pending = [];
    if (str_starts_with($case, 'teardown_') || $case === 'save_unchanged_enabled') {
        \OPNsense\Core\Config::$shared['enabled'] = $case === 'teardown_disabled' ? '0' : '1';
        \OPNsense\Core\Config::$local = ['managed_interface' => 'wan', 'carrier' => 'hn1'];
        \OPNsense\Core\Config::$assignments['wan'] = 'dhcpha0lagg';
    }
    if ($case === 'configure_unrelated_pending') {
        \OPNsense\Core\Config::$pending = ['lan' => ['pending_action' => 'relink', 'pending_if' => 'hn3']];
    } elseif ($case === 'configure_exact_pending') {
        \OPNsense\Core\Config::$local['carrier'] = 'hn1';
        \OPNsense\Core\Config::$pending = ['wan' => ['pending_action' => 'relink', 'pending_if' => 'dhcpha0lagg']];
    } elseif ($case === 'configure_already_mapped') {
        \OPNsense\Core\Config::$local['carrier'] = 'hn1';
    } elseif ($case === 'configure_missing_device_retry') {
        \OPNsense\Core\Config::$assignments['wan'] = 'dhcpha0lagg';
        \OPNsense\Core\Config::$local['carrier'] = 'hn1';
    }
    if ($action === 'sync') {
        \OPNsense\Core\Config::$sync = $requestFlags['sync'] ?? \OPNsense\Core\Config::$sync;
    }
    if ($case === 'configure_readonly') {
        \FixtureRequest::$readOnly = true;
    }
    if ($case === 'configure_native_acl') {
        \FixtureRequest::$deniedRoutes = ['/api/interfaces/assignment/reconfigure'];
    }
    if ($case === 'sync_native_acl') {
        \FixtureRequest::$deniedRoutes = ['/api/core/hasync/set'];
    }

    $controller = new \OPNsense\DhcpInterfaceHa\Api\SettingsController();
    $settings = $controller->getAction();
    $shared = [
        'enabled' => $requestFlags['enabled'] ?? '0',
        'shared_mac' => '02:11:22:33:44:55',
        'failback_delay' => '0',
    ];
    $local = ['managed_interface' => 'wan', 'carrier' => 'client-controlled-device'];
    \FixtureRequest::$method = 'POST';
    \FixtureRequest::$post = [
        'dhcphashared' => $shared,
        'dhcphalocal' => $local,
        'revision' => $settings['revision'],
        'syncitems' => 'attacker-selected-list',
    ];
    if ($case === 'configure_invalid') {
        \FixtureRequest::$invalidSettings = true;
    }
    if ($case === 'configure_default_mac') {
        \FixtureRequest::$post['dhcphashared']['shared_mac'] = '';
    }
    if ($case === 'configure_stale_revision') {
        \OPNsense\Core\Config::$local['carrier'] = 'changed-after-read';
    }
    if ($case === 'configure_exact_pending' || $case === 'configure_already_mapped') {
        \FixtureRequest::$post['dhcphalocal']['carrier'] = 'hn1';
    }
    if ($action === 'sync' && isset($requestFlags['sync'])) {
        \OPNsense\Core\Config::$sync = $requestFlags['sync'];
    }
    if ($case === 'sync_no_destination') {
        \OPNsense\Core\Config::$sync['synchronizetoip'] = '';
    }
    if ($case === 'sync_already_selected') {
        \OPNsense\Core\Config::$sync['syncitems'] .= ',dhcp-interface-ha';
    }
    if ($case === 'sync_native_acl' || $case === 'sync_readonly') {
        \FixtureRequest::$readOnly = $case === 'sync_readonly';
    }
    if ($case === 'sync_save_unknown') {
        \OPNsense\Core\Config::$failSave = 'sync';
    }
    if ($case === 'configure_native_save_unknown') {
        \OPNsense\Core\Config::$nativeApply = 'save_unknown';
        \OPNsense\Core\Config::$failSave = 'native';
    }
    if ($case === 'teardown_apply_failed') {
        \OPNsense\Core\Config::$nativeApply = 'failed';
    }
    if ($case === 'teardown_unrelated_pending') {
        \OPNsense\Core\Config::$pending = ['lan' => ['pending_action' => 'relink', 'pending_if' => 'hn3']];
    }

    if (in_array($case, ['configure_apply_failed', 'configure_timeout', 'configure_native_save_unknown'], true)) {
        \OPNsense\Core\Config::$nativeApply = $case === 'configure_apply_failed'
            ? 'failed'
            : ($case === 'configure_timeout' ? 'timeout' : 'save_unknown');
        if ($case === 'configure_native_save_unknown') {
            \OPNsense\Core\Config::$failSave = 'native';
        }
    }

    if ($case === 'configure_unrelated_pending') {
        \FixtureRequest::$post['dhcphalocal']['carrier'] = 'hn1';
    }
    if ($action === 'sync' && $case === 'sync_invalid') {
        \FixtureRequest::$invalidSync = true;
    }

    if ($action === 'settings' && $case !== 'save_unchanged_enabled') {
        \FixtureRequest::$post['dhcphashared'] = [
            'enabled' => '0',
            'shared_mac' => '02:11:22:33:44:55',
            'failback_delay' => '0',
        ];
        \FixtureRequest::$post['dhcphalocal'] = ['managed_interface' => '', 'carrier' => ''];
    }

    if ($case === 'save_unchanged_enabled') {
        \FixtureRequest::$post['dhcphashared'] = \OPNsense\Core\Config::$shared;
        \FixtureRequest::$post['dhcphalocal'] = \OPNsense\Core\Config::$local;
    }

    try {
        $response = $action === 'sync'
            ? $controller->enable_syncAction()
            : ($action === 'settings' ? $controller->setAction() : $controller->configureAction());
    } catch (\Throwable $exception) {
        $response = ['result' => 'denied', 'error' => $exception->getMessage()];
    }

    echo json_encode([
        'response' => $response,
        'save_count' => \OPNsense\Core\Config::$saveCount,
        'shared' => \OPNsense\Core\Config::$shared,
        'local' => \OPNsense\Core\Config::$local,
        'assignments' => \OPNsense\Core\Config::$assignments,
        'pending' => \OPNsense\Core\Config::$pending,
        'sync' => \OPNsense\Core\Config::$sync,
        'events' => \FixtureRequest::$events,
        'logs' => \FixtureRequest::$logs,
        'acl_checks' => \FixtureRequest::$aclChecks,
        'locked' => \OPNsense\Core\Config::$locked,
    ]);
}
