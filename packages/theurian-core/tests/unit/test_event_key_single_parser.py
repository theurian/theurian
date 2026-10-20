"""Pins which ``re`` calls on a literal read digits after a `#`, and who calls ``parse_event_key``.

A census under ``theurian`` (#748), not an inverse test: that is
``test_parse_event_key_inverts_external_key``.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import override

import theurian

SRC = Path(theurian.__file__).parent
#: A regex literal that reads digits after a `#`: the shape of a PR-number parse.
_NUMBER_AFTER_HASH = re.compile(r"#.*(\\d|\[0-9\])")


class _Scan(ast.NodeVisitor):
    def __init__(self, module: str) -> None:
        self.module, self.scope = module, ["<module>"]
        self.regexes: list[str] = []
        self.callers: list[str] = []

    @override
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]  # noqa: N815

    @override
    def visit_Call(self, node: ast.Call) -> None:
        name, func = f"{self.module}:{self.scope[-1]}", node.func
        if isinstance(func, ast.Name) and func.id == "parse_event_key":
            self.callers.append(name)
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "re"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and _NUMBER_AFTER_HASH.search(repr(node.args[0].value))
        ):
            self.regexes.append(name)
        self.generic_visit(node)


def _scan() -> tuple[list[str], list[str]]:
    """``(regex parses, parse_event_key callers)`` as ``module:function`` names."""
    regexes: list[str] = []
    callers: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        scan = _Scan(path.stem)
        scan.visit(ast.parse(path.read_text(encoding="utf-8")))
        regexes += scan.regexes
        callers += scan.callers
    return regexes, callers


def test_one_function_parses_a_pull_request_number_out_of_an_event_key() -> None:
    """#748: two readers each parsed the trailing `#N` alone and ignored provider and repository.

    1 <key predicate> (key: a `re.<fn>(<str literal>)` call, in any module under
    ``theurian``, whose pattern has a `#` followed by `\\d` or `[0-9]`; the callers
    of ``parse_event_key``, by ``module:function``). A parse by ``partition`` or
    ``split`` is outside this key.
    """
    regexes, callers = _scan()

    assert regexes == ["review:<module>"], regexes
    assert sorted(callers) == [
        "candidate_generation:_event",
        "review_search_builder:_pull_request_of",
    ]
