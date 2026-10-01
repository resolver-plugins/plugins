<?php

namespace OPNsense\DhcpInterfaceHa;

use OPNsense\Base\BaseModel;
use OPNsense\Base\Messages\Message;
use OPNsense\Core\Config;

class Local extends BaseModel
{
    public static function blockedCarrierDevices($managedInterface)
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
        if (!is_array($runtime) || empty($runtime)) {
            return gettext('does not currently exist in the FreeBSD interface inventory');
        }

        $device = $devices[$carrier] ?? [];
        if (!is_array($device)) {
            return gettext('has an invalid assignment inventory record');
        }
        $group = $device['optgroup'] ?? null;
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
        if ($group === 'vlan' || (!empty($runtime['vlan']) && !is_array($runtime['vlan']))) {
            return gettext('requires VLAN carrier qualification; this experimental release supports Ethernet adapters only');
        }
        if ($group !== 'hardware') {
            return gettext('is not an eligible physical Ethernet interface');
        }

        if (!empty($runtime['laggproto']) || !empty($runtime['members']) || !empty($runtime['tunnel']) || !empty($runtime['vxlan'])) {
            return gettext('is already a virtual aggregation, bridge, or tunnel-like interface');
        }
        if (!empty($runtime['carp'])) {
            return gettext('already carries runtime CARP instances');
        }

        foreach ($ifconfig as $otherName => $other) {
            if (!is_array($other) || $otherName === $carrier || $otherName === 'dhcpha0lagg') {
                continue;
            }
            foreach (['laggport', 'members'] as $membership) {
                if (array_key_exists($membership, $other) && !is_array($other[$membership])) {
                    return gettext('cannot be checked because the runtime membership inventory is malformed');
                }
                if (
                    is_array($other[$membership] ?? null)
                    && array_key_exists($carrier, $other[$membership])
                ) {
                    return sprintf(gettext('is a runtime member of %s'), $otherName);
                }
            }
            if (array_key_exists('vlan', $other) && !empty($other['vlan']) && !is_array($other['vlan'])) {
                return gettext('cannot be checked because the runtime VLAN inventory is malformed');
            }
            if (is_array($other['vlan'] ?? null) && ($other['vlan']['parent'] ?? null) === $carrier) {
                return sprintf(gettext('is a runtime VLAN parent for %s'), $otherName);
            }
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
        if (
            $carrier !== ''
            && $this->carrier->isFieldChanged()
            && (!preg_match('/^[a-zA-Z][a-zA-Z0-9_.-]{0,14}$/', $carrier) || $carrier === 'dhcpha0lagg')
        ) {
            $messages->appendMessage(new Message(
                gettext('Choose a valid local carrier other than dhcpha0lagg.'),
                $this->carrier->getInternalXMLTagName()
            ));
        }
        return $messages;
    }
}
