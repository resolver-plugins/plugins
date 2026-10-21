"""Validate the selected internal path and read native IPv4 route inventory."""
import ipaddress
import json

from inventory import DEVICE_RE
from core import DHCPHA_DEVICE


def configuration(root, managed, carrier):
    local = root.find('OPNsense/DhcpInterfaceHaLocal')
    result = {'enabled': False, 'interface': '', 'vip': '', 'device': '',
              'source_address': '', 'error': None}
    if local is None:
        return result
    result.update(enabled=local.findtext('standby_enabled', '0') == '1',
                  interface=local.findtext('standby_interface', '').strip(),
                  vip=local.findtext('standby_vip', '').strip())
    if not result['enabled']:
        return result
    try:
        name = result['interface']
        if not DEVICE_RE.fullmatch(name) or name == managed:
            raise ValueError('Select an enabled internal interface distinct from the managed DHCP interface.')
        interface = root.find('interfaces/' + name)
        if interface is None or interface.findtext('enable', '0') != '1':
            raise ValueError('The selected internal interface must be enabled.')
        device = interface.findtext('if', '')
        if not DEVICE_RE.fullmatch(device) or device in (carrier, DHCPHA_DEVICE, 'lo0'):
            raise ValueError('The selected internal interface cannot use the reserved WAN carrier or loopback.')
        address = ipaddress.IPv4Address(interface.findtext('ipaddr', ''))
        prefix = int(interface.findtext('subnet', ''))
        if not 1 <= prefix <= 30 or address.is_multicast or address.is_unspecified or address.is_loopback:
            raise ValueError('The internal path requires a usable static IPv4 subnet with room for both nodes and their VIP.')
        network = ipaddress.IPv4Network((str(address), prefix), strict=False)
        vip = ipaddress.IPv4Address(result['vip'])
        if (vip not in network or vip == address or vip in (network.network_address, network.broadcast_address)
                or vip.is_multicast or vip.is_unspecified or vip.is_loopback):
            raise ValueError('Choose a distinct CARP VIP on the selected internal IPv4 subnet.')
        matches = [v for v in root.findall('virtualip/vip')
                   if v.findtext('mode') == 'carp' and v.findtext('disabled', '0') != '1'
                   and v.findtext('interface') == name and v.findtext('subnet') == str(vip)]
        if len(matches) != 1:
            raise ValueError('Choose an existing IPv4 CARP VIP on the selected internal interface.')
        gateways = root.findall('OPNsense/Gateways/gateway_item') + root.findall('gateways/gateway_item')
        managed_gateways = {managed.upper() + '_DHCP'}
        for gateway in gateways:
            if gateway.findtext('interface') == managed:
                managed_gateways.add(gateway.findtext('name', ''))
            elif (gateway.findtext('disabled', '0') != '1' and gateway.findtext('ipprotocol') == 'inet'
                  and gateway.findtext('defaultgw', '0') == '1'):
                raise ValueError('Another IPv4 upstream gateway conflicts with standby Internet access.')
        for route in root.findall('staticroutes/route'):
            if (route.findtext('disabled', '0') != '1' and route.findtext('network') == '0.0.0.0/0'
                    and route.findtext('gateway') not in managed_gateways):
                raise ValueError('An unrelated static IPv4 default route conflicts with standby Internet access.')
        result.update(device=device, source_address=str(address), vip=str(vip))
    except (ValueError, TypeError) as error:
        result['error'] = str(error)
    return result


def default_route(output):
    families = json.loads(output)['statistics']['route-information']['route-table']['rt-family']
    if not isinstance(families, list) or len(families) != 1 or families[0].get('address-family') != 'Internet':
        raise ValueError('Invalid native IPv4 route inventory.')
    entries = families[0].get('rt-entry', [])
    if not isinstance(entries, list) or any(not isinstance(row, dict) for row in entries):
        raise ValueError('Invalid native IPv4 route entries.')
    defaults = [row for row in entries if row.get('destination') == 'default']
    if len(defaults) > 1:
        raise ValueError('Multiple IPv4 default routes are unsupported.')
    if not defaults:
        return None
    row = defaults[0]
    if not isinstance(row.get('gateway'), str) or not DEVICE_RE.fullmatch(row.get('interface-name', '')):
        raise ValueError('Incomplete native IPv4 default route.')
    return {'gateway': row['gateway'], 'netif': row['interface-name']}
