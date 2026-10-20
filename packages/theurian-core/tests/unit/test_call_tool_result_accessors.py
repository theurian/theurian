"""No test reads a ``CallToolResult`` field through its camelCase serialization alias.

On mcp 2.3.0 ``CallToolResult`` names its fields ``structured_content``,
``is_error`` and ``result_type``; ``structuredContent``, ``isError`` and
``resultType`` are aliases, not attributes, so ``getattr`` of the
alias returns ``None`` on every call. Test files read results that way and
compared ``None`` with ``None`` (#635), among them the two knowledge
absence-proof closures, so their structured-copy comparisons held vacuously.
Population at 1256ca64: 29 lines in 20 files, from ``git grep -nE
'getattr\\([a-z_]+, "(structuredContent|isError|resultType)"' 1256ca64 --
packages/theurian-core/tests tests``.

**Key.** An ``ast.Call`` of the bare name ``getattr`` whose second argument is a
string literal equal to one of :data:`ALIAS_NAMES`, in any ``.py`` file under
``packages/theurian-core/tests`` or ``tests``. A ``getattr`` whose name is
computed (a variable, an f-string) is outside this reach, as is attribute access
by ``__dict__`` or ``vars``; ``result.structuredContent`` written as a plain
attribute fails at runtime and is not scanned either.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

pytestmark = pytest.mark.unit

ALIAS_NAMES = ("structuredContent", "isError", "resultType")

REPO = pathlib.Path(__file__).resolve().parents[4]
SCANNED_ROOTS = (REPO / "packages" / "theurian-core" / "tests", REPO / "tests")


def _alias_getattrs(source: str) -> list[tuple[int, str]]:
    return [
        (node.lineno, str(node.args[1].value))
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and node.args[1].value in ALIAS_NAMES
    ]


def _scan(
    roots: tuple[pathlib.Path, ...], base: pathlib.Path = REPO
) -> dict[str, list[tuple[int, str]]]:
    found: dict[str, list[tuple[int, str]]] = {}
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            if hits := _alias_getattrs(path.read_text(encoding="utf-8")):
                found[str(path.relative_to(base))] = hits
    return found


def test_no_test_reads_a_call_tool_result_field_through_its_alias() -> None:
    offenders = _scan(SCANNED_ROOTS)

    assert not offenders, (
        "a test reads a CallToolResult field with getattr and its camelCase alias, "
        "which returns the default on every call and turns the comparison into "
        "None == None; read result.structured_content / result.is_error / "
        "result.result_type and assert the value is not None:\n"
        + "\n".join(
            f"  {path}:{line} :: {name}" for path, hits in offenders.items() for line, name in hits
        )
    )


def test_the_scan_walks_a_real_population() -> None:
    walked = [p for root in SCANNED_ROOTS for p in root.rglob("test_*.py")]

    assert len(walked) > 100


@pytest.mark.parametrize("name", ALIAS_NAMES)
def test_the_scan_goes_red_on_each_forbidden_name(name: str, tmp_path: pathlib.Path) -> None:
    (tmp_path / "test_planted.py").write_text(
        f'def helper(result):\n    return getattr(result, "{name}", None)\n', encoding="utf-8"
    )

    assert _scan((tmp_path,), tmp_path) == {"test_planted.py": [(2, name)]}


@pytest.mark.parametrize(
    "source",
    [
        'getattr(result, "structured_content", None)',
        'getattr(result, "is_error", None)',
        'getattr(result, "result_type", None)',
        "getattr(result, name, None)",
        'getattr(result, f"{prefix}Content", None)',
        'getattr(result, "content", None)',
        "result.structured_content",
    ],
)
def test_the_scan_stays_green_on_the_correct_spelling_and_on_computed_names(source: str) -> None:
    assert _alias_getattrs(source) == []
