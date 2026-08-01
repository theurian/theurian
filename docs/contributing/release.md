# Release process

Theurian Core and the Claude Code plugin release **independently**
([ADR-0001](../adr/0001-monorepo-with-independent-artifacts.md)). A Core release
does not imply a plugin release, and the reverse. Either group of maintainers can
cut a release of their own artifact; neither can force the other's.

## Versioning

Semantic Versioning per artifact.

| | Pre-1.0 | Post-1.0 |
| :-- | :-- | :-- |
| Breaking change | MINOR | MAJOR |
| New feature | MINOR | MINOR |
| Fix | PATCH | PATCH |

Pre-1.0, a MINOR bump may break the protocol, which is why plugin
`coreCompatibility.maximumExclusive` pins to the next MINOR rather than the next
MAJOR.

## Releasing Core

### 1. Prepare

```sh
git switch -c release/core-0.2.0
uv sync --frozen
uv run ruff format --check packages tests
uv run ruff check packages tests
uv run mypy
uv run pytest --cov
```

Then confirm the things CI checks that are easy to forget:

- Every dependency is pinned with `==` and `uv.lock` is committed.
- The empty-database rebuild produces the golden canonical state.
- `CURRENT_PROTOCOL_VERSION` matches what the CHANGELOG claims.

### 2. Version and changelog

Update `packages/theurian-core/pyproject.toml` and `__version__`, then move the
`[Unreleased]` section of `packages/theurian-core/CHANGELOG.md` under the new
version with a date.

Write the changelog for someone deciding whether to upgrade. "Fixed a bug" tells
them nothing; "index builds no longer publish a partially built RAPTOR tree when
cancelled mid-build" tells them whether they were affected.

### 3. Protocol check

If the protocol changed, say so prominently and list every client that must
update. Then, in this order:

1. release Core;
2. update each client's `coreCompatibility` and `protocolVersion`;
3. release the clients.

Clients that stop working loudly are recoverable. Clients that keep working
against a changed contract are not.

### 4. Tag

```sh
git commit -s -m "chore(core): release 0.2.0"
git tag -s core-v0.2.0 -m "Theurian Core 0.2.0"
git push origin release/core-0.2.0 --tags
```

Core tags are `core-v*`; plugin tags are `plugin-v*`. Two release trains in one
repository need unambiguous tag namespaces.

### 5. Verify the artifacts

CI builds, verifies, and publishes checksums. Confirm before announcing:

```sh
uv build --package theurian
uv venv /tmp/verify
VIRTUAL_ENV=/tmp/verify uv pip install dist/theurian-0.2.0-py3-none-any.whl
/tmp/verify/bin/theurian version --json
sha256sum dist/*
```

Every release carries SHA-256 checksums and a CycloneDX SBOM. `/theurian:setup`
verifies the checksum before installing and aborts rather than installing an
artifact it could not verify (T-16).

### 6. Publish

- PyPI: `theurian`
- Docker: `ghcr.io/theurian/theurian:0.2.0` and `:latest`
- GitHub release: changelog section, checksums, SBOM

## Releasing the plugin

### 1. Prepare

```sh
uv run pytest packages/theurian-core/tests/unit/test_plugin_boundary.py -v
shellcheck --severity=warning plugins/claude-code/scripts/*.sh
claude plugin validate ./plugins/claude-code --strict
```

### 2. Version and compatibility

Update `version` in `.claude-plugin/plugin.json` **and** `pluginVersion` in
`compatibility.yaml`. They must agree — a test asserts it.

Review the range against the Core you actually tested with:

```yaml
pluginVersion: 0.2.0
coreCompatibility:
  minimum: 0.2.0
  maximumExclusive: 0.3.0
protocolVersion: theurian/v1
```

> Claude Code caches a plugin by its declared version. Shipping changes without
> bumping `version` means users keep the cached copy and `/plugin update` reports
> "already at the latest version".

### 3. Verify against Core

```sh
uv run theurian compat check \
  --plugin-version 0.2.0 --core-minimum 0.2.0 \
  --core-maximum-exclusive 0.3.0 --protocol-version theurian/v1 --json
```

### 4. Tag and publish

```sh
git commit -s -m "chore(plugin): release 0.2.0"
git tag -s plugin-v0.2.0 -m "Theurian Claude Code plugin 0.2.0"
git push origin --tags
```

Then update the marketplace entry.

## Compatibility matrix

Maintain this in the repository README as releases accumulate:

| Plugin | Core | Protocol |
| :-- | :-- | :-- |
| 0.1.x | ≥ 0.1.0-dev.0, < 0.2.0 | theurian/v1 |

## Release checklist

**Core**

- [ ] Format, lint, mypy, tests, coverage green
- [ ] Empty-database rebuild matches the golden state
- [ ] Every dependency pinned; `uv.lock` committed
- [ ] `pyproject.toml` and `__version__` agree
- [ ] CHANGELOG written for an upgrade decision
- [ ] Protocol change, if any, called out
- [ ] Tagged `core-v*`, signed
- [ ] Wheel installs into a clean environment and runs
- [ ] Checksums and SBOM published

**Plugin**

- [ ] Boundary tests, shellcheck, and `claude plugin validate` pass
- [ ] `plugin.json` version and `compatibility.yaml` agree
- [ ] The declared range matches the Core actually tested
- [ ] CHANGELOG updated
- [ ] Tagged `plugin-v*`, signed
- [ ] Marketplace entry updated

## Hotfixes

Branch from the tag, fix, PATCH bump, tag, publish, then forward-port to `main`.
Never fix only on `main` and hope the next release covers it — that is how a
hotfix gets reverted by the following release.

## Yanking

If a release is broken or has a vulnerability:

1. Yank from PyPI (do not delete — deletion breaks lock files that reference it).
2. Publish a fixed PATCH release immediately.
3. If it is a vulnerability, publish a security advisory per
   [SECURITY.md](../../SECURITY.md).
4. Untag only if the release never reached users.
