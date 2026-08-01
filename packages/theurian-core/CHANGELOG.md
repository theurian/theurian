# Changelog — Theurian Core

All notable changes to the **`theurian` Python package** are documented here.
The Claude Code plugin has its own changelog at
[`plugins/claude-code/CHANGELOG.md`](../../plugins/claude-code/CHANGELOG.md);
the two version and release independently (ADR-0001).

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Pre-1.0, a MINOR bump may change the protocol. Post-1.0, only a MAJOR may.

## [Unreleased]

### Milestone 1 — local canonical store

#### Added

- **Knowledge migration engine.** Applies the fourteen YAML operations
  transactionally, with `expectedRevision` optimistic concurrency, deterministic
  topological ordering, cycle detection that names the actual cycle, and
  idempotent re-application.
- **SQLite canonical store.** WAL, `foreign_keys=ON`, immutable revisions with no
  update path, alias resolution, bidirectional relation traversal, and a
  migration history that records checksums.
- **State hashing.** Content-addresses a whole canonical state so a database
  file's name describes its contents. Covered by a committed golden vector and a
  cross-process test that would catch a `PYTHONHASHSEED` dependency.
- **Single-writer guarantee.** An OS advisory lock serialises concurrent writers,
  behind an interface a daemon-owned queue can replace without touching
  application code.
- **CLI**: `theurian init`, `project register|unregister|list|status`, and
  `migrate status|validate|apply`, all with `--json`.
- **Deterministic fakes** for `Clock`, `IdGenerator`, and the migration writer.

#### Fixed

- The published JSON Schemas were not packaged into the wheel, so an installed
  `theurian` could not validate a migration at all. A build hook now ships them
  and an e2e test asserts an installed build can read a migration.
- Editing an already-applied migration was not detected. Editing one changes the
  state hash, which routed the next command to a fresh empty database where
  nothing looked wrong; the check now runs against the previously active state
  as well. See the amendment to ADR-0016.
- Re-registering a project reported a change every time, because the
  registration timestamp was refreshed. It now records when the project was
  *first* registered, restoring the idempotence FR-L2 requires.
- A read connection leaked when verifying migration history.

#### Security

- `contentFile` paths are resolved with symlinks followed before the containment
  check, so both `../` traversal and symlink escape are refused (SEC-7, T-4, T-5).
- YAML loading no longer coerces timestamps to `datetime`, which had made valid
  migrations fail their own schema validation.

---

### Milestone 0 — architecture and OSS foundation

### Added

- **Domain model.** Immutable `KnowledgeRevision` under a mutable
  `KnowledgeItem` pointer; typed relations, aliases, source anchors, and
  evidence; specifications with their structured form preserved; traceability
  edges and per-change-type policy; review events, threads, comments,
  resolutions, promotion gates, and knowledge candidates.
- **Ten enforced invariants**, including content-hash verification, immutable
  revisions, half-open validity windows, and mandatory source attribution.
- **Fourteen ports** as `Protocol`s, including `Clock` and `IdGenerator` — both
  ports because time and identifiers are inputs to the state hash, and without
  controlling them the reproducibility guarantee is not assertable.
- **Scope isolation primitives.** A RAPTOR tree's identity is
  `(project, tenant, sensitivity, acl_group, namespace)`, so a summary spanning
  two sensitivity levels has no tree to belong to.
- **Path containment and input limits.** `realpath` resolution before
  containment checks, symlink-escape rejection on intermediate components as
  well as final targets, size and depth caps, and permission checks for
  credential files.
- **Compatibility resolution**, including SemVer §11 ordering and a PEP 440 →
  SemVer translation so Core's own development versions resolve correctly.
- **CLI**: `theurian version --json` and `theurian compat check`, with exit code
  3 reserved for a compatibility mismatch.
- **Public JSON Schemas** for the migration format, MCP tool context, retrieval
  results, project configuration, the CLI version contract, and plugin
  compatibility metadata.
- **275 tests** covering domain invariants, layering, scope isolation, path
  security, schemas, the plugin boundary, and the CLI contract. All run offline.

### Security

- Retrieval results are structurally prevented from being marked executable.
- Every result requires at least one source anchor.
- Migration `contentFile` paths are rejected at both schema and runtime level if
  they escape the project root.

[Unreleased]: https://github.com/theurian/theurian/commits/main
