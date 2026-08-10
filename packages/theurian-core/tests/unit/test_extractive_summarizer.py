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
``prompt_hash`` exists to serve, so it is pinned here against the *versioned*
constant that describes the algorithm, not merely asserted to be a string: a
future change to selection semantics that forgets to bump that constant would
leave stale nodes silently unrebuilt, and this test is what would catch it in
review.
"""

from __future__ import annotations

import inspect
import re

import pytest

from theurian.domain.enums import KnowledgeStatus, Sensitivity
from theurian.domain.identifiers import ProjectId
from theurian.domain.ports.summarization import SummarizationProvider
from theurian.domain.ranking import estimate_tokens
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


def test_model_id_is_extractive() -> None:
    """Pinned to the literal the ADR's Milestone 6 amendment and raptor.md both
    describe the default as: "extractive". A caller reading ``model_id`` off a
    persisted node must be able to tell this apart from an abstractive model
    without guessing."""
    provider = ExtractiveSummarizer()

    assert provider.model_id == "extractive"


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


def test_prompt_hash_is_pinned_to_the_versioned_algorithm_description() -> None:
    """Decision 5's staleness rule only works if ``prompt_hash`` actually moves
    when selection semantics change. Asserting it equals
    ``ContentHash.of_text`` of the module's own versioned description --
    rather than merely asserting it is *some* stable string -- is what makes a
    reviewer's job checking a future semantics change mechanical: if the
    constant was not bumped, this assertion still holds and the review comment
    writes itself.
    """
    provider = ExtractiveSummarizer()

    assert provider.prompt_hash == ContentHash.of_text(extractive.ALGORITHM_DESCRIPTION).value


def test_prompt_hash_is_a_valid_content_hash() -> None:
    provider = ExtractiveSummarizer()

    # ContentHash's own constructor is the format guard (64 lowercase hex);
    # constructing it here is the assertion.
    ContentHash(provider.prompt_hash)


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
