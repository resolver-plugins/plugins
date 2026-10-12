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


def make_bind_component(kind, directory, *, series, creator, source_commit,
                        control_commit, profile, version, recipe_sha256=None,
                        runtime_receipt=None, policy_path=None):
    """Two concrete original component records with independently saved bytes."""
    ci = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(ci))
    from bind.shared import components
    if kind not in ('bind_runtime', 'bind_plugin'):
        raise ValueError('invalid fixture component')
    directory.mkdir()
    write_json(directory / 'upstream.json', profile)
    write_resolved_target(directory / 'creator-target.json', profile, creator=creator)
    source = dict(release=profile['tools_tag'], repository='https://github.com/opnsense/ports.git',
                  commit='4' * 40, path=creator['origin'])
    manifest = dict(schema=1, kind=kind.replace('_', '-'), series=series,
                    package_abi=creator['abi'], package_creator=creator, creator_source=source,
                    control_commit=control_commit)
    if kind == 'bind_runtime':
        distversion, _, revision = version.partition('_')
        pin = dict(BIND_PROFILE, distversion=distversion, portrevision=int(revision or 0))
        records = bind_records(version)
        inputs = dict(schema=4, series=series, freebsd_release=profile['freebsd_release'],
                      architecture='x86_64', bind_profile=pin, build_recipe_sha256=recipe_sha256 or 'e' * 64,
                      package_creator=creator)
        # Independent canonical hashing: do not manufacture the oracle using production.
        fingerprint = hashlib.sha256(json.dumps(inputs, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        provenance = {key: inputs[key] for key in inputs if key != 'bind_profile'}
        provenance.update(fingerprint=fingerprint, packages=records)
        write_json(directory / 'bind920-profile.json', pin)
        write_json(directory / 'bind920-provenance.json', provenance)
        manifest.update(profile_commit=source_commit, build_fingerprint=fingerprint)
        archives = [(name, version, record['origin']) for name, record in records.items()]
    else:
        if policy_path is None:
            policy = json.loads((ci.parents[1] / '.resolver-plugins/bind-compatibility.json').read_text())
            write_json(directory / 'bind-compatibility.json', policy)
        else:
            shutil.copyfile(policy_path, directory / 'bind-compatibility.json')
        inputs = dict(schema=1, source_commit=source_commit, series=series,
                      package_abi=creator['abi'], freebsd_release=profile['freebsd_release'],
                      package_creator=creator, build_recipe_sha256=recipe_sha256 or 'd' * 64,
                      compatibility_policy_sha256=hashlib.sha256((directory / 'bind-compatibility.json').read_bytes()).hexdigest())
        write_json(directory / 'plugin-build-inputs.json', inputs)
        fingerprint = hashlib.sha256(json.dumps(inputs, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        runtime_version = '9.20.26_2'
        runtime_inputs = dict(schema=4, series=series, freebsd_release=profile['freebsd_release'],
                             architecture='x86_64', bind_profile=BIND_PROFILE,
                             build_recipe_sha256='e' * 64, package_creator=creator)
        runtime_fingerprint = hashlib.sha256(json.dumps(runtime_inputs, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        runtime_packages = {record['filename']: hashlib.sha256(json.dumps(
            dict(name=name, version=runtime_version, origin=record['origin'], abi=creator['abi']), sort_keys=True).encode()).hexdigest()
            for name, record in bind_records(runtime_version).items()}
        if runtime_receipt is not None:
            runtime_version = next(name.removeprefix('bind920-').removesuffix('.pkg') for name in runtime_receipt['packages'] if name.startswith('bind920-'))
            runtime_fingerprint = runtime_receipt['fingerprint']
            runtime_packages = runtime_receipt['packages']
        write_build_metadata(directory / 'build-metadata.txt', series=series,
            upstream_commit=profile['upstream_commit'], core_commit=profile['core_commit'],
            opnsense_core_commit=profile['core_commit'], source_commit=source_commit,
            tools_tag=profile['tools_tag'], freebsd_release=profile['freebsd_release'],
            pkg_abi=creator['abi'], pkg_creator=creator['version'], creator_recipe_path=creator['origin'],
            creator_release=profile['tools_tag'], creator_ports_commit=source['commit'], bind920=runtime_version)
        manifest.update(source_commit=source_commit, plugin_version=version, build_fingerprint=fingerprint,
            built_against_runtime=dict(version=runtime_version, fingerprint=runtime_fingerprint,
                snapshot_tag=f'pkg-{series}-bind-runtime-{runtime_version}-{runtime_fingerprint}',
                packages=runtime_packages))
        archives = [('os-bind-rp', version, 'opnsense/os-bind-rp')]
    for name, archive_version, origin in archives:
        (directory / f'{name}-{archive_version}.pkg').write_bytes(json.dumps(
            dict(name=name, version=archive_version, origin=origin, abi=creator['abi']), sort_keys=True).encode())
    manifest['packages'] = {f'{name}-{archive_version}.pkg': hashlib.sha256(
        (directory / f'{name}-{archive_version}.pkg').read_bytes()).hexdigest()
        for name, archive_version, _ in archives}
    for name in ('meta', 'meta.conf', 'data.pkg', 'data.tzst', 'packagesite.pkg', 'packagesite.tzst'):
        (directory / name).write_bytes(b'synthetic signed catalogue')
    (directory / 'resolver-plugins.pub').write_bytes(components.PUBLIC_KEY.read_bytes())
    manifest['assets'] = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in directory.iterdir()}
    write_json(directory / 'channel.json', manifest)
    return directory
