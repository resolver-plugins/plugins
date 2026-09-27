<?php

namespace OPNsense\Base {
    class IndexController
    {
        public $view;

        public function __construct()
        {
            $this->view = new \FixtureView();
        }

        public function getForm($name)
        {
            return $name;
        }

        public function getUserName()
        {
            return 'fixture-user';
        }
    }
}

namespace OPNsense\Core {
    class ACL
    {
        public static $allowedRoutes = [];
        public static $readOnly = false;

        public function isPageAccessible($username, $route)
        {
            return in_array($route, self::$allowedRoutes, true);
        }

        public function hasPrivilege($username, $privilege)
        {
            return $privilege === 'user-config-readonly' && self::$readOnly;
        }
    }

    class Config
    {
        public static $object;

        public static function getInstance()
        {
            return new self();
        }

        public function object()
        {
            return self::$object;
        }
    }
}

namespace {
    class FixtureView
    {
        public $picked;

        public function pick($template)
        {
            $this->picked = $template;
        }
    }

    class FixtureInterfaces
    {
        public function children()
        {
            return ['wan' => new \stdClass(), 'bad/name' => new \stdClass()];
        }
    }

    function fixtureConfig($enabled, $loglocal)
    {
        $general = new \stdClass();
        if ($enabled !== null) {
            $general->enabled = $enabled;
        }
        if ($loglocal !== null) {
            $general->loglocal = $loglocal;
        }
        return (object)[
            'interfaces' => new FixtureInterfaces(),
            'OPNsense' => (object)[
                'Syslog' => (object)['general' => $general],
            ],
        ];
    }

    function check($condition, $message)
    {
        if (!$condition) {
            throw new \RuntimeException($message);
        }
    }

    require $argv[1];

    use OPNsense\Core\ACL;
    use OPNsense\Core\Config;
    use OPNsense\DhcpInterfaceHa\IndexController;

    $allRoutes = [
        '/api/dhcpinterfaceha/settings/set',
        '/api/interfaces/assignment/reconfigure',
        '/api/core/hasync/set',
        '/api/dhcpinterfaceha/service/prepare',
        '/api/dhcpinterfaceha/service/apply',
        '/api/dhcpinterfaceha/status/generate_mac',
        '/api/interfaces/assignment/set_item/wan',
        '/api/diagnostics/log/dhcpinterfaceha/core',
        '/api/diagnostics/log/dhcpinterfaceha/core/export',
        '/api/diagnostics/log/dhcpinterfaceha/core/live',
    ];
    ACL::$allowedRoutes = $allRoutes;
    Config::$object = fixtureConfig('1', '1');

    $controller = new IndexController();
    $controller->indexAction();
    check($controller->view->settings === 'settings', 'settings form capability was lost');
    check($controller->view->canWriteSettings === true, 'settings write capability was lost');
    check($controller->view->canConfigureInterface === true, 'interface setup capability was lost');
    check($controller->view->canRunRecovery === true, 'recovery capability was lost');
    check($controller->view->canGenerateMac === true, 'MAC generation capability was lost');
    check($controller->view->canViewLogs === true, 'log viewer capability was lost');
    check($controller->view->configureAllowedInterfaces === ['wan'], 'interface ACL filtering changed');

    $controller->logAction();
    check($controller->view->picked === 'OPNsense/DhcpInterfaceHa/log', 'log wrapper was not selected');
    check($controller->view->canViewLogs === true, 'all read-only log routes should allow the viewer');
    check($controller->view->localLoggingEnabled === true, 'enabled local logging was not detected');

    foreach (array_slice($allRoutes, -3) as $deniedRoute) {
        ACL::$allowedRoutes = array_values(array_diff($allRoutes, [$deniedRoute]));
        $controller->logAction();
        check($controller->view->canViewLogs === false, "missing log permission was accepted: $deniedRoute");
    }

    ACL::$allowedRoutes = $allRoutes;
    ACL::$readOnly = true;
    $controller->indexAction();
    check($controller->view->canWriteSettings === false, 'read-only user was allowed to write settings');
    check($controller->view->canViewLogs === true, 'read-only user lost log access');

    ACL::$readOnly = false;
    foreach ([['1', '0'], ['0', '1']] as [$enabled, $loglocal]) {
        Config::$object = fixtureConfig($enabled, $loglocal);
        $controller->logAction();
        check($controller->view->localLoggingEnabled === false, 'disabled local logging was misreported');
    }
    Config::$object = fixtureConfig('1', null);
    $controller->logAction();
    check($controller->view->localLoggingEnabled === null, 'incomplete logging config was treated as enabled');

    echo "Log controller access checks passed\n";
}
