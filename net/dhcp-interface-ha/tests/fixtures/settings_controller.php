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
    class ACL
    {
        public function isPageAccessible($user, $route)
        {
            return true;
        }
    }

    class Config
    {
        private static $instance;
        public static $shared;
        public static $local;
        public static $locked = false;
        public static $saveCount = 0;
        public static $saveSnapshot = null;

        public static function getInstance()
        {
            if (self::$instance === null) {
                self::$instance = new self();
            }
            return self::$instance;
        }

        public function object()
        {
            $device = str_starts_with(\FixtureRequest::$case, 'clear_')
                ? 'hn1'
                : (in_array(\FixtureRequest::$case, ['enable', 'attached_enable'], true) ? 'dhcpha0lagg' : 'hn0');
            return simplexml_load_string(
                '<opnsense><interfaces><wan><enable>1</enable><if>' . $device . '</if><descr>WAN</descr>' .
                '<ipaddr>dhcp</ipaddr><ipaddrv6>none</ipaddrv6></wan></interfaces>' .
                '<virtualip><vip><mode>carp</mode><interface>lan</interface><disabled>0</disabled></vip></virtualip>' .
                '<OPNsense><DhcpInterfaceHaShared/></OPNsense></opnsense>'
            );
        }

        public function lock()
        {
            self::$locked = true;
        }

        public function unlock()
        {
            self::$locked = false;
        }

        public function save($revision = null)
        {
            if (!self::$locked) {
                throw new \RuntimeException('save without config lock');
            }
            self::$saveCount++;
            self::$saveSnapshot = ['shared' => self::$shared, 'local' => self::$local];
        }
    }

    class Backend
    {
        public function configdRun($event, $detach = false, $timeout = 120, $connectTimeout = 10)
        {
            if (Config::$locked) {
                throw new \RuntimeException('configd called under config lock');
            }
            \FixtureRequest::$events[] = $event;
            if ($event === 'dhcp_interface_ha status') {
                if (\FixtureRequest::$applyOutcome === 'no_status') {
                    throw new \RuntimeException('status unavailable');
                }
                if (in_array(\FixtureRequest::$case, ['absent_device', 'clear_unavailable'], true)) {
                    return '{"error":"controller status unavailable"}';
                }
                $attachment = in_array(\FixtureRequest::$case, ['attached_enable', 'clear_attached'], true) ? 'ATTACHED' : 'FENCED';
                $members = $attachment === 'ATTACHED' ? '["hn2"]' : '[]';
                return '{"actual_attachment":"' . $attachment . '","desired_attachment":"FENCED",' .
                    '"owned":true,"controller_running":false,"stopped":false,"state":"DISABLED",' .
                    '"reason_code":"plugin_disabled","reason":"disabled","global_role":"BACKUP",' .
                    '"carp_allowed":true,"carp_maintenance":false,"carp_aligned":true,' .
                    '"expected_carp_instances":[{"interface":"lan0","vhid":"10"}],"live_carp_instances":[],' .
                    '"enabled":false,"managed_by_dhcpha":true,"carrier_safe":true,"carrier_capable":false,' .
                    '"shared_mac_collisions":[],"carrier":{"name":"hn1","exists":true,"link_up":true,' .
                    '"mac":"02:11:22:33:44:01","mtu":1500},"dhcpha":{"name":"dhcpha0lagg",' .
                    '"exists":true,"lagg_protocol":"failover","lagg_members":' . $members .
                    ',"mac":"02:11:22:33:44:55","mtu":1500}}';
            }
            if (ltrim($event, '!') === 'interface list assign-opts') {
                return '{"hn1":{"value":"hn1","optgroup":"hardware"},"hn2":{"value":"hn2","optgroup":"hardware"}}';
            }
            if ($event === 'interface list ifconfig') {
                if (\FixtureRequest::$case === 'clear_unavailable') {
                    return '{"error":"inventory unavailable"}';
                }
                $device = \FixtureRequest::$case === 'absent_device' ? '' :
                    ',"dhcpha0lagg":{"laggproto":"failover","laggport":{}}';
                return '{"hn1":{"is_physical":true,"macaddr":"02:11:22:33:44:01"},' .
                    '"hn2":{"is_physical":true,"macaddr":"02:11:22:33:44:02"}' . $device . '}';
            }
            if ($event === 'dhcp_interface_ha apply') {
                return match (\FixtureRequest::$applyOutcome) {
                    'failed' => '{"error":"apply failed"}',
                    'empty' => '',
                    'timeout', 'no_status' => throw new \RuntimeException('backend timed out'),
                    default => '{"state":"DISABLED","reason":"disabled"}',
                };
            }
            return '{}';
        }
    }
}

namespace OPNsense\DhcpInterfaceHa {
    class SetupAssignmentBridge
    {
        public function pendingChanges()
        {
            return [];
        }
    }

    class Shared
    {
        public $enabled;
        public $shared_mac;
        public $failback_delay;

        public function __construct()
        {
            $data = \OPNsense\Core\Config::$shared;
            foreach (['enabled', 'shared_mac', 'failback_delay'] as $field) {
                $this->$field = (string)($data[$field] ?? '');
            }
        }

        public function getNodes()
        {
            return [
                'enabled' => $this->enabled,
                'shared_mac' => $this->shared_mac,
                'failback_delay' => $this->failback_delay,
            ];
        }

        public function setNodes(array $nodes)
        {
            foreach (['enabled', 'shared_mac', 'failback_delay'] as $field) {
                if (array_key_exists($field, $nodes)) {
                    $this->$field = (string)$nodes[$field];
                }
            }
        }

        public function performValidation($full = false)
        {
            return [];
        }

