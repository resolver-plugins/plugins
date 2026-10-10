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


def creator_identity(abi='FreeBSD:15:amd64', **changes):
    return dict(name='pkg', version='2.3.1_1', origin='ports-mgmt/pkg', abi=abi) | changes


def write_resolved_target(path, profile, *, role='creator', creator=None, ports_commit='4' * 40):
    release = profile['tools_tag'] if role == 'creator' else {'26.1': '26.1.11', '26.7': '26.7'}[profile['series']]
    creator = creator or creator_identity(f"FreeBSD:{profile['freebsd_release'].split('.')[0]}:amd64")
    return write_json(path, dict(schema=1, role=role, series=profile['series'], release=release,
        recipe=dict(repository='https://github.com/opnsense/ports.git', commit=ports_commit,
                    path=creator['origin']), package_creator=creator))


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
                  pkg_creator='2.3.1_1', creator_release='26.7.1',
                  creator_ports_commit='4' * 40, creator_recipe_path='ports-mgmt/pkg')
    fields.update(changes)
    path.write_text(''.join(f'{key}={value}\n' for key, value in fields.items()))
    return fields


def bind_channel_inputs(directory):
    """Unsigned input files for staging; assertions calculate expected bytes separately."""
    directory.mkdir()
    records = bind_records('9.20.26_1')
    for name in [record['filename'] for record in records.values()] + ['os-bind-rp-26.7_1.pkg']:
        (directory / name).write_bytes(name.encode())
    provenance = dict(schema=4, fingerprint='f' * 64, series='26.7', freebsd_release='15.1',
                      architecture='x86_64', package_creator=creator_identity(),
                      build_recipe_sha256='e' * 64, packages=records)
    write_json(directory / 'bind920-provenance.json', provenance)
    write_build_metadata(directory / 'build-metadata.txt')
    return directory


def complete_bind_channel(directory, *, creator=None):
    """Current channel with a recorded recipe independent of today's build wrapper."""
    bind_channel_inputs(directory)
    provenance = json.loads((directory / 'bind920-provenance.json').read_text())
    metadata = dict(line.split('=', 1) for line in (directory / 'build-metadata.txt').read_text().splitlines())
    creator = creator or creator_identity()
    metadata.update(pkg_creator=creator['version'], creator_recipe_path=creator['origin'])
    provenance['package_creator'] = creator
    channel = dict(schema=5, series='26.7', plugin_version='26.7_1', source_commit='a' * 40,
        package_creator=creator, package_abi=creator['abi'], control_commit='c' * 40,
        creator_source=dict(release=metadata['creator_release'],
            repository='https://github.com/opnsense/ports.git', commit=metadata['creator_ports_commit'],
            path=metadata['creator_recipe_path']),
        build={key: metadata[key] for key in ('upstream_commit', 'core_commit', 'tools_tag', 'freebsd_release')},
        bind={key: provenance[key] for key in ('fingerprint', 'freebsd_release', 'architecture')},
        packages={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.glob('*.pkg')})
    write_json(directory / 'channel.json', channel)
    write_json(directory / 'bind920-provenance.json', provenance)
    (directory / 'build-metadata.txt').write_text(''.join(f'{key}={value}\n' for key, value in metadata.items()))
    for name in ('resolver-plugins.pub', 'meta.conf', 'packagesite.pkg'):
        (directory / name).write_bytes(b'trusted key' if name.endswith('.pub') else b'catalogue')
    return directory
