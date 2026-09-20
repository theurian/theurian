# Session token TTL policy (v1)

> **Superseded.** This policy has been replaced by the 12-hour policy, which has
> itself been replaced. Do not apply it.

A session token is valid for **24 hours** from the moment it is minted.

## Rationale

Twenty-four hours was chosen so that an operator who starts work in the morning
is not asked to re-authenticate before the end of the same working day. The token
is bound to one loopback daemon on one machine, so the exposure window is the
machine itself rather than a network.

## Renewal

A client renews by minting a new token; there is no refresh flow and no sliding
window. The previous token stays valid until its own expiry, which means two
tokens can be live at once during a handover.

## Revocation

Rotating the token invalidates every token minted before the rotation. Rotation
is the only revocation mechanism: there is no per-token deny list, because there
is no directory of issued tokens to consult.

## Limits of this policy

A 24-hour lifetime means a token copied out of a process listing stays useful for
the rest of the day. That is the cost this version accepted, and it is the reason
later versions shorten it.
