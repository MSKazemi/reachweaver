# The Python package: install, verify, and how it is built

Besides the Claude Code skill and plugin, every release publishes ReachWeaver as a standard Python package: a **wheel** and a **source distribution**, attached to the GitHub Release next to `skill.zip`. It is for people who want the `reachweaver` command on a server or in a script. The Claude Code plugin ([using ReachWeaver with Claude Code](CLAUDE-CODE.md)) stays the main route.

## Install from a release

You need Python 3.11 or newer. There are no dependencies to download.

```bash
pipx install "https://github.com/MSKazemi/reachweaver/releases/download/v0.5.1/reachweaver-0.5.1-py3-none-any.whl"
# or, inside a virtual environment:
python3 -m pip install "https://github.com/MSKazemi/reachweaver/releases/download/v0.5.1/reachweaver-0.5.1-py3-none-any.whl"
reachweaver version
```

To check the download first, fetch the wheel and `python-dist.sha256` from the [release page](https://github.com/MSKazemi/reachweaver/releases) into one folder and run `sha256sum --check python-dist.sha256` (it lists the wheel and the source archive; checking only the file you have is fine with `--ignore-missing`).

A pip-installed package is updated by installing a newer wheel. `reachweaver update --apply` only replaces an installed *skill bundle* and refuses on a pip install, on purpose. Your data (`config/`, `state/`) is separate from the program either way; see [your workspace folder](MOVING.md).

## What is in the package

- The `reachweaver` command and the `reachweaver` Python package: the runtime modules and the three bundled registries (capabilities, distribution channels, profile audits). Nothing else is installed.
- The license files (`LICENSE`, `NOTICE`, `DISCLAIMER.md`) and an Apache-2.0 license expression in the metadata.
- No runtime dependencies, by policy (`scripts/check_runtime_deps.py`).
- The source distribution is the same plus `pyproject.toml`, the README and the tests. It deliberately has no manifest file, so it can only hold the package: no docs, notes, state, databases, `.env` files, local accounts or security-review notes.

## How it is checked

CI (`.github/workflows/ci.yml`, job `package`) and the release workflow (job `verify`) run the same four steps with pinned tools, before anything is published:

1. `pyproject-build` builds the wheel and sdist (`build` 1.6.1).
2. `twine check --strict` confirms the metadata renders (`twine` 7.0.0).
3. `scripts/check_package.py` compares the archives with the checkout: version everywhere, every runtime module and registry present, entry point, license files, no dependency, Python >= 3.11, and nothing private in either archive.
4. `scripts/check_package.py --smoke` installs the wheel into a fresh virtual environment and runs `reachweaver version`, `capabilities` and `distribution-plan` from an empty folder, proving the bundled data is found without the checkout.

Locally: `make package` runs the same steps in a throwaway folder (it downloads `build` and `twine` through `pipx`). Build from a clean checkout when you intend to publish.

The release workflow's `publish` job rebuilds the package, checks it, writes `python-dist.sha256`, and attaches the wheel, the sdist and the checksum file to the GitHub Release with the skill bundle. It has exactly the one write permission it had before (`contents: write`, to create the release). The optional `pypi` job (below) is separate and only reads that release.

## What is not done, and why

- **GitHub Packages.** Not used. GitHub's introduction lists its registries as npm, RubyGems, Apache Maven, Gradle, Docker/OCI (the container registry) and NuGet; there is no Python registry (docs checked 2026-10-16: <https://docs.github.com/en/packages/learn-github-packages/introduction-to-github-packages>). Release assets are the GitHub-native home for a wheel. A container image on `ghcr.io` would be possible for server use but nothing needs it yet.
- **PyPI.** Prepared and **off**. The name `reachweaver` was free on 2026-10-16 (`https://pypi.org/pypi/reachweaver/json` answered 404). Publishing needs a PyPI account and a publisher on a third-party service, which only you can create. The release workflow has an optional `pypi` job that uploads the wheel and sdist that `publish` already attached to the GitHub Release, by *trusted publishing* (no stored token; <https://docs.pypi.org/trusted-publishers/>). It re-downloads those two files, verifies them against `python-dist.sha256`, and uploads exactly those bytes. It is skipped until you do the two things below, and then it still waits for your approval at the `pypi` environment on every release. Already done for you: the GitHub environment `pypi` exists, requires you as reviewer and only accepts `v*` tags.

  To turn it on:
  1. On PyPI (log in, then *Your projects* > *Publishing*, <https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/>), add a **pending publisher**: PyPI project name `reachweaver`, owner `MSKazemi`, repository `reachweaver`, workflow `release.yml`, environment `pypi`. A pending publisher does not reserve the name until the first upload, so release soon after creating it.
  2. Set the switch: `gh variable set PYPI_PUBLISH --body true --repo MSKazemi/reachweaver`.

  On the next tag: `verify` and `publish` run as before, then `pypi` waits for you to approve the `pypi` deployment in the Actions run, then uploads. After the first successful upload `pip install reachweaver` works, and PyPI shows the upload's publish attestation (the publish action generates it from the same OpenID Connect identity). To turn it off again, delete the variable. If the upload ever fails, the GitHub Release is already complete; nothing is half-published there.

  The job is covered by static tests (`tests/test_release_workflow.py`, `ReleaseWorkflowPyPITests`) but, by design, it has never run against PyPI: its first real run is the first release after you enable it. If it fails, fix the cause and release a new patch version; a published tag is never moved.
- **GitHub build-provenance attestations** (`actions/attest-build-provenance`, which lets users run `gh attestation verify`) would need `attestations: write` as well as `id-token: write` on the `publish` job. Not enabled; PyPI's own publish attestation (above) covers the PyPI copy.
- **Signing.** No signing key is configured; tags are annotated but unsigned.

## Honest limits

A green package check proves the files are what the checkout says and that the wheel installs and runs offline. It does not prove the code is secure, that a platform accepts what ReachWeaver sends, or that licenses and rights are cleared ([release playbook](RELEASE-PLAYBOOK.md)).
