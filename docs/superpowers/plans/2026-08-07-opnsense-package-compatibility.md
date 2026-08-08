# OPNsense Package Compatibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce target-compatible Resolver Plugins packages, make the installer reject incompatible archives before mutation, and prove the official `os-bind` to `os-bind-rp` transition on HA-2.

**Architecture:** The FreeBSD build jobs fetch a per-series `pkg` creator pinned by identity, ABI, and SHA-256 before any archive is created, and the pinned minimum-supported parser treats readable per-file checksums as a required artifact contract. The standalone shell installer freezes verified candidate bytes in an isolated local repository, preserves OPNsense state in durable backup storage before its first package mutation, and verifies identities and ownership afterward. Automated clean-install and official-package replacement gates precede a host-local, one-use-key HA-2 canary while HA-1 carries DNS.

**Tech Stack:** Python 3.12/3.13, POSIX shell, FreeBSD `pkg` 2.3.1_1, pytest 8.3.5, GitHub Actions FreeBSD VMs, OPNsense 26.1.11_10.

## Global Constraints

- Keep `PLUGIN_NAME=bind-rp` and `PLUGIN_CONFLICTS=bind` intact.
- Preserve the minimum OPNsense version `26.1.11_10`.
- Do not upgrade the package manager on an end-user OPNsense host; target `pkg` selection is builder-only.
- Do not weaken signed repository, provenance, public-key fingerprint, or immutable release checks.
- Keep durable regression tests in `.github/ci/ci-tests/`; keep HA traces and canary artifacts temporary and uncommitted.
- Keep the production installer URL and public-key fingerprint fixed; change only a temporary HA-2 canary copy.
- Do not publish, push, or alter the production signing boundary without a separate explicit user request.
- Keep HA-1 read-only and healthy throughout HA-2 testing.

---

### Task 1: Target-readable archive checksum verifier

**Files:**
- Create: `.github/ci/package_checksums.py`
- Create: `.github/ci/ci-tests/test_package_checksums.py`

**Interfaces:**
- Produces: `PackageChecksumError(ValueError)`.
- Produces: `archive_file_checksums(pkg_command: str, archive: pathlib.Path) -> tuple[tuple[str, str], ...]`.
- Produces: `verify_archive(pkg_command: str, archive: pathlib.Path) -> tuple[tuple[str, str], ...]`.
- CLI: `python3 .github/ci/package_checksums.py --pkg-command pkg ARCHIVE...` exits `0` only when every archive has at least one file and every `%Fs` value is non-empty and not `(null)`.

- [ ] **Step 1: Write failing verifier tests**

Create a fake `pkg` executable that handles `query -F <archive> '%Fp|%Fs'`. Add tests that assert:

```python
def test_accepts_complete_target_readable_file_checksums(tmp_path: Path) -> None:
    pkg = write_pkg_fixture(tmp_path, "usr/local/sbin/named|1$" + "a" * 64 + "\n")
    rows = package_checksums.verify_archive(str(pkg), tmp_path / "bind920.pkg")
    assert rows == (("usr/local/sbin/named", "1$" + "a" * 64),)


@pytest.mark.parametrize("output", ["", "usr/local/sbin/named|(null)\n", "usr/local/sbin/named|\n"])
def test_rejects_missing_or_null_file_checksums(tmp_path: Path, output: str) -> None:
    pkg = write_pkg_fixture(tmp_path, output)
    with pytest.raises(package_checksums.PackageChecksumError):
        package_checksums.verify_archive(str(pkg), tmp_path / "bind920.pkg")
```

- [ ] **Step 2: Run the focused tests and confirm RED**

Run:

```sh
pytest -q .github/ci/ci-tests/test_package_checksums.py
```

Expected: collection or import failure because `.github/ci/package_checksums.py` does not exist.

- [ ] **Step 3: Implement the minimal functional verifier**

Implement immutable tuple parsing around:

```python
result = subprocess.run(
    [pkg_command, "query", "-F", str(archive), "%Fp|%Fs"],
    check=True,
    text=True,
    capture_output=True,
)
rows = tuple(tuple(line.split("|", 1)) for line in result.stdout.splitlines())
```

Reject malformed rows, an empty tuple, empty checksums, and `(null)`. The CLI must name the failing archive on stderr without printing archive contents.

- [ ] **Step 4: Run focused and full tests**

Run:

```sh
pytest -q .github/ci/ci-tests/test_package_checksums.py
pytest -q .github/ci/ci-tests
```

Expected: all tests pass.

- [ ] **Step 5: Commit the verifier**

```sh
git add .github/ci/package_checksums.py .github/ci/ci-tests/test_package_checksums.py
git commit -m "ci: verify target-readable package checksums"
```

### Task 2: Select the pinned OPNsense package creator in build VMs

**Files:**
- Create: `.resolver-plugins/target-pkg.json`
- Create: `.github/ci/target_pkg.py`
- Create: `.github/ci/ci-tests/test_target_pkg.py`
- Modify: `.github/ci/build-bind920.sh`
- Modify: `.github/ci/build-os-bind-rp.sh`
- Modify: `.github/ci/ci-tests/pkg-build-fixture.sh`
- Modify: `.github/ci/ci-tests/test_build_os_bind_rp.py`

