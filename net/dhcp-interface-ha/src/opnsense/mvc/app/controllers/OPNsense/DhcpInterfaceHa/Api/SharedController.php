<?php

namespace OPNsense\DhcpInterfaceHa\Api;

use OPNsense\Base\ApiMutableModelControllerBase;

class SharedController extends ApiMutableModelControllerBase
{
    protected static $internalModelClass = '\\OPNsense\\DhcpInterfaceHa\\Shared';
    protected static $internalModelName = 'dhcphashared';
}
