#!/bin/sh

set -eu

fail() {
    printf '%s\n' "$1" >&2
    exit 1
}

[ "$#" -eq 2 ] || fail "usage: $0 <26.1|26.7> <artifact-directory>"

series=$1
artifact_directory=$2
case "$series" in
    26.1|26.7) ;;
    *) fail "unsupported OPNsense series: $series" ;;
esac

script_directory=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repository_root=$(CDPATH= cd -- "$script_directory/../.." && pwd)
pkg_command=${PKG_COMMAND:-pkg}
make_command=${MAKE_COMMAND:-make}

opnsense_core_commit=$("$script_directory/setup-opnsense-repository.sh" "$series")
"$pkg_command" install -y bind920
bind_version=$("$pkg_command" query -e '%n = bind920' '%v') || \
    fail 'bind920 is not installed after package setup'
comparison=$("$pkg_command" version -t "$bind_version" 9.20.26) || \
    fail "cannot compare bind920 version: $bind_version"
case "$comparison" in
    '='|'>') ;;
    *) fail "bind920 $bind_version is below the required 9.20.26" ;;
esac

"$make_command" -C "$repository_root/dns/bind" package

set -- "$repository_root"/dns/bind/work/pkg/os-bind-rp-*.pkg
[ -f "$1" ] || fail 'package build did not produce os-bind-rp'
[ "$#" -eq 1 ] || fail 'package build produced more than one os-bind-rp package'
package=$1

"$script_directory/check-os-bind-rp-package.sh" "$package"

mkdir -p "$artifact_directory"
cp "$package" "$artifact_directory/"
{
    printf 'series=%s\n' "$series"
    printf 'uname=%s\n' "$(uname -a)"
    printf 'pkg_abi=%s\n' "$("$pkg_command" config ABI)"
    printf 'bind920=%s\n' "$bind_version"
    printf 'opnsense_core_commit=%s\n' "$opnsense_core_commit"
    printf 'source_commit=%s\n' "$(git -C "$repository_root" rev-parse HEAD)"
} > "$artifact_directory/build-metadata.txt"
