# Tutorial: ship a plugin change

In this tutorial you take a small change to `os-bind-rp` from a branch to the
signed feed. You will open a PR, build and test a candidate, merge it, and see
the new version reach clients. The same steps apply to
`os-ha-dhcp-interface` with its own entry workflow.

You need write access to `resolver-plugins/plugins`, the GitHub CLI (`gh`)
signed in, and an OPNsense node of the active series for testing, ideally the
BACKUP node of a pair.

## 1. Make the change

```sh
git switch master
git pull
git switch -c bind-example
```

Edit a file under `dns/bind/src/` and add a line describing the change to
the changelog in `dns/bind/pkg-descr`: in the `Resolver Plugins changes`
block, under `On upstream <version>` for the upstream version the plugin is
on, newest first. (HA entries go under the series heading, such as `26.7`.)
Without the entry, the PR status fails. Run the plugin's tests locally; CI
runs none:

```sh
python3 -m pytest dns/bind/tests
```

Commit and push the branch. Do not use `[skip ci]` for a package change.

## 2. Open the PR

```sh
gh pr create --base master --fill
```

Within a minute the PR shows the status `Package build / dns/bind` as
pending: "Request a package build for this head". Nothing has been built yet.

## 3. Build a candidate

```sh
gh workflow run bind-plugin.yml -f pr=<number>
gh run list --workflow bind-plugin.yml
gh run watch <run-id>
```

The run prepares the release source with your change carried onto it, builds
the package in a FreeBSD VM and installs it in a clean VM to qualify it. When
it finishes, the PR status turns green. The archive already carries the
version it will be published under.

## 4. Try it on a node

Download the qualified archive and install it on the test node, as in
[Test a candidate on an appliance](how-to.md#test-a-candidate-on-an-appliance).
Check that your change behaves as intended.

## 5. Merge

Merge the PR. The merge starts a promotion run in `bind-plugin.yml`. It skips
building: it publishes the archive you tested, advances the current Release,
pushes the release branch and refreshes the series' signed feed.

## 6. See it on a client

On any node that uses the feed:

```sh
pkg update -f -r resolver-plugins
pkg rquery -r resolver-plugins '%n %v' os-bind-rp
```

The output shows the new version, and `pkg upgrade os-bind-rp` installs it.
The node you tested on already runs exactly this archive.

## What you have done

You have followed one change through the whole pipeline: PR status, a
candidate built once, an appliance test, promotion without a rebuild, and
publication to the signed feed. The [explanation](explanation.md) describes
why the pipeline works this way, and the [how-to guides](how-to.md) cover
failures, rollbacks and other tasks.
