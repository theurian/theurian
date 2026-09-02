"""Shaping one served review finding: the bounds, the vocabularies, the wire row.

The `review.findings` half that is not a tool registration (ADR-0029 phase-2
slice-3). ``mcp/tools.py`` owns the registration, resolves the project, and
constructs the store; this module owns what a caller may *ask* for and what a
finding looks like on the wire -- the same split ``mcp/results.py`` already holds
for a knowledge hit, and for the same reason: a shape constructed in two places
drifts in one of them.

**The trust triple comes from one place.** A served ``findingText`` is authored
commit text -- untrusted content in the sense SEC-15 and ADR-0029 decision 3
define -- so every row carries
:data:`~theurian.mcp.results.SAFETY`, imported rather than respelled. A second
literal of those three keys is how one surface ends up labelling and another
not, which is a knowledge body reaching an agent with nothing saying it is a
document.

**Every bound is here, and each is a refusal rather than a clamp.**
``knowledge.search`` clamps an out-of-range ``limit``, deliberately: a caller
asking for a million results wants "as much as you have", and that tool answers
a *ranked* question where fewer results is a worse answer, not a wrong one. This
tool answers a *filtered* question with no paging, so a silent clamp would let a
caller read "these are the 20 findings matching my filter" off a response that
was truncated from more. Naming the bound is the honest form; the caller narrows
with a filter.

**Refusals name the bound and never echo an over-long input.** A filter longer
than :data:`MAX_FILTER_CHARS` is reported by its length, never quoted -- the
amplifier ``MAX_QUERY_CHARS`` and ``ItemId`` already close for ``query`` and
``itemId``. Below that bound a caller's own token is quoted back, because a typo
is what the refusal exists to make visible and the bytes are the caller's own.

**The published input schema types ``reviewer`` and ``severity`` as strings, not
as enums, and that is a decision rather than an omission.** Annotating them as
``Literal[...]`` would put the vocabularies in ``tools/list`` -- read for real
against a running daemon, the SDK derives each parameter's schema straight from
its annotation -- but it would also move the *rejection* into the SDK's argument
validation, before any code here runs. What comes back then is a message this
module neither writes nor bounds: it cannot name the omit-the-filter remedy, it
cannot be held to the length discipline above, and it is one more error shape
this surface would have to reason about under SEC-13. The vocabulary is
published in ``schemas/mcp/review-findings-response.schema.json`` and in
``docs/protocol/mcp-tools.md`` instead, and the refusal a caller actually reads
is the one written here.
"""

from __future__ import annotations

import re
from typing import Any, Final

from theurian.domain.errors import TheurianError
from theurian.domain.ports.review_finding_store import FindingQuery, StoredFinding
from theurian.domain.review_finding import FindingSeverity, ReviewerToken
from theurian.mcp.results import SAFETY

#: How many findings one call may return. A hard cap, not a clamp: see the module
#: docstring. 100 against a corpus of 502 accepted findings (this repository's own
#: history, ``origin/main`` @ ``141cf6f``, 2026-09-02) is a page a caller can read
#: and a bound the daemon can promise; a caller wanting more narrows by filter,
#: which is the axis this tool actually offers.
MAX_FINDINGS_LIMIT: Final = 100

#: What a caller gets without asking. Smaller than the cap on purpose: the common
#: call is "what has been said about X", and twenty one-line findings is a page
#: rather than a context-budget event.
DEFAULT_FINDINGS_LIMIT: Final = 20

#: The bound on every string filter, applied before anything is matched or
#: echoed. One number rather than six, because every value on the other side of
#: these predicates is short: a commit sha is 40 or 64 characters, a reviewer
#: token 11, a severity 8, and a finding is one line. 200 is longer than any of
#: them and short enough that a refusal quoting a caller's token cannot become an
#: amplifier.
MAX_FILTER_CHARS: Final = 200

#: A commit sha as git's ``%H`` writes it, which is what the store keys on:
#: lowercase hex, 40 characters for a SHA-1 repository and 64 for a SHA-256 one.
#:
#: Validated rather than passed through, because the alternative is a **false
#: absence**: a caller pasting the seven-character short sha their terminal
#: printed would get ``count: 0`` -- indistinguishable from "no findings on that
#: commit" -- and act on it. A refusal naming the form is the answer that cannot
#: be misread. Prefix matching was the other way out and is deliberately not
#: taken: it would add a second matching mode to a surface whose whole contract
#: is exact equality on stored columns.
_COMMIT_SHA = re.compile(r"\A(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")


