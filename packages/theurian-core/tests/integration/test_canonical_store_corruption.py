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

import ast
import inspect
import json
import re
import shutil
import sqlite3
import subprocess
import textwrap
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, override

import pytest
import typer.main
from mcp.server.mcpserver.exceptions import ToolError as SdkToolError
from typer.testing import CliRunner

from theurian.application.project_service import ProjectRegistry
from theurian.cli.main import app
from theurian.daemon.runner import build_server
from theurian.domain.context import RequestContext
from theurian.domain.enums import KnowledgeKind, KnowledgeStatus, Sensitivity, TrustLevel
from theurian.domain.identifiers import ItemId, MigrationId, ProjectId, RevisionId
from theurian.domain.knowledge import KnowledgeRevision, RevisionMetadata, SourceAnchor
from theurian.domain.values import MARKDOWN, ValidityPeriod
from theurian.infrastructure.sqlite import store as sqlite_store
from theurian.infrastructure.sqlite.connection import (
    SchemaVersionMismatchError,
    StateDatabaseUnreadableError,
    write_transaction,
)
from theurian.infrastructure.sqlite.schema import DDL, SCHEMA_VERSION
from theurian.infrastructure.sqlite.store import SqliteCanonicalStore, SqliteWriter

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

#: :data:`SENTINEL` reduced to what SQLite accepts as an identifier, for the one
#: damage this file writes into the schema text rather than into a row. Spaces
#: and `=` would leave DDL that does not parse, which is a *different* failure.
#:
#: Still caught by :func:`leaked_fragments`: `9f2a7c41d8e3`, `payroll` and
#: `240000` survive the substitution unchanged, so a message that quotes this
#: identifier is recognised as a disclosure exactly as a quoted cell is.
SCHEMA_SENTINEL: Final = re.sub(r"[^0-9A-Za-z]", "_", SENTINEL)

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
#: The fourth position -- `knowledge.status` over a corrupt
#: `knowledge_items.status` -- arrived with the T-17 timing fix (#19).
#: `knowledge.status` now counts the surfaceable statuses in SQL instead of
#: parsing every row into a `KnowledgeStatus`, so a sentinel in the status column
#: fails the ``IN`` predicate and is under-reported rather than raising. Detecting
#: that corruption was a side effect of the O(total-rows) parse the fix removes:
#: the two are coupled through row examination and cannot both hold in the store,
#: and the silent 0 is also the confidentiality-correct answer, so this was
#: accepted rather than reverted and carried with the rest of the class (#30).
#:
#: **A fifth position left this set in #30 PR1.** `(knowledge.status,
#: migration_history, project_id)` used to belong here: a sentinel in that column
#: dropped every migration row out of the `WHERE`, so the tool answered
#: `appliedMigrations: 0` against a project that had applied several. PR1 closes
#: it -- `knowledge.status` now reports `appliedMigrations` from the active
#: pointer's own `migrationCount` (so it no longer shrinks) and emits the
#: `integrity` damage signal when the live migration-row count disagrees with it.
#: The position therefore no longer answers with a smaller integer, so it is no
#: longer a member of this set; it is caught rather than carried.
#:
#: The remaining four are recorded rather than fixed. Closing them means the
#: retrieval path noticing that a row it walked past could not be interpreted,
#: which is a change to the gate and the status tool rather than to this store;
#: they are carried as a Milestone 6 issue (#30). What this set buys until then
#: is that the reach cannot grow in silence -- a fifth position appears here as a
#: failure, and each of the four disappears the moment its surface starts
#: refusing, or emitting `integrity`, instead.
SILENTLY_EMPTIED: Final = frozenset(
    {
        ("knowledge.search", "knowledge_items", "item_id"),
        ("knowledge.search", "knowledge_items", "project_id"),
        ("knowledge.status", "knowledge_items", "project_id"),
        ("knowledge.status", "knowledge_items", "status"),
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


# -- The CLI population ----------------------------------------------------

#: Every command path this file corrupts the database underneath.
#:
#: Chosen by what is *safe to run against one corpus a few hundred times*, not
#: by what is believed to read the canonical store -- believing it is how this
#: sweep came to be one command wide while two HIGH findings walked out through
#: `migrate status` and `migrate apply`. What the population actually reaches is
#: measured rather than asserted here, by
#: :func:`test_exactly_these_commands_notice_a_single_damaged_cell`.
CLI_SWEEP: Final = (
    ("index", "build"),
    # Swept rather than excluded, though it never opens the canonical store: it
    # resolves the project through the registry and prints *filenames*, and the
    # sweep's question is what reaches a caller's output, not which file the
    # damage was in. A `gc` that echoed a resolved path or a pointer fragment
    # would be caught here and nowhere else.
    ("index", "gc"),
    ("index", "status"),
    ("migrate", "status"),
    ("migrate", "validate"),
    ("migrate", "apply"),
    ("project", "list"),
    ("project", "status"),
    ("version",),
)

#: Every remaining command path, with the reason it cannot be swept. Held as an
#: exact partition of the real Typer app by
#: :func:`test_every_shipped_command_is_swept_or_excluded_with_a_reason`, so a
#: command added in a later milestone has to be classified rather than
#: forgotten.
CLI_NOT_SWEPT: Final = {
    "auth rotate": "rotates a stored token, so the corpus stops being the same corpus",
    "compat check": "requires --core-version and friends; resolves no project",
    "daemon start": "spawns a process and binds a port",
    "daemon status": "probes for a daemon this suite must not have running",
    "daemon stop": "signals a process this suite must not have running",
    "doctor": "a health report, not a command over this project's state: it exits "
    "non-zero on a healthy corpus because the fixture installs no Claude Code",
    "ingest": "writes migration files, which moves the state hash and so the database",
    "init": "writes .theurian/ and appends to .gitignore in the working directory",
    "project register": "rewrites the registry the corpus was built from",
    "project unregister": "deletes the registration every other command resolves",
    "setup": "writes ~/.claude.json and a LaunchAgent on the developer's own machine",
    "uninstall": "removes what `setup` installed, on the developer's own machine",
}

#: The commands a single damaged cell can actually make fail, stated exactly.
#:
#: The vacuity guard for :data:`CLI_SWEEP`. "No swept command leaked" is
#: satisfied perfectly by a sweep whose commands never open the database at all,
#: and five of the eight above are exactly that today -- they resolve the
#: project, read the pointer and answer from files this corruption never
#: touches. Stating which three do the work means a change that stops one of
#: them reaching the store fails here rather than quietly hollowing out both
#: properties below.
COMMANDS_THAT_NOTICE_A_DAMAGED_CELL: Final = frozenset(
    {"index build", "migrate status", "migrate apply"}
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


def _rendered_traceback(exc: BaseException) -> str:
    """Every exception a Rich traceback would print for ``exc``, message included.

    **The chain, not the exception.** ``rich.traceback`` follows ``__cause__``
    and ``__context__`` and renders each link with its own message, so `raise
    ... from exc` publishes the cause to whoever reads the terminal. That is the
    whole disclosure surface of an uncaught failure here:
    `StateDatabaseUnreadableError` withholds the cell from its own message and
    keeps the real exception on ``__cause__`` for whoever holds the traceback --
    which, for a CLI command, is the operator.

    Measured against the real `theurian migrate status` with
    `migration_history.checksum` overwritten: exit 1, empty stdout, and the last
    line of the boxed traceback reading ``DomainError: ContentHash must be 64
    lowercase hex characters, got '<the cell>'``. Appending only
    ``type(exc).__name__`` and ``str(exc)`` -- what stood here -- reproduced the
    *withheld* half and made this sweep blind to the half that is published.
    """
    parts: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        parts.append(f"{type(current).__name__}: {current}")
        following = current.__cause__
        if following is None and not current.__suppress_context__:
            following = current.__context__
        current = following
    return "\n".join(parts)


def _invoke(*args: str) -> tuple[int, str]:
    """Run a CLI command, returning its exit code and everything it printed.

    An uncaught exception *does* reach a terminal as text: Typer installs a Rich
    traceback and renders it to stderr. ``CliRunner`` swallows it onto
    ``result.exception`` instead, so :func:`_rendered_traceback` is what keeps
    this sweep looking at what an operator sees rather than at what the runner
    happened to keep.
    """
    result = runner.invoke(app, [*args, "--json"])
    text = (result.stdout or "") + (result.stderr or "")
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        text += _rendered_traceback(result.exception)
    return result.exit_code, text


def _build_corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Corpus:
    """Build the corpus under ``tmp_path``, with `HOME` and the data directory moved.

    Both redirections are made through ``monkeypatch`` and never through
    ``os.environ``, so a corpus built for a module-scoped fixture leaves nothing
    behind when its context closes. ``chdir`` is here too: the CLI resolves a
    project from the working directory, so a sweep that forgot it would resolve
    the developer's own checkout.
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


@pytest.fixture
def corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Corpus:
    """One project holding a row in every table a migration can write.

    Indexed as well as migrated, so `knowledge.search` answers through
    ``ResultGate`` -- the canonical read site reached by
    ``store_factory=SqliteCanonicalStore`` rather than by a direct construction,
    and therefore the one a search for ``SqliteCanonicalStore(`` does not find.
    """
    return _build_corpus(tmp_path, monkeypatch)


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


def cli_sweep(corpus: Corpus) -> dict[tuple[str, str, str], tuple[int, str]]:
    """The same sweep over :data:`CLI_SWEEP`, one command per corruption.

    The database is restored between *commands*, not merely between columns.
    ``migrate apply`` opens a write transaction and upserts the project row, so
    a shared corruption would be a different corruption by the time the next
    command ran, and an observation attributed to the wrong cell is worse than
    no observation.
    """
    observed: dict[tuple[str, str, str], tuple[int, str]] = {}
    for column in corruptible_columns(corpus.database):
        for command in CLI_SWEEP:
            assert corrupt(corpus.database, column), f"{column} took no value"
            try:
                observed[" ".join(command), column.table, column.name] = _invoke(*command)
            finally:
                restore(corpus)
    return observed


@pytest.fixture(scope="module")
def cli_observations(
    tmp_path_factory: pytest.TempPathFactory,
) -> dict[tuple[str, str, str], tuple[int, str]]:
    """One CLI sweep -- 99 columns by eight commands -- read by three properties.

    Module-scoped over its own corpus, never over the function-scoped one. The
    sweep is corpus-neutral by construction (every corruption is restored before
    the next command runs), so sharing the *result* is safe; sharing the corpus
    with tests that damage a schema or open a write transaction would not be,
    which is why this builds its own.

    Shared because it is the expensive thing in this file. Recomputing it per
    property meant roughly 2,400 CLI invocations for three assertions over the
    same 792 observations.
    """
    with pytest.MonkeyPatch.context() as patch:
        corpus = _build_corpus(tmp_path_factory.mktemp("cli-sweep"), patch)
        return cli_sweep(corpus)


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
    """The control for the CLI half, over the whole swept population.

    Every non-zero exit the CLI sweeps observe has to be caused by the damaged
    cell. Asserted for all of :data:`CLI_SWEEP` rather than for the two commands
    that used to be swept, because a command that already fails on a healthy
    corpus contributes only noise to the remedy property below -- which is why
    `doctor`, whose non-zero exit means "problems found", is excluded from the
    population rather than tolerated inside it.
    """
    codes = {" ".join(cmd): _invoke(*cmd) for cmd in CLI_SWEEP}

    assert [name for name, (code, _) in codes.items() if code != 0] == [], (
        f"a command failed against an undamaged database: {codes}"
    )


def test_every_shipped_command_is_swept_or_excluded_with_a_reason() -> None:
    """The CLI population is a partition of the real app, not a list someone kept.

    This sweep was one command wide -- `index build` -- while the shipped CLI had
    twenty, and the two findings that walked out of `migrate status` and
    `migrate apply` were invisible to every property in this file. A list of
    commands to sweep cannot fail; a partition of the command set can, and a
    command added in a later milestone fails here until someone says which half
    it belongs in.
    """
    swept = frozenset(" ".join(command) for command in CLI_SWEEP)
    excluded = frozenset(CLI_NOT_SWEPT)

    assert swept & excluded == frozenset(), (
        f"a command is both swept and excluded: {sorted(swept & excluded)}"
    )
    assert swept | excluded == _command_paths(), (
        f"unclassified commands: {sorted(_command_paths() - swept - excluded)}; "
        f"classified but no longer shipped: {sorted((swept | excluded) - _command_paths())}"
    )
    assert all(CLI_NOT_SWEPT.values()), "an exclusion without a reason is a command someone forgot"


def test_exactly_these_commands_notice_a_single_damaged_cell(
    cli_observations: dict[tuple[str, str, str], tuple[int, str]],
) -> None:
    """The vacuity guard for the CLI sweeps. Measured, and stated exactly.

    Both CLI properties below are quantified over :data:`CLI_SWEEP`, and both are
    satisfied trivially by a command that never opens the state database. Five of
    the eight are in that position today -- they answer from the registry, the
    active pointer and the migration files, none of which this corruption
    touches -- so without this the sweep could lose `migrate apply` to a
    refactor and keep reporting green over seven commands that assert nothing.

    An exact set rather than "at least one": a command that *starts* reading the
    canonical store is a new surface for the same class, and it should arrive as
    a failure here rather than as a leak found in a review round.
    """
    noticed = {
        command for (command, _table, _name), (code, _text) in cli_observations.items() if code != 0
    }

    assert noticed == COMMANDS_THAT_NOTICE_A_DAMAGED_CELL, (
        "the set of commands a damaged cell reaches has moved; "
        f"newly reaching it: {sorted(noticed - COMMANDS_THAT_NOTICE_A_DAMAGED_CELL)}, "
        f"no longer reaching it: {sorted(COMMANDS_THAT_NOTICE_A_DAMAGED_CELL - noticed)}"
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


def test_no_cli_output_repeats_a_byte_of_the_state_database(
    cli_observations: dict[tuple[str, str, str], tuple[int, str]],
) -> None:
    """The same property on the CLI half of the class.

    `theurian index build` reads every project, item and revision through the
    canonical store, so it reaches converters no MCP tool does -- `projects.
    registered_at` among them. Asserted over *all* of its output rather than only
    over failures, because unlike `knowledge.get` this command publishes counts
    and paths and never a document's content: a cell in its output came from a
    converter's complaint whatever the exit code.

    Over the whole population rather than over the command that reaches the
    store widest. `index build` walks every table and so looked like the strong
    case, but breadth is not reach: `migration_history.checksum` is a column it
    exits 0 over and all three MCP tools stay silent about, and it published
    that cell verbatim through both `migrate status` and `migrate apply` while
    this file reported green over 297 positions.
    """
    leaked = {
        position: max(fragments, key=len)
        for position, (_code, text) in cli_observations.items()
        if (fragments := leaked_fragments(text))
    }

    assert not leaked, (
        f"{len(leaked)} command outputs published the corrupted cell "
        f"(longest fragment each): {leaked}"
    )


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


def _published_remedy(text: str) -> str:
    """The ``remedy`` field of a ``--json`` failure, or the empty string."""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return ""
    return str(payload.get("remedy", "")) if isinstance(payload, dict) else ""


def test_every_cli_failure_over_a_damaged_database_carries_a_remedy(
    cli_observations: dict[tuple[str, str, str], tuple[int, str]],
) -> None:
    """The CLI publishes `remedy` as a field, so the check is the field itself.

    `--json` output is a contract: a failure is `{"error": ..., "remedy": ...}`.
    A command that failed with an empty or absent `remedy` has broken that
    contract, and no amount of prose in `error` substitutes for it -- including
    prose in an uncaught exception's message, which is where this property is
    hardest and where a one-command sweep could not look. An exception that
    escapes a `--json` command prints a Rich traceback and *nothing* on stdout,
    so a caller parsing the contract gets an empty document and no field at all.
    """
    without = {
        position: text
        for position, (code, text) in cli_observations.items()
        if code != 0 and not _published_remedy(text)
    }

    assert not without, f"{len(without)} command failures carried no remedy: {without}"


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


# -- Where the guard sits, on the writer -----------------------------------
#
# `SqliteWriter` reads five times and is guarded four times, and until now
# nothing anywhere held either half. Deleting any of the four guards left the
# suite green while the corrupted cell walked out of `theurian migrate status`
# -- so the placement was correct and unproven, which is the state a later edit
# removes without noticing. The remaining read, `append_revision`'s
# `content_sha256`, is unguarded on purpose, and
# `test_a_failure_inside_the_write_transaction_never_offers_to_delete_the_state`
# below is the half that holds the absence.
#
# Reached through the writer directly rather than through the CLI, and that is
# forced rather than convenient. `record_migration`, `get_item`,
# `list_revision_ids` and `append_revision` run only for a *pending* migration,
# and adding one to the corpus changes the migration set, which changes the state
# hash, which sends the next command to a different -- empty, undamaged --
# database file (ADR-0016). There is no CLI invocation that reaches them over a
# damaged state. `applied_migrations` is the exception and is swept through
# `migrate status` as well.

PROJECT_ID: Final = ProjectId("demo")
ITEM_ID: Final = ItemId("architecture.auth-policy")
REVISION_ID: Final = RevisionId("01K1AAAREV01234567890ABCDE")
APPLIED_AT: Final = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)

#: Four of the five reads :class:`SqliteWriter` performs, each over a cell whose
#: converter quotes what it would not accept. Every one of these four sits behind
#: a guard today. The fifth, `append_revision`'s `content_sha256`, deliberately
#: does not, and is held one test below rather than here.
WRITER_READS: Final = (
    (
        "get_item",
        "knowledge_items",
        "status",
        lambda writer: writer.get_item(PROJECT_ID, ITEM_ID),
    ),
    (
        # A retirement asks this for the revisions a still-published index must
        # stop holding (ADR-0024 decision 5), and it builds a `RevisionId` per
        # row -- so a corrupt `revision_id` reaches `theurian migrate apply`'s
        # transaction exactly as `get_item`'s corrupt `status` does.
        "list_revision_ids",
        "knowledge_revisions",
        "revision_id",
        lambda writer: writer.list_revision_ids(PROJECT_ID, ITEM_ID),
    ),
    (
        "applied_migrations",
        "migration_history",
        "migration_id",
        lambda writer: writer.applied_migrations(PROJECT_ID),
    ),
    (
        "record_migration",
        "migration_history",
        "sequence",
        lambda writer: writer.record_migration(
            PROJECT_ID, MigrationId(MIGRATION_ID), "c" * 64, APPLIED_AT
        ),
    ),
)

#: The other half of the partition: every read :class:`SqliteWriter` performs that
#: is deliberately *outside* a ``_reading()`` block, with the reason it interprets
#: nothing.
#:
#: Held against the shipped source together with :data:`WRITER_READS` by
#: :func:`test_every_read_the_writer_performs_is_guarded_or_excluded_with_a_reason`,
#: so a read added in a later milestone has to be classified rather than
#: forgotten. Keyed by ``(method, table)`` -- the table the ``SELECT`` names, not
#: the column, because one read may interpret several cells.
WRITER_READS_NOT_GUARDED: Final = {
    ("append_revision", "knowledge_revisions"): (
        "compares two `content_sha256` strings and interprets neither, so no "
        "converter can put the stored cell into a message. The mismatch branch "
        "*does* interpret, and that one line -- `ContentHash(stored)` -- is "
        "guarded on its own. Guarding the read itself would answer a conflicting "
        "write with a remedy that deletes the state, which is what "
        "`test_a_failure_inside_the_write_transaction_never_offers_to_delete_the_state` "
        "holds."
    ),
}

#: The context manager that marks a read as guarded, by name.
#:
#: Matched lexically, so :func:`_writer_reads` reports a read as guarded only when
#: the read sits *inside* the block -- which is the distinction the whole
#: partition rests on. `append_revision` calls `_reading()` and is still an
#: unguarded read: its guard wraps the interpretation three lines further in, not
#: the ``SELECT``. A scan keyed on "this method mentions `_reading`" would call it
#: guarded and pass while the guard moved off the read entirely.
_GUARD: Final = "_reading"

#: What the scan counts as a read: the SQL verb that pulls bytes off the page, and
#: the cursor methods that carry rows away from one.
_READ_VERB: Final = "SELECT"
_FETCH_METHODS: Final = frozenset({"fetchone", "fetchall", "fetchmany"})
_TABLE_IN_SQL: Final = re.compile(r"\bFROM\s+([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)

#: Stands in for a table this scan could not read out of the SQL, so that such a
#: read joins the population as an unclassifiable member rather than being
#: dropped. A read the scanner cannot name must fail the partition, not vanish
#: from it -- silently skipping the reads it does not understand is the one way a
#: derived population degrades back into a list.
_UNRESOLVED: Final = "<table not derivable from the SQL literal>"


@dataclass(frozen=True)
class ReadSite:
    """One place :class:`SqliteWriter` pulls bytes out of the state database."""

    method: str
    table: str
    guarded: bool


def _select_table(node: ast.Call) -> str | None:
    """The table an ``.execute("SELECT ...")`` reads, or ``None`` if not a read."""
    if not (isinstance(node.func, ast.Attribute) and node.func.attr == "execute"):
        return None
    if not (node.args and isinstance(node.args[0], ast.Constant)):
        return None

    sql = node.args[0].value
    if not isinstance(sql, str) or not sql.lstrip().upper().startswith(_READ_VERB):
        return None

    found = _TABLE_IN_SQL.search(sql)
    return found.group(1) if found else _UNRESOLVED


def _unattributed_fetch(node: ast.Call) -> str | None:
    """A ``fetch*`` whose rows came from a statement this scan could not read.

    A ``fetch*`` chained straight onto a ``SELECT`` literal is the same read
    :func:`_select_table` already counted, so it is not counted twice. One reached
    through a variable -- ``cursor = conn.execute(sql); cursor.fetchall()`` -- is a
    read whose SQL is not in the tree, and it enters the population unresolved.
    """
    if not (isinstance(node.func, ast.Attribute) and node.func.attr in _FETCH_METHODS):
        return None
    receiver = node.func.value
    if isinstance(receiver, ast.Call) and _select_table(receiver) is not None:
        return None
    return _UNRESOLVED


def _opens_guard(node: ast.With | ast.AsyncWith) -> bool:
    """Whether this ``with`` statement is the ``_reading()`` guard."""
    return any(
        isinstance(item.context_expr, ast.Call)
        and isinstance(item.context_expr.func, ast.Name)
        and item.context_expr.func.id == _GUARD
        for item in node.items
    )


def _writer_reads() -> frozenset[ReadSite]:
    """Every read in :class:`SqliteWriter`, read out of the shipped source.

    Parsed from the source of the class *as imported*, so the tree scanned is the
    tree the suite runs against rather than a path assembled relative to this
    file, which can drift from the installed package.
    """
    found: set[ReadSite] = set()

    def visit(node: ast.AST, method: str | None, guarded: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                visit(child, method or child.name, guarded)
                continue
            if isinstance(child, ast.With | ast.AsyncWith):
                visit(child, method, guarded or _opens_guard(child))
                continue
            if isinstance(child, ast.Call) and method is not None:
                table = _select_table(child) or _unattributed_fetch(child)
                if table is not None:
                    found.add(ReadSite(method, table, guarded))
            visit(child, method, guarded)

    visit(ast.parse(textwrap.dedent(inspect.getsource(SqliteWriter))), None, False)
    return frozenset(found)


def _write_lock(corpus: Corpus) -> Path:
    """The project's real write lock, so this writer serialises like any other."""
    return corpus.root / ".theurian/runtime/write.lock"


@pytest.mark.parametrize(("method", "table", "name", "call"), WRITER_READS)
def test_a_writers_read_of_a_damaged_cell_answers_without_quoting_it(
    corpus: Corpus,
    method: str,
    table: str,
    name: str,
    call: Callable[[SqliteWriter], object],
) -> None:
    """SEC-13 on the write path. The same property, three reads nothing held.

    A write transaction is not a private context: `theurian migrate status`
    opens one, and everything raised inside it reaches an operator through
    Typer's Rich traceback. Measured with `migration_history.migration_id`
    overwritten and the guard removed, `migrate status` printed
    ``InvalidIdentifierError: MigrationId must be a 26-character ... got
    'ROTATE-ME sk-live-...'`` -- the cell, verbatim, from a command that reports
    on migrations and has no business publishing a stored value at all.

    Asserted over what the caller receives rather than over an exception type,
    like everything else in this file: no fragment of the cell, and a remedy the
    caller can run. The second half is what separates this from "it raised
    something": an unguarded `ValueError` names no next action, and an agent
    that receives one re-issues the identical command.
    """
    column = Column(table, name)
    assert corrupt(corpus.database, column), f"{column} took no value"
    assert holds_sentinel(corpus.database, column)

    with (
        pytest.raises(Exception) as caught,
        write_transaction(corpus.database, _write_lock(corpus)) as connection,
    ):
        call(SqliteWriter(connection))

    published = str(caught.value)
    assert leaked_fragments(published) == (), (
        f"`SqliteWriter.{method}` published the corrupted {column}: {published}"
    )
    assert names_a_remedy(published, commands=_command_paths(), tools=frozenset()), (
        f"`SqliteWriter.{method}` refused {column} with no next action: {published}"
    )


def test_a_failure_inside_the_write_transaction_never_offers_to_delete_the_state(
    corpus: Corpus,
) -> None:
    """The inverse, and the reason the writer is guarded three times and not four.

    `append_revision` reads a stored `content_sha256` and is deliberately *not*
    guarded, because past ``BEGIN IMMEDIATE`` a failure is the caller's statement
    against the caller's data. Answering one of those with "delete
    `.theurian/state/` and run `theurian migrate apply`" would hand an operator a
    destructive remedy for a write that simply did not apply -- and, worse, would
    make a conflicting write indistinguishable from a damaged file.

    The absence of a guard is as much a decision as its presence and was as
    unheld: wrapping that one read left the whole suite green. The two arms here
    are the boundary itself. The same damage -- a schema whose
    `knowledge_revisions` no longer declares the column both sides ask for, which
    is what a rewritten `sqlite_master.sql` cell looks like -- is a damaged
    database on the read side and the caller's problem on the write side.

    Neither arm may publish the cell, which is why the sentinel is written into
    the schema text rather than into a row: a `sqlite3` complaint about a broken
    schema quotes names it read out of the file.
    """
    # Built before the damage and before either `pytest.raises`, so a domain
    # object this test failed to construct is an error here rather than a pass
    # in the write arm -- which is what the first draft of this test did.
    revision = _a_revision_the_store_already_holds(corpus)

    connection = sqlite3.connect(corpus.database, isolation_level=None)
    try:
        connection.execute("PRAGMA writable_schema = ON")
        changed = connection.execute(
            "UPDATE sqlite_master SET sql = replace(sql, 'content_sha256', ?) "
            "WHERE type = 'table' AND name = 'knowledge_revisions'",
            (SCHEMA_SENTINEL,),
        ).rowcount
    finally:
        connection.close()
    assert changed == 1, "the schema row must exist, or this damages nothing"

    commands = _command_paths()
    context = RequestContext(project_id=PROJECT_ID)

    with pytest.raises(Exception) as reading, SqliteCanonicalStore(corpus.database) as store:
        store.list_revisions(context, ITEM_ID)

    with (
        pytest.raises(Exception) as writing,
        write_transaction(corpus.database, _write_lock(corpus)) as connection,
    ):
        SqliteWriter(connection).append_revision(revision)

    assert names_a_remedy(str(reading.value), commands=commands, tools=frozenset()), (
        f"a read over a damaged schema named no remedy: {reading.value}"
    )
    assert not names_a_remedy(str(writing.value), commands=commands, tools=frozenset()), (
        f"a write inside an open transaction was answered with a remedy that deletes "
        f"the state: {writing.value}"
    )
    assert (leaked_fragments(str(reading.value)), leaked_fragments(str(writing.value))) == (
        (),
        (),
    ), f"the damaged schema reached a caller: read={reading.value} write={writing.value}"


def _a_revision_the_store_already_holds(corpus: Corpus) -> KnowledgeRevision:
    """The corpus's own approved revision, rebuilt with different content.

    Read through a plain `sqlite3` connection rather than through the store,
    because the store is what the surrounding test is measuring. Read from the
    *pristine* copy, so the caller may damage the live database first.
    """
    connection = sqlite3.connect(corpus.pristine)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            "SELECT * FROM knowledge_revisions WHERE revision_id = ?",
            (REVISION_ID.value,),
        ).fetchone()
    finally:
        connection.close()
    assert row is not None, "the corpus must hold this revision, or nothing is appended"

    return KnowledgeRevision.create(
        revision_id=REVISION_ID,
        item_id=ITEM_ID,
        project_id=PROJECT_ID,
        migration_id=MigrationId(MIGRATION_ID),
        title=row["title"],
        # Different content under an existing id, so an intact schema answers
        # this with the immutability invariant rather than with silence.
        body="Rewritten by a migration nobody approved.\n",
        content_type=MARKDOWN,
        metadata=RevisionMetadata(
            kind=KnowledgeKind(row["kind"]),
            namespace=row["namespace"],
            status=KnowledgeStatus(row["status"]),
            trust_level=TrustLevel(row["trust_level"]),
            sensitivity=Sensitivity(row["sensitivity"]),
            owner=row["owner"],
        ),
        validity=ValidityPeriod(valid_from=datetime.fromisoformat(row["valid_from"])),
        author=row["author"],
        created_at=datetime.fromisoformat(row["created_at"]),
        # INV-4: a revision Theurian did not author needs somewhere it came
        # from, and `KnowledgeRevision.create` refuses one without it.
        source_anchors=(SourceAnchor(provider="git", source_uri="git://demo/rewritten.md"),),
    )


def test_the_scan_looks_for_a_guard_the_store_actually_defines() -> None:
    """Guards the partition below, which matches a name and cannot resolve a type.

    If ``_reading`` is renamed, every read in the writer reads as *unguarded*, and
    the partition fails saying that three guarded reads disappeared and three
    unclassified ones arrived. That is a rename reported as a wholesale loss of
    protection, which is the kind of failure whose expectation gets updated
    instead of read. This says which it was.
    """
    assert hasattr(sqlite_store, _GUARD), (
        f"`{_GUARD}` no longer exists in the store module, so the scan below is "
        f"looking for a guard the product has stopped using and will report every "
        f"read as unguarded. Rename _GUARD with it"
    )


def test_every_read_the_writer_performs_is_guarded_or_excluded_with_a_reason() -> None:
    """The writer's reads are a partition of the source, not a list someone kept.

    :data:`WRITER_READS` was a `parametrize` list and nothing else: a fifth read
    added to :class:`SqliteWriter` without a guard failed no test in this suite.
    The same shape twice cost this file real accuracy -- two comments here said
    the writer reads three times while a test in this same file said "guarded
    three times *and not four*", and the prose was wrong for as long as nothing
    checked it against the source.

    **The key is reads, not interpretations, and that is the decision.**
    ``_reading()`` exists to catch interpretations -- its own key is *does this
    line interpret bytes that came out of this file?* -- and that question is not
    decidable from a syntax tree. ``int(row["s"])`` interprets; ``stored !=
    revision.content_sha256.value`` does not; both are expressions over a fetched
    row. What the tree *does* decide is where bytes enter the writer: a ``SELECT``
    handed to ``execute``, and any ``fetch*`` carrying rows away from one.

    Reads are the safe side of that difference, because every interpretation is a
    read. Quantifying over reads over-approximates the population the guard cares
    about and therefore cannot miss one; quantifying over interpretations would
    require the scan to infer which lines convert, and a scanner that infers that
    wrongly says nothing and passes. So the semantic judgement is not made here at
    all -- *this read interprets nothing* is written down per read, with its
    reason, in :data:`WRITER_READS_NOT_GUARDED`.

    That is what a future reader needs from a red run: the two repairs are
    opposite. A new read that interprets a stored cell belongs in
    :data:`WRITER_READS`, behind a guard, where the sweep above corrupts its table
    and proves it refuses without quoting the cell. A new read that only moves
    opaque bytes belongs in :data:`WRITER_READS_NOT_GUARDED` with the argument for
    why -- and guarding it anyway is not the safe default, because past
    ``BEGIN IMMEDIATE`` a guard offers to delete the operator's state database in
    answer to a write that merely conflicted.
    """
    reads = _writer_reads()
    guarded = frozenset((site.method, site.table) for site in reads if site.guarded)
    unguarded = frozenset((site.method, site.table) for site in reads if not site.guarded)
    swept = frozenset((method, table) for method, table, _name, _call in WRITER_READS)
    excluded = frozenset(WRITER_READS_NOT_GUARDED)

    assert reads, (
        "no reads found in `SqliteWriter` at all -- the scan is looking at the "
        "wrong tree, or `SELECT` stopped being written as a literal, and every "
        "assertion below is vacuous"
    )
    assert guarded == swept, (
        f"guarded reads in the source and the swept set disagree.\n"
        f"  guarded in source but not swept by WRITER_READS: {sorted(guarded - swept)}\n"
        f"  swept by WRITER_READS but not guarded in source: {sorted(swept - guarded)}\n"
        f"A guarded read that WRITER_READS does not carry is never corrupted by "
        f"the sweep above, so nothing shows that it refuses without quoting the "
        f"cell. A swept entry the source no longer guards is a guard someone "
        f"removed."
    )
    assert unguarded == excluded, (
        f"unguarded reads in the source and the recorded exclusions disagree.\n"
        f"  unguarded in source, unclassified: {sorted(unguarded - excluded)}\n"
        f"  recorded as excluded but no longer present: {sorted(excluded - unguarded)}\n"
        f"Every read in `SqliteWriter` is either guarded and swept -- add it to "
        f"WRITER_READS with the column whose converter quotes what it would not "
        f"accept -- or unguarded on purpose, in which case record in "
        f"WRITER_READS_NOT_GUARDED why it interprets nothing. `{_UNRESOLVED}` "
        f"means the scan could not read the table out of the SQL, not that the "
        f"read is exempt."
    )
    assert all(WRITER_READS_NOT_GUARDED.values()), (
        "an exclusion without a reason is a read someone forgot to guard"
    )


# -- What the report says, and what it does not ----------------------------


def test_a_damaged_database_report_names_the_converter_that_failed(corpus: Corpus) -> None:
    """The detail is the failing exception's type, and nothing else can be.

    It is the whole of what this report carries beyond a fixed sentence, and the
    only thing that tells an operator holding two of them apart: a
    `ValueError` from a timestamp and an `InvalidIdentifierError` from a revision
    pointer are different repairs. Replacing it with an empty string leaves a
    grammatical, remedy-naming, entirely uninformative message -- and left the
    whole suite green.

    Two columns rather than one, because "the message contains the cause's type
    name" is satisfied by an implementation that hard-codes any single name.
    What is asserted is that the two reports *differ*, and that each names its
    own cause.
    """
    context = RequestContext(project_id=PROJECT_ID)
    reports: dict[str, str] = {}

    # An enum, which raises `ValueError`, and a domain value object, which raises
    # `InvalidIdentifierError` -- the two families whose failure to share a base
    # class is why the guard is written over the boundary and not the hierarchy.
    for column in (
        Column("knowledge_items", "status"),
        Column("knowledge_items", "current_revision_id"),
    ):
        assert corrupt(corpus.database, column), f"{column} took no value"
        try:
            with (
                pytest.raises(StateDatabaseUnreadableError) as caught,
                SqliteCanonicalStore(corpus.database) as store,
            ):
                store.get_item(context, ITEM_ID)
        finally:
            restore(corpus)

        cause = caught.value.__cause__
        assert cause is not None, f"{column}: the real exception did not travel on __cause__"
        assert type(cause).__name__ in str(caught.value), (
            f"{column}: the report does not say what failed. cause={type(cause).__name__}, "
            f"report={caught.value}"
        )
        reports[str(column)] = str(caught.value)

    _first, _second = reports.values()
    assert _first != _second, (
        f"two different converter failures produced the same report, so the detail "
        f"distinguishes nothing: {reports}"
    )


def test_a_nested_read_reports_the_converter_that_failed_not_the_wrapper(
    corpus: Corpus,
) -> None:
    """Reads nest, and a nested read must not answer with the answer's own name.

    `get_revision` maps its row by calling `_anchors_for`, which is a guarded
    read inside a guarded read. Without `StateDatabaseUnreadableError` in the
    already-answered set the outer guard wraps the inner one, and the detail --
    the only part of the report that carries information -- becomes the string
    ``StateDatabaseUnreadableError``, which says that a state database was
    unreadable to someone reading a message that already says so.

    `source_anchors.line_start` is the cell: `SourceAnchor` takes it as an int
    and the corrupted text reaches its comparison, so the failure happens in the
    *inner* read and the outer guard is what decides how it is reported.
    """
    column = Column("source_anchors", "line_start")
    assert corrupt(corpus.database, column), f"{column} took no value"
    assert holds_sentinel(corpus.database, column)
    context = RequestContext(project_id=PROJECT_ID)

    with (
        pytest.raises(StateDatabaseUnreadableError) as caught,
        SqliteCanonicalStore(corpus.database) as store,
    ):
        store.get_revision(context, REVISION_ID)

    chain: list[BaseException] = []
    current: BaseException | None = caught.value
    while current is not None:
        chain.append(current)
        current = current.__cause__

    wraps = [item for item in chain if isinstance(item, StateDatabaseUnreadableError)]
    assert len(wraps) == 1, (
        f"the nested read was answered {len(wraps)} times over: "
        f"{[type(item).__name__ for item in chain]}"
    )
    assert type(chain[-1]).__name__ in str(caught.value), (
        f"the report names the wrapper rather than the converter that failed: "
        f"chain={[type(item).__name__ for item in chain]}, report={caught.value}"
    )


def test_an_unsupported_schema_version_is_reported_as_a_version_not_as_damage(
    corpus: Corpus,
) -> None:
    """A header this build read successfully is not a damaged file.

    `schema_version` is the one cell whose *failure to be interpreted* and whose
    *successful interpretation* both stop a read, and they need different
    answers. A number this build does not support was read correctly: the file
    is intact, the build is the wrong one, and the caller needs the two version
    numbers to know that. Wrapping it discards both and asserts damage that is
    not there -- and the sweep cannot catch it, because the sweep writes text
    into that column and text is the *other* case.

    ADR-0017: state databases are rebuilt rather than migrated, so no
    compatibility window makes this unreachable.
    """
    unsupported = SCHEMA_VERSION + 1000
    connection = sqlite3.connect(corpus.database)
    try:
        changed = connection.execute(
            "UPDATE schema_metadata SET schema_version = ? WHERE id = 1", (unsupported,)
        ).rowcount
        connection.commit()
    finally:
        connection.close()
    assert changed == 1, "schema_metadata must hold its single row, or this changes nothing"

    with pytest.raises(SchemaVersionMismatchError) as caught, SqliteCanonicalStore(corpus.database):
        pass

    assert (caught.value.found, caught.value.expected) == (unsupported, SCHEMA_VERSION), (
        f"the mismatch does not say which two versions disagree: {caught.value!r}"
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
