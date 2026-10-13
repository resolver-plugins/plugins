#!/bin/sh
# Qualify the exact sealed artifact in a fresh FreeBSD environment.
set -eu

fail() {
    printf '%s\n' "$1" >&2
    exit 1
}

component='' mode='' repository_root='' trusted_directory='' series='' install_case=fresh
while [ "$#" -gt 0 ]
do
    [ "$#" -ge 2 ] || fail 'every verification flag requires a value'
    case "$1" in
        --component) component=$2 ;;
        --mode) mode=$2 ;;
        --repository-root) repository_root=$2 ;;
        --trusted-directory) trusted_directory=$2 ;;
        --series) series=$2 ;;
        --install-case) install_case=$2 ;;
        *) fail "unknown verification flag: $1" ;;
    esac
    shift 2
done
case "$component" in bind_runtime|bind_plugin) ;; *) fail 'invalid component' ;; esac
case "$mode" in local|public) ;; *) fail 'invalid verification mode' ;; esac
case "$install_case" in fresh|upstream|below-minimum|below-core) ;; *) fail 'invalid native installation case' ;; esac
if [ -z "$repository_root" ] || [ -z "$trusted_directory" ] || [ -z "$series" ]
then
    fail 'missing verification inputs'
fi
: "${RP_SELECTION_SHA256:?external controller digest is required}"

script_directory=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
ci=$(CDPATH='' cd -- "$script_directory/../.." && pwd)
checkout=$(CDPATH='' cd -- "$ci/../.." && pwd)
evidence=${RP_VERIFY_EVIDENCE_DIRECTORY:-"$repository_root/../verification-$component-$mode"}
temporary=$(mktemp -d)
cleanup() {
    status=$?
    trap - EXIT HUP INT TERM
    rm -rf "$temporary" || status=1
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' HUP INT TERM
metadata() {
    command=$1
    shift
    python3 "$script_directory/verification.py" "$command" \
        --component "$component" --mode "$mode" --series "$series" \
        --repository-root "$repository_root" --trusted-directory "$trusted_directory" \
        --evidence-directory "$evidence" "$@"
}

# Both external seals and every nested map are checked before any pkg/service operation.
metadata preflight
RP_UPSTREAM_METADATA="$trusted_directory/upstream.json"
RP_TARGET_PKG_METADATA="$trusted_directory/reader-target.json"
export RP_UPSTREAM_METADATA RP_TARGET_PKG_METADATA
"$ci/shared/setup-opnsense-repository.sh" "$series" >/dev/null
pkg update -f
if [ "$install_case" = below-core ]
then
    prepared_core=$(/usr/local/sbin/pkg-static query -e '%n = opnsense' '%n|%v|%o|%q')
    [ -n "$prepared_core" ] || fail 'below-core case requires a prepared installed core'
    pkg install -y -r OPNsense pkg
else
    pkg install -y -r OPNsense pkg opnsense
fi
python3 "$ci/shared/target_pkg.py" install "$RP_TARGET_PKG_METADATA" "$series" \
    --upstream "$RP_UPSTREAM_METADATA" --role reader --pkg-command /usr/local/sbin/pkg-static
python3 "$ci/shared/target_pkg.py" verify "$RP_TARGET_PKG_METADATA" "$series" \
    --upstream "$RP_UPSTREAM_METADATA" --role reader --pkg-command /usr/local/sbin/pkg-static
SSL_CERT_FILE=/usr/local/share/certs/ca-root-nss.crt
export SSL_CERT_FILE
metadata authenticate

install -d -m 0750 /conf
if [ ! -e /conf/config.xml ]
then
    test -r /usr/local/etc/config.xml || fail 'installed core factory configuration is missing'
    install -m 0640 /usr/local/etc/config.xml /conf/config.xml
fi
# A package-installed core has not run the appliance's early PHP initialization.
sh /usr/local/etc/rc.subr.d/php
service configd onestatus || service configd onestart
/usr/local/opnsense/service/template_ctl.py OPNsense/WebGui
/usr/local/bin/php -r '
if (stream_resolve_include_path("config.inc") !== "/usr/local/etc/inc/config.inc") {
    fwrite(STDERR, "native PHP cannot resolve config.inc\n");
    exit(1);
}
require_once "config.inc";
'

# An isolated feed configuration prevents a global repository from hiding a missing payload.
python3 - "$evidence/preflight.json" "$temporary" "$checkout/docs/package-repository/resolver-plugins.pub" <<'PY'
import json
from pathlib import Path
import sys
plan=json.loads(Path(sys.argv[1]).read_text())
root,key=Path(sys.argv[2]),sys.argv[3]
entries=plan['repositories']
if plan['selection']['component']=='bind_plugin' and plan['selection']['mode']=='production':
    entries=entries[2:]+entries[:2]
for index,entry in enumerate(entries):
    directory=root / str(index)
    repos=directory / 'repos'
    repos.mkdir(parents=True)
    signature='none' if entry['unsigned'] else 'pubkey'
    (repos / 'qualification.conf').write_text('qualification: {\n'
        f' url: {json.dumps(entry["url"])}, mirror_type: "none", signature_type: "{signature}",\n'
        f' pubkey: {json.dumps(key)}, enabled: yes\n}}\n')
    (directory / 'archives.txt').write_text(''.join(path+'\n' for path in entry['packages']))
    (directory / 'directory.txt').write_text(entry['directory']+'\n')
    (directory / 'complete.txt').write_text(str(any(Path(path).name.startswith('os-bind-rp') for path in entry['packages'])))
(root / 'indices.txt').write_text(''.join(str(index)+'\n' for index in range(len(entries))))
PY

set --
while IFS= read -r dependency
do
    set -- "$@" "$dependency"
done < "$evidence/dependencies.txt"
if [ "$#" -gt 0 ]
then
    /usr/local/sbin/pkg-static install -y -r OPNsense "$@"
fi

if [ "$install_case" = below-core ] && \
    [ "$prepared_core" != "$(/usr/local/sbin/pkg-static query -e '%n = opnsense' '%n|%v|%o|%q')" ]
then
    fail 'below-core preparation upgraded the core before the native checkpoint'
fi

if [ "$mode" = public ] && [ -d "$repository_root/combined" ]
then
    if [ "$install_case" = upstream ]
    then
        /usr/local/sbin/pkg-static install -y -r OPNsense os-bind
    fi
    metadata native-install --install-case "$install_case"
    outcome=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["outcome"])' \
        "$evidence/native-$install_case-result.json")
    if [ "$outcome" = installed ]
    then
        metadata source
        "$ci/bind/runtime/verify-bind-runtime.sh"
        metadata canary --label "native-$install_case-canary"
    fi
    if [ "$install_case" != fresh ]
    then
        python3 "$ci/shared/target_pkg.py" verify "$RP_TARGET_PKG_METADATA" "$series" \
            --upstream "$RP_UPSTREAM_METADATA" --role reader --pkg-command /usr/local/sbin/pkg-static
        exit 0
    fi
    # Retain the previous installer qualification until native replacement proof passes.
    /usr/local/sbin/pkg-static delete -f -y os-bind-rp
    /usr/local/sbin/pkg-static install -y -r OPNsense os-bind
    metadata installer-before
    evidence=$(CDPATH='' cd -- "$evidence" && pwd)
    RP_PKG_COMMAND=/usr/local/sbin/pkg-static RP_PKG_STATIC_COMMAND="$evidence/installer-bin/observe-pkg-static" \
        RP_TTY_PATH="$evidence/installer-approval" RP_STATE_DIRECTORY="$evidence/installer-state" \
        RP_TEMPORARY_DIRECTORY="$evidence/installer-transaction" RP_VERIFY_INSTALLER_LOG="$evidence/installer-pkg.log" \
        PATH="$evidence/installer-bin:$PATH" "$checkout/scripts/install-os-bind-rp.sh"
    metadata installed --directory "$repository_root/combined" --allow-upstream --label public-installer
    metadata installer-after
    metadata source
    "$ci/bind/runtime/verify-bind-runtime.sh"
    metadata canary --label public-canary
