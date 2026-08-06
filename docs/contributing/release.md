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

**`-s` must sign with a key GitHub holds for an account in `RELEASE_SIGNERS`.**
That list is declared at the top of
[`release-core.yml`](../../.github/workflows/release-core.yml) and holds `utchy`
today. CI assembles a trust root from those accounts' registered keys and runs
`git verify-tag` against it, so a locally valid signature made with a key that is
not on the account fails the release. Either signing format works — `git
verify-tag` picks the verifier from the signature.

There is no human verification step to pair with this. CI checks the same thing a
maintainer would, against the same keys, so a second pass by hand would be
ceremony. What is worth doing before tagging is confirming the key you are about
to sign with is the one GitHub has, because that is the failure this catches:

```sh
# SSH signing (gpg.format=ssh), with user.signingkey a path to the .pub file.
# Prints "registered" or "NOT registered".
gh api users/utchy/ssh_signing_keys --jq '.[].key' \
  | grep -qxF "$(cut -d' ' -f1,2 "$(git config --get user.signingkey)")" \
  && echo registered || echo "NOT registered"

# OpenPGP signing. The key ID you sign with must appear in this list.
gh api users/utchy/gpg_keys --jq '.[].key_id'
```

**Pushing the tag is the release.** `.github/workflows/release-core.yml` takes
over from here; there is no manual publish step and no maintainer holds a PyPI
credential.

### 5. What CI does with the tag

[`release-core.yml`](../../.github/workflows/release-core.yml), in order:

1. runs the full quality gate — a tag push does not trigger `core.yml`, so
   without this a red commit could be tagged and published;
2. refuses the tag unless `core-v<version>` matches `pyproject.toml`, the
   installed package reports the same version, the tag's signature **verifies**
   against a key registered to an account in `RELEASE_SIGNERS`, and
   `CHANGELOG.md` has a non-empty section for it;
3. builds, then installs the wheel into a clean environment and runs
   `theurian version --json` against it;
4. produces a reproducible CycloneDX 1.6 SBOM from that verified environment —
   from what a user installs, not from the lock file — and `SHA256SUMS` over
   every artifact including the SBOM;
5. publishes to PyPI over Trusted Publishing with PEP 740 attestations;
6. cuts the GitHub release with the changelog section, checksums, and SBOM.

To exercise every step except publication, run the workflow manually with
`dry_run` left at its default.

### Checksums are published; nothing verifies them at install time

Steps 4 and 6 satisfy the publication half of T-16 (OSS-7, OSS-11): the record a
verifier would check against exists on every release. **The verifying half does
not exist.** `theurian setup`'s artifact-integrity step is an unconditional
`return` of `not-applicable` —
[`setup_steps.py`](../../packages/theurian-core/src/theurian/application/setup_steps.py),
`probe_artifact_integrity` — so `theurian setup --dry-run --json` reports this,
on a machine with a release installed or without one:

```json
{
  "action": "",
  "detail": "Setup never downloads or installs Core, and no code in Theurian compares an installed artifact against a checksum. Checking a download against the checksums published with it is a manual step, tracked at https://github.com/theurian/theurian/issues/39 (T-16).",
  "id": "artifact-integrity",
  "outcome": "not-attempted",
  "paths": [],
  "status": "not-applicable",
  "summary": "Theurian does not verify the artifact it is running from."
}
```

Dedented for reading: in real output this is one element of the `steps` array,
so its fields sit at six spaces rather than two. The values are byte-identical,
and
`tests/unit/test_artifact_integrity_claim.py::test_the_release_document_quotes_what_setup_actually_publishes`
parses this block and compares it to the step's published JSON, so the quotation
cannot drift from the code.

**Neither string says whether a record to check against exists**, and that is
deliberate. They used to — "No signed release manifest exists yet; nothing to
verify against" — which was true only until step 4 above ran for the first time.
One function ships on both sides of that tag, so it states a property of
Theurian rather than one of the world.

