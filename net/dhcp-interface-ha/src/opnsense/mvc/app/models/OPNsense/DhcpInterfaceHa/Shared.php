<?php

namespace OPNsense\DhcpInterfaceHa;

use OPNsense\Base\BaseModel;
use OPNsense\Base\Messages\Message;
use OPNsense\Core\Backend;
use OPNsense\Core\Config;

class Shared extends BaseModel
{
    public function performValidation($validateFullModel = false)
    {
        $messages = parent::performValidation($validateFullModel);

        if (empty((string)$this->enabled)) {
            return $messages;
        }

        if ((int)(string)$this->failback_delay !== 0) {
            $messages->appendMessage(new Message(
                gettext('Delayed failback is not implemented in this experimental release; use zero.'),
                $this->failback_delay->getInternalXMLTagName()
            ));
        }

        $mac = strtolower(trim((string)$this->shared_mac));
        if (empty($mac)) {
            $messages->appendMessage(new Message(
                gettext('A shared interface MAC is required when DHCP Interface HA is enabled.'),
                $this->shared_mac->getInternalXMLTagName()
            ));
        } elseif (!preg_match('/^(?:[0-9a-f]{2}:){5}[0-9a-f]{2}$/', $mac)) {
            $messages->appendMessage(new Message(
                gettext('Use a colon-delimited MAC address such as 02:11:22:33:44:55.'),
                $this->shared_mac->getInternalXMLTagName()
            ));
        } elseif (filter_var($mac, FILTER_VALIDATE_MAC)) {
            $octets = array_map('hexdec', explode(':', $mac));
            if ($mac === '00:00:00:00:00:00' || $mac === 'ff:ff:ff:ff:ff:ff' || ($octets[0] & 0x01)) {
                $messages->appendMessage(new Message(
                    gettext('The shared interface MAC must be a usable unicast address.'),
                    $this->shared_mac->getInternalXMLTagName()
                ));
            }
        }

        $local = new Local();
        $localCarrier = trim((string)$local->carrier);

        if (!empty($mac)) {
            $runtimeInterfaces = json_decode((new Backend())->configdRun('interface list ifconfig'), true) ?? [];
            foreach ($runtimeInterfaces as $ifname => $details) {
                if ($ifname === $localCarrier || $ifname === 'dhcpha0lagg') {
                    continue;
                }
                $runtimeMacs = array_filter([
                    strtolower((string)($details['macaddr'] ?? '')),
                    strtolower((string)($details['macaddr_hw'] ?? '')),
                ]);
                if (in_array($mac, $runtimeMacs, true)) {
                    $messages->appendMessage(new Message(
                        sprintf(
                            gettext('The shared interface MAC duplicates local interface %s; choose a unique shared identity or the selected local carrier MAC.'),
                            $ifname
                        ),
                        $this->shared_mac->getInternalXMLTagName()
                    ));
                    break;
                }
            }
        }

        if (empty(trim((string)$local->carrier))) {
            $messages->appendMessage(new Message(
                gettext('Configure a valid local carrier on this node before enabling DHCP Interface HA.'),
                $this->enabled->getInternalXMLTagName()
            ));
        } elseif ($local->performValidation(true)->count() !== 0) {
            $messages->appendMessage(new Message(
                gettext('The node-local carrier configuration is not valid.'),
                $this->enabled->getInternalXMLTagName()
            ));
        }

        $ifconfig = json_decode((new Backend())->configdRun('interface list ifconfig'), true) ?? [];
        if (empty($ifconfig['dhcpha0lagg']['laggproto'])) {
            $messages->appendMessage(new Message(
                gettext('dhcpha0lagg must exist as a LAGG interface before DHCP Interface HA can be enabled.'),
                $this->enabled->getInternalXMLTagName()
            ));
        } elseif (!is_file('/var/run/dhcp-interface-ha/device.dhcpha0lagg')) {
            $messages->appendMessage(new Message(
                gettext('dhcpha0lagg exists but is not marked as a device created by this plugin in the current boot.'),
                $this->enabled->getInternalXMLTagName()
            ));
        } elseif ($ifconfig['dhcpha0lagg']['laggproto'] !== 'failover') {
            $messages->appendMessage(new Message(
                gettext('dhcpha0lagg must use the failover LAGG protocol.'),
                $this->enabled->getInternalXMLTagName()
            ));
        }

        $interface = (string)$this->managed_interface;
        $config = Config::getInstance()->object();
        if (empty($config->interfaces->$interface)) {
            $messages->appendMessage(new Message(
                gettext('The managed interface does not exist.'),
                $this->managed_interface->getInternalXMLTagName()
            ));
            return $messages;
        }

        $managed = $config->interfaces->$interface;
        if (empty((string)$managed->enable) || (string)$managed->ipaddr !== 'dhcp') {
            $messages->appendMessage(new Message(
                gettext('The managed interface must be enabled and use IPv4 DHCP.'),
                $this->managed_interface->getInternalXMLTagName()
            ));
        }

        $ipv6 = strtolower(trim((string)$managed->ipaddrv6));
        if (!empty($ipv6) && $ipv6 !== 'none') {
            $messages->appendMessage(new Message(
                gettext('IPv6 on the managed DHCP interface is not supported by version 1.'),
                $this->managed_interface->getInternalXMLTagName()
            ));
        }

        if ((string)$managed->if !== 'dhcpha0lagg') {
            $messages->appendMessage(new Message(
                gettext('The managed interface must be assigned to dhcpha0lagg before DHCP Interface HA can be enabled.'),
                $this->managed_interface->getInternalXMLTagName()
            ));
        }

        if (!empty((string)$managed->spoofmac)) {
            $messages->appendMessage(new Message(
                gettext('Remove the native interface MAC spoof setting and use the plugin Shared interface MAC instead.'),
                $this->shared_mac->getInternalXMLTagName()
            ));
        }


        if (!empty((string)$managed->hw_settings_overwrite)) {
            $messages->appendMessage(new Message(
                gettext('Per-interface hardware offload overrides on the managed DHCP interface are not supported by version 1; use global hardware settings or clear the interface override before enabling.'),
                $this->managed_interface->getInternalXMLTagName()
            ));
        }

        if (!empty((string)$managed->media) || !empty((string)$managed->mediaopt)) {
            $messages->appendMessage(new Message(
                gettext('Custom media/mediaopt settings on the managed DHCP interface are not supported by version 1 because they belong to the node-local carrier.'),
                $this->managed_interface->getInternalXMLTagName()
            ));
        }

        $carpCount = 0;
        $managedHasCarp = false;
        if (!empty($config->virtualip->vip)) {
            foreach ($config->virtualip->vip as $vip) {
                if ((string)$vip->mode !== 'carp') {
                    continue;
                }
                $carpCount++;
                if ((string)$vip->interface === $interface) {
                    $managedHasCarp = true;
                }
            }
        }

        if ($carpCount === 0) {
            $messages->appendMessage(new Message(
                gettext('DHCP Interface HA requires an existing native OPNsense CARP configuration.'),
                $this->enabled->getInternalXMLTagName()
            ));
        }

        if ($managedHasCarp) {
            $messages->appendMessage(new Message(
                gettext('Remove CARP VIPs from the managed DHCP interface before enabling DHCP Interface HA. CARP remains the cluster authority on other interfaces, but the selected DHCP interface itself must not carry a CARP VIP.'),
                $this->managed_interface->getInternalXMLTagName()
            ));
        }

        return $messages;
    }
}
