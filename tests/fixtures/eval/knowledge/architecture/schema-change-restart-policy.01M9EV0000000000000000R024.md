# Schema change restart policy

**After a SQLite schema version change the daemon MUST be restarted.**

## Why

The schema version participates in the canonical state hash, and the daemon
resolves that hash once, at startup, to decide which partitioned database file it
serves from. A schema change moves the hash. A daemon that keeps running after
the move holds an open handle on the file it resolved at startup and answers from
a partition that the migration path has already stopped writing to.

Nothing in the request path re-resolves the hash, so the divergence does not heal
on its own and nothing in the response says the partition is stale.

## Procedure

1. Stop the daemon.
2. Apply the migrations that carry the schema version change.
3. Run `index build`.
4. Start the daemon.

The order matters. Starting the daemon before the index build leaves a window in
which the daemon has resolved the new partition and the pointer still names an
index built from the old one.

## Scope

This applies to a **schema version** change, not to every migration. An ordinary
content migration leaves the schema version alone, leaves the state hash
partition alone, and needs no restart at all.
