#!/bin/sh

set -eu

fail() {
    printf '%s\n' "$1" >&2
    exit 1
}

[ "$#" -eq 2 ] || fail "usage: $0 <26.1|26.7> <artifact-directory>"

series=$1
artifact_directory=$2

script_directory=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
pkg_command=${PKG_COMMAND:-pkg}
make_command=${MAKE_COMMAND:-make}
git_command=${GIT_COMMAND:-git}
python_command=${PYTHON_COMMAND:-python3}
pkg_static=${RP_PKG_STATIC_COMMAND:-/usr/local/sbin/pkg-static}
: "${RP_TRUSTED_DIRECTORY:?RP_TRUSTED_DIRECTORY is required}"
: "${RP_SELECTION_SHA256:?RP_SELECTION_SHA256 is required}"
trusted_directory=$(CDPATH='' cd -- "$RP_TRUSTED_DIRECTORY" && pwd)
RP_UPSTREAM_METADATA=$trusted_directory/upstream.json
RP_TARGET_PKG_METADATA=$trusted_directory/creator-target.json
RP_READER_PKG_METADATA=$trusted_directory/reader-target.json
export RP_UPSTREAM_METADATA RP_TARGET_PKG_METADATA RP_READER_PKG_METADATA
target_pkg_metadata=$RP_TARGET_PKG_METADATA
profile_path=$trusted_directory/bind920-profile.json

build_recipe_sha256=$("$python_command" - "$script_directory" "$trusted_directory" \
    "$series" "$RP_SELECTION_SHA256" <<'PY'
import json
from pathlib import Path
import sys

script = Path(sys.argv[1])
sys.path.insert(0, str(script.parents[1]))
from bind.shared import components
from shared import release_channel

trusted = Path(sys.argv[2])
selection = components.load_selection(trusted, component="bind_runtime", series=sys.argv[3],
                                      selection_sha256=sys.argv[4])
if selection["producer_action"] != "build":
    raise SystemExit("runtime wrapper requires the sealed build action")
recipe = release_channel.sha256(script / "build-bind920.sh")
if recipe != json.loads((trusted / "producer-inputs.json").read_text())["build_recipe_sha256"]:
    raise SystemExit("runtime build wrapper differs from the selected recipe")
print(recipe)
PY
) || fail 'invalid sealed runtime build inputs'

metadata_field() {
    "$python_command" "$script_directory/bind920_profile.py" "$profile_path" "$1"
}

ports_repository=$(metadata_field ports_repository) || fail 'invalid BIND profile'
ports_commit=$(metadata_field ports_commit) || fail 'invalid BIND profile'
makefile_sha256=$(metadata_field makefile_sha256) || fail 'invalid BIND profile'
distinfo_sha256=$(metadata_field distinfo_sha256) || fail 'invalid BIND profile'
distversion=$(metadata_field distversion) || fail 'invalid BIND profile'
portrevision=$(metadata_field portrevision) || fail 'invalid BIND profile'
package_version=$(metadata_field package_version) || fail 'invalid BIND profile'
freebsd_release=$("$python_command" "$script_directory/../../shared/metadata_profile.py" \
    "$RP_UPSTREAM_METADATA" "$series" freebsd_release) || fail 'invalid upstream metadata'

"$python_command" "$script_directory/../../shared/target_pkg.py" field \
    "$target_pkg_metadata" "$series" release --upstream "$RP_UPSTREAM_METADATA" --role creator >/dev/null

"$pkg_command" update -f
"$pkg_command" install -y git
"$script_directory/../../shared/setup-opnsense-repository.sh" "$series" >/dev/null
pkg_creator_record=$("$python_command" "$script_directory/../../shared/target_pkg.py" install \
    "$target_pkg_metadata" "$series" --upstream "$RP_UPSTREAM_METADATA" --role creator --pkg-command "$pkg_command" \
    --pkg-static "$pkg_static") || fail 'cannot select target package creator'
[ -n "$pkg_creator_record" ] || fail 'target package creator record is empty'
lmdb_package=lmdb0
if [ "$series" = 26.1 ]; then lmdb_package=lmdb; fi
"$pkg_command" install -y -r OPNsense autoconf automake fstrm gmake json-c libedit libidn2 \
    libnghttp2 libtool liburcu libuv libxml2 "$lmdb_package" pkgconf protobuf-c
"$python_command" "$script_directory/../../shared/target_pkg.py" verify \
    "$target_pkg_metadata" "$series" --upstream "$RP_UPSTREAM_METADATA" --role creator --pkg-command "$pkg_command" \
    --pkg-static "$pkg_static"

temporary_directory=$(mktemp -d)
ports_directory="$temporary_directory/ports"
trap 'rm -rf "$temporary_directory"' EXIT HUP INT TERM

"$git_command" init "$ports_directory" >/dev/null
"$git_command" -C "$ports_directory" remote add origin "$ports_repository"
"$git_command" -C "$ports_directory" fetch --depth=1 --filter=blob:none origin "$ports_commit"
"$git_command" -C "$ports_directory" checkout --detach FETCH_HEAD >/dev/null
resolved_commit=$("$git_command" -C "$ports_directory" rev-parse HEAD)
[ "$resolved_commit" = "$ports_commit" ] || fail 'FreeBSD Ports checkout does not match the pinned commit'

