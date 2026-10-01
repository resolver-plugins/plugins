<?php
namespace OPNsense\Base {
    class ApiControllerBase
    {
        protected $request;

        public function __construct()
        {
            $this->request = new \FixtureRequest();
        }
    }
}

namespace OPNsense\DhcpInterfaceHa\Api {
    function is_file($path)
    {
        if ($path === '/tmp/.interfaces.todo') {
            return str_starts_with(\FixtureResponses::$case, 'pending_');
        }
        return \is_file($path);
    }
}

namespace OPNsense\Core {
    class FileObject
    {
        public function __construct($path, $mode, $permissions, $operation)
        {
            if ($mode !== 'r' || $permissions !== null || $operation !== (LOCK_SH | LOCK_NB)) {
                throw new \RuntimeException('pending observation must be a nonblocking read');
            }
            if (\FixtureResponses::$case === 'pending_unavailable') {
                throw new \RuntimeException('native queue unavailable');
            }
        }

        public function readJson()
        {
            if (\FixtureResponses::$case === 'pending_malformed') {
                return null;
            }
            if (in_array(\FixtureResponses::$case, ['pending_selected', 'pending_mismatched', 'pending_unrelated'], true)) {
                $name = \FixtureResponses::$case === 'pending_unrelated' ? 'lan' : 'wan';
                $device = \FixtureResponses::$case === 'pending_mismatched' ? 'em2' : 'dhcpha0lagg';
                return [$name => ['pending_action' => 'relink', 'pending_if' => $device]];
            }
            return [];
        }
    }

    class Backend
    {
        public function configdRun($event, $detach = false, $timeout = 120, $connectTimeout = 10)
        {
            \FixtureResponses::$calls[] = $event;
            return \FixtureResponses::$events[$event] ?? '{}';
        }
    }

    class Config
    {
        private static $instance;
        public $config;

        public static function getInstance()
        {
            if (self::$instance === null) {
                self::$instance = new self();
                self::$instance->config = simplexml_load_string(
                    '<opnsense><system><hostname>ha1</hostname></system><interfaces>' .
                    '<wan><enable>1</enable><if>dhcpha0lagg</if><descr>WAN</descr><ipaddr>dhcp</ipaddr>' .
                    '<ipaddrv6>none</ipaddrv6></wan></interfaces><virtualip><vip><mode>carp</mode>' .
                    '<interface>lan</interface><vhid>10</vhid></vip></virtualip><gateways><gateway_item>' .
                    '<name>GW_WAN</name><monitor>192.0.2.1</monitor></gateway_item></gateways>' .
                    '<OPNsense><DhcpInterfaceHaShared><enabled>1</enabled>' .
                    '<shared_mac>02:11:22:33:44:55</shared_mac><failback_delay>0</failback_delay></DhcpInterfaceHaShared>' .
                    '<DhcpInterfaceHaLocal><managed_interface>wan</managed_interface><carrier>em1</carrier></DhcpInterfaceHaLocal></OPNsense>' .
                    '<private_fixture_secret>do-not-export</private_fixture_secret></opnsense>'
                );
            }
            return self::$instance;
        }

        public function object()
        {
            return $this->config;
        }
    }

    class Hasync
    {
        public $syncitems = 'dhcp-interface-ha';
        public $pfsyncinterface = 'lan';
        public $pfsyncpeerip = '198.51.100.2';
        public $pfsyncversion = '1400';
        public $pfsyncdefer = '1';
        public $synchronizetoip = '198.51.100.3';
        public $disablepreempt = '0';

        public function __construct()
        {
            if (\FixtureResponses::$case === 'sender_missing_plugin') {
                $this->syncitems = 'pfsync';
            } elseif (\FixtureResponses::$case === 'no_sync_target') {
                $this->syncitems = 'pfsync';
                $this->synchronizetoip = '';
            }
        }
    }
}

namespace OPNsense\DhcpInterfaceHa {
    class Shared
    {
        public $shared_mac = '02:11:22:33:44:55';
        public $enabled = '1';
        public $failback_delay = '0';

        public function __construct()
        {
            if (in_array(\FixtureResponses::$case, ['none', 'preconfigure', 'preconfigure_unknown_mac', 'captured_original'], true)) {
                $this->enabled = '0';
            } elseif (\FixtureResponses::$case === 'bad_mac') {
                $this->shared_mac = '00:00:00:00:00:00';
            } elseif (\FixtureResponses::$case === 'legacy_failback') {
                $this->failback_delay = '60';
            }
        }
    }

    class Local
    {
        public $managed_interface = 'wan';
        public $carrier = 'em1';

