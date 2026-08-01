# Docker

```sh
docker build -f docker/Dockerfile -t theurian:dev .
docker run --rm theurian:dev version --json
```

Or with Compose:

```sh
THEURIAN_PROJECT=/path/to/your/repo docker compose -f docker/compose.yaml up
```

## Before you expose the port

The OSS Core binds loopback and authenticates with a local bearer token. That
model assumes **one trusted user on one machine**. Publishing the port to a
network interface leaves you with a token as the only control, over an
unencrypted connection.

A networked deployment needs TLS, OAuth 2.1 with audience and scope validation,
tenant isolation, rate limiting, and an audit log — none of which the local
daemon implements, because it does not need them and shipping half of them would
be worse than shipping none. See
[docs/architecture/cloud-ready-design.md](../docs/architecture/cloud-ready-design.md).

## Reproducibility

The build uses `uv sync --frozen`, so the committed lock file decides the
dependency set. A build that resolves differently from CI is not reproducible,
and reproducible builds are an OSS requirement (OSS-14).
