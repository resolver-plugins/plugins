#!/bin/sh

set -eu

[ "$1" = '-q' ]
[ "$2" = '-o' ]
[ -z "${FETCH_URL_LOG:-}" ] || printf '%s\n' "$4" > "$FETCH_URL_LOG"
cp "$FETCH_ARCHIVE" "$3"
