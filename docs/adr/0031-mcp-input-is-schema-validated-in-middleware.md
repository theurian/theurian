# ADR-0031: MCP tool input is validated against its published schema, in middleware, before any handler runs

- Status: accepted
- Date: 2026-09-12
- Deciders: Theurian maintainers
- Requirements: SEC-12, SEC-13, SEC-17, T-12
- Situates against [ADR-0003](0003-ports-and-adapters.md) (the boundary is an
  adapter concern, not a domain one), [ADR-0013](0013-ai-writes-produce-proposals.md)
  (the control SEC-12 owes before a write-intent tool opens),
  [ADR-0014](0014-dependency-pinning-and-pre-1-0-isolation.md) (why the SDK is
  not patched), and [ADR-0032](0032-the-write-intent-mcp-tool-surface.md) (the
  surface this control is a precondition for)

**This ADR recorded a decision and shipped no code**: no schema file, no
middleware, no registration change and no test landed with it, and its own diff
was confined to `docs/`. **The decision is implemented as of Phase B slice B2**
([#662](https://github.com/theurian/theurian/issues/662),
[PR #663](https://github.com/theurian/theurian/pull/663)), which is why the
status above is `accepted`. *Compliance* names what that slice discharged, with
the test that discharges it, and what stays owed and to whom. Nothing above
*Compliance* is rewritten: the measurements below are dated and anchored to a
commit, and re-writing them to today's tree would delete the evidence the
decision rests on.

**Every repository fact below was measured on 2026-09-12 against `be977ea7`**,
which is the commit this document was written at and is reachable from
`origin/main`. Each is stated with the file and the symbol that carries it, so a
reader can attack the reading rather than the conclusion.

## Context

SEC-12 reads: *"Validate every MCP tool input against its published JSON Schema
before it reaches application code"*
(`docs/architecture/requirements-analysis.md`, the SEC table). Two records say
plainly that it does not run:

| Record | What it says today |
| :-- | :-- |
| `docs/security/threat-model.md`, under *Future controls, not shipped* | "SEC-12 — validating every MCP tool input against its published JSON Schema at the boundary — is not implemented; input is checked by domain construction as above, not against the schemas." |
| `docs/roadmap.md`, the Phase 0 requirement table | the SEC-12 row's *What ships* column reads `nothing` and its *What is owed* column reads `the whole control` |

What runs instead is domain construction: a tool body builds `ProjectId`,
`ItemId`, `Sensitivity` and their siblings, and each type refuses a value it
cannot represent. That is a real control and it is not this one. It checks the
values a handler *reads*; it says nothing about the keys a caller *sent*.

**`schemas/mcp/` holds eight files, of which exactly one is input-side — and
nothing reads it.** Seven are responses or response fragments
(`knowledge-search-response`, `knowledge-status-response`,
`project-list-response`, `retrieval-metadata`, `review-findings-response`,
`review-search-response`, `system-capabilities-response`). The eighth,
`tool-context.schema.json`, is a published *input* contract: it types the
context fields every project-scoped call carries — `projectId` required,
`snapshotId`, `agentId`, `taskId` — and it already sets
`additionalProperties: false`.

**It is enforced against no real traffic, and the repository says so in its own
words.** `schemas/README.md`'s what-verifies-each-schema table gives that row as
"nothing, and nothing should: it describes tool *input*, so there is no response
to compare", and the one test it names,
`tests/unit/test_schemas.py::test_project_id_is_required_on_every_tool_call`,
validates two literal documents against the schema rather than validating any
real call.

**That row names one test where five hold properties of this file**, which slice
B2 needs to know because one of the five moves. Four of them are parametrized
over the schema tree and name the file in their node id; the fifth loads it by
literal path and does not. Collected:

```console
$ uv run --frozen python -m pytest packages/theurian-core/tests/unit/test_schemas.py \
    --collect-only -q | grep -i "tool.context"
.../test_schemas.py::test_schema_is_valid_draft_2020_12[tool-context.schema.json]
.../test_schemas.py::test_schema_declares_an_id_and_title[tool-context.schema.json]
.../test_schemas.py::test_object_schemas_reject_unknown_properties[tool-context.schema.json]
.../test_schemas.py::test_every_published_project_id_pattern_admits_exactly_what_projectid_constructs[mcp/tool-context.schema.json-('properties', 'projectId')]
```

(The node-id prefix is abbreviated to `.../`; nothing else is edited.) The fifth
is `::test_project_id_is_required_on_every_tool_call`, which builds a validator
from `_load("mcp/tool-context.schema.json")` at `test_schemas.py:294` — a
literal, so no node id carries the filename and the collection key cannot see
it. `git grep -n "tool-context" -- packages/theurian-core/tests` returns exactly
two lines, `:294` and `:781`, which is the other half of the derivation.

**Only `::test_object_schemas_reject_unknown_properties[tool-context.schema.json]`
moves.** It asserts that every top-level object schema sets
`additionalProperties: false`, and decision 1's composition moves that keyword
off this file. The other four hold shape, `$id`/title, the `projectId` pattern
against `ProjectId`, and the required-`projectId` property, none of which
decision 1 touches.

The population of *references* to the file under the ADR's own key is **five**
hits, not four — and the key carries its frame, because ADR-0032 has since added
hits of its own and a reader running this against `HEAD` gets a different number
for a reason that has nothing to do with SEC-12:

```console
$ git grep -n "tool-context" be977ea7 -- packages schemas docs tools | cut -d: -f2,3
docs/protocol/mcp-tools.md:50
packages/theurian-core/tests/unit/test_schemas.py:294
packages/theurian-core/tests/unit/test_schemas.py:781
schemas/README.md:98
schemas/mcp/tool-context.schema.json:3
```

In order: the protocol page's link, the literal-document test, the
`projectId`-pattern face's parametrization entry, the `schemas/README.md` row
that records the absence, and the schema's own `$id`. (The `cut` drops the
`be977ea7:` prefix and the matched line; nothing is annotated in place of it.)
**No filter is applied and none is needed at this frame** — an earlier draft
piped this through `grep -v ':docs/adr/0031'` and `grep -v ':docs/work-logs/'`,
and both are no-ops here: this ADR did not exist at `be977ea7` and the work logs
carry no hit, so the count is 5 with or without them. A reader running the key
against `HEAD` does need both filters. **The conclusion is unchanged and is the
one that matters: no code path under `src/` names it.**

So SEC-12 costs two things, and the first is smaller than it looks: a
**per-tool** input schema does not exist yet, and the artifact that does exist
has no seat to be enforced from. Decision 2 is that seat, and `schemas/README.md`'s
"nothing should" sentence is one of the records that stops being true when it
lands.

### Why the seat is forced, and both halves are measured

**The SDK validates arguments and silently drops the keys it does not know.**
`mcp==2.1.1` builds a per-tool pydantic model from the handler's signature
(`mcp/server/mcpserver/utilities/func_metadata.py`, `create_model(...,
__base__=ArgModelBase)`), and `ArgModelBase`'s configuration is
`ConfigDict(arbitrary_types_allowed=True)` — no `extra="forbid"`. The generated
wire models are the same shape: `mcp_types/_wire_base.py`'s `WireModel` sets
only `populate_by_name` and says in its own docstring that "subclasses set
`extra` themselves", and `extra="forbid"` appears **zero** times anywhere in the
`mcp_types` package — the whole package rather than one module, because a single
module is a key a reader can attack and the package is the claim that matters:

```console
$ grep -rho 'extra="forbid"' .venv/lib/python3.13/site-packages/mcp_types/ | wc -l
       0
```

Pydantic's default for an unset `extra` is `ignore`. So an unknown key
in `params.arguments` is dropped before any Theurian code sees the request, and
no code Theurian could write *inside a handler* can notice that it was there.

**The registration seam runs after that coercion, so it cannot hold this
alone.** `mcp/tools.py`'s `_tool` is the one registration seam, and it is
pinned twice — by
`tests/unit/test_tool_error_type_contract.py::test_every_tool_is_registered_through_the_one_seam`,
which reads `register`'s AST, and by
`tests/integration/test_mcp_tools.py::test_every_registered_tool_goes_through_the_forwarding_seam`,
which asks the *built* server whether each `Tool.fn` carries `functools.wraps`'
`__wrapped__`. That seam is where a below-surface refusal is converted into a
caller-visible one, and it is the right place for that. It is the wrong place
for this: by the time `_forwarding`'s wrapper runs, the arguments have already
been through the SDK's model, and the dropped keys are gone.

**The middleware tier is the one seat that sees raw params.** The SDK's
`ServerMiddleware` protocol (`mcp/server/context.py`) states its own contract:
it "runs at the top of `ServerRunner._on_request` / `_on_notify` after `ctx` is
built but **before any validation**, lookup, or handshake", and "the method and
the raw inbound params are `ctx.method` and `ctx.params` (**no model validation
has happened yet**)". `CallNext`'s docstring says the same from the other side:
"`ServerMiddleware` runs before params validation, so its rewrites change what
the handler is invoked with; an `Extension` interceptor runs after, so its
rewrites change only what the handler observes on `ctx`."

**Nothing is wired there today.** `daemon/runner.py`'s `build_server`
constructs `MCPServer(name=..., title=..., version=..., instructions=...)` and
passes no middleware, then hands the server to `register`. So the seat is empty
rather than occupied by something else, and slice B2 is the change that fills
it.

**The seat is a position inside the SDK's own two middlewares, not the outermost
one, and that placement is the SDK's rather than a choice.** `MCPServer.__init__`
appends `RequestStateBoundary` to a list `Server.__init__` has already seeded
with `OpenTelemetryMiddleware`, and then extends it with whatever the caller
passed, with the comment stating the order: "User middleware runs inside the
SDK's built-ins (OpenTelemetry, then the request-state boundary), outermost-first
in the order given." So Theurian controls the order *among its own* middlewares
and not its position relative to those two. That is fine for this control — both
built-ins run before params validation, so `ctx.params` still carries the raw
keys — and it is recorded because it is not something the ADR chose.

## Decision

### 1. Every registered tool has a published input schema, and `schemas/mcp/` is where it lives

An input schema is a **wire contract**, exactly as the response schemas already
are: it is versioned with the tree, it is what
`tests/integration/test_wire_contract.py` validates real traffic against, and a
change to it is a change to what clients may send. It is not an internal
convenience the server may tighten or loosen between builds.

The filenames are slice B2's to choose; this ADR fixes only that they live
beside the response schemas under `schemas/mcp/` and that there is one per
registered tool. **Naming the files here would assert artifacts that do not
exist**, which is the failure mode ADR-0030 records for a design document that
tries to stay in bijection with a tree.

**`tool-context.schema.json` becomes the first schema this control reads, not a
ninth thing to write.** It already types the context every project-scoped call
carries and already forbids additional properties; what it has never had is a
reader. `referencing`'s offline registry (decision 3) is what makes a `$ref`
resolvable with no network.

**There is exactly one composition that works, and it costs an edit to the
referent — measured rather than assumed.** The obstacle is that
`additionalProperties` in Draft 2020-12 considers only the `properties` in *its
own* schema object, so a per-tool schema that references the context and then
closes itself rejects the very fields it referenced. Three forms were driven
against `jsonschema==4.26.0` with a real registry, each against a valid document
(`projectId` + two tool fields) and a document carrying one unknown key:

| Form | Referent keeps `additionalProperties: false` | Referent's closure moved out |
| :-- | :-- | :-- |
| `$ref` with sibling `properties` + `additionalProperties: false` | **rejects the valid document** (`'body', 'itemId' were unexpected`) | **rejects the valid document** (`'projectId' was unexpected`) |
| `allOf: [{$ref}]` + `additionalProperties: false` | **rejects the valid document** | **rejects the valid document** |
| `allOf: [{$ref}]` + `unevaluatedProperties: false` | **rejects the valid document** | **accepts it, and rejects the unknown key** |

Only the bottom-right cell is a working input schema, so decision 1 fixes the
shape: **each per-tool schema is `allOf: [{"$ref": tool-context}]` plus its own
`properties`, closed with `unevaluatedProperties: false`** — the keyword that,
unlike `additionalProperties`, sees what the `allOf` branch evaluated.

**`tool-context.schema.json`'s own `additionalProperties: false` therefore moves
to the per-tool closure, and its pin moves with it.**
`tests/unit/test_schemas.py::test_object_schemas_reject_unknown_properties[tool-context.schema.json]`
asserts that every top-level object schema sets the keyword, so B2's edit to the
referent takes it RED — deliberately. The closure is not lost; it relocates to
each tool's `unevaluatedProperties`, and the pin has to be rewritten to say so
rather than deleted.

**The `retrieval-metadata` precedent does not transfer, and that is why this had
to be measured.** `knowledge-search-response.schema.json` uses `$ref` in a
**property slot** — `"retrieval": {"$ref": ...}` — where the referent's own
closure applies to its own object and nothing composes. Same-level composition
is a different problem, and reading the precedent as if it answered this one is
what an implementer would do without this table.

**The alternative stays available**: inline the four context properties in each
per-tool schema, keeping `additionalProperties: false` and duplicating the
context four keys at a time. Its cost is **one further published copy of the
`projectId` pattern per per-tool schema**, not one in total —
`_PROJECT_ID_FACES` (`tests/unit/test_schemas.py:779-788`) already tracks
**five** faces and pins each against `ProjectId`, so the first inlined schema
makes a sixth and the seven tools registered today
(`grep -c '@_tool(' packages/theurian-core/src/theurian/mcp/tools.py` → **7**)
would take it to **twelve**. The duplication is what `$ref` exists to avoid, and
at that multiplier it is the fallback rather than the plan.

### 2. The validation seat is an SDK `ServerMiddleware`, wired where the server is built

The check runs in a `ServerMiddleware` registered on the `MCPServer` that
`daemon/runner.py:build_server` constructs. It reads `ctx.method` and
`ctx.params`, and for a `tools/call` it resolves the tool name and validates
`params.arguments` against that tool's published input schema **before**
`call_next(ctx)` is awaited.

Two properties follow from the measurements above, and neither is available at
any other tier:

1. **The closure is enforceable.** The middleware sees the keys the caller
   actually sent, because the SDK has not yet built the model that would drop
   them.
2. **The check precedes the handler by construction, not by convention.** The
   SDK's own dispatcher wraps params validation, the handler call and the
   pre-init gate inside `call_next(ctx)`, so a tool cannot opt out of the
   control by being registered differently — the way it *could* opt out of a
   check written into individual tool bodies.

The `_tool` seam keeps the job it already has. The two controls compose: `_tool`
holds *how a refusal raised below the surface reaches the caller*, and the
middleware holds *which requests reach a handler at all*.

### 3. No new dependency: `jsonschema` is already a runtime dependency, and the offline registry pattern already exists

`packages/theurian-core/pyproject.toml` lists `jsonschema==4.26.0` and
`referencing==0.37.0` among the core's six runtime dependencies. `referencing`
is declared there specifically because Theurian imports it for "the no-network
registry that keeps the installed migration schema offline (issue #235)" — the
pyproject comment says so in those words.

Validation reuses that pattern rather than inventing a second one:
`infrastructure/filesystem/migration_loader.py` already builds a validator over
a locally-resolved registry and already bounds a document before validating it
(`MAX_DOCUMENT_NESTING`, `MAX_DOCUMENT_NODES`, `MAX_DOCUMENT_RENDERED_CHARS`,
recorded on `validate_migration_document` for issues #291 and #245 — an
unbounded document cost unbounded work inside `jsonschema`'s own message
building). The MCP boundary takes untrusted input from a caller, which is the
same shape, so the bounds travel with the pattern.

This keeps ADR-0014's dependency count unchanged, which matters because the
alternative that would have added one is rejected below.

### 4. Unknown keys refuse, and a refusal is bounded and does not echo unbounded caller input

Every published input schema is closed against unknown keys — with decision 1's
`unevaluatedProperties: false`, the one keyword the table there measured as
composing with the context `$ref` — and a request carrying a key the schema does
not name is refused rather than trimmed. The reason is the one the
domain-construction control cannot reach: a key the server silently drops is a
caller believing it asked for something it did not get, and on a write-intent
surface (ADR-0032) that difference is the difference between a proposal a human
reviews and a proposal a human reviews *without the constraint the agent
thought it had set*.

**A refusal message is bounded, and the house pattern is already in the tree.**
`mcp/tools.py`'s unregistered-project refusal reports an oversized `project_id`
by its **length** rather than echoing it, with the reason written beside it:
"an oversized id is reported by its length rather than echoed, so the error
cannot be turned into an ~1x amplifier of the caller's own bytes (#17)". The
same shape governs a schema refusal: it names the offending **key path** and the
constraint that rejected it, and it does not reproduce an arbitrary-length
value the caller supplied. The bound and the exact wording are slice B2's; the
rule is this ADR's.

### 5. Fail-closed by structure: a registered tool with no loaded input schema is refused at dispatch

A tool name the middleware cannot resolve to a loaded schema is refused, and the
refusal is the *dispatch* answer — not a pass-through with a log line. Stated as
the property a test must hold: **a tool registered without a published input
schema is unreachable.**

This is the clause that makes the control survive the next contributor. A
control that applies to the tools someone remembered to enumerate is a control
that a future tool leaves by omission, and the failure is silent in exactly the
direction that matters. The same reckoning is why `PROCESS_SPAWN_SITES` in
`tests/unit/test_network_call_sites.py` is asserted by equality against the
whole set rather than by membership: it fails on an addition *and* on a removal.

### 6. The published schema and the SDK-derived schema must agree, and the agreement is recomputed

The SDK derives its own input schema from each handler's type annotations, and
`_tool`'s use of `functools.wraps` is what keeps that introspection working —
`_tool`'s docstring records it: "`functools.wraps` keeps the wrapped signature,
annotations and docstring, which is what the SDK reads to build each tool's
input and output schema".

So two descriptions of the same input exist: the published file and the one the
SDK computes. **They must not drift**, and the check is a test that recomputes
the agreement from both live sides rather than a reviewer comparing them by eye.
The exact equivalence relation is slice B2's to fix — a derived schema and a
hand-written one differ in incidental ways (title strings, `$defs` placement)
that a naive equality would trip over, so what is owed is a *stated* relation
with a positive control proving it can fail, not a `==`.

**"Tighter" is not the defect; two specific things are.** A published schema is
*supposed* to be tighter than a Python annotation on the value domain — that is
the whole argument in the alternatives table against generating the published
file from the handler signature, and it is why an `enum`, a `pattern`, a range
or a `format` belongs in the file and not in a type hint. What the agreement
must actually forbid:

1. **The published schema permits what the handler refuses.** A request the
   contract admits and the server rejects is a published lie, and it is the
   direction that costs a caller a failed call it was told would work.
2. **Tightening that deletes a documented capability.** A published `enum`
   narrower than the set the handler serves retires a capability by editing a
   file, with no changelog entry and no deprecation.

So the relation is asymmetric: the published schema **may** constrain a value
domain the annotation does not, and **may not** admit what the handler refuses.

**The closure axis is outside the relation by design, and that is measured.**
The SDK sets no `additionalProperties` on any tool's derived schema. Driven
against the built server:

```python
# over build_server(...)._tool_manager.list_tools(), sorted by name
for tool in sorted(tools, key=lambda t: t.name):
    print(f"{tool.name:22}{tool.parameters.get('additionalProperties', '<absent>')}")
```

```console
knowledge.get         <absent>
knowledge.search      <absent>
knowledge.status      <absent>
project.list          <absent>
review.findings       <absent>
review.search         <absent>
system.capabilities   <absent>
tools with no additionalProperties: 7 of 7
```

A relation that compared closure would therefore be RED on every tool, for ever
— the published schema closes and the derived one never does. The closure is
enforced by the **middleware** (decision 2), not by this comparison, and the
relation states that exclusion explicitly rather than discovering it on the
first run.

**What stays a handler-layer refusal, and what becomes a schema refusal.**
Shape, key set, enum membership, pattern, range and format move to the schema,
and their refusals arrive as a key path and a constraint (decision 4). Refusals
that depend on state a schema cannot see stay in the handler with their remedies
intact: an unregistered project id, a snapshot that does not resolve, an item
the caller may not read. That split is what stops decision 6 from being read as
"every refusal becomes a schema refusal", which would delete the remedies
`mcp/tools.py` carries.

## Consequences

### Positive

- **SEC-12 stops being owed and starts running**, and the three records that
  currently say it does not (`threat-model.md`'s *Future controls, not shipped*
  entry, `roadmap.md`'s `nothing` / `the whole control` row, and
  `schemas/README.md`'s "nothing, and nothing should" row for
  `tool-context.schema.json`) move in the slice that ships it, not later.
- **A published input contract stops being decorative.**
  `tool-context.schema.json` has typed every project-scoped call's context
  since it was written and has never been read; the middleware is what makes it
  a control rather than a description.
- **The write-intent surface gets its precondition.** `docs/roadmap.md`'s
  Phase B row already states that "SEC-12 … becomes mandatory the moment a
  write-intent tool opens". ADR-0032 depends on this decision landing first.
- **The contract becomes checkable in both directions.** A client can read what
  it may send; a server test can hold that what it accepts is what it published.
- **No new dependency, and no fork of the SDK.** The core's runtime dependency
  list stays at six packages.

### Negative

- **A second description of every tool's input exists**, and two descriptions of
  one thing drift. Decision 6 is the answer, and it is an owed test rather than
  a structural impossibility — until it lands, the agreement is a convention.
- **The middleware tier is SDK surface, and the SDK is pinned rather than
  stable.** `ServerMiddleware`'s ordering guarantees are `mcp==2.1.1`'s, the
  position of user middleware relative to the SDK's own two is fixed by
  `MCPServer.__init__`, and the SDK's own `middleware` property says the chain
  "may change in a 2.x minor release". A version bump is where those would
  change. ADR-0014's exact pinning is what makes that a deliberate event rather
  than a surprise, but the coupling is real and is recorded here rather than
  discovered at the bump.
- **The context schema loses a keyword it has always carried.** Decision 1's
  composition moves the closure off `tool-context.schema.json` and onto each
  per-tool schema, which reddens a pin that has held since the file was written.
  The closure is not weakened — it is enforced once per tool instead of once in
  the referent — but for the length of slice B2's commit the file that types
  every call's context does not close itself, and an implementer who moved the
  keyword and did not rewrite the pin would read a red test as noise.
- **Refusing unknown keys is a compatibility decision.** A client that sends a
  forward-looking field today gets a refusal instead of silence. That is the
  intended direction — silence is what this control exists to end — but it is a
  behaviour change for any caller relying on the current tolerance.

### Neutral

- **Domain construction is not replaced.** `ProjectId` and its siblings keep
  refusing values they cannot represent; the two controls answer different
  questions, and one is not a weaker form of the other.
- **Response schemas are untouched.** This ADR adds an input side; it changes
  nothing about what `test_wire_contract.py` already validates on the way out.

## What this does not close

1. **Which tools exist.** This is a control over the registered set, whatever it
   is. ADR-0032 decides what joins it.
2. **Authorization.** SEC-13 project scoping is a separate control at a
   different point, and a schema-valid request for a project the caller may not
   read is still a schema-valid request.
3. **Content safety of the values.** A schema constrains shape, not meaning.
   SEC-11's secret scan and SEC-15's safety triple keep their own seats.
4. **Rate and cost bounds.** A caller who can make the server spend work not
   bounded by a recorded limit is the T-6 family's, not this one's;
   [#26](https://github.com/theurian/theurian/issues/26)'s concurrency cap is
   the precedent for how such a bound is recorded.
5. **The exact input-schema filenames and the exact refusal wording.** Slice
   B2's, deliberately: naming them here would assert artifacts that do not
   exist.

## Alternatives considered

| Alternative | Why rejected |
| :-- | :-- |
| **Validate inside the `_tool` registration seam** | The seam runs after the SDK has built its argument model, and that model drops unknown keys (`ArgModelBase` sets no `extra="forbid"`, measured above). A check there can validate the values a handler receives and can never see the keys a caller sent, so the closure — the half that catches a client asking for something this build does not implement — is unenforceable from it. |
| **A Starlette middleware in `daemon/server.py`** | It sits below the MCP framing, so it would have to re-parse JSON-RPC envelopes and Streamable-HTTP batching to find `params.arguments`. That is a second implementation of the SDK's own dispatch, and it would be wrong in a way nothing tests the day the transport changes. The SDK middleware tier is handed the parsed method and params by contract. |
| **Patch or fork the SDK's request models to `extra="forbid"`** | Two costs. The dependency is exact-pinned by [ADR-0014](0014-dependency-pinning-and-pre-1-0-isolation.md), so a patched model is a fork to carry across every bump. And it answers the wrong requirement: `forbid` rejects keys the *handler signature* does not name, while SEC-12's own text demands validation "against its published JSON Schema" — a stricter and different statement, since a published schema constrains value ranges, enums and formats that a Python annotation does not. |
| **Generate the published schema from the handler signature at build time** | It removes the drift of decision 6 by removing one of the two descriptions, which sounds strictly better and is not: the published artifact would then be a projection of the implementation rather than a contract the implementation is held to, so every accidental widening of a parameter type would publish itself as an intentional contract change. The contract is supposed to be the thing that does not move by accident. |
| **Validate only the write-intent tools, since those are the new surface** | The control would then apply to the tools someone remembered, which is the failure decision 5 exists to prevent. It also gets the risk backwards on the read side: `knowledge.search` and `review.search` take caller text today and are the surfaces a malformed request reaches first. |

## Compliance

**This ADR shipped no behaviour, so at the time it was written it had no shipped
test to name.** Its enforcement at design time is the measurements it cites; its
enforcement at implementation time is the tests slice B2 owed and has now
delivered. The two halves are kept apart below rather than merged: what B2
landed is named with the test that holds it, and what is still owed keeps the
house *Still owed* heading and names the slice or the issue that will satisfy it.

Measured at `be977ea7` on 2026-09-12 — the state this decision was taken
against, reproducible from that sha and **deliberately not re-measured**, since
slice B2 moved most of them and re-writing them to today's tree would delete the
evidence the decision rests on:

- `schemas/mcp/` holds **8** files (`ls schemas/mcp/`): seven response or
  response-fragment schemas and one input-side contract,
  `tool-context.schema.json`. **No per-tool input schema exists**, and the one
  input schema that does has **no reader under `src/`** —
  `git grep -n "tool-context" be977ea7 -- packages schemas docs tools` returns
  five hits, needing no filter at that frame: a link, two test references, the
  `schemas/README.md` row that records the absence, and the file's own `$id`.
  The key takes the sha because ADR-0032 added four hits of its own after this
  frame, none of them a reader.
- **Five tests hold a property of `tool-context.schema.json`**, not the one
  `schemas/README.md`'s row names: four collected node ids over `test_schemas.py`'s
  parametrized suites (`--collect-only -q | grep -i "tool.context"`, key in
  *Context*), plus `::test_project_id_is_required_on_every_tool_call`, which
  loads the path as a literal at `test_schemas.py:294` and so names it in no node
  id. Exactly one of the five —
  `::test_object_schemas_reject_unknown_properties[tool-context.schema.json]` —
  moves with decision 1's composition.
- `_PROJECT_ID_FACES` (`tests/unit/test_schemas.py:779-788`) holds **5**
  published `projectId` pattern faces, and **7** tools are registered
  (`grep -c '@_tool(' .../mcp/tools.py`), which is the multiplier that prices
  decision 1's inline alternative at twelve faces rather than six.
- The SDK's argument model sets no `extra="forbid"`:
  `grep -rho 'extra="forbid"' .venv/lib/python3.13/site-packages/mcp_types/ | wc -l`
  answers **0** over the whole package, and `ArgModelBase`'s own config is
  `ConfigDict(arbitrary_types_allowed=True)`
  (`mcp/server/mcpserver/utilities/func_metadata.py`).
- **No SDK-derived tool schema sets `additionalProperties`** — measured over the
  built server, **7 of 7** absent, which is why decision 6's equivalence
  relation excludes the closure axis.
- `build_server` passes no middleware (`daemon/runner.py`, the `MCPServer(...)`
  construction), and the seat it would occupy is *inside* the SDK's own two —
  `OpenTelemetryMiddleware` then `RequestStateBoundary`, appended by
  `Server.__init__` and `MCPServer.__init__` before the caller's list is
  extended in.
- **Only one `$ref` composition admits a valid document and rejects an unknown
  key**, and it requires the referent's own closure to move:
  `allOf: [{"$ref": tool-context}]` with `unevaluatedProperties: false`. The
  table in decision 1 records all three forms driven against
  `jsonschema==4.26.0`, both with the referent closed and with it open.
- The core's runtime dependency list is **6** packages and already includes
  `jsonschema==4.26.0` and `referencing==0.37.0`
  (`packages/theurian-core/pyproject.toml`).

Landed in Phase B slice B2
([PR #663](https://github.com/theurian/theurian/pull/663)), each item with the
test that discharges it:

- **One published input schema per registered tool**, and the population is
  derived rather than listed.
  `tests/integration/test_input_validation_dispatch.py::test_every_registered_tool_resolves_to_a_published_input_schema`
  walks the **built** server's tool manager and asserts set equality in *both*
  directions — a registered tool with no loaded schema fails, and so does a
  schema published for a tool this build no longer registers, which would be a
  wire contract naming a call that answers "no such tool" (`PROCESS_SPAWN_SITES`'
  rule). `::test_every_published_schema_names_the_file_it_came_from` holds the
  origin map beside it, so a failure says which file.
- **The fail-closed dispatch refusal (decision 5).**
  `::test_a_tool_with_no_published_schema_is_refused_at_dispatch` registers a
  tool through `_tool` with no published schema on a real built server and drives
  a real `tools/call` at it. The assertion is that the **handler did not run** —
  a sentinel list the body appends to stays empty — because a refusal assertion
  alone passes against a build that refuses *after* dispatching, and a guard no
  data reaches survives its own deletion.
  `::test_an_ordinary_tool_is_still_served_on_that_same_server` is the positive
  control the decision asked for, and
  `::test_the_unpublished_tool_name_is_one_no_build_artifact_claims` pins the
  premise that the name is one nothing else registers or publishes.
- **Each per-tool schema's `unevaluatedProperties: false` is enforced on the
  wire, and against the SDK's own drop.**
  `tests/integration/test_input_validation_wire.py::test_an_unknown_key_is_refused_and_the_refusal_names_the_key_not_the_value`
  drives a real `tools/call` — initialize, the session id,
  `notifications/initialized` — because `server.call_tool` is the SDK's tool
  dispatcher and never reaches this tier.
  `::test_the_same_call_is_served_when_the_middleware_is_lifted_off` is the
  second half this ADR required, the one that makes the seat load-bearing rather
  than stylistic: with this one middleware removed, the same call carrying an
  unknown key is served at `isError: false` and answers byte-identically with the
  key and without it.
  `::test_a_valid_call_is_served_unchanged_through_the_middleware` is the served
  control, and `::test_a_refusal_carries_the_shape_a_handler_refusal_carries`
  holds that a caller cannot tell which tier refused it from the shape of the
  answer.
- **Refusal messages are bounded and do not echo unbounded caller input
  (decision 4).** `tests/unit/test_input_validation.py` drives a
  ten-thousand-character unknown key and a ten-thousand-character tool name to
  short messages; `::test_a_refused_value_is_never_echoed_back` and the two
  control-character arms hold that no caller-written value reaches a message raw;
  and `::test_a_refusal_past_the_ceiling_cannot_be_built` with
  `::test_every_refusal_template_fits_the_ceiling` recompute the ceiling from the
  live templates and the fragment cap, so a builder that grows a new unbounded
  fragment raises at construction instead of shipping an amplifier. Over the
  wire, `test_input_validation_dispatch.py::test_the_widest_body_this_transport_admits_is_refused_without_echoing_it`.
- **The published schema and the SDK-derived schema agree (decision 6).**
  `tests/integration/test_input_schema_agreement.py` states the relation in its
  module docstring, with the exclusions this ADR required it to name: the closure
  axis — **recomputed** by `::test_no_derived_schema_carries_a_closure_keyword`
  rather than cited from the measurement above — value-domain tightening, and
  `snapshotId`/`agentId`/`taskId`, which the shared context publishes and no
  handler reads. `::test_no_published_type_admits_what_the_handler_refuses`,
  `::test_the_published_and_derived_schemas_agree_about_what_is_required` and
  `::test_the_published_schema_names_no_key_the_handler_has_no_parameter_for`
  are the direction the decision said must be caught, and both positive controls
  were driven: dropping `itemId` from `knowledge-get-input.schema.json`'s
  `required` reddens the requiredness arm in this ADR's own words, and giving
  `project_list` an unpublished parameter reddens the other direction.
  `::test_the_parametrization_covers_the_tools_the_fixture_serves` pins the
  premise that the parametrization covers the registered set.
- **Every published input-side `maxLength` is the live constant it transcribes.**
  Not an item this ADR foresaw, and a member of decision 6's class rather than a
  new one: a hand-transcribed bound drifts from the module it was copied from, in
  whichever direction nothing checks — above the constant it publishes a value
  the handler refuses, below it it refuses at the wire what the handler would
  have served. `tests/unit/test_input_schema_bounds.py` maps each bound to the
  constant *and* to the code that enforces it, because two bounds can be equal by
  coincidence, and asserts the **population** by equality so a new unpinned bound
  fails.
- **The bounds on an untrusted document are applied at this boundary too.**
  `tests/unit/test_input_validation.py` drives each of `MAX_PARAMS_NESTING`,
  `MAX_PARAMS_NODES` and `MAX_PARAMS_RENDERED_CHARS` against a synthetic schema
  set, with `::test_the_nesting_fixture_reaches_the_depth_it_claims` pinning the
  fixture's own arithmetic, and `test_input_validation_dispatch.py` drives the
  nesting and node bounds through a real inbound `tools/call` with an
  at-the-bound positive control beside each. The rendered-character bound is the
  one that **cannot** be driven over this transport, which is recorded below
  rather than quietly skipped.
- **`tool-context.schema.json` gained a reader, and the record that said it
  should not have one moved with it.** `mcp/validation.py`'s loader reads it
  through every per-tool schema's `$ref`, and the middleware applies it on every
  project-scoped call. `schemas/README.md`'s row now names that reader, names the
  five-test population by the collection **command** rather than by a list, and
  says which one of the five moved.
- **The closure pin moved with the closure, and was rewritten rather than
  deleted.**
  `tests/unit/test_schemas.py::test_object_schemas_reject_unknown_properties`
  now holds one claim in three arms — `additionalProperties: false` on a response
  schema, `unevaluatedProperties: false` on an input schema, and
  `tool-context.schema.json` as the tree's one recorded exception, whose arm
  asserts that the delegation is *real*: that referrers exist at all, and that
  every one of them carries the keyword. The vacuity control was driven by moving
  the referent's `$id` so nothing referenced it.
- **The records that said SEC-12 does not run were rewritten in the commit that
  made them false** — `docs/security/threat-model.md`'s T-11 entry,
  `docs/roadmap.md`'s SEC-12 requirement row and its Phase B rows, and
  `schemas/README.md`'s row. **Two more than this ADR listed**, both found while
  writing them. T-11's *Controls* paragraph itself asserted that `projectId` "is
  *not* validated by a JSON schema at the MCP boundary — there is no such
  validation, `jsonschema` is imported only by the migration loader", which the
  middleware falsifies on both clauses. And `docs/protocol/mcp-tools.md` — the
  wire contract a client author reads — described no input validation at all;
  it now publishes the per-tool schema table, the refusal semantics and the three
  caller-observable behaviour changes. Whether any of these rewrites is
  *faithful* is a reading and no mechanical check reaches it, which is said here,
  as it was before, rather than left to be inferred.

Still owed, with the milestone that will satisfy it:

- **The per-tool schemas carry the value-domain constraints
  [ADR-0032](0032-the-write-intent-mcp-tool-surface.md) decision 3's table
  assigns them** — an explicit `maxLength` on `body`, the wire equivalent of the
  `MAX_SOURCE_FILE_BYTES` cap that `_read_body` applies to a body *file* and that
  nothing applies on a path with no file, and `uniqueItems` on `labels[]`. B2
  published one schema for each of the **seven** tools this build registers
  (`grep -c '@_tool(' packages/theurian-core/src/theurian/mcp/tools.py` → 7,
  `ls schemas/mcp/*-input.schema.json | wc -l` → 7, 2026-09-13), and not one of
  them takes a `body` or a `labels[]`: those fields arrive with the write-intent
  tools, so the constraints and their driving cases are both owed at **slice
  B4**. What B2 owed here and delivered is the mechanism that will carry them,
  and the sweep that refuses a write-intent tool registered without a published
  schema at all.
- **`MAX_PARAMS_RENDERED_CHARS` is unreachable over this transport, and the two
  caps are unreconciled** —
  [#669](https://github.com/theurian/theurian/issues/669), at slice B4.
  `daemon/server.py` calls `streamable_http_app` without `max_request_body_size`,
  so the SDK's 4 MiB `DEFAULT_MAX_REQUEST_BODY_SIZE` answers `413` before any MCP
  framing exists, and the wire case for that axis is the widest body the
  transport admits instead.
  `test_input_validation_dispatch.py::test_the_rendered_character_bound_sits_above_what_the_transport_will_carry`
  pins the relationship from both live constants and drives the `413`, so the gap
  cannot widen unnoticed while that issue waits.
- **`snapshotId`, `agentId` and `taskId` are published, now enforced, and read by
  nothing** — [#665](https://github.com/theurian/theurian/issues/665), Phase B.
  They are decision 6's third exclusion, and the exclusion is held *equal* to the
  published-but-underived population by
  `test_input_schema_agreement.py::test_the_excluded_context_keys_are_still_the_unread_three`,
  so a fourth such key cannot join it by being excluded and the file goes RED
  whichever way #665 decides. A recorded deferral, not an acceptance.
- **The `protocolVersion` treatment of the three caller-observable refusals.**
  *Consequences → Negative* already records that refusing unknown keys is a
  compatibility decision; what is recorded nowhere is whether it bumps
  `theurian/v1`. `docs/protocol/mcp-tools.md`'s *Changing this contract* section
  now carries the question and the evidence it would take to settle it — the
  section's five existing exemptions each rest on a search-verified consumer
  census, and none has been taken for these three. The decision falls due with
  the release that ships SEC-12.
- **`_meta.serverInfo` is absent from a refusal this tier answers.**
  `ServerRunner._serialize` stamps it on a modern-era result from server state no
  middleware is handed, so a refusal from this seat reaches a `2026-07-28` client
  without it. Recorded on `mcp/middleware.py`'s module docstring rather than
  discovered later; nothing about the refusal changes, and a client learns the
  server's identity from the handshake. Owed a fix only if a client is found that
  reads it.
