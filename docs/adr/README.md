# Architecture Decision Records

An ADR records a decision that was expensive to make and would be expensive to
reverse. It captures the forces, the choice, and — most importantly — the
alternatives that were rejected and why.

## Index

| ADR | Title | Status |
| :-- | :-- | :-- |
| [0001](0001-monorepo-with-independent-artifacts.md) | Monorepo with independently released artifacts | accepted |
| [0002](0002-single-local-daemon-over-streamable-http.md) | A single local daemon, reached over Streamable HTTP | accepted |
| [0003](0003-ports-and-adapters.md) | Ports and adapters as the top-level structure | accepted |
| [0004](0004-sqlite-is-a-derived-artifact.md) | SQLite is a derived artifact, never a Git-tracked one | accepted |
| [0005](0005-yaml-knowledge-migrations.md) | Knowledge migrations are YAML domain operations, not SQL | accepted |
| [0006](0006-immutable-revisions-and-optimistic-concurrency.md) | Immutable revisions with optimistic concurrency | accepted |
| [0007](0007-state-hash-partitioned-databases.md) | State-hash-partitioned databases for Git branches and worktrees | accepted |
| [0008](0008-raptor-forest.md) | A RAPTOR forest, not a single tree | accepted |
| [0009](0009-no-llm-vendor-lock-in.md) | No vendor lock-in for LLM, embedding, or cloud providers | accepted |
| [0010](0010-three-layer-knowledge-model.md) | Source, Canonical, and Index are three distinct layers | accepted |
| [0011](0011-local-mcp-authentication.md) | Local MCP authentication and token handling | accepted |
| [0012](0012-plugin-does-not-autoregister-mcp-server.md) | The plugin does not declare an MCP server; setup installs the connection | accepted |
| [0013](0013-ai-writes-produce-proposals.md) | AI writes produce proposals, never approved state | accepted |
| [0014](0014-dependency-pinning-and-pre-1-0-isolation.md) | Exact dependency pinning and pre-1.0 isolation | accepted |
| [0015](0015-dco-over-cla.md) | Developer Certificate of Origin, not a Contributor License Agreement | accepted |
| [0016](0016-state-hash-covers-the-working-tree.md) | The state hash covers the working tree, not just committed migrations | accepted |
| [0017](0017-sqlite-schema-versioning.md) | SQLite schema version participates in the state hash | accepted |
| [0018](0018-single-writer-synchronous-in-m1.md) | One writer, expressed as a lock in Milestone 1 and a queue later | accepted |
| [0019](0019-front-matter-is-data-not-governance.md) | Front matter is data, not governance metadata | accepted |
| [0020](0020-deterministic-text-projection.md) | Text projection of structured sources is a deterministic pure function | accepted |
| [0021](0021-rank-fusion-over-score-normalisation.md) | Fuse retrievers by rank, not by normalised score | accepted |
| [0022](0022-index-lives-in-its-own-database.md) | The retrieval index lives in its own database file | accepted |
| [0023](0023-trigram-index-beside-the-word-index.md) | A trigram index beside the word index, not instead of it | accepted |
| [0024](0024-a-purge-is-a-build.md) | A purge is a build; a published index is never written | accepted |
| [0025](0025-sensitivity-is-enforced-before-0-1-0-stable.md) | Sensitivity is enforced before 0.1.0 stable | accepted |
| [0026](0026-evidence-plane-not-control-plane.md) | Theurian is an evidence plane, not a control plane | accepted |

## Writing a new ADR

1. Copy [`0000-adr-template.md`](https://github.com/theurian/theurian/blob/main/docs/adr/0000-adr-template.md) to
   `NNNN-kebab-case-title.md`, using the next free number.
2. Open it with status `proposed`.
3. Reference requirement IDs from
   [`../architecture/requirements-analysis.md`](../architecture/requirements-analysis.md).
4. Fill in **Alternatives considered**. An ADR with no rejected alternatives is
   not recording a decision; it is recording a preference.
5. Fill in **Compliance**. A decision with no enforcement mechanism will be
   violated within a quarter.
6. Add a row to the table above.

## Changing a decision

Do not edit an accepted ADR beyond typo fixes. Write a new one, set the old one's
status to `superseded by NNNN`, and set the new one's `Supersedes` field. The
history of the decision is the point.
