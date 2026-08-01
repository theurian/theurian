# Authentication and authorization policy

## Decision

Every service-to-service call carries a signed JWT issued by the platform
identity service. Services verify the signature and the `aud` claim on every
request. No service accepts an unsigned internal call, including from within the
same cluster.

## Why

Network position is not identity. A pod inside the cluster is not automatically
trustworthy: a compromised sidecar, a misrouted ingress, or a debugging proxy
left in place all produce calls that look internal and are not.

Requiring a verifiable token on every hop means the security boundary is the
token, not the network topology -- which is a boundary we can reason about and
test.

## Consequences

- Every service needs the platform JWT library. It is small and has no transitive
  dependencies for exactly this reason.
- Local development needs a dev-mode issuer. `platform-dev-issuer` provides one;
  it will not issue tokens accepted by any non-local audience.
- Latency cost is roughly 0.3 ms per call for signature verification, measured on
  the order service. This was accepted deliberately.

## Exceptions

The health endpoint (`GET /health`) is unauthenticated so that load balancers can
probe it. It returns liveness and version only -- no business data, no
configuration.
