"""``SqliteReviewSearchStore.relative_path_for``: the by-key read (ADR-0033 decision 5).

The store's *other* read. ``search`` is filtered and answers rows;
``relative_path_for`` is keyed and answers one path or ``None``, and it exists
because ``review.generateKnowledgeCandidate`` has to turn a caller's
``(repository, recordKey)`` into a stored file. That makes it a **disclosure
surface**, which is why its misses are pinned as carefully as its hits: the
withheld class #575 creates arrives on exactly this path, and what keeps withheld
and absent indistinguishable is that both are ``None`` *by construction* --
``ReviewSearchBuilder`` drops a withheld key before a row exists, so there is no
flag a later read could consult.

Four things can make this answer ``None``, and a test for only the first would
leave three shapes of "the caller learns something" untested:

1. **no such key** -- nothing in this repository carries it;
2. **another repository's key** -- the pair is matched on *both* columns, so a key
   that exists next door is a miss here. A lookup keyed on ``record_key`` alone
   would be a cross-tenant read, and it would look exactly like a hit;
3. **the empty key** -- a miss, though *not* because of anything this method does:
   no row can carry an empty key, so the arm below pins the invariant that makes
   it so rather than an observable that would hold under any implementation;
4. **more than one matching row** -- ``relative_path`` is the primary key and
   nothing makes ``(repository, record_key)`` unique, so a duplicate is a miss
   too. Answering with either row would make what the caller does next depend on
   which one SQLite handed back first.

**The duplicate is driven through the shipped builder, not planted.** It is
reachable without a hand-authored store, and the route is the two key spaces
meeting: ``EvidenceRecord.record_key`` is a pull request's **number** and a
thread's **node id**, and nothing stops a provider minting a thread whose node id
is the decimal string of some pull request's number. Planting two rows directly
would test the SQL and prove nothing about whether the collision can occur; this
builds both records, runs the real projection over them, and asserts the store
that came out answers ``None``.

Every store here is built by ``ReviewSearchBuilder`` over evidence
``ReviewEvidenceStore`` wrote, so the relative paths compared against are the ones
``infrastructure/review_evidence/layout.py`` derives rather than strings this
module composed -- a test that wrote its own expected path would keep passing over
a layout that had moved.

Marked ``integration``: real SQLite files and a real evidence tree under
``tmp_path``. No project, no daemon, no socket.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pytest
import review_candidate_fixtures as corpus

from theurian.application.review_search_builder import (
    ReviewSearchBuilder,
    ReviewSearchBuildRequest,
)
from theurian.cli.review_commands import evidence_entries, evidence_fingerprints
from theurian.domain.enums import ReviewThreadState
from theurian.domain.errors import InvariantViolationError
from theurian.domain.identifiers import ProjectId
from theurian.domain.knowledge import SourceAnchor
from theurian.domain.review import (
    ReviewComment,
    ReviewParticipant,
    ReviewResolution,
    ReviewThread,
)
from theurian.domain.review_search import ReviewSearchRecord
from theurian.infrastructure.review_evidence import EvidenceRecord, ReviewEvidenceStore
from theurian.infrastructure.sqlite.review_search_schema import REVIEW_SEARCH_SCHEMA_VERSION
from theurian.infrastructure.sqlite.review_search_store import (
    ReviewSearchStoreError,
    SqliteReviewSearchStore,
)

pytestmark = pytest.mark.integration

#: A repository this corpus does not hold. Used for the cross-repository miss, so
#: the key being looked up is one that genuinely exists -- next door.
OTHER_REPOSITORY: Final = "acme/billing-service"


def _built(tmp_path: Path, *records: EvidenceRecord) -> SqliteReviewSearchStore:
    """Land ``records`` and project them, through the shipped writer and builder.

    ``evidence_entries`` and ``evidence_fingerprints`` are the composition root's
    own mapping, imported rather than re-implemented: a helper that mapped the
    records itself would keep answering over a root that had stopped carrying a
    field.
    """
    review_root = tmp_path / "review"
    evidence = ReviewEvidenceStore(review_root)
    evidence.write(records, run=corpus.INGESTION_RUN)
    store = SqliteReviewSearchStore(tmp_path / "state" / "theurian-review-local.sqlite")
    ReviewSearchBuilder(
        read_evidence=evidence_entries(evidence),
        list_evidence_fingerprints=evidence_fingerprints(review_root),
        write=store.replace_all,
    ).build(ReviewSearchBuildRequest(withheld_record_keys=frozenset()))
    return store


def _thread_record(external_id: str, *, repository: str = corpus.REPOSITORY) -> EvidenceRecord:
    """One landed thread whose ``record_key`` is ``external_id``.

    Built here rather than taken from the corpus module because two arms need a
    node id that module does not mint: the decimal string that collides with a
    pull request's key, and a key under a second repository.
    """
    reviewer = ReviewParticipant(
        provider="github", external_id="MDQ6_kwDOzzzzzzzzzzzz", display_name="rev"
    )
    return EvidenceRecord(
        provider="github",
        repository=repository,
        anchor=SourceAnchor(
            provider="github",
            source_uri=f"https://github.com/{repository}/pull/"
            f"{corpus.PULL_REQUEST_CI_PASSED}#discussion_r9",
            repository=repository,
            file_path=corpus.FILE_PATH,
        ),
        payload=ReviewThread(
            external_id=external_id,
            project_id=ProjectId("demo"),
            event_key=f"github:{repository}#{corpus.PULL_REQUEST_CI_PASSED}",
            file_path=corpus.FILE_PATH,
            comments=(
                ReviewComment(
                    external_id=f"PRRC_kwDO{'z' * 12}",
                    author=reviewer,
                    body="Take the lock after the read.",
                    created_at=datetime(2026, 9, 1, 10, 0, tzinfo=UTC),
                ),
            ),
            state=ReviewThreadState.RESOLVED,
            # `ReviewThread.__post_init__` refuses a resolved thread with no
            # resolution, so the state and the record of it move together.
            resolution=ReviewResolution(state=ReviewThreadState.RESOLVED, resolved_by=reviewer),
        ),
    )


def _keyless_row() -> ReviewSearchRecord:
    """A projected row with no record key, which the type refuses to construct.

    Every other field is well-formed, so what the refusal is about is the key
    alone: a row that differed in two ways could be refused for the other one and
    the arm calling this would read that as the invariant holding.
    """
    return ReviewSearchRecord(
        relative_path="a/review-thread/keyless.json",
        record_key="",
        kind="review-thread",
        provider="github",
        repository=corpus.REPOSITORY,
        pull_request=corpus.PULL_REQUEST_CI_PASSED,
        thread_state="resolved",
        file_path=corpus.FILE_PATH,
        source_uri=f"https://github.com/{corpus.REPOSITORY}/pull/1",
        author_external_id="MDQ6_kwDOzzzzzzzzzzzz",
        author_display_name="rev",
        participant_ids=(),
        texts=(),
        last_seen_run_id=corpus.INGESTION_RUN.run_id,
        last_seen_at=corpus.INGESTION_RUN.observed_at.isoformat(),
    )


def _record_named(store_records: tuple[EvidenceRecord, ...], key: str) -> EvidenceRecord:
    """The one corpus record carrying ``key``, so its path is the layout's own."""
    found = [record for record in store_records if record.record_key == key]

    assert len(found) == 1, f"the corpus carries {len(found)} records keyed {key!r}, expected 1"
    return found[0]


