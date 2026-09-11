"""Every SQL statement the review search store issues, and the rows it binds.

Split out of :mod:`theurian.infrastructure.sqlite.review_search_store` when that
module passed this project's 800-line ceiling, on the seam the evidence package
already uses (``store.py`` / ``reader.py`` / ``records.py``): **what the
statements are** lives here, **when they are issued** lives next door with the
connection handling.

The seam is worth having beyond the line count. Every claim about *what reaches a
statement* is now checkable in one file: the only text any function here
concatenates is text this module wrote -- the column tuple, the fixed operators,
the order clause and the escape character -- and every caller-supplied value
leaves as a bound parameter in the list beside the clause. A reader auditing
"can a repository name, an author id, a file path or a search string reach the
SQL text" reads this module and stops.

Nothing here opens a connection, touches the filesystem or knows a path.
"""

from __future__ import annotations

from typing import Final

from theurian.domain.review_search import ReviewSearchLoad, ReviewSearchQuery

#: One inserted record row: the thirteen columns of ``review_records``, in order.
#: Named so the insert statement and the row builder cannot drift on arity.
RecordRow = tuple[
    str, str, str, str, str, int | None, str | None, str | None, str, str, str, str, str
]

#: One inserted participant row: ``(relative_path, position, external_id)``.
ParticipantRow = tuple[str, int, str]

#: One inserted text row: ``(relative_path, position, channel, content)``.
TextRow = tuple[str, int, str, str]

#: The thirteen columns of one record, in :class:`ReviewSearchRecord` order.
#: One tuple, so the dump and the search cannot drift on what a row is -- not on
#: which columns, not on their order, not on their number.
RECORD_COLUMN_NAMES: Final = (
    "relative_path",
    "record_key",
    "kind",
    "provider",
    "repository",
    "pull_request",
    "thread_state",
    "file_path",
    "source_uri",
    "author_external_id",
    "author_display_name",
    "last_seen_run_id",
    "last_seen_at",
)

#: Composed from the column tuple rather than typed out, so the column list and
#: the placeholder count cannot drift apart. The only interpolation is this
#: module's own constant and a generated run of ``?`` (S608).
INSERT_RECORD: Final = (
    f"INSERT INTO review_records ({', '.join(RECORD_COLUMN_NAMES)}) "  # noqa: S608
    f"VALUES ({', '.join('?' for _ in RECORD_COLUMN_NAMES)})"
)
INSERT_PARTICIPANT: Final = (
    "INSERT INTO review_participants (relative_path, position, external_id) VALUES (?, ?, ?)"
)
INSERT_TEXT: Final = (
    "INSERT INTO review_texts (relative_path, position, channel, content) VALUES (?, ?, ?, ?)"
)
INSERT_METADATA: Final = (
    "INSERT INTO review_search_metadata "
    "(id, review_search_schema_version, evidence_format_version, built_at) VALUES (1, ?, ?, ?)"
)
SELECT_STAMP: Final = (
    "SELECT review_search_schema_version, evidence_format_version FROM review_search_metadata "
    "WHERE id = 1"
)

#: The dump's projection: every record column whole, qualified so it reads the
#: same as the search's projection over the same aliased table.
DUMP_COLUMNS: Final = ", ".join(f"r.{name}" for name in RECORD_COLUMN_NAMES)

#: The served order. ``relative_path`` is the primary key and therefore unique,
#: so the whole ordering is **total**: two calls over one store return one
#: sequence, and ``LIMIT`` truncates a defined sequence rather than one SQLite is
#: free to vary between runs. The three keys in front of it are there to make the
#: order readable -- a repository's records together, oldest pull request first,
#: the three kinds in a fixed order within it -- and none of them is a rank: no
#: value in this ordering is computed from what a query asked for, so the
#: sequence is a property of the corpus alone.
SEARCH_ORDER: Final = (
    "ORDER BY r.repository ASC, r.pull_request ASC, r.kind ASC, r.relative_path ASC"
)

