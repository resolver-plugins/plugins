#!/bin/sh

case "$1" in
    install) exit 0 ;;
    query) printf '%s\n' '9.20.24' ;;
    rquery) printf '%s\n' '26.1.11_10' ;;
    fetch)
        [ "$2" = '-y' ] && [ "$3" = '-r' ] && [ "$4" = 'OPNsense' ] && \
            [ "$5" = '-o' ] && [ "$7" = 'os-bind' ] || exit 2
        mkdir -p "$6/All"
        : > "$6/All/os-bind-1.34_3.pkg"
        ;;
    config) printf '%s\n' 'FreeBSD:14:amd64' ;;
    version) printf '%s\n' '=' ;;
    *) exit 2 ;;
esac
