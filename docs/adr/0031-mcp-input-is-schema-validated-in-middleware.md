# ADR-0031: MCP tool input is validated against its published schema, in middleware, before any handler runs

- Status: proposed
- Date: 2026-09-12
- Deciders: Theurian maintainers
- Requirements: SEC-12, SEC-13, SEC-17, T-12
- Situates against [ADR-0003](0003-ports-and-adapters.md) (the boundary is an
  adapter concern, not a domain one), [ADR-0013](0013-ai-writes-produce-proposals.md)
  (the control SEC-12 owes before a write-intent tool opens),
  [ADR-0014](0014-dependency-pinning-and-pre-1-0-isolation.md) (why the SDK is
  not patched), and [ADR-0032](0032-the-write-intent-mcp-tool-surface.md) (the
  surface this control is a precondition for)

**This ADR records a decision and ships no code.** No schema file, no
middleware, no registration change and no test lands with it; the diff is
confined to `docs/`. What each implementation slice owes is named in
*Compliance*.

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

**That row names one test where two hold properties of this file**, which slice
B2 needs to know because the second one moves.
`::test_object_schemas_reject_unknown_properties` is parametrized over every
schema in the tree, so `[tool-context.schema.json]` is what holds its
`additionalProperties: false` — and decision 1's composition moves that keyword.
A third, `::test_every_published_project_id_pattern_admits_exactly_what_projectid_constructs`,
holds its `projectId` pattern against `ProjectId` and does not move.

The population under the ADR's own key is **five** hits, not four — and the key
carries its frame, because ADR-0032 has since added hits of its own and a reader
running this against `HEAD` gets a different number for a reason that has
nothing to do with SEC-12:

```console
$ git grep -n "tool-context" be977ea7 -- packages schemas docs tools \
    | grep -v ':docs/adr/0031' | grep -v ':docs/work-logs/'
docs/protocol/mcp-tools.md:50            the link
packages/theurian-core/tests/unit/test_schemas.py:294    the literal-document test
packages/theurian-core/tests/unit/test_schemas.py:781    the ProjectId-pattern face
schemas/README.md:98                     the row that records the absence
schemas/mcp/tool-context.schema.json:3   the schema's own `$id`
```

The fifth is the file naming itself. **The conclusion is unchanged and is the
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
context four keys at a time. It costs a fifth published copy of the `projectId`
pattern — `_PROJECT_ID_FACES` already tracks five — and the duplication is what
`$ref` exists to avoid, so it is the fallback rather than the plan.

### 2. The validation seat is an SDK `ServerMiddleware`, wired where the server is built

The check runs in a `ServerMiddleware` registered on the `MCPServer` that
`daemon/runner.py:build_server` constructs. It reads `ctx.method` and
`ctx.params`, and for a `tools/call` it resolves the tool name and validates
`params.arguments` against that tool's published input schema **before**
`call_next(ctx)` is awaited.

Two properties follow from the measurements above, and neither is available at
any other tier:

1. **`additionalProperties: false` is enforceable.** The middleware sees the
   keys the caller actually sent, because the SDK has not yet built the model
   that would drop them.
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

Every published input schema sets `additionalProperties: false`, and a request
carrying a key the schema does not name is refused rather than trimmed. The
reason is the one the domain-construction control cannot reach: a key the server
silently drops is a caller believing it asked for something it did not get, and
on a write-intent surface (ADR-0032) that difference is the difference between
a proposal a human reviews and a proposal a human reviews *without the
constraint the agent thought it had set*.

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

