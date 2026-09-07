"""Turning one review record into JSON and back without losing a field.

**Every field of the three domain shapes is written and read**, because the file
is the source: a field dropped here is evidence Theurian cannot recover, since
the upstream comment it came from may already be edited or deleted (ADR-0030
decision 3). ``tests/unit/test_review_evidence_store.py``'s round-trip cases are
what hold that -- they compare whole frozen dataclasses, so a field added to
:mod:`theurian.domain.review` and forgotten here fails on equality rather than
passing on the subset somebody remembered.

**Keys are camelCase**, matching every other JSON document this product writes
(``active.json``'s ``databaseFilename``, the migration schema's ``contentFile``).

**Nothing here validates for safety.** The readers below refuse a value of the
wrong *shape* so that a malformed file produces a message naming the field rather
than a ``KeyError`` three frames up; what makes reading an evidence file safe is
the containment its caller applies to the path
(:class:`~theurian.infrastructure.review_evidence.store.ReviewEvidenceStore`) and
the domain types' own ``__post_init__``, which every value below is handed to.

**The two halves raise different families, and that difference bit once.** The
readers below raise :class:`ValueError` for a wrong shape; the domain types they
hand every value to raise :class:`~theurian.domain.errors.DomainError` --
``InvariantViolationError`` for a thread with no comments or a blank submission
state, ``InvalidIdentifierError`` for a malformed project id -- and a
``DomainError`` is **not** a ``ValueError``. A reader that caught only the first
let a landed file whose *record* was impossible escape as a bare traceback.
:meth:`~theurian.infrastructure.review_evidence.store.ReviewEvidenceStore.read_all`
catches both families and is the one place that turns either into a refusal
carrying the file's name and a remedy.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from theurian.domain.enums import ReviewCommentCategory, ReviewThreadState
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

# -- writing -----------------------------------------------------------------


def anchor_to_json(anchor: SourceAnchor) -> dict[str, Any]:
    """One source anchor, every field of it."""
    return {
        "provider": anchor.provider,
        "sourceUri": anchor.source_uri,
        "repository": anchor.repository,
        "commitSha": anchor.commit_sha,
        "blobSha": anchor.blob_sha,
        "filePath": anchor.file_path,
        "lineStart": anchor.line_start,
        "lineEnd": anchor.line_end,
        "externalId": anchor.external_id,
    }


def _participant_to_json(participant: ReviewParticipant) -> dict[str, Any]:
    return {
        "provider": participant.provider,
        "externalId": participant.external_id,
        "displayName": participant.display_name,
    }


def _comment_to_json(comment: ReviewComment) -> dict[str, Any]:
    return {
        "externalId": comment.external_id,
        "author": _participant_to_json(comment.author),
        "body": comment.body,
        "createdAt": comment.created_at.isoformat(),
        "category": None if comment.category is None else comment.category.value,
        "lineStart": comment.line_start,
        "lineEnd": comment.line_end,
    }


def _resolution_to_json(resolution: ReviewResolution) -> dict[str, Any]:
    return {
        "state": resolution.state.value,
        "resolvedBy": (
            None if resolution.resolved_by is None else _participant_to_json(resolution.resolved_by)
        ),
        "resolvedAt": (
            None if resolution.resolved_at is None else resolution.resolved_at.isoformat()
        ),
        "fixCommit": resolution.fix_commit,
    }


def event_to_json(event: ReviewEvent) -> dict[str, Any]:
    """One pull-request event, every field of it."""
    return {
        "projectId": str(event.project_id),
        "provider": event.provider,
        "repository": event.repository,
        "number": event.number,
        "title": event.title,
        "body": event.body,
        "author": _participant_to_json(event.author),
        "createdAt": event.created_at.isoformat(),
        "url": event.url,
        "headCommit": event.head_commit,
        "baseCommit": event.base_commit,
        "headRefName": event.head_ref_name,
        "labels": list(event.labels),
        "merged": event.merged,
        "mergeCommit": event.merge_commit,
        "mergedAt": None if event.merged_at is None else event.merged_at.isoformat(),
        "ciSuccessful": event.ci_successful,
        "linkedIssueIds": list(event.linked_issue_ids),
        "milestone": event.milestone,
    }


def submission_to_json(submission: ReviewSubmission) -> dict[str, Any]:
    """One top-level review, every field of it."""
    return {
        "externalId": submission.external_id,
        "projectId": str(submission.project_id),
        "eventKey": submission.event_key,
        "author": _participant_to_json(submission.author),
        "body": submission.body,
        "state": submission.state,
        "submittedAt": (
            None if submission.submitted_at is None else submission.submitted_at.isoformat()
        ),
    }


def thread_to_json(thread: ReviewThread) -> dict[str, Any]:
    """One conversation and every comment in it."""
    return {
        "externalId": thread.external_id,
        "projectId": str(thread.project_id),
        "eventKey": thread.event_key,
        "filePath": thread.file_path,
        "comments": [_comment_to_json(comment) for comment in thread.comments],
        "state": thread.state.value,
        "resolution": (
            None if thread.resolution is None else _resolution_to_json(thread.resolution)
        ),
        "lineStart": thread.line_start,
        "lineEnd": thread.line_end,
        "commitSha": thread.commit_sha,
    }


# -- reading -----------------------------------------------------------------


def _mapping(value: object, where: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"`{where}` is a {type(value).__name__}, not an object")
    typed: Mapping[str, Any] = value
    return typed


def _string(source: Mapping[str, Any], key: str, where: str) -> str:
    value = source.get(key)
    if not isinstance(value, str):
        raise ValueError(f"`{where}.{key}` is a {type(value).__name__}, not a string")
    return value


def _optional_string(source: Mapping[str, Any], key: str, where: str) -> str | None:
    value = source.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"`{where}.{key}` is a {type(value).__name__}, not a string or null")
    return value


def _integer(source: Mapping[str, Any], key: str, where: str) -> int:
    value = source.get(key)
    # `bool` is an `int` subclass, and `true` in this position is a file that says
    # something different from what it appears to say.
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"`{where}.{key}` is a {type(value).__name__}, not an integer")
    return value


def _optional_integer(source: Mapping[str, Any], key: str, where: str) -> int | None:
    if source.get(key) is None:
        return None
    return _integer(source, key, where)


def _boolean(source: Mapping[str, Any], key: str, where: str) -> bool:
    value = source.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"`{where}.{key}` is a {type(value).__name__}, not true or false")
    return value


def _optional_boolean(source: Mapping[str, Any], key: str, where: str) -> bool | None:
    if source.get(key) is None:
        return None
    return _boolean(source, key, where)


def _moment(source: Mapping[str, Any], key: str, where: str) -> datetime:
    text = _string(source, key, where)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"`{where}.{key}` is not an ISO 8601 instant") from exc
    if parsed.tzinfo is None:
        raise ValueError(
            f"`{where}.{key}` carries no time zone, and a naive instant compares "
            "wrong against one written on another machine"
        )
    return parsed


def _optional_moment(source: Mapping[str, Any], key: str, where: str) -> datetime | None:
    if source.get(key) is None:
        return None
    return _moment(source, key, where)


def _strings(source: Mapping[str, Any], key: str, where: str) -> tuple[str, ...]:
    value = source.get(key)
    if not isinstance(value, list):
        raise ValueError(f"`{where}.{key}` is a {type(value).__name__}, not a list")
    items: Sequence[Any] = value
    for index, item in enumerate(items):
        if not isinstance(item, str):
            raise ValueError(f"`{where}.{key}[{index}]` is a {type(item).__name__}, not a string")
    return tuple(str(item) for item in items)


def anchor_from_json(value: object, where: str) -> SourceAnchor:
    """The source anchor a record carries."""
    source = _mapping(value, where)
    return SourceAnchor(
        provider=_string(source, "provider", where),
        source_uri=_string(source, "sourceUri", where),
        repository=_optional_string(source, "repository", where),
        commit_sha=_optional_string(source, "commitSha", where),
        blob_sha=_optional_string(source, "blobSha", where),
        file_path=_optional_string(source, "filePath", where),
        line_start=_optional_integer(source, "lineStart", where),
        line_end=_optional_integer(source, "lineEnd", where),
        external_id=_optional_string(source, "externalId", where),
    )


def _participant_from_json(value: object, where: str) -> ReviewParticipant:
    source = _mapping(value, where)
    return ReviewParticipant(
        provider=_string(source, "provider", where),
        external_id=_string(source, "externalId", where),
        display_name=_string(source, "displayName", where),
    )


def _comment_from_json(value: object, where: str) -> ReviewComment:
    source = _mapping(value, where)
    category = _optional_string(source, "category", where)
    return ReviewComment(
        external_id=_string(source, "externalId", where),
        author=_participant_from_json(source.get("author"), f"{where}.author"),
        body=_string(source, "body", where),
        created_at=_moment(source, "createdAt", where),
        category=None if category is None else ReviewCommentCategory(category),
        line_start=_optional_integer(source, "lineStart", where),
        line_end=_optional_integer(source, "lineEnd", where),
    )


def _resolution_from_json(value: object, where: str) -> ReviewResolution:
    source = _mapping(value, where)
    resolved_by = source.get("resolvedBy")
    return ReviewResolution(
        state=ReviewThreadState(_string(source, "state", where)),
        resolved_by=(
            None
            if resolved_by is None
            else _participant_from_json(resolved_by, f"{where}.resolvedBy")
        ),
        resolved_at=_optional_moment(source, "resolvedAt", where),
        fix_commit=_optional_string(source, "fixCommit", where),
    )


def event_from_json(value: object, where: str) -> ReviewEvent:
    """One pull-request event, read back."""
    source = _mapping(value, where)
    return ReviewEvent(
        project_id=ProjectId(_string(source, "projectId", where)),
        provider=_string(source, "provider", where),
        repository=_string(source, "repository", where),
        number=_integer(source, "number", where),
        title=_string(source, "title", where),
        body=_string(source, "body", where),
        author=_participant_from_json(source.get("author"), f"{where}.author"),
        created_at=_moment(source, "createdAt", where),
        url=_string(source, "url", where),
        head_commit=_string(source, "headCommit", where),
        base_commit=_string(source, "baseCommit", where),
        head_ref_name=_string(source, "headRefName", where),
        labels=_strings(source, "labels", where),
        merged=_boolean(source, "merged", where),
        merge_commit=_optional_string(source, "mergeCommit", where),
        merged_at=_optional_moment(source, "mergedAt", where),
        ci_successful=_optional_boolean(source, "ciSuccessful", where),
        linked_issue_ids=_strings(source, "linkedIssueIds", where),
        milestone=_optional_string(source, "milestone", where),
    )


def submission_from_json(value: object, where: str) -> ReviewSubmission:
    """One top-level review, read back."""
    source = _mapping(value, where)
    return ReviewSubmission(
        external_id=_string(source, "externalId", where),
        project_id=ProjectId(_string(source, "projectId", where)),
        event_key=_string(source, "eventKey", where),
        author=_participant_from_json(source.get("author"), f"{where}.author"),
        body=_string(source, "body", where),
        state=_string(source, "state", where),
        submitted_at=_optional_moment(source, "submittedAt", where),
    )


def thread_from_json(value: object, where: str) -> ReviewThread:
    """One conversation and every comment in it, read back."""
    source = _mapping(value, where)
    comments = source.get("comments")
    if not isinstance(comments, list):
        raise ValueError(f"`{where}.comments` is a {type(comments).__name__}, not a list")
    listed: Sequence[Any] = comments
    resolution = source.get("resolution")
    return ReviewThread(
        external_id=_string(source, "externalId", where),
        project_id=ProjectId(_string(source, "projectId", where)),
        event_key=_string(source, "eventKey", where),
        file_path=_optional_string(source, "filePath", where),
        comments=tuple(
            _comment_from_json(comment, f"{where}.comments[{index}]")
            for index, comment in enumerate(listed)
        ),
        state=ReviewThreadState(_string(source, "state", where)),
        resolution=(
            None if resolution is None else _resolution_from_json(resolution, f"{where}.resolution")
        ),
        line_start=_optional_integer(source, "lineStart", where),
        line_end=_optional_integer(source, "lineEnd", where),
        commit_sha=_optional_string(source, "commitSha", where),
    )
