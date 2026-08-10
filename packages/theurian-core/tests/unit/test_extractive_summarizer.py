"""The default extractive ``SummarizationProvider`` (ADR-0008 decisions 5, 6, 7).

RAPTOR's default summariser "selects sentences rather than generating them, so
it cannot state a fact the children do not contain; quality is lower than an
abstractive summary; groundedness is perfect" (``docs/architecture/raptor.md``).
That claim is tested here as an explicit, testable property: every sentence the
summariser emits must be a verbatim substring of some child text.

Decision 6's Milestone 6 amendment adds the purity constraint this file spends
most of its weight on: "a summariser is a pure function of its own children's
texts, its scope tuple, and a configuration-derived ``max_tokens``. No
corpus-wide statistic may enter ... and ``max_tokens`` must never be a
corpus-derived quantity." ADR-0008's Compliance section names the owed test
verbatim -- "Summarise the same children under the same scope in two corpora
that differ everywhere else, including in documents the caller may not read,
and the node text must be byte-identical" -- plus two negative controls, one
per carrier the purity test can see (carrier (a), the text inputs, and carrier
(c), ``max_tokens``; carrier (b), which children get clustered together, is
explicitly out of scope here by construction and is covered by the two-corpus
equality test decision 9 owes instead).

Decision 5's staleness rule -- "a summary whose model or prompt hash differs
from the current configuration is stale by definition and rebuilt" -- is what
``prompt_hash`` exists to serve, so it is pinned here against a hard-coded
sha256 literal rather than against its own derivation: a comparison to
``ContentHash.of_text(SEMANTICS_VERSION)`` moves whenever the constant moves
and can never fail (3c5bd6d). What the literal holds is one direction only --
bumping ``SEMANTICS_VERSION`` cannot land without a human re-pinning the hash
in the same diff. A selection change that forgets to bump the constant leaves
every stored node's staleness check blind, and nothing in this file goes red
for it; that omission is caught by a reviewer reading
``ALGORITHM_DESCRIPTION`` against the diff, or not at all.
"""

from __future__ import annotations

import inspect
import re
import subprocess
import sys

import pytest

from theurian.domain import chunking
from theurian.domain.enums import KnowledgeStatus, Sensitivity
from theurian.domain.errors import InvariantViolationError
from theurian.domain.identifiers import ProjectId
from theurian.domain.ports.summarization import SummarizationProvider
from theurian.domain.ranking import RankingError, estimate_tokens
from theurian.domain.values import AclGroup, ContentHash, Scope, TenantId
from theurian.infrastructure.raptor import extractive
from theurian.infrastructure.raptor.extractive import ExtractiveSummarizer

# -- Fixtures ------------------------------------------------------------

# Four short, individually addressable "sentences", spread across two
# children. _BETA and _DELTA repeat their own keyword several times so that a
# local term-frequency scorer -- an entirely legitimate choice under the
# purity constraint -- would rank them above _ALPHA and _GAMMA respectively.
# That asymmetry is deliberate: an implementation that selected by score and
# then emitted its selection *in score order* rather than document order would
# produce ``_DELTA ... _BETA ...`` here, which the order-preservation tests
# below would catch and a naive "pin the exact output" test would not.
_ALPHA = "S1 sentence about tokens appears first."
_BETA = "S2 sentence repeats tokens tokens tokens tokens tokens for salience."
_GAMMA = "S3 sentence about caching appears third."
_DELTA = "S4 sentence repeats caching caching caching caching caching for salience."

CHILD_ONE = f"{_ALPHA} {_BETA}"
CHILD_TWO = f"{_GAMMA} {_DELTA}"
_ENGLISH_SENTENCES = (_ALPHA, _BETA, _GAMMA, _DELTA)
_ENGLISH_CHILDREN = (CHILD_ONE, CHILD_TWO)

# A second, separate fixture whose first sentence dominates every other
# sentence's score, used only by
# test_a_latin_sentence_is_selected_whole_not_carved_from_its_unsplit_child.
# _ENGLISH_SENTENCES cannot serve double duty there: _BETA's own salience,
# which the order-preservation tests need, would let it legitimately
# outcompete _ALPHA once its cost alone fits the swept budget -- a correct
# scoring outcome, not evidence that splitting is broken. Here nothing can
# outcompete the first sentence anywhere in the swept range.
_SOLO_FIRST = "Rotating tokens tokens tokens tokens tokens reduces exposure quickly."
_SOLO_SECOND = "Revocation happens immediately for severe incidents."
_SOLO_THIRD = "Audit records capture the caller and the timestamp."
_SOLO_FOURTH = "Backups run nightly across every region."
_SOLO_CHILDREN = (f"{_SOLO_FIRST} {_SOLO_SECOND}", f"{_SOLO_THIRD} {_SOLO_FOURTH}")

# Japanese data, per the project's own convention that a query or corpus
# sample is correct as non-English *data* and must not be translated. No space
# follows a CJK full stop, matching how the codebase's own sentence splitter
# (``theurian.domain.chunking._SENTENCE_END``) treats CJK terminators.
_JA_ONE = "署名付きトークンを持つリクエストのみ許可される。"
_JA_TWO = "有効期限を過ぎたトークンは拒否される。"
_JA_THREE = "監査ログにはリクエスト元と時刻が記録される。"
_JA_FOUR = "ログは90日間保持される。"

CHILD_JA_ONE = f"{_JA_ONE}{_JA_TWO}"
CHILD_JA_TWO = f"{_JA_THREE}{_JA_FOUR}"
_JAPANESE_SENTENCES = (_JA_ONE, _JA_TWO, _JA_THREE, _JA_FOUR)
_JAPANESE_CHILDREN = (CHILD_JA_ONE, CHILD_JA_TWO)

_MIXED_CHILD = (
    "Rotating tokens reduces exposure. "
    "署名付きトークンを持つリクエストのみ許可される。"
    "Both concepts matter here."
)

# A fixture where trigram-frequency scoring and a plausible-looking substitute
# that scores by sentence length instead rank two sentences oppositely.
# _SHORT_REPETITIVE is barely half _LONG_UNIQUE's length but repeats its own
# keyword, so real cross-sentence trigram frequency ranks it above the longer,
# almost entirely unique sentence; a length-only scorer (more raw trigrams)
# ranks them the other way around. Real prose, not gibberish, per this
# project's convention that an adversarial fixture should look like content
# someone actually wrote.
_SHORT_REPETITIVE = "Cache cache cache cache invalidation matters."
_LONG_UNIQUE = (
    "The distributed system replicates configuration snapshots across every region nightly."
)

# Two sentences whose trigram-frequency scores are genuinely tied, not merely
# close: the same three characters in reversed order, so each contributes
# every trigram the other does, in equal count.
_TIE_FIRST = "ab cd."
_TIE_SECOND = "cd ab."

# Splits on the same terminator families the ADR spec names for the
# implementation: Latin .!? and CJK ideographic full stop / exclamation /
# question mark (escaped by codepoint, as domain.chunking does, to avoid an
# ambiguous-character lint on the fullwidth glyphs themselves). Defined
# independently of whatever regex the implementation uses internally, so this
# checks the *output's* shape rather than assuming the two share code.
_TERMINATOR_SPLIT = re.compile(r"(?<=[\u3002\uff01\uff1f])|(?<=[.!?])\s+")


