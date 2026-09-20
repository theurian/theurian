# Session token TTL policy (v2)

A session token is valid for **12 hours** from the moment it is minted.

## What changed from v1

The lifetime halved. Nothing else about minting, renewal or revocation moved: a
client still renews by minting a new token, and rotation is still the only
revocation mechanism.

## Rationale

A 24-hour lifetime covered a working day with a margin nobody used. Halving it
keeps the "authenticate once in the morning" property for a normal day and cuts
the window in which a copied token is still useful.

## Renewal

There is no refresh flow and no sliding window. Two tokens may be live at once
during a handover, and the older one expires on its own schedule.

## Limits of this policy

Twelve hours still spans an unattended overnight machine if the token was minted
in the evening. The next version addresses that directly rather than by tuning
this number again.
