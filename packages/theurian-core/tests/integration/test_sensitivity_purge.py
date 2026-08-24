"""The ``changeSensitivity`` purge trigger (ADR-0025 part 2, #119).

ADR-0025 records this file as owed and says exactly what it owes: *a test that a
``changeSensitivity`` migration publishes a purged build in the same ``migrate
apply``, the way a withdrawal already does.* The behaviour it describes was the
opposite and deliberate -- ``_withdrawal_affected_item`` excluded the operation,
on the recorded ground that the built index's ``sensitivity`` column "is read by
no gate". #119 phases 3 and 4 made that false: a build writes no row above the
deployment's ceiling, and every retriever filters on the column. So a
*reclassified* row is the only above-ceiling row a served build can still hold,
and until this trigger existed the only thing withholding it was the canonical
re-check -- a gate over a *result*, with the document's text still sitting in
``chunks_fts``, ``chunks_trigram``, ``nodes_fts`` and ``nodes_trigram``, whose
collection statistics price every visible row against it (T-17a on this axis).

Four claims, and each needs its own arrangement because the axis is not
symmetric:

- **Upward, above the build's ceiling.** The window closes at the migration
  seam. Asserted over the *file* and over all four text indexes, not over a
  response, because that is where the T-17a mechanism lives; and over the
  response too, with ``indexed: true``, because a purge that pushed the query
  onto the unranked fallback would satisfy every absence here while proving
  nothing about the build.
- **Within the build's ceiling.** Nothing is purged and no file is written. This
  is the common case -- ``migrate apply`` replays the whole set whenever the
  state hash shifts (ADR-0016) -- and the reason ``changeSensitivity`` can join
  the candidate set unconditionally instead of the engine needing to know a
  ceiling it cannot see.
- **Downward, into the ceiling.** *Not* closed, and recorded rather than fixed
  (ADR-0025's residuals). A purge copies a build and deletes rows from the copy;
  a row the build never wrote is not there to add back. The item stays withheld
  until the next ``index build``, failing toward fewer results -- the same honest
  direction a draft approved after the build already fails in.
- **Equality.** A build that held the row and had it purged answers with the same
  forest as one built when the item was already above the ceiling. That is
  ADR-0008 decision 9's two-corpus property, inherited by this trigger rather
  than re-argued for it: the purge machinery is the same, and this file's job is
  to show the new trigger reaches it.

**What "gone from the index" means here, measured rather than assumed.** The
absences below are over *rows and FTS5 postings* -- ``chunks``, ``nodes``, and
each text index read through ``fts5vocab`` -- and deliberately not over the
file's bytes. A purge page-copies the published build
(``sqlite3.Connection.backup``) and then ``DELETE``s from the copy, and SQLite
does not zero a freed page unless ``PRAGMA secure_delete`` is on, so the
withdrawn text can linger in the copy's free list until a later write reuses the
page. Measured on 2026-08-24 against 6087be4, through the real CLI: after the
purge the marker string is absent from every row and every posting and still
present in the file's raw bytes. **It is not this trigger's property** -- the
same run with a ``deprecateItem`` in place of the ``changeSensitivity``, the
trigger ADR-0024 shipped, leaves exactly the same residue. Nor is it a channel
to a caller: no query reads a free page, and the document's plaintext is in
``.theurian/knowledge/`` beside the index either way. ADR-0024 records the
mechanism as a disk cost ("a purge does not compact; ``backup`` copies free
pages"); this note is where the *content* reading of it is written down, so the
next reader does not mistake these assertions for byte absence.

Real repositories, real index files and the real CLI under ``tmp_path``, with
``HOME`` and ``THEURIAN_DATA_DIR`` redirected -- the pattern
``test_forest_builder.py`` establishes, widened to two projects for the equality
the way ``test_forest_purge_equality.py`` does. Nothing here reaches the
developer's own machine, and nothing starts a daemon or registers a service.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import subprocess
from collections.abc import Sequence
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import pytest
from migration_fixtures import body_pin
from typer.testing import CliRunner

from theurian.application.authorization import (
    SERVING_PROFILE_FILENAME,
    StaticAuthorizationProvider,
    load_serving_profile,
)
from theurian.application.project_service import (
    ProjectPaths,
    ProjectRegistry,
    read_active_index_pointer,
)
from theurian.cli.main import app
from theurian.daemon.runner import build_server
from theurian.domain.enums import Sensitivity

pytestmark = pytest.mark.integration

runner = CliRunner()

PROJECT: Final = "demo"

#: The four text indexes ADR-0025 part 4 names, two per half of the derived
#: index. Every one is an external-content FTS5 table whose BM25 scores are
#: computed over every row it holds, so residue in any one of them is the T-17a
#: channel this trigger exists to close.
TEXT_INDEXES: Final = ("chunks_fts", "chunks_trigram", "nodes_fts", "nodes_trigram")

#: A query both visible documents answer, so an empty response cannot be mistaken
#: for a withholding.
QUERY: Final = "quarantine ledger"


def _ulid(tag: str) -> str:
    """A valid ULID literal, padded to 26 Crockford base32 characters.

    Crockford base32 excludes I, L, O and U; ``tests/unit/test_test_fixtures.py``
    guards *quoted* 26-character literals for exactly that, and an id assembled at
    runtime slips past that guard -- so the charset is asserted here rather than
    assumed. ``Z`` is the pad because it is in the alphabet and sorts last.
    """
    value = f"01K1{tag}".ljust(26, "Z")
    assert len(value) == 26, f"{value!r} is not a 26-character ULID"
    assert not set(value) & set("ILOU"), f"{value!r} is not Crockford base32"
    return value


@dataclass(frozen=True, slots=True)
class Doc:
    """One knowledge item, with **identifiers derived from ``slug``**.

    The equality below compares two builds row by row across two projects, and a
    ``nodes`` row carries ``source_revision_id``. A survivor present in both has
    to carry the same revision id in each, or the rows would differ for a reason
    that is not the purge -- so the ids come off the document rather than off its
    position in a corpus (the rule ``test_forest_purge_equality.py`` records).
    """

    slug: str
    code: str
    sensitivity: str = "internal"
    kind: str = "architecture"

    @property
    def item_id(self) -> str:
        return f"{self.kind}.{self.slug}"

    @property
    def migration_id(self) -> str:
        return _ulid(f"M{self.code}")

    @property
    def revision_id(self) -> str:
        return _ulid(f"R{self.code}")

    @property
    def heading(self) -> str:
        return self.slug.replace("-", " ").title()

    @property
    def marker(self) -> str:
        """A token in every sentence of this document and in no other.

        Delimited at both ends so no marker is a substring of another: a node
        built from the wrong children would otherwise pass the check that exists
        to catch exactly that.
        """
        return f"mk-{self.slug}-mk"


#: Three headed sections, each long enough to be its own chunk, so every document
#: splits into three. The count is load-bearing rather than incidental: a RAPTOR
#: tier is skipped below ``minChildrenPerSummary``, and a document that produced
#: no node at all could show nothing about the node half.
_SECTIONS: Final = (
    ("Tokens", "Every call carries a signed token issued by the gateway service."),
    ("Rotation", "Tokens rotate on restart and expire after one hour of idle time."),
    ("Revocation", "The quarantine ledger records every revoked token and its reason."),
)


def _body(doc: Doc) -> str:
    sections = "\n\n".join(
        f"## {heading}\n\n" + f"{doc.marker} {sentence} " * 4 for heading, sentence in _SECTIONS
    )
    return f"{doc.heading}\n\n{sections}\n"


def _migration(doc: Doc) -> str:
    """One document's ``createItem``/``upsertRevision`` pair.

    ``contentSha256`` is derived here from :func:`_body`, the same function
    :func:`_write_corpus` writes the file with, so the pin and the bytes cannot
    drift (ADR-0027 decision 1, ``migration_fixtures``). It is required on every
    ``upsertRevision`` since #342, and an absent one is a schema refusal at
    ``migrate apply`` -- which is what these fixtures met on the rebase.
    """
    return f"""apiVersion: theurian.dev/v1
