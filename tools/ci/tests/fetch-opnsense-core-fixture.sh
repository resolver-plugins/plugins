#!/bin/sh

set -eu

[ "$1" = '-q' ]
[ "$2" = '-o' ]
cp "$FETCH_ARCHIVE" "$3"
