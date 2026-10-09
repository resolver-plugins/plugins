<?php
namespace { require __DIR__ . '/api_fixture.php'; }

namespace OPNsense\DhcpInterfaceHa\Api {
    function is_file($path)
    {
        if ($path === '/tmp/.interfaces.todo') {
            return array_key_exists('pending', \FixtureResponses::$scenario);
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
            if (!empty(\FixtureResponses::$scenario['pending_error'])) {
                throw new \RuntimeException('native queue unavailable');
            }
        }

        public function readJson()
        {
            return \FixtureResponses::$scenario['pending'];
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
                    '<opnsense><system><hostname>fixture-fw</hostname></system><interfaces>' .
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

    class Hasync extends \FixtureModel
    {
        protected const ROOT = 'hasync';
        protected const DEFAULTS = ['syncitems' => 'dhcp-interface-ha', 'pfsyncinterface' => 'lan',
            'pfsyncpeerip' => '198.51.100.2', 'pfsyncversion' => '1400', 'pfsyncdefer' => '1',
            'synchronizetoip' => '198.51.100.3', 'disablepreempt' => '0'];
    }
}

namespace OPNsense\DhcpInterfaceHa {
    class Shared extends \FixtureModel
    {
        protected const ROOT = 'shared';
        protected const DEFAULTS = ['shared_mac' => '02:11:22:33:44:55', 'enabled' => '1', 'failback_delay' => '0'];
    }

    class Local extends \FixtureModel
    {
        protected const ROOT = 'local';
        protected const DEFAULTS = ['managed_interface' => 'wan', 'carrier' => 'em1'];
        public static function blockedCarrierDevices($managedInterface = 'wan') { return []; }
        public static function carrierRuntimeEligibility($carrier, array $devices, array $ifconfig) { return null; }
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
    abstract class FixtureModel
    {
        public function __get($key)
        {
            return (FixtureResponses::$scenario[static::ROOT] ?? [])[$key] ?? static::DEFAULTS[$key] ?? null;
        }
        public function __isset($key) { return $this->__get($key) !== null; }
    }

    class FixtureResponses
    {
        public static $scenario = [];
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
    $unmanaged = ['state' => 'SETUP_INCOMPLETE', 'reason_code' => 'managed_assignment_missing',
        'reason' => 'Configure interface to capture the original carrier and migrate the native assignment.',
        'actual_attachment' => 'UNMANAGED', 'desired_attachment' => 'UNMANAGED',
        'enabled' => false, 'managed_by_dhcpha' => false, 'owned' => false, 'dhcpha' => ['exists' => false]];
    $preconfigure = ['shared' => ['enabled' => '0'], 'local' => ['carrier' => ''],
        'native' => ['if' => 'em1'], 'runtime' => $unmanaged];
    $cases = [
        'pending_selected' => ['pending' => ['wan' => ['pending_action' => 'relink', 'pending_if' => 'dhcpha0lagg']]],
        'pending_mismatched' => ['pending' => ['wan' => ['pending_action' => 'relink', 'pending_if' => 'em2']]],
        'pending_unrelated' => ['pending' => ['lan' => ['pending_action' => 'relink', 'pending_if' => 'dhcpha0lagg']]],
        'pending_malformed' => ['pending' => null],
        'pending_unavailable' => ['pending' => [], 'pending_error' => true],
        'sender_missing_plugin' => ['hasync' => ['syncitems' => 'pfsync']],
        'no_sync_target' => ['hasync' => ['syncitems' => 'pfsync', 'synchronizetoip' => '']],
        'legacy_failback' => ['shared' => ['failback_delay' => '60']],
        'malformed' => ['events' => ['dhcp_interface_ha status' => '{"state":"STANDBY","carrier":"invalid"}']],
        'bad_address' => ['events' => ['interface address' => '{"wan":{"not-an-address":"invalid"}}']],
        'addressless' => ['empty_addresses' => 'wan'],
        'unrelated_addressless' => ['empty_addresses' => 'lan'],
        'missing_collision_inventory' => ['remove' => ['shared_mac_collisions']],
        'malformed_collision_inventory' => ['runtime' => ['shared_mac_collisions' => ['unexpected' => 'em2']]],
        'confirmed_collision' => ['runtime' => ['shared_mac_collisions' => ['em2']]],
        'native_ipv6_conflict' => ['native' => ['ipaddrv6' => 'track6']],
        'native_ipv6_conflict_unknown_mac' => ['native' => ['ipaddrv6' => 'track6'], 'remove' => ['shared_mac_collisions']],
        'active_receive_drift' => ['runtime' => ['state' => 'FAULT', 'reason_code' => 'attachment_unverified',
            'reason' => 'The observed interface attachment does not match the verified plugin configuration.',
            'actual_attachment' => 'UNVERIFIED', 'desired_attachment' => 'ATTACHED', 'global_role' => 'MASTER',
            'carrier' => ['promiscuous' => false], 'dhcpha' => ['promiscuous' => false, 'lagg_members' => ['em1']]]],
        'stopped_master_detached' => ['runtime' => ['state' => 'FENCED', 'reason_code' => 'controller_stopped',
            'reason' => 'The controller is stopped and the device is verified detached.',
            'controller_running' => false, 'stopped' => true, 'global_role' => 'MASTER',
            'carrier' => ['promiscuous' => false], 'dhcpha' => ['promiscuous' => false]]],
        'foreign_device' => ['runtime' => ['state' => 'FAULT', 'reason_code' => 'device_ownership_unverified',
            'reason' => 'dhcpha0lagg ownership is not verified.', 'actual_attachment' => 'UNVERIFIED', 'owned' => false]],
        'maintenance' => ['runtime' => ['state' => 'FENCED', 'reason_code' => 'carp_maintenance',
            'reason' => 'Native CARP maintenance mode prevents attachment.', 'carp_maintenance' => true]],
        'none' => ['shared' => ['enabled' => '0'], 'local' => ['managed_interface' => '', 'carrier' => ''],
            'runtime' => array_replace($unmanaged, ['state' => 'DISABLED', 'reason_code' => 'plugin_disabled',
                'reason' => 'The plugin is disabled and no interface is configured.'])],
        'unconfigured_enabled' => ['local' => ['managed_interface' => '', 'carrier' => ''],
            'runtime' => array_replace($unmanaged, ['enabled' => true,
                'reason' => 'The plugin is disabled and no interface is configured.'])],
        'preconfigure' => $preconfigure,
        'preconfigure_unknown_mac' => $preconfigure + ['remove' => ['shared_mac_collisions']],
        'captured_original' => array_replace($preconfigure, ['local' => [],
            'runtime' => $unmanaged + ['carrier_safe' => false]]),
    ];
    $case = $argv[2] ?? '';
    FixtureResponses::$scenario = $scenario = $cases[$case] ?? [];
    $runtime = json_decode(FixtureResponses::$events['dhcp_interface_ha status'], true);
    foreach ($scenario['runtime'] ?? [] as $key => $value) {
        // Only device records merge fields. Lists (including empty/malformed ones) replace whole values.
        $runtime[$key] = in_array($key, ['carrier', 'dhcpha'], true) ? array_replace($runtime[$key], $value) : $value;
    }
    foreach ($scenario['remove'] ?? [] as $key) {
        unset($runtime[$key]);
    }
    FixtureResponses::$events['dhcp_interface_ha status'] = json_encode($runtime);
    FixtureResponses::$events = array_replace(FixtureResponses::$events, $scenario['events'] ?? []);
    if (isset($scenario['empty_addresses'])) {
        $interface = $scenario['empty_addresses'];
        $addresses = json_decode(FixtureResponses::$events['interface address'], true);
        $addresses[$interface] = array_map(fn($family) => [
            'address' => null, 'network' => null, 'bits' => null, 'device' => null,
            'interface' => $interface, 'family' => $family,
        ], ['inet', 'inet6']);
        FixtureResponses::$events['interface address'] = json_encode($addresses);
    }
    foreach ($scenario['native'] ?? [] as $key => $value) {
        \OPNsense\Core\Config::getInstance()->object()->interfaces->wan->$key = $value;
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
