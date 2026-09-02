"""Every read of the findings store is governed, however the reader reached it.

The store-level universal invariant ADR-0029's closure section names, and the
one instrument the three reach residuals slice-2 recorded are owed to. Those
residuals are all one shape -- *a reach my scanners cannot spell* -- and
``tests/unit/test_findings_store_is_unreachable.py`` records them as three faces:

- **(a) string-concatenation reach.** A serving module that assembles a store
  filename or a table name at runtime (``"find" + "ings"``) names neither in its
  source, so prong (a)'s AST import scan and prong (b)'s token grep both read it
  as clean.
- **(b) runtime one-hop transitivity.** ``test_findings_tool_registry.py`` walks
  a *tool's own* code object and its nested consts, so a tool that called a
  module-level helper which reached the store would show only the helper's name
  in its ``co_names``.
- **(c) the static sibling of (b).** Prong (a) reads each serving module's own
  import statements and does not follow them, so an adapter under an
  acknowledged subtree that imports the store, imported in turn by a serving
  module under an innocuous name, is invisible at both hops.

**Why one instrument closes all three: it never reads a name.** The three faces
evade *source spellings*; this file asserts nothing about source at all. It
audits the SQLite connections opened while the whole tool surface is driven,
identifies the store **by the file SQLite reports it opened** (``PRAGMA
database_list``, so a concatenated path and a literal one are the same file),
and holds every statement executed against that file to the three properties
the port promises (``domain/ports/review_finding_store.py``):

1. **no read of ``rejected_trailers``** -- author-controlled untrusted text with
   no reviewed serving surface;
2. **no unbounded read of ``findings``** -- every statement naming that table
   carries a ``LIMIT``, which is :class:`FindingQuery`'s positive-``limit``
   requirement expressed where it cannot be sidestepped by not using the type;
3. **no findings read on a connection that did not first read the stamp** --
   "current, or nothing", checked on the same handle the rows come back on, so
   a rebuild landing mid-call cannot have the check pass on one file and the
   rows come from another.

A reach spelled any of the three evasive ways still has to execute SQL against
that file to get bytes out of it, and reimplementing those three controls to
stay silent here is a diff a reviewer reads, not an accident -- which is exactly
the disposition slice-2 recorded for the residuals.

**What this does not cover, stated rather than implied.** The audit is a
*runtime* instrument, so it sees a reach only on a path the drive actually
executes; a store reference in code nothing calls is the static prongs' job, and
they remain the arm for that. :data:`_DRIVE` is therefore checked against the
built server's own tool list, so a new tool cannot be registered without being
driven here. Two mechanisms are outside it as well: a reader that constructed
``sqlite3.Connection`` directly instead of calling ``sqlite3.connect``, and one
that parsed the database file's bytes without SQLite at all. Both are recorded
bounds, not oversights -- neither is reachable without a diff at least as visible
as the one this file is written to catch.

The instrument's own prongs are demonstrated against a synthetic evasive reader
below (:func:`test_the_audit_catches_a_reach_that_names_neither_the_file_nor_the_table`),
whose file name *and* table names are assembled at runtime: it asserts each of
the three prongs fires, so a green run of the drive means the checks looked and
found nothing rather than that they stopped looking.
"""

from __future__ import annotations

import inspect
import re
import sqlite3
import subprocess
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final

import pytest
from migration_fixtures import body_pin
from typer.testing import CliRunner

from theurian.application.project_service import (
    FINDINGS_STORE_ID,
    BuildProvenance,
    ProjectPaths,
    ProjectRegistry,
)
from theurian.cli.main import app
from theurian.daemon.runner import build_server
from theurian.domain.knowledge import SourceAnchor
from theurian.domain.review_finding import (
    FindingLoad,
    FindingSeverity,
    RejectedTrailer,
    ReviewerToken,
    ReviewFinding,
)
from theurian.infrastructure.sqlite.findings_store import SqliteReviewFindingStore

pytestmark = pytest.mark.integration

runner = CliRunner()

MIGRATION_ID = "01K1BBBBBB01234567890ABCDE"
REVISION_ID = "01K1BBBREV01234567890ABCDE"
ITEM_ID = "architecture.auth-policy"
BODY = "# Authentication policy\n\nEvery call carries a signed token.\n"

