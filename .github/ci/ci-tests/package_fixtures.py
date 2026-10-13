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
PLUGIN_PACKAGE = '''PLUGIN_NAME= bind-rp
PLUGIN_DEPENDS= bind920
PLUGIN_DEPEND_FORMULA= bind920 >= 9.20.26
PLUGIN_DEPEND_FORMULA_DEPENDS= bind920
PLUGIN_CONFLICTS= bind
PLUGIN_MANIFEST_DEPENDS= opnsense: { version: "26.1.11_10", origin: "opnsense/opnsense" }
'''
PLUGIN_PRE_INSTALL = r'''# Native dependency records do not enforce an installed core's minimum version.
set -eu

bind_package_pkg=${BIND_PACKAGE_PKG_COMMAND:-/usr/local/sbin/pkg-static}
bind_package_minimum_core=$(printf '%s\n' '%%PLUGIN_MANIFEST_DEPENDS%%' |
    sed -n 's/^opnsense: { version: "\([^"]*\)", origin: "opnsense\/opnsense" }$/\1/p')
[ -n "${bind_package_minimum_core}" ] || {
    printf '%s\n' 'os-bind-rp minimum OPNsense version is missing' >&2
    exit 1
}
bind_package_core=$("${bind_package_pkg}" query -e '%n = opnsense' '%v')
bind_package_comparison=$("${bind_package_pkg}" version -t "${bind_package_core}" "${bind_package_minimum_core}")
case "${bind_package_comparison}" in
    '='|'>') ;;
    *)
        printf 'os-bind-rp requires OPNsense %s or later (installed %s)\n' \
            "${bind_package_minimum_core}" "${bind_package_core}" >&2
        exit 1
        ;;
esac

# A native archive conflict list is an installed-package database relationship.
# Replacement uses shared-file resolution; this read-only check fences name drift.
bind_package_installed=$("${bind_package_pkg}" query '%n')
for bind_package_conflict in %%PLUGIN_CONFLICTS%%; do
    for bind_package_name in ${bind_package_installed}; do
        if [ "${bind_package_name}" = "${bind_package_conflict}" ]; then
            printf 'os-bind-rp cannot coexist with installed %s\n' \
                "${bind_package_conflict}" >&2
            exit 1
        fi
    done
done
'''
BIND_COLLISION_PATH = '/usr/local/etc/inc/plugins.inc.d/bind.inc'


def plugin_manifest(definition=PLUGIN_PACKAGE, *, development=False, payload=b'fixture bind.inc\n'):
    """Independent source fixture and the actual ASCII native script representation."""
    fields = dict(re.findall(r'^(PLUGIN_\w+)=\s*(.*)$', definition, re.M))
    core = re.search(r'version: "([^"]+)"', fields['PLUGIN_MANIFEST_DEPENDS'])[1]
    conflicts = 'os-bind os-bind-devel ' + ('os-bind-rp' if development else 'os-bind-rp-devel')
    script = PLUGIN_PRE_INSTALL.replace('%%PLUGIN_MANIFEST_DEPENDS%%', fields['PLUGIN_MANIFEST_DEPENDS'])
    script = script.replace('%%PLUGIN_CONFLICTS%%', conflicts)
    name = 'os-bind-rp-devel' if development else 'os-bind-rp'
    return dict(name=name, origin='opnsense/' + name, dep_formula=fields['PLUGIN_DEPEND_FORMULA'],
                deps={'opnsense': {'version': core, 'origin': 'opnsense/opnsense'}},
                files={BIND_COLLISION_PATH: '1$' + hashlib.sha256(payload).hexdigest()},
                scripts={'pre-install': script.replace('%', '%25').strip(' \t\r\n\f\v')})


def runtime_approval(version='9.20.26_2', ports_commit='1' * 40):
    return f'RP_BIND_VERSION= {version}\nRP_BIND_PORTS_COMMIT= {ports_commit}\n'


def fixture_version_comparison(left, right):
    """Order the suite's numeric package versions, including native revisions."""
    def key(version):
        base, _, revision = version.partition('_')
        return tuple(map(int, base.split('.'))), int(revision or '0')
    left, right = key(left), key(right)
    return '<' if left < right else '>' if left > right else '='


def make_ports_recipe(directory, version='9.20.26_2'):
    """A real immutable Ports tree with independent expected Git-blob hashes."""
    from git_fixtures import init_repository, commit
    repository = init_repository(directory)
    distversion, _, revision = version.partition('_')
    makefile = (f'PORTNAME= bind\nDISTVERSION= {distversion}\n'
                f'.if defined(BIND_TOOLS_SLAVE)\nPORTREVISION= {revision or 0}\n'
                f'.else\nPORTREVISION= {revision or 0}\n.endif\n').encode()
    distinfo = f'SHA256 (bind-{distversion}.tar.xz) = {"c" * 64}\n'.encode()
    selected = commit(repository, {'dns/bind920/Makefile': makefile.decode(),
                                   'dns/bind920/distinfo': distinfo.decode()}, 'approved recipe')
    profile = dict(ports_repository=BIND_PROFILE['ports_repository'], ports_commit=selected,
                   makefile_sha256=hashlib.sha256(makefile).hexdigest(),
                   distinfo_sha256=hashlib.sha256(distinfo).hexdigest(),
                   distversion=distversion, portrevision=int(revision or 0))
    return repository, runtime_approval(version, selected), profile


def make_tools_repository(directory, profile):
    from git_fixtures import init_repository, commit, git
    repository = init_repository(directory)
    commit(repository, {f"config/{profile['series']}/build.conf": f"OS?= {profile['freebsd_release']}\n"}, 'tools environment')
    git(repository, 'tag', profile['tools_tag'])
    return repository


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
                        runtime_receipt=None, package_definition=None, bind_profile=None):
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
        pin = dict(bind_profile or BIND_PROFILE, distversion=distversion, portrevision=int(revision or 0))
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
        if package_definition is None:
            (directory / 'plugin-package.mk').write_text(PLUGIN_PACKAGE)
        else:
            shutil.copyfile(package_definition, directory / 'plugin-package.mk')
        inputs = dict(schema=1, source_commit=source_commit, series=series,
                      package_abi=creator['abi'], freebsd_release=profile['freebsd_release'],
                      package_creator=creator, build_recipe_sha256=recipe_sha256 or 'd' * 64)
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
        record = dict(name=name, version=archive_version, origin=origin, abi=creator['abi'])
        if name == 'os-bind-rp':
            record.update(plugin_manifest((directory / 'plugin-package.mk').read_text()))
        (directory / f'{name}-{archive_version}.pkg').write_bytes(json.dumps(record, sort_keys=True).encode())
    manifest['packages'] = {f'{name}-{archive_version}.pkg': hashlib.sha256(
        (directory / f'{name}-{archive_version}.pkg').read_bytes()).hexdigest()
        for name, archive_version, _ in archives}
    for name in ('meta', 'meta.conf', 'data.pkg', 'data.tzst', 'packagesite.pkg', 'packagesite.tzst'):
        (directory / name).write_bytes(b'synthetic signed catalogue')
    (directory / 'resolver-plugins.pub').write_bytes(components.PUBLIC_KEY.read_bytes())
    manifest['assets'] = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in directory.iterdir()}
    write_json(directory / 'channel.json', manifest)
    return directory
