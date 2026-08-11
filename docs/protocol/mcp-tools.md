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
  "excerpt": "The gateway verifies the request signature before any handler runs ...",
  "contentType": "text/markdown",
  "status": "approved",
  "trustLevel": "reviewed",
  "sensitivity": "internal",
  "freshness": { "revisionCreatedAt": "2026-08-01T09:00:00+09:00", "isWithinValidity": true, "ageDays": 12 },
  "sourceAnchors": [
    {
      "provider": "git",
      "sourceUri": "git://backend/architecture/auth-policy.md",
      "repository": "backend",
      "commitSha": "a1b2c3",
      "filePath": "architecture/auth-policy.md",
      "lineStart": 1,
      "lineEnd": 42
    }
  ],
  "contentClassification": "untrusted-knowledge",
  "mayContainInstructions": true,
  "executable": false,
  "fusedScore": 0.87,
  "foundBy": ["lexical", "summary"],
  "raptorPath": [
    { "nodeId": "3f9a1c...", "level": 2, "title": "Backend architecture — gateway, caching, and queueing policies" },
    { "nodeId": "7c1b04...", "level": 1, "title": "Authentication and authorization policy" }
  ]
}
```

`sourceAnchors` is always present and occasionally empty: an empty array means the
revision declares it originates in Theurian rather than in a repository — the one
case the domain's INV-8 allows — and a result with no route back to its origin
cannot be constructed at all. `fusedScore` and `foundBy` appear together, on the
ranked path only. `raptorPath` appears only over a `--raptor` index — the hit's
summary ancestry, catalog root to leaf, one `{nodeId, level, title}` per node,
`level` descending 3→1 (ADR-0008 decision 8). `snapshotId` and `indexBuildId` are
provenance too, but they belong to the response's `retrieval` envelope, not to a
hit: one query is answered from one canonical state and one index build, so a
per-hit copy could never differ between hits (FR-R5).

Schema: [`retrieval-result.schema.json`](../../schemas/knowledge/retrieval-result.schema.json).

### `asOf` pins a search to a moment

`knowledge.search` takes an optional `asOf`: an RFC 3339 timestamp with any
explicit offset, e.g. `2026-08-01T00:00:00Z` or `2026-08-01T09:00:00+09:00` —
not only `Z`. Omit it and nothing changes —
every surfaceable item is a candidate whatever its declared validity window,
exactly as every prior release of this tool already behaved. Pass it and an
item outside its `validFrom`/`validTo` window *at that moment* is excluded from
`results`, and every returned hit's `freshness.isWithinValidity` is computed
against that same moment rather than against real time.

It is a refinement, not a default filter (FR-R1,
[#63](https://github.com/theurian/theurian/issues/63) phase 2). A permanent
filter was rejected: it would make `isWithinValidity` constant-`true` on a
healthy index — a published field that can never read `false` is not a field —
and it would give the ranked path a stale-index statistics residual with no way
to turn off. It is also not a withholding: everything one call excludes is
returned to the same caller by the identical query with `asOf` omitted, so no
response field here can carry information the caller could not already obtain
directly, and none of the disclosure guarantees this page states elsewhere for
a document a caller may not read apply to it.

`knowledge.get` does not take `asOf`. It names an item by id, and refusing to
resolve an id the caller already holds — on the grounds that the item is not
current *at some other moment* — would be a worse answer than the one it
already gives: `freshness.isWithinValidity: false` on the current revision,
computed against real time, exactly as before this parameter existed.

An unparseable `asOf` is a clean `ToolError` naming the fix, never a traceback.

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
| `project.list` | Registered projects, and the ids nothing can serve | no |
| `project.status` | Migration, index, and commit state | yes |
| `system.health` | Liveness and version | no |
| `system.capabilities` | What this build supports | no |
| `system.indexStatus` | Active build, staleness, builds in progress | yes |

`system.capabilities` exists so a client can degrade per feature rather than
all-or-nothing. Version gating is coarse; if only summarization is unconfigured,
everything else should still work.

### `project.list`

Four keys, all four always present. The contract is
[`schemas/mcp/project-list-response.schema.json`](../../schemas/mcp/project-list-response.schema.json).

```json
{
  "count": 1,
  "projects": [{ "projectId": "demo", "rootPath": "/home/dev/demo" }],
  "unreadable": ["api"],
  "remedy": "Remove them with `theurian project unregister <id>`, then register each project again from its repository. …"
}
```

| Key | Meaning |
| :-- | :-- |
| `count` | The length of `projects`, and nothing else — how many projects can be queried |
| `projects` | Every registration the daemon could read, sorted by id. Two fields per entry, not the whole registry record |
| `unreadable` | Ids present in the registry whose entries name no root path, sorted. Every other tool refuses these ids; this is where a caller finds out why |
| `remedy` | What to do about `unreadable`, or `null` when it is empty |

`unreadable` and `remedy` are **required**, empty array and `null` included.
Emitting a key only when it applies makes "nothing is unreadable"
indistinguishable from "this daemon predates the field", and a client that has to
branch on key presence eventually forgets to.

**`projects` and `unreadable` are not a partition of one snapshot.** They come
from two independent reads of the registry file, so an id can appear in both or
in neither if a registration lands between them. Do not compute the size of the
registry by adding the two, and do not read membership of one as absence from the
other.

`projectId` carries no pattern. Ids Theurian creates are lowercase kebab-case,
but this value is a *key* of a hand-editable file and nothing validates keys on
read, so an entry keyed `Not An Id` with a valid `rootPath` loads and is
published verbatim. A pattern here would make the schema reject output the
product really produces.

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

`executable` is `const: false` in the schema, and a real tool response carrying
`executable: true` is rejected by it (`tests/integration/test_wire_contract.py`).
This used to add "and cannot be set true in the domain type": the type that
refuses it, `domain.retrieval.SafetyMetadata`, is not on the path that produces
this value. See the round-eight correction to T-3 in
[the threat model](../security/threat-model.md).

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

**`protocolVersion` is still `theurian/v1` after Milestone 5, which made three
breaking changes to this contract.** That is a decision, recorded here because
the alternative reading is that somebody forgot. The rule above governs changes
*from a released protocol*: no published version of Core has ever lacked them,
so no client can be pinned to a `v1` that lacks them, and bumping would publish
a `theurian/v2` whose `v1` never shipped. Milestone 5's breaking set is the
*content* of `v1`, not a departure from it.

The three, so that "breaking but unbumped" is checkable rather than asserted:
the `knowledge.search` response reshape, the removal of `withheldSuperseded`,
and the two required fields `project.list` gained. Each is named as BREAKING in
the changelog, which is what protects an integrator. The first bump is the
first breaking change after the version that first carries `theurian/v1`.
