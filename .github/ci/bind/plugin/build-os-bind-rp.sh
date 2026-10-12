#!/bin/sh

set -eu

fail() {
    printf '%s\n' "$1" >&2
    exit 1
}

[ "$#" -eq 2 ] || fail "usage: $0 <26.1|26.7> <artifact-directory>"
series=$1
artifact_directory=$2
: "${RP_TRUSTED_DIRECTORY:?RP_TRUSTED_DIRECTORY is required}"
: "${RP_RUNTIME_DIRECTORY:?RP_RUNTIME_DIRECTORY is required}"
: "${RP_SELECTION_SHA256:?RP_SELECTION_SHA256 is required}"
: "${SOURCE_COMMIT:?SOURCE_COMMIT is required}"

script_directory=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
repository_root=$(CDPATH='' cd -- "$script_directory/../../../.." && pwd)
pkg_command=${PKG_COMMAND:-pkg}
make_command=${MAKE_COMMAND:-make}
python_command=${PYTHON_COMMAND:-python3}
pkg_static=${RP_PKG_STATIC_COMMAND:-/usr/local/sbin/pkg-static}
trusted=$RP_TRUSTED_DIRECTORY
RP_UPSTREAM_METADATA=$trusted/upstream.json
RP_TARGET_PKG_METADATA=$trusted/creator-target.json
RP_READER_PKG_METADATA=$trusted/reader-target.json
export RP_UPSTREAM_METADATA RP_TARGET_PKG_METADATA RP_READER_PKG_METADATA

if ! command -v "$python_command" >/dev/null 2>&1
then
    "$pkg_command" install -y python3
fi
command -v "$python_command" >/dev/null 2>&1 || fail 'python3 is not available after package setup'

"$python_command" -I "$script_directory/../shared/components.py" validate \
    --component bind_plugin --series "$series" --trusted-directory "$trusted" \
    --selection-sha256 "$RP_SELECTION_SHA256" >/dev/null
selection_field() {
    "$python_command" -c 'import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' \
        "$trusted/selection.json" "$1"
}
[ "$(selection_field producer_action)" = build ] || fail 'plugin wrapper requires sealed build action'
[ "$(selection_field source_commit)" = "$SOURCE_COMMIT" ] || fail 'source differs from sealed selection'
mode=$(selection_field mode)
if [ "$mode" = development ]
then
    [ "${RP_PLUGIN_DEVEL:-}" = yes ] || fail 'development plugin requires explicit preview suffix'
else
    [ -z "${RP_PLUGIN_DEVEL:-}" ] || fail 'production plugin cannot use preview suffix'
fi

"$pkg_command" update -f
"$pkg_command" install -y git
git config --global --add safe.directory "$repository_root"
"$python_command" -I "$script_directory/plugin_release.py" materialize \
    --series "$series" --trusted-directory "$trusted" --selection-sha256 "$RP_SELECTION_SHA256" \
    --repository-root "$repository_root" --source-commit "$SOURCE_COMMIT"

metadata_field() {
    "$python_command" -I "$script_directory/../../shared/metadata_profile.py" \
        "$RP_UPSTREAM_METADATA" "$series" "$1"
}
upstream_commit=$(metadata_field upstream_commit)
core_commit=$(metadata_field core_commit)
tools_tag=$(metadata_field tools_tag)
freebsd_release=$(metadata_field freebsd_release)

opnsense_core_commit=$(RP_TARGET_PKG_METADATA="$RP_READER_PKG_METADATA" \
    "$script_directory/../../shared/setup-opnsense-repository.sh" "$series")
"$python_command" -I "$script_directory/../../shared/target_pkg.py" install \
    "$RP_READER_PKG_METADATA" "$series" --upstream "$RP_UPSTREAM_METADATA" --role reader \
    --pkg-command "$pkg_command" --pkg-static "$pkg_static" >/dev/null
"$python_command" -I "$script_directory/../shared/components.py" freeze-runtime \
    --component bind_plugin --series "$series" --trusted-directory "$trusted" \
    --selection-sha256 "$RP_SELECTION_SHA256" --directory "$RP_RUNTIME_DIRECTORY" \
    --pkg-command "$pkg_static" >/dev/null

dependencies=$(
    for archive in "$RP_RUNTIME_DIRECTORY"/bind-tools-*.pkg "$RP_RUNTIME_DIRECTORY"/bind920-*.pkg
    do
        "$pkg_static" query -F "$archive" '%dn'
    done | awk '
        $0 !~ /^[A-Za-z0-9][A-Za-z0-9+_.-]*$/ { exit 1 }
        $0 != "bind-tools" && $0 != "bind920" && !seen[$0]++'
)
if [ -n "$dependencies" ]
then
    printf '%s\n' "$dependencies" | while IFS= read -r dependency
    do
        "$pkg_static" install -y -r OPNsense "$dependency"
    done
fi
"$pkg_static" add -f "$RP_RUNTIME_DIRECTORY"/bind-tools-*.pkg "$RP_RUNTIME_DIRECTORY"/bind920-*.pkg

