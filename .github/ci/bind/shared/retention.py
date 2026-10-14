#!/usr/bin/env python3
"""Bounded published history with original build-time runtime references."""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import tempfile

from bind.shared import components as bind
from shared import package_catalogue as catalogue, release_channel as releases, target_pkg


def snapshot_kind(tag, series):
    if not isinstance(tag,str):
        return None
    prefix=f"pkg-{series}-"
    if not tag.startswith(prefix):
        return None
    suffix=tag.removeprefix(prefix)
    digest=r"[0-9a-f]{64}"
    if re.fullmatch(rf"bind-runtime-9\.20\.[0-9]+(?:_[0-9]+)?-{digest}",suffix):
        return "bind_runtime"
    if re.fullmatch(rf"bind-plugin-{re.escape(series)}_[1-9][0-9]*-{digest}",suffix):
        return "bind_plugin"
    if not suffix.startswith("os-bind-rp-"):
        return None
    suffix=suffix.removeprefix("os-bind-rp-")
    if "-plugin-" in suffix:
        return "stack" if re.fullmatch(rf"{re.escape(series)}_[1-9][0-9]*-bind-{digest}-plugin-{digest}",suffix) else None
    version,separator,fingerprint=suffix.rpartition("-bind-")
    if separator:
        return "old_stack" if releases.PACKAGE_VERSION_PATTERN.fullmatch(version) and releases.SHA256_PATTERN.fullmatch(fingerprint) else None
    return "old_stack" if releases.PACKAGE_VERSION_PATTERN.fullmatch(suffix) else None


def component_manifest(data, kind, series):
    extra={"profile_commit"} if kind=="bind_runtime" else {"source_commit","plugin_version","built_against_runtime"}
    if (not isinstance(data,dict) or set(data)!=bind.COMMON_FIELDS | extra
            or type(data["schema"]) is not int or data["schema"]!=1
            or data["kind"]!=kind.replace("_","-") or data["series"]!=series):
        raise ValueError("invalid published component history")
    bind._commit(data["control_commit"])
    releases.validate_package_abi(data["package_abi"])
    creator=target_pkg.normalize_creator(data["package_creator"])
    target_pkg.validate_source(data["creator_source"],creator,series)
    assets,packages=bind._hashes(data["assets"]),bind._hashes(data["packages"])
    sidecars=bind.RUNTIME_SIDECARS if kind=="bind_runtime" else bind.PLUGIN_SIDECARS
    if "series-build-inputs.json" in assets:
        sidecars = sidecars | {"series-build-inputs.json"}
    if (creator["abi"]!=data["package_abi"] or not sidecars | {"resolver-plugins.pub","meta.conf"} <= set(assets)
            or not bind.CATALOGUE_FILES.intersection(assets)
            or not set(assets) <= sidecars | {"resolver-plugins.pub"} | bind.CATALOGUE_ASSETS | set(packages)
            or assets["resolver-plugins.pub"]!=releases.sha256(bind.PUBLIC_KEY)
            or any(assets.get(name)!=digest for name,digest in packages.items())):
        raise ValueError("invalid original history asset map")
    if kind=="bind_runtime":
        bind._commit(data["profile_commit"])
        names=[name for name in packages if name.startswith("bind920-")]
        if len(names)!=1:
            raise ValueError("invalid historical runtime pair")
        version=names[0].removeprefix("bind920-").removesuffix(".pkg")
        if set(packages)!={f"{name}-{version}.pkg" for name in ("bind-tools","bind920")}:
            raise ValueError("invalid historical runtime pair")
    else:
        bind._commit(data["source_commit"])
        version=data["plugin_version"]
        bind._runtime_reference(data["built_against_runtime"],series)
        if set(packages)!={f"os-bind-rp-{version}.pkg"}:
            raise ValueError("invalid historical plugin archive")
    return bind.component_tags(kind,series,version,data["build_fingerprint"])[1]


