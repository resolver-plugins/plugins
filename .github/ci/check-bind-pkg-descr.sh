#!/bin/sh

set -eu

if [ "$#" -ne 2 ]; then
    echo "usage: $0 <base> <head>" >&2
    exit 2
fi

base=$1
head=$2
base=$(git merge-base "$base" "$head")
description_changed=false
runtime_changed=false
changed_paths=$(git diff --no-renames --name-only "$base" "$head" -- dns/bind)

while IFS= read -r path; do
    case "$path" in
        dns/bind/pkg-descr)
            description_changed=true
            ;;
        dns/bind/src/*|dns/bind/+*)
            runtime_changed=true
            ;;
        dns/bind/Makefile)
            if git diff --unified=0 "$base" "$head" -- "$path" |
                awk '
                    /^[+-]PLUGIN_[A-Z0-9_]+[[:space:]]*[?+:!]?=/ &&
                    $0 !~ /^[+-]PLUGIN_REVISION[[:space:]]*=/ { changed = 1 }
                    END { exit !changed }
                '; then
                runtime_changed=true
            fi
            ;;
    esac
done <<EOF
$changed_paths
EOF

if [ "$runtime_changed" = true ] && [ "$description_changed" = false ]; then
    echo "dns/bind/pkg-descr must change with publishable BIND changes" >&2
    exit 1
fi
