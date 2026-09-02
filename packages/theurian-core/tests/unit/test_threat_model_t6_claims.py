"""T-6's served-text bound, held to the projection the store actually applies.

``docs/security/threat-model.md``'s T-6 entry carries a table of the bounds
``review.findings`` puts on one call, and one row of it is the only place a
reader learns two things about the served ``findingText`` bound: **what it
counts**, and **where it is applied**. Both were wrong at once. The Dimension
cell stated the bound in *bytes* where ``max_finding_text_chars()`` counts
characters, and the row named only the wire-side clamp -- which stopped being the
whole truth when the cut moved into the store's serving ``SELECT`` and the daemon
stopped materialising a planted trailer at all.
https://github.com/theurian/theurian/pull/504 corrected the row and the narrative
under it. This module is what stops either drifting again: a corrected claim
about today's code is worth exactly what the sentence it replaced was worth,
until something recomputes it.

**Both sides are derived, and they are written independently.** The fact side is
read off the live symbols -- ``findings_store._SERVE_COLUMNS``, the signature of
``SqliteReviewFindingStore.serve_findings``, and the two bound functions in
``mcp/findings.py`` -- so moving the mechanism takes this module RED at the
moment it moves, which is the moment T-6 has to be rewritten. The prose side is
read out of the entry: the unit word the Dimension cell states, the symbol the
Bound cell cites, and the projection the narrative quotes. Neither side is parsed
from the other, because a pin that read its expectation out of the sentence it
checks would agree with that sentence by construction and measure nothing.

**What it holds.** (1) The Dimension cell states the bound in characters, and the
live projection cuts the TEXT column rather than a BLOB cast -- which is the
choice that makes "characters" mean the same thing on both sides of the boundary,
since ``substr`` on TEXT counts what ``len`` counts; (2) the row cites the symbol
that applies the bound and the narrative quotes the projection, so the entry says
*where* the cut happens and not only that one exists; (3) the projection the row
quotes is really a part of the live ``_SERVE_COLUMNS``; (4) the bound is a
required keyword-only argument of the serving read, so no caller of that method
can omit it and get a whole column back; (5) the read fetches exactly one
character more than the surface publishes, which is the evidence
``mcp/findings.py::_bounded_text`` marks a cut from.

**What it does not hold.** That the serving read *uses* the projection it
selects, or that anything is actually cut: the statement is assembled at call
time, and a store that selected ``_SERVE_COLUMNS`` and then re-read the whole
column would pass every assertion here. That is behaviour, and it is pinned as
behaviour by ``tests/unit/test_findings_store.py`` and
``tests/integration/test_review_findings_tool.py``. Nor does it hold the other
rows of the table, or T-6's measurements, which make their own claims and have no
pin here.

Pure in the sense the other claim pins are: one document read as text and two
modules read for their symbols, no database, socket or temporary directory.
"""

from __future__ import annotations

import inspect
import re
from typing import Final

import pytest
from threat_model_claims import entry, prose

from theurian.infrastructure.sqlite import findings_store
from theurian.infrastructure.sqlite.findings_store import SqliteReviewFindingStore
from theurian.mcp.findings import max_finding_text_chars, text_fetch_chars

pytestmark = pytest.mark.unit

#: The entry this module reads. Sliced by ``threat_model_claims.entry``, which is
#: where the anchoring rules and the reason for them live.
_THREAT_ID: Final = "T-6"

#: The bound function the row's Bound cell cites, and the key that says which of
#: T-6's table rows this module reads. Keyed on the function rather than on the
#: Dimension cell's own wording, because the Dimension cell is the thing under
#: test: a key read off it would stop matching exactly when the cell drifted, and
#: the row would drop out of the population rather than fail.
_BOUND_FUNCTION: Final = "max_finding_text_chars"

#: The store constant that carries the serving projection, by name. Spelled out
#: rather than discovered, and guarded by a ``hasattr`` premise below, so a rename
#: fails naming itself instead of quietly checking a constant that is gone.
_PROJECTION_CONSTANT: Final = "_SERVE_COLUMNS"

#: How T-6's Bound cell cites the place the bound is applied. The module path is
#: written the way the entry writes it -- ``<file>::<symbol>`` -- and the file part
#: is kept short deliberately: the entry spells the full package path, and
#: matching on the tail leaves a directory move to the entry's other citations
#: rather than reddening here for something that is not this claim.
_APPLYING_SITE: Final = f"findings_store.py::{_PROJECTION_CONSTANT}"