def stack_receipts(data, series):
    fields={"schema","kind","series","package_abi","catalogue_creator","components","packages","assets"}
    if (not isinstance(data,dict) or set(data)!=fields or type(data["schema"]) is not int or data["schema"]!=6
            or data["kind"]!="bind-stack" or data["series"]!=series
            or not isinstance(data["components"],dict) or set(data["components"])!=set(bind.LEGACY_KINDS)
            or not isinstance(data["catalogue_creator"],dict)
            or set(data["catalogue_creator"])!={"package_creator","creator_source"}):
        raise ValueError("invalid published paired history")
    creator=target_pkg.normalize_creator(data["catalogue_creator"]["package_creator"])
    target_pkg.validate_source(data["catalogue_creator"]["creator_source"],creator,series)
    if creator["abi"]!=data["package_abi"]:
        raise ValueError("historical catalogue ABI differs")
    assets=bind._hashes(data["assets"])
    if (not {"resolver-plugins.pub","catalogue-upstream.json","catalogue-creator-target.json","meta.conf"} <= set(assets)
            or not bind.CATALOGUE_FILES.intersection(assets)
            or assets["resolver-plugins.pub"]!=releases.sha256(bind.PUBLIC_KEY)):
        raise ValueError("invalid historical catalogue assets")
    receipts,packages={},{}
    expected_assets={"resolver-plugins.pub","catalogue-upstream.json","catalogue-creator-target.json"} | (bind.CATALOGUE_ASSETS & set(assets))
    for kind,record in data["components"].items():
        if not isinstance(record,dict) or set(record)!=catalogue.RECORD_FIELDS:
            raise ValueError("invalid original paired history receipt")
        receipt=dict(schema=1,component=kind,series=series,package_abi=data["package_abi"],
                     fingerprint=record["snapshot_tag"].rsplit("-",1)[-1],**record)
        bind.validate_receipt(receipt,kind,series)
        if receipt["assets"].get("resolver-plugins.pub")!=releases.sha256(bind.PUBLIC_KEY):
            raise ValueError("invalid retained history key")
        for name,digest in receipt["assets"].items():
            if name in receipt["packages"]:
                if name in packages or assets.get(name)!=digest:
                    raise ValueError("invalid historical payload reference")
                packages[name]=digest
            elif name not in catalogue.CATALOGUE_ARCHIVES and assets.get(f"{kind}-{name}")!=digest:
                raise ValueError("invalid historical sidecar reference")
            elif name not in catalogue.CATALOGUE_ARCHIVES:
                expected_assets.add(f"{kind}-{name}")
        receipts[kind]=receipt
    if data["packages"]!=packages or set(assets)!=expected_assets | set(packages):
        raise ValueError("historical stack package map differs")
    return receipts


def paired_tag(data, series):
    receipts=stack_receipts(data,series)
    plugin=receipts["bind_plugin"]
    version=next(name.removeprefix("os-bind-rp-").removesuffix(".pkg") for name in plugin["packages"])
    return bind.stack_tags(series,version,receipts["bind_runtime"]["fingerprint"],plugin["fingerprint"])[1]