**Interfaces:**
- Produces: `PackageIdentity(name: str, version: str, origin: str, abi: str)` as a frozen dataclass.
- Produces: `TargetPackage(series: str, identity: PackageIdentity, filename: str, sha256: str, pkg_static_sha256: str)` as a frozen dataclass.
- Produces: `select_target_pkg(metadata: pathlib.Path, series: str, pkg_command: str, repository: str = "OPNsense") -> TargetPackage`.
- Produces: `verify_target_pkg(target: TargetPackage, pkg_command: str) -> None`, requiring exact installed identity, a locked `pkg` package, and exact `/usr/local/sbin/pkg-static` SHA-256.
- CLI: `python3 .github/ci/target_pkg.py install .resolver-plugins/target-pkg.json 26.1 --pkg-command pkg --repository OPNsense` installs, locks, verifies, and prints the complete canonical creator record as sorted single-line JSON, including filename, archive hash, and `pkg_static_sha256`. The `field` subcommand reads one named value from the same trusted metadata without shell reconstruction.
- Build metadata fields for 26.1: `pkg_creator=2.3.1_1` and `pkg_creator_sha256=74dbb941de91fda8b470eeab78926170e35737346fbe55e8a2e4c3968f79e1e3`; 26.7 uses the exact hash in its metadata record below.
- The pinned verifier executable is `/usr/local/sbin/pkg-static` from the verified creator archive.

- [ ] **Step 1: Write failing target-selection tests**

Add immutable metadata with these exact signed-repository package records:

```json
{
  "schema": 1,
  "series": {
    "26.1": {
      "name": "pkg",
      "version": "2.3.1_1",
      "origin": "ports-mgmt/pkg",
      "abi": "FreeBSD:14:amd64",
      "filename": "pkg-2.3.1_1.pkg",
      "sha256": "74dbb941de91fda8b470eeab78926170e35737346fbe55e8a2e4c3968f79e1e3",
      "pkg_static_sha256": "d21190515479c960d29391bcd38a94e517948cb9aa8c27e5d4fb2e03046dc7b3"
    },
    "26.7": {
      "name": "pkg",
      "version": "2.3.1_1",
      "origin": "ports-mgmt/pkg",
      "abi": "FreeBSD:15:amd64",
      "filename": "pkg-2.3.1_1.pkg",
      "sha256": "8061b1eadcbd288968eb2006b379d6a0b4e8a45dfcbcab88e446402cc22cf109",
      "pkg_static_sha256": "9636e459feac6e45b83cbe087a8a2e7e5e56235ca5fffa91acf129f4456911ab"
    }
  }
}
```

Use a stateful fake `pkg` command and assert this order:

```text
fetch -y -r OPNsense -o <temporary> pkg-2.3.1_1
query -F <temporary>/pkg-2.3.1_1.pkg %n|%v|%o|%q
add -f <temporary>/pkg-2.3.1_1.pkg
lock -y pkg
query -e %n = pkg %n|%v|%o|%q
lock -l
```

The happy fixture provides exact archive bytes matching the test metadata and returns `pkg|2.3.1_1|ports-mgmt/pkg|FreeBSD:14:amd64` from the archive and installed database. Add rejection tests for a bad archive SHA-256, wrong archive origin, wrong ABI, missing lock, changed installed identity, and a changed `pkg-static` hash. Add a stateful test where a later dependency install attempts to upgrade `pkg`; the lock must prevent it, and an intentionally changed identity/hash must make `verify_target_pkg` fail before package creation. Add a test where the repository's default package is a newer self-consistent version; selecting the exact `pkg-2.3.1_1` archive must still succeed, while absence of that exact archive must fail closed.

- [ ] **Step 2: Run the focused tests and confirm RED**

```sh
pytest -q .github/ci/ci-tests/test_target_pkg.py
```

Expected: import failure because `target_pkg.py` does not exist.

- [ ] **Step 3: Implement exact identity selection**

Strictly validate the metadata schema. Fetch the exact versioned filename through the signed OPNsense repository, locate only that filename in supported `pkg fetch` layouts, verify SHA-256 in Python, then query and compare the archive identity before `pkg add -f`. Lock `pkg` immediately after installation. Require the installed identity, `pkg lock -l`, `pkg-static -v`, and the static executable hash to match afterward. Never resolve or install the repository's unversioned current `pkg` candidate.

- [ ] **Step 4: Add build-wrapper tests before wrapper changes**

Extend `test_build_os_bind_rp.py` to require:

```python
assert "fetch -y -r OPNsense" in package_calls
assert "pkg-2.3.1_1" in package_calls
assert any(call.startswith("add -f ") and "pkg-2.3.1_1.pkg" in call for call in package_calls)
assert "pkg_creator=2.3.1_1\n" in metadata
assert "pkg_creator_sha256=74dbb941de91fda8b470eeab78926170e35737346fbe55e8a2e4c3968f79e1e3\n" in metadata
assert next(index for index, call in enumerate(package_calls) if call.startswith("add -f ")) < package_calls.index("install -y bind920")
```

Add shell-wrapper assertions that both builders invoke target selection before dependency installation, then run the `verify` subcommand immediately before and after every `make package`. Require archive verification to use the private pinned `/usr/local/sbin/pkg-static` only after the post-package identity/hash check.

- [ ] **Step 5: Run wrapper tests and confirm RED**

```sh
pytest -q .github/ci/ci-tests/test_target_pkg.py .github/ci/ci-tests/test_build_os_bind_rp.py
```

Expected: the new ordering and metadata assertions fail.

- [ ] **Step 6: Wire target selection into both builders**

After `setup-opnsense-repository.sh` and before reuse, dependency installation, or `make package`, invoke:

