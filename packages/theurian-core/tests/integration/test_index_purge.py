"""A purged build answers as if the withdrawn rows had never been indexed (ADR-0024, T-17a).

**The property is not "the withdrawn rows are gone from the result".** The
visibility gate already does that, and T-17a is the demonstration that it is not
enough: FTS5's `bm25` scores every visible row against collection statistics
computed over *every* row in the file, so a document retired since the last build
reweights the ones the caller may see. What a purge has to hold is

    an index that held the withdrawn rows and had them purged answers
    **identically** to an index that never held them.

which is why every test below that asserts equality also asserts that a `stale`
control is **different**. Without the control the comparison is satisfied by any
two indexes that happen to agree, and this file would pass with the purge
deleting nothing.

The withdrawn documents are long relative to the corpus mean, deliberately.
Review round five of Milestone 5 established that `avgdl` — BM25's length
normalisation — is the channel a withheld document moves even when it shares no
term with the query, and a withheld document of average length moves it least.
A fixture built from same-length documents exercises `nHit` and quietly stops
exercising `avgdl`.
"""

from __future__ import annotations

import hashlib
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest

from theurian.domain.chunking import Chunk, IndexableChunk
from theurian.domain.errors import TheurianError
from theurian.infrastructure.sqlite import index_purge
from theurian.infrastructure.sqlite.index_purge import IndexPurgeError, _verify
from theurian.infrastructure.sqlite.index_store import SqliteIndexStore

pytestmark = pytest.mark.integration

PROJECT = "demo"

#: Ordinary documents, and the long ones that get withdrawn. Ten times the length
#: so `avgdl` moves measurably; see the module docstring.
ORDINARY_BODY = (
    "Retention and isolation are decided per namespace. Authentication tokens "
    "rotate on restart. The quarantine ledger records every attempt. "
)
LONG_BODY = ORDINARY_BODY * 10

ORDINARY = 40
WITHDRAWN = 6

QUERIES = ("retention isolation", "authentication token", "quarantine ledger")


def _indexable(chunk_id: str, text: str, *, revision: str, derived: bool = False) -> IndexableChunk:
    return IndexableChunk(
        chunk=Chunk(chunk_id=chunk_id, ordinal=0, text=text, heading=""),
        project_id=PROJECT,
        item_id=f"architecture.{revision}",
        revision_id=revision,
        status="approved",
        sensitivity="internal",
        trust_level="reviewed",
    )


def _populate(store: SqliteIndexStore, *, include_withdrawn: bool) -> list[str]:
    """Build a corpus. `include_withdrawn` is the only difference between the two."""
    chunks = [
        _indexable(f"keep-{n:03d}#0", f"{ORDINARY_BODY} paragraph {n}.", revision=f"keep-{n:03d}")
        for n in range(ORDINARY)
    ]
    withdrawn: list[str] = []
    if include_withdrawn:
        for n in range(WITHDRAWN):
            revision = f"gone-{n:03d}"
            withdrawn.append(revision)
            chunks.append(
                _indexable(f"{revision}#0", f"{LONG_BODY} paragraph {n}.", revision=revision)
            )
    # Interleaved by chunk id so the two corpora insert the shared rows in the
    # same order: `chunks` has an implicit rowid, and FTS5 keys on it, so a
    # different insertion order would make the two indexes differ for a reason
    # that has nothing to do with the purge.
    store.add_chunks(sorted(chunks, key=lambda c: c.chunk.chunk_id))
    return withdrawn


def _build(
    path: Path, *, include_withdrawn: bool, build_id: str
) -> tuple[SqliteIndexStore, list[str]]:
    store = SqliteIndexStore(path)
    store.create(index_build_id=build_id, state_hash="state-abc")
    withdrawn = _populate(store, include_withdrawn=include_withdrawn)
    return store, withdrawn


def _ranking(store: SqliteIndexStore, query: str) -> list[tuple[str, float]]:
    """The whole ranking, ids and scores to ten decimals."""
    return [
        (row.chunk_id, round(row.score, 10))
        for row in store.search_lexical(
            query, project_id=PROJECT, limit=100_000, include_unapproved=False
        ).rows
    ]


def _substring_ranking(store: SqliteIndexStore, query: str) -> list[tuple[str, float]]:
    return [
        (row.chunk_id, round(row.score, 10))
        for row in store.search_substring(
            query, project_id=PROJECT, limit=100_000, include_unapproved=False
        ).rows
    ]


@pytest.fixture
def corpora(tmp_path: Path) -> tuple[SqliteIndexStore, SqliteIndexStore, list[str]]:
    """`stale` (holds the withdrawn rows) and `fresh` (never did)."""
    stale, withdrawn = _build(
        tmp_path / "theurian-index-stale.sqlite", include_withdrawn=True, build_id="01K1STALE"
    )
    fresh, _ = _build(
        tmp_path / "theurian-index-fresh.sqlite", include_withdrawn=False, build_id="01K1FRESH"
    )
    return stale, fresh, withdrawn


