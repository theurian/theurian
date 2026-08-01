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
2. Milestone 1 enforces exclusivity with an **OS advisory file lock** on the
   state database, acquired for the duration of a write transaction. Two
   concurrent `theurian migrate apply` invocations serialise; the loser waits,
   then observes the other's work and becomes a no-op (idempotence, FR-K8).
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
  filesystems. Accepted: a `.theurian/state/` directory on NFS is already
  outside the supported configuration, and `doctor` will warn about it.
- Two enforcement mechanisms exist between Milestone 3 and 1.0 — the queue for
  in-daemon writes and the lock for CLI writes. Both are required, because a CLI
  invocation is a separate process that a queue cannot reach.

### Neutral

- Reads need neither mechanism. WAL allows concurrent readers during a write,
  which is the property that lets search keep serving during a rebuild (NFR-4).

## Alternatives considered

| Alternative | Why rejected |
| :-- | :-- |
| Build the asyncio queue now | Async plumbing with no concurrent caller, and an execution model the CLI does not have. |
| Rely on SQLite's own locking | `busy_timeout` turns contention into a timeout error rather than serialisation, and gives no place to enforce NFR-8. |
| Allow writes from anywhere until Milestone 3 | The retrofit this ADR exists to prevent. Every call site becomes a place the guarantee can be missed. |
| A PID-file mutex | The failure mode in ADR-0002: recycled PIDs and stale files. |

## Compliance

- `CanonicalStore` exposes no connection object and no write method outside
  `transaction()`; a test asserts the Protocol surface.
- An integration test runs N concurrent `migrate apply` processes against one
  project and asserts serialisation, a consistent final state, and no error.
- A test asserts a second application of the same migration set is a no-op.
- A lint check keeps `import sqlite3` inside `infrastructure/sqlite/`.
