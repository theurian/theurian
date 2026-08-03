# Changelog — Theurian Core

All notable changes to the **`theurian` Python package** are documented here.
The Claude Code plugin has its own changelog at
[`plugins/claude-code/CHANGELOG.md`](../../plugins/claude-code/CHANGELOG.md);
the two version and release independently (ADR-0001).

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Pre-1.0, a MINOR bump may change the protocol. Post-1.0, only a MAJOR may.

## [Unreleased]

### Milestone 5 — hybrid retrieval

#### Added

- **Reciprocal Rank Fusion** over three retrievers (FR-R2): a word index, a
  trigram substring index, and — opt-in — a dense one. Fusion uses *ranks*,
  never scores. BM25 and cosine similarity are not comparable quantities, and
  neither are two BM25 scores computed over different token spaces; normalising
  any of them onto one scale needs assumptions about their distributions that do
  not survive a change of corpus, tokenizer, or embedding model (ADR-0021).
- **A trigram index beside the word index**, which is what makes languages
  without word spacing searchable at all. `unicode61` splits on whitespace and
  punctuation only, so `署名付きトークンを持つ` is one token and `トークン`
  matched nothing. Both indexes feed the fusion as separate retrievers; the
  trigram one is not a replacement, because trigrams are worse at the exact
  identifiers engineering queries are mostly made of — a trigram search for
  `cat` matches `concatenate`. (ADR-0023)
- **Document chunking** on structure first and length second — headings, then
  paragraphs, then sentences, then words, then a hard character cut as the
  backstop that always terminates.
- **A retrieval index in its own SQLite file**: FTS5 for terms, an exact vector
  scan for the rest. Separate from the canonical store on purpose — the
  canonical `SCHEMA_VERSION` is an input to the state hash (ADR-0017), so
  co-locating them would make every index change invalidate every canonical
  state.
- **A default embedding provider** that is deterministic, local, and needs no
  API key: hashed character trigrams. It is **not a semantic model, does not
  claim to be, and is no longer on by default** — see the breaking change below.
- **Diversification and token budgeting** (FR-R4). At most N chunks per item, so
  one long document cannot take every slot; packing strictly in rank order,
  never a knapsack fill that would trade relevance for a number the caller
  cannot see.
- **`theurian index build` and `theurian index status`.** Status reports three
  hashes — what the knowledge *is*, what the database *holds*, and what the
  index was *built from* — because all three can differ, and comparing only the
  last two calls an index fresh exactly when someone most needs to be told
  otherwise.
- **`theurian project register --project-id <id>`**, which is how a directory
  name collision is broken. See the breaking change below.

#### Changed

- **BREAKING — `knowledge.search` response shape.** The flat `note` string is
  replaced by a structured `retrieval` object carrying `mode`, `indexed`,
  `stale`, `staleAgainst`, `withheldSuperseded`, `indexesUnapproved`,
  `indexBuildId`, `embeddingModel`, `usedTokens`, and `droppedForBudget`. Each
  hit gains `foundBy` (which retrievers surfaced it) and `fusedScore`. A ranking
  nobody can explain is a ranking nobody can debug.

  `retrieval.mode` takes four values, not three: `substring` when no index has
  been built, then `lexical`, `dense`, or `hybrid` according to which retrievers
  actually returned anything.

  When the answer came from the unranked fallback, `retrieval.fallbackReason`
  says which of four things happened — `no-index`, `index-pointer-invalid`,
  `index-file-missing`, or `unapproved-not-indexed`. All four used to produce the
  same sentence, "no retrieval index has been built for this project", which is
  true of exactly one of them; the other three told a user to run a command they
  had already run and said nothing about the one that would have helped.

- **BREAKING — the index schema is version 2; existing indexes must be rebuilt.**
  The trigram table is new, and `INDEX_SCHEMA_VERSION` went 1 → 2 with it. Run
  `theurian index build`. Nothing canonical is affected: the index is derived and
  disposable, and this is the lifecycle separation ADR-0022 exists for, exercised
  for the first time.

  **A version-1 index is not detected.** The missing table surfaces as a SQLite
  error that the substring retriever catches and answers with no results, so an
  index built before this release silently loses the trigram half and a Japanese
  knowledge base goes back to being invisible — with nothing in the response
  saying so. A version check on open is owed (ADR-0022, Milestone 6). Until then,
  rebuild after upgrading.

