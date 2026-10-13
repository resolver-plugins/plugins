#!/bin/sh

set -eu

readonly public_key_sha256=bd89d6f91807c71f8a744532c9ce2f97e9590f8858ac779bfb2f23c10804e07e
readonly repository_base_url=https://resolver-plugins.github.io/repository/pkg
# pkg uses legacy/SHA256 hex and type-2 BLAKE2b z-base32 file digests.
readonly file_checksum_pattern='^((1\$)?[[:xdigit:]]{64}|2\$[ybndrfg8ejkmcpqxot1uwisza345h769]{103})$'

state_directory=
temporary_directory=
temporary_directory_owned=no
pkg_lock_changed=no
completed=no
key_stage_directory=
key_stage_owned=no
transaction_started=no
recovery_repository=
recovery_repository_directory=
recovery_exact_identities=

cleanup() {
    status=$?
    trap - EXIT HUP INT TERM
    if [ "$pkg_lock_changed" = yes ]
    then
        if ! "$pkg_command" unlock -y pkg >/dev/null 2>&1
        then
            printf '%s\n' 'WARNING: could not restore the original pkg lock state' >&2
            [ "$status" -ne 0 ] || status=1
        fi
    fi
    if [ "$status" -eq 0 ] && [ "$completed" = yes ] && \
        [ "$temporary_directory_owned" = yes ] && [ -n "$temporary_directory" ] && \
        [ "$temporary_directory" != / ]
    then
        rm -rf "$temporary_directory"
    elif [ "$status" -ne 0 ]
    then
        if [ -n "$state_directory" ]
        then
            printf 'Diagnostic state retained at %s\n' "$state_directory" >&2
        fi
        if [ -n "$temporary_directory" ]
        then
            printf 'Temporary package data retained at %s\n' "$temporary_directory" >&2
        fi
        if [ "$transaction_started" = yes ] && [ -n "$recovery_repository" ]
        then
            printf 'Recovery package repository: %s\n' "$recovery_repository" >&2
            printf '%s\n' 'Dry-run recovery before applying it:' >&2
            printf '  %s -o REPOS_DIR=%s install -n -f -r resolver-recovery%s\n' \
                "$pkg_static_command" "$recovery_repository_directory" \
                "$recovery_exact_identities" >&2
            printf '%s\n' 'Stop BIND before an approved recovery, restore config.xml.bak, then validate and restart BIND.' >&2
        fi
    fi
    if [ "$key_stage_owned" = yes ] && [ -n "$key_stage_directory" ] && \
        [ "$key_stage_directory" != / ]
    then
        rm -rf "$key_stage_directory"
    fi
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' HUP INT TERM

fail() {
    printf '%s\n' "$1" >&2
    exit 1
}

package_field() {
    printf '%s\n' "$1" | awk -F '|' -v field="$2" 'NF == 3 { print $field; exit }'
}

package_description() {
    name=$(package_field "$1" 1)
    version=$(package_field "$1" 2)
    origin=$(package_field "$1" 3)
    if [ -n "$name" ] && [ -n "$version" ] && [ -n "$origin" ]
    then
        printf '%s %s from %s' "$name" "$version" "$origin"
    else
        printf '%s' 'not installed'
    fi
}

write_repository() {
    name=$1
    url=$2
    enabled=$3
    destination=$4
    key=${5:-}
    {
        printf '%s\n' "$name: {"
        printf '  url: "%s",\n' "$url"
        printf '%s\n' '  mirror_type: "none",'
        if [ -n "$key" ]
        then
            printf '%s\n' '  signature_type: "pubkey",'
            printf '  pubkey: "%s",\n' "$key"
        else
            printf '%s\n' '  signature_type: "none",'
        fi
        printf '  enabled: %s\n' "$enabled"
        printf '%s\n' '}'
    } > "$destination"
}

series_from_opnsense_version() {
    product_version=$(opnsense-version 2>/dev/null | awk 'NR == 1 { print $2 }')
    series=$(printf '%s\n' "$product_version" | sed -E 's/^([0-9]+\.[0-9]+).*/\1/')
    case "$series" in
        26.1)
            case "$("$pkg_command" version -t "$product_version" '26.1.11_10')" in
                '='|'>') ;;
                *) fail "OPNsense 26.1.11_10 or newer is required (installed: $product_version)" ;;
            esac
            printf '%s\n' "$series"
            ;;
        26.7)
            printf '%s\n' "$series"
            ;;
        *)
            fail "unsupported OPNsense release series: ${series:-unknown}"
            ;;
    esac
}

