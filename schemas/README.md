# Public schemas

These JSON Schemas are the **contract** between Theurian Core and every client,
including the Claude Code plugin. They are co-owned: a change here requires
review from both Core and plugin maintainers (see `.github/CODEOWNERS`).

The plugin never imports Core's Python modules (ADR-0001, CP-2). These schemas
and the CLI JSON they describe are the entire permitted surface, which is what
keeps the plugin movable to its own repository.

| Directory | Contract |
| :-- | :-- |
| `cli/` | JSON emitted by `theurian … --json`, validated by `tests/contract/` |
| `config/` | `.theurian/config.yaml`, the Git-tracked per-project configuration |
| `knowledge/` | Canonical knowledge shapes returned by retrieval |
| `migrations/` | The knowledge migration format (ADR-0005) |
| `mcp/` | MCP tool input and output shapes |
| `protocol/` | Plugin/Core compatibility metadata (§30) |

## Compatibility rules

- **Additive changes** (a new optional property) are a MINOR change and do not
  bump `protocolVersion`.
- **Breaking changes** (removing a property, tightening a type, adding a
  required property) bump `protocolVersion` and therefore every plugin's
  `coreCompatibility` range.
- **Closing against unknown fields is deliberate throughout**, and which keyword
  does it depends on the side. A response schema closes with
  `additionalProperties: false`. A published input schema closes with
  `unevaluatedProperties: false`, because `additionalProperties` in Draft 2020-12
  considers only the `properties` of its own schema object and so rejects the
  shared-context fields a per-tool schema has just referenced by `$ref`
  ([ADR-0031](../docs/adr/0031-mcp-input-is-schema-validated-in-middleware.md)
  decision 1 measured all three arrangements). `mcp/tool-context.schema.json` is
  the one file here that closes with neither: its closure moved to each referrer,
  and `test_object_schemas_reject_unknown_properties` asserts that delegation is
  real rather than skipping the file. Silently accepting an unknown field turns a
  typo into a value that is quietly ignored — in a migration format, that means
  an operation someone believes they applied and did not; over MCP it means a
  caller was answered confidently about a question it did not ask.
- **Correcting a schema to describe what is already emitted is neither.** The
  rules above are about the *wire*: they exist so a client that works keeps
  working. Removing a property no version ever sent cannot break a client,
  because no response ever carried it and no schema-validating client could have
  existed — the schema rejected every real response. Such a change does not bump
  `protocolVersion`. It must, however, say plainly in the changelog that the
  published contract was wrong, because an integrator who wrote code against the
  document rather than the product has work to do.

## A published output schema is validated against real output

Well-formedness is not conformance. `retrieval-result.schema.json` was valid,
reviewed, and linked from the protocol documentation while it rejected **every**
result Theurian emits: it required four fields nothing sets and, with
`additionalProperties: false`, refused the two that every ranked hit carries. The
tests that covered it asserted properties *of the schema* — that `executable` is
`const: false`, that at least one source anchor is required — and not one had
ever compared it against a response. A contract with no conformance test drifts
silently, and this one drifted through a whole milestone.

The anchor assertion is the sharper lesson, because it outlived the conformance
test being written. `minItems: 1` was transcribed from the first half of INV-8,
which is a disjunction: a revision carries a source anchor **or** declares that
it originates in Theurian. Both conformance documents carried anchors, so a
supported document still produced a response the published schema rejected, and
the schema-shape test agreed with the schema. A test written from the same
reading as the schema confirms the reading, not the product.

So the rule for anything under `mcp/` or `knowledge/` that describes what a tool
returns:

- it is validated against a response obtained from the tool, not a fixture. A
  hand-written example passes while the wire shape stays wrong;
- both answer paths are covered where a shape has two constructions. Ranked
  retrieval and the unranked fallback are two functions producing one shape, and
  the one that drifts is whichever the fixtures did not reach;
- the corpus reaches every branch of the invariant a constraint is transcribed
  from. A conformance test is only as total as the documents it contains, and an
  invariant with an `or` in it needs one document on each side — otherwise the
  constraint and the corpus share a blind spot and validate each other;
