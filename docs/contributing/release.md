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

Two things CI checks that are easy to forget locally, and they are separate
checks:

- **Every dependency is pinned with `==`** — `security.yml`'s `pinning` job,
  which reads the two `pyproject.toml` files and the workflows.
- **`uv.lock` is committed and current** — held by `uv sync --frozen`, in nine
  places across five workflows. The `pinning` job never reads `uv.lock`.

Both fail outside the release workflow, so they are worth seeing before the tag.

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

**Pushing the tag starts the release, and the run stops once for a human.**
`.github/workflows/release-core.yml` takes over from here. Nothing is uploaded
by hand and no maintainer holds a PyPI credential, but the run does pause before
the upload and wait for a `core-maintainers` approval on the `pypi`
environment — step 6 below.

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
5. **drafts** the GitHub release carrying the changelog section, the checksums
   and the SBOM — created, but visible only to accounts with push access;
6. publishes to PyPI over Trusted Publishing with PEP 740 attestations, after
   waiting for a `core-maintainers` approval on the `pypi` environment;
7. takes the release out of draft, which is the moment it becomes public.

**Steps 5 and 7 sit either side of step 6 on purpose.** PyPI refuses a second
upload of a filename it already holds, so an upload that succeeds cannot be
retried, only yanked. If the release were cut after the upload, a failure
between the two would leave a wheel that installs and checksums that exist
nowhere — the state T-16's shipped mitigation is supposed to rule out.

### Never address a release by its tag

**A tag does not identify a release.** Several releases can carry one tag —
`gh release create --draft` does not refuse a duplicate — and the commands that
take a tag then pick one of them by a rule nobody chose:

| Command | Measured behaviour with more than one release on the tag |
| :-- | :-- |
| `gh release edit <tag> --draft=false` | publishes the **oldest** draft, 4 times out of 4 |
| `gh release delete <tag>` | deleted the **published** release and left the draft |

Both resolve through the same lookup, which races a REST call against a GraphQL
one. So **every command below addresses a release by its numeric id**, and the
workflow does the same from creation onward. Find the id — never act on the
list without reading it first:

```sh
gh api repos/theurian/theurian/releases --paginate \
  --jq '.[] | {id, name, tag_name, draft, published_at}'               # find it

gh api -X PATCH repos/theurian/theurian/releases/<id> \
  -F draft=false -f make_latest=legacy                                 # publish

gh api -X DELETE repos/theurian/theurian/releases/<id>                 # delete
```

`make_latest=legacy` on the publish: the REST default is `true`, which would
point "Latest" at whatever published most recently rather than at the highest
version. `published_at` on the lookup: it is what distinguishes a draft that was
never published from a published release reverted by a re-tag, and those two want
opposite handling.

`--paginate` is not optional: without it you see the first page, and a page is
not the list.

`draft-release` refuses to start if the tag already has **any** release, draft
or published. That guard is the one thing the *old* order gave for free — `gh
release create` without `--draft` refuses a duplicate tag, so a re-run used to
fail loudly on its own — and it is what makes step 5 safe to re-run.

**There is no API backstop in front of the upload.** It is tempting to assume
one: the REST API does refuse a second *published* release on a tag with **HTTP
422**. But that refusal comes from step 7's `PATCH`, which is after the
irreversible step. Step 5 creates with `draft: true`, and posting a draft to a
tag that already carries a published release was measured returning **201**. So
the guard is the only thing standing between a mistake and PyPI — which is why
it matches every release on the tag rather than only drafts.

### The re-run rule, which is not the obvious one

**"Re-run failed jobs" does not re-run step 5 when step 5 succeeded — it reuses
the release id that run recorded.** So the two buttons are not
interchangeable, and choosing wrongly reaches the state this whole ordering
exists to prevent:

