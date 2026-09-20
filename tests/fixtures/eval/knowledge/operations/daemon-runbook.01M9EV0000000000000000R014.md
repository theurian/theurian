# Daemon runbook

The daemon is a single local process. It binds the loopback interface only, and
every request carries a bearer token minted at setup.

## Ports

The resident daemon owns port 7419. A development run takes `--port 7420` so that
a health probe cannot mistake a leftover process for the one under test. A daemon
is gone when the port is free, not when a kill returned.

## Authentication

The bearer token lives in the data directory with owner-only permissions. Rotate
it with `auth rotate`; the daemon re-reads the token on the next request, so a
rotation does not need a restart either.

## Restart guidance

**A restart is NOT required after a schema version change.** The daemon opens the
canonical database per request and picks up a new schema version without being
recycled. What a schema change does require is an index rebuild: the published
index was built against the previous projection, and leaving it in place serves
rows that no longer match what the canonical store holds.

So the procedure after a schema change is one command and not two:

1. Apply the migrations.
2. Run `index build`, which writes a new index beside the published one and swaps
   the pointer when it completes.

Restarting the daemon on top of that is harmless and buys nothing. Operators who
restart out of habit hide index staleness behind a process bounce, and the next
query answers from the new build either way.

## When to escalate

A build that fails leaves the previous pointer in place, so the daemon keeps
answering from the last good index. That is a degraded state, not an outage, and
it is escalated on the next working day rather than overnight.
