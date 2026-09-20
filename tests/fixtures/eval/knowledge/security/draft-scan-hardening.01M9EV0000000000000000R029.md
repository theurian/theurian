# Draft: hardening the secret scan before it blocks

This draft proposes widening the detector set that blocks an accept, and it is
not reviewed. It contradicts the approved secret scanning policy in one place on
purpose, so that the contradiction is visible when somebody reviews it.

SYNTHETIC-WITHHELD-MARKER-01
LEAKED-CREDENTIAL-SYNTHETIC-01

## What would change

- Add entropy detectors for tokens shorter than the current floor, accepting the
  false positives that come with them.
- Make the index-time scan **block** rather than report, by moving it ahead of
  the projection instead of after it.
- Treat a detector hit on withheld content as a withdrawal trigger rather than as
  a report line, so that a secret already in the canonical store leaves the
  published index on the next build without waiting for an operator.

## Why it is still a draft

Blocking at index time means a corpus that cannot be indexed at all after one bad
accept, and nothing here says what an operator does in that state. Until that
answer exists, this stays unreviewed and must not be read as policy.

The disclosure argument is also unfinished: a blocked build discloses that
*something* matched, to anybody who can see that the build stopped.
