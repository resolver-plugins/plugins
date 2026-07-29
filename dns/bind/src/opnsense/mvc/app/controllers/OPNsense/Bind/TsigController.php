<?php

namespace OPNsense\Bind;

class TsigController extends \OPNsense\Base\IndexController
{
    public function indexAction()
    {
        $this->view->formDialogEditBindTsig = $this->getForm('dialogEditBindTsig');
        $this->view->pick('OPNsense/Bind/tsig');
    }
}
