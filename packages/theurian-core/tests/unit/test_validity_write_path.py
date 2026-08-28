"""The domain is the only gate left on ``valid_from``/``valid_to`` after #117.

Schema version 4 drops ``knowledge_revisions``' ``CHECK (valid_to IS NULL OR
valid_to > valid_from)`` (``infrastructure/sqlite/schema.py``) because it
compared the stored ISO-8601 strings as TEXT and refused a domain-valid window
whose ``valid_to`` sorted earlier as a string than its ``valid_from`` while
naming a later instant. Removing a guard is only safe if the one it is replaced
by is complete -- otherwise the CHECK's removal is a hole, not a fix.

``ValidityPeriod.__post_init__`` (INV-4, ``domain/values.py``) is that guard:
it compares aware ``datetime``s and orders by instant, which is what the SQL
comparison could not do. This module pins the other half of the argument --
that INV-4 is unavoidable, because it is the *only* thing that can put a value
into ``valid_from``/``valid_to`` in the first place.

**The population key.** Every ``.execute(...)``/``.executemany(...)`` call
anywhere under the imported ``theurian`` package whose SQL literal names
``valid_from`` or ``valid_to`` -- read as text, not by which class or function a
spelling happens to sit in, which is what keeps this scan honest about
statements it does not recognise rather than about a curated allowlist.
``SELECT`` reads never appear here: every read of these tables in the shipped
store uses ``SELECT *`` (``store.py``, ``get_revision``, ``get_item``,
``list_specifications`` and their siblings), so a column name in a SQL literal
is, in this codebase today, always a write. That is a fact about the current
source, not a rule the scan enforces -- see the guard test below, which is what
catches the day a ``SELECT valid_from`` is added and quietly starts looking
like a fourth write site.

**Every call is attributed to exactly the scope it lexically sits in**, module
function or class method or nested closure alike, through a single scope-
tracking walk (:func:`_iter_calls_with_scope`) rather than the
``ast.walk(class_node)`` shortcut a round-one version of this file used. That
shortcut collapsed two distinct failures into one green result: it never
descended a module-level function at all (so a bypass sitting outside every
class was invisible), and for a function nested inside a method it double
-counted the nested body's calls -- once folded into the enclosing method's
label, because ``ast.walk`` does not stop at a nested ``def``, and once more
under the nested function's own name when that def was independently
enumerated. Both were demonstrated: a mutation adding a raw module-level
``conn.execute(...)`` and a raw ``self._conn.executemany(...)`` to
``store.py`` left the enumeration below unchanged.

**What this cannot see.** Like ``test_gate_call_sites.py``'s scan, it reads
names: a SQL string built through an f-string or string concatenation across a
variable, rather than the adjacent-literal form the parser folds into one
``ast.Constant``, would not be recognised as naming the columns at all. It is a
floor on the review a new call site gets, not a proof that a bypass is
impossible.
"""

from __future__ import annotations

import ast
import pathlib
from collections.abc import Iterator

import pytest

import theurian

pytestmark = pytest.mark.unit

#: The package as *imported*, matching ``test_gate_call_sites.py`` and
#: ``test_config_key_call_sites.py``'s own reckoning, for the same reason: a
#: hand-built relative path can drift from the installed package and scan a
#: directory with no write sites in it at all.
SRC = pathlib.Path(theurian.__file__).resolve().parent

#: The two column names whose only writer is being pinned.
WATCHED_COLUMNS = ("valid_from", "valid_to")

#: The two DB-API methods that can carry a bound write -- a single row and a
#: batch. A batch write is exactly as unguarded as a single one if its rows
#: were never built through ``ValidityPeriod``; only the parameter *shape*
#: differs (a sequence of tuples rather than one tuple), which the bound-value
#: text scan below still reaches because it works on source text, not on a
#: fixed argument layout.
_SQL_METHODS = frozenset({"execute", "executemany"})

#: The label a call gets when it sits in neither a function nor a method --
#: true module level, or a class body outside any ``def``.
MODULE_SCOPE = "<module>"