def _split_sentences(text: str) -> list[str]:
    return [piece.strip() for piece in _TERMINATOR_SPLIT.split(text) if piece.strip()]


def _document_order_index(sentence: str, texts: tuple[str, ...]) -> tuple[int, int]:
    """A sortable ``(child_index, char_offset)`` position for ``sentence``.

    Used to check that whichever sentences a restrictive budget selects, they
    are emitted in the order they occur in ``texts`` -- not in score order,
    and not in some other order an implementation detail happened to produce.
    """
    for child_index, child in enumerate(texts):
        offset = child.find(sentence)
        if offset != -1:
            return (child_index, offset)
    raise AssertionError(f"{sentence!r} was not found verbatim in any of {texts!r}")


def _partial_selection_budget(sentences: tuple[str, ...]) -> int:
    """A budget that admits the two cheapest sentences but not all of them.

    Computed from the fixture rather than hard-coded, so a future edit to the
    sentence text does not silently stop exercising a partial selection.
    """
    costs = sorted(estimate_tokens(sentence) for sentence in sentences)
    return costs[0] + costs[1] + 1


def _ample_budget(sentences: tuple[str, ...]) -> int:
    """A budget comfortably larger than every sentence combined.

    The margin covers whatever separator the implementation joins selected
    sentences with, which is not itself pinned by this test file.
    """
    return sum(estimate_tokens(sentence) for sentence in sentences) + 20


def _scope(**overrides: object) -> Scope:
    """Mirrors ``test_raptor_scope.py``'s helper of the same name and shape."""
    base: dict[str, object] = {
        "project_id": ProjectId("backend-service"),
        "tenant_id": TenantId("local"),
        "sensitivity": Sensitivity.INTERNAL,
        "acl_group": AclGroup("default"),
        "namespace": "architecture",
        "status": KnowledgeStatus.APPROVED,
    }
    base.update(overrides)
    return Scope(**base)  # type: ignore[arg-type]


# -- Port conformance ------------------------------------------------------


def test_it_satisfies_the_summarization_provider_port() -> None:
    provider = ExtractiveSummarizer()

    assert isinstance(provider, SummarizationProvider)


def test_model_id_is_namespaced_and_names_the_algorithm() -> None:
    """Pinned to a literal, and re-pinned in round 2 from the bare
    ``"extractive"`` to the namespaced form the sibling default already uses
    (``HashingEmbedding``'s ``theurian-hashed-char-ngram``). ``model_id`` is
    persisted per node in ``nodes.summary_model``, so it has to survive being
    read years later next to some other vendor's extractive summariser: a
    caller reading ``"extractive"`` off a stored node could tell it apart from
    an abstractive model but not from a second extractive implementation.
    Changing this literal is a change to what that column means, which is why
    it is pinned rather than pattern-matched.
    """
    provider = ExtractiveSummarizer()

    assert provider.model_id == "theurian-extractive-sentences"


def test_model_revision_is_a_non_empty_string() -> None:
    provider = ExtractiveSummarizer()

    assert isinstance(provider.model_revision, str)
    assert provider.model_revision != ""


def test_prompt_hash_is_stable_across_independently_constructed_instances() -> None:
    """A node's staleness comparison (ADR-0008 decision 5) reads ``prompt_hash``
    off whatever instance the active configuration builds; it must not depend
    on which instance happened to compute it."""
    first = ExtractiveSummarizer()
    second = ExtractiveSummarizer()

    assert first.prompt_hash == second.prompt_hash


def test_prompt_hash_is_a_valid_content_hash() -> None:
    provider = ExtractiveSummarizer()

    # ContentHash's own constructor is the format guard (64 lowercase hex);
    # constructing it here is the assertion.
    ContentHash(provider.prompt_hash)


# -- Staleness key redesign (round 2: SEMANTICS_VERSION) --------------------
#
# ``prompt_hash`` moves from hashing all of ``ALGORITHM_DESCRIPTION``'s prose
# to hashing a compact ``SEMANTICS_VERSION`` identifier instead, with
# ``MODEL_REVISION`` derived from that same constant rather than kept as an
# independent literal an editor can bump in one place and forget in the
# other. The tests below were written first and went red against a module
# that had no ``SEMANTICS_VERSION`` attribute at all; the constant landed in
# the same change, so they are green here.


def test_semantics_version_is_the_compact_identifier_the_algorithm_description_already_names() -> (
    None
):
    """Pinned to the literal ``ALGORITHM_DESCRIPTION`` already opens with
    ("extractive-sentence-selection/1: split each child text ..."), not
    invented -- ``SEMANTICS_VERSION`` is that same leading identifier
    promoted to its own constant, not a new value to guess at.
    """
    assert extractive.SEMANTICS_VERSION == "extractive-sentence-selection/1"


def test_model_revision_is_derived_from_semantics_version_not_an_independent_literal() -> None:
    """One constant, two surfaces: ``MODEL_REVISION`` must move automatically
    when ``SEMANTICS_VERSION``'s trailing version does. The two halves are an
    absolute value and a structural relationship to ``SEMANTICS_VERSION``, and
    at version 1 they do **not** separate a derivation from a hard-coded
    literal: ``MODEL_REVISION = "1"`` passes both, because "1" is exactly what
    the derivation produces. What the second half buys is the first bump --
    against ``extractive-sentence-selection/2`` a literal "1" no longer ends
    the version string and this goes red, which is the moment the two could
    otherwise drift apart unnoticed.
    """
    assert extractive.MODEL_REVISION == "1"
    assert extractive.SEMANTICS_VERSION.endswith(f"/{extractive.MODEL_REVISION}")


def test_prompt_hash_is_pinned_to_the_literal_sha256_of_semantics_version() -> None:
    """Pinned to a hard-coded literal, not to ``ContentHash.of_text`` of
    ``extractive.SEMANTICS_VERSION`` compared against itself -- that form can
    never fail, because both sides of the comparison move together no matter
    what the hashing mechanism does or stops doing (this repository's own
    precedent against pinning a value by itself, 3c5bd6d). The literal below
    is ``sha256("extractive-sentence-selection/1")``, computed independently
    of this module. **A semantics change that bumps ``SEMANTICS_VERSION``'s
    trailing digit must re-pin this literal** -- that re-pin, made by a human
    reading the diff, is the staleness mechanism ADR-0008 decision 5 depends
    on to invalidate every existing summary node.
    """
    provider = ExtractiveSummarizer()

    assert (
        provider.prompt_hash == "d2825b717d2c04374a3d19d6b94680344b5e0646ea0a01e2454d31587a5eade3"
    )


def test_semantics_version_appears_in_the_algorithm_description() -> None:
    """``ALGORITHM_DESCRIPTION`` stays free-form review prose that a human
    reads to judge whether a diff changes selection semantics; ``prompt_hash``
    now hashes ``SEMANTICS_VERSION`` alone. Asserting the identifier appears
    inside the prose is what keeps the two travelling together -- a reviewer
    reading only the prose can still see which version it claims to describe.
    """
    assert extractive.SEMANTICS_VERSION in extractive.ALGORITHM_DESCRIPTION


