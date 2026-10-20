#!/bin/sh
# Configure the shared signed repository without installing any packages.
set -eu

readonly public_key_sha256=bd89d6f91807c71f8a744532c9ce2f97e9590f8858ac779bfb2f23c10804e07e
readonly repository_base_url=https://resolver-plugins.github.io/repository/pkg
repository_directory=${RP_PKG_REPOSITORY_DIR:-/usr/local/etc/pkg/repos}
key_directory=${RP_PKG_KEYS_DIR:-/usr/local/etc/pkg/keys}
series=$(opnsense-version -a)
abi=$(pkg config ABI)
public_key="$key_directory/resolver-plugins.pub"

temporary_directory=$(mktemp -d)
trap 'rm -rf "$temporary_directory"' EXIT
trap 'exit 1' HUP INT TERM
fetch -o "$temporary_directory/resolver-plugins.pub" \
    "$repository_base_url/$abi/$series/latest/resolver-plugins.pub"
if [ "$(sha256 -q "$temporary_directory/resolver-plugins.pub")" != "$public_key_sha256" ]
then
    printf '%s\n' 'resolver-plugins public-key fingerprint verification failed' >&2
    exit 1
fi

cat > "$temporary_directory/resolver-plugins.conf" <<EOF
resolver-plugins: {
  url: "$repository_base_url/\${ABI}/$series/latest",
  mirror_type: "none",
  signature_type: "pubkey",
  pubkey: "$public_key",
  enabled: yes
}
EOF
install -d -m 0755 "$key_directory" "$repository_directory"
install -m 0644 "$temporary_directory/resolver-plugins.pub" "$public_key"
install -m 0644 "$temporary_directory/resolver-plugins.conf" "$repository_directory/resolver-plugins.conf"
pkg -o "REPOS_DIR=$repository_directory" update -r resolver-plugins
