#!/bin/sh
# Run only in the disposable FreeBSD build VM, never on a firewall.
set -eu

[ "$#" -eq 2 ] || { echo "usage: $0 <series> <artifact-directory>" >&2; exit 1; }
series=$1
output=$2
: "${RP_UPSTREAM_METADATA:?immutable upstream metadata is required}"
: "${SOURCE_COMMIT:?plugin source commit is required}"
: "${PROFILE_COMMIT:?compatibility profile commit is required}"
root=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)
ci="$root/.github/ci"
plugin="$root/net/dhcp-interface-ha"
pkg_static=${RP_PKG_STATIC_COMMAND:-/usr/local/sbin/pkg-static}
pkg_command=${PKG_COMMAND:-pkg}
make_command=${MAKE_COMMAND:-make}
target_metadata=${RP_TARGET_PKG_METADATA:-$root/.resolver-plugins/target-pkg.json}

freebsd_release=$(python3 "$ci/metadata_profile.py" "$RP_UPSTREAM_METADATA" "$series" freebsd_release)
RP_OPNSENSE_SNAPSHOT=$(python3 "$ci/dhcp_interface_ha_release.py" snapshot --series "$series")
export RP_OPNSENSE_SNAPSHOT
core_commit=$("$ci/setup-opnsense-repository.sh" "$series")
"$pkg_command" update -f
python3 "$ci/target_pkg.py" install "$target_metadata" "$series" --pkg-command "$pkg_command" --pkg-static "$pkg_static"
# Install the target framework for the package installation and native route check.
"$pkg_command" install -y -r OPNsense opnsense
python3 "$ci/target_pkg.py" verify "$target_metadata" "$series" --pkg-command "$pkg_command" --pkg-static "$pkg_static"

# Keep the package name unsuffixed, regardless of the checkout's Mk marker.
# Do not use make clean: the upstream target resets tracked plugin source.
rm -rf "$plugin/work"
"$make_command" -C "$plugin" PLUGIN_DEVEL= PLUGIN_ABI="$series" PLUGIN_HASH="$SOURCE_COMMIT" package
python3 "$ci/target_pkg.py" verify "$target_metadata" "$series" --pkg-command "$pkg_command" --pkg-static "$pkg_static"
set -- "$plugin"/work/pkg/os-dhcp-interface-ha-*.pkg
if [ "$#" -ne 1 ] || [ ! -f "$1" ]; then
    echo 'Expected exactly one plugin archive' >&2
    exit 1
fi
package=$1
[ "$("$pkg_static" query -F "$package" '%n|%o')" = 'os-dhcp-interface-ha|opnsense/os-dhcp-interface-ha' ]
python3 "$ci/package_checksums.py" --pkg-command "$pkg_static" "$package"

# Verify installation in the disposable VM, with no configured HA interface.
install -d -m 0750 /conf
if [ ! -e /conf/config.xml ]; then
    printf '%s\n' '<opnsense/>' > /conf/config.xml
    chmod 0640 /conf/config.xml
fi
"$pkg_static" add "$package"
"$pkg_static" check -s os-dhcp-interface-ha
/usr/local/bin/php "$plugin/tests/native/test_ui_routes.php"
python3 "$plugin/tests/native/test_fresh_install.py"
python3 "$ci/target_pkg.py" verify "$target_metadata" "$series" --pkg-command "$pkg_command" --pkg-static "$pkg_static"

mkdir -p "$output"
cp "$package" "$output/"
cp "$RP_UPSTREAM_METADATA" "$output/upstream.json"
{
    printf 'series=%s\n' "$series"
    printf 'source_commit=%s\n' "$SOURCE_COMMIT"
    printf 'profile_commit=%s\n' "$PROFILE_COMMIT"
    printf 'repository_snapshot=%s\n' "$RP_OPNSENSE_SNAPSHOT"
    printf 'freebsd_release=%s\n' "$freebsd_release"
    printf 'core_commit=%s\n' "$core_commit"
    printf 'uname=%s\n' "$(uname -a)"
    printf 'pkg_abi=%s\n' "$("$pkg_command" config ABI)"
    printf 'opnsense=%s\n' "$("$pkg_command" query -e '%n = opnsense' '%v')"
    printf 'plugin_version=%s\n' "$("$pkg_static" query -F "$package" '%v')"
    printf 'pkg_creator=%s\n' "$(python3 "$ci/target_pkg.py" field "$target_metadata" "$series" version)"
    printf 'pkg_creator_sha256=%s\n' "$(python3 "$ci/target_pkg.py" field "$target_metadata" "$series" sha256)"
} > "$output/build-metadata.txt"
(cd "$output" && sha256 -r ./*.pkg build-metadata.txt upstream.json > SHA256SUMS)
