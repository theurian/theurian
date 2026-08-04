"""Turning caller text into something safe to hand SQLite (SEC-8).

Split out of :mod:`theurian.infrastructure.sqlite.index_store`, which had grown
past the size at which a file can be read in one sitting. The seam is not
arbitrary: nothing here opens a connection, names a table, or imports `sqlite3`.
It is a *query language* module — what FTS5 treats as an operator, what a trigram
index can match, what `LIKE` treats as a wildcard, what SQLite's C API can carry
at all — that happens to live beside a store.

That separation is worth having on its own. This is the only place a
caller-supplied string becomes SQL text: the dense retriever takes a vector, and
the by-id reads take chunk ids the index itself produced. Everything a query has
to survive before it reaches a database is therefore in one file, and a change to
it is a change a reviewer can see whole.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

#: Characters that mean something to FTS5's query syntax. A user searching for
#: `auth OR "token"` means those as words, not as operators, and a query that
#: raised a syntax error at them would be a search box that punishes punctuation.
_FTS_SPECIAL: Final = '"*():^-'

#: Bounds on what one query may cost. FTS5's cost is roughly quadratic in term
#: count and linear in corpus size: measured against 2,000 chunks, a 500-term
#: query took 8.7 seconds and a 2,000-term query did not finish inside a minute.
#:
#: That is not merely slow. The MCP SDK runs synchronous tools on a 40-thread
#: pool, and `sqlite3` releases the GIL, so a handful of such queries saturate
#: the CPU and every tool call for *every project this daemon serves* waits
#: behind them. A query is attacker-influenceable — an agent composes it after
#: reading content — so it gets the same input bounds as any other parser
#: input (SEC-8).
MAX_QUERY_CHARS: Final = 2_000
MAX_QUERY_TERMS: Final = 64

#: Trigram matching needs at least three characters to form one gram.
_MIN_TRIGRAM_CHARS: Final = 3

#: The escape character for the `LIKE` floor. It reaches SQLite as the literal
#: `ESCAPE '\'`, which is exactly what SQLite reads: it applies no C-style
#: processing inside string literals, so the backslash stands for itself.
#:
#: Public because the caller writing the `ESCAPE` clause has to agree with the
#: caller writing the needle, and the two are now in different files. Disagreeing
#: about the escape character would not raise -- it would silently change which
#: rows match.
LIKE_ESCAPE: Final = "\\"

#: What a `LIKE` needle has to neutralise, the escape character first so it is
#: not escaped a second time by the passes that follow.
_LIKE_WILDCARDS: Final = (LIKE_ESCAPE, "%", "_")

#: Below this a term is only worth scanning for if it is a word on its own.
_MIN_SCAN_CHARS: Final = 2

#: Scripts written without word boundaries, where one character can be a term.
#:
#: The same idea as :data:`theurian.domain.ranking._DENSE_SCRIPT_RANGES`, and
#: deliberately **not** that table. Importing it would be legal — infrastructure
#: may read domain — but it answers a different question: how expensive a
#: character is to tokenize. Its ranges therefore span whole blocks including
#: emoji and CJK punctuation, so borrowing it would let `。` and `🎉` each start a
#: scan of the corpus. Two tables that happen to agree today are safer than one
#: table asked two questions, because retuning the token estimate would otherwise
#: change which queries are allowed to scan.
#:
#: **Letters only**, which is where the two tables actually differ: every block
#: below is trimmed to its letters, so the marks that live at the end of the kana
#: blocks — `ー`, `・`, and the sound and iteration marks — are refused. They are
#: punctuation in any script, and no corpus is worth reading end to end to find
#: one.
#:
#: A single kana is admitted even though it is far more often a particle than a
#: word, so `の` does still read the whole index. That is a recall-shaped
#: nuisance, bounded at 0.11s by the measurement on
#: :meth:`SqliteIndexStore._scan_below_the_trigram_floor`, and it is preferred to
#: the alternative: guessing which kana are words is how the trigram floor
#: blacked out Japanese search in the first place, and the cost of guessing wrong
#: is not symmetric.
_SINGLE_CHARACTER_WORD_RANGES: Final = (
    (0x3041, 0x3096),  # Hiragana letters
    (0x30A1, 0x30FA),  # Katakana letters
    (0x3400, 0x4DBF),  # CJK extension A
    (0x4E00, 0x9FFF),  # CJK unified ideographs
    (0xAC00, 0xD7A3),  # Hangul syllables
    (0xF900, 0xFAFF),  # CJK compatibility ideographs
    (0xFF66, 0xFF6F),  # Halfwidth katakana, small forms
    (0xFF71, 0xFF9D),  # Halfwidth katakana, the rest -- FF70 is the sound mark
)


@dataclass(frozen=True, slots=True)
class ScanTerm:
    """One term in the two forms the scan below the trigram floor needs.

    Built together because they have to agree. The pattern decides *which* rows
    match and the text decides *where* they sort, so a term whose wildcards were
    escaped for one and not the other would produce an order that no longer
    describes the match — `100%` matching a row and then counting zero
    occurrences in it. Handing the caller a pair makes that impossible to get
    wrong by using the two builders inconsistently.
    """

    #: `%…%`, wildcards neutralised, for `LIKE … ESCAPE`.
    pattern: str
    #: The term as the caller typed it, for counting occurrences.
    text: str


def _is_transportable(term: str) -> bool:
    """Whether this term can be handed to SQLite as text at all (SEC-8).

    The contract with SQLite is not "a Python string"; it is *a NUL-terminated
    UTF-8 byte string*. Two kinds of `str` cannot become one, and both are
    reachable from a JSON-RPC caller because JSON can carry ``\\u0000`` and an
    unpaired ``\\ud800``:

    - a NUL ends the C string early, so FTS5 stops reading the MATCH expression
      mid-token and reports ``unterminated string``;
    - a lone surrogate cannot be encoded as UTF-8 at all, so the failure is a
      ``UnicodeEncodeError`` raised by the driver before SQLite is even called —
      which no ``except sqlite3.OperationalError`` could ever have caught.

    Both used to escape as a tool failure at the agent. Both are now a term this
    matcher declines to spend, which is the same answer punctuation already got —
    held by ``test_a_nul_byte_in_a_query_returns_nothing_rather_than_raising``
    and ``test_a_lone_surrogate_in_a_query_returns_nothing_rather_than_raising``
    in ``tests/integration/test_index_store.py``, both against both retrievers.
    Dropping the encode check below puts the ``UnicodeEncodeError`` straight back
    out of ``connection.execute``, which is where the four surrogate cases fail.

    Stated as *this* property rather than as a list of bad characters, on
    purpose. A query is an arbitrary string chosen by something that has just
    read untrusted content, so the safe formulation is "what can cross this
    boundary", not "which characters have been observed to break it". Measured
    against the alternative: every other C0 and C1 control, ZWSP, the BOM, the
    non-characters U+FFFE/U+FFFF, and U+10FFFF all cross it intact and match
    nothing, so banning controls wholesale would have been a rule with no defect
    behind it.
    """
    if "\x00" in term:
        return False
    try:
        term.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _query_terms(query: str, *, min_length: int) -> list[str]:
    """The distinct terms a query is allowed to spend, longest first.

    De-duplicated, because a repeated term adds cost to the expression and
    changes nothing about the BM25 order: ``"token " * 2000`` collapses to one
    term rather than to a minute of CPU.

    **Longest first is a selection rule, not a display order.** When a query
    brings more distinct terms than :data:`MAX_QUERY_TERMS`, something has to be
    dropped, and taking the first N in the order they were typed is the worst
    available choice — an English question front-loads its least selective words
    ("how do we handle the ...") so the truncated query keeps `how`, `do`, `we`
    and discards the noun it was about. The caller believes they searched for
    that noun. Length is a cheap, tokenizer-free proxy for selectivity, and under
    an OR match a low-IDF term barely moves the BM25 order anyway.

    The alternative was to keep the typed order and report the truncation. That
    was rejected here rather than dismissed: the count would have to travel back
    through :class:`~theurian.domain.ports.index_store.IndexStore` to reach a
    client, widening the port for a condition a query must exceed 64 distinct
    terms to reach — while still answering the question worse than this does.

    Ties keep the order the user typed, because ``sorted`` is stable, so one
    query always produces one expression (FR-R7).

    Every public function in this module goes through here, which is what makes
    :func:`_is_transportable` unskippable: a term that cannot cross into SQLite
    is dropped rather than the whole query, so `auth token\\x00` still searches
    for `auth`. Adding a fourth builder that bypasses this is how that guarantee
    would be lost.
    """
    unique: dict[str, None] = {}
    for word in query[:MAX_QUERY_CHARS].replace('"', " ").split():
        term = word.strip(_FTS_SPECIAL)
        if len(term) >= min_length and _is_transportable(term):
            unique.setdefault(term, None)
    return sorted(unique, key=lambda term: -len(term))[:MAX_QUERY_TERMS]


def to_match_expression(query: str) -> str:
    """Turn user text into an FTS5 MATCH expression.

    Every term is quoted, so FTS5's operators cannot be reached from user input.
    That is a correctness measure as much as a security one: someone searching
    for `auth OR token` means three words, and a bare `-` or unbalanced `"` would
    otherwise raise a syntax error at a person who typed a perfectly ordinary
    sentence.

    Bounded in length, in distinct terms, and de-duplicated — see
    :data:`MAX_QUERY_CHARS` and :func:`_query_terms`.

    Terms are ORed and left to BM25 to rank. ANDing them requires every token to
    appear in one chunk -- including `how`, `do`, `for`, which the `unicode61`
    tokenizer does not treat as stop words -- so a natural-language question, the
    main thing an agent actually sends, matches nothing at all. Recall is BM25's
    problem to rank, not the matcher's problem to refuse.
    """
    return " OR ".join(f'"{term}"' for term in _query_terms(query, min_length=1))


def to_trigram_expression(query: str) -> str:
    """Turn user text into a trigram MATCH expression.

    Terms shorter than a trigram are dropped rather than sent: FTS5 cannot match
    them against a trigram index, and including one makes the whole expression
    return nothing.

    Dropped *from the expression*, not from the search — but only when the
    expression ends up empty. Then this returns `""`,
    :meth:`~theurian.infrastructure.sqlite.index_store.SqliteIndexStore.search_substring`
    reads that as "this index cannot answer by lookup" and scans with
    :func:`to_scan_terms` instead, and a two-character Japanese noun is
    searchable at all. Anyone tightening the floor here is changing what that
    branch is asked to cover.

    **The residual, stated where it is caused rather than only in the ADR.** A
    short term *mixed with* one of three characters or more is still dropped and
    nothing scans for it: the expression is non-empty, so the floor does not
    fire, and `認証 トークン` searches only for `トークン` on this retriever. That
    is a recall loss rather than the blackout the empty case was — the long term
    still answers — and it is left open deliberately. Closing it means OR-ing
    `LIKE` predicates into the same statement as the MATCH, where `bm25` is
    undefined for the rows only `LIKE` matched, so the retriever would have to
    return an order it cannot compute. That is a ranking-model decision, and it
    belongs with the per-term IDF work deferred to Milestone 6 (ADR-0023).
    """
    return " OR ".join(f'"{term}"' for term in _query_terms(query, min_length=_MIN_TRIGRAM_CHARS))


def _is_a_word_on_its_own(term: str) -> bool:
    """Whether a single character is a word rather than a letter.

    `鍵` is a noun; `e` is a letter. The distinction is the whole reason this
    predicate exists, because the two want opposite answers from the same code
    path and length cannot tell them apart.
    """
    return len(term) == 1 and any(
        low <= ord(term) <= high for low, high in _SINGLE_CHARACTER_WORD_RANGES
    )


def _is_worth_scanning(term: str) -> bool:
    """Whether this term earns a pass over the corpus.

    The trigram floor is three characters; this one is two, and the gap is where
    the scan lives. Going to one — which is what the floor originally did — sends
    `e` through as ``LIKE '%e%'``, and the answer to a single Latin letter is
    then whichever fifty chunks the sort happens to select. Nothing is gained by
    it either: `unicode61` tokenizes `e` perfectly well, so the word index
    already answers that query as a *word*, which is the only sense in which it
    is a query at all.

    A single CJK character is the opposite case and the reason the floor was
    lowered in the first place. `鍵` is a noun, `unicode61` cannot segment it out
    of a Japanese sentence, and no other retriever in the system can answer it —
    so the decision is made by script, not by length. Raising this to two flat
    would take back exactly what the previous round fixed.
    """
    return len(term) >= _MIN_SCAN_CHARS or _is_a_word_on_its_own(term)


def to_scan_terms(query: str) -> tuple[ScanTerm, ...]:
    """The terms the scan below the trigram floor may spend, longest first.

    `%` and `_` are LIKE's wildcards, so an unescaped term does not mean what the
    caller typed: `a_b` would match `a<any character>b`, and a query of a single
    `%` would match the entire corpus -- a search whose result set is chosen by
    punctuation. Escaped rather than rejected, because both characters are
    ordinary in the identifiers engineering knowledge is full of (`state_hash`,
    `100%`).

    Goes through :func:`_query_terms` like the two expression builders, and for
    the same reason: this is caller-controlled text on its way into SQL, so it
    gets the same bounds and the same transportability check they get (SEC-8).

    **Longest first is part of the contract**, not an accident of
    :func:`_query_terms`. The caller ranks on a prefix of this tuple, because
    counting occurrences of every one of `MAX_QUERY_TERMS` terms costs seconds
    on a corpus where the match itself costs milliseconds, so which terms come
    first decides which ones carry the ordering. Length is the same selectivity
    proxy the term bound already uses.

    **Case folding is asymmetric across the floor, and not fixed here.** SQLite's
    `LIKE` folds ASCII only, while the trigram tokenizer folds the whole of
    Unicode — so a two-letter Greek query is case-sensitive on this path while
    the same word with one letter more is not. Japanese and Chinese are caseless,
    so the scripts this branch exists for are unaffected; and the obvious remedy,
    `lower()` on both sides, is ASCII-only in SQLite too, so it would buy nothing
    but cost. Recorded rather than closed: it becomes real the day a Greek or
    Cyrillic corpus turns up, and then it is the `icu` tokenizer or nothing.
    """
    return tuple(
        ScanTerm(pattern=_escape_like(term), text=term)
        for term in _query_terms(query, min_length=1)
        if _is_worth_scanning(term)
    )


def _escape_like(term: str) -> str:
    escaped = term
    for wildcard in _LIKE_WILDCARDS:
        escaped = escaped.replace(wildcard, LIKE_ESCAPE + wildcard)
    return f"%{escaped}%"


__all__ = [
    "LIKE_ESCAPE",
    "MAX_QUERY_CHARS",
    "MAX_QUERY_TERMS",
    "ScanTerm",
    "to_match_expression",
    "to_scan_terms",
    "to_trigram_expression",
]
