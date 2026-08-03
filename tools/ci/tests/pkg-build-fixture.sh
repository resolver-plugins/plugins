#!/bin/sh

if [ -n "${PKG_CALL_LOG:-}" ]
then
    printf '%s\n' "$*" >> "$PKG_CALL_LOG"
fi

case "$1" in
    update) exit 0 ;;
    install) exit 0 ;;
    query) printf '%s\n' '9.20.24' ;;
    rquery) printf '%s\n' '26.1.11_10' ;;
    config) printf '%s\n' 'FreeBSD:14:amd64' ;;
    version) printf '%s\n' '=' ;;
    *) exit 2 ;;
esac