validate_plugin_candidate_version() {
    candidate_version=$1
    expected_series=$2
    case "$candidate_version" in
        "$expected_series"_[1-9]*)
            revision=${candidate_version#"$expected_series"_}
            case "$revision" in
                ''|*[!0-9]*)
                    fail "os-bind-rp version does not match OPNsense series $expected_series"
                    ;;
            esac
            ;;
        *)
            fail "os-bind-rp version does not match OPNsense series $expected_series"
            ;;
    esac
}

remote_pkg() {
    "$pkg_command" -o "REPOS_DIR=$repository_directory" "$@"
}

verified_pkg() {
    "$pkg_static_command" -o "REPOS_DIR=$isolated_repository_directory" "$@"
}

exact_identity_is_installed() {
    identity=$1
    case "$identity" in
        bind-tools-*) package_name=bind-tools ;;
        bind920-*) package_name=bind920 ;;
        os-bind-rp-*) package_name=os-bind-rp ;;
        os-bind-*) package_name=os-bind ;;
        *) return 1 ;;
    esac
    installed=$(installed_record "$package_name") || fail "could not inspect installed identity: $package_name"
    installed_version=$(package_field "$installed" 2)
    [ "$package_name-$installed_version" = "$identity" ]
}

package_dry_run() {
    output=$1
    repository_config=$2
    repository_name=$3
    force=$4
    shift 4
    set +e
    if [ "$force" = yes ]
    then
        "$pkg_static_command" -o "REPOS_DIR=$repository_config" install -n -f \
            -r "$repository_name" "$@" > "$output" 2>&1
    else
        "$pkg_static_command" -o "REPOS_DIR=$repository_config" install -n \
            -r "$repository_name" "$@" > "$output" 2>&1
    fi
    status=$?
    set -e
    case "$status" in
        0|1) ;;
        *) fail "package dry run failed with status $status" ;;
    esac
    grep -Eq 'will be affected|already installed' "$output" || \
        fail 'package dry run did not produce a recognized transaction plan'
    requested_identities="$*"
    exact_installed_identities=
    for identity in "$@"
    do
        if exact_identity_is_installed "$identity"
        then
            exact_installed_identities="${exact_installed_identities:+$exact_installed_identities }$identity"
        fi
    done
    if grep -Eq '(^|[[:space:]])(pkg|opnsense)(-[0-9]|:[[:space:]]*[0-9])' "$output"
    then
        fail 'package dry run attempted to change pkg or OPNsense core'
    fi
    set +e
    # shellcheck disable=SC2016 # The single-quoted text is an awk program.
    unexpected_change=$("$dry_run_awk_command" \
        -v "requested_identities=$requested_identities" \
        -v "exact_installed_identities=$exact_installed_identities" '
        BEGIN {
            requested_count = split(requested_identities, requested, " ")
            for (item = 1; item <= requested_count; item++) {
                requested_name[item] = requested[item]
                sub(/-[0-9].*/, "", requested_name[item])
            }
            if (exact_installed_identities != "") {
                exact_count = split(exact_installed_identities, exact, " ")
                for (item = 1; item <= exact_count; item++) {
                    may_be_omitted[exact[item]] = 1
                }
            }
        }
        function allowed(name) {
            return name == "bind-tools" || name == "bind920" || \
                name == "os-bind" || name == "os-bind-rp"
        }
        function mutation_action(line) {
            if (line == "New packages to be INSTALLED:") return "install"
            if (line == "Installed packages to be UPGRADED:") return "upgrade"
            if (line == "Installed packages to be REINSTALLED:") return "reinstall"
            if (line == "Installed packages to be DOWNGRADED:") return "downgrade"
            if (line == "Installed packages to be REMOVED:") return "remove"
            return ""
        }
        function invalid_plan() {
            structural_error = 1
            exit 3
        }
        {
            raw = $0
            line = $0
            sub(/^[[:space:]]*/, "", line)
            if (line ~ /will be affected/) changes_reported = 1
            action = mutation_action(line)
            if (action != "") {
                if (in_mutation_section && !section_has_entry) invalid_plan()
                saw_mutation_section = 1
                in_mutation_section = 1
                section_has_entry = 0
                section_action = action
                next
            }
            if (line == "") {
                if (in_mutation_section && !section_has_entry) invalid_plan()
                in_mutation_section = 0
                next
            }
            if (in_mutation_section && raw !~ /^[[:space:]]/) {
                if (!section_has_entry) invalid_plan()
                in_mutation_section = 0
            }
            name = ""
            entry_identity = ""
            if (line ~ /^[A-Za-z0-9][A-Za-z0-9+_.-]*:[[:space:]]/) {
                name = line
                sub(/:.*/, "", name)
                if (section_action != "remove") {
                    result_version = line
                    sub(/^[^:]*:[[:space:]]*/, "", result_version)
                    if (result_version ~ /[[:space:]]->[[:space:]]/) {
                        sub(/^.*[[:space:]]->[[:space:]]*/, "", result_version)
                    }
                    sub(/[[:space:]].*/, "", result_version)
                    entry_identity = name "-" result_version
                }
            } else if (line ~ /^[A-Za-z0-9][A-Za-z0-9+_.-]*-[0-9][^[:space:]]*([[:space:]]|$)/) {
                entry_identity = line
                sub(/[[:space:]].*/, "", entry_identity)
                name = entry_identity
                sub(/-[0-9].*/, "", name)
                if (section_action == "remove") entry_identity = ""
            }
            if (in_mutation_section) {
                if (name == "") invalid_plan()
                saw_mutation_entry = 1
                section_has_entry = 1
                for (item = 1; item <= requested_count; item++) {
                    if (name == requested_name[item]) {
                        requested_appeared[item] = 1
                        if (entry_identity == requested[item]) {
                            requested_satisfied[item] = 1
                        }
                    }
                }
                if (!allowed(name)) {
                    print name
                    unexpected_package = 1
                    exit
                }
                next
            }
            if (raw ~ /^[[:space:]]/ && name != "") invalid_plan()
        }
        END {
            if (!structural_error && !unexpected_package && \
                in_mutation_section && !section_has_entry) exit 3
            if (!structural_error && !unexpected_package && changes_reported && \
                (!saw_mutation_section || !saw_mutation_entry)) exit 3
            if (!structural_error && !unexpected_package) {
                for (item = 1; item <= requested_count; item++) {
                    if (!requested_satisfied[item] && \
                        (!may_be_omitted[requested[item]] || requested_appeared[item])) {
                        print requested[item]
                        exit 4
                    }
                }
            }
        }
    ' "$output")
    validation_status=$?
    set -e
    case "$validation_status" in
        0) ;;
        3) fail 'package dry run produced an unrecognized package mutation plan' ;;
        4) fail "package dry run omitted requested identity: $unexpected_change" ;;
        *) fail "could not validate package dry run (status $validation_status)" ;;
    esac
    if [ -n "$unexpected_change" ]
    then
        fail "package dry run attempted unexpected package change: $unexpected_change"
    fi
}

