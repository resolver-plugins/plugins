#!/bin/sh

repository_config=${OS_BIND_RP_REPOSITORY_CONFIG:-/usr/local/etc/pkg/repos/resolver-plugins.conf}
pending_marker=${OS_BIND_RP_PENDING_MARKER:-/var/db/os-bind-rp/repository-reconcile.pending}
opnsense_version_command=${OS_BIND_RP_OPNSENSE_VERSION_COMMAND:-/usr/local/sbin/opnsense-version}
public_key=/usr/local/etc/pkg/keys/resolver-plugins.pub
repository_base='https://resolver-plugins.github.io/repository/pkg'

warn()
{
    printf '%s\n' \
        "WARNING: resolver-plugins.conf was not changed; manual migration: set its URL to ${repository_base}/\${ABI}/${series:-<series>}/latest after reviewing the custom repository configuration." >&2
}

repository_body()
{
    url=$1
    printf '%s\n' 'resolver-plugins: {'
    printf '  url: "%s",\n' "${url}"
    printf '%s\n' '  mirror_type: "none",'
    printf '%s\n' '  signature_type: "pubkey",'
    printf '  pubkey: "%s",\n' "${public_key}"
    printf '%s\n' '  enabled: yes'
    printf '%s\n' '}'
}

managed_body()
{
    url=$1
    temporary=$2
    repository_body "${url}" > "${temporary}"
}

extract_url()
{
    sed -n 's/^[[:space:]]*url: "\([^"]*\)",[[:space:]]*$/\1/p' "$1"
}

series=$(${opnsense_version_command} -a 2>/dev/null || true)
case "${series}" in
    [0-9]*.[0-9]*) ;;
    *) warn; exit 0 ;;
esac
case "${series}" in
    *[!0-9.]*) warn; exit 0 ;;
    *.*.*|.*|*.) warn; exit 0 ;;
esac

target_url="${repository_base}/\${ABI}/${series}/latest"

if [ -L "${repository_config}" ] || [ ! -f "${repository_config}" ]
then
    warn
    exit 0
fi

case "${repository_config}" in
    */*)
        repository_directory=${repository_config%/*}
        repository_name=${repository_config##*/}
        ;;
    *)
        repository_directory=.
        repository_name=${repository_config}
        ;;
esac

temporary=$(mktemp "${repository_directory}/.${repository_name}.XXXXXX") || {
    warn
    exit 0
}
trap 'rm -f "${temporary}"' EXIT HUP INT TERM

repository_body "${target_url}" > "${temporary}"
if cmp "${repository_config}" "${temporary}" >/dev/null 2>&1
then
    rm -f "${temporary}"
    trap - EXIT HUP INT TERM
    exit 0
fi

repository_managed=no
for managed_url in \
    "https://github.com/resolver-plugins/repository/releases/download/pkg-26.1" \
    "https://github.com/resolver-plugins/repository/releases/download/pkg-26.7" \
    "${repository_base}/\${ABI}/latest"
do
    managed_body "${managed_url}" "${temporary}"
    if cmp "${repository_config}" "${temporary}" >/dev/null 2>&1
    then
        repository_managed=yes
        break
    fi
done

url=$(extract_url "${repository_config}")
case "${url}" in
    "${repository_base}/\${ABI}/"[0-9]*.[0-9]*"/latest")
        managed_body "${url}" "${temporary}"
        if cmp "${repository_config}" "${temporary}" >/dev/null 2>&1
        then
            repository_managed=yes
        fi
        ;;
esac

if [ "${repository_managed}" != yes ]
then
    warn
    exit 0
fi

if cp -p "${repository_config}" "${temporary}" && \
    repository_body "${target_url}" > "${temporary}" && \
    mv -f "${temporary}" "${repository_config}"
then
    trap - EXIT HUP INT TERM
    marker_directory=${pending_marker%/*}
    [ "${marker_directory}" = "${pending_marker}" ] || mkdir -p "${marker_directory}"
    : > "${pending_marker}" || true
    exit 0
fi

warn
exit 0
