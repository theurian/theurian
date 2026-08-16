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

Three arms, because each has a blind spot the others cover:

- :func:`test_no_module_outside_the_daemon_health_probe_reaches_a_network_client`
  reads the shipped source and pins *who may reach a client at all*. It catches a
  fetch added anywhere in the package, including on a path no test exercises --
  but it reads names, so it cannot see one reached dynamically under a name it
  cannot resolve.
- :func:`test_no_module_outside_the_git_and_service_adapters_can_spawn_a_process`
  asks the same question about the other way out of this process. ``curl``,
  ``gh`` and ``git fetch`` are network clients Theurian never has to import: the
  Milestone 7 review-ingestion adapter is most naturally built on ``gh api``, and
  that diff would contain no client module at all. A mutation doing exactly that
  -- ``subprocess.run(["curl", ...])`` beside the ref recording -- survived the
  whole suite while the arm above passed.
- :func:`test_parsing_a_hostile_document_opens_no_socket` watches the socket layer
  while *every parser the registry ships* handles a document carrying an
  attacker-chosen URL. It catches a fetch however it is spelled, including
  through a dependency and including a name assembled at runtime -- but only on
  the paths it drives, which is why the parsers come from ``default_parsers()``
  rather than from a hand-written list a new parser would never join.

**The population key**, so a reader can attack the key rather than the number:
both scans walk every ``*.py`` under the *imported* ``theurian`` package (so they
scan the tree the suite runs against, not a hand-built relative path), and flag a
module for reaching any name in :data:`NETWORK_CLIENT_MODULES`,
:data:`NETWORK_CONNECT_CALLS`, :data:`PROCESS_SPAWN_MODULES` or
:data:`PROCESS_SPAWN_CALLS`. Two things are deliberately outside it. **Servers
are not clients**: ``socketserver``, ``http.server``, ``starlette`` and
``uvicorn`` are unlisted, because accepting a connection is not the SSRF in T-7.
**Only the shipped package is scanned**: ``tools/``, ``plugins/`` and the tests
themselves are not, so this file may import ``socket`` to watch it.

**What no arm here sees.** Both structural arms read names, and a name spelled as
a string constant still counts -- ``__import__("subprocess")`` and
``__import__("_socket")`` are resolved, and each has a case in
:data:`SCANNER_CASES` or :data:`SPAWN_SCANNER_CASES`. What stays outside them is a
name that does not exist until the line runs: ``importlib.import_module("urllib"
+ ".request")``, a module read from configuration, a dispatch table, or a client
reached through ``getattr``. That residual is measured, not assumed. The
concatenating fetch is invisible to both scans and is killed by the socket watch,
in every parser; the concatenating *spawn* is killed by nothing, because ``curl``
opens its socket in another process where a patched ``socket`` module cannot
reach it, and that mutation survives the whole suite today.

So the three arms bound different things and none of them is a proof. The
structural pair bound *what may be added anywhere in the package*, by name. The
behavioural one bounds *what may happen while a document is parsed*, in this
process. A fetch that is both spelled at runtime and issued from a child process
is outside all three, and
:func:`test_no_module_outside_the_git_and_service_adapters_can_spawn_a_process`
says so where a reader deciding whether to trust this file will see it.

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
from typing import Any, Final, NoReturn, cast

import pytest

import theurian
from theurian.domain.knowledge import SourceAnchor
from theurian.domain.values import JSON, MARKDOWN, YAML, MediaType
from theurian.infrastructure.filesystem.parsers.openapi import OpenApiParser
from theurian.infrastructure.filesystem.parsers.registry import (
    OPENAPI,
    ParserRegistry,
    default_parsers,
)

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
#: ``ssl`` is listed on the same reasoning from the other end -- it opens nothing
#: either, and wrapping a socket in TLS is what a client does *after* connecting,
#: so a module that needs it is a module that connects.
#:
#: ``_socket`` is the C extension ``socket`` is a wrapper over. It is listed
#: because it is reachable without going through the listed name at all:
#: ``_socket.socket().connect(...)`` is a complete outbound connection, and it is
#: also invisible to the socket watch further down, which patches attributes on
#: the ``socket`` module. Two arms both blind to the same import is how a
#: connection ships unremarked, and a mutation using it survived the suite.
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
        "_socket",
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