remote_package() {
    requested_name=$1
    expected_origin=$2
    record=$(remote_pkg rquery -r resolver-plugins -e "%n = $requested_name" '%n|%v|%o') || \
        fail "could not inspect $requested_name in the Resolver repository"
    [ "$(printf '%s\n' "$record" | awk 'NF { count++ } END { print count + 0 }')" -eq 1 ] || \
        fail "Resolver repository returned an ambiguous $requested_name candidate"
    name=$(package_field "$record" 1)
    version=$(package_field "$record" 2)
    origin=$(package_field "$record" 3)
    if [ "$name" != "$requested_name" ] || [ "$origin" != "$expected_origin" ] || \
        ! printf '%s\n' "$version" | grep -Eq '^[A-Za-z0-9][A-Za-z0-9._+,-]*$'
    then
        fail "Resolver repository returned an invalid $requested_name identity"
    fi
    printf '%s\n' "$record"
}

version_at_least() {
    comparison=$("$pkg_command" version -t "$1" "$2") || fail 'could not compare package versions'
    case "$comparison" in
        '='|'>') return 0 ;;
        '<') return 1 ;;
        *) fail 'invalid package version comparison' ;;
    esac
}

read_plugin_requirements() {
    plugin_archive=$(archive_path_for "os-bind-rp-$candidate_plugin_version")
    "$pkg_static_command" info -F "$plugin_archive" --raw --raw-format json \
        > "$temporary_directory/plugin-manifest.json" || fail 'could not inspect plugin prerequisites'
    requirements=$(python3 - "$temporary_directory/plugin-manifest.json" <<'PY'
import json
from pathlib import Path
import re
import sys

record = json.loads(Path(sys.argv[1]).read_text())
formula = re.fullmatch(r"bind920 >= (9\.20\.[0-9]+(?:_[1-9][0-9]*)?)", record.get("dep_formula", ""))
deps = record.get("deps") or {}
core = deps.get("opnsense") or {}
runtime = deps.get("bind920") or {}
if (formula is None or set(deps) != {"opnsense", "bind920"}
        or core.get("origin") != "opnsense/opnsense"
        or runtime.get("origin") != "dns/bind920"
        or re.fullmatch(r"[0-9]+(?:\.[0-9]+)*(?:_[0-9]+)?", core.get("version", "")) is None):
    raise SystemExit("invalid plugin prerequisite manifest")
print(formula[1] + "|" + core["version"])
PY
    ) || fail 'could not read authenticated plugin prerequisites'
    minimum_bind920_version=${requirements%%|*}
    minimum_opnsense_version=${requirements#*|}
    installed_core=$(installed_record opnsense)
    if [ "$(package_field "$installed_core" 3)" != opnsense/opnsense ] || \
        ! version_at_least "$(package_field "$installed_core" 2)" "$minimum_opnsense_version"
    then
        fail "plugin requires installed OPNsense $minimum_opnsense_version or newer"
    fi
    if ! version_at_least "$candidate_bind920_version" "$minimum_bind920_version" || \
        ! version_at_least "$candidate_bind_tools_version" "$minimum_bind920_version"
    then
        fail 'repository BIND packages do not meet the plugin prerequisite'
    fi
}

installed_bind_is_eligible() {
    bind920_name=$(package_field "$bind920" 1)
    bind920_version=$(package_field "$bind920" 2)
    bind920_origin=$(package_field "$bind920" 3)
    bind_tools_name=$(package_field "$bind_tools" 1)
    bind_tools_version=$(package_field "$bind_tools" 2)
    bind_tools_origin=$(package_field "$bind_tools" 3)

    [ "$bind920_name" = bind920 ] && [ "$bind920_origin" = dns/bind920 ] || return 1
    [ "$bind_tools_name" = bind-tools ] && [ "$bind_tools_origin" = dns/bind-tools ] || return 1
    version_at_least "$bind920_version" "$minimum_bind920_version" || return 1
    version_at_least "$bind_tools_version" "$minimum_bind920_version" || return 1
    installed_edges=$("$pkg_command" query -e '%n = bind920' '%dn|%do|%dv') || \
        fail 'could not inspect installed BIND prerequisites'
    printf '%s\n' "$installed_edges" | grep -Fxq "bind-tools|dns/bind-tools|$bind_tools_version"
}

reject_downgrade() {
    installed_record=$1
    candidate_version=$2
    package_name=$3
    installed_version=$(package_field "$installed_record" 2)
    if [ -n "$installed_version" ]
    then
        comparison=$("$pkg_command" version -t "$installed_version" "$candidate_version") || \
            fail "could not compare installed $package_name"
        case "$comparison" in
            '>') fail "refusing to downgrade $package_name from $installed_version to $candidate_version" ;;
            '='|'<') ;;
            *) fail "invalid $package_name version comparison" ;;
        esac
    fi
}

