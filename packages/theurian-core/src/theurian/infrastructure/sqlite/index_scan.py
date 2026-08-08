"""The statement behind the scan below the trigram floor (FR-R2, FR-R7, SEC-8).

Split out of :mod:`theurian.infrastructure.sqlite.index_store` for the reason
:mod:`theurian.infrastructure.sqlite.index_query` was: that file had grown past
the size at which it can be read in one sitting, and this is a seam rather than a
cut. Nothing here opens a connection or maps an error — the store keeps both,
because both are decisions about *this index file* rather than about the query.
What lives here is the one retriever in the system that has to invent its own
ranking, together with what that ranking costs.

The reader this file is for is the one asking why a two-character Japanese query
returns the rows it does. That question is answerable here and was not answerable
anywhere before: the ordering, the term bound that keeps it affordable, and the
columns it reads are three decisions that only make sense together.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from theurian.infrastructure.sqlite.index_query import LIKE_ESCAPE, ScanTerm

#: The columns `chunks_trigram` indexes, and therefore the columns this scan has
#: to read. Split out so the two cannot drift: a column added to the index and
#: forgotten here makes a two-character query search less of the corpus than the
#: same query with one character more — a chunk whose heading carries the term
#: and whose body does not is reachable by `トークン` and would not be by `認証`.
SUBSTRING_COLUMNS: Final = ("text", "heading")

#: How many of a query's terms this scan spends — in the match *and* in the
#: order, which is one number rather than two on purpose.
#:
#: **The two used to differ, and the gap was a bug rather than a tuning.** Every
#: term went into the `WHERE` and only the longest four voted on the order, which
#: was described here as "a far milder boundary" than a term bound. It was not,
#: because the sentence directly above it is also true: wherever a cut is made,
#: the ordering key *is* the selection key. Measured on the shipped code, which
#: made that cut with a `LIMIT` rather than in the caller as it does now,
#: `認証 決済 監査 契約 暗号` against a chunk carrying `暗号` thirty times scored
#: that chunk 0.0 — below chunks carrying `契約` twice — and at `limit=10` against
#: 60 such chunks it was not returned at all. Moving the cut moved nothing about
#: this: the caller keeps the best fifty rows it may show. A term that selects
#: rows it cannot rank has not selected them; it has queued them behind every row
#: that can be ranked, in creation order, which is the exact defect
#: `scan_statement`'s ordering exists to fix.
#:
#: So the bound governs both halves, and three things follow from that alone:
#: terms five to eight now carry a vote they did not have, terms past eight are
#: honestly absent rather than notionally present, and lowering this constant
#: changes *which rows come back* — so a test can hold it from below, which
#: nothing could when it only touched the order.
#:
#: **Eight, and what the number costs.** Cost is close to linear in terms spent,
#: because each one is a `LIKE` and a `replace()` over every row of every column
#: in :data:`SUBSTRING_COLUMNS`. Worst legal query, 20,000 chunks of 1,000 CJK
#: characters (`chunking.TARGET_CHARS`), terms 1..N-1 matching nothing and term N
#: matching every row:
#:
#: ======== ========
#: N        worst
#: ======== ========
#: 4           0.81s
#: 8           1.67s
#: 16          3.37s
#: 64 (was)    4.25s
#: ======== ========
#:
#: Four is cheaper but takes back recall the previous shape did deliver, in the
#: one case where a term past the ranking prefix was genuinely reachable: when
#: fewer than `limit` rows matched anything better. Sixteen buys terms nine to
#: sixteen at twice the worst case, for queries this branch does not really see.
#: Eight doubles what previously ranked and leaves the worst case at under 40% of
#: what it was — and a query reaching this branch at all, every term under three
#: characters and split on whitespace, is a keyword list by the time it has eight
#: of them rather than a question.
#:
#: **The residual, because it is not the one the old comment claimed.** A query
#: with more than eight short terms searches only its first eight, and *first*
#: means first **typed**: `to_scan_terms` sorts longest first, but every term on
#: this branch is one or two characters, so a stable sort over equal lengths
#: leaves the typed order untouched. The selectivity proxy that justifies the
#: bound in `_query_terms` therefore does not apply here, and nothing cheaper
#: does — picking the selective term out of `認証 決済 監査 契約` needs corpus
#: statistics this retriever does not have. Left as the caller's order rather
#: than guessed at, and deferred with the rest of the ranking model to Milestone
#: 6 (ADR-0023).
SCAN_TERMS: Final = 8


def scan_statement(
    terms: Sequence[ScanTerm],
    *,
    clauses: Sequence[str],
    scope: Sequence[object],
) -> tuple[str, tuple[object, ...]]:
    """The scan, as a statement and the parameters that go with it.

    **No `LIMIT`, and that is the whole ranking rather than an oversight.** It
    had one, and the parameter was a fiction of exactly the kind
    `IndexStore.search_dense` records: `ORDER BY matched_characters DESC` scores
    every matching row before it can name the best of them, so a `LIMIT` bounded
    the rows returned and not the work done. Measured on 20,000 chunks of 1,000
    CJK characters, the four shapes in the table below cost 0.49s / 1.30s / 1.69s
    / 0.19s with `LIMIT 100` and 0.48s / 1.31s / 1.71s / 0.21s without one.

    Nothing was saved by cutting here, and something was spent: the caller has to
    look past rows the canonical store has withdrawn, so a truncated answer sent
    it back for another pass — and each pass was another full scan.
    :meth:`~theurian.application.retrieval_service.RetrievalService._visible_ranking`
    measured six of them, 3.06s against the 0.51s one pass costs, on a corpus a
    third of which had been retired since the build.

    **Handing back everything is now also what the caller is told**, and that
    edge is closed rather than held shut. `_visible_ranking`'s exit test used to
    be unable to tell "this ranking is exactly `FIRST_PASS_DEPTH` rows because
    that is the whole corpus" from "...because it was truncated", so it asked a
    second time exactly when the true match count on this branch equalled
    `FIRST_PASS_DEPTH` and fewer than `CANDIDATE_DEPTH` of those rows survived
    the canonical store — on a 100-row match set, 50 withheld cost one call and
    51 cost two.
    :meth:`~theurian.infrastructure.sqlite.index_store.SqliteIndexStore._scan_below_the_trigram_floor`
    now returns a page reporting itself exhausted, so there is no second call
    (issue #16). It used to memoise this statement's result instead, and that
    memo has gone with the call it answered. Two tests, one per half:
    ``test_one_search_reads_the_scan_once_however_many_rows_were_withheld``
    (``tests/integration/test_scan_exhaustion.py``) goes red if the call count
    or the statement count starts moving with the withheld count, and
    ``test_the_second_pass_arrives_at_fifty_withheld_rows_and_not_before``
    (``tests/unit/test_retrieval_depth.py``) goes red if the edge on the
    truncating retrievers moves off fifty.

    **Ordered by how much of the query is in the chunk, because the ordering key
    is the selection key.** The caller keeps the best fifty it may show; this
    decides which fifty. The branch first shipped ordered by `chunk_id` alone and
    described as unranked, which was true about scores and beside the point:
    `chunk_id` is `<revision ULID>#<ordinal>`, so once more than fifty chunks
    matched, the *oldest* fifty were the only ones any caller could ever see —
    and revising a document, which mints a newer ULID, sank it further. "Honestly
    unranked" is not available to a query that has to choose fifty rows out of a
    thousand.

    The sort key is how many characters the query's terms account for: occurrences
    weighted by term length, so a longer and therefore more selective term counts
    for more per hit. `chunk_id` stays as the tie-break, which is what keeps the
    order total and the answer reproducible (FR-R7) — but it is now the
    second-order effect it was always suited to be. Two residuals, stated rather
    than hidden:

    - a chunk saturating one term can outrank a chunk covering two, which BM25
      gets right and this proxy does not. Closing it needs per-term IDF, deferred
      with the rest of the ranking-model work to Milestone 6 (ADR-0023);
    - among chunks that are *genuinely* equally relevant, creation order still
      decides which survive the caller's cut, because that is what a tie-break is.
      Measured against the retriever this branch stands in for: over 60
      near-identical documents, the trigram lookup and BM25 select the same first
      ten in the same order. The scan is no more arbitrary under a tie than the
      path it replaces, which is the most it can promise.

    ADR-0023 rejected `LIKE` for having no ordering. This is the answer to that
    objection; the `chunk_id` order was not.

    **Cost, measured rather than assumed, and measured on the corpus that exists
    (SEC-8).** The table this replaced was taken on chunks of 500 characters
    while `chunking.TARGET_CHARS` is 1,000 — real Japanese documents chunk to
    within a few characters of it — so every figure in it was roughly half. Over
    20,000 chunks of 1,000 CJK characters, a 392MB index:

    ================================== =========== =========
    Worst legal case                   by chunk_id  as it is
    ================================== =========== =========
    query terms matching nothing             0.48s     0.48s
    match typed first, matching all          0.02s     1.54s
    match typed last, matching all           0.43s     1.92s
    one noun matching everything             0.02s     0.23s
    ================================== =========== =========

    The right-hand column moved since it was first taken, by the 14% that
    :func:`_matched_characters` costs now that it folds both of its lengths, and
    by the nothing that dropping the `LIMIT` costs. Both re-measured on the same
    corpus rather than adjusted.

    **The two expensive cases are not disjoint, and the claim that they were is
    what hid the worst one.** Terms are OR-ed and SQLite short-circuits on the
    first `LIKE` that matches, so where the matching term sits in the query
    decides the price: type it first and the scan stops at predicate one, type it
    last and every row pays for every `LIKE` *and* for the order. That is the
    third row, it is reachable from the public API by typing eight two-character
    terms with the real one at the end — about 24 characters, a hundredth of
    `MAX_QUERY_CHARS` — and it needs no tuning to reach.

    Note what the left-hand column now says: dropping the relevance order would
    save 1.49s of the 1.92s, so this is a query whose price really is its
    ordering and :data:`SCAN_TERMS` really is what holds it. That was *not* true
    of the shape this replaced. There the `WHERE` alone cost 3.56s of 4.19s, so
    the ranking prefix governed 15% of the query while being described as the
    thing that kept the total down — a bound on the cheap half, in front of an
    unbounded expensive one.

    1.92s is accepted rather than hidden, and the acceptance belongs here rather
    than in a reader's inference from a table. It is a single GIL-releasing
    thread in a daemon serving every project on the machine, so it is not free:
    it is 46% of the 4.19s the same query cost before, it is paid **once** per
    search now rather than once per depth pass, and it is held down by a
    constant with a measured table beside it. Going lower means spending fewer
    terms and answering less; why that trade stops at eight rather than four is
    recorded at the constant, which is where anyone retuning it will be.

    **What this paragraph used to add — that 1.92s is "far below the alternative
    on this path" — is backwards, measured.** The alternative is
    :func:`~theurian.mcp.search.substring_answer`, the canonical-store walk that
    runs whenever no index can answer. Same machine, same corpus sizes, minimum
    of three runs, 1,000 CJK characters per row:

    =========== ==================== =======================
    rows        ``_scan``, no match  this scan, worst 8-term
    =========== ==================== =======================
    4,000                     198 ms                  401 ms
    8,000                     398 ms                  806 ms
    =========== ==================== =======================

    About half, not far above — and further apart on document-shaped input,
    where `_scan` costs roughly 43us per document plus 8us per thousand
    characters, so the same 20M characters carried as 9,000-character documents
    costs it about 260ms against the 1.92s above.

    **The "same match" premise was wrong too, which is why the ordering
    inverts.** `_scan` tests the whole query as one literal substring; this
    statement is an up-to-eight-term OR with a relevance order over every
    matching row. Different work, not the same work in a different language —
    handing `_scan` the eight-term query measured 196ms at 4,000 rows,
    indistinguishable from no match, because it does not spend terms. It also
    does *two* queries per document, not one: the revision, then its source
    anchors.

    **The ground that does hold is the GIL, and it inverts the reasoning without
    changing the conclusion.** `_scan` is a Python ``in`` over each revision's
    whole title and body, and it holds the interpreter lock for all of it, where
    `sqlite3` releases it around `execute`. Under four concurrent callers, 5ms
    asyncio ticks, `_scan` raises the p95 tick delay of the loop serving
    `/health` by about 2.1x and the worst by an order of magnitude; this
    statement leaves the p95 at its idle value and the worst within a small
    multiple of it. Ratios rather than absolutes: the harness cannot control the
    machine, and its worst column moved by a third between runs.

    So this branch trades wall clock for latency isolation in a daemon shared by
    every project on the machine, which is the right trade — but it is a trade,
    not a saving. `docs/security/threat-model.md` carries the numbers under T-6,
    where this scan is one of three members rather than the only one.

    One cheaper shape was measured and rejected. Hoisting the case fold into a
    ``WITH ... AS MATERIALIZED`` computes `lower()` once per row instead of once
    per term and brings the third row back to 1.67s — but what it materialises is
    the folded *text* of every matching chunk, tens of megabytes of temporary
    storage per query, in the daemon SEC-8 is about. Trading 0.25s for a temp
    allocation proportional to the corpus is the wrong direction here. A plain
    subquery buys nothing at all: SQLite flattens it and evaluates the fold per
    reference again, measured at 1.92s.

    Args:
        terms: What to match, longest first. Must not be empty; only the first
            :data:`SCAN_TERMS` are spent.
        clauses: The project-and-status filter, from `SqliteIndexStore._scope`
            -- two of FR-R1's five axes, not the whole of it (#63). Module-owned
            literals — this function interpolates them and binds nothing.
        scope: The values those clauses bind.

    Returns:
        ``(sql, parameters)``, assembled together on purpose. Splitting them
        across two functions is how the order and the match come to disagree
        about which term is which.

    Raises:
        ValueError: if ``terms`` is empty. Not reachable from a caller — the one
            call site returns early — but an empty tuple builds a statement with
            an empty `SELECT` expression and an empty `WHERE`, which SQLite
            rejects as a syntax error near `AS`. That is a fair description of
            neither the fault nor its location.
    """
    if not terms:
        msg = (
            "scan_statement needs at least one term; callers must return early "
            "when `to_scan_terms` yields nothing, as "
            "`SqliteIndexStore._scan_below_the_trigram_floor` does."
        )
        raise ValueError(msg)

    # One slice, feeding both halves. Two slices is how the rows a query selects
    # and the rows it can rank came to differ in the first place.
    spent = terms[:SCAN_TERMS]
    matches = " OR ".join(
        f"chunks.{column} LIKE ? ESCAPE '{LIKE_ESCAPE}'"
        for _ in spent
        for column in SUBSTRING_COLUMNS
    )
    match_parameters = [term.pattern for term in spent for _ in SUBSTRING_COLUMNS]
    ranking_parameters = [term.text for term in spent for _ in SUBSTRING_COLUMNS]

    sql = (
        "SELECT chunks.chunk_id, chunks.item_id, chunks.revision_id, "  # noqa: S608 - clauses are module-owned literals; every value is bound
        f"  {_matched_characters(spent)} AS matched_characters "
        "FROM chunks "
        f"WHERE ({matches}) AND {' AND '.join(clauses)} "
        "ORDER BY matched_characters DESC, chunks.chunk_id"
    )
    # Parameters bind by position in the statement text, so the ordering terms
    # come first: they appear in the SELECT list, ahead of the WHERE.
    return sql, (*ranking_parameters, *match_parameters, *scope)


def _matched_characters(terms: Sequence[ScanTerm]) -> str:
    """How many characters of the query a row accounts for, as SQL.

    `length(x) - length(replace(x, term, ''))` is occurrences times the term's
    length — SQLite has no `count`, and this is the idiom that stands in for one.

    `lower()` inside `replace` because `LIKE` is case-insensitive: without it a
    row matched through `AB` counts zero occurrences of `ab` and sorts below rows
    that matched it less often, so the order would contradict the selection that
    produced it.

    **And `lower()` on the outer `length` too, which is not symmetry for its own
    sake.** Subtracting a folded length from an unfolded one is only arithmetic
    while folding cannot change a string's length. SQLite's own `lower()` folds
    ASCII and stops, so the two agree exactly — including where they are both
    wrong, which
    :func:`~theurian.infrastructure.sqlite.index_query.to_scan_terms` records.
    But `lower()` is one of the functions an ICU-enabled build **replaces**, and
    a full Unicode fold does change lengths: `İ` becomes `i` + U+0307. Measured
    with SQLite's `lower` overridden by a full fold, a chunk holding five `İ` and
    one occurrence of the query term scored 0 instead of 5 — indistinguishable
    from a chunk the term does not appear in, having been selected precisely
    because it does. Enough of them and it goes negative and sorts below those.
    The caller keeps the best fifty by this key, so a key that contradicts the
    `WHERE` hides documents rather than merely misordering them, which is the
    same defect :data:`SCAN_TERMS` records under a different cause.

    Costs 14% on the worst legal query, priced in :func:`scan_statement`, which
    also records the cheaper shape that was measured and rejected.
    """
    return " + ".join(
        f"(length(lower(chunks.{column})) - length(replace(lower(chunks.{column}), lower(?), '')))"
        for _ in terms
        for column in SUBSTRING_COLUMNS
    )


__all__ = ["SCAN_TERMS", "SUBSTRING_COLUMNS", "scan_statement"]
