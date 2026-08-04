"""A damaged canonical state database must not answer with its own bytes.

The canonical store holds *every* revision a project ever recorded -- `draft`,
`rejected` and `deprecated` alongside `approved` (ADR-0006) -- and the retrieval
gate is what keeps the withheld ones out of a response. An exception raised while
a row is being interpreted goes around that gate entirely: `datetime.
fromisoformat` quotes the string it would not parse, every enum quotes the member
it could not find, and each domain value object renders its argument with
``!r``. Under corruption those strings are whatever bytes happened to be on the
page, so the message a caller receives had become a function of the cell.

Measured on `67a792c` through ``build_server(registry).call_tool``: overwriting
one cell published it verbatim to an MCP client from **60** (column, tool)
positions -- among them `knowledge.get: Invalid isoformat string: '<the cell>'`.
Issue #18, and the last named member of the class
:data:`~theurian.infrastructure.sqlite.index_store._UNREADABLE_VALUES` closes on
the index side.

**Two properties, held separately because they fail separately.** The enum face
leaked a cell *and* named a remedy; the `json.loads` face named no remedy and
leaked nothing (`Expecting value: line 1 column 1 (char 0)` says nothing about
its input). A test that checked only one of them would have called each face
clean in turn.

**Written against the published surface.** Nothing here asserts an exception
type or a remedy's wording -- both were being chosen while this file was
written. What is asserted is what a caller receives: the text of a refusal, and
whether it names something the caller can run.

**The population is read out of the live schema, not listed here.** A column
added in a later milestone is swept the moment a migration writes to it, and
:func:`test_every_table_the_schema_declares_holds_a_row_to_corrupt` fails if the
corpus stops covering a table -- so neither the column list nor the corpus can
quietly fall behind the DDL.
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, override

import pytest
import typer.main
from mcp.server.mcpserver.exceptions import ToolError as SdkToolError
from typer.testing import CliRunner

from theurian.application.project_service import ProjectRegistry
from theurian.cli.main import app
from theurian.daemon.runner import build_server
from theurian.infrastructure.sqlite.schema import DDL

pytestmark = pytest.mark.integration

runner = CliRunner()

#: The cell every corruption writes. Nothing in it is a word this codebase uses,
#: so a fragment of it appearing anywhere in a published message came out of the
#: database file and nowhere else.
SENTINEL: Final = "ROTATE-ME sk-live-9f2a7c41d8e3 payroll band L7 = 240000"

#: How much of :data:`SENTINEL` has to survive into a message before it counts as
#: disclosed. Twelve characters is short enough to catch a truncated echo and
#: long enough that no English sentence produces one by accident -- and checking
#: *windows* rather than the whole string is what stops this file pinning the
#: sample: an implementation that echoed the first half of the cell would satisfy
#: ``SENTINEL not in message`` and fail here.
LEAK_WINDOW: Final = 12

#: `knowledge.get`'s two id-resolution refusals, which name no remedy on purpose.
#:
#: Both fire *before* any cell is interpreted -- the corrupted column is part of
#: the item -> revision pointer chain, so the lookup misses and no value is
#: converted. Neither message contains anything but the caller's own arguments,
#: which is why they are exempt from the remedy property rather than from the
#: disclosure one. Their reach is pinned exactly by
#: :data:`REFUSALS_WITHOUT_A_REMEDY`, so this exemption cannot silently absorb a
#: converter that starts refusing without a remedy.
#:
#: That they report damage as absence is a real gap and not a design decision:
#: a caller is told the item does not exist when its row is in fact unreadable.
#: Recorded here rather than asserted, because closing it is a change to
#: `knowledge.get`, not to this store.
#:
#: **This tuple, and the exact set below, say nothing about a tool that does not
#: refuse at all.** Both are read only where ``answer.refused`` holds, so the
#: worse face of the same gap is invisible to them: corrupt
#: `knowledge_items.project_id` and `knowledge.search` answers
#: ``{"count": 0, "results": []}`` while `knowledge.status` answers
#: ``{"itemCount": 0}`` -- each a successful, false statement to an agent, and
#: neither a refusal. :data:`SILENTLY_EMPTIED` is where that class is stated;
#: framing it here would have required a set of refusals to hold something that
#: never refuses.
_ID_RESOLUTION_REFUSALS: Final = (
    "is not present in project",
    "points at a missing revision",
)

#: Every (tool, table, column) whose refusal names no remedy, stated exactly.
#:
#: An exact set rather than an allowance: ``names_a_remedy(msg) or is_exempt(msg)``
#: passes for any implementation that stops naming remedies altogether, and this
#: does not. A column added in a later milestone that refuses without a remedy
#: appears here as a failure.
REFUSALS_WITHOUT_A_REMEDY: Final = frozenset(
    {
        ("knowledge.get", "knowledge_items", "item_id"),
        ("knowledge.get", "knowledge_items", "project_id"),
        ("knowledge.get", "knowledge_revisions", "revision_id"),
        ("knowledge.get", "knowledge_revisions", "project_id"),
    }
)

#: Every (tool, table, column) where one damaged cell makes a tool answer
#: **successfully with less than the database holds**, stated exactly.
#:
#: Not a leak and not a refusal, which is why nothing else in this file can see
#: it: every other property here is read either over ``answer.refused`` or over
#: the text of a message, and these four positions produce neither. A caller is
#: told ``count: 0`` with ``stale: false``, or ``itemCount: 0``, and has no way
#: to tell that from a project which genuinely holds nothing.
#:
#: Recorded rather than fixed. Closing it means the retrieval path noticing that
#: a row it walked past could not be interpreted, which is a change to the gate
#: and the status tool rather than to this store; it is carried as a Milestone 6
#: issue. What this set buys until then is that the reach cannot grow in
#: silence -- a fifth position appears here as a failure, and each of the four
#: disappears the moment its surface starts refusing instead.
SILENTLY_EMPTIED: Final = frozenset(
    {
        ("knowledge.search", "knowledge_items", "item_id"),
        ("knowledge.search", "knowledge_items", "project_id"),
        ("knowledge.status", "knowledge_items", "project_id"),
        ("knowledge.status", "migration_history", "project_id"),
    }
)

#: Declared by the DDL, written by no migration operation and read by no store
#: method. Excluded from the corpus-coverage guard with its reason, so the guard
#: stays a real check on every other table.
UNPOPULATED_TABLES: Final = frozenset({"traceability_edges"})

#: How many columns a single ``UPDATE`` can put a string into, across the whole
#: populated schema. Arithmetic over the DDL, table by table, ``INTEGER PRIMARY
#: KEY`` rowids and `traceability_edges` excluded: 4 + 8 + 24 + 13 + 4 + 6 + 11
#: + 13 + 11 + 5.
#:
#: An exact number rather than a floor. ``len(columns) > 90`` -- what stood here
#: -- let nine columns vanish from the sweep without a word, and a sweep that
#: has quietly stopped covering a column asserts nothing about it while still
#: reporting green.
CORRUPTIBLE_COLUMN_COUNT: Final = 99

BODY: Final = "# Authentication policy\n\nEvery call carries a signed token.\n"
DRAFT_BODY: Final = "# Caching draft\n\nA proposal nobody has reviewed.\n"

MIGRATION_ID: Final = "01K1AAAAAA01234567890ABCDE"

#: One migration that reaches every table a migration can reach. The narrow
#: fixtures elsewhere in this suite leave `knowledge_relations`,
#: `knowledge_aliases`, `knowledge_evidence` and `specifications` empty, and a
#: sweep over an empty table asserts nothing about the converters that read it.
MIGRATION: Final = f"""apiVersion: theurian.dev/v1
id: {MIGRATION_ID}
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
    metadata:
      title: Authentication policy
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      trustLevel: reviewed
      labels: [security]
      scope:
        paths: ["services/api"]
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/auth-policy.md
  - op: createItem
    itemId: architecture.caching-draft
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.caching-draft
    revisionId: 01K1BBBREV01234567890ABCDE
    contentFile: ../knowledge/architecture/caching-draft.md
    metadata:
      title: Caching draft
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: draft
      owner: platform-team
      trustLevel: inferred
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/caching-draft.md
  - op: addRelation
    sourceItemId: architecture.auth-policy
    relationType: depends_on
    targetItemId: architecture.caching-draft
    note: the token cache
  - op: addAlias
    alias: architecture.auth
    itemId: architecture.auth-policy
  - op: addEvidence
    itemId: architecture.auth-policy
    anchor:
      provider: git
      sourceUri: git://demo/evidence.md
    description: the RFC that decided it
    confidence: 0.75
  - op: registerSpecification
    specId: spec.auth
    itemId: architecture.auth-policy
    sourceUri: git://demo/spec.yaml
    format: application/yaml
    status: active