create_state_directory() {
    umask 077
    if [ -n "${RP_STATE_DIRECTORY:-}" ]
    then
        state_directory=$RP_STATE_DIRECTORY
    else
        backup_root=${RP_BACKUP_ROOT:-/var/backups}
        mkdir -p "$backup_root"
        state_directory="$backup_root/os-bind-rp-install.$(date -u +%Y%m%dT%H%M%SZ).$$"
    fi
    mkdir -m 0700 "$state_directory" || fail "could not create state directory: $state_directory"
    config_file=${RP_CONFIG_FILE:-/conf/config.xml}
    [ -f "$config_file" ] || fail "OPNsense configuration is missing: $config_file"
    cp -p "$config_file" "$state_directory/config.xml.bak"
    "$pkg_command" info -a > "$state_directory/packages.before.txt"
    "$pkg_command" query -a '%n|%v|%o' > "$state_directory/package-identities.before.raw" || \
        fail 'could not preserve package inventory'
    sort "$state_directory/package-identities.before.raw" > "$state_directory/package-identities.before.txt"
    cp "$state_directory/package-identities.before.txt" "$state_directory/installed-packages.txt"
    "$pkg_command" query -a '%n|%k' > "$state_directory/package-locks.before.txt" || \
        fail 'could not preserve package locks'
}

prepare_temporary_directory() {
    if [ -n "${RP_TEMPORARY_DIRECTORY:-}" ]
    then
        temporary_directory=$RP_TEMPORARY_DIRECTORY
        mkdir -p "$temporary_directory"
    else
        temporary_directory=$(mktemp -d /tmp/os-bind-rp-install.XXXXXX)
        temporary_directory_owned=yes
    fi
    chmod 0700 "$temporary_directory"
    archive_root="$temporary_directory/verified-repository"
    archive_all="$archive_root/All"
    isolated_repository_directory="$temporary_directory/isolated-repos"
    mkdir -p "$archive_all" "$isolated_repository_directory"
    : > "$temporary_directory/package-file-checksums.txt"
    : > "$temporary_directory/package-archives.txt"
}

archive_path_for() {
    identity=$1
    for candidate in \
        "$archive_root/$identity.pkg" "$archive_root/$identity.txz" \
        "$archive_all/$identity.pkg" "$archive_all/$identity.txz"
    do
        if [ -f "$candidate" ]
        then
            printf '%s\n' "$candidate"
        fi
    done
}

