# Index rebuild runbook

The retrieval index is never written in place. A rebuild writes a whole new index
database beside the published one and swaps a pointer when the new build is
complete and verified.

## Blue/green

Two builds coexist: the one the pointer names, which every query reads, and the
one being written, which nothing reads. The swap is a single pointer write, so a
query either sees the old build in full or the new build in full, never a half
one.

A failed build leaves the pointer alone. The consequence is that a failure is a
staleness problem and not an availability problem: the daemon keeps answering
from the last good build until somebody fixes the cause and runs the rebuild
again.

## Pointer swap

The pointer is the only mutable part of the publication path. Everything else —
the index database file, its build identifier, the canonical state hash it was
built from — is written once and then read-only.

Because the pointer names a build identifier rather than a path, two builds with
the same identifier can never both be published, and a swap that raced another
swap is detectable after the fact.

## Purge on withdrawal

Withdrawing knowledge is a build, not a delete. The rows belonging to the
withdrawn revisions are removed by writing a new index without them and swapping
the pointer at it. Deleting rows out of the published index would mean writing
the file every query is reading, which is the one thing this design does not do.

When no index is published, a withdrawal purges nothing — there is no build to
remove rows from — and the next `index build` simply never includes them.
