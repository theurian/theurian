# MCP tools

Protocol version: `theurian/v1`. Transport: Streamable HTTP at
`http://127.0.0.1:7419/mcp`.

Today, Core registers five callable MCP tools:

- `knowledge.search`
- `knowledge.get`
- `knowledge.status`
- `project.list`
- `system.capabilities`

`system.capabilities` is the runtime boundary for clients. In this build it
reports `writeTools: false`, `reviewIngestion: false`, and
`traceability: false`; those values mean the write-intent, review, and
traceability tools described below are designed protocol shape, not callable
tools in the current server.

## Every project-scoped call names its project

```json
{
  "projectId": "backend-service"
}
```

`projectId` is **required** on every project-scoped tool that ships today:
`knowledge.search`, `knowledge.get`, and `knowledge.status`. Omitting it is a
validation error, never a fallback to "the last one used". With ten subagents
sharing one daemon, an implicit default resolves one agent's query against
another agent's project ([ADR-0002](../adr/0002-single-local-daemon-over-streamable-http.md)).

`snapshotId` is response provenance today. `knowledge.search` returns it in the
`retrieval` envelope, and `knowledge.status` returns the same state as
`stateHash`, so a caller can compare which canonical state answered. Passing a
`snapshotId` back as a request pin is designed behavior, not implemented in the
current MCP tools.

`agentId` and `taskId` are designed proposal provenance fields. Theurian does
not authenticate agents, and no MCP proposal tool accepts them today.

