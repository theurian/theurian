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
  wire, `test_input_validation_dispatch.py::test_the_widest_body_the_schema_tier_can_see_is_refused_without_echoing_it`
  — renamed from `…the_widest_body_this_transport_admits…` in *Amendment 1*'s
  change, because the transport now admits bodies wider than the schema tier is
  ever handed.
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
- **The `protocolVersion` treatment of the three caller-observable refusals is
  settled and recorded.** `docs/protocol/mcp-tools.md`'s *Changing this contract*
  section, as of `76e1628c` on the branch of
  [#663](https://github.com/theurian/theurian/pull/663), grants the three the
  **sixth, seventh and eighth** breaking-but-unbumped exemptions — taking that
  series from five to eight — and `protocolVersion` stays `theurian/v1`. Two legs
  are shared by all three: a consumer census re-measured for this change and
  pasted as the commands that produce it rather than summarised — the population
  is every place outside Core and `docs/` that builds a `tools/call`, and it holds
  one construction site, a test helper, whose eight call sites are partitioned and
  listed over the four key sets this contract defines; no plugin script builds an
  MCP call at all — and the pre-1.0 versioning policy the Core changelog states in
  its own header, deliberately *not* the "no known external integration to break"
  leg the fourth and fifth exemptions rest on, since Core is published on PyPI and
  nobody here can say what is installed against it. Each refusal then carries a
  ground of its own and a scope line that stops it widening to the next: unknown
  keys were never published as accepted, `project.list` and `system.capabilities`
  never took an argument in this document or in any schema under `schemas/mcp/`,
  and `query`'s 2,000-character bound was already published in two places, so what
  moved there is the disposition of an over-bound query from clamp to refuse.

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
- **The two caps are reconciled; what stays owed under this heading is narrower
  than the reconciliation was** —
  [#669](https://github.com/theurian/theurian/issues/669), landed. This bullet
  used to read *"`MAX_PARAMS_RENDERED_CHARS` is unreachable over this transport,
  and the two caps are unreconciled"*; both clauses are false now, and
  *Amendment 1* below records what landed, what measuring it revealed, and what
  it left open. In short: `build_app` passes a derived
  `max_request_body_size` instead of taking the SDK's default, and
  `MAX_PARAMS_RENDERED_CHARS` is held by `_rendered_width`'s charge rather than
  by the two caps' ordering, so it is reachable and enforced at any transport
  cap. **Still owed at slice B4**, both narrower: the *unit* a published
  `maxLength` on a write-intent `body` counts — JSON Schema counts code points,
  while the transport cap's derivation is in landed bytes — and a pin that
  recomputes that cap's `+ 1 MiB` envelope allowance from the sibling fields'
  own published bounds, which do not exist yet
  ([#691](https://github.com/theurian/theurian/issues/691)). Two wire-escape
  classes still meet the bare `413` at a landed size the store would accept;
  that is recorded as an accepted residual on `MAX_REQUEST_BODY_BYTES` and in
  the threat model's SEC-12 entry, not owed to a milestone.
- **`snapshotId`, `agentId` and `taskId` are published, now enforced, and read by
  nothing** — [#665](https://github.com/theurian/theurian/issues/665), Phase B.
  They are decision 6's third exclusion, and the exclusion is held *equal* to the
  published-but-underived population by
  `test_input_schema_agreement.py::test_the_excluded_context_keys_are_still_the_unread_three`,
  so a fourth such key cannot join it by being excluded and the file goes RED
  whichever way #665 decides. A recorded deferral, not an acceptance.
- **`_meta.serverInfo` is absent from a refusal this tier answers.**
  `ServerRunner._serialize` stamps it on a modern-era result from server state no
  middleware is handed, so a refusal from this seat reaches a `2026-07-28` client
  without it. Recorded on `mcp/middleware.py`'s module docstring rather than
  discovered later; nothing about the refusal changes, and a client learns the
  server's identity from the handshake. Owed a fix only if a client is found that
  reads it.

## Amendment 1 — the transport body cap is chosen and recorded, and the render budget no longer rests on it (2026-09-14, #669, PR #685)

> **This is an append-only amendment. The decisions above are unchanged.** What
> it corrects is a *Compliance* item: the second owed bullet, which said
> `MAX_PARAMS_RENDERED_CHARS` is unreachable over this transport and the two
> caps are unreconciled. Both clauses are false as of this change; the bullet now
> points here, and the test cite it carried named a test this change deleted.
>
> **Where the figures below come from**, because they no longer share one
> anchor and an earlier draft of this block said they did:
>
> * **Measured 2026-09-14 at `172e9d28`, a branch commit** — the cap derivation,
>   the wire-escape table, the `413` refusal's own size.
> * **Measured 2026-09-15 at `5f96c781`, a branch commit** — the per-request
>   memory table, the per-node punctuation cost, the fallback's transient and its
>   timings. These are round two's re-measurements; the addendum at the end of
>   this amendment records what they replaced and why.
> * **Measured 2026-09-15 at `097d754c`, a branch commit** — the transport and
>   parse terms, taken on the call this request path actually parses with. No row
>   of the table moved; only the model that has to explain it.
> * **Measured 2026-09-15 at `286b3d2a`, a branch commit** — round three's:
>   `jsonschema`'s message-construction term and the family-(ii) worst instances,
>   the maximum-over-moments composition, and the chunked transient's second
>   factor. These replaced a model that was missing a term and a ceiling that was
>   missing a factor; the second addendum at the end says what each was.
> * **Derived from the live constants rather than measured**, and said so where
>   it happens: the composed render ceiling, and the worst render the pre-#669
>   charge admitted at a given cap.
>
> Every commit named above is a branch commit of this pull request and not on
> `main` until it lands — which is why each anchor carries the pull-request
> qualifier rather than reading as a tree a reader can check out.
> One figure is older than either, and the sentence quoting it says so: the
> 53,476,811-character reproduction was taken against the interim `2 *` cap this
> amendment withdrew, not against the cap it ships.
> CPython 3.13, `mcp==2.1.1`, `jsonschema==4.26.0`.

**What the record said, and what is true now.** `build_app` called
`streamable_http_app` without `max_request_body_size`, so the SDK's
`DEFAULT_MAX_REQUEST_BODY_SIZE` — 4 MiB, `mcp/server/transport_security.py` —
was the first bound an inbound body met: a number this project never chose and
never recorded. It now passes `daemon/server.py`'s `MAX_REQUEST_BODY_BYTES`,
**26,214,400 bytes**, derived as `3 * MAX_SOURCE_FILE_BYTES + 1 MiB` from the
byte cap ADR-0032 decision 3 puts on the file a proposal lands. That sits above
`MAX_PARAMS_RENDERED_CHARS` (12 MiB), so a body between the two arrives, is
framed, and meets this seam's bounded refusal — which names the tool and the
limit it passed — instead of a bare `413 Request body too large` (22 bytes,
measured) from a tier that exists before any MCP framing does. The formula is
recomputed from both live constants by
`test_input_validation_dispatch.py::test_the_transport_body_cap_is_derived_from_the_cap_on_a_landed_file`,
the ordering by `::test_the_transport_body_cap_sits_above_the_rendered_character_bound`,
and the tier boundary is driven at the exact byte on both sides by
`::test_a_body_of_exactly_the_transport_cap_still_reaches_mcp_framing` and
`::test_a_body_one_byte_past_the_transport_cap_is_refused_without_echoing_it`.

**The multiplier is an enumeration, and that is what makes it an authority.**
The first draft of this change sized the cap at `2 *`, from a sample of
encodings judged realistic — quote, backslash, newline, tab, CJK — every one of
which measures 2.0x and none of which is the worst case. The ratio is a property
of a character's UTF-8 length, not of how ordinary its script is: under
`ensure_ascii`, every 2-byte character (Latin supplements, Greek, Cyrillic,
Hebrew, Arabic) costs a 6-byte `\uXXXX` over 2 landed bytes, **3.0x**, and a
Cyrillic body landing under `MAX_SOURCE_FILE_BYTES` met the bare `413` *inside
the change that was closing this issue*. The constant now records one row per
UTF-8 byte length, under both encoder families — `ensure_ascii=True`, the
stdlib default, and raw UTF-8, which the official Python and JS clients emit —
and `tests/unit/test_transport_body_cap.py` is what makes that table
falsifiable: `::test_the_multiplier_is_the_worst_wire_ratio_any_non_control_class_reaches`
recovers the multiplier from the live constant by arithmetic and compares it to
the measured maximum, and
`::test_the_classes_that_exceed_the_multiplier_are_exactly_the_two_the_residual_names`
holds the residual set by equality, so a third class rising past the multiplier
fails too. Over the wire the acceptance is parametrized per script class —
`test_input_validation_dispatch.py::test_a_write_intent_sized_body_arrives_and_is_refused_by_its_schema`
posts a body landing at `MAX_SOURCE_FILE_BYTES` in ASCII, 2-byte, 3-byte and
astral text, `ensure_ascii`-escaped, and asserts each is answered by its schema
rather than by a `413`.

**The render budget is held by the charge, not by the caps' ordering — and the
premise that said otherwise is withdrawn.** `MAX_PARAMS_RENDERED_CHARS` was
written under *"a request's rendered width never exceeds the bytes the caller
sent"*, and `_rendered_width` charged a string `len(value)` accordingly, while
`jsonschema` renders a failing instance with `{instance!r}` and `repr` escapes.
The premise is false in both directions and was false before this change: raw
U+007F is **one** wire byte and **four** rendered characters, and JSON admits it
raw, so even under the SDK's 4 MiB default the worst reachable render was
16,777,216 characters — 1.33x the 12 MiB budget. Raising the cap widened that
breach rather than creating it, and the worst instance at **this** cap is **any
raw non-printable 2-byte BMP character**, U+0600 among them: two wire bytes, a
six-character `\uXXXX` render, so under the old code-point charge the gate
admitted 12,582,912 of them — 25,165,824 wire bytes, inside this cap with the
`+ 1 MiB` envelope allowance to spare, sent raw as the official clients send it
— and `jsonschema` would render **75,497,472 characters, 6.00x the budget**,
which is **4.50x** the worst the SDK's own default could reach. That is the
worst over every escape class and not over one body someone tried, and it is the
2-byte class because that class alone saturates the charge without meeting the
transport first: raw U+007F is charge-bound too but renders only four characters
per code point (4.00x), and a non-printable astral character renders ten but is
transport-bound at 6,553,600 of them (5.21x). Derived from the two constants and
swept on CPython 3.13 over the 1,112,064 code points UTF-8 can carry — every one
but the 2,048 surrogates, which no encoder will put on the wire — rather than
argued from the three named here.

The reproduction behind those figures was run on the same character class at the
interim `2 *` cap (**17,825,792 bytes**) this amendment withdrew, where the
transport bound first instead of the charge: a body of U+0600 passed the gate
charged about 8.9 M and made `jsonschema` build a measured
**53,476,811-character** message, **4.25x the budget** — that pair belongs to
the interim cap, and the shipped cap's pair is the one above. An earlier draft
of this paragraph printed the interim figures under the shipped cap's name.
Either way the refusal eventually produced stayed bounded at 341 bytes and
echoed nothing — the transient was the cost, not the answer.

`_rendered_width` now charges **every leaf at least the number of characters
that leaf contributes to the render**: a string through an `isprintable()` fast
path that allocates nothing, through `len(repr(value)) - 2` up to
`_CHUNK_CODE_POINTS` and a chunked upper bound above it; an integer by its digit
count; and every remaining leaf type — `float` and `None`, which an earlier
draft charged zero, among them — by its whole `repr`. The escape classes under
that charge are verified exhaustively over all 1,114,112 code points rather than
argued. So the budget is enforced by the charge at **any** transport cap, and
the ordering above now decides only *which* refusal a caller between the two
bounds receives. Pinned per escape class by
`tests/unit/test_rendered_width_charge.py` and driven over the wire by
`test_input_validation_dispatch.py::test_escape_heavy_text_smaller_than_the_render_budget_still_exceeds_it`,
whose body is smaller in bytes than the render budget itself — so no ordering of
the two caps could have refused it.

What that budget bounds is the **charged leaves**, not the whole render. An
instance is its leaves plus the punctuation holding them together — braces,
brackets, `, `, `: `, the quotes around each string — which no leaf is charged
for, because `_iter_nodes` descends into containers and charges their members
instead, and a container is the one deliberate zero. That excess is a fixed cost
per node, measured at no more than **4 characters per node** over both the
structured worst cases and 300,000 random shapes, so the render a request can
actually reach is `MAX_PARAMS_RENDERED_CHARS + MAX_PARAMS_NODES * 4` =
**12,982,912 characters**, 1.032x the constant alone. Quote the composed figure
wherever the real ceiling matters; the constant alone under-states it.

The withdrawn sentence survives in the tree only where something refutes it:
the two constant docstrings
(`daemon/server.py`'s `MAX_REQUEST_BODY_BYTES` and `mcp/validation.py`'s
`MAX_PARAMS_RENDERED_CHARS`), the two test modules that drive its falseness, and
this amendment. No record states it as fact.

**Three caller-visible behaviour changes**, each named here because a client
author reads the refusal, not the constant. A body up to 26,214,400 bytes is now
read where 4 MiB was the limit, so the write-intent body ADR-0032 sizes for
arrives and is answered by its schema — naming the tool and the constraint —
where a 2-byte-script body of the same landed size previously met a `413` that
named nothing. An escape-heavy body that previously reached a published
`maxLength` refusal can now meet the rendered-character refusal first: the same
`isError`, a different limit and a different message. And arguments whose whole
render passes the composed ceiling are now refused at this seam where they were
*admitted*: with `float` and `None` charged nothing, a request pairing a string
at the budget with 99,995 full-precision floats rendered 15,182,796 characters
and was let through. It now receives the seam's bounded refusal.

**The residual is recorded, not closed, and it is two rows of the wire table.**
Only the control classes exceed 3.0x, both at 6.0x: C0 characters other than
`\b` `\t` `\n` `\f` `\r`, which have **no** remedy because raw C0 is illegal
JSON, and DEL (U+007F), whose remedy is to send it raw rather than
`ensure_ascii`-escaped (1.0x). Text dense in either still meets the bare `413`
at a landed size the store would accept. Accepted rather than closed: that
density is not what a knowledge store lands, and some residual is inherent to
any finite byte bound at a tier with no MCP framing to refuse through. Astral
characters are **not** in that set — they expand at 3.0x and the cap covers
them.

**What the bound costs per request, and what still bounds nothing.** The SDK
buffers a whole body before anything parses it, so a multiple of the wire bytes
is live in Python heap while one at-cap request is in flight — and that multiple
is **neither a single multiple of the wire bytes nor a function of the body
alone.** Up to three terms are live, and *which* of them exist depends on the
path the request takes rather than on how big it is:

- **The transport's buffers — 2x the wire bytes, whatever the body holds.**
  `RequestBodyLimitMiddleware` accumulates the body into a `bytearray` and makes
  the `bytes` copy itself; Starlette's `request.body()` joins that single chunk
  and hands back the very same object rather than copying again.
- **The parse's own peak — 1x, 3x or 5x, set by the widest code point, over
  single-large-leaf bodies.** The SDK parses with
  `pydantic_core.from_json(body)` (`streamable_http.py`), straight from the
  bytes, and PEP 393 sizes the resulting `str` by its widest member — 1, 2 or 4
  bytes per code point — so the finished string alone is 1x, 2x or 4x, and above
  the 1-byte kind the parse holds one further wire-byte-sized buffer while it
  widens. **There is one string, not two**: nothing downstream copies it, and
  `jsonrpc_message_adapter.validate_python` peaks at 0.0 MiB and hands back the
  very same object, checked by identity. **Those multiples are measured over
  single-large-leaf bodies**, the shape this cap is sized for; a body of many
  small values instead pays CPython's per-object overhead, which the ratio does
  not include — ~46 bytes a value, taking 98,900 distinct short strings to 4.87x
  their wire bytes where one leaf of the same size parses at 1.00x. That excess
  is bounded *absolutely* rather than by the body: `MAX_PARAMS_NODES` caps it at
  a few MiB.
- **`jsonschema`'s message construction — on refusal paths only.** *Most*
  keywords build their message with `{instance!r}` — including every constraint
  the published schemas apply to a string field that a large value can fail
  (`maxLength`, `minLength`, `pattern`), and `type` and `enum` besides. Measured
  on `jsonschema==4.26.0`, `required`, `const` and `maximum` name no instance
  value at all, and the two in `_KEYWORDS_THAT_NAME_KEYS` name only a key; an
  earlier draft of this bullet said every keyword but those two rendered the
  instance, which over-states the set. A request that *passes* the charge gate
  and then fails a rendering keyword makes `iter_errors` build the instance a
  second time, bounded by `2 × MAX_PARAMS_RENDERED_CHARS × 4` bytes, **~96 MiB
  isolated**, and uncorrelated with the size of the request that triggers it.
  **That string sits at the width of the `repr` *output*, which is not always
  the parsed string's**: `repr` escapes every non-printable code point to ASCII,
  so only a code point that survives it raw can widen the result. The render
  strings take the parsed kind when the widest code point is *printable*, and 1
  byte per character when every wide one is escaped — the rule is exact, the
  output's kind being the kind of the widest *printable* code point, checked over
  200,000 mixed strings with no exception.

**The peak is a maximum over moments, not a sum**, because the parse's transient
buffers are freed before `jsonschema` renders anything, so the two never stand
together:

```text
peak = max(2*wire + parse_peak,                          # the parse moment
           2*wire + code_points*kind + 2*rendered*kind)  # the render moment
```

where `kind` is the *parsed* string's bytes per code point. **Read it as an upper
bound, not as a prediction.** It is tight — within **+0.12x**, and always above,
by the request's fixed overhead — for the five single-large-leaf shapes
`test_request_memory_model.py` pins, whose widest code point is printable or
1-byte. Outside that set it over-predicts, because the render moment's `kind` is
the parsed string's while the strings it prices sit at the `repr` output's: a
body of non-printable astral characters measures 8.11x where the expression says
23.00x, and one of non-printable U+0600 measures 9.11x against 15.00x. A DEL-only
body is unaffected, its parsed kind already being 1. Swept over the code point
space the expression **never under-predicts** — worst over-prediction −14.88x —
which is why it is recorded as the bound this daemon can be held to rather than
as a figure to expect.

Three path families follow, and they are the honest unit of this record.
**(i) Charge-refused shapes** meet `_unbounded` before `iter_errors` is ever
called, so only the parse moment exists. One authenticated at-cap POST per row,
one fresh process each:

| Body at 26,214,400 wire bytes | `tracemalloc` peak | vs wire bytes | `ru_maxrss` |
| :-- | --: | --: | --: |
| all ASCII | 75.1 MiB | 3.00x | ~50 MiB |
| dense U+007F | 75.2 MiB | 3.01x | ~50 MiB |
| U+007F + one 2-byte character | 125.1 MiB | 5.00x | ~25 MiB |
| **U+007F + one astral character** | **175.1 MiB** | **7.00x** | ~75 MiB |

**The parse moment reproduces every one of those rows**: 2x + 1x = 3.00x for
both 1-byte-kind bodies, 2x + 3x = 5.00x with a 2-byte character, 2x + 5x =
7.00x with an astral one, the dense-U+007F row's extra 0.01x being the charge's
own chunked transient. Holding on all four is what makes it a model rather than
an arithmetic that fits one row.

**(ii) `jsonschema`-answered shapes** carry both moments, and whichever is larger
wins. A body of *printable* multi-byte text is charged one character per code
point, so it passes the gate the family-(i) rows meet and reaches `iter_errors`.
Round-3 measurements, `tracemalloc`, one authenticated POST each:

| Worst by | Peak | The instance that reaches it |
| :-- | :-- | :-- |
| ratio | **114.1 MiB = 38.04x** the wire bytes | `"\x7f" * 3,145,717` plus one U+1F600 — only **3,145,848 wire bytes**, charged 12,582,869, just under the budget and therefore admitted. Its render moment is 38.00x against a 7.00x parse moment, so the render decides it |
| absolute | **~194–200 MiB, up to 8.00x** | CJK filler plus ASCII plus one astral character, at the cap, saturating the wire bound and the render budget at once |

**(iii) Valid shapes** render nothing, so family (i)'s composition applies with no
second moment.

**The moments are not added.** Presenting the three terms additively over-states
a printable 2-byte body by 1.88x the wire bytes — 7.00x predicted against 5.12x
measured — by charging it for buffers already freed; and the `max` is
load-bearing rather than a formality, since printable CJK peaks at its *parse*
moment, 5.00x against a 4.00x render moment. Different shapes' peaks are decided
by different terms, and neither term alone is the model.

**The worst of family (i) is 7.00x, and 3.00x is the ASCII row** — an earlier
draft of this paragraph recorded the ASCII row as though it bounded every body,
and a later one recorded family (i)'s 7.00x the same way. Both family-(ii) rows
exceed 175.1 MiB, and the ratio-worst exceeds every ratio in that table
five-fold at an eighth of the size. The two columns are named because they
answer different questions and disagree by
design: `tracemalloc` is the Python heap and reproduces to the tenth of a MiB
across runs, while `ru_maxrss` is a process high-water mark that moves with
whatever the process already touched, which is why it is quoted only to the MiB
and why it is **not monotone in the row order** — the 2-byte row reads lower
than the ASCII row there while `tracemalloc` reads half again higher. Read the
`tracemalloc` column for what a request costs; `ru_maxrss` answers what the
process peaked at, which is not the same question.

Two sentences earlier drafts of this paragraph carried are **withdrawn**, and
family (ii) is the counterexample to each: *"the multiple is set by the body's
widest code point, not by its length"*, and *"all four rows are those two terms
and nothing else"*. The charge's own fallback is a separate cost again: it once
reprred a whole leaf, peaking at 100 MiB (dense U+007F) to 400 MiB (the same leaf
with one emoji), `tracemalloc`, and `_chunked_width` bounded it to
`_CHUNK_CODE_POINTS * (10 + 1) * 4` — **~352 KiB**, two terms because the same
expression builds the repr output *and* the concatenated slice it reprs, measured
across three readings as **360,548 to 361,156 bytes**, worst over a leaf of
non-printable astral characters carrying one printable astral. An earlier record
priced only the repr output and called it 320 KB.

The derivation also couples this daemon's per-request memory ceiling to a
*filesystem* constant — raising `MAX_SOURCE_FILE_BYTES` for a reason about files
raises every row above by three times as much, and then by up to four times that
again for a body carrying one astral character — which is recorded on
`MAX_REQUEST_BODY_BYTES` because nothing at the `security/paths.py` end says so.
The *number* of concurrent arrivals is bounded
nowhere in-process; that is T-6's recorded deferral, and the figures are on
[#26](https://github.com/theurian/theurian/issues/26#issuecomment-5661638879).

**Two homes for what this hands on.** The unit question and the envelope pin
both fall to slice B4 ([#691](https://github.com/theurian/theurian/issues/691)).
A `maxLength` transcribed from an 8 MiB byte cap admits up to four times the bytes
it names, because JSON Schema counts code points, and the `+ 1 MiB` addend is an
allowance inside a hard total rather than a derived figure — nothing could
derive it until `description`, `evidence.*`, `sourceAnchors[]`, `labels[]` and
`scopePaths[]` carry published bounds. Separately, the same under-charge exists
in `infrastructure/filesystem/migration_loader.py`'s own `_rendered_width`,
whose docstring states a lower bound as intended behaviour
([#693](https://github.com/theurian/theurian/issues/693)) — box-split so this
class is closed across both seats rather than at the one this change touched.
Finally, the stale test cite corrected above was found by review rather than by
the suite: nothing checks that a test name an ADR cites still exists
([#692](https://github.com/theurian/theurian/issues/692)).

**Addendum, 2026-09-15 — round two: three figures were measurements over a
favourable instance, written down as bounds.** Appended rather than folded into
the text above, because the class is worth more than the corrected numbers. Each
face was a real measurement, recorded without the instance it was taken over, so
each read as universal and each was smaller than the truth. The faces, as
[PR #685](https://github.com/theurian/theurian/pull/685)'s round two found them:

1. **Per-request memory, recorded as "roughly 3.0x the wire bytes, 75.1 MiB".**
   That is the all-ASCII row. PEP 393 sizes a `str` by its widest member, so one
   astral character anywhere in the body takes the same request to **7.00x,
   175.1 MiB** — the worst of the four rows measured at this cap, and the row
   the table above bolds.
2. **The render charge, recorded as charging "every leaf what `repr` renders it
   as".** `float` and `None` fell through to a `return 0`, so a request pairing
   a string at the budget with 99,995 full-precision floats was charged
   12,582,906 and admitted while rendering 15,182,796 characters, 1.207x the
   budget. Fixed in code rather than reworded, and with it the whole-render
   ceiling became statable: **12,982,912 characters**, not the 12,582,912 the
   constant alone names.
3. **The pre-fix reach, recorded as "53,476,811 characters, 4.25x the budget,
   widening the breach 3.2x".** Measured against the interim `2 *` cap and
   printed under the shipped `3 *` cap's name. At the cap this amendment ships
   the worst is **75,497,472 characters, 6.00x the budget, 4.50x the SDK
   default's worst** — the same character class, at the larger size the shipped
   cap admits. The reproduction was right; the cap it was filed under was not.

Every figure in this amendment now names the instance it is worst over and the
unit it is counted in, and the measurement block at the top says which anchor
each was taken at. **That is now enforced rather than merely intended**, which
it was not when this addendum was first written: this amendment's prose was read
by nothing, and the paragraph here said so. It is wired now.
`tests/integration/test_sec12_record_figures.py` recomputes the composed ceiling
from `MAX_PARAMS_RENDERED_CHARS` and `MAX_PARAMS_NODES`, recomputes the worst
render the old charge admitted, and drives `_rendered_width` over every leaf
type the records call *every leaf* — so a figure that drifts from the build goes
RED against the build rather than against a second copy of itself.
`tests/integration/test_sec12_retired_claims.py` holds the other direction:
each retired figure above — the ASCII-only memory row, the universal charge
claim, and the interim cap's three — may appear only with the attribution that
marks it retired, so none of them can quietly come back as a live one. A revert
of these corrections is no longer silent.

**Addendum, 2026-09-15 — round three: the same class, one surface further in.**
Round two's class was *a measurement over a favourable instance, written down as
a bound*. Round three found it three more times, and the worst face was produced
by the stage that was closing it:

1. **The memory model was missing the term `jsonschema` itself spends.** The
   render budget is character-denominated, so a body of *printable* multi-byte
   text is charged one character per code point, passes the charge gate, and
   reaches `iter_errors` — which renders the instance a second time at PEP 393
   width. A legal authenticated request of 3,145,848 wire bytes peaks at
   **114.1 MiB, 38.04x**, where the model then recorded predicted 7.00x. The
   model above is now three terms composed as a **maximum over moments**, with
   family (ii) and its two worst instances written out. The term had in fact been
   measured while the two-term model was being written, recorded as an aside, and
   the pin steered onto a fixture where the two-term model held — which is the
   class's own mechanism, executed by the stage closing it.
2. **The chunked transient's ceiling was one term of two.**
   `_CHUNK_CODE_POINTS * 10 * 4` priced the repr output and omitted the
   concatenated slice the same expression builds. The real ceiling is
   `(10 + 1) * 4` per code point, **~352 KiB**, measured 360,548–361,156 bytes.
3. **The retired charge universal came back in a new dress** — in the CHANGELOG's
   own `Fixed` heading, and in the docstring of the module whose job is refusing
   it. A verbatim key cannot catch a reworded sentence, so
   `test_sec12_retired_claims.py` now carries a subject-plus-phrasing key that
   does, and runs it over the two production docstrings as well as these records.

**What terminates the class is structural, not another round of faces.** No
cost or memory universal survives in a #669 record unless it is recomputed from
live constants by a pin, driven at a named worst instance by a pin, or rewritten
as an enumeration of measured rows with their instruments and instances named.
The three paragraphs above are the third form; the falsified universals are kept
as attributed retired claims rather than deleted, so a reader meets the
correction instead of a gap.
