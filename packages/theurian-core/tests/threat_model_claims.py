"""Reading one entry out of the threat model, for every pin that holds one.

``docs/security/threat-model.md`` is a durable security record whose entries
state facts about today's code -- how many places may spawn a process, which
derived database families exist, which symbol gates a serve path. Each of those
sentences is worth exactly what it says only while something recomputes it, so
this file set holds them against the code, one module per entry.

**The slicing lives here because it is the part every such pin gets wrong the
same way.** An entry is a Markdown section with no closing delimiter: it runs
from its own heading to whichever heading comes next, at any level. A pin that
searched the whole document would read a neighbouring entry's prose as its own
-- the threat model quotes threat ids across entries constantly -- and a pin that
anchored on a bare ``#### T-7`` would also open on a ``T-7a`` heading. Both
mistakes are silent: they widen or misplace the text a pin scans, and every
assertion downstream then passes over the wrong bytes. One implementation,
called by each entry's module, is the same reasoning ``write_lock_claims``
records for the write-lock derivation -- a copy per module drifts in whichever
copy its author forgot.

**What is here is only what more than one entry needs**: the slice, the prose
normalisation, and the spelled-number tables every entry uses because the
document spells its counts as words. Anything specific to one entry -- which
bullet, which phrase keys it, which constant it is held against -- stays in that
entry's own module, where its reasoning can be read beside the assertion.

**One copy is still outside this module, and that is recorded rather than done
quietly.** ``tests/unit/test_threat_model_t16_claims.py`` carries its own
``_entry`` and its own ``_HEADING_MARKERS``, written before this module existed;
the two implementations agree today, and the constants are character-identical.
Rewiring it is mechanical apart from moving its own synthetic slicing test, and
it is left for whoever next touches that module rather than folded into an
unrelated change. Until then, "one derivation" describes T-7 and T-19 and is a
goal for T-16 -- which is the honest reading, and the shape this module exists to
stop drifting.

Pure: two files read as text, no database, socket or temporary directory.
"""

from __future__ import annotations

from typing import Final

from write_lock_claims import REPO_ROOT, collapsed

#: The document every module in this set reads.
THREAT_MODEL: Final = REPO_ROOT / "docs/security/threat-model.md"

#: Every heading level that ends an entry. An entry has no closing marker, so it
#: runs to whichever of these comes first -- including a deeper one, because a
#: sibling entry at the same level is the common case and a new ``###`` section
#: is the other.
_HEADING_MARKERS: Final = ("\n## ", "\n### ", "\n#### ")

#: The spelled numbers an entry could carry, mapped to what they mean. Spelled
#: rather than digits because that is how the threat model writes its counts, and
#: a pin reading digits would pass over the sentences it exists to hold. The range
#: brackets every live figure with room to move on both sides; a word outside it
#: fails loudly in the caller, since a sentence that started spelling its count
#: some other way has stopped being the sentence its pin reads.
SPELLED_NUMBERS: Final = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
}

#: The same mapping the other way round, so a RED can name the word an entry
#: should now carry rather than leaving an editor to work it out from a count.
WORD_FOR_COUNT: Final = {count: word for word, count in SPELLED_NUMBERS.items()}


def prose(text: str) -> str:
    """*text* normalised for a prose scan: no markup, no wraps, lower case.

    :func:`~write_lock_claims.collapsed` -- the shared primitive every claim pin
    uses -- lowercases and flattens the soft wraps. Backticks and asterisks go
    first, because the threat model writes its symbols and paths in code spans
    and bolds its counts, and a key written the way a sentence reads would miss
    both.
    """
    return collapsed(text.replace("`", "").replace("*", ""))


def entry_in(text: str, threat_id: str) -> str:
    """The one section of *text* headed *threat_id*, raw.

    Raw rather than normalised: a caller that splits the entry into bullets needs
    the line starts, and :func:`prose` destroys them. Normalise afterwards, per
    piece.

    The heading marker carries its ``\\n`` and its trailing space so the slice
    anchors on a line start and on a whole threat id: a bare ``#### T-7`` would
    also open on a ``T-7a`` heading, and an unanchored ``T-7`` matches the id
    inside another entry's prose. The leading ``\\n`` is a precondition as well as
    a guard -- a document whose *first* line is the heading has no break before it
    and fails the premise rather than being sliced. The shipped threat model opens
    with its title, so no entry is ever first; a caller handing in a fragment
    would meet it, and meets it loudly.

    Takes the document as an argument so the slicing can be driven by a synthetic
    one -- ``tests/unit/test_threat_model_claims.py`` does exactly that. A slicer
    that only ever ran against the shipped file would be checked by whichever pin
    happened to fail, and the failure mode that matters here is the one that never
    fails: a slice running past the next heading widens every scan above it
    silently.
    """
    heading = f"\n#### {threat_id} "

    assert text.count(heading) == 1, (
        f"the document has {text.count(heading)} lines starting "
        f"`{heading.strip()}`, expected 1; with none of them the pin over this "
        f"entry scans nothing, and with two it scans whichever came first"
    )

    rest = text.split(heading, 1)[1]
    ends = [found for marker in _HEADING_MARKERS if (found := rest.find(marker)) >= 0]
    return rest[: min(ends)] if ends else rest


def entry(threat_id: str) -> str:
    """The one section of the threat model headed *threat_id*, raw."""
    return entry_in(THREAT_MODEL.read_text(encoding="utf-8"), threat_id)
