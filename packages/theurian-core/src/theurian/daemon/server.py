"""The Theurian daemon (ADR-0002, ADR-0011).

A Starlette application exposing:

- ``GET /health`` — unauthenticated, liveness and identity only
- ``/mcp`` — the MCP server over Streamable HTTP, bearer-authenticated

Bearer authentication is a Starlette middleware rather than the SDK's
``AuthSettings``, which requires an ``issuer_url`` and a ``resource_server_url``
and would drag OAuth resource-metadata endpoints into a single-user loopback
daemon. ADR-0011 rejected full OAuth 2.1 locally for exactly that reason.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Final, override

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route

from theurian import __protocol_version__, __version__
from theurian.security.paths import MAX_SOURCE_FILE_BYTES
from theurian.security.tokens import (
    AUTHORIZATION_HEADER,
    extract_bearer,
    verify_token,
)

#: Paths reachable without a credential. Deliberately a fixed set rather than a
#: prefix match: a prefix would let `/healthcheck-admin` through by accident.
UNAUTHENTICATED_PATHS: Final = frozenset({"/health"})

#: How many bytes of one HTTP request body this daemon reads before answering
#: ``413``. This is the transport tier's own denial-of-service bound, and until
#: #669 it was a number nobody here chose: ``build_app`` called
#: ``streamable_http_app`` without ``max_request_body_size``, so the SDK's
#: ``DEFAULT_MAX_REQUEST_BODY_SIZE`` applied -- 4 MiB,
#: ``mcp/server/transport_security.py``, measured 2026-09-14 against
#: ``mcp==2.1.1``. What a caller past this bound gets is a bare ``413 Request
#: body too large`` (22 bytes, measured), emitted by the SDK's
#: ``RequestBodyLimitMiddleware`` before any MCP framing exists -- so it names no
#: tool, carries no remedy, and has no refusal shape. That is inherent to the
#: tier, which is why the bound is set where a legitimate request does not meet
#: it rather than left at a default.
#:
#: **Derived, not chosen.** The largest legitimate body this daemon is sized for
#: is a write-intent one -- the seven tools registered today are all read-side
#: and sit far below this, so the sizing is for the surface ADR-0032 designs and
#: slice B4 registers -- and what bounds such a body is its *landed* form:
#: :data:`~theurian.security.paths.MAX_SOURCE_FILE_BYTES` is the byte cap on the
#: file a proposal writes (ADR-0032 decision 3). The ``2 *`` covers every
#: realistic JSON wire form of such a body, measured 2026-09-14 on CPython 3.13
#: as wire bytes over landed bytes: text whose every character takes a two-byte
#: escape -- a quote, a backslash, a newline, a tab -- is exactly 2.0x, and
#: ``ensure_ascii``-escaped CJK (3 UTF-8 bytes becoming a 6-byte ``\uXXXX``) is
#: exactly 2.0x as well. The ``+ 1 MiB`` is headroom for the JSON-RPC envelope
#: around the body: the frame, the tool name, and the call's other arguments.
#:
#: **The residual is recorded, not closed.** Two encodings expand past 2.0x and
#: can still meet this ``413`` at a landed size the store would accept: text
#: dense in C0 control characters escapes at 6.0x (one byte becoming a 6-byte
#: ``\u0001``), and ``ensure_ascii``-escaped astral characters at 3.0x (4 UTF-8
#: bytes becoming a 12-byte surrogate pair). Some such residual is inherent to
#: any finite byte bound at a tier with no MCP framing to refuse through. For
#: the astral case the remedy is to send the body as raw UTF-8 instead of
#: ``ensure_ascii``-escaped, which measures 1.0x; the control-character case has
#: none, because JSON requires C0 escaping whatever the encoder is set to -- and
#: a body that is mostly control characters is not the text a knowledge store
#: lands.
#:
#: **It sits above** ``mcp/validation.py``'s ``MAX_PARAMS_RENDERED_CHARS``
#: (12 MiB), which is the reconciliation #669 asked for and is deliberate. A
#: request between the two caps now arrives framed and meets that seam's
#: bounded refusal -- which names the tool and the limit it passed -- rather
#: than the bare ``413`` above.
MAX_REQUEST_BODY_BYTES: Final = 2 * MAX_SOURCE_FILE_BYTES + 1024 * 1024


#: Origins the browser may present. Anything else is a cross-origin attempt at a
#: loopback service, which is DNS rebinding (SEC-2, T-2).
def _allowed(host: str, port: int) -> TransportSecuritySettings:
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[f"{host}:{port}", f"localhost:{port}", f"[::1]:{port}"],
        allowed_origins=[
            f"http://{host}:{port}",
            f"http://localhost:{port}",
            f"http://[::1]:{port}",
        ],
    )


@dataclass(frozen=True, slots=True)
class DaemonConfig:
    """Everything the daemon needs to serve."""

    token: str
    data_dir: Path
    host: str = "127.0.0.1"
    port: int = 7419
    started_at: str = ""

    def __post_init__(self) -> None:
        # Binding a non-loopback interface is not a supported configuration of
        # the OSS Core (SEC-1). A networked deployment needs TLS, OAuth 2.1,
        # audience validation, and tenant isolation -- none of which this
        # daemon implements, and shipping half of them would be worse than
        # shipping none.
        if self.host not in {"127.0.0.1", "localhost", "::1"}:
            msg = (
                f"Refusing to bind {self.host}. The OSS daemon is loopback-only; "
                f"see docs/architecture/cloud-ready-design.md for the hosted path."
            )
            raise ValueError(msg)


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Requires a bearer token on everything except ``/health``.

    ``/health`` is exempt so that the ``SessionStart`` hook and the
    single-instance probe can run without a credential. It returns liveness and
    version only -- nothing about projects or knowledge (ADR-0011).
    """

    def __init__(self, app: object, token: str) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._token = token

    @override
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.url.path in UNAUTHENTICATED_PATHS:
            return await call_next(request)

        presented = extract_bearer(request.headers.get(AUTHORIZATION_HEADER))
        if presented is None or not verify_token(presented, self._token):
            # The message names the fix, because "401 Unauthorized" on a tool
            # you just installed is otherwise a mystery. It reveals nothing: the
            # variable name is public, the token is not.
            return JSONResponse(
                {
                    "error": "unauthorized",
                    "detail": (
                        "Theurian requires a bearer token. Claude Code expands "
                        "${THEURIAN_MCP_TOKEN} in its MCP configuration; if that "
                        "variable is unset the literal text is sent instead. "
                        "Run `theurian doctor` for the fix."
                    ),
                },
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )

        return await call_next(request)