```sh
target_pkg_metadata="$repository_root/.resolver-plugins/target-pkg.json"
pkg_creator_record=$("$python_command" "$script_directory/target_pkg.py" install \
    "$target_pkg_metadata" "$series" --pkg-command "$pkg_command")
pkg_creator=$("$python_command" "$script_directory/target_pkg.py" field \
    "$target_pkg_metadata" "$series" version)
pkg_creator_sha256=$("$python_command" "$script_directory/target_pkg.py" field \
    "$target_pkg_metadata" "$series" sha256)
```

Read version and SHA-256 from trusted metadata through the `field` subcommand, record both in plugin build metadata, and pass the canonical JSON creator record directly to BIND provenance generation. Keep the `pkg` lock for the lifetime of each disposable VM. Invoke `target_pkg.py verify` after dependency installs and immediately before and after each package target. Extend the fixture to model exact-version fetch, archive query, `add -f`, locking, installed query, static executable hashing, and an attempted dependency-driven self-upgrade without changing existing BIND identity behavior.

- [ ] **Step 7: Run focused and full tests**

```sh
pytest -q .github/ci/ci-tests/test_target_pkg.py .github/ci/ci-tests/test_build_os_bind_rp.py
pytest -q .github/ci/ci-tests
```

Expected: all tests pass.

- [ ] **Step 8: Commit builder selection**

```sh
git add .resolver-plugins/target-pkg.json .github/ci/target_pkg.py .github/ci/build-bind920.sh .github/ci/build-os-bind-rp.sh .github/ci/ci-tests/test_target_pkg.py .github/ci/ci-tests/pkg-build-fixture.sh .github/ci/ci-tests/test_build_os_bind_rp.py
git commit -m "ci: build packages with the target OPNsense pkg"
```

### Task 3: Reject incompatible reused and newly built archives

**Files:**
- Modify: `.github/ci/reuse_bind920.py`
- Modify: `.github/ci/bind920_profile.py`
- Modify: `.github/ci/release_channel.py`
- Modify: `.github/ci/build-bind920.sh`
- Modify: `.github/ci/build-os-bind-rp.sh`
- Modify: `.github/workflows/package-release.yml`
- Modify: `.github/ci/ci-tests/test_bind920_reuse.py`
- Modify: `.github/ci/ci-tests/test_reuse_bind920.py`
- Modify: `.github/ci/ci-tests/test_build_os_bind_rp.py`
- Modify: `.github/ci/ci-tests/test_release_channel_provenance.py`
- Modify: `.github/ci/ci-tests/test_release_channel_archive.py`
- Modify: `.github/ci/ci-tests/test_package_release_workflow.py`

**Interfaces:**
- Consumes: `package_checksums.verify_archive(pkg_command, archive)` from Task 1.
- BIND provenance schema records `package_creator` with exact name, version, origin, ABI, filename, and SHA-256; the compatibility fingerprint includes that immutable record.
- New plugin build metadata fields are `pkg_creator` and `pkg_creator_sha256`.
- New `channel.json` schema `2` records `package_creator`; schema `1` remains accepted only when validating already-published recovery/snapshot bytes.
- Existing BIND reuse exit code `3` continues to mean a safe cache miss.

- [ ] **Step 1: Write a failing BIND-reuse regression test**

First bump the expected BIND provenance schema and add a creator record equal to the selected series entry in `.resolver-plugins/target-pkg.json`. Assert that old provenance without `package_creator`, a different creator hash, or the former schema produces `CacheMiss`, not a hard failure. Add release-channel tests that strict new build metadata rejects missing/extra creator fields, trusted validation rejects plugin/BIND/target-metadata creator mismatches, schema-2 `channel.json` records the exact creator, and recovery validation still accepts a structurally valid immutable schema-1 channel. Then make the fake pinned `pkg-static query -F ... '%Fp|%Fs'` return `(null)` and assert `reuse(...)` raises `CacheMiss` with `target-readable checksums` in the message. Keep malformed package identity and signature failures as hard errors.

- [ ] **Step 2: Run the test and confirm RED**

```sh
pytest -q .github/ci/ci-tests/test_reuse_bind920.py .github/ci/ci-tests/test_release_channel_provenance.py .github/ci/ci-tests/test_release_channel_archive.py -k 'checksum or creator or schema'
```

Expected: reuse accepts the archive or does not issue a checksum query.

- [ ] **Step 3: Reject incompatible reuse as a cache miss**

Extend `compatibility_fingerprint` and `build_provenance` with the immutable creator record. Extend `release_channel.BUILD_METADATA_FIELDS`, `validate_bind_provenance`, `validate_build_metadata`, staging, and channel audit generation so trusted `.resolver-plugins/target-pkg.json`, plugin build metadata, BIND provenance, and schema-2 `channel.json` must agree. Add `--target-pkg-metadata` to both signer validation commands in the workflow. Preserve a narrow schema-1/read-old-metadata path only inside existing-channel recovery validation; new build validation and staging require schema 2 and creator fields. Call `verify_archive` through `/usr/local/sbin/pkg-static` after identity verification and before `pkg add`. Convert only old/mismatched creator reuse provenance and `PackageChecksumError` to `CacheMiss`; let command, identity, signature, and malformed-current-schema errors remain hard failures.

- [ ] **Step 4: Add failing builder-output assertions**

Require `build-bind920.sh` to verify both produced archives before either is copied to artifacts, and require `build-os-bind-rp.sh` to verify the plugin archive before copying it or writing successful metadata.

- [ ] **Step 5: Run assertions and confirm RED**

```sh
pytest -q .github/ci/ci-tests/test_reuse_bind920.py .github/ci/ci-tests/test_build_os_bind_rp.py
```

Expected: builder verification assertions fail.

- [ ] **Step 6: Add builder archive gates**