        public function __construct()
        {
            if (in_array(\FixtureResponses::$case, ['none', 'unconfigured_enabled'], true)) {
                $this->managed_interface = '';
                $this->carrier = '';
            } elseif (in_array(\FixtureResponses::$case, ['preconfigure', 'preconfigure_unknown_mac', 'missing_carrier'], true)) {
                $this->carrier = '';
            }
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

namespace OPNsense\Routing {
    class Gateways
    {
        public function getInterfaceGateway($interface, $family, $detail = false, $field = null)
        {
            return $field === 'name' ? 'GW_WAN' : '192.0.2.1';
        }
    }
}

namespace {
    class FixtureRequest
    {
        public function getQuery($name, $filter = null, $default = null)
        {
            return $default;
        }

        public function isGet()
        {
            return true;
        }
    }

    class FixtureResponses
    {
        public static $case = '';
        public static $calls = [];
        public static $events = [
            'dhcp_interface_ha status' => '{
                "state":"STANDBY","reason_code":"global_backup","reason":"Standby — local adapter intentionally disconnected.",
                "controller_running":true,"stopped":false,"actual_attachment":"FENCED","desired_attachment":"FENCED",
                "global_role":"BACKUP","carp_allowed":true,"carp_maintenance":false,"carp_aligned":true,
                "expected_carp_instances":[{"interface":"lan0","vhid":"10"}],
                "live_carp_instances":[{"interface":"lan0","vhid":"10","status":"BACKUP"}],
                "enabled":true,"managed_by_dhcpha":true,"owned":true,"carrier_safe":true,"carrier_capable":true,
                "shared_mac_collisions":[],
                "carrier":{"name":"em1","exists":true,"link_up":true,"promiscuous":true,"mac":"02:11:22:33:44:01","mtu":1500},
                "dhcpha":{"name":"dhcpha0lagg","exists":true,"lagg_protocol":"failover","lagg_members":[],"promiscuous":true,"mac":"02:11:22:33:44:55","mtu":1500}
            }',
            'interface show carp' => '{"demotion":0}',
            'interface address' => '{"wan":[{"address":"2001:db8::10","bits":64},{"address":"192.0.2.10","bits":24}]}',
            'interface gateways status' => '{"GW_WAN":{"name":"GW_WAN","status":"none"}}',
            'filter list pfsync json' => '{"hostid":"1234","nodes":[{"creatorid":"1234","this":true}]}'
        ];
    }

    require $argv[1];
    FixtureResponses::$case = $argv[2] ?? '';
    if (($argv[2] ?? '') === 'malformed') {
        FixtureResponses::$events['dhcp_interface_ha status'] = '{"state":"STANDBY","carrier":"invalid"}';
    } elseif (($argv[2] ?? '') === 'bad_address') {
        FixtureResponses::$events['interface address'] = '{"wan":{"not-an-address":"invalid"}}';
    } elseif (in_array($argv[2] ?? '', ['addressless', 'unrelated_addressless'], true)) {
        $interface = $argv[2] === 'addressless' ? 'wan' : 'lan';
        $addresses = json_decode(FixtureResponses::$events['interface address'], true);
        $addresses[$interface] = array_map(fn($family) => [
            'address' => null, 'network' => null, 'bits' => null, 'device' => null,
            'interface' => $interface, 'family' => $family,
        ], ['inet', 'inet6']);
        FixtureResponses::$events['interface address'] = json_encode($addresses);
    }

    $case = $argv[2] ?? '';
    $runtime = json_decode(FixtureResponses::$events['dhcp_interface_ha status'], true);
    if (is_array($runtime)) {
        if (in_array($case, ['missing_collision_inventory', 'native_ipv6_conflict_unknown_mac'], true)) {
            unset($runtime['shared_mac_collisions']);
        } elseif ($case === 'malformed_collision_inventory') {
            $runtime['shared_mac_collisions'] = ['unexpected' => 'em2'];
        } elseif ($case === 'confirmed_collision') {
            $runtime['shared_mac_collisions'] = ['em2'];
        } elseif ($case === 'active_receive_drift') {
            $runtime['state'] = 'FAULT';
            $runtime['reason_code'] = 'attachment_unverified';
            $runtime['reason'] = 'The observed interface attachment does not match the verified plugin configuration.';
            $runtime['actual_attachment'] = 'UNVERIFIED';
            $runtime['desired_attachment'] = 'ATTACHED';
            $runtime['global_role'] = 'MASTER';
            $runtime['carrier']['promiscuous'] = false;
            $runtime['dhcpha']['promiscuous'] = false;
            $runtime['dhcpha']['lagg_members'] = ['em1'];
        } elseif ($case === 'stopped_master_detached') {
            $runtime['state'] = 'FENCED';
            $runtime['reason_code'] = 'controller_stopped';
            $runtime['reason'] = 'The controller is stopped and the device is verified detached.';
            $runtime['controller_running'] = false;
            $runtime['stopped'] = true;
            $runtime['actual_attachment'] = 'FENCED';
            $runtime['desired_attachment'] = 'FENCED';
            $runtime['global_role'] = 'MASTER';
            $runtime['dhcpha']['lagg_members'] = [];
            $runtime['carrier']['promiscuous'] = false;
            $runtime['dhcpha']['promiscuous'] = false;
        } elseif ($case === 'foreign_device') {
            $runtime['state'] = 'FAULT';
            $runtime['reason_code'] = 'device_ownership_unverified';
            $runtime['reason'] = 'dhcpha0lagg ownership is not verified.';
            $runtime['actual_attachment'] = 'UNVERIFIED';
            $runtime['owned'] = false;
        } elseif ($case === 'maintenance') {
            $runtime['state'] = 'FENCED';
            $runtime['reason_code'] = 'carp_maintenance';
            $runtime['reason'] = 'Native CARP maintenance mode prevents attachment.';
            $runtime['carp_maintenance'] = true;
        } elseif (in_array($case, ['none', 'unconfigured_enabled'], true)) {
            $runtime['state'] = $case === 'none' ? 'DISABLED' : 'SETUP_INCOMPLETE';
            $runtime['reason_code'] = $case === 'none' ? 'plugin_disabled' : 'managed_assignment_missing';
            $runtime['reason'] = 'The plugin is disabled and no interface is configured.';
            $runtime['actual_attachment'] = 'UNMANAGED';
            $runtime['desired_attachment'] = 'UNMANAGED';
            $runtime['enabled'] = $case === 'unconfigured_enabled';
            $runtime['managed_by_dhcpha'] = false;
            $runtime['owned'] = false;
            $runtime['dhcpha']['exists'] = false;
        } elseif (in_array($case, ['preconfigure', 'preconfigure_unknown_mac', 'captured_original'], true)) {
            $runtime['state'] = 'SETUP_INCOMPLETE';
            $runtime['reason_code'] = 'managed_assignment_missing';
            $runtime['reason'] = 'Configure interface to capture the original carrier and migrate the native assignment.';
            $runtime['actual_attachment'] = 'UNMANAGED';
            $runtime['desired_attachment'] = 'UNMANAGED';
            $runtime['enabled'] = false;
            $runtime['managed_by_dhcpha'] = false;
            $runtime['owned'] = false;
            $runtime['dhcpha']['exists'] = false;
            if ($case === 'captured_original') {
                $runtime['carrier_safe'] = false;
            }
        }
        if ($case === 'preconfigure_unknown_mac') {
            unset($runtime['shared_mac_collisions']);
        }
        FixtureResponses::$events['dhcp_interface_ha status'] = json_encode($runtime);
    }
    if (in_array($case, ['native_ipv6_conflict', 'native_ipv6_conflict_unknown_mac'], true)) {
        \OPNsense\Core\Config::getInstance()->object()->interfaces->wan->ipaddrv6 = 'track6';
    }
    if (in_array($case, ['preconfigure', 'preconfigure_unknown_mac', 'captured_original', 'missing_carrier'], true)) {
        \OPNsense\Core\Config::getInstance()->object()->interfaces->wan->if = 'em1';
    }
    $controller = new \OPNsense\DhcpInterfaceHa\Api\StatusController();
    if (($argv[2] ?? '') === 'mac_suggestion') {
        FixtureResponses::$events['interface list assign-opts'] = '{"em1":{"value":"em1","optgroup":"hardware"}}';
        FixtureResponses::$events['interface list ifconfig'] = json_encode([
            'em1' => ['is_physical' => true, 'macaddr' => '02:11:22:33:44:55'],
            'dhcpha0lagg' => ['laggproto' => 'failover', 'laggport' => [], 'macaddr' => $argv[3]],
        ]);
        if (isset($argv[4])) {
            \OPNsense\Core\Config::getInstance()->object()->interfaces->wan->spoofmac = $argv[4];
        }
        echo json_encode($controller->carriersAction());
        exit;
    }
    $payload = $controller->environmentAction();
    if ($case === 'readonly_calls') {
        $payload['_fixture_calls'] = FixtureResponses::$calls;
    }
    echo json_encode($payload);
}
