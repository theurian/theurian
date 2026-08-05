"""The scan's ranking key against the `lower()` this SQLite happens to ship.

`SqliteIndexStore` cannot reach this: it opens whatever `lower()` the interpreter
was linked against, and CPython's bundled SQLite folds ASCII only. So the branch
:func:`~theurian.infrastructure.sqlite.index_scan._matched_characters` documents
-- an ICU-enabled build, where `lower()` is a full Unicode fold that *changes
string lengths* -- is unreachable from the store's own fixtures, and dropping the
outer `lower()` was measured to pass the entire suite.

A real `sqlite3` connection with `lower` replaced through `create_function` is
what an ICU build looks like from the statement's side, which is the only side
this statement has. Real SQLite, real statement builder, no store.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Final

import pytest

from theurian.infrastructure.sqlite.index_query import to_scan_terms
from theurian.infrastructure.sqlite.index_scan import SUBSTRING_COLUMNS, scan_statement

pytestmark = pytest.mark.integration

#: The columns the statement names, beyond the ones it ranks on. Derived from
#: `SUBSTRING_COLUMNS` rather than written out, so a column added to the scan is
#: added to this table too and the test keeps measuring the shipped statement.
_CHUNK_COLUMNS: Final = ("chunk_id", "item_id", "revision_id", "project_id", *SUBSTRING_COLUMNS)

#: `İ` (U+0130) is the one common character whose lowercase is *longer* than
#: itself: a full Unicode fold gives `i` + U+0307, two code points for one. Five
#: of them is what the docstring in `_matched_characters` records measuring, and
#: five is enough for the unfolded length to fall below the folded one by more
#: than the term accounts for -- so the defect shows up as a negative score
#: rather than merely a small one.
WITNESS_TEXT: Final = "İ" * 5 + " gateway notes"

#: Two characters, because that is what this branch is for: below the trigram
#: floor, matched by `LIKE`, ranked by counting characters. Present in
#: `WITNESS_TEXT` exactly once, so the honest answer is its own length.
WITNESS_TERM: Final = "ga"


def _matched_characters(*, fold: Callable[[str], str] | None) -> list[int]:
    """Run the shipped scan over one row, with `lower()` replaced or left alone.

    Args:
        fold: what this SQLite's `lower()` does, or ``None`` for the ASCII-only
            fold CPython's bundled SQLite ships.

    Returns:
        The ranking key of every row the statement selected -- empty if the
        `WHERE` matched nothing, which would mean the fixture never reached the
        branch under test.
    """
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    try:
        if fold is not None:
            connection.create_function(
                "lower", 1, lambda value: None if value is None else fold(str(value))
            )
        columns = ", ".join(f"{column} TEXT" for column in _CHUNK_COLUMNS)
        connection.execute(f"CREATE TABLE chunks ({columns})")
        row = dict.fromkeys(SUBSTRING_COLUMNS, "") | {
            "chunk_id": "c0",
            "item_id": "architecture.auth",
            "revision_id": "rev-c0",
            "project_id": "demo",
            "text": WITNESS_TEXT,
        }
        placeholders = ", ".join("?" for _ in _CHUNK_COLUMNS)
        connection.execute(
            f"INSERT INTO chunks VALUES ({placeholders})",  # noqa: S608 - placeholders only
            tuple(row[column] for column in _CHUNK_COLUMNS),
        )

        sql, parameters = scan_statement(
            to_scan_terms(WITNESS_TERM), clauses=["chunks.project_id = ?"], scope=["demo"]
        )
        return [int(hit["matched_characters"]) for hit in connection.execute(sql, parameters)]
    finally:
        connection.close()


def test_the_scan_ranks_a_chunk_the_same_however_this_sqlite_folds_case() -> None:
    """The ranking key may not contradict the `WHERE` that produced it (FR-R7).

    The scan selects rows by `LIKE`, which is case-insensitive, and then orders
    them by how many characters of the query each row accounts for. The caller
    keeps the best fifty of those, so the key is a *selection* key: a row that
    scores below rows the term does not appear in is not misordered, it is
    hidden.

    `length(x) - length(replace(lower(x), lower(term), ''))` is only arithmetic
    while folding cannot change a length. That holds for the ASCII fold CPython
    ships and fails for the full Unicode fold an ICU-enabled SQLite installs in
    its place: `İ` folds to two code points, so five of them inflate the
    subtrahend by five and the row scores -3 -- below every row that does not
    contain `ga` at all, having been selected precisely because it does.

    Asserted as an equality between the two folds rather than as a constant,
    because that is the property: the same corpus and the same query rank the
    same way whichever SQLite the operator's machine has. `len(WITNESS_TERM)` is
    then what both must be -- one occurrence of a two-character term is two
    characters of the query accounted for.

    Not reachable through `SqliteIndexStore`, which opens the interpreter's own
    SQLite and therefore only ever sees the ASCII fold. Removing the outer
    `lower()` from `_matched_characters` passes the whole suite without this.
    """
    assert len(WITNESS_TEXT.lower()) > len(WITNESS_TEXT), (
        "the witness needs a character whose fold is longer than itself, or "
        "both folds agree and this measures nothing"
    )

    ascii_only = _matched_characters(fold=None)
    full_unicode = _matched_characters(fold=str.lower)

    assert ascii_only == [len(WITNESS_TERM)], (
        "the row must be selected and scored by the term it contains"
    )
    assert full_unicode == ascii_only, (
        "an ICU-enabled SQLite must rank this row exactly as a stock one does; "
        "a negative key here hides a matching chunk rather than misordering it"
    )
