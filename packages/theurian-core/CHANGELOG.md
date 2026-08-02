# Changelog — Theurian Core

All notable changes to the **`theurian` Python package** are documented here.
The Claude Code plugin has its own changelog at
[`plugins/claude-code/CHANGELOG.md`](../../plugins/claude-code/CHANGELOG.md);
the two version and release independently (ADR-0001).

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Pre-1.0, a MINOR bump may change the protocol. Post-1.0, only a MAJOR may.

## [Unreleased]

### Fixed after Milestone 4

- **`theurian auth rotate` did not exist**, while three user-facing messages told
  people to run it — including the one shown when a token is found readable by
  other users. A remedy that errors out is worse than no remedy, because it is
  shown at the moment a credential has already been exposed. Rotation now
  replaces the token, rewrites the env file that names its location, and
  restarts the daemon: the daemon reads its token once at startup, so writing a
  new file alone leaves every client getting a 401 with no visible cause.
- **`theurian daemon stop`** now exists. Milestone 3 omitted it deliberately —
  the service manager owns the lifecycle and a PID-based stop would contradict
  the reason this design uses an advisory lock. Milestone 4 made `daemon start`
  delegate to that manager, which left the absence of `stop` an arbitrary
  asymmetry rather than a principled one. It asks the service manager, and
  refuses rather than guessing when none is registered.

### Milestone 4 — setup, service adapters, and doctor

#### Added

- **`theurian setup`**, an idempotent plan-then-apply state machine over the
  eighteen steps of the specification. It probes everything, shows what it would
  do, applies only that, and then probes everything again — so the report states
  what *is*, not what the apply functions believe they did. Running it twice
  changes nothing (FR-L1, FR-L2).
- **`--dry-run`** is the same code path with the apply skipped, so what the user
  is shown cannot drift from what runs.
- **User-scoped service adapters** for macOS LaunchAgent and Linux systemd user
  units. Never a LaunchDaemon and never a system unit: those need administrator
  rights and would run Theurian as root or a service account to read one user's
  home directory.
- **`theurian doctor`**, read-only by design, and **`doctor --report`**, which
  redacts the home directory, the token path, and repository paths by default
  because its output is what people paste into public issues (O-3).
- **`theurian uninstall`**, which removes the OS service and the MCP entry
  independently and never touches approved knowledge (FR-L5).
- **MCP connection installation** into Claude Code at user scope, carrying
  `${THEURIAN_MCP_TOKEN}` rather than a literal token (SEC-5).

#### Changed

- Theurian **reads** `~/.claude.json` and delegates every **write** to
  `claude mcp add` / `claude mcp remove`. That file is Claude Code's live state,
  not a configuration file Theurian has any business reformatting, and Claude
  Code may be writing to it concurrently. See the amendment to ADR-0012.
- `theurian daemon start` without `--foreground` now asks the service manager
  to start the daemon rather than refusing. Theurian never daemonises itself:
  launchd and systemd already do supervision, restart-on-failure, and log
  redirection, and a hand-rolled double-fork would be a second, worse
  implementation of all three. Starting an *unregistered* service is refused
  rather than improvised — a hook may resume a service the user approved, but it
  must never be the thing that installs one (FR-L3).
- `theurian daemon status` now distinguishes `not-installed` from
  `installed-stopped` by asking the service manager. The SessionStart hook
  branches on exactly this: one means a user-approved service may be resumed,
  the other means send the user to `/theurian:setup` and install nothing.

#### Fixed

- Setup reported `degraded` on almost every successful install. The verification
  pass re-probed the daemon's health microseconds after the start command
  returned, long before it had bound its port. Starting a service and having it
  answer are separate events, and setup now waits for the second one.

#### Known limitations

- Artifact integrity verification is reported as *not applicable* rather than
  satisfied: there is no signed release manifest to check against yet, and a
  step claiming success without checking anything would be a false assurance
  about supply chain integrity (T-16).
- Rollback is a journal, not an undo. Every apply is a create-or-tighten, so a
  critical failure stops and reports where it stopped rather than deleting a
  token another session may already be using.

---

### Milestone 3 — the single MCP daemon

#### Added

- **One daemon per user per machine**, serving MCP over Streamable HTTP at
  `http://127.0.0.1:7419/mcp`. Ten subagents cost one process, one writer, and
  one warm index rather than ten of each (ADR-0002).