# -- What it finds ----------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "key"),
    [
        ("a thread, keyed by its node id", corpus.THREAD_SATISFYING),
        ("a pull request, keyed by its number", str(corpus.PULL_REQUEST_CI_PASSED)),
    ],
    ids=["thread-node-id", "pull-request-number"],
)
def test_a_key_the_store_holds_resolves_to_the_path_the_layout_derived(
    tmp_path: Path, label: str, key: str
) -> None:
    """Both key spaces resolve through one lookup, which is why there is no kind filter.

    The candidate path resolves a thread's node id and then, from that thread's
    own ``event_key``, a pull request's *number* -- two shapes of key against one
    method. A lookup that filtered on ``kind`` would be the caller naming the
    record it expected rather than the one it asked for, and the second of these
    two would miss.

    The expected path is read off the ``EvidenceRecord`` the writer landed, so this
    compares against ``layout.record_path``'s answer and not against a string
    composed here.
    """
    records = corpus.evidence_records()
    store = _built(tmp_path, *records)

    assert (
        store.relative_path_for(corpus.REPOSITORY, key) == _record_named(records, key).relative_path
    ), label


# -- What it does not find --------------------------------------------------


def test_a_key_this_repository_never_held_is_a_miss(tmp_path: Path) -> None:
    """The plain miss, and the shape #575's withheld record will arrive as.

    A key no row carries and a key a build *withheld* are the same ``None``, and
    that is a property of the artifact rather than of this method: the builder
    drops a withheld key before a row exists, so there is nothing here that could
    tell them apart even if it wanted to. Pinning the plain miss is what makes the
    withheld one indistinguishable rather than merely undocumented.
    """
    store = _built(tmp_path, *corpus.evidence_records())

    assert store.relative_path_for(corpus.REPOSITORY, corpus.THREAD_ABSENT) is None


def test_a_key_that_exists_under_another_repository_is_a_miss(tmp_path: Path) -> None:
    """The cross-tenant arm, which a plain-miss test cannot reach.

    Both columns are bound, so the pair is the key. A lookup that matched
    ``record_key`` alone would answer this call with the *other* repository's
    path -- a read across a boundary that would look exactly like a hit and would
    hand the caller a file it was never granted.

    The control is the same key resolving under the repository that does hold it,
    in the same store: without it, a build that answered ``None`` for everything
    would pass.
    """
    shared_key = corpus.THREAD_SATISFYING
    store = _built(tmp_path, _thread_record(shared_key, repository=OTHER_REPOSITORY))

    assert store.relative_path_for(OTHER_REPOSITORY, shared_key) is not None, (
        "the key does not resolve under the repository that holds it, so the miss "
        "below would be about the key rather than about the repository"
    )
    assert store.relative_path_for(corpus.REPOSITORY, shared_key) is None