verify_archive_manifest() {
    package_name=$1
    package_version=$2
    package_origin=$3
    archive=$4
    checksum_output=$5
    identity="$package_name-$package_version"
    archive_identity=$("$pkg_static_command" query -F "$archive" '%n|%v|%o') || \
        fail "could not inspect archive identity for $identity"
    [ "$archive_identity" = "$package_name|$package_version|$package_origin" ] || \
        fail "archive identity mismatch for $identity: $archive_identity"

    "$pkg_static_command" query -F "$archive" '%Fp|%Fs' > "$checksum_output" || \
        fail "could not inspect file checksums for $identity"
    [ -s "$checksum_output" ] || fail "archive contains no file checksums: $identity"
    while IFS='|' read -r file_path file_checksum extra
    do
        if [ -z "$file_path" ] || [ -n "${extra:-}" ] || \
            ! printf '%s\n' "$file_checksum" | grep -Eq "$file_checksum_pattern"
        then
            fail "incompatible file checksum in $identity for ${file_path:-unknown}: ${file_checksum:-(null)}"
        fi
    done < "$checksum_output"
}

fetch_and_verify_archive() {
    package_name=$1
    package_version=$2
    package_origin=$3
    identity="$package_name-$package_version"
    remote_pkg fetch -y -r resolver-plugins -o "$archive_root" "$identity" || \
        fail "could not fetch exact package $identity"
    archive=$(archive_path_for "$identity")
    [ "$(printf '%s\n' "$archive" | awk 'NF { count++ } END { print count + 0 }')" -eq 1 ] || \
        fail "expected one archive for $identity"
    checksum_output="$temporary_directory/$package_name.file-checksums"
    verify_archive_manifest "$package_name" "$package_version" "$package_origin" \
        "$archive" "$checksum_output"
    while IFS='|' read -r file_path file_checksum
    do
        printf '%s|%s|%s|%s\n' "$identity" "$file_path" "$file_checksum" "$archive" \
            >> "$temporary_directory/package-file-checksums.txt"
    done < "$checksum_output"
    archive_sha256=$(sha256 -q "$archive")
    printf '%s|%s|%s\n' "$identity" "$archive_sha256" "$archive" \
        >> "$temporary_directory/package-archives.txt"
}

verify_archive_hashes() {
    while IFS='|' read -r identity expected archive
    do
        [ "$(sha256 -q "$archive")" = "$expected" ] || \
            fail "verified archive changed before installation: $identity"
    done < "$temporary_directory/package-archives.txt"
}

recovery_archive_path() {
    identity=$1
    archive_directory=${2:-$recovery_repository}
    for candidate in \
        "$archive_directory/$identity.pkg" "$archive_directory/$identity.txz" \
        "$archive_directory/All/$identity.pkg" "$archive_directory/All/$identity.txz"
    do
        if [ -f "$candidate" ]
        then
            printf '%s\n' "$candidate"
        fi
    done
}

capture_recovery_packages() {
    recovery_repository="$state_directory/recovery-packages"
    recovery_repository_directory="$state_directory/recovery-repos"
    mkdir -m 0700 "$recovery_repository" "$recovery_repository_directory"
    : > "$state_directory/recovery-identities.txt"
    recovery_exact_identities=
    set --
    for record in "$bind_tools" "$bind920" "$official_plugin" "$installed_plugin"
    do
        package_name=$(package_field "$record" 1)
        if [ -n "$package_name" ]
        then
            set -- "$@" "$package_name"
            printf '%s\n' "$record" >> "$state_directory/recovery-identities.txt"
        fi
    done
    [ "$#" -gt 0 ] || return 0
    "$pkg_command" create -o "$recovery_repository" "$@" || \
        fail 'could not preserve installed packages for recovery'
    set --
    while IFS='|' read -r package_name package_version package_origin
    do
        identity="$package_name-$package_version"
        archive=$(recovery_archive_path "$identity")
        [ "$(printf '%s\n' "$archive" | awk 'NF { count++ } END { print count + 0 }')" -eq 1 ] || \
            fail "expected one recovery archive for $identity"
        verify_archive_manifest "$package_name" "$package_version" "$package_origin" \
            "$archive" "$state_directory/$package_name.recovery-checksums.txt"
        set -- "$@" "$identity"
        recovery_exact_identities="$recovery_exact_identities $identity"
    done < "$state_directory/recovery-identities.txt"
    "$pkg_static_command" repo "$recovery_repository" || \
        fail 'could not construct recovery package repository'
    write_repository resolver-recovery "file://$recovery_repository" yes \
        "$recovery_repository_directory/resolver-recovery.conf"
    package_dry_run "$state_directory/pkg-recovery.dry-run.txt" \
        "$recovery_repository_directory" resolver-recovery yes "$@"
}

