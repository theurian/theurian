"""One query against two corpora, at the review search store (ADR-0030 decision 6).

``test_absence_proof.py`` and ``test_sensitivity_absence_proof.py`` state this
property over the knowledge index; this module states it over the **review**
store, which is a different machine reached through a different build. The claim
is the one ADR-0030 decision 6 inherits from T-17a in its literal words: *a store
holding the withheld records and a store that never held them must answer the
same*.

The builder below is a **deliberate second implementation**, for the reason
``test_sensitivity_absence_proof.py``'s docstring gives for its own: the corpora
of two files proving one property must be constructed independently, or a bug in
one construction masks the same bug in the other. Concretely, this module does
not import ``test_review_search_builder.py``'s fixtures -- it writes its own
evidence records, with its own vocabulary and its own withheld set -- while the
*production* seam it drives (``evidence_entries``, ``ReviewSearchBuilder``,
``SqliteReviewSearchStore``) is deliberately the real one, because a second
implementation of the composition root would prove something about this file
rather than about the product.

What "withheld" means here
--------------------------
Not a status and not a gate. ``ReviewSearchBuildRequest.withheld_record_keys``
names record keys a build **must not write**, and the builder drops them before a
load exists, so the store file has no row for them in any table. There is
therefore nothing on the read side to fail open: the property below is a property
of the artifact, and what would break it is a build that stopped filtering.

Three stores, and the third is why the first two mean anything
--------------------------------------------------------------
``W``
    the whole corpus, built while withholding :data:`WITHHELD_KEYS`.
``N``
    the corpus **minus** those records, built withholding nothing.
``F``
    the whole corpus, built withholding nothing. The **positive control**: the
    same query that ``W`` and ``N`` must answer identically returns the planted
    row here. Without it, ``W == N`` is satisfied by a build that writes nothing,
    by a query that matches nothing, and by a corpus whose planted record was
    never reachable -- three ways for an equality to hold vacuously, and this
    project has recorded suites failing all three.

What is compared
----------------
The **whole answer, serialised** -- every field of every hit, in order, plus the
excerpt and the channel it was cut from -- and not a field list, which is the
thing this shape exists to stop maintaining. A refusal is serialised into the
same string, so *error distinguishability* is part of the comparison rather than a
separate claim: a store that refused a query naming a withheld record while the
other answered it would separate here (SEC-13).

What this module does not reach
-------------------------------
- **The MCP surface.** Every call here is a store method. The response-level form
  of this argument -- the safety triple, ``_resolve``, the capability pins -- is a
  later commit's and is not claimed by anything below.
- **Durations and resource consumption.** Two members of the observable-family
  table, and neither is asserted here: a per-row term of this size is far below
  what one process's wall clock separates from noise, so a timing assertion would
  read as covering the family while asserting nothing. They stay the round's
  lenses.

Marked ``integration`` because every case lands real evidence files and opens a
real SQLite database. Writes only under ``tmp_path``.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pytest
from hypothesis import given, seed, settings
from hypothesis import strategies as st

from theurian.application.project_service import ProjectPaths
from theurian.application.review_search_builder import (
    ReviewSearchBuilder,
    ReviewSearchBuildRequest,
)
from theurian.cli.review_commands import evidence_entries
from theurian.domain.enums import ReviewThreadState
from theurian.domain.identifiers import ProjectId
from theurian.domain.knowledge import SourceAnchor
from theurian.domain.review import (
    ReviewComment,
    ReviewEvent,
    ReviewParticipant,
    ReviewResolution,
    ReviewSubmission,
    ReviewThread,
)
from theurian.domain.review_search import ReviewSearchQuery
from theurian.infrastructure.review_evidence import (
    EvidenceRecord,
    IngestionRun,
    ReviewEvidenceStore,
)
from theurian.infrastructure.sqlite.review_search_store import SqliteReviewSearchStore

pytestmark = pytest.mark.integration

PROJECT: Final = ProjectId("absence")
PROVIDER: Final = "github"
REPOSITORY: Final = "acme/order-service"

#: Crockford base32: no ``I``, ``L``, ``O`` or ``U``. ``tests/unit/test_test_fixtures.py``
#: is what catches a readable spelling that forgets.
RUN: Final = IngestionRun("01K1ABSENCE0123456789ABCDE", datetime(2026, 9, 7, 9, 0, tzinfo=UTC))

#: Every word a *visible* record's text is built from: **a to o only**.
#:
#: The split at ``o`` is load-bearing rather than decorative. ``LIKE`` folds the
#: 26 ASCII letters, so "the payload cannot appear in a visible record" has to
#: hold after folding, and splitting the alphabet gives that by construction
#: rather than by inspection of a word list.
#: :func:`test_a_visible_record_can_never_spell_a_withheld_payload` is this
#: file's own guard on its own constants.
VOCABULARY: Final = (
    "cache",
    "manifold",
    "beacon",
    "backend",
    "domain",
    "handle",
    "logical",
    "machine",
    "combine",
    "median",
    "nominal",
    "chained",
)

#: The other half of the split. Upper case only for legibility in a failure --
#: ``LIKE`` folds it to ``p``--``z``, which is what the disjointness rests on.
PAYLOAD_ALPHABET: Final = "PQRSTUVWXYZ"

#: The string no visible record can spell, planted in the withheld records.
PLANTED_PAYLOAD: Final = "PQRSTUVWXYZPQRS"

#: The withheld records' own distinguishing values, each of which is a filter a
#: caller can name: an author, a file path and a pull-request number. A store that
#: answered any of them differently from one that never held the records would be
#: telling a caller the records exist.
PLANTED_AUTHOR: Final = "USER_WXYZ"
PLANTED_DISPLAY_NAME: Final = "Reviewer PQRST"
PLANTED_FILE_PATH: Final = "src/PQRSTUV.py"
PLANTED_THREAD_ID: Final = "PRRT_WXYZ"

#: The withheld pull request's number, chosen **below** every visible one so that
#: it sorts first under ``SEARCH_ORDER`` (repository, pull request, kind, path).
#: That is what makes the ``LIMIT``-contested arm able to fail: a build that wrote
#: the withheld rows and filtered them on the way out would spend the caller's
#: first slots on them.
PLANTED_NUMBER: Final = 2

#: The visible pull requests, all numbered above the planted one.
VISIBLE_NUMBERS: Final = (10, 11, 12, 13, 14)

#: What a visible record and a withheld one both carry, so a query on it reaches
#: both -- which is what a displacement arm needs.
SHARED_TERM: Final = "cache"


def _participant(external_id: str, display_name: str = "Reviewer One") -> ReviewParticipant:
    return ReviewParticipant(provider=PROVIDER, external_id=external_id, display_name=display_name)


def _anchor(uri: str, repository: str = REPOSITORY) -> SourceAnchor:
    return SourceAnchor(provider=PROVIDER, source_uri=uri, repository=repository)


def _pull_request(  # noqa: PLR0913 - one keyword per field a search filters or matches on
    *,
    number: int,
    body: str,
    title: str = "Bound the retry budget",
    author: str = "USER_VISIBLE",
    display_name: str = "Reviewer One",
    repository: str = REPOSITORY,
) -> EvidenceRecord:
    """One landed pull request. Its record key is its number (``records.py``)."""
    return EvidenceRecord(
        provider=PROVIDER,
        repository=repository,
        anchor=_anchor(f"https://github.com/{repository}/pull/{number}", repository),
        payload=ReviewEvent(
            project_id=PROJECT,
            provider=PROVIDER,
            repository=repository,
            number=number,
            title=title,
            body=body,
            author=_participant(author, display_name),
            created_at=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
            url=f"https://github.com/{repository}/pull/{number}",
            head_commit="b" * 40,
            base_commit="c" * 40,
            head_ref_name="fix/retry-budget",
            labels=("security",),
        ),
    )


def _submission(
    *, external_id: str, number: int, body: str, author: str = "USER_VISIBLE"
) -> EvidenceRecord:
    """One landed review submission. Its record key is its provider node id."""
    return EvidenceRecord(
        provider=PROVIDER,
        repository=REPOSITORY,
        anchor=_anchor(
            f"https://github.com/{REPOSITORY}/pull/{number}#pullrequestreview-{external_id}"
        ),
        payload=ReviewSubmission(
            external_id=external_id,
            project_id=PROJECT,
            event_key=f"{PROVIDER}:{REPOSITORY}#{number}",
            author=_participant(author),
            body=body,
            state="APPROVED",
        ),
    )


def _thread(  # noqa: PLR0913 - one keyword per field a search filters or matches on
    *,
    external_id: str,
    number: int,
    bodies: Sequence[str],
    author: str = "USER_VISIBLE",
    display_name: str = "Reviewer One",
    file_path: str = "src/order.py",
) -> EvidenceRecord:
    """One landed review thread. Its record key is its provider node id."""
    return EvidenceRecord(
        provider=PROVIDER,
        repository=REPOSITORY,
        anchor=_anchor(f"https://github.com/{REPOSITORY}/pull/{number}#discussion_{external_id}"),
        payload=ReviewThread(
            external_id=external_id,
            project_id=PROJECT,
            event_key=f"{PROVIDER}:{REPOSITORY}#{number}",
            file_path=file_path,
            comments=tuple(
                ReviewComment(
                    external_id=f"IC_{external_id}_{ordinal}",
                    author=_participant(author, display_name),
                    body=body,
                    created_at=datetime(2026, 8, 1, 13, ordinal, tzinfo=UTC),
                )
                for ordinal, body in enumerate(bodies)
            ),
            state=ReviewThreadState.RESOLVED,
            resolution=ReviewResolution(
                state=ReviewThreadState.RESOLVED, resolved_by=_participant(author, display_name)
            ),
        ),
    )


def _visible_corpus() -> tuple[EvidenceRecord, ...]:
    """Everything both stores hold, in all three kinds.

    Every record carries :data:`SHARED_TERM`, so one query reaches the whole
    corpus and the ``LIMIT``-contested arm has visible rows to contest with.
    """
    return (
        *(
            _pull_request(number=number, body=f"{SHARED_TERM} manifold beacon {number}.")
            for number in VISIBLE_NUMBERS
        ),
        _submission(
            external_id="PRR_VSBL", number=10, body=f"Approving; the {SHARED_TERM} is bounded."
        ),
        _thread(
            external_id="PRRT_VSBL",
            number=10,
            bodies=(f"This {SHARED_TERM} retries forever.", "Fixed in b1c2d3."),
        ),
    )


def _withheld_corpus() -> tuple[EvidenceRecord, ...]:
    """The records ``W`` is asked not to write, and ``N`` never held.

    Two kinds and two keys, because the withheld set is matched on
    ``EvidenceRecord.record_key`` and that property answers differently per kind:
    a pull request is keyed by its **number**, a thread by its node id. A corpus
    that withheld only one shape would leave the other's key untested.
    """
    return (
        _pull_request(
            number=PLANTED_NUMBER,
            title=f"{SHARED_TERM} {PLANTED_PAYLOAD}",
            body=f"{SHARED_TERM} manifold {PLANTED_PAYLOAD}.",
            author=PLANTED_AUTHOR,
            display_name=PLANTED_DISPLAY_NAME,
        ),
        _thread(
            external_id=PLANTED_THREAD_ID,
            number=PLANTED_NUMBER,
            bodies=(f"{SHARED_TERM} {PLANTED_PAYLOAD} beacon.", f"Second {PLANTED_PAYLOAD}."),
            author=PLANTED_AUTHOR,
            display_name=PLANTED_DISPLAY_NAME,
            file_path=PLANTED_FILE_PATH,
        ),
    )


#: The keys ``W`` withholds, taken from the records' **own** ``record_key``
#: property rather than spelled out here. Spelling them would be a second
#: definition of the same rule, and the drift that makes a withheld key match one
#: spelling and miss the other is exactly what ``EvidenceEntry`` copies the value
#: across to avoid.
WITHHELD_KEYS: Final = frozenset(record.record_key for record in _withheld_corpus())


def _built(
    base: Path, name: str, records: Sequence[EvidenceRecord], *, withheld: frozenset[str]
) -> SqliteReviewSearchStore:
    """One project's evidence, landed and projected, exactly as the CLI does it.

    ``evidence_entries`` is the composition root's own nine-field mapping rather
    than a local copy: a test that mapped the records itself would go on passing
    over a root that had stopped carrying a field.
    """
    root = base / name
    (root / ".theurian").mkdir(parents=True)
    paths = ProjectPaths.of(root)
    evidence = ReviewEvidenceStore(paths.review)
    evidence.write(tuple(records), run=RUN)
    store = SqliteReviewSearchStore(paths.review_search_for("local"))
    ReviewSearchBuilder(read_evidence=evidence_entries(evidence), write=store.replace_all).build(
        ReviewSearchBuildRequest(withheld_record_keys=withheld)
    )
    return store


@dataclass(frozen=True, slots=True)
class _Pair:
    """Two stores that differ only in records no caller may be told about."""

    withholding: SqliteReviewSearchStore
    never_held: SqliteReviewSearchStore
    #: The positive control: the same corpus as :attr:`withholding`, built with
    #: nothing withheld. What says the planted records were reachable at all.
    control: SqliteReviewSearchStore


def _pair(base: Path) -> _Pair:
    visible = _visible_corpus()
    withheld = _withheld_corpus()
    return _Pair(
        withholding=_built(base, "withholding", (*visible, *withheld), withheld=WITHHELD_KEYS),
        never_held=_built(base, "never-held", visible, withheld=frozenset()),
        control=_built(base, "control", (*visible, *withheld), withheld=frozenset()),
    )


def _answer(store: SqliteReviewSearchStore, query: ReviewSearchQuery, *, text_chars: int) -> str:
    """One search's whole outcome as a single string, refusals included.

    Serialised rather than compared field by field, because the property is about
    *everything published* and a field list is what this shape exists to stop
    maintaining. A refusal serialises into the same string on purpose: "this query
    raised here and answered there" is the error-distinguishability face of the
    family (SEC-13), and folding it in means no comparison below can forget it.

    ``sort_keys`` so the string is a function of the values and not of dict
    insertion order, and ``ensure_ascii=False`` so a CJK excerpt compares as the
    text it is.
    """
    try:
        hits = store.search(query, text_chars=text_chars)
    # Deliberately every exception: what a caller observes is the refusal, whatever
    # its Python type, and a comparison that let one class escape would compare a
    # crash on one side against an answer on the other by failing rather than by
    # separating.
    except Exception as exc:
        return json.dumps(
            {"refusal": f"{type(exc).__name__}: {exc}"}, sort_keys=True, ensure_ascii=False
        )
    return json.dumps([asdict(hit) for hit in hits], sort_keys=True, ensure_ascii=False)


def _paths(store: SqliteReviewSearchStore, query: ReviewSearchQuery) -> tuple[str, ...]:
    return tuple(hit.relative_path for hit in store.search(query, text_chars=200))


#: Every query the enumerated equality is driven with, and each is here because it
#: could separate the pair in a different way.
#:
#: The text arms reach the planted payload and the shared term; the structural
#: arms name the withheld records' own author, file path and pull-request number
#: -- the three values a caller could use to ask "does this exist"; the tight
#: ``limit`` arms contest the slots, which is the only way a store that wrote the
#: withheld rows and filtered them afterwards becomes observable; and the
#: ``text_chars`` arms vary the cut, because an excerpt is the one field whose
#: size the corpus rather than the caller decides.
QUERY_BATTERY: Final[tuple[tuple[str, ReviewSearchQuery, int], ...]] = (
    ("unfiltered-wide", ReviewSearchQuery(limit=50), 200),
    ("unfiltered-first-slot", ReviewSearchQuery(limit=1), 200),
    ("unfiltered-two-slots", ReviewSearchQuery(limit=2), 200),
    ("planted-payload", ReviewSearchQuery(limit=50, text_contains=PLANTED_PAYLOAD), 200),
    ("planted-payload-one-slot", ReviewSearchQuery(limit=1, text_contains=PLANTED_PAYLOAD), 200),
    ("shared-term", ReviewSearchQuery(limit=50, text_contains=SHARED_TERM), 200),
    ("shared-term-two-slots", ReviewSearchQuery(limit=2, text_contains=SHARED_TERM), 200),
    ("shared-term-tiny-cut", ReviewSearchQuery(limit=50, text_contains=SHARED_TERM), 1),
    ("planted-author", ReviewSearchQuery(limit=50, author=PLANTED_AUTHOR), 200),
    ("planted-file-path", ReviewSearchQuery(limit=50, file_path=PLANTED_FILE_PATH), 200),
    ("planted-pull-request", ReviewSearchQuery(limit=50, pull_request=PLANTED_NUMBER), 200),
    ("planted-repository", ReviewSearchQuery(limit=50, repository=REPOSITORY), 200),
    ("resolved-threads", ReviewSearchQuery(limit=50, thread_state="resolved"), 200),
)


# -- the property ------------------------------------------------------------


def test_a_query_that_matches_a_withheld_record_answers_as_a_corpus_that_never_held_it(
    tmp_path: Path,
) -> None:
    """ADR-0030 decision 6, T-17a, SEC-13. The mandatory case, stated explicitly.

    The query's text is the payload planted **inside the withheld records**, which
    is the query a caller issues when they already suspect what is being withheld.
    The two stores must answer it byte-identically: no marker, no shifted count,
    no distinguishable refusal, no excerpt of a fragment that is not there.

    The positive control is what makes that equality mean anything. A third store
    holds the same corpus with nothing withheld, and the same query returns the
    planted rows there -- so this case cannot be satisfied by a query that matches
    nothing, a corpus whose plant was unreachable, or a build that wrote no rows
    at all. The three separated answers are asserted in the order that makes a
    failure readable: the control must differ, and the pair must not.
    """
    pair = _pair(tmp_path)
    query = ReviewSearchQuery(limit=50, text_contains=PLANTED_PAYLOAD)

    withholding = _answer(pair.withholding, query, text_chars=200)
    never_held = _answer(pair.never_held, query, text_chars=200)
    control = _answer(pair.control, query, text_chars=200)

    assert PLANTED_PAYLOAD in control, (
        "the positive control must return the planted rows for this query, or the equality "
        "below is between two stores that were never asked anything"
    )
    assert control != withholding, (
        "the control and the withholding store must differ, or nothing was withheld and this "
        "case passes over a build that did no filtering at all"
    )
    assert withholding.encode("utf-8") == never_held.encode("utf-8"), (
        "a store that withheld a record answered a query matching that record's own text "
        "differently from a store that never held it"
    )
    assert PLANTED_PAYLOAD not in withholding, "and no withheld payload may reach the answer"


@pytest.mark.parametrize(
    ("query", "text_chars"),
    [(case[1], case[2]) for case in QUERY_BATTERY],
    ids=[case[0] for case in QUERY_BATTERY],
)
def test_every_query_in_the_battery_answers_identically_over_the_two_corpora(
    tmp_path: Path, query: ReviewSearchQuery, text_chars: int
) -> None:
    """ADR-0030 decision 6, T-17a. The property over the whole published answer.

    Nothing is masked and no field is named: the comparison is the serialised
    result, so a difference in *which rows reached* a field separates here as
    loudly as a difference in a field's value -- the two faces Milestone 5 found
    were not one claim.

    Each parametrisation asserts its own precondition first: the pair must not be
    two empty answers, which is the way a query battery quietly stops testing
    anything when a corpus is edited.
    """
    pair = _pair(tmp_path)

    withholding = _answer(pair.withholding, query, text_chars=text_chars)
    never_held = _answer(pair.never_held, query, text_chars=text_chars)

    assert _answer(pair.control, query, text_chars=text_chars) != "[]", (
        "this query matches nothing even in a store that holds the whole corpus, so both "
        "sides of the equality below are empty and it holds for any implementation"
    )
    assert withholding == never_held, (
        "a published value varies with a record the caller may not be told about"
    )
    assert PLANTED_PAYLOAD not in withholding, "and no withheld payload may reach the answer"


def test_a_withheld_record_never_costs_a_visible_one_its_slot_under_the_limit(
    tmp_path: Path,
) -> None:
    """T-17's row-reach face: a slot spent on a row nobody may see.

    Equality of *values* does not settle this on its own, which is why the case is
    built rather than drawn. The withheld pull request is numbered below every
    visible one, so under ``SEARCH_ORDER`` it sorts **first**; a build that wrote
    the withheld rows and removed them on the way out would hand a caller asking
    for two rows either nothing or one, while a store that never held them returns
    two. The caller then reads off how many withheld records their query reached.

    The preconditions are asserted before the equality, in the order that makes a
    failure readable: the corpus must outnumber the bound, and the control must
    really place the withheld rows inside it.
    """
    pair = _pair(tmp_path)
    contested = ReviewSearchQuery(limit=2, text_contains=SHARED_TERM)
    withheld_paths = set(_paths(pair.control, contested)) - set(_paths(pair.never_held, contested))

    assert len(_paths(pair.control, ReviewSearchQuery(limit=50, text_contains=SHARED_TERM))) > 2, (
        "the corpus must offer more matching records than the bound, or no row can be "
        "displaced by anything"
    )
    assert withheld_paths, (
        "the control must place at least one withheld record inside the first two slots -- "
        "otherwise the gate removes nothing a visible row wanted and this case holds for any "
        "implementation"
    )
    assert _paths(pair.withholding, contested) == _paths(pair.never_held, contested), (
        "a withheld record cost a visible one its slot: the bound truncated a sequence that "
        "still counted rows the caller may not be told about"
    )
    assert len(_paths(pair.withholding, contested)) == 2, (
        "and the caller must get the rows it asked for rather than a short page"
    )


def test_asking_by_a_withheld_records_own_values_refuses_nothing_and_reveals_nothing(
    tmp_path: Path,
) -> None:
    """SEC-13. "Withheld" and "never existed" must not be two different events.

    A refusal that fires for one input and not another is a member of the
    observable family in its own right, and it is the one an equality over
    *returned rows* cannot see: an implementation that answered "no such record"
    for a key nobody ever landed and raised "you may not read that" for a withheld
    one would satisfy every row comparison while confirming the record exists.

    Four arms, and the last is the guard on the guard: a filter naming a value
    only a **visible** record carries must still return that record, or the three
    empty answers above are satisfied by a store that answers nothing.
    """
    pair = _pair(tmp_path)
    withheld_author = ReviewSearchQuery(limit=10, author=PLANTED_AUTHOR)
    absent_author = ReviewSearchQuery(limit=10, author="USER_NOBODY_EVER_LANDED")
    visible_author = ReviewSearchQuery(limit=10, author="USER_VISIBLE")

    assert _answer(pair.withholding, withheld_author, text_chars=200) == "[]", (
        "a filter naming a withheld record's own author must answer with no rows rather than "
        "with a refusal that says the record is there"
    )
    assert _answer(pair.withholding, withheld_author, text_chars=200) == _answer(
        pair.withholding, absent_author, text_chars=200
    ), "and identically to a filter naming an author no record ever carried"
    assert _answer(pair.withholding, withheld_author, text_chars=200) == _answer(
        pair.never_held, withheld_author, text_chars=200
    ), "and identically to the same filter against a store that never held the record"
    assert _paths(pair.withholding, visible_author), (
        "the guard on this guard: an author the caller may read must come back, or the three "
        "empty answers above agree because this store serves nothing"
    )


# -- physical absence, deeper than the answer --------------------------------


def _every_object(connection: sqlite3.Connection) -> tuple[tuple[str, str, str], ...]:
    """Every object in the store's schema: ``(type, name, sql)``.

    Read from ``sqlite_master`` itself rather than from
    ``REVIEW_SEARCH_TABLES``. The curated tuple is the schema's own statement of
    what it *creates* and is the right population for a reader; it is the wrong
    population for a sweep whose whole claim is exhaustiveness, because an object
    added without joining it would be excluded from the very check that exists to
    reach everything. Indexes, triggers and views are in scope for the same
    reason: each can hold a copy of a column's value.
    """
    return tuple(
        (str(row[0]), str(row[1]), str(row[2] or ""))
        for row in connection.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name")
    )


def test_no_trace_of_a_withheld_record_survives_in_any_table_or_any_byte_of_the_store(
    tmp_path: Path,
) -> None:
    """ADR-0030 decision 6, T-17a. Absence in the artifact, not in the answer.

    Search-level equality is the property; this is what makes it durable. A row
    that no query happens to select today is a row a later change selects
    tomorrow, so the sweep ranges over every *table* ``sqlite_master`` reports --
    the store's own schema listing is deliberately not the population, for the
    reason :func:`_every_object` records.

    Then the same question is asked of the **file's bytes**, which is strictly
    deeper rather than a restatement, and that is measured rather than asserted:
    on SQLite 3.47.1, a row inserted, committed and then deleted is absent from
    every ``SELECT`` over its table and still present in the file, on the page the
    delete returned to the freelist. So a table sweep answers "no query reaches
    it" and a byte sweep answers "it is not in the artifact", and the second is
    the claim ADR-0030 decision 6 makes. The store is assembled from empty on
    every build, so a byte of a record that was never written was never there, and
    finding one means it was.

    The kept corpus is asserted present in the same case, so a build that wrote
    nothing at all cannot satisfy this.
    """
    pair = _pair(tmp_path)
    traces = (
        PLANTED_PAYLOAD,
        PLANTED_AUTHOR,
        PLANTED_DISPLAY_NAME,
        PLANTED_FILE_PATH,
        PLANTED_THREAD_ID,
    )

    with closing(sqlite3.connect(pair.withholding.path)) as connection:
        tables = [name for kind, name, _sql in _every_object(connection) if kind == "table"]
        rendered = "\n".join(
            repr(tuple(row))
            for table in tables
            for row in connection.execute(f"SELECT * FROM {table}")  # noqa: S608 - names from sqlite_master
        )
    raw = pair.withholding.path.read_bytes()

    assert tables, "the sweep found no tables at all, so it looked in nothing"
    for trace in traces:
        assert trace not in rendered, f"a withheld record's {trace!r} is still in a table"
        assert trace.encode("utf-8") not in raw, (
            f"a withheld record's {trace!r} is still in the store file's bytes, in a page no "
            f"SELECT visits"
        )
    assert SHARED_TERM in rendered, "the records that were kept are missing too"
    assert PLANTED_PAYLOAD.encode("utf-8") in pair.control.path.read_bytes(), (
        "the positive control must carry the payload in its own bytes, or the byte sweep "
        "above is looking for a string this corpus never produces"
    )


def test_the_store_schema_carries_no_full_text_index_for_a_withheld_row_to_price(
    tmp_path: Path,
) -> None:
    """ADR-0030 decision 6's inherited constraint, held over the built artifact.

    The statistics-over-rows family has nothing to act through *only while there
    is no ranked surface*: an FTS5 external-content table computes ``N``,
    ``avgdl`` and per-term document frequencies over every row it holds, and a
    filter does not clean them and a tombstone does not move them. The schema
    module states the absence in prose; this is the assertion that the file a
    build really produces still has it.

    Checked over ``sqlite_master`` -- both the object types and the DDL text --
    so an FTS5 table added to the DDL fails here, and so do the shadow tables one
    creates. A virtual table is reported by ``sqlite_master`` as an ordinary
    ``table`` whose ``sql`` carries ``USING``, which is why the DDL text is
    searched rather than only the names.
    """
    pair = _pair(tmp_path)

    with closing(sqlite3.connect(pair.withholding.path)) as connection:
        objects = _every_object(connection)

    assert objects, "the schema is empty, so this assertion ranges over nothing"
    for kind, name, sql in objects:
        assert "using fts" not in sql.casefold(), (
            f"`{name}` is a full-text index; a ranked surface prices its results over "
            f"collection statistics a withheld row would still contribute to (T-17a), so it "
            f"owes the physical purge ADR-0030 decision 6 names"
        )
        assert not name.casefold().endswith(("_content", "_docsize", "_idx", "_data", "_config")), (
            f"`{name}` is an FTS5 shadow table, so a full-text index exists even though no "
            f"`USING fts` reached this schema's own DDL"
        )
        assert kind in {"table", "index"}, (
            f"`{name}` is a {kind}; a trigger or a view can copy a column's value into a "
            f"second place, and the withholding argument ranges over what the file holds"
        )


# -- what the withheld key means ---------------------------------------------


def test_a_withheld_key_withholds_that_key_in_every_repository_one_build_spans(
    tmp_path: Path,
) -> None:
    """The measured scope of ``withheld_record_keys``, documented rather than assumed.

    A pull request's record key is its **number** (``records.py``), which is
    unique inside a repository and not across a project -- while one build reads
    every repository directory under ``.theurian/review/`` and projects them into
    one store. Withholding ``"42"`` therefore withholds pull request 42 in *every*
    repository the project has ingested.

    That is **over**-withholding, never under-withholding, so the coarseness fails
    in the safe direction; ``ReviewSearchBuildRequest.withheld_record_keys``
    records it as a deliberate, recorded choice rather than an oversight, and #575
    lands the setter with the repository scoping the question needs. This test is
    what makes the recorded behaviour a measured one: it fails if a build stops
    spanning repositories *or* if the key silently becomes repository-scoped, and
    either of those is a change somebody must decide rather than discover.

    The positive control is the same build with nothing withheld: both pull
    requests are there, so the emptiness above is the withholding and not a corpus
    that never spanned two repositories.
    """
    other = "acme/billing"
    corpus = (
        _pull_request(number=42, body=f"{SHARED_TERM} in order-service."),
        _pull_request(number=42, body=f"{SHARED_TERM} in billing.", repository=other),
        _pull_request(number=43, body=f"{SHARED_TERM} kept everywhere."),
    )

    withholding = _built(tmp_path, "spanning", corpus, withheld=frozenset({"42"}))
    control = _built(tmp_path, "spanning-control", corpus, withheld=frozenset())

    assert {(record.repository, record.pull_request) for record in control.dump()} == {
        (REPOSITORY, 42),
        (other, 42),
        (REPOSITORY, 43),
    }, "one build must span both repositories, or this case says nothing about the key's scope"
    assert {(record.repository, record.pull_request) for record in withholding.dump()} == {
        (REPOSITORY, 43)
    }, (
        "withholding record key `42` must remove pull request 42 from every repository the "
        "build spans -- coarse, and coarse in the fail-closed direction"
    )


# -- the generated arm -------------------------------------------------------

#: ``deadline=None`` because one example lands evidence files and builds three
#: SQLite stores; ``database=None`` because the default example database writes
#: ``.hypothesis/`` into whatever directory pytest was launched from, which for
#: this repository is the repository.
#:
#: ``derandomize`` and an explicit :data:`EXAMPLE_SEED` together, for the reason
#: ``test_absence_proof.py`` measured: ``derandomize`` alone derives its seed from
#: the test's source, so a prose-only docstring edit re-rolls every example and
#: silently changes what was checked.
_GENERATED = settings(deadline=None, derandomize=True, database=None)

#: Fixed so a docstring edit cannot silently change what was checked. Any constant
#: would do; this one is the issue number.
EXAMPLE_SEED: Final = 479


@dataclass(frozen=True, slots=True)
class _Case:
    """One generated pair, before either store exists."""

    visible_bodies: tuple[str, ...]
    #: Each withheld record's body, already carrying :attr:`term` so the query
    #: reaches it in a corpus that holds it.
    withheld_bodies: tuple[str, ...]
    payloads: tuple[str, ...]
    term: str
    query_text: str
    limit: int
    text_chars: int


def _sentence() -> st.SearchStrategy[str]:
    return st.lists(st.sampled_from(VOCABULARY), min_size=3, max_size=8).map(
        lambda words: " ".join(words) + "."
    )


def _assemble(  # noqa: PLR0913 - one parameter per generated knob
    *,
    visible_bodies: list[str],
    payloads: list[str],
    term: str,
    names_the_payload: bool,
    limit: int,
    text_chars: int,
) -> _Case:
    """Turn the generated knobs into a pair, resolving the one dependency.

    Every withheld body is made to carry ``term``, so the query reaches the
    withheld records whichever way ``names_the_payload`` fell -- which is what
    makes the positive control able to separate. Resolved here rather than
    filtered away with ``assume``, so no example is discarded for a reason this
    module controls.
    """
    return _Case(
        visible_bodies=tuple(visible_bodies),
        withheld_bodies=tuple(f"{term} {payload}." for payload in payloads),
        payloads=tuple(payloads),
        term=term,
        query_text=payloads[0] if names_the_payload else term,
        limit=limit,
        text_chars=text_chars,
    )


def _cases() -> st.SearchStrategy[_Case]:
    """One generated pair: a visible corpus, a withheld one, and a query.

    Built in one ``flatmap`` because the guarantee is relational -- the query's
    term is sampled from the words the *visible* corpus really contains, so a
    query that reaches both halves is structural rather than hoped for.
    """

    def with_term(bodies: list[str]) -> st.SearchStrategy[_Case]:
        words = sorted({word for body in bodies for word in body.replace(".", "").split()})
        return st.builds(
            _assemble,
            visible_bodies=st.just(bodies),
            payloads=st.lists(
                st.text(alphabet=PAYLOAD_ALPHABET, min_size=6, max_size=12),
                min_size=1,
                max_size=3,
                unique=True,
            ),
            term=st.sampled_from(words),
            names_the_payload=st.booleans(),
            limit=st.sampled_from((1, 2, 5, 50)),
            text_chars=st.sampled_from((1, 12, 200)),
        )

    return st.lists(_sentence(), min_size=2, max_size=6).flatmap(with_term)


def _generated_pair(base: Path, case: _Case) -> _Pair:
    visible = tuple(
        _pull_request(number=10 + ordinal, body=body)
        for ordinal, body in enumerate(case.visible_bodies)
    )
    withheld = tuple(
        _pull_request(number=ordinal + 2, body=body, author=PLANTED_AUTHOR)
        for ordinal, body in enumerate(case.withheld_bodies)
    )
    keys = frozenset(record.record_key for record in withheld)
    return _Pair(
        withholding=_built(base, "withholding", (*visible, *withheld), withheld=keys),
        never_held=_built(base, "never-held", visible, withheld=frozenset()),
        control=_built(base, "control", (*visible, *withheld), withheld=frozenset()),
    )


@seed(EXAMPLE_SEED)
@settings(_GENERATED, max_examples=12)
@given(case=_cases())
def test_no_generated_query_separates_a_withholding_store_from_one_that_never_held_the_records(
    tmp_path_factory: pytest.TempPathFactory, case: _Case
) -> None:
    """ADR-0030 decision 6, T-17a. The property over corpora nobody chose.

    The enumerated battery above covers the queries a person thought of. This
    covers the ones nobody did: what the visible records say, how many there are,
    what the withheld ones say, which of them the query reaches, how tight the
    bound is and how short the cut is are all generated, and the whole serialised
    answer must still be equal.

    ``tmp_path_factory`` rather than ``tmp_path`` because a function-scoped
    fixture under ``@given`` is reused across examples, which hypothesis reports
    as a health-check failure and this suite promotes to an error
    (``filterwarnings = error``).

    Every example asserts its own bite before its equality: the control must
    return more rows than the pair does, which is exactly the statement that the
    query reached records the withholding build refused to write.
    """
    pair = _generated_pair(tmp_path_factory.mktemp("absence"), case)
    query = ReviewSearchQuery(limit=case.limit, text_contains=case.query_text)

    withholding = _answer(pair.withholding, query, text_chars=case.text_chars)
    never_held = _answer(pair.never_held, query, text_chars=case.text_chars)

    assert set(_paths(pair.control, ReviewSearchQuery(limit=50, text_contains=case.query_text))) > (
        set(_paths(pair.never_held, ReviewSearchQuery(limit=50, text_contains=case.query_text)))
    ), (
        "the query must reach the withheld records in a corpus that holds them, or the "
        "equality below compares two stores that were asked about nothing"
    )
    assert withholding == never_held, (
        "a published value varies with a record the caller may not be told about"
    )
    for payload in case.payloads:
        assert payload not in withholding, "and no withheld payload may reach the answer"


# -- guards on this file's own premises --------------------------------------


def test_a_visible_record_can_never_spell_a_withheld_payload() -> None:
    """The premise every equality above rests on, checked rather than assumed.

    ``LIKE`` folds the 26 ASCII letters, so if a visible record's text could
    contain a payload substring, a pair would separate for a reason that is not a
    leak -- and the obvious response to that failure is to relax the assertion
    that caught it. This file has its own copy of the vocabulary, so it needs its
    own copy of the guard: the module it was modelled on cannot notice a
    ``p``--``z`` letter added here.
    """
    visible = {character for word in (*VOCABULARY, SHARED_TERM) for character in word.casefold()}
    payload = set(PAYLOAD_ALPHABET.casefold())

    assert not visible & payload, (
        f"the alphabets overlap on {sorted(visible & payload)}; a visible record could then "
        f"carry a payload substring and separate the pair without anything leaking"
    )
    assert PLANTED_PAYLOAD.upper() == PLANTED_PAYLOAD, "the planted payload must fold to itself"
    assert min(VISIBLE_NUMBERS) > PLANTED_NUMBER, (
        "the withheld pull request must sort before every visible one, or the "
        "LIMIT-contested arm cannot show a slot being spent on it"
    )
