# ADR-0001: Monorepo with independently released artifacts

- Status: accepted
- Date: 2026-08-01
- Deciders: Theurian maintainers
- Requirements: CP-1, CP-9, OSS-4

## Context

Theurian ships two things that people install separately: `theurian` (a Python
package with a CLI, a daemon, and an MCP server) and a Claude Code plugin. They
have different audiences, different release cadences, and different review
expertise.

Early in a project, a shared contract changes constantly. Splitting the
repository on day one means every protocol change becomes a two-repository,
two-pull-request, ordering-sensitive operation — before anyone knows whether the
protocol is right. Keeping them in one repository *as one artifact*, however,
guarantees the boundary rots: someone will import a Core module from a plugin
script because it is right there, and the split becomes impossible later.

## Decision

One repository. Two release trains. The boundary is enforced mechanically from
the first commit.

1. `packages/theurian-core/` and `plugins/claude-code/` each own a version,
   CHANGELOG, test suite, CI job, release workflow, README, and CODEOWNERS entry.
2. Neither is ever released as a unit with the other. A Core release does not
   imply a plugin release, and the reverse.
3. `schemas/`, `tests/contract/`, `tests/e2e/`, and `docs/` are shared and
   co-owned.
4. Cross-boundary source dependencies are forbidden and CI-checked (see ADR-0003
   and `plugin.yml`'s `boundary` job).
5. The conditions that trigger a split are written down in advance
   ([requirements-analysis §21](../architecture/requirements-analysis.md#21-conditions-for-splitting-the-plugin-into-its-own-repository)),
   so the decision to split is a measurement, not an argument.

## Consequences

### Positive

- A protocol change lands atomically with its producer and both consumers.
- The shared E2E suite runs against both trees in one checkout with no
  cross-repository choreography.
- Contributors get one clone, one `uv sync`, one test command.

### Negative

- Repository-level tooling (CI, CODEOWNERS, release automation) must be
  path-aware from day one, which is more setup cost than a single artifact.
- Issue triage needs a label convention to keep the two audiences separate.

### Neutral

- Git history for the plugin stays extractable via `git subtree split`.

## Alternatives considered

| Alternative | Why rejected |
| :-- | :-- |
| Two repositories from the start | Every protocol change becomes a coordinated two-repo release while the protocol is still unstable. Highest cost at the point of highest churn. |
| One repository, one artifact (plugin bundled in the Python package) | Violates CP-1 and CP-9. Claude Code users would have to install a Python package to get a plugin, and the boundary would be unenforceable. |
| Monorepo with a shared version number | Forces a plugin release for every Core patch and vice versa. Makes the compatibility matrix meaningless. |

## Compliance

- `.github/workflows/plugin.yml`, job `boundary`, step "The plugin must not
  import Core" fails if any file under `plugins/` matches
  `^\s*(from|import)\s+theurian\b`. Beside it, "The plugin must not contain
  Python" fails on any `.py` outside a `tests/` path, and
  `tests/unit/test_plugin_boundary.py::test_plugin_contains_no_python` and
  `test_no_plugin_file_imports_theurian` assert the same two properties from the
  suite. This section called the step `plugin-boundary`; no job or step has ever
  had that name.
- `.github/CODEOWNERS` declares distinct owner groups per tree, and both groups
  on `/schemas/`, `/tests/contract/`, `/tests/e2e/` and `/docs/protocol/`.
- `tests/contract/` runs against the *installed* `theurian` CLI, not a source
  import, so the plugin's real integration path is the one under test.

Still owed, with the milestone that will satisfy it:

- **CODEOWNERS requests review; it does not require it.** This section said it
  "requires distinct reviewer groups per tree". `main`'s branch protection has
  `require_code_owner_reviews: false` and `required_approving_review_count: 0`,
  so the file routes review requests and nothing blocks a merge that ignores
  them. The dual ownership on `/schemas/` exists so the shared contract cannot
  be changed unilaterally by either side, and that is the claim that does not
  hold today: [#67](https://github.com/theurian/theurian/issues/67). It belongs
  with the second maintainer — requiring code-owner review with one maintainer
  blocks every pull request.