Invoke:

```sh
"$python_command" "$script_directory/package_checksums.py" \
    --pkg-command /usr/local/sbin/pkg-static "$archive"
```

for both new BIND archives and the plugin archive. Extend package fixtures to return a non-null checksum for the generated archive.

- [ ] **Step 7: Run focused and full tests**

```sh
pytest -q .github/ci/ci-tests/test_reuse_bind920.py .github/ci/ci-tests/test_build_os_bind_rp.py
pytest -q .github/ci/ci-tests
```

Expected: all tests pass.

- [ ] **Step 8: Commit archive gating**

```sh
git add .github/ci/reuse_bind920.py .github/ci/bind920_profile.py .github/ci/release_channel.py .github/ci/build-bind920.sh .github/ci/build-os-bind-rp.sh .github/workflows/package-release.yml .github/ci/ci-tests/test_bind920_reuse.py .github/ci/ci-tests/test_reuse_bind920.py .github/ci/ci-tests/test_build_os_bind_rp.py .github/ci/ci-tests/test_release_channel_provenance.py .github/ci/ci-tests/test_release_channel_archive.py .github/ci/ci-tests/test_package_release_workflow.py
git commit -m "ci: reject package archives with unreadable checksums"
```

### Task 4: Bump the rebuilt BIND package identity

**Files:**
- Modify: `.resolver-plugins/bind920.json`
- Modify: `.github/ci/bind920_profile.py`
- Modify: `.github/ci/build-bind920.sh`
- Modify: `.github/ci/patches/bind920-portrevision.patch`
- Modify: `.github/ci/ci-tests/test_bind920_reuse.py`
- Modify: `.github/ci/ci-tests/test_release_channel_archive.py`
- Modify: `.github/ci/ci-tests/test_release_channel_provenance.py`
- Modify: `.github/ci/ci-tests/test_reuse_bind920.py`
- Modify: `.github/ci/ci-tests/test_install_os_bind_rp.py`
- Modify: `docs/building.md`
- Modify: `docs/package-channel-distribution-design.md`

**Interfaces:**
- Changes the Resolver fallback identities from `bind920-9.20.26_1` and `bind-tools-9.20.26_1` to `bind920-9.20.26_2` and `bind-tools-9.20.26_2`.
- Changes compatibility profile `portrevision` from `1` to `2`, forcing an old-channel reuse cache miss.
- `bind920_profile.package_version(profile: object) -> str` returns `9.20.26_2`; build output paths consume that value rather than embedding `_1`.

- [ ] **Step 1: Change identity expectations in tests first**

Mechanically change only Resolver fallback fixtures and expected provenance from `_1` to `_2`. Do not change the minimum version `9.20.26` or official OPNsense fixture versions where a test intentionally represents the older installed package. Add tests that `load_profile` accepts any positive integer `portrevision`, rejects zero/negative/non-integers, and that `package_version(PROFILE)` returns `9.20.26_2`. Add a wrapper assertion proving no `_1.pkg` output path is hard-coded.

- [ ] **Step 2: Run focused tests and confirm RED**

```sh
pytest -q .github/ci/ci-tests/test_bind920_reuse.py .github/ci/ci-tests/test_reuse_bind920.py .github/ci/ci-tests/test_release_channel_archive.py .github/ci/ci-tests/test_release_channel_provenance.py
```

Expected: profile/provenance tests report revision `1` instead of `2`, and the wrapper still contains hard-coded `_1.pkg` paths.

- [ ] **Step 3: Bump the profile and port patch**

Set:

```json
"portrevision": 2
```

and change both added `PORTREVISION` lines in `bind920-portrevision.patch` to `2`. Replace `bind920_profile.py`'s revision-equals-one restriction with a positive-integer check and add `package_version`. In `build-bind920.sh`, obtain the validated version from the profile and locate exactly `bind-tools-$package_version.pkg` and `bind920-$package_version.pkg`; remove both `_1` literals. Update exact-version documentation examples to `_2`.

- [ ] **Step 4: Run focused and full tests**

```sh
pytest -q .github/ci/ci-tests/test_bind920_reuse.py .github/ci/ci-tests/test_reuse_bind920.py .github/ci/ci-tests/test_release_channel_archive.py .github/ci/ci-tests/test_release_channel_provenance.py
pytest -q .github/ci/ci-tests
```

Expected: all tests pass.

- [ ] **Step 5: Commit the revision bump**

```sh
git add .resolver-plugins/bind920.json .github/ci/bind920_profile.py .github/ci/build-bind920.sh .github/ci/patches/bind920-portrevision.patch .github/ci/ci-tests docs/building.md docs/package-channel-distribution-design.md
git commit -m "build: revise target-compatible BIND packages"
```

### Task 5: Installer fail-closed archive preflight

**Files:**
- Modify: `scripts/install-os-bind-rp.sh`
- Modify: `.github/ci/ci-tests/test_install_os_bind_rp.py`

