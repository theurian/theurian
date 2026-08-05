"""What every surface says about a registry entry it cannot read (ADR-0002, SEC-13).

``ProjectRegistry.load`` skips an entry that names no root path rather than
refusing the whole file, so one hand-edited line no longer stops every project
on the machine. The price is that the skipped id has to be *reported*, by every
surface, with the one remedy that actually works -- and the remedies here are
opposites. An unregistered id needs ``theurian project register``; an id whose
entry cannot be parsed needs ``theurian project unregister`` first, because
``register`` refuses the id while that entry holds it. A single message for both
sends half its readers into a loop.

These tests are about the *message and the field*, not about retrieval, so they
build a registry file directly instead of a project: ``project.list`` reads that
file and nothing else, and ``_resolve`` fails before it opens any database. The
registry lives under ``THEURIAN_DATA_DIR`` in ``tmp_path``, with ``HOME``
redirected and the working directory moved out of this checkout, because the CLI
half of the cross-surface test runs the real command.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

import pytest
from mcp.server.mcpserver.exceptions import ToolError as SdkToolError
from typer.testing import CliRunner

from theurian.application.project_service import ProjectRegistry
from theurian.cli.main import app
from theurian.daemon.runner import build_server

pytestmark = pytest.mark.integration

runner = CliRunner()

#: The one registration that loads. Present so that "not registered" has a real
#: id to name, and so `Registered:` is never empty -- an empty list would make
#: "the unreadable ids are not folded into it" unfalsifiable.
READABLE_ID: Final = "demo"

#: Two ids whose entries name no root path, in the two shapes a hand edit
#: leaves: a missing key and an empty string. Keyed so that file order and
#: sorted order differ, because both the error message and `project.list` sort
#: them -- a user retypes these into `unregister`, and JSON-file order would
#: read differently on two machines holding the same registry.
BROKEN_IDS: Final = ("alpha-empty-root", "zeta-no-root-key")

REGISTRY_WITH_BROKEN_ENTRIES: Final[dict[str, Any]] = {
    "zeta-no-root-key": {"defaultBranch": "main"},
    READABLE_ID: {"rootPath": "/somewhere/team-one/api"},
    "alpha-empty-root": {"rootPath": ""},
}

#: Every project-scoped tool resolves through ``_resolve``, so each has to carry
#: the same distinction. Only `knowledge.search` was ever asserted on.
PROJECT_SCOPED_TOOLS: Final[dict[str, dict[str, Any]]] = {
    "knowledge.search": {"query": "signed token"},
    "knowledge.get": {"itemId": "architecture.auth-policy"},
    "knowledge.status": {},
}


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A machine whose registry is under ``tmp_path`` and nowhere else."""
    directory = tmp_path / "datadir"
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(directory))
    monkeypatch.chdir(tmp_path)
    return directory


def _write_registry(data_dir: Path, contents: dict[str, Any] | str) -> ProjectRegistry:
    """Put ``contents`` in the registry file, valid JSON or not."""
    registry = ProjectRegistry.default(data_dir)
    registry.path.parent.mkdir(parents=True, exist_ok=True)
    registry.path.write_text(
        contents if isinstance(contents, str) else json.dumps(contents), encoding="utf-8"
    )
    return registry


async def _call(registry: ProjectRegistry, tool: str, **arguments: Any) -> dict[str, Any]:
    result = await build_server(registry).call_tool(tool, arguments)
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        payload: dict[str, Any] = structured
        return payload
    content: Any = result.content  # type: ignore[union-attr]
    loaded: dict[str, Any] = json.loads(content[0].text)
    return loaded


async def _failing(registry: ProjectRegistry, tool: str, **arguments: Any) -> str:
    """The text a client actually sees when a tool refuses.

    Through the SDK's re-raise, not off the exception this module raised: the
    SDK rebuilds the error as ``ToolError(f"Error executing tool {name}: {e}")``,
    which keeps ``str(exc)`` and drops every other attribute. Asserting on the
    ``ProjectError`` directly would pass while the client got a dead end.
    """
    with pytest.raises(SdkToolError) as raised:
        await _call(registry, tool, **arguments)
    return str(raised.value)


def _cli(*args: str) -> dict[str, Any]:
    result = runner.invoke(app, [*args, "--json"], catch_exceptions=False)
    assert result.exit_code == 0, result.stdout + (result.stderr or "")
    payload: dict[str, Any] = json.loads(result.stdout)
    return payload


