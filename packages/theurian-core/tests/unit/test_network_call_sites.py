"""What in the product may open an outbound connection (SEC-10, T-7, #129).

T-7 is SSRF: a hostile Git remote or an external ``$ref`` inside an ingested
document persuading Theurian to issue a request on its behalf. The threat model
records three controls for it -- a scheme allowlist, private-network rejection,
and the repository allowlist in ``.theurian/config.yaml`` -- and #129 established
that none of the three is built. What stands in for them until review ingestion
lands (Milestone 7) is not a filter but an **absence**: outside the daemon's
health probe against its own loopback port, nothing in the shipped package can
open a connection at all.

That absence is asserted outright by ``docs/security/threat-model.md`` (T-7) and
by the ``infrastructure/github/`` package docstring, and it is what every other
"the allowlist is not protecting you yet" note in #129 rests on. Until now it was
enforced by nothing. The adversarial review of #129 proved that: a mutation that
kept
``_external_refs`` recording every ref exactly as before and *added* a real
``urllib.request.urlopen`` beside the recording SURVIVED the whole suite --
2493 passed, with the parser demonstrably issuing HTTP requests against a local
listener. ``test_external_refs_are_recorded_never_fetched`` reads the recorded
output, which the mutation did not change; nothing anywhere observed the
request. This file is the missing half.

Two arms, because each has a blind spot the other covers:

- :func:`test_no_module_outside_the_daemon_health_probe_reaches_a_network_client`
  reads the shipped source and pins *who may reach a client at all*. It catches a
  fetch added anywhere in the package, including on a path no test exercises --
  but it reads names, so it cannot see one reached dynamically under a name it
  cannot resolve.
- :func:`test_parsing_a_document_with_an_external_ref_opens_no_socket` watches the
  socket layer while the parser handles a hostile ``$ref``. It catches a fetch
  however it is spelled, including through a dependency -- but only on the path
  it runs.

**The population key**, so a reader can attack the key rather than the number:
the scan walks every ``*.py`` under the *imported* ``theurian`` package (so it
scans the tree the suite runs against, not a hand-built relative path), and flags
a module for reaching any name in :data:`NETWORK_CLIENT_MODULES` or
:data:`NETWORK_CONNECT_CALLS`. Two things are deliberately outside it. **Servers
are not clients**: ``socketserver``, ``http.server``, ``starlette`` and
``uvicorn`` are unlisted, because accepting a connection is not the SSRF in T-7.
**Only the shipped package is scanned**: ``tools/``, ``plugins/`` and the tests
themselves are not, so this file may import ``socket`` to watch it.

Pure in the sense the other structural tests are: the scan parses ``.py`` files
as text, and the socket watch refuses every connection it records, so neither
opens a database, a socket, or a temporary directory.
"""

from __future__ import annotations

import ast
import errno
import json
import pathlib
import socket
from collections.abc import Callable, Iterator
from typing import Any, NoReturn, cast

import pytest

import theurian
from theurian.domain.knowledge import SourceAnchor
from theurian.infrastructure.filesystem.parsers.openapi import OpenApiParser
from theurian.infrastructure.filesystem.parsers.registry import OPENAPI

pytestmark = pytest.mark.unit

#: The package as *imported*, not a path relative to this file -- the reckoning
#: ``test_gate_call_sites.py`` uses, and for the same reason: a hand-built
#: relative path can drift from the installed package and would then scan a
#: directory with no network client in it whatever the source did.
SRC = pathlib.Path(theurian.__file__).resolve().parent

#: Modules whose import means "this file can open an outbound connection".
#:
#: Matched as a dotted prefix, so ``http.client`` covers ``from http.client
#: import HTTPSConnection`` and ``socket`` does not accidentally cover
#: ``socketserver``. Three groups:
#:
#: - the stdlib clients (``urllib.request`` and the protocol libraries);
#: - the socket layer every one of them ends up in, which is what catches a
#:   hand-rolled fetch that imports no client library at all;
#: - the third-party clients, *none* of which is a dependency today. They are
#:   listed precisely because adding one is the change this test exists to make
#:   visible: ``import httpx`` in a parser would otherwise be a one-line diff
#:   nobody reviews as a security decision.
#:
#: ``urllib.error`` is listed although it opens nothing: it has no use except
#: beside ``urllib.request``, so it names the same capability and removing it
#: from a file that keeps the fetch would be a suspicious edit, not a cleanup.
NETWORK_CLIENT_MODULES = frozenset(
    {
        "urllib.request",
        "urllib.error",
        "http.client",
        "ftplib",
        "smtplib",
        "poplib",
        "imaplib",
        "telnetlib",
        "xmlrpc.client",
        "socket",
        "ssl",
        "httpx",
        "httpcore",
        "requests",
        "urllib3",
        "aiohttp",
        "websockets",
    }
)

