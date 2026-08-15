"""A revision id offered to a second item, end to end (SEC-13, INV-1, INV-2).

The shape is a copy-pasted `upsertRevision`: the `itemId` was changed and the
`revisionId` was not. The store used to answer the second operation as an FR-K8
no-op, so the second item never got a revision of its own and its
`current_revision_id` was left pointing at the first item's row.

That is not a defect any reader can catch. `knowledge.get`, the search fallback,
the ranked path and the index builder all dereference `current_revision_id`, and
they are right to -- the gate clears the *item*, and the item is the one the
caller named. Measured on this CLI before the fix, ``migrate apply`` accepted the
migration below with exit 0, and `knowledge.get` for the approved id answered
with the rejected item's id, title, source anchors and full body.

So these tests are written from the caller's side: the refusal, and then the
withheld body reaching nothing. Both halves are needed. A test that only asserts
the refusal passes on a build that refuses for the wrong reason and leaks
anyway, and a test that only asserts the absence passes on a build where the
retrievers reach nothing at all -- which is why the precondition below pins that
the withheld row is present and matchable, and that `rejected` is what keeps it
from a caller.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from theurian.application.project_service import ProjectPaths, ProjectRegistry, read_active_state
from theurian.cli.main import app
from theurian.daemon.runner import build_server

pytestmark = pytest.mark.integration

runner = CliRunner()

#: Exit code Core uses for a knowledge-state problem the user must resolve.
EXIT_STATE_ERROR = 4

WITHHELD_ITEM = "architecture.withheld-credentials"
PUBLISHED_ITEM = "architecture.public-note"

SHARED_REVISION = "01K1AAAREV01234567890ABCDE"
WITHHELD_MIGRATION = "01K1AAAAAA01234567890ABCDE"
REUSING_MIGRATION = "01K1BBBBBB01234567890ABCDE"

#: A cell that is a digest to no reader and holds nothing this codebase says
#: elsewhere, so a fragment of it in a response came out of the withheld body.
SENTINEL = "ROTATE-ME sk-live-9f2a7c41d8e3 payroll band L7 = 240000"
WITHHELD_BODY = f"# Withheld credentials\n\nThe production key is {SENTINEL}.\n"

#: Applied first, and it stays applied: the author wrote the document, the review
#: rejected it, and `rejected` is what withholds it from every read path.
WITHHELD_MIGRATION_YAML = f"""apiVersion: theurian.dev/v1
id: {WITHHELD_MIGRATION}
createdAt: 2026-08-02T10:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: {WITHHELD_ITEM}
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: {WITHHELD_ITEM}
    revisionId: {SHARED_REVISION}
    contentFile: ../knowledge/architecture/withheld-credentials.md
    metadata:
      title: Withheld credentials
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: rejected
      owner: platform-team
      trustLevel: inferred
      sensitivity: restricted
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/withheld-credentials.md
"""

#: The copy-paste. `itemId` and `status` were edited; `revisionId` was not.
REUSING_MIGRATION_YAML = f"""apiVersion: theurian.dev/v1
id: {REUSING_MIGRATION}
createdAt: 2026-08-02T11:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: {PUBLISHED_ITEM}
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: {PUBLISHED_ITEM}
    revisionId: {SHARED_REVISION}
    contentFile: ../knowledge/architecture/withheld-credentials.md
    metadata:
      title: Public note
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      trustLevel: reviewed
      sensitivity: public
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/public-note.md
"""


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A registered project holding one applied, rejected item.

    Applied on its own first, so the reuse below lands against *existing* state.
    A reuse inside a single migration is refused before any database is created,
    and a test written only that way passes on a build that refuses nothing and
    merely never wrote anything down.
    """
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

    _run("init")
    (root / ".theurian/knowledge/architecture/withheld-credentials.md").write_text(WITHHELD_BODY)
    _write(root, WITHHELD_MIGRATION, WITHHELD_MIGRATION_YAML)
    _run("project", "register")
    _run("migrate", "apply")

    yield root


@pytest.fixture
def registry(tmp_path: Path) -> ProjectRegistry:
    return ProjectRegistry.default(tmp_path / "datadir")


def _write(root: Path, migration_id: str, body: str) -> None:
    (root / f".theurian/migrations/{migration_id}-m.yaml").write_text(body)


def _run(*args: str) -> None:
    result = runner.invoke(app, [*args, "--json"], catch_exceptions=False)
    assert result.exit_code == 0, result.stdout + (result.stderr or "")


def _run_failing(*args: str) -> dict[str, str]:
    """Invoke a command that must refuse, and return the report a user reads."""
    result = runner.invoke(app, [*args, "--json"], catch_exceptions=False)
    assert result.exit_code == EXIT_STATE_ERROR, (
        f"`theurian {' '.join(args)}` exited {result.exit_code}: {result.stdout}"
    )
    report: dict[str, str] = json.loads(result.stderr)
    return report


