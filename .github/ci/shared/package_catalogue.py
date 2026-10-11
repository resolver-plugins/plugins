#!/usr/bin/env python3
"""Combine independently released BIND and HA DHCP packages for the shared feed."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ha_dhcp import dhcp_interface_ha_channel as dhcp
from shared import metadata_profile, package_checksums, target_pkg
from shared import release_channel as releases

ROOT = Path(__file__).resolve().parents[3]
PUBLIC_KEY = ROOT / 'docs/package-repository/resolver-plugins.pub'
PKG = '/usr/local/sbin/pkg-static'
COMPONENTS = ('bind', 'dhcp')
CATALOGUE_FILES = {'data.pkg', 'packagesite.pkg'}


def tag(component, series):
    return releases.channel_tag(series) if component == 'bind' else dhcp.tags(series, '')[0]


def component_packages(directory, component, series):
    """Keep each component's existing provenance and package-set checks."""
    if component == 'bind':
        releases.validate_channel_directory(directory)
        data = json.loads((directory / 'channel.json').read_text())
        packages = releases.select_channel_packages(directory)
    else:
        data = dhcp.validate(directory)
        packages = [directory / f"{dhcp.NAME}-{data['plugin_version']}.pkg"]
    if (data['series'] != series or 'package_creator' not in data
            or (directory / 'resolver-plugins.pub').read_bytes() != PUBLIC_KEY.read_bytes()
            or {p.name for p in packages} != {p.name for p in directory.glob('*.pkg') if p.name not in CATALOGUE_FILES}):
        raise ValueError('component series, creator provenance, key or package set differs from trusted inputs')
    return packages


def fetch(repository, component, series, output):
    if component == 'dhcp' and series != '26.7':
        return
    with tempfile.TemporaryDirectory() as temporary:
        snapshot = releases.snapshot_release(repository, tag(component, series), Path(temporary))
        if not snapshot.existed:
            if component == 'bind':
                raise ValueError('publish the BIND component before adding HA DHCP to the shared feed')
            return
        if snapshot.draft:
            raise ValueError('cannot combine an unpublished component')
        component_packages(snapshot.directory, component, series)
        shutil.copytree(snapshot.directory, output)


def verify_packages(packages, url, target_metadata, upstream, *, role="reader"):
    target = target_pkg.load_target(target_metadata, json.loads(upstream.read_text())["series"], upstream=upstream, role=role)
    target_pkg.verify_target_pkg(target, PKG, pkg_static_path=Path(PKG))
    """Verify signatures, the complete catalogue and exact fetched archive bytes."""
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        repos = root / 'repos'
        repos.mkdir()
        (root / 'db').mkdir()
        key = root / 'repository.pub'
        key.write_bytes(PUBLIC_KEY.read_bytes())
        (repos / 'catalogue.conf').write_text(
            'catalogue: {\n'
            f'  url: {json.dumps(url)},\n'
            '  mirror_type: "none", signature_type: "pubkey", enabled: yes,\n'
            f'  pubkey: {json.dumps(str(key))}\n}}\n')
        command = [PKG, '-o', f'REPOS_DIR={repos}', '-o', f'PKG_DBDIR={root / "db"}',
                   '-o', f'PKG_CACHEDIR={root / "cache"}']
        subprocess.run([*command, 'update', '-f', '-r', 'catalogue'], check=True)
        identities = [releases.query_package(p, PKG)[0] for p in packages]
        if any(identity[3] != target.identity.abi for identity in identities):
            raise ValueError('component package ABI differs from trusted reader')
        expected = sorted('|'.join(identity) for identity in identities)
        observed = subprocess.check_output(
            [*command, 'rquery', '-U', '-a', '-r', 'catalogue', '%n|%v|%o|%q'], text=True).splitlines()
        if sorted(observed) != expected:
            raise ValueError('signed catalogue package identities differ from selected archives')
        names = [identity.split('|')[0] for identity in expected]
        subprocess.run([*command, 'fetch', '-U', '-y', '-r', 'catalogue', '-o', str(root / 'fetch'), *names], check=True)
        for package in packages:
            fetched = list((root / 'fetch').rglob(package.name))
            if len(fetched) != 1 or releases.sha256(fetched[0]) != releases.sha256(package):
                raise ValueError('signed catalogue archive differs from selected bytes')