@pytest.mark.parametrize("query", QUERIES)
def test_a_purged_build_answers_as_if_the_rows_were_never_indexed(
    tmp_path: Path, corpora: tuple[SqliteIndexStore, SqliteIndexStore, list[str]], query: str
) -> None:
    """One query, three corpora. The control is what makes the equality mean anything."""
    stale, fresh, withdrawn = corpora
    purged_path = tmp_path / "theurian-index-purged.sqlite"
    stale.derive_purged(
        purged_path,
        revision_ids=withdrawn,
        index_build_id="01K1PURGED",
        state_hash="state-abc",
    )
    purged = SqliteIndexStore(purged_path)

    visible = {chunk_id for chunk_id, _ in _ranking(fresh, query)}
    stale_gated = [row for row in _ranking(stale, query) if row[0] in visible]

    assert stale_gated != _ranking(fresh, query), (
        "the control must differ, or this fixture cannot demonstrate anything: the withdrawn "
        "rows are supposed to reweight the visible ones through BM25's collection statistics, "
        "and if gating them out of a stale ranking already reproduces the fresh one there is "
        "no channel here to close"
    )
    assert _ranking(purged, query) == _ranking(fresh, query), (
        "a purged build must answer identically to one that never held the rows -- chunk ids "
        "and BM25 scores to ten decimals, not merely the same set of documents"
    )


@pytest.mark.parametrize("query", ["retention", "認証"])
def test_the_substring_retriever_holds_the_same_equality(
    tmp_path: Path, corpora: tuple[SqliteIndexStore, SqliteIndexStore, list[str]], query: str
) -> None:
    """Both branches of `search_substring`, because they read different statistics.

    The trigram lookup scores with `bm25` exactly as the word index does, so it
    carries the same channel. The scan below the trigram floor ranks by
    occurrences counted inside each row and reads no collection statistic at all,
    so it is expected to agree *before* the purge as well -- asserted here so
    that a future change which gives the scan a corpus-wide statistic fails
    loudly rather than silently opening a third channel.
    """
    stale, fresh, withdrawn = corpora
    purged_path = tmp_path / "theurian-index-purged.sqlite"
    stale.derive_purged(
        purged_path, revision_ids=withdrawn, index_build_id="01K1PURGED", state_hash="state-abc"
    )

    assert _substring_ranking(SqliteIndexStore(purged_path), query) == _substring_ranking(
        fresh, query
    )


def test_a_purge_leaves_the_published_build_untouched(
    tmp_path: Path, corpora: tuple[SqliteIndexStore, SqliteIndexStore, list[str]]
) -> None:
    """ADR-0024 point 1. The file a search is reading is never written to.

    Byte-for-byte, because "the rows are still there" would also pass for a build
    that had been rewritten in place and happened to end up equivalent.
    """
    stale, _, withdrawn = corpora
    before = stale.path.read_bytes()

    stale.derive_purged(
        tmp_path / "theurian-index-purged.sqlite",
        revision_ids=withdrawn,
        index_build_id="01K1PURGED",
        state_hash="state-abc",
    )

    assert stale.path.read_bytes() == before, (
        "a purge must produce a new build and leave the published one alone; this file changed"
    )


def test_a_purged_build_holds_no_embedding_of_a_withdrawn_chunk(
    tmp_path: Path, corpora: tuple[SqliteIndexStore, SqliteIndexStore, list[str]]
) -> None:
    """`ON DELETE CASCADE` is enforced per *connection*, and defaults to off.

    A purge that opened its own connection without `CONNECTION_PRAGMAS` would
    delete the chunk and keep the vector. That fails in the safe direction --
    `search_dense` joins `embeddings` to `chunks`, so an orphan returns nothing
    rather than returning a withdrawn row -- which is exactly why review does not
    catch it and this test has to.
    """
    stale, _, withdrawn = corpora
    stale.add_embeddings([(f"{revision}#0", [0.1, 0.2, 0.3]) for revision in withdrawn])
    stale.add_embeddings([(f"keep-{n:03d}#0", [0.3, 0.2, 0.1]) for n in range(ORDINARY)])
    purged_path = tmp_path / "theurian-index-purged.sqlite"

    stale.derive_purged(
        purged_path, revision_ids=withdrawn, index_build_id="01K1PURGED", state_hash="state-abc"
    )

    with closing(sqlite3.connect(purged_path)) as connection:
        orphans = connection.execute(
            "SELECT count(*) FROM embeddings WHERE chunk_id NOT IN (SELECT chunk_id FROM chunks)"
        ).fetchone()[0]
        surviving = connection.execute("SELECT count(*) FROM embeddings").fetchone()[0]

    assert orphans == 0, "an embedding outlived its chunk: the delete ran without foreign keys on"
    assert surviving == ORDINARY, "the surviving chunks must keep their vectors"