Schema: [`tool-context.schema.json`](https://github.com/theurian/theurian/blob/main/schemas/mcp/tool-context.schema.json).

## Knowledge

| Tool | Status | Purpose |
| :-- | :-- | :-- |
| `knowledge.search` | Shipped | Hybrid-capable search with provenance and trust labels; falls back to substring retrieval when no index can answer |
| `knowledge.get` | Shipped | Fetch one knowledge item's current revision, with provenance |
| `knowledge.status` | Shipped | Canonical state hash, surfaceable item counts, applied-migration count, schema version, and optional integrity signal |
| `knowledge.getContext` | Planned | Assemble a token-budgeted context pack |
| `knowledge.trace` | Planned | Follow relations from an item |
| `knowledge.listChanges` | Planned | What changed between two snapshots |
| `knowledge.checkFreshness` | Planned | Which knowledge is outside its validity window |
| `knowledge.proposeChange` | Planned write-intent | Emit a proposal; no approved-state write |
| `knowledge.submitFeedback` | Planned | Record retrieval quality signals |
| `knowledge.generateMigrationDraft` | Planned write-intent | Emit a proposal; no approved-state write |

### Planned write-intent tools do not write approved state

No MCP write-intent tool exists in the current server. ADR-0013's designed
write-intent tools produce proposal directories like this illustrative shape:

```text
.theurian/proposals/<proposal-id>/
├── <migration-id>-<slug>.yaml  # schema-valid and directly applicable
├── knowledge/...               # content files, matching the knowledge format
└── evidence.json      # anchors and the reasoning trail
```

There is no MCP path to approved state today — not a flag, not a permission.
`system.capabilities` reports `writeTools: false`, and a test enumerates every
registered tool and asserts none reaches a canonical write
([ADR-0013](../adr/0013-ai-writes-produce-proposals.md)). Proposals happen
through the CLI today: `theurian propose` drafts one, and `theurian propose
accept` moves the files into place. The intended approval path is human review
and merge in Git; Core does not verify that a migration was merged before
`theurian migrate apply` reads it.

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

Schema: [`retrieval-result.schema.json`](https://github.com/theurian/theurian/blob/main/schemas/knowledge/retrieval-result.schema.json).

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

### `knowledge.status`

Six keys, all six always present — and a seventh, `integrity`, only when a
bounded damage check fired ([below](#damage-is-reported-through-a-present-only-integrity-key)).
The contract is
[`knowledge-status-response.schema.json`](https://github.com/theurian/theurian/blob/main/schemas/mcp/knowledge-status-response.schema.json),
which also records why two of them are what they are.

```json
{
  "projectId": "demo",
  "stateHash": "4e640de8baeb1f70e293d88c0b1160f15e6d02df676574937a9557ac8f6d87af",
  "itemCount": 1,
  "itemsByStatus": { "approved": 1 },
  "appliedMigrations": 1,
  "schemaVersion": 3
}
```

Captured from a project the real CLI built, not written by hand. `schemaVersion`
is **3** since #30 PR2 added the `project_integrity` table the damage check reads;
a state hash covers the schema version (ADR-0017), so a reader's own `stateHash`
differs from this one whatever else matches.

| Key | Meaning |
| :-- | :-- |
| `projectId` | Echoed from the request. Required, like every project-scoped tool |
| `stateHash` | Which canonical state the counts were read from. Byte-identical to the `retrieval.snapshotId` a `knowledge.search` answered from that state publishes, so the two can be compared without a second call (FR-R5) |
| `itemCount` | The sum of `itemsByStatus`, and deliberately not the number of items in the store |
| `itemsByStatus` | How many items hold each status a caller may see. A status with no items is absent rather than present with a zero, so `{}` is valid and expected |
| `appliedMigrations` | How many migration files this project has applied. Files, never items. Read from the active pointer's own `migrationCount`, so it cannot shrink when the state database loses migration rows — any difference between the two is reported as `integrity` instead ([#30](https://github.com/theurian/theurian/issues/30)). It is the pointer's number, not a measurement of the rows: if the pointer is itself wrong, this field is wrong with it |
| `schemaVersion` | The canonical store's SQLite schema version — not `protocolVersion`, and not the retrieval index's schema version |

**The counts report nothing about withheld content, not even a total.**
`itemsByStatus` covers `approved`, `draft` and `proposed` only, and `itemCount`
is the sum of that breakdown rather than the size of the store, so a project
whose items are all retired answers `{}` and `0` — the same answer a project
holding nothing gives. Publishing the true total beside a filtered breakdown
would hand the withheld count back by subtraction, which is the question
`knowledge.get` refuses to answer when it declines to distinguish a withheld id
from an absent one (SEC-13, and T-17 in
[the threat model](../security/threat-model.md)).

That is a claim about the counts and not about the whole response.
`stateHash` and `appliedMigrations` both move when a migration creating only
withheld items lands, but on different triggers: `stateHash` moves for any change
to canonical state, `appliedMigrations` only when a migration is *added* — an edit
to an existing one moves the hash alone. Neither can be made to name a status, an
id or a body, and this tool takes only `projectId`, so no request parameter
reaches either. The per-field reasoning is in the schema.

**Index state and proposal ages are not in this response.** The table above
describes the tool this page is a contract for; what ships today is the six keys
here, plus the conditional `integrity` key below.

### Damage is reported through a present-only `integrity` key

`knowledge.search`, `knowledge.get` and `knowledge.status` each carry an optional
top-level `integrity` object ([#30](https://github.com/theurian/theurian/issues/30)):

```json
{
  "integrity": {
    "damageDetected": true,
    "remedy": "Run `theurian migrate apply` to rebuild the derived state from the Git-tracked migrations. If this signal persists, delete `.theurian/state/` and run `theurian migrate apply` again, then `theurian index build` to restore ranked retrieval; the state is derived, so nothing is lost."
  }
}
```

**It is present only when a bounded check detected a discrepancy. Its absence
asserts nothing** — not "verified clean", and a client must not display it as
one. There is no `damageDetected: false` form and there will not be one: the
check is incomplete by design, so a `false` token would claim more than the
product knows, where an absent key claims nothing. `damageDetected` is therefore
always `true` when the key is present; branch on the key, not on its value. This
is the same present-only shape `raptorPath` already uses (ADR-0008 decision 8).

What today's check measures, and the whole of it: **two counts, each compared
against a record of what it should be.**

| Compared | The live number | The record it is checked against |
| :-- | :-- | :-- |
| Migrations | this project's rows in `migration_history` | the `migrationCount` in the active pointer that chose the database |
| Surfaceable items | this project's `knowledge_items` whose status is `approved`, `draft` or `proposed` | `project_integrity.expected_surfaceable_count`, written by `theurian migrate apply` inside its own write transaction |

Either pair disagreeing sets the key. The state database is immutable once built,
so both pairs agree on a healthy project and a difference in *either* direction is
damage: fewer when a row is lost or falls out of a `WHERE` (a sentinel in
`migration_history.project_id`, `knowledge_items.project_id` or
`knowledge_items.status`), more when another project's rows reach this one. This
is a change in *how many* surfaceable rows there are, not in *which* surfaceable
status a row holds: both counts include `approved`, `draft` and `proposed` alike,
so a row moved from one of them to another leaves the counts equal and sets no
key, even though the default answer surfaces only a subset of those statuses and
can shrink — a recorded integrity residual, not a disclosure, since the row is
caller-readable at either status. A
*missing* `project_integrity` row is damage too rather than "not recorded": every
database this build opens declares schema version 4 or is refused unread, and
every apply that creates a database or applies a migration records the count, so a
readable database with no record has lost one.

The first comparison is why `knowledge.status` no longer answers with an
`appliedMigrations` that has shrunk. The second is what makes a response's own
emptiness visible, and the corruption sweep pins it against the real tools over a
real damaged database: a sentinel in `knowledge_items.project_id` gets `count: 0,
results: []` from `knowledge.search` and `itemCount: 0` from `knowledge.status`
**with this key present**, where before PR2 it got those same numbers alone.

**It still does not detect a corruption that leaves the row inside both counts.**
A sentinel in `knowledge_items.item_id` keeps the row's `project_id` and its
`status`, so neither count moves while the item → revision pointer
`knowledge.search` walks is broken: the tool answers with one result fewer — `count:
0, results: []` when it was the only match — with this key *absent* and nothing
else on the response saying otherwise, since `retrieval.stale` reports `false` on
the ranked path and `null` on the unranked one and neither reports damage. That is
the single member of `UNDETECTED_UNDERREPORT` in
`tests/integration/test_canonical_store_corruption.py`, an exact set: a second
position appearing there is a test failure, not an expectation to update. A
different pointer fault would *disclose* rather than under-report — an item whose
`current_revision_id` names another item's revision, served as the wrong item's
body — and that one is refused at read time by the item → revision consistency
guard (#24, #30), not by this count. Neither
count is a checksum, so two damaged cells that cancel out are invisible, as is a
corrupt `title` or `body` the response hands over directly.

**A corrupt `status` cell reaches the three tools differently, and a client
should expect all three answers.** Measured on a sandbox project, one approved
item's `status` overwritten: `knowledge.status` answers a shrunken `itemCount`
with this key present; `knowledge.search` answers `count: 0` with the key present
when the project has no published index and the substring fallback is answering,
and *refuses* when a published index makes the ranked path answer, because the
canonical gate parses each candidate's status and an uninterpretable one raises
rather than being skipped; `knowledge.get` refuses for the same reason as soon as
it reads that row. The refusals are the state-database message naming
delete-and-rebuild, not the `integrity` key.

**The remedy names a fallback because the first command does not cure every
shape.** The state database is derived and Git-ignored
([ADR-0004](../adr/0004-sqlite-is-a-derived-artifact.md)), so nothing authored is
lost by rebuilding it. `theurian migrate apply` is the cheap cure and comes first:
measured, it clears the key for a lost row, for a sentinel in
`migration_history.project_id`, and for a pointer that over-counts. It clears
nothing for a *surplus* row — the direction `!=` deliberately catches — because
every authored migration is already applied, so the command exits 0 and the key is
still there on the next call. Deleting `.theurian/state/` makes the following
apply rebuild the database, and `theurian index build` is named third because that
deletion takes the published retrieval index with it: measured, `retrieval.indexed`
is `false` with `fallbackReason: "no-index"` after the second step and `true` again
after the third. Following the string token by token takes a surplus row from
`integrity` present to absent with ranked retrieval intact. The efficacy is
measured, not yet pinned by a test.

It clears nothing for a damaged item count either, and that is deliberate rather
than an oversight: an apply with nothing pending records no new expected count,
because re-recording it from the damaged state would clear the signal without
repairing anything. The second step is what cures that shape
(`test_a_pending_free_apply_does_not_re_record_over_a_damaged_state`, with
`test_an_apply_that_changes_the_store_records_the_new_count` holding the other
direction).

`knowledge.get` refuses with a message rather than a payload when an item cannot
be returned, so it carries the distinction in the text: over detected damage it
reports a project that "could not be fully read: its derived state disagrees with
its own records about what it holds", instead of the message it gives for an item
that is simply not present. A withheld id and an absent id still get the *same*
message as each other (SEC-13) — what changed is that "the state disagrees with
its own records" is no longer reported as absence.
Both directions are pinned, since either alone is satisfied by a tool that says
one thing always
(`test_an_absent_item_over_a_damaged_state_is_refused_as_damage_not_absence`,
`test_an_absent_item_over_a_healthy_state_is_refused_as_absence`).

The reasoning, the measurements and what remains uncovered are in
[the threat model](../security/threat-model.md) under T-17.

## Review

Review ingestion is planned, not shipped. The current server reports
`reviewIngestion: false`, and none of these tools are callable today.

| Tool | Status | Purpose |
| :-- | :-- | :-- |
| `review.search` | Planned | Search review history |
| `review.getThread` | Planned | One thread with comments and resolution |
| `review.findSimilar` | Planned | Threads resembling a described situation |
| `review.getDecisions` | Planned | Decisions reached in review |
| `review.generateKnowledgeCandidate` | Planned write-intent | Emit a proposal; no approved-state write |
| `review.listUnresolved` | Planned | Open threads |

The designed `review.findSimilar` tool is the one expected to change outcomes:
it would answer "has this come up before?" before an agent reimplements something
the team already rejected.

## Specification

Specification-specific MCP tools are planned, not shipped. Specification content
can be stored and searched as knowledge today, but none of these callable tools
exist in the current server.

| Tool | Status | Purpose |
| :-- | :-- | :-- |
| `spec.search` | Planned | Search specifications |
| `spec.get` | Planned | Fetch a spec with its **structured** fields intact |
| `spec.getDependencies` | Planned | What this spec depends on |
| `spec.getImplementationStatus` | Planned | Implemented, partial, or not started |
| `spec.getCoverage` | Planned | Which declared outcomes have verifying tests |
| `spec.findContradictions` | Planned | Specs that disagree |
| `spec.findStaleImplementations` | Planned | Code referencing superseded specs |

In the planned specification surface, `spec.get` returns the native structure,
not a prose rendering. `getCoverage` depends on that: coverage means "which of
these declared outcomes has a test", and the outcomes must still exist as data
([ADR-0010](../adr/0010-three-layer-knowledge-model.md)).

## Traceability

Traceability graph queries are planned, not shipped. The current server reports
`traceability: false`, and none of these tools are callable today.

| Tool | Status | Purpose |
| :-- | :-- | :-- |
| `trace.get` | Planned | Edges touching a node |
| `trace.findImplementations` | Planned | What implements a spec |
| `trace.findTests` | Planned | What verifies a spec |
| `trace.findUnimplementedSpecs` | Planned | Specified, not built |
| `trace.findUnverifiedSpecs` | Planned | Built, not tested |
| `trace.findCodeWithoutSpec` | Planned | Maintained, never specified |

## Project and system

| Tool | Status | Purpose | Project-scoped |
| :-- | :-- | :-- | :-- |
| `project.list` | Shipped | Registered projects, and the ids nothing can serve | no |
| `project.status` | Planned | Migration, index, and commit state | yes |
| `system.health` | HTTP route, not an MCP tool | Liveness and version | no |
| `system.capabilities` | Shipped | What this build supports | no |
| `system.indexStatus` | Planned | Active build, staleness, builds in progress | yes |

`system.capabilities` exists so a client can degrade per feature rather than
all-or-nothing. Version gating is coarse; if only summarization is unconfigured,
everything else should still work.

`sensitivityEnforcement: true` is the flag with a consequence for how a client
reads an *empty* answer. This build enforces the disclosure axis against a
ceiling the operator declares, so no results can mean "withheld", not only
"nothing matched" — a client should not tell a user that a project holds no such
knowledge. The flag reports that the axis is enforced and **never which ceiling
this deployment declares**: that word would tell a caller which levels it is not
being shown, and this tool resolves no project and passes no authorization gate.
An operator reads the ceiling from the file they wrote it into
([ADR-0025](https://github.com/theurian/theurian/blob/main/docs/adr/0025-sensitivity-is-enforced-before-0-1-0-stable.md)).

<!-- capabilities-fields:begin -->
The response's fields serve different roles, not one uniform contract.
`capabilities` is what a client degrades against, one feature at a time.
`version` and `protocolVersion` re-publish the same two process constants
`theurian compat check` reads directly when it resolves CP-6
(`docs/architecture/requirements-analysis.md`, "Claude Code plugin
requirements") — the gate itself never reads this response, so a client does
not compare these two fields to decide compatibility; that comparison already
happened before either surface answered. If the two ever disagree, a client
that already passed the gate sees a different build reported here than the
one it was checked against. For a client that only calls MCP tools, this is
still the sole place `protocolVersion` is readable at all: liveness is served
at the `/health` HTTP route, outside the MCP tool surface, with no
callable-tool equivalent in this build. `schemaVersion` reports the canonical
store's schema version. `note` is prose, not a field a client parses.
<!-- capabilities-fields:end -->

### `project.list`

Four keys, all four always present. The contract is
[`schemas/mcp/project-list-response.schema.json`](https://github.com/theurian/theurian/blob/main/schemas/mcp/project-list-response.schema.json).

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

`knowledge.search` publishes one refusal this table does not cover: under
sustained concurrent load — more than `MAX_CONCURRENT_SEARCHES` calls already
in the retrieval answer block — the daemon refuses with a constant, retryable
`ToolError` naming the cap, rather than queueing the caller without bound.
It carries no code from the table above and no `details`; today the refusal
is distinguishable only by its message text, not by a machine-readable field.
A coded, `retryAfter`-carrying envelope for it is tracked in
[#419](https://github.com/theurian/theurian/issues/419).

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
[SECURITY.md](https://github.com/theurian/theurian/blob/main/SECURITY.md) rather than buried here.

## Changing this contract

Additive changes (a new optional field, a new tool) are MINOR and do not bump
`protocolVersion`. Removing a field, tightening a type, adding a required field,
or renaming a tool is breaking and bumps it. See
[plugin-core-compatibility.md](plugin-core-compatibility.md).

`knowledge.search`'s admission refusal (see Errors, above) is a behaviour of
the shipped surface as of this change. It is a client-visible behaviour
change — a call that once queued can now be refused (the CHANGELOG records it
under Changed, not Added) — but it alters no schema and no message shape, so
it does not bump `protocolVersion`.

**`protocolVersion` is still `theurian/v1` after Milestone 5 and #206, which
between them made four breaking changes to this contract.** That is a
decision, recorded here because the alternative reading is that somebody
forgot. The rule above governs changes *from a released protocol*, and
Milestone 5's three qualify outright: no published version of Core has ever
lacked them, so no client was ever pinned to a `v1` that lacked them, and
bumping would publish a `theurian/v2` whose `v1` never shipped. Milestone 5's
breaking set is the *content* of `v1`, not a departure from it. `milestone`'s
removal is the fourth, and it does not qualify the same way — its own ground
is below.

The four, so that "breaking but unbumped" is checkable rather than asserted:
the `knowledge.search` response reshape, the removal of `withheldSuperseded`,
and the two required fields `project.list` gained (all Milestone 5), and the
removal of `system.capabilities.milestone` (#206). Each is named as BREAKING
in the changelog, which is what protects an integrator. The first bump is the
first breaking change after the version that first carries `theurian/v1`.

**`milestone`'s exemption rests on different ground.** Measured across
`core-v0.1.0.dev0` through `core-v0.1.0.dev4`, the field shipped in every
released tag, all under `theurian/v1` — unlike Milestone 5's three, which
never shipped under a released `v1` at all before the milestone that changed
them, so a client *could*, in principle, have been built against
`milestone`'s presence. The exemption is granted anyway, on grounds specific
to this one field: it was never defined in this document or in any schema
under `schemas/mcp/`, it has zero consumers — search-verified across this
repository, plugins included — and the project is pre-1.0 on a `dev` line
with no known external integration to break. Publishing `theurian/v2` over a
field nothing reads would trip CP-6's compatibility gate
(`docs/architecture/requirements-analysis.md`, "Claude Code plugin
requirements") for a change no known integrator consumes. **This exemption is
scoped to `milestone` alone**: it says nothing about `version` or
`protocolVersion`, which re-publish the same constants the gate itself reads
directly, never through this response (see the `system.capabilities`
paragraph under "Project and system" above).

**`theurian propose accept`'s exit code for an already-accepted proposal is the
fifth, and is exempted on the same narrow grounds
([#254](https://github.com/theurian/theurian/issues/254)).** Re-accepting an
accepted proposal exited 1 and now exits 4, which the compatibility table's
"changing an exit code's meaning" row makes breaking. The command shipped in
`core-v0.1.0.dev7`, so unlike Milestone 5's three this *did* ship under a
released `theurian/v1`. The exemption is granted on the same three facts
`milestone`'s rests on: the code has zero consumers — search-verified across
this repository, plugins included, where the only exit-code branch in any
plugin script is `session-start.sh` on `compat check`'s exit 3
(`THEURIAN_EXIT_INCOMPATIBLE`), and `/theurian:propose` reads `--json`; the
project is pre-1.0 on a `dev` line with no known external integration; and the
change moves a case *toward* the meaning this document already published for 4
rather than away from it, since the table always documented 4 for "that
migration is already in place". **Scoped to this one code on this one command**:
it says nothing about `compat check`'s 0/2/3, which a plugin script does branch
on, or about `migrate apply`'s 4.
