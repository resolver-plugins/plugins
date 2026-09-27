<?php

namespace OPNsense\DhcpInterfaceHa;

use OPNsense\Base\BaseModel;
use OPNsense\Base\Messages\Message;

/**
 * Shared settings have only value-level validation here. Checks involving the
 * local model, native configuration or runtime observations belong to the
 * combined Settings API transaction, which validates both candidates at once.
 */
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
                gettext('Delayed failback is unavailable in this release; use zero.'),
                $this->failback_delay->getInternalXMLTagName()
            ));
        }

        $mac = strtolower(trim((string)$this->shared_mac));
        if ($mac === '') {
            $messages->appendMessage(new Message(
                gettext('A shared interface MAC is required when DHCP Interface HA is enabled.'),
                $this->shared_mac->getInternalXMLTagName()
            ));
        } elseif (!preg_match('/^(?:[0-9a-f]{2}:){5}[0-9a-f]{2}$/', $mac)) {
            $messages->appendMessage(new Message(
                gettext('Use a colon-delimited MAC address such as 02:11:22:33:44:55.'),
                $this->shared_mac->getInternalXMLTagName()
            ));
        } else {
            $octets = array_map('hexdec', explode(':', $mac));
            if (
                $mac === '00:00:00:00:00:00'
                || $mac === 'ff:ff:ff:ff:ff:ff'
                || ($octets[0] & 0x01)
            ) {
                $messages->appendMessage(new Message(
                    gettext('The shared interface MAC must be a usable unicast address.'),
                    $this->shared_mac->getInternalXMLTagName()
                ));
            }
        }

        return $messages;
    }
}
