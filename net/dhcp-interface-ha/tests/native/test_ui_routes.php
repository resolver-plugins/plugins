<?php

/* Run on OPNsense: php test_ui_routes.php [/path/to/plugin/src]
 * Resolve the actual menu/tab links with the native router. No dispatch,
 * authentication changes, configuration writes or interface operations.
 */
require_once '/usr/local/opnsense/mvc/script/load_phalcon.php';

$root = $argv[1] ?? '/usr/local';
$app = $root . '/opnsense/mvc/app';
$menu = simplexml_load_file($app . '/models/OPNsense/DhcpInterfaceHa/Menu/Menu.xml');
$urls = [];
foreach ($menu->xpath('//@url') as $url) {
    $urls[] = (string)$url;
}
foreach (['index', 'log'] as $view) {
    $content = file_get_contents($app . '/views/OPNsense/DhcpInterfaceHa/' . $view . '.volt');
    preg_match_all('~href="(/ui/dhcpinterfaceha[^" ]*)"~', $content, $matches);
    $urls = array_merge($urls, $matches[1]);
}

$router = new \OPNsense\Mvc\Router('/ui/');
$parse = new ReflectionMethod($router, 'parsePath');
$resolve = new ReflectionMethod($router, 'resolveNamespace');
$checked = [];
foreach (array_unique($urls) as $url) {
    $path = parse_url($url, PHP_URL_PATH);
    $parts = $parse->invoke($router, substr($path, 4), [
        'controller' => 'IndexController', 'action' => 'indexAction',
    ]);
    $namespace = $resolve->invoke($router, $parts['namespace'], $parts['controller']);
    if ($namespace === null || !method_exists($namespace . '\\' . $parts['controller'], $parts['action'])) {
        throw new RuntimeException('Page not found for plugin link: ' . $url);
    }
    $checked[$path] = true;
}
if (count($checked) < 2) {
    throw new RuntimeException('Settings and Log routes must both be exercised.');
}
echo 'Native menu and tab routes resolve: ' . implode(', ', array_keys($checked)) . "\n";