**Interfaces:**
- Environment for tests and bounded operations: `RP_CONFIG_FILE` defaults to `/conf/config.xml`; `RP_STATE_DIRECTORY` defaults to a unique mode-0700 `/var/backups/os-bind-rp-install.<UTC timestamp>` directory.
- Durable preserved files: `config.xml.bak`, `installed-packages.txt`, and `candidate-sha256.txt` under `RP_STATE_DIRECTORY`.
- Existing `RP_TEMPORARY_DIRECTORY` is ephemeral download/repository storage. A caller-owned directory is never deleted; an installer-created directory is deleted only after success.
- Every remote Resolver Plugins `pkg` call uses `-o REPOS_DIR=$RP_PKG_REPOSITORY_DIR`; this makes the existing repository-directory override real on OPNsense rather than merely changing where the file is written. The production default remains `/usr/local/etc/pkg/repos`.
- `verified_pkg` invokes `${RP_PKG_STATIC_COMMAND:-/usr/local/sbin/pkg-static} -o REPOS_DIR=<isolated-config-directory>`. The isolated configuration contains the system OPNsense repository plus `resolver-plugins-verified`, a `file://` repository generated from the exact fetched archives; it never contains the moving remote Resolver Plugins configuration.
- The installer records whether `pkg` was already locked, locks it before dry-run/mutation when needed, verifies its identity immediately before the transaction, and restores the original lock state on every exit path.

- [ ] **Step 1: Extend fixtures for fetch and archive inspection**

Make the fake `pkg` implement `fetch`, `repo`, archive `query -F`, installed queries for `os-bind`/`os-bind-rp`, exact-version dry-run/install, and `which`. Candidate archive rows must default to:

```text
usr/local/sbin/named|1$aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
```

Provide test parameters for `(null)`, empty output, fetch failure, official `os-bind` presence, remote candidate replacement after fetch, and install failure.

- [ ] **Step 2: Write failing preflight ordering tests**

Assert that all three archive checksum queries occur after repository update but before `pkg repo`, dry-run, or the first mutating `pkg install`. For a null checksum, assert a non-zero exit, an `incompatible package file checksums` diagnostic naming the package, no local repository creation, no durable state directory, and no `pkg install` calls.

- [ ] **Step 3: Run preflight tests and confirm RED**

```sh
pytest -q .github/ci/ci-tests/test_install_os_bind_rp.py -k 'checksum or preflight'
```

Expected: the installer does not call `pkg fetch` or archive checksum queries.

- [ ] **Step 4: Implement archive fetch and checksum preflight**

Add POSIX-shell functions that scope all remote update/query/fetch calls with `-o REPOS_DIR="$repository_directory"`, fetch exactly `bind-tools`, `bind920`, and `os-bind-rp` into the temporary directory, locate one archive for each expected package, validate `%n|%v|%o`, validate `%Fp|%Fs` with `awk`, and record SHA-256 values. Copy the exact archives into a new local repository, run `pkg repo`, re-check archive hashes, and create an isolated repository configuration containing only the local candidate plus the existing OPNsense configuration. Resolve exact `name-version` arguments from the frozen identities. Do not use a recursive deletion target outside the installer-created temporary directory.

- [ ] **Step 5: Run preflight tests to GREEN**

```sh
pytest -q .github/ci/ci-tests/test_install_os_bind_rp.py -k 'checksum or preflight'
```

Expected: all selected tests pass.

- [ ] **Step 6: Prove verified bytes are the only install source**

Add a regression test whose fake remote candidate changes identity and checksum after preflight. Assert the installer never fetches again, invokes dry-run and install through the configured `pkg-static` with exact `name-version` arguments and `-o REPOS_DIR=<isolated>`, and consumes the original local archive hashes. Add a failure case where a frozen archive changes before dry-run; require a hash-mismatch exit before any mutating package command. Add locked/unlocked fixtures proving a temporary package-manager lock is established before dry-run and the original state is restored after success and failure.

- [ ] **Step 7: Run frozen-candidate tests to GREEN**

```sh
pytest -q .github/ci/ci-tests/test_install_os_bind_rp.py -k 'frozen or changed_candidate or hash'
```

Expected: all selected tests pass and no install resolves from the moving remote repository.

- [ ] **Step 8: Commit the isolated preflight behavior**

```sh
git add scripts/install-os-bind-rp.sh .github/ci/ci-tests/test_install_os_bind_rp.py
git commit -m "fix: preflight package manifests before installation"
```

### Task 6: Installer backup, diagnostics, and post-install ownership

**Files:**
- Modify: `scripts/install-os-bind-rp.sh`
- Modify: `.github/ci/ci-tests/test_install_os_bind_rp.py`

**Interfaces:**
- Consumes candidate archive identities and file lists from Task 5.
- Produces stderr transition message `Replacing official os-bind with os-bind-rp` or `Upgrading installed os-bind-rp`.
- Produces a mode-0700 durable state directory and mode-preserving configuration backup immediately before the first mutating install.

- [ ] **Step 1: Write failing transition and backup tests**

Add tests asserting:

```python
assert "Replacing official os-bind with os-bind-rp" in result.stderr
assert backup.read_text() == config.read_text()
assert stat.S_IMODE(backup.stat().st_mode) == stat.S_IMODE(config.stat().st_mode)
assert calls.index("backup-state") < first_install_index
```

Add a test where BIND is already eligible so the backup still precedes the plugin install, and a test where a declined BIND prompt creates no backup.
Add a default-path test using an overridden test backup root that requires the state directory to remain after a successful install with mode `0700`, its configuration backup and inventory intact, while the distinct installer-created download directory is gone.

- [ ] **Step 2: Run the transition tests and confirm RED**

```sh
pytest -q .github/ci/ci-tests/test_install_os_bind_rp.py -k 'backup or transition'
```

Expected: no backup or transition message exists.

- [ ] **Step 3: Implement one-time state capture**

Detect official and Resolver plugin identities with separate simple `pkg query` calls. Implement an idempotent `capture_state` function invoked only immediately before the first mutating install. Create durable state below `${RP_BACKUP_ROOT:-/var/backups}`, use `umask 077` plus explicit mode `0700`, `cp -p` for configuration, and `pkg query '%n|%v|%o' | sort` for package inventory. Copy candidate hashes, not package contents, into durable state; ephemeral candidate archives remain in the separate download directory on failure and are removed after success.

