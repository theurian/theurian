"""Where a record's sentence ends, defined once for the pins that read records by sentence (#832).

``test_ports.py`` and ``test_adr_0038_gate_read_records.py`` each locate a
record's sentence, and each had its own boundary; a sentence one of them ended
where the other did not is how a rule reads a different sentence than the one
the prose means.

**The key.** A sentence ends at a full stop that is followed by whitespace or the
end of the text, and the stop may carry one closer: ``.**`` (a bold sentence),
``.)`` (a parenthetical) or ``.*`` (an italic one). Text is read after Markdown
quote markers (``>``) and comment markers (``#:``, ``#``) are stripped from the
start of each line and whitespace is collapsed.

**Its known-weak half.** An abbreviation's full stop ends a sentence (``e.g. ``,
``i.e. ``), a full stop inside a code span followed by a space does too, and
``?``, ``!`` and ``:`` never end one. Every record held through this key is
prose that spells none of those today; a record that starts to is read as two
sentences, and a rule keyed on the first of them stops seeing the second.
"""

from __future__ import annotations

import re
from typing import Final, NoReturn

import pytest

SENTENCE_END: Final = re.compile(r"\.(?:\*\*|\*|\))?(?=\s|$)")
_MARKERS: Final = re.compile(r"(?m)^\s*(?:>|#:|#)\s?")


def not_found(what: str) -> NoReturn:
    """Every locator fails through here, so a missing record never reads as a false claim."""
    pytest.fail(f"record not found: {what}", pytrace=False)


def flat(text: str) -> str:
    return re.sub(r"\s+", " ", _MARKERS.sub("", text)).strip()


def sentences(text: str) -> list[str]:
    """*text* as its sentences, each with its closing stop."""
    flattened = flat(text)
    found: list[str] = []
    start = 0
    for stop in SENTENCE_END.finditer(flattened):
        found.append(flattened[start : stop.end()].strip())
        start = stop.end()
    if flattened[start:].strip():
        found.append(flattened[start:].strip())
    return found


def sentence_with(text: str, pattern: str) -> str:
    """The first sentence of *text* whose text matches *pattern*."""
    for sentence in sentences(text):
        if re.search(pattern, sentence):
            return sentence
    not_found(f"no sentence carries {pattern!r}")


def from_anchor(text: str, anchor: str, *, span: int = 1) -> str:
    """*text* from the one occurrence of *anchor* to the end of its *span*-th sentence."""
    flattened = flat(text)
    if flattened.count(anchor) != 1:
        not_found(f"{flattened.count(anchor)} occurrences of the anchor {anchor!r}")
    start = flattened.index(anchor)
    end = len(flattened)
    position = start
    for _ in range(span):
        stop = SENTENCE_END.search(flattened, position)
        if stop is None:
            break
        end, position = stop.end(), stop.end()
    return flattened[start:end]


def sentence_containing(text: str, anchor: str) -> str:
    """The one sentence of *text* holding *anchor*, whole."""
    found = [sentence for sentence in sentences(text) if anchor in sentence]
    if len(found) != 1:
        not_found(f"{len(found)} sentences carry the anchor {anchor!r}")
    return found[0]


def clauses(sentence: str) -> list[str]:
    """A sentence cut at ``;``, ``--``, the em dash and ``, and ``.

    ``, and `` is a cut because a sentence that gives two call sites their reads
    joins them there. A clause that gives its second read after a comma alone is
    one clause, which is the key's weak half.
    """
    return re.split(r";|--|—|, and ", sentence)
