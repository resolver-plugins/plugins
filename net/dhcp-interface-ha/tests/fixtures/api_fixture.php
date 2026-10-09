<?php
namespace {
    class FixtureReadOnly extends \RuntimeException {}

    class FixtureRequest
    {
        public static $method = 'GET';
        public static $username = 'fixture-admin';
        public static $post = [];
        public static $readOnly = false;

        public function isGet() { return self::$method === 'GET'; }
        public function isPost() { return self::$method === 'POST'; }
        public function getQuery($key, $filter = null, $default = null) { return $default; }
        public function getPost($key, $filter = null, $default = null) { return self::$post[$key] ?? $default; }
    }
}

namespace OPNsense\Base {
    class ApiControllerBase
    {
        protected $request;

        public function __construct()
        {
            $this->request = new \FixtureRequest();
        }

        protected function throwReadOnly()
        {
            if (\FixtureRequest::$readOnly) {
                throw new \FixtureReadOnly('read-only');
            }
        }

        public function getUserName() { return \FixtureRequest::$username; }
    }
}