| You have | Use | Because |
| :-- | :-- | :-- |
| deleted the draft | **Re-run all jobs** | step 5 must run again to create a release and record a new id |
| deleted nothing, and nothing is on PyPI | either | |
| deleted nothing, and the upload succeeded | **Re-run failed jobs** | "Re-run all" stops at the guard, because the draft is still there — measured, `publish-pypi` skipped. It costs a run and changes nothing |

Deleting a draft and then pressing "Re-run failed jobs" was measured leaving a
version on PyPI and no release on GitHub: step 5 is skipped, step 6 succeeds
irreversibly, and step 7 fails with `HTTP 404` on an id that no longer exists.
**Never delete a draft and re-run only the failed jobs.**

And do not read the third row backwards. **Deleting the draft does not make
"Re-run all" safe once the upload has succeeded** — it removes the guard that
was harmlessly stopping you, and step 6 then meets a duplicate filename PyPI
will not accept. After a successful upload the draft is the record; publish it,
do not delete it.

### What each failure leaves, and what to do

Every cell below is **what this run did**, not what exists. A previous run may
have put this version on PyPI already — nothing in the workflow can see PyPI, so
check before you delete anything.

| Failed at | This run put on PyPI | This run left on GitHub | Do this |
| :-- | :-- | :-- | :-- |
| 1–4 | nothing | nothing | Fix and re-tag — safe as long as this version is not already on PyPI from an earlier run. Check, then see the re-tagging warning below. |
| 5 | nothing | a draft, possibly partial | Delete that draft **by id**, then **re-run all jobs**. Re-running without deleting fails at the guard, which is intended. |
| 6, declined or unapproved | nothing | a complete draft | Either approve and re-run the failed jobs, or delete the draft by id and **re-run all jobs**. Not: delete, then re-run failed. |
| 6, upload refused outright | nothing | a complete draft | Fix the cause, **re-run failed jobs**, and do not delete the draft. |
| 6, **partial** upload | some filenames permanently taken | a complete draft | No clean recovery — see below. |
| 7, `404` | full upload | nothing — the draft was deleted | The bad state. Download this run's artifact before it expires and cut the release by hand. |
| 7, other | full upload | a complete draft | Publish it by id. Nothing rebuilt, nothing yanked. |

**Do not delete and re-push a tag that already has a published release.**
Re-pushing silently reverts that release to a **draft** — measured — so it
vanishes from the releases page while its version stays installable from PyPI,
and the guard then blocks the next run. Rows 1–4 fail before anything is
published, so re-tagging is safe there; once step 7 has run, bump the version
instead.

**That state recovers completely, and deleting is the one thing that destroys
it.** A reverted release keeps its body, its assets and its URL. Publish it
back:

```sh
gh api -X PATCH repos/theurian/theurian/releases/<id> \
  -F draft=false -f make_latest=legacy
```

The trap is that `draft: true` looks the same either way. A draft that was never
published and a published release knocked back by a re-tag cannot be told apart
from that field alone — and they want opposite handling, because the second
one's wheel is already on PyPI. **Check `published_at`, and check PyPI, before
deleting anything**; the guard prints both, and the version's PyPI URL, when it
blocks. Deleting a reverted release and re-running leaves the wheel installable
with its record gone — step 6 then refuses the duplicate filename, so step 7
never runs to make a new one.

**A partial upload has no clean recovery.** twine sends files in order and stops
at the first failure, so the wheel can land while the sdist does not. Those
filenames are now permanently taken, `skip-existing` is deliberately unset (see
below), and no maintainer holds a PyPI credential to finish the upload by hand —
so the only route is to yank and release a new version.

The `404` row above has no clean recovery either, for a different reason: the
artifact is public and its record was deleted. Everything else on this page is
repaired without rebuilding or yanking. **Those two are the states worth
recognising before you touch anything**, and both are reached by acting between
steps rather than by any job failing on its own.

Two artifacts of the design worth knowing. `skip-existing` is left unset on
purpose: with it, a re-run would treat "this filename already exists" as success
and publish a GitHub release pointing at a PyPI artifact this run did not
upload. And `uv build` is byte-reproducible on this tree — two consecutive
builds of the same commit produce identical checksums — so duplicate drafts from
one commit carry the same checksum *values*; what differs between them is which
files they carry.

