"""Every place a ``--json`` CLI command reaches ``resolve_context`` (issue #205).

``resolve_context`` loads and validates every migration under a project's
``.theurian/migrations/``, including reading each ``upsertRevision``'s
``contentFile`` from disk. A raw ``OSError`` from that read used to escape as a
bare ``FileNotFoundError``: none of the codebase's ``except TheurianError`` /
``except MigrationError`` guards catch a type that is not a ``TheurianError``,
so it reached Typer as a Rich traceback -- exit 1, empty stdout, no ``{error,
remedy}`` payload, even under ``--json`` (CP-2). Reproduced against both
``migrate validate`` and ``init`` re-run on a project whose migrations already
held one.

``MigrationContentUnreadableError`` -- raised at the one place the read
happens, in ``infrastructure/filesystem/migration_loader.py`` -- closes the
class at its root: every caller already guards ``TheurianError``, so a
``TheurianError`` subclass is caught wherever ``resolve_context`` is already
caught, with no per-command patch needed. Its sibling
``MigrationFileUnreadableError`` closes the identical escape one call site
over, for the migration file's own read rather than a `contentFile` it names
(a `chmod 000`'d migration crashed the same way). Both are members of the same
class for the purposes of this file: what it pins is the *reaching* commands,
not which subclass the loader happens to raise. What neither fix alone closes
is a *new* command reaching ``resolve_context`` (or ``_require_project``,
which wraps it) without going through one of the two guarded shapes below --
that is what this file pins, the ``_reclaim`` docstring's CP-2 precedent for
this shape (``cli/index_commands.py``).

Two things are pinned, because they are two separate ways the class could
reopen:

- **The population.** Every ``(module, enclosing function)`` that calls
  ``resolve_context`` or ``_require_project`` -- an equality assertion against
  the whole shipped source, the same shape as
  ``tests/unit/test_gate_call_sites.py``, so a call site added anywhere is a
  decision someone records here, not a diff nobody notices.
- **The guard.** ``resolve_context`` is called directly (not through
  ``_require_project``) from four places. Each must end its own ``except``
  chain in ``except TheurianError``, the one clause broad enough to catch
  every present and future ``TheurianError`` subclass the loader might raise.
  Narrower than that is exactly the gap ``FileNotFoundError`` slipped through,
  because ``FileNotFoundError`` was never a ``TheurianError`` at all -- a
  direct caller that only named ``MigrationError`` and its known subclasses
  would still miss a *new* ``TheurianError`` subclass the loader starts
  raising later.

What this cannot see, same as its sibling: a name it cannot resolve
statically -- ``getattr``, a dispatch table, a re-export under a third name.
It is a floor on the review a new call site gets, not a proof that an
unguarded one cannot exist.
"""

from __future__ import annotations

import ast
import pathlib
from collections.abc import Iterator

import pytest

import theurian
from theurian.domain.errors import (
    MigrationContentUnreadableError,
    MigrationFileUnreadableError,
    TheurianError,
)

pytestmark = pytest.mark.unit

#: The package as *imported*, not a path relative to this file -- see
#: ``test_gate_call_sites.py`` for why: a hand-built relative path can drift
#: from the installed package and would then scan a directory with no call
#: sites in it at all.
SRC = pathlib.Path(theurian.__file__).resolve().parent

#: The two names that reach the migration loader. ``_require_project`` wraps
#: ``resolve_context``, so a command that calls either is a member of the
#: class this file is about.
CALLEES = {"resolve_context", "_require_project"}

#: Every ``(module path under theurian/, enclosing function)`` that calls
#: ``resolve_context`` or ``_require_project``, read off the shipped source
#: with:
#: ``grep -rn "resolve_context(\\|_require_project(" src/theurian/cli/*.py``
#: on 2026-08-17 against ``fix/205-json-crash-on-unresolvable-content-file``.
RESOLVE_CONTEXT_CALL_SITES = {
    ("cli/commands.py", "init_command"),
    ("cli/commands.py", "project_register"),
    ("cli/commands.py", "project_status"),
    ("cli/commands.py", "_require_project"),
    ("cli/commands.py", "migrate_status"),
    ("cli/commands.py", "migrate_validate"),
    ("cli/commands.py", "migrate_apply"),
    ("cli/commands.py", "ingest_command"),
    ("cli/index_commands.py", "index_build"),
    ("cli/index_commands.py", "index_status"),
    ("cli/index_commands.py", "index_gc"),
}

