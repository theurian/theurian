"""Reading a GraphQL response's shapes without trusting them (ADR-0030 clause 9).

A response is a document from somewhere else, so every field this package reads
is read through a helper that answers with a value or with a **graded refusal** --
never with a ``KeyError``, a coerced type, or a permissive default that loses
content quietly. That is clause 9's requirement, and it is the whole of what this
module does: the adapter next door decides *what* to ask GitHub and *when* to
refuse the request, and this decides what an answer is allowed to be read as.

It is a module of its own because the adapter reached its size limit, and the
split has a boundary worth keeping: nothing here spawns anything, nothing here
consults the allowlist, and nothing here knows which read it is serving. Three
records are built here rather than in the adapter -- a participant, a comment and
a review submission -- because each is built straight out of a response's shapes
and out of nothing else.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Final

from theurian.domain.identifiers import ProjectId
from theurian.domain.review import ReviewComment, ReviewParticipant, ReviewSubmission
from theurian.domain.review_ingest import (
    RefusalGrade,
    ReviewIngestRefusedError,
    bounded_echo,
)

#: Recorded on every participant and every anchor this adapter produces.
PROVIDER_ID: Final = "github"

#: What a deleted GitHub account resolves to. GitHub's own name for it, used
#: because ``ReviewParticipant.external_id`` may not be empty and inventing an
#: identifier per deleted author would make two records look like two people.
GHOST_LOGIN: Final = "ghost"


def mapping(value: object) -> Mapping[str, Any]:
    """``value`` as a mapping, or an empty one.

    Every field below is read through this rather than indexed, because a GraphQL
    response is a document from somewhere else: a ``null`` where an object was
    expected is an ordinary answer, not a fault, and a ``KeyError`` escaping this
    adapter would be the traceback clause 9 forbids.
    """
    return value if isinstance(value, dict) else {}


def boolean(value: object, field: str) -> bool:
    """A field the schema types as ``Boolean``, refused rather than read loosely.

    **Every one of these decides whether a record is whole**, which is why they
    are refused instead of folded. ``hasNextPage`` says whether what arrived is
    all of it, and ``merged`` selects the guard that a merged pull request
    carries its merge commit; a comparison against ``is True`` reads the *string*
    ``"true"`` as not-merged and skips that guard, and a missing ``pageInfo``
    reads as no-next-page and returns a truncated answer as a complete one. Both
    are documents a partly-errored GraphQL response can be -- the errored field
    comes back ``null`` beside a ``data`` that otherwise looks ordinary -- so the
    permissive read fails silently and in the direction that loses content.

    ``isPrivate`` is the one Boolean this adapter reads that does not come
    through here, and it is **stricter** rather than looser: see
    :meth:`GitHubReviewProvider._repository_of`, where anything that is not
    literally ``False`` refuses the repository.
    """
    if not isinstance(value, bool):
        raise ReviewIngestRefusedError(
            RefusalGrade.TOOL_FAILED,
            f"GitHub's answer carried {field} as {type(value).__name__} where the "
            f"schema types it Boolean, so this adapter cannot tell what the answer "
            f"says. It records nothing out of an answer whose shape it cannot check.",
        )
    return value


def nodes(connection: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """A connection's ``nodes``, keeping only the members that are objects."""
    members = connection.get("nodes")
    if not isinstance(members, list):
        return []
    return [node for node in members if isinstance(node, dict)]


