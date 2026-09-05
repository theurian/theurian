"""The ``ReviewProvider`` adapter: public allowlisted repositories, over ``gh``.

FR-V1's fetch half -- pull requests, review threads, comments and resolution
state -- read as **evidence only**. Nothing here classifies, generalises or
reaches a model, which is FR-V5 satisfied structurally rather than by a fallback
path: raw ingestion cannot be broken by candidate generation failing because
candidate generation is not in this path at all.

**The order of the three refusals is the design, not an implementation detail**
(ADR-0030 decisions 1 and 2):

1. **The allowlist, before any process exists.** A repository
   ``providers.review.repositories`` does not name produces *no spawn* -- not a
   filtered result. There is nothing to filter, because nothing was asked.
2. **The transport-override check, before any binary probe.** A version read and
   an authentication probe are themselves spawns, and a check that runs after one
   has already handed the operator's ``gh`` configuration a request.
3. **Everything the binary can tell us**, in one place: it exists, it is at or
   above the recorded version floor, and it has a session.

Two further refusals are about the *answer* rather than the request, and both
happen before a single record is built:

* **A repository that resolves as private is refused at ingestion**, allowlisted
  or not. This version ingests no advisory-private GitHub surface.
* **A repository that resolves to a different name is refused, not followed.**
  GitHub redirects a renamed ``owner/repo``, so an allowlisted name can resolve
  to a repository nobody allowlisted. The comparison is **case-folded**, because
  GitHub treats owner and repository names case-insensitively and a byte
  comparison would refuse a correct answer. The repository **id** is
  deliberately not checked here: on a first ingest there is nothing to compare it
  to, and an id read out of the same response it would validate proves nothing.

**Bodies are carried, never interpreted.** Every author-controlled string -- a
comment body, a title, a display name, a file path as received -- is copied into
the domain record as data. In particular a received ``path`` is never joined into
a filesystem path here, and this slice writes no file at all.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, Final, final

from theurian.domain.enums import ReviewThreadState
from theurian.domain.identifiers import ProjectId
from theurian.domain.review import (
    ReviewComment,
    ReviewEvent,
    ReviewParticipant,
    ReviewResolution,
    ReviewThread,
)
from theurian.domain.review_ingest import RefusalGrade, ReviewIngestRefusedError
from theurian.infrastructure.github import queries
from theurian.infrastructure.github.environment import child_environment
from theurian.infrastructure.github.gh_cli import GhCli, locate_binary
from theurian.infrastructure.github.limits import (
    MAX_COMMENTS_PER_THREAD,
    MAX_PAGES,
    MAX_PULL_REQUESTS,
    PAGE_SIZE,
)
from theurian.infrastructure.github.transport_guard import refuse_transport_overrides
from theurian.security.review_allowlist import allowlisted_repository

#: Recorded on every participant and every anchor this adapter produces.
PROVIDER_ID: Final = "github"

#: What a deleted GitHub account resolves to. GitHub's own name for it, used
#: because ``ReviewParticipant.external_id`` may not be empty and inventing an
#: identifier per deleted author would make two records look like two people.
GHOST_LOGIN: Final = "ghost"


@final
class GitHubReviewProvider:
    """Satisfies :class:`~theurian.domain.ports.ReviewProvider` structurally.

    Args:
        project_root: The project whose ``.theurian/config.yaml`` holds the
            allowlist. Also the containment boundary that file is read through.
        config_file: Where that file is, composed by the caller from
            ``ProjectPaths`` -- this adapter does not decide where a project
            keeps its configuration.
        parent_environment: The environment the three ``gh`` config-locating
            variables are forwarded from, and whose ``PATH`` locates ``gh``.
            Taken as an argument rather than read from ``os.environ`` here so
            that a test drives the real construction against a synthetic parent.
        binary: An already-resolved ``gh``. Left ``None`` in production, where it
            is looked up on the first call; supplied by tests that point the
            adapter at a stand-in child.
    """

    def __init__(
        self,
        *,
        project_root: Path,
        config_file: Path,
        parent_environment: Mapping[str, str],
        binary: Path | None = None,
    ) -> None:
        self._root = project_root
        self._config_file = config_file
        self._parent = dict(parent_environment)
        self._binary = binary
        self._cli: GhCli | None = None

    @property
    def provider_id(self) -> str:
        """Provider name recorded on every anchor."""
        return PROVIDER_ID

    async def list_pull_requests(
        self,
        project_id: ProjectId,
        repository: str,
        *,
        since_number: int | None = None,
        limit: int = 100,
    ) -> tuple[ReviewEvent, ...]:
        """Pull requests, newest first, for one allowlisted public repository.

        ``since_number`` is the incremental handle: the read stops at the first
        pull request whose number is at or below it. Numbers are assigned in
        creation order and the query is ordered by creation, so that is the same
        boundary the caller means.

        Raises:
            ReviewIngestRefusedError: For every refusal this adapter has, each
                carrying its own grade and the recorded remedy for it.
        """
        entry = self._allowlisted(repository)
        if limit < 1 or limit > MAX_PULL_REQUESTS:
            raise ReviewIngestRefusedError(
                RefusalGrade.LIMIT_EXCEEDED,
                f"Review ingestion was asked for {limit} pull requests and the recorded "
                f"cap is {MAX_PULL_REQUESTS}. The run stopped rather than quietly "
                f"returning fewer than were asked for.",
            )
        cli = await self._ready()
        owner, name = entry.split("/", 1)

        events: list[ReviewEvent] = []
        cursor: str | None = None
        for _page in range(MAX_PAGES):
            variables = {"owner": owner, "name": name, "first": str(min(PAGE_SIZE, limit))}
            if cursor is not None:
                variables["after"] = cursor
            repo = self._repository_of(
                await self._request(cli, queries.PULL_REQUESTS, variables), entry
            )
            connection = _mapping(repo.get("pullRequests"))
            for node in _nodes(connection):
                event = self._event(project_id, entry, node)
                if since_number is not None and event.number <= since_number:
                    return tuple(events)
                events.append(event)
                if len(events) >= limit:
                    return tuple(events)
            cursor = _next_cursor(connection)
            if cursor is None:
                return tuple(events)
        raise self._page_cap("pull requests", entry)

    async def get_threads(
        self, project_id: ProjectId, event: ReviewEvent
    ) -> tuple[ReviewThread, ...]:
        """Review threads for one pull request, with comments and resolution state.

        The repository is re-checked against the allowlist here rather than
        trusted from ``event``: a ``ReviewEvent`` is an ordinary value a caller
        can build, so taking its ``repository`` on faith would make the control
        depend on where the value came from.
        """
        entry = self._allowlisted(event.repository)
        cli = await self._ready()
        owner, name = entry.split("/", 1)

        threads: list[ReviewThread] = []
        cursor: str | None = None
        for _page in range(MAX_PAGES):
            variables = {
                "owner": owner,
                "name": name,
                "number": str(event.number),
                "first": str(PAGE_SIZE),
            }
            if cursor is not None:
                variables["after"] = cursor
            repo = self._repository_of(
                await self._request(cli, queries.REVIEW_THREADS, variables), entry
            )
            pull_request = _mapping(repo.get("pullRequest"))
            connection = _mapping(pull_request.get("reviewThreads"))
            threads.extend(self._thread(project_id, event, node) for node in _nodes(connection))
            cursor = _next_cursor(connection)
            if cursor is None:
                return tuple(threads)
        raise self._page_cap(f"review threads on #{event.number}", entry)

    # -- the three pre-spawn refusals, in the order ADR-0030 fixes -------------

    def _allowlisted(self, repository: str) -> str:
        """The allowlist entry, then the transport-override check. Nothing spawned yet.

        Both refusals happen before ``_ready`` is reached, which is where the
        first process would be started. The order between them is the ADR's: the
        allowlist decides whether this repository may be contacted at all, and
        the transport check decides whether a spawn would go where the vector
        says.
        """
        entry = allowlisted_repository(self._root, self._config_file, repository)
        refuse_transport_overrides(self._parent)
        return entry

    async def _ready(self) -> GhCli:
        """The probed ``gh``, memoised: located, at or above the floor, authenticated.

        Once per adapter instance rather than once per call. The authentication
        probe is itself a request, so repeating it per page would spend a caller's
        rate limit on a question already answered.
        """
        if self._cli is None:
            binary = self._binary if self._binary is not None else locate_binary(self._parent)
            cli = GhCli(binary=binary, environment=child_environment(self._parent))
            await cli.version()
            await cli.require_authenticated()
            self._cli = cli
        return self._cli

    # -- the response ---------------------------------------------------------

    async def _request(
        self, cli: GhCli, document: str, variables: Mapping[str, str]
    ) -> Mapping[str, Any]:
        """One page, as parsed JSON, or a graded refusal carrying the child's stderr."""
        outcome = await cli.graphql(document=document, variables=variables)
        if outcome.returncode != 0:
            raise ReviewIngestRefusedError(
                RefusalGrade.TOOL_FAILED,
                "The GitHub CLI refused this review-ingestion request. Its own report "
                "is in this envelope's detail and reaches nothing else.",
                detail=outcome.stderr,
            )
        try:
            payload = json.loads(outcome.stdout.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ReviewIngestRefusedError(
                RefusalGrade.TOOL_FAILED,
                "The GitHub CLI answered with something this adapter cannot read as a "
                "GraphQL response.",
                detail=outcome.stderr,
            ) from exc
        if not isinstance(payload, dict):
            raise ReviewIngestRefusedError(
                RefusalGrade.TOOL_FAILED,
                "The GitHub CLI's answer was not a GraphQL response document.",
            )
        typed: Mapping[str, Any] = payload
        return typed

    def _repository_of(self, payload: Mapping[str, Any], entry: str) -> Mapping[str, Any]:
        """The response's ``repository``, checked back against the allowlisted entry.

        Two refusals live here because both are properties of the *answer* and
        both must fire before any record is built from it: the resolved name must
        be the one that was asked for (case-folded), and the repository must be
        public.
        """
        repo = _mapping(_mapping(payload.get("data")).get("repository"))
        resolved = repo.get("nameWithOwner")
        if not isinstance(resolved, str) or not resolved:
            raise ReviewIngestRefusedError(
                RefusalGrade.TOOL_FAILED,
                f"GitHub's answer for {entry!r} named no repository, so this adapter "
                f"cannot tell which repository it describes.",
            )
        if resolved.casefold() != entry.casefold():
            raise ReviewIngestRefusedError(
                RefusalGrade.REPOSITORY_RESOLVED_ELSEWHERE,
                f"Review ingestion asked GitHub for {entry!r} and GitHub answered for "
                f"{resolved!r}. A rename redirect is followed by nobody here: the "
                f"allowlist names a repository, not wherever that name now points. "
                f"Nothing was read from the answer.",
            )
        if repo.get("isPrivate") is not False:
            raise ReviewIngestRefusedError(
                RefusalGrade.REPOSITORY_IS_PRIVATE,
                f"Review ingestion refused {entry!r}: it does not resolve as a public "
                f"repository, and this version ingests public repositories only. "
                f"Nothing was read from the answer and nothing was written.",
            )
        return repo

    def _page_cap(self, what: str, entry: str) -> ReviewIngestRefusedError:
        """The graded stop for a read that would need more than the recorded pages."""
        return ReviewIngestRefusedError(
            RefusalGrade.LIMIT_EXCEEDED,
            f"Review ingestion stopped after the recorded {MAX_PAGES}-page cap while "
            f"reading {what} for {entry!r}. It is reported rather than truncated "
            f"silently, so the partial read is not mistaken for the whole.",
        )

    # -- mapping the provider's shapes onto the domain ------------------------

    def _event(self, project_id: ProjectId, entry: str, node: Mapping[str, Any]) -> ReviewEvent:
        """One pull request as a :class:`ReviewEvent`.

        The ``repository`` recorded is the **allowlisted entry**, not the
        response's spelling: the two are equal case-folded by the check above,
        and using the configured one keeps a project's own records in one
        spelling however GitHub happens to case its answer.
        """
        merged = node.get("merged") is True
        merge_commit = _mapping(node.get("mergeCommit")).get("oid")
        if merged and not isinstance(merge_commit, str):
            raise ReviewIngestRefusedError(
                RefusalGrade.TOOL_FAILED,
                f"GitHub reported pull request {entry}#{node.get('number')} as merged "
                f"with no merge commit, which is not a pull request this adapter can "
                f"record honestly.",
            )
        rollup = _nodes(_mapping(node.get("commits")))
        state = None
        if rollup:
            status = _mapping(_mapping(rollup[0].get("commit")).get("statusCheckRollup"))
            state = status.get("state") if isinstance(status.get("state"), str) else None
        return ReviewEvent(
            project_id=project_id,
            provider=PROVIDER_ID,
            repository=entry,
            number=_integer(node.get("number"), "pull request number"),
            title=_text(node.get("title")),
            author=_participant(node.get("author")),
            created_at=_instant(node.get("createdAt"), "createdAt"),
            url=_text(node.get("url")),
            head_commit=_text(node.get("headRefOid")),
            base_commit=_text(node.get("baseRefOid")),
            merged=merged,
            merge_commit=merge_commit if isinstance(merge_commit, str) else None,
            merged_at=_optional_instant(node.get("mergedAt")),
            ci_successful=queries.ci_outcome(state),
            linked_issue_ids=tuple(
                str(_integer(issue.get("number"), "linked issue number"))
                for issue in _nodes(_mapping(node.get("closingIssuesReferences")))
            ),
        )

    def _thread(
        self, project_id: ProjectId, event: ReviewEvent, node: Mapping[str, Any]
    ) -> ReviewThread:
        """One review thread, its comments, and its resolution if it has one."""
        external_id = _required_text(node.get("id"), "review thread id")
        comments = _mapping(node.get("comments"))
        if _mapping(comments.get("pageInfo")).get("hasNextPage") is True:
            raise ReviewIngestRefusedError(
                RefusalGrade.LIMIT_EXCEEDED,
                f"Review thread {external_id} on {event.repository}#{event.number} "
                f"carries more than the recorded {MAX_COMMENTS_PER_THREAD}-comment cap. "
                f"The read stopped rather than recording a thread that looks whole and "
                f"is not.",
            )
        built = tuple(_comment(comment) for comment in _nodes(comments))
        if not built:
            raise ReviewIngestRefusedError(
                RefusalGrade.TOOL_FAILED,
                f"GitHub returned review thread {external_id} on "
                f"{event.repository}#{event.number} with no comments, which is not a "
                f"thread this adapter can record.",
            )
        resolved = node.get("isResolved") is True
        state = (
            ReviewThreadState.RESOLVED
            if resolved
            else ReviewThreadState.OUTDATED
            if node.get("isOutdated") is True
            else ReviewThreadState.OPEN
        )
        return ReviewThread(
            external_id=external_id,
            project_id=project_id,
            event_key=event.external_key,
            file_path=node.get("path") if isinstance(node.get("path"), str) else None,
            comments=built,
            state=state,
            # `resolved_at` is `None` and always will be: the API object carries
            # no resolution timestamp (ADR-0030 decision 5), and the honest value
            # for a quantity the provider does not record is the unknown one.
            resolution=ReviewResolution(
                state=ReviewThreadState.RESOLVED,
                resolved_by=_optional_participant(node.get("resolvedBy")),
            )
            if resolved
            else None,
            line_start=_optional_integer(node.get("startLine")),
            line_end=_optional_integer(node.get("line")),
            # The thread's anchor commit is the first comment's `originalCommit`:
            # a thread has no commit of its own, and the first comment is the one
            # that opened it against a diff.
            commit_sha=_optional_text(
                _mapping(_nodes(comments)[0].get("originalCommit")).get("oid")
            ),
        )


# -- reading a response's shapes without trusting them ------------------------


def _mapping(value: object) -> Mapping[str, Any]:
    """``value`` as a mapping, or an empty one.

    Every field below is read through this rather than indexed, because a GraphQL
    response is a document from somewhere else: a ``null`` where an object was
    expected is an ordinary answer, not a fault, and a ``KeyError`` escaping this
    adapter would be the traceback clause 9 forbids.
    """
    return value if isinstance(value, dict) else {}


def _nodes(connection: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """A connection's ``nodes``, keeping only the members that are objects."""
    nodes = connection.get("nodes")
    if not isinstance(nodes, list):
        return []
    return [node for node in nodes if isinstance(node, dict)]


def _next_cursor(connection: Mapping[str, Any]) -> str | None:
    """The cursor for the next page, or ``None`` when this was the last.

    A cursor is an opaque string this adapter hands back in a typed variable
    (clause 6). It chooses no destination: the vector is unchanged but for the
    value of ``after``.
    """
    page_info = _mapping(connection.get("pageInfo"))
    cursor = page_info.get("endCursor")
    if page_info.get("hasNextPage") is True and isinstance(cursor, str) and cursor:
        return cursor
    return None


def _text(value: object) -> str:
    """A string field, or the empty string. Author-controlled text is never parsed."""
    return value if isinstance(value, str) else ""


def _optional_text(value: object) -> str | None:
    """A string field, or ``None`` -- for a field the provider is allowed to omit."""
    return value if isinstance(value, str) and value else None


def _required_text(value: object, field: str) -> str:
    """A string field a record's identity depends on, refused when it is missing.

    The domain types raise ``InvariantViolationError`` on an empty identifier,
    and that exception would leave this adapter as the traceback clause 9
    forbids. Refusing here turns the same fact into a graded envelope with a
    remedy.
    """
    text = _text(value)
    if not text:
        raise ReviewIngestRefusedError(
            RefusalGrade.TOOL_FAILED,
            f"GitHub's answer carried no {field}, so this adapter cannot identify the "
            f"record it belongs to.",
        )
    return text


def _integer(value: object, field: str) -> int:
    """An integer field, refused rather than coerced when it is not one."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ReviewIngestRefusedError(
            RefusalGrade.TOOL_FAILED,
            f"GitHub's answer carried no readable {field}, so this adapter cannot "
            f"record the item it belongs to.",
        )
    return value


def _optional_integer(value: object) -> int | None:
    """A line number, or ``None``: GitHub leaves them null on an outdated thread."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _instant(value: object, field: str) -> datetime:
    """A required timestamp, refused rather than fabricated when it cannot be read."""
    parsed = _optional_instant(value)
    if parsed is None:
        raise ReviewIngestRefusedError(
            RefusalGrade.TOOL_FAILED,
            f"GitHub's answer carried no readable {field}, and this adapter records no "
            f"timestamp it did not receive.",
        )
    return parsed


def _optional_instant(value: object) -> datetime | None:
    """An ISO-8601 timestamp, or ``None``. Never the ingestion time as a stand-in."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _participant(actor: object) -> ReviewParticipant:
    """An author, with GitHub's ``ghost`` standing in for a deleted account."""
    resolved = _optional_participant(actor)
    if resolved is not None:
        return resolved
    return ReviewParticipant(
        provider=PROVIDER_ID, external_id=GHOST_LOGIN, display_name=GHOST_LOGIN
    )


def _optional_participant(actor: object) -> ReviewParticipant | None:
    """An actor as a participant, or ``None`` when the provider recorded none.

    ``external_id`` is the node id when the response carries one and the login
    otherwise. The login is author-visible and can be changed by its owner, so it
    is the weaker identity -- which is exactly why the id is preferred and the
    display name is kept separately: redaction replaces a display name without
    breaking the identity graph.
    """
    fields = _mapping(actor)
    if not fields:
        return None
    login = _text(fields.get("login"))
    node_id = _text(fields.get("id"))
    external_id = node_id or login
    if not external_id:
        return None
    return ReviewParticipant(
        provider=PROVIDER_ID, external_id=external_id, display_name=login or external_id
    )


def _comment(node: Mapping[str, Any]) -> ReviewComment:
    """One comment. The body crosses as bytes-in-a-string and is never interpreted."""
    return ReviewComment(
        external_id=_required_text(node.get("id"), "comment id"),
        author=_participant(node.get("author")),
        body=_text(node.get("body")),
        created_at=_instant(node.get("createdAt"), "comment createdAt"),
        # `category` is a classification, and classification is FR-V2's -- out of
        # this slice and out of this path entirely (FR-V5).
        category=None,
    )
