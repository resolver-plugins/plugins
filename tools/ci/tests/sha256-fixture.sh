#!/bin/sh

set -eu

[ "$#" -eq 1 ]
[ -n "${SHA256_VALUE:-}" ]
printf '%s\n' "$SHA256_VALUE"
