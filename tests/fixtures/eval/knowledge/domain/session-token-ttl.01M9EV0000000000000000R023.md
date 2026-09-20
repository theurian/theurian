# Session token TTL policy

A session token is valid for **1 hour** from the moment it is minted. This is the
current policy.

## Rationale

The daemon is local and the client is a process on the same machine, so minting a
fresh token is cheap and needs no human in the loop. Once renewal costs nothing,
the only thing a long lifetime buys is a longer window for a token that has been
copied somewhere it should not be.

One hour is short enough that an unattended machine holds no useful token by
morning, and long enough that a single agent session never renews mid-task.

## Renewal

A client mints a new token when the current one is within five minutes of expiry.
There is still no refresh flow and no sliding window: renewal is a new mint, and
the previous token expires on its own schedule.

## Revocation

Rotation invalidates every token minted before it, and remains the only
revocation mechanism. With a one-hour lifetime, rotation and expiry converge
quickly enough that a deny list would buy almost nothing.

## Relationship to earlier policies

This policy supersedes the 12-hour policy, which superseded the 24-hour one. The
earlier documents are kept for the record and must not be read as current.