class FindingsQueryError(TheurianError):
    """A caller asked for something outside this tool's bounds or vocabularies.

    A :class:`~theurian.domain.errors.TheurianError`, so ``mcp/tools.py``'s
    ``_forwarding`` seam converts it into the SDK's ``ToolError`` and the message
    reaches the caller under mcp >= 2.1 (#491). That seam forwards ``str(exc)``
    and drops ``remedy``, so **the message is self-contained**: it names the bound
    or the vocabulary and what to send instead, rather than leaving the cure on
    an attribute the wire has no field for.

    ``remedy`` is carried anyway, because ``theurian doctor`` and the CLI's own
    error rendering read it, and an error type in this codebase that carries none
    is one somebody has to remember is special.
    """

    def __init__(self, detail: str) -> None:
        self.remedy = (
            "Call `review.findings` again with a value inside the bound the message names."
        )
        super().__init__(detail)


def _bounded(name: str, value: str) -> str:
    """``value``, or a refusal naming the bound -- never quoting an over-long one."""
    if len(value) > MAX_FILTER_CHARS:
        raise FindingsQueryError(
            f"`{name}` is {len(value)} characters long, and no findings filter may be "
            f"longer than {MAX_FILTER_CHARS}. Nothing was searched. Send a shorter "
            f"value, or omit `{name}` to not filter on it."
        )
    if not value:
        raise FindingsQueryError(
            f"`{name}` is empty, which matches nothing rather than everything. "
            f"Omit `{name}` to not filter on it."
        )
    return value


def _reviewer(value: str | None) -> ReviewerToken | None:
    """One of the three governed reviewer tokens, or a refusal naming all three.

    The historical alias ``code`` (ADR-0029 Amendment 1) is deliberately *not*
    accepted here. It exists because signed history cannot be edited and the
    parser must read the lines already in it; the parser normalises it, so every
    stored row carries a canonical token and a query for ``code`` would match
    nothing. Accepting it would be a second spelling for one value on a surface
    with no frozen installed base -- and the refusal below names the three
    canonical tokens, which is what a caller who read a trailer needs.
    """
    if value is None:
        return None
    token = _bounded("reviewer", value)
    try:
        return ReviewerToken(token)
    except ValueError as exc:
        expected = ", ".join(member.value for member in ReviewerToken)
        raise FindingsQueryError(
            f"`reviewer` must be one of {expected}; got {token!r}. Nothing was "
            f"searched. Omit `reviewer` to search every reviewer's findings."
        ) from exc


def _severity(value: str | None) -> FindingSeverity | None:
    """One of the four governed severities, or a refusal naming all four."""
    if value is None:
        return None
    token = _bounded("severity", value)
    try:
        return FindingSeverity(token)
    except ValueError as exc:
        expected = ", ".join(member.value for member in FindingSeverity)
        raise FindingsQueryError(
            f"`severity` must be one of {expected}; got {token!r}. The tokens are "
            f"upper-case, as the trailer writes them. Nothing was searched. Omit "
            f"`severity` to search every severity."
        ) from exc


def _commit_sha(value: str | None) -> str | None:
    """A full commit sha, or a refusal naming the form -- never an empty answer."""
    if value is None:
        return None
    candidate = _bounded("commitSha", value)
    if not _COMMIT_SHA.match(candidate):
        raise FindingsQueryError(
            f"`commitSha` must be a full commit sha -- 40 or 64 lower-case hex "
            f"characters, as `git log --format=%H` prints it; got {candidate!r}. A "
            f"short sha is refused rather than searched, because it would match no "
            f"row and the empty answer would read as 'no findings on that commit'. "
            f"Nothing was searched."
        )
    return candidate


def _pull_request(value: int | None) -> int | None:
    """A PR number, or a refusal: there is no pull request numbered zero."""
    if value is None:
        return None
    if value < 1:
        raise FindingsQueryError(
            f"`pullRequest` must be a positive number; got {value}. Nothing was "
            f"searched. Omit `pullRequest` to search findings from every PR."
        )
    return value


