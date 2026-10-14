"""Package ownership, native dependency provenance, and index-only composition."""
from common_imports import *
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from git_fixtures import upstream_profile
from package_fixtures import (make_bind_component, make_ports_recipe, make_tools_repository,
                              creator_identity, PLUGIN_PACKAGE, write_json, write_resolved_target)
from ha_fixtures import make_ha_channel
import pytest
from bind.shared import components as bind, selection
from bind.plugin import build_dependency
from bind.runtime import bind920_profile as profiles, candidate_matrix
from shared import package_catalogue as catalogue, repository_index as index
from workflow_fixtures import workflow_jobs, trigger_paths


def component(directory, kind, version=None):
    native = kind in ("bind_tools", "bind920", "bind_runtime")
    profile = upstream_profile("26.7")
    version = version or ("9.20.26_2" if native else "26.7_1")
    make_bind_component("bind_runtime" if native else "bind_plugin", directory, series="26.7",
                        creator=creator_identity(), source_commit="a" * 40, control_commit="b" * 40,
                        profile=profile, version=version)
    data = bind._json(directory / "channel.json")
    if kind == "bind_runtime":
        return directory
    data.update(schema=2, kind=kind.replace("_", "-"))
    if native:
        selected = kind.replace("_", "-")
        provenance = bind._json(directory / "bind920-provenance.json")
        pin = bind._json(directory / "bind920-profile.json")
        records = {selected: provenance["packages"][selected]}
        # Exercise unequal native package revisions rather than assuming a pair.
        tools_version = version.split("_")[0] if kind == "bind920" else None
        provenance = profiles.build_provenance(pin, "26.7", "15.1", "x86_64", creator_identity(), records,
            recipe_sha256=provenance["build_recipe_sha256"], package=selected, tools_version=tools_version)
        write_json(directory / "bind920-provenance.json", provenance)
        data["build_fingerprint"] = provenance["fingerprint"]
        for name in list(data["packages"]):
            if not name.startswith(selected + "-"):
                (directory / name).unlink()
                del data["packages"][name]
    else:
        dependency = build_dependency.from_recipe(profile, bind._json(directory / "creator-target.json"),
            b"DISTVERSION= 9.20.26\nPORTREVISION= 2\n", b"distinfo fixture\n")
        write_json(directory / "bind-build-dependency.json", dependency)
        inputs = bind._json(directory / "plugin-build-inputs.json")
        inputs.update(schema=3, build_dependency_sha256=bind.releases.sha256(directory / "bind-build-dependency.json"))
        write_json(directory / "plugin-build-inputs.json", inputs)
        metadata = directory / "build-metadata.txt"
        metadata.write_text(metadata.read_text().replace("bind_source=resolver", "bind_source=opnsense"))
        data.pop("built_against_runtime")
        data["build_runtime"] = {field: dependency[field] for field in ("name", "version", "origin", "abi")}
        data["build_fingerprint"] = bind.plugin_build_fingerprint(inputs)
    data["assets"] = {name: digest for name, digest in bind._files(directory).items() if name != "channel.json"}
    write_json(directory / "channel.json", data)
    bind.validate_component(directory, kind, "26.7")
    return directory


