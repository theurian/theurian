"""The retrieval index: FTS5 and an exact vector scan (FR-R1, FR-R2).

A real SQLite file every time. FTS5's tokenizer, its query syntax, and the
external-content triggers are exactly the things a fake would paper over.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from theurian.domain.chunking import Chunk
from theurian.infrastructure.sqlite.index_store import (
    MAX_QUERY_CHARS,
    MAX_QUERY_TERMS,
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
    store.create(index_build_id="01K1DXAA", state_hash="abc123")
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

    assert metadata["index_build_id"] == "01K1DXAA"
    assert metadata["state_hash"] == "abc123"


def test_building_over_an_existing_file_is_refused(store: SqliteIndexStore) -> None:
    """An index build is all-or-nothing. Appending to a half-built one produces
    a file that looks complete and silently is not."""
    with pytest.raises(IndexBuildError, match="already exists"):
        store.create(index_build_id="01K1DXAB", state_hash="def456")


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


def test_matching_every_term_beats_matching_one_of_them(store: SqliteIndexStore) -> None:
    """Terms are ORed, and precision is BM25's job rather than the matcher's.

    ANDing them read better and shipped broken: `unicode61` has no stop words,
    so a real question required `how` and `do` to appear in the chunk and
    matched nothing at all -- see
    :func:`test_a_natural_language_question_reaches_the_lexical_index`, which
    fails if this is ever changed back. So a partial match is *returned*; what
    protects the caller is that it loses.

    This test previously asserted the opposite -- that every term must appear --
    and could not fail either way: its only control document contained neither
    query term, so it was excluded under AND and under OR alike. Both halves
    below are therefore load-bearing. The exclusion is what stops "any term"
    from meaning "everything"; the order is what replaced the AND guarantee.
    """
    store.add_chunks(
        [
            _indexable("both", "Authentication uses signed tokens."),
            _indexable("one", "Tokens are minted hourly.", item="architecture.mint"),
            _indexable("neither", "Caching uses a short TTL.", item="architecture.cache"),
        ]
    )

    hits = store.search_lexical("authentication tokens", project_id="demo")

    assert [h.chunk_id for h in hits] == ["both", "one"], "a chunk sharing no term is not returned"
    assert hits[0].score > hits[1].score, "the full match outranks the partial one"


def test_fts5_operators_typed_by_a_user_are_treated_as_words(store: SqliteIndexStore) -> None:
    """Someone searching `auth NOT token` means three words.

    If FTS5 read them as syntax, `NOT` would exclude the very chunk the user was
    looking for — a wrong answer rather than an error, which is worse.
    """
    store.add_chunks([_indexable("c1", "auth and token rotation")])

    assert store.search_lexical("auth NOT token", project_id="demo"), (
        "NOT must be a word, not an operator that excludes the match"
    )
    assert store.search_lexical('auth OR "token"', project_id="demo")


def test_a_natural_language_question_reaches_the_lexical_index(
    store: SqliteIndexStore,
) -> None:
    """The main thing an agent actually sends.

    ANDing every term required `how`, `do`, and `for` to appear in the chunk --
    `unicode61` has no stop words -- so lexical search returned nothing for any
    real question, and the hybrid half of retrieval quietly never fused.
    """
    store.add_chunks([_indexable("c1", "Rotate signing keys for auth tokens every ninety days.")])

    hits = store.search_lexical("How do we rotate signing keys for auth tokens?", project_id="demo")

    assert [h.chunk_id for h in hits] == ["c1"]


@pytest.mark.parametrize("query", ['"', "-", "*", "((", "^", ":", "   "])
def test_punctuation_never_raises_at_the_user(store: SqliteIndexStore, query: str) -> None:
    """A search box that punishes punctuation is a broken search box."""
    store.add_chunks([_indexable("c1", "some text")])

    assert store.search_lexical(query, project_id="demo") == ()


@pytest.mark.parametrize("retriever", ["search_lexical", "search_substring"])
@pytest.mark.parametrize("query", ["token\x00", "tok\x00en"])
def test_a_nul_byte_in_a_query_returns_nothing_rather_than_raising(
    store: SqliteIndexStore, query: str, retriever: str
) -> None:
    """An input that survives quoting and still breaks FTS5.

    `_to_match_expression` wraps every term in quotes, so no punctuation reaches
    FTS5 as syntax. A NUL is not punctuation: SQLite's contract is a
    NUL-terminated UTF-8 string, so the C string ends early and FTS5 reports
    `unterminated string` -- a message containing neither "fts5" nor "syntax",
    which is why the guard used to miss it and re-raise at the caller.

    Both retrievers, because both used to fail and only one was reported.
    JSON-RPC can carry ``\\u0000``, so an agent can send this.
    """
    store.add_chunks([_indexable("c1", "every call carries a signed token")])

    assert getattr(store, retriever)(query, project_id="demo", limit=10) == ()


@pytest.mark.parametrize("retriever", ["search_lexical", "search_substring"])
@pytest.mark.parametrize("query", ["token\ud800", "tok\udc80en"])
def test_a_lone_surrogate_in_a_query_returns_nothing_rather_than_raising(
    store: SqliteIndexStore, query: str, retriever: str
) -> None:
    """The same defect as the NUL, arriving as a different exception.

    An unpaired surrogate cannot be encoded as UTF-8 at all, so the driver
    raises `UnicodeEncodeError` before SQLite is called -- which no
    `except sqlite3.OperationalError` could have caught, however well written.
    `json.loads('"\\\\ud800"')` yields one, so this is reachable over JSON-RPC.
    """
    store.add_chunks([_indexable("c1", "every call carries a signed token")])

    assert getattr(store, retriever)(query, project_id="demo", limit=10) == ()


def test_a_well_formed_term_survives_beside_an_untransportable_one(
    store: SqliteIndexStore,
) -> None:
    """The whole query is not thrown away for one bad byte.

    Dropping the offending *term* rather than the query keeps a search usable
    when a caller concatenated something odd onto the end of it.
    """
    store.add_chunks([_indexable("c1", "every call carries a signed token")])

    hits = store.search_lexical("carries token\x00", project_id="demo", limit=10)

    assert [h.chunk_id for h in hits] == ["c1"]


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
    store.add_chunks([_indexable("near", "a"), _indexable("mid", "b", item="i2")])
    store.add_embeddings([("near", [1.0, 0.0]), ("mid", [0.7, 0.7])])

    hits = store.search_dense([0.9, 0.1], project_id="demo")

    assert [h.chunk_id for h in hits] == ["near", "mid"]


def test_a_barely_similar_vector_is_not_returned_at_all(store: SqliteIndexStore) -> None:
    """Hashed n-grams give almost any pair of strings a small nonzero cosine.

    Without a floor, every query returns the whole corpus ranked by accident,
    and an agent asking about payroll receives an approved architecture
    decision. "We have no such decision" has to be expressible.
    """
    store.add_chunks([_indexable("near", "a"), _indexable("far", "b", item="i2")])
    store.add_embeddings([("near", [1.0, 0.0]), ("far", [0.0, 1.0])])

    hits = store.search_dense([0.9, 0.1], project_id="demo")

    assert [h.chunk_id for h in hits] == ["near"], "0.11 cosine is noise, not a match"


def test_a_query_matching_nothing_returns_nothing(store: SqliteIndexStore) -> None:
    store.add_chunks([_indexable("c1", "a")])
    store.add_embeddings([("c1", [1.0, 0.0])])

    assert store.search_dense([0.0, 1.0], project_id="demo") == ()


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


# -- Scripts without word boundaries -----------------------------------------


JAPANESE = (
    "すべての内部呼び出しは、アイデンティティゲートウェイが発行した"
    "署名付きトークンを持つ。トークンはデプロイごとにローテーションされる。"
)


@pytest.mark.parametrize(
    "query", ["トークン", "署名付きトークン", "ローテーション", "デプロイ", "内部呼び出し"]
)
def test_japanese_is_searchable_by_substring(store: SqliteIndexStore, query: str) -> None:
    """`unicode61` splits on whitespace and punctuation only, so a Japanese
    sentence becomes one token and none of these match — the entire knowledge
    base of a Japanese-language project is invisible to search. The trigram
    index is what makes it reachable.
    """
    store.add_chunks([_indexable("ja", JAPANESE, heading="認証ポリシー")])

    assert store.search_lexical(query, project_id="demo") == (), "the word index cannot"
    assert store.search_substring(query, project_id="demo"), "the trigram index can"


def test_substring_matching_still_discriminates(store: SqliteIndexStore) -> None:
    """A substring index that matched everything would trade one broken search
    for another."""
    store.add_chunks([_indexable("ja", JAPANESE, heading="認証ポリシー")])

    assert store.search_substring("kubernetes", project_id="demo") == ()
    assert store.search_substring("課金モデル", project_id="demo") == ()


def test_the_substring_index_is_scoped_to_one_project(store: SqliteIndexStore) -> None:
    """SEC-13, FR-R1, for the newest retriever.

    Every query above this one is single-project, so the trigram retriever's
    scoping was carried entirely by the word index's tests: removing the
    ``project_id`` predicate from all three retrievers killed the lexical and
    dense isolation tests and left every substring test green. A retriever added
    later must not inherit an isolation guarantee it is never asked to prove.
    """
    store.add_chunks(
        [
            _indexable("mine", JAPANESE, project="demo"),
            _indexable("theirs", JAPANESE, project="other", item="architecture.other"),
        ]
    )

    mine = store.search_substring("トークン", project_id="demo")
    theirs = store.search_substring("トークン", project_id="other")

    assert [h.chunk_id for h in mine] == ["mine"]
    assert [h.chunk_id for h in theirs] == ["theirs"]


def test_the_substring_index_withholds_drafts_too(store: SqliteIndexStore) -> None:
    """The status filter is per retriever, so it is asserted per retriever.

    A draft reachable through trigrams but not through terms would be withheld
    from an English query and served for a Japanese one.
    """
    store.add_chunks(
        [
            _indexable("approved", JAPANESE, status="approved"),
            _indexable("draft", JAPANESE, status="draft", item="architecture.draft"),
        ]
    )

    withheld = store.search_substring("トークン", project_id="demo")
    asked_for = store.search_substring("トークン", project_id="demo", include_unapproved=True)

    assert [h.chunk_id for h in withheld] == ["approved"]
    assert len(asked_for) == 2


def test_the_substring_index_ors_its_terms_like_the_word_index(store: SqliteIndexStore) -> None:
    """The two lexical retrievers must agree about what a multi-term query means.

    Switching this one to AND changed nothing in the suite: every substring test
    sent a single term, so the conjunction was never exercised. Divergence here
    would be invisible and would surface as a Japanese query answering
    differently from its English equivalent.
    """
    store.add_chunks(
        [
            _indexable("both", "トークンのローテーション手順"),
            _indexable("one", "ローテーションのみを説明する文書", item="architecture.rotate"),
            _indexable("neither", "課金モデルの説明", item="architecture.billing"),
        ]
    )

    hits = store.search_substring("トークン ローテーション", project_id="demo")

    assert sorted(h.chunk_id for h in hits) == ["both", "one"], "OR, as the word index does"


def test_a_query_of_only_common_words_still_matches_today(store: SqliteIndexStore) -> None:
    """Documents a known gap rather than a desired behaviour.

    A query whose terms all appear in every document carries no lexical
    evidence, yet it still matches: SQLite's BM25 returns -1.375e-06 rather than
    zero for that case, so no score threshold excludes it. Separating "matched
    only common words" from "matched weakly" needs a per-term IDF test.

    Asserted so the day someone fixes it, this test fails and gets rewritten,
    rather than the gap persisting because nothing described it.
    """
    store.add_chunks([_indexable("only", "The gateway verifies the token.")])

    assert store.search_lexical("the", project_id="demo"), "known gap, not a feature"
    assert store.search_lexical("gateway token", project_id="demo")


# -- Input bounds (SEC-8) ----------------------------------------------------
#
# `MAX_QUERY_CHARS` and `MAX_QUERY_TERMS` carry a measured DoS rationale -- a
# 2,000-term query did not finish inside a minute, and `sqlite3` releases the
# GIL, so a handful of them starve every tool call for every project this daemon
# serves. Neither constant was named anywhere in the suite: raising both to a
# hundred million broke nothing.


def test_a_query_is_truncated_at_the_character_bound(store: SqliteIndexStore) -> None:
    """The bound is a truncation, so it has to be observable as one.

    The term is placed just past the cut, where it is dropped, and just before
    it, where it is kept. A test that only sent a long query would pass against
    no bound at all.

    This pins the *mechanism*, not the number: the padding is sized from
    :data:`MAX_QUERY_CHARS`, so retuning the constant moves both sides together
    and this test follows. Deleting the truncation is what it fails on, which is
    the regression worth catching -- the number is a deliberate knob and the
    rationale for its value lives with the constant.
    """
    store.add_chunks([_indexable("c1", "the gateway rejects an unsigned request")])
    padding = "x" * MAX_QUERY_CHARS

    assert store.search_lexical(f"{padding} gateway", project_id="demo") == (), "past the cut"
    assert store.search_lexical(f"gateway {padding}", project_id="demo"), "before the cut"


def test_only_the_first_terms_by_length_are_spent(store: SqliteIndexStore) -> None:
    """FR-R7 and SEC-8 together: bounded, and bounded the same way every time.

    Longest-first is the selection rule, not a display order -- an English
    question front-loads its least selective words, so taking the first N as
    typed would keep `how`, `do`, `we` and discard the noun the caller believes
    they searched for. Pinned here because nothing else states it.
    """
    store.add_chunks([_indexable("c1", "gateway")])
    fillers = " ".join(f"filler{index:04d}long" for index in range(MAX_QUERY_TERMS))

    assert store.search_lexical(f"gateway {fillers}", project_id="demo") == (), (
        "the short, real term loses its slot to longer fillers"
    )
    assert store.search_lexical(f"gateway {fillers[:20]}", project_id="demo"), (
        "well inside the bound, the same term is spent"
    )