        public function serializeToConfig($full = false, $disableValidation = false)
        {
            \OPNsense\Core\Config::$shared = [
                'enabled' => $this->enabled,
                'shared_mac' => $this->shared_mac,
                'failback_delay' => $this->failback_delay,
            ];
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
            return [
                'managed_interface' => $this->managed_interface,
                'carrier' => $this->carrier,
            ];
        }

        public function setNodes(array $nodes)
        {
            if (array_key_exists('managed_interface', $nodes)) {
                $this->managed_interface = (string)$nodes['managed_interface'];
            }
            if (array_key_exists('carrier', $nodes)) {
                $this->carrier = (string)$nodes['carrier'];
            }
        }

        public function performValidation($full = false)
        {
            return [];
        }

        public function serializeToConfig($full = false, $disableValidation = false)
        {
            \OPNsense\Core\Config::$local = [
                'managed_interface' => $this->managed_interface,
                'carrier' => $this->carrier,
            ];
        }

        public static function blockedCarrierDevices($managedInterface = 'wan')
        {
            return [];
        }

        public static function carrierRuntimeEligibility($carrier, array $devices, array $ifconfig)
        {
            return null;
        }
    }
}

namespace {
    class FixtureRequest
    {
        public static $method = 'GET';
        public static $post = [];
        public static $events = [];
        public static $readOnly = false;
        public static $case = 'success';
        public static $applyOutcome = 'success';

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

    require __DIR__ . '/../../src/opnsense/mvc/app/models/OPNsense/DhcpInterfaceHa/NativeReceiveMode.php';
    require $argv[1];
    require_once dirname($argv[1]) . '/ServiceController.php';
    $case = $argv[2] ?? 'success';
    \FixtureRequest::$case = $case;
    \FixtureRequest::$applyOutcome = $argv[4] ?? 'success';
    $config = \OPNsense\Core\Config::getInstance();
    \OPNsense\Core\Config::$shared = [
        'enabled' => in_array($case, ['enabled_identity', 'enabled_assignment'], true) ? '1' : '0',
        'shared_mac' => $case === 'enabled_identity' ? '02:11:22:33:44:55' : '',
        'failback_delay' => '0',
    ];
    \OPNsense\Core\Config::$local = ['managed_interface' => 'wan', 'carrier' => $case === 'enabled_identity' ? 'hn1' : ''];
    if (str_starts_with($case, 'clear_')) {
        \OPNsense\Core\Config::$shared = [
            'enabled' => $case === 'clear_enabled' ? '1' : '0',
            'shared_mac' => '02:11:22:33:44:55',
            'failback_delay' => '60',
        ];
        \OPNsense\Core\Config::$local = ['managed_interface' => 'wan', 'carrier' => 'hn1'];
    }
    $controller = new \OPNsense\DhcpInterfaceHa\Api\SettingsController();
    $settings = $controller->getAction();
    $inputShared = $settings['dhcphashared'];
    $inputLocal = $settings['dhcphalocal'];
    \FixtureRequest::$method = 'POST';
    \FixtureRequest::$post = [
        'dhcphashared' => [
            'enabled' => in_array($case, ['enable', 'attached_enable'], true) ? '1' : '0',
                'shared_mac' => $case === 'draft' ? '' : '02:11:22:33:44:66',
            'failback_delay' => $case === 'draft' ? '30' : '0',
        ],
        'dhcphalocal' => [
            'managed_interface' => 'wan',
            'carrier' => $case === 'draft' ? '' : 'hn2',
        ],
        'revision' => $settings['revision'],
    ];
    if (str_starts_with($case, 'clear_')) {
        // Direct API callers may leave the other form fields populated.
        \FixtureRequest::$post['dhcphalocal']['managed_interface'] = '';
        \FixtureRequest::$post['dhcphashared'] = [
            'enabled' => '1', 'shared_mac' => '02:11:22:33:44:55', 'failback_delay' => '60',
        ];
    }
    if ($case === 'stale_local') {
        \OPNsense\Core\Config::$local['managed_interface'] = 'opt7';
    }
    if ($case === 'local_assignment' || $case === 'enabled_assignment') {
        \FixtureRequest::$post['dhcphalocal']['managed_interface'] = 'opt7';
        \FixtureRequest::$post['dhcphalocal']['carrier'] = '';
        \FixtureRequest::$post['dhcphashared']['shared_mac'] = '';
    }
    if ($case === 'legacy_field') {
        \FixtureRequest::$post['dhcphashared']['managed_interface'] = 'wan';
    }
    if ($case === 'stale') {
        \OPNsense\Core\Config::$shared['shared_mac'] = '02:11:22:33:44:99';
    }
    if ($case === 'readonly') {
        \FixtureRequest::$readOnly = true;
        \FixtureRequest::$post['dhcphashared']['shared_mac'] = '';
        \FixtureRequest::$post['dhcphalocal']['carrier'] = '';
    }
    if ($case === 'extra_field') {
        \FixtureRequest::$post['dhcphashared']['unrecognized'] = 'must not persist';
    }
    try {
        $result = ($argv[3] ?? 'settings') === 'service'
            ? (new \OPNsense\DhcpInterfaceHa\Api\ServiceController())->applyAction()
            : $controller->setAction();
    } catch (\Throwable $exception) {
        $result = ['result' => 'denied', 'error' => $exception->getMessage()];
    }
    echo json_encode([
        'response' => $result,
        'loaded_settings' => $settings,
        'save_count' => \OPNsense\Core\Config::$saveCount,
        'save_snapshot' => \OPNsense\Core\Config::$saveSnapshot,
        'locked' => \OPNsense\Core\Config::$locked,
        'events' => \FixtureRequest::$events,
    ]);
}
