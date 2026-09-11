"""Syntax-tree keys shared by claims that read the shipped source (#526, #586).

Two families live here, both for the same reason: a copy is a key that gets fixed
in whichever file its author remembered, and round two of #586 found both copies
of the first one wrong the same way.

**The one-opener keys.** Two modules assert that every open of a database goes
through one function -- ``connection.py::_connect`` for the state database,
``index_store.py::_connect_to`` for the index. The claim is the same shape in
both.

**The write-path name-hygiene key.** :func:`private_side_effect_calls` is the
structural reading of "what does this write refuse before it touches a name": a
bare-statement call to one of the module's own private helpers, standing between
the caller and a file. ``test_review_search_store_guards.py`` derives its
``WRITE_PATH_HYGIENE`` census from it, and
``test_threat_model_t19_claims.py`` reads the same census to ask *where in the
write* the sidecar reap happens -- which is the claim T-19 gained when the
"redundant defense-in-depth" sentence turned out not to generalise to a publish
by ``os.replace``.

Lives beside ``migration_fixtures`` and ``hang_guard`` at the tests root, which
is what makes a bare ``from ast_keys import ...`` resolve from any test module.
"""

from __future__ import annotations

import ast
import inspect
import pathlib
from types import ModuleType


def opens_inside(tree: ast.Module, function_name: str, opens: set[int]) -> set[int]:
    """Which of ``opens`` are lexically inside ``function_name``.

    ``opens`` is a set of ``id()``s of ``ast.Call`` nodes taken from *this*
    ``tree``. Identity is the key, so the caller must not parse twice: two parses
    give two sets of nodes and every membership test answers ``False``, which
    reads as "no open is inside the guarded function" and passes nothing.

    **Membership is answered from the one function, not from a map of every
    function** (#586 round two, M-2). Both callers used to build
    ``{name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}``
    and compare the *set of enclosing names* against a singleton. Four spellings
    were invisible to that, each measured passing with a real second opener in
    place: a call at **module scope** and one in a **class body** are inside no
    ``FunctionDef`` at all; a call inside a **lambda** likewise; and an
    ``async def`` is an ``AsyncFunctionDef``, which the ``isinstance`` filter
    dropped. In every case the enclosing set stayed the singleton the assertion
    wanted.

    Comparing membership makes the answer total instead: an open the guarded
    function does not contain is outside it, however it is spelled and whatever
    it sits in.

    **What it still does not see**, recorded rather than implied: this is a
    *lexical* check. An open reached through a helper the guarded function calls
    is outside it and is reported as such, which is correct; an open in another
    *module* is not in this tree at all, and is nobody's business here. What
    covers those is behavioural -- the named-pipe refusals each caller drives.

    Raises:
        StopIteration: If ``function_name`` is not defined in ``tree``. That is
            the right failure: a renamed guard must not read as "everything is
            inside it".
    """
    guarded = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == function_name
    )
    return {id(node) for node in ast.walk(guarded) if id(node) in opens}


def opens_a_database(node: ast.Call) -> bool:
    """Whether ``node`` opens a SQLite database, in any spelling this key covers.

    Three: the attribute call ``sqlite3.connect(...)``, the bare ``connect(...)``
    a ``from sqlite3 import connect`` produces, and ``sqlite3.Connection(...)``,
    which the stdlib exposes as a constructor that opens a file just as the
    factory does. The bare-name arm is what makes an import-style change fail
    rather than silently widening the surface.

    **The bound, recorded rather than left implicit** (#585 round one, adv M-5).
    It does not match an aliased module (``import sqlite3 as db``), a name bound
    at runtime, or a connection handed in from another module. Those are not
    covered here and are not claimed to be; what covers them is behavioural --
    the named-pipe sweeps, which fail on an unbounded open however the connection
    was obtained. This is the cheap structural net for the ordinary way a guard
    gets bypassed: somebody adding an opener beside the existing one.
    """
    target = node.func
    if isinstance(target, ast.Attribute):
        return target.attr in {"connect", "Connection"} and (
            isinstance(target.value, ast.Name) and target.value.id == "sqlite3"
        )
    return isinstance(target, ast.Name) and target.id in {"connect", "Connection"}


def module_tree(module: ModuleType) -> ast.Module:
    """*module*'s shipped source as a syntax tree.

    Read from the file the import resolved to, so a pin cannot end up parsing a
    copy of the source that is not the one the product runs.
    """
    return ast.parse(pathlib.Path(inspect.getfile(module)).read_text(encoding="utf-8"))


def function_named(tree: ast.Module, name: str) -> ast.FunctionDef:
    """The named function anywhere in *tree*, method or module level.

    Raises:
        LookupError: If *name* is not defined. That is the right failure: a
            renamed function must not read as "the function has no guards".
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise LookupError(f"{name} is gone from the module this census reads")


def private_side_effect_calls(module: ModuleType, function_name: str) -> frozenset[str]:
    """Bare-statement calls to *module*'s own private helpers inside a function.

    The structural shape of a name-hygiene guard: a call whose value nobody uses,
    made to a helper this module defines, standing between the caller and a file.
    ``_finding_rows(load.accepted)`` is assigned and therefore excluded; a
    ``mkdir`` on ``self._path.parent`` is an attribute call rather than a call to
    a module-level helper, and is a precondition rather than a refusal.
    """
    tree = module_tree(module)
    private = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    return frozenset(
        node.value.func.id
        for node in ast.walk(function_named(tree, function_name))
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id in private
    )


__all__ = [
    "function_named",
    "module_tree",
    "opens_a_database",
    "opens_inside",
    "private_side_effect_calls",
]
