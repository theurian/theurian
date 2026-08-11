"""The retrieval index: FTS5 and an exact vector scan (FR-R1, FR-R2).

A real SQLite file every time. FTS5's tokenizer, its query syntax, and the
external-content triggers are exactly the things a fake would paper over.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from theurian.domain.chunking import Chunk
from theurian.infrastructure.sqlite.index_scan import SCAN_TERMS, scan_statement
from theurian.infrastructure.sqlite.index_schema import INDEX_SCHEMA_VERSION
from theurian.infrastructure.sqlite.index_store import (
    MAX_QUERY_CHARS,
    MAX_QUERY_TERMS,
    IndexableChunk,
    IndexBuildError,
    IndexUnreadableError,
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


def _corrupt(database: Path, statement: str, parameters: tuple[Any, ...] = ()) -> None:
    """Write a value into an index that no build would ever write.

    The index is derived, unsigned and git-ignored (SEC-7), so a cell can hold
    anything a partial overwrite, a truncated copy or a flipped bit left there —
    and the reads below are the only thing standing between such a cell and the
    caller. Statements rather than random bytes because a named cell is
    reproducible; the random-fixture campaign that found this class lives in the
    review record, not in the suite.

    ``closing`` rather than ``with sqlite3.connect(...)``: the latter commits and
    does *not* close, leaking a handle whose ``ResourceWarning`` — with
    ``filterwarnings = error`` — fails whichever test happens to be running when
    it is collected.
    """
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(statement, parameters)
        connection.commit()


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

    hits = store.search_lexical("JWT", project_id="demo").rows

    assert [h.chunk_id for h in hits] == ["c1"]


def test_matching_is_case_and_accent_insensitive(store: SqliteIndexStore) -> None:
    store.add_chunks([_indexable("c1", "The résumé parser rejects malformed input.")])

    assert store.search_lexical("RESUME", project_id="demo").rows


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

    hits = store.search_lexical("authentication tokens", project_id="demo").rows

    assert [h.chunk_id for h in hits] == ["both", "one"], "a chunk sharing no term is not returned"
    assert hits[0].score > hits[1].score, "the full match outranks the partial one"


def test_fts5_operators_typed_by_a_user_are_treated_as_words(store: SqliteIndexStore) -> None:
    """Someone searching `auth NOT token` means three words.

    If FTS5 read them as syntax, `NOT` would exclude the very chunk the user was
    looking for — a wrong answer rather than an error, which is worse.
    """
    store.add_chunks([_indexable("c1", "auth and token rotation")])

    assert store.search_lexical("auth NOT token", project_id="demo").rows, (
        "NOT must be a word, not an operator that excludes the match"
    )
    assert store.search_lexical('auth OR "token"', project_id="demo").rows


def test_a_natural_language_question_reaches_the_lexical_index(
    store: SqliteIndexStore,
) -> None:
    """The main thing an agent actually sends.

    ANDing every term required `how`, `do`, and `for` to appear in the chunk --
    `unicode61` has no stop words -- so lexical search returned nothing for any
    real question, and the hybrid half of retrieval quietly never fused.
    """
    store.add_chunks([_indexable("c1", "Rotate signing keys for auth tokens every ninety days.")])

    hits = store.search_lexical(
        "How do we rotate signing keys for auth tokens?", project_id="demo"
    ).rows

    assert [h.chunk_id for h in hits] == ["c1"]


@pytest.mark.parametrize("query", ['"', "-", "*", "((", "^", ":", "   "])
def test_punctuation_never_raises_at_the_user(store: SqliteIndexStore, query: str) -> None:
    """A search box that punishes punctuation is a broken search box."""
    store.add_chunks([_indexable("c1", "some text")])

    assert store.search_lexical(query, project_id="demo").rows == ()


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

    assert getattr(store, retriever)(query, project_id="demo", limit=10).rows == ()


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

    assert getattr(store, retriever)(query, project_id="demo", limit=10).rows == ()


def test_a_well_formed_term_survives_beside_an_untransportable_one(
    store: SqliteIndexStore,
) -> None:
    """The whole query is not thrown away for one bad byte.

    Dropping the offending *term* rather than the query keeps a search usable
    when a caller concatenated something odd onto the end of it.
    """
    store.add_chunks([_indexable("c1", "every call carries a signed token")])

    hits = store.search_lexical("carries token\x00", project_id="demo", limit=10).rows

    assert [h.chunk_id for h in hits] == ["c1"]


def test_results_are_ranked_best_first(store: SqliteIndexStore) -> None:
    store.add_chunks(
        [
            _indexable("sparse", "A passing mention of tokens among much other prose. " * 8),
            _indexable("dense", "Tokens tokens tokens.", item="architecture.tokens"),
        ]
    )

    hits = store.search_lexical("tokens", project_id="demo").rows

    assert hits[0].chunk_id == "dense"


#: More matching chunks than ``search_lexical``'s own default ``limit`` of 50.
#: A corpus smaller than that default cannot tell "the argument was honoured"
#: apart from "the corpus ran out", which is the shape a ceiling test fails in.
CEILING_CORPUS = 60


@pytest.mark.parametrize(
    ("limit", "expected"),
    [(1, 1), (50, 50), (59, 59), (60, 60), (61, 60)],
    ids=["one", "the-default", "one-below", "exactly", "one-past"],
)
def test_the_lexical_limit_is_a_ceiling_and_never_pads_past_the_corpus(
    store: SqliteIndexStore, limit: int, expected: int
) -> None:
    """The port contract ``RetrievalService._visible_ranking`` reads as truth.

    ``IndexStore.search_lexical`` states that an implementation must never
    return more rows than ``limit``, and the deepening loop turns that into an
    inference: more rows than it asked for means this retriever never truncated,
    so it already holds everything and asking deeper buys another scan and no
    new rows -- and the loop exits. An adapter that both truncated *and*
    overshot would fire that branch on a retriever that had *not* finished, and
    the caller would lose rows it never learns existed.

    Nothing in the tree bound the shipped adapter to that requirement; the
    deepening logic was tested against fakes that obey it by construction.

    The two boundaries either side of the corpus are where an off-by-one shows:
    asking for exactly what exists must return all of it, and asking for more
    must return what exists rather than erroring or padding.

    Counts only, deliberately. "The truncated rows are the best ones" cannot be
    asserted here by comparing against this same method at full depth: any
    change to the ordering moves both answers together, so the comparison holds
    however wrong the order is. What ranking survives truncation is held instead
    by :func:`test_results_are_ranked_best_first`, against a corpus whose best
    row is known by construction.
    """
    store.add_chunks(
        [
            _indexable(f"c{n:03d}", f"Chunk {n} discusses the key rotation clause.")
            for n in range(CEILING_CORPUS)
        ]
    )
    # The precondition the boundary cases rest on. Without it a corpus that
    # matched only three chunks would make "asking for 61 returns 60" pass by
    # being wrong twice.
    complete = store.search_lexical("rotation", project_id="demo", limit=CEILING_CORPUS).rows
    assert len(complete) == CEILING_CORPUS, "every chunk must match, or the boundaries move"

    hits = store.search_lexical("rotation", project_id="demo", limit=limit).rows

    assert len(hits) == expected


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

    hits = store.search_lexical("shared secret", project_id="demo").rows

    assert [h.chunk_id for h in hits] == ["mine"]


def test_drafts_are_withheld_unless_asked_for(store: SqliteIndexStore) -> None:
    store.add_chunks(
        [
            _indexable("approved", "caching policy", status="approved"),
            _indexable("draft", "caching policy", status="draft", item="architecture.draft"),
        ]
    )

    assert [h.chunk_id for h in store.search_lexical("caching", project_id="demo").rows] == [
        "approved"
    ]
    with_drafts = store.search_lexical("caching", project_id="demo", include_unapproved=True).rows
    assert len(with_drafts) == 2


# -- Dense search ------------------------------------------------------------


def test_the_nearest_vector_ranks_first(store: SqliteIndexStore) -> None:
    store.add_chunks([_indexable("near", "a"), _indexable("mid", "b", item="i2")])
    store.add_embeddings([("near", [1.0, 0.0]), ("mid", [0.7, 0.7])])

    hits = store.search_dense([0.9, 0.1], project_id="demo").rows

    assert [h.chunk_id for h in hits] == ["near", "mid"]


def test_a_barely_similar_vector_is_not_returned_at_all(store: SqliteIndexStore) -> None:
    """Hashed n-grams give almost any pair of strings a small nonzero cosine.

    Without a floor, every query returns the whole corpus ranked by accident,
    and an agent asking about payroll receives an approved architecture
    decision. "We have no such decision" has to be expressible.
    """
    store.add_chunks([_indexable("near", "a"), _indexable("far", "b", item="i2")])
    store.add_embeddings([("near", [1.0, 0.0]), ("far", [0.0, 1.0])])

    hits = store.search_dense([0.9, 0.1], project_id="demo").rows

    assert [h.chunk_id for h in hits] == ["near"], "0.11 cosine is noise, not a match"


def test_a_query_matching_nothing_returns_nothing(store: SqliteIndexStore) -> None:
    store.add_chunks([_indexable("c1", "a")])
    store.add_embeddings([("c1", [1.0, 0.0])])

    assert store.search_dense([0.0, 1.0], project_id="demo").rows == ()


def test_dense_search_respects_the_same_filters(store: SqliteIndexStore) -> None:
    store.add_chunks(
        [_indexable("mine", "a", project="demo"), _indexable("theirs", "a", project="other")]
    )
    store.add_embeddings([("mine", [1.0, 0.0]), ("theirs", [1.0, 0.0])])

    hits = store.search_dense([1.0, 0.0], project_id="demo").rows

    assert [h.chunk_id for h in hits] == ["mine"]


def test_a_corpus_embedded_by_another_model_is_skipped_not_scored(
    store: SqliteIndexStore,
) -> None:
    """Vectors of different dimension are comparable arithmetically and
    meaningless semantically. Scoring them would produce confident nonsense."""
    store.add_chunks([_indexable("two-dim", "a"), _indexable("three-dim", "b", item="i2")])
    store.add_embeddings([("two-dim", [1.0, 0.0]), ("three-dim", [1.0, 0.0, 0.0])])

    hits = store.search_dense([1.0, 0.0], project_id="demo").rows

    assert [h.chunk_id for h in hits] == ["two-dim"]


@pytest.mark.parametrize(
    "cell",
    ["eight-ch", 42, b"\x00" * 9],
    ids=["text", "integer", "nine-bytes"],
)
def test_a_vector_cell_that_cannot_be_one_is_skipped_rather_than_failing_the_search(
    store: SqliteIndexStore, cell: Any
) -> None:
    """SEC-7, ADR-0004. Bytes read from a derived file are not to be trusted.

    ``vector`` is declared ``BLOB``, which in SQLite is an affinity and not a
    constraint: a cell there can hold TEXT, an INTEGER, or a blob of the wrong
    width, and none of them can be refused by ``struct.unpack`` in a way the
    caller survives. Measured before the width check moved in front of the
    unpack: two bytes gave ``unpack requires a buffer of 0 bytes``, nine gave
    ``8 bytes``, TEXT gave ``a bytes-like object is required, not 'str'`` and an
    INTEGER gave ``object of type 'int' has no len()`` — none of them a
    ``sqlite3`` exception, so none of them reached the fallback that keys on one.

    The three cases reach two different guards, and each is sized to reach the
    one it names. TEXT and INTEGER are refused by the ``isinstance`` check —
    **eight characters** of text and not a sentence, because ``len()`` on a
    ``str`` counts characters and a longer one is turned away by the width test
    before ``isinstance`` is ever the reason. Nine bytes is refused by the width
    test's modulo: nine divided by four is two, exactly the width this query
    asks for, so a check comparing only component counts would hand those nine
    bytes to ``struct``.

    **Skipped, not raised.** A corpus embedded by another model is skipped the
    same way, so this is the answer the caller already gets for a vector that
    cannot be compared; failing the whole search over one unreadable row would
    take away the lexical answer that was never in doubt. The healthy row is what
    says the skip was a skip: an assertion that the call merely did not raise is
    satisfied by returning nothing at all.
    """
    store.add_chunks([_indexable("healthy", "a"), _indexable("corrupt", "b", item="i2")])
    store.add_embeddings([("healthy", [1.0, 0.0]), ("corrupt", [1.0, 0.0])])
    _corrupt(store.path, "UPDATE embeddings SET vector = ? WHERE chunk_id = 'corrupt'", (cell,))

    hits = store.search_dense([1.0, 0.0], project_id="demo").rows

    assert [h.chunk_id for h in hits] == ["healthy"]


def test_an_index_with_no_embeddings_returns_nothing_rather_than_failing(
    store: SqliteIndexStore,
) -> None:
    """A machine with no embedding provider still gets lexical search. The
    reported mode says so; the search does not crash."""
    store.add_chunks([_indexable("c1", "text")])

    assert store.search_dense([1.0, 0.0], project_id="demo").rows == ()


def test_a_zero_query_vector_yields_nothing(store: SqliteIndexStore) -> None:
    """Cosine similarity is undefined against a zero vector."""
    store.add_chunks([_indexable("c1", "text")])
    store.add_embeddings([("c1", [1.0, 0.0])])

    assert store.search_dense([0.0, 0.0], project_id="demo").rows == ()


def test_dense_ties_break_deterministically(store: SqliteIndexStore) -> None:
    """FR-R7. Identical vectors must not come back in storage order."""
    store.add_chunks([_indexable("b", "x", item="i1"), _indexable("a", "x", item="i2")])
    store.add_embeddings([("b", [1.0, 0.0]), ("a", [1.0, 0.0])])

    assert [h.chunk_id for h in store.search_dense([1.0, 0.0], project_id="demo").rows] == [
        "a",
        "b",
    ]


def test_vectors_survive_a_round_trip(store: SqliteIndexStore) -> None:
    """Packed as fixed little-endian float32 so an index built on one machine
    reads correctly on another."""
    store.add_chunks([_indexable("c1", "x")])
    store.add_embeddings([("c1", [0.25, -0.5, 0.125])])

    hits = store.search_dense([0.25, -0.5, 0.125], project_id="demo").rows

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

    rows = store.texts(["c1", "missing"], project_id="demo")

    assert rows["c1"]["text"] == "the body"
    assert rows["c1"]["heading"] == "Auth"
    assert "missing" not in rows


def test_a_raw_row_is_never_returned_for_another_projects_chunk(
    store: SqliteIndexStore,
) -> None:
    """The same SEC-13 predicate as `chunk_texts`, on the wider read.

    ``texts`` returns *every* column rather than one, so an unscoped version of
    it discloses strictly more than the method that was already scoped: text,
    heading, sensitivity and the owning item id. It is documented "for adapters
    and tests", which describes its callers rather than its reach -- the class
    is public and the method is not underscored, so the guard is the predicate,
    not the docstring.
    """
    store.add_chunks(
        [
            _indexable("mine", "our own retention policy", project="demo"),
            _indexable("theirs", "the other team's incident runbook", project="other"),
        ]
    )

    assert set(store.texts(["mine", "theirs"], project_id="demo")) == {"mine"}
    assert set(store.texts(["mine", "theirs"], project_id="other")) == {"theirs"}, (
        "and the control: the predicate scopes rather than merely returning less"
    )


def test_a_passage_is_never_returned_for_another_projects_chunk(
    store: SqliteIndexStore,
) -> None:
    """SEC-13, defence in depth behind the scope filter on every retriever.

    ``chunk_texts`` is what turns a ranked chunk id into the *body text* a
    caller reads, so its scoping is the last thing standing between a scoping
    bug upstream and a cross-project disclosure of knowledge prose. Every id
    reaching it today came from a search this class already scoped -- and
    "already scoped" is precisely what a scoping bug sounds like in the moment
    before it stops being true.

    That makes the predicate structurally unobservable through the search path:
    both filters would have to fail together. It is observable *here*, one layer
    down, by asking for an id the caller has no business seeing. Changing the
    ``WHERE`` to ``(project_id = ? OR 1=1)`` left the whole suite green before
    this existed.
    """
    store.add_chunks(
        [
            _indexable("mine", "our own retention policy", project="demo"),
            _indexable("theirs", "the other team's incident runbook", project="other"),
        ]
    )

    passages = store.chunk_texts(["mine", "theirs"], project_id="demo")

    assert set(passages) == {"mine"}, "an out-of-project id resolves to no text at all"
    assert "runbook" not in "".join(passages.values())


def test_a_passage_is_returned_for_the_project_that_owns_it(store: SqliteIndexStore) -> None:
    """The control that keeps the scoping test above from being satisfied by a
    lookup that returns nothing for anybody."""
    store.add_chunks([_indexable("theirs", "the other team's incident runbook", project="other")])

    assert store.chunk_texts(["theirs"], project_id="other") == {
        "theirs": "the other team's incident runbook"
    }


def test_a_passage_comes_back_as_text_even_when_the_row_holds_bytes(
    store: SqliteIndexStore,
) -> None:
    """SEC-7, ADR-0004. The port promises ``Mapping[str, str]``; the file promises
    nothing.

    A ``TEXT`` column has TEXT *affinity*, which converts a number on the way in
    and leaves a blob exactly as it found it — so bytes are the one foreign value
    a ``chunks.text`` cell can actually hold, and a partial overwrite of a
    derived, unsigned file is how it gets there. Handed straight through, that
    value leaves this adapter as ``bytes`` and becomes a result's ``excerpt``,
    where it is a ``TypeError`` at the shaper rather than a passage.

    Failing towards a readable wrong answer is deliberate here: the row is
    already unreadable, and the alternative — refusing the whole search over one
    unprintable cell — costs the caller the results that were never in doubt.

    The key half of the same coercion is **not** asserted, and the reason is
    worth writing down rather than rediscovering: ``chunk_id`` is looked up with
    a bound ``str``, and SQLite compares an INTEGER or a BLOB cell against TEXT as
    unequal whatever its affinity, so a row whose id is not text cannot be
    returned by this statement at all. The ``str()`` there guards a value this
    lookup cannot produce; the one below guards one it can.
    """
    store.add_chunks([_indexable("c1", "the body")])
    _corrupt(store.path, "UPDATE chunks SET text = ? WHERE chunk_id = 'c1'", (b"\xff\x80",))

    passages = store.chunk_texts(["c1"], project_id="demo")

    assert passages == {"c1": "b'\\xff\\x80'"}, "the passage must be text, whatever the cell holds"


def test_a_chunk_records_its_own_size(store: SqliteIndexStore) -> None:
    """Written at build time and read by nothing in production. FR-R4's budget
    moved onto the payload that actually goes out, so the store no longer
    exposes a size lookup -- but the column survives the method, and a column
    nobody reads is one nobody notices going wrong. Asserted so its removal
    stays a decision someone makes rather than a schema field nobody remembers.
    """
    store.add_chunks([_indexable("c1", "x" * 400)])

    assert store.texts(["c1"], project_id="demo")["c1"]["token_estimate"] >= 100


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

    assert store.search_lexical(query, project_id="demo").rows == (), "the word index cannot"
    assert store.search_substring(query, project_id="demo").rows, "the trigram index can"


def test_substring_matching_still_discriminates(store: SqliteIndexStore) -> None:
    """A substring index that matched everything would trade one broken search
    for another."""
    store.add_chunks([_indexable("ja", JAPANESE, heading="認証ポリシー")])

    assert store.search_substring("kubernetes", project_id="demo").rows == ()
    assert store.search_substring("課金モデル", project_id="demo").rows == ()


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

    mine = store.search_substring("トークン", project_id="demo").rows
    theirs = store.search_substring("トークン", project_id="other").rows

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

    withheld = store.search_substring("トークン", project_id="demo").rows
    asked_for = store.search_substring("トークン", project_id="demo", include_unapproved=True).rows

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

    hits = store.search_substring("トークン ローテーション", project_id="demo").rows

    assert sorted(h.chunk_id for h in hits) == ["both", "one"], "OR, as the word index does"


# -- Below the trigram floor --------------------------------------------------
#
# A trigram index stores three-character grams, so a two-character term has no
# gram to look up. Dropping such a term did not degrade a Japanese answer, it
# deleted it: 認証, 決済, 監査, 契約 are two characters each, the most common noun
# length in the language, and `unicode61` cannot segment CJK at all -- so
# `theurian index build`, the documented operation, made search strictly worse
# than having no index while reporting `count: 0, indexed: true`.

#: 認証 and 決済 are two characters; 鍵 is one. All three are below the trigram
#: floor and all three are ordinary engineering vocabulary.
SHORT_TERMS = "認証は署名付きトークンで行う。決済は監査ログに記録し、鍵は四半期ごとに交換する。"


@pytest.mark.parametrize("query", ["認証", "決済", "鍵", "監査"])
def test_a_term_too_short_to_form_a_trigram_is_still_searchable(
    store: SqliteIndexStore, query: str
) -> None:
    """The evidence was never missing -- `chunks.text` holds it -- only
    unreachable by the trigram lookup.

    Asserted against the word index too, because that is what makes this
    retriever's answer load bearing rather than redundant: `unicode61` turns the
    whole sentence into one token, so nothing else in the system can answer.
    """
    store.add_chunks([_indexable("ja", SHORT_TERMS)])

    assert store.search_lexical(query, project_id="demo").rows == (), "the word index cannot"
    assert store.search_substring(query, project_id="demo").rows, "the scan below the floor can"


def test_the_scan_below_the_floor_still_discriminates(store: SqliteIndexStore) -> None:
    """A short query that matched everything would trade one broken search for
    another -- and it is the branch with no index behind it, so nothing but the
    `LIKE` pattern is keeping it honest."""
    store.add_chunks([_indexable("ja", SHORT_TERMS)])

    assert store.search_substring("課金", project_id="demo").rows == ()
    assert store.search_substring("鍵", project_id="demo").rows, "and a term that is there is found"


def test_the_scan_below_the_floor_keeps_the_project_filter(store: SqliteIndexStore) -> None:
    """SEC-13, FR-R1, for the newest branch of the newest retriever.

    Every other substring test sends a query of three characters or more, so it
    exercises the trigram lookup and its `_scope` call — not this one. A branch
    added later must not inherit an isolation guarantee it is never asked to
    prove; that is exactly how the trigram retriever itself shipped unscoped.
    """
    store.add_chunks(
        [
            _indexable("mine", SHORT_TERMS, project="demo"),
            _indexable("theirs", SHORT_TERMS, project="other", item="architecture.other"),
        ]
    )

    assert [h.chunk_id for h in store.search_substring("認証", project_id="demo").rows] == ["mine"]
    assert [h.chunk_id for h in store.search_substring("認証", project_id="other").rows] == [
        "theirs"
    ]


def test_the_scan_below_the_floor_withholds_drafts_too(store: SqliteIndexStore) -> None:
    """The status filter is per branch, so it is asserted per branch. A draft
    reachable by a two-character query but not by a four-character one would be
    withheld from `トークン` and served for `認証`."""
    store.add_chunks(
        [
            _indexable("approved", SHORT_TERMS, status="approved"),
            _indexable("draft", SHORT_TERMS, status="draft", item="architecture.draft"),
        ]
    )

    withheld = store.search_substring("認証", project_id="demo").rows
    asked_for = store.search_substring("認証", project_id="demo", include_unapproved=True).rows

    assert [h.chunk_id for h in withheld] == ["approved"]
    assert len(asked_for) == 2


@pytest.mark.parametrize("query", ["a%", "a_"])
def test_a_like_wildcard_typed_by_a_user_is_a_character_not_a_pattern(
    store: SqliteIndexStore, query: str
) -> None:
    """A search whose result set is chosen by punctuation is not a search.

    `%` and `_` are `LIKE`'s wildcards and both are ordinary in the identifiers
    engineering knowledge is full of (`state_hash`, `100%`), so they are escaped
    rather than rejected. Unescaped, `a%` is "anything after an a" and `a_` is
    "an a and one more character", so both match `gateway` -- and this branch is
    the only place in the system where caller text becomes a `LIKE` pattern.

    Two characters rather than a lone `%`, because a lone `%` is now dropped by
    the one-character floor before escaping is ever reached: the test would have
    kept passing with the escaping deleted, which is the shape of an assertion
    that has quietly stopped testing anything.
    """
    store.add_chunks([_indexable("plain", "the gateway verifies every signed token")])

    assert store.search_substring(query, project_id="demo").rows == ()


def test_an_escaped_wildcard_still_finds_a_literal_one(store: SqliteIndexStore) -> None:
    """The control. Escaping that matched nothing at all would satisfy the test
    above while making `100%` unsearchable.

    The score is asserted too, because matching and ordering are now two uses of
    the same term: the `WHERE` gets the escaped pattern and the `ORDER BY` gets
    the raw text, and feeding the pattern to both would find this row and then
    count zero occurrences in it -- a hit that sorts as if it did not match.
    """
    store.add_chunks([_indexable("literal", "budget utilisation reached 100% last quarter")])

    hits = store.search_substring("0%", project_id="demo").rows

    assert [h.chunk_id for h in hits] == ["literal"]
    assert hits[0].score > 0, "found by the pattern, ordered by the term it came from"


def test_the_scan_orders_case_insensitively_because_it_matches_that_way(
    store: SqliteIndexStore,
) -> None:
    """`LIKE` folds ASCII case, so the occurrence count has to fold it too.

    Without `lower()` on both sides, a row matched through `AB` counts zero
    occurrences of `ab` and sorts below rows that matched it less often -- the
    ordering would contradict the selection that produced it. `b-shouting` is
    named to sort second on the tie-break, so leading is something it can only do
    by being counted.
    """
    store.add_chunks(
        [
            _indexable("a-quiet", "the ab appears once", item="architecture.quiet"),
            _indexable("b-shouting", "AB AB AB everywhere", item="architecture.shouting"),
        ]
    )

    assert [h.chunk_id for h in store.search_substring("ab", project_id="demo").rows] == [
        "b-shouting",
        "a-quiet",
    ]


def test_the_scan_below_the_floor_reads_every_column_the_trigram_index_does(
    store: SqliteIndexStore,
) -> None:
    """`_SUBSTRING_COLUMNS` is what keeps the two paths reading the same corpus.

    Nothing asserted it. Cutting the constant to ``("text",)`` left 108 tests
    green, because every chunk in every other case carries its terms in the body
    as well as the heading -- so the branch that makes a heading searchable could
    be deleted and the suite would say the search was fine.

    A heading is where a Japanese document puts its subject, and both queries
    below name the same subject: one is two characters and scans, the other is
    six and is looked up. Answering one and not the other is the asymmetry the
    constant exists to prevent, so the control is asserted beside it.
    """
    store.add_chunks([_indexable("head", "この文書は署名の話をする。", heading="認証ポリシー")])

    assert [h.chunk_id for h in store.search_substring("認証", project_id="demo").rows] == [
        "head"
    ], "the scan reads the heading"
    assert store.search_substring("認証ポリシー", project_id="demo").rows, "and so does the lookup"


def test_the_scan_below_the_floor_selects_by_relevance_not_by_creation_order(
    store: SqliteIndexStore,
) -> None:
    """The ordering key is the *selection* key, because the caller keeps a prefix.

    `chunk_id` is `<revision ULID>#<ordinal>`, so ordering by it alone ordered by
    revision creation order -- and once more than fifty chunks matched, the
    oldest fifty were the only ones a caller could ever see. Newly added
    knowledge was unreachable at any depth, and revising a document, which mints
    a newer ULID, sank it further.

    The chunk that is most about the term is deliberately the *last* one created,
    which is what makes this fail rather than pass by luck if the relevance term
    is dropped from the `ORDER BY`.
    """
    store.add_chunks(
        [
            _indexable(
                f"c{n:03d}",
                "認証" * (40 if n == 119 else 1) + f" body {n} " + "x" * 200,
                item=f"architecture.i{n}",
            )
            for n in range(120)
        ]
    )

    hits = store.search_substring("認証", project_id="demo", limit=50).rows

    assert len(hits) == 120, "the scan branch ranks everything it matched; `limit` is a floor"
    assert hits[0].chunk_id == "c119", "the densest chunk leads, though it is the newest"
    shallow = store.search_substring("認証", project_id="demo", limit=1).rows
    assert shallow[0].chunk_id == "c119", "and it leads at every limit, which is what was broken"


def test_the_scan_below_the_floor_breaks_ties_the_way_the_lookup_does(
    store: SqliteIndexStore,
) -> None:
    """Equal relevance still has to come out in one order (FR-R7).

    The tie-break is `chunk_id`, the same key the trigram lookup and the dense
    scan use, so a corpus of equally relevant chunks answers a two-character
    query exactly as it answers a four-character one. That equivalence is the
    point: this branch stands in for the lookup, so it may not select
    differently from it when neither has a relevance signal to go on.
    """
    store.add_chunks(
        [_indexable(f"c{n:02d}", "認証の規則。署名付きトークンを運ぶ。") for n in range(10)]
    )

    scanned = [h.chunk_id for h in store.search_substring("認証", project_id="demo", limit=4).rows]
    looked_up = [
        h.chunk_id for h in store.search_substring("トークン", project_id="demo", limit=4).rows
    ]

    # Compared as prefixes, because the two branches disagree about `limit` and
    # only about `limit`: the lookup truncates to four, the scan hands back its
    # whole ranking for the caller to cut. What has to match is the order.
    assert scanned[:4] == looked_up == ["c00", "c01", "c02", "c03"]
    assert len(scanned) == 10, "and the scan's own answer is the complete one"


#: A keyword query of the shape this branch exists for: five two-character
#: Japanese nouns, none of which any other retriever can answer. Deliberately
#: **not** sized from `SCAN_TERMS` -- it is the floor the constant has to clear,
#: so a test written from the constant would move with it and hold nothing.
REALISTIC_NOUNS = ("認証", "決済", "監査", "契約", "暗号")


def test_a_realistic_keyword_query_is_searched_in_full(store: SqliteIndexStore) -> None:
    """The lower edge of the cost knob, stated as a promise rather than as a
    number.

    `SCAN_TERMS` is the only reason the module's cost and relevance argument
    holds, and a test sized from it cannot hold it: shrink the constant and the
    expectation shrinks with it. Mutation showed exactly that -- an earlier
    attempt at this test sized its expected set from `SCAN_TERMS`, and 8 -> 1
    survived it, which is the same defect it was written to close.

    So the floor comes from the product instead. Five spaced two-character nouns
    is an ordinary keyword search in Japanese, every one of them is below the
    trigram floor, and `unicode61` cannot segment any of them -- this scan is the
    only retriever in the system that can answer at all. Each noun must therefore
    both find its chunk and rank it, which fails for any `SCAN_TERMS` under five
    however the constant is written.
    """
    store.add_chunks(
        [
            _indexable(f"c{n}", f"{noun}についての決定。" + "x" * 200, item=f"architecture.i{n}")
            for n, noun in enumerate(REALISTIC_NOUNS)
        ]
    )

    hits = store.search_substring(" ".join(REALISTIC_NOUNS), project_id="demo").rows

    found = {h.chunk_id: h.score for h in hits}
    for n, noun in enumerate(REALISTIC_NOUNS):
        assert f"c{n}" in found, f"{noun} is not searched at all"
        assert found[f"c{n}"] > 0.0, f"{noun} selects its chunk but cannot rank it"


def test_the_scan_spends_a_bounded_number_of_terms(store: SqliteIndexStore) -> None:
    """The upper edge: that there *is* a bound, whatever it is set to.

    One chunk per term, each matching exactly one of them, so the set that comes
    back is the set of terms the scan spent. Sized from the constant on purpose
    here -- this half holds the mechanism for any value, and the value itself is
    held from below by the test above and by the band asserted first.

    The band is the recorded decision made executable, not a change detector.
    Below five the scan stops answering an ordinary keyword query; at sixteen the
    worst legal query measured 3.34s against the 1.72s that was accepted, on the
    way back to the 4.20s this bound was introduced to cut. Retuning inside the
    band costs nothing; leaving it should mean re-running the measurement in
    `scan_statement`, not editing a number until the suite goes quiet.
    """
    assert SCAN_TERMS < MAX_QUERY_TERMS, "a bound no term can exceed is not a bound"
    assert len(REALISTIC_NOUNS) <= SCAN_TERMS <= 16, "outside the band the cost table justifies"

    terms = [chr(0x4E00 + n) * 2 for n in range(SCAN_TERMS + 1)]
    store.add_chunks(
        [_indexable(f"c{n:02d}", term, item=f"architecture.i{n}") for n, term in enumerate(terms)]
    )

    hits = store.search_substring(" ".join(terms), project_id="demo").rows

    assert {h.chunk_id for h in hits} == {f"c{n:02d}" for n in range(SCAN_TERMS)}, (
        "every term up to the bound selects, and the one past it is not spent at all"
    )


def test_a_scan_with_no_terms_is_refused_where_it_is_written() -> None:
    """The statement builder's one precondition, held rather than described.

    No caller can reach it -- `_scan_below_the_trigram_floor` returns early on an
    empty term tuple -- so this is the guard's only witness. Without it the empty
    case builds a `SELECT` whose relevance expression and `WHERE` are both empty
    strings, and the failure arrives as `sqlite3.OperationalError: near "AS":
    syntax error`, which names neither the fault nor the file it is in.
    """
    with pytest.raises(ValueError, match="at least one term"):
        scan_statement((), clauses=["chunks.project_id = ?"], scope=["demo"])


def test_every_term_the_scan_matches_on_also_ranks(store: SqliteIndexStore) -> None:
    """The invariant that made the two bounds one number (FR-R7, SEC-8).

    There used to be a wider match bound than ranking bound, described as "a far
    milder boundary" because terms past the ranking bound still selected rows.
    Under a `LIMIT` that is not milder, it is the same loss wearing a disguise:
    the row is selected at score 0.0 and then sorted below every row that any
    ranking term touched. Measured on the shipped code before this changed,
    `認証 決済 監査 契約 暗号` against a chunk holding `暗号` thirty times put it
    below chunks holding `契約` twice, and at `limit=10` against sixty of them it
    did not come back.

    So the last term the bound admits is the one under test, saturated, against
    noise that matches the first. If matching and ranking ever separate again it
    scores zero and this fails -- which is what the old shape did, and what no
    test said.
    """
    terms = [chr(0x4E00 + n) * 2 for n in range(SCAN_TERMS)]
    store.add_chunks(
        [_indexable("saturated", terms[-1] * 30, item="architecture.saturated")]
        + [_indexable(f"n{n:02d}", terms[0] * 2, item=f"architecture.n{n}") for n in range(60)]
    )

    hits = store.search_substring(" ".join(terms), project_id="demo", limit=10).rows

    assert hits[0].chunk_id == "saturated", "the last admitted term carries full weight"


@pytest.mark.parametrize("query", ["e", "a b c", "7", "#"])
def test_a_single_letter_does_not_earn_a_pass_over_the_corpus(
    store: SqliteIndexStore, query: str
) -> None:
    """The floor is decided by script, not by length, and this is the half that
    says no.

    `LIKE '%e%'` reads every row in the index to return whichever of them the
    sort happens to favour, and buys nothing: `unicode61` tokenizes `e` perfectly
    well, so the word index already answers that query in the only sense in which
    it is a query -- as a word. Sending it to the scan as well produced fifty
    results with a fused score attached, which reads as a ranked answer.
    """
    store.add_chunks([_indexable("plain", "the gateway verifies every signed token # 7")])

    assert store.search_substring(query, project_id="demo").rows == ()


def test_a_single_character_that_is_a_whole_word_still_scans(store: SqliteIndexStore) -> None:
    """And this is the half that says yes, which is what a length floor of two
    would have taken back.

    `鍵` is a noun. `unicode61` cannot segment it out of a Japanese sentence and
    the trigram index has no gram for it, so the scan is the only retriever in
    the system that can answer at all -- exactly the blackout the floor was
    lowered to fix.
    """
    store.add_chunks([_indexable("ja", SHORT_TERMS)])

    assert store.search_lexical("鍵", project_id="demo").rows == (), "the word index cannot"
    assert [h.chunk_id for h in store.search_substring("鍵", project_id="demo").rows] == ["ja"]


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

    assert store.search_lexical("the", project_id="demo").rows, "known gap, not a feature"
    assert store.search_lexical("gateway token", project_id="demo").rows


# -- One corpus, two builds (FR-R7) -------------------------------------------
#
# Every retriever here breaks a score tie on `chunk_id`, and until now nothing
# held the three that do it in SQL. Removing `, chunks.chunk_id` from
# `search_lexical`, from the trigram lookup and from `scan_statement` left the
# whole suite green -- because every fixture that could see an ordering inserted
# its chunks in ascending id order, so rowid order and chunk-id order were the
# same list and a tie-break that had been deleted still looked like it was
# working. The two tie-breaks that live in Python (`reciprocal_rank_fusion` and
# `_dense_ranking`) were held; these three were not.
#
# So the corpus is built twice from the same chunks in two different insertion
# orders, which is what an unrelated edit followed by `theurian index build`
# amounts to. Measured with each `ORDER BY` shortened in turn: not merely a
# different order but a different *set* -- the second build answered with 50 rows
# starting at `01K1TEB060...` where the first answered with 50 starting at
# `01K1TEB000...`, because the tie straddles the `LIMIT` boundary and rowid order
# decides which side of it each row falls on.

#: Enough near-identical chunks to overflow :data:`TIE_LIMIT` twice over, so the
#: tie the retrievers have to break genuinely straddles the `LIMIT` boundary. A
#: corpus at or below the limit comes back whole in any order and holds nothing.
TIE_COUNT = 120

#: What the two lookups are asked for. Absolute rather than the adapters' own
#: default, so retuning that default cannot quietly make this corpus fit.
TIE_LIMIT = 50

#: How far build B is rotated before insertion. Any non-zero rotation would do;
#: what matters is that no chunk keeps its rowid and that the first row inserted
#: is not the lowest id.
TIE_ROTATION = 60

#: One body for the whole corpus, in both writing systems.
#:
#: Identical text and identical length in every chunk, which is what makes every
#: BM25 score equal to the last bit -- the state the tie-break exists for, and
#: the state a corpus of *varying* prose never reaches. The Japanese half is not
#: decoration: `unicode61` cannot segment it, so it is the only thing the trigram
#: lookup and the sub-trigram scan can match on (ADR-0023).
TIE_BODY = "the shared gateway meters every request 共有ゲートウェイは全リクエストを計測する"


def _tie_chunk_id(number: int) -> str:
    """A chunk id whose ascending order is known, shaped like a real one.

    `chunk_id` is `<revision ULID>#<ordinal>`, so the ids a real build mints
    ascend with creation order -- which is exactly the coincidence that hid the
    missing tie-break. Here the ids ascend and the *insertion* order does not.

    Twenty-six characters before the `#`, and every one of them Crockford base32:
    no `I`, `L`, `O` or `U`. An id of the wrong shape would sort the same way and
    prove the same thing, which is precisely why it would be wrong -- the ids
    this file compares are the ids the adapter compares in production.
    """
    return f"01K1TEB{number:03d}0123456789ABCDEF#0"


def _tie_corpus() -> list[IndexableChunk]:
    return [
        _indexable(_tie_chunk_id(number), TIE_BODY, item=f"architecture.note-{number:03d}")
        for number in range(TIE_COUNT)
    ]


def _build(path: Path, chunks: Sequence[IndexableChunk]) -> SqliteIndexStore:
    built = SqliteIndexStore(path)
    built.create(index_build_id="01K1DXAA", state_hash="abc123")
    built.add_chunks(list(chunks))
    return built


def _physical_order(store: SqliteIndexStore) -> list[str]:
    """The order SQLite would hand rows back in with nothing to sort by."""
    with closing(sqlite3.connect(store.path)) as connection:
        return [row[0] for row in connection.execute("SELECT chunk_id FROM chunks ORDER BY rowid")]


@pytest.mark.parametrize(
    ("retriever", "query", "expected_rows"),
    [
        ("search_lexical", "gateway", TIE_LIMIT),
        ("search_substring", "ゲートウェイ", TIE_LIMIT),
        ("search_substring", "計測", TIE_COUNT),
    ],
    ids=["word-index", "trigram-lookup", "scan-below-the-floor"],
)
def test_two_builds_of_one_corpus_answer_a_tie_identically(
    tmp_path: Path, retriever: str, query: str, expected_rows: int
) -> None:
    """FR-R7. A rebuild must not change which rows a query returns.

    An index is derived and rebuilt often -- after every `migrate apply`, and on
    any machine that clones the repository. If a tie is settled by rowid, then
    editing an unrelated document reorders the answer to a query that has nothing
    to do with it, and past the `LIMIT` it changes *which rows come back at all*:
    a pinned result stops reproducing, and two engineers asking the same question
    are answered from different halves of the same corpus.

    The third case is the sub-trigram scan, and its row count is deliberately the
    whole corpus rather than :data:`TIE_LIMIT`: that branch has no `LIMIT` to
    straddle, because it must score every matching row before it can name the
    best of them. So it cannot lose rows to a rebuild -- only their order, which
    the caller then truncates. Held here beside the other two because the
    `limit` a caller passes is applied to whatever order this branch chose.

    Two preconditions, both able to make this pass for the wrong reason. Every
    score must tie, or relevance settles the order and the tie-break never runs.
    And the two builds must genuinely differ in physical order, or the comparison
    is a build against its own twin -- which is the state every existing fixture
    was in, and the reason three tie-breaks could be deleted with the suite still
    green.
    """
    chunks = _tie_corpus()
    first = _build(tmp_path / "a" / "theurian-index-a.sqlite", chunks)
    second = _build(
        tmp_path / "b" / "theurian-index-b.sqlite",
        [*chunks[TIE_ROTATION:], *chunks[:TIE_ROTATION]],
    )

    hits = [
        getattr(built, retriever)(query, project_id="demo", limit=TIE_LIMIT).rows
        for built in (first, second)
    ]

    assert _physical_order(first) != _physical_order(second), (
        "the two builds must disagree about rowid order, or this compares a build with itself"
    )
    assert len({row.score for row in hits[0]}) == 1, (
        "every score must tie, or relevance decides the order and the tie-break is unread"
    )
    expected = [_tie_chunk_id(number) for number in range(expected_rows)]
    assert [row.chunk_id for row in hits[0]] == expected, "ascending chunk id, and no other order"
    assert [row.chunk_id for row in hits[1]] == expected, "the same rows, from the other build"


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

    assert store.search_lexical(f"{padding} gateway", project_id="demo").rows == (), "past the cut"
    assert store.search_lexical(f"gateway {padding}", project_id="demo").rows, "before the cut"


def test_only_the_first_terms_by_length_are_spent(store: SqliteIndexStore) -> None:
    """FR-R7 and SEC-8 together: bounded, and bounded the same way every time.

    Longest-first is the selection rule, not a display order -- an English
    question front-loads its least selective words, so taking the first N as
    typed would keep `how`, `do`, `we` and discard the noun the caller believes
    they searched for. Pinned here because nothing else states it.
    """
    store.add_chunks([_indexable("c1", "gateway")])
    fillers = " ".join(f"filler{index:04d}long" for index in range(MAX_QUERY_TERMS))

    assert store.search_lexical(f"gateway {fillers}", project_id="demo").rows == (), (
        "the short, real term loses its slot to longer fillers"
    )
    assert store.search_lexical(f"gateway {fillers[:20]}", project_id="demo").rows, (
        "well inside the bound, the same term is spent"
    )


# -- Exhaustion (issue #16) --------------------------------------------------
#
# `limit` is a ceiling on both `LIMIT`-bearing retrievers, so exactly `limit`
# rows is the one answer a row count cannot interpret. The adapter resolves it by
# fetching `limit + 1` and reporting whether the extra row arrived. Both halves
# are asserted: the report, and that the extra row never ships.

EXHAUSTION_CORPUS = 6


@pytest.fixture
def six_matching_chunks(store: SqliteIndexStore) -> SqliteIndexStore:
    store.add_chunks(
        [
            _indexable(f"c{number}", f"authentication token rotation, paragraph {number}")
            for number in range(EXHAUSTION_CORPUS)
        ]
    )
    return store


@pytest.mark.parametrize("retriever", ["search_lexical", "search_substring"])
@pytest.mark.parametrize(
    ("limit", "expected_rows", "expected_exhausted"),
    [
        (1, 1, False),
        (EXHAUSTION_CORPUS - 1, EXHAUSTION_CORPUS - 1, False),
        # The boundary. Exactly as many rows as there are: a count says nothing,
        # and the probe row's absence is what makes this `True`.
        (EXHAUSTION_CORPUS, EXHAUSTION_CORPUS, True),
        (EXHAUSTION_CORPUS + 1, EXHAUSTION_CORPUS, True),
        (100, EXHAUSTION_CORPUS, True),
    ],
)
def test_a_limit_bearing_retriever_states_exhaustion_at_the_boundary(
    six_matching_chunks: SqliteIndexStore,
    retriever: str,
    limit: int,
    expected_rows: int,
    expected_exhausted: bool,
) -> None:
    """The row count is ambiguous at ``limit``; the page is not.

    ``limit == EXHAUSTION_CORPUS`` is the case the whole change is about. Six
    rows come back for an ask of six whether or not a seventh exists, so an
    adapter that reported exhaustion from ``len(rows) < limit`` would answer
    ``False`` here and send the caller back for a pass that finds nothing — and
    one that reported it from ``len(rows) <= limit`` would answer ``True`` at
    ``limit == 1`` and cost the caller five rows it never learns it lost.
    """
    page = getattr(six_matching_chunks, retriever)(
        "authentication", project_id="demo", limit=limit, include_unapproved=False
    )

    assert len(page.rows) == expected_rows
    assert page.exhausted is expected_exhausted


@pytest.mark.parametrize("retriever", ["search_lexical", "search_substring"])
def test_the_probe_row_is_never_returned(
    six_matching_chunks: SqliteIndexStore, retriever: str
) -> None:
    """``limit`` stays a true ceiling despite the fetch asking for one more.

    The probe and the cut have to move together. Fetching ``limit + 1`` and
    forgetting to slice hands the caller a row past the ceiling on every query
    that has one — silently, since a row too many looks like a longer ranking
    rather than like a bug.
    """
    page = getattr(six_matching_chunks, retriever)(
        "authentication", project_id="demo", limit=2, include_unapproved=False
    )

    assert len(page.rows) == 2, "the probe row must be dropped, not returned"
    assert page.exhausted is False


@pytest.mark.parametrize("query", ["認証", "認証は署名付きトークンで行う"])
def test_the_scan_and_the_lookup_both_answer_exhausted_when_they_hold_everything(
    store: SqliteIndexStore, query: str
) -> None:
    """One query below the trigram floor and one above it, same claim.

    The two are answered by different branches -- the scan, which has no `LIMIT`
    and is exhausted by construction, and the trigram lookup, which is exhausted
    because the probe row did not arrive. A test that exercised only the second
    would leave the branch that used to be read twice unmeasured.
    """
    store.add_chunks([_indexable("ja", "認証は署名付きトークンで行う。")])

    page = store.search_substring(query, project_id="demo", limit=50, include_unapproved=False)

    assert page.rows, "the fixture must match, or exhaustion is trivially true"
    assert page.exhausted is True


@pytest.mark.parametrize("retriever", ["search_lexical", "search_substring"])
@pytest.mark.parametrize("limit", [0, -1, -2, -8, -9])
def test_a_limit_below_one_is_refused_rather_than_sliced(
    six_matching_chunks: SqliteIndexStore, retriever: str, limit: int
) -> None:
    """A negative `limit` made `ranked[:limit]` a from-the-end slice.

    Measured before the guard, on this six-row corpus's eight-row sibling:
    `limit=-2` returned six rows with `exhausted=False` — more rows than `limit`
    on a port that calls it a true ceiling, and a claim that more was coming.
    `0` and `-1` raised `RankingError` instead, whose message says to fix the
    adapter when the adapter is right and the caller's argument is wrong.

    Both are refused at the entry now, before any query runs. Unreachable through
    the documented API — `_visible_ranking` starts at `FIRST_PASS_DEPTH` and only
    doubles — so this pins a contract rather than guarding a live path.
    """
    with pytest.raises(ValueError, match="limit must be at least 1"):
        getattr(six_matching_chunks, retriever)(
            "authentication", project_id="demo", limit=limit, include_unapproved=False
        )


@pytest.mark.parametrize("retriever", ["search_lexical", "search_substring"])
def test_a_limit_of_exactly_one_is_allowed(
    six_matching_chunks: SqliteIndexStore, retriever: str
) -> None:
    """The boundary on the permitted side, so the guard cannot creep upward.

    One is a legitimate ask — `knowledge.get` resolves a single passage — and a
    guard written as `limit < 2`, or moved to `limit <= 1` by someone tidying,
    would refuse it while every test above still passed.
    """
    page = getattr(six_matching_chunks, retriever)(
        "authentication", project_id="demo", limit=1, include_unapproved=False
    )

    assert len(page.rows) == 1
    assert page.exhausted is False


# -- The schema, pinned by version and by content (ADR-0024 decision 8) -------
#
# `INDEX_SCHEMA_VERSION - 1` is what most of the suite uses, and it cannot catch
# a bump to the wrong number or a bump made without a matching DDL change. These
# pin the literal and the two structures the M6 bump added, so a change to one
# without the other fails here rather than shipping an index whose version and
# shape disagree.


def test_a_fresh_build_reports_the_current_schema_version(store: SqliteIndexStore) -> None:
    """The literal, so a DDL change that forgets to bump -- or bumps wrong -- fails.

    Five at the time of writing. Version 3 added `chunks.derived` and
    `chunk_derivation` for a writer that did not exist yet; version 4 drops both
    and adds `nodes`, `node_derivation` and `nodes_fts` in their place (ADR-0008
    decision 5's amendment, ADR-0024 decision 8's amendment) -- a RAPTOR summary
    repeats its children's terms, so a derived row sharing `chunks_fts` would move
    `N`, `avgdl` and the per-term document frequencies under every ordinary leaf
    query. Version 5 adds `chunks.kind`, so the withdrawal purge can re-derive the
    forest from the index's own surviving rows (ADR-0008 decision 9): a Domain
    tree is keyed on `kind`, which lived only on the in-memory chunk until now.
    Asserted directly rather than against `INDEX_SCHEMA_VERSION - 1`, because a
    relative check moves with the constant and pins nothing about what the
    constant *is*.
    """
    assert store.schema_version() == INDEX_SCHEMA_VERSION
    assert store.schema_version() == 5, (
        "the index schema version changed. If a DDL change intended it, update this literal "
        "and the CHANGELOG; if not, a bump slipped in without a schema change behind it"
    )
    assert store.is_searchable() is True


#: The 14 provenance columns ADR-0008 decision 5 names, plus the three queryable
#: scope columns the amendment adds ahead of #119's predicate: `project_id`
#: (the future single-point predicate, mirroring `chunks.project_id`),
#: `sensitivity` (D6: node rows carry sensitivity, still a published label and
#: not a control -- see the Context amendment) and `status` (the build-flavor
#: column a node-table predicate will filter on, per decision 1's amendment).
#: `tree_id` already encodes the full six-component scope tuple; these three are
#: carried denormalised on the row for the same reason `chunks` carries them --
#: filtering has to happen in the same statement as the match, before ranking.
_NODE_COLUMNS = frozenset(
    {
        "node_id",
        "tree_id",
        "level",
        "node_type",
        "text",
        "content_hash",
        "summary_model",
        "summary_model_revision",
        "summary_prompt_hash",
        "embedding_model",
        "embedding_model_revision",
        "embedding_dimension",
        "source_revision_id",
        "index_build_id",
        "project_id",
        "sensitivity",
        "status",
    }
)

#: The node_derivation edge: `node_id` to either a chunk or another node, never
#: both -- a Domain node summarises Document-tree nodes, a Document node
#: summarises chunks. `source_chunk_id` and `source_node_id` are nullable and
#: mutually exclusive per row, unlike v3's `chunk_derivation` which only ever
#: pointed at chunks.
_NODE_DERIVATION_COLUMNS = frozenset({"node_id", "source_chunk_id", "source_node_id"})


def test_the_schema_carries_the_node_tables_the_purge_traversal_will_walk(
    store: SqliteIndexStore,
) -> None:
    """`nodes`, `node_derivation` and `nodes_fts`, RAPTOR's own storage at v4.

    Held at v3 by this test under the name
    `test_the_schema_carries_the_derivation_provenance_the_purge_walks`, over
    `chunks.derived` and `chunk_derivation`. ADR-0008's amendment moved the
    storage out of `chunks` entirely -- a summary's text systematically repeats
    the terms of the children it was built from, so a derived row sharing
    `chunks_fts` would move that table's BM25 collection statistics under every
    ordinary leaf query. `chunks.derived` and `chunk_derivation` are dropped
    rather than kept beside the new tables: a column nothing will ever write
    serves nothing, and keeping it would leave two provenance mechanisms of
    which one is permanently dead. Held here rather than left to the builder that
    now writes these rows (`index build --raptor`): without this test the tables
    could be dropped and every purge test over nodes would still pass on a corpus
    that never has one.
    """
    with closing(sqlite3.connect(store.path)) as connection:
        chunk_columns = {row[1] for row in connection.execute("PRAGMA table_info(chunks)")}
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        node_columns = {row[1] for row in connection.execute("PRAGMA table_info(nodes)")}
        node_derivation_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(node_derivation)")
        }
        nodes_fts_ddl = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'nodes_fts'"
        ).fetchone()

    assert "derived" not in chunk_columns, (
        "`chunks.derived` survived the v4 cutover; RAPTOR now stores its own rows apart from chunks"
    )
    assert "chunk_derivation" not in tables, (
        "`chunk_derivation` survived the v4 cutover; a table nothing will ever write serves nothing"
    )
    assert "nodes" in tables, "the node table RAPTOR summaries will live in is missing"
    assert "node_derivation" in tables, (
        "the node provenance table the transitive purge will walk is missing"
    )
    assert node_columns == _NODE_COLUMNS, (
        f"`nodes` carries the wrong columns: {sorted(node_columns)}"
    )
    assert node_derivation_columns == _NODE_DERIVATION_COLUMNS, (
        f"`node_derivation` carries the wrong columns: {sorted(node_derivation_columns)}"
    )
    assert nodes_fts_ddl is not None, (
        "`nodes_fts` is missing -- node text must have its own FTS5 index, separate from "
        "`chunks_fts`, or a summary's repeated terms move every leaf query's BM25 statistics"
    )
    assert "content='nodes'" in nodes_fts_ddl[0], (
        f"`nodes_fts` is not external-content over `nodes`: {nodes_fts_ddl[0]}"
    )


def test_a_built_index_is_always_in_wal_mode(store: SqliteIndexStore) -> None:
    """The premise `_open_read`'s pragmas rest on (ADR-0024 decision 7, M-5).

    `_open_read` runs `PRAGMA journal_mode = WAL` on a *read-only* connection.
    That only reports the mode when the file is already WAL; on a DELETE-mode
    file it tries to set it and raises `attempt to write a readonly database`.
    Every index is created WAL, so the pragma is harmless -- but that is a
    property of `create`, not of SQLite, and this is what keeps it true.
    """
    store.add_chunks([_indexable("c1", "retention and isolation per namespace")])

    with closing(sqlite3.connect(store.path)) as connection:
        mode = connection.execute("PRAGMA journal_mode").fetchone()[0]

    assert mode == "wal", (
        f"a built index is in {mode!r} mode, not WAL. `_open_read` runs "
        f"`PRAGMA journal_mode = WAL` on a read-only connection, which raises on a "
        f"non-WAL file -- so every read of this build would fail as unreadable"
    )


# -- Every read refuses a missing file rather than creating one (#103 item 5) --

#: The read surface of the store. Each opens a connection, and each must open it
#: `mode=ro`, or a bare `sqlite3.connect` conjures an empty database at a path
#: that is not there -- which is how a reaped build turned "fall back to the
#: substring scan" into "every later request fails with `no such table`". A list
#: so a read method added later that forgets `_open_read` is a missing entry
#: here, not a silent hole. Queries are long enough to reach the SQL: a
#: single-character substring query short-circuits before opening anything.
_READS_THAT_MUST_NOT_CONJURE_A_FILE: tuple[
    tuple[str, Callable[[SqliteIndexStore], object]], ...
] = (
    ("metadata", lambda store: store.metadata()),
    ("chunk_count", lambda store: store.chunk_count()),
    ("chunk_texts", lambda store: store.chunk_texts(["r0#0"], project_id="demo")),
    ("texts", lambda store: store.texts(["r0#0"], project_id="demo")),
    ("search_lexical", lambda store: store.search_lexical("token", project_id="demo", limit=10)),
    (
        "search_substring",
        lambda store: store.search_substring("token", project_id="demo", limit=10),
    ),
    ("search_dense", lambda store: store.search_dense([0.1, 0.2, 0.3], project_id="demo")),
)


@pytest.mark.parametrize(
    ("name", "read"),
    _READS_THAT_MUST_NOT_CONJURE_A_FILE,
    ids=[name for name, _ in _READS_THAT_MUST_NOT_CONJURE_A_FILE],
)
def test_a_read_of_a_missing_index_creates_no_file(
    tmp_path: Path, name: str, read: Callable[[SqliteIndexStore], object]
) -> None:
    """ADR-0024 decision 7, over the whole read surface rather than one method.

    A `theurian index gc` unlink can leave a pointer aimed at a file that is
    gone. `sqlite3.connect` on that path *creates* an empty database there, so
    every read must open `mode=ro`, which raises instead. The failure is uniform
    -- `IndexUnreadableError` -- and what is asserted here is the invariant behind
    it: no read leaves a file where there was none.
    """
    missing = tmp_path / "theurian-index-gone.sqlite"
    store = SqliteIndexStore(missing)

    with pytest.raises(IndexUnreadableError):
        read(store)

    assert not missing.exists(), (
        f"a read of a missing index created a file: {name} opened its connection without `mode=ro`"
    )


def test_the_answering_reads_report_a_safe_value_for_a_missing_index(tmp_path: Path) -> None:
    """`schema_version` and `is_searchable` answer rather than raise, and create nothing.

    The two reads a caller uses to *decide* whether a file is usable must never
    raise -- `theurian index status` promises so -- and must not create the file
    they are asked about either.
    """
    missing = tmp_path / "theurian-index-gone.sqlite"
    store = SqliteIndexStore(missing)

    assert store.schema_version() == 0
    assert store.is_searchable() is False
    assert not missing.exists(), "deciding whether a missing index is usable created it"
