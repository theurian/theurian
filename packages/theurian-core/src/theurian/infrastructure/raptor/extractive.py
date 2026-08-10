"""The default ``SummarizationProvider``: extractive, deterministic, offline
(ADR-0008 decisions 5, 6, 7).

Extractive rather than generative: every sentence this emits is a verbatim
substring of the children it was built from, which is what lets
``docs/architecture/raptor.md`` say the default "cannot state a fact the
children do not contain" as a structural property rather than a hope about
prompting. Deterministic and dependency-free, so a forest can be built with no
LLM configured (OSS-15, ADR-0009) -- the sentence-level splitting, scoring, and
selection below are the whole algorithm, and nothing here calls out.

Selection: split each child on the same Latin (``.!?``) and CJK ideographic
full stop, exclamation and question mark terminators ``domain.chunking``
splits on, score each sentence by the sum -- over its lower-cased character
trigrams -- of that trigram's count across every sentence this call was given,
then greedily add sentences in
descending score order (ties broken by document position), skipping any
sentence that would not fit the *remaining* budget rather than stopping at the
first one that does not. The last step matters: a strict prefix cut over a
score-sorted list can leave a cheap, well-scored sentence unselected merely
because a pricier one was tried first and exhausted the budget. Selected
sentences are re-ordered to document position before being joined, which is
what keeps the emitted text readable regardless of score order -- a caller
reads a summary top to bottom, not by salience.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Final, final

from theurian.domain.ranking import estimate_tokens
from theurian.domain.values import ContentHash, Scope

MODEL_ID: Final = "extractive"

#: Bumped whenever this module's own code changes in a way that changes what
#: it selects. ``prompt_hash`` is derived from :data:`ALGORITHM_DESCRIPTION`,
#: not from this alone, but the two only stay honest together: a change to
#: selection semantics that forgot to touch either constant would leave
#: every stored node's staleness check silently unable to see it.
MODEL_REVISION: Final = "1"

#: The versioned prose :attr:`ExtractiveSummarizer.prompt_hash` hashes.
#:
#: Not documentation on the side: ADR-0008 decision 5 makes a node stale the
#: moment its stored ``summary_prompt_hash`` stops matching the current
#: provider's, so this string *is* the mechanism that invalidates every
#: existing summary node when selection semantics change. Editing the
#: algorithm below without bumping the trailing version here leaves stale
#: nodes unrebuilt and undetected -- change the number whenever a change here
#: would pick a different sentence for the same inputs.
ALGORITHM_DESCRIPTION: Final = (
    "extractive-sentence-selection/1: split each child text on Latin `.!?` "
    "and the CJK ideographic full stop, exclamation and question mark "
    "terminators; score each sentence by the sum, over its "
    "lower-cased character trigrams, of that trigram's count across every "
    "sentence split from this call's `texts`; visiting sentences in "
    "descending score order (ties broken by document position), add a "
    "sentence to the selection whenever its cost still fits the remaining "
    "`max_tokens`, skipping -- not stopping at -- one that does not; join "
    "the selected sentences with a single space in document order; if no "
    "single sentence's cost fits `max_tokens`, emit the longest character "
    "prefix of the first sentence (by document position) whose cost fits."
)

#: Matches the project's own sentence boundary: a CJK ideographic terminator
#: needs no trailing space to end a sentence, so the CJK alternative is a
#: zero-width lookbehind, while the Latin alternative requires the whitespace
#: a period alone does not guarantee ends a sentence (an abbreviation, a
#: decimal). Kept as a private local pattern rather than importing
#: ``domain.chunking``'s: that module's split points feed a *length* budget,
#: this one's feed a *token* one, and the two must be free to diverge without
#: either import breaking the other.
_SENTENCE_TERMINATOR: Final = re.compile(r"(?<=[\u3002\uff01\uff1f])|(?<=[.!?])\s+")

_TRIGRAM_SIZE: Final = 3
_WHITESPACE: Final = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class _Sentence:
    """One split sentence, its position in ``texts``, and its token cost.

    ``ordinal`` is the sole order key: it is assigned once, while ``texts`` is
    walked strictly left to right, so sorting on it recovers document order
    regardless of what order scoring or selection visited sentences in.
    """

    ordinal: int
    text: str
    cost: int


@final
class ExtractiveSummarizer:
    """Selects sentences rather than generating them; never leaves this process.

    ADR-0008 decision 6's Milestone 6 amendment: **"a summariser is a pure
    function of its own children's texts, its scope tuple, and a
    configuration-derived ``max_tokens``. No corpus-wide statistic may enter
    ... and ``max_tokens`` must never be a corpus-derived quantity."** This
    class holds to that by construction rather than by discipline: every
    quantity ``summarize`` computes -- the sentence split, the trigram
    frequencies, the selection -- is derived solely from the ``texts`` and
    ``max_tokens`` of the one call in progress. Nothing is cached on
    ``self`` between calls, no corpus handle is acquired in ``__init__``, and
    ``scope`` is accepted only because the port's shape requires it of every
    implementation, not because this one reads it.
    """

    model_id = MODEL_ID
    model_revision = MODEL_REVISION
    prompt_hash = ContentHash.of_text(ALGORITHM_DESCRIPTION).value

    async def summarize(
        self,
        texts: tuple[str, ...],
        *,
        scope: Scope,  # noqa: ARG002 -- port shape; the extractive default makes no hosted call to gate
        max_tokens: int,
    ) -> str:
        """Extractively summarize ``texts`` within ``max_tokens``.

        Never empty for non-empty input: a budget too small for even the
        cheapest whole sentence still returns a truncated prefix of the first
        one rather than silence, because a caller reading a RAPTOR node
        cannot distinguish "summarized to nothing" from "not summarized" any
        other way.
        """
        sentences = _split_sentences(texts)
        if not sentences:
            return ""

        cheapest = min(sentence.cost for sentence in sentences)
        if cheapest > max_tokens:
            return _truncate(sentences[0].text, max_tokens)

        selected = _select(sentences, max_tokens)
        return " ".join(sentence.text for sentence in selected)


def _split_sentences(texts: tuple[str, ...]) -> tuple[_Sentence, ...]:
    """Split every text into sentences, numbered in document order.

    A piece that strips to empty (a trailing zero-width CJK split producing
    nothing after the last terminator, for instance) is dropped rather than
    numbered -- an empty "sentence" would cost a token slot and select
    nothing.
    """
    sentences: list[_Sentence] = []
    ordinal = 0
    for text in texts:
        for piece in _SENTENCE_TERMINATOR.split(text):
            candidate = piece.strip()
            if not candidate:
                continue
            sentences.append(
                _Sentence(ordinal=ordinal, text=candidate, cost=estimate_tokens(candidate))
            )
            ordinal += 1
    return tuple(sentences)


def _trigrams(text: str) -> tuple[str, ...]:
    """Overlapping lower-cased character trigrams, whitespace collapsed first.

    Character-level rather than word-level so the same scorer handles Latin
    and CJK text without a script-specific branch: a five-fold repeated CJK
    term produces five-fold repeated trigrams exactly as a repeated Latin word
    does, with no separate tokenizer to keep in step with the sentence
    splitter above. A sentence shorter than the trigram size still yields one
    "gram" -- itself -- so it is not scored as if it contained nothing.
    """
    normalized = _WHITESPACE.sub(" ", text.strip().lower())
    if len(normalized) < _TRIGRAM_SIZE:
        return (normalized,) if normalized else ()
    span = len(normalized) - _TRIGRAM_SIZE + 1
    return tuple(normalized[index : index + _TRIGRAM_SIZE] for index in range(span))


def _score(sentences: tuple[_Sentence, ...]) -> tuple[int, ...]:
    """Score each sentence by its trigrams' combined frequency across the call.

    Local to this one call's sentences -- the frequency table is built and
    discarded within this function, never a corpus-wide statistic. A sentence
    that repeats a trigram scores that repetition again for each occurrence,
    which is deliberate: a term the source itself emphasises by repeating it
    is exactly what an extractive summary should be more likely to keep.
    """
    frequency: Counter[str] = Counter()
    grams_per_sentence: list[tuple[str, ...]] = []
    for sentence in sentences:
        grams = _trigrams(sentence.text)
        grams_per_sentence.append(grams)
        frequency.update(grams)
    return tuple(sum(frequency[gram] for gram in grams) for grams in grams_per_sentence)


def _select(sentences: tuple[_Sentence, ...], max_tokens: int) -> tuple[_Sentence, ...]:
    """Choose the highest-scoring sentences that fit ``max_tokens``, in order.

    Visits candidates in descending score, ties broken by ``ordinal`` -- a
    total key, not an accident of how ``sorted`` happens to treat equal keys.
    Each candidate is added whenever the *remaining* budget covers it and
    skipped otherwise, rather than the selection stopping at the first
    candidate that does not fit: a stop-at-first-miss rule can leave a
    cheaper, still-well-scored sentence unselected purely because a costlier
    one was tried before it exhausted the budget. The result is re-sorted to
    document order before being returned, so the join in ``summarize`` never
    has to reason about score order again.
    """
    scored = sorted(
        zip(sentences, _score(sentences), strict=True),
        key=lambda pair: (-pair[1], pair[0].ordinal),
    )

    selected: list[_Sentence] = []
    used = 0
    for sentence, _score_value in scored:
        if used + sentence.cost <= max_tokens:
            selected.append(sentence)
            used += sentence.cost

    return tuple(sorted(selected, key=lambda sentence: sentence.ordinal))


def _truncate(text: str, max_tokens: int) -> str:
    """The longest prefix of ``text`` whose cost fits ``max_tokens``.

    ``estimate_tokens`` is non-decreasing in text length -- dropping trailing
    characters can only lower or hold its cost, never raise it -- so the
    longest fitting prefix is well defined and a binary search finds it in
    ``O(log n)`` calls. Never returns empty for non-empty ``text``: even a
    one-character prefix whose own cost exceeds ``max_tokens`` is returned
    rather than nothing, because a caller cannot act on silence at all,
    while it can at least see a truncated fragment name what it was cut from.
    """
    if not text:
        return text
    if estimate_tokens(text[:1]) > max_tokens:
        return text[:1]

    lo, hi = 1, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if estimate_tokens(text[:mid]) <= max_tokens:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo]
