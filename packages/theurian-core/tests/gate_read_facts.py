"""The reads each gate makes, and the reads it makes after a row clears, derived from `src` (#832).

Records name a read by symbol, and each of them was written from memory of one
call site. This module derives the sets the records are held to.

**The keys, each with its known-weak half.**

- *Body-selecting.* A method of the SQLite store reads a body when the SQL it
  reaches names the ``knowledge_revisions`` table, where a body lives. Reached =
  the method plus every ``self.<method>(...)`` call, transitively, whose target
  the class defines, and SQL = a string constant in those bodies or a module-level
  string constant one of them names. The table name is matched case-insensitively,
  as SQLite resolves it. Blind: SQL assembled from fragments, a method
  handed on by reference rather than called (``mapper=self.x``), a body kept in
  another table, and any call not spelled ``self.<name>(...)``. A method's own
  docstring is not read.
- *A gate's post-gate readers.* The body-reading names (session members plus the
  adapter's ``current_revision``) that a function's body calls, by attribute or
  bare name. Per gate: ``knowledge_get`` for `knowledge.get`; every method of
  ``CanonicalVisibility`` and of ``ResultGate`` for search; ``_relation_is_visible``
  for the relation gate. Blind: a reader reached through a function the gate calls
  in another scope. **The list of gates is enumerated by hand, not derived.** The
  write path's gate, ``register._draft_only_proposals.current_revision`` in
  ``mcp/tools.py``, is not modelled: it reads no body today, so no record is held
  to it, and a body read added there is not seen. #870
  (https://github.com/theurian/theurian/issues/870) owns deriving the list.
- *A gate's read.* The session members the same functions call that read no body,
  which for search is taken from ``CanonicalVisibility._lookup`` alone.
"""

from __future__ import annotations

import ast
import functools
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from theurian.domain.ports.canonical_store import CanonicalReadSession

REPO_ROOT: Final = Path(__file__).resolve().parents[3]
_SRC: Final = REPO_ROOT / "packages/theurian-core/src/theurian"
STORE_FILE: Final = _SRC / "infrastructure/sqlite/store.py"
VISIBILITY_FILE: Final = _SRC / "application/visibility.py"
RETRIEVAL_FILE: Final = _SRC / "application/retrieval_service.py"
TOOLS_FILE: Final = _SRC / "mcp/tools.py"

BODY_TABLE: Final = "knowledge_revisions"
SESSION_MEMBERS: Final = frozenset(
    name for name in dir(CanonicalReadSession) if not name.startswith("_")
)
#: `current_revision` is the adapter's, not the session's; `knowledge.get` reads through it.
READ_NAMES: Final = SESSION_MEMBERS | {"current_revision"}


@functools.cache
def tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _only[N: ast.AST](found: list[N], what: str) -> N:
    assert len(found) == 1, f"{what}: found {len(found)}, this derivation reads exactly one"
    return found[0]


def klass(module: ast.Module, name: str) -> ast.ClassDef:
    return _only(
        [n for n in ast.walk(module) if isinstance(n, ast.ClassDef) and n.name == name],
        f"class {name}",
    )


def methods(owner: ast.ClassDef) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    return {n.name: n for n in owner.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)}


