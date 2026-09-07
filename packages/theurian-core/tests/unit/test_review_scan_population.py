"""The ingestion scan's population, derived from what the codec writes.

``application/review_landing_gate.py::_scanned_values`` is a hand-written list of
the values SEC-11's ingestion gate reads. A hand-written list has one failure
mode, and PR #596 round 1 met it: a field the codec writes into a landed record
and the list never names is not scanned, and nothing goes RED. That was
``participant.external_id``, which the GitHub adapter fills with the author's own
login whenever GitHub's answer carried no node id.

So the **population** is derived here rather than remembered. This file walks
every string leaf of the three documents
``infrastructure/review_evidence/codec.py`` writes -- ``event_to_json``,
``submission_to_json`` and ``thread_to_json`` -- and requires each one to be
either

* a value ``_scanned_values`` yields for the same record, or
* an entry in :data:`_EXEMPT`, which carries one line per field saying **who
  chooses its value**. That line is what has to agree with ADR-0030 decision 3's
  table; an entry nobody can write is a field that should be scanned.

Two things make the walk mean something rather than pass vacuously:

* :func:`test_the_fixtures_leave_no_field_unpopulated` -- every optional field of
  the three records is set, so no leaf is missing from the walk because a fixture
  left it ``None``. A walk over a half-built record covers half a document.
* :func:`test_a_new_string_leaf_in_a_written_document_is_reported` -- the
  positive control, planting a leaf at three depths and requiring each to be
  reported.

**What this file does not cover, said rather than implied.** The walk is over the
*payload* documents the gate screens. The envelope
``review_evidence/store.py::_document`` writes around one -- the ``sourceAnchor``
and the run stamp -- is not walked here, because those are values Theurian writes
at ingestion (decision 3's third *Controlled by* row) and the gate is handed a
payload, not a record. A change that puts a response's text into that envelope is
outside what this file would notice.

Marked ``unit``; touches no filesystem at all.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import fields, is_dataclass, replace
from datetime import UTC, datetime
from typing import Any, Final

import pytest

from theurian.application import review_landing_gate as gate
from theurian.domain.enums import ReviewCommentCategory, ReviewThreadState
from theurian.domain.identifiers import ProjectId
from theurian.domain.review import (
    ReviewComment,
    ReviewEvent,
    ReviewParticipant,
    ReviewResolution,
    ReviewSubmission,
    ReviewThread,
)
from theurian.infrastructure.review_evidence.codec import (
    event_to_json,
    submission_to_json,
    thread_to_json,
)

pytestmark = pytest.mark.unit

PROVIDER: Final = "github"


def _sentinel(name: str) -> str:
    """A string carried by exactly one field, so coverage is decided by value.

    Every fixture string below is distinct. Membership in the scanned set is then
    an exact answer about *that* field rather than an accident of two fields
    sharing a value -- which is how a fixture reusing ``"github"`` everywhere
    would report an unscanned field as covered.
    """
    return f"sentinel-{name}"


def _participant(where: str) -> ReviewParticipant:
    return ReviewParticipant(
        provider=PROVIDER,
        external_id=_sentinel(f"{where}-external-id"),
        display_name=_sentinel(f"{where}-display-name"),
    )


def _event() -> ReviewEvent:
    return ReviewEvent(
        project_id=ProjectId("demo"),
        provider=PROVIDER,
        repository=_sentinel("repository"),
        number=42,
        title=_sentinel("title"),
        body=_sentinel("body"),
        author=_participant("event-author"),
        created_at=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
        url=_sentinel("url"),
        head_commit=_sentinel("head-commit"),
        base_commit=_sentinel("base-commit"),
        head_ref_name=_sentinel("head-ref-name"),
        labels=(_sentinel("label"),),
        merged=True,
        merge_commit=_sentinel("merge-commit"),
        merged_at=datetime(2026, 8, 2, 12, 0, tzinfo=UTC),
        ci_successful=True,
        linked_issue_ids=(_sentinel("linked-issue-id"),),
        milestone=_sentinel("milestone"),
    )


def _submission() -> ReviewSubmission:
    return ReviewSubmission(
        external_id=_sentinel("submission-external-id"),
        project_id=ProjectId("demo"),
        event_key=_sentinel("submission-event-key"),
        author=_participant("submission-author"),
        body=_sentinel("submission-body"),
        state=_sentinel("submission-state"),
        submitted_at=datetime(2026, 8, 1, 15, 0, tzinfo=UTC),
    )


def _thread() -> ReviewThread:
    return ReviewThread(
        external_id=_sentinel("thread-external-id"),
        project_id=ProjectId("demo"),
        event_key=_sentinel("thread-event-key"),
        file_path=_sentinel("file-path"),
        comments=(
            ReviewComment(
                external_id=_sentinel("comment-external-id"),
                author=_participant("comment-author"),
                body=_sentinel("comment-body"),
                created_at=datetime(2026, 8, 1, 13, 0, tzinfo=UTC),
                category=ReviewCommentCategory.SECURITY_RULE,
                line_start=40,
                line_end=42,
            ),
        ),
        state=ReviewThreadState.RESOLVED,
        resolution=ReviewResolution(
            state=ReviewThreadState.RESOLVED,
            resolved_by=_participant("resolver"),
            resolved_at=datetime(2026, 8, 3, 9, 0, tzinfo=UTC),
            fix_commit=_sentinel("fix-commit"),
        ),
        line_start=40,
        line_end=42,
        commit_sha=_sentinel("thread-commit-sha"),
    )


#: The three payload kinds, as ``(name, record, document)``.
_WRITTEN: Final[tuple[tuple[str, gate.ReviewRecordPayload, dict[str, Any]], ...]] = (
    ("event", _event(), event_to_json(_event())),
    ("submission", _submission(), submission_to_json(_submission())),
    ("thread", _thread(), thread_to_json(_thread())),
)


#: Every string a landed record carries that the ingestion scan deliberately does
#: **not** read, keyed by ``(payload kind, path)`` and carrying the one line that
#: says who chooses the value.
#:
#: Keyed by kind as well as path because two documents spell different things the
#: same way: a submission's ``state`` is GitHub's review verdict, a thread's is
#: this model's own enum, and ``externalId`` at the root names a different record
#: in each. One reason per entry, so each can be argued with on its own.
#:
#: **An entry here is a claim about ADR-0030 decision 3's table**, and the table
#: has been wrong once: ``participant.external_id`` sat on the provider's side
#: until PR #596 round 1 measured the adapter writing an author's login into it.
#: A field whose line cannot be written without hedging belongs in
#: ``_scanned_values`` instead.
_EXEMPT: Final[dict[tuple[str, str], str]] = {
    # -- ids and structure the provider assigns --------------------------------
    ("submission", "externalId"): "the review's node id, minted by GitHub",
    ("thread", "externalId"): "the thread's node id, minted by GitHub",
    ("thread", "comments[].externalId"): "the comment's node id, minted by GitHub",
    ("event", "headCommit"): "a git object id GitHub resolved, not a name anyone types",
    ("event", "baseCommit"): "a git object id GitHub resolved",
    ("event", "mergeCommit"): "a git object id GitHub resolved",
    ("thread", "commitSha"): "the git object id the thread is anchored to",
    ("thread", "resolution.fixCommit"): "the git object id that closed the thread",
    ("event", "linkedIssueIds[]"): "issue numbers GitHub answered, rendered as strings",
    ("event", "url"): "GitHub's own rendering of the pull request's address",
    ("event", "repository"): (
        "the `owner/name` the operator allowlisted, matched against "
        "`security/review_allowlist.py`'s pattern before any fetch"
    ),
    # -- values Theurian itself writes -----------------------------------------
    ("event", "projectId"): "this project's own id, from `.theurian/config.yaml`",
    ("submission", "projectId"): "this project's own id, from `.theurian/config.yaml`",
    ("thread", "projectId"): "this project's own id, from `.theurian/config.yaml`",
    ("submission", "eventKey"): "composed here from provider, repository and number",
    ("thread", "eventKey"): "composed here from provider, repository and number",
    ("event", "provider"): "`response.PROVIDER_ID`, a constant of this adapter",
    ("event", "author.provider"): "`response.PROVIDER_ID`, a constant of this adapter",
    ("submission", "author.provider"): "`response.PROVIDER_ID`, a constant of this adapter",
    ("thread", "comments[].author.provider"): (
        "`response.PROVIDER_ID`, a constant of this adapter"
    ),
    ("thread", "resolution.resolvedBy.provider"): (
        "`response.PROVIDER_ID`, a constant of this adapter"
    ),
    # -- enum-like states, and timestamps rendered from `datetime` -------------
    ("submission", "state"): "GitHub's review verdict vocabulary, carried verbatim",
    ("thread", "state"): "`ReviewThreadState`, this model's own word for two booleans",
    ("thread", "resolution.state"): "`ReviewThreadState`, as above",
    ("thread", "comments[].category"): "`ReviewCommentCategory`, a classification Theurian applies",
    ("event", "createdAt"): "an instant, rendered by `datetime.isoformat`",
    ("event", "mergedAt"): "an instant, rendered by `datetime.isoformat`",
    ("submission", "submittedAt"): "an instant, rendered by `datetime.isoformat`",
    ("thread", "comments[].createdAt"): "an instant, rendered by `datetime.isoformat`",
    ("thread", "resolution.resolvedAt"): "an instant, rendered by `datetime.isoformat`",
}


#: What a failure tells whoever added the field. Both branches are named, because
#: the wrong one is chosen by default when only one is offered.
_REMEDY: Final = (
    "{where} reaches a landed review record and the ingestion secret scan never reads it.\n"
    "Either yield it from `_scanned_values` in "
    "`packages/theurian-core/src/theurian/application/review_landing_gate.py` -- do that "
    "if any part of its value is chosen by a person -- or add it to `_EXEMPT` in this file "
    "with the one line saying who does choose it. That line has to agree with decision 3's "
    "table in `docs/adr/0030-github-review-ingestion-spawns-gh.md`; if it cannot be written "
    "without hedging, the field is scanned."
)


def _string_leaves(document: object, path: str = "") -> Iterator[tuple[str, str]]:
    """Every string in ``document``, with the path it sits at.

    List indices collapse to ``[]``: what an exemption is about is the field, and
    a per-index key would mean a fixture with two labels needed two entries.
    """
    if isinstance(document, str):
        yield path, document
        return
    if isinstance(document, dict):
        mapped: dict[str, Any] = document
        for key, value in mapped.items():
            yield from _string_leaves(value, f"{path}.{key}" if path else key)
        return
    if isinstance(document, list):
        listed: list[Any] = document
        for item in listed:
            yield from _string_leaves(item, f"{path}[]")


def _uncovered(kind: str, document: object, scanned: frozenset[str]) -> list[tuple[str, str]]:
    """The leaves of ``document`` that are neither scanned nor exempt."""
    return [
        (path, value)
        for path, value in _string_leaves(document)
        if value not in scanned and (kind, path) not in _EXEMPT
    ]


def _scanned(record: gate.ReviewRecordPayload) -> frozenset[str]:
    return frozenset(text for _field, _comment_id, text in gate._scanned_values(record))


@pytest.mark.parametrize(("kind", "record", "document"), _WRITTEN, ids=[row[0] for row in _WRITTEN])
def test_every_string_a_landed_record_carries_is_scanned_or_exempt(
    kind: str, record: gate.ReviewRecordPayload, document: dict[str, Any]
) -> None:
    """The class fix for PR #596's H-D: the population is the codec's, not a memory.

    Three assertions rather than one, because the first alone passes for two
    uninteresting reasons: a document with no leaves, and an exemption list that
    swallowed the record. So the scanned side must be non-empty, and the two
    sides must be disjoint -- an exemption over a field the gate does read is a
    reason nobody will ever check, sitting where one is required.
    """
    scanned = _scanned(record)
    uncovered = _uncovered(kind, document, scanned)
    by_scan = {path for path, value in _string_leaves(document) if value in scanned}

    assert not uncovered, _REMEDY.format(
        where=", ".join(f"`{kind}.{path}`" for path, _value in uncovered)
    )
    assert by_scan, f"nothing in a {kind} document is scanned, so this walk proves nothing"
    assert not by_scan & {path for exempt_kind, path in _EXEMPT if exempt_kind == kind}


@pytest.mark.parametrize(("kind", "record", "document"), _WRITTEN, ids=[row[0] for row in _WRITTEN])
def test_every_value_the_scan_reads_is_a_string_the_record_writes(
    kind: str, record: gate.ReviewRecordPayload, document: dict[str, Any]
) -> None:
    """The other direction: the scan does not read a value no file carries.

    A yielded value the codec never writes is either a field the gate reports on
    and nobody can see, or -- worse -- a rendering the gate made up, which is what
    ``_scanned_values`` refuses to do for an absent ``milestone``.
    """
    written = {value for _path, value in _string_leaves(document)}

    assert _scanned(record) <= written, (
        f"the scan reads a value no {kind} document carries: {sorted(_scanned(record) - written)}"
    )


@pytest.mark.parametrize(("kind", "record", "document"), _WRITTEN, ids=[row[0] for row in _WRITTEN])
def test_a_new_string_leaf_in_a_written_document_is_reported(
    kind: str, record: gate.ReviewRecordPayload, document: dict[str, Any]
) -> None:
    """The positive control, planted at three depths.

    A walk that stopped at the top level, or that skipped objects inside lists,
    would leave the test above passing over a document it never fully read --
    which is the shape of the defect this file exists to catch.
    """
    planted = {**document, "noteToSelf": "a field somebody added"}
    planted["author"] = {**planted.get("author", {}), "avatarUrl": "a nested field"}
    planted["reactions"] = [{"emoji": "a field inside a list"}]

    reported = {path for path, _value in _uncovered(kind, planted, _scanned(record))}

    assert reported == {"noteToSelf", "author.avatarUrl", "reactions[].emoji"}


def test_no_exemption_names_a_field_no_document_has() -> None:
    """A stale entry is a reason nobody can check, sitting where one is required.

    Renaming a codec key without moving its exemption leaves the old line in
    place, reading as a decision about the new field. This is what makes the list
    describe the tree rather than its own history.
    """
    present = {
        (kind, path)
        for kind, _record, document in _WRITTEN
        for path, _value in _string_leaves(document)
    }

    assert set(_EXEMPT) <= present, (
        f"exemptions for fields nothing writes: {sorted(set(_EXEMPT) - present)}"
    )


def _unpopulated(value: object, path: str = "") -> Iterator[str]:
    """Every field of ``value`` a fixture left absent, as a path.

    ``None`` and an empty tuple both count: each leaves the codec writing ``null``
    or ``[]``, so the leaf the walk was supposed to visit is not in the document.
    """
    if value is None:
        yield path
        return
    if isinstance(value, tuple):
        items: tuple[Any, ...] = value
        if not items:
            yield f"{path} (empty)"
            return
        for index, item in enumerate(items):
            yield from _unpopulated(item, f"{path}[{index}]")
        return
    if is_dataclass(value) and not isinstance(value, type):
        for field in fields(value):
            name = field.name
            yield from _unpopulated(getattr(value, name), f"{path}.{name}" if path else name)


@pytest.mark.parametrize(("kind", "record", "document"), _WRITTEN, ids=[row[0] for row in _WRITTEN])
def test_the_fixtures_leave_no_field_unpopulated(
    kind: str, record: gate.ReviewRecordPayload, document: dict[str, Any]
) -> None:
    """Without this, an optional field added and left ``None`` skips the walk.

    The record is walked by its dataclass fields rather than by the document, so
    a field the codec forgot *and* the fixture set is still reported here -- the
    two walks disagree exactly where a value is lost between the domain and the
    file.
    """
    assert list(_unpopulated(record)) == [], (
        f"the {kind} fixture leaves a field absent, so the document it writes is "
        f"missing the leaves that field would carry"
    )


def test_an_absent_field_is_what_the_populated_check_reports() -> None:
    """The positive control on the check above, in both shapes it treats as absent.

    A walker that descended into nothing would report an empty list for every
    record and make that test pass against a fixture built from defaults.
    """
    thinned = replace(_event(), milestone=None, labels=(), merged=False, merge_commit=None)

    assert sorted(_unpopulated(thinned)) == [
        "labels (empty)",
        "merge_commit",
        "milestone",
    ]