- [ ] **Step 4: Add failing post-install and failure-retention tests**

Assert successful installation queries all three installed identities, checks archive-listed paths with `pkg which`, and invokes `pkg check -s bind-tools bind920 os-bind-rp`. On simulated install failure, assert the durable state directory and ephemeral candidate directory remain, the backup and inventory remain, and stderr prints their exact directories without broad cleanup commands.

- [ ] **Step 5: Run post-install tests and confirm RED**

```sh
pytest -q .github/ci/ci-tests/test_install_os_bind_rp.py -k 'ownership or retained or package_check'
```

Expected: missing `which`/`check` calls and failure diagnostics.

- [ ] **Step 6: Implement bounded post-install verification**

Require exact installed name/origin identities, official `os-bind` absence, ownership for each candidate file, and scoped checksum checks. Replace the unconditional temporary-directory trap with an exit-status-aware cleanup function: delete only the installer-created ephemeral directory on success; always preserve the durable backup directory; preserve both directories and print their paths on failure.

- [ ] **Step 7: Run installer and full tests**

```sh
pytest -q .github/ci/ci-tests/test_install_os_bind_rp.py
sh -n scripts/install-os-bind-rp.sh
pytest -q .github/ci/ci-tests
```

Expected: all tests pass and shell syntax is valid.

- [ ] **Step 8: Commit installer recovery behavior**

```sh
git add scripts/install-os-bind-rp.sh .github/ci/ci-tests/test_install_os_bind_rp.py
git commit -m "fix: preserve and verify os-bind replacement state"
```

### Task 7: Target-native workflow replacement gates

**Files:**
- Modify: `.github/workflows/package-release.yml`
- Modify: `.github/ci/ci-tests/test_package_release_workflow.py`

**Interfaces:**
- Consumes: `target_pkg.py` and `package_checksums.py`.
- Development and signed staged verification start from official `os-bind`.
- Published-channel verification continues to invoke the real installer.

- [ ] **Step 1: Write failing workflow assertions**

Require every FreeBSD verification job to install and lock the exact per-series pinned creator archive after OPNsense repository setup and verify identity, lock, and executable hash again after dependencies are installed. Require archive verification and the replacement transaction itself to use its `pkg-static`. Require this ordering in development, staged, and published gates:

```text
pkg install -y -r OPNsense opnsense os-bind
archive checksum verification
Resolver Plugins install or real installer
official os-bind absence assertion
non-null installed-file checksum assertion
```

- [ ] **Step 2: Run workflow tests and confirm RED**

```sh
pytest -q .github/ci/ci-tests/test_package_release_workflow.py
```

Expected: target selection, official baseline, and checksum assertions are missing.

- [ ] **Step 3: Add target-native transition gates**

In each applicable FreeBSD VM, call `target_pkg.py .resolver-plugins/target-pkg.json "$series"` immediately after repository setup. Install `opnsense os-bind` from `OPNsense` in the disposable VM while the target `pkg` is locked, re-run the complete pinned identity/lock/hash check immediately before the transition, verify all staged archives through `/usr/local/sbin/pkg-static` with `package_checksums.py`, and perform the Resolver replacement through `/usr/local/sbin/pkg-static`. Re-run the complete check after the transaction. Add a stateful workflow fixture/test showing an attempted dependency or transition-time `pkg` upgrade cannot change the locked identity. After installation, require:

```sh
[ -z "$(pkg query -e '%n = os-bind' '%n')" ]
for package in bind-tools bind920 os-bind-rp; do
  pkg query -e "%n = $package" '%Fp|%Fs' |
    awk -F '|' 'NF != 2 || $2 == "" || $2 == "(null)" { exit 1 } END { if (NR == 0) exit 1 }'
done
pkg check -s bind-tools bind920 os-bind-rp
```

- [ ] **Step 4: Run workflow and full tests**

```sh
pytest -q .github/ci/ci-tests/test_package_release_workflow.py
pytest -q .github/ci/ci-tests
```

Expected: all tests pass.

- [ ] **Step 5: Commit workflow gates**

```sh
git add .github/workflows/package-release.yml .github/ci/ci-tests/test_package_release_workflow.py
git commit -m "ci: test the official os-bind replacement path"
```

### Task 8: Maintainer and operator documentation

**Files:**
- Modify: `docs/building.md`
- Modify: `docs/package-repository.md`
- Modify: `docs/package-channel-distribution-design.md`
- Modify: `.github/ci/ci-tests/test_package_documentation.py`

**Interfaces:**
- Documents `pkg_creator`, the archive verifier command, installer preflight artifacts, official replacement gate, and HA canary rollback boundaries.

- [ ] **Step 1: Add failing documentation assertions**

Require documentation to contain `pkg_creator`, `package_checksums.py`, `official os-bind`, `non-null`, `configuration backup`, and `target package manager` in the relevant guides.

- [ ] **Step 2: Run documentation tests and confirm RED**

```sh
pytest -q .github/ci/ci-tests/test_package_documentation.py
```

Expected: one or more new contract strings are absent.

- [ ] **Step 3: Update documentation accurately**

Describe the builder-only forced `pkg` selection, archive and installed-file gates, preflight failure behavior, preserved diagnostic directory, and official-package transition. Explicitly state that the installer does not upgrade the host package manager or change BIND service configuration.

- [ ] **Step 4: Run documentation and full verification**

