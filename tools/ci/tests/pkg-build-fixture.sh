#!/bin/sh

case "$1" in
    install) exit 0 ;;
    query) printf '%s\n' '9.20.26' ;;
    config) printf '%s\n' 'FreeBSD:14:amd64' ;;
    version) printf '%s\n' '=' ;;
    *) exit 2 ;;
esac