installed_record() {
    package_name=$1
    inventory=$("$pkg_command" query -a '%n|%v|%o') || fail 'could not inspect installed package inventory'
    [ -n "$inventory" ] || fail 'installed package inventory is empty'
    printf '%s\n' "$inventory" | awk -F '|' -v name="$package_name" '
        NF != 3 || $1 == "" || $2 == "" || $3 == "" || seen[$1]++ { invalid = 1 }
        $1 == name { record = $0 }
        END { if (invalid) exit 1; if (record != "") print record }
    ' || fail 'invalid installed package inventory'
}

verify_installed_record() {
    expected=$1
    package_name=$(package_field "$expected" 1)
    actual=$(installed_record "$package_name") || fail "could not inspect installed identity: $package_name"
    [ "$actual" = "$expected" ] || fail "installed identity mismatch for $package_name: ${actual:-missing}"
}

verify_installed_checksums() {
    package_name=$1
    checksum_identity=$(installed_record "$package_name") || fail "could not inspect $package_name identity"
    expected_owner="$package_name-$(package_field "$checksum_identity" 2)"
    checksum_output="$temporary_directory/$package_name.installed-checksums"
    "$pkg_command" query -e "%n = $package_name" '%Fp|%Fs' > "$checksum_output" || \
        fail "could not inspect installed checksums for $package_name"
    [ -s "$checksum_output" ] || fail "installed package has no file checksums: $package_name"
    while IFS='|' read -r file_path file_checksum extra
    do
        if [ -z "$file_path" ] || [ -n "${extra:-}" ] || \
            ! printf '%s\n' "$file_checksum" | grep -Eq "$file_checksum_pattern"
        then
            fail "incompatible installed file checksum in $package_name for ${file_path:-unknown}: ${file_checksum:-(null)}"
        fi
        owner=$("$pkg_command" which -q "$file_path") || fail "installed file is not package-owned: $file_path"
        [ "$owner" = "$expected_owner" ] || fail "installed file has the wrong owner: $file_path"
    done < "$checksum_output"
    sort "$checksum_output" > "$checksum_output.sorted"
    sort "$temporary_directory/$package_name.file-checksums" > "$checksum_output.expected"
    cmp -s "$checksum_output.expected" "$checksum_output.sorted" || \
        fail "installed file map differs from authenticated archive: $package_name"
}

observe_retained_runtime() {
    phase=$1
    observation_directory="$temporary_directory/retained-runtime"
    mkdir -p "$observation_directory"
    for retained_record in "$bind_tools" "$bind920"
    do
        retained_name=$(package_field "$retained_record" 1)
        retained_version=$(package_field "$retained_record" 2)
        retained_origin=$(package_field "$retained_record" 3)
        verify_installed_record "$retained_record"
        native_map="$state_directory/$retained_name.native-$phase.txt"
        "$pkg_command" query -e "%n = $retained_name" '%Fp|%Fs' > "$native_map.raw" || \
            fail "could not inspect retained native files: $retained_name"
        sort "$native_map.raw" > "$native_map"
        [ -s "$native_map" ] || fail "retained package has no native file records: $retained_name"
        measured_map="$state_directory/$retained_name.recovery-checksums.txt"
        if [ "$phase" = after ]
        then
            "$pkg_command" create -o "$observation_directory" "$retained_name" || \
                fail "could not observe retained package files: $retained_name"
            measured_map="$state_directory/$retained_name.retained-checksums.txt"
            retained_archive=$(recovery_archive_path "$retained_name-$retained_version" "$observation_directory")
            [ "$(printf '%s\n' "$retained_archive" | awk 'NF { count++ } END { print count + 0 }')" -eq 1 ] || \
                fail "expected one retained observation archive for $retained_name"
            verify_archive_manifest "$retained_name" "$retained_version" "$retained_origin" \
                "$retained_archive" "$measured_map"
            sort "$state_directory/$retained_name.recovery-checksums.txt" > "$temporary_directory/retained-before.txt"
            sort "$measured_map" > "$temporary_directory/retained-after.txt"
            cmp -s "$temporary_directory/retained-before.txt" "$temporary_directory/retained-after.txt" || \
                fail "retained package files changed: $retained_name"
            cmp -s "$state_directory/$retained_name.native-before.txt" "$native_map" || \
                fail "retained native file records changed: $retained_name"
        fi
        cut -d '|' -f 1 "$native_map" | sort > "$temporary_directory/retained-native-paths.txt"
        cut -d '|' -f 1 "$measured_map" | sort > "$temporary_directory/retained-measured-paths.txt"
        cmp -s "$temporary_directory/retained-native-paths.txt" "$temporary_directory/retained-measured-paths.txt" || \
            fail "retained native file ownership map differs: $retained_name"
        while IFS='|' read -r retained_path _retained_checksum
        do
            [ "$("$pkg_command" which -q "$retained_path")" = "$retained_name-$retained_version" ] || \
                fail "retained file has the wrong owner: $retained_path"
        done < "$measured_map"
    done
}