```sh
pytest -q .github/ci/ci-tests/test_package_documentation.py
python3 -m py_compile .github/ci/*.py
sh -n .github/ci/build-bind920.sh .github/ci/build-os-bind-rp.sh scripts/install-os-bind-rp.sh
pytest -q .github/ci/ci-tests
git diff --check
```

Expected: all commands exit `0`.

- [ ] **Step 5: Commit documentation**

```sh
git add docs/building.md docs/package-repository.md docs/package-channel-distribution-design.md .github/ci/ci-tests/test_package_documentation.py
git commit -m "docs: explain package compatibility upgrade safeguards"
```

### Task 9: Prepare revision-bumped release sources

**Files:**
- Modify on `release/bind-rp/26.1`: `dns/bind/Makefile`
- Modify on `release/bind-rp/26.7`: `dns/bind/Makefile`

**Interfaces:**
- 26.1 candidate identity: `os-bind-rp-1.36_10`.
- 26.7 candidate identity: `os-bind-rp-1.36_3`.
- No source, conflict, dependency, or provenance fields change.

- [ ] **Step 1: Create isolated release worktrees from the current remote branches**

Use separate local branches and verify each worktree is clean before editing. Do not rewrite or force-update either release branch.

- [ ] **Step 2: Write a failing identity check before each edit**

Run:

```sh
test "$(awk '/^PLUGIN_REVISION=/{print $2}' dns/bind/Makefile)" = 10
```

on 26.1 and the equivalent expected value `3` on 26.7.

Expected: each check exits non-zero against revisions `9` and `2`.

- [ ] **Step 3: Increment only `PLUGIN_REVISION`**

Set 26.1 to `10` and 26.7 to `3`, preserving `PLUGIN_VERSION=1.36`, conflicts, dependency formula, and upstream provenance.

- [ ] **Step 4: Verify release-source metadata and diffs**

From the 26.1 release worktree run:

```sh
python3 /workspace/.worktrees/os-bind-rp-package-compat/.github/ci/metadata_profile.py .resolver-plugins/upstream.json 26.1 freebsd_release
git diff --check
git diff -- dns/bind/Makefile
```

From the 26.7 release worktree run the same three commands with the explicit metadata argument `26.7`.

Expected: metadata validation succeeds and the diff contains one revision line.

- [ ] **Step 5: Commit each release-source bump locally**

```sh
git add dns/bind/Makefile
git commit -m "build: revise os-bind-rp for compatible package manifests"
```

Do not push either commit without explicit authorization.

### Task 10: Local code review, simplification, and fresh verification

**Files:**
- Review all files changed by Tasks 1-9.

**Interfaces:**
- No new behavior; this is the final local quality gate before live HA testing.

- [ ] **Step 1: Run the required simplification and documentation review**

Check that checksum parsing is implemented once in Python build code, installer shell functions remain bounded, error messages name exact artifacts without contents, and documentation matches actual defaults. Remove duplication only while tests remain green.

- [ ] **Step 2: Run fresh complete verification**

```sh
python3 -m py_compile .github/ci/*.py
sh -n .github/ci/build-bind920.sh .github/ci/build-os-bind-rp.sh .github/ci/setup-opnsense-repository.sh scripts/install-os-bind-rp.sh
pytest -q .github/ci/ci-tests
git diff --check
git status --short --branch
```

Expected: compilation and syntax checks exit `0`, all tests pass, no whitespace errors exist, and only intentional commits/files appear.

- [ ] **Step 3: Commit any review corrections**

Stage only reviewed files and use a narrow `fix:` or `docs:` commit message. If no correction is needed, make no empty commit.

- [ ] **Step 4: Run the required independent code-review agent**

Ask a fresh review agent to inspect the complete committed diff against the approved design and this plan for correctness, security, test quality, simplification, and documentation accuracy. The agent must categorize findings by priority and explicitly decide whether the branch is ready for the live canary.

- [ ] **Step 5: Remediate and re-review until approved**

Resolve every high-priority finding with a failing regression test first, rerun the complete verification command from Step 2, commit the correction, and send the new diff to a fresh review turn. Do not begin HA-2 mutation until the reviewer reports no high-priority findings and approves the branch for canary testing.

### Task 11: HA-2 rollback capture and official-package baseline

**Files:**
- Temporary remote directory only: `/var/backups/os-bind-rp-canary-<UTC timestamp>` on HA-2.
- Temporary local records only: `.github/ci-local/ha2-canary-<UTC timestamp>/`.

**Interfaces:**
- HA-1 hostname: `opnsense-ha-1.home.internal.bkwfamily.net`.
- HA-2 hostname: `opnsense-ha-2.home.internal.bkwfamily.net`.
- Rollback package set: current `bind-tools`, `bind920`, and `os-bind-rp` archives created by HA-2 `pkg` 2.3.1_1.

- [ ] **Step 1: Prove HA-1 health immediately before mutation**

Over approved SSH, capture OPNsense/package identities, CARP status and roles, `pgrep`/`named -V`, `named-checkconf`, managed service status, DNS VIP ownership, and representative direct-node and VIP recursive queries. Require HA-1 active, HA-2 backup, and the VIP served by HA-1. Identify and bound configuration synchronization so the package canary cannot propagate a package/configuration change to HA-1. Abort HA-2 mutation if any check fails.

- [ ] **Step 2: Capture HA-2 state and rollback artifacts**

Create an explicit mode-0700 timestamped backup directory, copy `/conf/config.xml` with metadata preservation, copy `/var/db/pkg/local.sqlite` as read-only evidence only, and copy the existing production Resolver Plugins repository configuration and public key with modes preserved. Record their SHA-256 values, enabled state, and production URL. Record scoped package/service/query state, and run:

