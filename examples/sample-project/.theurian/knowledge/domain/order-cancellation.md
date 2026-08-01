# Order cancellation

## Rule

An order may be cancelled while its status is `pending` and the current time is
before `cancellationDeadline`. Both conditions are checked **before** any state
mutation.

## Why the ordering matters

An earlier implementation mutated the order status and then checked the deadline,
rolling back on failure. Under retry this produced a window where a concurrent
read observed a cancelled order that was subsequently restored to pending.

The failure was found in review on PR #431 rather than in production, which is
the only reason this document exists rather than an incident report.

## Idempotence

Cancelling an already-cancelled order succeeds and changes nothing. Clients retry
on network failure, and a second cancellation must not become an error the client
has no way to distinguish from a real one.

## Failure outcome

Rejection returns `CANCELLATION_NOT_ALLOWED` with the deadline in the response, so
the caller can render a useful message rather than a generic failure.