def next_cursor(connection: Mapping[str, Any], what: str) -> str | None:
    """The cursor for the next page of ``what``, or ``None`` when there is none.

    A cursor is an opaque string this adapter hands back in a typed variable
    (clause 6). It chooses no destination: the vector is unchanged but for the
    value of ``after``.

    It is also the one value a response supplies that **re-enters an argument
    vector**, and what these checks ask is whether it can *be* one: no NUL, and
    encodable as UTF-8. ``asyncio.create_subprocess_exec`` declines an argument
    for both, raising a ``ValueError`` either way -- a bare one for the NUL and a
    ``UnicodeEncodeError`` for an unpaired surrogate.

    **That pair travels together, and this repository guards it in several
    places -- named here rather than counted.** Counting them is what two
    earlier versions of this sentence got wrong, in both directions. The key is
    *a value crossing a boundary that accepts neither an embedded NUL nor an
    unpaired surrogate*: ``infrastructure/sqlite/index_query.py``'s
    ``_is_transportable`` guards a query term before FTS5, ``mcp/findings.py``'s
    ``_transportable`` guards a filter value before the store and the response,
    and this guards a cursor before an argv element.
    Those three are the **checking** members, and that list is exact. The
    *catching* side -- code that meets the same pair and translates whatever
    ``ValueError`` arrives, rather than testing for each -- is larger and is
    deliberately not enumerated here: ``application/proposal_service.py`` at a
    resolved path is one, ``gh_cli._start`` and ``gh_cli._binding`` are two more,
    and a grep finds others still. Naming a subset of an unenumerated population
    reads as naming the population, which is the mistake this paragraph exists
    to stop making.

    **The closure is two seams, and neither of them is this function.** An argv
    element can fail at two stages, and they are caught in different places
    because one happens a whole stage before the other:
    :func:`~theurian.infrastructure.github.gh_cli._binding` catches what
    *rendering* declines, so an element that cannot be built refuses before any
    vector exists; ``gh_cli._start`` catches what ``execve`` declines --
    ``(OSError, ValueError)`` around the spawn -- so an element that was built
    and cannot be run refuses there. An earlier version of this paragraph
    credited the spawn's catch with "a value some later caller builds", which it
    cannot see: a construction failure never reaches a spawn, and ``get_threads``
    demonstrated it by leaving a ``ValueError`` as a traceback after its two
    probes had already run. What the checks here add is a refusal that names the
    cursor and the read it stopped, raised before either seam is reached.

    **A page this adapter cannot ask for is a refusal, not a last page.**
    ``hasNextPage`` true with no usable ``endCursor`` says the answer in hand is
    part of a larger one, so returning it would present a partial read as the
    whole -- the silent truncation this adapter's **read** caps exist to replace
    with a report. (Its one bound that does truncate silently is the stderr
    drain, which keeps a prefix of a child's own diagnostic; ``limits.py``
    records that as the exception it is.)
    """
    page_info = mapping(connection.get("pageInfo"))
    if not boolean(page_info.get("hasNextPage"), f"`hasNextPage` on {what}"):
        return None
    cursor = page_info.get("endCursor")
    if not isinstance(cursor, str) or not cursor:
        raise ReviewIngestRefusedError(
            RefusalGrade.TOOL_FAILED,
            f"GitHub reported another page of {what} and gave no cursor to ask for it "
            f"with. The read stopped at the page boundary rather than returning what "
            f"it had as though that were the whole answer.",
        )
    if "\x00" in cursor:
        raise ReviewIngestRefusedError(
            RefusalGrade.TOOL_FAILED,
            f"GitHub's answer carried a pagination cursor for {what} with a NUL byte "
            f"in it, which is not a value that can be spawned as an argument. The read "
            f"stopped at the page boundary rather than handing it to a process.",
        )
    try:
        cursor.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ReviewIngestRefusedError(
            RefusalGrade.TOOL_FAILED,
            f"GitHub's answer carried a pagination cursor for {what} that is not "
            f"encodable text -- an unpaired surrogate, which JSON can spell and an "
            f"argument vector cannot hold. The read stopped at the page boundary "
            f"rather than handing it to a process.",
        ) from exc
    return cursor


def text(value: object) -> str:
    """A string field, or the empty string. Author-controlled text is never parsed."""
    return value if isinstance(value, str) else ""


def optional_text(value: object) -> str | None:
    """A string field, or ``None`` -- for a field the provider is allowed to omit."""
    return value if isinstance(value, str) and value else None


def required_text(value: object, field: str) -> str:
    """A string field a record's identity depends on, refused when it is missing.

    The domain types raise ``InvariantViolationError`` on an empty identifier,
    and that exception would leave this adapter as the traceback clause 9
    forbids. Refusing here turns the same fact into a graded envelope with a
    remedy.
    """
    found = text(value)
    if not found:
        raise ReviewIngestRefusedError(
            RefusalGrade.TOOL_FAILED,
            f"GitHub's answer carried no {field}, so this adapter cannot identify the "
            f"record it belongs to.",
        )
    return found