# -- Async -------------------------------------------------------------


def test_summarize_is_declared_async() -> None:
    """The port declares ``summarize`` ``async def``. A synchronous method that
    happened to return an awaitable object would pass every ``await``-based
    test below while still breaking a caller that assumed a coroutine
    function, e.g. for introspection or wrapping."""
    provider = ExtractiveSummarizer()

    assert inspect.iscoroutinefunction(provider.summarize)


@pytest.mark.asyncio
async def test_summarize_is_awaitable_and_returns_a_string() -> None:
    provider = ExtractiveSummarizer()

    result = await provider.summarize(
        _ENGLISH_CHILDREN, scope=_scope(), max_tokens=_ample_budget(_ENGLISH_SENTENCES)
    )

    assert isinstance(result, str)


# -- Extractiveness / groundedness --------------------------------------


@pytest.mark.asyncio
async def test_every_sentence_of_the_output_is_a_verbatim_substring_of_a_child() -> None:
    """The testable form of "it cannot state a fact the children do not
    contain": nothing paraphrased, nothing synthesised -- every emitted
    sentence must appear character-for-character in one of the children.
    """
    provider = ExtractiveSummarizer()

    result = await provider.summarize(
        _ENGLISH_CHILDREN, scope=_scope(), max_tokens=_ample_budget(_ENGLISH_SENTENCES)
    )

    selected = _split_sentences(result)
    assert selected, "an ample budget over non-empty children produced nothing"
    for sentence in selected:
        assert any(sentence in child for child in _ENGLISH_CHILDREN), (
            f"{sentence!r} is not a verbatim substring of any child text"
        )


@pytest.mark.asyncio
async def test_extractiveness_holds_under_a_restrictive_budget_too() -> None:
    """Groundedness cannot depend on there being room for everything -- a
    truncated or partially-selected summary must still say only what is
    already in the children."""
    provider = ExtractiveSummarizer()
    max_tokens = _partial_selection_budget(_ENGLISH_SENTENCES)

    result = await provider.summarize(_ENGLISH_CHILDREN, scope=_scope(), max_tokens=max_tokens)

    assert result
    for sentence in _split_sentences(result) or [result]:
        assert any(sentence in child for child in _ENGLISH_CHILDREN), (
            f"{sentence!r} is not a verbatim substring of any child text"
        )


# -- Budget ---------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("max_tokens", [2, 11, 21, 56, 200])
async def test_output_never_exceeds_the_token_budget(max_tokens: int) -> None:
    """FR-R4's budget discipline applies here exactly as it does to search
    results: exceeding the caller's budget silently drops something they did
    not ask to lose."""
    provider = ExtractiveSummarizer()

    result = await provider.summarize(_ENGLISH_CHILDREN, scope=_scope(), max_tokens=max_tokens)

    assert estimate_tokens(result) <= max_tokens, (
        f"output cost {estimate_tokens(result)} exceeds the {max_tokens}-token budget: {result!r}"
    )


@pytest.mark.asyncio
async def test_a_generous_budget_includes_every_sentence_in_document_order() -> None:
    """The floor case: with room for everything, nothing should be left out --
    otherwise ``max_tokens`` would be silently doing something other than
    bounding cost, e.g. capping a fixed sentence count regardless of budget.
    """
    provider = ExtractiveSummarizer()

    result = await provider.summarize(
        _ENGLISH_CHILDREN, scope=_scope(), max_tokens=_ample_budget(_ENGLISH_SENTENCES)
    )

    assert _split_sentences(result) == list(_ENGLISH_SENTENCES)


@pytest.mark.asyncio
async def test_a_restrictive_budget_still_emits_selected_sentences_in_document_order() -> None:
    """Whichever sentences a restrictive budget admits, they must come out in
    the order they occur in the children -- not in whatever order a local
    scorer happened to rank them.

    A single hand-picked budget is not reliable bait for this: which budget
    happens to select two *differently scored* sentences together (the only
    situation where a score-order bug is visible instead of coincidentally
    matching document order) depends on the implementer's own scoring choice,
    which this file must not pin. Sweeping every budget from the cheapest
    single sentence up to just under the full cost is the implementation-
    agnostic substitute: the property is checked at each point, and at least
    one of them is required to have selected more than one sentence, or the
    sweep proves nothing. The fixture's _BETA/_DELTA repeat their own keyword
    so a highest-score-first implementation has a real chance to visibly
    invert order somewhere in this range (see the module docstring).
    """
    provider = ExtractiveSummarizer()
    costs = sorted(estimate_tokens(sentence) for sentence in _ENGLISH_SENTENCES)
    cheapest, total = costs[0], sum(costs)

    saw_a_multi_sentence_selection = False
    for max_tokens in range(cheapest, total):
        result = await provider.summarize(_ENGLISH_CHILDREN, scope=_scope(), max_tokens=max_tokens)
        selected = _split_sentences(result)
        assert len(selected) < len(_ENGLISH_SENTENCES), (
            f"budget {max_tokens} < total cost {total} selected everything: {selected!r}"
        )
        if len(selected) >= 2:
            saw_a_multi_sentence_selection = True
        positions = [_document_order_index(sentence, _ENGLISH_CHILDREN) for sentence in selected]
        assert positions == sorted(positions), (
            f"budget {max_tokens} selected {selected!r} out of document order "
            f"(positions {positions!r})"
        )

    assert saw_a_multi_sentence_selection, (
        "no budget in the swept range selected more than one sentence; the "
        "fixture cannot exercise order preservation and must be recalibrated"
    )


@pytest.mark.asyncio
async def test_a_budget_no_sentence_fits_truncates_the_first_sentence_deterministically() -> None:
    """decision 7 / raptor.md's deterministic fallback: when no whole sentence
    fits, the output is the *first* sentence truncated to budget -- not the
    first child's whole text, and not silence."""
    provider = ExtractiveSummarizer()
    max_tokens = 2  # Well under the cost of even the cheapest whole sentence.
    assert max_tokens < min(estimate_tokens(sentence) for sentence in _ENGLISH_SENTENCES)

    result = await provider.summarize(_ENGLISH_CHILDREN, scope=_scope(), max_tokens=max_tokens)

    assert result != "", "the no-sentence-fits fallback must still emit something"
    assert estimate_tokens(result) <= max_tokens
    assert _ALPHA.startswith(result), (
        f"fallback output {result!r} is not a verbatim prefix of the first sentence {_ALPHA!r}"
    )


@pytest.mark.asyncio
async def test_the_no_sentence_fits_fallback_is_deterministic_across_calls() -> None:
    provider = ExtractiveSummarizer()
    max_tokens = 2

    first = await provider.summarize(_ENGLISH_CHILDREN, scope=_scope(), max_tokens=max_tokens)
    second = await provider.summarize(_ENGLISH_CHILDREN, scope=_scope(), max_tokens=max_tokens)

    assert first == second


