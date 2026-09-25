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

function wan_ha_dhcp_devices()
{
    $exclude = [];
    $config = \OPNsense\Core\Config::getInstance()->object();
    $carrier = trim((string)($config->OPNsense->WanHaDhcpLocal->carrier ?? ''));
    $wanha_assigned = false;

    if (!empty($config->interfaces)) {
        foreach ($config->interfaces->children() as $interface) {
            if ((string)$interface->if === 'wanha0lagg') {
                $wanha_assigned = true;
                break;
            }
        }
    }

    if ($wanha_assigned && !empty($carrier)) {
        $exclude[] = $carrier;
    }

    return [[
        'function' => 'wan_ha_dhcp_prepare_device',
        'configurable' => true,
        'pattern' => '^wanha[0-9]+lagg$',
        'reload' => true,
        'spoofmac' => false,
        'volatile' => true,
        'type' => 'wanha',
        'names' => [
            'wanha0lagg' => [
                'descr' => gettext('wanha0lagg (WAN HA DHCP)'),
                'ifdescr' => gettext('WAN HA DHCP'),
                'name' => 'wanha0lagg',
                'exclude' => $exclude,
            ],
        ],
    ]];
}

function wan_ha_dhcp_prepare_device($device)
{
    if ($device !== 'wanha0lagg') {
        return null;
    }

    // Preparation and carrier movement use the same controller lock and identity check.
    if (mwexec('/usr/local/bin/python3 /usr/local/opnsense/scripts/wan_ha_dhcp/wan_ha_dhcp.py prepare') !== 0) {
        log_msg('WAN HA DHCP could not prepare its owned virtual device', LOG_ERR);
        return null;
    }

    return $device;
}

function wan_ha_dhcp_xmlrpc_sync()
{
    return [[
        'description' => gettext('WAN HA DHCP'),
        'section' => 'OPNsense.WanHaDhcpShared',
        'id' => 'wan-ha-dhcp',
    ]];
}