def test_a_purged_build_names_itself_in_its_own_metadata(
    tmp_path: Path, corpora: tuple[SqliteIndexStore, SqliteIndexStore, list[str]]
) -> None:
    """`Connection.backup` copies pages, so the copy inherits the parent's identity.

    Nothing in `src/` reads `index_metadata.index_build_id` back today, which is
    what makes this cheap to get wrong and expensive to find: the first thing to
    read it would meet a file whose own record of itself disagrees with the
    pointer that names it (ADR-0024 decision 2).
    """
    stale, _, withdrawn = corpora
    purged_path = tmp_path / "theurian-index-purged.sqlite"

    stale.derive_purged(
        purged_path, revision_ids=withdrawn, index_build_id="01K1PURGED", state_hash="state-xyz"
    )

    metadata = SqliteIndexStore(purged_path).metadata()
    assert metadata["index_build_id"] == "01K1PURGED", "the copy still names the build it came from"
    assert metadata["state_hash"] == "state-xyz"
    assert stale.metadata()["index_build_id"] == "01K1STALE", "the source must not be restamped"


def test_a_purge_into_an_existing_path_is_refused(
    tmp_path: Path, corpora: tuple[SqliteIndexStore, SqliteIndexStore, list[str]]
) -> None:
    """The failure `IndexBuilder` already learned: writing into someone else's build.

    `create` refuses an existing file, and the cleanup that followed a refusal
    once deleted the very file it had been refused permission to touch -- which
    is the file `active-index.json` names. A purge writes a *new* build, so the
    same refusal applies, and the existing file must survive it.
    """
    stale, _, withdrawn = corpora
    occupied = tmp_path / "theurian-index-occupied.sqlite"
    occupied.write_bytes(b"not an index, and not to be deleted")

    with pytest.raises(IndexPurgeError, match="already exists"):
        stale.derive_purged(
            occupied,
            revision_ids=withdrawn,
            index_build_id="01K1PURGED",
            state_hash="state-abc",
        )

    assert occupied.read_bytes() == b"not an index, and not to be deleted"


def test_purging_nothing_is_a_faithful_copy(
    tmp_path: Path, corpora: tuple[SqliteIndexStore, SqliteIndexStore, list[str]]
) -> None:
    """The identity case, which a purge that silently dropped rows would fail.

    Also the case a caller reaches by purging a revision that is not in the index
    -- a withdrawal of knowledge that was never indexed, which is ordinary.
    """
    stale, _, _ = corpora
    purged_path = tmp_path / "theurian-index-purged.sqlite"

    removed = stale.derive_purged(
        purged_path, revision_ids=[], index_build_id="01K1PURGED", state_hash="state-abc"
    )

    assert removed == 0
    assert SqliteIndexStore(purged_path).chunk_count() == stale.chunk_count()
    assert _ranking(SqliteIndexStore(purged_path), QUERIES[0]) == _ranking(stale, QUERIES[0])


# -- Node rows (ADR-0008 decision 5's amendment, ADR-0024 decision 8) --------
#
# Nothing writes a node row yet: RAPTOR (ADR-0008) is an empty package, and
# `SummarizationProvider` a port with no adapter. The rows below are inserted
# with raw SQL for exactly that reason, and the seam is deliberate -- the
# *traversal* is what is under test and it runs through the interface. Writing
# the purge's transitive path after RAPTOR lands would mean designing it twice,
# and the second time under pressure from a feature that already ships.
#
# **v4, not v3.** Until ADR-0008's amendment these rows lived in `chunks` with
# `derived = 1`, and their provenance in `chunk_derivation`. The amendment moves
# both into their own tables -- `nodes` and `node_derivation` -- because a
# summary's text repeats its children's terms and a derived row sharing
# `chunks_fts` would move that table's BM25 collection statistics under every
# ordinary leaf query. The five tests this section held at v3 (ADR-0024's
# Compliance section: direct, a three-level chain, mixed provenance, an
# unprovenanced node and its children, and a control) migrate here rather than
# being deleted -- what they hold is the *rule*, and the rule is unchanged:
# withdrawal is transitive over derived content, and an unresolvable derivation
# edge means delete, not keep. A sixth, `test_a_derived_row_that_cannot_say_
# where_it_came_from_is_deleted` at v3, migrates alongside them: it is a distinct
# case from the fixed-point one below (zero children reachable through it, not
# one node reachable *from* an unprovenanced node), and dropping `chunk_
# derivation` would otherwise leave it stranded against a table that no longer
# exists.


