# Retention exceptions

The 90-day retention policy has three standing exceptions. This document is
reclassified to restricted because the third one names the account under which
the extended records are held.

SYNTHETIC-WITHHELD-MARKER-07
LEAKED-CREDENTIAL-SYNTHETIC-07

## The exceptions

1. **Incident records** are kept for 365 days rather than 90, because an incident
   review routinely reaches back further than a quarter.
2. **Session token mint and rotation records** are kept for 180 days. The token
   values are not kept — only the moment a token was minted, its lifetime, and
   whether a rotation invalidated it.
3. **Records held under a legal hold** are kept indefinitely and are excluded
   from the daily deletion pass entirely.

## Why this is restricted rather than internal

The exceptions themselves are unremarkable. What is not is that exception 3
describes where the held records live and who can reach them, and that pair is
enough to plan against.

## Relationship to the TTL policy

A retention window is not a token lifetime. A session token expires in one hour;
the record that it existed survives for 180 days. Confusing the two produces the
question "why is the TTL 180 days", which has no answer because nothing has that
TTL.
