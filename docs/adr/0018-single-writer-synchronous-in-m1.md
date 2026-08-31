# ADR-0018: One writer, expressed as a lock in Milestone 1 and a queue later

- Status: accepted
- Date: 2026-08-02
- Deciders: Theurian maintainers
- Requirements: NFR-7, NFR-8, T-13, ADR-0002

## Context

The concurrency model (NFR-7) is: many read connections in WAL mode, exactly one
writer. ADR-0002 describes that writer as "one asyncio task owning one write
connection, fed by a queue" — which presumes a daemon.

Milestone 1 has no daemon. It has a CLI that opens a database, applies
migrations, and exits. Building an asyncio write queue now would mean writing
async plumbing with no concurrent callers to serve, and shaping the application
layer around an execution model that does not yet exist.

The opposite mistake is worse: letting Milestone 1 code call `sqlite3` wherever
convenient, and discovering in Milestone 3 that "exactly one writer" has to be
retrofitted across every call site.

## Decision

**The single-writer guarantee is a contract in the application layer from
Milestone 1. Only its enforcement mechanism changes.**

1. All writes go through one interface: `CanonicalStore.transaction()`, a
   context manager yielding a write handle. There is no other way to write, and
   `CanonicalStore` exposes no connection object.
2. Milestone 1 enforces exclusivity with an **OS advisory file lock on a
   separate lock file**, `.theurian/runtime/write.lock`, held for the duration
   of a write transaction and guarding the state databases under
   `.theurian/state/`. Two concurrent `theurian migrate apply` invocations
   serialise; the loser waits, then observes the other's work and becomes a
   no-op (idempotence, FR-K8).
3. Milestone 3 replaces the lock with an in-process asyncio queue owned by the
   daemon, plus the same file lock for any CLI invocation running alongside it.
   **`transaction()` keeps its signature**, so no application code changes.
4. The application layer is written synchronously in Milestone 1. Async is a
   transport concern; the migration engine is CPU- and disk-bound and gains
   nothing from it.
5. NFR-8 applies from the start: no external I/O inside a transaction. In
   Milestone 1 that means reading and hashing content files *before* opening
   one, not inside it.

The rule that makes this work is point 1. A guarantee implemented behind a
single interface can change mechanism. A guarantee implemented by convention at
each call site cannot.