else
    while IFS= read -r index
    do
        directory=$(cat "$temporary/$index/directory.txt")
        if [ "$(cat "$temporary/$index/complete.txt")" = True ]
        then
            for plugin in os-bind-rp os-bind-rp-devel
            do
                if /usr/local/sbin/pkg-static query -e "%n = $plugin" '%n' >/dev/null 2>&1
                then
                    /usr/local/sbin/pkg-static delete -f -y "$plugin"
                fi
            done
            /usr/local/sbin/pkg-static install -f -y -r OPNsense os-bind
        fi
        set --
        while IFS= read -r archive
        do
            identity=$(/usr/local/sbin/pkg-static query -F "$archive" '%n-%v')
            [ -n "$identity" ] || fail 'empty archive identity'
            set -- "$@" "$identity"
        done < "$temporary/$index/archives.txt"
        /usr/local/sbin/pkg-static -o "REPOS_DIR=$temporary/$index/repos" update -f -r qualification
        /usr/local/sbin/pkg-static -o "REPOS_DIR=$temporary/$index/repos" install -f -y -r qualification "$@"
        metadata installed --directory "$directory" --label "installed-$index"
        if [ "$(cat "$temporary/$index/complete.txt")" = True ]
        then
            metadata source
        fi
        "$ci/bind/runtime/verify-bind-runtime.sh"
        metadata canary --label "canary-$index"
    done < "$temporary/indices.txt"
fi
python3 "$ci/shared/target_pkg.py" verify "$RP_TARGET_PKG_METADATA" "$series" \
    --upstream "$RP_UPSTREAM_METADATA" --role reader --pkg-command /usr/local/sbin/pkg-static
