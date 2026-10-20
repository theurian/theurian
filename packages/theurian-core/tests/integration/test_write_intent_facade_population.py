"""Four keys for "a write-intent tool" must name the same set.

ADR-0032 decision 4, "Evidence requiredness is preserved over the wire, and it is
already enforced twice" (``docs/adr/0032-the-write-intent-mcp-tool-surface.md``),
puts ``evidence`` among the fields required on every write-intent call. The e2e
session derives its write-intent tools from that (``"evidence"`` in the
``required`` of the ``inputSchema`` that ``tools/list`` serves, which the SDK
builds from the handler signature, not from the ``schemas/mcp`` files), the
draft-only walk in ``test_mcp_tools.py`` (``WRITE_INTENT_TOOLS``) and the
flag coupling in ``test_write_tools_flag_claims.py`` (``_WRITE_INTENT_TOOL_NAMES``)
list them by hand, and the facade key (:mod:`write_intent_population`)
reads the registration. Before this pin none of the four checked another (#711).

Residual, not closed: a tool that drafts without the facade, requires no
``evidence`` and is absent from both hand lists is seen by none of the four keys.

The two hand lists are read from their modules' source by syntax tree.
No test module imports another at 6c3c2827 (key: ``git grep -n -E
'^from test_[a-zA-Z0-9_]+ import|^import test_[a-zA-Z0-9_]+' 6c3c2827 --
packages/theurian-core/tests tests`` printed nothing), and ``test_mcp_tools.py``
is 9646 lines at that commit (``wc -l``), so importing it for one constant would
execute its module body.
"""

from __future__ import annotations

import ast
import asyncio
import json
import pathlib
from typing import Any, Final

import pytest
from write_intent_population import demo_project, write_intent_names  # noqa: F401

from theurian.application.project_service import ProjectRegistry
from theurian.daemon.runner import build_server
from theurian.domain.identifiers import ProposalId

_HAND_LISTS: Final = (
    ("test_mcp_tools.py", "WRITE_INTENT_TOOLS"),
    ("../unit/test_write_tools_flag_claims.py", "_WRITE_INTENT_TOOL_NAMES"),
)


def _hand_list(relative: str, name: str) -> frozenset[str]:
    """The ``frozenset({...})`` literal bound to *name*, read without importing the module."""
    path = pathlib.Path(__file__).parent / relative
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if any(isinstance(t, ast.Name) and t.id == name for t in targets) and isinstance(
            node.value, ast.Call
        ):
            return frozenset(ast.literal_eval(node.value.args[0]))
    raise AssertionError(f"{name} not found in {relative}")


def evidence_required_names(tools: Any) -> frozenset[str]:
    return frozenset(
        tool.name for tool in tools if "evidence" in tool.parameters.get("required", ())
    )


def disagreements(
    by_schema: frozenset[str], by_facade: frozenset[str], *by_hand_lists: frozenset[str]
) -> dict[str, frozenset[str]]:
    """Names some key holds and another lacks, against the facade key; empty when all agree."""
    pairs = {"evidence-required": by_schema} | {
        f"hand list {i}": names for i, names in enumerate(by_hand_lists)
    }
    return {label: names ^ by_facade for label, names in pairs.items() if names != by_facade}


@pytest.fixture
def tools(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(tmp_path / "datadir"))
    server = build_server(ProjectRegistry.default(tmp_path / "datadir"))
    return list(server._tool_manager.list_tools())


@pytest.mark.integration
def test_the_four_write_intent_keys_name_the_same_tools(tools: list[Any]) -> None:
    assert write_intent_names(tools), "the facade key found no tool, so nothing is compared"

    assert not disagreements(
        evidence_required_names(tools),
        write_intent_names(tools),
        *(_hand_list(*entry) for entry in _HAND_LISTS),
    )


