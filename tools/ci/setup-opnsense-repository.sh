#!/bin/sh

set -eu

fail() {
    printf '%s\n' "$1" >&2
    exit 1
}

[ "$#" -eq 1 ] || fail "usage: $0 <26.1|26.7>"

series=$1
case "$series" in
    26.1|26.7) ;;
    *) fail "unsupported OPNsense series: $series" ;;
esac

git_command=${GIT_COMMAND:-git}
repository_directory=${PKG_REPOS_DIR:-/usr/local/etc/pkg/repos}
fingerprint_directory=${PKG_FINGERPRINTS_DIR:-/usr/local/etc/pkg/fingerprints/OPNsense}
checkout_directory=$(mktemp -d)
trap 'rm -rf "$checkout_directory"' 0

"$git_command" clone -q --depth 1 --branch "stable/$series" \
    https://github.com/opnsense/core.git "$checkout_directory"

repository_source="$checkout_directory/src/etc/pkg/repos/OPNsense.conf"
fingerprints_source="$checkout_directory/src/etc/pkg/fingerprints/OPNsense"
[ -f "$repository_source" ] || fail 'OPNsense repository configuration is missing'
[ -d "$fingerprints_source/trusted" ] || fail 'OPNsense trusted fingerprints are missing'

expected_url='https://pkg.opnsense.org/${ABI}/'"$series"'/latest'
grep -Fq "$expected_url" "$repository_source" || \
    fail "OPNsense repository configuration does not target $series"
grep -Fq 'signature_type: "fingerprints"' "$repository_source" || \
    fail 'OPNsense repository configuration does not require fingerprints'

mkdir -p "$repository_directory" "$fingerprint_directory"
install -m 0644 "$repository_source" "$repository_directory/OPNsense.conf"

for group in trusted revoked
do
    source_directory="$fingerprints_source/$group"
    [ -d "$source_directory" ] || continue
    destination_directory="$fingerprint_directory/$group"
    mkdir -p "$destination_directory"
    for fingerprint in "$source_directory"/*
    do
        [ -f "$fingerprint" ] || continue
        install -m 0644 "$fingerprint" "$destination_directory/$(basename "$fingerprint")"
    done
done

set -- "$fingerprint_directory/trusted"/*
[ -f "$1" ] || fail 'no trusted OPNsense fingerprint was installed'

"$git_command" -C "$checkout_directory" rev-parse HEAD
