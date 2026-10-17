# The Python package: install, verify, and how it is built

Besides the Claude Code skill and plugin, every release publishes ReachWeaver as a standard Python package: a **wheel** and a **source distribution**, attached to the GitHub Release next to `skill.zip`. It is for people who want the `reachweaver` command on a server or in a script. The Claude Code plugin ([using ReachWeaver with Claude Code](CLAUDE-CODE.md)) stays the main route.

## Install

You need Python 3.11 or newer. There are no dependencies to download.

```bash
pipx install reachweaver                      # from PyPI (first published with 0.5.2)
python3 -m pip install reachweaver
```

Or install the wheel attached to a GitHub release:

```bash
pipx install "https://github.com/MSKazemi/reachweaver/releases/download/v0.7.0/reachweaver-0.7.0-py3-none-any.whl"
# or, inside a virtual environment:
python3 -m pip install "https://github.com/MSKazemi/reachweaver/releases/download/v0.7.0/reachweaver-0.7.0-py3-none-any.whl"
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
- **PyPI** is live as of 0.5.2 (<https://pypi.org/project/reachweaver/>). The release workflow's optional `pypi` job uploads the wheel and sdist that `publish` attached to the GitHub Release, by *trusted publishing* (no stored token; <https://docs.pypi.org/trusted-publishers/>): it re-downloads those two files, verifies them against `python-dist.sha256` and uploads exactly those bytes, so the PyPI files and the GitHub files have identical SHA-256 (checked for 0.5.2). It runs only for the repository owner, only while the repository variable `PYPI_PUBLISH` is `true`, and the `pypi` environment accepts `v*` tags only. Since 0.7.0 (2026-10-17, owner decision) the environment has no required reviewer, so a `v*` tag push that passes `verify` and `publish` uploads to PyPI without a manual approval; before that, each release waited for the owner's approval. The PyPI project was created by a *pending publisher* (owner `MSKazemi`, repository `reachweaver`, workflow `release.yml`, environment `pypi`). To stop publishing to PyPI, delete the variable `PYPI_PUBLISH` (or set it to anything but `true`); to bring the manual approval back, add yourself as a required reviewer of the `pypi` environment in Settings → Environments. An uploaded PyPI version cannot be replaced: a mistake needs a new version, and PyPI lets you yank (hide) a release but not reuse its number. If the job ever fails, the GitHub Release is already complete.

- **GitHub build-provenance attestations** (`actions/attest-build-provenance`, which lets users run `gh attestation verify`) would need `attestations: write` as well as `id-token: write` on the `publish` job. Not enabled; PyPI's own publish attestation (above) covers the PyPI copy.
- **Signing.** No signing key is configured; tags are annotated but unsigned.

## Honest limits

A green package check proves the files are what the checkout says and that the wheel installs and runs offline. It does not prove the code is secure, that a platform accepts what ReachWeaver sends, or that licenses and rights are cleared ([release playbook](RELEASE-PLAYBOOK.md)).
