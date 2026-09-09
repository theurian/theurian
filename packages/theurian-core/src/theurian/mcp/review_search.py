"""Shaping one served review record: the bounds, the vocabularies, the wire row.

The ``review.search`` half that is not a tool registration (ADR-0030 decision 6).
``mcp/tools.py`` owns the registration, resolves the project, checks provenance
and constructs the store; this module owns what a caller may *ask* for and what a
matched record looks like on the wire -- the same split ``mcp/findings.py`` holds
for a landed trailer and ``mcp/results.py`` for a knowledge hit, and for the same
reason: a shape constructed in two places drifts in one of them.

**The trust triple comes from one place, and the field table decides who needs
it.** ADR-0030 decision 3 enumerates every evidence field with *who controls its
value*, and decision 6 makes that column the trust boundary: an **author**
-controlled value -- a comment or review body, a pull request's title or
description, a display name, a file path as received -- is served under
:data:`~theurian.mcp.results.SAFETY`, imported and splatted rather than
respelled. A second literal of those three keys is how one surface ends up
labelling and another not, which is untrusted text reaching an agent with nothing
saying it is a document (SEC-15, T-3).

The classification is published here as data --
:data:`AUTHOR_CONTROLLED_FIELDS` and :data:`PROVIDER_CONTROLLED_FIELDS` -- rather
than left implicit in :func:`review_record` 's body, so a field added to the
shaper without being classified fails a test rather than shipping unlabelled.

**The file path is served as data and is never joined into a filesystem path**
(ADR-0030 decisions 3 and 6, SEC-7). It is chosen by whoever authored the pull
request, so it sits on the untrusted side of the table beside the bodies, and
nothing in this module or above it opens anything named by it.

**Three of decision 3's author-controlled fields are not served at all** --
labels, head branch name and milestone name. The projection does not carry them
(:class:`~theurian.domain.review_search.ReviewSearchRecord` records that omission
as a deliberate schema decision, not a loss), so there is no wire key for them
here. Adding one later is a change to :data:`AUTHOR_CONTROLLED_FIELDS`, the
published schema and the store's own schema version together.

**Every bound on a caller's *request* is a refusal rather than a clamp**, for the
reason ``mcp/findings.py`` gives: this tool answers a *filtered* question with no
paging, so a silent clamp would let a caller read "these are the records matching
my filter" off a response that was truncated from more. The one bound that is a
clamp is :func:`_bounded_excerpt`, whose input is a stored value rather than a
request -- refusing there would let one planted comment deny the whole tool.

**A refusal never echoes a number, and echoes a string only inside the bound.**
The sibling surface's refusal for an absurd ``pullRequest`` crashed while it was
being built, because rendering an integer past CPython's
``sys.get_int_max_str_digits()`` raises (``mcp/findings.py::_digits``, PR #504
round 1, R1-2 face ii). This surface closes that class by construction rather
than by measuring the integer: **no refusal here interpolates a caller's
number at all**, so there is nothing to render and no arm that can fail. The cost
is that a mistyped ``limit`` is not quoted back; the refusal names both ends of
the bound instead, which is the part a caller acts on.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Final

from theurian.domain.enums import ReviewThreadState
from theurian.domain.errors import TheurianError
from theurian.domain.retrieval import EXCERPT_CHARS
from theurian.domain.review_search import ReviewSearchHit, ReviewSearchQuery
from theurian.mcp.results import SAFETY

#: How many records one call may return. A hard cap, not a clamp.
#:
#: Half the sibling surface's 100, and the difference is the row rather than a
#: preference: a landed trailer is one line, while a record here carries an
#: excerpt bounded at :data:`MAX_EXCERPT_CHARS` characters, so 50 rows is the
#: comparable page in bytes. The daemon's own footprint while assembling one is
#: bounded by ``limit * (MAX_EXCERPT_CHARS + 1)`` because the cut is made by the
#: store's read (see :func:`excerpt_fetch_chars`), not applied to what it
#: returned.
MAX_REVIEW_SEARCH_LIMIT: Final = 50

#: What a caller gets without asking. Smaller than the cap on purpose: the common
#: call is "what was said about X", and twenty records is a page rather than a
#: context-budget event.
DEFAULT_REVIEW_SEARCH_LIMIT: Final = 20

#: The bound on every string filter, applied before anything is matched or
#: echoed. One number rather than five.
#:
#: 400 rather than the sibling surface's 200, because the long member here is a
#: **file path**: an equality filter against a path whoever opened the pull
#: request chose, where a refusal for a legitimately deep path would be a false
#: refusal on real data. Every other filter is far shorter -- a repository is
#: ``owner/name``, a GitHub login is at most 39 characters and a node id is
#: shorter than that.
#:
#: ``q`` shares the bound rather than taking ``knowledge.search``'s 2,000,
#: because the match is **literal**: there is no query language behind it, so a
#: longer needle is a longer verbatim substring of one stored fragment, not a
#: richer question. A caller past the bound is told the bound and asked for a
#: shorter substring -- an answer it can act on, and not the false absence a
#: silent truncation of the needle would produce.
MAX_FILTER_CHARS: Final = 400

#: The bound on a served excerpt, which is ``knowledge.search``'s own.
#:
#: Derived from :data:`~theurian.domain.retrieval.EXCERPT_CHARS` rather than
#: respelled, so one number governs what "an excerpt" means on every surface this
#: daemon publishes one on. The shape is that surface's too: cut at the bound,
#: then an explicit marker, so a truncated value cannot be read as a whole one.
MAX_EXCERPT_CHARS: Final = EXCERPT_CHARS

#: What :func:`_bounded_excerpt` appends when it cuts. Three characters, so the
#: two lengths are disjoint and a reader can tell them apart by arithmetic alone:
#: an untouched excerpt is at most :data:`MAX_EXCERPT_CHARS` characters and a cut
#: one is exactly ``MAX_EXCERPT_CHARS + 3``.
_CUT_MARKER: Final = "..."

#: The largest ``pullRequest`` this surface accepts, which is the largest value
#: the column behind it can hold: SQLite's INTEGER is a signed 64-bit value, and
#: ``sqlite3`` raises ``OverflowError`` binding anything past it -- an error no
#: layer converts, so it reaches a caller as a crash rather than as a refusal.
#:
#: A twin of ``mcp/findings.MAX_PULL_REQUEST`` rather than an import of it, for
#: the reason ``review_search_sql.contains_pattern`` is a twin of its own sibling:
#: the two surfaces version and bound separately, and what they share here is a
#: property of *SQLite*, not of either of them. Importing one into the other
#: would make a change to the findings surface's bounds a change to this one's.
MAX_PULL_REQUEST: Final = 2**63 - 1

#: The one character that cannot appear in any value this store holds and that
#: silently changes what a filter means. See :func:`_transportable`.
_NUL: Final = "\x00"

#: Every published key of one record row whose value is **author**-controlled
#: (ADR-0030 decision 3's field table, decision 6's trust boundary). Each rides
#: under :data:`~theurian.mcp.results.SAFETY`, which this surface attaches at the
#: row rather than per field -- so a row carrying any of these carries the triple.
#:
#: ``excerpt`` is the served face of the table's *comment body, review body, PR
#: title, PR description* row: which of the four it came from is
#: ``excerptChannel``, and that value is one of three words this codebase chose,
#: not one an author wrote.
AUTHOR_CONTROLLED_FIELDS: Final = frozenset({"filePath", "authorDisplayName", "excerpt"})

#: Every published key whose value is **provider** structure or a value Theurian
#: itself wrote at ingestion -- states, ids, timestamps, the source anchor, the
#: last-seen stamp. Validated and normalized rather than labelled, which is the
#: other side of decision 6's boundary.
#:
#: ``authorExternalId`` is here **with a caveat ADR-0030 records in the same
#: table**: GitHub can answer with a *login* where a node id is expected, and a
#: login is author-chosen. It stays on this side because the record cannot say
#: which of the two a given value is, and because R-12 pseudonymises the
#: login-fallback case before the record is written -- but a consumer must not
#: treat it as unforgeable identity. The whole row carries the triple regardless,
#: which is what makes that caveat safe to record rather than urgent to fix.
PROVIDER_CONTROLLED_FIELDS: Final = frozenset(
    {
        "recordPath",
        "recordKey",
        "kind",
        "provider",
        "repository",
        "pullRequest",
        "threadState",
        "sourceUri",
        "authorExternalId",
        "excerptChannel",
        "lastSeenRunId",
        "lastSeenAt",
    }
)


class ReviewSearchQueryError(TheurianError):
    """A caller asked for something outside this tool's bounds or vocabularies.

    A :class:`~theurian.domain.errors.TheurianError`, so ``mcp/tools.py``'s
    ``_forwarding`` seam converts it into the SDK's ``ToolError`` and the message
    reaches the caller under mcp >= 2.1 (#491). That seam forwards ``str(exc)``
    and drops ``remedy``, so **the message is self-contained**: it names the bound
    or the vocabulary and what to send instead, rather than leaving the cure on an
    attribute the wire has no field for.

    ``remedy`` is carried anyway, because ``theurian doctor`` and the CLI's own
    error rendering read it, and an error type in this codebase that carries none
    is one somebody has to remember is special.

    **Nothing it says varies with a project's contents.** Every message below is
    built from the caller's own argument and this module's constants, so a refusal
    is the same string against an empty corpus and a full one (SEC-13).
    """

    def __init__(self, detail: str) -> None:
        self.remedy = "Call `review.search` again with a value inside the bound the message names."
        super().__init__(detail)


def _transportable(name: str, value: str) -> None:
    """Refuse a filter that no stored value can equal or that cannot cross the wire.

    Two shapes, both refused rather than repaired, and **neither refusal echoes
    the value** -- a message quoting a lone surrogate is a message the wire
    encoder cannot serialise, which is how a refusal becomes a 200 with an empty
    body (``mcp/tools._publishable``).

    **A NUL.** SQLite compares NUL-terminated strings, so a needle carrying one
    is matched by its pre-NUL prefix: measured on SQLite 3.47.1, ``'%nul%'`` does
    not match a stored ``'with\\x00nul'`` and ``'%with\\x00ZZZZ%'`` does. That is
    a *wrong* match rather than a smaller one, so the promise "matched literally"
    cannot be kept for such a value. The equality filters carry it for the first
    reason alone -- no stored value can contain one, because the builder refuses
    a record that does -- and only ``q`` carries the second.

    **An unpaired surrogate.** A ``str`` Python accepts and UTF-8 cannot encode,
    so it raises ``UnicodeEncodeError`` at the SQLite bind -- an exception no
    ``except sqlite3.Error`` catches, which would arrive at this surface as a
    second refusal shape for a second kind of input (SEC-13).

    Stated as the property rather than as a list of bad characters, the
    formulation :func:`~theurian.domain.review_search.untransportable_reason`
    uses: the domain refuses the same population at query construction, and this
    is the layer that turns that refusal into a message naming the argument.
    """
    if _NUL in value:
        raise ReviewSearchQueryError(
            f"`{name}` contains a NUL byte (U+0000). No stored review value can contain "
            f"one, so this filter could match nothing at best; sent as `q`, it would not "
            f"even do that, because SQLite's comparison stops reading at a NUL and would "
            f"silently shorten the filter instead of matching what was sent. Nothing was "
            f"searched. Send the value without it."
        )
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ReviewSearchQueryError(
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

    An **empty** string is refused rather than passed through. Every filter here
    is an equality or a substring test, and an empty needle matches every row
    while an empty equality matches none -- two opposite wrong answers to a filter
    the caller almost certainly meant to omit.
    """
    if len(value) > MAX_FILTER_CHARS:
        raise ReviewSearchQueryError(
            f"`{name}` is {len(value)} characters long, and no review-search filter may "
            f"be longer than {MAX_FILTER_CHARS}. Nothing was searched. Send a shorter "
            f"value, or omit `{name}` to not filter on it."
        )
    _transportable(name, value)
    if not value:
        raise ReviewSearchQueryError(
            f"`{name}` is empty, which matches everything or nothing depending on the "
            f"filter rather than what you meant. Nothing was searched. Omit `{name}` to "
            f"not filter on it."
        )
    return value


def _thread_state(value: str | None) -> str | None:
    """One of the four governed thread states, or a refusal naming all four.

    Validated rather than passed through, for the reason
    ``mcp/findings._commit_sha`` is: an unrecognised token would match no row and
    answer ``count: 0``, which a caller reads as "no threads are in that state"
    rather than "that is not a state". The vocabulary is
    :class:`~theurian.domain.enums.ReviewThreadState`, recomputed from the enum so
    a member added there cannot leave this refusal naming a smaller set.

    It is deliberately **not** a review *submission*'s ``state`` (``APPROVED``,
    ``CHANGES_REQUESTED``): the store keeps the two apart in separate columns for
    the reason ``review_search_builder._thread_state_of`` records, and one filter
    answering over both vocabularies would leave a caller unable to tell which
    kind a value came from.
    """
    if value is None:
        return None
    token = _bounded("threadState", value)
    if token not in {state.value for state in ReviewThreadState}:
        expected = ", ".join(state.value for state in ReviewThreadState)
        raise ReviewSearchQueryError(
            f"`threadState` must be one of {expected}; got {token!r}. The tokens are "
            f"lower-case, as the store writes them. Nothing was searched. Omit "
            f"`threadState` to search every thread, and every record that is not a "
            f"thread."
        )
    return token


def _pull_request(value: int | None) -> int | None:
    """A PR number inside the column's own range, or a refusal naming both ends.

    There is no pull request numbered zero, and none past
    :data:`MAX_PULL_REQUEST` either: the store's column is a signed 64-bit
    integer, and ``sqlite3`` raises ``OverflowError`` binding anything wider --
    an overflow no layer catches, so the bound is what turns a crash into an
    answer a caller can act on.

    **The caller's number is not echoed**, which is this surface's whole answer to
    the class that made the sibling refusal crash while it was being built: there
    is no arm here that renders an integer, so there is none that can raise.
    """
    if value is None:
        return None
    if value < 1 or value > MAX_PULL_REQUEST:
        raise ReviewSearchQueryError(
            f"`pullRequest` must be a positive number no larger than "
            f"{MAX_PULL_REQUEST}. Nothing was searched. Omit `pullRequest` to search "
            f"every pull request's records."
        )
    return value


def _limit(value: int) -> int:
    """The caller's page size, or a refusal naming the bound (never a clamp)."""
    if value < 1 or value > MAX_REVIEW_SEARCH_LIMIT:
        raise ReviewSearchQueryError(
            f"`limit` must be between 1 and {MAX_REVIEW_SEARCH_LIMIT}. Nothing was "
            f"searched. This is a refusal rather than a silent clamp: a truncated answer "
            f"to a filtered question reads as the whole answer, so narrow with a filter "
            f"instead of asking for a larger page."
        )
    return value


def build_query(  # noqa: PLR0913 - one parameter per published filter
    *,
    repository: str | None,
    pull_request: int | None,
    thread_state: str | None,
    author: str | None,
    file_path: str | None,
    text_contains: str | None,
    limit: int,
) -> ReviewSearchQuery:
    """A caller's arguments as a store query, or a refusal naming what was wrong.

    **Every bound is checked before anything is read.** The store is not opened,
    no row is scanned and no file is touched until this returns -- so a refused
    request costs a caller nothing and buys the daemon nothing to do (T-6).

    The order within a single filter is length, then transportability, then
    vocabulary: an over-long token is reported by its length, and only a token
    already inside the bound is ever quoted back.

    **``file_path`` is bounded and carried as data.** It is matched against a
    stored column and reaches no filesystem call, here or below (SEC-7, ADR-0030
    decision 6).
    """
    return ReviewSearchQuery(
        limit=_limit(limit),
        repository=None if repository is None else _bounded("repository", repository),
        pull_request=_pull_request(pull_request),
        thread_state=_thread_state(thread_state),
        author=None if author is None else _bounded("author", author),
        file_path=None if file_path is None else _bounded("filePath", file_path),
        text_contains=None if text_contains is None else _bounded("q", text_contains),
    )


def excerpt_fetch_chars() -> int:
    """How many characters of a stored fragment the store read fetches.

    One more than :data:`MAX_EXCERPT_CHARS`, and the extra character is the entire
    mechanism: the read cuts in SQL
    (:func:`~theurian.infrastructure.sqlite.review_search_sql.excerpt_columns`),
    so the only evidence left that a fragment *was* longer is whether that one
    extra character came back. :func:`_bounded_excerpt` decides from exactly that.

    Asking for the bound itself would delete the distinction -- a fragment of
    exactly the bound and a 2 MiB one would arrive identical, and this surface
    would have to either mark both (lying about an authored value that fits) or
    mark neither (publishing a cut as the whole value).
    """
    return MAX_EXCERPT_CHARS + 1


def _bounded_excerpt(fetched: str) -> str:
    """A stored fragment as the wire carries it: whole, or cut and marked.

    **The one value on this surface whose size a caller does not control and the
    corpus does.** Every other bound here refuses; this one truncates, because the
    over-long input is a *stored row*, not a request -- refusing the response
    would let one planted comment deny the whole tool.

    **It trims at most one character, because the store already did the cutting.**
    The serving read fetches :func:`excerpt_fetch_chars` characters, so the
    longest value that reaches here is one past the bound whatever the row holds.
    This function's job is not to move bytes but to *decide*: the extra character
    means the fragment was longer, and the marker is what says so.

    **The match is not cut with the text.** The store's ``EXISTS`` predicate names
    the whole stored fragment and only the excerpt sub-select cuts it, so a record
    whose only match lies past the bound is still served -- cut and marked, but
    present. Matching the cut text instead would manufacture a false absence.

    **One recorded gap, which fails closed.** SQLite's ``substr`` stops at an
    embedded NUL, so a fragment carrying one arrives *shorter* than the bound and
    is published unmarked even though more text exists. It under-states rather
    than over-states, and it is unreachable through the shipped path: the builder
    refuses a landed record carrying an untransportable value by name
    (``review_search_builder._refuse_untransportable``), so only a load assembled
    by hand can hold one.
    """
    if len(fetched) <= MAX_EXCERPT_CHARS:
        return fetched
    return fetched[:MAX_EXCERPT_CHARS] + _CUT_MARKER


def review_record(hit: ReviewSearchHit) -> dict[str, Any]:
    """One matched record as the wire carries it: every column, plus the triple.

    camelCase because that is this surface's convention, and **every key is always
    present**, ``null`` included: a field that appears only when it has a value
    cannot be told apart from a server that predates the field, and
    ``pullRequest``, ``threadState``, ``filePath``, ``excerpt`` and
    ``excerptChannel`` are all legitimately ``None`` for some record kind.

    **The row rides under the SEC-15 triple as a whole**, because it carries
    author-controlled text: the excerpt, the display name and the file path
    (:data:`AUTHOR_CONTROLLED_FIELDS`). An instruction hidden in a review comment
    therefore arrives marked ``mayContainInstructions: true``, which is a
    not-executed guarantee rather than a not-disclosed one (ADR-0030 decision 6,
    T-3). ``**SAFETY`` is the imported object splatted in, never three literals
    typed here.

    Nothing here is computed across rows: every value is this row's own stored
    column, bounded in length and otherwise unmodified, so no published field can
    be a function of anything but the record it came from.
    """
    return {
        "recordPath": hit.relative_path,
        "recordKey": hit.record_key,
        "kind": hit.kind,
        "provider": hit.provider,
        "repository": hit.repository,
        "pullRequest": hit.pull_request,
        "threadState": hit.thread_state,
        # Author-controlled, and served as *data*: nothing joins it into a
        # filesystem path, here or anywhere this response reaches (SEC-7).
        "filePath": hit.file_path,
        "sourceUri": hit.source_uri,
        "authorExternalId": hit.author_external_id,
        "authorDisplayName": hit.author_display_name,
        "excerpt": None if hit.excerpt is None else _bounded_excerpt(hit.excerpt),
        "excerptChannel": hit.excerpt_channel,
        "lastSeenRunId": hit.last_seen_run_id,
        "lastSeenAt": hit.last_seen_at,
        **SAFETY,
    }


def probing(query: ReviewSearchQuery) -> ReviewSearchQuery:
    """``query``, asking for one row past its page.

    The whole mechanism behind ``truncated``: a page of ``limit`` records cannot
    say whether an eleventh matched, so the read asks for ``limit + 1`` and the
    response serves ``limit``. The extra record is **read and discarded** -- never
    shaped, never counted, never published.

    Deliberately not a ``COUNT(*)`` over the matching records. That would publish
    a number computed over rows the caller did not receive, which is the
    "statistic over rows the caller may not see" family. One row past the window
    is a property *of the window's boundary*: it says the page ends somewhere, not
    how much is behind it.

    ``limit + 1`` cannot exceed what the domain accepts -- ``ReviewSearchQuery``
    bounds only the low end -- and it is one past a value :func:`_limit` has
    already capped at :data:`MAX_REVIEW_SEARCH_LIMIT`.
    """
    return replace(query, limit=query.limit + 1)


def review_search_payload(probed: tuple[ReviewSearchHit, ...], *, page_size: int) -> dict[str, Any]:
    """The whole response: the records, how many, and whether the page ended early.

    ``probed`` is what :func:`probing` asked for -- up to ``page_size + 1``
    records. The page is the first ``page_size`` of them; the extra one, if it
    came back, is discarded here and is the entire basis of ``truncated``.

    **Three members, and the shortness is the point.** Every value is a function
    of the served rows and of this page's own boundary, which is ADR-0030 decision
    6's closure stated at the response rather than at a field:

    * ``count`` is the number of records in *this* response, not a total before
      ``limit`` and not a count of anything the caller did not receive;
    * ``records`` is those records;
    * ``truncated`` is whether a matching record existed past the page -- one bit
      about *this* page's edge, which is what stops a full page from being misread
      as the whole answer.

    Members that were considered and are deliberately absent. A **total matching
    count** is a number over unserved rows, and ``truncated`` is what it was
    rejected in favour of. The **store's stamp** would publish build metadata
    whose only purpose is a staleness decision this tool has already made by
    refusing. An **echo of the filters** would restate the caller's own request
    back at it, which reads like confirmation and drifts the first time a filter
    is renamed. A **count of withheld records** would be the disclosure this whole
    design exists to prevent: a withheld record has no row in the store at all
    (``ReviewSearchBuildRequest``), so nothing here could compute one even if it
    wanted to -- which is the property, not an omission.

    The differential this shape is written to survive: a store built from a corpus
    that **held** withheld records and one that **never did** answer identically,
    to every query -- ``truncated`` included, which holds because the probe row
    comes from the same read as every served row and a withheld record contributes
    no row to it.
    """
    served = probed[:page_size]
    return {
        "count": len(served),
        "truncated": len(probed) > page_size,
        "records": [review_record(hit) for hit in served],
    }


__all__ = [
    "AUTHOR_CONTROLLED_FIELDS",
    "DEFAULT_REVIEW_SEARCH_LIMIT",
    "MAX_EXCERPT_CHARS",
    "MAX_FILTER_CHARS",
    "MAX_PULL_REQUEST",
    "MAX_REVIEW_SEARCH_LIMIT",
    "PROVIDER_CONTROLLED_FIELDS",
    "ReviewSearchQueryError",
    "build_query",
    "excerpt_fetch_chars",
    "probing",
    "review_record",
    "review_search_payload",
]
