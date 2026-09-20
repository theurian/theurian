# Knowledge lifecycle statuses

Every knowledge item carries exactly one lifecycle status, and the status is what
decides whether a default search may surface it.

## The six statuses

- **draft** — written but not offered for review. A draft is the author's working
  text and may contradict approved knowledge without anybody noticing.
- **proposed** — offered for review. A proposal has a reviewer and an open
  question attached to it; nothing has been decided.
- **approved** — reviewed and adopted. This is the only status a default query
  returns, because it is the only one a reader may act on without checking who
  wrote it.
- **deprecated** — still true enough to read, no longer the way to do the thing.
  A deprecated item is kept so that a reader who finds it elsewhere can see that
  it has been retired.
- **superseded** — replaced by a named successor. The successor is recorded as a
  relation, so a reader landing on the old item can walk to the new one.
- **rejected** — considered and turned down. A rejected item is kept on purpose:
  the argument against an approach is worth more than its absence, and without it
  the same proposal returns every six months.

## What surfaces by default

A default query returns **approved** items only. The other five are reachable,
but a caller has to ask for them explicitly, and the response says that it was
asked. That asymmetry is deliberate: an agent reading knowledge it did not ask to
widen is reading decisions somebody stood behind.

Withdrawal is not a seventh status. Withdrawing an item removes it from the
served corpus entirely, and the removal is a rebuild rather than an edit.