"""


# -- Reading a published message ------------------------------------------


def leaked_fragments(text: str) -> tuple[str, ...]:
    """Every recognisable piece of :data:`SENTINEL` present in ``text``.

    Two overlapping tests, because a message can echo a cell either whole or in
    pieces: contiguous windows of :data:`LEAK_WINDOW` characters, and the rare
    whitespace-separated tokens on their own. ``sk-live-9f2a7c41d8e3`` alone is
    the whole disclosure even if the rest of the cell never appears.
    """
    windows = {SENTINEL[i : i + LEAK_WINDOW] for i in range(len(SENTINEL) - LEAK_WINDOW + 1)}
    tokens = {token for token in SENTINEL.split() if len(token) >= 6}
    return tuple(sorted(piece for piece in windows | tokens if piece in text))


def _command_paths() -> frozenset[str]:
    """Every command path the shipped CLI accepts, read off the Typer app.

    A remedy is checked against the real command set rather than against a list
    written here, so a remedy naming a command that was renamed or removed stops
    counting as a remedy.
    """

    def walk(command: Any, prefix: tuple[str, ...] = ()) -> Iterator[str]:
        children = getattr(command, "commands", None)
        if children:
            for name, child in children.items():
                yield from walk(child, (*prefix, name))
        elif prefix:
            yield " ".join(prefix)

    return frozenset(walk(typer.main.get_command(app)))


def _tool_names(registry: ProjectRegistry) -> frozenset[str]:
    """Every MCP tool the server registers. A remedy may name one of these."""
    return frozenset(tool.name for tool in build_server(registry)._tool_manager.list_tools())


def names_a_remedy(text: str, *, commands: frozenset[str], tools: frozenset[str]) -> bool:
    """Whether ``text`` names something the caller can actually run.

    Matched only in the forms a published remedy really uses -- ``theurian
    <path>``, or the name inside backquotes -- because bare names are ordinary
    English and would make almost anything look actionable. Three concrete false
    positives this shape closes, all of them measured rather than imagined:

    - ``version`` occurs in "schema version" and ``init`` in "initialised";
    - the SDK prefixes every failure with ``Error executing tool
      knowledge.get:``, so a bare tool-name test calls *every* refusal a remedy.
      That one silently emptied this check: `'X' is not present in project 'Y'.`
      counted as actionable because of the prefix the SDK had added to it.
    """
    return any(f"theurian {path}" in text or f"`{path}`" in text for path in commands) or any(
        f"`{name}`" in text for name in tools
    )


# -- The corpus ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Corpus:
    """A registered, migrated, indexed project and a pristine copy of its state."""

    registry: ProjectRegistry
    root: Path
    database: Path
    pristine: Path


def _run(*args: str) -> None:
    result = runner.invoke(app, [*args, "--json"], catch_exceptions=False)
    assert result.exit_code == 0, result.stdout + (result.stderr or "")


def _invoke(*args: str) -> tuple[int, str]:
    """Run a CLI command, returning its exit code and everything it printed."""
    result = runner.invoke(app, [*args, "--json"])
    text = (result.stdout or "") + (result.stderr or "")
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        # An uncaught exception *does* reach a terminal as text: Typer installs a
        # Rich traceback, which prints the exception's own message under the
        # frame that raised it. `CliRunner` swallows it instead of rendering it,
        # so appending it here is what keeps this sweep looking at what an
        # operator sees. Measured with `schema_metadata.schema_version`
        # overwritten: the real `theurian migrate apply` printed a boxed
        # traceback ending in the cell, exit 1, with empty JSON on stdout.
        text += f"{type(result.exception).__name__}: {result.exception}"
    return result.exit_code, text


@pytest.fixture
def corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Corpus:
    """One project holding a row in every table a migration can write.

    Indexed as well as migrated, so `knowledge.search` answers through
    ``ResultGate`` -- the canonical read site reached by
    ``store_factory=SqliteCanonicalStore`` rather than by a direct construction,
    and therefore the one a search for ``SqliteCanonicalStore(`` does not find.
    """
    root = tmp_path / "demo"
    root.mkdir()
    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603

    data_dir = tmp_path / "datadir"
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(data_dir))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(root)

    _run("init")
    knowledge = root / ".theurian/knowledge/architecture"
    (knowledge / "auth-policy.md").write_text(BODY)
    (knowledge / "caching-draft.md").write_text(DRAFT_BODY)
    (root / f".theurian/migrations/{MIGRATION_ID}-auth.yaml").write_text(MIGRATION)
    _run("project", "register")
    _run("migrate", "apply")
    _run("index", "build")

    (database,) = (root / ".theurian/state").glob("theurian-state-*.sqlite")
    pristine = tmp_path / "pristine-state.sqlite"
    shutil.copy2(database, pristine)
    return Corpus(ProjectRegistry.default(data_dir), root, database, pristine)


# -- Corrupting one cell ---------------------------------------------------


@dataclass(frozen=True, slots=True)
class Column:
    table: str
    name: str

    @override
    def __str__(self) -> str:
        return f"{self.table}.{self.name}"


def corruptible_columns(database: Path) -> tuple[Column, ...]:
    """Every column an on-disk bit flip could turn into a string, from the schema.

    Read out of the live database rather than listed, so the population follows
    the DDL. Two exclusions, both structural rather than editorial:

    - a table with no rows has no cell to corrupt;
    - an ``INTEGER PRIMARY KEY`` is SQLite's rowid and refuses a text value, so
      no ``UPDATE`` can put one there.

    Both are asserted elsewhere in this file rather than trusted: the first by
    :func:`test_every_table_the_schema_declares_holds_a_row_to_corrupt`, the
    second by :func:`test_every_column_outside_a_rowid_really_took_the_cell`.
    """
    connection = sqlite3.connect(database)
    try:
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        columns: list[Column] = []
        for table in tables:
            rows = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
            if rows == 0:
                continue
            for _cid, name, declared, _notnull, _default, pk in connection.execute(
                f"PRAGMA table_info({table})"
            ):
                if pk == 1 and declared.upper() == "INTEGER":
                    continue
                columns.append(Column(table, name))
        return tuple(columns)
    finally:
        connection.close()


#: A ``CREATE TABLE`` body, up to the closing paren that sits alone on a line.
_TABLE_BLOCK: Final = re.compile(r"CREATE TABLE (\w+) \((.*?)\n\);", re.DOTALL)

#: Lines inside a table body that declare a constraint rather than a column.
_CONSTRAINT_HEADS: Final = frozenset({"CHECK", "PRIMARY", "UNIQUE", "FOREIGN", "CONSTRAINT"})


def declared_corruptible_columns() -> frozenset[Column]:
    """The same population, parsed out of :data:`DDL` instead of the live file.

    Deliberately a second, independent derivation. :func:`corruptible_columns`
    asks SQLite through ``PRAGMA table_info``; this reads the source text. A
    change that narrows one -- widening the rowid exclusion, say, so real
    columns stop being swept -- moves the two apart, and
    :func:`test_every_column_outside_a_rowid_really_took_the_cell` names exactly
    which columns went missing rather than reporting a count that got smaller.
    """
    declared: set[Column] = set()
    for table, body in _TABLE_BLOCK.findall(DDL):
        if table in UNPOPULATED_TABLES:
            continue
        for raw in body.splitlines():
            line = raw.strip().rstrip(",")
            if not line:
                continue
            name, _, rest = line.partition(" ")
            if name.upper() in _CONSTRAINT_HEADS:
                continue
            if "INTEGER PRIMARY KEY" in " ".join(rest.split()).upper():
                continue
            declared.add(Column(table, name))
    return frozenset(declared)


def corrupt(database: Path, column: Column) -> bool:
    """Write :data:`SENTINEL` into ``column``, returning whether anything landed.

    ``PRAGMA ignore_check_constraints`` because real corruption is a bit flip on
    a page and never passes through a constraint. Without it `confidence`, whose
    ``CHECK`` bounds it to [0, 1], could not be given a string at all -- and
    `float()` over a cell that is not a number is one of the two families the
    index store records a guard having missed.

    Every row takes the cell where the column allows it, so a corrupted value in
    a *withheld* row is swept as well as one in a visible row. Where that
    collides with a primary key, the first row alone is corrupted.
    """
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA ignore_check_constraints = ON")
    try:
        try:
            connection.execute(
                f"UPDATE {column.table} SET {column.name} = ?",  # noqa: S608 - schema-derived
                (SENTINEL,),
            )
            connection.commit()
            return True
        except sqlite3.Error:
            connection.rollback()
        try:
            connection.execute(
                f"UPDATE {column.table} SET {column.name} = ? "  # noqa: S608 - schema-derived
                f"WHERE rowid = (SELECT MIN(rowid) FROM {column.table})",
                (SENTINEL,),
            )
            connection.commit()
        except sqlite3.Error:
            connection.rollback()
            return False
        return True
    finally:
        connection.close()


def restore(corpus: Corpus) -> None:
    """Put the pristine state database back, WAL sidecars included."""
    for suffix in ("-wal", "-shm"):
        Path(str(corpus.database) + suffix).unlink(missing_ok=True)
    shutil.copy2(corpus.pristine, corpus.database)


def holds_sentinel(database: Path, column: Column) -> bool:
    """Whether the cell really carries :data:`SENTINEL` after a corruption."""
    connection = sqlite3.connect(database)
    try:
        found = connection.execute(
            f"SELECT COUNT(*) FROM {column.table} WHERE {column.name} = ?",  # noqa: S608
            (SENTINEL,),
        ).fetchone()[0]
        return bool(found)
    finally:
        connection.close()


# -- Calling the published surfaces ---------------------------------------

#: Each MCP tool with an argument set that reaches the canonical store.
TOOL_CALLS: Final = (
    ("knowledge.search", {"projectId": "demo", "query": "token"}),
    ("knowledge.get", {"projectId": "demo", "itemId": "architecture.auth-policy"}),
    ("knowledge.status", {"projectId": "demo"}),
)


@dataclass(frozen=True, slots=True)
class Answer:
    """What one call gave back: either a refusal's text, or a payload."""

    refused: bool
    text: str


async def call_tool(server: Any, tool: str, arguments: dict[str, Any]) -> Answer:
    """Invoke one tool and capture what a client would receive.

    ``call_tool`` re-raises a failing tool as the SDK's own ``ToolError``; the
    transport turns it into ``isError=True`` content carrying the same message.
    Either way the *text* is what reaches the caller, which is what this file is
    about.
    """
    try:
        result = await server.call_tool(tool, arguments)
    except SdkToolError as exc:
        return Answer(refused=True, text=str(exc))
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return Answer(refused=False, text=json.dumps(structured, ensure_ascii=False))
    return Answer(refused=False, text=result.content[0].text)


async def sweep(corpus: Corpus) -> dict[tuple[str, str, str], Answer]:
    """Corrupt every column in turn and record what each tool answered.

    One server for the whole sweep, as a running daemon would have.
    """
    server = build_server(corpus.registry)
    observed: dict[tuple[str, str, str], Answer] = {}
    for column in corruptible_columns(corpus.database):
        assert corrupt(corpus.database, column), f"{column} took no value"
        try:
            for tool, arguments in TOOL_CALLS:
                observed[tool, column.table, column.name] = await call_tool(server, tool, arguments)
        finally:
            restore(corpus)
    return observed


# -- The corpus really covers the schema ----------------------------------


def _declared_tables() -> frozenset[str]:
    return frozenset(re.findall(r"CREATE TABLE (\w+)", DDL))


def test_every_table_the_schema_declares_holds_a_row_to_corrupt(corpus: Corpus) -> None:
    """Guards every sweep below. An empty table is a silently skipped population.

    The sweep reads its columns from the live database, so a table the corpus
    never populates disappears from it without a word -- which is how a
    converter family comes to be "covered" by a fixture that never reaches it.
    Compared against the DDL rather than against a list, so a table added in a
    later milestone fails here until the migration above writes to it.
    """
    connection = sqlite3.connect(corpus.database)
    try:
        populated = {
            table
            for table in _declared_tables()
            if connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
        }
    finally:
        connection.close()

    assert populated == _declared_tables() - UNPOPULATED_TABLES, (
        "the corpus no longer covers every canonical table; a sweep over the "
        "missing one asserts nothing"
    )


def test_every_column_outside_a_rowid_really_took_the_cell(corpus: Corpus) -> None:
    """Guards the population against a corruption that silently does nothing.

    ``corrupt`` returns True on a committed ``UPDATE``, which an ``UPDATE`` that
    matched no row also does. This reads the cell back: a column that reports
    success without holding the sentinel would put an untested column in the
    sweep and make every assertion about it vacuous.

    The population is pinned two ways before that runs, because a sweep can also
    be hollowed out by never reaching a column at all. Set equality against
    :func:`declared_corruptible_columns` names any column that stopped being
    swept; :data:`CORRUPTIBLE_COLUMN_COUNT` catches the case set equality cannot
    -- a column added to the DDL, which the live database and the parsed DDL
    both grow at once and neither notices.
    """
    columns = corruptible_columns(corpus.database)
    declared = declared_corruptible_columns()

    assert set(columns) == declared, (
        "the swept columns and the DDL's own columns have moved apart; "
        f"missing from the sweep: {sorted(map(str, declared - set(columns)))}, "
        f"swept but undeclared: {sorted(map(str, set(columns) - declared))}"
    )
    assert len(columns) == CORRUPTIBLE_COLUMN_COUNT, (
        f"the schema now offers {len(columns)} corruptible columns rather than "
        f"{CORRUPTIBLE_COLUMN_COUNT}; every exact set in this file was measured "
        f"against the old population"
    )

    silent: list[str] = []
    for column in columns:
        corrupt(corpus.database, column)
        if not holds_sentinel(corpus.database, column):
            silent.append(str(column))
        restore(corpus)

    assert not silent, f"corrupted without effect: {silent}"


# -- The control ----------------------------------------------------------


@pytest.mark.asyncio
async def test_an_intact_state_database_answers_every_tool(corpus: Corpus) -> None:
    """The control. Without it nothing below is attributable to the corruption.

    Every refusal the sweeps observe has to be caused by the damaged cell, and
    the only way to know that is that the same call against the same corpus
    succeeds when the cell is intact.
    """
    server = build_server(corpus.registry)

    answers = {tool: await call_tool(server, tool, args) for tool, args in TOOL_CALLS}

    assert [tool for tool, answer in answers.items() if answer.refused] == [], (
        f"a tool refused an undamaged database: {answers}"
    )


def test_an_intact_state_database_answers_the_cli(corpus: Corpus) -> None:
    """The control for the CLI half. `index build` reads the canonical store
    through ``IndexBuilder``, which is the second `store_factory=` site."""
    codes = {" ".join(cmd): _invoke(*cmd) for cmd in (("index", "build"), ("index", "status"))}

    assert [name for name, (code, _) in codes.items() if code != 0] == [], (
        f"a command failed against an undamaged database: {codes}"
    )


# -- The disclosure property ----------------------------------------------


@pytest.mark.asyncio
async def test_no_tool_refusal_repeats_a_byte_of_the_state_database(corpus: Corpus) -> None:
    """SEC-13, issue #18. The property the whole file exists for.

    Asserted over refusals only, and that is deliberate: a *successful*
    `knowledge.get` returns the corrupted `title` and `sourceAnchors` in its
    payload, which is the caller's own content answered correctly. Six of the
    sixty-six positions where the sentinel surfaces on `67a792c` are exactly
    that, and treating them as leaks would make this test assert that the store
    stops answering.

    Reported as the whole set rather than at the first failure, because the
    defect is a class: the four faces the reproduction found were four converter
    families, and a test that stopped at `created_at` would have sent someone to
    fix one of them.
    """
    observed = await sweep(corpus)

    leaked = {
        position: leaked_fragments(answer.text)
        for position, answer in observed.items()
        if answer.refused and leaked_fragments(answer.text)
    }

    assert not leaked, f"{len(leaked)} refusals published the corrupted cell: {leaked}"


@pytest.mark.asyncio
async def test_a_damaged_row_the_caller_may_not_read_discloses_nothing(
    corpus: Corpus,
) -> None:
    """SEC-13. The sharpest face: the cell belongs to a document that is withheld.

    `architecture.caching-draft` is a `draft`, so a default `knowledge.search`
    withholds it and `knowledge.get` answers "not present". Every row is still
    walked before the gate runs, so damage in the draft's row raises during
    conversion -- and on `67a792c` that exception carried the draft's bytes to a
    caller who had just been refused the draft.

    Only the draft's row is corrupted here, unlike the sweep, so a message
    carrying the sentinel can have come from nowhere else.
    """
    connection = sqlite3.connect(corpus.database)
    try:
        changed = connection.execute(
            "UPDATE knowledge_items SET valid_from = ? WHERE item_id = ?",
            (SENTINEL, "architecture.caching-draft"),
        ).rowcount
        connection.commit()
    finally:
        connection.close()
    assert changed == 1, "the draft row must exist, or this corrupts nothing"

    server = build_server(corpus.registry)
    answers = {tool: await call_tool(server, tool, args) for tool, args in TOOL_CALLS}

    leaked = {
        tool: leaked_fragments(answer.text)
        for tool, answer in answers.items()
        if leaked_fragments(answer.text)
    }

    assert [tool for tool, answer in answers.items() if answer.refused], (
        "the draft's row must be interpreted by some tool, or this asserts nothing"
    )
    assert not leaked, f"a withheld document's cell reached the caller: {answers}"


#: A window that closes before it opens, and a timestamp no corpus produces.
#: Both halves are *valid* values -- a parseable ISO-8601 string in a column
#: whose type it fits -- so what fails is the domain invariant and not a
#: converter. That distinction is the whole point of the test below.
_IMPOSSIBLE_VALID_TO: Final = "1999-01-02T03:04:05.678901+00:00"


@pytest.mark.asyncio
async def test_a_broken_invariant_over_a_withheld_row_publishes_neither_operand(
    corpus: Corpus,
) -> None:
    """The case that decides whether the guard may carve out invariant violations.

    An `InvariantViolationError` looks like the one exception worth letting
    through: it is the domain reporting a real integrity failure -- INV-3's
    content-hash check is how a tampered stored hash is caught on read -- and
    wrapping it as "this database cannot be read" reads like losing that signal.

    It is not, and this is the measurement. `ValidityPeriod.__post_init__`
    renders **both timestamps verbatim** into its message, and `_item_from_row`
    builds one for every item the store hands back, including the ones the gate
    is about to withhold. So a caller who is refused `architecture.caching-draft`
    as "not present" would, under a carve-out, be told in the same breath exactly
    when that document's validity window opens and closes.

    The values injected here are individually valid -- a parseable ISO-8601
    string in a TEXT column -- so `datetime.fromisoformat` succeeds and the only
    thing that fails is the invariant. A guard that excluded invariant
    violations would therefore let this one through while still catching every
    converter, which is precisely why it cannot be tested by the sweep above.

    What the wrapping does *not* cost: the published message still names
    `InvariantViolationError`, and the original travels on `__cause__`. The
    integrity signal survives; only its operands are withheld.
    """
    connection = sqlite3.connect(corpus.database)
    try:
        # A `CHECK` refuses this window, and real corruption never passes
        # through one -- see `corrupt`.
        connection.execute("PRAGMA ignore_check_constraints = ON")
        real_valid_from = connection.execute(
            "SELECT valid_from FROM knowledge_items WHERE item_id = ?",
            ("architecture.caching-draft",),
        ).fetchone()[0]
        changed = connection.execute(
            "UPDATE knowledge_items SET valid_to = ? WHERE item_id = ?",
            (_IMPOSSIBLE_VALID_TO, "architecture.caching-draft"),
        ).rowcount
        connection.commit()
    finally:
        connection.close()
    assert changed == 1, "the draft row must exist, or this breaks no invariant"

    server = build_server(corpus.registry)
    answers = {tool: await call_tool(server, tool, args) for tool, args in TOOL_CALLS}
    published = {tool: answer.text for tool, answer in answers.items()}

    assert [tool for tool, answer in answers.items() if answer.refused], (
        "no tool built a ValidityPeriod from the draft's row; nothing here is tested"
    )
    assert [tool for tool, text in published.items() if _IMPOSSIBLE_VALID_TO in text] == [], (
        f"the withheld document's validity window reached the caller: {published}"
    )
    assert [tool for tool, text in published.items() if real_valid_from in text] == [], (
        f"the withheld document's start of validity reached the caller: {published}"
    )


def test_no_cli_output_repeats_a_byte_of_the_state_database(corpus: Corpus) -> None:
    """The same property on the CLI half of the class.

    `theurian index build` reads every project, item and revision through the
    canonical store, so it reaches converters no MCP tool does -- `projects.
    registered_at` among them. Asserted over *all* of its output rather than only
    over failures, because unlike `knowledge.get` this command publishes counts
    and paths and never a document's content: a cell in its output came from a
    converter's complaint whatever the exit code.

    """
    leaked: dict[str, tuple[str, ...]] = {}
    for column in corruptible_columns(corpus.database):
        assert corrupt(corpus.database, column), f"{column} took no value"
        try:
            _code, text = _invoke("index", "build")
            if fragments := leaked_fragments(text):
                leaked[str(column)] = fragments
        finally:
            restore(corpus)

    assert not leaked, f"`index build` published the corrupted cell: {leaked}"


# -- The remedy property --------------------------------------------------


@pytest.mark.asyncio
async def test_every_refusal_over_a_damaged_database_names_a_remedy(corpus: Corpus) -> None:
    """A refusal a caller cannot act on repeats forever.

    The state database is derived and git-ignored (ADR-0004), so the remedy is
    always cheap and always the same -- but an agent that receives `Expecting
    value: line 1 column 1 (char 0)` has no way to know that, and will re-issue
    the identical query. This is the face the disclosure test cannot see: the
    `json.loads` family leaked nothing and named nothing.

    The exemption is an **exact set**, not an allowance. Written as
    ``names_a_remedy(...) or is_exempt(...)`` this test would pass for an
    implementation that stopped naming remedies entirely; written as an equality
    over positions it fails the moment a new converter refuses silently.
    """
    commands, tools = _command_paths(), _tool_names(corpus.registry)
    observed = await sweep(corpus)

    silent = {
        position
        for position, answer in observed.items()
        if answer.refused and not names_a_remedy(answer.text, commands=commands, tools=tools)
    }

    assert all(any(f in observed[p].text for f in _ID_RESOLUTION_REFUSALS) for p in silent), (
        f"a refusal named neither a remedy nor an id it could not resolve: "
        f"{ {p: observed[p].text for p in silent} }"
    )
    assert silent == REFUSALS_WITHOUT_A_REMEDY, (
        "the set of refusals that name no remedy has moved; each one is a caller "
        "left with no next action"
    )


def test_every_cli_failure_over_a_damaged_database_carries_a_remedy(corpus: Corpus) -> None:
    """The CLI publishes `remedy` as a field, so the check is the field itself.

    `--json` output is a contract: a failure is `{"error": ..., "remedy": ...}`.
    A command that failed with an empty or absent `remedy` has broken that
    contract, and no amount of prose in `error` substitutes for it.
    """
    without: dict[str, str] = {}
    for column in corruptible_columns(corpus.database):
        assert corrupt(corpus.database, column), f"{column} took no value"
        try:
            code, text = _invoke("index", "build")
            if code == 0:
                continue
            try:
                remedy = json.loads(text).get("remedy", "")
            except json.JSONDecodeError:
                remedy = ""
            if not remedy:
                without[str(column)] = text
        finally:
            restore(corpus)

    assert not without, f"`index build` failed without a remedy: {without}"


# -- The sweep really reaches each converter family ------------------------

#: One column per converter family this store reads, with the family named.
#:
#: The families come from the index store's own key -- **does this line
#: interpret bytes that came out of this file?** -- applied to the canonical
#: store: `datetime.fromisoformat`, `int`, `json.loads`, the six enums, and the
#: domain value objects, which raise `DomainError` rather than `ValueError` and
#: so escape any guard written over the latter.
#:
#: `float()` is absent, and that is a stated gap rather than an oversight: its
#: only canonical home is `knowledge_evidence.confidence`, and no MCP tool or CLI
#: command reads `list_evidence`. It is swept -- the population is the whole
#: schema -- but nothing observes it, so it cannot appear here.
CONVERTER_FAMILIES: Final = (
    ("datetime.fromisoformat", "knowledge_revisions", "created_at"),
    ("int", "schema_metadata", "schema_version"),
    ("json.loads", "knowledge_revisions", "scope_paths"),
    ("KnowledgeStatus", "knowledge_items", "status"),
    ("MediaType", "knowledge_revisions", "content_type"),
    ("RelationType", "knowledge_relations", "relation_type"),
    ("ContentHash / INV-3", "knowledge_revisions", "body"),
)


@pytest.mark.asyncio
@pytest.mark.parametrize(("family", "table", "name"), CONVERTER_FAMILIES)
async def test_the_corpus_reaches_each_converter_family(
    corpus: Corpus, family: str, table: str, name: str
) -> None:
    """Guards the sweeps against being vacuous, one family at a time.

    "No refusal leaked" is satisfied perfectly by a corpus that produces no
    refusals -- which is what a fixture with an empty `knowledge_relations` does
    for `RelationType`, and what a project with no index does for the ranked
    path. This asserts that the damaged cell *is* interpreted: some published
    tool must refuse the same call it answered against the intact database.

    A refusal rather than merely a different answer, and that is the stronger
    claim on purpose. A store that read a corrupt canonical cell and answered
    anyway would be answering from state it could not interpret, which is the
    failure this product exists to prevent -- worse than refusing, not better.
    """
    column = Column(table, name)
    assert corrupt(corpus.database, column), f"{column} took no value"
    assert holds_sentinel(corpus.database, column)

    server = build_server(corpus.registry)
    answers = {tool: await call_tool(server, tool, args) for tool, args in TOOL_CALLS}

    assert [tool for tool, answer in answers.items() if answer.refused], (
        f"no tool interpreted the {family} cell in {column}; every assertion "
        f"about this family is vacuous. Answers: {answers}"
    )


# -- Answering successfully with less than the file holds -------------------


def _published_integers(text: str) -> dict[str, int]:
    """Every integer a payload publishes, keyed by its path through the JSON.

    Derived from the payload rather than from a list of field names, so a count
    added to a response in a later milestone is compared without anyone
    remembering to add it here. Booleans are excluded: `True` is an `int` in
    Python and ``stale: false -> true`` is not a shrinking count.
    """
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {}

    found: dict[str, int] = {}

    def walk(node: object, path: str) -> None:
        if isinstance(node, bool):
            return
        if isinstance(node, int):
            found[path] = node
        elif isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            found[f"{path}[]"] = len(node)
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")

    walk(payload, "")
    return found


@pytest.mark.asyncio
async def test_no_tool_answers_with_less_than_the_intact_database_holds(
    corpus: Corpus,
) -> None:
    """The face no property framed around refusals can see, stated as a set.

    Both other sweeps read ``answer.refused`` before they assert anything, so a
    tool that answers *successfully* and wrongly is structurally invisible to
    them -- and that is the worse outcome, not the milder one. `knowledge.search`
    replying ``{"count": 0, "results": [], "retrieval": {"stale": false}}`` over
    a damaged `knowledge_items.project_id` tells an agent that the index is fresh
    and this project holds no answer, which is a false statement it will act on;
    a refusal at least stops it.

    The comparison is against the same corpus one moment earlier, so a shrinking
    count cannot be a property of the corpus or of the clock. Only shrinking is
    read: a corrupted `title` changes what `knowledge.get` returns and that is
    the caller's own content answered correctly, which is why this is not "the
    answer changed".

    :data:`SILENTLY_EMPTIED` is an exact set and the behaviour is carried to
    Milestone 6. What must not happen in the meantime is the set growing without
    anyone noticing, and an inequality here would have permitted exactly that.
    """
    server = build_server(corpus.registry)
    intact = {
        tool: _published_integers((await call_tool(server, tool, args)).text)
        for tool, args in TOOL_CALLS
    }
    assert all(intact.values()), f"a tool published no integer to compare against: {intact}"

    emptied: dict[tuple[str, str, str], dict[str, str]] = {}
    for column in corruptible_columns(corpus.database):
        assert corrupt(corpus.database, column), f"{column} took no value"
        try:
            for tool, args in TOOL_CALLS:
                answer = await call_tool(server, tool, args)
                if answer.refused:
                    continue
                published = _published_integers(answer.text)
                shrunk = {
                    field: f"{before} -> {published[field]}"
                    for field, before in intact[tool].items()
                    if field in published and published[field] < before
                }
                if shrunk:
                    emptied[tool, column.table, column.name] = shrunk
        finally:
            restore(corpus)

    assert set(emptied) == SILENTLY_EMPTIED, (
        f"the set of positions where a tool answers successfully with less than it "
        f"holds has moved. Newly emptied: "
        f"{ {p: emptied[p] for p in set(emptied) - SILENTLY_EMPTIED} }; "
        f"no longer emptied: {sorted(SILENTLY_EMPTIED - set(emptied))}"
    )
