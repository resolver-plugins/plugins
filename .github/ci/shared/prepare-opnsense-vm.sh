#!/bin/sh
# Initialize a package-installed core in a disposable FreeBSD build/test VM.
set -eu

install -d -m 0750 /conf
if [ ! -e /conf/config.xml ]
then
    test -r /usr/local/etc/config.xml || {
        printf '%s\n' 'installed core factory configuration is missing' >&2
        exit 1
    }
    install -m 0640 /usr/local/etc/config.xml /conf/config.xml
fi
sh /usr/local/etc/rc.subr.d/php
service configd onestatus || service configd onestart
/usr/local/opnsense/service/template_ctl.py OPNsense/WebGui
/usr/local/bin/php -r '
if (stream_resolve_include_path("config.inc") !== "/usr/local/etc/inc/config.inc") {
    fwrite(STDERR, "native PHP cannot resolve config.inc\n");
    exit(1);
}
require_once "config.inc";
'
