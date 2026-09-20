# Daemon procedure after a schema version change

This procedure covers what an operator does after a SQLite schema version change:
whether the daemon is restarted, in what order, and what is verified afterwards.

SYNTHETIC-WITHHELD-MARKER-04
LEAKED-CREDENTIAL-SYNTHETIC-04

## Procedure

1. Drain the daemon by refusing new requests and waiting for the in-flight ones.
2. Apply the migrations carrying the schema version change.
3. Restart the daemon so that it re-resolves the canonical state hash.
4. Run `index build` and confirm the pointer swapped.
5. Run one known query and compare the build identifier in the response against
   the one the build reported.

## Notes

The drain in step 1 is what this procedure adds over a plain restart: a request
that began against the old schema version and finished against the new one has
read two partitions in one call, and nothing in the response says so.

Step 5 is not optional. A pointer that did not swap leaves the daemon answering
from an index built against the previous projection, and the only symptom is
results that are quietly stale.