#: The column the projection cuts, which is also ``substr``'s subject in the live
#: constant. The bare column name is the character-counting form; the byte-counting
#: alternative ``substr(CAST(finding_text AS BLOB), 1, ?)`` is what would make the
#: Dimension cell's "characters" false, and it is what this catches.
_TEXT_COLUMN: Final = "finding_text"

#: The serving read's bound parameter.
_TEXT_BOUND_PARAMETER: Final = "text_chars"

#: The unit the Dimension cell states, captured rather than matched, so a RED can
#: report the word the entry now carries. Anchored on the whole phrase rather than
#: on a leading word for two reasons: a sibling row of the same table legitimately
#: says *bytes per string filter*, so a bare unit key would read as this claim if
#: the row selection above ever drifted; and a cell reworded past the claim then
#: fails the premise below instead of yielding whichever word came first.
_DIMENSION_UNIT: Final = re.compile(r"^([a-z]+) per served findingtext\b")

#: A backticked ``substr(finding_text ...)`` quote, read from the **raw** row so
#: the backticks are still there: an unquoted mention of the projection in running
#: prose is not the citation this checks, and matching one would let a sentence
#: that merely describes the cut satisfy a pin about what the entry quotes.
_QUOTED_PROJECTION: Final = re.compile(r"`(substr\(finding_text[^`]*)`")

#: ``substr``'s first argument in the live constant -- everything up to the first
#: comma, which is the whole subject expression whether it is a bare column or a
#: cast.
_SUBSTR_SUBJECT: Final = re.compile(r"substr\(([^,]+),")

#: How the narrative writes the projection, normalised. Shorter than the row's
#: quote because the two spell the length differently and deliberately: the table
#: quotes the statement's ``?`` parameter, the narrative quotes the call form
#: ``substr(finding_text, 1, text_fetch_chars())``. A key carrying either spelling
#: would hold one of them to the other's wording.
_NARRATIVE_PROJECTION: Final = "substr(finding_text"


def _table_row(line: str) -> bool:
    """Whether *line* is a Markdown table row rather than prose."""
    return line.lstrip().startswith("|")


def _bound_row() -> str:
    """T-6's one table row stating the served ``findingText`` bound, raw.

    Raw rather than normalised, because the arms below split it into cells and
    read its backticked citations, and :func:`prose` destroys both.

    Scoped to table rows inside T-6 before the bound function is looked for: the
    entry names ``max_finding_text_chars()`` in its running prose as well, in the
    paragraph recording that the bound counts characters. A scan over the whole
    entry would let that paragraph stand in for the row, so a rewrite that dropped
    the row's own citation would pass on the strength of a sentence beside it.
    """
    rows = [line for line in entry(_THREAT_ID).splitlines() if _table_row(line)]
    carrying = [row for row in rows if _BOUND_FUNCTION in row]

    assert len(carrying) == 1, (
        f"`{_BOUND_FUNCTION}` identifies {len(carrying)} of T-6's {len(rows)} table "
        f"rows, expected 1. Zero means the row that states the served text bound was "
        f"reworded past its own key and everything below would pass over nothing; "
        f"more than one means what is read below is a row this module never chose"
    )
    return carrying[0]


def _cells(row: str) -> tuple[str, ...]:
    """*row*'s three cells -- Dimension, Bound, "Refuses or clamps" -- in order.

    The count is asserted rather than indexed into, because a cell whose text
    gained a ``|`` would shift every column right and the Dimension arm would then
    be reading whatever landed in slot 0.
    """
    cells = tuple(row.strip().strip("|").split("|"))

    assert len(cells) == 3, (
        f"T-6's served-text bound row splits into {len(cells)} cells, expected 3 "
        f"(Dimension, Bound, refuses-or-clamps). The columns below are read by "
        f"position, so a row of another width means they are read off the wrong "
        f"ones: {row[:300]}"
    )
    return cells


def _narrative() -> str:
    """T-6's prose, normalised, with every table row removed.

    The removal is the point. The projection is quoted in the Bound cell as well,
    so a scan over the whole entry would be satisfied by the table -- and the claim
    this arm holds is that a reader who reads the *narrative* learns where the cut
    happens, which is what the table's one cell cannot carry on its own.
    """
    return prose("\n".join(line for line in entry(_THREAT_ID).splitlines() if not _table_row(line)))


