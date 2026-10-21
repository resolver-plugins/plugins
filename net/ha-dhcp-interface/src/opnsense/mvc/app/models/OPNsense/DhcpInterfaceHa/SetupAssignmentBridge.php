<?php

namespace OPNsense\DhcpInterfaceHa;

use OPNsense\Interfaces\Api\AssignmentController;
use OPNsense\Core\Config;

/**
 * Narrow in-process adapter to the native assignment controller.
 *
 * OPNsense 26.7 keeps assignment staging in NetworkInterface and its shared
 * queue-consuming apply/finalization in AssignmentController. Reusing that
 * action preserves native validation, configd apply and queue accounting.
 */
class SetupAssignmentBridge extends AssignmentController
{
    public function pendingChanges()
    {
        return $this->getModel()->get_if_todo();
    }

    public function stageRelink($interfaceName, $deviceName)
    {
        $model = $this->getModel();
        $model->setNodes([
            'interface' => [
                $interfaceName => ['if' => $deviceName],
            ],
        ]);

        $validations = [];
        foreach ($model->performValidation(false) as $message) {
            $field = $message->getField();
            $validations[$field] = $message->getMessage();
        }
        if (!empty($validations)) {
            return ['staged' => false, 'validations' => $validations];
        }

        // serializeToConfig() uses the native model to stage pending_if. It
        // does not commit the assignment; reconfigureAction() owns that step.
        $model->serializeToConfig(false, true);
        return ['staged' => true, 'validations' => []];
    }

    public function applyStagedChanges($request)
    {
        // The enclosing plugin request has already passed native route ACL,
        // plugin write and ApiControllerBase CSRF checks.
        $this->request = $request;
        try {
            return parent::reconfigureAction();
        } finally {
            // Native reconfigureAction normally relies on request shutdown to
            // release its lock. This in-process call continues in our action.
            Config::getInstance()->unlock();
        }
    }
}
