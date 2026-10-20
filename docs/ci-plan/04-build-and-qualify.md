# Phase 04: Build and qualify

Status: planned
Before: `prepare` works (phase 03); build and qualify are stubs.
After: a production or candidate request produces the
`qualified-<component>-<series>-<source_sha>` artifact: the archive built in a
FreeBSD VM of the series' release, its build record, and the source bundle,
installed and checked on a clean OPNsense. Publication is still a stub.

Rules: the specification's "Simplicity and limits", "Failed jobs stay failed"
and "Testing decisions" apply ([spec](../ci-rebuild-spec.md)). Nothing is run:
review by reading the diff and `git diff --check`. Stop and ask before any
inline or CI script exceeds 30 physical lines. Commit once per phase with
`[skip ci]`; nothing is pushed or dispatched.

Shared references: design, "Lifecycle", "Artifacts between jobs", "Data
formats", "Upstream discovery" (last bullet), and spec, "Signing and
publication" (qualification) ([design](../ci-rebuild-design.md)); step names
and env in [02-ci-structure.md](02-ci-structure.md#lifecycle-interface).

Facts the stages rely on (from the plugins framework in `Mk/`):
`make package` writes `<source_directory>/work/pkg/<name>-<version>.pkg`;
without `PLUGIN_DEVEL=` the framework builds a `-devel` package; plugin
manifests declare `abi: "FreeBSD:*:*"`, so the archive does not carry the
target ABI; `make package` installs `PLUGIN_DEPENDS` with `pkg install -yA`.
The old qualification initialized core with: install `/usr/local/etc/config.xml`
as `/conf/config.xml` (dir 0750, file 0640), `sh /usr/local/etc/rc.subr.d/php`,
`service configd onestart`, `/usr/local/opnsense/service/template_ctl.py
OPNsense/WebGui`.

## Stages

| Stage | Goal | Model | Effort | Order | Status |
| --- | --- | --- | --- | --- | --- |
| 04.1 | OPNsense repository configuration step | sonnet | low | first | planned |
| 04.2 | Build job | sonnet | medium | after 04.1 | planned |
| 04.3 | Qualify job | sonnet | medium | after 04.2 | planned |

## 04.1 OPNsense repository configuration

```
Goal:            The three "Configure the OPNsense package repository" steps
                 write the series' OPNsense pkg configuration into
                 .opnsense-pkg/ in the workspace for the VM to copy.
Context:         Design, "Upstream discovery": the OPNsense package repository
                 for VMs comes from core's OPNsense.conf.shadow.in with
                 https://pkg.opnsense.org/${ABI}/<series>/latest. Core's
                 FreeBSD.conf.shadow disables the FreeBSD repositories for the
                 series; its fingerprints directory holds the trust anchors.
                 The step appears in build and qualify (plugin-target.yml) and
                 in the index's stage job (repository-index.yml), each with
                 SERIES and CORE_COMMIT in the job env.
Contracts:       .opnsense-pkg/repos/OPNsense.conf, .opnsense-pkg/repos/FreeBSD.conf,
                 .opnsense-pkg/fingerprints/OPNsense/{trusted,revoked}/…
                 Each VM copies repos/* to /usr/local/etc/pkg/repos/ and
                 fingerprints/OPNsense to /usr/local/etc/pkg/fingerprints/.
Files:           .github/workflows/plugin-target.yml (build and qualify steps)
                 .github/workflows/repository-index.yml (stage step)
Work:            The same body in all three:
                 1. git init -q $RUNNER_TEMP/core; add remote origin
                    https://github.com/opnsense/core.git; git fetch -q --depth 1
                    --filter=blob:none origin "$CORE_COMMIT"; git checkout -q
                    FETCH_HEAD -- src/etc/pkg.
                 2. mkdir -p .opnsense-pkg/repos; sed replacing
                    %%CORE_PACKAGESITE%% with https://pkg.opnsense.org and
                    %%CORE_ABI%% with $SERIES in OPNsense.conf.shadow.in into
                    .opnsense-pkg/repos/OPNsense.conf.
                 3. cp FreeBSD.conf.shadow to .opnsense-pkg/repos/FreeBSD.conf;
                    cp -R the fingerprints directory into .opnsense-pkg/.
External inputs: core's files at core_commit; pkg verifies the catalogue
                 signature itself, so no further checks.
Done when:       Three identical bodies of about ten lines, no hard-coded series.
Out of scope:    The VM steps.
```

## 04.2 Build job

```
Goal:            The build job builds the prepared source with make package in
                 a FreeBSD VM of the series and uploads the archive with its
                 build record.
Context:         Design, "Lifecycle": build runs make package from the prepared
                 source, holds no secrets. Phase 02 declares the steps; 04.1
                 fills the repository step. The source bundle has HEAD with
                 prerequisite source_base, which the full checkout contains.
Contracts:       build-record.json fields, all strings: package, origin, version
                 (from `pkg query -F <archive> '%n|%o|%v'`), series, abi
                 (prepare's target ABI), freebsd_release, source_base,
                 master_commit (inputs.source_sha), source_commit
                 (prepared_sha), control_commit ($GITHUB_WORKFLOW_SHA),
                 tools_tag, core_commit, plugins_commit,
                 upstream_package_commit, pkg_version (`pkg -v` in the VM),
                 previous_tag, run_url. The VM leaves .build/<archive>,
                 .build/identity and .build/pkg-version in the workspace.
Files:           .github/workflows/plugin-target.yml (build job bodies: "Check
                 out the prepared source", the VM run, "Write the build record")
Work:            1. Check out: git fetch -q "$RUNNER_TEMP/source/source.bundle"
                    HEAD; git checkout -q --detach "$PREPARED_SHA".
                 2. VM run (sh, set -eu): mkdir the pkg repos and fingerprints
                    directories; copy .opnsense-pkg in; pkg-static install -fy
                    -r OPNsense pkg; pkg install -y -r OPNsense opnsense; make -C
                    "$SOURCE_DIRECTORY" PLUGIN_DEVEL= PLUGIN_ABI="$SERIES"
                    PLUGIN_HASH="$PREPARED_SHA" package; mkdir .build; copy the
                    archive; write .build/identity and .build/pkg-version.
                 3. Record: read identity with IFS='|' read; jq -n '$ARGS.named'
                    with one --arg per field, into .build/build-record.json.
External inputs: the OPNsense repository (pkg verifies it).
Done when:       One VM step, no secrets, no checksum rewrite, every record
                 field filled, each body under 30 lines.
Out of scope:    Qualification (04.3).
```

## 04.3 Qualify job

```
Goal:            The qualify job installs the archive with its dependencies on a
                 clean OPNsense of the series and checks it against the record.
Context:         Spec, "Signing and publication": qualify the actual archive
                 once in a clean matching environment and reuse that result.
                 Design, "Lifecycle": clean VM, no secrets. Because the
                 manifest's ABI is a wildcard, installing on a VM of the series'
                 FreeBSD release is what shows ABI compatibility.
Contracts:       EXPECTED = "<package>|<origin>|<version>" from the record. The
                 archive is served from an unsigned local repository named
                 `candidate` so pkg resolves dependencies from OPNsense.
Files:           .github/workflows/plugin-target.yml (qualify bodies: "Read the
                 expected identity", the VM run)
Work:            1. Expected identity: echo "EXPECTED=$(jq -r '[.package,
                    .origin, .version] | join("|")' dist/build-record.json)" >>
                    "$GITHUB_ENV".
                 2. VM run (sh, set -eu): configure the OPNsense repository as in
                    04.2; install pkg and opnsense; initialize core as listed at
                    the top of this file; pkg repo dist; write
                    /usr/local/etc/pkg/repos/candidate.conf with url
                    file://$PWD/dist and signature_type none; pkg update -f;
                    name=${EXPECTED%%|*}; pkg install -y "$name"; pkg check -s
                    "$name"; test "$(pkg query '%n|%o|%v' "$name")" =
                    "$EXPECTED".
External inputs: the OPNsense repository.
Done when:       One clean VM, no copyback, no secrets, install by name, pkg
                 check -s, identity compared, VM body under 30 lines.
Out of scope:    Plugin test suites (spec forbids them in CI).
```

## Notes

- Build, qualify and the index's stage job each carry the same short
  repository step, over a shared helper, because the design has no helper
  component (2026-10-20).
- The record's `abi` is the target ABI from `prepare`, since the archive's own
  ABI is the framework's wildcard (2026-10-20).
- Conflict behavior is qualified by `pkg` itself during the install; workflows
  name no packages, so there is no package-specific conflict test (2026-10-20).
- Failure diagnostics are the normal Actions logs, which include the VM output;
  no extra diagnostics artifact is uploaded (2026-10-20).