```sh
pkg create -o "$backup/packages" bind-tools bind920 os-bind-rp
```

Verify exactly three archives, query their identities and dependency records, prove they form the complete saved Resolver package set, and inspect each with `%Fp|%Fs`; these target-created archives must contain no null checksums. Run a dry-run in an isolated local repository to prove the saved archives can reinstall together before altering the baseline.

- [ ] **Step 3: Build a one-use signed local candidate repository**

Generate a local RSA key with mode `0600`, derive its public key, place only the public key and the three target-created packages under a host-local `pkg-26.1` directory, run `pkg repo` with the private key, record asset SHA-256 values, and keep the private key only in the mode-0700 backup directory. Record that this canary proves target-created manifest and installer compatibility but is not a substitute for the source-built, revision-bumped workflow gate required before production promotion.

- [ ] **Step 4: Prepare the canary installer copy**

Copy the committed installer to HA-2's backup directory and alter only its local copy of `release_base` and `public_key_sha256` to the `file://` repository and one-use public key. Compare the remaining script body to the committed installer so no logic differs. Invoke the canary with `RP_PKG_REPOSITORY_DIR` and `RP_PKG_KEYS_DIR` pointing to dedicated subdirectories below the backup directory; the revised installer must scope repository operations to those paths, so it never overwrites the production configuration or public key.

- [ ] **Step 5: Establish the official `os-bind` baseline**

Record the available official identity with `pkg rquery -r OPNsense`. Preview `pkg delete -n os-bind-rp` and require that only `os-bind-rp` is removed. Delete only that plugin package, then preview `pkg install -n -r OPNsense os-bind` and require that only official `os-bind` is added, with no core, package-manager, BIND, or unrelated changes. Install only `os-bind`—never `opnsense`—from the OPNsense repository. Require official `os-bind` present, `os-bind-rp` absent, the saved BIND pair still registered, valid `named-checkconf`, a running managed BIND service, and successful representative queries.

- [ ] **Step 6: Stop and roll back on any baseline failure**

Stop managed BIND, remove official `os-bind` if it is registered, install the exact saved `bind-tools`, `bind920`, and `os-bind-rp` archives from the proven local rollback repository, restore `/conf/config.xml`, reload templates, restart BIND, and run the full HA-2 health gates. Do not copy the saved `local.sqlite` over the live database. Limit any residue cleanup to paths listed by those archives and confirmed unowned. Re-run HA-2 health checks before continuing or reporting the actual blocker.

### Task 12: HA-2 exact replacement, reboot, and acceptance

**Files:**
- Uses only the temporary HA-2 canary directory and local ignored evidence directory from Task 11.

**Interfaces:**
- Consumes the local signed repository and canary installer copy from Task 11.
- Acceptance requires both pre-reboot and post-reboot results.

- [ ] **Step 1: Run the revised installer against the official baseline**

Provide `y` through a mode-0600 approval file via `RP_TTY_PATH`, use a caller-owned state directory, and capture stdout/stderr and exit status without printing sensitive configuration.

- [ ] **Step 2: Verify the exact package transition**

Require official `os-bind` absent; `os-bind-rp`, `bind920`, and `bind-tools` registered at candidate identities; every installed `%Fs` non-null; every archive-listed path owned; `pkg check -s` successful; and no unowned archive-listed residue.

- [ ] **Step 3: Verify BIND behavior before reboot**

Run `named-checkconf`, a managed BIND restart, process/version checks, representative recursive queries, and scoped crash/package/BIND log searches beginning at the test timestamp. Re-hash the persistent production Resolver Plugins repository configuration and public key, require them to match the captured bytes/modes/enabled state, require the production URL and committed public-key fingerprint, and run a production `pkg update -r resolver-plugins` before reboot acceptance.

- [ ] **Step 4: Reboot only HA-2 and wait through approved SSH polling**

Issue a normal HA-2 reboot. Poll the exact approved HA-2 hostname at bounded intervals, keeping commentary updates under 60 seconds, until SSH and the managed BIND service return or the bounded boot window expires.

- [ ] **Step 5: Repeat full acceptance after reboot**

Repeat package identities, per-file checksums, ownership, `pkg check -s`, `named-checkconf`, service/process version, recursive queries, and scoped log checks. Compare representative DNS results and expected HA configuration state with read-only HA-1 checks.

- [ ] **Step 6: Recover HA-2 if any acceptance gate fails**

Stop managed BIND; remove official `os-bind` if present; reinstall exact saved `bind-tools`, `bind920`, and `os-bind-rp` archives from the previously dry-run rollback repository; restore `/conf/config.xml`; restore the captured production repository configuration/key only if their hashes differ; reload templates; restart BIND; verify the production URL/key/fingerprint with `pkg update -r resolver-plugins`; and repeat health checks. Never overwrite the live package database, mutate HA-1, or install the OPNsense core package. Preserve failure evidence and report the exact unmet gate.

- [ ] **Step 7: Remove one-use private material after success**

Delete only the explicit one-use private key and approval file after recording non-secret asset hashes and successful results. Retain the one-use public key, isolated canary configuration/catalogue, and timestamped configuration/package rollback directory so later rollback verification remains possible until the user chooses to remove them.

- [ ] **Step 8: Record final evidence**

Record exact installed versions, `pkg` version, checksum counts, package-check result, daemon version, query results, reboot timestamp, and HA comparison in the ignored local evidence directory. Do not publish or commit runtime logs.
