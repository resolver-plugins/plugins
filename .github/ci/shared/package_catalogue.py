#!/usr/bin/env python3
"""Compose independent original BIND components and optional HA."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bind.shared import components as bind
from ha_dhcp import dhcp_interface_ha_channel as dhcp
from shared import metadata_profile, package_checksums, target_pkg
from shared import release_channel as releases
ROOT = Path(__file__).resolve().parents[3]
PUBLIC_KEY = ROOT / "docs/package-repository/resolver-plugins.pub"
PKG = "/usr/local/sbin/pkg-static"
COMPONENTS = ("bind_runtime", "bind_tools", "bind920", "bind_plugin", "dhcp")
CATALOGUE_FILES = bind.CATALOGUE_FILES
CATALOGUE_ASSETS = bind.CATALOGUE_ASSETS
CATALOGUE_ARCHIVES = {"data.pkg", "data.tzst", "packagesite.pkg", "packagesite.tzst"}
RECORD_FIELDS = {"current_tag", "snapshot_tag", "assets", "packages"}


def tag(component, series):
    if component in bind.KINDS:
        releases.channel_tag(series)
        return f"pkg-{series}-{component.replace('_', '-')}"
    if component != "dhcp":
        raise ValueError("invalid catalogue component")
    return dhcp.tags(series, "")[0]


def component_packages(directory, component, series):
    bind._files(directory)
    if component in bind.KINDS:
        data = bind.validate_component(directory, component, series)
        return [directory / name for name in data["packages"]]
    if component != "dhcp":
        raise ValueError("unsupported HA component")
    data = dhcp.validate(directory)
    if data["series"] != series:
        raise ValueError("component series differs")
    return [directory / f"{dhcp.NAME}-{data['plugin_version']}.pkg"]


def receipt(directory, component, series):
    if component in bind.KINDS:
        return bind.component_receipt(directory, component, series)
    packages = component_packages(directory, component, series)
    data = dhcp.validate(directory)
    current, snapshot = dhcp.tags(series, data["plugin_version"], data.get("build_fingerprint"))
    return dict(schema=1, component="dhcp", series=series, package_abi=data["package_creator"]["abi"],
                current_tag=current, snapshot_tag=snapshot, fingerprint=data.get("build_fingerprint"),
                assets=bind._files(directory), packages={path.name:releases.sha256(path) for path in packages})


def component_record(receipt):
    return {field: receipt[field] for field in RECORD_FIELDS}


def fetch(repository, component, series, output):
    if component in bind.KINDS:
        return bind.fetch_component(repository, component, series, output)
    if component != "dhcp":
        raise ValueError("invalid component")
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        current = releases.snapshot_release(repository, tag(component,series), root)
        if not current.existed:
            return None
        if current.draft:
            raise ValueError("registered HA current is unpublished")
        selected = receipt(current.directory,component,series)
        snapshot = releases.snapshot_release(repository,selected["snapshot_tag"],root)
        if (not snapshot.existed or snapshot.draft or receipt(snapshot.directory,component,series) != selected):
            raise ValueError("HA current differs from its published snapshot")
        shutil.copytree(snapshot.directory,output)
        return selected


def verify_packages(packages, url, target_metadata, upstream, *, role="reader", pkg_command=None):
    command = pkg_command or PKG
    target = target_pkg.load_target(target_metadata, json.loads(upstream.read_text())["series"], upstream=upstream, role=role)
    target_pkg.verify_target_pkg(target, command, pkg_static_path=Path(command))
    if any(releases.query_package(p, command)[0][3] != target.identity.abi for p in packages):
        raise ValueError('component package ABI differs from trusted reader')
    verify_repository(packages, url, pkg_command=command)


def verify_repository(packages, url, *, pkg_command, unsigned=False):
    """Authenticate an isolated catalogue and compare its exact fetched archives."""
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
            f'  mirror_type: "none", signature_type: "{ "none" if unsigned else "pubkey" }", enabled: yes,\n'
            f'  pubkey: {json.dumps(str(key))}\n}}\n')
        command = [pkg_command, '-o', f'REPOS_DIR={repos}', '-o', f'PKG_DBDIR={root / "db"}',
                   '-o', f'PKG_CACHEDIR={root / "cache"}']
        subprocess.run([*command, 'update', '-f', '-r', 'catalogue'], check=True, stdout=sys.stderr)
        identities = [releases.query_package(p, pkg_command)[0] for p in packages]
        expected = sorted('|'.join(identity) for identity in identities)
        observed = subprocess.check_output(
            [*command, 'rquery', '-U', '-a', '-r', 'catalogue', '%n|%v|%o|%q'], text=True).splitlines()
        if sorted(observed) != expected:
            raise ValueError('signed catalogue package identities differ from selected archives')
        names = [identity.split('|')[0] for identity in expected]
        subprocess.run([*command, 'fetch', '-U', '-y', '-r', 'catalogue', '-o', str(root / 'fetch'), *names], check=True, stdout=sys.stderr)
        for package in packages:
            fetched = list((root / 'fetch').rglob(package.name))
            if len(fetched) != 1 or releases.sha256(fetched[0]) != releases.sha256(package):
                raise ValueError('signed catalogue archive differs from selected bytes')



def authenticate_components(runtime, plugin, ha, *, series, reader_target, upstream,
                            approved_profile, runtime_approval, pkg_command):
    target = target_pkg.load_target(reader_target,series,upstream=upstream,role="reader")
    target_pkg.verify_target_pkg(target,pkg_command,pkg_static_path=Path(pkg_command))
    directories = {"bind_runtime":runtime,"bind_plugin":plugin}
    if ha is not None and ha.is_dir():
        directories["dhcp"] = ha
    packages, records = [], {}
    for component,directory in directories.items():
        selected = component_packages(directory,component,series)
        if component in bind.KINDS:
            bind.validate_component(directory,component,series,pkg_command=pkg_command)
        for path in selected:
            identity,_ = releases.query_package(path,pkg_command)
            if identity[3] != target.identity.abi:
                raise ValueError("component package ABI differs from selected series")
            if component == "dhcp" and identity != (dhcp.NAME,path.name.removeprefix(dhcp.NAME+"-").removesuffix(".pkg"),f"opnsense/{dhcp.NAME}",target.identity.abi):
                raise ValueError("HA archive identity differs")
            package_checksums.verify_archive(pkg_command,path)
        verify_packages(selected,directory.resolve().as_uri(),reader_target,upstream,pkg_command=pkg_command)
        packages.extend(selected)
        records[component] = component_record(receipt(directory,component,series))
    approved = bind.bind920_profile.load_profile(approved_profile)
    approval = bind.bind920_profile.approval_from_makefile(runtime_approval.read_text())
    target_upstream = metadata_profile.validate_profile(json.loads(upstream.read_text()), series)
    version=bind.bind920_profile.package_version(approved)
    if (approved["ports_commit"] != approval["ports_commit"] or version != approval["version"]
            or json.loads((runtime / "bind920-profile.json").read_text()) != approved
            or json.loads((runtime / "bind920-provenance.json").read_text())["freebsd_release"] != target_upstream["freebsd_release"]):
        raise ValueError("runtime differs from approved recipe or upstream target")
    requirement = bind.plugin_requirement_from_makefile((plugin / "plugin-package.mk").read_text())
    if bind.compare_native_versions(pkg_command,version,requirement["minimum_version"]) == "<":
        raise ValueError("runtime does not satisfy retained plugin dependency")
    if len({path.name for path in packages}) != len(packages):
        raise ValueError("component package filenames collide")
    return packages,records


def _stage(runtime, plugin, ha, output, *, series, key, creator_target, upstream,
           approved_profile, runtime_approval, stack):
    reader_target=creator_target.parent / "reader-target.json"
    bind.select_role(upstream,reader_target,series,"reader",PKG)
    packages,records=authenticate_components(runtime,plugin,ha,series=series,
        reader_target=reader_target,upstream=upstream,approved_profile=approved_profile,
        runtime_approval=runtime_approval,pkg_command=PKG)
    bind.select_role(upstream,creator_target,series,"creator",PKG)
    target=target_pkg.load_target(creator_target,series,upstream=upstream,role="creator")
    target_pkg.verify_target_pkg(target,PKG,pkg_static_path=Path(PKG))
    directories={"bind_runtime":runtime,"bind_plugin":plugin}
    if ha is not None and ha.is_dir():
        directories["dhcp"]=ha
    if key is None or subprocess.check_output(["openssl","pkey","-in",str(key),"-pubout"]) != PUBLIC_KEY.read_bytes():
        raise ValueError("signing key differs from committed public key")
    releases.stage_selected_repository(packages,output,key,PKG,[])
    shutil.copyfile(PUBLIC_KEY,output / "resolver-plugins.pub")
    for component,directory in directories.items():
        for path in directory.iterdir():
            if path.name not in records[component]["packages"] and path.name not in CATALOGUE_ARCHIVES:
                shutil.copyfile(path,output / f"{component}-{path.name}")
    shutil.copyfile(creator_target,output / "catalogue-creator-target.json")
    shutil.copyfile(upstream,output / "catalogue-upstream.json")
    data = dict(kind="bind-stack" if stack else "combined",schema=6 if stack else 2,series=series,
                package_abi=target.identity.abi,catalogue_creator=dict(package_creator=target.record(),creator_source=target.source_record()),
                components=records,assets=bind._files(output))
    if stack:
        data["packages"] = {path.name:releases.sha256(path) for path in packages}
    (output / "channel.json").write_text(json.dumps(data,sort_keys=True,indent=2)+"\n")
    return validate(output)


def stage_bind_stack(runtime,plugin,output,*,series,key,creator_target,upstream,approved_profile,runtime_approval):
    return _stage(runtime,plugin,None,output,series=series,key=key,creator_target=creator_target,upstream=upstream,
                  approved_profile=approved_profile,runtime_approval=runtime_approval,stack=True)


def stage(runtime,plugin,dhcp,output,*,series,key,creator_target,upstream,approved_profile,runtime_approval,
          owner=None,composition_output=None,repository=bind.REPOSITORY):
    data = _stage(runtime,plugin,dhcp,output,series=series,key=key,creator_target=creator_target,upstream=upstream,
                  approved_profile=approved_profile,runtime_approval=runtime_approval,stack=False)
    if owner is not None:
        if owner != "dhcp" or composition_output is None or composition_output.name != "composition-inputs.json":
            raise ValueError("HA composition requires its external signer seal path")
        root = composition_output.parent
        if output != root / "combined" or receipt(root / "current","dhcp",series) != receipt(dhcp,"dhcp",series):
            raise ValueError("HA artifact layout differs from original owned channel")
        records={component:receipt(directory,component,series) for component,directory in
                 (("bind_runtime",runtime),("bind_plugin",plugin),("dhcp",dhcp))}
        digest=bind.write_composition(root,component="dhcp",series=series,
            initialized=bind._initialized(repository,series,data["package_abi"]),components=records)
        composition=validate_composition(root,component="dhcp",series=series,composition_sha256=digest)
        bind.check_composition_peers(repository,composition)
        return dict(composition_sha256=digest)
    return data


def validate(directory):
    actual = bind._files(directory)
    data = json.loads((directory / "channel.json").read_text())
    fields = {"schema","kind","series","package_abi","catalogue_creator","components","assets"}
    if data.get("kind") == "bind-stack":
        fields.add("packages")
        schema = 6
    elif data.get("kind") == "combined":
        schema = 3 if data.get("schema") == 3 else 2
    else:
        raise ValueError("invalid composed catalogue kind")
    if (set(data) != fields or type(data["schema"]) is not int or data["schema"] != schema
            or not isinstance(data["components"], dict)
            or (not data["components"] or not set(data["components"]) <= set(COMPONENTS))
            or schema != 3 and set(data["components"]) not in ({"bind_runtime","bind_plugin"},{"bind_runtime","bind_plugin","dhcp"})
            or data["kind"] == "bind-stack" and "dhcp" in data["components"]
            or (directory / "resolver-plugins.pub").read_bytes() != PUBLIC_KEY.read_bytes()):
        raise ValueError("invalid composed catalogue identity")
    releases.validate_package_abi(data["package_abi"])
    assets = dict(actual); assets.pop("channel.json")
    if bind._hashes(data["assets"]) != assets:
        raise ValueError("composed catalogue assets differ")
    upstream = metadata_profile.validate_profile(json.loads((directory / "catalogue-upstream.json").read_text()),data["series"])
    creator,source = bind._role(json.loads((directory / "catalogue-creator-target.json").read_text()),upstream,"creator")
    if data["catalogue_creator"] != dict(package_creator=creator,creator_source=source) or creator["abi"] != data["package_abi"]:
        raise ValueError("catalogue original creator differs")
    expected = {}
    expected_assets = {"resolver-plugins.pub","catalogue-upstream.json","catalogue-creator-target.json"} | (CATALOGUE_ASSETS & set(assets))
    if schema == 3:
        from shared import repository_index
        index = repository_index.validate_inputs(directory, releases.sha256(directory / "index-inputs.json"), prefix="catalogue-")
        if data["components"] != repository_index.selected_records(index["components"]):
            raise ValueError("index package selection differs from sealed original receipts")
        expected_assets |= {"index-inputs.json", "catalogue-reader-target.json", "catalogue-tools-profile.json"}
    for component,record in data["components"].items():
        if not isinstance(record,dict) or set(record) != RECORD_FIELDS | ({"selected_packages"} if schema == 3 else set()):
            raise ValueError("invalid original composition record")
        if (directory / f"{component}-resolver-plugins.pub").read_bytes() != PUBLIC_KEY.read_bytes():
            raise ValueError("retained component key differs from committed public key")
        originals,packages = bind._hashes(record["assets"]),bind._hashes(record["packages"])
        selected = bind._hashes(record["selected_packages"]) if schema == 3 else packages
        if not selected or any(packages.get(name) != digest for name, digest in selected.items()):
            raise ValueError("index selection differs from original package bytes")
        for name,digest in selected.items():
            if name in expected or originals.get(name) != digest or actual.get(name) != digest:
                raise ValueError("composed package bytes differ from original provenance")
            expected[name] = digest
        for name,digest in originals.items():
            if name not in packages and name not in CATALOGUE_ARCHIVES:
                prefixed = f"{component}-{name}"
                if actual.get(prefixed) != digest:
                    raise ValueError("composed sidecar differs from original release")
                expected_assets.add(prefixed)
        channel = json.loads((directory / f"{component}-channel.json").read_text())
        if component in bind.KINDS:
            names = bind.RUNTIME_SIDECARS if component in bind.RUNTIME_KINDS else bind.PLUGIN_SIDECARS
            if component == "bind_plugin" and channel.get("schema") == 2:
                names = names | {"bind-build-dependency.json"}
            if "series-build-inputs.json" in channel["assets"]:
                names = names | {"series-build-inputs.json"}
            sidecars = {name:(directory / f"{component}-{name}").read_text() if name.endswith((".txt", ".mk"))
                        else json.loads((directory / f"{component}-{name}").read_text()) for name in names}
            bind.validate_component_record(channel,sidecars,packages,component,data["series"])
            component_creator = channel["package_creator"]
            version = bind.bind920_profile.package_version(sidecars["bind920-profile.json"]) if component in bind.RUNTIME_KINDS else channel["plugin_version"]
            tags = bind.component_tags(component,data["series"],version,channel["build_fingerprint"])
        else:
            build = dhcp.read_metadata(directory / "dhcp-build-metadata.txt")
            original = metadata_profile.validate_profile(json.loads((directory / "dhcp-upstream.json").read_text()),data["series"])
            inputs = json.loads((directory / "dhcp-series-build-inputs.json").read_text()) if channel.get("schema") == 3 else None
            component_creator = dhcp.validate_component(channel,build,original,inputs)
            tags = dhcp.tags(data["series"],channel["plugin_version"],channel.get("build_fingerprint"))
            if set(packages) != {f"{dhcp.NAME}-{channel['plugin_version']}.pkg"}:
                raise ValueError("unexpected composed HA package set")
        if (record["current_tag"],record["snapshot_tag"]) != tags or component_creator["abi"] != data["package_abi"]:
            raise ValueError("composition original identity differs")
        if channel["assets"] != {name:digest for name,digest in originals.items() if name != "channel.json"}:
            raise ValueError("original receipt differs from original manifest")
    expected_assets.update(expected)
    if (set(assets) != expected_assets or "meta.conf" not in assets or not CATALOGUE_FILES.intersection(assets)
            or {name for name in assets if name.endswith(".pkg") and name not in CATALOGUE_FILES
                } != set(expected)
            or data["kind"] == "bind-stack" and data["packages"] != expected):
        raise ValueError("composed catalogue package or sidecar set differs")
    return data


def verify(directory,url,reader_target,upstream):
    data = validate(directory)
    packages = [directory / name for record in data["components"].values() for name in record.get("selected_packages", record["packages"])]
    verify_packages(packages,url,reader_target,upstream)


def validate_composition(repository_root,*,component,series,composition_sha256):
    composition=bind.load_composition(repository_root,component=component,series=series,
                                      composition_sha256=composition_sha256)
    owned=repository_root / ("current" if component=="dhcp" else "component/current")
    if receipt(owned,component,series)!=composition["components"][component]:
        raise ValueError("owned channel differs from sealed composition")
    if composition["schema"] == 2:
        return composition
    if composition["components"]["bind_plugin"] is not None:
        data=validate(repository_root / "combined")
        expected={name:component_record(record) for name,record in composition["components"].items() if record is not None}
        if data["components"]!=expected:
            raise ValueError("combined feed differs from sealed composition")
    elif (repository_root / "combined").exists():
        raise ValueError("bootstrap composition cannot expose combined feed")
    return composition


def seal_owned(repository_root, component, series):
    """Seal one HA publication without consulting any peer package."""
    if component != "dhcp":
        raise ValueError("BIND controllers seal their own channels")
    current = repository_root / "current"
    shutil.copytree(current, repository_root / "snapshot")
    selected = receipt(current, component, series)
    path = repository_root / "composition-inputs.json"
    path.write_text(json.dumps(dict(schema=2, component=component, series=series,
                                   components={component: selected}, native_dependencies={}),
                               sort_keys=True, indent=2) + "\n")
    digest = releases.sha256(path)
    validate_composition(repository_root, component=component, series=series, composition_sha256=digest)
    return digest


def publish(repository,repository_root,recovery,*,component,series,composition_sha256):
    composition=validate_composition(repository_root,component=component,series=series,
                                     composition_sha256=composition_sha256)
    bind.check_composition_peers(repository,composition,owned_must_match=True)
    if composition["components"]["bind_plugin"] is None:
        return
    releases.publish_abi_channel(repository,repository_root / "combined",recovery)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command",choices=("fetch","stage","stage-stack","validate","verify","verify-owned","seal-owned","publish"))
    parser.add_argument("--repository",default="resolver-plugins/repository")
    parser.add_argument("--component",choices=COMPONENTS)
    parser.add_argument("--owner",choices=("dhcp",))
    parser.add_argument("--composition-sha256")
    parser.add_argument("--series")
    for name in ("directory","output","runtime-channel","plugin-channel","dhcp-channel","creator-target","reader-target","upstream","approved-profile","runtime-approval","key","recovery","repository-root","composition-output"):
        parser.add_argument("--"+name,type=Path)
    parser.add_argument("--url")
    args = parser.parse_args()
    if args.command == "seal-owned":
        print(seal_owned(args.repository_root, args.component, args.series))
        return
    elif args.command == "verify-owned":
        selected = component_packages(args.directory, args.component, args.series)
        verify_packages(selected, args.url, args.reader_target, args.upstream)
        return
    elif args.command == "fetch":
        result = fetch(args.repository,args.component,args.series,args.output)
    elif args.command in ("stage","stage-stack"):
        if args.approved_profile is None or args.runtime_approval is None:
            parser.error("composition requires --approved-profile and --runtime-approval")
        kwargs = dict(series=args.series,key=args.key,creator_target=args.creator_target,upstream=args.upstream,
                      approved_profile=args.approved_profile,runtime_approval=args.runtime_approval)
        result = stage_bind_stack(args.runtime_channel,args.plugin_channel,args.output,**kwargs) if args.command == "stage-stack" else stage(
            args.runtime_channel,args.plugin_channel,args.dhcp_channel,args.output,owner=args.owner,
            composition_output=args.composition_output,repository=args.repository,**kwargs)
    elif args.command == "verify":
        result = verify(args.directory,args.url,args.reader_target,args.upstream)
    elif args.command == "publish":
        if any(value is None for value in (args.repository_root,args.component,args.series,args.composition_sha256,args.recovery)):
            parser.error("publish requires --repository-root, --component, --series, --composition-sha256 and --recovery")
        result = publish(args.repository,args.repository_root,args.recovery,component=args.component,series=args.series,
                         composition_sha256=args.composition_sha256)
    elif args.repository_root is not None:
        if any(value is None for value in (args.component,args.series,args.composition_sha256)):
            parser.error("sealed validation requires --component, --series and --composition-sha256")
        result = validate_composition(args.repository_root,component=args.component,series=args.series,
                                      composition_sha256=args.composition_sha256)
    else:
        result = validate(args.directory)
    if result is not None: print(json.dumps(result,sort_keys=True))


if __name__ == "__main__":
    main()