def stage(bind, dhcp_channel, output, series, key, target_metadata, upstream):
    target = target_pkg.load_target(target_metadata, series, upstream=upstream, role="creator")
    target_pkg.verify_target_pkg(target, 'pkg')
    if subprocess.check_output(['openssl', 'pkey', '-in', str(key), '-pubout']) != PUBLIC_KEY.read_bytes():
        raise ValueError('signing key does not match the committed public key')
    components = {'bind': bind}
    if dhcp_channel.is_dir():
        components['dhcp'] = dhcp_channel
    packages, records = [], {}
    for component, directory in components.items():
        selected = component_packages(directory, component, series)
        if component == 'bind':
            releases.validate_channel_package_manifests(selected, directory / releases.PROVENANCE_NAME, PKG, series)
        for package in selected:
            identity, _ = releases.query_package(package, PKG)
            if identity[3] != target.identity.abi:
                raise ValueError('component package ABI differs from selected series')
            if component == 'dhcp' and identity != (dhcp.NAME, package.name.removeprefix(dhcp.NAME + '-').removesuffix('.pkg'),
                                                   f'opnsense/{dhcp.NAME}', target.identity.abi):
                raise ValueError('HA DHCP package identity differs from its component')
            package_checksums.verify_archive(PKG, package)
        verify_packages(selected, directory.resolve().as_uri(), target_metadata, upstream, role="creator")
        packages.extend(selected)
        records[component] = {'tag': tag(component, series), 'assets': releases.directory_checksums(directory),
                              'packages': {p.name: releases.sha256(p) for p in selected}}
    releases.stage_selected_repository(packages, output, key, PKG, [])
    shutil.copyfile(PUBLIC_KEY, output / 'resolver-plugins.pub')
    # Keep provenance readable without colliding build-metadata.txt filenames.
    for component, directory in components.items():
        for name in ('channel.json', 'build-metadata.txt', 'bind920-provenance.json', 'upstream.json'):
            if (directory / name).is_file():
                shutil.copyfile(directory / name, output / f'{component}-{name}')
    data = dict(kind='combined', schema=1, series=series, package_abi=target.identity.abi,
                components=records, assets=releases.directory_checksums(output))
    (output / 'channel.json').write_text(json.dumps(data, sort_keys=True, indent=2) + '\n')
    validate(output)


