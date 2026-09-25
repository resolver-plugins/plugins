<?php

namespace OPNsense\DhcpInterfaceHa\Api;

use OPNsense\Base\ApiControllerBase;
use OPNsense\Core\Backend;
use OPNsense\Core\Config;
use OPNsense\Core\Hasync;
use OPNsense\DhcpInterfaceHa\Local;
use OPNsense\DhcpInterfaceHa\Shared;

class StatusController extends ApiControllerBase
{
    public function carriersAction()
    {
        $backend = new Backend();
        $devices = json_decode($backend->configdRun('interface list assign-opts'), true) ?? [];
        $ifconfig = json_decode($backend->configdRun('interface list ifconfig'), true) ?? [];
        $shared = new Shared();
        $managedInterface = (string)$shared->managed_interface ?: 'wan';
        $blocked = Local::blockedCarrierDevices($managedInterface);
        $result = [];

        foreach ($devices as $name => $details) {
            if ($name === 'dhcpha0lagg' || isset($blocked[$name])) {
                continue;
            }

            $runtimeReason = Local::carrierRuntimeEligibility($name, $devices, $ifconfig);
            if ($runtimeReason !== null) {
                $blocked[$name] = $runtimeReason;
                continue;
            }

            $group = $details['optgroup'] ?? '';
            $result[$name] = [
                'name' => $name,
                'label' => $details['value'] ?? $name,
                'type' => $group,
            ];
        }

        $local = new Local();
        $currentCarrier = trim((string)$local->carrier);
        if (
            !empty($currentCarrier) &&
            empty($result[$currentCarrier]) &&
            empty($blocked[$currentCarrier])
        ) {
            $runtimeReason = Local::carrierRuntimeEligibility($currentCarrier, $devices, $ifconfig);
            if ($runtimeReason === null) {
                $runtime = $ifconfig[$currentCarrier] ?? [];
                $result[$currentCarrier] = [
                    'name' => $currentCarrier,
                    'label' => sprintf(gettext('%s (current DHCP Interface HA carrier)'), $currentCarrier),
                    'type' => !empty($runtime['vlan']) ? 'vlan' : 'hardware',
                ];
            } else {
                $blocked[$currentCarrier] = $runtimeReason;
            }
        }

        ksort($result, SORT_NATURAL);
        return ['items' => array_values($result), 'blocked' => $blocked];
    }

    public function generateMacAction()
    {
        $bytes = chr(0x02) . random_bytes(5);
        return ['mac' => implode(':', str_split(bin2hex($bytes), 2))];
    }

    private function reduceGlobalCarpRole(array $interfaces)
    {
        $states = [];

        foreach ($interfaces as $details) {
            foreach (($details['carp'] ?? []) as $carp) {
                if (!empty($carp['status'])) {
                    $states[] = strtoupper($carp['status']);
                }
            }
        }

        if (empty($states)) {
            return 'INDETERMINATE';
        }
        if (in_array('BACKUP', $states, true)) {
            return 'BACKUP';
        }
        foreach ($states as $state) {
            if ($state !== 'MASTER') {
                return 'INDETERMINATE';
            }
        }
        return 'MASTER';
    }