> **Amended in Milestone 5.** Points 1 and 3 name `CanonicalStore.transaction()`.
> **There is no such method, and there never has been** —
> `git grep "def transaction" -- packages/theurian-core/src` returns nothing.
>
> What implementing it revealed: the port publishes its thirteen write methods
> directly (`append_revision`, `put_item`, `add_relation`, …), and exclusivity
> lives one layer down, in `write_transaction()` in
> `infrastructure/sqlite/connection.py`, a context manager that takes the OS
> advisory file lock point 2 describes and yields a connection.
>
> So the mechanism point 2 specifies is real and works. What is false is the
> claim that makes it *durable*: writes do not go through one interface, and
> `CanonicalStore` does expose write methods a caller can reach without any
> lock. The guarantee is held by convention at each call site — which is the
> exact failure this ADR's closing sentence says cannot be repaired later.
>
> The decision is not superseded, because the decision is right: a single
> interface is still what this should have. The ADR was describing an interface
> that was planned and never built, and the correct record is that the interface
> is owed, not that the design changed. Tracked with the index writer in
> [#15](https://github.com/theurian/theurian/issues/15), which Milestone 6 has to
> answer for both stores at once.
>
> **Repointed on 2026-08-31
> ([#436](https://github.com/theurian/theurian/issues/436)): the sentence above
> names a tracker that is closed and a milestone that has passed.** #15 closed on
> 2026-08-10 (`66a43ae`) by wiring ADR-0024 decision 5, the withdrawal→purge
> trigger — which is neither store's write interface. What this amendment records
> as owed is still owed, and its live owner is
> [#439](https://github.com/theurian/theurian/issues/439): the single write
> interface, the Protocol-surface pin below, and the index's own contract, filed
> without a milestone. The sentence is left standing rather than rewritten
> because it is a dated record of what was believed in Milestone 5.
>
> **Corrected on 2026-08-31
> ([#436](https://github.com/theurian/theurian/issues/436)): the count above said
> *twelve*, and no revision of the port has ever published twelve.** Counted by
> the key `test_connection_claims.py` uses — `CanonicalStore`'s public members
> that declare no return value — it has published thirteen since `261eff3`
> (2026-08-01), the commit that introduced the port, and still did at `f665ecf`
> (2026-08-07), the commit that wrote this amendment; a sweep of every commit
> touching that file finds no other count. So the number is corrected in place
> rather than left standing as a record that aged: it was wrong when written, not
> stale by one. **The count above is held against the port rather than by hand**:
> `test_adr_0018_claims.py::test_the_amendment_spells_the_write_method_count_the_port_publishes`
> reads the number out of that sentence and asserts it equals what
> `canonical_store_surface.py::write_methods()` derives from the live
> `CanonicalStore` — the same derivation `test_connection_claims.py` imports, so
> the two records cannot disagree about the port — and it goes RED whether this
> record drifts or the port gains a write method. Re-derive it there rather than
> trusting this sentence.
>
> GOVERNANCE says an accepted ADR is superseded rather than edited. This is
> recorded as an amendment instead, and the judgement is deliberate: superseding
> is for a decision that turned out wrong, and nothing here decided wrongly. What
> changed is a fact about the codebase the ADR asserted and never checked. The
> Decision text above is left standing so the amendment has something to amend;
> a reader who takes point 1 at face value and stops reading gets the same wrong
> answer as before, which is the cost of this choice and the reason it is stated.

> **Corrected in the #199 unit-A follow-up
> ([#424](https://github.com/theurian/theurian/issues/424)).** Point 2 said the
> lock is taken **on the state database**. It never was: in
> `application/project_service.py`, `ProjectPaths.write_lock` is
> `.theurian/runtime/write.lock` and `ProjectPaths.database_for` puts the
> databases under `.theurian/state/`, so `write_transaction(database_path,
> lock_path)` in `infrastructure/sqlite/connection.py` flocks a file that is not
> a database. Exclusivity held the whole time — only the object the record named
> was wrong — so the clause is corrected in place rather than superseded. The
> Milestone 5 amendment above compounded it by re-reading point 2 as accurate,
> having checked that a lock is taken and not what it is taken on; the Negative
> consequence below has named both paths correctly since
> [#420](https://github.com/theurian/theurian/pull/420), so the two halves of
> this document disagreed until now.

## Consequences

### Positive

- Milestone 1 ships without async plumbing that has no caller.
- Two concurrent CLI invocations are already safe, which is a real scenario:
  an editor plugin and a terminal, or a shell script and a watcher.
- Milestone 3 changes one class rather than every write path.
- Synchronous code is easier to reason about and to test where async buys
  nothing.

### Negative

- The file lock is advisory and behaves inconsistently on some network
  filesystems. Accepted: a `.theurian/` directory on NFS is outside the
  supported configuration — the advisory lock is `.theurian/runtime/write.lock`,
  and the databases it guards are under `.theurian/state/` — and nothing
  detects that it is. No step in `application/setup_steps.py::STEPS` reads a
  filesystem type (measured 2026-08-30 at 06de58a), and `doctor` reports that
  tuple in full: `cli/setup_commands.py::doctor_command` runs `SetupService` on
  its default step set, and
  `tests/integration/test_setup_service.py::test_every_specified_step_is_reported`
  pins the reported set equal to `StepId`. No probe is planned either — building
  one is rejected rather than deferred, for want of a portable detection design,
  per the disposition recorded on #417. An operator whose project directory sits
  on NFS is therefore told nothing by `doctor`. Nothing enforces the exclusion:
  no step reads a filesystem type. The disposition behind that is recorded on
  [#417](https://github.com/theurian/theurian/issues/417).
- Two enforcement mechanisms exist between Milestone 3 and 1.0 — the queue for
  in-daemon writes and the lock for CLI writes. Both are required, because a CLI
  invocation is a separate process that a queue cannot reach.

### Neutral

- Reads need neither mechanism. WAL allows concurrent readers during a write,
  which is the property that lets search keep serving during a rebuild (NFR-4).

  > **Amended in Milestone 5. The first sentence holds; the citation of NFR-4
  > does not, and it names a requirement that is currently unmet.**
  >
  > This point said WAL is "the property that lets search keep serving during a
  > rebuild". WAL is a property of one SQLite database, and the rebuild NFR-4 is
  > about is the *retrieval index*, which since ADR-0022 lives in its own file
  > and is republished by writing a new file and swapping a pointer. No WAL
  > connection spans that: `SqliteIndexStore` holds no handle between calls, and
  > `theurian index build` reaps every build the new pointer does not name, so a
  > search racing a rebuild falls back to the substring scan rather than
  > answering from the previous build. See the amendment to ADR-0022 point 6,
  > where the guarantee was withdrawn rather than delivered.
  >
  > What this point is right about is the canonical store: a `migrate apply`
  > write does not block readers of the state database, and that is what
  > `infrastructure/sqlite/store.py` cites. **NFR-4 — "the previously published
  > index answers every query while a new build runs, zero read downtime" — is
  > owed to Milestone 6's blue/green work and is not discharged here.** It was
  > cited as satisfied by a mechanism that does not reach the artifact it is
  > about.

## Alternatives considered

| Alternative | Why rejected |
| :-- | :-- |
| Build the asyncio queue now | Async plumbing with no concurrent caller, and an execution model the CLI does not have. |
| Rely on SQLite's own locking | `busy_timeout` turns contention into a timeout error rather than serialisation, and gives no place to enforce NFR-8. |
| Allow writes from anywhere until Milestone 3 | The retrofit this ADR exists to prevent. Every call site becomes a place the guarantee can be missed. |
| A PID-file mutex | The failure mode in ADR-0002: recycled PIDs and stale files. |

## Compliance

- `tests/unit/test_migration_engine.py::test_reapplying_the_same_set_is_a_no_op`
  and `tests/integration/test_cli_commands.py::test_apply_is_idempotent` — a
  second application of the same migration set changes nothing.

Still owed, with the issue or milestone that will satisfy it:

- **Nothing holds point 1, and this section claimed a test that does not check
  it.** The bullet here read "`CanonicalStore` exposes no connection object and
  no write method outside `transaction()`; `tests/unit/test_ports.py` asserts the
  Protocol surface." On `main` the same claim was unattributed — *"a test asserts
  the Protocol surface"* — and naming a file made it more believable without
  making it true. `test_ports.py` holds `CanonicalStore` as one string in
  `EXPECTED_PORTS` and checks properties common to every port: that it is a
  runtime-checkable `Protocol`, has no implementation body, declares a member,
  annotates its methods. None of that is about *which* methods.

  Measured in Milestone 5, when this bullet was written: adding a `connection()`
  method to the `CanonicalStore` Protocol left `test_ports.py` and the whole
  suite green, so the escape hatch this ADR says cannot exist could be added and
  nothing noticed. The two counts this sentence used to quote are dropped rather
  than refreshed — they were that suite's, and re-quoting a number nobody
  re-measured is the defect this document keeps meeting.

  **Re-measured on 2026-08-31 at `4e37097`, each spelling injected into the port
  in a throwaway checkout, and two of the three now fail.**
  `-> sqlite3.Connection`, the spelling anyone reaching for this hatch would
  write, is RED under
  `test_connection_claims.py::test_the_canonical_store_port_declares_no_single_write_interface`,
  which reads it as a member returning a context manager. The unannotated
  `def connection(self)` is RED under
  `test_ports.py::test_port_methods_are_annotated[CanonicalStore]`. **`-> object`
  is the residual**: with that member on the port the suite is green at 4,394
  passed, which is the control's own result at the same commit. So this bullet's
  heading is now narrower than it reads — what nothing holds is point 1's *first*
  clause, that all writes go through one interface; the port surface its second
  clause describes is watched in two spellings out of three. Owed and
  unscheduled, tracked in
  [#439](https://github.com/theurian/theurian/issues/439) — the interface has to
  exist before a test can pin its surface. This bullet named Milestone 6 and
  [#15](https://github.com/theurian/theurian/issues/15) until 2026-08-31; #15
  closed on 2026-08-10 without shipping the interface, and #439 is where the work
  now lives ([#436](https://github.com/theurian/theurian/issues/436)).

- **Nothing runs two writers at once.** This section claimed an integration test
  running N concurrent `migrate apply` processes against one project, asserting
  serialisation, a consistent final state, and no error. No such test exists —
  the only concurrency tests in the repository are
  `tests/e2e/test_daemon_single_instance.py`'s two, which race daemon starts
  rather than writes, and no CI job runs those either
  ([#65](https://github.com/theurian/theurian/issues/65)). This is the ADR's
  central claim, so its evidence is the one that was missing: everything above
  holds that a *single* writer behaves, which is what an unserialised design
  would also do. The evidence stays bundled with the index writer below, whose
  live owner is [#439](https://github.com/theurian/theurian/issues/439); this
  bullet said Milestone 6, which has passed without the test being written
  ([#436](https://github.com/theurian/theurian/issues/436)).
- **`sqlite3` is not confined, and is already imported outside
  `infrastructure/sqlite/`.** This section claimed a lint check kept it there.
  There is none, and `cli/index_commands.py` imports it directly.
  `test_volatile_dependencies_are_confined` is the mechanism that would carry
  the rule and is parametrised over `sqlite_vec` and `mcp` only.
  [#66](https://github.com/theurian/theurian/issues/66).

- **The derived index has no single-writer contract at all** (owed,
  [#439](https://github.com/theurian/theurian/issues/439)). Everything above is
  about `CanonicalStore`. Milestone 5 gave the product a second writable SQLite
  artifact — the retrieval index — and `theurian index build` is today its only
  writer, serialised by nothing but the fact that a person runs it. Point 1's
  rule, that a guarantee behind one interface can change mechanism while a
  guarantee held by convention at each call site cannot, has not been applied to
  it.

  This becomes load-bearing rather than theoretical in Milestone 6. T-17a's root
  fix removes withdrawn rows from the index, and the shape chosen for it is a
  **single-writer incremental purge, not a purge on read** — purging on read
  would put a write on the retrieval path, and tombstones do not work here
  because what leaks is FTS5's collection statistics, which count rows a
  tombstone would leave in place. So Milestone 6 adds a second writer to a file
  that searches are reading, and this ADR is where the interface it writes
  through has to be named. Blue/green (ADR-0022) decides whether that write
  produces a new build and swaps, or mutates the published one under a lock;
  this ADR decides that there is exactly one thing allowed to do it.

  > **The blue/green half is decided:
  > [ADR-0024](0024-a-purge-is-a-build.md) — a new build and a pointer swap, and
  > nothing writes to a file `active-index.json` names.** Its point 4 is this
  > ADR's point 1 applied to the index for the first time, and it is what
  > discharges this bullet when it lands. The `CanonicalStore.transaction()`
  > half above is untouched by it and still owed.

  > **Repointed on 2026-08-31
  > ([#436](https://github.com/theurian/theurian/issues/436)): Milestone 6 has
  > passed and this bullet did not close.** The tracker it named,
  > [#15](https://github.com/theurian/theurian/issues/15), closed on 2026-08-10
  > (`66a43ae`) by wiring ADR-0024 decision 5 — the withdrawal→purge trigger — so
  > the second writer the paragraph above predicted is here and the sentence
  > calling `theurian index build` the index's only writer no longer holds:
  > `migrate apply` publishes a purged build through
  > `application/withdrawal_purge.py`. What it writes through is still not an
  > interface. There is no index write lock in the package: at `6b83be1`,
  > `git grep -nE "flock|lockf|LOCK_EX|write_lock" -- packages/theurian-core/src`
  > returns ten lines, every one of them the canonical `ProjectPaths.write_lock`
  > or the daemon's single-instance lock, and none of them in an index write
  > path. The purge records the gap in its own source — "No new index-write lock
  > is taken" — and rests on a fresh ULID and an `os.replace` instead. Owed and
  > unscheduled, tracked in
  > [#439](https://github.com/theurian/theurian/issues/439).
- **NFR-4 is not discharged**, per the amendment above. It belongs with the same
  blue/green work.
