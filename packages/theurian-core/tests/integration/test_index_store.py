"""The retrieval index: FTS5 and an exact vector scan (FR-R1, FR-R2).

A real SQLite file every time. FTS5's tokenizer, its query syntax, and the
external-content triggers are exactly the things a fake would paper over.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from theurian.domain.chunking import Chunk
from theurian.infrastructure.sqlite.index_store import (
    IndexableChunk,
    IndexBuildError,
    SqliteIndexStore,
    fts5_available,
)

pytestmark = pytest.mark.integration


def _indexable(  # noqa: PLR0913 - one keyword per canonical field the filters read
    chunk_id: str,
    text: str,
    *,
    item: str = "architecture.auth",
    project: str = "demo",
    status: str = "approved",
    heading: str = "",
) -> IndexableChunk:
    return IndexableChunk(
        chunk=Chunk(chunk_id=chunk_id, ordinal=0, text=text, heading=heading),
        project_id=project,
        item_id=item,
        revision_id=f"rev-{chunk_id}",
        status=status,
        sensitivity="internal",
        trust_level="reviewed",
    )


@pytest.fixture
def store(tmp_path: Path) -> SqliteIndexStore:
    store = SqliteIndexStore(tmp_path / "index" / "theurian-index-01.sqlite")
    store.create(index_build_id="01K1IDX", state_hash="abc123")
    return store


# -- Building ---------------------------------------------------------------


def test_this_python_has_fts5() -> None:
    """Lexical search is not optional. If this fails, every search test below
    is testing nothing, so it is asserted once and loudly."""
    assert fts5_available()


def test_an_index_records_what_it_was_built_from(store: SqliteIndexStore) -> None:
    """The index is derived. Without the state hash there is no way to tell
    whether it still describes the canonical store."""
    metadata = store.metadata()

    assert metadata["index_build_id"] == "01K1IDX"
    assert metadata["state_hash"] == "abc123"


def test_building_over_an_existing_file_is_refused(store: SqliteIndexStore) -> None:
    """An index build is all-or-nothing. Appending to a half-built one produces
    a file that looks complete and silently is not."""
    with pytest.raises(IndexBuildError, match="already exists"):
        store.create(index_build_id="01K1IDX2", state_hash="def456")


def test_the_index_lives_apart_from_the_canonical_store(store: SqliteIndexStore) -> None:
    """ADR-0004, ADR-0017. Sharing a file would make an index schema change
    invalidate every canonical state -- rebuilding what is authoritative to
    accommodate what is disposable."""
    assert "index" in store.path.name


# -- Lexical search ----------------------------------------------------------


def test_a_term_finds_the_chunk_that_contains_it(store: SqliteIndexStore) -> None:
    store.add_chunks(
        [
            _indexable("c1", "Every call carries a signed JWT for authentication."),
            _indexable("c2", "Caching uses a two-minute TTL.", item="architecture.cache"),
        ]
    )

    hits = store.search_lexical("JWT", project_id="demo")

    assert [h.chunk_id for h in hits] == ["c1"]


def test_matching_is_case_and_accent_insensitive(store: SqliteIndexStore) -> None:
    store.add_chunks([_indexable("c1", "The résumé parser rejects malformed input.")])

    assert store.search_lexical("RESUME", project_id="demo")


def test_all_query_terms_must_appear(store: SqliteIndexStore) -> None:
    """Any-term matching on a knowledge base returns everything, which is the
    same as returning nothing useful."""
    store.add_chunks(
        [
            _indexable("c1", "Authentication uses signed tokens."),
            _indexable("c2", "Caching uses a short TTL.", item="architecture.cache"),
        ]
    )

    hits = store.search_lexical("authentication tokens", project_id="demo")

    assert [h.chunk_id for h in hits] == ["c1"]


def test_fts5_operators_typed_by_a_user_are_treated_as_words(store: SqliteIndexStore) -> None:
    """Someone searching `auth OR token` means three words. Letting FTS5 read
    them as syntax turns an ordinary sentence into a syntax error."""
    store.add_chunks([_indexable("c1", "auth and token rotation")])

    assert store.search_lexical('auth OR "token"', project_id="demo") == ()
    assert store.search_lexical("auth token", project_id="demo")


@pytest.mark.parametrize("query", ['"', "-", "*", "((", "^", ":", "   "])
def test_punctuation_never_raises_at_the_user(store: SqliteIndexStore, query: str) -> None:
    """A search box that punishes punctuation is a broken search box."""
    store.add_chunks([_indexable("c1", "some text")])

    assert store.search_lexical(query, project_id="demo") == ()


def test_results_are_ranked_best_first(store: SqliteIndexStore) -> None:
    store.add_chunks(
        [
            _indexable("sparse", "A passing mention of tokens among much other prose. " * 8),
            _indexable("dense", "Tokens tokens tokens.", item="architecture.tokens"),
        ]
    )

    hits = store.search_lexical("tokens", project_id="demo")

    assert hits[0].chunk_id == "dense"


# -- Filtering happens before ranking (FR-R1) ---------------------------------


def test_another_project_is_never_ranked(store: SqliteIndexStore) -> None:
    """SEC-13. Filtering after ranking would let a caller infer that a document
    they may not read exists, by noticing how many results vanished."""
    store.add_chunks(
        [
            _indexable("mine", "shared secret terminology", project="demo"),
            _indexable("theirs", "shared secret terminology", project="other"),
        ]
    )

    hits = store.search_lexical("shared secret", project_id="demo")

    assert [h.chunk_id for h in hits] == ["mine"]


def test_drafts_are_withheld_unless_asked_for(store: SqliteIndexStore) -> None:
    store.add_chunks(
        [
            _indexable("approved", "caching policy", status="approved"),
            _indexable("draft", "caching policy", status="draft", item="architecture.draft"),
        ]
    )

    assert [h.chunk_id for h in store.search_lexical("caching", project_id="demo")] == ["approved"]
    with_drafts = store.search_lexical("caching", project_id="demo", include_unapproved=True)
    assert len(with_drafts) == 2


# -- Dense search ------------------------------------------------------------


def test_the_nearest_vector_ranks_first(store: SqliteIndexStore) -> None:
    store.add_chunks([_indexable("near", "a"), _indexable("far", "b", item="i2")])
    store.add_embeddings([("near", [1.0, 0.0]), ("far", [0.0, 1.0])])

    hits = store.search_dense([0.9, 0.1], project_id="demo")

    assert [h.chunk_id for h in hits] == ["near", "far"]


def test_dense_search_respects_the_same_filters(store: SqliteIndexStore) -> None:
    store.add_chunks(
        [_indexable("mine", "a", project="demo"), _indexable("theirs", "a", project="other")]
    )
    store.add_embeddings([("mine", [1.0, 0.0]), ("theirs", [1.0, 0.0])])

    hits = store.search_dense([1.0, 0.0], project_id="demo")

    assert [h.chunk_id for h in hits] == ["mine"]


def test_a_corpus_embedded_by_another_model_is_skipped_not_scored(
    store: SqliteIndexStore,
) -> None:
    """Vectors of different dimension are comparable arithmetically and
    meaningless semantically. Scoring them would produce confident nonsense."""
    store.add_chunks([_indexable("two-dim", "a"), _indexable("three-dim", "b", item="i2")])
    store.add_embeddings([("two-dim", [1.0, 0.0]), ("three-dim", [1.0, 0.0, 0.0])])

    hits = store.search_dense([1.0, 0.0], project_id="demo")

    assert [h.chunk_id for h in hits] == ["two-dim"]


def test_an_index_with_no_embeddings_returns_nothing_rather_than_failing(
    store: SqliteIndexStore,
) -> None:
    """A machine with no embedding provider still gets lexical search. The
    reported mode says so; the search does not crash."""
    store.add_chunks([_indexable("c1", "text")])

    assert store.search_dense([1.0, 0.0], project_id="demo") == ()


def test_a_zero_query_vector_yields_nothing(store: SqliteIndexStore) -> None:
    """Cosine similarity is undefined against a zero vector."""
    store.add_chunks([_indexable("c1", "text")])
    store.add_embeddings([("c1", [1.0, 0.0])])

    assert store.search_dense([0.0, 0.0], project_id="demo") == ()


def test_dense_ties_break_deterministically(store: SqliteIndexStore) -> None:
    """FR-R7. Identical vectors must not come back in storage order."""
    store.add_chunks([_indexable("b", "x", item="i1"), _indexable("a", "x", item="i2")])
    store.add_embeddings([("b", [1.0, 0.0]), ("a", [1.0, 0.0])])

    assert [h.chunk_id for h in store.search_dense([1.0, 0.0], project_id="demo")] == ["a", "b"]


def test_vectors_survive_a_round_trip(store: SqliteIndexStore) -> None:
    """Packed as fixed little-endian float32 so an index built on one machine
    reads correctly on another."""
    store.add_chunks([_indexable("c1", "x")])
    store.add_embeddings([("c1", [0.25, -0.5, 0.125])])

    hits = store.search_dense([0.25, -0.5, 0.125], project_id="demo")

    assert hits[0].score == pytest.approx(1.0)


def test_the_embedding_model_is_recorded(store: SqliteIndexStore) -> None:
    """A query embedded by a different model than the corpus is a bug that
    produces plausible output. Recording the model is what lets it be caught."""
    store.record_embedding_model(model_id="fake-v1", dimension=8)

    metadata = store.metadata()
    assert metadata["embedding_model"] == "fake-v1"
    assert metadata["embedding_dimension"] == 8


# -- Chunk retrieval ---------------------------------------------------------


def test_chunk_rows_come_back_by_id(store: SqliteIndexStore) -> None:
    store.add_chunks([_indexable("c1", "the body", heading="Auth")])

    rows = store.texts(["c1", "missing"])

    assert rows["c1"]["text"] == "the body"
    assert rows["c1"]["heading"] == "Auth"
    assert "missing" not in rows


def test_a_token_estimate_is_stored_for_budgeting(store: SqliteIndexStore) -> None:
    """FR-R4 packs to a budget, and asking each chunk its size at query time
    would mean reading every candidate's text to decide whether to read it."""
    store.add_chunks([_indexable("c1", "x" * 400)])

    assert store.texts(["c1"])["c1"]["token_estimate"] >= 100