#: Connection openers that arrive as an attribute on a module which is itself
#: perfectly ordinary to import. ``asyncio`` and ``anyio`` are used all over the
#: daemon, so their names cannot be listed above; these four are the entry points
#: through which either one reaches a socket.
NETWORK_CONNECT_CALLS = frozenset(
    {
        "asyncio.open_connection",
        "asyncio.open_unix_connection",
        "anyio.connect_tcp",
        "anyio.connect_unix",
    }
)

#: Callables that import by string, where the string is the name that matters.
DYNAMIC_IMPORTERS = frozenset({"__import__", "import_module", "importlib.import_module"})

#: Every place in the shipped package that may open an outbound connection, as
#: ``(module path under theurian/, the listed name it reaches)``.
#:
#: Exactly one module, and its use is the single-instance health probe
#: (ADR-0002, T-13): ``probe_health`` asks ``http://127.0.0.1:7419/health``
#: whether the daemon already running is a healthy Theurian, and ``port_is_free``
#: binds the same loopback port to find out whether one is there at all. Both
#: default to ``instance.DEFAULT_HOST``, which is ``127.0.0.1``, and the daemon a
#: probe can reach is loopback-bound because ``DaemonConfig`` refuses any other
#: host (SEC-1, ``test_binding_a_non_loopback_address_is_refused`` in
#: ``tests/integration/test_daemon.py``). No ingested content reaches either
#: destination, which is what keeps this exception outside T-7.
NETWORK_CLIENT_SITES = {
    ("daemon/instance.py", "socket"),
    ("daemon/instance.py", "urllib.error"),
    ("daemon/instance.py", "urllib.request"),
}


