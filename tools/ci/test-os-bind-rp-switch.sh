#!/bin/sh

set -eu

fail() {
    printf '%s\n' "$1" >&2
    exit 1
}

[ "$#" -eq 2 ] || fail "usage: $0 <os-bind-package> <os-bind-rp-package>"

official_package=$1
rp_package=$2
[ -f "$official_package" ] || fail "official package does not exist: $official_package"
[ -f "$rp_package" ] || fail "os-bind-rp package does not exist: $rp_package"

pkg_command=${PKG_COMMAND:-pkg-static}
switch_root=$(mktemp -d /tmp/os-bind-rp-switch.XXXXXX)
collision_log=$(mktemp /tmp/os-bind-rp-collision.XXXXXX)
trap 'rm -rf "$switch_root" "$collision_log"' EXIT HUP INT TERM

"$pkg_command" -r "$switch_root" add -I -M "$official_package" >/dev/null

if "$pkg_command" -r "$switch_root" add -I -M "$rp_package" >"$collision_log" 2>&1
then
    fail 'os-bind-rp unexpectedly co-installed with os-bind'
fi
grep -F 'conflicts with os-bind' "$collision_log" >/dev/null || \
    fail 'os-bind-rp installation did not fail on the shared os-bind payload'
"$pkg_command" -r "$switch_root" info -q | grep -E '^os-bind-[0-9]' >/dev/null || \
    fail 'official os-bind was not retained after the rejected installation'

"$pkg_command" -r "$switch_root" delete -y -f os-bind >/dev/null
"$pkg_command" -r "$switch_root" add -I -M "$rp_package" >/dev/null
"$pkg_command" -r "$switch_root" info -q | grep -E '^os-bind-rp-' >/dev/null || \
    fail 'os-bind-rp was not installed after the manual switch'
if "$pkg_command" -r "$switch_root" info -q | grep -E '^os-bind-[0-9]' >/dev/null
then
    fail 'official os-bind remained installed after the manual switch'
fi

printf '%s\n' 'package switch verified'
