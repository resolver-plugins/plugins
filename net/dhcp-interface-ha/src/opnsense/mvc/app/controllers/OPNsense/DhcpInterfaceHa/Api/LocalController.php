<?php

namespace OPNsense\DhcpInterfaceHa\Api;

use OPNsense\Base\ApiMutableModelControllerBase;

class LocalController extends ApiMutableModelControllerBase
{
    protected static $internalModelClass = '\\OPNsense\\DhcpInterfaceHa\\Local';
    protected static $internalModelName = 'dhcphalocal';
}