@pytest.mark.asyncio
async def test_a_latin_sentence_is_selected_whole_not_carved_from_its_unsplit_child() -> None:
    """The Latin counterpart of the CJK test below, and needed for the same
    reason: at an ample budget, a correct per-sentence split and a "treat each
    child as one atomic, unsplit unit" implementation emit the same
    characters, because Latin sentences are already whitespace-separated --
    which is *also* enough for this file's own ``_split_sentences`` to repair
    a broken implementation's output by accident, regardless of whether the
    summariser itself ever split anything.

    The two are only distinguishable where the first sentence alone fits but
    its own unsplit child (both its sentences, concatenated) does not: a
    correct implementation holds its output flat at exactly the first
    sentence, because nothing else fits either the first or second position
    in that range; an atomic-per-child implementation cannot select "just the
    first sentence" from an indivisible blob and falls back to truncating the
    unsplit child instead, extending a partial, non-terminator-ending prefix
    as the budget grows.
    """
    provider = ExtractiveSummarizer()
    lower = estimate_tokens(_SOLO_FIRST)
    upper = lower + min(estimate_tokens(s) for s in (_SOLO_SECOND, _SOLO_THIRD, _SOLO_FOURTH))
    assert lower < upper, "fixture leaves no budget range to sweep"

    for max_tokens in range(lower, upper):
        result = await provider.summarize(_SOLO_CHILDREN, scope=_scope(), max_tokens=max_tokens)
        assert result == _SOLO_FIRST, (
            f"budget {max_tokens} produced {result!r} instead of the first "
            "sentence alone and unmodified -- a summariser that never actually "
            "splits sentences would fall back to truncating the unsplit child "
            "instead of selecting the first sentence as its own whole unit"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("texts", "max_tokens"),
    [
        (("abc. def. ghi. jkl.",), 4),
        (("Same sentence text here.",) * 4, 12),
        (("東京。京都。大阪。名古屋。",), 11),
    ],
)
async def test_output_never_exceeds_the_token_budget_for_any_corpus(
    texts: tuple[str, ...], max_tokens: int
) -> None:
    """FR-R4 again, on corpora ``test_output_never_exceeds_the_token_budget``'s
    five sampled budgets cannot reach. Selection charges each *sentence's*
    own ``estimate_tokens`` cost against the budget and never re-prices the
    *joined* text it actually returns; a sentence-length distribution that
    leaves no slack for ``estimate_tokens``'s ceiling rounding lets the join
    separators push the final string's own cost past what was charged for
    it. That is what the charging did before this change, and each corpus is
    here because it overshot: the first cost 5 against a budget of 4.
    """
    provider = ExtractiveSummarizer()

    result = await provider.summarize(texts, scope=_scope(), max_tokens=max_tokens)

    assert estimate_tokens(result) <= max_tokens, (
        f"output cost {estimate_tokens(result)} exceeds the {max_tokens}-token budget: {result!r}"
    )


@pytest.mark.asyncio
async def test_output_never_exceeds_the_token_budget_across_every_english_budget() -> None:
    """The parametrized sweep above samples five budgets; every budget from 1
    up to the corpus's own total cost is checked here and in the Japanese
    counterpart below, so a charging bug is not free to hide between the
    sampled points."""
    provider = ExtractiveSummarizer()
    total = sum(estimate_tokens(sentence) for sentence in _ENGLISH_SENTENCES)

    for max_tokens in range(1, total + 1):
        result = await provider.summarize(_ENGLISH_CHILDREN, scope=_scope(), max_tokens=max_tokens)
        assert estimate_tokens(result) <= max_tokens, (
            f"output cost {estimate_tokens(result)} exceeds the {max_tokens}-token "
            f"budget: {result!r}"
        )


@pytest.mark.asyncio
async def test_output_never_exceeds_the_token_budget_across_every_japanese_budget() -> None:
    """The CJK counterpart: dense-script costs round differently from Latin
    ones, and the exhaustive sweep is what actually finds an overshoot on
    this fixture: budgets 1, 65, 69 and 98 each exceeded their own budget
    against the per-sentence charging this change replaced."""
    provider = ExtractiveSummarizer()
    total = sum(estimate_tokens(sentence) for sentence in _JAPANESE_SENTENCES)

    for max_tokens in range(1, total + 1):
        result = await provider.summarize(_JAPANESE_CHILDREN, scope=_scope(), max_tokens=max_tokens)
        assert estimate_tokens(result) <= max_tokens, (
            f"output cost {estimate_tokens(result)} exceeds the {max_tokens}-token "
            f"budget: {result!r}"
        )


