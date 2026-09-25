<?php

/*
 * Copyright (C) 2026 Resolver Plugins contributors
 * All rights reserved.
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are met:
 *
 * 1. Redistributions of source code must retain the above copyright notice,
 *    this list of conditions and the following disclaimer.
 * 2. Redistributions in binary form must reproduce the above copyright notice,
 *    this list of conditions and the following disclaimer in the documentation
 *    and/or other materials provided with the distribution.
 *
 * THIS SOFTWARE IS PROVIDED ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES,
 * INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY
 * AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED.
 */

function dhcp_interface_ha_devices()
{
    $exclude = [];
    $config = \OPNsense\Core\Config::getInstance()->object();
    $carrier = trim((string)($config->OPNsense->DhcpInterfaceHaLocal->carrier ?? ''));
    $dhcpha_assigned = false;

    if (!empty($config->interfaces)) {
        foreach ($config->interfaces->children() as $interface) {
            if ((string)$interface->if === 'dhcpha0lagg') {
                $dhcpha_assigned = true;
                break;
            }
        }
    }

    if ($dhcpha_assigned && !empty($carrier)) {
        $exclude[] = $carrier;
    }

    return [[
        'function' => 'dhcp_interface_ha_prepare_device',
        'configurable' => true,
        'pattern' => '^dhcpha[0-9]+lagg$',
        'reload' => true,
        'spoofmac' => false,
        'volatile' => true,
        'type' => 'dhcpha',
        'names' => [
            'dhcpha0lagg' => [
                'descr' => gettext('dhcpha0lagg (DHCP Interface HA)'),
                'ifdescr' => gettext('DHCP Interface HA'),
                'name' => 'dhcpha0lagg',
                'exclude' => $exclude,
            ],
        ],
    ]];
}

function dhcp_interface_ha_prepare_device($device)
{
    if ($device !== 'dhcpha0lagg') {
        return null;
    }

    // Preparation and carrier movement use the same controller lock and identity check.
    if (mwexec('/usr/local/bin/python3 /usr/local/opnsense/scripts/dhcp_interface_ha/dhcp_interface_ha.py prepare') !== 0) {
        log_msg('DHCP Interface HA could not prepare its owned virtual device', LOG_ERR);
        return null;
    }

    return $device;
}

function dhcp_interface_ha_xmlrpc_sync()
{
    return [[
        'description' => gettext('DHCP Interface HA'),
        'section' => 'OPNsense.DhcpInterfaceHaShared',
        'id' => 'dhcp-interface-ha',
    ]];
}