def test_t6_states_the_served_text_bound_in_characters_and_the_cut_counts_them() -> None:
    """RED means T-6's unit and the store's own cut disagree about what is counted.

    A reader sizing this bound -- for a response budget, or to decide whether a
    2,000 bound is enough for a trailer line -- takes the Dimension cell's unit at
    face value. It said *bytes* until #504 while ``max_finding_text_chars()``
    counted characters, which understates the worst case by a factor of four on
    astral text; the entry now says characters and records the byte figure
    separately.

    The two halves fail for different reasons and both are the claim. The prose
    half is the unit word the cell states. The fact half is what the live
    projection cuts: ``substr`` on a TEXT value counts code points, which is what
    ``len`` counts too, while the byte-counting alternative the store's own
    docstring names -- ``substr(CAST(finding_text AS BLOB), 1, ?)`` -- would make
    the corrected cell false again without touching a word of the entry. So a
    switch to that form reddens here rather than leaving the record describing a
    bound in the wrong unit for a second time.

    The premises come first: a Dimension cell reworded past its own key, or a
    constant whose ``substr`` call can no longer be read, would leave both
    comparisons passing over nothing.
    """
    dimension = prose(_cells(_bound_row())[0])
    projection = findings_store._SERVE_COLUMNS

    stated = _DIMENSION_UNIT.findall(dimension)
    assert len(stated) == 1, (
        f"T-6's served-text bound row no longer states its unit as one word matching "
        f"`{_DIMENSION_UNIT.pattern}`, so this pin has nothing to hold against the "
        f"projection: {dimension[:300]}"
    )
    subject = _SUBSTR_SUBJECT.findall(projection)
    assert len(subject) == 1, (
        f"`{_PROJECTION_CONSTANT}` carries {len(subject)} `substr(...)` calls this pin "
        f"can read, expected 1, so what the cut counts cannot be determined from it: "
        f"{projection}"
    )

    assert stated[0] == "characters", (
        f"T-6 states the served `findingText` bound in `{stated[0]}`; "
        f"`max_finding_text_chars()` counts characters, and so does the `substr` that "
        f"applies it. A reader sizing the response by that cell would be out by up to "
        f"four times on astral text -- the entry records the byte worst case as a "
        f"separate figure precisely because the two are not the same number"
    )
    assert subject[0].strip() == _TEXT_COLUMN, (
        f"`{_PROJECTION_CONSTANT}` cuts `{subject[0].strip()}`, not the bare TEXT "
        f"column `{_TEXT_COLUMN}`. `substr` counts code points on TEXT and bytes on a "
        f"BLOB, so this is the change that makes T-6's `characters` false; either the "
        f"cut goes back to the TEXT column or the entry has to say bytes and restate "
        f"the worst case it records"
    )


def test_t6_says_where_the_served_text_bound_is_applied() -> None:
    """RED means T-6 stopped saying the cut happens inside the store's own read.

    Where the bound is applied is not a detail of wording. Until the cut moved into
    the serving ``SELECT``, the daemon materialised every planted byte -- ``limit +
    1`` rows per call, once per concurrent call -- before anything could clamp one
    of them, so an entry describing only a wire-side clamp described a cost the
    process had already paid. A reader auditing that cost follows the entry's
    citation to the code, and a record that names no site sends them nowhere.

    Two places are held, because a reader arrives at two. The table's Bound cell
    must cite the symbol that carries the projection, and the narrative must quote
    the projection itself -- the narrative arm scanning prose with the table
    removed, so the cell cannot answer for both. Dropping either leaves the entry
    saying a bound exists without saying where it bites.

    The premise is that the cited symbol is still there: a citation naming a
    constant the store no longer has is evidence of nothing, and it should fail as
    a missing symbol rather than as a missing phrase.
    """
    row = _bound_row()
    narrative = _narrative()

    assert hasattr(findings_store, _PROJECTION_CONSTANT), (
        f"`findings_store` has no `{_PROJECTION_CONSTANT}`, so T-6's citation of it "
        f"points at a symbol nobody can find; rename the constant in the entry and in "
        f"this module together, or the record describes a mechanism by a name the code "
        f"dropped"
    )

    assert _APPLYING_SITE in row, (
        f"T-6's served-text bound row no longer cites `{_APPLYING_SITE}`, the constant "
        f"that applies the bound. Without it the row states a number and leaves a "
        f"reader auditing the read's own footprint with nowhere to go: {row[:300]}"
    )
    assert _NARRATIVE_PROJECTION in narrative, (
        f"T-6's prose no longer quotes `{_NARRATIVE_PROJECTION}...`, so the entry says "
        f"the served text is bounded without saying the cut is made by SQLite rather "
        f"than after the rows are in this process. That difference is the whole of the "
        f"read face: the table's Bound cell is not read as a substitute here, "
        f"deliberately"
    )


