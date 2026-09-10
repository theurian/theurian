"""What a caller's text is allowed to *mean* at the review search store (ADR-0030).

The store promises that a search string is **text**: there is no FTS5 table
behind it, no tokenizer and no query language, so ``*``, ``OR``, ``NEAR`` and
``"`` are ordinary characters, ``%`` and ``_`` are escaped before the pattern is
bound, and every filter value leaves this codebase as a bound parameter. Those
are four separate claims and each fails on its own, so each is driven separately
here.

Every case pairs a record that **does** spell the needle with a decoy chosen so
that the *specific* misreading would select it
--------------------------------------------------------------------------------
A needle driven against a corpus of one record proves only that something
matched. What separates "matched as text" from "matched as syntax" is what the
needle would have selected had it been read the other way, so :data:`NEEDLES`
pairs each needle with the decoy its own misreading reaches: ``%`` against a
record it would select as a wildcard, ``retry OR budget`` against a record an FTS
``OR`` would select, ``100%`` against ``100X``, ``a_c`` against ``abc``. A needle
whose decoy were unrelated would pass under either reading.

Two of the four claims have no shipped way to fail, and that is recorded
-------------------------------------------------------------------------
The FTS-shaped needles -- ``NEAR(...)``, ``^prefix``, ``body:term``, ``{a b}`` --
cannot be misread by the *current* implementation, because there is no matcher on
this path that could read them: they are inert by construction rather than
escaped. They are here as the guard on the day a ranked surface lands, and the
honest statement of their reach is that the mutation which reddens them is the
one that stops the text predicate filtering at all. The metacharacter needles and
the bound-parameter cases have shipped failure modes and are driven at them.

What each section holds
-----------------------
* **Search text.** Operator-shaped needles, the two ``LIKE`` metacharacters and
  the escape character itself; the cut bounding the projection and never the
  match; and the exact bound on case folding, which is a published claim rather
  than an implementation detail.
* **Filter values.** A structural filter is an equality, so a wildcard in one is
  a character; and a SQL statement in one is a value, asserted by looking for the
  tables afterwards rather than by reading the code that builds the clause.
* **What cannot cross the boundary at all.** Refused at construction, over
  **every** text-typed field of the query rather than over the one a test author
  remembered -- reached by reflection, so a field added tomorrow is covered by the
  change that adds it.

Marked ``integration``: every case opens a real SQLite database. Writes only
under ``tmp_path``.
"""

from __future__ import annotations

import fnmatch
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, get_type_hints

import pytest

from theurian.application.project_service import ProjectPaths
from theurian.application.review_search_builder import (
    ReviewSearchBuilder,
    ReviewSearchBuildRequest,
)
from theurian.cli.review_commands import evidence_entries, evidence_paths
from theurian.domain.enums import ReviewThreadState
from theurian.domain.errors import DomainError
from theurian.domain.identifiers import ProjectId
from theurian.domain.knowledge import SourceAnchor
from theurian.domain.review import (
    ReviewComment,
    ReviewParticipant,
    ReviewResolution,
    ReviewThread,
)
from theurian.domain.review_search import (
    ReviewSearchLoad,
    ReviewSearchQuery,
    ReviewSearchRecord,
    ReviewTextChannel,
    ReviewTextFragment,
)
from theurian.infrastructure.review_evidence import (
    EvidenceRecord,
    IngestionRun,
    ReviewEvidenceStore,
)
from theurian.infrastructure.sqlite.review_search_schema import REVIEW_SEARCH_TABLES
from theurian.infrastructure.sqlite.review_search_store import SqliteReviewSearchStore

pytestmark = pytest.mark.integration

PROJECT: Final = ProjectId("bound-input")
PROVIDER: Final = "github"
REPOSITORY: Final = "acme/order-service"
ONE_INSTANT: Final = "2026-09-07T09:00:00.000000+00:00"

#: Crockford base32 -- no ``I``, ``L``, ``O`` or ``U``.
RUN: Final = IngestionRun("01K1BND00000000000000ABCDE", datetime(2026, 9, 7, 9, 0, tzinfo=UTC))

