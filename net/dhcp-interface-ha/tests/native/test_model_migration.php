<?php

/* Run on OPNsense: php test_model_migration.php /path/to/models/OPNsense/DhcpInterfaceHa
 * Uses the real MVC models against synthetic in-memory configuration only.
 * Never calls Config::save(), configd or interface commands.
 */
require_once '/usr/local/opnsense/mvc/script/load_phalcon.php';
require $argv[1] . '/Local.php';
require $argv[1] . '/Shared.php';

use OPNsense\Core\Config;
use OPNsense\DhcpInterfaceHa\Local;
use OPNsense\DhcpInterfaceHa\Shared;

function check($condition, $message)
{
    if (!$condition) {
        throw new RuntimeException($message);
    }
}

$config = Config::getInstance();
$cases = [
    'legacy' => ['opt7', null, 'opt7'],
    'unavailable legacy selection' => ['opt99', null, 'opt99'],
    'existing local selection' => ['opt7', 'opt2', 'opt2'],
    'explicitly empty local selection' => ['opt7', '', ''],
    'new installation' => [null, null, ''],
];
foreach ([['Shared', 'Local'], ['Local', 'Shared']] as $order) {
    foreach ($cases as $case => [$legacy, $local, $expected]) {
        $shared = ['@attributes' => ['version' => '1.0.0'], 'enabled' => '0', 'failback_delay' => '0'];
        $localData = ['@attributes' => ['version' => '1.0.0'], 'carrier' => 'hn1'];
        if ($legacy !== null) {
            $shared['managed_interface'] = $legacy;
        }
        if ($local !== null) {
            $localData['managed_interface'] = $local;
        }
        $config->fromArray(['OPNsense' => $case === 'new installation' ? [] : [
            'DhcpInterfaceHaShared' => $shared, 'DhcpInterfaceHaLocal' => $localData,
        ]]);
        foreach ($order as $name) {
            $class = 'OPNsense\\DhcpInterfaceHa\\' . $name;
            $model = new $class(true);
            $model->runMigrations();
            check($model->getVersion() === '1.1.0', "$case: $name migration failed");
        }
        $root = $config->object()->OPNsense;
        check((string)$root->DhcpInterfaceHaLocal->managed_interface === $expected, "$case: selection lost");
        check((string)$root->DhcpInterfaceHaLocal->carrier === ($case === 'new installation' ? '' : 'hn1'), "$case: carrier changed");
        check(!isset($root->DhcpInterfaceHaShared->managed_interface), "$case: selection still shared");
        // Simulate another XMLRPC delivery from an old sender after migration.
        $root->DhcpInterfaceHaShared->managed_interface = 'opt42';
        $root->DhcpInterfaceHaShared['version'] = '1.0.0';
        check((new Shared(true))->runMigrations(), "$case: legacy shared sync migration failed");
        check((string)(new Local(true))->managed_interface === $expected, "$case: peer retargeted local interface");
        check(!(new Local(true))->runMigrations(), "$case: local migration repeated");
        echo implode(' then ', $order) . ": $case passed\n";
    }
}
