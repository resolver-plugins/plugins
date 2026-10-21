#!/usr/local/bin/php
<?php
// Recalculate current native IPv4 routes without filter reload or callbacks
// into the controller that requested cleanup under its transition lock.
require_once 'config.inc';
require_once 'util.inc';
require_once 'interfaces.inc';
require_once 'system.inc';
system_routing_configure(false, null, false, 'inet');
