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
``itemId``. A *number* past :data:`MAX_ECHOED_DIGITS` digits is reported by its
digit count, for the same reason and one more: rendering it is where the refusal
itself used to crash. Below either bound a caller's own value is quoted back,
because a typo is what the refusal exists to make visible and the bytes are the
caller's own.

**Every refusal path here is total.** A refusal that can raise while it is being
built is not a refusal, and this surface shipped two of them: an integer past
CPython's 4,300-digit string limit died in the f-string of its own error message,
and a ``pullRequest`` past 2**63 passed every check here and died at the SQLite
bind. Both are closed by measuring an input rather than rendering it, and by
bounding it at the boundary rather than where it is used (:func:`_digits`,
:data:`MAX_PULL_REQUEST`). The same rule is why a value no stored column can hold
-- a NUL byte, an unpaired surrogate -- is refused here rather than passed down to
fail somewhere without a remedy (:func:`_transportable`).

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
from dataclasses import replace
from typing import Any, Final

from theurian.domain.errors import TheurianError
from theurian.domain.ports.review_finding_store import FindingQuery, StoredFinding
from theurian.domain.review_finding import FindingSeverity, ReviewerToken
from theurian.mcp.results import SAFETY

#: How many findings one call may return. A hard cap, not a clamp: see the module
#: docstring. 100 against a corpus of 502 accepted findings (this repository's own
#: history, ``origin/main`` @ ``141cf6f``, 2026-09-02) is a page a caller can read
#: and a bound the daemon can promise.
#:
#: **A caller that hits the cap is told so, rather than told to narrow.** The
#: earlier sentence here said the remedy was a narrower filter, and it was false
#: against the very corpus it cited: ``(code-review, MEDIUM)`` matches 128 rows,
#: and the axes left to narrow on -- ``pullRequest``, ``family``, ``specialist``
#: -- are ``null`` on every row this build produces. So the response carries
#: ``truncated`` (see :func:`findings_payload`), which is the honest signal; the
#: filters that do work (``reviewer``, ``severity``, ``commitSha``, ``q``) are
#: still how a caller narrows when one of them applies.
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

#: The largest ``pullRequest`` this surface accepts, which is the largest value
#: the column behind it can hold: SQLite's INTEGER is a signed 64-bit value, and
#: ``sqlite3`` raises ``OverflowError`` binding anything past it.
#:
#: Bounded here rather than left to that bind, because the bind is not a graded
#: refusal: it is an ``OverflowError`` no layer catches, so ``pullRequest =
#: 2**63`` reached the caller as a crash rather than as a refusal naming a bound
#: (PR #504 round 1, R1-2 face i). The number is not a policy choice -- a PR
#: number past it cannot be stored, so it could never match a row.
MAX_PULL_REQUEST: Final = 2**63 - 1

#: How many decimal digits of a caller's own number a refusal will quote back.
#:
#: The "never echo an over-long input" rule of the module docstring, extended to
#: integers. A string filter past :data:`MAX_FILTER_CHARS` is reported by its
#: length; a number past this is reported by its digit count, for the same reason
#: -- a refusal that interpolated a 4,300-digit integer would be a reflector of
#: whatever the caller sent, and building it is where the refusal itself crashed
#: (R1-2 face ii). 20 is one digit more than :data:`MAX_PULL_REQUEST` has, so
#: every value a caller could plausibly have meant is still quoted verbatim.
MAX_ECHOED_DIGITS: Final = 20

#: The size past which :func:`_digits` stops proving its own answer, in bits.
#:
#: The digit count is derived from ``int.bit_length()`` and then *corrected* by
#: comparing against a power of ten -- exact, and cheap while the power is small.
#: ``10 ** 6_000_000`` costs 3.9 seconds to compute (measured, 2026-09-02), which
#: is a caller supplying work the daemon must do (T-6), so the correction stops
#: at this ceiling and the estimate stands above it, within one digit. Nothing on
#: the wire reaches it: ``json.loads`` refuses an integer literal past CPython's
#: 4,300-digit limit outright (measured), so an argument that large arrives only
#: from an in-process caller -- which is exactly how round 1 reproduced the crash.
_EXACT_DIGIT_BITS: Final = 1 << 20