def function(module: ast.Module, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    return _only(
        [
            n
            for n in ast.walk(module)
            if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n.name == name
        ],
        f"function {name}",
    )


def callees(node: ast.AST) -> set[str]:
    """Names called by attribute or bare name anywhere under *node*."""
    found: set[str] = set()
    for call in ast.walk(node):
        if isinstance(call, ast.Call):
            match call.func:
                case ast.Attribute(attr=name) | ast.Name(id=name):
                    found.add(name)
    return found


def _self_calls(node: ast.AST) -> set[str]:
    return {
        call.func.attr
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "self"
    }


def self_reach(owner: ast.ClassDef, name: str) -> set[str]:
    """*name* and every method of *owner* it reaches through ``self.<method>(...)`` calls."""
    defined = methods(owner)
    seen: set[str] = set()
    todo = [name]
    while todo:
        current = todo.pop()
        if current in seen or current not in defined:
            continue
        seen.add(current)
        todo.extend(_self_calls(defined[current]))
    return seen


def module_strings(module: ast.Module) -> dict[str, str]:
    """Module-level ``NAME = "..."`` constants, implicit concatenation included."""
    found: dict[str, str] = {}
    for node in module.body:
        match node:
            case ast.Assign(targets=[ast.Name(id=name)], value=ast.Constant(value=str(text))):
                found[name] = text
            case ast.AnnAssign(target=ast.Name(id=name), value=ast.Constant(value=str(text))):
                found[name] = text
    return found


def _without_docstring(function_node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.stmt]:
    body = function_node.body
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        return body[1:]
    return body


def reached_sql(module: ast.Module, owner: ast.ClassDef, name: str) -> list[str]:
    """The string constants, and the module constants they name, in *name*'s reach."""
    constants = module_strings(module)
    defined = methods(owner)
    found: list[str] = []
    for reached in self_reach(owner, name):
        for statement in _without_docstring(defined[reached]):
            for node in ast.walk(statement):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    found.append(node.value)
                elif isinstance(node, ast.Name) and node.id in constants:
                    found.append(constants[node.id])
    return found


def reads_body(module: ast.Module, owner: ast.ClassDef, name: str) -> bool:
    """Whether the SQL *name* reaches names the body table; SQLite resolves it in any case."""
    return any(BODY_TABLE in sql.lower() for sql in reached_sql(module, owner, name))


def _names_a_module_constant(owner: ast.ClassDef, name: str, constant: str) -> bool:
    defined = methods(owner)
    return any(
        isinstance(node, ast.Name) and node.id == constant
        for reached in self_reach(owner, name)
        for statement in _without_docstring(defined[reached])
        for node in ast.walk(statement)
    )


@functools.cache
def _store() -> tuple[ast.Module, ast.ClassDef]:
    module = tree(STORE_FILE)
    return module, klass(module, "SqliteCanonicalStore")


@functools.cache
def body_readers() -> frozenset[str]:
    """The names in :data:`READ_NAMES` whose SQLite implementation selects a body."""
    module, owner = _store()
    defined = methods(owner)
    return frozenset(
        name for name in READ_NAMES if name in defined and reads_body(module, owner, name)
    )


@functools.cache
def joined_readers() -> frozenset[str]:
    """The readers that go through ``_ITEM_WITH_CURRENT_CONTENT_SQL``, the served-content join."""
    _, owner = _store()
    return frozenset(
        name
        for name in READ_NAMES
        if name in methods(owner)
        and _names_a_module_constant(owner, name, "_ITEM_WITH_CURRENT_CONTENT_SQL")
    )


@dataclass(frozen=True)
class Gate:
    """One gate: the read it decides on, its call sites, and the body reads they make after it."""

    name: str
    read: frozenset[str]
    sites: frozenset[str]
    after: Mapping[str, frozenset[str]]
    #: Whether the call site also makes the decision, so that a record naming it may name the read.
    decides: bool = False

    @property
    def readers(self) -> frozenset[str]:
        return frozenset().union(*self.after.values())

    def narrowed_to(self, readers: frozenset[str]) -> Gate:
        """The same gate, kept to the call sites that make one of *readers*."""
        after = {site: found for site, found in self.after.items() if found & readers}
        return Gate(f"{self.name}, narrowed", self.read, frozenset(after), after, self.decides)

    def allowed_near(self, site: str) -> frozenset[str]:
        """The reads a sentence may name beside *site*: its post-gate reads and its decision."""
        return self.after.get(site, frozenset()) | (self.read if self.decides else frozenset())


def _after(nodes: Mapping[str, ast.AST]) -> dict[str, frozenset[str]]:
    reads = {name: frozenset(callees(node) & body_readers()) for name, node in nodes.items()}
    return {name: found for name, found in reads.items() if found}


def _methods_of(path: Path, class_name: str) -> dict[str, ast.AST]:
    return dict(methods(klass(tree(path), class_name)))


@functools.cache
def gates() -> Mapping[str, Gate]:
    """The three gates, by name: ``knowledge.get``, ``search`` and ``relation``."""
    tools = tree(TOOLS_FILE)
    knowledge_get = function(tools, "knowledge_get")
    relation = function(tools, "_relation_is_visible")
    lookup = methods(klass(tree(VISIBILITY_FILE), "CanonicalVisibility"))["_lookup"]
    search = _after(
        _methods_of(VISIBILITY_FILE, "CanonicalVisibility")
        | _methods_of(RETRIEVAL_FILE, "ResultGate")
    )
    return {
        "knowledge.get": Gate(
            "knowledge.get",
            frozenset(callees(knowledge_get) & SESSION_MEMBERS) - body_readers(),
            frozenset({"knowledge_get"}),
            _after({"knowledge_get": knowledge_get}),
            decides=True,
        ),
        "search": Gate(
            "search", frozenset(callees(lookup) & SESSION_MEMBERS), frozenset(search), search
        ),
        "relation": Gate(
            "relation",
            frozenset(callees(relation) & SESSION_MEMBERS) - body_readers(),
            frozenset({"_relation_is_visible"}),
            _after({"_relation_is_visible": relation}),
            decides=True,
        ),
    }


def combined(*names: str) -> Gate:
    """The named gates as one, for a record that describes several of them."""
    chosen = [gates()[name] for name in names]
    after: dict[str, frozenset[str]] = {}
    for gate in chosen:
        after |= gate.after
    return Gate(
        " + ".join(names),
        frozenset().union(*(gate.read for gate in chosen)),
        frozenset().union(*(gate.sites for gate in chosen)),
        after,
    )