#: The value used wherever the claim is "this is a parameter, not SQL". It is a
#: complete statement: a filter that reached the statement text would either
#: destroy the table named in it or fail to parse, and both are observable
#: afterwards without reading a line of the code that built the clause.
SQL_STATEMENT: Final = "'; DROP TABLE review_records; --"


def _record(  # noqa: PLR0913 - one keyword per filterable column, keyword-only
    *,
    relative_path: str,
    record_key: str = "T1",
    repository: str = REPOSITORY,
    thread_state: str | None = "resolved",
    file_path: str | None = "src/order.py",
    author: str = "USER_A",
    texts: tuple[str, ...] = (),
) -> ReviewSearchRecord:
    return ReviewSearchRecord(
        relative_path=relative_path,
        record_key=record_key,
        kind="review-thread",
        provider=PROVIDER,
        repository=repository,
        pull_request=42,
        thread_state=thread_state,
        file_path=file_path,
        source_uri="https://example.invalid/1",
        author_external_id=author,
        author_display_name="Reviewer One",
        participant_ids=(author,),
        texts=tuple(
            ReviewTextFragment(channel=ReviewTextChannel.COMMENT, content=body) for body in texts
        ),
        last_seen_run_id=RUN.run_id,
        last_seen_at=ONE_INSTANT,
    )


def _built(tmp_path: Path, *records: ReviewSearchRecord) -> SqliteReviewSearchStore:
    store = SqliteReviewSearchStore(tmp_path / "state" / "theurian-review-local.sqlite")
    store.replace_all(ReviewSearchLoad(records=records))
    return store


def _paths(store: SqliteReviewSearchStore, query: ReviewSearchQuery) -> tuple[str, ...]:
    return tuple(hit.relative_path for hit in store.search(query, text_chars=400))


def _filtered(field_name: str, value: str) -> ReviewSearchQuery:
    """One query carrying ``value`` in the named filter, and nothing else.

    The field is chosen at run time -- by a parametrisation, or by the reflected
    :data:`TEXT_FIELDS` -- so the keyword dict is typed loosely here and checked
    where it matters, by ``ReviewSearchQuery``'s own construction.
    """
    arguments: dict[str, Any] = {field_name: value}
    return ReviewSearchQuery(limit=10, **arguments)


MATCHING: Final = "a/review-thread/spells-it.json"
DECOY: Final = "a/review-thread/decoy.json"


# -- what a caller's search text is allowed to mean ---------------------------

