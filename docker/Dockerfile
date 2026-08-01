# Theurian Core -- reproducible container build.
#
# The daemon binds 127.0.0.1 inside the container. Publishing the port to a host
# interface is a deliberate act by whoever runs it, and it steps outside the OSS
# Core's security model (SEC-1): loopback-only assumes one trusted user.
# See docs/security/local-mcp.md before doing it.

FROM python:3.13-slim-bookworm AS builder

COPY --from=ghcr.io/astral-sh/uv:0.7.2 /uv /usr/local/bin/uv

WORKDIR /build
COPY pyproject.toml uv.lock ./
COPY packages/theurian-core/ packages/theurian-core/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

# --frozen: the lock file decides, not the resolver. A build that silently
# resolves differently from CI is not reproducible (ADR-0014, OSS-14).
RUN uv sync --frozen --no-dev --package theurian --extra daemon --extra vector

FROM python:3.13-slim-bookworm

RUN useradd --create-home --uid 10001 theurian
USER theurian
WORKDIR /home/theurian

COPY --from=builder --chown=theurian:theurian /build/.venv /opt/theurian/.venv
ENV PATH="/opt/theurian/.venv/bin:$PATH" \
    THEURIAN_DATA_DIR=/home/theurian/.theurian

EXPOSE 7419

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:7419/health', timeout=2).status == 200 else 1)"]

ENTRYPOINT ["theurian"]
CMD ["daemon", "start", "--foreground"]
