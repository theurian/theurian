# MCP tools

Protocol version: `theurian/v1`. Transport: Streamable HTTP at
`http://127.0.0.1:7419/mcp`.

> Tools land across Milestones 3–8. This page is the contract they are being
> built against; each schema is published under
> [`schemas/mcp/`](../../schemas/mcp/) as it ships.

## Every call carries its own context

```json
{
  "context": {
    "projectId": "backend-service",
    "snapshotId": null,
    "agentId": null,
    "taskId": null
  }
}
```

`projectId` is **required** on every project-scoped tool. Omitting it is a
validation error, never a fallback to "the last one used". With ten subagents
sharing one daemon, an implicit default resolves one agent's query against
another agent's project ([ADR-0002](../adr/0002-single-local-daemon-over-streamable-http.md)).

`snapshotId` pins a state hash so results stay reproducible for the lifetime of a
task, even if the developer switches branches mid-run.

`agentId` and `taskId` are provenance only. Theurian does not authenticate
agents; these label which run produced a proposal.

Schema: [`tool-context.schema.json`](../../schemas/mcp/tool-context.schema.json).

## Knowledge

| Tool | Purpose |
| :-- | :-- |
| `knowledge.search` | Hybrid search with provenance and trust labels |
| `knowledge.get` | Fetch an item or a specific revision |
| `knowledge.getContext` | Assemble a token-budgeted context pack |
| `knowledge.trace` | Follow relations from an item |
| `knowledge.listChanges` | What changed between two snapshots |
| `knowledge.status` | Migration state, index state, proposal ages |
| `knowledge.checkFreshness` | Which knowledge is outside its validity window |
| `knowledge.proposeChange` | **Write-intent** — emits a proposal |
| `knowledge.submitFeedback` | Record retrieval quality signals |
| `knowledge.generateMigrationDraft` | **Write-intent** — emits a proposal |

### Write-intent tools do not write

The three marked tools produce:

```text
.theurian/proposals/<proposal-id>/
├── migration.yaml     # schema-valid and directly applicable
├── content.md         # or .yaml / .json, matching the knowledge format
└── evidence.json      # anchors and the reasoning trail
```

There is no MCP path to approved state — not a flag, not a permission. A test
enumerates every registered tool and asserts none reaches a canonical write
([ADR-0013](../adr/0013-ai-writes-produce-proposals.md)).

### Result shape

```json
{
  "itemId": "architecture.auth-policy",
  "revisionId": "01K1DEFREV1234567890ABCDEF",
  "title": "Authentication and authorization policy",
  "excerpt": "...",
  "contentType": "text/markdown",
  "score": 0.87,
  "trustLevel": "reviewed",
  "freshness": { "isWithinValidity": true, "ageDays": 12, "...": "..." },
  "snapshotId": "a1b2c3...",
  "indexBuildId": "01K1DEFDX01234567890ABCDEF",
  "sourceAnchors": [
    { "provider": "git", "commitSha": "a1b2c3", "filePath": "...", "lineStart": 1, "lineEnd": 42 }
  ],
  "raptorPath": [{ "nodeId": "...", "level": 2, "title": "Backend architecture" }],
  "contentClassification": "untrusted-knowledge",
  "mayContainInstructions": true,
  "executable": false
}
```

`sourceAnchors` has `minItems: 1`. A result with no route back to its origin is
an unverifiable assertion, and returning one would undermine every other
guarantee here.

Schema: [`retrieval-result.schema.json`](../../schemas/knowledge/retrieval-result.schema.json).

## Review

| Tool | Purpose |
| :-- | :-- |
| `review.search` | Search review history |
| `review.getThread` | One thread with comments and resolution |
| `review.findSimilar` | Threads resembling a described situation |
| `review.getDecisions` | Decisions reached in review |
| `review.generateKnowledgeCandidate` | **Write-intent** — emits a proposal |
| `review.listUnresolved` | Open threads |

`review.findSimilar` is the one that changes outcomes: it answers "has this come
up before?" before an agent reimplements something the team already rejected.

## Specification

| Tool | Purpose |
| :-- | :-- |
| `spec.search` | Search specifications |
| `spec.get` | Fetch a spec with its **structured** fields intact |
| `spec.getDependencies` | What this spec depends on |
| `spec.getImplementationStatus` | Implemented, partial, or not started |
| `spec.getCoverage` | Which declared outcomes have verifying tests |
| `spec.findContradictions` | Specs that disagree |
| `spec.findStaleImplementations` | Code referencing superseded specs |

`spec.get` returns the native structure, not a prose rendering. `getCoverage`
depends on that: coverage means "which of these declared outcomes has a test",
and the outcomes must still exist as data
([ADR-0010](../adr/0010-three-layer-knowledge-model.md)).

## Traceability

| Tool | Purpose |
| :-- | :-- |
| `trace.get` | Edges touching a node |
| `trace.findImplementations` | What implements a spec |
| `trace.findTests` | What verifies a spec |
| `trace.findUnimplementedSpecs` | Specified, not built |
| `trace.findUnverifiedSpecs` | Built, not tested |
| `trace.findCodeWithoutSpec` | Maintained, never specified |

## Project and system

| Tool | Purpose | Project-scoped |
| :-- | :-- | :-- |
| `project.list` | Registered projects | no |
| `project.status` | Migration, index, and commit state | yes |
| `system.health` | Liveness and version | no |
| `system.capabilities` | What this build supports | no |
| `system.indexStatus` | Active build, staleness, builds in progress | yes |

`system.capabilities` exists so a client can degrade per feature rather than
all-or-nothing. Version gating is coarse; if only summarization is unconfigured,
everything else should still work.

## Errors

```json
{
  "error": {
    "code": "REVISION_CONFLICT",
    "message": "Revision conflict on architecture.auth-policy",
    "details": { "itemId": "...", "expected": "01K1ABC...", "actual": "01K1DEF..." }
  }
}
```

| Code | Meaning |
| :-- | :-- |
| `PROJECT_NOT_REGISTERED` | Unknown `projectId` |
| `NOT_AUTHORIZED` | Not permitted for this project |
| `SNAPSHOT_NOT_FOUND` | The pinned state no longer exists |
| `INDEX_BUILDING` | No complete index yet — first build only |
| `REVISION_CONFLICT` | `expectedRevision` did not match |
| `MIGRATION_CHECKSUM_MISMATCH` | An applied migration was edited |
| `INVALID_INPUT` | Failed schema validation |

Errors carry structured `details` because a message alone forces the caller back
into the code to find out what happened.

## Safety contract

Every knowledge-bearing result carries the same three fields, always:

```json
{
  "contentClassification": "untrusted-knowledge",
  "mayContainInstructions": true,
  "executable": false
}
```

`executable` is `const: false` in the schema and cannot be set true in the domain
type.

**Theurian labels; it does not enforce.** A calling agent must treat retrieved
content as data. An agent that follows instructions found inside a document will
be influenced by a document that contains instructions, and no MCP server can
prevent that from the server side. This is stated in
[SECURITY.md](../../SECURITY.md) rather than buried here.

## Changing this contract

Additive changes (a new optional field, a new tool) are MINOR and do not bump
`protocolVersion`. Removing a field, tightening a type, adding a required field,
or renaming a tool is breaking and bumps it. See
[plugin-core-compatibility.md](plugin-core-compatibility.md).