def retention_plan(records, current_components, current_stack, *, keep=5):
    if type(keep) is not int or keep<1 or not isinstance(current_components,dict):
        raise ValueError("invalid retention inputs")
    runtime=current_components.get("bind_runtime")
    if not isinstance(runtime,dict):
        raise ValueError("registered runtime is required for retention")
    series=runtime["series"]
    protected=set()
    for kind in bind.LEGACY_KINDS:
        receipt=current_components.get(kind)
        if receipt is not None:
            bind.validate_receipt(receipt,kind,series)
            protected.add(receipt["snapshot_tag"])
    history={}
    for record in records:
        if (not isinstance(record,dict) or set(record)!={"tag","created_at","draft","manifest"}
                or type(record["draft"]) is not bool or not isinstance(record["created_at"],str)):
            raise ValueError("invalid Release history record")
        kind=snapshot_kind(record["tag"],series)
        if kind is None or record["draft"]:
            continue
        if record["tag"] in history:
            raise ValueError("duplicate published snapshot")
        manifest=record["manifest"]
        if kind in bind.LEGACY_KINDS:
            expected=component_manifest(manifest,kind,series)
        elif kind=="stack":
            expected=paired_tag(manifest,series)
        else:
            if manifest is not None:
                raise ValueError("old stack must remain opaque")
            expected=record["tag"]
        if record["tag"]!=expected:
            raise ValueError("historical manifest differs from snapshot tag")
        history[record["tag"]]=(kind,record)
    def newest(kinds):
        return [tag for tag,(kind,record) in sorted(history.items(),
                key=lambda item:(item[1][1]["created_at"],item[0]),reverse=True) if kind in kinds]
    stacks=set(newest({"stack","old_stack"})[:keep])
    if current_stack is not None:
        current_tag=paired_tag(current_stack,series)
        stacks.add(current_tag)
    elif current_components.get("bind_plugin") is not None:
        raise ValueError("registered plugin requires complete current stack")
    for tag in stacks:
        if tag not in history:
            raise ValueError("required current stack history is missing")
        kind,record=history[tag]
        if kind=="stack":
            for peer,receipt in stack_receipts(record["manifest"],series).items():
                protected.add(receipt["snapshot_tag"])
                original=history.get(receipt["snapshot_tag"])
                if (original is None or original[0]!=peer
                        or original[1]["manifest"]["assets"]!={name:digest for name,digest in receipt["assets"].items() if name!="channel.json"}
                        or original[1]["manifest"]["packages"]!=receipt["packages"]):
                    raise ValueError("retained stack original component history is missing or inconsistent")
    for tag in protected:
        if tag not in history or history[tag][0] not in bind.LEGACY_KINDS:
            raise ValueError("required current component history is missing")
    for receipt in current_components.values():
        if receipt is not None and receipt["component"] in bind.LEGACY_KINDS:
            original=history[receipt["snapshot_tag"]][1]["manifest"]
            if (original["assets"]!={name:digest for name,digest in receipt["assets"].items() if name!="channel.json"}
                    or original["packages"]!=receipt["packages"]):
                raise ValueError("current component history differs from registered receipt")
    protected.update([tag for tag in newest({"bind_plugin"}) if tag not in protected][:keep])
    for tag in list(protected):
        if history[tag][0]!="bind_plugin":
            continue
        plugin=history[tag][1]["manifest"]
        reference=plugin["built_against_runtime"]
        original=history.get(reference["snapshot_tag"])
        if (original is None or original[0]!="bind_runtime"
                or original[1]["manifest"]["build_fingerprint"]!=reference["fingerprint"]
                or original[1]["manifest"]["packages"]!=reference["packages"]
                or original[1]["manifest"]["package_abi"]!=plugin["package_abi"]):
            raise ValueError("retained plugin original runtime is missing or inconsistent")
        protected.add(reference["snapshot_tag"])
    protected.update([tag for tag in newest({"bind_runtime"}) if tag not in protected][:keep])
    return sorted(set(history)-protected-stacks)


