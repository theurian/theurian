"""Why the ranked path stood aside, and what a client is told (FR-R5, ADR-0004).

The index is derived, so every way it can be unusable is a missing optimisation
rather than a reason to refuse an answer. That makes the *reporting* the load
bearing part: all six failures once produced the same sentence -- "no retrieval
index has been built for this project" -- which is true of exactly one of them.
The other five told a user to run a command they had already run.

Two invariants hold across all of them and are asserted across all of them:

- a fallback never reports ``indexed: true``. Answering from a broken index and
  calling it healthy is the failure the schema gate exists to prevent: a v1 file
  left behind by an upgrade had no ``chunks_trigram``, `unicode61` cannot
  segment CJK, and so every Japanese query returned nothing while the response
  said it had been answered from an index.
- a fallback still answers. Falling back is what keeps "we have no such
  decision" from being said to a project whose knowledge is simply not indexed.

The one asymmetry is deliberate and pinned below: a *stale* index is still used.
It is behind, not unreadable, and its results are checked against the canonical
store on the way out (FR-R5).

Real repositories, real index files, and the real CLI, all under ``tmp_path``
with ``THEURIAN_DATA_DIR`` redirected.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import subprocess
from collections.abc import Callable, Iterator
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from theurian.application.project_service import ProjectRegistry
from theurian.cli.main import app
from theurian.daemon.runner import build_server
from theurian.domain.chunking import Chunk
from theurian.infrastructure.sqlite.index_schema import INDEX_SCHEMA_VERSION
from theurian.infrastructure.sqlite.index_store import (
    IndexableChunk,
    IndexUnreadableError,
    SqliteIndexStore,
)
from theurian.mcp.search import (
    INDEX_FILE_MISSING,
    INDEX_POINTER_INVALID,
    INDEX_SCHEMA_MISMATCH,
    INDEX_UNREADABLE,
    NO_INDEX,
    UNAPPROVED_NOT_INDEXED,
)

pytestmark = pytest.mark.integration

runner = CliRunner()

MIGRATION_ID = "01K1AAAAAA01234567890ABCDE"
REVISION_ID = "01K1AAAREV01234567890ABCDE"
DRAFT_ID = "01K1BBBBBB01234567890ABCDE"
DRAFT_REVISION_ID = "01K1BBBREV01234567890ABCDE"
SECOND_ID = "01K1CCCCCC01234567890ABCDE"
SECOND_REVISION_ID = "01K1CCCREV01234567890ABCDE"

BODY = "# Authentication policy\n\nEvery call carries a signed token.\n"
DRAFT_BODY = "# Caching draft\n\nAn unreviewed proposal about the token cache.\n"
SECOND_BODY = "# Quota policy\n\nThe gateway meters every signed token per tenant.\n"


def _migration(migration_id: str, item: str, revision: str, title: str, status: str) -> str:
    """One item, one revision. The filename is derived from the item id, so a
    migration cannot name content that belongs to a different item."""
    filename = f"{item.split('.', 1)[1]}.md"
    return f"""apiVersion: theurian.dev/v1
