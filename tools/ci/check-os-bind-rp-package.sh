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

printf '%s\n' "$manifest" | grep -Fqx 'name: os-bind-rp' || \
    fail 'package manifest name is not os-bind-rp'
printf '%s\n' "$manifest" | grep -Fqx 'conflicts: [ "os-bind" ]' || \
    fail 'package manifest does not conflict with os-bind'

bind_version=$(printf '%s\n' "$manifest" | \
    sed -n 's/^[[:space:]]*bind920: { version: "\([^"]*\)".*$/\1/p')
[ -n "$bind_version" ] || fail 'package manifest does not declare a bind920 dependency'

pkg_command=${PKG_COMMAND:-pkg}
comparison=$("$pkg_command" version -t "$bind_version" 9.20.26) || \
    fail "cannot compare bind920 version: $bind_version"
case "$comparison" in
    '='|'>') ;;
    *) fail "bind920 $bind_version is below the required 9.20.26" ;;
esac
