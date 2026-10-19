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
import asyncio
import json
import pathlib
import subprocess
from collections.abc import Iterator
from typing import Any, Final

import pytest
from git_harness import commit_migrations
from migration_fixtures import body_pin
from typer.testing import CliRunner
from write_intent_population import write_intent_names

from theurian.application.project_service import ProjectRegistry
from theurian.cli.main import app
from theurian.daemon.runner import build_server
from theurian.domain.identifiers import ProposalId

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


_MIGRATION_ID: Final = "01K1AAAAAA01234567890ABCDE"
_BODY: Final = "# Authentication policy\n\nEvery call carries a signed token.\n"
_MIGRATION: Final = f"""apiVersion: theurian.dev/v1
id: {_MIGRATION_ID}
createdAt: 2026-08-02T10:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.auth-policy
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.auth-policy
    revisionId: 01K1AAAREV01234567890ABCDE
    contentFile: ../knowledge/architecture/auth-policy.md
    contentSha256: {body_pin(_BODY)}
    metadata:
      title: Authentication policy
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/auth-policy.md
"""
_EVIDENCE: Final = {
    "agentId": "claude-code",
    "taskId": "task-7",
    "model": "claude-opus-5",
    "reasoning": "The alias review settled that the item should be deprecated.",
}


def _cli(*args: str) -> None:
    result = CliRunner().invoke(app, [*args, "--json"], catch_exceptions=False)
    assert result.exit_code == 0, result.stdout + (result.stderr or "")


@pytest.fixture
def demo(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ProjectRegistry]:
    """A registered, built ``demo`` project with one approved item, via the real CLI."""
    root = tmp_path / "demo"
    root.mkdir()
    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(tmp_path / "datadir"))
    monkeypatch.chdir(root)

    _cli("init")
    (root / ".theurian/knowledge/architecture/auth-policy.md").write_text(_BODY)
    (root / f".theurian/migrations/{_MIGRATION_ID}-auth.yaml").write_text(_MIGRATION)
    _cli("project", "register")
    commit_migrations()
    _cli("migrate", "apply")

    yield ProjectRegistry.default(tmp_path / "datadir")


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
    (ADR-0032 decision 8); the unit walks never call them, so a backstop replaced
    by ``return ()`` kept 259 tests green at 6c3c2827 (key: test_mcp_tools.py,
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