- **BREAKING — dense retrieval is off by default.** `SearchRequest.use_dense` and
  the MCP parameter `useDense` both default to `false`, so a healthy default
  search now reports `retrieval.mode: "lexical"` rather than `"hybrid"`.

  This is measured, not cautious. Against a real corpus, **91% of unrelated
  natural-language questions cleared the bundled embedder's similarity floor**,
  while the lowest genuinely related query scored below the unrelated median. The
  distributions overlap; no threshold separates them, because what the embedder
  measures is English surface-form overlap and not topical relevance. The floor
  in the code was calibrated against random strings, which turned out to be the
  easy case and the wrong population to calibrate on.

  The retriever is kept and made opt-in rather than deleted, so the code path
  stays exercised and works the day a real model is configured through the same
  port (ADR-0009). `theurian index build` still writes embeddings unless
  `--no-embeddings` is passed, so opting in needs no rebuild.

- **BREAKING — a project id already registered to another root is refused.**
  `ProjectRegistry.register` used to overwrite. Ids default to the directory
  name, directory names repeat, and registering `team-two/api` therefore
  re-pointed the id `api` at the newer root — after which an agent working in
  `team-one` that asked for `api` was served `team-two`'s knowledge, with no
  error and nothing in the answer naming the repository (SEC-13). Registration
  that used to succeed now exits 1 and names the conflict. Break it with
  `theurian project register --project-id <id>`. Choosing a suffix automatically
  would have been worse: an already-configured agent keeps naming `api` and would
  silently follow the id to whichever project kept it.

- **Project id resolution order changed** to: explicit `--project-id`, then the
  registry keyed by *root path*, then the directory name. Without the middle
  step, a project registered under a disambiguated id would still be addressed by
  the colliding default on its own command line — the CLI writing to one project
  while every agent reads the other.

- `knowledge.search` gains a `maxTokens` parameter (FR-R4).
- Searching a project with no index falls back to the previous substring scan
  and says so, rather than returning nothing — which would read as "we have no
  such decision" rather than "ask me again in a moment".
- The substring fallback now honours `maxTokens` as well. FR-R4 is a promise
  about every answer, and this path ignored it: fifty results carrying their
  provenance and trust labels are several thousand tokens handed to a caller who
  asked for five hundred.
- The relevance floor on the lexical retriever was removed, because it was dead
  code. A review reported that BM25 returns "exactly 0.0000" when the only
  matching terms appear in every row, and proposed excluding those hits.
  Measured, SQLite returns `-1.375e-06` for that case — the `0.0000` was a
  printed rounding — so the threshold excluded nothing while claiming to be a
  floor. Separating "matched only common words" from "matched weakly" needs a
  per-term IDF test, which is recorded as an outstanding gap rather than papered
  over.

#### Fixed

- Japanese documents were indexed as a single chunk. Japanese puts no space
  after a full stop, so the sentence pattern matched nothing and the word
  fallback had no spaces to split on either. Found by running it, not by
  reading it.
- `theurian index build` reported "no built knowledge state" on a project that
  had one: `_require_project` returns the state *database* as its second value,
  and the new code treated it as the repository root.

#### Security

- **`knowledge.get` was not gated on status** (SEC-13). Closing every path
  through `knowledge.search` achieved nothing while this stood open: a caller
  read an approved item, took a `targetItemId` off one of its relations, and
  fetched the withheld body in one further call. No flag, no guessing. A
  rejected revision is where the secret that caused the rejection still lives.
  Both the item and its relations are now limited to surfaceable statuses by the
  same authority search uses.
- The refusal for a withheld item is byte-identical to the one for an item that
  does not exist, so the error cannot be used to confirm that a retired item
  exists at a given id.
- A stale index no longer resurrects retired knowledge or superseded revisions.
  Status and current-revision are both re-checked against the canonical store on
  *both* the default and the `includeUnapproved` path; a stale index returns
  fewer results rather than wrong ones, and `retrieval.withheldSuperseded` says
  how many it held back.

#### Known limitations

- The default embedder is lexical in vector form, and off by default for the
  reason given above. Semantic retrieval needs a real model, which plugs in
  through the `EmbeddingProvider` port without touching anything else
  (ADR-0003, ADR-0009).
- `INDEX_SCHEMA_VERSION` is written into every index and never read back, so a
  schema mismatch degrades silently instead of being reported (ADR-0022).
- A search running while `theurian index build` publishes is not protected. The
  new build reaps the old file immediately, and the retrieval store holds no
  open handle between queries, so such a search falls back to the substring scan.
  Blue/green index builds in Milestone 6 are what fix this properly; ADR-0022's
  original promise that the previous build survives has been withdrawn rather
  than delivered.
- Scope filtering is not implemented. `sensitivity`, `trust_level`, and
  `namespace` are carried on every chunk and read by no query; `namespace` is not
  even populated. Milestone 6.
- RAPTOR summary nodes (FR-R3) and reranking arrive in Milestone 6.

---

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