#: The escape character for every substring match on this path. A caller's text is
#: matched *literally*: ``%`` and ``_`` in it are its own characters, not
#: wildcards, so a search string cannot be turned into a pattern language.
LIKE_ESCAPE: Final = "\\"

#: The pattern that selects every fragment, used when a query names no text
#: filter. ``LIKE '%'`` matches any non-NULL value including the empty string, so
#: the excerpt subqueries below take one shape for both cases rather than being
#: assembled differently depending on whether a filter is present.
ANY_FRAGMENT: Final = "%"


def contains_pattern(needle: str) -> str:
    """``needle`` as a LIKE pattern that matches it literally, anywhere in a value.

    The three characters LIKE gives meaning to -- the escape itself, ``%`` and
    ``_`` -- are escaped, so a caller's ``%`` matches a percent sign rather than
    every row. The escape character is doubled *first*: doing it after would also
    escape the backslashes this function itself just introduced.

    A twin of ``findings_store._contains_pattern`` rather than a shared helper.
    The two stores version and rebuild separately (see ``review_search_schema``),
    and each owns its own escape character and its own columns; a shared function
    would make one store's escaping change the other's stored-pattern semantics,
    which is exactly the coupling the separate version constants exist to avoid.
    """
    escaped = (
        needle.replace(LIKE_ESCAPE, LIKE_ESCAPE * 2)
        .replace("%", f"{LIKE_ESCAPE}%")
        .replace("_", f"{LIKE_ESCAPE}_")
    )
    return f"%{escaped}%"


def fragment_pattern(query: ReviewSearchQuery) -> str:
    """The pattern both excerpt subqueries and the text filter share.

    One value, computed once, so the row a record *matched* on and the row its
    excerpt is *cut from* cannot come from two different predicates.
    """
    if query.text_contains is None:
        return ANY_FRAGMENT
    return contains_pattern(query.text_contains)


def where_clause(query: ReviewSearchQuery, pattern: str) -> tuple[str, list[str | int]]:
    """``query``'s filters as a SQL clause and its bound parameters.

    Every value is a **bound parameter**; only the fixed column names, the two
    correlated sub-selects and the operators below are ever concatenated into SQL,
    so no caller-supplied text reaches the statement text. An absent filter
    contributes no clause at all rather than a tautology, so an unfiltered query
    is a plain ordered read.

    The two set-valued filters are ``EXISTS`` sub-selects rather than joins: a
    join would multiply a record's row by the number of participants or fragments
    that matched, and ``LIMIT`` would then bound *rows* instead of *records*.
    """
    clauses: list[str] = []
    parameters: list[str | int] = []
    equalities: tuple[tuple[str, str | int | None], ...] = (
        ("repository", query.repository),
        ("pull_request", query.pull_request),
        ("thread_state", query.thread_state),
        ("file_path", query.file_path),
    )
    for column, value in equalities:
        if value is not None:
            clauses.append(f"r.{column} = ?")
            parameters.append(value)
    if query.author is not None:
        clauses.append(
            "EXISTS (SELECT 1 FROM review_participants p "
            "WHERE p.relative_path = r.relative_path AND p.external_id = ?)"
        )
        parameters.append(query.author)
    if query.text_contains is not None:
        clauses.append(
            # The only interpolation is this module's own escape character; the
            # pattern itself is the bound parameter appended below (S608).
            "EXISTS (SELECT 1 FROM review_texts x "  # noqa: S608
            f"WHERE x.relative_path = r.relative_path AND x.content LIKE ? ESCAPE '{LIKE_ESCAPE}')"
        )
        parameters.append(pattern)
    return ("WHERE " + " AND ".join(clauses) if clauses else ""), parameters


