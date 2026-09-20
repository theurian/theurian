# Daemon procedure after a schema version change

> **Superseded.** The drain step below was never implementable on a single-writer
> daemon, and no successor procedure has been written. Do not apply it.

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

The drain in step 1 is what this procedure added over a plain restart, and it is
also why it was retired: the daemon has no request-refusing mode, so step 1 was
performed by stopping the process, which makes steps 1 and 3 the same step.

Step 5 remains good advice wherever it is written down next.