**One window stays open.** Between a successful upload and a successful step 7,
the wheel is installable while the draft holding its checksums is readable only
with push access. T-16's beneficiary is the person installing, so that window is
real; it lasts one job.

### A manual run proves less than it looks like

A manual run (`workflow_dispatch`) is the rehearsal path and publishes nothing:
steps 5–7 are all `if: github.event_name == 'push'`, so it stops once the
artifacts exist. It does exercise the quality gate, the changelog guard, the
build, the wheel install-back check that the installed package reports
`pyproject.toml`'s version, the SBOM and `SHA256SUMS`. There is no input to set:
the workflow carried a `dry_run` boolean wired to nothing, and it is gone.

What it cannot reach is most of what can go wrong. It skips the two checks that
read a tag — `core-v<version>` agreeing with `pyproject.toml`, and the signature
guard — and it skips steps 5, 6 and 7 entirely. **Nothing in the repository
tests those three steps either**: no test names them, and there is no
`actionlint`, `zizmor` or `yamllint`. A green rehearsal is evidence about the
build, and about nothing downstream of it.

### Checksums are published; nothing verifies them at install time

Steps 4, 5 and 7 satisfy the publication half of T-16 (OSS-7, OSS-11): on every
release the record a verifier would check against is produced, attached to the
release before the artifact is installable, and then made public. **The
verifying half does not exist.** `theurian setup`'s artifact-integrity step is
an unconditional `return` of `not-applicable` —
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
`uv tool install --python 3.13 'theurian[daemon]'` or
`pipx install --python 3.13 'theurian[daemon]'`. So a user's integrity
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

**1. A GitHub environment named `pypi`, with `core-maintainers` as a required
reviewer.** Not optional: §5 states the approval as a fact of the release
process, and `release-core.yml` rests an argument on it — `draft-release` holds
`contents: write` before the reviewer sees anything, and the reason that is
acceptable is that the reviewer stands between the draft and the upload. The
setting lives in GitHub, not in this repository, so nothing here enforces it.
Confirm it rather than recall it:

```console
$ gh api repos/theurian/theurian/environments/pypi \
    --jq '.protection_rules[] | select(.type == "required_reviewers")
          | {reviewers: [.reviewers[].reviewer.slug], prevent_self_review}'
{"prevent_self_review":false,"reviewers":["core-maintainers"]}
```

**What that approval is and is not worth today.** `core-maintainers` has one
member, `utchy` — the same account as `RELEASE_SIGNERS`. `prevent_self_review`
is `false` and `can_admins_bypass` is `true`, both measured. So the gate stops a
tag pushed by someone with write access who is not a maintainer, and it does not
stop a maintainer approving their own release. `prevent_self_review` is left off
deliberately: with one member, turning it on would make an ordinary release
impossible except through the admin bypass. Revisit all three settings when the
team gains a second member.

**2. A trusted publisher on PyPI**, registered at
<https://pypi.org/manage/account/publishing/> — as a *pending* publisher until
the project exists, which is what bootstraps the first upload:

| Field | Value |
| :-- | :-- |
| PyPI project name | `theurian` |
| Owner | `theurian` |
| Repository name | `theurian` |
| Workflow name | `release-core.yml` |
| Environment name | `pypi` — **fill this in; see below** |

The workflow filename and the environment name are part of the credential. A
renamed workflow file stops publishing until the publisher is updated to match —
which is the point.

### The environment row is a control, not a label

**PyPI marks that field optional** — its own documentation says *"environment is
optional but strongly recommended"* — and a publisher registered with it blank
is the single easiest way to lose the required reviewer without anyone noticing.

The reasoning, which is why it must not be left blank:

- GitHub puts an `environment` claim in the OIDC token only for a job that
  declares an environment — a job without one gets a token where the claim is
  **absent**, not empty, measured in a real run. `publish-pypi` is the only job
  in `release-core.yml` that declares `environment: pypi`.
