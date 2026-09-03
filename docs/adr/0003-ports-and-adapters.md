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

   > **Amended in Milestone 7, by the port-register CL
   > ([#140](https://github.com/theurian/theurian/issues/140)). The closed set
   > is `ALL_PORTS`; it holds seventeen. The register is the part this point
   > never wrote down, and that omission — not the number — is what let the
   > set drift.**
   >
   > "Exactly these fourteen" gives a count and a list but names no *register*:
   > no statement of which collection the closed set is closed **over**. Two
   > readings are available and they disagree — membership of
   > `theurian.domain.ports.ALL_PORTS`, or "declares a `typing.Protocol` under
   > `domain/ports/`". Because the point never chose, a Protocol can be added
   > under `domain/ports/`, injected by a composition root, and never reach the
   > test that is supposed to make adding a port an architecture decision. That
   > is not hypothetical. `McpClientConfig` did exactly that, and `IndexStore`
   > reached `ALL_PORTS` only in Milestone 6 — before which every check in
   > `tests/unit/test_ports.py` had been silently vacuous for it.
   >
   > **The register is `ALL_PORTS`, in `domain/ports/__init__.py`**: the set of
   > *injected boundaries*, each a substitution point a composition root wires
   > an adapter into. `test_port_set_is_closed` compares that tuple against a
   > committed list, so `ALL_PORTS` membership is what "requires an ADR"
   > actually gates. A `Protocol` declared under `domain/ports/` but absent
   > from `ALL_PORTS` is **not** covered by that test — so this amendment names
   > the current two rather than leaving them to be rediscovered.
   >
   > The count is a measurement, not a decision: **`ALL_PORTS` held 17 entries,
   > against 19 `Protocol` classes declared under `domain/ports/`, measured on
   > 2026-09-03 at `e2a950ef`.** The live claim is `test_port_set_is_closed`
   > and its `EXPECTED_PORTS` list, not this sentence. A number in prose is a
   > snapshot, and the reason this point sat at "fourteen" while the register
   > grew to seventeen is precisely that nothing recomputed it.
   >
   > The two Protocols outside the register, and why each is outside it:
   >
   > | Outside `ALL_PORTS` | Standing |
   > | :-- | :-- |
   > | `CanonicalReadSession` | **A narrowing of `CanonicalStore`, not a second substitution point.** Its members are `list_items`, `get_item`, `get_revision`, `get_item_exact`, `__enter__` and `__exit__`; the first three are `CanonicalStore`'s own, and the rest add the explicit handle lifetime that port deliberately does not express. It is handed to `IndexBuilder` and `RetrievalService` through an injected `store_factory: Callable[[Path], CanonicalReadSession]` — **no `CanonicalStore` method returns one** — but what an operator substitutes is still a `CanonicalStore` adapter, so it opens no boundary `ALL_PORTS` does not already govern |
   > | `McpClientConfig` | **An open question, recorded here rather than settled.** It has a port's shape: `SetupContext.mcp_config` is constructor-injected, and `cli/setup_commands.py` — a composition root — names `ClaudeCodeMcpConfig` as its adapter. Yet it is absent from `ALL_PORTS` and unimported by `ports/__init__.py`, so `test_port_set_is_closed` has never seen it. Whether it *joins* the register is itself a decision this point says requires an ADR, and this amendment does not take it. Trail: [#140](https://github.com/theurian/theurian/issues/140) |

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
  compares `ALL_PORTS` against a committed list, so adding a port is an
  architecture decision rather than a refactor. **Nine further tests run over
  every entry of `ALL_PORTS`** — six parametrised over it
  (`test_port_is_a_protocol`, `test_port_is_runtime_checkable`,
  `test_port_documents_itself`, `test_port_declares_at_least_one_member`,
  `test_port_methods_are_annotated`, `test_port_has_no_implementation`) and
  three looping over it (`test_all_ports_is_exported_and_consistent`,
  `test_protocols_are_not_instantiable`, `test_typing_protocol_is_the_base`).
  That all nine are *driven by the tuple* is what makes it the register point
  5's amendment names: a `Protocol` under `domain/ports/` that never reaches
  `ALL_PORTS` is not merely unlisted, it is unreached by every one of them.
  (A tenth, `test_determinism_ports_are_present`, names `Clock` and
  `IdGenerator` specifically and so is not in that nine.)

Still owed, with the milestone that will satisfy it:

- **Nothing recomputes point 5's amendment, so it can drift the same way point
  5 did.** The amendment states a register (`ALL_PORTS`), a count (17 entries
  against 19 `Protocol` classes under `domain/ports/`), and a two-row table of
  the Protocols outside the register. All three are prose today: adding an
  eighteenth port, or a twentieth Protocol, falsifies this ADR silently and no
  test goes RED. The pin owed is a derivation rather than a string match — it
  recomputes both figures from `ALL_PORTS` and from an AST walk of
  `domain/ports/*.py`, and it recomputes the outside-the-register set as the
  difference between them, so the table's membership is checked and not just
  its size. Landing in the tests stage of
  [#140](https://github.com/theurian/theurian/issues/140); until it does, this
  ADR's numbers are a dated measurement and `test_port_set_is_closed` is the
  only live claim.

- **No test asserts every port has a fake, and most do not have one.** This
  section claimed one did. `tests/fakes/` defines **six** doubles —
  `FrozenClock`, `FakeReviewFindingSource`, `SeededIdGenerator`, `FakeService`,
  `FakeMcpConfig`, `InMemoryWriter` — and between them they satisfy **four of
  the seventeen** ports in `ALL_PORTS`: `Clock`, `IdGenerator`, `DaemonManager`
  and `ReviewFindingSource`. The other two doubles stand in for things that are
  not in the register at all — `FakeMcpConfig` for `McpClientConfig`, and
  `InMemoryWriter` for `MigrationWriter`, which is an `application/` Protocol
  rather than a port. Thirteen ports have no double. Measured on 2026-09-03 at
  `e2a950ef`.

  The **key matters more than the count here**, so it is stated rather than
  implied: each double is tested with `isinstance` against every
  runtime-checkable port, not matched by name. A name search answers
  differently and wrongly in both directions — `git grep -w IndexStore` hits
  `fakes/pages.py`, which builds `RetrieverPage` helpers and defines no
  `IndexStore` double, while `git grep -w Clock` misses `FrozenClock`
  entirely.

  The claim is also the wrong shape for this design: a port with one adapter and
  no in-memory double is not a gap, and demanding a fake per port would produce
  fakes nobody uses. What ADR-0003 actually wants is that *application code is
  testable without infrastructure*, and neither a test nor this section states
  that in a checkable form. **Still owed, and no milestone currently owns it.**
  It was deferred to Milestone 6 on the expectation that new ports would force
  the question. They did not force it. Milestone 6 landed
  `SummarizationProvider` (`ExtractiveSummarizer`) with no double, and
  Milestone 7's two ADR-0029 ports landed one between them —
  `FakeReviewFindingSource` for `ReviewFindingSource`, nothing for
  `ReviewFindingStore`. Each addition was decided on its own, which is the
  behaviour a rule with no test produces. It still needs a decision about what
  the property *is* before it can have one. Trail:
  [#140](https://github.com/theurian/theurian/issues/140).
