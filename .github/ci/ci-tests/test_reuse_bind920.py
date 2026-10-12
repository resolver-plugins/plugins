#!/usr/bin/env python3
"""Frozen runtime intake, byte preservation, and the no-build reuse boundary."""

from __future__ import annotations

from module_fixtures import *
from git_fixtures import upstream_profile
from package_fixtures import creator_identity, make_bind_component, write_json, write_resolved_target

reuse_bind920 = load_module("reuse_bind920", "bind/runtime/reuse_bind920.py")
components = reuse_bind920.components
catalogue = reuse_bind920.package_catalogue
WRAPPER = CI / "bind/runtime/build-bind920.sh"


def checksums(directory):
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in directory.iterdir()}


@contextmanager
def frozen_runtime():
    fixture_root = CI.parent / "ci-local"
    fixture_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="runtime-reuse-", dir=fixture_root) as temporary:
        root = Path(temporary)
        upstream = upstream_profile()
        creator = creator_identity("FreeBSD:14:amd64")
        runtime = make_bind_component("bind_runtime", root / "runtime", series="26.1",
            creator=creator, source_commit="a" * 40, control_commit="b" * 40,
            profile=upstream, version="9.20.26_2")
        provenance = json.loads((runtime / "bind920-provenance.json").read_text())
        inputs = dict(schema=4, series="26.1", freebsd_release="14.3", architecture="x86_64",
            bind_profile=json.loads((runtime / "bind920-profile.json").read_text()),
            build_recipe_sha256=hashlib.sha256(WRAPPER.read_bytes()).hexdigest(), package_creator=creator)
        fingerprint = hashlib.sha256(json.dumps(inputs, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        provenance.update(fingerprint=fingerprint, build_recipe_sha256=inputs["build_recipe_sha256"])
        # Deliberately preserve original serialization as well as JSON meaning.
        (runtime / "bind920-provenance.json").write_text(json.dumps(provenance, indent=4) + "\n\n")
        manifest = json.loads((runtime / "channel.json").read_text())
        manifest["build_fingerprint"] = fingerprint
        manifest["assets"] = {name: digest for name, digest in checksums(runtime).items() if name != "channel.json"}
        write_json(runtime / "channel.json", manifest)
        trusted = root / "trusted"
        trusted.mkdir()
        for name in ("upstream.json", "creator-target.json", "bind920-profile.json"):
            shutil.copyfile(runtime / name, trusted / name)
        shutil.copyfile(CI.parents[1] / ".resolver-plugins/bind-compatibility.json", trusted / "bind-compatibility.json")
        write_resolved_target(trusted / "reader-target.json", upstream, role="reader", creator=creator)
        write_json(trusted / "producer-inputs.json", inputs)
        receipt = dict(schema=1, component="bind_runtime", series="26.1", package_abi=creator["abi"],
            current_tag="pkg-26.1-bind-runtime", snapshot_tag=f"pkg-26.1-bind-runtime-9.20.26_2-{fingerprint}",
            fingerprint=fingerprint, packages=manifest["packages"], assets=checksums(runtime))
        write_json(trusted / "cached-component.json", receipt)
        selection = dict(schema=1, component="bind_runtime", series="26.1", mode="production",
            control_commit="c" * 40, source_commit=None, profile_commit="d" * 40, producer_action="reuse",
            expected_fingerprint=fingerprint, files=checksums(trusted))
        write_json(trusted / "selection.json", selection)
        digest = hashlib.sha256((trusted / "selection.json").read_bytes()).hexdigest()
        yield root, runtime, trusted, digest


def query_archive(path, command):
    record = json.loads(path.read_text())
    dependencies = {("bind-tools", "dns/bind-tools", record["version"])} if record["name"] == "bind920" else set()
    return tuple(record[key] for key in ("name", "version", "origin", "abi")), dependencies


class ReuseBind920Test(unittest.TestCase):
    def test_frozen_reuse_preserves_the_original_pair_and_provenance_bytes(self):
        with frozen_runtime() as (root, runtime, trusted, digest):
            before = {path.name: path.read_bytes() for path in runtime.iterdir()}
            expected = {name: before[name] for name in (
                "bind-tools-9.20.26_2.pkg", "bind920-9.20.26_2.pkg", "bind920-provenance.json")}
            authenticate = Mock()
            with patch.object(catalogue, "verify_packages", authenticate), \
                    patch.object(components.releases, "query_package", side_effect=query_archive), \
                    patch.object(components.package_checksums, "verify_archive"):
                reuse_bind920.reuse(runtime, trusted, "26.1", root / "producer",
                    selection_sha256=digest, pkg_command="minimum-reader")
            self.assertEqual(expected, {path.name: path.read_bytes() for path in (root / "producer").iterdir()})
            self.assertEqual(before, {path.name: path.read_bytes() for path in runtime.iterdir()})
            authenticate.assert_called_once_with(
                [runtime / name for name in json.loads((runtime / "channel.json").read_text())["packages"]],
                runtime.resolve().as_uri(), trusted / "reader-target.json", trusted / "upstream.json",
                pkg_command="minimum-reader")

    def test_a_coherent_substituted_snapshot_stops_before_native_work_and_output(self):
        with frozen_runtime() as (root, runtime, trusted, digest):
            manifest = json.loads((runtime / "channel.json").read_text())
            manifest["control_commit"] = "e" * 40
            write_json(runtime / "channel.json", manifest)
            components.validate_component(runtime, "bind_runtime", "26.1")
            authenticate = Mock()
            with patch.object(catalogue, "verify_packages", authenticate):
                with self.assertRaises(ValueError):
                    reuse_bind920.reuse(runtime, trusted, "26.1", root / "producer",
                        selection_sha256=digest, pkg_command="minimum-reader")
            authenticate.assert_not_called()
            self.assertFalse((root / "producer").exists())

    def test_the_build_wrapper_obeys_the_sealed_action_before_package_commands(self):
        for action in ("build", "reuse"):
            with self.subTest(action=action), frozen_runtime() as (root, runtime, trusted, digest):
                if action == "build":
                    (trusted / "cached-component.json").unlink()
                    selection = json.loads((trusted / "selection.json").read_text())
                    selection.update(producer_action="build", files={name: value for name, value
                        in checksums(trusted).items() if name != "selection.json"})
                    write_json(trusted / "selection.json", selection)
                    digest = hashlib.sha256((trusted / "selection.json").read_bytes()).hexdigest()
                sentinel = root / "command-ran"
                command = root / "command"
                command.write_text("#!/bin/sh\nprintf '%s\\n' invoked > \"$RP_COMMAND_SENTINEL\"\nexit 86\n")
                command.chmod(0o755)
                environment = os.environ | dict(RP_TRUSTED_DIRECTORY=str(trusted), RP_SELECTION_SHA256=digest,
                    PYTHON_COMMAND=sys.executable, PKG_COMMAND=str(command), MAKE_COMMAND=str(command),
                    RP_COMMAND_SENTINEL=str(sentinel))
                result = subprocess.run(["/bin/sh", str(WRAPPER), "26.1", str(root / "producer")],
                                        env=environment, capture_output=True, text=True)
                self.assertEqual(action == "build", sentinel.exists())
                if action == "build":
                    self.assertEqual(86, result.returncode)
                else:
                    self.assertNotEqual(0, result.returncode)
                self.assertFalse((root / "producer").exists())

if __name__ == "__main__":
    unittest.main()