- If the publisher records `pypi`, a token from any *other* job does not match
  it, and PyPI refuses with `invalid-publisher`. PyPI's troubleshooting page
  names this case directly: check whether "the workflow is using the same
  environment as configured when the publisher was configured on PyPI".
- So the required reviewer becomes **the one control a tag-pusher who rewrites
  this workflow cannot route around.** They can delete `environment: pypi` from
  the job, but the token then carries no environment claim and PyPI rejects it.

**Leave the field blank and that inverts.** Any job in `release-core.yml` can
publish, including one added with no `environment:` and therefore no reviewer —
and the tag-pusher chooses the commit the workflow is read from. The gate is
still in the file; it just no longer gates anything.

**One publisher, not one correct publisher.** Warehouse resolves a token by
looking for a publisher matching its `environment` claim and, failing that,
falling back to one registered with an empty environment. So a *second*
publisher for this same repository and workflow with a blank environment
disarms the reviewer completely, while the `pypi` one sits beside it looking
exactly right. The property to check is not "the `pypi` publisher is correct" —
it is **"`release-core.yml` in this repository has exactly one publisher, and
its environment is `pypi`."**

**Nothing in this repository can detect that.** PyPI's side is not observable
from CI, and a pending publisher is visible only to the account that registered
it, so there is no check to add and no failure to wait for. The threat model
records it as an assumption it cannot verify; this is where a human makes it
true. **Re-check it after any rename** of the workflow file, the repository or
the environment, because each of those forces the publisher to be re-registered.

## Releasing the plugin

### 1. Prepare

```sh
uv run pytest packages/theurian-core/tests/unit/test_plugin_boundary.py -v
shellcheck --severity=warning plugins/claude-code/scripts/*.sh
claude plugin validate ./plugins/claude-code --strict
```

### 2. Version and compatibility

Update `version` in `.claude-plugin/plugin.json` **and** `pluginVersion` in
`compatibility.yaml`. They must agree —
`test_the_version_claude_code_caches_is_the_version_the_plugin_declares` in
[`test_plugin_boundary.py`](../../packages/theurian-core/tests/unit/test_plugin_boundary.py)
asserts it.

Write `pluginVersion` as a plain unquoted scalar on one line. The SessionStart
hook does not parse YAML — `lib.sh` reads the line with `sed` — so a quoted
value is correct YAML that ships the quotes to `theurian compat check`, which
then answers `invalid-declaration`.
`test_the_shipped_hook_reads_the_same_version_the_yaml_parser_does` runs the
real `lib.sh` and holds this.

Both tests run in `plugin.yml`'s `manifest` job, which triggers when a pull
request or a push to `main` touches `plugins/**`, `schemas/protocol/**`, or
`plugin.yml` itself. Both files above are under `plugins/`, so bumping either
runs the check. They are also ordinary unit tests, so `core.yml`'s test matrix
runs them whenever it runs at all — the check is not confined to plugin-only
pull requests. What runs neither job is a change touching neither tree: a
docs-only pull request does not run this check.

It is also not a release gate. Nothing triggers on `plugin-v*` (§4), so a tag
cut from a commit that never went through a pull request is unchecked — which is
why the item stays on the checklist below.

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
not so they are checked twice. Items marked *(no check)* are the opposite: real
properties with nothing that verifies them, listed so their absence is visible at
the moment someone is deciding whether to tag.