#: ``(needle, what the record that spells it says, what the decoy says)``.
#:
#: The decoy is the load-bearing column. Each is the record the needle's *own*
#: misreading would select and nothing else would: a wildcard reading of ``%``
#: selects any record at all, so the decoy need only exist; a wildcard reading of
#: ``100%`` selects ``100X``; an FTS ``OR`` selects a record carrying either term;
#: an FTS prefix search selects a longer word with the same stem. A decoy chosen
#: without that argument would pass under either reading, which is how a battery
#: of this shape stops testing anything.
NEEDLES: Final[tuple[tuple[str, str, str, str], ...]] = (
    # The two characters LIKE gives meaning to, and the escape itself. Each decoy
    # is what the unescaped pattern would select.
    ("percent", "%", "before % after", "nothing special at all"),
    ("underscore", "_", "before _ after", "nothing special at all"),
    ("backslash", "\\", "before \\ after", "100% no backslash here"),
    ("percent-in-context", "100%", "we cut 100% of it", "we cut 100X of it"),
    ("underscore-in-context", "a_c", "the a_c token", "the abc token"),
    ("escaped-percent", "\\%", "a literal \\% sequence", "a literal 100% sign"),
    ("both-metacharacters", "50%_off", "sale 50%_off today", "sale 50X-off today"),
    # Quoting. A single quote is the one that separates "bound parameter" from
    # "string concatenation": interpolated, it would end the literal and the
    # search would raise rather than answer.
    ("double-quote", '"', 'before " after', "nothing special at all"),
    ("single-quote", "'", "before ' after", "nothing special at all"),
    ("apostrophe-in-word", "don't", "don't retry that", "do not retry that"),
    # FTS5-shaped operators. Inert here because there is no matcher that could
    # read them; each decoy is what the corresponding FTS reading would select.
    ("star", "*", "before * after", "nothing special at all"),
    ("fts-prefix", "retr*", "the retr* spelling", "retry the request"),
    ("fts-or", "retry OR budget", "retry OR budget, verbatim", "budget on its own"),
    # The decoy carries both terms and not the phrase, which is what an FTS `AND`
    # selects. It must not carry the phrase in ANY case, because LIKE folds ASCII:
    # "retry and budget separately" reads as the needle and was measured doing so.
    ("fts-and", "retry AND budget", "retry AND budget, verbatim", "retry it; the budget is fine"),
    ("fts-not", "NOT budget", "NOT budget, verbatim", "retry only, nothing else here"),
    ("fts-near", "NEAR(retry budget, 5)", "NEAR(retry budget, 5) verbatim", "retry near budget"),
    ("fts-phrase", '"exact phrase"', 'an "exact phrase" here', "an exact phrase here"),
    ("fts-column", "body:retry", "body:retry verbatim", "retry inside the body"),
    ("fts-initial", "^retry", "^retry verbatim", "retry at the start"),
    ("fts-column-set", "{title body}", "{title body} verbatim", "the title body pair"),
    # A whole SQL statement, as search text rather than as a filter value.
    ("sql-statement", SQL_STATEMENT, f"quoted: {SQL_STATEMENT}", "nothing special at all"),
)


@pytest.mark.parametrize(
    ("needle", "spelled", "decoy"),
    [(case[1], case[2], case[3]) for case in NEEDLES],
    ids=[case[0] for case in NEEDLES],
)
def test_operator_shaped_search_text_selects_only_the_record_that_spells_it(
    tmp_path: Path, needle: str, spelled: str, decoy: str
) -> None:
    """ADR-0030 decision 6. A search string is text, and the decoy says so.

    Both directions are asserted in one case and they fail differently. The record
    that spells the needle must come back, so a needle that matched *nothing* --
    an over-eager escape, a pattern that never terminates -- fails here. The decoy
    must not, so a needle read as a wildcard, as an FTS operator or as SQL fails
    here too, and the decoy is chosen per needle to be exactly what that misreading
    would select.

    The whole expected set is named rather than a membership test, because a
    predicate that widened to "every record" is the direction a dropped or
    degraded ``WHERE`` fails in, and ``in`` cannot see it.
    """
    store = _built(
        tmp_path,
        _record(relative_path=MATCHING, record_key="SPELLED", texts=(spelled,)),
        _record(relative_path=DECOY, record_key="DECOY", texts=(decoy,)),
    )

    assert needle in spelled, "the matching record must actually spell the needle"
    # Case-folded, because the match is: `LIKE` folds ASCII, so a decoy that
    # differs from the needle only in case is not a decoy at all. Written after a
    # decoy reading "retry and budget separately" was measured spelling the needle
    # `retry AND budget` and turning this parametrisation red for the wrong reason.
    assert needle.casefold() not in decoy.casefold(), (
        "the decoy must not spell the needle in any case, or it is not a decoy"
    )
    assert _paths(store, ReviewSearchQuery(limit=10, text_contains=needle)) == (MATCHING,)


def test_a_match_beyond_the_excerpt_cut_still_selects_its_record(tmp_path: Path) -> None:
    """The cut bounds the projection, never the match.

    ``substr`` is applied in the ``SELECT`` list while the ``EXISTS`` predicate
    names the whole ``content`` column, and the difference is what stops a small
    ``text_chars`` from manufacturing false absences: a caller asking for a
    twelve-character excerpt is asking about the size of what comes back, not
    about how much of the corpus was searched.

    The needle sits five thousand characters into the fragment, far past any cut a
    serving surface would ask for, and the excerpt that comes back is asserted to
    be the cut prefix -- so this holds both halves rather than the one that would
    pass if matching and cutting were the same expression.
    """
    buried = "z" * 5_000 + " the buried needle"
    store = _built(tmp_path, _record(relative_path=MATCHING, texts=(buried,)))

    (hit,) = store.search(
        ReviewSearchQuery(limit=10, text_contains="the buried needle"), text_chars=12
    )

    assert hit.relative_path == MATCHING
    assert hit.excerpt == "z" * 12, (
        "the excerpt must be the cut prefix of the fragment that matched, so the cut is the "
        "projection's bound and not the predicate's"
    )