id: {doc.migration_id}
createdAt: 2026-08-05T10:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: {doc.item_id}
    kind: {doc.kind}
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: {doc.item_id}
    revisionId: {doc.revision_id}
    contentFile: ../knowledge/{doc.kind}/{doc.slug}.md
    contentSha256: {body_pin(_body(doc))}
    metadata:
      title: {doc.heading}
      contentType: text/markdown
      kind: {doc.kind}
      namespace: backend
      status: approved
      sensitivity: {doc.sensitivity}
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/{doc.slug}.md
"""


def _reclassification(doc: Doc, level: Sensitivity) -> str:
    """A migration moving one item's disclosure class, and nothing else.

    Its id starts ``01K1W`` so it sorts after every create migration
    (``01K1M...``) and therefore applies last, whatever order the loader reads
    the directory in.
    """
    return f"""apiVersion: theurian.dev/v1
id: {_ulid(f"W{doc.code}")}
createdAt: 2026-08-05T11:00:00+09:00
author: engineer@example.com
operations:
  - op: changeSensitivity
    itemId: {doc.item_id}
    sensitivity: {level.value}
    reason: reclassified after the index was built
"""


def _write_corpus(root: Path, docs: Sequence[Doc]) -> None:
    for doc in docs:
        knowledge = root / ".theurian/knowledge" / doc.kind
        knowledge.mkdir(parents=True, exist_ok=True)
        (knowledge / f"{doc.slug}.md").write_text(_body(doc), encoding="utf-8")
        (root / f".theurian/migrations/{doc.migration_id}-{doc.slug}.yaml").write_text(
            _migration(doc), encoding="utf-8"
        )


def _write_reclassification(root: Path, doc: Doc, level: Sensitivity) -> None:
    (root / f".theurian/migrations/{_ulid(f'W{doc.code}')}-reclassify.yaml").write_text(
        _reclassification(doc, level), encoding="utf-8"
    )


# -- The project -------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Project:
    """One initialised, registered project in its own ``HOME`` and data directory.

    Two of these are compared by the equality test, and they must both register
    under the project id ``demo`` for their chunks and trees to share a scope key
    -- which they can only do without colliding because ``projects.json`` lives
    under ``THEURIAN_DATA_DIR`` and each has its own.
    """

    root: Path
    datadir: Path
    home: Path


def _cli(project: Project, *args: str) -> tuple[int, dict[str, Any]]:
    """Invoke the real CLI inside ``project`` with its environment redirected.

    ``HOME`` is redirected beside ``THEURIAN_DATA_DIR`` even though nothing this
    file runs reads it: the fixture shells out to `git`, and a test that reaches
    the developer's real home directory is a defect that surfaces somewhere else
    entirely. Both are set in the same call that changes directory, never in an
    earlier one.
    """
    monkey = pytest.MonkeyPatch()
    monkey.setenv("HOME", str(project.home))
    monkey.setenv("THEURIAN_DATA_DIR", str(project.datadir))
    monkey.chdir(project.root)
    try:
        result = runner.invoke(app, [*args, "--json"], catch_exceptions=False)
    finally:
        monkey.undo()
    stream = result.stdout if result.exit_code == 0 else (result.stderr or result.stdout)
    return result.exit_code, json.loads(stream) if stream.strip() else {}


def _must(project: Project, *args: str) -> dict[str, Any]:
    code, payload = _cli(project, *args)
    assert code == 0, f"{' '.join(args)}: {payload}"
    return payload


def _new_project(base: Path, name: str) -> Project:
    project = Project(root=base / name, datadir=base / f"{name}-data", home=base / f"{name}-home")
    for directory in (project.root, project.datadir, project.home):
        directory.mkdir(parents=True)
    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(args, cwd=project.root, check=True, capture_output=True)  # noqa: S603
    _must(project, "init")
    _must(project, "project", "register", "--project-id", PROJECT)
    return project


@pytest.fixture
def project(tmp_path: Path) -> Project:
    return _new_project(tmp_path, "demo")


def _declare_a_ceiling(project: Project, ceiling: Sensitivity) -> None:
    """Write the deployment serving profile the CLI and the server both read.

    Mode 0600 is not tidiness. ``load_serving_profile`` refuses a profile other
    local users can reach, so a test that skipped this would exercise the refusal
    rather than the ceiling -- and would say "the build failed" while looking like
    a withholding.
    """
    auth = project.datadir / "auth"
    auth.mkdir(parents=True, exist_ok=True)
    profile = auth / SERVING_PROFILE_FILENAME
    profile.write_text(f"{ceiling.value}\n", encoding="utf-8")
    profile.chmod(0o600)


# -- Reading a published build -----------------------------------------------


def _pointer(project: Project) -> dict[str, Any]:
    payload = read_active_index_pointer(ProjectPaths.of(project.root)).payload
    assert payload is not None, "the project must have a published index"
    return dict(payload)


def _published_index(project: Project) -> Path:
    return ProjectPaths.of(project.root).index_for(str(_pointer(project)["indexBuildId"]))


def _rows(path: Path, sql: str) -> list[dict[str, Any]]:
    """Every row of a query as plain dicts.

    ``closing`` rather than ``with sqlite3.connect(...)``: that context manager
    commits and does not close, and ``filterwarnings = error`` turns the leaked
    handle's ``ResourceWarning`` into a failure in whichever test is running.
    """
    with closing(sqlite3.connect(path)) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(sql)]


def _chunk_items(path: Path) -> set[str]:
    return {str(row["item_id"]) for row in _rows(path, "SELECT item_id FROM chunks")}


def _node_text(path: Path) -> str:
    return " ".join(str(row["text"]) for row in _rows(path, "SELECT text FROM nodes")).lower()


def _terms(path: Path, table: str) -> set[str]:
    """Every term an external-content FTS5 table currently indexes.

    ``fts5vocab`` reads the *index* rather than the content table, which is the
    point: a delete trigger that never fired leaves terms here with no row behind
    them, and querying ``chunks`` or ``nodes`` would not show it.
    """
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(f"CREATE VIRTUAL TABLE temp.v USING fts5vocab('main', '{table}', 'row')")
        return {str(row[0]) for row in connection.execute("SELECT term FROM temp.v")}


def _only_from(withheld: Doc, visible: Sequence[Doc], terms: set[str]) -> set[str]:
    """The indexed terms that could only have come from ``withheld``.

    Written as "in that document's text and in no other document's" rather than
    as a token list, so it means the same thing for a word index and for a trigram
    one: ``nodes_fts`` indexes words while ``chunks_trigram`` indexes
    three-character sequences, and an assertion phrased in either one's units
    would silently pass over the other.
    """
    elsewhere = " ".join(_body(doc) for doc in visible).lower()
    body = _body(withheld).lower()
    return {term for term in terms if term in body and term not in elsewhere}


@dataclass(frozen=True, slots=True)
class Forest:
    """One build's node graph, in a form two builds can be compared by value.

    ``index_build_id`` is dropped from every node row: it names the build, and two
    builds are two builds. Everything else -- ``node_id`` included, per ADR-0008
    decision 9's insistence that a content-addressed id is what determinism across
    builds amounts to -- is compared, along with the derivation edges and the node
    vectors, because the equality is over the whole derived layer.
    """

    nodes: dict[str, tuple[tuple[str, Any], ...]]
    edges: tuple[tuple[Any, ...], ...]
    embeddings: dict[str, tuple[int, bytes]]


def _forest(path: Path) -> Forest:
    raw = {str(row["node_id"]): row for row in _rows(path, "SELECT * FROM nodes")}
    return Forest(
        nodes={
            node_id: tuple(
                sorted((key, value) for key, value in row.items() if key != "index_build_id")
            )
            for node_id, row in raw.items()
        },
        edges=tuple(
            sorted(
                (str(e["node_id"]), e["source_chunk_id"], e["source_node_id"])
                for e in _rows(path, "SELECT * FROM node_derivation")
            )
        ),
        embeddings={
            str(row["node_id"]): (int(row["dimension"]), bytes(row["vector"]))
            for row in _rows(path, "SELECT * FROM node_embeddings")
        },
    )


# -- Searching the way the daemon does ---------------------------------------


def _search(project: Project, query: str = QUERY) -> dict[str, Any]:
    """One ``knowledge.search`` under the grant this project's own profile grants.

    The grant is resolved from the profile file through the same
    ``StaticAuthorizationProvider(load_serving_profile(...))`` the daemon uses and
    ``theurian index build`` read a moment earlier, so the build's flavor and the
    serving grant are one derivation and not two. A second spelling here is how a
    test ends up measuring ``serving-profile-mismatch`` while claiming to measure
    a withholding.
    """
    grant = StaticAuthorizationProvider(load_serving_profile(project.datadir)).deployment_grant()
    registry = ProjectRegistry.default(project.datadir)

    async def invoke() -> Any:
        return await build_server(registry, grant).call_tool(
            "knowledge.search", {"projectId": PROJECT, "query": query}
        )

    result = asyncio.run(invoke())
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        payload: dict[str, Any] = structured
        return payload
    loaded: dict[str, Any] = json.loads(result.content[0].text)
    return loaded


def _answered_from_the_index(answer: dict[str, Any]) -> None:
    """Assert this response came off the published build, not the canonical scan.

    Every absence in this file is read off a response, and an absence on the
    unranked fallback says nothing about what the *index* holds -- the fallback
    answers from canonical state, where the reclassified item is withheld by the
    canonical gate whatever the file contains. So this is a precondition on every
    such assertion, not decoration.
    """
    retrieval = answer["retrieval"]
    assert retrieval["indexed"] is True, (
        f"the response did not come from the published build, so an absence in it is not "
        f"evidence about the file: {retrieval}"
    )
    assert not retrieval["fallbackReason"], (
        f"the ranked path stood the build aside ({retrieval['fallbackReason']}), so this "
        f"measures the canonical scan rather than the purge"
    )


# -- The corpus --------------------------------------------------------------

#: Four documents of one kind in one scope. Four rather than three so the purge
#: has a Domain node to *rebuild* over three survivors rather than merely delete:
#: a tier is skipped below ``minChildrenPerSummary``, and a corpus that lost its
#: only Domain node either way could not tell a re-derivation from a deletion.
_VISIBLE: Final = (
    Doc("auth-policy", code="AA"),
    Doc("quota-policy", code="AB"),
    Doc("cache-policy", code="AC"),
)
_RECLASSIFIED: Final = Doc("payroll-bands", code="AD")
_ALL: Final = (*_VISIBLE, _RECLASSIFIED)

#: The same corpus with the fourth document authored above the ceiling from the
#: start -- the build that *never held* the row, for the equality.
_NEVER_HELD: Final = (*_VISIBLE, Doc("payroll-bands", code="AD", sensitivity="confidential"))


def _built_under_an_internal_ceiling(project: Project, docs: Sequence[Doc] = _ALL) -> Path:
    """Apply ``docs`` and publish a ``--raptor`` build under a declared ceiling.

    ``internal`` rather than the shipped default, because the shipped default
    serves every level and a build made under it holds every level -- against
    which no reclassification is ever outside the flavor, and nothing this file
    asserts could fail.
    """
    _declare_a_ceiling(project, Sensitivity.INTERNAL)
    _write_corpus(project.root, docs)
    _must(project, "migrate", "apply")
    _must(project, "index", "build", "--raptor")
    return _published_index(project)


# -- Upward: the window closes at the migration seam --------------------------


@pytest.mark.parametrize("table", TEXT_INDEXES)
def test_a_reclassification_above_the_ceiling_purges_the_published_index_without_a_separate_build(
    project: Project, table: str
) -> None:
    """ADR-0025 part 2's owed test, over both halves and all four text indexes.

    An item indexed while it was ``internal`` is reclassified to ``confidential``
    by an ordinary migration against a build made under an ``internal`` ceiling.
    One ``migrate apply``, **no ``index build``**, and the row must be gone from
    the build the pointer names -- because the property is that the purge leaves a
    published build a search can go on using, exactly as a withdrawal does
    (ADR-0024 decision 5).

    The assertion is over the *file* and not over a response, and that is the
    whole point of the axis: the canonical re-check has withheld this item from
    results since #119 phase 2, while its text stayed in four FTS5 tables whose
    ``N``, ``avgdl`` and per-term document frequencies price every visible row
    against it. A test that only read the response would have been green on the
    behaviour this trigger exists to change.

    Each parametrization establishes its own non-vacuity twice: the table must
    index a term unique to the reclassified document *before* the apply, and the
    survivors' rows must still be there *after* it. A purge that took the whole
    build with it would satisfy every absence here.

    Measured RED against the pre-#119-phase-5 engine -- ``changeSensitivity`` was
    excluded from ``_withdrawal_affected_item``, so the apply purged nothing and
    all four parametrizations failed on the item's chunk rows still being present.
    """
    before = _built_under_an_internal_ceiling(project)
    assert _RECLASSIFIED.item_id in _chunk_items(before), (
        "the fixture must index the document while it is still `internal`, or its absence "
        "afterwards says nothing about the reclassification"
    )
    assert _RECLASSIFIED.marker in _node_text(before), (
        "no summary node was derived from the document, so the node half cannot be shown to lose it"
    )
    discriminating = _only_from(_RECLASSIFIED, _VISIBLE, _terms(before, table))
    assert discriminating, (
        f"{table} indexed no term unique to the reclassified document, so its absence below "
        f"would prove nothing"
    )

    _write_reclassification(project.root, _RECLASSIFIED, Sensitivity.CONFIDENTIAL)
    applied = _must(project, "migrate", "apply")

    after = _published_index(project)
    assert applied["indexPurge"]["published"] is True, (
        f"the apply reported no purge ({applied['indexPurge']}), so the still-published "
        f"build is the one that holds the reclassified row"
    )
    assert after != before, "a purge is a build: it must publish a new file (ADR-0024)"
    assert _RECLASSIFIED.item_id not in _chunk_items(after), (
        "a document reclassified above the ceiling its build ran under kept its chunk rows"
    )
    assert _RECLASSIFIED.marker not in _node_text(after), (
        "a summary node built from the reclassified document's text survived the purge"
    )
    assert discriminating.isdisjoint(_terms(after, table)), (
        f"{table} still holds {sorted(discriminating & _terms(after, table))}, terms that "
        f"appear in no document this build may now serve -- the withheld text is in the "
        f"file's collection statistics even though no row of it can be returned"
    )
    assert {doc.item_id for doc in _VISIBLE} <= _chunk_items(after), (
        "the purge took the visible documents with it, which satisfies every absence above "
        "and destroys the capability"
    )
    assert _terms(after, table), f"{table} is empty after a purge that left rows standing"


def test_the_purged_build_still_answers_the_query_from_its_own_index(project: Project) -> None:
    """The response half: withheld **with** ``indexed: true``, not by falling back.

    Split from the file assertions above so it runs once rather than four times,
    and because it can fail where they cannot. A purge republishes the pointer,
    and everything the serve path checks before it will search a build is read off
    that pointer: the project id, the state hash, ``indexesUnapproved`` and --
    since #119 phase 3 -- ``indexedSensitivities``. A purge that dropped or
    widened any of them would leave a correct *file* behind a
    ``serving-profile-mismatch`` (or ``index-project-mismatch``) fallback, and the
    caller would get its silence from the canonical scan instead. That is not the
    same product: the ranked path is gone, and nothing says so beyond a
    ``fallbackReason`` the caller has to read.

    So the withholding is asserted together with its precondition, and the
    surviving documents are asserted *present* in the same answer -- an empty
    response would satisfy the absence and mean the build was unusable.
    """
    _built_under_an_internal_ceiling(project)
    before = _search(project)
    _answered_from_the_index(before)
    assert _RECLASSIFIED.item_id in {hit["itemId"] for hit in before["results"]}, (
        "precondition: the document must be served while it is still `internal`, or its "
        "absence afterwards says nothing"
    )

    _write_reclassification(project.root, _RECLASSIFIED, Sensitivity.CONFIDENTIAL)
    _must(project, "migrate", "apply")

    served = _search(project)

    _answered_from_the_index(served)
    assert served["count"] > 0, (
        "the purge left a build that answers nothing, so the absence below is the absence "
        "of an answer rather than of a document"
    )
    assert _RECLASSIFIED.item_id not in {hit["itemId"] for hit in served["results"]}, (
        f"the reclassified document was served by a deployment whose ceiling is `internal`: "
        f"{served['results']}"
    )
    assert _RECLASSIFIED.marker not in json.dumps(served), (
        "no fragment of the reclassified document may appear anywhere in the response -- "
        "an excerpt or a raptorPath title carries its text as surely as a result does"
    )


# -- Within the ceiling: nothing is purged and nothing is written -------------


def test_a_reclassification_within_the_ceiling_leaves_the_published_build_untouched(
    project: Project,
) -> None:
    """The other direction of the flavor test, and the reason the trigger is cheap.

    ``migrate apply`` replays the whole migration set whenever the state hash
    shifts (ADR-0016), so a project with any past reclassification would copy its
    whole index on every apply if the trigger fired on the *operation* rather than
    on the item's final class against the build's recorded flavor. Here the build
    was made under the shipped default, whose pointer records every level, so
    moving an item ``internal -> public`` leaves a row that build is still allowed
    to hold.

    Asserted on the file's bytes and on the pointer, not on a count: "nothing was
    purged" and "a byte-identical copy was published" are different outcomes with
    the same row count, and only the first is the one being claimed.
    """
    _write_corpus(project.root, _ALL)
    _must(project, "migrate", "apply")
    _must(project, "index", "build", "--raptor")
    before_pointer = _pointer(project)
    before_bytes = _published_index(project).read_bytes()

    _write_reclassification(project.root, _RECLASSIFIED, Sensitivity.PUBLIC)
    applied = _must(project, "migrate", "apply")

    assert applied["indexPurge"]["published"] is False, (
        f"a reclassification within the build's own ceiling published a purge "
        f"({applied['indexPurge']}) -- every relabelling would copy the whole index"
    )
    assert _pointer(project) == before_pointer, "the published pointer moved"
    assert _published_index(project).read_bytes() == before_bytes, (
        "the published build's bytes changed, so something rewrote a file this apply had "
        "no reason to touch"
    )
    assert _RECLASSIFIED.item_id in _chunk_items(_published_index(project)), (
        "the reclassified item's rows left a build that is still allowed to hold them"
    )


# -- Downward: the residual, recorded rather than closed ----------------------


def test_a_downward_reclassification_waits_for_the_next_build(project: Project) -> None:
    """ADR-0025's recorded residual, pinned so it is a decision and not a surprise.

    The asymmetry is structural, not an oversight. A purge copies the published
    build and deletes rows from the copy (``index_purge.purge_into``); an item
    that was ``restricted`` when the build ran has no row in that file, and no
    amount of deleting produces one. So a reclassification *into* the ceiling
    leaves the document unserved until the next ``index build`` re-derives from
    canonical state.

    That is the honest direction to fail in -- fewer results, never more -- and it
    is the same one a draft approved after the build already fails in. It is
    asserted in both halves: still withheld immediately after the apply, and
    served after the rebuild, so a future change that closes the window turns the
    first assertion RED rather than passing unnoticed, and one that breaks the
    rebuild turns the second RED.
    """
    below = Doc(_RECLASSIFIED.slug, code=_RECLASSIFIED.code, sensitivity="restricted")
    _built_under_an_internal_ceiling(project, (*_VISIBLE, below))
    assert below.item_id not in _chunk_items(_published_index(project)), (
        "precondition: a `restricted` item must be excluded from a build made at `internal`, "
        "or there is no residual here to record"
    )

    _write_reclassification(project.root, below, Sensitivity.INTERNAL)
    _must(project, "migrate", "apply")

    waiting = _search(project)
    _answered_from_the_index(waiting)
    assert below.item_id not in {hit["itemId"] for hit in waiting["results"]}, (
        "the apply served a document the published build never held -- which would mean a "
        "purge invented a row, so read this as a defect in the purge, not a fix"
    )

    _must(project, "index", "build", "--raptor")

    rebuilt = _search(project)
    _answered_from_the_index(rebuilt)
    assert below.item_id in {hit["itemId"] for hit in rebuilt["results"]}, (
        f"a rebuild is the documented remedy for the downward residual and it did not serve "
        f"the item: {rebuilt['results']}"
    )


# -- Equality: the trigger inherits ADR-0008 decision 9's property -------------


def test_the_purged_forest_equals_one_built_above_the_ceiling(tmp_path: Path) -> None:
    """ADR-0008 decision 9's two-corpus equality, reached by the new trigger.

    ``test_forest_purge_equality.py`` holds this for the *withdrawal* trigger: a
    build that held the withdrawn rows and had them purged produces a forest
    identical to one built over a corpus that never held them, because the purge
    re-derives each affected scope's trees rather than deleting them
    (``make_forest_recompute``). This asserts the reclassification trigger reaches
    that same machinery, which is the whole of what "inherits" can mean here --
    the recompute is not re-implemented for this axis, and a test that only
    checked the leaf half would not have noticed if it were bypassed.

    The two corpora differ in exactly one authored byte: the fourth document's
    ``sensitivity`` in its revision metadata. The probe indexes it at ``internal``
    and then reclassifies it to ``confidential``; the control authors it
    ``confidential`` and so never writes a row for it under the same ceiling. Ids
    are derived from each document's ``code``, so a survivor carries the same
    ``source_revision_id`` in both and the comparison is about the purge rather
    than about two unrelated corpora.

    The pre-purge forest is asserted *different* from the control's, so the
    equality cannot be satisfied by two builds that agree for an unrelated reason.
    """
    probe = _new_project(tmp_path, "held-it")
    stale = _forest(_built_under_an_internal_ceiling(probe))

    _write_reclassification(probe.root, _RECLASSIFIED, Sensitivity.CONFIDENTIAL)
    _must(probe, "migrate", "apply")
    purged = _forest(_published_index(probe))

    control = _new_project(tmp_path, "never-held")
    fresh = _forest(_built_under_an_internal_ceiling(control, _NEVER_HELD))

    assert fresh.nodes, "the never-held corpus derived no forest, so the equality is vacuous"
    assert stale.nodes != fresh.nodes, (
        "the pre-purge build already equals the never-held one, so the equality below holds "
        "for any implementation"
    )
    assert purged.nodes == fresh.nodes, (
        "a purged forest must equal one built when the item was already above the ceiling"
    )
    assert purged.edges == fresh.edges, "the derivation edges must match too"
    assert purged.embeddings == fresh.embeddings, "the node vectors must match too"
