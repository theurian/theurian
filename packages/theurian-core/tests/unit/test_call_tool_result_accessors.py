"""No test reads a ``CallToolResult`` field through its camelCase serialization alias.

On mcp 2.3.0 ``CallToolResult`` names its fields ``structured_content``,
``is_error`` and ``result_type``; ``structuredContent``, ``isError`` and
``resultType`` are aliases, not attributes, so ``getattr`` of the
alias returns ``None`` on every call. Test helpers read results that way
(#635) and took their ``if structured is not None`` branch never, so they fell back
to the text block and the structured copy of a response was never read, among them
the two knowledge absence-proof closures.
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
from mcp.types import CallToolResult, TextContent
from tool_result import structured_payload

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
        "which returns the default on every call, so the field is never read; "
        "read result.structured_content / result.is_error / result.result_type "
        "and assert the value is not None:\n"
        + "\n".join(
            f"  {path}:{line} :: {name}" for path, hits in offenders.items() for line, name in hits
        )
    )


def test_the_scanned_roots_are_every_tests_directory_and_each_holds_modules() -> None:
    assert set(SCANNED_ROOTS) == {REPO / "tests", *(REPO / "packages").glob("*/tests")}
    for root in SCANNED_ROOTS:
        assert any(root.rglob("*.py")), f"{root} holds no module, so the scan reads nothing there"


@pytest.mark.parametrize("name", ALIAS_NAMES)
def test_the_scan_goes_red_on_each_forbidden_name_in_every_root_and_any_module_name(
    name: str, tmp_path: pathlib.Path
) -> None:
    roots = (tmp_path / "one", tmp_path / "two")
    (roots[0] / "nested").mkdir(parents=True)
    roots[1].mkdir()
    (roots[0] / "nested" / "helpers.py").write_text(
        f'def helper(result):\n    return getattr(result, "{name}", None)\n', encoding="utf-8"
    )
    (roots[1] / "conftest.py").write_text(
        f'x = getattr(result, "{name}", None)\n', encoding="utf-8"
    )

    assert _scan(roots, tmp_path) == {
        "one/nested/helpers.py": [(2, name)],
        "two/conftest.py": [(1, name)],
    }


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


def _result(
    text: str = '{"a": 1}',
    structured: dict[str, object] | None = {"a": 1},  # noqa: B006
    *,
    blocks: int = 1,
    is_error: bool = False,
) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=text)] * blocks,
        structured_content=structured,
        is_error=is_error,
    )


@pytest.mark.parametrize(
    ("result", "reason"),
    [
        (_result('{"a": 1, "b": 2}'), "disagree"),
        (_result(blocks=2), "other than one content block"),
        (_result('{"a": true}', {"a": 1}), "disagree"),
        (_result(is_error=True), "error flag"),
        (_result(structured=None), "no structured copy"),
    ],
    ids=["extra-key-in-text", "two-blocks", "true-vs-one", "error-flag", "no-structured-copy"],
)
def test_structured_payload_refuses_a_result_whose_copies_do_not_agree(
    result: CallToolResult, reason: str
) -> None:
    """Accepting any of these would let a one-copy defect pass every #635 closure."""
    with pytest.raises(AssertionError, match=reason):
        structured_payload(result)


def test_structured_payload_returns_the_structured_copy_of_an_agreeing_result() -> None:
    assert structured_payload(_result()) == {"a": 1}