def validate(directory):
    data = json.loads((directory / 'channel.json').read_text())
    if (set(data) != {'kind', 'schema', 'series', 'package_abi', 'components', 'assets'}
            or data['kind'] != 'combined' or data['schema'] != 1
            or set(data['components']) not in ({'bind'}, {'bind', 'dhcp'})
            or ('dhcp' in data['components'] and data['series'] != '26.7')
            or releases.PACKAGE_ABI_PATTERN.fullmatch(data['package_abi']) is None
            or (directory / 'resolver-plugins.pub').read_bytes() != PUBLIC_KEY.read_bytes()):
        raise ValueError('invalid combined catalogue identity')
    actual = releases.directory_checksums(directory)
    actual.pop('channel.json')
    if actual != data['assets'] or any(not path.is_file() for path in directory.iterdir()):
        raise ValueError('combined catalogue assets differ from recorded bytes')
    expected = {}
    for component, record in data['components'].items():
        if set(record) != {'tag', 'assets', 'packages'} or record['tag'] != tag(component, data['series']):
            raise ValueError('invalid combined component identity')
        allowed_names = ('bind-tools-', 'bind920-', 'os-bind-rp-') if component == 'bind' else (dhcp.NAME + '-',)
        if (len(record['packages']) != len(allowed_names)
                or any(sum(name.startswith(prefix) for name in record['packages']) != 1 for prefix in allowed_names)):
            raise ValueError('unexpected combined component package set')
        for name, digest in record['packages'].items():
            if name in expected or record['assets'].get(name) != digest or actual.get(name) != digest:
                raise ValueError('combined package bytes differ from component provenance')
            expected[name] = digest
        for name in ('channel.json', 'build-metadata.txt', 'bind920-provenance.json' if component == 'bind' else 'upstream.json'):
            if name not in record['assets'] or actual.get(f'{component}-{name}') != record['assets'][name]:
                raise ValueError('combined component metadata differs from original release')
        channel = json.loads((directory / f'{component}-channel.json').read_text())
        if channel.get('series') != data['series'] or 'package_creator' not in channel:
            raise ValueError('combined component series or creator is inconsistent')
        if component == 'bind':
            metadata = releases.read_build_metadata(directory / 'bind-build-metadata.txt')
            provenance = json.loads((directory / 'bind-bind920-provenance.json').read_text())
            releases.validate_bind_component(channel, provenance, metadata)
            creator = target_pkg.normalize_creator(channel['package_creator'])
            if channel['packages'] != record['packages']:
                raise ValueError('combined BIND package lineage is inconsistent')
        else:
            # Parse the prefixed sidecar directly; its original catalogue is not this catalogue.
            metadata = dhcp.read_metadata(directory / 'dhcp-build-metadata.txt')
            upstream = metadata_profile.load_profile(directory / 'dhcp-upstream.json', data['series'])
            creator = dhcp.validate_component(channel, metadata, upstream)
            for name, digest in record['packages'].items():
                if channel['assets'].get(name) != digest:
                    raise ValueError('combined HA package lineage is inconsistent')
        if creator['abi'] != data['package_abi']:
            raise ValueError('combined component ABI is inconsistent')
    if ({p.name for p in directory.glob('*.pkg') if p.name not in CATALOGUE_FILES} != set(expected)
            or 'meta.conf' not in actual or not CATALOGUE_FILES.intersection(actual)):
        raise ValueError('combined catalogue is incomplete or contains extra packages')
    return data


def publish(repository, directory, recovery):
    data = validate(directory)
    # Both parent workflows use the same concurrency group. Recheck sources too:
    # a retry or an external publisher must not replace a newer component.
    with tempfile.TemporaryDirectory() as temporary:
        for component in COMPONENTS:
            if component == 'dhcp' and data['series'] != '26.7':
                continue
            snapshot = releases.snapshot_release(repository, tag(component, data['series']), Path(temporary))
            record = data['components'].get(component)
            if record is None:
                if snapshot.existed:
                    raise RuntimeError('a new component appeared after catalogue staging')
            elif (not snapshot.existed or snapshot.draft
                  or releases.directory_checksums(snapshot.directory) != record['assets']):
                raise RuntimeError('component changed after catalogue staging; rebuild the combined catalogue')
    releases.publish_abi_channel(repository, directory, recovery)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['fetch', 'stage', 'validate', 'verify', 'publish'])
    parser.add_argument('--repository', default='resolver-plugins/repository')
    parser.add_argument('--component', choices=COMPONENTS)
    parser.add_argument('--series')
    parser.add_argument('--directory', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--bind-channel', type=Path)
    parser.add_argument('--dhcp-channel', type=Path)
    parser.add_argument('--key', type=Path)
    parser.add_argument('--url')
    parser.add_argument('--recovery', type=Path)
    parser.add_argument('--target-pkg-metadata', type=Path)
    parser.add_argument('--reader-target', type=Path)
    parser.add_argument('--upstream', type=Path)
    args = parser.parse_args()
    if args.command == 'fetch':
        fetch(args.repository, args.component, args.series, args.output)
    elif args.command == 'stage':
        stage(args.bind_channel, args.dhcp_channel, args.output, args.series, args.key, args.target_pkg_metadata, args.upstream)
    elif args.command == 'publish':
        publish(args.repository, args.directory, args.recovery)
    else:
        data = validate(args.directory)
        if args.command == 'verify':
            packages = [args.directory / name for record in data['components'].values() for name in record['packages']]
            target = target_pkg.load_target(args.reader_target, data['series'], upstream=args.upstream, role="reader")
            if target.identity.abi != data['package_abi']:
                raise ValueError('combined catalogue ABI differs from trusted reader')
            verify_packages(packages, args.url, args.reader_target, args.upstream)
