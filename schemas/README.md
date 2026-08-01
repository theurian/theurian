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

## Validation

Schemas are validated in CI for well-formedness, and every example under
`examples/` is validated against them. To check locally:

```sh
uv run pytest packages/theurian-core/tests/unit/test_schemas.py -v
```