def test_substring_matching_folds_ascii_case_and_nothing_else(tmp_path: Path) -> None:
    """The published bound on case folding, pinned so it cannot quietly widen.

    ``SqliteReviewSearchStore.search`` states it: ``LIKE`` folds the 26 ASCII
    letters and leaves every other code point exact, so ``retry`` finds ``Retry``
    and ``é`` does not find ``É``. That is a limitation a caller has to know
    about, and a docstring saying it is not the same as a test that fails when it
    stops being true -- in either direction. Measured on SQLite 3.47.1: ``'É
    accent' LIKE '%é%'`` is 0, and so is the reverse.

    Both arms matter. If the fold widened to Unicode, the accented arm would start
    matching and the published sentence would be wrong; if it narrowed, the ASCII
    arm would stop and every caller's lower-case query would go blind.
    """
    store = _built(
        tmp_path,
        _record(relative_path=MATCHING, record_key="ASCII", texts=("Retry The Call",)),
        _record(relative_path=DECOY, record_key="ACCENT", texts=("ÉCLAIR",)),
    )

    assert _paths(store, ReviewSearchQuery(limit=10, text_contains="retry the call")) == (
        MATCHING,
    ), "ASCII case is folded, so a lower-case query finds a capitalised fragment"
    assert _paths(store, ReviewSearchQuery(limit=10, text_contains="éclair")) == (), (
        "and nothing else is folded: an accented query does not find its own capital, which "
        "is what the store's docstring publishes rather than a claim of case insensitivity"
    )


# -- what a caller's filter value is allowed to mean --------------------------

#: The five filters a caller supplies text to, paired with the value that would be
#: SQL if it reached the statement, and the column the planted record carries it
#: in. ``pull_request`` is absent because it is an ``int`` and carries no text.
TEXT_FILTERS: Final[tuple[str, ...]] = ("repository", "thread_state", "file_path", "author")


@pytest.mark.parametrize("filter_name", TEXT_FILTERS)
def test_a_sql_statement_in_a_filter_is_a_value_and_alters_nothing(
    tmp_path: Path, filter_name: str
) -> None:
    """SEC-7's spirit at the SQL boundary: every caller value is a bound parameter.

    Asserted by consequence rather than by reading the clause builder. The value
    is a complete statement that drops the store's own record table, so a filter
    reaching the statement text either destroys that table or fails to parse --
    and both are visible afterwards, in ``sqlite_master`` and in a second search
    that must still answer.

    The planted record carries the statement *as its column value*, so the
    equality still has to select it. That is the half a "nothing was destroyed"
    assertion alone would miss: a store that answered every filter with no rows
    would satisfy the safety claim while serving nothing.
    """
    planted = _record(
        relative_path=MATCHING,
        record_key="PLANTED",
        repository=SQL_STATEMENT if filter_name == "repository" else REPOSITORY,
        thread_state=SQL_STATEMENT if filter_name == "thread_state" else "resolved",
        file_path=SQL_STATEMENT if filter_name == "file_path" else "src/order.py",
        author=SQL_STATEMENT if filter_name == "author" else "USER_A",
        texts=("planted",),
    )
    ordinary = _record(relative_path=DECOY, record_key="ORDINARY", texts=("ordinary",))
    store = _built(tmp_path, planted, ordinary)

    selected = _paths(store, _filtered(filter_name, SQL_STATEMENT))

    with closing(sqlite3.connect(store.path)) as connection:
        surviving = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    assert selected == (MATCHING,), (
        f"a `{filter_name}` filter must select the record whose column holds that exact value, "
        f"and only that one"
    )
    assert surviving == set(REVIEW_SEARCH_TABLES), (
        f"a `{filter_name}` filter carrying a SQL statement reached the statement text: the "
        f"store now holds {sorted(surviving)}"
    )
    # A set rather than a sequence, and only here: `SEARCH_ORDER` leads on
    # `repository`, and one parametrisation plants the statement *in* that column,
    # so the two records swap places between cells for an honest reason. What this
    # line asserts is that both rows survived the query, which is a membership
    # question; the ordering is pinned in `test_review_search_store.py`.
    assert set(_paths(store, ReviewSearchQuery(limit=10))) == {MATCHING, DECOY}, (
        "and both records must still be there afterwards"
    )


