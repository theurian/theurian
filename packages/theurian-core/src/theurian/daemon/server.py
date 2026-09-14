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
#: is a write-intent one -- every tool registered today is read-side and sits far
#: below this, so the sizing is for the surface ADR-0032 designs and slice B4
#: registers (``git grep -c '@_tool(' -- packages/theurian-core/src`` answers 7,
#: 2026-09-14: ``knowledge.search``/``.get``/``.status``, ``project.list``,
#: ``review.findings``/``.search``, ``system.capabilities``) -- and what bounds
#: such a body is its *landed* form:
#: :data:`~theurian.security.paths.MAX_SOURCE_FILE_BYTES` is the byte cap on the
#: file a proposal writes (ADR-0032 decision 3).
#:
#: The ``3 *`` is **the worst ratio of wire bytes to landed UTF-8 bytes that any
#: non-control text reaches**, derived from a complete enumeration by UTF-8 byte
#: length rather than from a sample of scripts someone judged realistic. That
#: distinction is the whole correction: the sample an earlier draft used returned
#: 2.0x while ordinary Cyrillic, Greek, Hebrew and Arabic prose was expanding at
#: 3.0x, because the ratio is a property of a character's UTF-8 length and not of
#: how ordinary its script is. Measured 2026-09-14 on CPython 3.13 through
#: ``json.dumps``, per class, under both encoder families (``ensure_ascii=True``,
#: the stdlib default, and raw UTF-8, which the official Python and JS clients
#: emit):
#:
#: * **1-byte printable ASCII** -- 1.0x escaped and raw. The seven characters
#:   JSON must escape are 2.0x under *both* encoders: ``"``, ``\``, and the
#:   five two-character control escapes ``\b`` ``\t`` ``\n`` ``\f`` ``\r``.
#: * **1-byte C0 other than those five** -- 6.0x (``\u0001``) under both
#:   encoders, since JSON mandates escaping U+0000-U+001F whatever
#:   ``ensure_ascii`` is set to. Raw is illegal, so there is no cheaper form.
#: * **1-byte DEL (U+007F)** -- 6.0x escaped, because ``ensure_ascii`` escapes
#:   everything outside U+0020-U+007E; 1.0x raw, which JSON permits.
#: * **2-byte (U+0080-U+07FF -- Latin supplements, Greek, Cyrillic, Hebrew,
#:   Arabic)** -- **3.0x** escaped, a 6-byte ``\uXXXX`` over 2 landed bytes;
#:   1.0x raw.
#: * **3-byte (CJK, kana, Hangul, Thai)** -- 2.0x escaped, 1.0x raw.
#: * **4-byte astral** -- **3.0x** escaped, a 12-byte surrogate pair over 4
#:   landed bytes; 1.0x raw.
#:
#: So the worst over that population, controls excepted, is **3.0x**, reached by
#: the 2-byte class and by the astral class under ``ensure_ascii``; raw UTF-8
#: never exceeds 2.0x. Swept over every non-control code point rather than
#: sampled inside the classes, so the class boundaries are measured too.
#:
#: **The residual is recorded, not closed**, and it is now two rows of that table
#: rather than a guess. Only the control classes exceed 3.0x, both at 6.0x, and
#: either can still meet this ``413`` at a landed size the store would accept:
#:
#: * **C0 other than** ``\b`` ``\t`` ``\n`` ``\f`` ``\r`` -- no remedy,
#:   because raw C0 is illegal JSON. Accepted rather than closed: text dense
#:   enough in control characters to reach 6.0x is not what a knowledge store
#:   lands.
#: * **DEL (U+007F)** -- the remedy is to send it raw rather than
#:   ``ensure_ascii``-escaped, which measures 1.0x.
#:
#: Nothing else exceeds 3.0x. Some residual is inherent to any finite byte bound
#: at a tier with no MCP framing to refuse through.
#:
#: **The ``+ 1 MiB`` is envelope slack inside the bound, not a coverage claim.**
#: It is not derived from a population and does not assert one: ADR-0032's
#: ``description``, ``evidence.*``, ``sourceAnchors[]``, ``labels[]`` and
#: ``scopePaths[]`` carry no published ``maxLength`` until slice B4, so nothing
#: could derive it yet. What makes that safe is that the addend sits *inside* a
#: hard total -- whatever the envelope costs, a caller cannot exceed
#: :data:`MAX_REQUEST_BODY_BYTES` -- so it buys a worst-case body room for its
#: JSON-RPC frame, tool name and sibling arguments without changing what this
#: daemon will read. Sizing those fields, and the pin that would recompute this
#: addend from them, is https://github.com/theurian/theurian/issues/691.
#:
#: **The unit is landed bytes, and the schema side does not yet agree.**
#: ADR-0032's compliance table records that no code applies
#: ``MAX_SOURCE_FILE_BYTES`` on this wire path, and the ``maxLength`` slice B4
#: plans counts *code points* rather than bytes -- so a ``maxLength`` set to the
#: byte cap would admit up to four times the bytes. The landed-byte framing here
#: is the intended invariant; which unit the published schema states it in is
#: #691's to settle.
#:
#: **What the bound costs, per request.** The SDK buffers a whole body before
#: anything parses it -- ``RequestBodyLimitMiddleware`` accumulates into a
#: ``bytearray``, copies that to ``bytes``, and the JSON parse then materialises
#: a ``str`` -- so roughly 3.0x the wire bytes of Python heap is live while one
#: at-cap request is in flight. Measured 2026-09-14 at this cap: one
#: authenticated at-cap POST peaks at 75.1 MiB of Python heap (``tracemalloc``,
#: 3.00x the 26,214,400 wire bytes) and adds 50.1 MiB to ``ru_maxrss`` (2.00x --
#: lower, because part of that peak lands on pages the process already holds).
#: Both instruments are named because they answer different questions and
#: disagree by design. Raising this constant raises those figures
#: proportionally, and the derivation makes them track a *filesystem* constant:
#: raising :data:`~theurian.security.paths.MAX_SOURCE_FILE_BYTES` for a
#: reason about files raises this daemon's per-request memory ceiling by three
#: times as much. Nothing at the ``security/paths.py`` end says so, which is why
#: it is recorded here. The *number* of concurrent arrivals is not bounded
#: in-process at all -- that is T-6's recorded deferral, and the aggregate knob
#: and its figures are on
#: https://github.com/theurian/theurian/issues/26#issuecomment-5661638879.
#:
#: **It sits above** ``mcp/validation.py``'s ``MAX_PARAMS_RENDERED_CHARS``
#: (12 MiB), deliberately: a request between the two caps arrives framed and
#: meets that seam's bounded refusal -- which names the tool and the limit it
#: passed -- rather than the bare ``413`` above. That ordering is no longer what
#: makes the render budget hold, though. ``_rendered_width`` charges every leaf
#: at least what ``repr`` renders it as, so that budget is enforced by the charge
#: at *any* transport cap: moving this constant moves how many bytes get
#: buffered, never how much render work ``jsonschema`` can be made to do.
#:
#: Read ``_rendered_width``'s own table before reasoning about the two together.
#: It counts *rendered characters per code point* under ``{instance!r}``, a
#: different question from the wire table above -- *wire bytes per landed UTF-8
#: byte* under ``json.dumps`` -- and conflating the two is exactly what produced
#: the "a request's rendered width never exceeds the bytes the caller sent"
#: premise that had to be withdrawn.
MAX_REQUEST_BODY_BYTES: Final = 3 * MAX_SOURCE_FILE_BYTES + 1024 * 1024


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
