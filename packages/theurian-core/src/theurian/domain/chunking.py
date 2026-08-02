"""Splitting a document into retrievable passages (FR-R2).

A pure function of text. Retrieval returns *chunks*, not whole documents,
because an architecture decision record is often ten pages of which one
paragraph answers the question — and returning the other nine spends a caller's
context budget on material they did not ask for.

Splitting is on structure first and length second. A heading boundary is a real
semantic boundary that the author chose; a character count is an arbitrary one
that happens to be cheap. Falling back to the arbitrary boundary only when the
real one leaves a passage too large keeps most chunks aligned with the document's
own shape.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from theurian.domain.errors import TheurianError

#: Target passage size in characters. Roughly 250 tokens at four characters per
#: token — large enough to carry an argument, small enough that several fit in a
#: caller's budget alongside their own prompt.
TARGET_CHARS: Final = 1000

#: A passage below this is folded into its neighbour. A two-line chunk retrieves
#: badly (too little signal) and reads badly (no context), so a heading with one
#: sentence under it belongs with what follows.
MIN_CHARS: Final = 120

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)
_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
#: Sentence end followed by whitespace. The last resort before a length cut.
# CJK sentence marks are deliberate, and so is the missing whitespace after
# them: Japanese does not put a space after a full stop, so a pattern that
# required one would return an entire Japanese document as a single chunk.
_SENTENCE_END = re.compile(r"(?<=[\u3002\uff01\uff1f])|(?<=[.!?])\s+")


class ChunkingError(TheurianError):
    """A document could not be split."""


@dataclass(frozen=True, slots=True)
class Chunk:
    """One retrievable passage of a revision."""

    #: Stable within a revision: ``<revision_id>#<ordinal>``. Deterministic so
    #: that rebuilding an index over unchanged content produces the same ids and
    #: a pinned result still resolves (FR-R7).
    chunk_id: str
    ordinal: int
    text: str
    #: The nearest enclosing heading, or empty. Carried so a hit can say *where*
    #: in a document it came from without the caller fetching the whole thing.
    heading: str = ""

    @property
    def char_count(self) -> int:
        return len(self.text)


def chunk_document(
    revision_id: str, body: str, *, target_chars: int = TARGET_CHARS
) -> tuple[Chunk, ...]:
    """Split ``body`` into passages.

    Deterministic: the same text always yields the same chunks with the same
    ids, which is what lets an index rebuild leave pinned results resolvable.

    Returns:
        Chunks in document order. A document with no body yields none — an empty
        chunk would match nothing and cost a row.
    """
    if target_chars < MIN_CHARS:
        msg = f"target_chars must be at least {MIN_CHARS}, got {target_chars}"
        raise ChunkingError(msg)

    if not body.strip():
        return ()

    passages: list[tuple[str, str]] = []
    for heading, section in _sections(body):
        for piece in _split_to_length(section, target_chars):
            passages.append((heading, piece))

    merged = _merge_runts(passages, target_chars)
    return tuple(
        Chunk(chunk_id=f"{revision_id}#{ordinal}", ordinal=ordinal, text=text, heading=heading)
        for ordinal, (heading, text) in enumerate(merged)
    )


def _sections(body: str) -> list[tuple[str, str]]:
    """Split on Markdown headings, keeping each heading with its content.

    The heading is a boundary the author chose, which makes it a better split
    point than any length we could pick.
    """
    matches = list(_HEADING.finditer(body))
    if not matches:
        return [("", body.strip())]

    sections: list[tuple[str, str]] = []
    preamble = body[: matches[0].start()].strip()
    if preamble:
        sections.append(("", preamble))

    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        title = match.group(2).strip()
        content = body[match.start() : end].strip()
        if content:
            sections.append((title, content))
    return sections


def _split_to_length(section: str, target: int) -> list[str]:
    """Break an over-long section, on the best boundary still available.

    Paragraphs first, then sentences. Never mid-sentence: a passage cut in half
    retrieves on terms it no longer explains, which is worse than a passage
    slightly over budget.

    The sentence fallback matters more than it looks. A wall of text with no
    blank lines -- a pasted transcript, a generated summary, a wrapped log --
    would otherwise come back as one enormous chunk that spends a caller's whole
    budget on a single hit.
    """
    if len(section) <= target:
        return [section] if section.strip() else []

    pieces = _accumulate(_PARAGRAPH_BREAK.split(section), target, "\n\n")
    pieces = _refine(pieces, target, _SENTENCE_END.split, " ")
    pieces = _refine(pieces, target, str.split, " ")
    # A hard character cut is the last resort, and the only one that always
    # terminates. Unbroken CJK prose has no spaces, and a base64 blob has
    # neither spaces nor sentences; returning either as one unbounded chunk
    # would spend a caller's whole budget on a single hit they cannot use.
    return _refine(pieces, target, lambda text: _by_length(text, target), "")


def _refine(
    pieces: list[str], target: int, split: Callable[[str], list[str]], joiner: str
) -> list[str]:
    """Re-split any piece still over ``target`` on a finer boundary.

    Pieces already within budget pass through untouched, so a document that
    yielded cleanly to paragraphs is never re-cut on sentences or words.
    """
    refined: list[str] = []
    for piece in pieces:
        if len(piece) <= target:
            refined.append(piece)
            continue
        refined.extend(_accumulate(split(piece), target, joiner))
    return refined


def _by_length(text: str, target: int) -> list[str]:
    """Cut on character count. Loses no content, respects no boundary."""
    return [text[start : start + target] for start in range(0, len(text), target)]


def _accumulate(parts: list[str], target: int, joiner: str) -> list[str]:
    """Greedily group ``parts`` into pieces of at most ``target`` characters.

    A part longer than the target on its own is emitted whole rather than cut:
    at that point the only boundary left is an arbitrary one, and an over-long
    passage is a smaller problem than a truncated sentence.
    """
    pieces: list[str] = []
    current = ""
    for part in parts:
        stripped = part.strip()
        if not stripped:
            continue
        candidate = f"{current}{joiner}{stripped}" if current else stripped
        if current and len(candidate) > target:
            pieces.append(current)
            current = stripped
        else:
            current = candidate
    if current:
        pieces.append(current)
    return pieces


def _merge_runts(passages: list[tuple[str, str]], target: int) -> list[tuple[str, str]]:
    """Fold passages below :data:`MIN_CHARS` into the following one.

    A heading with a single sentence under it is not a retrievable unit: too
    little signal to rank on, too little context to read. It is folded *forward*
    because such a passage almost always introduces what comes next -- "## Rules"
    followed by one line belongs with the rules, not with the section before it.

    The last passage has nothing to merge forward into, so it folds backward
    instead. A lone runt -- a document that is one short paragraph -- is kept as
    it is: a short document is not a useless one.
    """
    merged: list[tuple[str, str]] = []
    pending: tuple[str, str] | None = None

    for heading, text in passages:
        if pending is not None:
            # Merged forward even when the result exceeds the target. The point
            # of the target is to keep chunks retrievable, and a stranded
            # fifteen-character chunk is not retrievable at any size -- so a
            # slightly over-long neighbour is the cheaper outcome.
            pending_heading, pending_text = pending
            combined = f"{pending_text}\n\n{text}"
            # A heading section split on its own paragraph break can leave a
            # bare "## Rules" line, so absorbing one runt may just produce
            # another. Keep going until there is enough to retrieve on.
            pending = (pending_heading, combined) if len(combined) < MIN_CHARS else None
            if pending is None:
                merged.append((pending_heading, combined))
            continue

        if len(text) < MIN_CHARS:
            pending = (heading, text)
            continue
        merged.append((heading, text))

    if pending is not None:
        # Nothing followed it. Fold backward if there is room, else keep it: a
        # document that is one short paragraph is not a useless document.
        heading, text = pending
        if merged and len(merged[-1][1]) + len(text) <= target:
            previous_heading, previous_text = merged[-1]
            merged[-1] = (previous_heading, f"{previous_text}\n\n{text}")
        else:
            merged.append(pending)

    return merged


@dataclass(frozen=True, slots=True)
class IndexableChunk:
    """A chunk together with the canonical facts retrieval filters on (FR-R1).

    Carried alongside rather than inside :class:`Chunk` because chunking is a
    property of text and these are properties of the revision it came from.

    The index denormalises them so filtering can happen in the same statement as
    the match, before ranking. Only ``status`` is filtered on today;
    ``sensitivity`` and ``trust_level`` are carried for the scope filtering
    Milestone 6 adds.
    """

    chunk: Chunk
    project_id: str
    item_id: str
    revision_id: str
    status: str
    sensitivity: str
    trust_level: str
    namespace: str = ""