- where the domain constrains a field with a pattern or a bound, the published
  one is compared against the domain's own decision rather than transcribed and
  trusted. That comparison is exhaustive over the cases listed and needs no
  fixture, which is the only way to cover values no corpus contains;
- a field is declared only once something emits it. Declaring a field nothing
  sends tells an integrator to expect something that never arrives, which is the
  same defect as an undeclared field and harder to notice, because nothing
  rejects it. Forward-looking capability is announced through
  `system.capabilities`, which is live and tested, rather than through a schema
  that promises a shape with no delivery date.

## Validation

Schemas are validated in CI for well-formedness, every example under `examples/`
is validated against them, and each published response shape is validated against
a real response. Since SEC-12
([ADR-0031](../docs/adr/0031-mcp-input-is-schema-validated-in-middleware.md)) the
input side is checked too, and against real traffic for the same reason: each
`mcp/*-input.schema.json` is what an SDK `ServerMiddleware` validates every
`tools/call` against before dispatch, so the tests drive calls through the
transport rather than validating a fixture. Which test does that, and against
what, because the rule above is worth nothing if the reader has to guess where it
has been applied:

| Schema | Checked against real traffic by |
| :-- | :-- |
| `mcp/knowledge-search-response.schema.json` | `test_wire_contract.py`, on **both** answer paths — ranked retrieval and the unranked fallback |
| `knowledge/retrieval-result.schema.json` | the same test, transitively: the response `$ref`s it, so every validated response validates every hit |
| `mcp/retrieval-metadata.schema.json` | the same test, transitively, by the same `$ref` |
| `mcp/knowledge-status-response.schema.json` | `test_wire_contract.py`, against two projects the real CLI built: one holding an `approved`, a `draft` and a `proposed` item, and one holding only retired ones |
| `cli/version.schema.json` | `test_schemas.py::test_version_output_matches_its_published_schema`, against the payload `theurian version` emits |
| `mcp/project-list-response.schema.json` | `test_wire_contract.py`, against a registry that reads cleanly and one holding two unreadable entries |
| `mcp/review-findings-response.schema.json` | `test_wire_contract.py`, against three real `review.findings` responses over a store that also holds a rejected trailer: a full read carrying rows with the derived fields both set and null, a filtered read, and the empty one — `count: 0` is the case a `minItems` would have rejected |
| `mcp/knowledge-search-input.schema.json` | the three set-wide sweeps below, plus `test_input_validation_wire.py`, against a real `tools/call`: an unknown key is refused, and the refusal names the key and never the value it carried. `test_input_validation_dispatch.py` drives the request-size bounds and the widest body this transport admits through this same tool, and `test_input_schema_bounds.py` holds `query` and `asOf` against `MAX_QUERY_CHARS` and `MAX_AS_OF_CHARS` |
| `mcp/knowledge-get-input.schema.json` | the three set-wide sweeps below; `test_input_schema_bounds.py` holds `itemId` against `MAX_IDENTIFIER_LENGTH` |
| `mcp/knowledge-status-input.schema.json` | the three set-wide sweeps below. It publishes no length bound, so it has no entry in `test_input_schema_bounds.py`'s table — which is asserted by equality, so a bound added here without an entry fails there |
| `mcp/project-list-input.schema.json` | the three set-wide sweeps below, plus both of `test_input_validation_wire.py`'s controls over a real `project.list` call: with the middleware **lifted off**, the same call carrying an unknown key is served at `isError: false` and answers byte-identically with the key and without it — which is what makes the refusal above attributable to this seat — and **through** the middleware a valid call is answered identically to the same call with the seat off |
| `mcp/review-findings-input.schema.json` | the three set-wide sweeps below; `test_input_schema_bounds.py` holds its six filter bounds against `mcp/findings.py`'s `MAX_FILTER_CHARS` |
| `mcp/review-search-input.schema.json` | the three set-wide sweeps below; `test_input_schema_bounds.py` holds its five filter bounds against `mcp/review_search.py`'s `MAX_FILTER_CHARS` |
| `mcp/system-capabilities-input.schema.json` | the three set-wide sweeps below. Like `knowledge-status`, it publishes no length bound and therefore no entry in the bounds table |
| `mcp/tool-context.schema.json` | `mcp/validation.py`'s loader, reached through every per-tool input schema's `$ref` and applied by the middleware, so this file is read on every project-scoped `tools/call`. **Five tests hold a property of it**, and the honest way to name them is the command rather than a list, because four arrive by parametrization and a sixth would join the same way: `uv run --frozen python -m pytest packages/theurian-core/tests/unit/test_schemas.py --collect-only -q \| grep -i "tool.context"`, plus `test_schemas.py::test_project_id_is_required_on_every_tool_call`, which loads the path as a literal and so appears in no node id. Exactly one of the five moved: `test_object_schemas_reject_unknown_properties[tool-context.schema.json]`, rewritten rather than deleted because ADR-0031 decision 1 relocated the closure to each per-tool schema's `unevaluatedProperties: false` — its arm now asserts the delegation is real, that referrers exist at all and that every one of them carries the keyword |

