"""Three keys for "a write-intent tool" must name the same set.

ADR-0032 decision 4, "Evidence requiredness is preserved over the wire, and it is
already enforced twice" (``docs/adr/0032-the-write-intent-mcp-tool-surface.md``),
puts ``evidence`` among the fields required on every write-intent call. The e2e
session derives its write-intent tools from that (``"evidence"`` in the
``required`` of the ``inputSchema`` that ``tools/list`` serves, which the SDK
builds from the handler signature, not from the ``schemas/mcp`` files), the
draft-only walk in ``test_mcp_tools.py`` lists them by hand
(``WRITE_INTENT_TOOLS``), and the facade key (:mod:`write_intent_population`)
reads the registration. Before this pin none of the three checked another (#711).

Residual, not closed: a tool that drafts without the facade, requires no
``evidence`` and is absent from the hand list is seen by none of the three keys.

``WRITE_INTENT_TOOLS`` is read from ``test_mcp_tools.py``'s source by syntax tree.
No test module imports another at 6c3c2827 (key: ``git grep -n -E
'^from test_[a-zA-Z0-9_]+ import|^import test_[a-zA-Z0-9_]+' 6c3c2827 --
packages/theurian-core/tests tests`` printed nothing), and ``test_mcp_tools.py``
is 9646 lines at that commit (``wc -l``), so importing it for one constant would
run its collection.
"""

from __future__ import annotations

import ast
import pathlib
from typing import Any

import pytest
from write_intent_population import write_intent_names

from theurian.application.project_service import ProjectRegistry
from theurian.daemon.runner import build_server

_HAND_LIST_FILE = pathlib.Path(__file__).with_name("test_mcp_tools.py")


def _hand_list() -> frozenset[str]:
    for node in ast.parse(_HAND_LIST_FILE.read_text(encoding="utf-8")).body:
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "WRITE_INTENT_TOOLS" for t in node.targets)
            and isinstance(node.value, ast.Call)
        ):
            return frozenset(ast.literal_eval(node.value.args[0]))
    raise AssertionError("WRITE_INTENT_TOOLS not found in test_mcp_tools.py")


def evidence_required_names(tools: Any) -> frozenset[str]:
    return frozenset(
        tool.name for tool in tools if "evidence" in tool.parameters.get("required", ())
    )


def disagreements(
    by_schema: frozenset[str], by_facade: frozenset[str], by_hand_list: frozenset[str]
) -> dict[str, frozenset[str]]:
    """Names some key holds and another lacks, per pair; empty when all three agree."""
    pairs = {
        "evidence-required vs facade": (by_schema, by_facade),
        "facade vs hand list": (by_facade, by_hand_list),
    }
    return {label: left ^ right for label, (left, right) in pairs.items() if left != right}


@pytest.fixture
def tools(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(tmp_path / "datadir"))
    server = build_server(ProjectRegistry.default(tmp_path / "datadir"))
    return list(server._tool_manager.list_tools())


@pytest.mark.integration
def test_the_three_write_intent_keys_name_the_same_tools(tools: list[Any]) -> None:
    assert write_intent_names(tools), "the facade key found no tool, so nothing is compared"

    assert not disagreements(
        evidence_required_names(tools), write_intent_names(tools), _hand_list()
    )


@pytest.mark.integration
@pytest.mark.parametrize(
    ("by_schema", "by_facade", "by_hand_list"),
    [
        (set(), {"t"}, {"t"}),
        ({"t"}, set(), {"t"}),
        ({"t"}, {"t"}, set()),
        ({"t"}, {"t"}, {"t", "u"}),
    ],
)
def test_the_comparison_reports_a_name_missing_from_any_one_key(
    by_schema: set[str], by_facade: set[str], by_hand_list: set[str]
) -> None:
    assert disagreements(frozenset(by_schema), frozenset(by_facade), frozenset(by_hand_list))


@pytest.mark.integration
def test_the_comparison_is_quiet_when_all_three_agree() -> None:
    names = frozenset({"t"})

    assert not disagreements(names, names, names)
