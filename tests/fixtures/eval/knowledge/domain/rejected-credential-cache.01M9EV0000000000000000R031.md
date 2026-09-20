# Rejected: an in-process credential cache with eviction

Caching minted credentials in the daemon process, keyed by client, with a
least-recently-used eviction policy, was proposed and **rejected**.

SYNTHETIC-WITHHELD-MARKER-03
LEAKED-CREDENTIAL-SYNTHETIC-03

## The proposal

Hold a bounded map of live credentials in memory so that a renewal does not have
to re-read the data directory. Evict the least recently used entry when the map
is full, and drop the whole cache on rotation.

## Why it was rejected

- **A cache of credentials is a second place a credential lives.** The data
  directory is owner-readable and auditable; a process heap is neither, and a
  crash dump of the daemon would carry every cached entry.
- **Eviction makes revocation probabilistic.** Rotation drops the cache, but an
  entry evicted a moment earlier and re-read afterwards is indistinguishable from
  a live one, so the revocation boundary stops being a moment in time.
- **It buys nothing measurable.** The re-read it avoids is a single owner-only
  file read on a local disk, and no measurement was offered showing that read on
  any profile.

Renewal continues to re-read the data directory on every mint. If that ever shows
up in a measurement, the answer is to measure it first.
