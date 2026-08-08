"""The two honest shapes a retriever's answer can take (issue #16).

Six classes across five files stand in for ``IndexStore``'s retrievers or wrap
it — ``rg "def search_lexical" packages/theurian-core`` is the key, and the six
are ``_ScriptedIndex``, ``_CountingIndex``, ``_NeverFinished``, ``_TwoOpinions``,
``_TwoRankings`` and ``_CountedStore``. Four use the helpers below; the other two
are named at the end of this docstring, with why. Every one of them has to answer
``exhausted``. Answering it wrong is not a failing
test — it is a *passing* one: a fake that reports ``exhausted=True``
unconditionally ends the depth loop for a reason the real store would not, and
takes the depth tests green with it while measuring nothing.

So the two shapes the real adapter has are written once, here, rather than four
times from memory:

``truncating``
    a ``LIMIT``-bearing lookup. ``search_lexical`` and the trigram branch of
    ``search_substring``. Exhausted exactly when the ranking did not fill the
    ask — which the real adapter establishes by fetching ``limit + 1`` and
    seeing whether the extra row arrives, and which a fake holding the whole
    ranking in memory can simply read off.

``whole``
    a retriever with no ``LIMIT`` to bound: ``search_dense``, and
    ``search_substring``'s scan below the trigram floor. It has read and scored
    everything by the time it returns, so it is exhausted on its first and only
    call — whatever the corpus, and whatever the canonical store has withdrawn
    since the build.

The two that do not use these helpers are the two that must not.
``_NeverFinished`` is *dishonest* on purpose — it checks that the loop refuses a
retriever which never terminates — and constructs :class:`RetrieverPage`
directly, because there is deliberately no helper for a page that cannot be
honest. ``_CountedStore`` delegates to a real ``SqliteIndexStore`` and returns
whatever it says, which is the point of it: what is under test there is the real
adapter's answer, so a helper standing in front of it would be measuring itself.
"""

from __future__ import annotations

from collections.abc import Sequence

from theurian.domain.ranking import Ranked, RetrieverPage


def truncating(rows: Sequence[Ranked], limit: int) -> RetrieverPage:
    """What a ``LIMIT``-bearing lookup would return for this ask."""
    return RetrieverPage(rows=tuple(rows[:limit]), exhausted=len(rows) <= limit)


def whole(rows: Sequence[Ranked]) -> RetrieverPage:
    """What a retriever that cannot bound its work returns: all of it, once."""
    return RetrieverPage(rows=tuple(rows), exhausted=True)
