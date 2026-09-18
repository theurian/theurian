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
import review_candidate_fixtures as corpus
from git_harness import commit_migrations
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
    if args[:2] == ("migrate", "apply"):
        # ADR-0034: commit the migration (tracked, byte-identical to HEAD) before
        # apply; a behavioural no-op today, green once the committed-check lands.
        commit_migrations()
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

    # `review.generateKnowledgeCandidate` verifies its `fixCommit` against this
    # repository (ADR-0033 decision 3), so the drive below needs a commit here that
    # touched the file the stored thread is anchored to. Staged alone, so the sha
    # `_verifying_commit` reads back means *this commit touched that path* rather
    # than *this commit touched everything*.
    (root / corpus.FILE_PATH).parent.mkdir(parents=True, exist_ok=True)
    (root / corpus.FILE_PATH).write_text("def retry():\n    pass\n")
    subprocess.run(  # noqa: S603
        ["git", "add", corpus.FILE_PATH],  # noqa: S607
        cwd=root,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "add retry helper"],  # noqa: S607
        cwd=root,
        check=True,
        capture_output=True,
    )

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
    _land_review_evidence(registry)
    return path


def _land_review_evidence(registry: ProjectRegistry) -> None:
    """A review search store beside the findings one, so ``review.search`` runs.

    The drive calls every registered tool, and a tool refused before its body runs
    executes no SQL -- which would leave the audit unable to say anything about
    ``review.search`` at all. With the store here, that tool reaches its own read
    and the audit observes what it does against the *findings* file: nothing. That
    absence is the point rather than a side effect. ``review.search`` is the second
    serving surface under ``.theurian/state/``, and "another tool reaching the same
    content" is the family a second store makes reachable; this instrument scopes
    by the opened **file**, so a review-search read that strayed onto the findings
    one would be recorded and graded like any other.
    """
    from datetime import UTC

    from theurian.application.project_service import REVIEW_SEARCH_STORE_ID
    from theurian.application.review_search_builder import (
        ReviewSearchBuilder,
        ReviewSearchBuildRequest,
    )
    from theurian.cli.review_commands import evidence_entries, evidence_fingerprints
    from theurian.domain.identifiers import ProjectId
    from theurian.domain.review import ReviewEvent, ReviewParticipant
    from theurian.infrastructure.review_evidence import (
        EvidenceRecord,
        IngestionRun,
        ReviewEvidenceStore,
    )
    from theurian.infrastructure.sqlite.review_search_store import SqliteReviewSearchStore

    paths = ProjectPaths.of(Path(registry.load()["demo"]["rootPath"]))
    repository = "acme/order-service"
    record = EvidenceRecord(
        provider="github",
        repository=repository,
        anchor=SourceAnchor(
            provider="github",
            source_uri=f"https://github.com/{repository}/pull/42",
            repository=repository,
            commit_sha=_sha("a"),
        ),
        payload=ReviewEvent(
            project_id=ProjectId("demo"),
            provider="github",
            repository=repository,
            number=42,
            title="Bound the retry budget",
            body="Only retry calls that carry a signed token.",
            author=ReviewParticipant(
                provider="github", external_id="USER_A", display_name="Reviewer One"
            ),
            created_at=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
            url=f"https://github.com/{repository}/pull/42",
            head_commit=_sha("b"),
            base_commit=_sha("c"),
            head_ref_name="fix/retry-budget",
            labels=("security",),
        ),
    )
    evidence = ReviewEvidenceStore(paths.review)
    # The corpus records join the one above rather than replacing it: that record
    # is what `review.search`'s own `pullRequest=42` and `q="token"` drive shapes
    # match, and a filter that matched nothing would still reach the body but would
    # audit a read that returned no rows. The corpus adds the thread
    # `review.generateKnowledgeCandidate` resolves, which has no other source --
    # ingestion needs the network, and `.theurian/review/` is source rather than
    # derived state, so writing the records here is the shape a clone delivers.
    evidence.write(
        (record, *corpus.evidence_records()),
        run=IngestionRun("01K1AAAAAA01234567890ABCDE", datetime(2026, 9, 7, 9, 0, tzinfo=UTC)),
    )
    store = SqliteReviewSearchStore(paths.review_search_for(REVIEW_SEARCH_STORE_ID))
    ReviewSearchBuilder(
        read_evidence=evidence_entries(evidence),
        list_evidence_fingerprints=evidence_fingerprints(paths.review),
        write=store.replace_all,
    ).build(ReviewSearchBuildRequest(withheld_record_keys=frozenset()))
    BuildProvenance.for_registry(registry).record_review(paths.root, REVIEW_SEARCH_STORE_ID)


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


