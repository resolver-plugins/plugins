#!/bin/sh

if [ -n "${PKG_CALL_LOG:-}" ]
then
    printf '%s\n' "$*" >> "$PKG_CALL_LOG"
fi

case "$1" in
    update) exit 0 ;;
    fetch)
        [ "$2" = -U ] && [ "$3" = -y ] && [ "$4" = -r ] && [ "$5" = OPNsense ] || exit 2
        [ "$6" = -o ] && [ "$8" = pkg-2.3.1_1 ] || exit 2
        mkdir -p "$7/All"
        printf '%s\n' 'fixture target package archive' > "$7/All/pkg-2.3.1_1.pkg"
        ;;
    add)
        [ "$2" = -f ] || exit 2
        if [ -n "${PKG_STATIC_PATH:-}" ]
        then
            cp "${0%/*}/pkg-static-fixture.sh" "$PKG_STATIC_PATH"
            chmod 0755 "$PKG_STATIC_PATH"
            sha256sum "$PKG_STATIC_PATH" > "$PKG_STATIC_PATH.manifest"
        fi
        ;;
    lock)
        case "$2" in
            -y) : > "${PKG_LOCK_MARKER:?}" ;;
            -l) if [ -f "${PKG_LOCK_MARKER:?}" ]; then printf '%s\n' 'pkg-2.3.1_1'; fi ;;
            *) exit 2 ;;
        esac
        ;;
    unlock) rm -f "${PKG_LOCK_MARKER:?}" ;;
    which) [ -f "${PKG_STATIC_PATH:?}.manifest" ] || exit 2
        [ "$3" = "$PKG_STATIC_PATH" ] || exit 2
        printf '%s\n' 'pkg-2.3.1_1' ;;
    check) sha256sum -c "${PKG_STATIC_PATH:?}.manifest" >/dev/null ;;
    install)
        if [ "${3:-}" = python3 ] && [ -n "${PYTHON_COMMAND:-}" ]
        then
            printf '%s\n' '#!/bin/sh' 'exec python3 "$@"' > "$PYTHON_COMMAND"
            chmod 0755 "$PYTHON_COMMAND"
        fi
        exit 0
        ;;
    query)
        case "$*" in
        *'%k pkg'*) if [ -f "${PKG_LOCK_MARKER:?}" ]; then printf '1\n'; else printf '0\n'; fi ;;
        *'%n = pkg'*|*'pkg-2.3.1_1.pkg'*)
            printf '%s\n' 'pkg|2.3.1_1|ports-mgmt/pkg|FreeBSD:14:amd64'
            ;;
        *)
        if [ "${4:-}" = '%n\t%v\t%o' ]
        then
            case "$3" in
                *bind-tools*) printf 'bind-tools\t%s\t%s\n' "${PKG_BIND_TOOLS_VERSION:-9.20.26}" "${PKG_BIND_TOOLS_ORIGIN:-dns/bind-tools}" ;;
                *) printf 'bind920\t%s\t%s\n' "${PKG_BIND_VERSION:-9.20.26}" "${PKG_BIND_ORIGIN:-dns/bind920}" ;;
            esac
        else
            printf '%s\n' '9.20.26'
        fi
        ;;
        esac
        ;;
    rquery)
        case "$*" in
            *'%n = pkg'*) printf '%s\n' 'pkg|2.3.1_1|ports-mgmt/pkg|FreeBSD:14:amd64' ;;
            *) printf '%s\n' '26.1.11_10' ;;
        esac ;;

    config) printf '%s\n' 'FreeBSD:14:amd64' ;;
    version) printf '%s\n' "${PKG_VERSION_COMPARISON:-=}" ;;
    *) exit 2 ;;
esac
