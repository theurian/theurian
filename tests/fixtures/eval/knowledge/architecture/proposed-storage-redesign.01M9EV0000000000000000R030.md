# Proposed: a single-file storage redesign

This proposal is under review and has not been adopted. It would replace the
current database design principles — a derived canonical store, state-hash
partitioning, schema versioning inside the hash, and an index in its own database
file — with one file per project.

SYNTHETIC-WITHHELD-MARKER-02
LEAKED-CREDENTIAL-SYNTHETIC-02

## The proposal

- One SQLite database holding both canonical knowledge and the retrieval index,
  so a query never opens two files.
- Drop state-hash partitioning; key branch isolation on a column instead of on a
  filename.
- Keep the schema version as a table row rather than as a participant in the
  canonical state hash.

## Why it is only proposed

Merging the index into the canonical database contradicts the rule that a
published index is never written: a rebuild would have to write the file every
query is reading. The proposal has no answer to that yet, and the reviewer has
asked for one before this goes further.

Partitioning by column also makes a branch switch a query-time concern rather
than a file-open concern, which moves an isolation guarantee out of the operating
system and into application code.
