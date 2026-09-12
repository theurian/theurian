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

**`schemas/mcp/` publishes responses and nothing else.** Its eight files are
`knowledge-search-response`, `knowledge-status-response`,
`project-list-response`, `retrieval-metadata`, `review-findings-response`,
`review-search-response`, `system-capabilities-response` and `tool-context` —
seven response or response-fragment schemas and one shared context fragment.
There is no input schema in the directory, so "its published JSON Schema" names
an artifact that does not yet exist. Writing those artifacts is half of what
SEC-12 costs; the other half is finding a seat where they can be enforced.

### Why the seat is forced, and both halves are measured

**The SDK validates arguments and silently drops the keys it does not know.**
`mcp==2.1.1` builds a per-tool pydantic model from the handler's signature
(`mcp/server/mcpserver/utilities/func_metadata.py`, `create_model(...,
__base__=ArgModelBase)`), and `ArgModelBase`'s configuration is
`ConfigDict(arbitrary_types_allowed=True)` — no `extra="forbid"`. The generated
wire models are the same shape: `mcp_types/_wire_base.py`'s `WireModel` sets
only `populate_by_name` and says in its own docstring that "subclasses set
`extra` themselves", and `extra="forbid"` appears **zero** times in
`mcp_types/_types.py`
(`grep -c 'extra="forbid"' .venv/lib/python3.13/site-packages/mcp_types/_types.py`
→ `0`). Pydantic's default for an unset `extra` is `ignore`. So an unknown key
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

Drift in either direction is a defect: a published schema looser than the
handler means a request the schema admits and the handler rejects, and a
published schema tighter than the handler means a documented capability the
server refuses.

## Consequences

### Positive

- **SEC-12 stops being owed and starts running**, and the two records that
  currently say it does not (`threat-model.md`'s *Future controls, not shipped*
  entry and `roadmap.md`'s `nothing` / `the whole control` row) move in the
  slice that ships it, not later.
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
  stable.** `ServerMiddleware`'s ordering guarantees are `mcp==2.1.1`'s, and a
  version bump is where they would change. ADR-0014's exact pinning is what
  makes that a deliberate event rather than a surprise, but the coupling is
  real and is recorded here rather than discovered at the bump.
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

- `schemas/mcp/` holds **8** files and **none** of them is an input schema
  (`ls schemas/mcp/`): seven response or response-fragment schemas plus
  `tool-context.schema.json`.
- The SDK's argument model sets no `extra="forbid"`:
  `grep -c 'extra="forbid"' .venv/lib/python3.13/site-packages/mcp_types/_types.py`
  answers **0**, and `ArgModelBase`'s own config is
  `ConfigDict(arbitrary_types_allowed=True)`
  (`mcp/server/mcpserver/utilities/func_metadata.py`).
- `build_server` passes no middleware (`daemon/runner.py`, the `MCPServer(...)`
  construction).
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
  one a later contributor will loosen to make a failure go away.
- **Slice B2 — the bounds on an untrusted document are applied at this boundary
  too.** `validate_migration_document`'s nesting, node and rendered-character
  caps exist because unbounded documents cost unbounded work in `jsonschema`'s
  message building (#291, #245). Owed a test that the MCP boundary refuses a
  document past each bound rather than paying for it.
- **Slice B2 — the two records that say SEC-12 does not run are rewritten in
  the commit that makes them false**: `docs/security/threat-model.md`'s *Future
  controls, not shipped* entry and `docs/roadmap.md`'s SEC-12 requirement row
  (`nothing` / `the whole control`). Not a later documentation pass — an
  on-main claim must not call a control unimplemented while it runs. Whether
  the rewrite is *faithful* is a reading and no mechanical check reaches it,
  which is said here rather than left to be inferred.
