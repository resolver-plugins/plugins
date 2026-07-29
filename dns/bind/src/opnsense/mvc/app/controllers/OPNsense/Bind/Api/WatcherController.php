<?php

namespace OPNsense\Bind\Api;

use OPNsense\Base\ApiMutableModelControllerBase;

class WatcherController extends ApiMutableModelControllerBase
{
    protected static $internalModelName = 'watcher';
    protected static $internalModelClass = '\\OPNsense\\Bind\\Watcher';

    public function searchMappingAction() { return $this->searchBase('mappings.mapping', ['enabled', 'dhcp_source', 'hostname_suffix', 'tsigkey']); }
    public function getMappingAction($uuid = null) { return $this->getBase('mapping', 'mappings.mapping', $uuid); }
    public function addMappingAction() { return $this->addBase('mapping', 'mappings.mapping'); }
    public function delMappingAction($uuid) { return $this->delBase('mappings.mapping', $uuid); }
    public function setMappingAction($uuid) { return $this->setBase('mapping', 'mappings.mapping', $uuid); }
    public function toggleMappingAction($uuid) { return $this->toggleBase('mappings.mapping', $uuid); }
}