def _apply_whatever_it_does(*args: str) -> None:
    """Run a command and look at nothing it returns.

    For the tests whose subject is the state afterwards rather than the exit
    code. Asserting the refusal first would put every one of them behind it, and
    a build that refuses for some unrelated reason would then satisfy them all
    without the property underneath ever being read.
    """
    runner.invoke(app, [*args, "--json"], catch_exceptions=False)


def _json(*args: str) -> dict[str, Any]:
    result = runner.invoke(app, [*args, "--json"], catch_exceptions=False)
    assert result.exit_code == 0, result.stdout + (result.stderr or "")
    payload: dict[str, Any] = json.loads(result.stdout)
    return payload


async def _call(registry: ProjectRegistry, tool: str, **arguments: Any) -> str:
    """Everything one tool call would put in front of an agent, as one string.

    A refusal is an outcome here rather than an error: both `knowledge.get` for
    an item that is not published and `knowledge.get` for one that does not exist
    raise, and this file's question is only ever whether the withheld body is in
    what came back.
    """
    try:
        result = await build_server(registry).call_tool(tool, arguments)
    except Exception as exc:
        return str(exc)
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return json.dumps(structured, ensure_ascii=False)
    return json.dumps([block.text for block in result.content], ensure_ascii=False)  # type: ignore[union-attr]


async def _payload(registry: ProjectRegistry, tool: str, **arguments: Any) -> dict[str, Any]:
    """The same call as :func:`_call`, for the one test that reads its fields."""
    result = await build_server(registry).call_tool(tool, arguments)
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return dict(structured)
    loaded: dict[str, Any] = json.loads(result.content[0].text)  # type: ignore[union-attr]
    return loaded


async def _everything_a_caller_can_reach(registry: ProjectRegistry) -> str:
    """The four responses the finding travelled through, concatenated.

    Written as one string on purpose. The defect put the withheld revision behind
    a *different* item's id, so a per-response assertion keyed on the item asked
    for is exactly the reasoning that missed it.
    """
    return "\n".join(
        [
            await _call(registry, "knowledge.get", projectId="demo", itemId=PUBLISHED_ITEM),
            await _call(registry, "knowledge.get", projectId="demo", itemId=WITHHELD_ITEM),
            await _call(registry, "knowledge.search", projectId="demo", query="production key"),
            await _call(registry, "knowledge.search", projectId="demo", query="credentials"),
        ]
    )


# -- The refusal -----------------------------------------------------------


def test_a_migration_reusing_a_revision_id_across_items_is_refused(project: Path) -> None:
    """``migrate apply`` must not exit 0 on this set. It did."""
    _write(project, REUSING_MIGRATION, REUSING_MIGRATION_YAML)

    report = _run_failing("migrate", "apply")

    assert SHARED_REVISION in report["error"], "the reused id is the line the author must edit"
    assert WITHHELD_ITEM in report["error"], "and the item that already holds it"
    assert PUBLISHED_ITEM in report["error"], "and the item that tried to claim it"
    assert SENTINEL not in json.dumps(report), "a refusal is not a place to quote the body"


def test_the_claiming_item_is_written_into_no_database(project: Path) -> None:
    """All operations in one migration share one transaction (ADR-0005).

    The `createItem` preceding the reused `upsertRevision` must go back with it,
    or the next `migrate apply` starts from a half-written item pointing at a
    revision that belongs to another.

    Asserted over *every* state database in the project, not the one the pointer
    names. Adding a migration file shifts the state hash, and the hash is in the
    database's filename (ADR-0017), so this apply built a second, empty file
    beside the one the fixture left -- and a check aimed at a single path would
    look straight past whichever of the two the write actually reached.
    """
    _write(project, REUSING_MIGRATION, REUSING_MIGRATION_YAML)

    _apply_whatever_it_does("migrate", "apply")

    for database in _databases(project):
        with closing(sqlite3.connect(database)) as raw, raw:
            items = {row[0] for row in raw.execute("SELECT item_id FROM knowledge_items")}
            owners = {
                (row[0], row[1])
                for row in raw.execute("SELECT revision_id, item_id FROM knowledge_revisions")
            }
        assert PUBLISHED_ITEM not in items, f"the refused item was written to {database.name}"
        assert owners <= {(SHARED_REVISION, WITHHELD_ITEM)}, (
            f"{database.name} holds a revision the refused migration wrote: {owners}"
        )

    served = _active_database(project)
    with closing(sqlite3.connect(served)) as raw, raw:
        pointers = raw.execute(
            "SELECT item_id, current_revision_id FROM knowledge_items"
        ).fetchall()
    assert pointers == [(WITHHELD_ITEM, SHARED_REVISION)], (
        "the state the tools read must be the one the refusal found"
    )