def _limit(value: int) -> int:
    """The caller's page size, or a refusal naming the bound (never a clamp)."""
    if value < 1 or value > MAX_FINDINGS_LIMIT:
        raise FindingsQueryError(
            f"`limit` must be between 1 and {MAX_FINDINGS_LIMIT}; got {value}. "
            f"Nothing was searched. This is a refusal rather than a silent clamp: a "
            f"truncated answer to a filtered question reads as the whole answer, so "
            f"narrow with a filter instead of asking for a larger page."
        )
    return value


def build_query(  # noqa: PLR0913 - one parameter per published filter
    *,
    reviewer: str | None,
    severity: str | None,
    family: str | None,
    specialist: str | None,
    commit_sha: str | None,
    pull_request: int | None,
    text_contains: str | None,
    limit: int,
) -> FindingQuery:
    """A caller's arguments as a store query, or a refusal naming what was wrong.

    **Every bound is checked before anything is read.** The store is not opened,
    no row is scanned, and no file is touched until this returns -- so a refusal
    here costs a caller nothing and buys the daemon nothing to do (T-6).

    The order within a single filter is length first, then vocabulary: an
    over-long token is reported by its length, and only a token already inside
    the bound is quoted back.
    """
    return FindingQuery(
        limit=_limit(limit),
        reviewer=_reviewer(reviewer),
        severity=_severity(severity),
        family=None if family is None else _bounded("family", family),
        specialist=None if specialist is None else _bounded("specialist", specialist),
        commit_sha=_commit_sha(commit_sha),
        pull_request=_pull_request(pull_request),
        text_contains=None if text_contains is None else _bounded("q", text_contains),
    )


def finding_row(finding: StoredFinding) -> dict[str, Any]:
    """One stored finding as the wire carries it: every column, plus the triple.

    camelCase because that is this surface's convention, and **every key is
    always present**, ``null`` included: a field that appears only when it has a
    value cannot be told apart from a server that predates the field, and
    ``pullRequest``/``family``/``specialist`` are exactly the three that are
    ``None`` on every row the shipped source produces today (ADR-0029 D5).

    ``findingText`` is authored commit text and rides under the SEC-15 triple
    like any other knowledge body: an instruction hidden in a reviewer's line
    arrives marked ``mayContainInstructions: true``, which is a not-executed
    guarantee rather than a not-disclosed one (ADR-0029 decision 3, T-3).

    Nothing here is computed: each value is the stored column, so no published
    field can be a function of anything but the row it came from.
    """
    return {
        "commitSha": finding.commit_sha,
        "position": finding.position,
        "reviewer": finding.reviewer,
        "severity": finding.severity,
        "findingText": finding.finding_text,
        "provider": finding.provider,
        "sourceUri": finding.source_uri,
        "committedAt": finding.committed_at,
        "pullRequest": finding.pull_request,
        "family": finding.family,
        "specialist": finding.specialist,
        **SAFETY,
    }


def findings_payload(findings: tuple[StoredFinding, ...]) -> dict[str, Any]:
    """The whole response: the rows, and how many of them there are.

    **Two members, and the shortness is the point.** Every value here is a
    function of the served rows alone, which is ADR-0029's closure stated at the
    response rather than at a field:

    * ``count`` is ``len(findings)`` -- the rows in *this* response, not a total
      before ``limit`` and not a count of anything the caller did not receive;
    * ``findings`` is those rows.

    Three members that were considered and are deliberately absent. A **rejected
    count** would be a statistic over rows this tool never serves, so a malformed
    trailer somebody committed would move a served value -- the "statistic over
    rows the caller may not see" family, opened for no gain. The **store's stamp**
    (schema version, parser stamp) would publish build metadata whose only
    purpose is a staleness decision this tool has already made: a stale store is
    refused, so a served response has nothing to say about it. And an **echo of
    the filters** would restate the caller's own request back at it, which
    reads like confirmation and drifts the first time a filter is renamed.

    The differential this shape is written to survive: a store holding a rejected
    row and a store that never held one answer identically, to every query.
    """
    return {"count": len(findings), "findings": [finding_row(f) for f in findings]}
