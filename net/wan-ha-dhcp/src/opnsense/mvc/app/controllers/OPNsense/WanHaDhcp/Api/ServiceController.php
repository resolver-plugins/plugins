<?php

namespace OPNsense\WanHaDhcp\Api;

use OPNsense\Base\ApiControllerBase;
use OPNsense\Core\Backend;

class ServiceController extends ApiControllerBase
{
    public function applyAction()
    {
        if ($this->request->isPost()) {
            $this->throwReadOnly();
            $result = json_decode((new Backend())->configdRun('wan_ha_dhcp apply'), true);
            return is_array($result) ? $result : ['error' => gettext('Controller apply failed; check system logs.')];
        }
        return ['error' => gettext('POST required.')];
    }
}
