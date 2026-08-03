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

repository_directory=${PKG_REPOS_DIR:-/usr/local/etc/pkg/repos}
fingerprint_directory=${PKG_FINGERPRINTS_DIR:-/usr/local/etc/pkg/fingerprints/OPNsense}
fetch_command=${FETCH_COMMAND:-fetch}
archive_url=${OPNSENSE_CORE_ARCHIVE_URL:-https://github.com/opnsense/core/archive/refs/heads/stable/$series.tar.gz}
temporary_directory=$(mktemp -d)
archive_path="$temporary_directory/core.tar.gz"
checkout_directory="$temporary_directory/core"
trap 'rm -rf "$temporary_directory"' EXIT HUP INT TERM

"$fetch_command" -q -o "$archive_path" "$archive_url"
mkdir -p "$checkout_directory"
tar -xzf "$archive_path" -C "$checkout_directory"
set -- "$checkout_directory"/*
[ "$#" -eq 1 ] || fail 'OPNsense core archive has an unexpected layout'
core_directory=$1

repository_template="$core_directory/src/etc/pkg/repos/OPNsense.conf.shadow.in"
fingerprints_source="$core_directory/src/etc/pkg/fingerprints/OPNsense"
[ -f "$repository_template" ] || fail 'OPNsense repository template is missing'
[ -d "$fingerprints_source/trusted" ] || fail 'OPNsense trusted fingerprints are missing'

repository_source="$temporary_directory/OPNsense.conf"
sed \
    -e 's|%%CORE_PACKAGESITE%%|https://pkg.opnsense.org|g' \
    -e "s|%%CORE_ABI%%|$series|g" \
    "$repository_template" > "$repository_source"
grep -Fq "https://pkg.opnsense.org/\${ABI}/$series/latest" "$repository_source" || \
    fail "OPNsense repository configuration does not target $series"
grep -Fq 'signature_type: "fingerprints"' "$repository_source" || \
    fail 'OPNsense repository configuration does not require fingerprints'

mkdir -p "$repository_directory" "$fingerprint_directory"
install -m 0644 "$repository_source" "$repository_directory/OPNsense.conf"
printf '%s\n' 'FreeBSD: {' '  enabled: no' '}' > "$repository_directory/FreeBSD.conf"

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

if command -v sha256 >/dev/null 2>&1
then
    sha256 -q "$archive_path"
elif command -v sha256sum >/dev/null 2>&1
then
    sha256sum "$archive_path" | awk '{print $1}'
else
    fail 'no SHA-256 command is available'
fi
