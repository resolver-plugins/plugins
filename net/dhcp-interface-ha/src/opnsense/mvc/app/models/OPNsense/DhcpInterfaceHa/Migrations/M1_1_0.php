<?php

namespace OPNsense\DhcpInterfaceHa\Migrations;

use OPNsense\Base\BaseModelMigration;
use OPNsense\Core\Config;
use OPNsense\DhcpInterfaceHa\Local;
use OPNsense\DhcpInterfaceHa\Shared;

class M1_1_0 extends BaseModelMigration
{
    public function run($model)
    {
        if ($model instanceof Shared) {
            // Model discovery order is unspecified. Copy the legacy field
            // before serializing Shared, which no longer includes it.
            $local = new Local(true);
            if (version_compare($local->getVersion(), '1.1.0', '<') && !$local->runMigrations()) {
                throw new \RuntimeException('Unable to migrate the local DHCP Interface HA assignment.');
            }
        } elseif ($model instanceof Local) {
            $config = Config::getInstance()->object()->OPNsense;
            // Run only for pre-1.1.0 Local models. Later XMLRPC updates of
            // Shared must never import a peer's old logical interface ID.
            if (!isset($config->DhcpInterfaceHaLocal->managed_interface)) {
                $model->managed_interface = trim((string)($config->DhcpInterfaceHaShared->managed_interface ?? ''));
            }
        }
        parent::run($model);
    }
}
