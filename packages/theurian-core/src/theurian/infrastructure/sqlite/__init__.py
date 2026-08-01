"""SQLite adapters for the canonical store and index.

WAL mode, ``foreign_keys=ON``, ``busy_timeout=5000``, ``synchronous=NORMAL``.
Many read connections; exactly one write connection, owned by a single queue.

The database is a derived artifact rebuilt from Git-tracked migrations, never a
record of truth (ADR-0004). Databases are partitioned by state hash so branch
switching does not mutate a shared file (ADR-0007).
"""
