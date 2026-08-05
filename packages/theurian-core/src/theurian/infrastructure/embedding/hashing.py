"""The default embedding provider: deterministic, local, no API key (ADR-0009).

**This is not a semantic model, and it does not pretend to be.** It is a hashed
character n-gram vectoriser. What it buys over FTS5's exact term matching is
morphological and typographic tolerance: `rotating` retrieves `rotation`,
`autentication` retrieves `authentication`, and `トークン` retrieves `トークン
検証` — none of which a term index matches at all. What it does not buy is
meaning: it will never connect "credential lifetime" to "token expiry".

Shipping it as the default is a deliberate trade. The alternative defaults are
worse in ways that matter more:

- an API-backed model needs a key, a network, and a per-call cost, which makes
  first-run setup fail for someone evaluating the tool offline (OSS-15);
- a bundled local transformer adds hundreds of megabytes and a hard dependency on
  a specific runtime, which is exactly the lock-in ADR-0009 rejects;
- no dense retriever at all removes the fusion path entirely, so the day a real
  provider is configured, an untested code path runs for the first time.

``model_id`` says what this is, and the retrieval mode a caller sees names the
model — so a hybrid search backed by n-grams is never mistaken for one backed by
a semantic model.
"""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from typing import Final, final

#: Vector width. Large enough that unrelated n-grams rarely collide, small enough
#: that an exact scan over thousands of chunks stays instant.
DIMENSION: Final = 256

#: Character n-gram size. Three is the usual choice for this family: long enough
#: to carry a morpheme, short enough to survive a typo in a neighbouring letter.
#: It also happens to work for CJK, where three characters is often a whole word.
NGRAM: Final = 3

MODEL_ID: Final = "theurian-hashed-char-ngram"
MODEL_REVISION: Final = "1"

_WHITESPACE = re.compile(r"\s+")


@final
class HashingEmbedding:
    """Embeds text as L2-normalised hashed character trigrams."""

    model_id = MODEL_ID
    model_revision = MODEL_REVISION
    dimension = DIMENSION

    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        """Embed a batch. Never touches the network, so there is nothing to time
        out (SEC-19)."""
        return tuple(self.embed_one(text) for text in texts)

    def embed_one(self, text: str) -> tuple[float, ...]:
        """Embed one string.

        Deterministic across processes and machines: the hash is BLAKE2b rather
        than :func:`hash`, whose randomisation would give a rebuilt index vectors
        that no longer match the ones a pinned result was ranked against.
        """
        normalised = _normalise(text)
        if not normalised:
            return tuple([0.0] * DIMENSION)

        vector = [0.0] * DIMENSION
        for gram in _ngrams(normalised):
            digest = hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "little") % DIMENSION
            # The sign comes from a different byte of the same digest, so
            # colliding n-grams cancel as often as they reinforce. Without it,
            # every collision inflates a bucket and long documents drift towards
            # a single dense direction that matches everything.
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[bucket] += sign

        return _l2_normalise(vector)


def _normalise(text: str) -> str:
    """Case-fold, strip accents, and collapse whitespace.

    NFKD then dropping combining marks makes `résumé` and `resume` produce the
    same n-grams, matching what the FTS5 tokenizer does with
    ``remove_diacritics``. Two retrievers disagreeing about whether an accent
    matters would be a confusing way to lose a result.
    """
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _WHITESPACE.sub(" ", stripped).strip()


def _ngrams(text: str) -> list[str]:
    """Character n-grams, padded so short words still produce grams.

    Word boundaries are marked so that `token` and `tokens` share most grams
    while `stoken` does not accidentally look like `token` at the start of a
    word.
    """
    grams: list[str] = []
    for word in text.split(" "):
        padded = f" {word} "
        if len(padded) <= NGRAM:
            grams.append(padded)
            continue
        grams.extend(padded[i : i + NGRAM] for i in range(len(padded) - NGRAM + 1))
    return grams


def _l2_normalise(vector: list[float]) -> tuple[float, ...]:
    """Scale to unit length.

    Cosine similarity is then a dot product, and — more importantly — a long
    document no longer outranks a short one merely by having more n-grams.
    """
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0:
        return tuple(vector)
    return tuple(value / norm for value in vector)
