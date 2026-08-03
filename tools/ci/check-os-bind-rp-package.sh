#!/bin/sh

set -eu

fail() {
    printf '%s\n' "$1" >&2
    exit 1
}

[ "$#" -eq 1 ] || fail "usage: $0 <os-bind-rp-package>"

package=$1
[ -f "$package" ] || fail "package does not exist: $package"

manifest=$(tar -xOf "$package" +MANIFEST) || fail "cannot read +MANIFEST from: $package"

if printf '%s\n' "$manifest" | grep -Eq '^[[:space:]]*"conflicts"[[:space:]]*:|^[[:space:]]*conflicts:'
then
    fail 'package manifest must not declare an os-bind conflict'
fi

if printf '%s\n' "$manifest" | grep -Eq '^[[:space:]]*\{'
then
    package_name=$(printf '%s\n' "$manifest" | \
        sed -n 's/.*"name":"\([^"]*\)".*/\1/p')
    bind_version=$(printf '%s\n' "$manifest" | \
        sed -n 's/.*"bind920":{[^}]*"version":"\([^"]*\)".*/\1/p')
    opnsense_version=$(printf '%s\n' "$manifest" | \
        sed -n 's/.*"opnsense":{[^}]*"version":"\([^"]*\)".*/\1/p')
else
    package_name=$(printf '%s\n' "$manifest" | \
        sed -n 's/^[[:space:]]*name: \([^[:space:]]*\)$/\1/p')
    bind_version=$(printf '%s\n' "$manifest" | \
        sed -n 's/^[[:space:]]*bind920: { version: "\([^"]*\)".*$/\1/p')
    opnsense_version=$(printf '%s\n' "$manifest" | \
        sed -n 's/^[[:space:]]*opnsense: { version: "\([^"]*\)".*$/\1/p')
fi

[ "$package_name" = 'os-bind-rp' ] || fail 'package manifest name is not os-bind-rp'
[ -n "$bind_version" ] || fail 'package manifest does not declare a bind920 dependency'
[ -n "$opnsense_version" ] || \
    fail 'package manifest does not declare an opnsense dependency'

pkg_command=${PKG_COMMAND:-pkg}
comparison=$("$pkg_command" version -t "$opnsense_version" 26.1.11_10) || \
    fail "cannot compare OPNsense version: $opnsense_version"
case "$comparison" in
    '='|'>') ;;
    *) fail "OPNsense $opnsense_version is below the required 26.1.11_10" ;;
esac
