"""The review corpus ``review.generateKnowledgeCandidate``'s wire tests are driven against.

One builder, shared by the integration wire battery and the E2E session, because
the corpus has to reach the same four gate outcomes in both and a second copy is a
second place for one of them to stop being reachable.

**Every synthetic node id here is born low-entropy, and that is a measured
requirement rather than a style.** A realistic-looking GitHub node id reads as a
credential to ``gitleaks``' ``generic-api-key`` rule: ``PRRT_kwDOABCD1M5abcde``
tripped it at entropy 4.202 on a full-history scan, and a full-history scan runs
across every lane's pull request, so one such id on this branch reddens the Secret
scan job on work that never touched it. The tails below are runs of a single
character, which is what holds each id's entropy at ~2.25 -- the reading taken on
``tests/unit/test_candidate_generation.py``'s ``THREAD_KEY``, whose value moved for
exactly this reason. Minting a realistic-looking one re-trips the job under a fresh
fingerprint no ``.gitleaksignore`` entry covers. They live in one module so the
discipline lives in one place.

The ids are **not** ULIDs and must not be read as ones: they carry ``O``, which
Crockford base32 excludes. They are provider identifiers Theurian stores rather
than ones it mints, which is the same reason ``test_candidate_generation.py``'s
fixture carries one.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final

from theurian.domain.enums import ReviewCommentCategory, ReviewThreadState
from theurian.domain.identifiers import ProjectId
from theurian.domain.knowledge import SourceAnchor
from theurian.domain.review import (
    ReviewComment,
    ReviewEvent,
    ReviewParticipant,
    ReviewResolution,
    ReviewThread,
)
from theurian.infrastructure.review_evidence import EvidenceRecord, IngestionRun

PROVIDER: Final = "github"

#: The repository every record below names. Spelled as the store holds it:
#: ``review.search``'s equality filters are byte-for-byte, and the candidate tool
#: resolves a record by the same ``(repository, recordKey)`` pair.
REPOSITORY: Final = "acme/order-service"

#: The file the satisfying thread is anchored to, and the path the ``fixCommit``
#: verification asks git about.
FILE_PATH: Final = "src/retrying.py"

#: A second tracked path, so a commit can exist in the repository and touch
#: *nothing* the thread names -- the ``TOUCHES_NOTHING_HERE`` verdict, which is
#: what separates a verification from a bare existence check.
OTHER_FILE_PATH: Final = "src/unrelated.py"

#: A commit shape that resolves to nothing in any repository. Forty hex digits, so
#: it is refused by the repository rather than by a grammar check upstream of it.
ABSENT_COMMIT: Final = "f" * 40

#: One pull request per ``ci_successful`` state. A number rather than a node id:
#: ``EvidenceRecord.record_key`` keys a pull request by the number a reviewer
#: types, so these are the keys the tool's second resolve looks up.
PULL_REQUEST_CI_PASSED: Final = 431
PULL_REQUEST_CI_UNKNOWN: Final = 432
PULL_REQUEST_CI_FAILED: Final = 433


def _node_id(prefix: str, filler: str) -> str:
    """One synthetic provider node id, born at the entropy the scan tolerates.

    The tail is twelve copies of ``filler`` rather than twelve varied characters;
    see this module's own docstring for the measurement that makes that a
    requirement.
    """
    return f"{prefix}_kwDO{filler * 12}"


#: The thread that meets every gate signal: resolved, on a merged pull request
#: with CI green, anchored to :data:`FILE_PATH`, carrying a comment.
THREAD_SATISFYING: Final = _node_id("PRRT", "a")

#: The same thread shape on a pull request whose stored ``ciSuccessful`` is
#: ``null`` -- ADR-0033 decision 4's *unknown*, which does not satisfy the gate
#: and whose refusal sentence must differ from the failed one's.
THREAD_CI_UNKNOWN: Final = _node_id("PRRT", "b")

#: The same shape again on a pull request whose CI is a definite ``false``.
THREAD_CI_FAILED: Final = _node_id("PRRT", "c")

#: A thread GitHub anchored to no string ``path``, which the adapter stores as
#: ``file_path=None`` (``review_provider.py:665``). ADR-0033 decision 3's
#: amendment refuses it in its own words.
THREAD_NO_FILE_ANCHOR: Final = _node_id("PRRT", "d")

#: A well-formed thread key this corpus never stores. The uniform refusal's input.
THREAD_ABSENT: Final = _node_id("PRRT", "e")

#: A thread meeting every gate signal, planted so one corpus can **withhold** it
#: (ADR-0033 decision 5's two-corpora battery). Identical in every stored field to
#: :data:`THREAD_SATISFYING` but its id, so what separates the two corpora is the
#: withholding and not the record's shape -- and so the same call that answers a
#: miss where the key is withheld lands a **proposal** where it is not, which is
#: the positive control the equality needs.
THREAD_WITHHELD: Final = _node_id("PRRT", "m")

#: A pull request one corpus withholds. Its record key is its number, the other
#: of the two key shapes ``withheld_record_keys`` is matched against, and
#: withholding it drives the tool's **second** resolve -- the one that looks up
#: the pull request named by a thread's ``event_key`` -- rather than the first.
PULL_REQUEST_WITHHELD: Final = 434

#: A thread the caller may see, anchored to :data:`PULL_REQUEST_WITHHELD`. Its own
#: record is in both corpora; what is withheld is the record it depends on. This
#: is the neighbour case: a *visible* row whose answer must not move with a row
#: nobody may be told about.
THREAD_ON_WITHHELD_PULL_REQUEST: Final = _node_id("PRRT", "n")

#: Text **only** the withheld records carry, planted in every string of theirs a
#: caller could plausibly receive: a thread's comment, a pull request's title and
#: its body.
#:
#: The distinction from a record *key* is load-bearing. A key is something the
#: caller sends, so finding one in an answer says only that the request was
#: echoed; this string is something the caller cannot know, so finding it says a
#: withheld record's own content reached the wire. A battery that scanned for the
#: key instead would go red on an echoed request and call it a disclosure.
#:
#: Letters ``p``--``z`` only, and no visible record spells anything from that
#: half of the alphabet, so the disjointness holds by construction rather than by
#: inspection -- the guard that ranges over the rendered corpus is
#: ``test_candidate_generation_absence_proof.py``'s
#: ``test_the_two_corpora_differ_by_exactly_the_withheld_records``.
WITHHELD_PAYLOAD: Final = "VWXYZPRSTVWXYZ"


def _participant(login: str, filler: str) -> ReviewParticipant:
    return ReviewParticipant(
        provider=PROVIDER, external_id=_node_id("MDQ6", filler), display_name=login
    )


def _anchor(uri: str) -> SourceAnchor:
    return SourceAnchor(
        provider=PROVIDER, source_uri=uri, repository=REPOSITORY, file_path=FILE_PATH
    )


def _marked(text: str, marker: str) -> str:
    """``text``, carrying ``marker`` when there is one to carry."""
    return text if not marker else f"{text} {marker}"


def _event(number: int, *, ci_successful: bool | None, marker: str = "") -> ReviewEvent:
    return ReviewEvent(
        project_id=ProjectId("demo"),
        provider=PROVIDER,
        repository=REPOSITORY,
        number=number,
        title=_marked("Take the lock after the read", marker),
        body=_marked("Fixes the retry deadlock reported in the payments incident.", marker),
        author=_participant("author", "f"),
        created_at=datetime(2026, 9, 1, 9, 0, tzinfo=UTC),
        url=f"https://github.com/{REPOSITORY}/pull/{number}",
        head_commit="a" * 40,
        base_commit="b" * 40,
        head_ref_name="fix/retry-deadlock",
        labels=("bug",),
        merged=True,
        merge_commit="c" * 40,
        merged_at=datetime(2026, 9, 2, 11, 30, tzinfo=UTC),
        ci_successful=ci_successful,
    )


def _thread(
    external_id: str,
    *,
    number: int,
    file_path: str | None,
    comment_filler: str,
    marker: str = "",
) -> ReviewThread:
    """One resolved conversation, as ``review_provider.py`` builds one.

    ``resolution`` carries no ``fix_commit``: the shipped adapter never assigns
    one, so a corpus that did would be testing the gate against a record shape
    this product cannot produce (ADR-0033 decision 3's measurement).
    """
    return ReviewThread(
        external_id=external_id,
        project_id=ProjectId("demo"),
        event_key=f"{PROVIDER}:{REPOSITORY}#{number}",
        file_path=file_path,
        comments=(
            ReviewComment(
                external_id=_node_id("PRRC", comment_filler),
                author=_participant("reviewer", "g"),
                body=_marked(
                    "This will deadlock under retry. Take the lock after the read.", marker
                ),
                created_at=datetime(2026, 9, 1, 10, 0, tzinfo=UTC),
                category=ReviewCommentCategory.RELIABILITY_RULE,
            ),
        ),
        state=ReviewThreadState.RESOLVED,
        resolution=ReviewResolution(
            state=ReviewThreadState.RESOLVED, resolved_by=_participant("reviewer", "g")
        ),
    )


def _record(payload: ReviewEvent | ReviewThread, uri: str) -> EvidenceRecord:
    return EvidenceRecord(
        provider=PROVIDER, repository=REPOSITORY, anchor=_anchor(uri), payload=payload
    )


def evidence_records() -> tuple[EvidenceRecord, ...]:
    """The corpus: three pull requests and four threads.

    Three pull requests, because ADR-0033 decision 4 needs three ``ci_successful``
    readings -- ``True``, ``None`` and ``False`` -- and two of them would not
    distinguish *unknown is unmet* from *the gate ignores this signal*. Four
    threads, because the fourth is the ``file_path is None`` branch decision 3's
    amendment refuses, and a corpus with only anchored threads cannot reach it.

    The satisfying thread is first so a reader can see what the other three differ
    from in one field each.
    """
    return (
        _record(
            _event(PULL_REQUEST_CI_PASSED, ci_successful=True),
            f"https://github.com/{REPOSITORY}/pull/{PULL_REQUEST_CI_PASSED}",
        ),
        _record(
            _event(PULL_REQUEST_CI_UNKNOWN, ci_successful=None),
            f"https://github.com/{REPOSITORY}/pull/{PULL_REQUEST_CI_UNKNOWN}",
        ),
        _record(
            _event(PULL_REQUEST_CI_FAILED, ci_successful=False),
            f"https://github.com/{REPOSITORY}/pull/{PULL_REQUEST_CI_FAILED}",
        ),
        _record(
            _thread(
                THREAD_SATISFYING,
                number=PULL_REQUEST_CI_PASSED,
                file_path=FILE_PATH,
                comment_filler="h",
            ),
            f"https://github.com/{REPOSITORY}/pull/{PULL_REQUEST_CI_PASSED}#discussion_r1",
        ),
        _record(
            _thread(
                THREAD_CI_UNKNOWN,
                number=PULL_REQUEST_CI_UNKNOWN,
                file_path=FILE_PATH,
                comment_filler="i",
            ),
            f"https://github.com/{REPOSITORY}/pull/{PULL_REQUEST_CI_UNKNOWN}#discussion_r1",
        ),
        _record(
            _thread(
                THREAD_CI_FAILED,
                number=PULL_REQUEST_CI_FAILED,
                file_path=FILE_PATH,
                comment_filler="j",
            ),
            f"https://github.com/{REPOSITORY}/pull/{PULL_REQUEST_CI_FAILED}#discussion_r1",
        ),
        _record(
            _thread(
                THREAD_NO_FILE_ANCHOR,
                number=PULL_REQUEST_CI_PASSED,
                file_path=None,
                comment_filler="k",
            ),
            f"https://github.com/{REPOSITORY}/pull/{PULL_REQUEST_CI_PASSED}#discussion_r2",
        ),
    )


def visible_corpus() -> tuple[EvidenceRecord, ...]:
    """:func:`evidence_records`, plus the thread anchored to the withheld pull request.

    The corpus **both** halves of the two-corpora battery hold. The extra thread
    is visible in both and is the one whose answer must not move with the record
    it depends on: its ``event_key`` names :data:`PULL_REQUEST_WITHHELD`, whose
    record one corpus withholds and the other never had.
    """
    return (
        *evidence_records(),
        _record(
            _thread(
                THREAD_ON_WITHHELD_PULL_REQUEST,
                number=PULL_REQUEST_WITHHELD,
                file_path=FILE_PATH,
                comment_filler="q",
            ),
            f"https://github.com/{REPOSITORY}/pull/{PULL_REQUEST_WITHHELD}#discussion_r1",
        ),
    )


def withheld_corpus() -> tuple[EvidenceRecord, ...]:
    """The records one corpus holds and withholds, and the other never had.

    Two kinds, so both record-key shapes are exercised: a thread is keyed by its
    node id and a pull request by its number, and a battery that withheld only one
    would leave the other's key untested.

    Both carry :data:`WITHHELD_PAYLOAD`, which is what a scan for a leak looks
    for: content the caller cannot know, rather than a key the caller sent.
    """
    return (
        _record(
            _thread(
                THREAD_WITHHELD,
                number=PULL_REQUEST_CI_PASSED,
                file_path=FILE_PATH,
                comment_filler="p",
                marker=WITHHELD_PAYLOAD,
            ),
            f"https://github.com/{REPOSITORY}/pull/{PULL_REQUEST_CI_PASSED}#discussion_r3",
        ),
        _record(
            _event(PULL_REQUEST_WITHHELD, ci_successful=True, marker=WITHHELD_PAYLOAD),
            f"https://github.com/{REPOSITORY}/pull/{PULL_REQUEST_WITHHELD}",
        ),
    )


#: The keys a withholding build is asked to drop, read off the records' own
#: ``record_key`` rather than spelled again here: a second spelling is how a
#: withheld key comes to match one definition and miss the other.
WITHHELD_KEYS: Final = frozenset(record.record_key for record in withheld_corpus())

#: The run every record above is stamped with. A ULID, unlike the node ids: this
#: one Theurian mints, so it is Crockford base32 and carries no ``I``/``L``/``O``/``U``.
INGESTION_RUN: Final = IngestionRun(
    "01K1AAAAAA01234567890ABCDE", datetime(2026, 9, 7, 9, 0, tzinfo=UTC)
)
