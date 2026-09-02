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
- `additionalProperties: false` is deliberate throughout. Silently accepting an
  unknown field turns a typo into a value that is quietly ignored — in a
  migration format, that means an operation someone believes they applied and
  did not.
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
a real response. Which test does that, and against what, because the rule above is
worth nothing if the reader has to guess where it has been applied:

| Schema | Checked against real output by |
| :-- | :-- |
| `mcp/knowledge-search-response.schema.json` | `test_wire_contract.py`, on **both** answer paths — ranked retrieval and the unranked fallback |
| `knowledge/retrieval-result.schema.json` | the same test, transitively: the response `$ref`s it, so every validated response validates every hit |
| `mcp/retrieval-metadata.schema.json` | the same test, transitively, by the same `$ref` |
| `mcp/knowledge-status-response.schema.json` | `test_wire_contract.py`, against two projects the real CLI built: one holding an `approved`, a `draft` and a `proposed` item, and one holding only retired ones |
| `cli/version.schema.json` | `test_schemas.py::test_version_output_matches_its_published_schema`, against the payload `theurian version` emits |
| `mcp/project-list-response.schema.json` | `test_wire_contract.py`, against a registry that reads cleanly and one holding two unreadable entries |
| `mcp/review-findings-response.schema.json` | `test_wire_contract.py`, against three real `review.findings` responses over a store that also holds a rejected trailer: a full read carrying rows with the derived fields both set and null, a filtered read, and the empty one — `count: 0` is the case a `minItems` would have rejected |
| `mcp/tool-context.schema.json` | nothing, and nothing should: it describes tool *input*, so there is no response to compare. `test_project_id_is_required_on_every_tool_call` holds what it is for |

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
             packages/theurian-core/tests/integration/test_wire_contract.py -v
```

Cross-file `$ref`s are resolved from a registry built out of this directory.
Nothing here is fetched over the network — the offline CI job blocks it, and a
schema that silently resolved to nothing would validate everything.
