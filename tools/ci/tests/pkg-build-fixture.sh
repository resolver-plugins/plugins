#!/bin/sh

case "$1" in
    install) exit 0 ;;
    query) printf '%s\n' '9.20.24' ;;
    rquery) printf '%s\n' '26.1.11_10' ;;
    config) printf '%s\n' 'FreeBSD:14:amd64' ;;
    version) printf '%s\n' '=' ;;
    *) exit 2 ;;
esac
