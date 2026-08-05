"""The default embedding provider (ADR-0009, §26).

Deterministic and offline, so the suite never needs a key, a network, or a
model download.
"""

from __future__ import annotations

import math

import pytest

from theurian.domain.ports.embedding import EmbeddingProvider
from theurian.infrastructure.embedding import HashingEmbedding


def _cosine(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


@pytest.fixture
def embedder() -> HashingEmbedding:
    return HashingEmbedding()


def test_it_satisfies_the_port(embedder: HashingEmbedding) -> None:
    assert isinstance(embedder, EmbeddingProvider)


def test_the_same_text_always_embeds_identically(embedder: HashingEmbedding) -> None:
    """FR-R7. A rebuilt index must produce vectors a pinned result still ranks
    against, so `hash()` -- randomised per process -- would be disqualifying."""
    assert embedder.embed_one("token rotation") == embedder.embed_one("token rotation")


def test_it_is_deterministic_across_processes() -> None:
    """The property `hash()` would break. Asserted against a value computed in a
    separate interpreter, because within one process randomisation is invisible.
    """
    import subprocess
    import sys

    code = (
        "from theurian.infrastructure.embedding import HashingEmbedding;"
        "print(HashingEmbedding().embed_one('token rotation')[:4])"
    )
    result = subprocess.run(  # noqa: S603 - this interpreter, a literal snippet
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )

    assert str(HashingEmbedding().embed_one("token rotation")[:4]) == result.stdout.strip()


def test_vectors_are_unit_length(embedder: HashingEmbedding) -> None:
    """Without normalising, a long document outranks a short one merely by
    having more n-grams."""
    vector = embedder.embed_one("a reasonably long sentence about authentication tokens")

    assert math.sqrt(sum(v * v for v in vector)) == pytest.approx(1.0)


def test_the_dimension_is_constant(embedder: HashingEmbedding) -> None:
    for text in ("", "x", "a much longer piece of text " * 20):
        assert len(embedder.embed_one(text)) == embedder.dimension


def test_empty_text_yields_a_zero_vector(embedder: HashingEmbedding) -> None:
    """Which the dense search then declines to score, rather than dividing by
    zero."""
    assert set(embedder.embed_one("   ")) == {0.0}


# -- What it is actually for -------------------------------------------------


def test_a_morphological_variant_is_closer_than_an_unrelated_word(
    embedder: HashingEmbedding,
) -> None:
    """The reason this exists at all: FTS5 matches terms exactly, so `rotating`
    does not retrieve `rotation`. Character n-grams do."""
    base = embedder.embed_one("rotation")
    variant = embedder.embed_one("rotating")
    unrelated = embedder.embed_one("caching")

    assert _cosine(base, variant) > _cosine(base, unrelated)


def test_a_typo_stays_close_to_the_word_it_meant(embedder: HashingEmbedding) -> None:
    base = embedder.embed_one("authentication")
    typo = embedder.embed_one("autentication")
    unrelated = embedder.embed_one("serialisation")

    assert _cosine(base, typo) > _cosine(base, unrelated)


def test_japanese_text_produces_useful_grams(embedder: HashingEmbedding) -> None:
    """CJK has no spaces, so a word-level vectoriser would see one token. Three
    characters is often a whole word in Japanese."""
    base = embedder.embed_one("トークン検証")
    related = embedder.embed_one("トークンの検証手順")
    unrelated = embedder.embed_one("キャッシュ戦略")

    assert _cosine(base, related) > _cosine(base, unrelated)


def test_accents_are_folded_the_way_fts5_folds_them(embedder: HashingEmbedding) -> None:
    """Two retrievers disagreeing about whether an accent matters is a
    confusing way to lose a result."""
    assert embedder.embed_one("résumé") == embedder.embed_one("resume")


def test_case_does_not_matter(embedder: HashingEmbedding) -> None:
    assert embedder.embed_one("Token") == embedder.embed_one("token")


def test_it_does_not_claim_to_be_semantic(embedder: HashingEmbedding) -> None:
    """The model id is surfaced to callers. Naming it honestly is what stops a
    hybrid search backed by n-grams being read as one backed by a real model."""
    assert "ngram" in embedder.model_id


@pytest.mark.asyncio
async def test_a_batch_preserves_order(embedder: HashingEmbedding) -> None:
    """Vectors are zipped back onto chunk ids by position. Reordering here would
    attach every vector to the wrong chunk -- silently, and only visible as
    inexplicably bad results."""
    texts = ("first", "second", "third")

    vectors = await embedder.embed(texts)

    assert len(vectors) == 3
    for text, vector in zip(texts, vectors, strict=True):
        assert vector == embedder.embed_one(text)