#: ``log10(2)``, for turning a bit length into a decimal digit count.
_LOG10_OF_2: Final = 0.30102999566398119521

#: The one byte that cannot appear in any value this store holds, and that
#: silently changes what a filter means. See :func:`_transportable`.
_NUL: Final = "\x00"

#: What a caller sees for a filter on an axis this build derives no value for.
#:
#: Published as a working filter, ``pullRequest`` returned ``count: 0`` for every
#: number, because ``theurian findings build`` sets it ``NULL`` on every row --
#: an absence a caller reads as "no findings were recorded on that PR" (PR #504
#: round 1, R1-5). ``family`` and ``specialist`` are the same shape for their own
#: reasons (ADR-0029 D5), so all three are refused together and with one message.
#:
#: **A constant of the build, not of the request or the store.** It interpolates
#: nothing -- not which of the three was sent, not the value, not the project --
#: so it cannot vary with anything a caller could use to learn about content
#: (SEC-13). It changes only when a future source derives one of these axes, at
#: which point the refusal is lifted for that axis in the same change that starts
#: producing values for it.
INERT_FILTER_REFUSAL: Final = (
    "`pullRequest`, `family` and `specialist` cannot be filtered on in this build. "
    "`theurian findings build` derives none of the three from git history, so every "
    "stored row carries null for them (ADR-0029 D5) and any value here would match "
    "no finding at all -- an empty answer that reads as 'nothing was recorded' "
    "rather than 'this filter does not work yet'. Nothing was searched. Narrow with "
    "`reviewer`, `severity`, `commitSha` or `q` instead. This refusal message is a "
    "constant: it carries nothing from your request or from any project's contents."
)

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


def _digits(value: int) -> int:
    """How many decimal digits ``value`` has, without ever building its string.

    ``len(str(value))`` is not available here: CPython refuses to render an
    integer past ``sys.get_int_max_str_digits()`` (4,300 by default) and raises
    ``ValueError``. So the refusal for an absurd number crashed *while it was
    being built* -- the tool promised a graded refusal and delivered a traceback
    (R1-2 face ii). **A refusal path that can crash is not a refusal**, which is
    why the size of a number is measured rather than rendered.

    ``bit_length`` is exact and costs nothing. Turning it into a digit count is
    the inexact step -- ``floor(bits * log10(2)) + 1`` is off by one near a power
    of ten (521 such values in the first 400 exponents, measured) -- so the
    estimate is corrected against a power of ten, which is exact. The correction
    stops at :data:`_EXACT_DIGIT_BITS`, above which the answer is within one
    digit; see that constant for why nothing on the wire gets there.
    """
    magnitude = abs(value)
    if magnitude == 0:
        return 1
    estimate = int(magnitude.bit_length() * _LOG10_OF_2) + 1
    if magnitude.bit_length() <= _EXACT_DIGIT_BITS:
        if magnitude >= 10**estimate:
            estimate += 1
        elif estimate > 1 and magnitude < 10 ** (estimate - 1):
            estimate -= 1
    return estimate


def _sized(value: int) -> str:
    """``value`` as a refusal may quote it: the number itself, or only its size.

    The integer half of "never echo an over-long input". Under
    :data:`MAX_ECHOED_DIGITS` the caller's own number is quoted, because a typo is
    what the refusal exists to make visible; past it the number is described by
    its digit count and its bytes stay out of the response.
    """
    digits = _digits(value)
    if digits > MAX_ECHOED_DIGITS:
        return f"a {digits}-digit number"
    return str(value)


