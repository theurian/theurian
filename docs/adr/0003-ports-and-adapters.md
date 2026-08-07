# ADR-0003: Ports and adapters as the top-level structure

- Status: accepted
- Date: 2026-08-01
- Deciders: Theurian maintainers
- Requirements: FR-S4, OSS-15, NFR-9, §4.4 of the brief

## Context

Theurian starts as a local tool over SQLite and is expected to grow into a
multi-tenant hosted service over PostgreSQL, an external vector database, and
managed embeddings. It must also work today with no LLM API key at all.

Those are not compatible unless the substitution points are decided before the
code exists. Retrofitting a port onto a codebase where `sqlite3.Connection` has
already leaked into thirty call sites is a rewrite.

There is an opposite failure mode that is just as real: a codebase where every
concept has an interface, a factory, and a registry, and nothing can be read
end to end. §34 of the brief explicitly rules that out.

## Decision

Ports and adapters, with the port set fixed in advance and deliberately small.

1. `domain/` holds entities, value objects, invariants, and **ports**. A port is
   a `typing.Protocol`. `domain/` imports nothing from `application/` or
   `infrastructure/`.
2. `application/` holds use cases. It depends on `domain/` only, and receives
   adapters by constructor injection.
3. `infrastructure/` holds adapters. An adapter may import `domain/`; nothing
   imports an adapter except a composition root.
4. Composition roots are `cli/`, `daemon/`, and `mcp/`. They are the only places
   allowed to name a concrete adapter.
5. The port set is exactly these fourteen. Adding a port requires an ADR:

   `CanonicalStore`, `VectorStore`, `EmbeddingProvider`, `SummarizationProvider`,
   `RerankingProvider`, `ReviewProvider`, `SpecificationProvider`, `SourceParser`,
   `ObjectStore`, `AuthorizationProvider`, `SecretStore`, `DaemonManager`,
   and the two determinism ports `Clock` and `IdGenerator`.

6. No dependency-injection framework. Composition roots wire objects with plain
   constructor calls, in one readable function per entry point.
7. Every port ships a deterministic fake under `tests/fakes/`. A port with no
   fake is not finished.

`Clock` and `IdGenerator` earn their place because ULIDs and timestamps are
inputs to the state hash (ADR-0007). Without controlling them, "same inputs
produce the same state hash" is not assertable.

## Consequences

### Positive

- SQLite → PostgreSQL is a new `CanonicalStore` adapter, not a migration of the
  application layer.
- The whole test suite runs offline with no paid API key (OSS-15, NFR-10).
- Pre-1.0 dependencies are quarantined behind a port (ADR-0014).
- Determinism is testable, which is what makes ADR-0007 verifiable.

### Negative

- One extra indirection between a use case and its storage. Accepted; the
  substitution requirement is real, not speculative.
- Protocol definitions and adapters must stay in sync; mypy strict mode is what
  makes that a compile-time concern rather than a runtime surprise.

### Neutral

- Adapters may use their technology fully. The SQLite adapter writes SQLite SQL.
  The rule is containment, not abstraction of SQL itself.

## Alternatives considered

| Alternative | Why rejected |
| :-- | :-- |
| Direct SQLite calls from the application layer | Fastest to Milestone 1, forecloses §4.4 entirely. |
| A repository interface per entity | Dozens of near-identical interfaces; a `CanonicalStore` façade per aggregate is the right granularity here. |
| ABCs instead of `Protocol` | Forces adapters to inherit from a domain class, inverting the dependency the ADR exists to protect. |
| A DI container | Runtime wiring errors instead of type errors, plus a dependency, to solve a problem three composition roots do not have. |

## Compliance

- `[tool.ruff.lint.flake8-tidy-imports.banned-api]` bans importing
  `theurian.infrastructure` outside composition roots.
- `tests/unit/test_layering.py` walks the AST import graph and asserts
  `domain/` imports neither `application/` nor `infrastructure/`.
- mypy strict mode verifies every adapter satisfies its Protocol.
- `tests/unit/test_ports.py` pins the port set itself: `test_port_set_is_closed`
  compares `ALL_PORTS` against a committed list so adding a port is an
  architecture decision rather than a refactor, and
  `test_port_is_a_protocol`, `test_port_is_runtime_checkable`,
  `test_port_has_no_implementation`, `test_port_declares_at_least_one_member`
  and `test_port_methods_are_annotated` run over every one of them.

Still owed, with the milestone that will satisfy it:

- **No test asserts every port has a fake, and most do not have one.** This
  section claimed one did. `tests/fakes/` defines five doubles —
  `FrozenClock`, `SeededIdGenerator`, `InMemoryWriter`, `FakeService`,
  `FakeMcpConfig` — naming three of the fourteen ports
  `test_port_set_is_closed` pins (`Clock`, `IdGenerator`, `DaemonManager`). The claim
  is also the wrong shape for this design: a port with one adapter and no
  in-memory double is not a gap, and demanding a fake per port would produce
  fakes nobody uses. What ADR-0003 actually wants is that *application code is
  testable without infrastructure*, and neither a test nor this section states
  that in a checkable form. Deferred rather than filed: it needs a decision
  about what the property is before it can have a test, and Milestone 6 adds
  ports (RAPTOR summarisation) that will force the question.
