#!/bin/sh
set -u

plugin=os-ha-dhcp-interface
marker=/var/db/os-dhcp-interface-ha/repository-reconcile.pending
log=/var/log/os-dhcp-interface-ha-reconcile.log
provider=/usr/local/opnsense/scripts/firmware/repos/ResolverPluginsHA.sh

fail() {
    printf '%s %s\n' "$(date)" "$1" >> "${log}"
    exit 1
}

[ -f "${marker}" ] || exit 0

series=$(opnsense-version -a) || fail "cannot read the series"
printf '%s\n' "${series}" | grep -Eq '^[0-9]+\.[0-9]+$' || fail "invalid series ${series}"

"${provider}" || fail "repository provider failed"
pkg-static update -f -r resolver-plugins || fail "catalogue update failed"

candidate=$(pkg-static rquery -r resolver-plugins '%o %v %q' "${plugin}") || fail "candidate query failed"
set -- ${candidate}
[ "$#" -eq 3 ] && [ "$1" = "opnsense/${plugin}" ] || fail "unexpected candidate origin"
case "$2" in "${series}"_*) ;; *) fail "candidate version $2 is not ${series}" ;; esac
[ "$3" = "$(pkg-static config ABI)" ] || fail "candidate ABI $3 does not match"
version=$2

plan=$(pkg-static install -n "${plugin}-${version}")
[ "$?" -le 1 ] || fail "transaction plan failed"
! printf '%s\n' "${plan}" | grep -Eq 'REMOVED:|DOWNGRADED:' || fail "plan removes or downgrades packages"

pkg-static install -y "${plugin}-${version}" || fail "install failed"

[ "$(pkg-static query '%v' "${plugin}")" = "${version}" ] || fail "installed version is not ${version}"
rm -f "${marker}"
