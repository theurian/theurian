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

**The budget is charged for the string this returns, separators included.**
Charging each sentence its own isolated cost and joining afterwards under-
charges: ``estimate_tokens`` rounds up once per call, so the spaces between the
selected sentences arrive unpriced and the returned text can cost more than the
caller allowed -- four four-character sentences at a budget of four came back
costing five. Every sentence after the first is therefore charged
``estimate_tokens(_SEPARATOR + text)``, and ``k`` sentences carry ``k - 1``
separators however they are ordered, so charging in score order and joining in
document order price the same string.

That charge is an upper bound on what appending a sentence adds rather than the
joined string's exact cost. ``estimate_tokens`` sums two ceilings and
``ceil(a) + ceil(b) >= ceil(a + b)``, so the parts' charges never total less
than the whole's cost -- which is the direction that matters, since exceeding a
budget silently drops the end of the caller's context (FR-R4) while under-
filling it costs under two tokens per selected sentence, one per ceiling
(measured at no more than one over random Latin and CJK corpora, and 97-99% of
the budget spent on this repository's own prose). The two exact alternatives
are worse: re-measuring the prospective join per candidate is quadratic, and
mirroring ``estimate_tokens``'s arithmetic over running dense/sparse character
counts puts a second copy of the budget rule in this module. Both are also
*rejected by the suite* --
``test_a_restrictive_budget_selects_the_mixed_childs_first_sentence_whole``
sweeps a budget that the exact joined cost of two sentences fills to the token,
and requires the second one left out.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Final, final

from theurian.domain.errors import InvariantViolationError
from theurian.domain.ranking import CHARS_PER_TOKEN, RankingError, estimate_tokens
from theurian.domain.values import ContentHash, Scope

#: Namespaced the way the sibling default is (``HashingEmbedding``'s
#: ``theurian-hashed-char-ngram``), and for the same reason: this lands in
#: every summary node's ``summary_model`` column, where a bare "extractive"
#: could not be told apart from some later, differently-behaved extractive
#: implementation -- including one from another vendor.
MODEL_ID: Final = "theurian-extractive-sentences"

#: The compact identity of what this module *selects*, and the only input to
#: :attr:`ExtractiveSummarizer.prompt_hash`.
#:
#: ADR-0008 decision 5 makes a node stale the moment its stored
#: ``summary_prompt_hash`` stops matching the current provider's, so this
#: string is the mechanism that invalidates every existing summary node when
#: selection semantics change. **Editing the prose of**
#: :data:`ALGORITHM_DESCRIPTION` **does not move the hash; bumping the trailing
#: version here does** -- rewording a description is not a semantics change and
#: must not cost a whole forest a rebuild, while a change that would pick a
#: different sentence for the same inputs must.
#:
#: What counts as "a different sentence for the same inputs" reaches past this
#: module. Selection is charged in ``estimate_tokens``, so its charging model --
#: ``domain.ranking``'s characters-per-token constant, its dense-script rate,
#: and which code points it counts as dense -- decides what fits a budget just
#: as directly as the code below does, and nothing in it is hashed here.
#: Measured on the suite's own fixtures: raising characters-per-token from 4 to
#: 5 changes the output at 41 of 56 English budgets, and raising the dense rate
#: from 1.5 to 2.0 changes it at 101 of 116 Japanese ones, while ``prompt_hash``
#: does not move at all. **A change to any of the three is a bump here too.**
#: ``test_extractive_summarizer.py`` pins the two rates so that lands in front
#: of a reviewer; the dense ranges carry the same obligation with no test
#: behind it.
#:
#: ``/2`` removed trailing whitespace from the truncation fallback (see
#: :func:`_truncate`).
SEMANTICS_VERSION: Final = "extractive-sentence-selection/2"

#: Derived, never an independent literal: a revision that had to be bumped in
#: two places is a revision that will be bumped in one.
MODEL_REVISION: Final = SEMANTICS_VERSION.rpartition("/")[2]

#: Review-facing prose describing what :data:`SEMANTICS_VERSION` names.
#:
#: Not hashed -- see the note above -- so it is free to be as explicit as a
#: reviewer needs. It carries the version identifier inside it so that someone
#: reading only this paragraph can still see which semantics it claims to
#: describe, and so that a diff which changes the description without touching
#: the version is visibly incomplete.
ALGORITHM_DESCRIPTION: Final = (
    f"{SEMANTICS_VERSION}: split each child text on Latin `.!?` "
    "and the CJK ideographic full stop, exclamation and question mark "
    "terminators; score each sentence by the sum, over its "
    "lower-cased character trigrams, of that trigram's count across every "
    "sentence split from this call's `texts`; visiting sentences in "
    "descending score order (ties broken by document position), add a "
    "sentence to the selection whenever the cost of appending it still fits "
    "the remaining `max_tokens` -- `estimate_tokens` of the sentence alone for "
    "the first one kept, and of the single space followed by the sentence, "
    "priced as one string rather than as a separately-rounded separator, for "
    "every later one, which is an upper bound on what the join costs -- "
    "skipping -- not stopping at -- one that does not; join the selected "
    "sentences with a single space in document order; if no single sentence's "
    "cost fits `max_tokens`, emit the longest character prefix of the first "
    "sentence (by document position) whose cost fits, with trailing whitespace "
    "removed, which is the empty string when not even that sentence's first "
    "character fits."
)

#: The largest ``texts`` this will scan, in total characters.
#:
#: Every stage below is linear in that count, so without a recorded limit the
#: only bound on the work one call may cost is what the caller passes. Measured
#: at exactly this cap: 1.45 s of CPU and 5.6 MB of peak heap over Latin prose,
#: 1.10 s and 16.3 MB over Japanese -- denser text builds a larger table of
#: distinct trigrams -- and 0.91 s and 0.05 MB for a single unsplittable
#: sentence, which never reaches scoring at all. Call it a second and a half of
#: CPU and sixteen megabytes, once, for input a thousand times larger than the
#: builder produces.
#:
#: A thousand times ``domain.chunking``'s 1000-character chunk target: a
#: cluster of a thousand chunks is already orders of magnitude past anything a
#: RAPTOR clustering produces, so what the cap refuses is a defect upstream
#: rather than a large but legitimate document.
MAX_TOTAL_INPUT_CHARS: Final = 1_000_000

#: Matches the project's own sentence boundary: a CJK ideographic terminator
#: needs no trailing space to end a sentence, so the CJK alternative is a
#: zero-width lookbehind, while the Latin alternative requires the whitespace
#: a period alone does not guarantee ends a sentence (an abbreviation, a
#: decimal). Kept as a private local pattern rather than importing
#: ``domain.chunking``'s: that module's split points feed a *length* budget,
#: this one's feed a *token* one, and the two must be free to diverge without
#: either import breaking the other.
_SENTENCE_TERMINATOR: Final = re.compile(r"(?<=[\u3002\uff01\uff1f])|(?<=[.!?])\s+")

#: What selected sentences are joined by, and what every sentence after the
#: first is charged for on top of its own cost.
_SEPARATOR: Final = " "

_TRIGRAM_SIZE: Final = 3
_WHITESPACE: Final = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class _Sentence:
    """One split sentence, its position in ``texts``, and its token cost.

    ``ordinal`` is the sole order key: it is assigned once, while ``texts`` is
    walked strictly left to right, so sorting on it recovers document order
    regardless of what order scoring or selection visited sentences in.

    ``cost`` is what this sentence costs *alone*, which is what it is charged
    when it is the first one selected. Every later selection is charged
    :func:`_appended_cost` instead, because it also pays for the separator.
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
    prompt_hash = ContentHash.of_text(SEMANTICS_VERSION).value

    async def summarize(
        self,
        texts: tuple[str, ...],
        *,
        scope: Scope,  # noqa: ARG002 -- port shape; the extractive default makes no hosted call to gate
        max_tokens: int,
    ) -> str:
        """Extractively summarize ``texts`` within ``max_tokens``.

        ``max_tokens`` carries the same contract as
        :func:`~theurian.domain.ranking.take_within_budget`, the system's only
        other budget rule, and is refused the same way and with the same error
        for the same reason: ``estimate_tokens`` prices even the empty string
        at one token, so below one token there is nothing a summary could be
        that would not already break the budget it was handed.

        Never empty for input carrying any non-whitespace content, as long as
        the budget covers the first character of its first sentence: a budget
        too small for even the cheapest whole sentence returns a truncated
        prefix rather than silence, because a caller reading a RAPTOR node
        cannot distinguish "summarized to nothing" from "not summarized" any
        other way. The single exception is ``max_tokens`` of exactly 1 where
        the *first sentence's first character* is dense: ``estimate_tokens``
        prices one such character at two, so the empty string is the only one
        that fits. A dense character anywhere else, or any budget of 2 or more,
        is not that case.

        Raises:
            RankingError: ``max_tokens`` is below one token.
            InvariantViolationError: ``texts`` totals more than
                :data:`MAX_TOTAL_INPUT_CHARS` characters.
        """
        if max_tokens < 1:
            msg = (
                f"max_tokens must be at least 1, got {max_tokens} -- a summary "
                "cannot cost less than the empty string, which `estimate_tokens` "
                "prices at one token. Pass the node's configured summary budget "
                "rather than a corpus-derived quantity (ADR-0008 decision 6)."
            )
            raise RankingError(msg)

        total_chars = sum(len(text) for text in texts)
        if total_chars > MAX_TOTAL_INPUT_CHARS:
            msg = (
                f"Refusing to summarize {total_chars} characters -- more than "
                f"the recorded {MAX_TOTAL_INPUT_CHARS}-character limit this "
                "module's cost is bounded by. Cluster fewer children per "
                "summary node: at a 1000-character chunk target, a cluster this "
                "large is a clustering defect rather than a large document."
            )
            raise InvariantViolationError(msg)

        sentences = _split_sentences(texts)
        if not sentences:
            return ""

        cheapest = min(sentence.cost for sentence in sentences)
        if cheapest > max_tokens:
            return _truncate(sentences[0].text, max_tokens)

        selected = _select(sentences, max_tokens)
        return _SEPARATOR.join(sentence.text for sentence in selected)


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

    Two passes, re-deriving each sentence's grams in the second rather than
    keeping every sentence's grams from the first: peak heap is then one
    sentence's grams plus the frequency table, which is bounded by the number
    of *distinct* trigrams, instead of one three-character string per character
    of the whole input, alive at once. Measured at
    :data:`MAX_TOTAL_INPUT_CHARS`: 53.9 MB down to 5.6 MB over Latin prose and
    78.0 MB down to 16.3 MB over Japanese, for 7% more CPU *on the whole call*
    -- inside this function alone the second pass costs 41% to 51% more,
    depending on the corpus, which the whole-call figure hides. Re-deriving is
    still the cheap half; holding was the expensive one.
    """
    frequency: Counter[str] = Counter()
    for sentence in sentences:
        frequency.update(_trigrams(sentence.text))
    return tuple(
        sum(frequency[gram] for gram in _trigrams(sentence.text)) for sentence in sentences
    )


def _appended_cost(text: str) -> int:
    """What appending ``text`` to a non-empty selection costs, separator included.

    Measured on ``_SEPARATOR + text`` rather than added to ``text``'s own cost,
    because ``estimate_tokens`` rounds up and a separator often lands inside a
    rounding that has already been paid for.
    """
    return estimate_tokens(_SEPARATOR + text)


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

    What each candidate is charged is what adding it to the *joined output*
    costs, which is why the first one kept is charged differently from the
    rest: the join puts ``k - 1`` separators between ``k`` sentences whatever
    order they end up in, so exactly one selection escapes paying for one.
    """
    ranked = sorted(
        zip(sentences, _score(sentences), strict=True),
        key=lambda pair: (-pair[1], pair[0].ordinal),
    )

    selected: list[_Sentence] = []
    used = 0
    for sentence, _score_value in ranked:
        charge = _appended_cost(sentence.text) if selected else sentence.cost
        if used + charge <= max_tokens:
            selected.append(sentence)
            used += charge

    return tuple(sorted(selected, key=lambda sentence: sentence.ordinal))


def _truncate(text: str, max_tokens: int) -> str:
    """The longest prefix of ``text`` whose cost fits ``max_tokens``, right-stripped.

    The cut lands wherever the budget runs out, which is as often mid-space as
    mid-word, and a node persisted with a trailing space carries a character
    that renders as nothing, breaks equality against the same prefix produced
    any other way, and was paid for out of the caller's budget. Stripping is
    the whole of ``/2`` in :data:`SEMANTICS_VERSION`, and it can only lower the
    cost, so the result still fits.

    ``estimate_tokens`` is non-decreasing in text length -- dropping trailing
    characters can only lower or hold its cost, never raise it -- so the
    longest fitting prefix is well defined and a binary search finds it in
    ``O(log n)`` calls, each of which re-scans its own prefix, for ``O(n log
    n)`` character examinations in the length searched.

    That length is bounded by the *budget*, not by the input: no character
    costs less than ``1 / CHARS_PER_TOKEN`` of a token, so nothing longer than
    ``max_tokens * CHARS_PER_TOKEN`` characters can fit whatever it contains,
    and searching from there keeps the fallback's cost independent of
    :data:`MAX_TOTAL_INPUT_CHARS`.

    Cuts at code points, not grapheme clusters, so a combining mark or an
    emoji ZWJ sequence can be split and the tail rendered differently from the
    characters it was cut from. Deliberate: grapheme segmentation needs a
    dependency ADR-0009 declines to take for a fallback path, and the result is
    a verbatim prefix of the child text either way.

    Returns the empty string when not even ``text``'s first character fits --
    reachable only when ``max_tokens`` is 1 *and* that character is dense,
    since ``estimate_tokens`` prices one dense character at two and every other
    at one. From ``summarize`` that means the first character of the first
    sentence, the only text this is ever called with. Emitting it regardless
    would make this the one place in the module that knowingly returns a string
    costing more than the caller allowed (FR-R4).
    """
    if not text:
        return text
    if estimate_tokens(text[:1]) > max_tokens:
        return ""

    lo, hi = 1, min(len(text), max_tokens * CHARS_PER_TOKEN)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if estimate_tokens(text[:mid]) <= max_tokens:
            lo = mid
        else:
            hi = mid - 1
    prefix = text[:lo]
    # Unreachable from `summarize`, whose sentences are stripped at the split,
    # so a fitting prefix always opens on a non-whitespace character. Kept
    # because that is the caller's invariant and not this function's: a prefix
    # of an all-whitespace text is worth less than nothing, but returning
    # nothing at all is the one outcome the fallback exists to avoid.
    return prefix.rstrip() or prefix
