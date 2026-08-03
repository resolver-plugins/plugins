#!/bin/sh

set -eu

[ "$1" = '-r' ] || exit 2
root_directory=$2
shift 2
action=$1
shift
state_directory="$root_directory/pkg-switch-state"
mkdir -p "$state_directory"

last_argument() {
    package_argument=
    for package_argument
    do
        :
    done
    printf '%s\n' "$package_argument"
}

case "$action" in
    register)
        [ "$1" = '-t' ] && [ "$2" = '-M' ] && [ -f "$3" ] || exit 2
        case "$(cat "$3")" in
            *'"name":"os-bind"'*) touch "$state_directory/os-bind" ;;
        esac
        ;;
    add)
        package=$(last_argument "$@")
        case "$(basename "$package")" in
            os-bind-rp-*.pkg)
                if [ -f "$state_directory/os-bind" ]
                then
                    touch "$state_directory/collision-checked"
                    printf '%s\n' \
                        'pkg-static: os-bind-rp conflicts with os-bind (installs files into the same place)' >&2
                    exit 1
                fi
                [ -f "$state_directory/collision-checked" ] || exit 3
                touch "$state_directory/os-bind-rp"
                ;;
            os-bind-*.pkg)
                touch "$state_directory/os-bind"
                ;;
            bind920-*.pkg)
                touch "$state_directory/bind920"
                ;;
            *) exit 2 ;;
        esac
        ;;
    delete)
        [ "$1" = '-y' ] && [ "$2" = '-f' ] && [ "$3" = 'os-bind' ] || exit 2
        rm -f "$state_directory/os-bind"
        ;;
    info)
        [ "$1" = '-q' ] || exit 2
        [ ! -f "$state_directory/os-bind" ] || printf '%s\n' 'os-bind-1.34_3'
        [ ! -f "$state_directory/os-bind-rp" ] || printf '%s\n' 'os-bind-rp-1.36_1'
        ;;
    *) exit 2 ;;
esac
