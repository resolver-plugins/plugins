<?php

namespace OPNsense\DhcpInterfaceHa\Api;

use OPNsense\Base\ApiControllerBase;
use OPNsense\Core\Backend;

class ServiceController extends ApiControllerBase
{
    public function applyAction()
    {
        if (!$this->request->isPost()) {
            return ['applied' => false, 'error' => gettext('POST required.')];
        }
        $this->throwReadOnly();
        try {
            $raw = (new Backend())->configdRun('dhcp_interface_ha apply', false, 15, 2);
            $status = json_decode($raw, true);
            if (!is_array($status) || isset($status['error'])) {
                return [
                    'applied' => $raw === '' ? null : false,
                    'error' => $raw === ''
                        ? gettext('Apply result is unknown. Refresh status before retrying.')
                        : gettext('Apply failed; check system logs.'),
                    'status' => $this->readStatus(),
                ];
            }
            return ['applied' => true, 'error' => null, 'status' => $status];
        } catch (\Throwable $exception) {
            return [
                'applied' => null,
                'error' => gettext('Apply result is unknown. Refresh status before retrying.'),
                'status' => $this->readStatus(),
            ];
        }
    }

    public function prepareAction()
    {
        if (!$this->request->isPost()) {
            return ['prepared' => false, 'error' => gettext('POST required.')];
        }
        $this->throwReadOnly();
        try {
            $result = json_decode((new Backend())->configdRun('dhcp_interface_ha prepare_setup', false, 10, 2), true);
            if (!is_array($result) || empty($result['prepared']) || empty($result['detached']) || empty($result['owned'])) {
                return ['prepared' => false, 'error' => gettext('Device preparation could not be verified; check system logs.')];
            }
            return $result;
        } catch (\Throwable $exception) {
            return ['prepared' => false, 'error' => gettext('Device preparation failed; check system logs.')];
        }
    }

    private function readStatus()
    {
        try {
            $status = json_decode((new Backend())->configdRun('dhcp_interface_ha status', false, 5, 1), true);
            return is_array($status) && !isset($status['error']) ? $status : null;
        } catch (\Throwable $exception) {
            return null;
        }
    }
}