#: Every place in the shipped package whose SQL literal names either column, as
#: ``(module path under theurian/, qualified scope name)``.
#:
#: Three, all in ``SqliteWriter``: ``append_revision`` writes
#: ``knowledge_revisions``, ``put_item`` writes ``knowledge_items``, and
#: ``register_specification`` writes ``specifications`` -- the three tables
#: `schema.py` gives both columns. Each binds ``<entity>.validity.valid_from``
#: / ``.valid_to``, and ``validity`` is typed ``ValidityPeriod`` on
#: ``KnowledgeRevision``, ``KnowledgeItem`` and ``Specification`` alike, so a
#: value cannot reach any of the three without having passed
#: ``ValidityPeriod.__post_init__`` first. The second assertion below checks
#: that binding directly, so a fourth site that read a bare parameter instead
#: of ``.validity.valid_from`` -- bypassing INV-4 even while adding no new
#: table -- fails there rather than passing this enumeration by accident.
WRITE_PATH_SITES: frozenset[tuple[str, str]] = frozenset(
    {
        ("infrastructure/sqlite/store.py", "SqliteWriter.append_revision"),
        ("infrastructure/sqlite/store.py", "SqliteWriter.put_item"),
        ("infrastructure/sqlite/store.py", "SqliteWriter.register_specification"),
    }
)


def _sql_literal(call: ast.Call) -> str | None:
    """The SQL text of an ``x.execute(sql, ...)``/``x.executemany(sql, ...)`` call.

    Adjacent string literals -- the form every statement in ``store.py`` uses
    to keep one SQL statement readable across several source lines -- are
    folded by the parser into a single ``ast.Constant`` before this ever runs,
    so no manual concatenation is needed here.
    """
    if (
        not call.args
        or not isinstance(call.func, ast.Attribute)
        or call.func.attr not in _SQL_METHODS
    ):
        return None
    literal = call.args[0]
    if isinstance(literal, ast.Constant) and isinstance(literal.value, str):
        return literal.value
    return None


def _bound_source(call: ast.Call, source: str) -> str:
    """The source text of the value(s) bound to ``call``, or ``""``.

    Every matched call in the shipped tree passes the bound values as a second
    positional argument -- a tuple for ``execute``, a sequence of tuples for
    ``executemany`` -- so this is where ``.validity.valid_from``/``.valid_to``
    would appear if the value reached the column through the domain type,
    rather than as some other expression an author wrote directly. A plain
    source-text scan rather than an argument-shape-aware one, deliberately: it
    reads identically whether the writer passed one tuple or a list of them.
    """
    if len(call.args) < 2:
        return ""
    return ast.get_source_segment(source, call.args[1]) or ""


def _iter_calls_with_scope(tree: ast.AST) -> Iterator[tuple[ast.Call, str]]:
    """Yield every ``Call`` in ``tree``, paired with its nearest enclosing scope.

    The scope is a dotted path built from every ``ClassDef``/``FunctionDef``/
    ``AsyncFunctionDef`` a call is lexically inside, most specific last --
    ``"SqliteWriter.append_revision"`` for a call directly in that method,
    ``"SqliteWriter.append_revision.helper"`` for one inside a closure defined
    there, a bare function name at module level, and :data:`MODULE_SCOPE` for a
    call that sits inside none of them.

    Deliberately a single recursive descent rather than ``ast.walk(node)``
    applied per class or per function: ``ast.walk`` returns every descendant
    regardless of depth, so walking a class and then walking each of its
    methods in turn visits a nested function's calls twice -- once folded into
    the enclosing method (``ast.walk`` does not stop at a nested ``def``) and
    once more under the nested function's own name when it is separately
    enumerated as one of the class's descendants. Tracking scope as this walk
    descends attributes each call to exactly one place, its own.
    """

    def walk(node: ast.AST, scope: str) -> Iterator[tuple[ast.Call, str]]:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                nested = f"{scope}.{child.name}" if scope else child.name
                yield from walk(child, nested)
                continue
            if isinstance(child, ast.Call):
                yield child, scope or MODULE_SCOPE
            yield from walk(child, scope)

    yield from walk(tree, "")