#: The two structural filters a wildcard is driven through, with the value that
#: spells one and the ordinary value a wildcard reading would sweep up with it.
#:
#: **Two rows because the clause builder has two shapes, not one.** ``repository``
#: is a column equality in the ``WHERE``; ``author`` is an equality inside an
#: ``EXISTS`` sub-select over ``review_participants``, written on its own line and
#: reachable by its own edit. A case that drove only the first left the second's
#: ``=`` free to become a ``LIKE`` with every test in this file still green.
_WILDCARD_FILTERS: Final[tuple[tuple[str, str, str], ...]] = (
    ("repository", "acme/%", REPOSITORY),
    ("author", "USER_%", "USER_A"),
)


def _swept_up_by_a_wildcard_reading(pattern: str, ordinary: str) -> bool:
    """Whether ``ordinary`` would be selected if ``pattern`` were read as LIKE.

    ``%`` is LIKE's "any sequence" and ``_`` its "any single character", which are
    ``fnmatch``'s ``*`` and ``?``. Computed rather than asserted by hand, because
    the whole value of the decoy is that it is *exactly* what the misreading
    selects: a decoy no wildcard reading would reach makes the case pass under
    either implementation.
    """
    return fnmatch.fnmatchcase(ordinary, pattern.replace("%", "*").replace("_", "?"))


@pytest.mark.parametrize(
    ("filter_name", "spelled", "ordinary"),
    _WILDCARD_FILTERS,
    ids=[row[0] for row in _WILDCARD_FILTERS],
)
def test_a_wildcard_in_a_structural_filter_is_a_character_and_not_a_pattern(
    tmp_path: Path, filter_name: str, spelled: str, ordinary: str
) -> None:
    """A structural filter is an equality, so nothing in it is a metacharacter.

    Worth its own case because the two halves of this store escape for *different*
    reasons and one of them could be lost without the other noticing: the text
    filter is a ``LIKE`` whose pattern is escaped, and the structural filters are
    ``=`` comparisons where a metacharacter is inert by construction. A change that
    turned a structural filter into a ``LIKE`` -- to make it "more useful" -- would
    pass every text-side test in this file and let ``acme/%`` select every
    repository the project has ingested, or ``USER_%`` every author.

    Driven over both clause shapes (:data:`_WILDCARD_FILTERS`). The decoy's
    quality is computed rather than claimed: it must be a value the wildcard
    reading really would sweep up, or the case holds whichever operator the clause
    carries.
    """
    planted: dict[str, Any] = {filter_name: spelled}
    plain: dict[str, Any] = {filter_name: ordinary}
    literal = _record(relative_path=MATCHING, record_key="LITERAL", texts=("literal",), **planted)
    decoy = _record(relative_path=DECOY, record_key="ORDINARY", texts=("ordinary",), **plain)
    store = _built(tmp_path, literal, decoy)

    assert _swept_up_by_a_wildcard_reading(spelled, ordinary), (
        f"{ordinary!r} is not what a wildcard reading of {spelled!r} would select, so the "
        f"decoy cannot tell an equality from a LIKE"
    )
    assert _paths(store, _filtered(filter_name, spelled)) == (MATCHING,), (
        f"`{spelled}` must select the record whose `{filter_name}` is spelled that way and "
        f"not every record the same value would match as a pattern"
    )
    assert _paths(store, _filtered(filter_name, ordinary)) == (DECOY,), (
        f"and the ordinary `{filter_name}` must still select its own record, so the equality "
        f"above is not satisfied by a filter that stopped matching anything"
    )


