"""ADR-0037 decision 6: the OKF import path fetches nothing (T-3, T-7).

Decision 6 says it in prose: *"The input is a local directory the operator
names, and the importer fetches nothing. No URL, no network call, no
credential."* This file is the structural half of that claim -- who *could*
reach a network client at all, by walking the theurian-internal import
closure the way ``tests/unit/test_network_call_sites.py`` already does for
the whole package.

**The population key.** Two entry points: :data:`ENTRY_POINTS`'s
``application/okf_import.py``, the service the ADR describes, and
``cli/okf_commands.py``, its composition root. Each closure is every module
under ``theurian/`` either reaches by import, transitively, over the whole
syntax tree (so a deferred import inside a function counts). The watched
vocabulary is :data:`NETWORK_CLIENT_MODULES`, the same set
``test_network_call_sites.py`` uses for T-7: every stdlib and third-party
network client, plus the socket layer they all end up in.

**One sanctioned reach, not zero, for the CLI entry point.**
``cli/okf_commands.py`` imports from ``cli/commands.py`` for `_emit`,
`_fail` and `_require_project` -- the same composition root every CLI command
shares -- and that module pulls in ``daemon/instance.py`` for other commands'
sake. ``daemon/instance.py`` is the single-instance health probe against
``127.0.0.1`` (ADR-0002, T-13), already recorded as the one exception in
``test_network_call_sites.py``'s own ``NETWORK_CLIENT_SITES``. It predates
this import, has nothing to do with a bundle's content, and asserting it away
would either report a false CRITICAL on every future CLI command or force
this file to invent a second, narrower closure -- so it is named as the one
admitted reach rather than filtered out. ``application/okf_import.py``'s own
closure, the service the ADR's prose actually describes, admits none at all.

Deliberately narrower than the whole-package scan: it answers "does *this*
entry point's own reach include a network client", not "can this package ever
open one" -- ``test_network_call_sites.py`` already answers that question for
the shipped package as a whole, this file's job is only to say that
importing the OKF path adds nothing outside what the CLI already carries.

**The population key is name-based, like its sibling**, so it does not see a
module reached under a string assembled at runtime; the parametrized cases
below prove what it *does* see, which is the residual any such scan states.
"""

from __future__ import annotations

import ast
import pathlib
from typing import Final

import pytest

import theurian

pytestmark = pytest.mark.unit

SRC: Final = pathlib.Path(theurian.__file__).resolve().parent

ENTRY_POINTS: Final[tuple[str, str]] = ("application/okf_import.py", "cli/okf_commands.py")

