"""Synthetic HA component channels with real asset checksums."""
import json

import dhcp_interface_ha_channel as channel


def make_ha_channel(directory, *, version='0.2_30', source='a' * 40, profile='b' * 40):
    directory.mkdir()
    metadata = dict(source_commit=source, profile_commit=profile, series='26.7',
                    repository_snapshot='26.7.4', plugin_version=version)
    (directory / 'build-metadata.txt').write_text(''.join(f'{key}={value}\n' for key, value in metadata.items()))
    for name in (f'{channel.NAME}-{version}.pkg', 'upstream.json', 'meta.conf', 'packagesite.pkg'):
        (directory / name).write_text('fixture\n')
    (directory / 'resolver-plugins.pub').write_bytes(channel.PUBLIC_KEY.read_bytes())
    data = dict(schema=1, source_commit=source, profile_commit=profile, series='26.7',
                plugin_version=version, package_creator=channel.target_pkg.load_target(channel.TARGET, '26.7').record(),
                assets=channel.releases.directory_checksums(directory))
    (directory / 'channel.json').write_text(json.dumps(data))
    return directory