MIGRATION = f"""apiVersion: theurian.dev/v1
id: {MIGRATION_ID}
createdAt: 2026-08-02T10:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: {ITEM_ID}
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: {ITEM_ID}
    revisionId: {REVISION_ID}
    contentFile: ../knowledge/architecture/auth-policy.md
    contentSha256: {body_pin(BODY)}
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


def _sha(seed: str) -> str:
    return seed * 40


def _finding(sha: str, *, text: str, when: str) -> ReviewFinding:
    return ReviewFinding(
        reviewer=ReviewerToken.SECURITY,
        severity=FindingSeverity.HIGH,
        finding_text=text,
        anchor=SourceAnchor(provider="git", source_uri=sha, commit_sha=sha),
        pull_request=None,
        date=datetime.fromisoformat(when),
        family=None,
        specialist=None,
    )


#: Two accepted findings and one rejected trailer. The rejected member is what
#: makes prong 1 of the audit answerable at all: over a corpus with nothing to
#: withhold, "no statement read the rejected table" is true of a store that has
#: no such rows to read.
LANDED = FindingLoad(
    accepted=(
        _finding(
            _sha("a"), text="a bearer token reached the log", when="2026-08-25T09:00:00+00:00"
        ),
        _finding(_sha("b"), text="a name reads as its opposite", when="2026-08-26T09:00:00+00:00"),
    ),
    rejected=(
        RejectedTrailer(
            _sha("c"),
            "Review-Finding: nonsense CRITICAL — the private key is in fixtures/",
            "unknown reviewer 'nonsense'",
        ),
    ),
)


def _run(*args: str) -> None:
    result = runner.invoke(app, [*args, "--json"], catch_exceptions=False)
    assert result.exit_code == 0, result.stdout + (result.stderr or "")


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ProjectRegistry]:
    """A registered, migrated project holding one approved item and a findings store.

    Built by the real CLI under a redirected ``THEURIAN_DATA_DIR``, because the
    drive below calls *every* registered tool: ``knowledge.get`` and
    ``knowledge.search`` need real canonical state to reach their own bodies, and
    a tool that bounced off an unresolvable project would execute none of the code
    a planted reach could hide in.
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
    monkeypatch.chdir(root)
    _run("init")
    (root / ".theurian/knowledge/architecture/auth-policy.md").write_text(BODY)
    (root / f".theurian/migrations/{MIGRATION_ID}-auth.yaml").write_text(MIGRATION)
    _run("project", "register")
    _run("migrate", "apply")

    yield ProjectRegistry.default(data_dir)


def _store_path(registry: ProjectRegistry) -> Path:
    root = Path(registry.load()["demo"]["rootPath"])
    return ProjectPaths.of(root).findings_for(FINDINGS_STORE_ID)


def _land(registry: ProjectRegistry) -> Path:
    path = _store_path(registry)
    SqliteReviewFindingStore(path).replace_all(LANDED)
    # Landing the file is not enough to make it servable: `review.findings` refuses
    # a store this installation has no record of building (ADR-0004, SEC-7, T-19),
    # so the audit below would observe no read at all without this. The command
    # `theurian findings build` makes the same record; it is not driven here because
    # it reads `refs/remotes/origin/main`, which this fixture repository lacks.
    BuildProvenance.for_registry(registry).record_findings(
        Path(registry.load()["demo"]["rootPath"]), FINDINGS_STORE_ID
    )
    return path


# -- The audit --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Read:
    """One statement executed against the findings store, and on which handle.

    ``handle`` is a counter this module assigns, never ``id(connection)``: ids
    are reused once an object is freed, and a reused id would let one
    connection's stamp read vouch for a *later* connection's rows -- which is
    precisely the split prong 3 exists to detect.
    """

    handle: int
    statement: str


#: The rejected table, in any case and through any quoting a SQL statement can
#: spell it with -- the substring is enough, since ``"rejected_trailers"``,
#: ``main.rejected_trailers`` and ``[rejected_trailers]`` all contain it.
_REJECTED_TABLE: Final = re.compile(r"rejected_trailers", re.IGNORECASE)

#: The findings table, on a word boundary so ``findings_metadata`` -- a
#: different table, and the one prong 3 requires -- does not read as a hit.
_FINDINGS_TABLE: Final = re.compile(r"\bfindings\b", re.IGNORECASE)

_METADATA_TABLE: Final = re.compile(r"\bfindings_metadata\b", re.IGNORECASE)

_BOUND: Final = re.compile(r"\bLIMIT\b", re.IGNORECASE)

#: The three prongs, named as the failure each reports.
_REJECTED_READ: Final = "a statement read the rejected-trailer table"
_UNBOUNDED_READ: Final = "a statement read the findings table with no LIMIT"
_UNSTAMPED_READ: Final = "a connection read findings rows without first reading the stamp"


def _opened_file(connection: sqlite3.Connection) -> Path | None:
    """The file SQLite says this connection opened, resolved -- or ``None``.

    ``PRAGMA database_list`` is the spelling-blind half of this instrument: it
    reports the path SQLite actually opened, so a filename assembled at runtime
    from split pieces (face (a)) is the same answer as a literal one. Asked
    before the trace callback is installed, so this probe never appears in the
    recorded statements it exists to scope.
    """
    try:
        rows = connection.execute("PRAGMA database_list").fetchall()
    except sqlite3.Error:
        # A connection to bytes that are not a database. Nothing can be read
        # from it, so it carries no statement worth auditing.
        return None
    for row in rows:
        if row[1] == "main" and row[2]:
            return Path(str(row[2])).resolve()
    return None