- [ ] *(CI)* Format, lint, mypy, tests green
- [ ] Coverage reviewed
- [ ] Every dependency pinned; `uv.lock` committed
- [ ] *(no check)* The canonical state rebuilds from an empty database —
      [#64](https://github.com/theurian/theurian/issues/64). There is no command
      to run; this is a judgement about whether the release changed migration
      handling.
- [ ] *(no check)* `CURRENT_PROTOCOL_VERSION` agrees with what the CHANGELOG
      says about the protocol — [#74](https://github.com/theurian/theurian/issues/74)
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
- [ ] The `pypi` environment still has `core-maintainers` as a required
      reviewer — it is a GitHub setting, not a file in this repository, so
      nothing here fails if it is removed. One command, above under *One-time
      setup*
- [ ] **`release-core.yml` in this repository has exactly one PyPI publisher,
      and its environment is `pypi`.** Not "the `pypi` one looks right": a
      second publisher with a blank environment silently takes over, because
      warehouse falls back to it. Blank removes the required reviewer's teeth
      and nothing in CI can see it. Re-check after any rename of the workflow
      file, the repository or the environment, since each forces
      re-registration — *The environment row is a control, not a label*, above
- [ ] No leftover release on the tag you are about to push. `draft-release`
      refuses to run if there is one. **Check `published_at` and PyPI before
      deleting it** — a published release reverted by a re-tag also shows
      `draft: true`, and deleting that one destroys the record of a wheel users
      can already install. It is restored with `-F draft=false`, not deleted

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

**A vulnerability does not start here.** This procedure is public from the first
push, so the defect is described in a branch name, a pull request and a commit
message before anyone can install the fix.
[*Fixing a vulnerability privately*](#fixing-a-vulnerability-privately) below is
the route that is not, and this section is where its step 4 lands when the fix
cannot ship off `main`.

A hotfix publishes out of version order — `0.2.1` after `0.3.0` — which is the
one case where "Latest" can move backwards. Step 7 passes `make_latest=legacy`
so GitHub decides by version rather than by whichever published most recently;
without it the REST default is `true` and the hotfix takes the Latest badge off
`0.3.0`. Measured. Nothing to do here — it is noted because this is the
procedure that would otherwise hit it.

## Fixing a vulnerability privately

Everything above is public from the first push — the branch, the pull request,
and a commit message this project requires to say *why*, all of them describing
the defect before anyone can install the fix. [SECURITY.md](../../SECURITY.md)
promises the opposite for a released artifact: a private advisory first,
published with the release that carries the fix. **This is the procedure behind
that promise.** SECURITY.md states the policy and points here; the steps live
only here.

Whether a defect takes this route at all is SECURITY.md's question, not this
document's. An ordinary bug in a released artifact is a public issue and takes
*Hotfixes* above.

**What this procedure says about GitHub's own behaviour is documented by GitHub
and not measured here** — no advisory has been exercised on this repository.
Three claims below rest on that: that CI cannot reach a temporary private fork
(step 2), that branch protection is not enforced on the advisory's merge
(step 3), and that the "Report a vulnerability" button is offered only where
private vulnerability reporting is enabled (step 1). The sources are
[Collaborating in a temporary private fork](https://docs.github.com/en/code-security/security-advisories/working-with-repository-security-advisories/collaborating-in-a-temporary-private-fork-to-resolve-a-repository-security-vulnerability)
and [Privately reporting a security vulnerability](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability).
What this procedure says about *this repository* — its workflows, its branch
protection — is measured, and step 3 carries the fallback for the one GitHub
claim that hurts if it is wrong.

1. **Open a draft advisory.** A report that arrived through GitHub's ["Report a
   vulnerability"](https://github.com/theurian/theurian/security/advisories/new)
   form already is one; for a defect we found ourselves, create it under the
   repository's **Security → Advisories**.

   **Opening it does not start SECURITY.md's ninety days.** They run from
   *confirmation*: for a report, the assessment SECURITY.md gives seven business
   days, so at most seven business days after it arrived; for a defect we found
   ourselves, the moment we accept it. **Write the confirmation date on the
   advisory as you confirm it** — nothing else records it, and step 5 counts from
   it. Counting from the advisory's creation instead publishes an unfixed
   vulnerability up to seven business days early.

   SECURITY.md promises a reporter two more things, and the advisory is where
   both are answered and recorded: an acknowledgement within three business days,
   and that assessment within seven.

   The intake form is offered to a reporter only while private vulnerability
   reporting is enabled (documented by GitHub, not measured here) — a repository
   setting, not a file here, so nothing in this repository fails if it is
   switched off. The answer has to be `{"enabled":true}`:

   ```sh
   gh api repos/theurian/theurian/private-vulnerability-reporting
   gh api -X PUT repos/theurian/theurian/private-vulnerability-reporting  # enable
   ```

   On `{"enabled":false}` the button is shown to nobody without admin or security
   permissions, and SECURITY.md's intake link leads a reporter to a form they
   cannot submit.

2. **Fix it in the temporary private fork.** *Start a temporary private fork*
   sits at the bottom of the advisory form; it makes a copy of this repository
   named `theurian-ghsa-xxxx-xxxx-xxxx`, visible only to the advisory's
   collaborators and cloned like any other repository:

   ```sh
   git clone git@github.com:theurian/theurian-ghsa-xxxx-xxxx-xxxx.git
   ```

   **No CI runs there** (documented by GitHub, not measured here). GitHub does
   not let integrations, including CI, reach a temporary private fork, and status
   checks do not run on pull requests inside one. Every workflow in
   `.github/workflows/` is therefore unavailable until the
   fix reaches this repository, and four things normally done for you have to be
   done by hand — **all four before step 3**, which cannot be undone:

   - **Run the gate on your own machine**, from the fork's branch. *Releasing
     Core* §1 *Prepare* above is the list — including the two checks it names as
     easy to forget, `==` on every dependency and a current `uv.lock` — and
     *Releasing the plugin* §1 is the list for a plugin fix. Those commands check
     `packages tests` where `core.yml` checks `packages tests tools`
     ([#170](https://github.com/theurian/theurian/issues/170)); add `tools` if you
     touched it.
   - **Scan for secrets before the merge rather than after.** `security.yml`'s
     `secret-scan` job does run on the step-3 push, and that is too late: the
     commit is public by then and a leaked credential cannot be recalled. Locally
     it is the same command that job's scan step runs:

     ```sh
     gitleaks git --redact --verbose --exit-code 1 .
     ```

   - **Review the dependency changes yourself.** `security.yml`'s
     `dependency-review` job is `if: github.event_name == 'pull_request'` and the
     step-3 merge is a push, so unlike everything else here it does not run late
     — it never runs at all. A vulnerability fix is exactly where a dependency
     gets bumped.
   - **Sign, sign off, and write a Conventional Commits subject.** Normally an
     unsigned commit is refused by `main`'s `required_signatures`, and a missing
     sign-off or a malformed subject turns `shared.yml`'s `commits` job red.
     Neither happens here: that job is `if: github.event_name == 'pull_request'`,
     and GitHub does not enforce branch protection on the step-3 merge.

   Write as much of the release into those commits as the fix's line allows: the
   CHANGELOG **Security** entry, the threat-model entry, and the version bump
   where the release will come off `main`. Whatever is left out is work done
   after the defect is public.

3. **Merge from the advisory. This is where it becomes public.** Not from the
   fork: *Merge pull request(s)*, at the bottom of the advisory form, lands the
   fork's open pull requests in this repository, and only a maintainer can do it.
   The commits are readable by anyone from the moment they land, whatever state
   the advisory is in.

   **Do not merge until step 4 can run without waiting.** `publish-pypi` stops
   for a `core-maintainers` approval on the `pypi` environment (§5, step 6), so
   line up an approver first. That pause sits inside the window between a public
   fix and a fetchable one, and an approval nobody is waiting for makes the
   window as long as it takes someone to notice.

   Branch protection is not enforced on that merge (documented by GitHub, not
   measured here). This is what would otherwise apply, measured with
   `gh api repos/theurian/theurian/branches/main/protection`:

   | Setting | Value |
   | :-- | :-- |
   | `required_signatures` | `true` |
   | `required_linear_history` | `true` |
   | `enforce_admins` | `true` |
   | `required_status_checks` | `null` — nothing is required, so no check either blocks or clears this merge |

   **If the merge is refused, do not push the fork's branch here.** That is the
   public branch *Hotfixes* is, and it carries the account of the defect with it.
   A refusal most likely means the claim above is wrong and
   `required_linear_history` is rejecting a merge commit. Take the patch out
   instead: `git format-patch` in the clone, apply it to a branch off `main`,
   commit it signed and signed off, open an ordinary pull request and merge it as
   soon as it is green. It is public either way from the moment you first tried,
   so step 4's window opens at the merge attempt and not at the pull request.

   **What runs after the merge, and what does not.** The push to `main` runs
   `security.yml` and `shared.yml`, plus `core.yml` or `plugin.yml` as the changed
   paths select. From there the two trains differ, and only one of them has a
   second gate:

   - **Core** — the tag in step 4 runs the full **quality** gate again inside
     `release-core.yml` (§5, step 1): ruff over `packages tests`, mypy, and
     `pytest -q` on a single runner. `build` `needs` that job and the three
     publication jobs need `build`, so a failure cannot reach PyPI. It is the
     quality gate and not the whole of CI — `security.yml` and `shared.yml` filter
     on `branches: [main]` and do not trigger on a tag, so the supply-chain and
     cross-artifact contract jobs run only on the push above.
   - **The plugin** — nothing triggers on `plugin-v*` at all (*Releasing the
     plugin* §4), so there is no second gate. What `plugin.yml` caught on the
     step-3 push, plus your own run in step 2, is all of it.

4. **Release immediately.** The gap between step 3 and a fetchable fix is the
   window this procedure exists to keep short.

   - **Core** — if `main` is releasable, follow *Releasing Core* above; if it is
     not, cherry-pick onto a branch from the tag and follow *Hotfixes*. The
     forward-port that section insists on has already happened, in step 3.
   - **The plugin** — bump `version` in `.claude-plugin/plugin.json` and
     `pluginVersion` in `compatibility.yaml`, then move the marketplace pin. Those
     two are the delivery; the rest follows *Releasing the plugin* above. A pin
     that moves under an unchanged version reaches new installs only.

5. **Publish the advisory**, once that release is fetchable — *delivered*, in
   SECURITY.md's sense. Fill in the fixed version before publishing: without one
   the advisory still reaches users through Dependabot, offering them no safe
   version to move to. For Core, name the affected product as well — ecosystem
   `pip`, package `theurian`, and the affected range — because that is what
   carries it into the GitHub Advisory Database and in front of Dependabot at
   all. The plugin has no ecosystem to declare, so its advisory is a public
   record rather than a notification.

   **Confirm the credit with the reporter first.** SECURITY.md credits them
   unless they asked not to be named; it is opt-out, and easy to get backwards.

   If the ninety days from step 1's confirmation date run out before a fix ships,
   publish anyway, with whatever a reader can act on in place of a fixed version:
   the affected versions, and the mitigation SECURITY.md's table promises for a
   critical issue at thirty days. That is the commitment, not an accident.

   Then finish the record. Step 2 asked for the CHANGELOG **Security** entry and
   the threat-model entry in the fork's commits; write whatever slipped now.
   `release-core.yml` refuses a tag whose version has no changelog section at all
   (§5, step 2), so the Core path cannot lose the section — but nothing checks
   that it says *Security*, and nothing anywhere checks the threat model.

## Yanking

**If it is a vulnerability, stop here and take
[*Fixing a vulnerability privately*](#fixing-a-vulnerability-privately) first.**
This list is in the wrong order for that case: its steps 1 and 2 are what keep
the fix private, and you rejoin at its step 4, with the yank below in front of
the PATCH. Coming back here first is how the advisory ends up trailing a release
that already described the defect.

If a release is broken:

1. Yank from PyPI (do not delete — deletion breaks lock files that reference it).
2. Publish a fixed PATCH release immediately.
3. Untag only if the release never reached users.