@pytest.mark.asyncio
async def test_any_budget_that_fits_one_sentence_emits_at_least_one() -> None:
    """Kills a stop-at-first-misfit selection: visiting candidates in
    descending score order and breaking out of the loop the first time one
    does not fit can reach the *most expensive* sentence first and stop
    before ever trying a cheaper one that would fit alone. The order-
    preservation sweeps above only assert ordering among whatever got
    selected, so an empty selection passes them silently; this asserts
    non-emptiness directly, at every budget from the cheapest sentence's own
    cost up to the corpus total.
    """
    provider = ExtractiveSummarizer()
    costs = [estimate_tokens(sentence) for sentence in _ENGLISH_SENTENCES]

    for max_tokens in range(min(costs), sum(costs) + 1):
        result = await provider.summarize(_ENGLISH_CHILDREN, scope=_scope(), max_tokens=max_tokens)
        assert result != "", (
            f"budget {max_tokens} fits the cheapest sentence ({min(costs)}) "
            "but the summariser emitted nothing"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("max_tokens", [0, -1, -1000])
async def test_a_budget_below_one_token_is_refused(max_tokens: int) -> None:
    """Round-2 contract: no sentence, not even a one-character prefix, can
    cost zero or fewer tokens, so ``max_tokens < 1`` has nothing legitimate
    to return. ``domain.ranking.take_within_budget`` faces the identical
    situation (FR-R4) and raises ``RankingError`` rather than silently
    returning something that violates the very budget it was given -- which
    is what the fallback did before this change, returning a single character
    whose own cost already exceeded the requested budget.
    """
    provider = ExtractiveSummarizer()

    with pytest.raises(RankingError):
        await provider.summarize(_ENGLISH_CHILDREN, scope=_scope(), max_tokens=max_tokens)


# -- CJK --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_japanese_sentences_split_and_select_on_cjk_terminators() -> None:
    """A splitter that only recognised Latin ``.!?`` would see each two-sentence
    Japanese child as one inseparable blob and could never select just its
    first sentence."""
    provider = ExtractiveSummarizer()

    result = await provider.summarize(
        _JAPANESE_CHILDREN, scope=_scope(), max_tokens=_ample_budget(_JAPANESE_SENTENCES)
    )

    assert _split_sentences(result) == list(_JAPANESE_SENTENCES)


@pytest.mark.asyncio
async def test_a_japanese_sentence_is_selected_whole_not_carved_from_its_unsplit_child() -> None:
    """The test above cannot actually tell correct CJK splitting apart from a
    summariser that treats each *child* as one atomic, unsplit unit: at an
    ample budget both end up emitting the same four sentences' worth of
    characters, and because a CJK sentence needs no separator to recover
    afterward, ``_split_sentences`` repairs the atomic version's output by
    accident -- re-deriving the same four fragments regardless of whether the
    summariser itself ever split anything.

    The two are only distinguishable in the budget range where the first
    sentence alone fits but the *whole, unsplit* first child (both its
    sentences concatenated) does not: a correctly-splitting implementation
    holds its output flat at exactly the first sentence -- nothing else fits
    at either position either -- while an atomic-per-child implementation,
    unable to select "just the first sentence" from an indivisible blob, has
    to fall into the "no whole unit fits" truncation fallback instead, and
    that fallback keeps extending a partial, non-terminator-ending prefix as
    the budget grows across this same range.
    """
    provider = ExtractiveSummarizer()
    lower = estimate_tokens(_JA_ONE)
    upper = lower + min(estimate_tokens(s) for s in (_JA_TWO, _JA_THREE, _JA_FOUR))
    assert lower < upper, "fixture leaves no budget range to sweep"

    for max_tokens in range(lower, upper):
        result = await provider.summarize(_JAPANESE_CHILDREN, scope=_scope(), max_tokens=max_tokens)
        assert result == _JA_ONE, (
            f"budget {max_tokens} produced {result!r} instead of the first "
            "sentence alone and unmodified -- a summariser unable to split on "
            "CJK terminators would fall back to truncating the unsplit child "
            "instead of selecting the first sentence as its own whole unit"
        )


@pytest.mark.asyncio
async def test_a_restrictive_budget_selects_japanese_sentences_in_document_order() -> None:
    """The CJK counterpart of the English sweep above, same technique and same
    reason: which budget makes two *differently scored* sentences compete is
    the implementer's scoring choice, not something this file may assume, so
    every budget across the swept range is checked rather than one picked in
    advance."""
    provider = ExtractiveSummarizer()
    costs = sorted(estimate_tokens(sentence) for sentence in _JAPANESE_SENTENCES)
    cheapest, total = costs[0], sum(costs)

    saw_a_multi_sentence_selection = False
    for max_tokens in range(cheapest, total):
        result = await provider.summarize(_JAPANESE_CHILDREN, scope=_scope(), max_tokens=max_tokens)
        selected = _split_sentences(result)
        assert len(selected) < len(_JAPANESE_SENTENCES), (
            f"budget {max_tokens} < total cost {total} selected everything: {selected!r}"
        )
        if len(selected) >= 2:
            saw_a_multi_sentence_selection = True
        positions = [_document_order_index(sentence, _JAPANESE_CHILDREN) for sentence in selected]
        assert positions == sorted(positions), (
            f"budget {max_tokens} selected {selected!r} out of document order "
            f"(positions {positions!r})"
        )

    assert saw_a_multi_sentence_selection, (
        "no budget in the swept range selected more than one sentence; the "
        "fixture cannot exercise order preservation and must be recalibrated"
    )


@pytest.mark.asyncio
async def test_mixed_latin_and_cjk_terminators_both_split_correctly() -> None:
    """One child mixing an English sentence, a Japanese sentence, and another
    English sentence -- both terminator families must be recognised inside a
    single piece of text, not just at the top level across separate children.
    """
    provider = ExtractiveSummarizer()

    result = await provider.summarize(
        (_MIXED_CHILD,), scope=_scope(), max_tokens=_ample_budget((_MIXED_CHILD,))
    )

    assert _split_sentences(result) == [
        "Rotating tokens reduces exposure.",
        "署名付きトークンを持つリクエストのみ許可される。",
        "Both concepts matter here.",
    ]


@pytest.mark.asyncio
async def test_a_restrictive_budget_selects_the_mixed_childs_first_sentence_whole() -> None:
    """The Latin and CJK atomic-vs-split tests above each get a dedicated
    restrictive-budget sweep because an ample budget cannot tell a correct
    per-sentence splitter apart from one that treats a whole child as one
    indivisible unit -- this file's own ``_split_sentences`` repairs the
    atomic version's output by accident either way. ``_MIXED_CHILD`` has no
    such companion in the original suite. Same technique: in the budget
    range where the first (Latin) sentence alone fits but the CJK sentence
    after it does not, a correctly-splitting implementation holds flat at
    exactly the first sentence, while a never-splits implementation falls
    into truncating the whole, unsplit child instead.
    """
    provider = ExtractiveSummarizer()
    first_sentence = "Rotating tokens reduces exposure."
    cjk_sentence = "署名付きトークンを持つリクエストのみ許可される。"
    third_sentence = "Both concepts matter here."
    lower = estimate_tokens(first_sentence)
    upper = lower + min(estimate_tokens(cjk_sentence), estimate_tokens(third_sentence))
    assert lower < upper, "fixture leaves no budget range to sweep"

    for max_tokens in range(lower, upper):
        result = await provider.summarize((_MIXED_CHILD,), scope=_scope(), max_tokens=max_tokens)
        assert result == first_sentence, (
            f"budget {max_tokens} produced {result!r} instead of the mixed "
            "child's first sentence alone -- a summariser that never splits "
            "a child containing both scripts would fall back to truncating "
            "the unsplit blob instead"
        )


# -- Selection mechanism (skip-not-stop, scoring, ties, separator, splitting) -


@pytest.mark.asyncio
async def test_a_cheap_low_scoring_sentence_is_selected_when_an_expensive_high_scoring_one_does_not_fit() -> (  # noqa: E501
    None
):
    """``_select``'s own docstring names the mechanism this pins: each
    candidate is tried in descending score order and *skipped*, not treated
    as a reason to stop, when it does not fit the remaining budget. Here one
    sentence repeats a keyword and so scores far higher than a short,
    unrelated one. At any budget that fits the cheap sentence alone but not
    the expensive one, a stop-at-first-misfit selection tries the expensive
    sentence first (it scores higher), fails to fit it, and stops -- emitting
    nothing -- while skip-not-stop moves on and still selects the cheap one.
    """
    provider = ExtractiveSummarizer()
    expensive = (
        "Rotating tokens tokens tokens tokens reduces exposure across every hosted region nightly."
    )
    cheap = "Ok."
    texts = (f"{expensive} {cheap}",)
    lower = estimate_tokens(cheap)
    upper = estimate_tokens(expensive)
    assert lower < upper, "fixture leaves no budget range to sweep"

    for max_tokens in range(lower, upper):
        result = await provider.summarize(texts, scope=_scope(), max_tokens=max_tokens)
        assert result == cheap, (
            f"budget {max_tokens} fits {cheap!r} ({lower} tokens) but not "
            f"{expensive!r} ({upper} tokens); got {result!r}"
        )


@pytest.mark.asyncio
async def test_trigram_frequency_scoring_prefers_a_short_repetitive_sentence_over_a_longer_unique_one() -> (  # noqa: E501
    None
):
    """Distinguishes the documented scorer -- sum of cross-sentence trigram
    frequency -- from a plausible-looking substitute that scores by sentence
    length (number of trigrams) instead. ``_SHORT_REPETITIVE`` is barely half
    ``_LONG_UNIQUE``'s length but repeats its own keyword, so it out-scores
    the longer sentence under frequency and loses under length. At a budget
    that fits only one of the two, which one gets selected tells the two
    scorers apart.
    """
    provider = ExtractiveSummarizer()
    texts = (f"{_SHORT_REPETITIVE} {_LONG_UNIQUE}",)
    lower = estimate_tokens(_LONG_UNIQUE)
    upper = lower + estimate_tokens(_SHORT_REPETITIVE)
    assert lower < upper, "fixture leaves no budget range to sweep"

    for max_tokens in range(lower, upper):
        result = await provider.summarize(texts, scope=_scope(), max_tokens=max_tokens)
        assert result == _SHORT_REPETITIVE, (
            f"budget {max_tokens} selected {result!r}; a length-based scorer "
            f"would have preferred {_LONG_UNIQUE!r} here instead"
        )


def test_scoring_credits_a_case_only_variant_of_the_same_sentence() -> None:
    """The module docstring specifies scoring on *lower-cased* trigrams
    precisely so a differently-cased repetition of the same content is still
    recognised as a repetition. Comparing one sentence's score when it is
    alone in the call against its score when an all-uppercase duplicate of
    the exact same words also appears: under the documented lower-casing,
    the duplicate's trigrams merge with the original's and the score moves;
    without lower-casing, the two texts share no trigrams at all (every
    character differs in case) and the score does not move.
    """
    normal = extractive._Sentence(
        ordinal=0, text="Rotating tokens reduces exposure quickly today.", cost=1
    )
    shouting = extractive._Sentence(
        ordinal=1, text="ROTATING TOKENS REDUCES EXPOSURE QUICKLY TODAY.", cost=1
    )

    (alone_score,) = extractive._score((normal,))
    together_score, _ = extractive._score((normal, shouting))

    assert together_score > alone_score, (
        "score did not move when a same-content, differently-cased sentence "
        f"joined the call: alone={alone_score} together={together_score}"
    )


@pytest.mark.asyncio
async def test_a_genuine_score_tie_breaks_toward_document_order() -> None:
    """``ab cd.`` and ``cd ab.`` contain the same three trigrams in reverse,
    so their trigram-frequency scores are genuinely equal -- not merely
    close -- which is the one situation where the tie-break key, not the
    score, decides what gets selected. At a budget that fits exactly one of
    them, the tie must resolve toward document position (the earlier
    sentence), not away from it.
    """
    provider = ExtractiveSummarizer()
    texts = (f"{_TIE_FIRST} {_TIE_SECOND}",)

    result = await provider.summarize(texts, scope=_scope(), max_tokens=estimate_tokens(_TIE_FIRST))

    assert result == _TIE_FIRST, (
        f"a genuine score tie resolved to {result!r} instead of the "
        f"earlier-positioned sentence {_TIE_FIRST!r}"
    )


@pytest.mark.asyncio
async def test_selected_sentences_are_joined_by_a_single_space() -> None:
    """This file's own ``_split_sentences`` matches on ``\\s+`` after a Latin
    terminator, so it re-derives the same sentence list whether the real
    implementation joins with a space, a newline, or several spaces -- every
    order-preservation test above would pass regardless of which separator
    was actually used. The separator's exact character only shows up in a
    literal string comparison against the joined text.
    """
    provider = ExtractiveSummarizer()

    result = await provider.summarize(
        _ENGLISH_CHILDREN, scope=_scope(), max_tokens=_ample_budget(_ENGLISH_SENTENCES)
    )

    assert result == " ".join(_ENGLISH_SENTENCES)


@pytest.mark.asyncio
async def test_a_terminator_followed_by_a_space_does_not_leak_a_leading_space_into_the_next_sentence() -> (  # noqa: E501
    None
):
    """A CJK terminator needs no trailing whitespace to end a sentence, but
    prose sometimes has one anyway. If a split piece is not stripped, that
    leading space becomes part of the "sentence" text and shows up as a
    double space once the selection re-joins it with its own single-space
    separator -- invisible to a ``_split_sentences``-based check (``\\s+``
    absorbs both single and double spaces alike) and visible only in a
    literal comparison.
    """
    provider = ExtractiveSummarizer()
    text = "東京は都市。 京都も都市。 大阪も都市。"

    result = await provider.summarize((text,), scope=_scope(), max_tokens=200)

    assert result == text


def test_the_sentence_terminator_pattern_currently_matches_chunkings() -> None:
    """``extractive._SENTENCE_TERMINATOR`` is deliberately a private copy of
    ``domain.chunking._SENTENCE_END`` (see both modules' own docstrings): a
    token-budget splitter and a length-budget splitter that are free to
    diverge without either import breaking the other. This does not assert
    the two *must* stay equal forever -- it is the mechanism that notices if
    they silently stop agreeing. A change to one pattern that was not a
    deliberate divergence turns this red; a reviewer then either updates both
    or edits this assertion to record why they now differ.

    One inherited consequence, recorded because it is visible in the output:
    both patterns end a sentence at a numbered-list marker, so
    ``"Steps: 1. Install it. 2. Run it."`` splits into ``"Steps: 1."``,
    ``"Install it."``, ``"2."`` and ``"Run it."`` and a bare ``"2."`` can be
    selected as a sentence of its own. It comes from ``domain.chunking``
    rather than being chosen here, and changing it would change which text
    this module selects for the same children -- which makes it a
    ``SEMANTICS_VERSION`` bump, not a tidy-up (ADR-0008 decision 5).
    """
    assert extractive._SENTENCE_TERMINATOR.pattern == chunking._SENTENCE_END.pattern


# -- Determinism -------------------------------------------------------


@pytest.mark.asyncio
async def test_repeated_calls_are_byte_identical_even_with_freshly_built_string_objects() -> None:
    """Guards against any reliance on iteration order keyed by object identity
    (e.g. a ``set``/``dict`` walked without an explicit, value-based tie
    break) rather than by value or document position. ``PYTHONHASHSEED``
    variance across processes is not testable in-process; using a fresh string
    object with the same value on every call is the in-process substitute --
    it rules out a bug that only a coincidentally-reused object would hide.

    Deliberately a *restrictive* budget, not a generous one: with room for
    every sentence, which one gets considered first cannot change the
    result, so a generous budget would pass even against a selector that
    picks its candidate set in a fresh (and here, effectively random) order
    on every call and only coincidentally has nothing left to disagree
    about.
    """
    provider = ExtractiveSummarizer()
    max_tokens = _partial_selection_budget(_ENGLISH_SENTENCES)

    outputs = set()
    for _ in range(20):
        fresh_children = (
            "".join(list(CHILD_ONE)),
            "".join(list(CHILD_TWO)),
        )
        fresh_scope = _scope()
        result = await provider.summarize(fresh_children, scope=fresh_scope, max_tokens=max_tokens)
        outputs.add(result)

    assert len(outputs) == 1, f"repeated calls diverged: {outputs!r}"


def _run_summarize_in_a_fresh_process(children: tuple[str, ...], budget: int, seed: str) -> str:
    """One ``summarize`` call in a brand-new interpreter with a fixed
    ``PYTHONHASHSEED``, mirroring ``test_projection.py``'s cross-process
    pattern (ADR-0020)."""
    program = (
        "import asyncio;"
        "from theurian.domain.enums import KnowledgeStatus, Sensitivity;"
        "from theurian.domain.identifiers import ProjectId;"
        "from theurian.domain.values import AclGroup, Scope, TenantId;"
        "from theurian.infrastructure.raptor.extractive import ExtractiveSummarizer;"
        "scope = Scope(project_id=ProjectId('backend-service'), tenant_id=TenantId('local'), "
        "sensitivity=Sensitivity.INTERNAL, acl_group=AclGroup('default'), "
        "namespace='architecture', status=KnowledgeStatus.APPROVED);"
        f"children = {children!r};"
        f"budget = {budget};"
        "print(asyncio.run(ExtractiveSummarizer().summarize("
        "children, scope=scope, max_tokens=budget)))"
    )
    return subprocess.run(  # noqa: S603 - fixed argv
        [sys.executable, "-c", program],
        capture_output=True,
        text=True,
        check=True,
        env={"PYTHONHASHSEED": seed, "PATH": "/usr/bin:/bin"},
    ).stdout


#: The exact seeds ``test_projection.py`` cross-checks (ADR-0020). Two seeds
#: agreeing is not enough to trust: a hash-seed-dependent tie-break was found,
#: while drafting this test, to coincidentally order two fixture sentences
#: the same way under seeds "0" and "1" and only differently under "999" --
#: matching the cited file's own seed choice exactly is what makes this a
#: reliable guard rather than a coin flip.
_HASH_SEEDS = ("0", "1", "999")


def test_summarize_is_stable_across_processes() -> None:
    """``PYTHONHASHSEED`` varies by default across interpreter invocations, so
    a selection or scoring step that iterates a ``set`` or a ``dict`` keyed by
    something other than a stable value would be invisible within one process
    and non-deterministic across machines -- exactly the gap
    ``test_repeated_calls_are_byte_identical_...`` above, which never leaves
    this process, cannot see.
    """
    budget = _partial_selection_budget(_ENGLISH_SENTENCES)

    results = {
        _run_summarize_in_a_fresh_process(_ENGLISH_CHILDREN, budget, seed) for seed in _HASH_SEEDS
    }

    assert len(results) == 1, f"output varies with PYTHONHASHSEED: {results}"


def test_a_tied_selection_is_stable_across_processes() -> None:
    """``_ENGLISH_CHILDREN`` above has no genuine score ties, so a tie-break
    that accidentally started reading a hash-seed-dependent key (e.g.
    ``hash(sentence.text)``) instead of ``ordinal`` would pass that check by
    having nothing to disagree about. The tied fixture used to pin the
    tie-break direction (``test_a_genuine_score_tie_breaks_toward_document_
    order``) is exactly the corpus where such a regression would show up, so
    it is what this test runs across process boundaries.
    """
    budget = estimate_tokens(_TIE_FIRST)

    results = {
        _run_summarize_in_a_fresh_process((f"{_TIE_FIRST} {_TIE_SECOND}",), budget, seed)
        for seed in _HASH_SEEDS
    }

    assert len(results) == 1, f"output varies with PYTHONHASHSEED: {results}"


# -- Purity (ADR-0008 decision 6's Milestone 6 amendment) ------------------


@pytest.mark.asyncio
async def test_the_same_children_summarise_identically_across_contexts_that_differ_everywhere_else() -> (  # noqa: E501
    None
):
    """The owed test ADR-0008's Compliance section names verbatim: "Summarise
    the same children under the same scope in two corpora that differ
    everywhere else ... and the node text must be byte-identical." Carrier
    (a) only -- the summariser's text inputs.

    Context A constructs a fresh provider and calls it once, representing a
    corpus that holds nothing else. Context B reuses one provider instance
    across eight unrelated calls before the call under test and five after --
    a different scope, different content, different sizes each time --
    representing a corpus that differs from A's in every way except the one
    thing decision 6 says may influence the output: the children and the
    scope actually passed to this call.

    Checked at two budgets, not one. A budget with a wide margin over the
    content's own cost (``_ample_budget``) would hide a small, call-count-
    keyed perturbation -- e.g. an instance that shaved a token off its
    remaining budget per prior call -- because the margin absorbs it and
    every sentence still fits either way. A tight budget forces even a
    one-token difference to move whether the last sentence is included, and
    the restrictive, partial-selection budget exercises the same equality
    where *which* sentences get selected, not just how many, can move.
    """
    scope = _scope()
    other_scope = _scope(namespace="an-unrelated-namespace-with-a-much-larger-corpus")
    tight_budget = sum(estimate_tokens(sentence) for sentence in _ENGLISH_SENTENCES) + 3
    partial_budget = _partial_selection_budget(_ENGLISH_SENTENCES)

    async def _fresh_context(max_tokens: int) -> str:
        provider = ExtractiveSummarizer()
        return await provider.summarize(_ENGLISH_CHILDREN, scope=scope, max_tokens=max_tokens)

    async def _surrounded_context(max_tokens: int) -> str:
        provider = ExtractiveSummarizer()
        for index in range(8):
            await provider.summarize(
                (f"Unrelated corpus document {index}." * (index + 1),),
                scope=other_scope,
                max_tokens=50 * (index + 1),
            )
        result = await provider.summarize(_ENGLISH_CHILDREN, scope=scope, max_tokens=max_tokens)
        for index in range(5):
            await provider.summarize(
                (f"Yet another unrelated document {index}, interleaved afterward.",),
                scope=other_scope,
                max_tokens=200,
            )
        return result

    for max_tokens in (tight_budget, partial_budget):
        result_a = await _fresh_context(max_tokens)
        result_b = await _surrounded_context(max_tokens)

        assert result_a == result_b, (
            f"diverged at max_tokens={max_tokens}: {result_a!r} != {result_b!r}"
        )
        assert result_a.encode("utf-8") == result_b.encode("utf-8"), (
            "outputs compared equal but were not byte-identical -- str equality "
            "in Python cannot miss this, but the ADR states the property in bytes"
        )


class _CorpusReadingFakeSummarizer:
    """NEGATIVE CONTROL. Deliberately violates the purity constraint, to prove
    the two-corpus technique above can actually detect a corpus-reading
    summariser and is not passing merely because nothing in the harness could
    ever fail it.

    Holds a corpus handle acquired in its own constructor -- exactly the
    detour ADR-0008 decision 6 says an adapter would have to make, since
    ``SummarizationProvider.summarize`` itself is handed no such handle. This
    class is not the ``ExtractiveSummarizer`` under test anywhere else in this
    file; it exists only to be the broken fixture this one test needs.
    """

    model_id = "corpus-reading-fake"
    model_revision = "0"
    prompt_hash = "0" * 64

    def __init__(self, corpus: tuple[str, ...]) -> None:
        self._corpus = corpus

    async def summarize(
        self,
        texts: tuple[str, ...],
        *,
        scope: Scope,  # noqa: ARG002 - port shape; unused is exactly the point
        max_tokens: int,  # noqa: ARG002 - ditto
    ) -> str:
        # Illegitimately lets corpus size leak into output that is supposed to
        # be a pure function of `texts`, `scope`, and `max_tokens` alone.
        return f"{len(self._corpus)} other docs :: {texts[0]}"


@pytest.mark.asyncio
async def test_negative_control_a_corpus_reading_fake_is_detected_as_different() -> None:
    """Same ``texts``/``scope``/``max_tokens`` in both calls; only the
    constructor-held corpus differs. If this did not come out different, the
    two-corpus equality test above would be incapable of ruling out a
    corpus-reading summariser and its earlier pass would mean nothing."""
    scope = _scope()
    fake_a = _CorpusReadingFakeSummarizer(corpus=("doc-1", "doc-2"))
    fake_b = _CorpusReadingFakeSummarizer(corpus=("doc-1", "doc-2", "doc-3", "doc-4", "doc-5"))

    result_a = await fake_a.summarize(_ENGLISH_CHILDREN, scope=scope, max_tokens=200)
    result_b = await fake_b.summarize(_ENGLISH_CHILDREN, scope=scope, max_tokens=200)

    assert result_a != result_b


@pytest.mark.asyncio
async def test_negative_control_corpus_derived_max_tokens_is_detected_as_different() -> None:
    """The second carrier the purity test needs a control for: carrier (c),
    ``max_tokens`` itself. The summariser under test here is the real
    ``ExtractiveSummarizer``, which reads nothing but its parameters -- what
    is impure is a caller that derives ``max_tokens`` from corpus size, a
    plausible-looking builder bug the port's own signature does not prevent.
    Demonstrating the harness catches this even though the summariser itself
    is innocent is the point.
    """
    provider = ExtractiveSummarizer()
    scope = _scope()
    shared_budget_tokens = 400
    corpus_size_a = 4
    corpus_size_b = 40
    max_tokens_a = shared_budget_tokens // corpus_size_a  # 100: room for everything
    max_tokens_b = shared_budget_tokens // corpus_size_b  # 10: room for very little

    result_a = await provider.summarize(_ENGLISH_CHILDREN, scope=scope, max_tokens=max_tokens_a)
    result_b = await provider.summarize(_ENGLISH_CHILDREN, scope=scope, max_tokens=max_tokens_b)

    assert result_a != result_b


# -- Input cap (round 2) -----------------------------------------------------
#
# Nothing yet bounds how many characters ``summarize`` will scan, and the
# trigram scorer's cost grows with corpus size for a single giant sentence.
# ``MAX_TOTAL_INPUT_CHARS`` is the module's own recorded limit on the total
# size of ``texts`` -- not implemented yet, so both tests below are RED
# against the shipped module, which has no such attribute.


@pytest.mark.asyncio
async def test_input_above_the_recorded_cap_is_refused() -> None:
    """Read from the module's own constant rather than duplicated as a
    literal here, so this test moves automatically if the implementer
    changes the recorded limit."""
    provider = ExtractiveSummarizer()
    cap = extractive.MAX_TOTAL_INPUT_CHARS
    text = "a" * cap + "b"

    with pytest.raises(InvariantViolationError):
        await provider.summarize((text,), scope=_scope(), max_tokens=10_000)


@pytest.mark.asyncio
async def test_input_exactly_at_the_recorded_cap_still_summarizes() -> None:
    """The boundary itself must still work -- a cap that rejected its own
    limit would silently narrow the documented contract by one character.
    """
    provider = ExtractiveSummarizer()
    cap = extractive.MAX_TOTAL_INPUT_CHARS
    text = "a" * (cap - 1) + "."

    result = await provider.summarize((text,), scope=_scope(), max_tokens=10_000)

    assert result != ""


# -- Whitespace-only input ---------------------------------------------------


@pytest.mark.asyncio
async def test_whitespace_only_children_summarize_to_the_empty_string() -> None:
    """Every piece a whitespace-only child splits into strips to empty and is
    dropped (see ``_split_sentences``'s own docstring), so there is nothing
    to select from. Pinned explicitly as the contract, not left as an
    accident of how splitting happens to behave, so it cannot silently
    regress into raising or into echoing the whitespace back.
    """
    provider = ExtractiveSummarizer()

    result = await provider.summarize(("   ", "\n\t  "), scope=_scope(), max_tokens=1000)

    assert result == ""


#: Every stdlib module that can open a socket, plus the two that reach one
#: through a layer. ``asyncio`` is listed as a whole rather than by its
#: ``asyncio.streams`` submodule because importing the package imports the
#: submodule: a module that pulled in ``asyncio`` for its ``async def`` -- it
#: does not need to -- would drag the socket transports in with it.
_SOCKET_CAPABLE_MODULES = frozenset(
    {
        "asyncio",
        "ftplib",
        "http",
        "http.client",
        "imaplib",
        "poplib",
        "select",
        "selectors",
        "smtplib",
        "socket",
        "socketserver",
        "ssl",
        "telnetlib",
        "urllib.request",
        "webbrowser",
        "xmlrpc.client",
    }
)


def test_the_default_summarizer_reaches_no_socket_capable_module() -> None:
    """The module's own claim that "nothing here calls out" (ADR-0009's
    offline default, SEC-19) is a property of the whole import closure, not of
    that one file: a module that reaches nothing over the network can still
    import one that does, and the day someone adds a hosted fallback "just for
    the abstractive case" the extractive default inherits its dependencies.

    Checked in a fresh interpreter because ``sys.modules`` inside the test
    process already holds most of the standard library, imported by pytest and
    by the rest of the suite -- an in-process assertion would fail regardless
    of what this module does, which is the same reason
    ``_run_summarize_in_a_fresh_process`` above exists.
    """
    program = (
        "import sys;"
        "import theurian.infrastructure.raptor.extractive;"
        "print(chr(10).join(sorted(sys.modules)))"
    )

    loaded = set(
        subprocess.run(  # noqa: S603 - fixed argv
            [sys.executable, "-c", program],
            capture_output=True,
            text=True,
            check=True,
            env={"PATH": "/usr/bin:/bin"},
        ).stdout.split()
    )

    assert "theurian.infrastructure.raptor.extractive" in loaded, (
        "the subprocess did not import the module under test at all"
    )
    assert not (loaded & _SOCKET_CAPABLE_MODULES), (
        "importing the default summariser loaded socket-capable modules: "
        f"{sorted(loaded & _SOCKET_CAPABLE_MODULES)}"
    )


@pytest.mark.asyncio
async def test_content_bearing_input_stays_non_empty_alongside_whitespace_only_children() -> None:
    """Distinguishes "no sentences at all" from "a real sentence, plenty of
    budget": the never-empty guarantee (decision 7 / the class docstring)
    must still hold once genuine content is present, even mixed in with
    children that are themselves whitespace-only.
    """
    provider = ExtractiveSummarizer()

    result = await provider.summarize(
        ("   ", "Real content here."), scope=_scope(), max_tokens=1000
    )

    assert result != ""