# -- what cannot cross the boundary at all ------------------------------------

#: Every field of :class:`ReviewSearchQuery` whose value is caller-supplied text,
#: **reflected** rather than listed.
#:
#: ``ReviewSearchQuery.__post_init__`` itself reflects too, over
#: :func:`~theurian.domain.review_search.texts_of` -- the same walk
#: ``review_search_builder``'s build-time check consumes, so the two cannot
#: drift into checking different populations. This test derives its own
#: population a second, independent way -- from the field's declared type
#: rather than from that production walk -- so a regression back to a
#: hand-written tuple, or a narrowing of the shared walk to stop reaching some
#: shape, still has something outside both to notice.
TEXT_FIELDS: Final = tuple(
    name for name, hint in get_type_hints(ReviewSearchQuery).items() if hint == (str | None)
)

#: The two spellings a Python ``str`` can carry that SQLite cannot be handed as
#: the text it is, with the word each refusal must contain.
UNTRANSPORTABLE: Final[tuple[tuple[str, str, str], ...]] = (
    ("nul", "with\x00nul", "NUL"),
    ("surrogate", "lone \ud800 surrogate", "unpaired surrogate"),
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(case[1], case[2]) for case in UNTRANSPORTABLE],
    ids=[case[0] for case in UNTRANSPORTABLE],
)
def test_every_text_field_of_a_query_refuses_a_value_sqlite_cannot_be_handed(
    value: str, expected: str
) -> None:
    """SEC-13. The refusal is about the caller's input, over the whole population.

    Both spellings are reachable from a JSON-RPC caller -- JSON carries
    ``\\u0000`` and an unpaired ``\\ud800`` -- and they fail differently. A NUL
    binds without raising and then truncates the comparison on both sides, so the
    caller's text is matched by its pre-NUL prefix, which is a **wrong** answer
    rather than a smaller one. A lone surrogate has no UTF-8 encoding at all, so
    the driver raises ``UnicodeEncodeError`` before SQLite is called -- an
    exception no ``except sqlite3.Error`` catches.

    Driven over :data:`TEXT_FIELDS`, which is reflected off the type rather than
    written down: ``__post_init__`` reflects too, through the same shared walk
    the review-search builder's own check consumes, but this test derives its
    population independently -- from the field's declared type -- so a
    regression back to a hand-written tuple still has something outside it to
    catch. The population is asserted to be the five that exist today, so the
    day a field is added this test names it rather than silently covering four
    of six.

    Refused at construction, so no such query object exists to be handed to a
    store, and the refusal says nothing about what the store holds.
    """
    assert set(TEXT_FIELDS) == {
        "repository",
        "thread_state",
        "author",
        "file_path",
        "text_contains",
    }, (
        f"ReviewSearchQuery's text-typed fields are {sorted(TEXT_FIELDS)}; a field added here "
        f"is a new way for a caller's text to reach SQLite and needs this refusal"
    )
    for field_name in TEXT_FIELDS:
        with pytest.raises(DomainError) as excinfo:
            _filtered(field_name, value)
        assert expected in str(excinfo.value), (
            f"`{field_name}` refused for the wrong reason: {excinfo.value}"
        )
        assert field_name in str(excinfo.value), "and the refusal must name the field"


def test_a_query_carrying_only_transportable_text_is_constructed(tmp_path: Path) -> None:
    """The guard on the guard: the refusals above must not be refusing everything.

    Every other control character, the BOM, a non-character and a lone combining
    mark cross the boundary intact and match nothing in particular -- the
    population ``untransportable_reason`` deliberately does not decline, stated as
    a property rather than as a list of bad characters. Without this case, a
    construction check that raised on any string at all would satisfy every
    assertion above.
    """
    awkward = "tab\there\r\nbell\a bom﻿ noncharacter￾ marḱ"
    store = _built(tmp_path, _record(relative_path=MATCHING, texts=(awkward,)))

    assert _paths(store, ReviewSearchQuery(limit=10, text_contains=awkward)) == (MATCHING,), (
        "text made only of transportable code points must build a query and match itself"
    )
    for field_name in TEXT_FIELDS:
        _filtered(field_name, awkward)