- **Single-instance enforcement** through three independent mechanisms, because
  each alone has a known failure mode: an advisory `flock`, a port probe, and a
  startup handshake that reports version and data directory. A losing starter
  exits 0 after confirming the winner is healthy; it never kills the winner and
  never repairs data. A daemon serving a *different* data directory is a
  conflict, not something to reuse — reusing it would answer every query from
  the wrong knowledge base.
- **Local authentication.** A 256-bit token in a 0600 file inside a 0700
  directory, compared in constant time. `/health` stays unauthenticated so the
  SessionStart hook and the instance probe need no credential (ADR-0011).
- **Five read-only MCP tools**: `knowledge.search`, `knowledge.get`,
  `knowledge.status`, `project.list`, and `system.capabilities`. No write-intent
  tool exists at all — not behind a flag, not behind a permission (ADR-0013).
- **Explicit project context.** Every project-scoped tool requires `projectId`.
  There is no "last used project", because with many agents sharing one daemon
  an implicit default resolves one agent's query against another's project.
- **Trust labelling on every result**: `contentClassification:
  untrusted-knowledge`, `mayContainInstructions: true`, `executable: false`,
  plus source anchors and freshness. Knowledge bodies contain imperative
  sentences because they describe rules; the labels say so explicitly (SEC-15).
- **`theurian daemon start --foreground` and `theurian daemon status`**, both
  with `--json`.

#### Security

- The daemon refuses to bind anything but loopback. A networked deployment needs
  TLS, OAuth 2.1, audience validation, and tenant isolation; shipping half of
  them would be worse than shipping none (SEC-1).
- Origin and Host validation, so a page the user visits cannot reach the MCP
  endpoint by resolving a hostname to 127.0.0.1 (SEC-2, T-2).
- Access logging is off. Every request carries an `Authorization` header, and an
  access log is the easiest place for one to escape its 0600 file (SEC-6).
- A token file readable by other users is refused rather than quietly repaired:
  a credential others could already read is not a credential (SEC-4).
- An unregistered `projectId` returns an error naming what *is* registered,
  never another project's knowledge (SEC-13).

#### Known limitations

- `theurian daemon start` supports `--foreground` only. Detaching belongs to the
  user's service manager — a LaunchAgent or a systemd unit — which arrives with
  `/theurian:setup` in Milestone 4. There is deliberately no `daemon stop`:
  the lifecycle owner is the service manager, and a PID-based stop would
  contradict the reason the design uses an advisory lock rather than a PID file.
- `knowledge.search` matches substrings. Ranked hybrid retrieval arrives in
  Milestone 5; the *result shape* is already the published one, so callers
  written now keep working.

---

### Milestone 2 — source ingestion

#### Added

- **Parsers** for Markdown, YAML, JSON, OpenAPI, AsyncAPI, and JSON Schema.
  Structured sources keep their structure: an OpenAPI document yields an index
  of operations, parameters, responses, and schemas, which is what specification
  coverage will read in Milestone 8.
- **Deterministic text projection** so lexical search can reach structured
  content. Renders `outcomes.failure.code: CANCELLATION_NOT_ALLOWED` rather than
  a bare value dump, and is byte-identical across processes and machines.
- **Front matter handling**: parsed, preserved as searchable data, and never
  permitted to govern. A `status: approved` in front matter is ignored *and
  reported*, because a silent ignore is exactly the case where an author
  believes something is approved and it is not.
- **Media-type detection** that prefers content over extension. An OpenAPI
  document is conventionally `openapi.yaml`, and treating it as plain YAML would
  discard the operation index — a loss that would only surface milestones later
  as a query returning nothing.
- **`theurian ingest`**, with per-document failure isolation and an incremental
  path: an unchanged file costs one hash, not a reparse.
- External `$ref` targets are recorded, never fetched (SEC-10, T-7).

#### Fixed

- Markdown files with front matter were reparsed on every run. The manifest
  stored the *body* hash while the early exit compared the *source* hash, and
  those differ for exactly such a file. The two are now distinct fields with
  distinct purposes.
- OpenAPI documents serialised as JSON were not detected, because the sniff
  assumed a YAML line start and `{"swagger": ...}` never matched. They fell
  through to the generic JSON parser and silently lost their operation index.
- The blank line after a front matter block stayed in the body, so identical
  prose hashed differently depending on whether the file carried front matter.

---

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