#: Modules whose import means "this file can start another program".
#:
#: One entry, because ``subprocess`` is the only module in the standard library
#: whose whole purpose is that. The point of watching it in a file about SSRF is
#: that a spawned program is a network client Theurian never imports: ``curl``,
#: ``gh``, ``git fetch`` and ``ssh`` all reach the network on its behalf, and the
#: network-client scan above sees nothing at all in a diff that adds one.
PROCESS_SPAWN_MODULES = frozenset({"subprocess"})

#: Process starters that arrive as an attribute on a module every layer imports
#: for other reasons, so the module name cannot be listed above. ``os`` is
#: imported for paths and the environment throughout the package and ``asyncio``
#: runs the daemon, which is why these are matched as whole dotted calls.
#:
#: The ``exec`` family replaces this process rather than starting a child, which
#: is a strange way to fetch a URL and a normal way to lose an audit trail; it is
#: listed because it is the same capability -- run a program of my choosing --
#: and excluding it would leave a hole an allowlist cannot see.
PROCESS_SPAWN_CALLS = frozenset(
    {
        "os.system",
        "os.popen",
        "os.spawnl",
        "os.spawnle",
        "os.spawnlp",
        "os.spawnlpe",
        "os.spawnv",
        "os.spawnve",
        "os.spawnvp",
        "os.spawnvpe",
        "os.posix_spawn",
        "os.posix_spawnp",
        "os.execl",
        "os.execle",
        "os.execlp",
        "os.execlpe",
        "os.execv",
        "os.execve",
        "os.execvp",
        "os.execvpe",
        "asyncio.create_subprocess_exec",
        "asyncio.create_subprocess_shell",
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

#: Every place in the shipped package that may start another program, in the same
#: ``(module path under theurian/, the listed name it reaches)`` shape.
#:
#: Two modules, and neither takes its command from a document:
#:
#: - ``cli/context.py`` runs ``git rev-parse`` and friends to locate the working
#:   tree and read the current commit. Fixed argument vectors, no shell, and a
#:   five-second timeout (``GIT_TIMEOUT_SECONDS``, SEC-19). ``git`` is on this
#:   list for the same reason ``curl`` would be -- ``git fetch`` reaches the
#:   network -- but nothing here passes it a remote.
#: - ``infrastructure/services/runner.py`` is ``SubprocessRunner``, the seam the
#:   launchd and systemd adapters run ``launchctl`` and ``systemctl`` through.
#:   Adapter-controlled argument vectors, never user input (its own ``noqa: S603``
#:   says so), and a twenty-second timeout.
#:
#: A third entry is the change this pin exists to make visible. Milestone 7's
#: review ingestion has to reach GitHub, and ``gh api`` or ``git fetch`` is the
#: cheapest way to do it -- a diff that adds no client module, that this file's
#: network scan reads as clean, and whose destination *does* come from
#: configuration. That is T-7's repository allowlist becoming load-bearing, so
#: the change that adds the entry is the change that owes the allowlist.
PROCESS_SPAWN_SITES = {
    ("cli/context.py", "subprocess"),
    ("infrastructure/services/runner.py", "subprocess"),
}


def _dotted(node: ast.AST) -> str | None:
    """The dotted name of a ``Name``/``Attribute`` chain, else ``None``."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


def _reaches(dotted: str, watched: frozenset[str]) -> str | None:
    """The listed module ``dotted`` names or lives under, else ``None``.

    Prefix-matched on a dot boundary, so ``urllib.request.urlopen`` matches
    ``urllib.request`` while ``urllib.parse`` matches nothing and ``socketserver``
    is not mistaken for ``socket``.
    """
    for entry in watched:
        if dotted == entry or dotted.startswith(f"{entry}."):
            return entry
    return None


def _module_uses(
    source: str,
    module: str,
    *,
    watched_modules: frozenset[str],
    watched_calls: frozenset[str],
) -> Iterator[tuple[str, str]]:
    """Every watched name ``source`` reaches, as ``(module, the listed name)``.

    Four arms, each a form the ones before it miss:

    - **imports**, resolved to the module they reach rather than to a spelling,
      so ``from urllib import request`` and ``from urllib.request import urlopen
      as fetch`` both count;
    - **imports by string** -- ``importlib.import_module("urllib.request")``,
      ``__import__("subprocess")`` -- which is how a reach hides from an import
      scan in one line;
    - **attribute chains reaching a watched module**, which is the arm that sees
      ``import urllib`` at the top and ``urllib.request.urlopen(ref)`` in the
      body. Both halves are innocent on their own and neither is an import of a
      listed name, and that pair is a working fetch that survived the suite;
    - **attribute chains naming a watched call** -- ``asyncio.open_connection``,
      ``os.popen`` -- which arrive on a module that is legitimate to import.

    The chain arm over-approximates in one direction only: a *local* named after
    a watched module (``socket = accept(); socket.close()``) reads as a reach and
    would have to be listed. That fails towards demanding a review, never away
    from one, and renaming the local is the cheaper fix.

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
        elif isinstance(node, ast.Attribute):
            chain = _dotted(node)
            if chain is None:
                continue
            if chain in watched_calls:
                yield module, chain
                continue
            entry = _reaches(chain, watched_modules)
            if entry is not None:
                yield module, entry
            continue
        else:
            continue

        for candidate in candidates:
            entry = _reaches(candidate, watched_modules)
            if entry is not None:
                yield module, entry


def _network_uses(source: str, module: str) -> Iterator[tuple[str, str]]:
    """Every network-client name ``source`` reaches."""
    return _module_uses(
        source,
        module,
        watched_modules=NETWORK_CLIENT_MODULES,
        watched_calls=NETWORK_CONNECT_CALLS,
    )


def _process_spawn_uses(source: str, module: str) -> Iterator[tuple[str, str]]:
    """Every process-starting name ``source`` reaches."""
    return _module_uses(
        source,
        module,
        watched_modules=PROCESS_SPAWN_MODULES,
        watched_calls=PROCESS_SPAWN_CALLS,
    )


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
    ("import _socket", "_socket"),
    ("import httpx", "httpx"),
    ("import requests", "requests"),
    ("import aiohttp", "aiohttp"),
    ("importlib.import_module('urllib.request')", "urllib.request"),
    ('__import__("http.client")', "http.client"),
    ('__import__("_socket")', "_socket"),
    ("reader, writer = await asyncio.open_connection(host, port)", "asyncio.open_connection"),
    # The chain arm. Each of these imports a package nobody would question and
    # reaches the client through an attribute, which is the form that survived.
    ("import urllib\nurllib.request.urlopen(ref)", "urllib.request"),
    ("import http\nhttp.client.HTTPSConnection(host).request('GET', p)", "http.client"),
    ("_socket.socket().connect((host, port))", "_socket"),
    ("context = ssl.create_default_context()", "ssl"),
    ("from urllib.parse import urlparse", None),
    ("from urllib.parse import quote", None),
    ("import urllib.parse", None),
    # The negative the chain arm has to keep giving: every recorded `$ref` goes
    # through `urlparse`, so a scan that read this as a reach would force the
    # parser onto the allowlist and the whole pin would be noise.
    ("import urllib\nscheme = urllib.parse.urlparse(ref).scheme", None),
    ("import socketserver", None),
    ("from http import server", None),
    ("from theurian.domain import enums", None),
    ("from . import request", None),
    ("connection = self.http.client", None),
    ("self.socket.close()", None),
)

#: The same guard for the process-spawn scan. Without it the enumeration below
#: could go green on a scanner that resolves nothing -- and a scanner that
#: resolves nothing is indistinguishable, from the outside, from a package that
#: starts no programs.
SPAWN_SCANNER_CASES: tuple[tuple[str, str | None], ...] = (
    ("import subprocess", "subprocess"),
    ("import subprocess as sp", "subprocess"),
    ("from subprocess import run", "subprocess"),
    ("from subprocess import run as execute", "subprocess"),
    ('__import__("subprocess").run(["curl", url])', "subprocess"),
    ("importlib.import_module('subprocess')", "subprocess"),
    ("subprocess.run(['curl', '-s', url], check=False)", "subprocess"),
    ("os.system(f'curl -s {url}')", "os.system"),
    ("handle = os.popen('gh api ' + path)", "os.popen"),
    ("os.execvp('curl', ['curl', url])", "os.execvp"),
    ("os.posix_spawn('/usr/bin/curl', argv, env)", "os.posix_spawn"),
    ("await asyncio.create_subprocess_exec('gh', 'api', path)", "asyncio.create_subprocess_exec"),
    ("await asyncio.create_subprocess_shell(command)", "asyncio.create_subprocess_shell"),
    ("import os", None),
    ("import shutil", None),
    ("import asyncio", None),
    ("path = shutil.which('git')", None),
    ("home = os.environ['HOME']", None),
    ("os.fspath(path)", None),
    ("self.runner.run(['launchctl', 'list'])", None),
    ("from theurian.infrastructure.services.runner import CommandRunner", None),
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

    Neither is the chain form. ``import urllib`` beside
    ``urllib.request.urlopen(ref)`` is a working fetch in which no statement
    names a listed module, and it survived the whole suite with this file's
    enumeration passing -- the import arm resolved ``urllib``, found nothing
    listed under that name, and let it go.
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

    **What it cannot see.** It reads names -- though a name written as a string
    constant is still a name, so ``__import__("_socket")`` is caught and has a
    case above. What passes is a name that does not exist until the line runs:
    ``import_module("urllib" + ".request")``, a client reached through
    ``getattr(module, "urlopen")``, a dispatch table, or a dependency that fetches
    on Theurian's behalf without Theurian naming a client. It is a floor on the
    review a new outbound call gets, not a proof that one cannot exist, which is
    why the socket-level companion below runs as well.
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


# -- The other way out of the process ---------------------------------------


@pytest.mark.parametrize(
    "source, expected",
    SPAWN_SCANNER_CASES,
    ids=[case[0] for case in SPAWN_SCANNER_CASES],
)
def test_the_process_scan_sees_each_spawning_form_and_no_other(
    source: str, expected: str | None
) -> None:
    """Guards the enumeration below for the same reason its network twin is guarded.

    A scanner that resolves nothing and a package that starts no programs produce
    the same green. Every form the spawn scan claims to catch is asserted here
    against a snippet, and every form it claims to let past is asserted to yield
    nothing -- ``shutil.which``, ``os.environ`` and a runner injected as a
    collaborator are all ordinary, and a scan that flagged them would push the
    whole package onto the allowlist and make the pin meaningless.
    """
    found = {entry for _, entry in _process_spawn_uses(source, "snippet.py")}

    assert found == ({expected} if expected else set()), (
        f"the process scan read `{source}` as {sorted(found)}, expected "
        f"{sorted({expected} if expected else set())}. The scanner is broken, not "
        f"the product: fix `_module_uses` before trusting a green result from "
        f"`test_no_module_outside_the_git_and_service_adapters_can_spawn_a_process`."
    )


def test_no_module_outside_the_git_and_service_adapters_can_spawn_a_process() -> None:
    """A spawned program is a network client Theurian never imports (SEC-10, T-7).

    The network scan above answers "who can open a connection" by reading which
    modules a file names. It answers nothing at all about ``curl``, ``gh``,
    ``git fetch`` or ``ssh``, each of which reaches the network on Theurian's
    behalf while the diff that added it contains no client module. That is not
    hypothetical: a mutation replacing the ``urlopen`` in ``_external_refs`` with
    ``subprocess.run(["curl", ...])`` -- a real fetch, verified against a local
    listener -- survived the whole suite, with the network enumeration green.

    It is also the shape Milestone 7 is most likely to arrive in. Review
    ingestion has to reach GitHub, ``gh api`` is the obvious way, and at that
    point the destination *does* come from configuration, which is exactly when
    T-7's repository allowlist stops being owed and starts being load-bearing.

    Same equality-against-the-whole-set reckoning as its twin, so it fails when a
    site is added *and* when one is removed, and :data:`PROCESS_SPAWN_SITES`
    records why each permitted site is not the threat.

    **What it cannot see**, measured rather than guessed: a program started under
    a name assembled at runtime. ``__import__("subprocess")`` is a string constant
    and is caught -- that mutation is killed here -- but the same line spelled
    ``__import__("sub" + "process")`` survives the entire suite, and no name-based
    scan can do better. The socket watch below does not cover that gap either:
    ``curl`` opens its socket inside another process, where a patched ``socket``
    module cannot see it. A spawned fetch is therefore the one shape this file
    holds by *name* alone.

    Nor does this arm say anything about *what* an allowlisted site runs.
    ``cli/context.py`` could pass ``git`` a remote tomorrow and this test would
    stay green, which is why :data:`PROCESS_SPAWN_SITES` records the fixed
    argument vector, not the executable, as the property that matters.
    """
    sites = sorted(
        {
            site
            for path in sorted(SRC.rglob("*.py"))
            for site in _process_spawn_uses(
                path.read_text(encoding="utf-8"), path.relative_to(SRC).as_posix()
            )
        }
    )

    assert sites == sorted(PROCESS_SPAWN_SITES), (
        f"{len(sites)} place(s) in the shipped package can start another program, "
        f"and the pinned set has {len(PROCESS_SPAWN_SITES)}:\n"
        + "\n".join(f"  {module} :: {name}" for module, name in sites)
        + "\n\nExpected exactly:\n"
        + "\n".join(f"  {module} :: {name}" for module, name in sorted(PROCESS_SPAWN_SITES))
        + "\n\nA spawned program is an outbound client the network scan in this "
        "file cannot see: `curl`, `gh` and `git fetch` reach the network without "
        "Theurian importing anything. T-7's controls -- a scheme allowlist, "
        "private-network rejection, and the repository allowlist in "
        "`.theurian/config.yaml` -- are owed with review ingestion (#129) and "
        "enforce nothing today, so what stands in for them is that nothing here "
        "can reach out at all.\n\n"
        "If you added a site, establish before listing it: the argument vector "
        "is fixed by the adapter rather than taken from a document or a "
        "configuration file; the command cannot be handed a URL or a remote; "
        "there is a timeout; and a test goes red when any of those stops "
        "holding. If the command *is* meant to reach the network -- the "
        "Milestone 7 `gh api` shape -- then the repository allowlist is due in "
        "the same change, along with the documents that currently promise "
        "nothing fetches: docs/security/threat-model.md (T-7) and the "
        "infrastructure/github/ package docstring."
    )


# -- The socket layer: what the scans above cannot read ----------------------

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


# -- Every parser, not only the one that records `$ref` ----------------------

#: A hostile document per registered parser, keyed by ``parser_id``: a media type
#: that parser claims, and bytes carrying both :data:`HOSTILE_REFS` somewhere the
#: *parse* has to surface them.
#:
#: The table exists because the watch above covers one parser, and the mutation
#: that proved that was not subtle: the same dynamic ``urlopen``, moved from the
#: OpenAPI parser into ``MarkdownParser.parse`` where it fires on every non-empty
#: document, survived the whole suite. Markdown is the format most ingested
#: documents are in, so the arm covered the narrower path and missed the wider
#: one.
#:
#: A URL is not exotic input for any of these. A Markdown document links out, a
#: specification carries ``$ref``, and a YAML or JSON document has a ``url``
#: field -- each is the natural place a fetch would be added "helpfully", and
#: each is chosen by whoever writes the file into the repository.
HOSTILE_DOCUMENTS: Final[dict[str, tuple[MediaType, bytes]]] = {
    "markdown": (
        MARKDOWN,
        (
            f"# Upstream {HOSTILE_REFS[0]}\n\n"
            f"Status is published at [the health endpoint]({HOSTILE_REFS[1]}).\n\n"
            f"## Probe {HOSTILE_REFS[1]}\n"
        ).encode(),
    ),
    "openapi": (
        OPENAPI,
        json.dumps(
            {
                "openapi": "3.1.0",
                "paths": {
                    f"/p{index}": {"get": {"responses": {"200": {"$ref": ref}}}}
                    for index, ref in enumerate(HOSTILE_REFS)
                },
            }
        ).encode(),
    ),
    "yaml": (
        YAML,
        ("servers:\n" + "".join(f"  - url: {ref}\n" for ref in HOSTILE_REFS)).encode(),
    ),
    "json": (
        JSON,
        json.dumps({"servers": [{"url": ref} for ref in HOSTILE_REFS]}).encode(),
    ),
}


def _hostile_urls_in(value: object) -> set[str]:
    """Every :data:`HOSTILE_REFS` entry reachable inside a *parsed* artefact.

    Walks ``structured`` rather than ``body`` deliberately. The body is the source
    text as written, so it contains the URLs whatever the parser did with them --
    reading it would let a parser that recognised nothing pass the fixture guard,
    which is the failure the guard exists to prevent.
    """
    if isinstance(value, str):
        return {url for url in HOSTILE_REFS if url in value}
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            found |= _hostile_urls_in(key) | _hostile_urls_in(item)
    elif isinstance(value, list | tuple):
        for item in value:
            found |= _hostile_urls_in(item)
    return found


def test_every_parser_the_registry_ships_has_a_hostile_document() -> None:
    """A parser nobody drives through the socket watch is a parser that may fetch.

    The table above is written by hand and the registry is not, so the two drift
    the moment a format is added -- and the drift is silent, because a
    parametrized test over a table simply runs one case fewer. Taking the
    expected set from ``ParserRegistry().parser_ids`` makes a new adapter fail
    here until someone writes it a hostile document, which is a one-line fixture
    and a decision about what "hostile" means for that format.
    """
    shipped = set(ParserRegistry().parser_ids)

    assert set(HOSTILE_DOCUMENTS) == shipped, (
        f"the socket watch drives {sorted(HOSTILE_DOCUMENTS)} and the registry "
        f"ships {sorted(shipped)}. Every parser is a place an external reference "
        f"can arrive from a repository, and T-7's shipped control is that none of "
        f"them follows one -- a control the behavioural arm can only demonstrate "
        f"for paths it actually drives. Add the missing parser to "
        f"`HOSTILE_DOCUMENTS` with a document carrying HOSTILE_REFS in whatever "
        f"its format calls a reference."
    )


@pytest.mark.parametrize("parser_id", sorted(HOSTILE_DOCUMENTS))
def test_parsing_a_hostile_document_opens_no_socket(
    parser_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No parser follows a URL a repository handed it (SEC-10, T-7).

    The behavioural arm, widened from the one parser that records ``$ref`` to
    every parser the registry ships. Where the structural scans read names, this
    watches the layer every client ends up in, so it catches a fetch through a
    dependency, through an alias, or through a module name assembled at runtime --
    the residual both scans state they cannot see.

    Three assertions, and the two before the last one are fixture guards rather
    than the subject. A parser that does not claim the media type, or that never
    surfaces the URLs it was given, would reach nothing and its silence would mean
    nothing: proving the document went through *this* parser and that the parser
    saw the URLs is what makes the empty attempt list evidence.
    """
    media_type, source = HOSTILE_DOCUMENTS[parser_id]
    parser = {p.parser_id: p for p in default_parsers()}[parser_id]
    attempts = _refuse_and_record(monkeypatch)

    document = parser.parse(source, media_type=media_type, anchor=ANCHOR)

    assert parser.supports(media_type), (
        f"the {parser_id} parser does not claim {media_type.value}, so the registry "
        f"would never route this document to it and the run below proves nothing "
        f"about a path the product takes. Fix the table, not the assertion."
    )
    reached = _hostile_urls_in(document.structured)
    assert reached == set(HOSTILE_REFS), (
        f"the {parser_id} parser surfaced {sorted(reached)} of "
        f"{sorted(HOSTILE_REFS)} in its structure. A parser that never recognised "
        f"the URLs cannot demonstrate that it declined to follow them -- it had "
        f"nothing to follow. Fix the document in `HOSTILE_DOCUMENTS` before "
        f"reading the assertion below."
    )
    assert attempts == [], (
        f"the {parser_id} parser opened {len(attempts)} connection(s) while "
        f"parsing:\n" + "\n".join(f"  {attempt}" for attempt in attempts) + "\n\n"
        "Every URL in that document came from a repository: whoever writes a "
        "file into it chooses the link, the `$ref`, and the `url:` value, and "
        "following one makes Theurian issue a request on their behalf from "
        "inside the developer's network -- T-7, whose scheme allowlist and "
        "private-network rejection are owed with Milestone 7 (#129) and enforce "
        "nothing today. Recording a reference is the shipped control; resolving "
        "one is the vulnerability."
    )