# -- the same text, through the real landing and build ------------------------


def _landed_thread(paths: ProjectPaths, body: str) -> ReviewEvidenceStore:
    store = ReviewEvidenceStore(paths.review)
    store.write(
        (
            EvidenceRecord(
                provider=PROVIDER,
                repository=REPOSITORY,
                anchor=SourceAnchor(
                    provider=PROVIDER,
                    source_uri=f"https://github.com/{REPOSITORY}/pull/42#discussion_r1",
                    repository=REPOSITORY,
                ),
                payload=ReviewThread(
                    external_id="PRRT_kwDOA1",
                    project_id=PROJECT,
                    event_key=f"{PROVIDER}:{REPOSITORY}#42",
                    file_path="src/order.py",
                    comments=(
                        ReviewComment(
                            external_id="IC_1",
                            author=ReviewParticipant(
                                provider=PROVIDER,
                                external_id="USER_A",
                                display_name="Reviewer One",
                            ),
                            body=body,
                            created_at=datetime(2026, 8, 1, 13, 0, tzinfo=UTC),
                        ),
                    ),
                    state=ReviewThreadState.RESOLVED,
                    resolution=ReviewResolution(
                        state=ReviewThreadState.RESOLVED,
                        resolved_by=ReviewParticipant(
                            provider=PROVIDER, external_id="USER_A", display_name="Reviewer One"
                        ),
                    ),
                ),
            ),
        ),
        run=RUN,
    )
    return store


def test_operator_shaped_text_survives_the_landing_and_the_build_byte_for_byte(
    tmp_path: Path,
) -> None:
    """The claim over the whole path, not over a load somebody assembled by hand.

    Every case above writes a load directly, which is right for a claim about the
    ``WHERE`` clause and wrong for a claim about the product: between an upstream
    comment and a stored fragment sit a JSON codec, a file on disk and a
    projection, and each is a place a metacharacter could be normalised,
    re-escaped or dropped. ``content`` is untrusted author text held **opaque**
    (T-3, SEC-15) -- never parsed, never tokenized, never normalised -- and this
    is the assertion that it really arrives that way.

    The body carries both ``LIKE`` metacharacters, the escape character, both
    quote characters, FTS-shaped operators, a whole SQL statement and CJK in one
    string, so the comparison is over the value and not over a character class.
    """
    body = (
        "100%_of \\ the \"quotes\" and 'single' * OR NEAR(a b, 5) "
        f"{SQL_STATEMENT} 署名付きトークンを持つ"
    )
    root = tmp_path / "repo"
    (root / ".theurian").mkdir(parents=True)
    paths = ProjectPaths.of(root)
    evidence = _landed_thread(paths, body)
    store = SqliteReviewSearchStore(paths.review_search_for("local"))
    ReviewSearchBuilder(
        read_evidence=evidence_entries(evidence),
        list_evidence_paths=evidence_paths(paths.review),
        write=store.replace_all,
    ).build(ReviewSearchBuildRequest(withheld_record_keys=frozenset()))

    (stored,) = store.dump()
    (hit,) = store.search(
        ReviewSearchQuery(limit=10, text_contains="100%_of \\ the"), text_chars=len(body)
    )

    assert [fragment.content for fragment in stored.texts] == [body], (
        "the stored fragment must be the comment's own bytes: nothing on this path parses, "
        "normalises or re-escapes untrusted author text"
    )
    assert hit.excerpt == body, "and a search must serve it back unchanged"
    assert _paths(store, ReviewSearchQuery(limit=10, text_contains=SQL_STATEMENT)) == (
        stored.relative_path,
    ), "and the SQL statement inside it is found as the text it is"