def _transportable(name: str, value: str) -> None:
    """Refuse a filter that no stored value can equal or that cannot cross the wire.

    Two shapes, and both are refused rather than repaired:

    **A NUL byte.** SQLite's ``patternCompare`` walks a NUL-terminated string, so
    ``q="log\\x00zzz"`` silently becomes a search for ``log`` and ``q="\\x00"``
    matches every row -- the "matched literally" promise broken without a word to
    the caller (R1-6). Refusing is honest rather than conservative: a git
    commit-message line cannot contain a NUL, so no stored value contains one and
    no legitimate filter needs one.

    **An unpaired surrogate.** ``"\\ud800"`` is a ``str`` Python accepts and UTF-8
    cannot encode, so it dies as a ``UnicodeEncodeError`` -- at the SQLite bind if
    it is a filter, or in the SDK's serializer if it were echoed back.

    **Refused, where ``knowledge.search`` folds** (``mcp/tools.py``, the
    ``encode("utf-8", "replace")`` on ``query``). That tool is a search box
    answering a ranked question, and refusing a search box is the behaviour it
    must not have; this tool answers a *filtered* question whose whole contract is
    exact equality or literal substring on stored columns. Folding a surrogate to
    ``U+FFFD`` here would search for a value the caller did not send and answer
    ``count: 0`` about it -- the false absence every other refusal on this surface
    exists to prevent. The divergence is deliberate, and it is the same reasoning
    that makes ``limit`` a refusal here and a clamp there.
    """
    if _NUL in value:
        raise FindingsQueryError(
            f"`{name}` contains a NUL byte (U+0000). No stored value can contain one -- "
            f"a git commit-message line cannot carry it -- and SQLite's pattern matcher "
            f"stops reading at it, which would silently shorten the filter instead of "
            f"matching what was sent. Nothing was searched. Send the value without it."
        )
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise FindingsQueryError(
            f"`{name}` contains a character that is not transportable text -- an "
            f"unpaired surrogate, which no UTF-8 encoder accepts -- so it cannot be "
            f"compared against a stored value or carried in this response. Nothing was "
            f"searched. Send well-formed Unicode."
        ) from exc


def _bounded(name: str, value: str) -> str:
    """``value``, or a refusal naming the bound -- never quoting an over-long one.

    Length first, because it is the amplification control (#17): a value past the
    bound is reported by its length before anything else looks at it, so no later
    refusal here can quote an unbounded string.
    """
    if len(value) > MAX_FILTER_CHARS:
        raise FindingsQueryError(
            f"`{name}` is {len(value)} characters long, and no findings filter may be "
            f"longer than {MAX_FILTER_CHARS}. Nothing was searched. Send a shorter "
            f"value, or omit `{name}` to not filter on it."
        )
    _transportable(name, value)
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
    """A PR number inside the column's own range, or a refusal naming both ends.

    There is no pull request numbered zero, and none past
    :data:`MAX_PULL_REQUEST` either: the store's column is a signed 64-bit
    integer, and ``sqlite3`` raises ``OverflowError`` binding anything wider. That
    overflow was caught by no layer, so a bound that names the ceiling is what
    turns a crash into an answer a caller can act on (R1-2 face i).
    """
    if value is None:
        return None
    if value < 1 or value > MAX_PULL_REQUEST:
        raise FindingsQueryError(
            f"`pullRequest` must be a positive number no larger than "
            f"{MAX_PULL_REQUEST}; got {_sized(value)}. Nothing was searched. Omit "
            f"`pullRequest` to search findings from every PR."
        )
    return value


def _limit(value: int) -> int:
    """The caller's page size, or a refusal naming the bound (never a clamp)."""
    if value < 1 or value > MAX_FINDINGS_LIMIT:
        raise FindingsQueryError(
            f"`limit` must be between 1 and {MAX_FINDINGS_LIMIT}; got {_sized(value)}. "
            f"Nothing was searched. This is a refusal rather than a silent clamp: a "
            f"truncated answer to a filtered question reads as the whole answer, so "
            f"narrow with a filter instead of asking for a larger page."
        )
    return value