```console
$ for each of the 7 registered tools: inputSchema.get("additionalProperties")
  knowledge.search       <absent>     review.findings      <absent>
  knowledge.get          <absent>     review.search        <absent>
  knowledge.status       <absent>     system.capabilities  <absent>
  project.list           <absent>
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
  composition moves `additionalProperties: false` off
  `tool-context.schema.json` and onto each per-tool closure, which reddens a pin
  that has held since the file was written. The closure is not weakened — it is
  enforced once per tool instead of once in the referent — but for the length of
  slice B2's commit the file that types every call's context does not close
  itself, and an implementer who moved the keyword and did not rewrite the pin
  would read a red test as noise.
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
| **Validate inside the `_tool` registration seam** | The seam runs after the SDK has built its argument model, and that model drops unknown keys (`ArgModelBase` sets no `extra="forbid"`, measured above). A check there can validate the values a handler receives and can never see the keys a caller sent, so `additionalProperties: false` — the half that catches a client asking for something this build does not implement — is unenforceable from it. |
| **A Starlette middleware in `daemon/server.py`** | It sits below the MCP framing, so it would have to re-parse JSON-RPC envelopes and Streamable-HTTP batching to find `params.arguments`. That is a second implementation of the SDK's own dispatch, and it would be wrong in a way nothing tests the day the transport changes. The SDK middleware tier is handed the parsed method and params by contract. |
| **Patch or fork the SDK's request models to `extra="forbid"`** | Two costs. The dependency is exact-pinned by [ADR-0014](0014-dependency-pinning-and-pre-1-0-isolation.md), so a patched model is a fork to carry across every bump. And it answers the wrong requirement: `forbid` rejects keys the *handler signature* does not name, while SEC-12's own text demands validation "against its published JSON Schema" — a stricter and different statement, since a published schema constrains value ranges, enums and formats that a Python annotation does not. |
| **Generate the published schema from the handler signature at build time** | It removes the drift of decision 6 by removing one of the two descriptions, which sounds strictly better and is not: the published artifact would then be a projection of the implementation rather than a contract the implementation is held to, so every accidental widening of a parameter type would publish itself as an intentional contract change. The contract is supposed to be the thing that does not move by accident. |
| **Validate only the write-intent tools, since those are the new surface** | The control would then apply to the tools someone remembered, which is the failure decision 5 exists to prevent. It also gets the risk backwards on the read side: `knowledge.search` and `review.search` take caller text today and are the surfaces a malformed request reaches first. |

## Compliance

**This ADR ships no behaviour, so it has no shipped test to name.** Its
enforcement at design time is the measurements it cites; its enforcement at
implementation time is the tests slice B2 owes. The names below are the
properties an implementation must pin, not files that exist today — the same
honest split [ADR-0030](0030-github-review-ingestion-spawns-gh.md) states for
the same reason.

Measured now, and reproducible from this ADR (2026-09-12, `be977ea7`):

- `schemas/mcp/` holds **8** files (`ls schemas/mcp/`): seven response or
  response-fragment schemas and one input-side contract,
  `tool-context.schema.json`. **No per-tool input schema exists**, and the one
  input schema that does has **no reader under `src/`** —
  `git grep -n "tool-context" be977ea7 -- packages schemas docs tools` returns
  five hits outside this ADR: a link, two test references, the
  `schemas/README.md` row that records the absence, and the file's own `$id`.
  The key takes the sha because ADR-0032 added four hits of its own after this
  frame, none of them a reader.
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

Still owed, with the milestone that will satisfy it:

- **Slice B2 — one published input schema per registered tool.** The owed
  property is a *derived* population, not a listed one: a test enumerates the
  **built** server's registered tools and asserts each resolves to a loaded
  schema, so a tool added later joins the sweep by existing. The shape to
  follow is
  `tests/integration/test_mcp_tools.py::test_no_registered_tool_can_reach_a_canonical_write`,
  which walks the built object graph rather than a directory of source files.
- **Slice B2 — the fail-closed dispatch refusal (decision 5).** Owed a driving
  test that registers a tool with no schema and asserts the call is refused,
  with a positive control that an ordinary tool with its schema is served — a
  refusal test with no served counterpart passes for a server that refuses
  everything.
- **Slice B2 — `additionalProperties: false` is enforced on the wire, not in
  the handler.** Owed a test driven through a real `tools/call` carrying an
  unknown key, asserting the refusal — and asserting it against the *SDK's own
  drop*, which is what makes the middleware seat load-bearing rather than
  stylistic. Without that second half the test would pass on a build whose
  handler merely ignored the key, which is today's behaviour.
- **Slice B2 — refusal messages are bounded and do not echo unbounded caller
  input (decision 4).** Owed a test that a key or value past the recorded bound
  is reported by length or by key path and never reproduced, in the shape
  `mcp/tools.py`'s `MAX_PROJECT_ID_CHARS` echo-bounding already uses.
- **Slice B2 — the published schema and the SDK-derived schema agree
  (decision 6).** Owed a test that recomputes the agreement from both live
  sides, plus the control that proves it can fail. The equivalence relation is
  stated in the test module's docstring, because a relation nobody wrote down is
  one a later contributor will loosen to make a failure go away — and the
  statement has to name what it **excludes**: the closure axis, which the
  derived schema never carries (7 of 7 absent, measured), and value-domain
  tightening, which is the published schema's purpose. What it must catch is a
  published schema **permitting** what the handler refuses, and its positive
  control is exactly that mutation.
- **Slice B2 — the bounds on an untrusted document are applied at this boundary
  too.** `validate_migration_document`'s nesting, node and rendered-character
  caps exist because unbounded documents cost unbounded work in `jsonschema`'s
  message building (#291, #245). Owed a test that the MCP boundary refuses a
  document past each bound rather than paying for it.
- **Slice B2 — `tool-context.schema.json` gains a reader, and the record that
  says it should not have one moves with it.** `schemas/README.md`'s row reads
  "nothing, and nothing should: it describes tool *input*, so there is no
  response to compare", which is true of a *response* check and false of this
  one. Owed: the row rewritten to name what now reads the schema, in the same
  commit — and to name **both** tests that hold properties of the file, since it
  names one today and
  `::test_object_schemas_reject_unknown_properties[tool-context.schema.json]`
  is the one decision 1's composition moves.
- **Slice B2 — the closure pin moves with the closure, and is rewritten rather
  than deleted.** Moving `additionalProperties: false` off
  `tool-context.schema.json` takes
  `tests/unit/test_schemas.py::test_object_schemas_reject_unknown_properties[tool-context.schema.json]`
  RED. Owed: that test rewritten so it still holds a closure claim over the
  context fields — asserting that every per-tool schema referencing the context
  sets `unevaluatedProperties: false` — with the control that a per-tool schema
  missing it is caught. A pin deleted because a keyword moved is a pin deleted.
- **Slice B2 — the three records that say SEC-12 does not run are rewritten in
  the commit that makes them false**: `docs/security/threat-model.md`'s *Future
  controls, not shipped* entry, `docs/roadmap.md`'s SEC-12 requirement row
  (`nothing` / `the whole control`), and the `schemas/README.md` row above. Not
  a later documentation pass — an on-main claim must not call a control
  unimplemented while it runs. Whether the rewrite is *faithful* is a reading
  and no mechanical check reaches it, which is said here rather than left to be
  inferred.
