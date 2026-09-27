<?php

namespace OPNsense\DhcpInterfaceHa;

use OPNsense\Core\ACL;
use OPNsense\Core\Config;

class IndexController extends \OPNsense\Base\IndexController
{
    public function indexAction()
    {
        $this->view->settings = $this->getForm('settings');
        $this->view->canWriteSettings = $this->canAccess('/api/dhcpinterfaceha/settings/set')
            && !$this->isReadOnly();
        $this->view->canConfigureInterface = $this->view->canWriteSettings
            && $this->canAccess('/api/interfaces/assignment/reconfigure');
        $this->view->configureAllowedInterfaces = $this->configureAllowedInterfaces();
        $this->view->canEnableSync = $this->view->canWriteSettings
            && $this->canAccess('/api/core/hasync/set');
        $this->view->canRunRecovery = $this->view->canWriteSettings
            && $this->canAccess('/api/dhcpinterfaceha/service/prepare')
            && $this->canAccess('/api/dhcpinterfaceha/service/apply');
        $this->view->canGenerateMac = $this->view->canWriteSettings
            && $this->canAccess('/api/dhcpinterfaceha/status/generate_mac');
        $this->view->canViewLogs = $this->canViewLogs();
        $this->view->pick('OPNsense/DhcpInterfaceHa/index');
    }

    public function logAction()
    {
        $this->view->canViewLogs = $this->canViewLogs();
        $this->view->localLoggingEnabled = $this->localLoggingEnabled();
        $this->view->pick('OPNsense/DhcpInterfaceHa/log');
    }

    private function canAccess($route)
    {
        try {
            $username = $this->getUserName();
            return is_string($username) && $username !== ''
                && (new ACL())->isPageAccessible($username, $route);
        } catch (\Throwable $exception) {
            return false;
        }
    }

    private function isReadOnly()
    {
        try {
            $username = $this->getUserName();
            return !is_string($username) || $username === ''
                || (new ACL())->hasPrivilege($username, 'user-config-readonly');
        } catch (\Throwable $exception) {
            return true;
        }
    }

    private function canViewLogs()
    {
        foreach ([
            '/api/diagnostics/log/dhcpinterfaceha/core',
            '/api/diagnostics/log/dhcpinterfaceha/core/export',
            '/api/diagnostics/log/dhcpinterfaceha/core/live',
        ] as $route) {
            if (!$this->canAccess($route)) {
                return false;
            }
        }
        return true;
    }

    private function configureAllowedInterfaces()
    {
        if (!$this->view->canConfigureInterface) {
            return [];
        }
        try {
            $allowed = [];
            $config = Config::getInstance()->object();
            foreach ($config->interfaces->children() as $name => $interface) {
                if (preg_match('/^[A-Za-z][A-Za-z0-9_.-]{0,14}$/', (string)$name)
                    && $this->canAccess('/api/interfaces/assignment/set_item/' . $name)) {
                    $allowed[] = (string)$name;
                }
            }
            return $allowed;
        } catch (\Throwable $exception) {
            return [];
        }
    }

    private function localLoggingEnabled()
    {
        try {
            $config = Config::getInstance()->object();
            $general = $config->OPNsense->Syslog->general ?? null;
            if ($general === null) {
                return null;
            }
            $enabled = isset($general->enabled) ? (string)$general->enabled : null;
            $local = isset($general->loglocal) ? (string)$general->loglocal : null;
            if ($enabled === '0' || $local === '0') {
                return false;
            }
            if ($enabled === '1' && $local === '1') {
                return true;
            }
            return null;
        } catch (\Throwable $exception) {
            return null;
        }
    }
}
