#!/bin/sh

set -eu

[ "$1" = "-C" ] && [ "$3" = "package" ] || exit 2
case "$2" in
    */dns/bind) ;;
    *) exit 2 ;;
esac

fixture_directory=$(mktemp -d)
trap 'rm -rf "$fixture_directory"' EXIT
mkdir -p dns/bind/work/pkg
printf '%s\n' \
    'name: os-bind-rp' \
    'version: "1.36_1"' \
    'conflicts: [ "os-bind" ]' \
    'deps: {' \
    '  bind920: { version: "9.20.26", origin: "dns/bind920" }' \
    '}' > "$fixture_directory/+MANIFEST"
tar -C "$fixture_directory" -cf dns/bind/work/pkg/os-bind-rp-1.36_1.pkg +MANIFEST
