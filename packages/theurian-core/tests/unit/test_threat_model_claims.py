"""The shared threat-model slicing and normalisation, driven by synthetic documents.

``threat_model_claims`` is the primitive every threat-model entry pin scopes
itself with. Nothing about it fails loudly when it is wrong: a slice that starts
too high scans a neighbouring entry and reddens some *other* pin on a correction
that has nothing to do with it, and a slice that runs past the next heading
widens every scan built on it **without ever failing**. That second one is why
this file exists -- a helper checked only by whichever pin happens to notice is a
helper nothing drives.

Synthetic documents, deliberately. Held against the shipped
``docs/security/threat-model.md`` these assertions would pass on the strength of
where that file's headings happen to sit today, which is not the property being
claimed: the claim is about the marker, and a marker is only observable against
text written to contain the cases it distinguishes.

Pure: strings in, strings out. No file is read here at all -- the file-reading
wrapper is exercised by the entry pins themselves.
"""

from __future__ import annotations

from typing import Final

import pytest
from threat_model_claims import SPELLED_NUMBERS, WORD_FOR_COUNT, entry_in, prose

pytestmark = pytest.mark.unit

#: A document with the three cases the slice has to tell apart: an entry above,
#: the entry being read, and a *shallower* heading after it. The trailing section
#: is ``###``, not ``####``, because the marker set claims the earliest heading of
#: **any** level ends the slice, and a document whose next heading were the same
#: level would leave that half of the claim untested.
_DOCUMENT: Final = (
    "# The threat model\n\n"
    "#### T-6 the entry above\n\nabove the slice\n\n"
    "#### T-7 the entry under test\n\ninside the slice\n\n"
    "### TB-3 the section after\n\nbelow the slice\n"
)


def test_an_entry_slice_starts_at_its_heading_and_stops_at_the_next() -> None:
    """RED means every pin built on this scans text its entry does not own.

    The two failures are not symmetric. Starting too high reddens a pin on
    another entry's prose, which is loud and misleading. Running past the next
    heading is silent: it never fails, it just answers about more text than it
    says it does -- and a pin that asserts "this entry does not name X" then
    passes because it read a neighbour that does.
    """
    sliced = entry_in(_DOCUMENT, "T-7")

    assert "inside the slice" in sliced, f"the slice does not contain the entry's body: {sliced!r}"
    assert "above the slice" not in sliced, f"the slice starts above the heading: {sliced!r}"
    assert "below the slice" not in sliced, f"the slice runs past the entry: {sliced!r}"


def test_an_id_that_prefixes_another_slices_its_own_entry() -> None:
    """RED means ``T-7`` opens on ``T-7a`` -- the case the trailing space is for.

    The threat model really does carry lettered siblings (``T-17`` and ``T-17a``),
    and the ids are ordinary substrings of one another. A marker without its
    trailing space matches both, and ``str.count`` would then see two headings and
    fail on the *premise* -- loudly, but naming the wrong problem -- or, in a
    document holding only the sibling, slice the sibling silently and report it as
    the entry.
    """
    document = (
        "# The threat model\n\n"
        "#### T-7 the entry under test\n\ninside the slice\n\n"
        "#### T-7a the lettered sibling\n\nthe sibling's own body\n"
    )

    sliced = entry_in(document, "T-7")

    assert "inside the slice" in sliced
    assert "sibling" not in sliced, f"`T-7` swallowed `T-7a`: {sliced!r}"
    assert "sibling's own body" in entry_in(document, "T-7a"), (
        "the lettered sibling is not reachable under its own id, so a pin over it "
        "would be scanning nothing"
    )


def test_the_last_entry_in_a_document_runs_to_its_end() -> None:
    """RED means the final entry slices empty, and every pin over it passes vacuously.

    An entry with no heading after it has no end marker to find. The fallback is
    "to the end of the document", and getting it wrong the other way -- returning
    nothing -- is the failure mode that does not announce itself: every membership
    check over an empty string reports the entry as saying nothing at all.
    """
    document = (
        "# The threat model\n\n"
        "#### T-6 the entry above\n\nabove the slice\n\n"
        "#### T-7 the last entry\n\nthe tail\n"
    )

    sliced = entry_in(document, "T-7")

    assert "the tail" in sliced, f"the final entry sliced away its own body: {sliced!r}"
    assert "above the slice" not in sliced


@pytest.mark.parametrize(
    ("document", "why"),
    [
        ("# T\n\n#### T-6 only this one\n\nbody\n", "the id is absent"),
        ("# T\n\n#### T-7 first\n\none\n\n#### T-7 second\n\ntwo\n", "the id appears twice"),
        ("#### T-7 the very first line\n\nbody\n", "the heading is not preceded by a line break"),
    ],
)
def test_a_heading_that_is_missing_duplicated_or_unanchored_fails_naming_itself(
    document: str, why: str
) -> None:
    """RED means a pin scopes itself to nothing, or to whichever copy came first, in silence.

    All three are premise failures rather than claim failures, and they have to
    arrive as such: an entry that is not there, an entry that is there twice, and
    an entry whose heading is the document's own first line all let every
    assertion downstream pass over text the module never chose.

    The third case is the marker's own leading ``\\n`` showing through, and it is
    a real precondition rather than an accident: anchoring on a line *break* is
    what keeps ``#### T-7`` from matching mid-sentence, and the cost is that a
    document beginning with the heading has none. The shipped threat model opens
    with its title, so no entry is ever first -- but a caller handing in a slice of
    the document would meet this, and it fails loudly instead of scanning nothing.
    """
    with pytest.raises(AssertionError, match="expected 1"):
        entry_in(document, "T-7")


def test_prose_flattens_the_wraps_the_case_and_the_markup_a_key_would_miss() -> None:
    """RED means a key written the way a sentence reads stops matching it.

    Every entry pin searches for phrases, and every phrase in this document is
    soft-wrapped, sentence-cased and marked up: symbols in code spans, counts in
    bold. The control is the second assertion -- the same key over the raw text
    finds nothing -- because a normalisation that had quietly stopped doing one of
    the three would leave the first assertion true for the other two.
    """
    raw = "The **three** database families that\nlive beside `active.json` are derived."

    normalised = prose(raw)

    assert "the three database families that live beside active.json" in normalised
    assert "the three database families that live beside active.json" not in raw.lower(), (
        "the key is found in the raw text too, so this test would pass against a "
        "`prose` that did nothing and proves nothing about the normalisation"
    )


def test_the_two_spelled_number_tables_are_inverses_over_the_same_range() -> None:
    """RED means a RED elsewhere reports the wrong word, or raises instead of failing.

    The entry pins read a count word out of prose through
    :data:`SPELLED_NUMBERS` and name the word a record *should* carry through
    :data:`WORD_FOR_COUNT`. A gap between the two shows up only in a failure
    message -- exactly when nobody is in a position to doubt it -- so the round
    trip is asserted here rather than discovered while reading a RED.
    """
    assert {WORD_FOR_COUNT[count] for count in WORD_FOR_COUNT} == set(SPELLED_NUMBERS)
    assert all(SPELLED_NUMBERS[word] == count for count, word in WORD_FOR_COUNT.items())
    assert set(WORD_FOR_COUNT) == set(range(1, max(WORD_FOR_COUNT) + 1)), (
        f"the counts these tables cover have a hole in them: {sorted(WORD_FOR_COUNT)}. "
        f"A pin whose figure lands in the hole raises a `KeyError` from inside an "
        f"assertion message, reporting itself as broken rather than the record as wrong"
    )