#: Same vocabulary as `test_network_call_sites.py::NETWORK_CLIENT_MODULES` --
#: every network client this package could reach, matched as a dotted prefix.
NETWORK_CLIENT_MODULES: Final = frozenset(
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

#: The one admitted reach: `daemon/instance.py`'s loopback health probe,
#: pulled in transitively by `cli/okf_commands.py` through `cli/commands.py`'s
#: shared composition root. See the module docstring for why it is named
#: rather than excluded.
_HEALTH_PROBE_EXCEPTION: Final = frozenset(
    {
        ("daemon/instance.py", "socket"),
        ("daemon/instance.py", "urllib.error"),
        ("daemon/instance.py", "urllib.request"),
    }
)


def _module_path(dotted: str) -> str | None:
    """``theurian.mcp.tools`` -> ``mcp/tools.py``, when such a module exists."""
    relative = dotted.removeprefix("theurian.").replace(".", "/")
    if (SRC / f"{relative}.py").is_file():
        return f"{relative}.py"
    if (SRC / relative / "__init__.py").is_file():
        return f"{relative}/__init__.py"
    return None


def _names_imported(tree: ast.AST) -> list[str]:
    """Every dotted name one ``import``/``from`` statement in *tree* binds."""
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and not node.level:
            names.append(node.module or "")
            names.extend(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def _import_closure(entry: str) -> frozenset[str]:
    """Every module under ``theurian/`` that *entry* reaches by import, transitively."""
    seen: set[str] = set()
    pending = [entry]
    while pending:
        module = pending.pop()
        if module in seen:
            continue
        seen.add(module)
        tree = ast.parse((SRC / module).read_text(encoding="utf-8"), filename=module)
        for name in _names_imported(tree):
            if name.startswith("theurian") and (found := _module_path(name)) is not None:
                pending.append(found)
    return frozenset(seen)


def _network_uses(source: str, module: str) -> frozenset[tuple[str, str]]:
    """Every ``(module, watched network module)`` pair *source*'s own text reaches.

    Only direct imports -- the caller sums this over a whole closure to see
    transitively reachable ones.
    """
    tree = ast.parse(source, filename=module)
    found: set[tuple[str, str]] = set()
    for name in _names_imported(tree):
        for watched in NETWORK_CLIENT_MODULES:
            if name == watched or name.startswith(f"{watched}."):
                found.add((module, watched))
    return frozenset(found)


def _network_sites_in(closure: frozenset[str]) -> frozenset[tuple[str, str]]:
    sites: set[tuple[str, str]] = set()
    for module in closure:
        sites |= _network_uses((SRC / module).read_text(encoding="utf-8"), module)
    return frozenset(sites)


def test_the_okf_import_services_own_closure_reaches_no_network_client() -> None:
    """`application/okf_import.py`'s own module graph -- the ADR's own subject.

    Not the CLI wrapper: this is the service `OkfImportService.import_bundle`
    itself pulls in, and the ADR's "fetches nothing" is a claim about exactly
    this path.
    """
    closure = _import_closure("application/okf_import.py")

    assert "application/okf_import.py" in closure and len(closure) >= 20, (
        f"the import walk reached only {len(closure)} module(s) from "
        "application/okf_import.py, and it reached 48 when this pin was written "
        "(2026-09-25). A walk that resolves almost nothing reports no network "
        "reach for the same reason a package with none does -- it has proven "
        "nothing."
    )

    sites = _network_sites_in(closure)
    assert not sites, (
        f"application/okf_import.py's own closure reaches a network client: "
        f"{sorted(sites)}. Decision 6 is explicit -- the importer fetches "
        f"nothing, no URL and no network call -- so any module this service "
        f"pulls in reaching one is a new fetch surface the ADR does not own."
    )


def test_the_okf_cli_entrypoint_reaches_network_only_through_the_recorded_health_probe() -> None:
    """`cli/okf_commands.py`'s closure, admitting the one pre-existing reach.

    Equality rather than emptiness, because `cli/commands.py`'s shared
    composition root pulls in the health probe every CLI command inherits --
    see the module docstring. The equality still fails on an *addition*: a
    second reach here is a new one this import brought in, not the one
    already there before it existed.
    """
    closure = _import_closure("cli/okf_commands.py")

    assert "cli/okf_commands.py" in closure and len(closure) >= 50, (
        f"the import walk reached only {len(closure)} module(s) from "
        "cli/okf_commands.py, and it reached 130 when this pin was written "
        "(2026-09-25). A walk that resolves almost nothing reports no network "
        "reach for the same reason a package with none does -- it has proven "
        "nothing."
    )

    sites = _network_sites_in(closure)
    assert sites == _HEALTH_PROBE_EXCEPTION, (
        f"cli/okf_commands.py's closure reaches {sorted(sites)}; the recorded "
        f"exception is {sorted(_HEALTH_PROBE_EXCEPTION)}.\n\n"
        "ADDED: a network client reachable from the OKF import CLI command that "
        "was not there before. Decision 6 says this path fetches nothing, so "
        "establish what the new reach is for before adding it here.\n\n"
        "MISSING: the recorded health-probe exception is gone, which most "
        "likely means cli/commands.py stopped composing daemon/instance.py -- "
        "correct this file's set to match rather than widening it to keep the "
        "assertion passing."
    )


#: One case per form the scan claims to see, and one per form it lets past --
#: the same guard-the-guard reasoning `test_network_call_sites.py` uses: a
#: scanner that resolves nothing and a module with no network reach produce
#: the same green, so each claim needs its own case here.
_SCANNER_CASES: Final[tuple[tuple[str, str | None], ...]] = (
    ("import socket", "socket"),
    ("import urllib.request", "urllib.request"),
    ("from urllib.request import urlopen", "urllib.request"),
    ("from urllib.request import urlopen as fetch", "urllib.request"),
    ("import httpx", "httpx"),
    ("import requests", "requests"),
    ("from theurian.domain import enums", None),
    ("import subprocess", None),
    ("from urllib.parse import urlparse", None),
)


@pytest.mark.parametrize("source, expected", _SCANNER_CASES, ids=[c[0] for c in _SCANNER_CASES])
def test_the_scan_recognises_each_reaching_form_and_no_other(
    source: str, expected: str | None
) -> None:
    """Guards the guard: a scanner seeing nothing and a clean module read alike.

    Without this, `test_the_okf_import_services_own_closure_reaches_no_network_client`
    could pass forever on a broken `_network_uses` -- the positive control this
    pin's own claim needs. `subprocess` and `urllib.parse` are asserted to pass
    *unflagged*, because they are not this file's vocabulary (a spawned process
    is `test_network_call_sites.py`'s concern, and `urlparse` opens nothing).
    """
    found = {name for _, name in _network_uses(source, "snippet.py")}

    assert found == ({expected} if expected else set()), (
        f"the scan read `{source}` as {sorted(found)}, expected "
        f"{sorted({expected} if expected else set())}. Fix `_network_uses` "
        f"before trusting a green result from the closure tests above, which "
        f"would keep passing with a scanner that sees nothing."
    )