# -- The two branches of `_resolve` ------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", sorted(PROJECT_SCOPED_TOOLS))
async def test_an_id_whose_entry_is_unreadable_is_sent_to_unregister(
    data_dir: Path, tool: str
) -> None:
    """The remedy loop this branch exists to break.

    ``theurian project register`` refuses an id while a broken entry holds it,
    so a message telling this caller to run it sends them round in a circle:
    register, be told the id is already in use, read the same advice again. The
    escape is ``unregister`` first, and it has to name the id -- a remedy the
    reader cannot type is not a remedy.

    Asserted on every project-scoped tool because all three go through
    ``_resolve``, and only ``knowledge.search`` had ever been asserted on.
    """
    registry = _write_registry(data_dir, REGISTRY_WITH_BROKEN_ENTRIES)

    message = await _failing(registry, tool, projectId=BROKEN_IDS[0], **PROJECT_SCOPED_TOOLS[tool])

    assert f"theurian project unregister {BROKEN_IDS[0]}" in message
    assert "Run `theurian project register`" not in message, (
        "the command that cannot succeed while this entry exists must not be the instruction"
    )
    assert "is not registered" not in message, "an unreadable entry is present, not missing"


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", sorted(PROJECT_SCOPED_TOOLS))
async def test_an_unknown_id_is_sent_to_register_with_the_unreadable_set_named_apart(
    data_dir: Path, tool: str
) -> None:
    """The opposite branch, and the reason the two lists stay separate.

    ``Registered:`` is built from ``load()``, which skips what it cannot parse,
    and a caller reads it as the whole of what this daemon serves. Folding the
    skipped ids into it would give them the ``register`` remedy that cannot
    work; omitting them entirely would leave a user comparing this message
    against their own registry file with a project missing from both the answer
    and the explanation. So they are named beside it, with their own command.
    """
    registry = _write_registry(data_dir, REGISTRY_WITH_BROKEN_ENTRIES)

    message = await _failing(registry, tool, projectId="typo", **PROJECT_SCOPED_TOOLS[tool])

    assert "Run `theurian project register`" in message
    assert f"Registered: {READABLE_ID}." in message, (
        "the unreadable ids must not be listed as projects this daemon serves"
    )
    assert (
        f"Present but unreadable, and served by nothing until removed with "
        f"`theurian project unregister <id>`: {', '.join(BROKEN_IDS)}."
    ) in message, "the skipped ids are named, in a stable order, with their own command"


# -- The remedy the SDK drops ------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("project.list", {}),
        ("knowledge.search", {"projectId": READABLE_ID, "query": "signed token"}),
    ],
    ids=["registry-snapshot", "resolve"],
)
async def test_a_registry_that_is_not_json_reaches_the_client_with_its_cure(
    data_dir: Path, tool: str, arguments: dict[str, Any]
) -> None:
    """``ProjectError`` carries its remedy on an attribute the wire has no room for.

    The SDK re-raises anything escaping a tool as
    ``ToolError(f"Error executing tool {name}: {e}")`` -- ``str(exc)`` survives
    and ``exc.remedy`` does not. A registry file that is not JSON therefore
    reached every agent as an error naming no way out, while
    ``theurian project list`` printed the cure for the same byte of the same
    file. The message is the only field there is, so the remedy is folded into
    it, and that folding is what regresses silently without this test.

    Both call sites are covered: ``project.list`` reads the file through
    ``_registry_snapshot`` and every project-scoped tool through ``_resolve``,
    and each catches ``ProjectError`` separately.
    """
    registry = _write_registry(data_dir, "{not json at all")

    message = await _failing(registry, tool, **arguments)

    assert "cannot be read as JSON" in message, "the failure itself still has to be stated"
    assert "re-register each project with `theurian project register`" in message, (
        "the remedy is on a separate attribute and is dropped unless it is folded in"
    )


# -- One registry, one answer ------------------------------------------------


@pytest.mark.asyncio
async def test_the_daemon_and_the_cli_report_the_same_unreadable_set(data_dir: Path) -> None:
    """Two surfaces read the same file with the same two methods, so they must agree.

    This is the cross-surface half of the closure argument for unreadable
    entries: the ids are published, deliberately, by everything that can publish
    them, because an id is not another project's content (SEC-13) and a remedy
    naming an id no surface prints is untypable. An agent that asked the daemon
    and a user who ran the CLI comparing notes on the same machine must not get
    different answers -- and a change to one surface is exactly how they would.

    The expected value is spelled out rather than only compared, because two
    surfaces that both regressed to ``[]`` would agree perfectly.
    """
    registry = _write_registry(data_dir, REGISTRY_WITH_BROKEN_ENTRIES)

    served = await _call(registry, "project.list")
    listed = _cli("project", "list")

    assert served["unreadable"] == list(BROKEN_IDS)
    assert listed["unreadable"] == list(BROKEN_IDS)