@pytest.mark.parametrize("kind", ["bind_tools", "bind920", "bind_plugin"])
def test_owned_stage_and_publication_need_no_peer_package(tmp_path, monkeypatch, kind):
    original = component(tmp_path / "original", kind)
    manifest = bind._json(original / "channel.json")
    selection_record = dict(schema=2, component=kind, series="26.7", mode="production", producer_action="reuse",
                            expected_fingerprint=manifest["build_fingerprint"], control_commit="b" * 40)
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    selected = bind.component_receipt(original, kind, "26.7")
    write_json(trusted / "cached-component.json", selected)
    def stage(_producer, _selection, _trusted, output, _key, *, pkg_command):
        shutil.copytree(original, output / "current")
        shutil.copytree(original, output / "snapshot")
    monkeypatch.setattr(bind, "stage_component", stage)
    producer = tmp_path / "producer"
    producer.mkdir()
    if kind == "bind920":
        (producer / "qualification-bind-tools-9.20.26.pkg").write_text("internal native prerequisite")
    output = tmp_path / "repository"
    digest = bind.stage_repository(producer, selection_record, trusted, output, None, pkg_command="native-pkg")
    composition = bind.load_composition(output, component=kind, series="26.7", composition_sha256=digest)
    assert composition["components"] == {kind: selected}
    assert not (output / "combined").exists() and not (output / "stack").exists()
    def forbidden(*_args, **_kwargs):
        raise AssertionError("package publication consulted another package or shared index")
    monkeypatch.setattr(bind, "load_selection", lambda *_args, **_kwargs: selection_record)
    monkeypatch.setattr(bind, "validate_selected_git", lambda *_args: None)
    monkeypatch.setattr(bind, "check_composition_peers", forbidden)
    monkeypatch.setattr(bind, "_initialized", forbidden)
    monkeypatch.setattr(catalogue, "publish", forbidden)
    monkeypatch.setattr(bind.subprocess, "run", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(bind.subprocess, "check_output", lambda *_args, **_kwargs: "b" * 40)
    monkeypatch.setattr(bind, "_ensure_commits", lambda *_args: None)
    monkeypatch.setattr(bind.releases, "commit_is_ancestor_or_equal", lambda *_args: True)
    published = []
    def promote(_repository, groups, _recovery):
        assert len(groups) == 1
        groups[0]["validate"](groups[0]["directory"])
        published.append(groups[0]["current_tag"])
    monkeypatch.setattr(bind.releases, "publish_channels", promote)
    def fetch(_repository, requested, series, _directory):
        assert requested == kind and series == "26.7"
        return selected
    monkeypatch.setattr(bind, "fetch_component", fetch)
    bind.publish_component("example/repository", output, trusted, tmp_path / "recovery", component=kind,
                           series="26.7", selection_sha256="c" * 64, composition_sha256=digest)
    assert published == [selected["current_tag"]]
    # A peer archive smuggled into the owned channel cannot be signed or published.
    (original / "unrelated-package-1.pkg").write_text("foreign payload")
    with pytest.raises(ValueError):
        bind.validate_component(original, kind, "26.7")


def test_plugin_selection_freezes_ordinary_dependency_without_runtime_publication(tmp_path, monkeypatch):
    upstream = upstream_profile("26.7")
    source = write_json(tmp_path / "upstream.json", upstream)
    definition = tmp_path / "package.mk"
    definition.write_text(PLUGIN_PACKAGE)
    tools = make_tools_repository(tmp_path / "tools", upstream)
    creator = write_resolved_target(tmp_path / "creator-target.json", upstream)
    reader = write_resolved_target(tmp_path / "reader-target.json", upstream, role="reader")
    dependency = build_dependency.from_recipe(upstream, bind._json(creator),
        b"DISTVERSION= 9.20.29\nPORTREVISION= 0\n", b"frozen upstream distinfo\n")
    monkeypatch.setattr(build_dependency, "resolve", lambda *_args: dependency)
    monkeypatch.setattr(bind, "fetch_component", Mock(side_effect=AssertionError("peer Release required")))
    monkeypatch.setattr(profiles, "resolve_profile", Mock(side_effect=AssertionError("RP runtime approval required")))
    monkeypatch.setattr(bind, "fetch_cached_component", lambda *_args: None)
    result = bind.prepare_selection(tmp_path / "trusted", component="bind_plugin", series="26.7", mode="production",
        control_commit="b" * 40, source_commit="a" * 40, profile_commit="a" * 40, upstream=source,
        runtime_approval=None, tools_repository=tools, recipe=definition, repository="example/repository",
        intake_directory=tmp_path / "intake", package_definition=definition, creator_target=creator,
        reader_target=reader, independent=True)
    trusted = tmp_path / "trusted"
    selected = bind.load_selection(trusted, component="bind_plugin", series="26.7", selection_sha256=result["selection_sha256"])
    assert selected["schema"] == 2 and "runtime-selection.json" not in selected["files"]
    assert "runtime-approval.mk" not in selected["files"]
    # Even resealing the outer file map cannot admit an altered build dependency.
    write_json(trusted / "bind-build-dependency.json", dict(dependency, version="9.20.30"))
    with pytest.raises(ValueError, match="dependency"):
        bind.seal_selection(trusted, selected)


def test_index_handles_missing_peers_and_supersedes_legacy_runtime_individually(tmp_path):
    receipts = {}
    for kind in ("bind_runtime", "bind_tools", "bind920", "bind_plugin"):
        version = "9.20.29" if kind in ("bind_tools", "bind920") else None
        directory = component(tmp_path / kind, kind, version)
        receipts[kind] = bind.component_receipt(directory, kind, "26.7")
    ha = make_ha_channel(tmp_path / "ha")
    receipts["dhcp"] = catalogue.receipt(ha, "dhcp", "26.7")
    assert set(index.selected_records({"dhcp": receipts["dhcp"]})) == {"dhcp"}
    records = index.selected_records({"bind_runtime": receipts["bind_runtime"], "bind_tools": receipts["bind_tools"]})
    assert set(records["bind_runtime"]["selected_packages"]) == {"bind920-9.20.26_2.pkg"}
    assert records["bind_tools"]["selected_packages"] == {"bind-tools-9.20.29.pkg": receipts["bind_tools"]["packages"]["bind-tools-9.20.29.pkg"]}
    assert records["bind_runtime"]["assets"] == receipts["bind_runtime"]["assets"]
    assert set(index.selected_records(receipts)) == {"bind_tools", "bind920", "bind_plugin", "dhcp"}


@pytest.mark.parametrize("kinds", [("dhcp",), ("bind_runtime", "bind_tools")])
def test_index_stages_available_packages_without_requiring_peers(tmp_path, monkeypatch, kinds):
    directory = tmp_path / "inputs"
    originals = directory / "originals"
    originals.mkdir(parents=True)
    profile = upstream_profile("26.7")
    tools = make_tools_repository(tmp_path / "tools", profile)
    write_json(directory / "upstream.json", profile)
    write_json(directory / "tools-profile.json", bind.metadata_profile.verify_tools_profile(profile, tools))
    for role in ("creator", "reader"):
        write_resolved_target(directory / f"{role}-target.json", profile, role=role)
    receipts = dict.fromkeys(index.ORDER)
    for kind in kinds:
        original = make_ha_channel(originals / kind) if kind == "dhcp" else component(originals / kind, kind)
        receipts[kind] = catalogue.receipt(original, kind, "26.7")
    selection_record = dict(schema=1, series="26.7", control_commit="b" * 40, source_commit="a" * 40,
        components=receipts, files={name: bind.releases.sha256(directory / name) for name in index.FILES})
    write_json(directory / "index-inputs.json", selection_record)
    digest = bind.releases.sha256(directory / "index-inputs.json")
    index.validate_inputs(directory, digest)
    monkeypatch.setattr(index, "authenticate", lambda *_args: selection_record)
    monkeypatch.setattr(bind, "select_role", lambda *_args: None)
    monkeypatch.setattr(index.subprocess, "check_output", lambda *_args, **_kwargs: bind.PUBLIC_KEY.read_bytes())
    def stage(packages, output, _key, _pkg, _metadata):
        output.mkdir()
        for path in packages:
            shutil.copyfile(path, output / path.name)
        (output / "meta.conf").write_text("isolated catalogue")
        (output / "packagesite.pkg").write_text("synthetic signed catalogue")
    monkeypatch.setattr(bind.releases, "stage_selected_repository", stage)
    output = tmp_path / "index"
    index.stage(directory, digest, output, tmp_path / "key", "native-pkg")
    manifest = catalogue.validate(output)
    assert manifest["schema"] == 3 and set(manifest["components"]) == set(kinds)
    selected = {name for record in manifest["components"].values() for name in record["selected_packages"]}
    assert {path.name for path in output.glob("*.pkg") if path.name not in bind.CATALOGUE_FILES} == selected
    for name in selected:
        assert any((originals / kind / name).exists() and (originals / kind / name).read_bytes() == (output / name).read_bytes() for kind in kinds)
    # A changed selected original cannot hide behind a freshly hashed outer manifest.
    owned = next(iter(manifest["components"]))
    manifest["components"][owned]["selected_packages"] = {}
    write_json(output / "channel.json", manifest)
    with pytest.raises(ValueError):
        catalogue.validate(output)


def test_distinct_runtime_revisions_and_own_approval_triggers():
    recipe = "DISTVERSION= 9.20.30\n.if defined(BIND_TOOLS_SLAVE)\nPORTREVISION= 0\n.else\nPORTREVISION= 2\n.endif\n"
    assert profiles.recipe_version(recipe, "bind-tools") == ("9.20.30", 0)
    assert profiles.recipe_version(recipe, "bind920") == ("9.20.30", 2)
    before = "RP_BIND_VERSION= 9.20.29\nRP_BIND_PORTS_COMMIT= " + "a" * 40 + "\n"
    assert not candidate_matrix.eligible("", before)  # Initial split is not a migration build.
    assert not candidate_matrix.eligible(before, before)
    after = before.replace("9.20.29", "9.20.30")
    assert candidate_matrix.eligible(before, after)
    for kind in ("bind_tools", "bind920"):
        name = kind.replace("_", "-")
        text = (Path(__file__).resolve().parents[2] / "workflows" / f"{name}-release.yml").read_text()
        assert trigger_paths(text, "push") == [f"packages/{name}/Makefile"]
        assert trigger_paths(text, "pull_request") == [f"packages/{name}/Makefile"]
        assert "secrets:" not in workflow_jobs(text)["preview"]
        assert not selection.watches(kind, "dns/bind/Makefile")
    sync = (Path(__file__).resolve().parents[2] / "workflows/upstream-sync.yml").read_text()
    assert "build-bind920.sh" not in sync and "vmactions/freebsd-vm" not in sync