makefile="$ports_directory/dns/bind920/Makefile"
distinfo="$ports_directory/dns/bind920/distinfo"
[ "$(sha256 -q "$makefile")" = "$makefile_sha256" ] || fail 'BIND Makefile hash does not match pinned metadata'
[ "$(sha256 -q "$distinfo")" = "$distinfo_sha256" ] || fail 'BIND distinfo hash does not match pinned metadata'
"$python_command" - "$script_directory" "$profile_path" "$resolved_commit" "$makefile" "$distinfo" <<'PYVERIFY'
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(sys.argv[1]).parents[1]))
from bind.runtime import bind920_profile
expected = json.loads(Path(sys.argv[2]).read_text())
actual = bind920_profile.profile_from_recipe(
    {"version": bind920_profile.package_version(expected), "ports_commit": expected["ports_commit"]},
    sys.argv[3], Path(sys.argv[4]).read_bytes(), Path(sys.argv[5]).read_bytes())
if actual != expected:
    raise SystemExit("native Ports recipe differs from the sealed approval")
PYVERIFY

"$python_command" "$script_directory/../../shared/target_pkg.py" verify \
    "$target_pkg_metadata" "$series" --upstream "$RP_UPSTREAM_METADATA" --role creator --pkg-command "$pkg_command" \
    --pkg-static "$pkg_static"
ALLOW_UNSUPPORTED_SYSTEM=yes BATCH=yes NO_DEPENDS=yes OPTIONS_SET=GSSAPI_NONE OPTIONS_UNSET='DOCS GSSAPI_BASE' "$make_command" -C "$ports_directory/dns/bind-tools" PORTSDIR="$ports_directory" package
"$python_command" "$script_directory/../../shared/target_pkg.py" verify \
    "$target_pkg_metadata" "$series" --upstream "$RP_UPSTREAM_METADATA" --role creator --pkg-command "$pkg_command" \
    --pkg-static "$pkg_static"
set -- "$ports_directory"/dns/bind-tools/work/pkg/bind-tools-"$package_version".pkg
[ "$#" -eq 1 ] && [ -f "$1" ] || fail 'bind-tools package was not produced as expected'
bind_tools_package=$1
"$python_command" "$script_directory/../../shared/package_checksums.py" \
    --pkg-command "$pkg_static" "$bind_tools_package"
"$pkg_command" add "$bind_tools_package"

"$python_command" "$script_directory/../../shared/target_pkg.py" verify \
    "$target_pkg_metadata" "$series" --upstream "$RP_UPSTREAM_METADATA" --role creator --pkg-command "$pkg_command" \
    --pkg-static "$pkg_static"
ALLOW_UNSUPPORTED_SYSTEM=yes BATCH=yes NO_DEPENDS=yes OPTIONS_SET=GSSAPI_NONE OPTIONS_UNSET='DOCS GSSAPI_BASE' "$make_command" -C "$ports_directory/dns/bind920" PORTSDIR="$ports_directory" package
"$python_command" "$script_directory/../../shared/target_pkg.py" verify \
    "$target_pkg_metadata" "$series" --upstream "$RP_UPSTREAM_METADATA" --role creator --pkg-command "$pkg_command" \
    --pkg-static "$pkg_static"
set -- "$ports_directory"/dns/bind920/work/pkg/bind920-"$package_version".pkg
[ "$#" -eq 1 ] && [ -f "$1" ] || fail 'bind920 package was not produced as expected'
bind_package=$1
"$python_command" "$script_directory/../../shared/package_checksums.py" \
    --pkg-command "$pkg_static" "$bind_package"
"$pkg_command" add "$bind_package"

bind_version=$("$pkg_command" query -e '%n = bind920' '%v') || fail 'bind920 is not installed after package setup'
comparison=$("$pkg_command" version -t "$bind_version" "$distversion") || fail 'cannot compare BIND versions'
case "$comparison" in
    '='|'>') ;;
    *) fail "BIND $bind_version is below the required $distversion" ;;
esac

mkdir -p "$artifact_directory"
provenance="$temporary_directory/bind920-provenance.json"
"$python_command" "$script_directory/bind920_profile.py" "$profile_path" \
    provenance "$series" "$freebsd_release" \
    --package-creator "$pkg_creator_record" \
    --recipe-sha256 "$build_recipe_sha256" \
    --bind-tools "$bind_tools_package" --bind920 "$bind_package" --output "$provenance"
cp "$bind_tools_package" "$bind_package" "$provenance" "$artifact_directory/"
"$python_command" "$script_directory/../shared/components.py" validate \
    --component bind_runtime --series "$series" --trusted-directory "$trusted_directory" \
    --selection-sha256 "$RP_SELECTION_SHA256" --directory "$artifact_directory" \
    --pkg-command "$pkg_static" >/dev/null
