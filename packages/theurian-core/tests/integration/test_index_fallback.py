"""Why the ranked path stood aside, and what a client is told (FR-R5, ADR-0004).

The index is derived, so every way it can be unusable is a missing optimisation
rather than a reason to refuse an answer. That makes the *reporting* the load
bearing part: every one of these failures once produced the same sentence -- "no
retrieval index has been built for this project" -- which is true of exactly one
of them. The rest told a user to run a command they had already run.

Seven reason codes, twelve recipes, and the two counts do not line up in either
direction:

- ``index-pointer-invalid`` is reached by five recipes. Four leave a pointer file
  that names no build at all -- truncated JSON, a JSON array, an object without
  ``indexBuildId``, arbitrary bytes -- and the fifth leaves one that parses and
  names a path outside the project. One remedy fixes all five, so they must say
  the same thing; they are five recipes rather than one because they reach that
  answer through four branches in two functions, and the first of those branches
  catches two unrelated exception types -- ``UnicodeDecodeError`` is a
  ``ValueError`` and not a ``JSONDecodeError``, so either can be dropped from the
  tuple without the other noticing. A table holding one of the five could not
  tell whether the other four still worked; it held one, and they did not.
- ``index-project-mismatch`` is the one reason carrying two notes: a client's next
  action is `index build` either way, while a person reading the transcript needs
  to know whether an id changed under the index or was never recorded at all.
  Telling everyone upgrading from a build that predates ``projectId`` that their
  index "was built for a different project id" would send them hunting for a
  rename that never happened -- which is the failure the reason codes exist to
  prevent, reintroduced one level down.

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

from theurian.application.project_service import (
    INDEX_POINTER_REMEDY,
    ProjectPaths,
    ProjectRegistry,
    read_active_index_pointer,
)
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
    INDEX_PROJECT_MISMATCH,
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
    built" is one of the recipes rather than a special case beside them.
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


def _paths(root: Path) -> ProjectPaths:
    return ProjectPaths(root=root, knowledge_dir=root / ".theurian")


def _rewrite_a_published_pointer(root: Path, payload: bytes) -> None:
    """Replace a published pointer's bytes, and check what that actually produced.

    Bytes rather than text, because one of the four recipes below is not valid
    UTF-8 and the point of it is that it is not.

    Two guards, and both of them are load-bearing, because neither fact is
    visible in the response the recipe goes on to produce:

    - **the index file is still on disk.** "This pointer is corrupt" and "nothing
      was ever built" are only distinguishable while it is there, and that
      distinction is the entire reason the two carry different remedies. A recipe
      that lost the file would be testing `never-built` under another name.
    - **the pointer reads back as ``unreadable``.** These recipes exist to cover
      the branch that tells those two apart, and that branch is chosen on this
      flag. Deleting the branch passed every one of the 1,161 tests that existed
      before this file grew them, so a recipe that quietly stopped reaching it --
      because `read_active_index_pointer` grew an arm, because a payload stopped
      being invalid -- would go on passing through whichever branch it landed on
      instead. That is precisely how the gap this closes was opened: the recipe
      that looked like it covered `index-pointer-invalid` reached it from the
      other function entirely.
    """
    _pointer(root).write_bytes(payload)

    _index_file(root)
    pointer = read_active_index_pointer(_paths(root))
    assert pointer.unreadable, "this recipe must reach the corrupt-pointer branch"
    assert pointer.payload is None, "and must not also hand back something to read"


# -- The ways an index cannot answer -----------------------------------------
#
# One recipe per distinguishable outcome. Each breaks a real project a different
# way, so the reason is produced by the branch it names rather than asserted
# against a constructed response.


def _never_built(root: Path) -> None:
    """The base fixture, untouched: migrations applied, `index build` not run."""


def _pointer_escapes_the_project(root: Path) -> None:
    """`active-index.json` is derived, git-ignored and unsigned, so any local
    process can put `../` in it (SEC-7)."""
    _must(root, "index", "build")
    pointer = json.loads(_pointer(root).read_text())
    pointer["indexBuildId"] = "../" * 8 + "tmp/elsewhere"
    _pointer(root).write_text(json.dumps(pointer))


def _pointer_truncated_mid_write(root: Path) -> None:
    """A pointer whose JSON stops in the middle of naming its build.

    Theurian cannot produce this itself -- `_publish` writes a temporary file and
    `os.replace`s it, which is atomic on POSIX -- and that is the reason it is
    worth a recipe. The file is derived, git-ignored and unsigned (SEC-7), so
    what leaves it half-written is a full disk, an interrupted copy, or a crash
    in something else entirely, and none of those are reachable from the code
    that would otherwise be trusted to keep the shape valid.
    """
    _must(root, "index", "build")
    _rewrite_a_published_pointer(root, b'{"indexBuildId":')


def _pointer_holding_a_json_array(root: Path) -> None:
    """Valid JSON that is not an object, so ``.get`` cannot be asked anything.

    Distinct from truncation because it parses. Everything downstream of the
    parse treats the payload as a mapping, so this is the shape that reaches
    furthest before failing -- and the one that would fail with an
    ``AttributeError`` rather than a fallback if the type check went away.
    """
    _must(root, "index", "build")
    _rewrite_a_published_pointer(root, b"[]")


def _pointer_naming_no_build(root: Path) -> None:
    """A well-formed pointer object with ``indexBuildId`` removed.

    What a hand-edit or a half-written generator leaves. Accepting it built a
    path out of an empty id and reported `index-file-missing` -- "the published
    index build is no longer on disk", about a build that was never named -- so
    the remaining keys are kept exactly as published, leaving the missing id as
    the only thing wrong with the file.
    """
    _must(root, "index", "build")
    published = json.loads(_pointer(root).read_text())
    _rewrite_a_published_pointer(
        root,
        json.dumps(
            {key: value for key, value in published.items() if key != "indexBuildId"}
        ).encode(),
    )


def _pointer_holding_raw_bytes(root: Path) -> None:
    """Bytes that are not UTF-8 at all: a partial overwrite, a restored binary.

    Its own recipe because it reaches ``unreadable`` by a different route from
    the three above. ``read_text`` raises ``UnicodeDecodeError``, which is a
    ``ValueError`` and *not* a ``JSONDecodeError``, so it is caught by a separate
    arm of the same ``except`` -- and before that arm existed it escaped the
    reader entirely and reached the agent as a crash, for a file that is derived
    and could simply have been ignored (ADR-0004).
    """
    _must(root, "index", "build")
    _rewrite_a_published_pointer(root, b"\xff\xfe\x00")


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


def _built_for_another_project_id(root: Path) -> None:
    """A legitimate rename with no rebuild: `project unregister`, re-register.

    Every chunk is stamped with the project id that built it and every retrieval
    query scopes on that id, so an index built for another project matches
    nothing *while reporting itself healthy* -- `count: 0, indexed: true, mode:
    none`, which an agent reads as "this team has made no such decision". The
    canonical store is deliberately left intact, so the fallback has real
    knowledge to answer from and `count: 0` cannot be honest.
    """
    _must(root, "index", "build")
    published = json.loads(_pointer(root).read_text())
    _pointer(root).write_text(json.dumps({**published, "projectId": "was-called-this-before"}))


def _pointer_predates_the_project_id_field(root: Path) -> None:
    """What an upgrade leaves behind: a pointer written before `projectId`.

    Unverifiable rather than provably wrong, and it gets its own note for that
    reason. The remedy is the same command, so the reason code is the same.
    """
    _must(root, "index", "build")
    published = json.loads(_pointer(root).read_text())
    _pointer(root).write_text(
        json.dumps({key: value for key, value in published.items() if key != "projectId"})
    )


Recipe = Callable[[Path], None]

#: The four ways a pointer file can exist and name no build at all.
#:
#: Named apart from :data:`BREAKAGES` because two more tests are parametrised
#: over exactly this set: it is the set for which `theurian index status` must
#: report a corrupt pointer, and the set on which the two surfaces' remedies are
#: compared. Deriving those from the reason code instead would drag in
#: `pointer-escapes-the-project`, whose file parses perfectly well.
UNREADABLE_POINTERS: tuple[tuple[str, Recipe], ...] = (
    ("pointer-truncated", _pointer_truncated_mid_write),
    ("pointer-is-an-array", _pointer_holding_a_json_array),
    ("pointer-names-no-build", _pointer_naming_no_build),
    ("pointer-holds-raw-bytes", _pointer_holding_raw_bytes),
)

#: ``(case, diagnosis, reason, recipe, extra search arguments)``.
#:
#: Three ids where one would seem to do, because they nest and the nesting is the
#: whole subject of this file: a **case** is one recipe; a **diagnosis** is the
#: sentence a person reads; a **reason** is the code a client branches on.
#:
#: The two mappings run in opposite directions, and a table keyed on either one
#: alone would lose the other. Five cases share the `pointer-invalid` diagnosis,
#: because one remedy fixes all five and no client could act on the difference --
#: key on the diagnosis and four of the five vanish. One reason,
#: `index-project-mismatch`, carries two diagnoses, because a person needs to
#: know whether an id changed or was never recorded -- key on the reason and one
#: of the two vanishes.
BREAKAGES: tuple[tuple[str, str, str, Recipe, dict[str, Any]], ...] = (
    ("never-built", "not-built", NO_INDEX, _never_built, {}),
    (
        "pointer-escapes-the-project",
        "pointer-invalid",
        INDEX_POINTER_INVALID,
        _pointer_escapes_the_project,
        {},
    ),
    *(
        (case, "pointer-invalid", INDEX_POINTER_INVALID, recipe, {})
        for case, recipe in UNREADABLE_POINTERS
    ),
    ("file-deleted", "file-missing", INDEX_FILE_MISSING, _file_deleted_under_the_pointer, {}),
    (
        "written-by-another-schema",
        "schema-mismatch",
        INDEX_SCHEMA_MISMATCH,
        _written_by_another_schema,
        {},
    ),
    ("table-dropped", "unreadable", INDEX_UNREADABLE, _passes_the_gate_and_still_breaks, {}),
    (
        "built-for-another-id",
        "project-renamed",
        INDEX_PROJECT_MISMATCH,
        _built_for_another_project_id,
        {},
    ),
    (
        "pointer-records-no-id",
        "project-unverified",
        INDEX_PROJECT_MISMATCH,
        _pointer_predates_the_project_id_field,
        {},
    ),
    (
        "holds-no-drafts",
        "no-drafts",
        UNAPPROVED_NOT_INDEXED,
        _holds_no_drafts,
        {"includeUnapproved": True},
    ),
)


@pytest.fixture
def broken(project: Path, request: pytest.FixtureRequest) -> tuple[str, dict[str, Any]]:
    """Apply one breakage recipe, and hand back what to ask for and expect.

    A **synchronous** fixture on purpose. `theurian index build` embeds chunks
    through `asyncio.run`, which raises inside an already-running loop, so a
    recipe cannot be applied from the body of an async test. Doing it here also
    keeps arrange out of the test bodies entirely.
    """
    _, _, reason, recipe, arguments = request.param
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
    """Machine-readable, because these remedies are different commands.

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
    shared sentence: every recipe here except `never-built` runs `index build`
    first, and every one of them was once answered by a note telling the user to
    run it.

    No count in that sentence on purpose. It said "five of these" while the table
    held eight recipes and seven reasons, and neither number was the one it
    meant.
    """
    _, arguments = broken

    result = await _search(registry, query="token", **arguments)

    assert "theurian" in result["retrieval"]["note"]
    assert "substring scan" in result["retrieval"]["note"]


@pytest.fixture
def notes_by_case(project: Path, registry: ProjectRegistry) -> dict[str, str]:
    """The note each recipe actually produces, gathered from one project.

    Keyed by *case*, which is the only one of the three ids that is unique per
    recipe. Keyed by reason, the two `index-project-mismatch` recipes would
    overwrite each other; keyed by diagnosis, the five `pointer-invalid` ones
    would. Either way the dict would be shorter than the table and the tests
    below would be comparing a subset against a total.

    Synchronous for the reason the `broken` fixture gives, which is why the
    search is driven with `asyncio.run` here rather than awaited.
    """
    collected: dict[str, str] = {}
    for case, _, _, recipe, arguments in BREAKAGES:
        _reset(project)
        recipe(project)
        result = asyncio.run(_search(registry, query="token", **arguments))
        collected[case] = result["retrieval"]["note"]
    return collected


def _reset(root: Path) -> None:
    """Return the project to "migrations applied, no index"."""
    _pointer(root).unlink(missing_ok=True)
    for built in (root / ".theurian/state").glob("theurian-index-*.sqlite*"):
        built.unlink()


def _notes_per_diagnosis(notes_by_case: dict[str, str]) -> dict[str, set[str]]:
    """Every distinct note each diagnosis produced, across its recipes."""
    grouped: dict[str, set[str]] = {}
    for case, diagnosis, _, _, _ in BREAKAGES:
        grouped.setdefault(diagnosis, set()).add(notes_by_case[case])
    return grouped


def test_no_two_diagnoses_share_a_sentence(notes_by_case: dict[str, str]) -> None:
    """Diagnoses sharing one sentence would be diagnoses nobody can act on.

    This is exactly what went wrong: every branch returned the `no-index`
    sentence, so most of them told a user to run a command they had already run,
    and nothing in the response distinguished them.

    Per diagnosis rather than per recipe, which the earlier form could not say.
    It asserted one note per *recipe*, which was true only while no two recipes
    reached the same answer -- and the four corrupt-pointer shapes added since
    are four recipes that must all say the same thing, so under the old form they
    would have had to be left out of the table to keep it passing.
    """
    grouped = _notes_per_diagnosis(notes_by_case)
    sentences = {note for notes in grouped.values() for note in notes}

    assert len(sentences) == len(grouped), f"one sentence per diagnosis, got {grouped}"


def test_one_diagnosis_never_produces_two_sentences(notes_by_case: dict[str, str]) -> None:
    """The other half, and the half that keeps the test above from being cheap.

    Uniqueness alone is satisfied by giving all twelve recipes twelve different
    sentences, which would put the four corrupt-pointer shapes -- one file, one
    remedy -- in front of a user as four different problems. Five recipes reach
    `pointer-invalid` through four arms of two functions, and what has to be
    asserted about them is that they *converge*.
    """
    varying = {
        diagnosis: notes
        for diagnosis, notes in _notes_per_diagnosis(notes_by_case).items()
        if len(notes) != 1
    }

    assert not varying, f"one diagnosis spoke with more than one voice: {varying}"


def test_the_two_project_mismatch_notes_say_which_of_the_two_happened(
    notes_by_case: dict[str, str],
) -> None:
    """One reason code, two diagnoses, and the difference matters to a person.

    A user upgrading from a build that predates ``projectId`` has had no rename;
    telling them their index "was built for a different project id" sends them
    looking for one. A user who *did* rename needs to be told exactly that.
    """
    renamed = notes_by_case["built-for-another-id"]
    unverified = notes_by_case["pointer-records-no-id"]

    assert "built for a different project id" in renamed
    assert "does not record which project it was built for" in unverified
    assert "theurian index build" in renamed
    assert "theurian index build" in unverified


@pytest.mark.parametrize(
    "recipe",
    [_built_for_another_project_id, _pointer_predates_the_project_id_field],
    ids=["built-for-another-id", "pointer-records-no-id"],
)
def test_index_status_calls_orphaned_exactly_what_search_falls_back_for(
    project: Path, recipe: Recipe
) -> None:
    """Two surfaces, one verdict. A user must not be told the index is orphaned
    by one and healthy by the other.

    `knowledge.search` answers an agent and `theurian index status` answers an
    operator at their own terminal, and they reach the conclusion through
    separate code — the pointer check in `mcp.search`, the `orphaned` flag in
    `cli.index_commands`. Two implementations of one rule is how the two drift,
    so the agreement is asserted rather than assumed.
    """
    recipe(project)

    status = _must(project, "index", "status")

    assert status["orphaned"] is True
    assert status["stale"] is True, "an index that cannot be shown to be ours is not fresh"
    assert "index build" in status["remedy"]


def test_index_status_does_not_call_a_healthy_index_orphaned(project: Path) -> None:
    """The control. Without it, `orphaned is True` above is satisfied by a flag
    that is always set."""
    _must(project, "index", "build")

    status = _must(project, "index", "status")

    assert status["orphaned"] is False
    assert status["indexProjectId"] == status["projectId"] == "demo"


# -- One corrupt pointer, two surfaces ----------------------------------------
#
# The same file read by `knowledge.search` and by `theurian index status`, which
# do not share the code that judges it. `index status` reported "no index was
# ever built" for a pointer `knowledge.search` was simultaneously calling corrupt
# and naming for deletion -- with the index file sitting on disk the whole time,
# so the operator's remedy (`index build`) would run against a project the tool
# had already told the agent was in a different state.


@pytest.fixture(params=UNREADABLE_POINTERS, ids=[case for case, _ in UNREADABLE_POINTERS])
def unreadable_pointer(project: Path, request: pytest.FixtureRequest) -> Path:
    """A published index whose pointer file names no build, one shape per param.

    Synchronous for the reason the `broken` fixture gives: `index build` embeds
    through `asyncio.run`, which raises inside an already-running loop.
    """
    _, recipe = request.param
    recipe(project)
    return project


def test_index_status_tells_a_corrupt_pointer_from_a_missing_one(
    unreadable_pointer: Path,
) -> None:
    """The round-2 finding, from the surface that was wrong about it.

    Both states report ``built: false`` -- correctly, since neither names a build
    -- so that field cannot carry the difference, and reading it alone is how an
    operator concluded nothing had ever been built while the index file was on
    disk and `knowledge.search` was telling their agent the pointer was corrupt.
    ``indexPointerCorrupt`` is the field that separates them, which is why it is
    asserted here rather than inferred from the remedy below.
    """
    status = _must(unreadable_pointer, "index", "status")

    assert status["indexPointerCorrupt"] is True
    assert status["built"] is False, "a pointer naming no build has not published one"
    assert status["stale"] is True, "an index nothing points at is not fresh"


def test_index_status_does_not_call_an_absent_pointer_corrupt(project: Path) -> None:
    """The control, and the exact pair the flag exists to tell apart.

    `never-built` reaches the same ``built: false`` by the honest route: no
    pointer file at all. Without this, ``indexPointerCorrupt is True`` above is
    satisfied by a flag that is always set -- and the remedy assertion is
    satisfied by a command that tells every new user to delete a file they do not
    have.
    """
    status = _must(project, "index", "status")

    assert status["indexPointerCorrupt"] is False
    assert status["built"] is False
    assert status["remedy"] != INDEX_POINTER_REMEDY, "nothing to delete before anything is built"


def test_index_status_hands_a_corrupt_pointer_the_shared_remedy(
    unreadable_pointer: Path,
) -> None:
    """Pinned to the constant, not to the sentence.

    ``INDEX_POINTER_REMEDY`` is public for exactly this: one cure for one file,
    named where both surfaces can reach it rather than typed out twice. Both now
    read it -- `cli.index_commands` prints it as its ``remedy``, `mcp.search`
    interpolates it into the note -- so each end of the agreement is held to
    something that cannot drift silently, and the comparison below is what
    notices if either end stops reading it.

    The second assertion is why this is not a comparison of a constant with
    itself. Emptying ``INDEX_POINTER_REMEDY`` satisfies the equality perfectly
    and leaves an operator staring at a blank remedy, so what is shared has to be
    checked for being a cure and not only for being shared.
    """
    status = _must(unreadable_pointer, "index", "status")

    assert status["remedy"] == INDEX_POINTER_REMEDY
    assert "active-index.json" in status["remedy"], "a cure naming no file is not one"


@pytest.mark.asyncio
async def test_both_surfaces_give_a_corrupt_pointer_the_same_remedy(
    unreadable_pointer: Path, registry: ProjectRegistry
) -> None:
    """Two live outputs compared against each other, not against a constant.

    Either surface can drift on its own -- `cli.index_commands` chooses a remedy
    by branch order, `mcp.search` carries one per `Fallback` -- and a test that
    checked each against ``INDEX_POINTER_REMEDY`` separately would still pass if
    one of them stopped reaching its corrupt-pointer branch and returned some
    other true-sounding sentence. What must hold is that a user who runs the
    command and an agent that reads the note are told to do the same thing about
    the same file.

    Plain substrings, backticks and all. This was briefly a comparison with the
    code-span markers stripped, because the note re-typed the cure with the path
    quoted and the ``remedy`` did not; now that both interpolate the same
    constant, normalising anything would only hide the next copy someone types
    out by hand.

    The second assertion is the one that survives a half-fix. The first says the
    agent was handed the shared cure; only the second says it is the same cure
    the operator at the terminal was handed, which is the property that fails
    when one end stops reading the constant and the other does not.

    The third rules the other two out of passing for free. ``in`` is true of the
    empty string against anything, so an ``INDEX_POINTER_REMEDY`` emptied by a
    bad edit would leave both of them green over a note that names no file and no
    command -- measured, not supposed: it was, and six other tests in this file
    caught it while these two did not.
    """
    status = _must(unreadable_pointer, "index", "status")

    result = await _search(registry, query="token")

    note = result["retrieval"]["note"]
    assert result["retrieval"]["fallbackReason"] == INDEX_POINTER_INVALID
    assert INDEX_POINTER_REMEDY in note
    assert status["remedy"] in note
    assert "active-index.json" in status["remedy"], "an empty cure is a substring of anything"


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


@pytest.fixture
def index_without_its_vectors(project: Path) -> Path:
    """A built index whose `embeddings` table has gone, version left correct.

    The shape a truncated copy or a half-restored backup takes. Only the dense
    retriever touches that table, so this is invisible until a caller passes
    `useDense`.
    """
    _must(project, "index", "build")
    _corrupt(_index_file(project), "DROP TABLE embeddings")
    return project


@pytest.mark.asyncio
async def test_a_dense_query_over_a_broken_index_falls_back_rather_than_failing(
    index_without_its_vectors: Path, registry: ProjectRegistry
) -> None:
    """The user-visible half of the `search_dense` guard.

    Before it, this exact state reached the agent as a raw `no such table:
    embeddings` tool failure: the fallback keys on `IndexBuildError`, and a bare
    `sqlite3.OperationalError` is not one. ADR-0004 says an unusable index is a
    missing optimisation, so the honest answer is an unranked scan that says why
    -- never an exception, and never silence.
    """
    result = await _search(registry, query="token", useDense=True)

    assert result["retrieval"]["fallbackReason"] == INDEX_UNREADABLE
    assert result["retrieval"]["indexed"] is False
    assert result["count"] >= 1, "the fallback still answers"


@pytest.mark.asyncio
async def test_the_same_index_still_answers_without_the_dense_retriever(
    index_without_its_vectors: Path, registry: ProjectRegistry
) -> None:
    """The control that keeps the test above specific to the dense path.

    `chunks_fts` and `chunks_trigram` are untouched here, so a ranked answer is
    still available to a caller who did not ask for vectors. If this fell back
    too, "the dense retriever raised" would not be what the other test measured.
    """
    result = await _search(registry, query="token")

    assert result["retrieval"]["indexed"] is True
    assert result["retrieval"]["fallbackReason"] is None


@pytest.fixture
def pointer_holding_a_nul(project: Path) -> Path:
    """A published index whose pointer now names an unusable filename.

    Synchronous for the reason the `broken` fixture gives: `index build` embeds
    through `asyncio.run`, which raises inside an already-running loop.
    """
    _must(project, "index", "build")
    published = json.loads(_pointer(project).read_text())
    _pointer(project).write_text(json.dumps({**published, "indexBuildId": "01K1\x00DXAA"}))
    return project


@pytest.mark.asyncio
async def test_a_nul_byte_in_the_pointer_is_a_fallback_rather_than_a_crash(
    pointer_holding_a_nul: Path, registry: ProjectRegistry
) -> None:
    """SEC-7, ADR-0004. `active-index.json` is derived, git-ignored and
    unsigned, so any local process can put anything in it.

    An embedded NUL makes `Path.resolve` raise `ValueError`, which is not a
    `TheurianError` and so escaped every caller that had correctly narrowed to
    one: `knowledge.search` failed *permanently* for the project instead of
    degrading to an answer, and the OS-level message reached the client. JSON
    can carry ``\\u0000``, so writing this file is not exotic.
    """
    result = await _search(registry, query="token")

    assert result["retrieval"]["fallbackReason"] == INDEX_POINTER_INVALID
    assert result["count"] >= 1, "the fallback still answers"
    assert "\x00" not in json.dumps(result), "nor does the byte come back out"


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
    assert result["retrieval"]["fallbackReason"] is None
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
    assert result["retrieval"]["fallbackReason"] is None


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


def test_a_dense_search_over_a_lost_embeddings_table_raises_too(
    store: SqliteIndexStore,
) -> None:
    """The third retriever, which had no guard at all.

    That made `hybrid_answer`'s promise -- never answer from a broken index,
    even where the version gate cannot see it -- false for `useDense=true`. The
    MCP fallback keys on `IndexBuildError`, and a raw `sqlite3.OperationalError`
    is not one, so an index whose metadata row outlived its `embeddings` table
    reached the agent as a bare `no such table: embeddings` tool failure.

    There is no query-shaped counterpart to check here: this retriever takes a
    vector, so no caller text reaches SQL and every complaint SQLite can make
    about the statement is about the file.
    """
    _corrupt(store.path, "DROP TABLE embeddings")

    with pytest.raises(IndexUnreadableError, match="cannot be read"):
        store.search_dense([1.0, 0.0], project_id="demo")


def test_a_dense_search_over_an_index_with_no_vectors_returns_nothing(
    store: SqliteIndexStore,
) -> None:
    """The control for the test above, and a supported state in its own right.

    A machine with no embedding provider still gets lexical search. "The table
    is gone" and "the table is empty" must not share an answer, or `--no-embeddings`
    would look like corruption.
    """
    assert store.search_dense([1.0, 0.0], project_id="demo") == ()


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
