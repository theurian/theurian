# Data retention policy

Operational records are retained for **90 days** and then deleted.

## What the 90 days covers

- Request logs written by the daemon, including the method name, the duration and
  the outcome. Query text is not written to them.
- Index build records: the build identifier, the canonical state hash it was
  built from, and the secret-scan report produced alongside it.
- Proposal drafts that were never accepted.

## What it does not cover

Canonical knowledge is not operational data and has no retention clock. An
approved item stays until somebody withdraws it, and a withdrawal is a deliberate
migration rather than the expiry of a timer.

Superseded and rejected items are likewise kept indefinitely. The whole point of
keeping a rejected approach is that the argument outlives the decision.

## Deletion

Deletion runs as part of the daily maintenance pass and removes whole records
rather than redacting fields. A record that is 90 days old at the start of the
pass is deleted in that pass; there is no grace period and no archive tier.

## Exceptions

There are none in the default configuration. An operator who needs a longer
window changes the retention setting, and the change is visible in the
configuration diff rather than in a per-record flag.
