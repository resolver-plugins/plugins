<?php

namespace OPNsense\Bind;

class WatcherController extends \OPNsense\Base\IndexController
{
    public function indexAction()
    {
        $this->view->formDialogEditBindWatcher = $this->getForm('dialogEditBindWatcher');
        $this->view->pick('OPNsense/Bind/watcher');
    }
}
