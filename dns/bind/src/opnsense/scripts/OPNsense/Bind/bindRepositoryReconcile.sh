#!/bin/sh
set -u

plugin=os-bind-rp
marker=/var/db/os-bind-rp/repository-reconcile.pending
log=/var/log/os-bind-rp-reconcile.log
provider=/usr/local/opnsense/scripts/firmware/repos/ResolverPlugins.sh

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
case "$2" in "${series}"_*|"${series}".*) ;; *) fail "candidate version $2 is not ${series}" ;; esac
[ "$3" = "$(pkg-static config ABI)" ] || fail "candidate ABI $3 does not match"
version=$2

! pkg-static info -e os-bind || fail "official os-bind is installed"

plan=$(pkg-static install -n "${plugin}-${version}")
[ "$?" -le 1 ] || fail "transaction plan failed"
! printf '%s\n' "${plan}" | grep -Eq 'REMOVED:|DOWNGRADED:' || fail "plan removes or downgrades packages"

bind_before=$(pkg-static query '%v' bind920)
pkg-static install -y "${plugin}-${version}" || fail "install failed"

if [ "$(pkg-static query '%v' bind920)" != "${bind_before}" ]; then
    configctl template reload OPNsense/Bind || fail "BIND template reload failed"
    configctl bind restart || fail "BIND restart failed"
fi

[ "$(pkg-static query '%v' "${plugin}")" = "${version}" ] || fail "installed version is not ${version}"
rm -f "${marker}"
