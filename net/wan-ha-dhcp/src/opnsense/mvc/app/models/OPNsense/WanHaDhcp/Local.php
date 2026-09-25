<?php

namespace OPNsense\WanHaDhcp;

use OPNsense\Base\BaseModel;
use OPNsense\Base\Messages\Message;
use OPNsense\Core\Backend;
use OPNsense\Core\Config;

class Local extends BaseModel
{
    public static function blockedCarrierDevices($managedInterface = 'wan')
    {
        $config = Config::getInstance()->object();
        $blocked = [];

        if (!empty($config->interfaces)) {
            foreach ($config->interfaces->children() as $name => $interface) {
                $device = trim((string)$interface->if);
                if (!empty($device) && $name !== $managedInterface) {
                    $blocked[$device] = sprintf(
                        gettext('already assigned to interface %s'),
                        strtoupper($name)
                    );
                }
            }
        }

        if (!empty($config->vlans)) {
            foreach ($config->vlans->children() as $vlan) {
                $parent = trim((string)$vlan->if);
                if (!empty($parent)) {
                    $blocked[$parent] = gettext('is the parent of a configured VLAN');
                }
            }
        }

        if (!empty($config->laggs)) {
            foreach ($config->laggs->children() as $lagg) {
                foreach (array_filter(explode(',', (string)$lagg->members)) as $member) {
                    $blocked[$member] = sprintf(
                        gettext('is a member of configured LAGG %s'),
                        (string)$lagg->laggif
                    );
                }
            }
        }

        if (!empty($config->bridges)) {
            foreach ($config->bridges->children() as $bridge) {
                foreach (array_filter(explode(',', (string)$bridge->members)) as $member) {
                    if (!empty($config->interfaces->$member->if)) {
                        $device = (string)$config->interfaces->$member->if;
                        $blocked[$device] = sprintf(
                            gettext('is a member of configured bridge %s'),
                            (string)$bridge->bridgeif
                        );
                    }
                }
            }
        }

        if (!empty($config->ppps)) {
            foreach ($config->ppps->children() as $ppp) {
                foreach (array_filter(explode(',', (string)$ppp->ports)) as $port) {
                    $blocked[$port] = sprintf(
                        gettext('is used by configured %s interface'),
                        strtoupper((string)$ppp->type)
                    );
                }
            }
        }

        return $blocked;
    }

    public static function carrierRuntimeEligibility($carrier, array $devices, array $ifconfig)
    {
        $runtime = $ifconfig[$carrier] ?? null;
        if (empty($runtime)) {
            return gettext('does not currently exist in the FreeBSD interface inventory');
        }

        $group = $devices[$carrier]['optgroup'] ?? null;
        if ($group === null) {
            /*
             * After migration the plugin intentionally excludes its carrier
             * from OPNsense's general assignment options.  Preserve validation
             * of that already-configured carrier using runtime shape.
             */
            if (!empty($runtime['vlan'])) {
                $group = 'vlan';
            } elseif (!empty($runtime['is_physical'])) {
                $group = 'hardware';
            }
        }
        if ($group === 'vlan') {
            return gettext('requires VLAN carrier qualification; this experimental release supports Ethernet adapters only');
        }
        if ($group !== 'hardware') {
            return gettext('is not an eligible physical Ethernet interface');
        }

        if (!empty($runtime['laggproto']) || !empty($runtime['members']) || !empty($runtime['tunnel']) || !empty($runtime['vxlan'])) {
            return gettext('is already a virtual aggregation, bridge, or tunnel-like interface');
        }

        $mac = strtolower((string)($runtime['macaddr'] ?? ''));
        if (empty($mac) || $mac === '00:00:00:00:00:00' || !filter_var($mac, FILTER_VALIDATE_MAC)) {
            return gettext('does not expose a usable Ethernet MAC address');
        }

        return null;
    }

    public function performValidation($validateFullModel = false)
    {
        $messages = parent::performValidation($validateFullModel);
        $carrier = trim((string)$this->carrier);
        $shared = new Shared();

        if (empty($carrier)) {
            if (!empty((string)$shared->enabled)) {
                $messages->appendMessage(new Message(
                    gettext('A local WAN carrier is required when WAN HA DHCP is enabled.'),
                    $this->carrier->getInternalXMLTagName()
                ));
            }
            return $messages;
        }

        if ($carrier === 'wanha0lagg') {
            $messages->appendMessage(new Message(
                gettext('wanha0lagg cannot be its own local carrier.'),
                $this->carrier->getInternalXMLTagName()
            ));
            return $messages;
        }

        $backend = new Backend();
        $devices = json_decode($backend->configdRun('interface list assign-opts'), true) ?? [];
        $ifconfig = json_decode($backend->configdRun('interface list ifconfig'), true) ?? [];
        $runtimeReason = self::carrierRuntimeEligibility($carrier, $devices, $ifconfig);
        if ($runtimeReason !== null) {
            $messages->appendMessage(new Message(
                sprintf(
                    gettext('The selected carrier cannot be used because it %s.'),
                    $runtimeReason
                ),
                $this->carrier->getInternalXMLTagName()
            ));
            return $messages;
        }

        $managedInterface = (string)$shared->managed_interface ?: 'wan';
        $blocked = self::blockedCarrierDevices($managedInterface);
        if (!empty($blocked[$carrier])) {
            $messages->appendMessage(new Message(
                sprintf(
                    gettext('The selected carrier cannot be used because it %s.'),
                    $blocked[$carrier]
                ),
                $this->carrier->getInternalXMLTagName()
            ));
        }

        return $messages;
    }
}