def _add_node(  # noqa: PLR0913 - one keyword per column a migrated test needs to vary
    store: SqliteIndexStore,
    node_id: str,
    *,
    sources: list[str] | None = None,
    node_sources: list[str] | None = None,
    node_type: str = "document",
    level: int = 1,
    tree_id: str = "tree-abc",
) -> None:
    """A node row and its provenance, as RAPTOR would write them (decision 5's amendment).

    `sources` names chunk ids this node derives from; `node_sources` names other
    node ids -- a Domain node summarises Document-tree nodes, a Document node
    summarises chunks (decision 5's amendment to ADR-0008). `node_derivation`'s
    `source_chunk_id`/`source_node_id` are nullable and mutually exclusive per
    row, so a mixed-provenance edge set writes one row per source rather than one
    row with both populated -- the row shape decision 5 leaves to the schema, not
    a choice this test module's helper needs to defend beyond internal
    consistency with itself.
    """
    with closing(sqlite3.connect(store.path)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        with connection:
            connection.execute(
                "INSERT INTO nodes (node_id, tree_id, level, node_type, text, content_hash, "
                "summary_model, summary_model_revision, summary_prompt_hash, embedding_model, "
                "embedding_model_revision, embedding_dimension, source_revision_id, "
                "index_build_id, project_id, sensitivity, status) "
                "VALUES (?, ?, ?, ?, ?, ?, '', '', '', '', '', 0, '', 'test-build', ?, "
                "'internal', 'approved')",
                (
                    node_id,
                    tree_id,
                    level,
                    node_type,
                    f"A summary of {(sources or []) + (node_sources or [])}.",
                    hashlib.sha256(node_id.encode()).hexdigest(),
                    PROJECT,
                ),
            )
            connection.executemany(
                "INSERT INTO node_derivation (node_id, source_chunk_id, source_node_id) "
                "VALUES (?, ?, NULL)",
                [(node_id, source) for source in sources or []],
            )
            connection.executemany(
                "INSERT INTO node_derivation (node_id, source_chunk_id, source_node_id) "
                "VALUES (?, NULL, ?)",
                [(node_id, source) for source in node_sources or []],
            )


def _surviving(path: Path) -> set[str]:
    with closing(sqlite3.connect(path)) as connection:
        return {str(row[0]) for row in connection.execute("SELECT chunk_id FROM chunks")}


def _surviving_nodes(path: Path) -> set[str]:
    with closing(sqlite3.connect(path)) as connection:
        return {str(row[0]) for row in connection.execute("SELECT node_id FROM nodes")}


def _node_count(path: Path) -> int:
    with closing(sqlite3.connect(path)) as connection:
        return int(connection.execute("SELECT count(*) FROM nodes").fetchone()[0])


def test_a_node_derived_from_a_withdrawn_chunk_goes_with_it(
    tmp_path: Path, corpora: tuple[SqliteIndexStore, SqliteIndexStore, list[str]]
) -> None:
    """The case the whole decision exists for.

    Held at v3 by `test_a_row_derived_from_a_withdrawn_chunk_goes_with_it`, over
    a `chunks.derived = 1` row. A purge can delete a chunk. It cannot delete a
    sentence out of a summary built from that chunk, so a summary that survives
    its source is a withdrawal that withdrew nothing -- the content is still
    retrievable, under a different id, and no gate will catch it because the
    summary is not the withheld row.
    """
    stale, _, withdrawn = corpora
    _add_node(stale, "summary-of-gone#0", sources=[f"{withdrawn[0]}#0"])
    _add_node(stale, "summary-of-kept#0", sources=["keep-000#0"])
    purged_path = tmp_path / "theurian-index-purged.sqlite"

    stale.derive_purged(
        purged_path, revision_ids=withdrawn, index_build_id="01K1PURGED", state_hash="state-abc"
    )

    surviving = _surviving_nodes(purged_path)
    assert "summary-of-gone#0" not in surviving, (
        "a summary built from a withdrawn chunk still contains it; deleting the chunk and "
        "keeping the summary withdraws nothing"
    )
    assert "summary-of-kept#0" in surviving, (
        "a summary built only from surviving chunks must survive, or a purge silently costs "
        "recall every time anything is withdrawn"
    )


def test_withdrawal_is_transitive_through_a_document_and_a_domain_node(
    tmp_path: Path, corpora: tuple[SqliteIndexStore, SqliteIndexStore, list[str]]
) -> None:
    """A Domain node summarising a Document node. RAPTOR builds trees, so this is the normal case.

    Held at v3 by `test_withdrawal_is_transitive_through_a_chain_of_derivations`,
    over a chain of three plain `chunks.derived = 1` rows. v4 gives the chain its
    real shape: a Document-tree node built from the withdrawn chunk, and a
    Domain-tree node built from that node (ADR-0008 decision 2's three levels).
    One level of traversal would pass the direct test above and leave the
    Domain node standing, which is the same disclosure one indirection further
    out.
    """
    stale, _, withdrawn = corpora
    _add_node(
        stale, "document-node#0", sources=[f"{withdrawn[0]}#0"], node_type="document", level=1
    )
    _add_node(stale, "domain-node#0", node_sources=["document-node#0"], node_type="domain", level=2)
    purged_path = tmp_path / "theurian-index-purged.sqlite"

    stale.derive_purged(
        purged_path, revision_ids=withdrawn, index_build_id="01K1PURGED", state_hash="state-abc"
    )

    surviving = _surviving_nodes(purged_path)
    assert not {"document-node#0", "domain-node#0"} & surviving, (
        f"every node reachable from a withdrawn chunk must go; these survived: "
        f"{sorted({'document-node#0', 'domain-node#0'} & surviving)}"
    )


def test_a_node_derived_from_both_a_withdrawn_and_a_surviving_chunk_goes(
    tmp_path: Path, corpora: tuple[SqliteIndexStore, SqliteIndexStore, list[str]]
) -> None:
    """Mixed provenance resolves against the withdrawal, not for it.

    Held at v3 by the same-named test over `chunk_derivation`; the case keeps its
    shape at v4. The node holds content from a chunk the caller may no longer
    read. That it also holds permitted content does not make it publishable -- a
    summary cannot be partially withdrawn, which is why decision 8 says delete
    *or recompute* and this half implements the first.
    """
    stale, _, withdrawn = corpora
    _add_node(stale, "mixed#0", sources=[f"{withdrawn[0]}#0", "keep-001#0"])
    purged_path = tmp_path / "theurian-index-purged.sqlite"

    stale.derive_purged(
        purged_path, revision_ids=withdrawn, index_build_id="01K1PURGED", state_hash="state-abc"
    )

    assert "mixed#0" not in _surviving_nodes(purged_path)


def test_a_node_that_cannot_say_where_it_came_from_is_deleted(
    tmp_path: Path, corpora: tuple[SqliteIndexStore, SqliteIndexStore, list[str]]
) -> None:
    """Decision 8's last clause, and the one a traversal alone does not reach.

    Held at v3 by `test_a_derived_row_that_cannot_say_where_it_came_from_is_
    deleted`, over a `chunks.derived = 1` row with no `chunk_derivation` edge. A
    node with no edges is reachable from nothing, so the recursive walk cannot
    see it. It is deleted rather than kept because a row that cannot say what it
    was built from cannot be shown to hold nothing withdrawn -- and the state
    arises from a partial build or a schema migration, which is to say from the
    situations where the index is least trustworthy.
    """
    stale, _, withdrawn = corpora
    _add_node(stale, "unprovenanced#0", sources=[])
    purged_path = tmp_path / "theurian-index-purged.sqlite"

    stale.derive_purged(
        purged_path, revision_ids=withdrawn, index_build_id="01K1PURGED", state_hash="state-abc"
    )

    assert "unprovenanced#0" not in _surviving_nodes(purged_path)


def test_an_ordinary_chunk_is_never_treated_as_an_unprovenanced_node(
    tmp_path: Path, corpora: tuple[SqliteIndexStore, SqliteIndexStore, list[str]]
) -> None:
    """The control for the tests above.

    Held at v3 by `test_an_ordinary_row_is_never_treated_as_derived`. No node is
    written here at all, so `nodes` must stay empty across the purge -- a rule
    that swept ordinary chunks into the unprovenanced-node seed regardless of
    which table it read would empty the index on the first purge, and every
    other test in this section would still pass, because they assert what is
    gone, not what is left.
    """
    stale, _, withdrawn = corpora
    purged_path = tmp_path / "theurian-index-purged.sqlite"

    removed = stale.derive_purged(
        purged_path, revision_ids=withdrawn, index_build_id="01K1PURGED", state_hash="state-abc"
    )

    assert removed == len(withdrawn), "only the withdrawn chunks should have gone"
    assert SqliteIndexStore(purged_path).chunk_count() == ORDINARY
    assert _node_count(purged_path) == 0, "no node was written; the purge must not conjure one"


# -- Guards that a correct purge never reaches -------------------------------
#
# Mutation found these. `_verify` and the choice of copy primitive are both
# invisible while everything else works: skipping the post-condition check left
# all fifteen tests above green, and swapping `Connection.backup` for
# `shutil.copyfile` left them green too, because nothing above ever puts the
# source in the state where the two differ. A guard no test can reach is a guard
# that will be deleted by whoever next tidies this file.


def test_a_purge_sees_content_that_is_committed_but_not_yet_checkpointed(
    tmp_path: Path, corpora: tuple[SqliteIndexStore, SqliteIndexStore, list[str]]
) -> None:
    """Why the copy is `Connection.backup` and not `shutil.copyfile` (ADR-0024).

    In WAL mode the `-wal` sidecar is a separate file. A byte copy of the main
    database alone silently drops everything committed since the last
    checkpoint -- measured at 1,055 rows against a writer that had committed 955,
    and, where the uncheckpointed pages carry the schema, a database with no
    table at all.

    Every other test here closes its connections before purging, so the WAL is
    checkpointed on last close and the two primitives agree. This one holds a
    writer open, which is the state a long-lived process leaves the file in
    between checkpoints -- and the state a purge racing anything else would meet.
    """
    stale, _, withdrawn = corpora
    writer = sqlite3.connect(stale.path, isolation_level=None)
    try:
        writer.execute("PRAGMA journal_mode = WAL")
        writer.execute("PRAGMA wal_autocheckpoint = 0")
        writer.execute("BEGIN IMMEDIATE")
        writer.execute(
            "INSERT INTO chunks (chunk_id, project_id, item_id, revision_id, ordinal, heading, "
            "text, token_estimate, status, sensitivity, trust_level) "
            "VALUES ('late#0', ?, 'architecture.late', 'late', 0, '', "
            "'a retention decision committed after the last checkpoint', 10, "
            "'approved', 'internal', 'reviewed')",
            (PROJECT,),
        )
        writer.execute("COMMIT")
        assert Path(str(stale.path) + "-wal").exists(), (
            "the fixture must leave content in the -wal sidecar, or this test cannot tell the "
            "two copy primitives apart"
        )

        purged_path = tmp_path / "theurian-index-purged.sqlite"
        stale.derive_purged(
            purged_path,
            revision_ids=withdrawn,
            index_build_id="01K1PURGED",
            state_hash="state-abc",
        )
    finally:
        writer.close()

    assert "late#0" in _surviving(purged_path), (
        "the purged build is missing a row the source had committed. The copy took the main "
        "database without its -wal sidecar, so every write since the last checkpoint was "
        "dropped -- silently, and in the direction that loses knowledge"
    )


def test_a_purge_that_leaves_a_withdrawn_row_refuses_rather_than_publishing(
    tmp_path: Path, corpora: tuple[SqliteIndexStore, SqliteIndexStore, list[str]]
) -> None:
    """The post-condition, exercised directly because a correct purge cannot reach it.

    `_verify` fires only when the delete above it is already wrong, so with the
    module working it is unreachable through `derive_purged` and mutation shows
    it: removing the call leaves every other test in this file green. It is kept
    because what publishes a build is a pointer swap, and no later stage looks
    inside the file.

    Called against a build that still holds the revisions, which is precisely the
    state a broken delete would hand it.
    """
    stale, _, withdrawn = corpora
    unpurged = tmp_path / "theurian-index-unpurged.sqlite"
    unpurged.write_bytes(stale.path.read_bytes())

    with pytest.raises(IndexPurgeError, match="still holds"):
        _verify(unpurged, withdrawn)


def test_the_post_condition_also_refuses_an_orphaned_embedding(
    tmp_path: Path, corpora: tuple[SqliteIndexStore, SqliteIndexStore, list[str]]
) -> None:
    """The other half of `_verify`, and the one the pragma is supposed to prevent.

    Constructed the way a pragma-less delete would leave the file: the chunk
    gone, the vector behind. The message names the cause rather than the symptom,
    because "an embedding has no chunk" sends the reader to the schema and
    `PRAGMA foreign_keys` sends them to the connection.
    """
    stale, _, _ = corpora
    damaged = tmp_path / "theurian-index-damaged.sqlite"
    damaged.write_bytes(stale.path.read_bytes())
    with closing(sqlite3.connect(damaged)) as connection, connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute(
            "INSERT INTO embeddings (chunk_id, dimension, vector) VALUES ('ghost#0', 3, X'00')"
        )

    with pytest.raises(IndexPurgeError, match="foreign_keys"):
        _verify(damaged, [])


def test_the_purge_runs_its_post_condition_and_leaves_nothing_behind_when_it_fails(
    tmp_path: Path,
    corpora: tuple[SqliteIndexStore, SqliteIndexStore, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """That `purge_into` *calls* `_verify`, which the two tests above do not check.

    Mutation found this gap: they exercise the guard's logic directly, so deleting
    the call from `purge_into` left all eighteen of them green and turned the
    post-condition into dead code. The wiring needs its own test, and the only way
    to reach it with the rest of the module working is to break the delete --
    which is exactly the situation the guard exists for.

    The second assertion is the one that matters more. A build that fails its
    post-condition must leave no file: what publishes a build is a pointer swap,
    so a half-purged file on disk is one `os.replace` away from being the
    published index, and it looks complete.
    """
    stale, _, withdrawn = corpora
    monkeypatch.setattr(index_purge, "_delete", lambda *_args, **_kwargs: 0)
    purged_path = tmp_path / "theurian-index-purged.sqlite"

    with pytest.raises(IndexPurgeError, match="still holds"):
        stale.derive_purged(
            purged_path,
            revision_ids=withdrawn,
            index_build_id="01K1PURGED",
            state_hash="state-abc",
        )

    assert not purged_path.exists(), (
        "a purge that failed its post-condition left a file behind; it holds withdrawn "
        "content and looks like a finished build"
    )
    for suffix in ("-wal", "-shm"):
        assert not Path(str(purged_path) + suffix).exists(), f"a {suffix} sidecar survived"


def test_a_node_derived_only_from_an_unprovenanced_node_goes_with_it(
    tmp_path: Path, corpora: tuple[SqliteIndexStore, SqliteIndexStore, list[str]]
) -> None:
    """The fixed point, and the case two independent sweeps left standing.

    Held at v3 by the same-named test over `chunk_derivation`. An unprovenanced
    node is deleted because it cannot say what it was built from. A node built
    *from* it inherits exactly that problem -- and it is reachable from nothing
    withdrawn, so the recursive walk never saw it. With the two sets deleted
    independently the parent went and the child stayed, text intact and
    retrievable.

    Seeding both into one recursive query takes the traversal to its fixed point
    instead, which is what makes this a property of the query rather than of the
    order two statements happen to run in.
    """
    stale, _, withdrawn = corpora
    _add_node(stale, "orphan#0", sources=[])
    _add_node(stale, "child-of-orphan#0", node_sources=["orphan#0"])
    _add_node(stale, "grandchild#0", node_sources=["child-of-orphan#0"])
    purged_path = tmp_path / "theurian-index-purged.sqlite"

    stale.derive_purged(
        purged_path, revision_ids=withdrawn, index_build_id="01K1PURGED", state_hash="state-abc"
    )

    surviving = _surviving_nodes(purged_path)
    assert not {"orphan#0", "child-of-orphan#0", "grandchild#0"} & surviving, (
        f"the whole chain below an unprovenanced node must go; these survived: "
        f"{sorted({'orphan#0', 'child-of-orphan#0', 'grandchild#0'} & surviving)}"
    )


def test_a_purge_whose_source_is_missing_names_a_remedy_and_creates_nothing(tmp_path: Path) -> None:
    """`mode=ro` on the *source*, which is the write path's version of decision 7.

    A bare `sqlite3.connect` on a missing source creates an empty database at
    that path. For a source that is the *published* index, this conjured a
    0-byte file where a real build belonged, raised a raw `OperationalError`,
    and made every later search misdiagnose the project as a schema mismatch --
    because a file that exists cannot take the missing-file branch.
    """
    missing = tmp_path / "theurian-index-gone.sqlite"
    store = SqliteIndexStore(missing)

    with pytest.raises(IndexPurgeError, match="could not be read"):
        store.derive_purged(
            tmp_path / "theurian-index-new.sqlite",
            revision_ids=["r0"],
            index_build_id="01K1NEW",
            state_hash="s",
        )

    assert not missing.exists(), "the purge conjured the source it could not find"
    assert not (tmp_path / "theurian-index-new.sqlite").exists()


def test_a_purge_refuses_to_write_over_another_writers_building_file(
    tmp_path: Path, corpora: tuple[SqliteIndexStore, SqliteIndexStore, list[str]]
) -> None:
    """A `.building` file is either live work or crash leftovers, and this cannot tell.

    Deleting it unconditionally is the same class of mistake as writing into an
    existing build: it destroys another writer's work to make room for this one.
    """
    stale, _, withdrawn = corpora
    target = tmp_path / "theurian-index-purged.sqlite"
    occupied = Path(f"{target}.building")
    occupied.write_bytes(b"another writer's work in progress")

    with pytest.raises(IndexPurgeError, match="already exists"):
        stale.derive_purged(
            target, revision_ids=withdrawn, index_build_id="01K1PURGED", state_hash="state-abc"
        )

    assert occupied.read_bytes() == b"another writer's work in progress"


def test_purge_errors_are_theurian_errors_with_remedies(
    tmp_path: Path, corpora: tuple[SqliteIndexStore, SqliteIndexStore, list[str]]
) -> None:
    """Every CLI handler catches `(TheurianError, sqlite3.Error, OSError)`.

    An `IndexPurgeError` outside that hierarchy reaches a user as a Rich
    traceback with their absolute paths in it, rather than as the
    `{error, remedy}` shape every command promises (CP-2). The refusal message
    also carries a filename rather than a path, because the condition is about
    a build id and the path names the operator's home directory.
    """
    stale, _, withdrawn = corpora
    occupied = tmp_path / "theurian-index-occupied.sqlite"
    occupied.touch()

    with pytest.raises(IndexPurgeError) as caught:
        stale.derive_purged(
            occupied, revision_ids=withdrawn, index_build_id="01K1P", state_hash="s"
        )

    assert isinstance(caught.value, TheurianError)
    assert "theurian index" in str(caught.value), "every message names the command that fixes it"
    assert str(tmp_path) not in str(caught.value), "an absolute path reached a user-facing message"


def test_a_purge_does_not_touch_a_source_whose_wal_is_hot(
    tmp_path: Path, corpora: tuple[SqliteIndexStore, SqliteIndexStore, list[str]]
) -> None:
    """The second face of opening the source read-write, and the one a fixture hides.

    `test_a_purge_leaves_the_published_build_untouched` hands over a source whose
    WAL has been checkpointed on last close, so a read-write open changes nothing
    and the assertion passes for the wrong reason. A *hot* WAL -- an indexer
    killed between commit and checkpoint -- is different: opening read-write runs
    WAL recovery, which rewrites the main database and removes the sidecar. That
    is a write to the published build, performed by the operation whose whole
    premise is that it never writes to it.

    The child process exits with `os._exit` precisely so no atexit handler
    checkpoints on the way out.
    """
    stale, _, withdrawn = corpora
    script = (
        "import sqlite3, os, sys\n"
        "c = sqlite3.connect(sys.argv[1], isolation_level=None)\n"
        "c.execute('PRAGMA journal_mode = WAL')\n"
        "c.execute('PRAGMA wal_autocheckpoint = 0')\n"
        "c.execute('BEGIN IMMEDIATE')\n"
        'c.execute("INSERT INTO chunks (chunk_id, project_id, item_id, revision_id, ordinal,'
        " heading, text, token_estimate, status, sensitivity, trust_level) VALUES"
        " ('hot#0','demo','architecture.hot','hot',0,'','committed but not checkpointed',"
        "10,'approved','internal','reviewed')\")\n"
        "c.execute('COMMIT')\n"
        "os._exit(0)\n"
    )
    subprocess.run([sys.executable, "-c", script, str(stale.path)], check=True)  # noqa: S603
    wal = Path(f"{stale.path}-wal")
    assert wal.exists() and wal.stat().st_size > 0, (
        "the fixture must leave a hot WAL, or a read-write open changes nothing and this "
        "test passes for the same wrong reason as its neighbour"
    )
    before = hashlib.sha256(stale.path.read_bytes()).hexdigest()

    stale.derive_purged(
        tmp_path / "theurian-index-purged.sqlite",
        revision_ids=withdrawn,
        index_build_id="01K1PURGED",
        state_hash="state-abc",
    )

    assert hashlib.sha256(stale.path.read_bytes()).hexdigest() == before, (
        "the purge ran WAL recovery on the published build: opening the source read-write "
        "rewrote the file the whole design says is immutable"
    )
    assert wal.exists(), "the purge checkpointed and removed the published build's -wal sidecar"


def test_verify_refuses_a_build_that_still_holds_an_unprovenanced_node(
    tmp_path: Path, corpora: tuple[SqliteIndexStore, SqliteIndexStore, list[str]]
) -> None:
    """`_verify`'s node post-condition, which the traversal keeps unreachable.

    Held at v3 by `test_verify_refuses_a_build_that_still_holds_an_
    unprovenanced_derived_row`, over a `chunks.derived = 1` row. v4 moves the row
    to `nodes`: `_DOOMED` seeds on unprovenanced nodes, so a real purge removes
    them and this check never fires -- `test_a_node_derived_only_from_an_
    unprovenanced_node_goes_with_it` proves the traversal converges. Disabling
    this check therefore survives the whole suite: nothing reaches it.

    It is the runtime backstop for the day a future `_DOOMED` reconstruction, or
    a RAPTOR builder writing node rows, leaves an unprovenanced one the
    traversal missed -- a node that cannot be shown to hold nothing withdrawn,
    and so must not be published. This reaches it directly: a build with such a
    node inserted past the traversal, verified as if a broken purge had handed
    it over.
    """
    stale, _, _ = corpora
    build = tmp_path / "theurian-index-leaked.sqlite"
    build.write_bytes(stale.path.read_bytes())
    with closing(sqlite3.connect(build)) as connection, connection:
        connection.execute(
            "INSERT INTO nodes (node_id, tree_id, level, node_type, text, content_hash, "
            "summary_model, summary_model_revision, summary_prompt_hash, embedding_model, "
            "embedding_model_revision, embedding_dimension, source_revision_id, "
            "index_build_id, project_id, sensitivity, status) "
            "VALUES ('leaked#0', 'tree-abc', 1, 'document', "
            "'SECRET a summary whose sources cannot be named', 'deadbeef', '', '', '', '', "
            "'', 0, '', 'test-build', ?, 'internal', 'approved')",
            (PROJECT,),
        )

    with pytest.raises(IndexPurgeError, match="no provenance"):
        _verify(build, [])


def test_verify_refuses_a_build_whose_node_derivation_points_at_a_chunk_that_is_gone(
    tmp_path: Path, corpora: tuple[SqliteIndexStore, SqliteIndexStore, list[str]]
) -> None:
    """`_verify`'s node post-condition for a dangling edge, which has no v3 analogue.

    ADR-0024 decision 8's own rule, applied to the new table: "a derived node
    whose derivation edges cannot be resolved is deleted, not kept." A dangling
    `node_derivation` row -- one naming a `source_chunk_id` no longer in `chunks`
    -- is exactly that state, distinct from the unprovenanced case above (that
    node has *zero* edges; this one has an edge that points nowhere). It arises
    the way `test_the_post_condition_also_refuses_an_orphaned_embedding`
    constructs its own case: `PRAGMA foreign_keys` off, so `ON DELETE CASCADE`
    never removes the edge when its source goes. `_verify` must refuse a build
    holding a node whose claimed source cannot be checked, exactly as it refuses
    one still holding an embedding whose chunk is gone.
    """
    stale, _, _ = corpora
    build = tmp_path / "theurian-index-dangling.sqlite"
    build.write_bytes(stale.path.read_bytes())
    store = SqliteIndexStore(build)
    _add_node(store, "dangling-node#0", sources=["keep-000#0"])
    with closing(sqlite3.connect(build)) as connection, connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("DELETE FROM chunks WHERE chunk_id = 'keep-000#0'")

    with pytest.raises(IndexPurgeError):
        _verify(build, [])