def _dotted(node: ast.AST) -> str | None:
    """The dotted name of a ``Name``/``Attribute`` chain, else ``None``."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


def _reaches(dotted: str) -> str | None:
    """The listed network module ``dotted`` names or lives under, else ``None``.

    Prefix-matched on a dot boundary, so ``urllib.request.urlopen`` matches
    ``urllib.request`` while ``urllib.parse`` matches nothing and ``socketserver``
    is not mistaken for ``socket``.
    """
    for entry in NETWORK_CLIENT_MODULES:
        if dotted == entry or dotted.startswith(f"{entry}."):
            return entry
    return None


def _network_uses(source: str, module: str) -> Iterator[tuple[str, str]]:
    """Every network name ``source`` reaches, as ``(module, the listed name)``.

    Three arms, each a form the previous one misses:

    - **imports**, resolved to the module they reach rather than to a spelling,
      so ``from urllib import request`` and ``from urllib.request import urlopen
      as fetch`` both count;
    - **imports by string** -- ``importlib.import_module("urllib.request")`` --
      which is how a fetch hides from an import scan in one line;
    - **attribute chains** for the openers in :data:`NETWORK_CONNECT_CALLS`,
      which arrive on a module that is legitimate to import.

    Relative imports are skipped: they reach into this package, never the stdlib.
    """
    tree = ast.parse(source, filename=module)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            candidates = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
            candidates = [node.module, *(f"{node.module}.{a.name}" for a in node.names)]
        elif isinstance(node, ast.Call) and _dotted(node.func) in DYNAMIC_IMPORTERS:
            candidates = [
                a.value
                for a in node.args
                if isinstance(a, ast.Constant) and isinstance(a.value, str)
            ]
        elif isinstance(node, ast.Attribute) and _dotted(node) in NETWORK_CONNECT_CALLS:
            yield module, cast("str", _dotted(node))
            continue
        else:
            continue

        for candidate in candidates:
            entry = _reaches(candidate)
            if entry is not None:
                yield module, entry


#: One case per form the scan claims to see, and per form it claims to let past.
#:
#: Without this, the enumeration below could go green while the scanner found
#: nothing at all -- a broken extractor and a clean product read identically from
#: the outside. The negative cases are as load-bearing as the positive ones: they
#: are the reason ``urllib.parse``, which every OpenAPI ref goes through, does not
#: have to be permitted by name in :data:`NETWORK_CLIENT_SITES`.
SCANNER_CASES: tuple[tuple[str, str | None], ...] = (
    ("import urllib.request", "urllib.request"),
    ("import urllib.request as u", "urllib.request"),
    ("from urllib import request", "urllib.request"),
    ("from urllib.request import urlopen", "urllib.request"),
    ("from urllib.request import urlopen as fetch", "urllib.request"),
    ("import http.client", "http.client"),
    ("from http import client", "http.client"),
    ("from http.client import HTTPSConnection", "http.client"),
    ("import socket", "socket"),
    ("from socket import create_connection", "socket"),
    ("import httpx", "httpx"),
    ("import requests", "requests"),
    ("import aiohttp", "aiohttp"),
    ("importlib.import_module('urllib.request')", "urllib.request"),
    ('__import__("http.client")', "http.client"),
    ("reader, writer = await asyncio.open_connection(host, port)", "asyncio.open_connection"),
    ("from urllib.parse import urlparse", None),
    ("import urllib.parse", None),
    ("import socketserver", None),
    ("from http import server", None),
    ("from theurian.domain import enums", None),
    ("from . import request", None),
    ("connection = self.http.client", None),
)


@pytest.mark.parametrize(
    "source, expected",
    SCANNER_CASES,
    ids=[case[0] for case in SCANNER_CASES],
)
def test_the_network_scan_sees_each_reaching_form_and_no_other(
    source: str, expected: str | None
) -> None:
    """Guards the guard below, which is worthless the moment its scanner stops seeing.

    A structural pin fails in a way nobody notices: if ``_network_uses`` stops
    resolving imports, the enumeration keeps passing forever, and the failure
    looks exactly like a product that never added a client. So each form the
    scan claims to catch is asserted against a snippet here, and each form it
    claims to let past is asserted to produce nothing.

    The aliased and ``from``-style forms are not decoration. The same review
    that pinned ``may_surface`` showed both survive a scan that only knows the
    bare dotted spelling, and ``import_module("urllib.request")`` is a one-line
    way around an import scan that only reads ``import`` statements.
    """
    found = {entry for _, entry in _network_uses(source, "snippet.py")}

    assert found == ({expected} if expected else set()), (
        f"the network scan read `{source}` as {sorted(found)}, expected "
        f"{sorted({expected} if expected else set())}. The scanner is broken, not "
        f"the product: fix `_network_uses` before trusting a green result from "
        f"`test_no_module_outside_the_daemon_health_probe_reaches_a_network_client`, "
        f"which would keep passing with a scanner that sees nothing."
    )


def test_no_module_outside_the_daemon_health_probe_reaches_a_network_client() -> None:
    """A second module that can open a connection is a new SSRF surface (SEC-10, T-7).

    T-7's shipped control is that ingested content has nowhere to send Theurian:
    ``_external_refs`` records an external ``$ref`` and its scheme rather than
    following it, and no code near it can follow anything. The recording half is
    pinned by ``test_external_refs_are_recorded_never_fetched``; a mutation that
    kept that recording intact and added a real ``urlopen`` beside it survived
    the entire suite (#129). This is the half that kills it -- and it kills it
    wherever it lands, not only in the parser, because the property T-7 needs is
    about the package rather than about one function.

    The one permitted site is the single-instance health probe, whose
    destination is ``127.0.0.1`` and never comes from a document. See
    :data:`NETWORK_CLIENT_SITES` for why that exception is outside the threat.

    The assertion is an equality against the whole enumeration rather than a
    length or a subset, so it fails in both directions -- a client added, and the
    known one moved or deleted -- and its message names the file and the symbol.

    **What it cannot see.** It reads names: a client reached through
    ``getattr(module, "urlopen")``, a dispatch table, or a dependency that fetches
    on Theurian's behalf without Theurian naming a client all pass. It is a floor
    on the review a new outbound call gets, not a proof that one cannot exist,
    which is why the socket-level companion below runs as well.
    """
    sites = sorted(
        {
            site
            for path in sorted(SRC.rglob("*.py"))
            for site in _network_uses(
                path.read_text(encoding="utf-8"), path.relative_to(SRC).as_posix()
            )
        }
    )

    assert sites == sorted(NETWORK_CLIENT_SITES), (
        f"{len(sites)} place(s) in the shipped package can open an outbound "
        f"connection, and the pinned set has {len(NETWORK_CLIENT_SITES)}:\n"
        + "\n".join(f"  {module} :: {name}" for module, name in sites)
        + "\n\nExpected exactly:\n"
        + "\n".join(f"  {module} :: {name}" for module, name in sorted(NETWORK_CLIENT_SITES))
        + "\n\nT-7 (SSRF) has no scheme allowlist, no private-network rejection "
        "and no repository allowlist -- #129 established that all three are owed "
        "with review ingestion in Milestone 7. What stands in for them is that "
        "nothing outside the daemon's loopback health probe can issue a request "
        "at all: docs/security/threat-model.md (T-7) and the "
        "infrastructure/github/ package docstring state that outright, and every "
        "other 'the allowlist is not protecting you yet' note -- "
        "docs/architecture/review-knowledge.md, "
        "plugins/claude-code/commands/ingest.md, "
        "schemas/config/project-config.schema.json -- rests on it.\n\n"
        "If you added a client, those statements are now false. Before listing "
        "the new site here, establish: the destination cannot be chosen by "
        "ingested content; the scheme and the network it may reach are checked; "
        "and a test goes red when either stops holding. Then correct the "
        "documents above in the same change."
    )


# -- The socket layer: what the scan above cannot read -----------------------

#: The socket entry points every outbound client ends up calling, whichever
#: library it is. ``create_connection`` is what ``http.client`` (and so
#: ``urllib``, ``requests`` and ``urllib3``) uses; ``socket`` is the constructor a
#: hand-rolled or asyncio connection needs; ``getaddrinfo`` is reached even by a
#: request that never completes, which is what makes a DNS-only leak visible.
WATCHED_SOCKET_PRIMITIVES = ("create_connection", "socket", "getaddrinfo")

#: A document whose ``$ref`` targets are the two shapes T-7 cares about: an
#: absolute URL naming a host that must never be contacted, and a loopback URL --
#: the destination an SSRF payload would pick to reach a service that trusts
#: local callers, and the one destination this package's own health probe uses,
#: so it cannot be excused as "ours".
HOSTILE_REFS = (
    "https://evil.test/x.json",
    "http://127.0.0.1:7419/health",
)

ANCHOR = SourceAnchor(provider="git", source_uri="git://demo/a", file_path="openapi.yaml")


def _refuse_and_record(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Watch the socket layer: record every attempt, then refuse it.

    Recording *before* refusing is the point. The mutation that survived wrapped
    its fetch in ``except Exception: pass``, so a watch that only raised would
    have been swallowed and the test would have passed with the request made. The
    assertion reads the record, which no caller can catch.

    Refusing rather than allowing is what keeps this test off the network: a green
    run and a red run both make zero connections.
    """
    attempts: list[str] = []

    def refuse(primitive: str) -> Callable[..., NoReturn]:
        def stub(*args: object, **kwargs: object) -> NoReturn:
            attempts.append(f"socket.{primitive}{args!r}")
            raise OSError(errno.ECONNREFUSED, f"socket.{primitive} refused by the T-7 pin")

        return stub

    for primitive in WATCHED_SOCKET_PRIMITIVES:
        monkeypatch.setattr(socket, primitive, refuse(primitive))
    return attempts


def test_parsing_a_document_with_an_external_ref_opens_no_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parsing hostile input must record where it points, and go nowhere (SEC-10, T-7).

    The behavioural half of this file. Where the enumeration above reads names,
    this watches the layer every client reaches whatever it is called, so it
    catches a fetch issued through a dependency, through an alias, or through a
    name resolved at runtime -- on this path.

    Both assertions are load-bearing and the first one is not decoration: the
    fetch this test exists to catch lives inside the branch that records a ref, so
    a document whose refs were never recognised would reach nothing and the
    silence would mean nothing. Asserting the refs *were* recorded proves the
    walker reached the branch under test before asserting that the branch stayed
    home.
    """
    source = json.dumps(
        {
            "openapi": "3.1.0",
            "paths": {
                f"/p{index}": {"get": {"responses": {"200": {"$ref": ref}}}}
                for index, ref in enumerate(HOSTILE_REFS)
            },
        }
    ).encode()
    attempts = _refuse_and_record(monkeypatch)

    document = OpenApiParser().parse(source, media_type=OPENAPI, anchor=ANCHOR)

    assert document.structured is not None, "the OpenAPI parser must produce structure"
    structured = cast("dict[str, Any]", document.structured)
    recorded = {ref["ref"] for ref in structured["_index"]["externalRefs"]}
    assert recorded == set(HOSTILE_REFS), (
        f"the parser recorded {sorted(recorded)}, expected {sorted(HOSTILE_REFS)}. "
        f"The fetch this test watches for lives inside the branch that records a "
        f"ref, so a document whose refs are not recognised proves nothing by "
        f"staying silent. Fix the fixture before reading the assertion below."
    )
    assert attempts == [], (
        "parsing a document opened "
        + f"{len(attempts)} connection(s):\n"
        + "\n".join(f"  {attempt}" for attempt in attempts)
        + "\n\nAn external `$ref` is attacker-controlled: whoever writes a "
        "specification into the repository chooses the URL, and following one "
        "makes Theurian issue a request on their behalf from inside the "
        "developer's network -- T-7, whose scheme allowlist and private-network "
        "rejection are owed with Milestone 7 (#129) and enforce nothing today. "
        "`_external_refs` records the target and its scheme instead, and "
        "`docs/security/threat-model.md` cites that as T-7's one shipped control."
    )
