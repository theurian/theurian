"""The review search store, driven against a real SQLite file (ADR-0030 slice 3).

The module-level drivers for the adapter itself: what a structural filter selects,
what a text filter selects, what a caller's text is allowed to *mean*, and what
the store refuses rather than answering. The two-corpora disclosure closure and
the ADR-owed battery are a different slice's and are deliberately not here.

Four properties, each of which fails on its own:

* **A filter selects exactly its matches.** Every structural filter is driven
  against a corpus where at least one record matches and at least one does not,
  so a filter that silently degraded to "everything" fails as loudly as one that
  degraded to "nothing".
* **A caller's search text is text.** ``%``, ``_``, ``"``, ``*``, ``OR`` and
  ``NEAR`` each mean something in *some* query language and none of them means
  anything here, so each is driven as a literal against a corpus that contains it
  and a corpus that does not.
* **Two spellings cannot cross the SQLite boundary as themselves**, and both are
  refused at query construction rather than answered with rows the caller did not
  ask for.
* **A store this build did not write is not served.** A stale stamp, a missing
  metadata row and a missing file are three refusals rather than three empty
  answers -- "nothing has been built here" and "the build found nothing" are
  different facts.

Marked ``integration`` because every case opens a real SQLite database. Writes
only under ``tmp_path``.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from theurian.domain.errors import DomainError
from theurian.domain.review_search import (
    ReviewSearchLoad,
    ReviewSearchQuery,
    ReviewSearchRecord,
    ReviewTextChannel,
    ReviewTextFragment,
)
from theurian.infrastructure.sqlite.review_search_schema import (
    REVIEW_SEARCH_SCHEMA_VERSION,
    REVIEW_SEARCH_TABLES,
)
from theurian.infrastructure.sqlite.review_search_store import (
    ReviewSearchStoreError,
    SqliteReviewSearchStore,
)

pytestmark = pytest.mark.integration

ONE_INSTANT = "2026-09-07T09:00:00.000000+00:00"


def _record(  # noqa: PLR0913 - one keyword per filterable column, keyword-only
    *,
    relative_path: str,
    record_key: str = "42",
    kind: str = "review-thread",
    repository: str = "acme/order-service",
    pull_request: int | None = 42,
    thread_state: str | None = "resolved",
    file_path: str | None = "src/order.py",
    author: str = "MDQ6VXNlcjE=",
    participants: tuple[str, ...] = ("MDQ6VXNlcjE=",),
    texts: tuple[ReviewTextFragment, ...] = (),
) -> ReviewSearchRecord:
    return ReviewSearchRecord(
        relative_path=relative_path,
        record_key=record_key,
        kind=kind,
        provider="github",
        repository=repository,
        pull_request=pull_request,
        thread_state=thread_state,
        file_path=file_path,
        source_uri=f"https://github.com/{repository}/pull/{pull_request}",
        author_external_id=author,
        author_display_name="Reviewer One",
        participant_ids=participants,
        texts=texts,
        last_seen_run_id="01K1AAAAAA01234567890ABCDE",
        last_seen_at=ONE_INSTANT,
    )


def _comment(body: str) -> ReviewTextFragment:
    return ReviewTextFragment(channel=ReviewTextChannel.COMMENT, content=body)


def _built(tmp_path: Path, *records: ReviewSearchRecord) -> SqliteReviewSearchStore:
    store = SqliteReviewSearchStore(tmp_path / "state" / "theurian-review-local.sqlite")
    store.replace_all(ReviewSearchLoad(records=records))
    return store


def _paths(store: SqliteReviewSearchStore, query: ReviewSearchQuery) -> tuple[str, ...]:
    return tuple(hit.relative_path for hit in store.search(query, text_chars=200))


# -- what a structural filter selects -----------------------------------------


def test_each_structural_filter_returns_exactly_the_records_it_names(tmp_path: Path) -> None:
    """AC-1: five filters, each against a corpus that both matches and does not.

    Every case names the whole expected set rather than asserting that the wanted
    record is present, so a predicate that widened to "every record" fails here --
    which is the direction a dropped ``WHERE`` clause fails in, and the one an
    ``in`` assertion cannot see.
    """
    wanted = _record(
        relative_path="a/review-thread/1.json",
        record_key="T1",
        repository="acme/order-service",
        pull_request=42,
        thread_state="resolved",
        file_path="src/order.py",
        author="USER_A",
        participants=("USER_A", "USER_B"),
    )
    other_repository = _record(
        relative_path="b/review-thread/2.json",
        record_key="T2",
        repository="acme/billing",
        pull_request=7,
        thread_state="outdated",
        file_path="src/billing.py",
        author="USER_C",
        participants=("USER_C",),
    )
    same_repository_other_pull_request = _record(
        relative_path="a/pull-request/9.json",
        record_key="9",
        kind="pull-request",
        repository="acme/order-service",
        pull_request=9,
        thread_state=None,
        file_path=None,
        author="USER_C",
        participants=("USER_C",),
    )
    store = _built(tmp_path, wanted, other_repository, same_repository_other_pull_request)

    assert _paths(store, ReviewSearchQuery(limit=10, repository="acme/order-service")) == (
        "a/pull-request/9.json",
        "a/review-thread/1.json",
    )
    assert _paths(store, ReviewSearchQuery(limit=10, pull_request=42)) == (
        "a/review-thread/1.json",
    )
    assert _paths(store, ReviewSearchQuery(limit=10, thread_state="resolved")) == (
        "a/review-thread/1.json",
    )
    assert _paths(store, ReviewSearchQuery(limit=10, file_path="src/order.py")) == (
        "a/review-thread/1.json",
    )
    # `USER_B` never opened anything: it is only a participant, which is the
    # difference between "who replied here" and "who opened this".
    assert _paths(store, ReviewSearchQuery(limit=10, author="USER_B")) == (
        "a/review-thread/1.json",
    )
    assert _paths(store, ReviewSearchQuery(limit=10)) == (
        "b/review-thread/2.json",
        "a/pull-request/9.json",
        "a/review-thread/1.json",
    )


def test_a_text_filter_selects_the_record_whose_fragment_carries_the_substring(
    tmp_path: Path,
) -> None:
    """AC-1's text half, matched against *any* fragment rather than the first.

    The matching comment is the second one in its thread, so a store that searched
    only a denormalised "first comment" column would report the record absent --
    the shape the separate ``review_texts`` table exists to prevent.
    """
    thread = _record(
        relative_path="a/review-thread/1.json",
        record_key="T1",
        texts=(_comment("This retries forever."), _comment("署名付きトークンを持つ呼び出しだけ")),
    )
    unrelated = _record(
        relative_path="a/review-thread/2.json",
        record_key="T2",
        texts=(_comment("Looks good to me."),),
    )
    store = _built(tmp_path, thread, unrelated)

    assert _paths(store, ReviewSearchQuery(limit=10, text_contains="署名付きトークン")) == (
        "a/review-thread/1.json",
    )
    assert _paths(store, ReviewSearchQuery(limit=10, text_contains="nothing here")) == ()


def test_the_excerpt_is_the_matching_fragment_cut_by_the_read(tmp_path: Path) -> None:
    """The excerpt names *why* a record matched, and its size is the read's to bound.

    Two claims in one case, because they are one statement: the excerpt is the
    fragment the filter matched (not the record's first), and it arrives already
    cut -- so a surface above never receives more than it asked for whatever the
    corpus holds.
    """
    store = _built(
        tmp_path,
        _record(
            relative_path="a/review-thread/1.json",
            texts=(
                ReviewTextFragment(channel=ReviewTextChannel.COMMENT, content="a" * 5_000),
                _comment("the budget is now bounded"),
            ),
        ),
    )

    (hit,) = store.search(ReviewSearchQuery(limit=10, text_contains="budget"), text_chars=12)

    assert hit.excerpt == "the budget i"
    assert hit.excerpt_channel == ReviewTextChannel.COMMENT.value

    # With no text filter, the excerpt is the record's first fragment, cut the same.
    (unfiltered,) = store.search(ReviewSearchQuery(limit=10), text_chars=4)
    assert unfiltered.excerpt == "aaaa"


def test_the_served_order_is_total_and_two_searches_agree(tmp_path: Path) -> None:
    """``LIMIT`` truncates a defined sequence rather than one SQLite may vary.

    Three records that tie on every ordering key but the last, so the assertion is
    about ``relative_path`` breaking the tie rather than about the leading keys
    happening to be distinct.
    """
    store = _built(
        tmp_path,
        *(
            _record(relative_path=f"a/review-thread/{leaf}.json", record_key=leaf)
            for leaf in ("c", "a", "b")
        ),
    )

    first = _paths(store, ReviewSearchQuery(limit=2))

    assert first == ("a/review-thread/a.json", "a/review-thread/b.json")
    assert first == _paths(store, ReviewSearchQuery(limit=2))


# -- what a caller's search text is allowed to mean ---------------------------


@pytest.mark.parametrize(
    "needle",
    ["%", "_", '"', "*", " OR ", "NEAR", "\\", "100%_of*"],
    ids=["percent", "underscore", "quote", "star", "or", "near", "backslash", "mixed"],
)
def test_operator_shaped_search_text_is_matched_literally(tmp_path: Path, needle: str) -> None:
    """AC-4: every character that means something *somewhere* means nothing here.

    Each needle is driven against two records: one whose comment contains it and
    one whose comment does not. Both directions matter and they fail differently.
    A needle read as a wildcard selects the record that does *not* contain it --
    ``%`` and ``_`` are the two LIKE gives meaning to, and dropping the escaping
    makes ``%`` select everything. A needle read as FTS syntax would raise or
    select on token boundaries instead; there is no FTS5 table on this path at
    all, which is what makes ``*``, ``OR``, ``NEAR`` and ``"`` inert rather than
    escaped.
    """
    store = _built(
        tmp_path,
        _record(
            relative_path="a/review-thread/1.json",
            record_key="T1",
            texts=(_comment(f"before {needle} after"),),
        ),
        _record(
            relative_path="a/review-thread/2.json",
            record_key="T2",
            texts=(_comment("nothing special at all"),),
        ),
    )

    assert _paths(store, ReviewSearchQuery(limit=10, text_contains=needle)) == (
        "a/review-thread/1.json",
    )


@pytest.mark.parametrize(
    ("value", "fragment"),
    [("with\x00nul", "NUL"), ("lone \ud800 surrogate", "unpaired surrogate")],
    ids=["nul", "surrogate"],
)
def test_text_that_cannot_cross_the_sqlite_boundary_is_refused_at_construction(
    value: str, fragment: str
) -> None:
    """AC-4's other half: refused, not silently answered on a truncated prefix.

    Neither value can be matched as the text it is, and each fails in its own way.
    A NUL binds without raising and then truncates the comparison on both sides --
    measured on SQLite 3.47.1, the pattern ``'%with\\x00ZZZZ%'`` matches a stored
    ``'with\\x00nul'``, so the caller's text is matched by its pre-NUL prefix. An
    unpaired surrogate has no UTF-8 encoding, so the driver raises
    ``UnicodeEncodeError`` before SQLite is called -- an exception no
    ``except sqlite3.Error`` catches.

    Refused at construction, so no such query object exists to be handed to a
    store, and the refusal is about the caller's own input rather than about
    anything the store holds.
    """
    with pytest.raises(DomainError) as excinfo:
        ReviewSearchQuery(limit=10, text_contains=value)

    assert fragment in str(excinfo.value)


def test_a_query_without_a_positive_bound_cannot_be_constructed() -> None:
    """An unbounded read is not expressible, rather than merely not issued."""
    with pytest.raises(DomainError):
        ReviewSearchQuery(limit=0)


def test_a_non_positive_text_cut_is_refused_rather_than_serving_empty_excerpts(
    tmp_path: Path,
) -> None:
    """``substr`` answers a non-positive length with ``''``, which is a wrong answer.

    A surface above, seeing a value inside its own bound, would publish that empty
    string as the whole fragment -- so the one parameter value that turns a bounded
    answer into a wrong one is the one the read refuses.
    """
    store = _built(
        tmp_path, _record(relative_path="a/review-thread/1.json", texts=(_comment("x"),))
    )

    with pytest.raises(DomainError):
        store.search(ReviewSearchQuery(limit=1), text_chars=0)


# -- what the store refuses rather than answering -----------------------------


def test_a_missing_store_raises_rather_than_answering_with_no_rows(tmp_path: Path) -> None:
    """ "Nothing was built here" and "the build found nothing" are different facts.

    A caller that cannot tell them apart reports one as the other, so the missing
    file is a refusal carrying the rebuild remedy rather than an empty tuple.
    """
    store = SqliteReviewSearchStore(tmp_path / "state" / "theurian-review-local.sqlite")

    with pytest.raises(ReviewSearchStoreError) as excinfo:
        store.search(ReviewSearchQuery(limit=1), text_chars=10)

    assert "theurian review build" in excinfo.value.remedy


def test_a_store_stamped_by_a_superseded_schema_is_refused(tmp_path: Path) -> None:
    """A stale store is refused in the same connection its rows would be read from.

    The stamp is moved by hand rather than by an older build, because there is no
    older build to run: what the assertion is about is that the comparison happens
    at all and that a mismatch refuses instead of serving rows a different schema
    produced.
    """
    store = _built(
        tmp_path, _record(relative_path="a/review-thread/1.json", texts=(_comment("x"),))
    )
    with closing(sqlite3.connect(store.path)) as connection:
        connection.execute(
            "UPDATE review_search_metadata SET review_search_schema_version = ?",
            (REVIEW_SEARCH_SCHEMA_VERSION + 1,),
        )
        connection.commit()

    with pytest.raises(ReviewSearchStoreError) as excinfo:
        store.search(ReviewSearchQuery(limit=1), text_chars=10)

    assert "superseded" in str(excinfo.value)


def test_a_rebuild_over_one_load_leaves_a_logically_identical_store(tmp_path: Path) -> None:
    """Wholesale by construction: two writes of one load are one store.

    Compared by dumped value rather than by the file's bytes, which legitimately
    drift under identical logical content.
    """
    load = ReviewSearchLoad(
        records=(
            _record(relative_path="a/review-thread/1.json", texts=(_comment("first"),)),
            _record(
                relative_path="a/review-thread/2.json", record_key="T2", texts=(_comment("second"),)
            ),
        )
    )
    store = SqliteReviewSearchStore(tmp_path / "state" / "theurian-review-local.sqlite")

    store.replace_all(load)
    once = store.dump()
    store.replace_all(load)

    assert store.dump() == once == load.records


def test_a_record_dropped_from_the_load_is_gone_from_every_table(tmp_path: Path) -> None:
    """The write is wholesale, so the previous build's rows do not survive it.

    Asserted over **every** table this schema creates rather than over the search
    result, because a soft-deleted row that no query happens to select is still a
    row a later change could select. :data:`REVIEW_SEARCH_TABLES` is the store's
    own statement of that population, and the query below compares it against
    ``sqlite_master`` so a table added without joining the tuple fails here.
    """
    kept = _record(relative_path="a/review-thread/1.json", record_key="T1", texts=(_comment("a"),))
    dropped = _record(
        relative_path="a/review-thread/2.json",
        record_key="DROPPED",
        author="GONE_USER",
        participants=("GONE_USER",),
        texts=(_comment("gone text"),),
    )
    store = _built(tmp_path, kept, dropped)

    store.replace_all(ReviewSearchLoad(records=(kept,)))

    with closing(sqlite3.connect(store.path)) as connection:
        on_disk = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert on_disk == set(REVIEW_SEARCH_TABLES)
        for table in REVIEW_SEARCH_TABLES:
            rows = connection.execute(f"SELECT * FROM {table}").fetchall()  # noqa: S608
            rendered = "\n".join(repr(tuple(row)) for row in rows)
            for trace in ("a/review-thread/2.json", "DROPPED", "GONE_USER", "gone text"):
                assert trace not in rendered, f"{table} still carries {trace!r}"
