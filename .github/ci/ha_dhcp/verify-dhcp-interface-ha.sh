#!/bin/sh
# Verify either a staged or published signed channel in a fresh FreeBSD VM.
set -eu
[ "$#" -eq 4 ] || { echo "usage: $0 <series> <expected-channel> <repository-url> <combined-catalogue>" >&2; exit 1; }
series=$1
expected=$2
url=$3
combined=$4
root=$(CDPATH='' cd -- "$(dirname -- "$0")/../../.." && pwd)
ci="$root/.github/ci"
: "${RP_UPSTREAM_METADATA:?trusted upstream metadata is required}"
python3 "$ci/ha_dhcp/dhcp_interface_ha_channel.py" validate --directory "$expected"
RP_OPNSENSE_SNAPSHOT=$(python3 "$ci/ha_dhcp/dhcp_interface_ha_release.py" snapshot --series "$series")
export RP_OPNSENSE_SNAPSHOT
"$ci/shared/setup-opnsense-repository.sh" "$series" >/dev/null
pkg update -f
python3 "$ci/shared/target_pkg.py" install "$root/.resolver-plugins/target-pkg.json" "$series"
pkg install -y -r OPNsense opnsense
python3 "$ci/shared/target_pkg.py" verify "$root/.resolver-plugins/target-pkg.json" "$series"
SSL_CERT_FILE=/usr/local/share/certs/ca-root-nss.crt
export SSL_CERT_FILE
case "$url" in
    https:*)
        attempt=1
        while ! python3 "$ci/shared/release_channel.py" verify-abi-endpoint --url "$url" --expected-channel "$combined"; do
            [ "$attempt" -lt 20 ] || exit 1
            sleep 30
            attempt=$((attempt + 1))
        done
        ;;
esac
python3 "$ci/shared/package_catalogue.py" verify --directory "$combined" --url "$url"
install -d -m 0750 /conf
if [ ! -e /conf/config.xml ]; then
    printf '%s\n' '<opnsense/>' > /conf/config.xml
    chmod 0640 /conf/config.xml
fi
install -d -m 0755 /usr/local/etc/pkg/keys /usr/local/etc/pkg/repos
install -m 0644 "$root/docs/package-repository/resolver-plugins.pub" /usr/local/etc/pkg/keys/resolver-plugins.pub
cat > /usr/local/etc/pkg/repos/resolver-plugins-dhcpha.conf <<CONFIG
resolver-plugins-dhcpha: {
  url: "$url",
  mirror_type: "none",
  signature_type: "pubkey",
  pubkey: "/usr/local/etc/pkg/keys/resolver-plugins.pub",
  enabled: yes
}
CONFIG
pkg update -f -r resolver-plugins-dhcpha
set -- "$expected"/os-dhcp-interface-ha-*.pkg
[ "$#" -eq 1 ]
archive=$1
python3 "$ci/shared/package_checksums.py" --pkg-command /usr/local/sbin/pkg-static "$archive"
identity=$(/usr/local/sbin/pkg-static query -F "$archive" '%n|%v|%o')
[ "$(pkg rquery -r resolver-plugins-dhcpha -e '%n = os-dhcp-interface-ha' '%n|%v|%o')" = "$identity" ]
/usr/local/sbin/pkg-static install -y -r resolver-plugins-dhcpha os-dhcp-interface-ha
[ "$(pkg query -e '%n = os-dhcp-interface-ha' '%n|%v|%o')" = "$identity" ]
# Check installed files against the exact staged archive, not just its own manifest.
/usr/local/sbin/pkg-static query -F "$archive" '%Fp|%Fs' > /tmp/dhcpha-expected-files
pkg query -e '%n = os-dhcp-interface-ha' '%Fp|%Fs' > /tmp/dhcpha-installed-files
sort /tmp/dhcpha-expected-files > /tmp/dhcpha-expected-sorted
sort /tmp/dhcpha-installed-files > /tmp/dhcpha-installed-sorted
cmp /tmp/dhcpha-expected-sorted /tmp/dhcpha-installed-sorted
pkg check -s os-dhcp-interface-ha
/usr/local/bin/php "$root/net/dhcp-interface-ha/tests/native/test_ui_routes.php"
python3 "$root/net/dhcp-interface-ha/tests/native/test_fresh_install.py"
python3 "$ci/shared/target_pkg.py" verify "$root/.resolver-plugins/target-pkg.json" "$series"