def excerpt_columns() -> str:
    """The two correlated sub-selects that carry the excerpt and its channel.

    **They pick the same fragment, and that is a fact rather than a hope.**
    ``(relative_path, position)`` is ``review_texts``' primary key, so ``ORDER BY
    x.position LIMIT 1`` over one record with one predicate is a total order over
    a unique key: both sub-selects see the same candidate set in the same order
    and take the same row. Written as two rather than one because SQLite's
    scalar sub-select yields one column, and the alternative -- packing channel
    and content into one string with a separator -- would put a parse in front of
    a value this module deliberately never parses.

    The cut happens **in the read**: ``substr`` means SQLite never hands Python
    more than ``text_chars`` characters of *excerpt* per row, so the read's
    excerpt footprint is bounded by ``limit * text_chars`` whatever the corpus
    holds. A surface that fetched the fragment whole and clamped afterwards would
    already have paid for every planted byte -- the measured shape
    ``findings_store._SERVE_COLUMNS`` records (22.0 MB of Python heap against
    0.1 MB, ``tracemalloc`` peak).

    **That is a bound on this one term and on no other column.** Every other
    column in ``DUMP_COLUMNS`` is selected whole, ``file_path`` and
    ``author_display_name`` included, and those two are author-controlled
    (ADR-0030 decision 3). The response is bounded above this layer, by
    ``mcp/review_search.MAX_REVIEW_SEARCH_RESPONSE_CHARS`` -- plus at most one
    record that alone exceeds it, the page's **first**, which is served whole and
    alone and is bounded by ``MAX_SOURCE_FILE_BYTES`` at landing rather than by
    that budget, while a later over-budget record is not served at all; the read
    is bounded only by what the evidence writer would let land.

    ``substr`` counts UTF-8 code points, which is what ``len`` counts, so a bound
    stated in characters means the same thing on both sides of the boundary.
    **One stored value would disagree and it is recorded rather than repaired: a
    ``content`` carrying an embedded NUL is cut short**, because ``substr`` stops
    there -- measured on SQLite 3.47.1, ``substr('with\\x00nul', 1, 20)`` is
    ``'with'`` and ``length`` of the same value is 4. Such a value can reach the
    store only from a hand-edited evidence file carrying a JSON ``\\u0000``
    escape, and the difference fails **closed**: the excerpt is shorter than the
    caller's bound, never longer, so nothing is published that a whole fetch would
    have withheld.
    """
    match_clause = (
        "WHERE t.relative_path = r.relative_path "
        f"AND t.content LIKE ? ESCAPE '{LIKE_ESCAPE}' ORDER BY t.position LIMIT 1"
    )
    # The only interpolation is `match_clause`, built two lines above from this
    # module's own literals; the cut and the pattern are bound parameters (S608).
    return (
        f"(SELECT substr(t.content, 1, ?) FROM review_texts t {match_clause}) AS excerpt, "  # noqa: S608
        f"(SELECT t.channel FROM review_texts t {match_clause}) AS excerpt_channel"
    )


def record_rows(load: ReviewSearchLoad) -> list[RecordRow]:
    """Project a load's records onto insertable record rows, in the load's order."""
    return [
        (
            record.relative_path,
            record.record_key,
            record.kind,
            record.provider,
            record.repository,
            record.pull_request,
            record.thread_state,
            record.file_path,
            record.source_uri,
            record.author_external_id,
            record.author_display_name,
            record.last_seen_run_id,
            record.last_seen_at,
        )
        for record in load.records
    ]


def participant_rows(load: ReviewSearchLoad) -> list[ParticipantRow]:
    """Project a load's participants onto insertable rows, keyed by first appearance.

    ``position`` is the participant's ordinal *within its record*, in the order
    the builder produced them, so a rebuild over unchanged evidence assigns the
    same positions and the store reproduces.
    """
    return [
        (record.relative_path, position, external_id)
        for record in load.records
        for position, external_id in enumerate(record.participant_ids)
    ]


def text_rows(load: ReviewSearchLoad) -> list[TextRow]:
    """Project a load's fragments onto insertable rows, in the record's reading order.

    ``content`` is copied through verbatim and never inspected: it is untrusted,
    author-written text (ADR-0030 decision 6) and this store neither parses nor
    normalises it.
    """
    return [
        (record.relative_path, position, fragment.channel.value, fragment.content)
        for record in load.records
        for position, fragment in enumerate(record.texts)
    ]