def collect(repository, series):
    result=subprocess.run(["gh","api","--paginate","--slurp",f"repos/{repository}/releases?per_page=100"],
                          check=True,capture_output=True,text=True)
    pages=json.loads(result.stdout)
    if not isinstance(pages,list) or not all(isinstance(page,list) for page in pages):
        raise ValueError("cannot read paginated Release history")
    records=[]
    with tempfile.TemporaryDirectory() as temporary:
        for release in (release for page in pages for release in page):
            if not isinstance(release,dict):
                raise ValueError("invalid listed Release")
            tag=release.get("tag_name")
            kind=snapshot_kind(tag,series)
            if kind is None:
                continue
            draft,created=release.get("draft"),release.get("created_at")
            if type(draft) is not bool or not isinstance(created,str):
                raise ValueError("invalid published history state")
            data=None
            if not draft and kind!="old_stack":
                directory=Path(temporary) / tag
                directory.mkdir()
                names={"channel.json"} | (bind.RUNTIME_SIDECARS if kind=="bind_runtime" else bind.PLUGIN_SIDECARS if kind=="bind_plugin" else
                    {"catalogue-upstream.json","catalogue-creator-target.json"})
                releases.download_release_asset(repository,tag,"channel.json",directory)
                data=bind._json(directory / "channel.json")
                if kind in bind.LEGACY_KINDS and "series-build-inputs.json" in data["assets"]:
                    names.add("series-build-inputs.json")
                for name in sorted(names-{"channel.json"}):
                    releases.download_release_asset(repository,tag,name,directory)
                if any(data["assets"].get(name)!=releases.sha256(directory / name) for name in names-{"channel.json"}):
                    raise ValueError("historical original metadata bytes differ")
                if kind in bind.LEGACY_KINDS:
                    bind.validate_component_record(data,bind._sidecars(directory,kind),data["packages"],kind,series)
                    expected=component_manifest(data,kind,series)
                else:
                    original=bind.metadata_profile.validate_profile(bind._json(directory / "catalogue-upstream.json"),series)
                    creator,source=bind._role(bind._json(directory / "catalogue-creator-target.json"),original,"creator")
                    if data["catalogue_creator"]!=dict(package_creator=creator,creator_source=source):
                        raise ValueError("historical original catalogue maker differs")
                    expected=paired_tag(data,series)
                if expected!=tag:
                    raise ValueError("history manifest differs from registered tag")
            records.append(dict(tag=tag,created_at=created,draft=draft,manifest=data))
    return records


def current_state(repository, series):
    with tempfile.TemporaryDirectory() as temporary:
        root=Path(temporary)
        components={kind:catalogue.fetch(repository,kind,series,root / kind) for kind in bind.LEGACY_KINDS}
        current=releases.snapshot_release(repository,releases.channel_tag(series),root / "stack-current")
        stack=None
        if current.existed:
            if current.draft:
                raise ValueError("registered stack is unpublished")
            stack=catalogue.validate(current.directory)
            selected=paired_tag(stack,series)
            snapshot=releases.snapshot_release(repository,selected,root / "stack-snapshot")
            if snapshot.draft or not releases.snapshot_matches_directory(snapshot,current.directory):
                raise ValueError("registered stack differs from paired published snapshot")
            if stack_receipts(stack,series)!=components:
                raise ValueError("registered stack differs from current components")
        return components,stack


def prune(repository, repository_root, trusted, recovery, *, component, series, selection_sha256, composition_sha256):
    selection=bind.load_selection(trusted,component=component,series=series,selection_sha256=selection_sha256)
    if selection["mode"]!="production":
        raise ValueError("retention requires production inputs")
    bind.validate_selected_git(selection,trusted)
    composition=bind.load_composition(repository_root,component=component,series=series,composition_sha256=composition_sha256)
    bind.validate_repository_root(repository_root,selection,trusted,composition)
    bind.check_composition_peers(repository,composition,owned_must_match=True)
    components,stack=current_state(repository,series)
    records=collect(repository,series)
    plan=retention_plan(records,components,stack)
    if current_state(repository,series)!=(components,stack):
        raise RuntimeError("current references changed during retention")
    recovery.mkdir(parents=True,exist_ok=False)
    (recovery / "retention-plan.json").write_text(json.dumps(dict(delete=plan,
        retain=sorted(record["tag"] for record in records if not record["draft"] and record["tag"] not in plan),
        components=components,current_stack=stack),sort_keys=True,indent=2)+"\n")
    (recovery / "excluded-drafts.json").write_text(json.dumps([record for record in records if record["draft"]],sort_keys=True,indent=2)+"\n")
    apply_plan(repository,plan,recovery)


def apply_plan(repository, plan, recovery):
    """Apply an already checked plan under the caller's single-writer boundary."""
    deleted=[]
    (recovery / "deleted.json").write_text("[]\n")
    try:
        for tag in plan:
            releases.run_gh(["release","delete",tag,"--yes","--repo",repository])
            deleted.append(tag)
            (recovery / "deleted.json").write_text(json.dumps(deleted,indent=2)+"\n")
    except Exception as error:
        (recovery / "failure.json").write_text(json.dumps(dict(error=str(error),deleted=deleted),indent=2)+"\n")
        raise