pkg_command=${RP_PKG_COMMAND:-pkg}
pkg_static_command=${RP_PKG_STATIC_COMMAND:-/usr/local/sbin/pkg-static}
dry_run_awk_command=${RP_DRY_RUN_AWK_COMMAND:-awk}
repository_directory=${RP_PKG_REPOSITORY_DIR:-/usr/local/etc/pkg/repos}
key_directory=${RP_PKG_KEYS_DIR:-/usr/local/etc/pkg/keys}
mkdir -p "$repository_directory" "$key_directory"

series=$(series_from_opnsense_version)
repository_abi=$("$pkg_command" config ABI) || fail 'could not determine the pkg ABI'
case "$repository_abi" in
    ''|*[!A-Za-z0-9._:+-]*) fail "invalid pkg ABI: ${repository_abi:-empty}" ;;
esac
repository_url="$repository_base_url/\${ABI}/$series/latest"
repository_fetch_url="$repository_base_url/$repository_abi/$series/latest"
public_key="$key_directory/resolver-plugins.pub"
if [ -n "${RP_TEMPORARY_DIRECTORY:-}" ]
then
    key_stage_directory=$RP_TEMPORARY_DIRECTORY
else
    key_stage_directory=$(mktemp -d /tmp/os-bind-rp-key.XXXXXX)
    key_stage_owned=yes
fi
mkdir -p "$key_stage_directory"
public_key_candidate="$key_stage_directory/resolver-plugins.pub"

fetch -o "$public_key_candidate" "$repository_fetch_url/resolver-plugins.pub"
[ "$(sha256 -q "$public_key_candidate")" = "$public_key_sha256" ] || \
    fail 'resolver-plugins public-key fingerprint verification failed'
install -m 0644 "$public_key_candidate" "$public_key"
if [ "$key_stage_owned" = yes ]
then
    rm -rf "$key_stage_directory"
    key_stage_directory=
    key_stage_owned=no
fi

write_repository resolver-plugins "$repository_url" yes \
    "$repository_directory/resolver-plugins.conf" "$public_key"
remote_pkg update -r resolver-plugins

candidate_bind920=$(remote_package bind920 dns/bind920)
candidate_bind_tools=$(remote_package bind-tools dns/bind-tools)
candidate_plugin=$(remote_package os-bind-rp opnsense/os-bind-rp)
candidate_bind920_version=$(package_field "$candidate_bind920" 2)
candidate_bind_tools_version=$(package_field "$candidate_bind_tools" 2)
candidate_plugin_version=$(package_field "$candidate_plugin" 2)
validate_plugin_candidate_version "$candidate_plugin_version" "$series"

prepare_temporary_directory
fetch_and_verify_archive os-bind-rp "$candidate_plugin_version" opnsense/os-bind-rp
read_plugin_requirements
for incompatible_plugin in os-bind-devel os-bind-rp-devel
do
    incompatible_record=$(installed_record "$incompatible_plugin") || fail 'could not inspect incompatible plugins'
    [ -z "$incompatible_record" ] || \
        fail "remove incompatible $incompatible_plugin before migration"
done

bind920=$(installed_record bind920)
bind_tools=$(installed_record bind-tools)
bind_update_required=no
if ! installed_bind_is_eligible
then
    bind_update_required=yes
    reject_downgrade "$bind920" "$candidate_bind920_version" bind920
    reject_downgrade "$bind_tools" "$candidate_bind_tools_version" bind-tools
    printf '%s\n' "Installed bind920: $(package_description "$bind920")" >&2
    printf '%s\n' "Installed bind-tools: $(package_description "$bind_tools")" >&2
    printf '%s\n' \
        "Available fallback: bind920 $candidate_bind920_version and bind-tools $candidate_bind_tools_version" >&2
    printf '%s\n' 'An update to BIND is required to address a breaking issue with DoT.' >&2
    printf '%s\n' "Compatible OPNsense BIND packages at $minimum_bind920_version or newer can be retained." >&2
    printf '%s' 'Do you wish to update BIND? [y/N] ' >&2
    tty_path=${RP_TTY_PATH:-/dev/tty}
    [ -r "$tty_path" ] || fail 'a terminal is required to approve the BIND update'
    if ! IFS= read -r response < "$tty_path"
    then
        response=
    fi
    case "$response" in
        y|Y|yes|YES|Yes) ;;
        *) fail 'BIND update declined; os-bind-rp was not installed.' ;;
    esac
fi

official_plugin=$(installed_record os-bind)
installed_plugin=$(installed_record os-bind-rp)
if [ -n "$official_plugin" ]
then
    printf '%s\n' "Replacing official os-bind ($(package_description "$official_plugin")) with os-bind-rp." >&2
elif [ -n "$installed_plugin" ]
then
    printf '%s\n' "Upgrading installed os-bind-rp ($(package_description "$installed_plugin"))." >&2