def _refuse_inert_axes(
    *, pull_request: int | None, family: str | None, specialist: str | None
) -> None:
    """Refuse a filter on an axis this build derives no value for.

    ``pullRequest``, ``family`` and ``specialist`` are ``NULL`` on every row
    ``theurian findings build`` produces (ADR-0029 D5), so a filter on any of them
    matched nothing and answered ``count: 0`` -- the exact misreadable absence
    ``_commit_sha``'s refusal already exists to prevent, and worse here, because
    there is no value a caller could send that would work. A caller reads "no
    findings were recorded on PR 504" off an axis that has never held a value.

    **One refusal for all three, and it is a constant of the build**
    (:data:`INERT_FILTER_REFUSAL`). It does not vary with the store, the project,
    or which of the three was sent, so refusal-uniformity (SEC-13) is untouched:
    the same three arguments produce the same string on every corpus, including
    an empty one.

    **The bounds still run first**, in :func:`build_query`, and that ordering is
    deliberate. A ``pullRequest`` past the column's range has a *different* thing
    wrong with it than an inert axis, and it is the one whose refusal the caller
    can act on if the axis is ever derived; folding it away would also make
    :func:`_pull_request` unreachable through the only surface that calls it --
    a guard no input reaches, which is the shape that survives its own deletion.
    """
    if pull_request is not None or family is not None or specialist is not None:
        raise FindingsQueryError(INERT_FILTER_REFUSAL)


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
    the bound is quoted back. Across filters, every value is bounded before the
    inert-axis refusal fires -- see :func:`_refuse_inert_axes` for why that
    ordering is the one that keeps both refusals meaningful.
    """
    bounded = FindingQuery(
        limit=_limit(limit),
        reviewer=_reviewer(reviewer),
        severity=_severity(severity),
        family=None if family is None else _bounded("family", family),
        specialist=None if specialist is None else _bounded("specialist", specialist),
        commit_sha=_commit_sha(commit_sha),
        pull_request=_pull_request(pull_request),
        text_contains=None if text_contains is None else _bounded("q", text_contains),
    )
    _refuse_inert_axes(
        pull_request=bounded.pull_request,
        family=bounded.family,
        specialist=bounded.specialist,
    )
    return bounded


def max_finding_text_chars() -> int:
    """The bound on a served ``findingText``, which is ``MAX_QUERY_CHARS``.

    Derived from that constant rather than respelled, and read through a function
    because the two modules cannot import each other at module scope:
    ``mcp/tools.py`` imports this one to register the tool. Deriving it keeps one
    number governing both directions of this daemon's caller-facing text -- the
    largest string it will accept in a ``query`` and the largest it will hand back
    in a finding -- which is the number ``test_a_refusal_is_never_a_bigger_reflector
    _than_the_published_echo`` already measures this surface against.
    """
    from theurian.mcp.tools import MAX_QUERY_CHARS  # noqa: PLC0415 - import cycle

    return MAX_QUERY_CHARS


def _bounded_text(text: str) -> str:
    """A stored ``findingText`` as the wire carries it: whole, or cut and marked.

    **The one value on this surface whose size a caller does not control and the
    corpus does.** Every other bound here refuses; this one truncates, because the
    over-long input is a *stored row*, not a request -- refusing the response would
    make one planted commit message deny the whole tool, which is a worse failure
    than a marked cut.

    A finding is one trailer line, and the real corpus's longest is 193 characters
    (``origin/main``, 2026-09-02), so this never fires on authored data. What it
    bounds is the planted case: ``findingText`` is byte-preserved from a commit
    message, a commit message line has no length limit, and a 2 MiB trailer served
    at ``limit=40`` measured 83.9 MB in one response (PR #504 round 1, R1-3). The
    row count was the only dimension bounded; this is the byte dimension.

    The shape is ``knowledge.search``'s excerpt: cut at the bound, then an explicit
    marker, so a truncated value cannot be read as the whole one. The length is
    *not* that function's 280 -- an excerpt is a fragment offered so a caller can
    decide whether to fetch the rest, and there is nothing further to fetch here.
    """
    bound = max_finding_text_chars()
    if len(text) <= bound:
        return text
    # The marker `domain.retrieval.excerpt` uses, spelled here rather than shared:
    # what is mirrored is the *shape* -- cut, then say so -- and borrowing that
    # function would borrow its 280-character bound with it.
    return text[:bound] + "..."


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
    guarantee rather than a not-disclosed one (ADR-0029 decision 3, T-3). It is
    also the one field with a size bound on the way out -- see
    :func:`_bounded_text`, and note that the bound is a function of *this row's*
    own length and of nothing else.

    Nothing here is computed across rows: each value is this row's stored column,
    so no published field can be a function of anything but the row it came from.
    """
    return {
        "commitSha": finding.commit_sha,
        "position": finding.position,
        "reviewer": finding.reviewer,
        "severity": finding.severity,
        "findingText": _bounded_text(finding.finding_text),
        "provider": finding.provider,
        "sourceUri": finding.source_uri,
        "committedAt": finding.committed_at,
        "pullRequest": finding.pull_request,
        "family": finding.family,
        "specialist": finding.specialist,
        **SAFETY,
    }


def probing(query: FindingQuery) -> FindingQuery:
    """``query``, asking for one row past its page.

    The whole mechanism behind ``truncated``: a page of ``limit`` rows cannot say
    whether an eleventh row matched, so the read asks for ``limit + 1`` and the
    response serves ``limit``. The extra row is **read and discarded** -- it is
    never shaped, never counted, never published.

    Deliberately not a ``COUNT(*)`` over the matching rows. That would publish a
    number computed over rows the caller did not receive, which is precisely the
    "statistic over rows the caller may not see" family
    :func:`findings_payload` refuses a rejected count for. One row past the
    window is a property *of the window's boundary*: it says the page ends
    somewhere, not how much is behind it.

    The probe goes through the same statement every serve uses, so it selects
    from ``findings`` alone -- a rejected trailer cannot occupy the probe slot any
    more than it can occupy a served one, and ``truncated`` therefore cannot carry
    a bit about the rejected population.
    """
    return replace(query, limit=query.limit + 1)


def findings_payload(probed: tuple[StoredFinding, ...], *, page_size: int) -> dict[str, Any]:
    """The whole response: the rows, how many, and whether the page ended early.

    ``probed`` is what :func:`probing` asked for -- up to ``page_size + 1`` rows.
    The page is the first ``page_size`` of them; the extra row, if it came back,
    is discarded here and is the entire basis of ``truncated``.

    **Three members, and the shortness is still the point.** Every value is a
    function of the served rows and of this page's own boundary, which is
    ADR-0029's closure stated at the response rather than at a field:

    * ``count`` is the number of rows in *this* response, not a total before
      ``limit`` and not a count of anything the caller did not receive;
    * ``findings`` is those rows;
    * ``truncated`` is whether a matching row existed past the page. It is one
      bit about *this* page's edge, and it is what stops a full page from being
      misread as the whole answer -- the false claim it replaces: `(code-review,
      MEDIUM)` matched 128 rows on this repository's own corpus, served 100 with
      no signal, and the remedy the record offered ("narrow by filter") was
      unavailable because the remaining axes are ``null`` on every row (PR #504
      round 1, R1-4).

    Three members that were considered and are deliberately absent. A **rejected
    count** would be a statistic over rows this tool never serves, so a malformed
    trailer somebody committed would move a served value -- the "statistic over
    rows the caller may not see" family, opened for no gain. The **store's stamp**
    (schema version, parser stamp) would publish build metadata whose only
    purpose is a staleness decision this tool has already made: a stale store is
    refused, so a served response has nothing to say about it. And an **echo of
    the filters** would restate the caller's own request back at it, which
    reads like confirmation and drifts the first time a filter is renamed.

    A **total matching count** belongs on that list too, and ``truncated`` is
    what it was rejected in favour of: the total is a number over unserved rows,
    while one boundary bit is a property of the page that was served.

    The differential this shape is written to survive: a store holding a rejected
    row and a store that never held one answer identically, to every query --
    ``truncated`` included, which holds because the probe row comes from the same
    ``findings``-only read as every served row.
    """
    served = probed[:page_size]
    return {
        "count": len(served),
        "truncated": len(probed) > page_size,
        "findings": [finding_row(f) for f in served],
    }