    public function environmentAction()
    {
        $backend = new Backend();
        $interfaces = json_decode($backend->configdRun('interface list ifconfig'), true) ?? [];
        $carp = json_decode($backend->configdRun('interface show carp'), true) ?? [];
        $globalRole = !empty($carp['allow']) && empty($carp['maintenancemode'])
            ? $this->reduceGlobalCarpRole($interfaces)
            : 'INDETERMINATE';

        $shared = new Shared();
        $local = new Local();
        $hasync = new Hasync();
        $config = Config::getInstance()->object();

        $managedName = (string)$shared->managed_interface ?: 'wan';
        $managed = !empty($config->interfaces->$managedName) ? $config->interfaces->$managedName : null;
        $syncItems = array_filter(explode(',', (string)$hasync->syncitems));

        $carpVipCount = 0;
        $managedCarpVipCount = 0;
        if (!empty($config->virtualip->vip)) {
            foreach ($config->virtualip->vip as $vip) {
                if ((string)$vip->mode !== 'carp') {
                    continue;
                }
                $carpVipCount++;
                if ((string)$vip->interface === $managedName) {
                    $managedCarpVipCount++;
                }
            }
        }

        $warnings = [];
        if ($carpVipCount === 0) {
            $warnings[] = gettext('No native OPNsense CARP VIPs are configured.');
        }
        if ($managedCarpVipCount > 0) {
            $warnings[] = gettext('The managed DHCP interface still has one or more CARP VIPs assigned to it.');
        }
        if (empty((string)$hasync->pfsyncinterface)) {
            $warnings[] = gettext('pfsync is not configured; established state preservation will not be available.');
        }
        if (!empty((string)$hasync->synchronizetoip) && !in_array('dhcp-interface-ha', $syncItems, true)) {
            $warnings[] = gettext('DHCP Interface HA shared settings are not selected for XMLRPC HA synchronization.');
        }
        if ($managed !== null && (string)$managed->if !== 'dhcpha0lagg') {
            $warnings[] = gettext('The managed logical interface is not assigned to dhcpha0lagg.');
        }
        if ($managed !== null && !empty((string)$managed->spoofmac)) {
            $warnings[] = gettext('The managed DHCP interface still has a native OPNsense spoof MAC configured.');
        }
        if ($managed !== null) {
            $managedIpv6 = strtolower(trim((string)$managed->ipaddrv6));
            if (!empty($managedIpv6) && $managedIpv6 !== 'none') {
                $warnings[] = gettext('IPv6 is configured on the managed DHCP interface; version 1 is IPv4-only.');
            }
        }
        $sharedMac = strtolower(trim((string)$shared->shared_mac));
        if (
            str_starts_with($sharedMac, '00:00:5e:00:01:') ||
            str_starts_with($sharedMac, '00:00:5e:00:02:')
        ) {
            $warnings[] = gettext('The configured shared MAC is in a standardized VRRP/CARP virtual-router range and may be rejected by access networks.');
        }

        $localCarrier = trim((string)$local->carrier);
        $carrierRuntime = !empty($interfaces[$localCarrier]) ? $interfaces[$localCarrier] : [];
        $dhcphaRuntime = !empty($interfaces['dhcpha0lagg']) ? $interfaces['dhcpha0lagg'] : [];
        $dhcphaMembers = !empty($dhcphaRuntime['laggport']) ? array_keys($dhcphaRuntime['laggport']) : [];
        $runtime = json_decode($backend->configdRun('dhcp_interface_ha status'), true) ?? [];
        $dhcphaOwned = !empty($runtime['owned']);
        if (!empty($runtime['reason'])) {
            $warnings[] = $runtime['reason'];
        }
        if (!empty($dhcphaRuntime) && !$dhcphaOwned) {
            $warnings[] = gettext('dhcpha0lagg exists without the plugin runtime ownership marker; the controller will refuse to mutate it.');
        }

        return [
            'global_role' => $globalRole,
            'warnings' => $warnings,
            'runtime' => $runtime,
            'carp' => $carp,
            'interfaces' => $interfaces,
            'pfsync_runtime' => json_decode($backend->configdRun('filter list pfsync json'), true) ?? [],
            'ha' => [
                'disable_preempt' => (string)$hasync->disablepreempt,
                'pfsync_interface' => (string)$hasync->pfsyncinterface,
                'pfsync_peer' => (string)$hasync->pfsyncpeerip,
                'pfsync_version' => (string)$hasync->pfsyncversion,
                'pfsync_defer' => (string)$hasync->pfsyncdefer,
                'xmlrpc_target' => (string)$hasync->synchronizetoip,
                'plugin_sync_enabled' => in_array('dhcp-interface-ha', $syncItems, true),
                'preemption_enabled' => empty((string)$hasync->disablepreempt),
            ],
            'managed' => [
                'name' => $managedName,
                'device' => $managed !== null ? (string)$managed->if : '',
                'enabled' => $managed !== null ? !empty((string)$managed->enable) : false,
                'ipv4' => $managed !== null ? (string)$managed->ipaddr : '',
                'ipv6' => $managed !== null ? (string)$managed->ipaddrv6 : '',
                'spoof_mac' => $managed !== null ? (string)$managed->spoofmac : '',
                'carp_vip_count' => $managedCarpVipCount,
            ],
            'plugin' => [
                'enabled' => !empty((string)$shared->enabled),
                'shared_mac' => (string)$shared->shared_mac,
                'failback_delay' => (string)$shared->failback_delay,
                'local_carrier' => $localCarrier,
                'carrier_status' => (string)($carrierRuntime['status'] ?? ''),
                'carrier_mac' => (string)($carrierRuntime['macaddr'] ?? ''),
                'carrier_hw_mac' => (string)($carrierRuntime['macaddr_hw'] ?? ''),
                'carrier_mtu' => (string)($carrierRuntime['mtu'] ?? ''),
                'dhcpha_exists' => !empty($dhcphaRuntime),
                'dhcpha_owned' => $dhcphaOwned,
                'dhcpha_status' => (string)($dhcphaRuntime['status'] ?? ''),
                'dhcpha_mac' => (string)($dhcphaRuntime['macaddr'] ?? ''),
                'dhcpha_mtu' => (string)($dhcphaRuntime['mtu'] ?? ''),
                'dhcpha_protocol' => (string)($dhcphaRuntime['laggproto'] ?? ''),
                'dhcpha_members' => $dhcphaMembers,
            ],
        ];
    }
}