def _write_path_calls(path: pathlib.Path) -> Iterator[tuple[str, str, ast.Call]]:
    """Every call in ``path`` whose SQL literal names a watched column.

    Yields ``(module path, qualified scope, the call node)`` so a caller can
    inspect the bound values as well as count the sites.
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    module = path.relative_to(SRC).as_posix()
    for call, scope in _iter_calls_with_scope(tree):
        sql = _sql_literal(call)
        if sql is not None and any(column in sql for column in WATCHED_COLUMNS):
            yield module, scope, call


def test_the_scanner_recognises_every_shape_it_claims_to_see() -> None:
    """Guards the enumeration below, which is worthless the moment its scanner stops seeing.

    An empty expected set and an empty found set look identical from the
    outside; this is what tells the difference apart, the same way
    ``test_gate_call_sites.py``'s own guard test does for its scan. Four shapes,
    one per claim this module's docstring makes: a bare module-level call, a
    method, an ``executemany`` batch, and a closure nested inside a method --
    the last is what pins that a nested writer is attributed to its own scope
    rather than folded into, or duplicated under, its enclosing method's.
    """
    source = (
        'conn.execute("INSERT INTO t (valid_from) VALUES (?)", (bare.validity.valid_from,))\n'
        "\n"
        "\n"
        "def module_level_write(conn, x):\n"
        "    conn.execute(\n"
        '        "INSERT INTO t (valid_from, valid_to) VALUES (?, ?)",\n'
        "        (x.validity.valid_from, x.validity.valid_to),\n"
        "    )\n"
        "\n"
        "\n"
        "class Writer:\n"
        "    def write(self, x):\n"
        "        self._conn.executemany(\n"
        '            "INSERT INTO t (valid_from, valid_to) VALUES (?, ?)",\n'
        "            [(x.validity.valid_from, x.validity.valid_to)],\n"
        "        )\n"
        "\n"
        "        def nested(y):\n"
        "            self._conn.execute(\n"
        '                "INSERT INTO t (valid_to) VALUES (?)",\n'
        "                (y.validity.valid_to,),\n"
        "            )\n"
        "\n"
        "        return nested\n"
    )
    tree = ast.parse(source, filename="snippet.py")
    found = [
        (scope, _bound_source(call, source))
        for call, scope in _iter_calls_with_scope(tree)
        if (sql := _sql_literal(call)) is not None
        if any(column in sql for column in WATCHED_COLUMNS)
    ]
    assert found == [
        (MODULE_SCOPE, "(bare.validity.valid_from,)"),
        ("module_level_write", "(x.validity.valid_from, x.validity.valid_to)"),
        ("Writer.write", "[(x.validity.valid_from, x.validity.valid_to)]"),
        ("Writer.write.nested", "(y.validity.valid_to,)"),
    ]


def test_valid_from_and_valid_to_are_written_only_through_validityperiod() -> None:
    """AC-2b (#117): the SQL ``CHECK`` is gone, so this is the whole remaining gate.

    Two claims, both checked against the shipped source rather than assumed:
    the write sites are exactly the three named above, and every one of them
    binds the domain-validated ``.validity.valid_from``/``.valid_to`` rather
    than a raw value that could skip ``ValidityPeriod.__post_init__``. Either
    failing independently is the finding #117's brief asked to be stopped on --
    a fourth site would be a write the domain never gated even before this
    change, and a site binding something other than ``.validity.*`` would mean
    the CHECK's removal opened exactly the hole it was meant not to.

    Confirmed to actually catch a bypass, not merely to have one in its
    docstring: a module-level ``conn.execute(...)`` and a
    ``self._conn.executemany(...)`` were injected into ``store.py`` in turn,
    each binding a raw parameter instead of ``.validity.valid_from``/
    ``.valid_to``, and each turned this test RED -- the module-level call as a
    fourth, unpinned site, and the ``executemany`` call the same way, both
    before either was reachable at all under the narrower scan this replaced.
    """
    calls = [
        (module, scope, call)
        for path in sorted(SRC.rglob("*.py"))
        for module, scope, call in _write_path_calls(path)
    ]

    sites = frozenset({(module, scope) for module, scope, _ in calls})
    assert sites == WRITE_PATH_SITES, (
        f"{len(sites)} place(s) in the shipped package write `valid_from` or "
        f"`valid_to` through `.execute(...)`/`.executemany(...)`, and the pinned "
        f"set has {len(WRITE_PATH_SITES)}:\n"
        + "\n".join(f"  {module} :: {scope}" for module, scope in sorted(sites))
        + "\n\nExpected exactly:\n"
        + "\n".join(f"  {module} :: {scope}" for module, scope in sorted(WRITE_PATH_SITES))
        + "\n\nThe SQL `CHECK` these columns used to carry is gone (#117, "
        "SCHEMA_VERSION 4); `ValidityPeriod.__post_init__` (INV-4) is the only "
        "remaining gate on their ordering. A new site here is a new place that "
        "guard can be bypassed -- construct the value through `ValidityPeriod` "
        "before this scan is widened to include it, not after."
    )

    source_by_module: dict[str, str] = {}
    for module, scope, call in calls:
        source_by_module.setdefault(module, (SRC / module).read_text(encoding="utf-8"))
        bound = _bound_source(call, source_by_module[module])
        for column in WATCHED_COLUMNS:
            assert f".validity.{column}" in bound, (
                f"{module} :: {scope} binds `valid_from`/`valid_to` from an "
                f"expression that does not read `.validity.{column}`:\n\n"
                f"  {bound}\n\n"
                f"Every write to these columns must come from a `ValidityPeriod` "
                f"the domain has already validated (INV-4) -- this is the check "
                f"that closes #117's AC-2b. If the entity's attribute was "
                f"renamed, update the assertion; if the value is now built some "
                f"other way, that is the hole this test exists to catch."
            )
