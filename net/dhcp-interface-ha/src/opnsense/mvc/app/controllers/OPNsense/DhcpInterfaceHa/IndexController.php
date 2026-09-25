<?php

namespace OPNsense\DhcpInterfaceHa;

class IndexController extends \OPNsense\Base\IndexController
{
    public function indexAction()
    {
        $this->view->shared = $this->getForm('shared');
        $this->view->local = $this->getForm('local');
        $this->view->pick('OPNsense/DhcpInterfaceHa/index');
    }
}