#: Of the set above, the ones that call ``resolve_context`` directly rather
#: than through ``_require_project``. These four are the only places nothing
#: else stands between the loader and Typer, so each must carry its own
#: ``except TheurianError``.
DIRECT_RESOLVE_CONTEXT_CALLERS = {
    ("cli/commands.py", "init_command"),
    ("cli/commands.py", "project_register"),
    ("cli/commands.py", "project_status"),
    ("cli/commands.py", "_require_project"),
}


def _iter_nodes_with_scope(tree: ast.AST) -> Iterator[tuple[ast.AST, tuple[str, ...]]]:
    """Yield ``(node, scope)`` for every node, ``scope`` the dotted enclosing defs.

    Copied from ``test_gate_call_sites.py`` rather than imported: each pin test
    in this suite is self-contained, so a change to one scanner cannot silently
    change what another one sees.
    """

    def walk(node: ast.AST, scope: tuple[str, ...]) -> Iterator[tuple[ast.AST, tuple[str, ...]]]:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                yield from walk(child, (*scope, child.name))
            else:
                yield child, scope
                yield from walk(child, scope)

    yield from walk(tree, ())


def _call_sites(path: pathlib.Path) -> list[tuple[str, str]]:
    """Every call to a name in :data:`CALLEES` in ``path``, as ``(module, function)``.

    Matches a bare-name call (``resolve_context(...)``) rather than an
    attribute call, because that is how both names are actually reached here:
    each is imported directly, never through a module alias.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    module = path.relative_to(SRC).as_posix()
    return [
        (module, ".".join(scope) or "<module>")
        for node, scope in _iter_nodes_with_scope(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in CALLEES
    ]


def test_the_scanner_looks_for_names_the_product_actually_calls() -> None:
    """Guards the guard below, the way ``test_gate_call_sites.py`` guards its own scan.

    If either name is renamed, the scan below starts looking for a name
    nothing in the product calls. It would still fail -- the expected set is
    non-empty -- but as "no call sites found", which reads as a broken test
    rather than as the rename it is.
    """
    from theurian.cli import commands, context

    assert hasattr(context, "resolve_context"), (
        "`resolve_context` no longer exists in `theurian.cli.context`; rename "
        "CALLEES and the pinned sets together, or this scan silently protects "
        "nothing"
    )
    assert hasattr(commands, "_require_project"), (
        "`_require_project` no longer exists in `theurian.cli.commands`; rename "
        "CALLEES and the pinned sets together, or this scan silently protects "
        "nothing"
    )


def test_every_call_site_of_resolve_context_or_require_project_is_enumerated() -> None:
    """A new call site is a new place issue #205's class can reopen.

    The assertion is an equality against the whole enumeration rather than a
    length, so it fails in both directions -- a call site added, and a known
    one moved or deleted -- and names the sites it found, the same discipline
    ``test_gate_call_sites.py`` uses for the result gate and the status gate.
    """
    sites = sorted({site for path in sorted(SRC.rglob("*.py")) for site in _call_sites(path)})

    assert sites == sorted(RESOLVE_CONTEXT_CALL_SITES), (
        f"`resolve_context`/`_require_project` are called from {len(sites)} "
        f"place(s) in the shipped source, expected exactly "
        f"{len(RESOLVE_CONTEXT_CALL_SITES)}:\n"
        + "\n".join(f"  {module} :: {function}" for module, function in sites)
        + "\n\nExpected exactly:\n"
        + "\n".join(
            f"  {module} :: {function}" for module, function in sorted(RESOLVE_CONTEXT_CALL_SITES)
        )
        + "\n\nEvery command reaching `resolve_context` inherits its migration "
        "loading, including reading each `upsertRevision`'s `contentFile` from "
        "disk (issue #205). A new call site is a new place that read failure "
        "can surface -- establish for it:\n"
        "  1. it reaches `resolve_context` only through `_require_project`, "
        "which already ends `except TheurianError`; or it wraps its own "
        "direct call in a `try`/`except TheurianError`, added to "
        "DIRECT_RESOLVE_CONTEXT_CALLERS below with that `except` verified.\n"
        "  2. a CLI-level test asserting `--json` reports `{error, remedy}` "
        "rather than crashing when a migration in the project it resolves "
        "cannot be loaded.\n"
        "Then list the site here."
    )


def _function_node(module_path: pathlib.Path, name: str) -> ast.FunctionDef:
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"No function named {name!r} in {module_path}")


def _names_of(expr: ast.expr) -> list[ast.expr]:
    """Every exception type an ``except`` clause names, unpacking a tuple form."""
    return list(expr.elts) if isinstance(expr, ast.Tuple) else [expr]


def _calls_target(node: ast.AST) -> bool:
    """Whether ``node`` or a descendant calls a name in :data:`CALLEES`."""
    return any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in CALLEES
        for n in ast.walk(node)
    )


def _guarding_try(function: ast.FunctionDef) -> ast.Try | None:
    """The ``Try`` node whose *body* (not its handlers) calls a name in :data:`CALLEES`.

    ``project_status`` guards two unrelated things in two separate ``try``
    blocks -- ``resolve_context`` in one, then a later read of the active-state
    pointer in another, each with its own ``except TheurianError``. A scan of
    every ``ExceptHandler`` anywhere in the function cannot tell those apart:
    it found the second block's handler and reported the function guarded even
    after the first block's own handler was mutated to `except ProjectError`,
    which is exactly the false negative a scan restricted to the *matching*
    ``Try`` node closes.
    """
    for node in ast.walk(function):
        if isinstance(node, ast.Try) and any(_calls_target(stmt) for stmt in node.body):
            return node
    return None


def _catches_theurian_error(function: ast.FunctionDef) -> bool:
    """Whether the ``try`` guarding a call to :data:`CALLEES` catches ``TheurianError``.

    Matches the bare name only: every direct caller imports it that way
    (``from theurian.domain.errors import ... TheurianError ...``), so
    resolving a module-attribute alias -- the way
    ``test_gate_call_sites.py``'s status-gate scan does for `enums.may_surface`
    -- is not needed for these four functions.
    """
    guarding = _guarding_try(function)
    if guarding is None:
        return False
    for handler in guarding.handlers:
        if handler.type is None:
            continue
        for candidate in _names_of(handler.type):
            if isinstance(candidate, ast.Name) and candidate.id == "TheurianError":
                return True
    return False


@pytest.mark.parametrize(
    "site", sorted(DIRECT_RESOLVE_CONTEXT_CALLERS), ids=lambda s: f"{s[0]}::{s[1]}"
)
def test_each_direct_caller_of_resolve_context_ends_in_except_theurian_error(
    site: tuple[str, str],
) -> None:
    """The guard half of the closure argument.

    ``resolve_context``'s own docstring promises ``ProjectError`` and
    ``MigrationError``, and a caller could reasonably guard only those two
    names. That would still have missed `FileNotFoundError` before issue
    #205's fix -- it was never a `TheurianError` at all -- and it would miss
    the *next* `TheurianError` subclass the loader starts raising, for exactly
    the same reason a narrower guard always trails what the callee can throw.
    `except TheurianError` is the one clause that does not need updating when
    the loader gains a new failure mode.
    """
    module, function = site
    node = _function_node(SRC / module, function)
    assert _catches_theurian_error(node), (
        f"{module} :: {function} calls `resolve_context` directly but has no "
        f"`except TheurianError` clause guarding it. `resolve_context` can "
        f"raise any `TheurianError` subclass the migration loader defines, "
        f"present or future -- narrower than that reopens issue #205's class "
        f"for whichever subclass the guard does not name."
    )


@pytest.mark.parametrize(
    "error_type", [MigrationContentUnreadableError, MigrationFileUnreadableError]
)
def test_the_loaders_read_errors_are_theurian_errors(error_type: type[TheurianError]) -> None:
    """The other half of the closure argument: what the loader actually raises.

    The parametrized test above proves each direct caller catches
    `TheurianError`. This proves what
    `infrastructure/filesystem/migration_loader.py` raises for its two raw
    reads -- an unresolvable `contentFile`, and the migration file itself --
    is in fact one, for both. Together, the two tests prove neither read
    failure can again escape as a bare `OSError` through any of the pinned
    call sites above.
    """
    assert issubclass(error_type, TheurianError)
