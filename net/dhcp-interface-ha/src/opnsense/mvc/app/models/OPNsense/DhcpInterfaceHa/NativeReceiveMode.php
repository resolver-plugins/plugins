<?php

namespace OPNsense\DhcpInterfaceHa;

/** Native interface apply must preserve the receive mode required by our LAGG. */
class NativeReceiveMode
{
    public static function update(\SimpleXMLElement $interface, bool $managed): bool
    {
        $before = $interface->asXML();
        $key = 'dhcpha_original_promisc';
        if ($managed && !isset($interface->$key)) {
            // Keep absent, empty and explicitly disabled distinct for exact removal.
            $interface->$key = json_encode([isset($interface->promisc), (string)$interface->promisc]);
        }
        if (isset($interface->$key)) {
            $original = json_decode((string)$interface->$key, true);
            if (!is_array($original) || array_keys($original) !== [0, 1]
                || !is_bool($original[0]) || !is_string($original[1])) {
                throw new \RuntimeException('The original native promiscuous-mode setting is invalid.');
            }
            if ($managed) {
                $interface->promisc = '1';
            } else {
                if ($original[0]) {
                    $interface->promisc = $original[1];
                } else {
                    unset($interface->promisc);
                }
                unset($interface->$key);
            }
        }
        return $interface->asXML() !== $before;
    }
}