def build_app(config: DaemonConfig, mcp: MCPServer) -> Starlette:
    """Assemble the ASGI application.

    The MCP app is mounted rather than used directly so that ``/health`` can sit
    beside it outside the authenticated path, and so the lifespan can own the
    session manager -- mounting disables the SDK's own lifespan, and forgetting
    to run the session manager makes every MCP request fail.
    """

    async def health(_: Request) -> Response:
        """Liveness and identity. Deliberately uninformative.

        Enough for a probe to decide whether to start a daemon and whether it is
        *this* daemon; nothing about projects or knowledge.
        """
        return JSONResponse(
            {
                "status": "ok",
                "version": __version__,
                "protocolVersion": __protocol_version__,
                "dataDir": str(config.data_dir),
                "startedAt": config.started_at,
            }
        )

    # The MCP app keeps the full `/mcp` path and is mounted at the root, rather
    # than being mounted *at* `/mcp` with an inner path of `/`. The latter makes
    # Starlette answer `/mcp` with a 307 to `/mcp/`, and a redirected POST loses
    # its body in some clients -- so the documented endpoint would work for some
    # callers and silently fail for others.
    # `max_request_body_size` is passed rather than left to default: omitting it
    # took the SDK's own 4 MiB, a bound this project never chose and never
    # recorded (#669). :data:`MAX_REQUEST_BODY_BYTES` carries what it is derived
    # from and what it leaves open.
    mcp_app = mcp.streamable_http_app(
        streamable_http_path="/mcp",
        host=config.host,
        max_request_body_size=MAX_REQUEST_BODY_BYTES,
        transport_security=_allowed(config.host, config.port),
    )

    @asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        async with mcp.session_manager.run():
            yield

    return Starlette(
        routes=[
            # Ordered: `/health` must match before the catch-all mount.
            Route("/health", health, methods=["GET"]),
            Mount("/", app=mcp_app),
        ],
        middleware=[Middleware(BearerAuthMiddleware, token=config.token)],
        lifespan=lifespan,
    )
