# ADR-0014: Exact dependency pinning and pre-1.0 isolation

- Status: accepted
- Date: 2026-08-01
- Deciders: Theurian maintainers
- Requirements: OSS-14, R-5, R-6, O-9, §10 of the brief

## Context

Theurian is a daemon a user installs once and then forgets about for months. When
it breaks after an unattended transitive upgrade, the user has no recent change to
correlate it with, and the failure mode — a corrupted index or a failed migration
— looks like data loss rather than a dependency problem.

Several dependencies are also young:

| Dependency | Version | Risk |
| :-- | :-- | :-- |
| `mcp` | 2.0.0 | Major-version rewrite; `FastMCP` became `MCPServer` in 2.x |
| `sqlite-vec` | 0.1.9 | Pre-1.0; on-disk format and API may change |
| `watchfiles` | 1.2.0 | Post-1.0 but a small maintainer surface |

## Decision

### Pin everything

1. Every runtime dependency is pinned with `==` in `pyproject.toml`. No `^`, no
   `~=`, no `>=`.
2. `uv.lock` is committed and CI runs `uv sync --frozen`, so a resolution change
   fails the build instead of silently landing in a release.
3. Upgrades are pull requests with a passing CI run — Dependabot proposes, CI
   decides, a human merges.
4. The full dependency set is reported in the SBOM attached to every release
   (OSS-7).

### Isolate pre-1.0 dependencies

5. A pre-1.0 dependency may only be reached through a port (ADR-0003), and only
   from the adapter that names it. `import sqlite_vec` outside
   `infrastructure/vector/` is a lint failure.
6. Every pre-1.0 dependency has a documented exit path — for `sqlite-vec`, an
   in-tree brute-force `VectorStore` that is correct but slower, kept working and
   tested so it is a configuration change rather than a project.
7. Adopting a new pre-1.0 dependency requires an ADR naming the port that
   contains it and the fallback if it is abandoned.

### Contain the young major

8. `mcp` 2.x is confined to `mcp/` and `daemon/`. Application and domain code
   never imports it.
9. Contract tests assert the **wire protocol** — an HTTP request against a running
   daemon — not SDK call signatures. An SDK API change then breaks one adapter,
   not the test suite's meaning.

### Split the install

10. Optional extras (`daemon`, `vector`, `telemetry`) keep `pip install theurian`
    small for CI images and pre-commit hooks that only need the CLI and the
    migration engine. `theurian[all]` is the developer install.

### Verify versions against upstream

11. Every pinned version in this repository was checked against the package index
    at pin time rather than recalled. The MCP 2.0 API surface used here was
    verified against the installed package, not only against documentation.

## Consequences

### Positive

- A given Theurian release behaves identically everywhere it is installed.
- A dependency-caused regression is isolated to one merged upgrade pull request.
- A pre-1.0 dependency's breakage is confined to one adapter with a tested fallback.
- Reproducible builds (OSS-14) are a property of the committed lock file.

### Negative

- Security patches require an explicit upgrade rather than arriving on the next
  install. Mitigated by Dependabot, and by the fact that this is a local daemon
  with no untrusted network exposure.
- Pinning can conflict with another package in a shared environment. Mitigated by
  recommending `uv tool install theurian` or `pipx`, which isolate it.

### Neutral

- Pins are floors and ceilings simultaneously, so the upgrade cadence is a
  deliberate maintainer activity rather than an emergent one.

## Alternatives considered

| Alternative | Why rejected |
| :-- | :-- |
| Compatible-release ranges (`~=`) | A transitive minor upgrade breaks an installed daemon with no correlating user action. |
| Lock file only, ranges in metadata | The lock protects contributors; PyPI consumers get the ranges, so the guarantee does not reach users. |
| Vendor pre-1.0 dependencies | Inherits the maintenance burden and diverges from upstream fixes. |
| Avoid pre-1.0 dependencies entirely | `sqlite-vec` has no mature alternative for embedded vector search; the port plus fallback is the proportionate control. |

## Compliance

- `security.yml`'s `pinning` job, step "Every dependency uses `==`", fails if any
  dependency in either `pyproject.toml` is unpinned. This section called it a
  test; it is a workflow step, and it runs on every push and pull request. The
  distinction is not pedantry — it does not run under `uv run pytest`, so a
  contributor who checks locally the way `CONTRIBUTING.md` describes will not see
  it fail.
- CI runs `uv sync --frozen`.
- `tests/unit/test_layering.py::test_volatile_dependencies_are_confined` keeps
  `sqlite_vec` inside `infrastructure/` and `mcp` inside `mcp/` and `daemon/`,
  by walking the import graph. This section called it "a banned-import lint
  rule" and named `infrastructure/vector/` as the boundary. Neither was right:
  `pyproject.toml`'s `[tool.ruff.lint.flake8-tidy-imports.banned-api]` has a
  single entry, `theurian.infrastructure` (ADR-0003), and the boundary the test
  enforces is the whole `infrastructure` package.

Still owed, with the milestone that will satisfy it:

- **There is no `VectorStore` conformance suite, and no `sqlite-vec` adapter.**
  This section claimed a suite running against both that adapter and a
  brute-force fallback. `infrastructure/vector/` contains only `__init__.py`,
  `sqlite_vec` is imported nowhere in `src/`, and the vector search that ships is
  an exact scan inside `SqliteIndexStore.search_dense` — deliberately, per
  ADR-0021, because a local corpus is small enough that an exact scan is both
  fast enough and reproducible. So the *second* implementation this item exists
  to compare against has not been written and may never be. The item comes due
  with the first ANN adapter; until then `test_volatile_dependencies_are_confined`
  is guarding an import that does not happen.