**The three set-wide sweeps** cover every `mcp/*-input.schema.json` without being
told which files exist, which is the property that matters: a schema added
tomorrow joins them by existing.

- `test_input_validation_dispatch.py::test_every_registered_tool_resolves_to_a_published_input_schema`
  walks the **built server**'s registered tools and asserts set equality in both
  directions — a registered tool with no published schema is refused at dispatch
  rather than served, and a schema published for a tool this build no longer
  registers is a contract naming a call that answers "no such tool".
- `test_input_schema_agreement.py` holds each published schema against the schema
  the SDK derives from that handler's signature, parametrized over every
  registered tool. Its module docstring states the relation and what it excludes:
  the closure axis, which no derived schema carries; value-domain tightening,
  which is the published file's purpose; and `snapshotId`, `agentId` and
  `taskId`, which the shared context publishes and no handler in this build reads
  ([#665](https://github.com/theurian/theurian/issues/665)).
- `test_schemas.py::test_object_schemas_reject_unknown_properties` holds the
  closure keyword, parametrized over every schema in this tree — for an input
  schema that is `unevaluatedProperties: false`, and for a response schema it is
  still `additionalProperties: false`.

`project-list-response.schema.json` was the gap this section was written to name,
and this text went on naming it for a week after it was filled — which is the
same defect as an unverified schema, one level up. It shipped in Milestone 5 with
`project.list`'s two new required fields and no assertion anywhere pinning that
tool's response shape: the fields were added with every test over the MCP tools,
the schemas and the wire contract green, 186 of them at the time. It was checked
by hand against four real responses, including a registry with an unreadable
entry and one whose `rootPath` is empty, and conformed in all four — evidence the
schema was right *that day* and none at all that it would stay right. The
conformance test it was owed landed inside Milestone 5 itself, in `21e1ba9`, and
covers both states the required fields exist to distinguish, because a capture
where nothing is unreadable validates equally well against a schema that had lost
`unreadable` and `remedy` entirely.

`knowledge-status-response.schema.json` is the newer one, and its corpus is
chosen the same way. A project whose items are all retired answers `{}` and `0`,
which is also what a project holding nothing answers, so the empty capture is
asserted beside what its canonical store really contains — otherwise it is a
document rather than evidence. The other capture reaches all three declared keys,
so a schema that had quietly lost `proposed`, or gained `rejected`, still fails.
The retired-only twin holds a `deprecated` item declared in revision metadata
beside one reached through `deprecateItem`, so both paths a retired status enters a
store by are covered: were either to surface in the breakdown,
`additionalProperties: false` rejects the response and this check fails.

To check locally:

```sh
uv run pytest packages/theurian-core/tests/unit/test_schemas.py \
             packages/theurian-core/tests/unit/test_input_schema_bounds.py \
             packages/theurian-core/tests/integration/test_wire_contract.py \
             packages/theurian-core/tests/integration/test_input_validation_wire.py \
             packages/theurian-core/tests/integration/test_input_validation_dispatch.py \
             packages/theurian-core/tests/integration/test_input_schema_agreement.py -v
```

Cross-file `$ref`s are resolved from a registry built out of this directory.
Nothing here is fetched over the network — the offline CI job blocks it, and a
schema that silently resolved to nothing would validate everything.
