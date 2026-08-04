# Contributing to Theurian

Thank you for considering a contribution. This document covers what you need to
know before your first pull request.

## Ground rules

1. **Every commit is signed off.** `git commit -s` adds the
   `Signed-off-by` line asserting you have the right to submit the work under
   Apache-2.0. There is no CLA — you keep your copyright, and Core cannot be
   relicensed without your agreement ([ADR-0015](docs/adr/0015-dco-over-cla.md)).
   Read the [DCO](docs/contributing/dco.md).
2. **One topic per pull request.** Separate branches for separate concerns.
   A refactor, a bug fix, and a feature are three pull requests.
3. **No test in this repository calls an external API.** Every port has a
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

## Commit messages

[Conventional Commits](https://www.conventionalcommits.org/), enforced in CI:

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

**Review the doctor output before posting it.** What `--report` redacts is
absolute paths — your home directory, the repository root, and the token file's
path — replaced wherever they appear, including inside each step's `summary` and
`detail`. It is not a credential filter and removes nothing else, so a path
outside those three roots, or a filename that is itself revealing, still goes out
verbatim.

For a security vulnerability, use the private path in [SECURITY.md](SECURITY.md)
instead of an issue.

## Review

Reviews follow [Google's engineering practices](https://google.github.io/eng-practices/review/).
Expect questions about *why*, about the failure mode a change prevents, and about
what a reviewer six months from now will need to know. Those questions are the
review doing its job.

## Code of conduct

Participation is governed by [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