# -- What the caller can reach --------------------------------------------


@pytest.mark.asyncio
async def test_the_withheld_body_reaches_no_tool_whatever_the_apply_did(
    project: Path, registry: ProjectRegistry
) -> None:
    """The finding itself: `knowledge.get` for the *approved* id served the body.

    Asked across both ids and both search paths, because the disclosure was not
    a field on the response for the item that carried it -- it was which row the
    pointer resolved to.

    The apply's own outcome is deliberately not asserted here. It is asserted
    above, and repeating it would make this test fail on the exit code first,
    which is the one line of it a build could satisfy while still handing the
    body to a caller. Verified by removing the store's guard: this test then
    fails on ``SENTINEL not in reachable`` rather than on an exit code.
    """
    _write(project, REUSING_MIGRATION, REUSING_MIGRATION_YAML)
    _apply_whatever_it_does("migrate", "apply")

    reachable = await _everything_a_caller_can_reach(registry)

    assert SENTINEL not in reachable, "the rejected item's body reached a caller"
    assert "Withheld credentials" not in reachable, "and so did its title"


@pytest.mark.asyncio
async def test_the_withheld_body_is_in_the_state_this_fixture_built(
    project: Path, registry: ProjectRegistry
) -> None:
    """The precondition, without which the assertion above proves nothing.

    An answer that omits a secret because nothing was ever written is not a gate
    working. The row is present, the retrievers can match its text, and what
    keeps it from a caller is `rejected`.
    """
    with closing(sqlite3.connect(_active_database(project))) as raw, raw:
        stored = raw.execute(
            "SELECT body, status FROM knowledge_revisions WHERE revision_id = ?",
            (SHARED_REVISION,),
        ).fetchone()

    assert stored is not None, "the fixture applied nothing"
    assert SENTINEL in stored[0], "the withheld body is not the text these tests search for"
    assert stored[1] == "rejected"

    found = await _call(registry, "knowledge.search", projectId="demo", query="production key")
    assert SENTINEL not in found, "and the status is what withholds it"


# -- FR-K8: the legitimate case the guard must not break -------------------


def test_re_applying_the_same_migration_stays_a_no_op(project: Path) -> None:
    """The input the idempotency check exists for, and the one it is decided on.

    A guard keyed on the revision id alone would answer this the same way it
    answered the reuse; a guard keyed on the *whole* revision has to let this
    through unchanged. Run twice more, because the first re-run is the one that
    repeats every append and the second proves the first wrote nothing new.
    """
    first = _json("migrate", "apply")
    second = _json("migrate", "apply")

    assert (first["changed"], second["changed"]) == (False, False)
    assert first["skipped"] == second["skipped"] == [WITHHELD_MIGRATION]

    with closing(sqlite3.connect(_active_database(project))) as raw, raw:
        revisions = raw.execute("SELECT COUNT(*) FROM knowledge_revisions").fetchone()[0]
    assert revisions == 1, "a repeated append must not duplicate the row"


@pytest.mark.asyncio
async def test_a_second_item_may_still_carry_the_same_content(
    project: Path, registry: ProjectRegistry
) -> None:
    """Only the id is spent. Two items sharing a `contentFile` is not the defect.

    Worth pinning separately: the cheapest wrong fix is to refuse on the content
    hash, which would forbid a second item from citing the same document and
    would still pass a reused id carrying different content. This is also what
    the corrected migration looks like, so it says what the refusal above asks
    the author for.
    """
    own_revision = "01K1CCCREV01234567890ABCDE"
    _write(
        project,
        REUSING_MIGRATION,
        REUSING_MIGRATION_YAML.replace(SHARED_REVISION, own_revision),
    )

    report = _json("migrate", "apply")
    published = await _payload(registry, "knowledge.get", projectId="demo", itemId=PUBLISHED_ITEM)

    assert REUSING_MIGRATION in report["applied"]
    assert (published["itemId"], published["revisionId"]) == (PUBLISHED_ITEM, own_revision), (
        "an item must answer with a revision of its own"
    )
    assert published["title"] == "Public note", "and with its own metadata, not the other item's"


def _databases(root: Path) -> tuple[Path, ...]:
    databases = tuple(sorted((root / ".theurian/state").glob("*.sqlite")))
    assert databases, "the fixture built no state database at all"
    return databases


def _active_database(root: Path) -> Path:
    """The database the MCP tools would read, per the active pointer."""
    active = read_active_state(ProjectPaths.of(root))
    assert active is not None, "no active state pointer"
    return root / ".theurian/state" / active.database_filename
