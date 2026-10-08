"""T-28's three code facts, read from the syntax tree.

``docs/security/threat-model.md``'s T-28 rests its residuals on three facts about
the source, and none moves a sentence of the entry when it stops being true:

1. The accept floors are one call each. ``lowered_sensitivities`` and
   ``readmitted_items`` (``application/item_labels.py``) are called once in the
   package, inside ``application/proposal_service.py``. A second call site is a
   second floor the entry does not describe, or one that quietly applies a
   different ``held``/``union`` pair.
2. Only ``upserted`` and ``wrote`` produce report rows: ``LabelWriters`` holds two row
   containers, ``_loosened`` and ``_moves``, and ``moves`` is built from both; the
   first is added to only from those two, the second only by ``_track`` extending
   it with ``self._settled()``. A sanctioned write (``wrote``: ``changeSensitivity``,
   ``restoreItem``, ...) adds a row only as ``reorders``, which
   ``test_reorders_report.py`` holds, and ``accept`` refuses a proposal introducing
   one. A further producer changes which operations are reported, what T-28's
   residuals 5 and 10 and the CHANGELOG state.
3. The end-state refusal is one decision: ``loosened_after``
   (``application/permissive_moves.py``) is called once in the package, inside
   ``application/proposal_service.py :: _refuse_a_landed_overwrite``.

**Fact side only.** The prose side of the entry is not pinned here.

**Reach.** Every ``.py`` under ``src/theurian`` is parsed; a floor call counts
when its callee is a bare name or an attribute with the floor's name. Rows,
within ``LabelWriters``: any statement that adds to ``self._loosened`` or
``self._moves`` outside ``upserted`` and ``wrote`` -- a subscript or attribute-chain store, an
augmented assignment, or an adding method call (``setdefault``, ``update``,
``append``, ``extend``, ...) -- is a producer, except the one allowed site,
``self._moves.extend(self._settled())`` in ``_track``, which must exist exactly
once. An empty literal or constructor (``{}``, ``dict()``, ``[]``, ``list()``)
or an argument-less ``clear()`` is a reset, not a row (``__init__`` and ``_track``
do it). Removals (``del``, ``pop``, ``popitem``) add no row and are not scanned.
``_loosened`` named anywhere outside ``class LabelWriters`` in the package, or
``_moves`` outside it in ``permissive_moves.py``, is reported.

**Blind spots.** Calls through an alias (``f = readmitted_items; f(...)``) or
``getattr``; a write to either container through a helper that receives it, or
through another name for the same object; a read that is later mutated;
``_moves`` reached from a module other than ``permissive_moves.py``; and a class
nested inside ``LabelWriters``. Each needs a reading, not this scan.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from typing import Final

import pytest
from write_lock_claims import REPO_ROOT

pytestmark = pytest.mark.unit

_SRC: Final = REPO_ROOT / "packages" / "theurian-core" / "src" / "theurian"
_FLOORS: Final = ("lowered_sensitivities", "readmitted_items")
_FLOOR_SITE: Final = "application/proposal_service.py"
_END_STATE: Final = "loosened_after"
_MOVES: Final = _SRC / "application" / "permissive_moves.py"
_WRITERS: Final = "LabelWriters"
_SOURCE_OF_ROWS: Final = "_loosened"
_PRODUCERS: Final = ("upserted", "wrote")
_MOVES_LIST: Final = "_moves"
_TRACK: Final = "_track"
_ADDERS: Final = frozenset(
    {
        "setdefault",
        "update",
        "__setitem__",
        "append",
        "extend",
        "insert",
        "add",
        "__iadd__",
        "__ior__",
    }
)


def _floor_calls(names: tuple[str, ...] = _FLOORS) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for path in sorted(_SRC.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            callee = node.func
            name = callee.id if isinstance(callee, ast.Name) else getattr(callee, "attr", None)
            if name in names:
                found.append((path.relative_to(_SRC).as_posix(), name))
    return found


def _is_row_container(node: ast.AST, name: str) -> bool:
    """``self.<name>``, possibly under subscripts."""
    while isinstance(node, ast.Subscript):
        node = node.value
    return (
        isinstance(node, ast.Attribute)
        and node.attr == name
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    )


def _is_reset(value: ast.AST | None) -> bool:
    if isinstance(value, ast.Dict):
        return not value.keys
    if isinstance(value, ast.List):
        return not value.elts
    return (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id in {"dict", "list"}
        and not value.args
        and not value.keywords
    )


def _is_settled_extend(node: ast.Call) -> bool:
    """``self._moves.extend(self._settled())``, the one allowed addition to ``_moves``."""
    arg = node.args[0] if len(node.args) == 1 and not node.keywords else None
    return (
        isinstance(node.func, ast.Attribute)
        and node.func.attr == "extend"
        and _is_row_container(node.func.value, _MOVES_LIST)
        and isinstance(arg, ast.Call)
        and not arg.args
        and not arg.keywords
        and isinstance(arg.func, ast.Attribute)
        and arg.func.attr == "_settled"
        and isinstance(arg.func.value, ast.Name)
        and arg.func.value.id == "self"
    )


def _row_additions(method: ast.FunctionDef, name: str) -> Iterator[ast.AST]:
    """Statements of ``method`` that add to ``self.<name>``; resets are not additions."""
    for node in ast.walk(method):
        if isinstance(node, ast.Assign):
            reset = all(isinstance(t, ast.Attribute) for t in node.targets) and _is_reset(
                node.value
            )
            if any(_is_row_container(t, name) for t in node.targets) and not reset:
                yield node
        elif isinstance(node, ast.AnnAssign):
            if _is_row_container(node.target, name) and not (
                isinstance(node.target, ast.Attribute) and _is_reset(node.value)
            ):
                yield node
        elif isinstance(node, ast.AugAssign):
            if _is_row_container(node.target, name):
                yield node
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _ADDERS
            and _is_row_container(node.func.value, name)
        ):
            if not (name == _MOVES_LIST and method.name == _TRACK and _is_settled_extend(node)):
                yield node


def _label_writers_methods() -> dict[str, ast.FunctionDef]:
    tree = ast.parse(_MOVES.read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == _WRITERS)
    return {n.name: n for n in cls.body if isinstance(n, ast.FunctionDef)}


def test_each_accept_floor_has_one_call_site_and_it_is_in_the_proposal_service() -> None:
    """A second call site is a floor T-28's residuals do not account for.

    RED means a floor is called from somewhere other than ``proposal_service``,
    or twice, or no longer: the entry's statement that ``accept`` applies each
    floor once over the replayed pair then describes a different system.
    """
    calls = _floor_calls()

    assert sorted(calls) == sorted((_FLOOR_SITE, name) for name in _FLOORS), (
        f"expected exactly one call of each of {_FLOORS} in the package, in "
        f"{_FLOOR_SITE}; found {calls}"
    )


def test_loosened_after_has_one_call_site_and_it_is_the_landed_overwrite_refusal() -> None:
    """RED means ``loosened_after`` is called from another module, twice, no longer, or
    outside ``_refuse_a_landed_overwrite``: a second end-state refusal T-28 omits."""
    tree = ast.parse((_SRC / _FLOOR_SITE).read_text(encoding="utf-8"))
    refusal = next(
        n for n in ast.walk(tree) if getattr(n, "name", None) == "_refuse_a_landed_overwrite"
    )
    inside = [n for n in ast.walk(refusal) if isinstance(n, ast.Name) and n.id == _END_STATE]

    assert _floor_calls((_END_STATE,)) == [(_FLOOR_SITE, _END_STATE)]
    assert len(inside) == 1, f"{_END_STATE} named {len(inside)} times in the refusal"


def test_only_upserted_and_wrote_add_a_row_source_to_label_writers() -> None:
    """Rows reach ``moves`` from two containers; a third producer would report, and make
    ``accept`` refuse, operations T-28 does not describe as reported.

    RED means a method of ``LabelWriters`` other than ``upserted`` and ``wrote`` adds to
    ``_loosened`` or ``_moves`` (beyond ``_track``'s one ``extend(self._settled())``),
    either no longer does, or that one allowed site is not there exactly once.
    """
    methods = _label_writers_methods()

    producers = sorted(
        name
        for name, fn in methods.items()
        if any(_row_additions(fn, _SOURCE_OF_ROWS)) or any(_row_additions(fn, _MOVES_LIST))
    )
    settled_extends = [
        node
        for node in ast.walk(methods[_TRACK])
        if isinstance(node, ast.Call) and _is_settled_extend(node)
    ]

    assert {*_PRODUCERS, _TRACK} <= methods.keys(), "premise: the methods exist"
    assert len(settled_extends) == 1, "premise: the allowed site is in _track, once"
    assert producers == sorted(_PRODUCERS), f"methods adding to a row container: {producers}"


def test_label_writers_row_containers_are_not_reached_from_outside_the_class() -> None:
    """The scan reads ``self._loosened`` and ``self._moves`` inside ``LabelWriters``, so
    nothing outside the class may name them: a second class with its own
    ``self._loosened`` would pass a filter that checks only the path and ``self``.

    RED means ``_loosened`` is named outside ``class LabelWriters`` anywhere in the
    package, or ``_moves`` outside it in ``permissive_moves.py``.
    """
    tree = ast.parse(_MOVES.read_text(encoding="utf-8"))
    inside = {
        id(node)
        for cls in tree.body
        if isinstance(cls, ast.ClassDef) and cls.name == _WRITERS
        for node in ast.walk(cls)
    }
    assert inside, "premise: LabelWriters is a top-level class of permissive_moves.py"

    outside = [
        f"{path.relative_to(_SRC).as_posix()}:{node.lineno}"
        for path in sorted(_SRC.rglob("*.py"))
        for node in ast.walk(
            tree if path == _MOVES else ast.parse(path.read_text(encoding="utf-8"))
        )
        if isinstance(node, ast.Attribute)
        and id(node) not in inside
        and (node.attr == _SOURCE_OF_ROWS or (path == _MOVES and node.attr == _MOVES_LIST))
    ]

    assert outside == []