installed_runtime() {
    "$python_command" -I "$script_directory/plugin_release.py" installed-runtime \
        --series "$series" --trusted-directory "$trusted" --selection-sha256 "$RP_SELECTION_SHA256" \
        --directory "$RP_RUNTIME_DIRECTORY" --pkg-command "$pkg_static"
}
bind_version=$(installed_runtime)

# The creator may differ from the runtime's original creator or minimum reader.
"$script_directory/../../shared/setup-opnsense-repository.sh" "$series" >/dev/null
"$python_command" -I "$script_directory/../../shared/target_pkg.py" install \
    "$RP_TARGET_PKG_METADATA" "$series" --upstream "$RP_UPSTREAM_METADATA" --role creator \
    --pkg-command "$pkg_static" --pkg-static "$pkg_static" >/dev/null
[ "$(installed_runtime)" = "$bind_version" ] || fail 'creator setup replaced the frozen runtime'
creator_field() {
    "$python_command" -I "$script_directory/../../shared/target_pkg.py" field \
        "$RP_TARGET_PKG_METADATA" "$series" "$1" --upstream "$RP_UPSTREAM_METADATA" --role creator
}
pkg_creator=$(creator_field version)
creator_release=$(creator_field release)
creator_ports_commit=$(creator_field ports_commit)
creator_recipe_path=$(creator_field recipe_path)
opnsense_version=$("$pkg_static" rquery -r OPNsense -e '%n = opnsense' '%v')
case "$("$pkg_static" version -t "$opnsense_version" 26.1.11_10)" in
    '='|'>') ;;
    *) fail "OPNsense $opnsense_version is below the required 26.1.11_10" ;;
esac

rm -rf "$repository_root/dns/bind/work"
if [ "$mode" = development ]
then
    "$make_command" -C "$repository_root/dns/bind" _PLUGIN_DEVEL=yes "PLUGIN_HASH=$SOURCE_COMMIT" package
else
    "$make_command" -C "$repository_root/dns/bind" _PLUGIN_DEVEL= "PLUGIN_HASH=$SOURCE_COMMIT" package
fi
"$python_command" -I "$script_directory/../../shared/target_pkg.py" verify \
    "$RP_TARGET_PKG_METADATA" "$series" --upstream "$RP_UPSTREAM_METADATA" --role creator \
    --pkg-command "$pkg_static" --pkg-static "$pkg_static"
[ "$(installed_runtime)" = "$bind_version" ] || fail 'plugin make replaced the frozen runtime'

set -- "$repository_root"/dns/bind/work/pkg/os-bind-rp-*.pkg
[ "$#" -eq 1 ] && [ -f "$1" ] || fail 'package build must produce exactly one plugin archive'
package=$1
plugin_version=$("$pkg_static" query -F "$package" '%v')
case "$plugin_version" in
    "$series"_[1-9]*)
        plugin_revision=${plugin_version#"$series"_}
        case "$plugin_revision" in *[!0-9]*) fail "invalid plugin revision: $plugin_version" ;; esac
        ;;
    *) fail "plugin version $plugin_version does not match release series $series" ;;
esac

[ ! -e "$artifact_directory" ] || fail 'plugin producer output already exists'
mkdir -p "$(dirname -- "$artifact_directory")"
temporary_output=$(mktemp -d "$artifact_directory.tmp.XXXXXXXX")
trap 'rm -rf "$temporary_output"' EXIT HUP INT TERM
cp "$package" "$temporary_output/"
cp "$trusted/producer-inputs.json" "$temporary_output/plugin-build-inputs.json"
{
    printf 'series=%s\n' "$series"
    printf 'uname=%s\n' "$(uname -a)"
    printf 'pkg_abi=%s\n' "$("$pkg_static" config ABI)"
    printf 'bind920=%s\n' "$bind_version"
    printf 'bind_source=resolver\n'
    printf 'opnsense=%s\n' "$opnsense_version"
    printf 'opnsense_core_commit=%s\n' "$opnsense_core_commit"
    printf 'upstream_commit=%s\n' "$upstream_commit"
    printf 'core_commit=%s\n' "$core_commit"
    printf 'tools_tag=%s\n' "$tools_tag"
    printf 'freebsd_release=%s\n' "$freebsd_release"
    printf 'source_commit=%s\n' "$SOURCE_COMMIT"
    printf 'pkg_creator=%s\n' "$pkg_creator"
    printf 'creator_release=%s\n' "$creator_release"
    printf 'creator_ports_commit=%s\n' "$creator_ports_commit"
    printf 'creator_recipe_path=%s\n' "$creator_recipe_path"
} > "$temporary_output/build-metadata.txt"
"$python_command" -I "$script_directory/plugin_release.py" validate-output \
    --series "$series" --trusted-directory "$trusted" --selection-sha256 "$RP_SELECTION_SHA256" \
    --directory "$temporary_output" --pkg-command "$pkg_static"
mv "$temporary_output" "$artifact_directory"