id: {migration_id}
createdAt: 2026-08-02T10:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: {item}
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: {item}
    revisionId: {revision}
    contentFile: ../knowledge/architecture/{filename}
    metadata:
      title: {title}
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: {status}
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/{filename}
"""


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Migrations applied, one approved item and one draft, and **no index**.

    Every recipe below starts here and breaks the index its own way, so "never
    built" is the base case rather than a seventh special case.
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
    _in(root, "init")

    knowledge = root / ".theurian/knowledge/architecture"
    (knowledge / "auth-policy.md").write_text(BODY)
    (knowledge / "caching-draft.md").write_text(DRAFT_BODY)
    (root / f".theurian/migrations/{MIGRATION_ID}-auth.yaml").write_text(
        _migration(
            MIGRATION_ID,
            "architecture.auth-policy",
            REVISION_ID,
            "Authentication policy",
            "approved",
        )
    )
    (root / f".theurian/migrations/{DRAFT_ID}-draft.yaml").write_text(
        _migration(
            DRAFT_ID,
            "architecture.caching-draft",
            DRAFT_REVISION_ID,
            "Caching draft",
            "draft",
        )
    )
    _in(root, "project", "register")
    _in(root, "migrate", "apply")

    yield root


@pytest.fixture
def registry(tmp_path: Path) -> ProjectRegistry:
    return ProjectRegistry.default(tmp_path / "datadir")


def _in(root: Path, *args: str) -> tuple[int, dict[str, Any]]:
    """Run a CLI command with ``root`` as the working directory."""
    monkey = pytest.MonkeyPatch()
    monkey.chdir(root)
    try:
        result = runner.invoke(app, [*args, "--json"], catch_exceptions=False)
    finally:
        monkey.undo()
    stream = result.stdout if result.exit_code == 0 else (result.stderr or result.stdout)
    return result.exit_code, json.loads(stream) if stream.strip() else {}


def _must(root: Path, *args: str) -> dict[str, Any]:
    code, payload = _in(root, *args)
    assert code == 0, payload
    return payload


async def _search(registry: ProjectRegistry, **arguments: Any) -> dict[str, Any]:
    """Call ``knowledge.search`` through the same entry point the transport uses."""
    result = await build_server(registry).call_tool(
        "knowledge.search", {"projectId": "demo", **arguments}
    )
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        payload: dict[str, Any] = structured
        return payload
    content: Any = result.content  # type: ignore[union-attr]
    loaded: dict[str, Any] = json.loads(content[0].text)
    return loaded


def _corrupt(database: Path, statement: str, parameters: tuple[Any, ...] = ()) -> None:
    """Run one statement against an index file, closing the handle.

    ``with sqlite3.connect(...)`` commits but does not close, which leaks a
    handle per call — the same trap `SqliteIndexStore._connect` documents, and
    `filterwarnings = error` turns the resulting `ResourceWarning` into a failed
    run in whichever test happens to be running when the object is collected.
    """
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(statement, parameters)
        connection.commit()


def _pointer(root: Path) -> Path:
    return root / ".theurian/state/active-index.json"


def _index_file(root: Path) -> Path:
    built = list((root / ".theurian/state").glob("theurian-index-*.sqlite"))
    assert len(built) == 1, f"expected exactly one built index, found {built}"
    return built[0]


# -- The six ways an index cannot answer -------------------------------------
#
# One recipe per `fallbackReason`. Each breaks a real project a different way,
# so the reason is produced by the branch it names rather than asserted against
# a constructed response.


def _never_built(root: Path) -> None:
    """The base fixture, untouched: migrations applied, `index build` not run."""


def _pointer_escapes_the_project(root: Path) -> None:
    """`active-index.json` is derived, git-ignored and unsigned, so any local
    process can put `../` in it (SEC-7)."""
    _must(root, "index", "build")
    pointer = json.loads(_pointer(root).read_text())
    pointer["indexBuildId"] = "../" * 8 + "tmp/elsewhere"
    _pointer(root).write_text(json.dumps(pointer))


def _file_deleted_under_the_pointer(root: Path) -> None:
    """A reclaim that raced, a cleanup script, a restored backup."""
    _must(root, "index", "build")
    _index_file(root).unlink()


def _written_by_another_schema(root: Path) -> None:
    """What an upgrade leaves behind. The version was written into every index
    from the first build and read by nothing until this gate existed."""
    _must(root, "index", "build")
    _corrupt(
        _index_file(root),
        "UPDATE index_metadata SET index_schema_version = ?",
        (INDEX_SCHEMA_VERSION - 1,),
    )


def _passes_the_gate_and_still_breaks(root: Path) -> None:
    """The case the version gate is *supposed* to catch and cannot always.

    Version left correct, a table removed underneath it: a truncated copy, a
    dropped table, a metadata row that outlived what it describes. `chunks_fts`
    is left intact deliberately, so the lexical retriever succeeds and only the
    trigram one fails -- exactly the shape that made an upgrade switch Japanese
    search off in silence.
    """
    _must(root, "index", "build")
    _corrupt(_index_file(root), "DROP TABLE chunks_trigram")


def _holds_no_drafts(root: Path) -> None:
    """Built without `--include-unapproved`, then asked for drafts."""
    _must(root, "index", "build")


Recipe = Callable[[Path], None]

#: ``(reason, recipe, extra search arguments)``.
BREAKAGES: tuple[tuple[str, Recipe, dict[str, Any]], ...] = (
    (NO_INDEX, _never_built, {}),
    (INDEX_POINTER_INVALID, _pointer_escapes_the_project, {}),
    (INDEX_FILE_MISSING, _file_deleted_under_the_pointer, {}),
    (INDEX_SCHEMA_MISMATCH, _written_by_another_schema, {}),
    (INDEX_UNREADABLE, _passes_the_gate_and_still_breaks, {}),
    (UNAPPROVED_NOT_INDEXED, _holds_no_drafts, {"includeUnapproved": True}),
)


@pytest.fixture
def broken(project: Path, request: pytest.FixtureRequest) -> tuple[str, dict[str, Any]]:
    """Apply one breakage recipe, and hand back what to ask for and expect.

    A **synchronous** fixture on purpose. `theurian index build` embeds chunks
    through `asyncio.run`, which raises inside an already-running loop, so a
    recipe cannot be applied from the body of an async test. Doing it here also
    keeps arrange out of the test bodies entirely.
    """
    reason, recipe, arguments = request.param
    recipe(project)
    return reason, arguments


_CASES = pytest.mark.parametrize(
    "broken", BREAKAGES, indirect=True, ids=[case[0] for case in BREAKAGES]
)


@_CASES
@pytest.mark.asyncio
async def test_a_fallback_names_the_reason_it_could_not_use_the_index(
    registry: ProjectRegistry, broken: tuple[str, dict[str, Any]]
) -> None:
    """Machine-readable, because the six remedies are different commands.

    "Rebuild your index" and "you asked for drafts an approved-only index does
    not hold" call for different next actions, and a client cannot tell them
    apart by parsing prose.
    """
    reason, arguments = broken

    result = await _search(registry, query="token", **arguments)

    assert result["retrieval"]["fallbackReason"] == reason


@_CASES
@pytest.mark.asyncio
async def test_a_broken_index_is_never_reported_as_a_healthy_one(
    registry: ProjectRegistry, broken: tuple[str, dict[str, Any]]
) -> None:
    """The core of the fix, asserted for every branch rather than for one.

    ``indexed: true`` over a file that could not be searched is what let a v1
    index answer every Japanese query with silence and still look healthy. The
    mode is asserted with it: an unranked scan must not describe itself as
    hybrid, lexical or dense.
    """
    _, arguments = broken

    result = await _search(registry, query="token", **arguments)

    assert result["retrieval"]["indexed"] is False
    assert result["retrieval"]["mode"] == "substring"


@_CASES
@pytest.mark.asyncio
async def test_a_fallback_still_answers_the_question(
    registry: ProjectRegistry, broken: tuple[str, dict[str, Any]]
) -> None:
    """ADR-0004 in one assertion.

    An unusable index is a missing optimisation. Refusing to answer -- or
    answering with nothing -- would take away the very fallback that exists so a
    project without an index still gets an answer, and an agent reads an empty
    result as "we have no such decision".
    """
    _, arguments = broken

    result = await _search(registry, query="token", **arguments)

    assert result["count"] >= 1
    assert result["results"][0]["itemId"] == "architecture.auth-policy"


@_CASES
@pytest.mark.asyncio
async def test_every_fallback_note_names_the_command_that_resolves_it(
    registry: ProjectRegistry, broken: tuple[str, dict[str, Any]]
) -> None:
    """The prose half of the same contract, for a human reading a transcript.

    Asserted per branch because the failure being prevented is precisely one
    shared sentence: five of these once told a user to run `index build` when
    they had already run it.
    """
    _, arguments = broken

    result = await _search(registry, query="token", **arguments)

    assert "theurian" in result["retrieval"]["note"]
    assert "substring scan" in result["retrieval"]["note"]


@pytest.fixture
def notes_by_reason(project: Path, registry: ProjectRegistry) -> dict[str, str]:
    """The note each reason actually produces, gathered from one project.

    Synchronous for the reason the `broken` fixture gives, which is why the
    search is driven with `asyncio.run` here rather than awaited.
    """
    collected: dict[str, str] = {}
    for reason, recipe, arguments in BREAKAGES:
        _reset(project)
        recipe(project)
        result = asyncio.run(_search(registry, query="token", **arguments))
        collected[reason] = result["retrieval"]["note"]
    return collected


def _reset(root: Path) -> None:
    """Return the project to "migrations applied, no index"."""
    _pointer(root).unlink(missing_ok=True)
    for built in (root / ".theurian/state").glob("theurian-index-*.sqlite*"):
        built.unlink()


def test_each_reason_gets_its_own_note(notes_by_reason: dict[str, str]) -> None:
    """Six codes sharing one sentence would be six codes nobody can act on.

    Asserted as a set because that is exactly what went wrong: every branch
    returned the `no-index` sentence, so five of them told a user to run a
    command they had already run, and nothing in the response distinguished
    them.
    """
    assert len(set(notes_by_reason.values())) == len(BREAKAGES), "one note per reason"


@pytest.fixture
def escaped_pointer(project: Path) -> Path:
    _pointer_escapes_the_project(project)
    return project


@pytest.mark.asyncio
async def test_a_rejected_pointer_does_not_echo_the_path_it_rejected(
    escaped_pointer: Path, registry: ProjectRegistry
) -> None:
    """SEC-7 message hygiene.

    `ProjectPaths.index_for` raises with the absolute path it refused, which is
    the right message for a CLI and the wrong one for a reply that leaves the
    machine. The branch deliberately does not pass it through, so this asserts
    the substitution actually happened rather than that some message exists.
    """
    result = await _search(registry, query="token")
    note = result["retrieval"]["note"]

    assert str(escaped_pointer) not in note, "no absolute path reaches the client"
    assert "tmp/elsewhere" not in note, "nor the rejected pointer value"
    assert "active-index.json" in note, "the file to delete is still named"


# -- The deliberate asymmetry: stale is not broken ----------------------------


@pytest.fixture
def fresh_index(project: Path) -> Path:
    _must(project, "index", "build")
    return project


@pytest.fixture
def stale_index(fresh_index: Path) -> Path:
    """A built index, then knowledge that moved on underneath it."""
    (fresh_index / ".theurian/knowledge/architecture/quota-policy.md").write_text(SECOND_BODY)
    (fresh_index / f".theurian/migrations/{SECOND_ID}-quota.yaml").write_text(
        _migration(
            SECOND_ID,
            "architecture.quota-policy",
            SECOND_REVISION_ID,
            "Quota policy",
            "approved",
        )
    )
    _must(fresh_index, "migrate", "apply")
    return fresh_index


@pytest.mark.asyncio
async def test_a_stale_index_is_still_used_rather_than_abandoned(
    stale_index: Path, registry: ProjectRegistry
) -> None:
    """Behind is not unreadable, and the difference is on purpose.

    A stale index still describes real chunks, and every hit it produces is
    re-resolved through the canonical store on the way out (FR-R5), so it
    returns *fewer* results rather than wrong ones. Falling back for it would
    trade a ranked answer for an unranked one to fix a problem the ranked path
    already handles -- and would hide the `stale` flag that tells a caller to
    rebuild.
    """
    result = await _search(registry, query="token")

    assert result["retrieval"]["indexed"] is True, "stale is not a fallback"
    assert result["retrieval"]["stale"] is True
    assert "fallbackReason" not in result["retrieval"]
    assert result["count"] >= 1


@pytest.mark.asyncio
async def test_a_fresh_index_is_neither_stale_nor_a_fallback(
    fresh_index: Path, registry: ProjectRegistry
) -> None:
    """The control the two assertions above are read against.

    Without it, "stale is True" and "indexed is False" could both be constants.
    """
    result = await _search(registry, query="token")

    assert result["retrieval"]["indexed"] is True
    assert result["retrieval"]["stale"] is False
    assert "fallbackReason" not in result["retrieval"]


# -- `theurian index status` --------------------------------------------------


def test_index_status_reports_the_schema_it_found_and_the_one_it_wants(project: Path) -> None:
    """Two numbers, because one of them cannot be acted on.

    "Your index is version 1" means nothing without "this build reads version
    2", and a person comparing a single number against a constant in the source
    is a person who has already been failed by the report.
    """
    _must(project, "index", "build")

    status = _must(project, "index", "status")

    assert status["indexSchemaVersion"] == INDEX_SCHEMA_VERSION
    assert status["expectedIndexSchemaVersion"] == INDEX_SCHEMA_VERSION
    assert status["stale"] is False


def test_a_schema_mismatch_is_stale_even_when_the_state_hash_matches(project: Path) -> None:
    """The gap this closed.

    Staleness was a state-hash comparison alone, so an index whose schema this
    build cannot read reported "fresh, nothing to do" -- for the very file
    retrieval had just refused to search. The state hash here is deliberately
    left correct so that the schema is the only thing that can make it stale.
    """
    _written_by_another_schema(project)

    status = _must(project, "index", "status")

    assert status["indexStateHash"] == status["currentStateHash"], "only the schema is wrong"
    assert status["indexSchemaVersion"] == INDEX_SCHEMA_VERSION - 1
    assert status["stale"] is True
    assert "index build" in status["remedy"]


def test_index_status_reports_an_unreadable_pointer_rather_than_failing(project: Path) -> None:
    """A status command that raises on a broken pointer is a status command that
    cannot report the one thing worth reporting."""
    _pointer_escapes_the_project(project)

    code, status = _in(project, "index", "status")

    assert code == 0
    assert status["indexSchemaVersion"] == 0, "0 is 'unknowable', not 'version zero'"
    assert status["stale"] is True


def test_index_status_has_no_schema_version_before_anything_is_built(project: Path) -> None:
    """``None`` rather than 0: nothing was found to have a version, which is a
    different statement from "what was found could not be read"."""
    status = _must(project, "index", "status")

    assert status["built"] is False
    assert status["indexSchemaVersion"] is None
    assert status["expectedIndexSchemaVersion"] == INDEX_SCHEMA_VERSION
    assert status["stale"] is True


# -- The store's own discrimination -------------------------------------------
#
# `search_lexical` and `search_substring` receive `sqlite3.OperationalError` for
# two unrelated reasons, and the two must not share a branch: a query-shaped
# complaint returns nothing, because a search box that raises at an unbalanced
# quote is broken; a file-shaped complaint must never return nothing, because
# "no results" is exactly what a caller cannot distinguish from a correct empty
# answer.


@pytest.fixture
def store(tmp_path: Path) -> SqliteIndexStore:
    store = SqliteIndexStore(tmp_path / "index" / "theurian-index-01.sqlite")
    store.create(index_build_id="01K1DXAA", state_hash="abc123")
    store.add_chunks(
        [
            IndexableChunk(
                chunk=Chunk(chunk_id="c1", ordinal=0, text="every call carries a signed token"),
                project_id="demo",
                item_id="architecture.auth",
                revision_id="rev-c1",
                status="approved",
                sensitivity="internal",
                trust_level="reviewed",
            )
        ]
    )
    return store


@pytest.mark.parametrize("table", ["chunks_fts", "chunks_trigram"])
def test_a_missing_table_raises_instead_of_answering_nothing(
    store: SqliteIndexStore, table: str
) -> None:
    """The distinction the whole `IndexUnreadableError` type exists for.

    Both retrievers are covered because an index can lose either table, and the
    trigram one is the only retriever that can answer at all for a Japanese
    corpus -- swallowing its error made that corpus invisible while the response
    still claimed to be indexed.
    """
    _corrupt(store.path, f"DROP TABLE {table}")

    search = store.search_lexical if table == "chunks_fts" else store.search_substring
    with pytest.raises(IndexUnreadableError, match="cannot be read"):
        search("token", project_id="demo")


@pytest.mark.parametrize("table", ["chunks_fts", "chunks_trigram"])
def test_the_error_names_the_rebuild_rather_than_the_sql(
    store: SqliteIndexStore, table: str
) -> None:
    """The message reaches an agent through the tool note. SQLite's own wording
    tells it nothing it can act on; the remedy is a command."""
    _corrupt(store.path, f"DROP TABLE {table}")

    search = store.search_lexical if table == "chunks_fts" else store.search_substring
    with pytest.raises(IndexUnreadableError) as raised:
        search("token", project_id="demo")

    assert "theurian index build" in str(raised.value)
    assert "nothing is lost" in str(raised.value)


@pytest.mark.parametrize("retriever", ["search_lexical", "search_substring"])
def test_a_query_that_matches_nothing_returns_nothing_and_does_not_raise(
    store: SqliteIndexStore, retriever: str
) -> None:
    """The control that makes the two tests above mean something.

    Without it, "an unreadable index raises" is satisfied by a retriever that
    raises at everything.
    """
    search = getattr(store, retriever)

    assert search("kubernetes", project_id="demo") == ()
    assert search("token", project_id="demo"), "and a matching one still matches"