#: Stands in for a value that does not exist until the fixture has run: the sha of
#: the commit it makes over :data:`corpus.FILE_PATH`. A sentinel object rather than
#: a string, so :func:`_resolved` substitutes it by identity and no caller-supplied
#: argument that merely *looks* like a placeholder is rewritten.
_FIX_COMMIT: Final = object()

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
    # Driven for the reason every tool here is: face (b) and face (c) are not
    # specific to the sanctioned tool, so a helper reaching the findings store
    # from *this* body would be invisible to the bytecode walk that inspects each
    # tool's own code object. Its own store is landed by `_land_review_evidence`
    # so these calls reach the body rather than the unavailable-store refusal.
    "review.search": (
        {"projectId": "demo"},
        {"projectId": "demo", "limit": 1},
        {"projectId": "demo", "repository": "acme/order-service", "pullRequest": 42},
        {"projectId": "demo", "q": "token"},
    ),
    "system.capabilities": ({},),
    # The write-intent tools (ADR-0032). Driven with arguments that reach the body
    # and draft a proposal successfully -- `call_tool` re-raises a refusal, so an
    # INV-8 or op-set refusal would crash the drive rather than exercise the body.
    # Neither touches the findings store: `proposeChange`'s caller-scoped
    # current-revision lookup reads the *canonical* store, and
    # `generateMigrationDraft` reads none. A new item id (not the fixture's) so the
    # concurrency check admits a first revision, and `authored-in-theurian` for
    # INV-8.
    "knowledge.proposeChange": (
        {
            "projectId": "demo",
            "itemId": "architecture.retry-budget",
            "title": "Retry budget",
            "kind": "architecture",
            "owner": "platform-team",
            "author": "engineer@example.com",
            "description": "Record the retry budget the API review settled on.",
            "body": "# Retry budget\n\nThree attempts, then fail loudly.\n",
            "contentType": "text/markdown",
            "evidence": {
                "agentId": "claude-code",
                "taskId": "task-7",
                "model": "claude-opus-5",
                "reasoning": "The review thread settled the retry budget at three attempts.",
            },
            "labels": ["authored-in-theurian"],
        },
    ),
    "knowledge.generateMigrationDraft": (
        {
            "projectId": "demo",
            "document": {
                "author": "engineer@example.com",
                "description": "Deprecate the auth policy.",
                "operations": [{"op": "deprecateItem", "itemId": ITEM_ID}],
            },
            "evidence": {
                "agentId": "claude-code",
                "taskId": "task-7",
                "model": "claude-opus-5",
                "reasoning": "The item is superseded by the new policy.",
            },
        },
    ),
    # The third write-intent tool (ADR-0033), driven to a *landed proposal* for the
    # same reason as the other two: `call_tool` re-raises, so any refusal -- an
    # unmet gate, an unverifiable commit, a record key the store does not answer
    # for -- would crash the drive instead of executing the body this audit exists
    # to watch. It reads the review *evidence* store, which is the second serving
    # database under `.theurian/state/`; that it touches the findings file not at
    # all is what the audit says, rather than this comment.
    "review.generateKnowledgeCandidate": (
        {
            "projectId": "demo",
            "repository": corpus.REPOSITORY,
            "recordKey": corpus.THREAD_SATISFYING,
            "fixCommit": _FIX_COMMIT,
            "itemId": "reliability.retry-lock-order",
            "title": "Acquire locks after reads in retry-eligible paths",
            "body": "Acquire locks after reads, never before, in retry-eligible paths.\n",
            "kind": "convention",
            "category": "reliability-rule",
            "owner": "platform-team",
            "author": "engineer@example.com",
            "description": "Generalise the resolved deadlock thread into a locking rule",
            "evidence": {
                "agentId": "claude-code",
                "taskId": "task-431",
                "model": "claude-opus-5",
                "reasoning": "The thread settled the lock ordering; this generalises it.",
            },
            "sourceAnchors": [
                {
                    "provider": "github",
                    "sourceUri": (
                        f"https://github.com/{corpus.REPOSITORY}/pull/"
                        f"{corpus.PULL_REQUEST_CI_PASSED}#discussion_r1"
                    ),
                    "repository": corpus.REPOSITORY,
                    "filePath": corpus.FILE_PATH,
                }
            ],
        },
    ),
}


def _verifying_commit(registry: ProjectRegistry) -> str:
    """A commit in the fixture repository that touched the stored thread's file.

    Asked of git at drive time rather than carried out of the fixture, because
    :data:`_DRIVE` is a module-level mapping and the sha does not exist until the
    fixture has run. Asserted non-empty: an empty answer would reach the wire as a
    ``fixCommit`` naming nothing, the tool would refuse, and ``call_tool`` would
    re-raise -- a crash that reads as the audit failing rather than as the fixture
    having stopped making the commit.
    """
    root = Path(registry.load()["demo"]["rootPath"])
    found = subprocess.run(  # noqa: S603
        ["git", "rev-list", "-1", "HEAD", "--", corpus.FILE_PATH],  # noqa: S607
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    assert found, (
        f"no commit in {root} touched {corpus.FILE_PATH}, so the candidate drive below "
        f"would be refused and this audit would never execute that tool's body"
    )
    return found


def _resolved(arguments: dict[str, Any], fix_commit: str) -> dict[str, Any]:
    """``arguments`` with :data:`_FIX_COMMIT` replaced by the sha git just named.

    A new mapping rather than an edit in place: :data:`_DRIVE` is also the
    population :func:`test_the_drive_covers_every_registered_tool` compares against
    the built server, and a drive that rewrote it would leave that equality reading
    arguments this function produced.
    """
    return {key: fix_commit if value is _FIX_COMMIT else value for key, value in arguments.items()}


async def _drive_every_tool(registry: ProjectRegistry) -> None:
    """Call every registered tool, each with every argument shape recorded above."""
    server = build_server(registry)
    fix_commit = _verifying_commit(registry)
    for name, calls in _DRIVE.items():
        for arguments in calls:
            await server.call_tool(name, _resolved(arguments, fix_commit))


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
