#!/bin/sh
set -eu
printf '%s\n' "$*" >> "$PKG_STATIC_CALL_LOG"
case "$*" in
    -v) printf '%s\n' '2.3.1';;
    'query -F '*" %v")
        package=${3##*/}
        version=${package#os-bind-rp-}
        printf '%s\n' "${version%.pkg}"
        ;;
    'query -F '*" %Fp|%Fs")
        # The pkg checksum format contains a literal dollar sign.
        # shellcheck disable=SC2016
        printf '%s\n' '/usr/local/opnsense/mvc/app/models/OPNsense/Bind/Menu/Menu.xml|1$aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        ;;
    *) exit 64;;
esac