def test_the_empty_key_misses_because_no_row_can_carry_one(tmp_path: Path) -> None:
    """The empty key is a miss by construction, and this is where the construction is.

    ``""`` is what a caller sends when it has nothing -- an unset field, a failed
    parse, a key read off an absent JSON member -- so whether it can select a row
    is a real question. But asserting only that ``relative_path_for(repo, "")``
    answers ``None`` over an ordinary corpus is **an assertion that cannot fail**:
    no row carries an empty key, so the ``SELECT`` matches nothing whatever its
    predicate says, and an implementation that special-cased the empty string is
    indistinguishable from one that did not. Measured: every mutation of the
    predicate and of the duplicate arm left that assertion green.

    What *can* fail is the invariant underneath it. ``ReviewSearchRecord`` refuses
    a row with no record key -- *nothing could withhold it by key or point a reader
    back at it*, which is the withheld-set's own requirement -- so the empty key
    selects nothing because nothing selectable exists. Both halves are asserted
    together, the invariant first, because the observable is a corollary of it and
    reading them apart is what made the weaker version look like a test.
    """
    store = _built(tmp_path, *corpus.evidence_records())

    with pytest.raises(InvariantViolationError, match="no record key"):
        _keyless_row()
    assert store.relative_path_for(corpus.REPOSITORY, "") is None


def test_a_key_two_rows_carry_is_a_miss_rather_than_whichever_row_came_first(
    tmp_path: Path,
) -> None:
    """The duplicate arm, reached the way a real corpus reaches it.

    ``(repository, record_key)`` is not unique: ``relative_path`` is the primary
    key, and the two key spaces meet because a pull request is keyed by its
    **number** while a thread is keyed by its **node id**. A provider that mints a
    thread whose node id is the decimal string of a pull request's number produces
    two rows sharing the pair -- through the shipped writer and the shipped
    builder, with nothing hand-authored.

    Answering with either would make the candidate path's next step depend on which
    row SQLite returned first, which is not a decision this product gets to make by
    accident. Both rows are asserted present first, or a build that simply failed
    to land one of them would pass this as a duplicate it never had.
    """
    colliding = str(corpus.PULL_REQUEST_CI_PASSED)
    records = (*corpus.evidence_records(), _thread_record(colliding))
    store = _built(tmp_path, *records)

    landed = {record.relative_path for record in store.dump()}
    both = {record.relative_path for record in records if record.record_key == colliding}
    assert len(both) == 2, f"the two colliding records share a path: {both}"
    assert both <= landed, (
        f"the builder did not land both colliding records ({sorted(both - landed)}), so "
        f"the miss below would be an ordinary absence rather than a duplicate"
    )
    assert store.relative_path_for(corpus.REPOSITORY, colliding) is None


def test_a_sibling_key_is_unaffected_by_a_duplicate_next_to_it(tmp_path: Path) -> None:
    """The duplicate refuses its own key and nothing else.

    Without this, a build that answered ``None`` whenever *any* duplicate existed
    anywhere in the store would pass the arm above -- and it would take the whole
    corpus down with one colliding pair, which is a denial a provider could cause
    by choosing a node id.
    """
    records = (*corpus.evidence_records(), _thread_record(str(corpus.PULL_REQUEST_CI_PASSED)))
    store = _built(tmp_path, *records)

    assert (
        store.relative_path_for(corpus.REPOSITORY, corpus.THREAD_CI_UNKNOWN)
        == _record_named(corpus.evidence_records(), corpus.THREAD_CI_UNKNOWN).relative_path
    )


# -- The guard it shares with the query read --------------------------------


def test_the_by_key_read_refuses_a_superseded_store_the_way_the_query_read_does(
    tmp_path: Path,
) -> None:
    """The stamp comparison is one guard one hop below two reads, and this is the second.

    ``_refuse_unless_current`` moved out of ``search`` when this method arrived, so
    the guards ledger in ``test_review_search_store_guards.py`` now describes it as
    shared. A shared guard is only shared if both callers take the hop: a
    ``relative_path_for`` that skipped it would answer ``None`` for a stale store,
    and ``None`` is the *miss* answer -- so the caller would read "no such record"
    where the truth is "this store was built by a superseded schema and its rows
    would be read differently now".

    That is the distinction worth a test: not that a stale store is refused in the
    abstract, but that this read refuses rather than silently reporting an absence.
    The stamp is moved by hand because there is no older build to run.
    """
    store = _built(tmp_path, *corpus.evidence_records())
    with closing(sqlite3.connect(store.path)) as connection:
        connection.execute(
            "UPDATE review_search_metadata SET review_search_schema_version = ?",
            (REVIEW_SEARCH_SCHEMA_VERSION + 1,),
        )
        connection.commit()

    with pytest.raises(ReviewSearchStoreError) as refusal:
        store.relative_path_for(corpus.REPOSITORY, corpus.THREAD_SATISFYING)

    assert "superseded" in str(refusal.value)
