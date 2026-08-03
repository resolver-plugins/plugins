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
    fetch)
        [ "$2" = '-y' ] && [ "$3" = '-r' ] && [ "$4" = 'OPNsense' ] && \
            [ "$5" = '-o' ] && [ "$7" = 'os-bind' ] && [ "$8" = 'bind920' ] && \
            [ "$9" = 'opnsense' ] || exit 2
        mkdir -p "$6/All"
        fixture_directory=$(mktemp -d)
        trap 'rm -rf "$fixture_directory"' EXIT
        printf '%s\n' \
            '{"name":"os-bind","version":"1.34_3","origin":"opnsense/os-bind"}' \
            > "$fixture_directory/+MANIFEST"
        tar -C "$fixture_directory" -cf "$6/All/os-bind-1.34_3.pkg" +MANIFEST
        printf '%s\n' \
            '{"name":"bind920","version":"9.20.24","origin":"dns/bind920"}' \
            > "$fixture_directory/+MANIFEST"
        tar -C "$fixture_directory" -cf "$6/All/bind920-9.20.24.pkg" +MANIFEST
        printf '%s\n' \
            '{"name":"opnsense","version":"26.1.11_10","origin":"opnsense/opnsense"}' \
            > "$fixture_directory/+MANIFEST"
        tar -C "$fixture_directory" -cf "$6/All/opnsense-26.1.11_10.pkg" +MANIFEST
        ;;
    config) printf '%s\n' 'FreeBSD:14:amd64' ;;
    version) printf '%s\n' '=' ;;
    *) exit 2 ;;
esac