def integer(value: object, field: str) -> int:
    """An integer field, refused rather than coerced when it is not one."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ReviewIngestRefusedError(
            RefusalGrade.TOOL_FAILED,
            f"GitHub's answer carried no readable {field}, so this adapter cannot "
            f"record the item it belongs to.",
        )
    return value


def positive_integer(value: object, field: str) -> int:
    """An integer a record's identity depends on, refused unless it is positive.

    The same argument as :func:`required_text`, one type over: ``ReviewEvent``
    raises ``InvariantViolationError`` on a number below one, and that exception
    would leave this adapter as the traceback clause 9 forbids. A linked issue
    number has no domain invariant to reach at all -- a zero there is recorded as
    the string ``"0"`` and looks like an issue -- so both go through here.
    """
    number = integer(value, field)
    # The refusal below renders `number`, and `str()` of an integer is not total:
    # CPython refuses past `sys.get_int_max_str_digits()`, 4300 by default. What
    # keeps that unreachable from here is `json.loads`, which applies the same
    # interpreter limit while parsing -- so a longer number never becomes an
    # `int` at all and `_request` refuses the document instead. The limit is a
    # default rather than a guarantee, which is why `bounded_echo` renders this
    # rather than an f-string: a process that raised it would otherwise turn this
    # refusal into the traceback clause 9 forbids.
    if number < 1:
        raise ReviewIngestRefusedError(
            RefusalGrade.TOOL_FAILED,
            f"GitHub's answer carried {bounded_echo(number)} as a {field}. GitHub "
            f"numbers pull requests and issues from one, so this adapter cannot "
            f"identify the record it belongs to.",
        )
    return number


def optional_integer(value: object) -> int | None:
    """A line number, or ``None``: GitHub leaves them null on an outdated thread."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def instant(value: object, field: str) -> datetime:
    """A required timestamp, refused rather than fabricated when it cannot be read."""
    parsed = optional_instant(value)
    if parsed is None:
        raise ReviewIngestRefusedError(
            RefusalGrade.TOOL_FAILED,
            f"GitHub's answer carried no readable {field}, and this adapter records no "
            f"timestamp it did not receive.",
        )
    return parsed


def optional_instant(value: object) -> datetime | None:
    """An ISO-8601 timestamp, or ``None``. Never the ingestion time as a stand-in."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def participant(actor: object) -> ReviewParticipant:
    """An author, with GitHub's ``ghost`` standing in for a deleted account."""
    resolved = optional_participant(actor)
    if resolved is not None:
        return resolved
    return ReviewParticipant(
        provider=PROVIDER_ID, external_id=GHOST_LOGIN, display_name=GHOST_LOGIN
    )


def optional_participant(actor: object) -> ReviewParticipant | None:
    """An actor as a participant, or ``None`` when the provider recorded none.

    ``external_id`` is the node id when the response carries one and the login
    otherwise. The login is author-visible and can be changed by its owner, so it
    is the weaker identity -- which is exactly why the id is preferred and the
    display name is kept separately: redaction replaces a display name without
    breaking the identity graph.
    """
    fields = mapping(actor)
    if not fields:
        return None
    login = text(fields.get("login"))
    node_id = text(fields.get("id"))
    external_id = node_id or login
    if not external_id:
        return None
    return ReviewParticipant(
        provider=PROVIDER_ID, external_id=external_id, display_name=login or external_id
    )


def comment(node: Mapping[str, Any]) -> ReviewComment:
    """One comment. The body crosses as bytes-in-a-string and is never interpreted."""
    return ReviewComment(
        external_id=required_text(node.get("id"), "comment id"),
        author=participant(node.get("author")),
        body=text(node.get("body")),
        created_at=instant(node.get("createdAt"), "comment createdAt"),
        # `category` is a classification, and classification is FR-V2's -- out of
        # this slice and out of this path entirely (FR-V5).
        category=None,
    )


def submission(node: Mapping[str, Any], project_id: ProjectId, event_key: str) -> ReviewSubmission:
    """One top-level review, as the provider spelled it.

    ``state`` goes through :func:`required_text` rather than being folded to the
    empty string, because it is the field the record exists to carry: a
    submission whose verdict could not be read is not one this adapter can record
    honestly, and :class:`~theurian.domain.review.ReviewSubmission` would raise
    ``InvariantViolationError`` on an empty one -- the traceback clause 9 forbids
    rather than the graded envelope it wants.

    ``submitted_at`` is optional and is read as such: a review that was never
    submitted has no submission time, and the alternative -- the ingestion time,
    or the pull request's -- is a measurement nobody took, which every reader
    downstream would take for one.
    """
    return ReviewSubmission(
        external_id=required_text(node.get("id"), "review id"),
        project_id=project_id,
        event_key=event_key,
        author=participant(node.get("author")),
        body=text(node.get("body")),
        state=required_text(node.get("state"), "review state"),
        submitted_at=optional_instant(node.get("submittedAt")),
    )
