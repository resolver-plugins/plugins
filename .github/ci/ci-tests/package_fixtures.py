"""Synthetic package inputs shared by the CI boundary tests (stdlib only)."""
from common_imports import *


BIND_PROFILE = {
    'ports_repository': 'https://github.com/freebsd/freebsd-ports.git',
    'ports_commit': '1' * 40,
    'makefile_sha256': '2' * 64,
    'distinfo_sha256': '3' * 64,
    'distversion': '9.20.26',
    'portrevision': 2,
}


def write_json(path, data):
    path.write_text(json.dumps(data), encoding='utf-8')
    return path


def package_creator(abi='FreeBSD:15:amd64', **changes):
    return dict(name='pkg', version='2.3.1_1', origin='ports-mgmt/pkg', abi=abi,
                filename='pkg-2.3.1_1.pkg', sha256='a' * 64,
                pkg_static_sha256='b' * 64) | changes


def bind_records(version='9.20.26_2'):
    return {name: dict(name=name, version=version, origin=f'dns/{name}',
                       filename=f'{name}-{version}.pkg')
            for name in ('bind-tools', 'bind920')}


def write_build_metadata(path, **changes):
    fields = dict(series='26.7', uname='FreeBSD fixture 15.1', pkg_abi='FreeBSD:15:amd64',
                  bind920='9.20.26_1', bind_source='resolver', opnsense='26.7',
                  opnsense_core_commit='2' * 40, source_commit='a' * 40,
                  upstream_commit='1' * 40, core_commit='2' * 40,
                  tools_tag='26.7.1', freebsd_release='15.1',
                  pkg_creator='2.3.1_1', pkg_creator_sha256='a' * 64)
    fields.update(changes)
    path.write_text(''.join(f'{key}={value}\n' for key, value in fields.items()))
    return fields


def bind_channel_inputs(directory):
    """Unsigned input files for staging; assertions calculate expected bytes separately."""
    directory.mkdir()
    records = bind_records('9.20.26_1')
    for name in [record['filename'] for record in records.values()] + ['os-bind-rp-26.7_1.pkg']:
        (directory / name).write_bytes(name.encode())
    write_json(directory / 'bind920-provenance.json', dict(
        schema=2, fingerprint='f' * 64, series='26.7', freebsd_release='15.1',
        architecture='x86_64', package_creator=package_creator(), packages=records))
    write_build_metadata(directory / 'build-metadata.txt')
    return directory


def write_target_metadata(path, creator=None):
    creator = creator or package_creator()
    return write_json(path, dict(schema=1, series={
        '26.1': dict(creator, abi='FreeBSD:14:amd64'),
        '26.7': dict(creator, abi='FreeBSD:15:amd64')}))
