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
comment body, a pull request title and description, a label, a head branch name,
a milestone name, a display name, a file path as received -- is copied into the
domain record as data. In particular a received ``path`` is never joined into
a filesystem path here, a label's value decides nothing (ADR-0019, discharged by
ADR-0030 decision 3), and this slice writes no file at all.

**What an answer may be read as lives next door**, in ``response.py``: every
field below goes through one of its helpers rather than being indexed, so a
``null`` where an object was expected is an ordinary answer and never a
``KeyError`` escaping as the traceback clause 9 forbids. This module decides what
to ask and when to refuse asking; that one decides what an answer is allowed to
mean.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, final

from theurian.domain.enums import ReviewThreadState
from theurian.domain.identifiers import ProjectId
from theurian.domain.review import (
    ReviewComment,
    ReviewEvent,
    ReviewResolution,
    ReviewThread,
)
from theurian.domain.review_ingest import (
    RefusalGrade,
    ReviewIngestRefusedError,
    bounded_echo,
    bounded_quote,
)
from theurian.infrastructure.github import queries, response
from theurian.infrastructure.github.environment import child_environment
from theurian.infrastructure.github.gh_cli import GhCli, locate_binary
from theurian.infrastructure.github.limits import (
    MAX_COMMENTS_PER_THREAD,
    MAX_LABELS_PER_PULL_REQUEST,
    MAX_LINKED_ISSUES,
    MAX_PAGES,
    MAX_PULL_REQUESTS,
    PAGE_SIZE,
)
from theurian.infrastructure.github.response import PROVIDER_ID
from theurian.infrastructure.github.transport_guard import refuse_transport_overrides
from theurian.security.review_allowlist import allowlisted_repository


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
        # `limit` is the caller's own integer and it is echoed through
        # `bounded_echo` for both reasons that helper exists: `str()` of an
        # integer is not total past the interpreter's digit limit -- a raw
        # f-string here raised `ValueError` out of a refusal path -- and a 4300
        # digit one rendered fine and then pushed the sentence past the type's
        # cut, so the summary lost "the recorded cap is 500" and the reader lost
        # the number to act on.
        #
        # Two summaries, one grade. An operator does the same thing about either
        # -- change `limit` -- and `RefusalGrade`'s membership is coarse on
        # purpose, but "the recorded cap is 500, so the run stopped rather than
        # returning fewer than were asked for" is not true of a request for zero.
        if limit < 1:
            raise ReviewIngestRefusedError(
                RefusalGrade.LIMIT_EXCEEDED,
                f"Review ingestion was asked for {bounded_echo(limit)} pull requests, "
                f"and there is no read of fewer than one to perform. Nothing was "
                f"spawned.",
            )
        if limit > MAX_PULL_REQUESTS:
            raise ReviewIngestRefusedError(
                RefusalGrade.LIMIT_EXCEEDED,
                f"Review ingestion was asked for {bounded_echo(limit)} pull requests "
                f"and the recorded cap is {MAX_PULL_REQUESTS}. The run stopped rather "
                f"than quietly returning fewer than were asked for.",
            )
        cli = await self._ready()
        owner, name = entry.split("/", 1)

        events: list[ReviewEvent] = []
        cursor: str | None = None
        for _page in range(MAX_PAGES):
            variables: dict[str, str | int] = {
                "owner": owner,
                "name": name,
                "first": min(PAGE_SIZE, limit),
            }
            if cursor is not None:
                variables["after"] = cursor
            repo = self._repository_of(
                await self._request(cli, queries.PULL_REQUESTS, variables), entry
            )
            connection = response.mapping(repo.get("pullRequests"))
            for node in response.nodes(connection):
                event = self._event(project_id, entry, node)
                if since_number is not None and event.number <= since_number:
                    return tuple(events)
                events.append(event)
                if len(events) >= limit:
                    return tuple(events)
            cursor = response.next_cursor(connection, "pull requests")
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
        # Named once: the page cap's report and a cursor refusal describe the same
        # read, and two spellings of it would drift apart.
        what = f"review threads on #{bounded_echo(event.number)}"
        for _page in range(MAX_PAGES):
            variables: dict[str, str | int] = {
                "owner": owner,
                "name": name,
                "number": event.number,
                "first": PAGE_SIZE,
            }
            if cursor is not None:
                variables["after"] = cursor
            repo = self._repository_of(
                await self._request(cli, queries.REVIEW_THREADS, variables), entry
            )
            pull_request = response.mapping(repo.get("pullRequest"))
            connection = response.mapping(pull_request.get("reviewThreads"))
            threads.extend(
                self._thread(project_id, event, node) for node in response.nodes(connection)
            )
            cursor = response.next_cursor(connection, what)
            if cursor is None:
                return tuple(threads)
        raise self._page_cap(what, entry)

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
        self, cli: GhCli, document: str, variables: Mapping[str, str | int]
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
        repo = response.mapping(response.mapping(payload.get("data")).get("repository"))
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
                f"{bounded_quote(resolved)}. A rename redirect is followed by nobody "
                f"here: the allowlist names a repository, not wherever that name now "
                f"points. Nothing was read from the answer.",
            )
        # **The one Boolean here that does not go through `response.boolean`, and it is
        # stricter rather than looser.** `response.boolean` refuses a non-bool; this
        # refuses everything that is not literally `False`, so an absent field, a
        # `null`, the string `"false"` and the integer `0` are all read as "not
        # shown to be public" and the repository is declined. The direction of
        # the error is the whole point: an unreadable answer about visibility
        # must not become an ingest.
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

        Two graded stops fire before any record exists -- beside the shape checks
        each field read carries, :func:`response.boolean` among them -- and both are there
        for the reason :meth:`_comments_of` has its own: a record that *looks*
        whole and is not is worse than a refusal naming what could not be read. A
        merged pull request must carry its merge commit, and neither of the two
        single-page connections may overflow its cap
        (:meth:`_refuse_a_capped_overflow`).

        ``milestone`` is the one field here the provider may answer with nothing,
        and it maps to ``None`` rather than to a name nobody chose (ADR-0030
        decision 5). ``body``, ``labels`` and ``head_ref_name`` are read as the
        author-controlled content they are: carried verbatim, interpreted by
        nothing.
        """
        merged = response.boolean(node.get("merged"), "`merged`")
        merge_commit = response.mapping(node.get("mergeCommit")).get("oid")
        if merged and not isinstance(merge_commit, str):
            raise ReviewIngestRefusedError(
                RefusalGrade.TOOL_FAILED,
                f"GitHub reported pull request {entry}#{bounded_echo(node.get('number'))} "
                f"as merged with no merge commit, which is not a pull request this "
                f"adapter can record honestly.",
            )
        self._refuse_a_capped_overflow(entry, node)
        rollup = response.nodes(response.mapping(node.get("commits")))
        state = None
        if rollup:
            commit = response.mapping(rollup[0].get("commit"))
            status = response.mapping(commit.get("statusCheckRollup"))
            state = status.get("state") if isinstance(status.get("state"), str) else None
        return ReviewEvent(
            project_id=project_id,
            provider=PROVIDER_ID,
            repository=entry,
            number=response.positive_integer(node.get("number"), "pull request number"),
            title=response.text(node.get("title")),
            body=response.text(node.get("body")),
            author=response.participant(node.get("author")),
            created_at=response.instant(node.get("createdAt"), "createdAt"),
            url=response.text(node.get("url")),
            head_commit=response.text(node.get("headRefOid")),
            base_commit=response.text(node.get("baseRefOid")),
            head_ref_name=response.text(node.get("headRefName")),
            labels=tuple(
                response.required_text(label.get("name"), "label name")
                for label in response.nodes(response.mapping(node.get("labels")))
            ),
            merged=merged,
            merge_commit=merge_commit if isinstance(merge_commit, str) else None,
            merged_at=response.optional_instant(node.get("mergedAt")),
            ci_successful=queries.ci_outcome(state),
            linked_issue_ids=tuple(
                str(response.positive_integer(issue.get("number"), "linked issue number"))
                for issue in response.nodes(response.mapping(node.get("closingIssuesReferences")))
            ),
            milestone=response.optional_text(response.mapping(node.get("milestone")).get("title")),
        )

    def _refuse_a_capped_overflow(self, entry: str, node: Mapping[str, Any]) -> None:
        """The two connections a pull request node carries one page of, each capped.

        ``closingIssuesReferences`` and ``labels`` are asked for a single page and
        this adapter follows no cursor into either, so an overflow is **reported**
        rather than recorded: an event naming half a pull request's closing
        issues, or carrying half its labels past the ingestion scan that reads
        them, looks whole and is not.

        Each is checked two ways, because either arm alone leaves the truncation
        reachable. ``hasNextPage`` is the provider saying there is more -- read
        through :func:`response.boolean`, so an unreadable flag refuses instead of
        folding into "there is no more" -- and a node count past the cap is what
        arrives if the ``first:`` literal in the document and the constant here
        ever drift apart.
        """
        for connection, cap, members in (
            (
                response.mapping(node.get("closingIssuesReferences")),
                MAX_LINKED_ISSUES,
                "closing issue",
            ),
            (response.mapping(node.get("labels")), MAX_LABELS_PER_PULL_REQUEST, "label"),
        ):
            if (
                response.boolean(
                    response.mapping(connection.get("pageInfo")).get("hasNextPage"),
                    f"`hasNextPage` on a pull request's {members}s",
                )
                or len(response.nodes(connection)) > cap
            ):
                raise ReviewIngestRefusedError(
                    RefusalGrade.LIMIT_EXCEEDED,
                    f"Pull request {entry}#{bounded_echo(node.get('number'))} carries more "
                    f"than the recorded {cap}-{members} cap. The read stopped rather than "
                    f"recording an event that looks whole and is not.",
                )

    def _thread(
        self, project_id: ProjectId, event: ReviewEvent, node: Mapping[str, Any]
    ) -> ReviewThread:
        """One review thread, its comments, and its resolution if it has one."""
        external_id = response.required_text(node.get("id"), "review thread id")
        comments = response.mapping(node.get("comments"))
        built = self._comments_of(comments, external_id, event)
        resolved = response.boolean(node.get("isResolved"), "`isResolved` on a review thread")
        # Read whether or not it is reached: a thread whose `isOutdated` is
        # unreadable is an answer this adapter cannot check, and only checking it
        # on the unresolved branch would make that depend on the other flag.
        outdated = response.boolean(node.get("isOutdated"), "`isOutdated` on a review thread")
        state = (
            ReviewThreadState.RESOLVED
            if resolved
            else ReviewThreadState.OUTDATED
            if outdated
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
                resolved_by=response.optional_participant(node.get("resolvedBy")),
            )
            if resolved
            else None,
            line_start=response.optional_integer(node.get("startLine")),
            line_end=response.optional_integer(node.get("line")),
            # The thread's anchor commit is the first comment's `originalCommit`:
            # a thread has no commit of its own, and the first comment is the one
            # that opened it against a diff.
            commit_sha=response.optional_text(
                response.mapping(response.nodes(comments)[0].get("originalCommit")).get("oid")
            ),
        )

    def _comments_of(
        self, comments: Mapping[str, Any], external_id: str, event: ReviewEvent
    ) -> tuple[ReviewComment, ...]:
        """One thread's comments, refusing rather than recording a partial thread.

        Two graded stops, and both exist because a record that *looks* whole and
        is not is worse than a refusal that says which thread it was. The
        provider paginates comments, so a thread past the recorded cap would
        otherwise arrive silently truncated; and a thread with none at all is
        not a shape ``ReviewThread`` can hold. The cap's own flag is read through
        :func:`response.boolean`, so an unreadable ``hasNextPage`` is a third refusal
        rather than a quiet "there is no more".
        """
        if response.boolean(
            response.mapping(comments.get("pageInfo")).get("hasNextPage"),
            "`hasNextPage` on a review thread's comments",
        ):
            raise ReviewIngestRefusedError(
                RefusalGrade.LIMIT_EXCEEDED,
                f"Review thread {bounded_echo(external_id)} on {event.repository}"
                f"#{bounded_echo(event.number)} carries more than the recorded "
                f"{MAX_COMMENTS_PER_THREAD}-comment cap. "
                f"The read stopped rather than recording a thread that looks whole and "
                f"is not.",
            )
        built = tuple(response.comment(comment) for comment in response.nodes(comments))
        if not built:
            raise ReviewIngestRefusedError(
                RefusalGrade.TOOL_FAILED,
                f"GitHub returned review thread {bounded_echo(external_id)} on "
                f"{event.repository}#{bounded_echo(event.number)} with no comments, "
                f"which is not a thread this adapter can record.",
            )
        return built
