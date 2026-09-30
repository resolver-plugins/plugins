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

    // Disabled setup must allow native assignment restoration to the carrier.
    if ($dhcpha_assigned && !empty($carrier)
        && !empty((string)($config->OPNsense->DhcpInterfaceHaShared->enabled ?? '0'))) {
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
                'descr' => gettext('dhcpha0lagg (HA DHCP Interface)'),
                'ifdescr' => gettext('HA DHCP Interface'),
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
    $result = mwexecf('/usr/local/bin/python3 /usr/local/opnsense/scripts/dhcp_interface_ha/dhcp_interface_ha.py prepare');
    if ($result !== 0) {
        // The controller logs its own failed attempt. Shell execution failures
        // cannot reach that event boundary and still need a native boot error.
        if (in_array($result, [126, 127], true)) {
            log_msg('HA DHCP Interface controller could not be executed', LOG_ERR);
        }
        return null;
    }

    return $device;
}

function dhcp_interface_ha_xmlrpc_sync()
{
    return [[
        'description' => gettext('HA DHCP Interface'),
        'section' => 'OPNsense.DhcpInterfaceHaShared',
        'id' => 'dhcp-interface-ha',
    ]];
}

function dhcp_interface_ha_configure()
{
    // Native routing finishes its default/static routes before this callback.
    return [
        'monitor' => ['dhcp_interface_ha_routing:0'],
        'newwanip' => ['dhcp_interface_ha_routing:0'],
    ];
}

function dhcp_interface_ha_routing()
{
    $config = \OPNsense\Core\Config::getInstance()->object();
    if ((string)($config->OPNsense->DhcpInterfaceHaLocal->standby_enabled ?? '0') !== '1'
        && !is_file('/var/run/dhcp-interface-ha/standby-route.json')) {
        return;
    }
    if (mwexecf('/usr/local/bin/python3 /usr/local/opnsense/scripts/dhcp_interface_ha/dhcp_interface_ha.py routing') !== 0) {
        log_msg('HA DHCP Interface standby routing could not be verified; inspect plugin diagnostics', LOG_ERR);
    }
}