@pytest.mark.parametrize(
    ("by_schema", "by_facade", "first_list", "second_list"),
    [
        (set(), {"t"}, {"t"}, {"t"}),
        ({"t"}, set(), {"t"}, {"t"}),
        ({"t"}, {"t"}, set(), {"t"}),
        ({"t"}, {"t"}, {"t"}, {"t", "u"}),
    ],
)
def test_the_comparison_reports_a_name_missing_from_any_one_key(
    by_schema: set[str], by_facade: set[str], first_list: set[str], second_list: set[str]
) -> None:
    assert disagreements(
        frozenset(by_schema), frozenset(by_facade), frozenset(first_list), frozenset(second_list)
    )


def test_the_comparison_is_quiet_when_all_four_agree() -> None:
    names = frozenset({"t"})

    assert not disagreements(names, names, names, names)


_EVIDENCE: Final = {
    "agentId": "claude-code",
    "taskId": "task-7",
    "model": "claude-opus-5",
    "reasoning": "The alias review settled that the item should be deprecated.",
}


def _cell(fn: Any, name: str) -> Any:
    body = getattr(fn, "__wrapped__", fn)
    assert body.__closure__ is not None
    return body.__closure__[body.__code__.co_freevars.index(name)].cell_contents


def _tree_bytes(root: pathlib.Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


@pytest.mark.integration
def test_accept_reached_through_the_facades_captured_service_fails_closed(
    demo: ProjectRegistry,
) -> None:
    """The write surface's accept collaborators are wired to raise, not to no-op
    (ADR-0032 decision 8); a backstop replaced by ``return ()`` kept
    259 tests green at 6c3c2827 (key: test_mcp_tools.py,
    test_write_intent_wire.py, test_write_intent_disclosure_oracle.py,
    unit/test_draft_only_proposals.py). RED driver: that mutant.

    Reflective by design: no attribute walk enumerates the service, which lives
    only in the facade's closure cells. Cannot see a path to a mover that does not
    go through the facade's captured service.
    """
    server = build_server(demo)
    root = pathlib.Path(demo.load()["demo"]["rootPath"])
    drafted: Any = asyncio.run(
        server.call_tool(
            "knowledge.generateMigrationDraft",
            {
                "projectId": "demo",
                "document": {
                    "author": "platform-team@example.com",
                    "operations": [{"op": "deprecateItem", "itemId": "architecture.auth-policy"}],
                },
                "evidence": _EVIDENCE,
            },
        )
    )
    proposal_id = json.loads(drafted.content[0].text)["proposalId"]
    tool = next(t for t in server._tool_manager.list_tools() if t.name == "knowledge.proposeChange")
    factory = _cell(tool.fn, "_draft_only_proposals")
    facade, _paths = factory("demo")
    draft = _cell(facade._draft, "draft")
    service = draft.__self__
    before = {tree: _tree_bytes(root / ".theurian" / tree) for tree in ("knowledge", "migrations")}
    pending = root / ".theurian/proposals" / proposal_id
    assert pending.is_dir()

    with pytest.raises(RuntimeError, match=r"ADR-0032 decision 8"):
        service.accept(ProposalId(proposal_id))

    assert before == {
        tree: _tree_bytes(root / ".theurian" / tree) for tree in ("knowledge", "migrations")
    }
    assert pending.is_dir()


_SRC: Final = pathlib.Path(__file__).resolve().parents[2] / "src"
#: The surfaces whose facade wiring a test drives, each with the node id of that test.
_DRIVEN: Final = {"theurian/mcp/tools.py": 1, "theurian/cli/okf_commands.py": 1}
_WIRING_TESTS: Final = {
    "theurian/mcp/tools.py": (
        "test_write_intent_facade_population.py"
        "::test_accept_reached_through_the_facades_captured_service_fails_closed"
    ),
    "theurian/cli/okf_commands.py": (
        "test_okf_cli.py"
        "::test_the_import_service_is_handed_the_draft_only_facade_and_not_the_service"
    ),
}


def missing_tests(node_ids: list[str]) -> list[str]:
    """Node ids (``file::function``, file beside this one) whose function is not defined."""
    missing = []
    for node_id in node_ids:
        file, _, function = node_id.partition("::")
        tree = ast.parse((pathlib.Path(__file__).parent / file).read_text(encoding="utf-8"))
        if not any(isinstance(n, ast.FunctionDef) and n.name == function for n in ast.walk(tree)):
            missing.append(node_id)
    return missing


def _named(node: ast.expr) -> str | None:
    return node.id if isinstance(node, ast.Name) else getattr(node, "attr", None)


def facade_surfaces(sources: dict[str, str]) -> dict[str, dict[str, int]]:
    """Per kind, per file: ``DraftOnlyProposals(...)`` calls (plain or qualified),
    subclasses of it, and calls passing ``drafts=``, by syntax tree."""
    found: dict[str, dict[str, int]] = {"sites": {}, "subclasses": {}, "consumers": {}}
    for name, text in sources.items():
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Call):
                if _named(node.func) == "DraftOnlyProposals":
                    found["sites"][name] = found["sites"].get(name, 0) + 1
                if any(
                    k.arg == "drafts"
                    or (
                        k.arg is None
                        and isinstance(k.value, ast.Dict)
                        and any(
                            isinstance(d, ast.Constant) and d.value == "drafts"
                            for d in k.value.keys
                        )
                    )
                    for k in node.keywords
                ):
                    found["consumers"][name] = found["consumers"].get(name, 0) + 1
            elif isinstance(node, ast.ClassDef) and any(
                _named(base) == "DraftOnlyProposals" for base in node.bases
            ):
                found["subclasses"][name] = found["subclasses"].get(name, 0) + 1
    return found


