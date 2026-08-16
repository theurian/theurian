# ADR-0017: SQLite schema version participates in the state hash

- Status: accepted
- Date: 2026-08-02
- Deciders: Theurian maintainers
- Requirements: FR-K4, NFR-5, ADR-0004, ADR-0007

## Context

Theurian has two migration systems (ADR-0005): YAML knowledge migrations that
own canonical state, and SQL schema migrations that own the table structure of
the derived SQLite store.

The question this ADR settles is what happens to existing state databases when
the *schema* changes — a new column, a new index, a changed constraint.

The tempting answer is to migrate them in place, the way an application database
is migrated. That is wrong here for a reason specific to Theurian: a state
database is named by a hash of its inputs, and the schema version is one of
those inputs. Migrating a database in place while leaving its filename unchanged
produces a file whose name asserts one thing and whose contents are another.

## Decision

**The schema version is an input to the state hash. A schema change invalidates
every existing state database, and they are rebuilt rather than migrated.**

1. `SCHEMA_VERSION` is an integer constant in
   `theurian.infrastructure.sqlite.schema`, bumped by any change to the DDL.
2. It is hashed into the state hash along with `MIGRATION_ENGINE_VERSION`.
3. A database whose recorded `schema_version` does not match the current
   constant is not opened for reading. It is treated as a foreign artifact.
4. There is no in-place schema migration path for state databases. There is one
   recovery path, and it always works: replay the YAML migrations from Git into
   a fresh database (FR-K4).
5. Stale databases are removed by an explicit `theurian index gc`, never
   automatically. A pinned `snapshotId` may still reference one.
6. `schema_version` is also stored *inside* each database, so a mismatch is
   detectable even if the file is renamed or copied.

This is only affordable because ADR-0004 holds: SQLite is derived, and every
byte in it is reconstructible from Git-tracked migrations and content. If the
database were a record of truth, discarding it on a schema change would be data
loss. Because it is not, it is a cache miss.

## Consequences

### Positive

- A database's filename always describes its contents, including its shape.
- No schema-migration code to write, test, or get wrong — historically a rich
  source of production incidents.
- A schema change cannot leave a half-migrated database in circulation.
- Testing a schema change means testing one path (build from empty), not a
  matrix of upgrade paths from every prior version.

### Negative

- A schema bump costs every user a rebuild. Mitigated by rebuilds being
  incremental where possible, by the previous index continuing to serve
  (NFR-4), and by schema changes being rare after 1.0.
- Disk usage grows until `index gc` runs. Deliberately explicit: automatically
  deleting a state that a pinned task still references is a data-loss bug.

### Neutral

- The same reasoning will not apply to a hosted PostgreSQL deployment, where the
  store is shared and long-lived. That deployment will need real schema
  migrations, and this ADR does not constrain it.

## Alternatives considered

| Alternative | Why rejected |
| :-- | :-- |
| In-place schema migration with `PRAGMA user_version` | Requires an upgrade path per version pair, and a bug leaves a half-migrated database named as though it were valid. All the cost of application-database migrations, for a cache. |
| Exclude schema version from the state hash | Two databases with the same name and different shapes. The hash would stop identifying a state. |
| Keep a compatibility window of N schema versions | Multiplies the test matrix to buy a rebuild that takes seconds. |
| Rebuild automatically on mismatch, without asking | Surprising work at an arbitrary moment, and it can invalidate a pinned snapshot mid-task. |

## Compliance

- `tests/unit/test_state_hash.py::test_schema_version_changes_the_hash` — a
  changed `SCHEMA_VERSION` changes the state hash.
- `tests/integration/test_canonical_store.py::test_a_foreign_schema_version_is_refused`
  — a database whose recorded `schema_version` differs from the constant raises
  rather than being read. `tests/integration/test_canonical_store_corruption.py::test_an_unsupported_schema_version_is_reported_as_a_version_not_as_damage`
  covers the adjacent case: it is reported as a version, not as corruption.
- `tests/integration/test_canonical_store.py::test_schema_version_is_recorded_inside_the_database`
  — the version is stored in the database and matches the constant on creation.
- `tests/integration/test_canonical_store_corruption.py::test_a_pre_integrity_database_is_refused_unread_by_every_tool`
  — decision 3 and the rejected "compatibility window" alternative, asserted on
  all three MCP read tools at once, because `_resolve` is shared and a window
  opened for one would be open for the others. Parametrised over *every* version
  below the current one, so the rule is held across the whole range rather than at
  its far end. Landed in Milestone 6 with a bump of this constant (2 → 3,
  `project_integrity` and the composite pointer key,
  [#30](https://github.com/theurian/theurian/issues/30) PR2 and
  [#24](https://github.com/theurian/theurian/issues/24)). The exact-match rule is
  load-bearing for more than tidiness now: the #30 detector reads a *missing*
  `project_integrity` row as damage, which is only sound while no database written
  before that table existed can be opened at all.

That bump was measured against a database the previous release really wrote, not
only against a stamped version cell: `0.1.0.dev3` built a state database at
version 2, after which all three read tools of this build refuse it with the
message this ADR's decision 4 implies — rebuild rather than migrate — and one
`theurian migrate apply` produces `databaseCreated: true` under a new state hash,
after which the tools answer at version 3. The superseded file is left in place
for `theurian index gc` (decision 5).

Still owed, with the milestone that will satisfy it:

- **Nothing proves the recovery path works.** This section said "the
  `empty-db-rebuild` CI job proves the recovery path works". That job does not
  exist. The three tests above hold that a mismatched version is *detected*;
  what is unproven is the half this ADR offers as the remedy — that deleting the
  database and rebuilding from Git-tracked migrations gets the state back.
  [#64](https://github.com/theurian/theurian/issues/64) (Milestone 6).