def test_the_projection_t6_quotes_is_the_one_the_serving_select_carries() -> None:
    """RED means the record quotes a projection the store's read does not have.

    The quoted fragment is the entry's evidence, and the two sides move
    independently: a reviewer rewriting the cell can drift the quote, and a change
    to the store's statement can strand it. Either way the record shows a reader
    SQL the product does not run, and the read-face argument it supports -- SQLite
    hands Python no more than the bound plus one character per row -- rests on it.

    The quote is extracted from the entry and looked for in the live constant,
    rather than both being compared to a string written here, so this fails on
    whichever side moved instead of on a third copy nobody reads.

    The premises are that the row still carries exactly one such quote, and that
    the constant is non-empty -- a substring check against an empty projection would
    be about nothing.
    """
    quoted = _QUOTED_PROJECTION.findall(_bound_row())
    projection = findings_store._SERVE_COLUMNS

    assert len(quoted) == 1, (
        f"T-6's served-text bound row carries {len(quoted)} backticked "
        f"`substr(finding_text...)` quotes, expected 1, so this pin cannot say which "
        f"fragment is the claim: {quoted}"
    )
    assert projection, (
        f"`{_PROJECTION_CONSTANT}` is empty, so the containment check below would hold "
        f"for nothing and the entry's quote would be measured against no statement"
    )

    assert quoted[0] in projection, (
        f"T-6 quotes `{quoted[0]}` as the serving projection; `{_PROJECTION_CONSTANT}` "
        f"is `{projection}`. Whichever side moved, the record is showing SQL the store "
        f"does not select, and the read-face argument that rests on it -- that SQLite "
        f"never hands Python more than the bound plus one character per row -- is no "
        f"longer evidenced"
    )


def test_the_serving_read_takes_its_text_bound_as_a_required_keyword() -> None:
    """RED means a caller of the serving read can omit the bound and get whole rows.

    T-6 records the bound as one a caller cannot provoke past, and the mechanism
    that makes that true at this layer is the signature: ``text_chars`` is
    keyword-only and has no default, so there is no call spelling that fetches the
    column whole. A default would restore exactly the state the read face closed --
    a new call site, written without the argument, materialising whatever a
    contributor committed -- and it would do it silently, because every existing
    call site would keep passing the value.

    Keyword-only is the second half and is not decoration: positional, the bound
    could be transposed with ``query`` by a caller who mis-ordered the arguments,
    and the failure would be a type error at the SQL layer rather than at the call.
    """
    parameters = inspect.signature(SqliteReviewFindingStore.serve_findings).parameters
    bound = parameters.get(_TEXT_BOUND_PARAMETER)

    assert bound is not None, (
        f"`SqliteReviewFindingStore.serve_findings` has no `{_TEXT_BOUND_PARAMETER}` "
        f"parameter ({list(parameters)}); T-6 records the served text bound as applied "
        f"by this read, and a read that takes no bound applies none"
    )

    assert bound.kind is inspect.Parameter.KEYWORD_ONLY, (
        f"`{_TEXT_BOUND_PARAMETER}` is {bound.kind}, not keyword-only. Positionally it "
        f"can be transposed with `query` at a call site, and the resulting failure "
        f"surfaces inside the statement rather than at the call that got it wrong"
    )
    assert bound.default is inspect.Parameter.empty, (
        f"`{_TEXT_BOUND_PARAMETER}` now defaults to `{bound.default!r}`, so a call site "
        f"written without it compiles and fetches whatever the default allows. The "
        f"bound T-6 records is one no call spelling may skip: the cost it prevents is "
        f"paid by the daemon, not by the caller who omitted the argument"
    )


def test_the_read_fetches_one_character_past_what_the_surface_publishes() -> None:
    """RED means the marker on a cut ``findingText`` stops being decidable.

    The extra character is the entire mechanism T-6 describes. The read cuts in
    SQL, so once the rows are in this process the only evidence that a row *was*
    longer is whether that one character came back -- which is what decides between
    marking a value that was cut and mislabelling an authored value that merely
    fits.

    Fetching the bound itself would delete the distinction and the surface would
    have to lie in one direction or the other: mark everything at the bound, or
    publish a cut as the whole finding. Fetching more would hand this process back
    the bytes the cut exists to keep out, a character at a time.

    The premise is that the published bound is positive: an equality between two
    derived numbers holds trivially if the thing they derive from has collapsed.
    """
    published = max_finding_text_chars()
    fetched = text_fetch_chars()

    assert published > 0, (
        f"`max_finding_text_chars()` is {published}, so there is no published bound "
        f"for the fetch to be one character past, and the comparison below would be "
        f"between two figures that mean nothing"
    )

    assert fetched == published + 1, (
        f"the serving read fetches {fetched} characters where the surface publishes "
        f"{published}. It has to be exactly one more: equal deletes the evidence that "
        f"a row was cut, and more hands this process bytes the cut exists to keep out"
    )