fi

fetch_and_verify_archive bind920 "$candidate_bind920_version" dns/bind920
fetch_and_verify_archive bind-tools "$candidate_bind_tools_version" dns/bind-tools

"$pkg_static_command" repo "$archive_root" || fail 'could not construct verified local package repository'
write_repository resolver-verified "file://$archive_root" yes \
    "$isolated_repository_directory/resolver-verified.conf"
opnsense_repository_config=${RP_OPNSENSE_REPOSITORY_CONFIG:-/usr/local/etc/pkg/repos/OPNsense.conf}
[ -f "$opnsense_repository_config" ] || \
    fail "OPNsense repository configuration is missing: $opnsense_repository_config"
cp -p "$opnsense_repository_config" "$isolated_repository_directory/OPNsense.conf"

verify_archive_hashes
create_state_directory
cp "$temporary_directory/package-file-checksums.txt" "$state_directory/package-file-checksums.txt"
cp "$temporary_directory/package-archives.txt" "$state_directory/package-archives.txt"
cp "$temporary_directory/package-archives.txt" "$state_directory/candidate-sha256.txt"
printf '%s\n' "$candidate_bind920" "$candidate_bind_tools" "$candidate_plugin" \
    > "$state_directory/candidates.txt"

pkg_identity_before=$(installed_record pkg)
pkg_lock_before=$("$pkg_command" query -e '%n = pkg' '%k') || fail 'could not inspect the original pkg lock'
case "$pkg_lock_before" in
    1) pkg_was_locked=yes ;;
    0)
        pkg_was_locked=no
        "$pkg_command" lock -y pkg >/dev/null
        pkg_lock_changed=yes
        ;;
    *) fail 'invalid pkg lock state' ;;
esac
[ "$(installed_record pkg)" = "$pkg_identity_before" ] || \
    fail 'pkg identity changed while establishing the transaction lock'
printf 'pkg_was_locked=%s\npkg_identity=%s\n' "$pkg_was_locked" "$pkg_identity_before" \
    > "$state_directory/transaction.txt"

set -- "os-bind-rp-$candidate_plugin_version"
if [ "$bind_update_required" = yes ]
then
    set -- "bind920-$candidate_bind920_version" "bind-tools-$candidate_bind_tools_version" "$@"
fi
package_dry_run "$state_directory/pkg-install.dry-run.txt" \
    "$isolated_repository_directory" resolver-verified no "$@"
verify_archive_hashes
capture_recovery_packages
if [ "$bind_update_required" = no ]
then
    observe_retained_runtime before
fi
[ "$(installed_record pkg)" = "$pkg_identity_before" ] || \
    fail 'pkg identity changed before the verified package transaction'

transaction_started=yes
if [ "$bind_update_required" = yes ]
then
    verified_pkg install -y -r resolver-verified \
        "bind920-$candidate_bind920_version" "bind-tools-$candidate_bind_tools_version"
    # pkg can report success without applying a refused operation. Check R before replacing P.
    verify_installed_record "$candidate_bind920"
    verify_installed_record "$candidate_bind_tools"
    verify_installed_checksums bind920
    verify_installed_checksums bind-tools
    runtime_edges=$("$pkg_command" query -e '%n = bind920' '%dn|%do|%dv') || fail 'could not inspect installed BIND prerequisites'
    printf '%s\n' "$runtime_edges" | grep -Fxq "bind-tools|dns/bind-tools|$candidate_bind_tools_version" || \
        fail 'installed BIND does not depend on the verified tools package'
    "$pkg_command" check -s bind920 bind-tools
fi
verified_pkg install -y -r resolver-verified "os-bind-rp-$candidate_plugin_version"

remaining_official=$(installed_record os-bind) || fail 'could not inspect official plugin replacement'
[ -z "$remaining_official" ] || fail 'official os-bind remains installed after replacement'
verify_installed_record "$candidate_plugin"
if [ "$bind_update_required" = yes ]
then
    verify_installed_record "$candidate_bind920"
    verify_installed_record "$candidate_bind_tools"
else
    verify_installed_record "$bind920"
    verify_installed_record "$bind_tools"
    observe_retained_runtime after
fi
verify_installed_checksums os-bind-rp
if [ "$bind_update_required" = yes ]
then
    verify_installed_checksums bind-tools
    verify_installed_checksums bind920
fi
"$pkg_command" check -s bind-tools bind920 os-bind-rp
"$pkg_command" info -a > "$state_directory/packages.after.txt"
"$pkg_command" query '%n|%v|%o' > "$state_directory/package-identities.after.txt"

completed=yes
printf 'Installed %s using verified exact archives. Recovery state: %s\n' \
    "os-bind-rp-$candidate_plugin_version" "$state_directory" >&2