@contextmanager
def _audit(store: Path) -> Iterator[list[_Read]]:
    """Record every statement executed against ``store`` while the body runs.

    Patches ``sqlite3.connect`` -- the module function every reader in this
    codebase and every plausible new one goes through -- and installs a trace
    callback on each connection whose opened file *is* ``store``. The recorded
    statement is SQLite's own unexpanded SQL, so a caller's filter values never
    enter this log or the failure messages built from it.
    """
    reads: list[_Read] = []
    target = store.resolve()
    real_connect = sqlite3.connect
    handles = iter(range(1_000_000))

    def audited(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        connection: sqlite3.Connection = real_connect(*args, **kwargs)
        if _opened_file(connection) == target:
            handle = next(handles)
            connection.set_trace_callback(lambda statement: reads.append(_Read(handle, statement)))
        return connection

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(sqlite3, "connect", audited)
        yield reads


def _violations(reads: list[_Read]) -> dict[str, list[str]]:
    """Which of the three store-level promises the recorded reads broke.

    A pure function over the log, so the drive below and the synthetic evasive
    reader are graded by the same code -- a check asserted in one place and
    demonstrated in another proves nothing about the check that runs.

    **A statement naming both tables counts as stamping, and that is the ``elif``
    below rather than an accident of it.** One ``SELECT`` that joins ``findings``
    to ``findings_metadata`` has read the stamp in the same statement -- and, being
    one statement on one connection, from the same file -- which is exactly what
    the third prong asks for; reporting it as an unstamped read as well would fail
    a reader that satisfied the promise in a single round trip. The other two
    prongs still see it: a join is graded for the rejected table and for its bound
    in the first loop, where no ``elif`` intervenes.
    """
    broken: dict[str, list[str]] = {}
    for read in reads:
        if _REJECTED_TABLE.search(read.statement):
            broken.setdefault(_REJECTED_READ, []).append(read.statement)
        if _FINDINGS_TABLE.search(read.statement) and not _BOUND.search(read.statement):
            broken.setdefault(_UNBOUNDED_READ, []).append(read.statement)

    stamped: set[int] = set()
    for read in reads:
        if _METADATA_TABLE.search(read.statement):
            stamped.add(read.handle)
        elif _FINDINGS_TABLE.search(read.statement) and read.handle not in stamped:
            broken.setdefault(_UNSTAMPED_READ, []).append(read.statement)
    return broken


def _findings_reads(reads: list[_Read]) -> list[_Read]:
    return [read for read in reads if _FINDINGS_TABLE.search(read.statement)]


# -- The drive --------------------------------------------------------------


#: Every registered tool, with arguments that reach its body rather than bounce
#: off its bounds. Keyed by tool name and checked against the built server's own
#: list by :func:`test_the_drive_covers_every_registered_tool`, so a new tool is
#: not silently undriven -- an audit is only as wide as what it runs.
_DRIVE: Final[dict[str, tuple[dict[str, Any], ...]]] = {
    "knowledge.search": ({"projectId": "demo", "query": "signed token"},),
    "knowledge.get": ({"projectId": "demo", "itemId": ITEM_ID},),
    "knowledge.status": ({"projectId": "demo"},),
    "project.list": ({},),
    "review.findings": (
        {"projectId": "demo"},
        {"projectId": "demo", "limit": 1},
        {"projectId": "demo", "reviewer": "security", "severity": "HIGH"},
        {"projectId": "demo", "commitSha": _sha("c")},
        {"projectId": "demo", "q": "token"},
    ),
    "system.capabilities": ({},),
}


async def _drive_every_tool(registry: ProjectRegistry) -> None:
    """Call every registered tool, each with every argument shape recorded above."""
    server = build_server(registry)
    for name, calls in _DRIVE.items():
        for arguments in calls:
            await server.call_tool(name, arguments)


def test_the_drive_covers_every_registered_tool(
    project: ProjectRegistry,
) -> None:
    """The population guard: an undriven tool is an unaudited one.

    The instrument below is a runtime one, so it sees only what runs. A tool
    added to the server and not to :data:`_DRIVE` would leave the audit passing
    over a surface it never executed -- the shape that lets a new serving path
    inherit an argument nobody made for it. Stated as an equality so a *removed*
    tool is caught too: a stale entry here would drive a name the server no
    longer has, which is a call that fails rather than a check that passes, and
    naming it now is cheaper than reading that failure later.
    """
    registered = {tool.name for tool in build_server(project)._tool_manager.list_tools()}

    assert set(_DRIVE) == registered, (
        f"the audited drive covers {sorted(_DRIVE)} and the built server registers "
        f"{sorted(registered)}. A tool that is registered but not driven is a "
        f"serving path this file's audit never executes, so its store reads -- "
        f"however it spells them -- are unmeasured. Add arguments that reach the "
        f"new tool's body, not ones it refuses."
    )


@pytest.mark.asyncio
async def test_every_read_of_the_findings_store_is_governed_however_it_was_reached(
    project: ProjectRegistry,
) -> None:
    """The one instrument for the three recorded reach residuals (a), (b), (c).

    Slice-2 left three faces open, all of them a reach whose *spelling* defeats a
    source scan: a filename or table name concatenated at runtime (a), a tool
    calling a named helper that reaches the store one hop away (b), and a serving
    module importing an innocuously-named adapter that imports the store (c).
    None of them is answered by another string pattern, which is why this asserts
    over executed SQL instead: whatever module reached the file, and however it
    spelled the reach, the statements it ran against that file are the same three
    promises the port makes -- accepted rows only, bounded, and stamp-checked on
    the connection the rows come back on.

    The whole registered tool surface is driven, not only ``review.findings``,
    because faces (b) and (c) are not specific to the sanctioned tool: a helper
    reached from ``knowledge.get`` would be invisible to the bytecode walk, which
    inspects each tool's own code object.
    """
    store = _land(project)

    with _audit(store) as reads:
        await _drive_every_tool(project)

    assert _findings_reads(reads), (
        "the drive executed no statement against the findings store, so every "
        "assertion below holds vacuously. Either `sqlite3.connect` is no longer "
        "the seam readers go through, or `review.findings` stopped reading the "
        "store -- both make this instrument's green result meaningless."
    )
    assert _violations(reads) == {}, (
        "a statement executed against the findings store broke one of the port's "
        "three serving promises:\n"
        + "\n".join(
            f"  {failure}:\n" + "\n".join(f"    {statement}" for statement in statements)
            for failure, statements in sorted(_violations(reads).items())
        )
    )


# -- The guard on the guard: a reach that names neither file nor table -------


def _assembled(*parts: str) -> str:
    """The pieces, joined -- so no literal in this file spells the result.

    Face (a) in one function: a name a source scan cannot grep for, because the
    source never contains it.
    """
    return "".join(parts)


def _a_named_helper_that_reaches_the_store(directory: Path, name: str) -> list[Any]:
    """The one hop faces (b) and (c) hide behind, reaching the store by a built name.

    A tool calling this would show only this function's *name* in its own
    bytecode, and a serving module importing it would show only an innocuous
    module and symbol in its imports. Neither the file it opens nor the tables it
    reads appear as a literal anywhere in its source, so prong (b)'s token grep
    finds nothing to report either.
    """
    with closing(sqlite3.connect(directory / name)) as connection:
        rows: list[Any] = connection.execute(
            _assembled("SELECT raw_line FROM ", "rejected", "_trailers")
        ).fetchall()
        rows += connection.execute(
            _assembled("SELECT finding_text FROM ", "find", "ings")
        ).fetchall()
    return rows


@pytest.mark.asyncio
async def test_the_audit_catches_a_reach_that_names_neither_the_file_nor_the_table(
    project: ProjectRegistry,
) -> None:
    """All three prongs fire against the evasion the static scans are blind to.

    Without this, the drive above is a search that reports *nothing found*, which
    is also what a broken audit reports -- forever. So the same
    :func:`_violations` the drive is graded by is run over a reader built to be
    exactly what faces (a), (b) and (c) describe: it assembles the store's
    filename from characters and both table names from fragments, it is reached
    through a named module-level helper rather than inline, and it reads rejected
    rows, reads findings unbounded, and never looks at the stamp.

    That the reach really is invisible to a literal scan is asserted, not
    assumed: the helper's own source is searched for the tokens prong (b) greps
    for, and must contain neither.
    """
    store = _land(project)
    reach_source = inspect.getsource(_a_named_helper_that_reaches_the_store) + inspect.getsource(
        _assembled
    )
    assert _assembled("rejected", "_trailers") not in reach_source
    assert _assembled("find", "ings_metadata") not in reach_source
    assert store.name not in reach_source

    with _audit(store) as reads:
        _a_named_helper_that_reaches_the_store(store.parent, _assembled(*store.name))

    assert set(_violations(reads)) == {_REJECTED_READ, _UNBOUNDED_READ, _UNSTAMPED_READ}, (
        f"a reach spelled the way the three recorded residuals describe was not "
        f"caught on every prong: {sorted(_violations(reads))}. A prong that cannot "
        f"fire here is one the drive above passes vacuously."
    )