@pytest.mark.integration
def test_every_facade_surface_is_one_a_wiring_test_drives() -> None:
    """A third construction site, a subclass, or a third consumer means a surface
    carrying the draft-only promise that needs its own test going RED when its wiring
    is replaced (#708). Measured at 6c3c2827: 2 construction sites (key: ``git grep -n
    -F 'DraftOnlyProposals(' 6c3c2827 -- packages/theurian-core/src`` ->
    mcp/tools.py:1975, cli/okf_commands.py:384), 0 subclasses, and 2 ``drafts=``
    consumers (key: ``git grep -n -E 'drafts=' 6c3c2827 -- packages/theurian-core/src``
    -> mcp/tools.py:3546, cli/okf_commands.py:384). RED drivers: a planted third site,
    qualified site, subclass, and consumer, each in a private copy.

    Each surface is paired with its wiring test's node id in ``_WIRING_TESTS``, and
    the function must exist there; that it still drives the surface is not read.

    Cannot see a facade built reflectively or under an alias, nor a consumer that
    takes it under another keyword or positionally, through ``**kwargs`` from a
    non-literal mapping, or through a ``**{...}`` literal with a computed key.
    """
    sources = {
        str(path.relative_to(_SRC)): path.read_text(encoding="utf-8")
        for path in sorted(_SRC.rglob("*.py"))
    }

    assert facade_surfaces(sources) == {
        "sites": _DRIVEN,
        "subclasses": {},
        "consumers": _DRIVEN,
    }
    assert set(_WIRING_TESTS) == set(_DRIVEN)
    assert not missing_tests(list(_WIRING_TESTS.values()))


def test_a_wiring_test_that_is_renamed_is_reported_missing() -> None:
    here = pathlib.Path(__file__).name

    assert missing_tests([f"{here}::test_no_such_wiring_test"]) == [
        f"{here}::test_no_such_wiring_test"
    ]
    assert not missing_tests([f"{here}::test_the_comparison_is_quiet_when_all_four_agree"])


def test_the_surface_scan_counts_each_shape_and_ignores_a_mention() -> None:
    sources = {
        "a.py": (
            "x = DraftOnlyProposals(s)\ny = m.DraftOnlyProposals(t)\n"
            "f(drafts=x)\ng(**{'drafts': x})\n"
        ),
        "b.py": '"""DraftOnlyProposals(s)"""\nclass C(DraftOnlyProposals): ...\n',
    }

    assert facade_surfaces(sources) == {
        "sites": {"a.py": 2},
        "subclasses": {"b.py": 1},
        "consumers": {"a.py": 2},
    }
