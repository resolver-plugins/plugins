"""Synthetic HA component channels with complete recorded provenance."""
from common_imports import *
from git_fixtures import upstream_profile
from package_fixtures import creator_identity, write_json
from ha_dhcp import dhcp_interface_ha_channel as channel


def make_ha_channel(directory, *, version='0.2_30', source='a' * 40, profile='b' * 40, creator=None,
                    snapshot='26.7.4', series='26.7', inputs=None, control='c' * 40, upstream_metadata=None):
    directory.mkdir()
    upstream = upstream_metadata or upstream_profile(series)
    creator = creator or creator_identity()
    metadata = dict(source_commit=source, profile_commit=profile, series=series,
        repository_snapshot=snapshot, plugin_version=version, freebsd_release=upstream['freebsd_release'],
        core_commit=upstream['core_commit'], uname='FreeBSD fixture', pkg_abi=creator['abi'],
        opnsense=series+'.4', pkg_creator=creator['version'], creator_release=upstream['tools_tag'],
        creator_ports_commit='4' * 40, creator_recipe_path=creator['origin'])
    data = dict(schema=2, source_commit=source, profile_commit=profile, series=series,
                plugin_version=version, package_creator=creator,
                creator_source=dict(release=upstream['tools_tag'],
                    repository='https://github.com/opnsense/ports.git', commit='4' * 40, path=creator['origin']))
    (directory / 'build-metadata.txt').write_text(''.join(f'{key}={value}\n' for key, value in metadata.items()))
    write_json(directory / 'upstream.json', upstream)
    if inputs is not None:
        from shared import series_builds
        digest = series_builds.fingerprint(inputs)
        metadata.update(control_commit=control, build_fingerprint=digest)
        (directory / 'build-metadata.txt').write_text(''.join(f'{key}={value}\n' for key,value in metadata.items()))
        data.update(schema=3, control_commit=control, build_fingerprint=digest)
        write_json(directory / series_builds.INPUT_FILE, inputs)
    for name in (f'{channel.NAME}-{version}.pkg', 'meta.conf', 'packagesite.pkg'):
        (directory / name).write_text('fixture\n')
    (directory / 'resolver-plugins.pub').write_bytes(channel.PUBLIC_KEY.read_bytes())
    data['assets'] = channel.releases.directory_checksums(directory)
    write_json(directory / 'channel.json', data)
    return directory
