# Contributing to Theurian

Thank you for considering a contribution. This document covers what you need to
know before your first pull request.

## Ground rules

1. **Every commit is signed off.** `git commit -s` adds the
   `Signed-off-by` line asserting you have the right to submit the work under
   Apache-2.0. There is no CLA — you keep your copyright, and Core cannot be
   relicensed without your agreement ([ADR-0015](docs/adr/0015-dco-over-cla.md)).
   Read the [DCO](docs/contributing/dco.md).
2. **Every commit is cryptographically signed.** Separate from the sign-off
   above: `main` requires a verified signature — GitHub's "Verified" badge —
   which the `Signed-off-by` trailer does not provide. A one-time config makes
   every commit sign automatically; see
   [Signing your commits](#signing-your-commits).
3. **One topic per pull request.** Separate branches for separate concerns.
   A refactor, a bug fix, and a feature are three pull requests.
4. **No test in this repository calls an external API.** Every port has a
   deterministic fake. A contribution that needs a paid API key to test is a
   contribution most people cannot review
   ([ADR-0009](docs/adr/0009-no-llm-vendor-lock-in.md)).

## Setup

```sh
git clone https://github.com/theurian/theurian
cd theurian
uv sync              # Python 3.13+, all extras
uv run pytest        # ~275 tests, offline, a couple of seconds
```

Full detail in [docs/contributing/development.md](docs/contributing/development.md).

## Before you open a pull request

```sh
uv run ruff format packages tests
uv run ruff check packages tests
uv run mypy
uv run pytest --cov
```

CI runs the same commands plus packaging, security scanning, and plugin
validation. Nothing here should surprise you on the runner.

## What `main` requires

These checks must pass before a pull request can merge. `enforce_admins` is on,
so they bind maintainers too.

| Required check | What it refuses |
| :-- | :-- |
| `Conventional Commits and DCO` | A subject that is not a Conventional Commit, or a commit with no `Signed-off-by` trailer |
| `Secret scan` | A credential anywhere in the branch's history (gitleaks over the full clone) |
| `Dependencies are pinned exactly` | A dependency, or a tool a workflow installs, that is not pinned with `==` (ADR-0014) |
| `Dependency licences` | A dependency under GPL, AGPL or SSPL, which Apache-2.0 cannot ship (O-4) |
| `Dependency review` | A dependency added by this pull request that carries a known advisory at moderate or above |
| `SBOM` | An SBOM that generates but lists no components |
| `Detect what changed` | A broken path filter — without it, the conditional jobs would skip and the pull request would read green |

Every one of those reports on **every** pull request, whatever it touches, and
that is what makes it requirable. `Core` and `Plugin` are filtered by path, so
on a docs-only pull request they do not report at all — and a required check
that never reports blocks the merge forever instead of passing it.

Which means the jobs carrying the most weight are **not** gates yet: `Format,
lint, types`, `Tests`, `Full suite with no network`, `Build and verify the
package`, and the ≥80% coverage floor inside `Tests`. They run, and a red one is
a maintainer's judgement rather than a block. Making them requirable is a change
to the workflows, not to a setting — it is the remaining half of
[#67](https://github.com/theurian/theurian/issues/67).

Two rules gate a merge while reporting no check at all: every commit needs a
verified signature, and the branch requires linear history. Neither appears in
the checks list, which is exactly how a correctly signed-off but unsigned commit
shows all green and still cannot merge — see
[Signing your commits](#signing-your-commits).

## Signing your commits

`main` requires every commit to carry a verified cryptographic signature — the
"Verified" badge on GitHub, enforced by the branch's `required_signatures` rule.
This is a **separate** requirement from the DCO sign-off, and satisfying one does
not satisfy the other:

| Requirement | What it is | How to satisfy it |
| :-- | :-- | :-- |
| DCO sign-off | A `Signed-off-by:` text trailer asserting your right to submit | `git commit -s` |
| Signed commit | A cryptographic signature GitHub verifies against a key you registered | `git commit -S`, or `commit.gpgsign true` to sign every commit |

A commit needs **both**, and a missing signature is easy to miss: no CI check
reports it, so the pull request shows every check green while staying
unmergeable. [#197](https://github.com/theurian/theurian/pull/197), the first
contribution from outside the team, hit exactly this — the sign-off was correct,
the commit was unsigned, and nothing in these docs said it needed to be.

The lowest-friction setup reuses an SSH key you already have:

```sh
git config --global gpg.format ssh
git config --global user.signingkey ~/.ssh/id_ed25519.pub
git config --global commit.gpgsign true
```

Then add that **public** key to GitHub a second time, as a signing key:
**Settings → SSH and GPG keys → New SSH key → Key type: Signing Key**. It is a
distinct entry from any authentication key, even when it is the same key — an
authentication key alone will not make your commits show as verified. After this,
`git commit` signs automatically and GitHub shows "Verified".

Prefer GPG? It works the same way; follow GitHub's
[commit signature verification](https://docs.github.com/en/authentication/managing-commit-signature-verification)
guide and register a GPG signing key. Either way, keep `git commit -s` for the
sign-off — the signature does not replace it.

## Commit messages

[Conventional Commits](https://www.conventionalcommits.org/), enforced by a
required check ([What `main` requires](#what-main-requires)):

```text
<type>(<scope>): <subject>

<body: why, not what>

Signed-off-by: Your Name <you@example.com>
```

Types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`, `ci`, `build`.
Scopes: `core`, `plugin`, `schemas`, `docs`, `ci`, `packaging`.

Keep commits small — roughly 100 lines is a good target, 1000 is usually too big.
Each commit should build and pass tests on its own. The body explains *why*; the
diff already shows *what*.

```text
feat(core): partition canonical state by content-addressed state hash

Branch switching previously rebuilt the whole database, so search went
dark for the duration and two worktrees of one repository corrupted each
other's state. Keying the database by a hash of the reachable migration
set makes a return to a previously visited branch O(1) and keeps
worktrees independent.

Refs ADR-0007.
```

## Where things go

| Change | Location | Reviewed by |
| :-- | :-- | :-- |
| Core logic | `packages/theurian-core/src/theurian/` | Core maintainers |
| Claude Code plugin | `plugins/claude-code/` | Plugin maintainers |
| Shared contract | `schemas/` | **Both** |
| Architecture decision | `docs/adr/` | Core maintainers |

## Architectural rules, and why they are tests

These are enforced by `packages/theurian-core/tests/unit/test_layering.py` and
`test_plugin_boundary.py`, not by review vigilance. A rule that lives only in a
document gets violated within a quarter.

- `domain/` imports nothing from `application/` or `infrastructure/`.
- `application/` depends on ports, never adapters.
- Only `cli/`, `daemon/`, and `mcp/` may name a concrete adapter.
- No vendor name appears in `domain/` or `application/`.
- No file under `plugins/` imports `theurian`.
- The plugin manifest declares no `mcpServers` entry.
- The `SessionStart` hook performs no install, rebuild, or mutation.

If a change requires breaking one of these, that is an ADR, not a `# noqa`.

## Writing an ADR

Any decision that would be expensive to reverse needs one:

1. Copy `docs/adr/0000-adr-template.md` to `NNNN-kebab-title.md`.
2. Fill in **Alternatives considered**. An ADR without rejected alternatives is
   recording a preference, not a decision.
3. Fill in **Compliance** — the lint rule, test, or CI job that enforces it.
4. Add a row to `docs/adr/README.md`.

Never edit an accepted ADR beyond typo fixes. Supersede it with a new one; the
history of the decision is the point.

## Testing expectations

- ≥80% line and branch coverage on Core, enforced in CI.
- Mark tests `unit`, `integration`, `contract`, or `e2e`.
- Test names state the behaviour, not the method:
  `test_symlink_pointing_outside_root_is_refused`, not `test_resolve_2`.
- Security controls need a test that proves the control *fires*, not only that
  the happy path works.
- Contract tests invoke the installed `theurian` binary as a subprocess. A test
  that imports Core would pass even if packaging were broken.

## Reporting bugs and requesting features

Use the [issue templates](.github/ISSUE_TEMPLATE/). For bugs, include
`theurian version --json` output and `theurian doctor --report --json`.

**Review the doctor output before posting it.** `--report` does two things.
Absolute paths Theurian itself put in the payload are substituted — your home
directory, the repository root, the token file, the executable, and any data
directory you chose yourself — wherever they appear,
including inside each step's `summary` and `detail`. Values Theurian did *not*
write are withheld instead of substituted, because there is nothing to match them
against: a step reporting a difference in Claude Code's MCP entry, a service unit,
another daemon's reply or the project registry says *that* they differ and names
only the fields Theurian itself writes — a name read out of your file is your
string, not Theurian's schema, so it is counted rather than named.

It is still not a general credential filter, and the list of what goes out is
not exhaustive: a path outside those anchors, a revealing filename, and facts
about your machine the diagnostic exists to report. Plain `theurian doctor`,
without `--report`, prints everything in full and is meant for your own terminal.

For a security vulnerability, use the private path in [SECURITY.md](SECURITY.md)
instead of an issue.

## Review

Reviews follow [Google's engineering practices](https://google.github.io/eng-practices/review/).
Expect questions about *why*, about the failure mode a change prevents, and about
what a reviewer six months from now will need to know. Those questions are the
review doing its job.

## Code of conduct

Participation is governed by [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