Two things are missing, not one. There is no code that hashes an artifact and
compares it to `SHA256SUMS`, and there is no point in setup where such code would
run: setup does not download or install Core. It checks that a `theurian`
executable is already present and, when it is not, tells the user to run
`uv tool install theurian` or `pipx install theurian`. So a user's integrity
guarantee today is whatever their installer and PyPI give them — Theurian
publishes PEP 740 attestations, but nothing in Theurian checks anything, and
whether a given installer checks them is that installer's behaviour.

Checking a download against `SHA256SUMS` is a manual step until this lands. T-16
stays open on this half; the residual is recorded under T-16 in
[the threat model](../security/threat-model.md).

### 6. Confirm before announcing

```sh
uv venv /tmp/verify
VIRTUAL_ENV=/tmp/verify uv pip install theurian==0.2.0
/tmp/verify/bin/theurian version --json
```

Docker (`ghcr.io/theurian/theurian`) is not yet automated; it is published by
hand until a `Dockerfile` lands.

## One-time setup: PyPI Trusted Publishing

No PyPI token exists in this repository's secrets, and none should. Publication
authenticates with a short-lived OIDC token minted per run, so there is nothing
to leak, rotate, or scope wrongly.

Two things must exist before the first release:

**1. A GitHub environment named `pypi`.** Settings → Environments → New
environment. Add required reviewers if a release should need a second pair of
eyes; the workflow will wait for them.

**2. A trusted publisher on PyPI**, registered at
<https://pypi.org/manage/account/publishing/> — as a *pending* publisher until
the project exists, which is what bootstraps the first upload:

| Field | Value |
| :-- | :-- |
| PyPI project name | `theurian` |
| Owner | `theurian` |
| Repository name | `theurian` |
| Workflow name | `release-core.yml` |
| Environment name | `pypi` |

The workflow filename and the environment name are part of the credential. A
renamed workflow file stops publishing until the publisher is updated to match —
which is the point.

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

Nothing verifies this signature. No workflow triggers on `plugin-v*` —
`release-plugin.yml` does not exist yet — so `-s` here is a convention, not a
gate, and the `RELEASE_SIGNERS` trust root above does not reach this train.

## Compatibility matrix

Maintain this in the repository README as releases accumulate:

| Plugin | Core | Protocol |
| :-- | :-- | :-- |
| 0.1.x | ≥ 0.1.0-dev.0, < 0.2.0 | theurian/v1 |

## Release checklist

**Core.** Items marked *(CI)* fail the release workflow rather than relying on
anyone remembering them; they are listed so it is clear what is already covered,
not so they are checked twice.

- [ ] *(CI)* Format, lint, mypy, tests green
- [ ] Coverage reviewed
- [ ] Empty-database rebuild matches the golden state
- [ ] Every dependency pinned; `uv.lock` committed
- [ ] *(CI)* `pyproject.toml`, `__version__`, and the tag all agree
- [ ] *(CI)* CHANGELOG has a non-empty section for the version
- [ ] CHANGELOG written for an upgrade decision, not just present
- [ ] Protocol change, if any, called out
- [ ] *(CI)* Tag is `core-v*` and its signature **verifies** against a key
      registered to an account in `RELEASE_SIGNERS`
- [ ] The key you are signing with is registered on that account (§4) — this is
      the precondition, and the only half of the old "verify against the
      maintainer keyring" item still left to a human. CI does the verifying now;
      it did not when that item was written
- [ ] *(CI)* Wheel installs into a clean environment and runs
- [ ] *(CI)* Checksums and SBOM published

**Plugin**

- [ ] Boundary tests, shellcheck, and `claude plugin validate` pass
- [ ] `plugin.json` version and `compatibility.yaml` agree
- [ ] The declared range matches the Core actually tested
- [ ] CHANGELOG updated
- [ ] Tagged `plugin-v*`, signed — by hand; no workflow triggers on `plugin-v*`,
      so nothing checks this
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
