"""How much of each retriever's opinion is fused, and why fifty (FR-R2, FR-R4).

:data:`~theurian.application.retrieval_service.CANDIDATE_DEPTH` is the other half
of the depth story from ``tests/unit/test_retrieval_depth.py``. That file pins
the *security* claim — how many withheld rows the first pass absorbs before a
second read betrays them (T-17). This one pins the *relevance* claim, which is
the reason the constant is generous in the first place, stated in its own
docstring::

    RRF rewards a document both retrievers found, and a document the dense
    retriever ranked 30th cannot demonstrate agreement if only 10 were asked for.

That is falsifiable, so it is asserted rather than believed. Every number here
comes from that sentence — thirty — and none from the constant it bounds:
``CANDIDATE_DEPTH`` was moved to 5 and to 200 and the whole suite passed,
because every fixture that mentioned it sized itself from it.

The complementary claim, that a document found twice at rank thirty actually
*wins* against a strong single hit, belongs to ``RRF_K`` and is asserted in
``tests/unit/test_ranking.py`` over the pure fusion. Here the question is
narrower and is about the cut alone: can such a document reach the fusion at
all?

Pure: the index is a fake, the visibility withholds nothing, and no file is
opened.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import final

import pytest
from fakes import truncating, whole

from theurian.application.retrieval_service import (
    RetrievalService,
    SearchOutcome,
    SearchRequest,
)
from theurian.domain.chunking import IndexableChunk
from theurian.domain.ports.index_store import ForestRecompute
from theurian.domain.ranking import LEXICAL, SUBSTRING, Ranked, RetrieverPage
from theurian.domain.raptor import IndexableNode

pytestmark = pytest.mark.unit

#: Where the document under test sits in *both* retrievers' rankings.
#:
#: The number the constant's own docstring names. A depth of ten cannot reach it
#: — which is the sentence, restated as a fixture.
RANKED_AT = 30

#: How long each retriever's ranking is. One row past the document under test,
#: so it is genuinely mid-list rather than the last thing either retriever had
#: to say — a ranking that ended exactly at it would also be a ranking the
#: retriever had exhausted, and the loop treats those differently.
RANKING_LENGTH = RANKED_AT + 1

AGREED = "agreed-at-thirty"
SOLO = "solo-at-one"


def _row(chunk: str) -> Ranked:
    """One retriever's opinion about one chunk, in its own document.

    A distinct ``item_id`` per row on purpose: ``diversify`` caps how many chunks
    one document may contribute, and rows sharing a document would be dropped by
    that cap rather than by the depth this file is about.
    """
    return Ranked(chunk_id=f"{chunk}#0", item_id=f"item.{chunk}", revision_id=f"rev-{chunk}")


def _ranking(retriever: str, *, opens_with: str | None = None) -> tuple[Ranked, ...]:
    """A ranking with :data:`AGREED` at :data:`RANKED_AT`, padded either side.

    The padding is named per retriever, so the two rankings agree on exactly one
    row. A filler present in both would be an agreement of its own, and would
    take the top slot away from the row under test for a reason that has nothing
    to do with depth.
    """
    head = [_row(opens_with)] if opens_with else []
    filler = [_row(f"{retriever}-{position:02d}") for position in range(RANKING_LENGTH)]
    rows = [*head, *filler]
    rows[RANKED_AT - 1] = _row(AGREED)
    return tuple(rows[:RANKING_LENGTH])


@final
class _TwoOpinions:
    """An index whose two lexical retrievers disagree about everything but one row.

    ``limit`` is honoured as SQL honours it — fewer rows come back only when
    there are fewer to give — because the depth loop reads a short answer as
    "that is all there is" and a fake that ignored ``limit`` would terminate it
    for a reason the real store would not.
    """

    def __init__(self, lexical: tuple[Ranked, ...], substring: tuple[Ranked, ...]) -> None:
        self._lexical = lexical
        self._substring = substring

    def search_lexical(
        self,
        query: str,  # noqa: ARG002 - named by the port; the fixture answers, not the query
        *,
        project_id: str,  # noqa: ARG002 - single-project fake
        limit: int,
        include_unapproved: bool,  # noqa: ARG002 - the fixture holds only approved rows
    ) -> RetrieverPage:
        return truncating(self._lexical, limit)

    def search_substring(
        self,
        query: str,  # noqa: ARG002 - as above
        *,
        project_id: str,  # noqa: ARG002 - as above
        limit: int,
        include_unapproved: bool,  # noqa: ARG002 - as above
    ) -> RetrieverPage:
        return truncating(self._substring, limit)

    def search_dense(
        self,
        query_vector: Sequence[float],  # noqa: ARG002 - unreachable without an embedder
        *,
        project_id: str,  # noqa: ARG002 - as above
        include_unapproved: bool,  # noqa: ARG002 - as above
    ) -> RetrieverPage:
        return whole(())

    def chunk_texts(
        self,
        chunk_ids: Sequence[str],
        *,
        project_id: str,  # noqa: ARG002 - single-project fake
    ) -> Mapping[str, str]:
        return {chunk_id: f"passage of {chunk_id}" for chunk_id in chunk_ids}

    def create(self, *, index_build_id: str, state_hash: str) -> None:
        raise NotImplementedError

    def derive_purged(
        self,
        target: Path,
        *,
        revision_ids: Sequence[str],
        index_build_id: str,
        state_hash: str,
        recompute_forest: ForestRecompute | None = None,
    ) -> int:
        raise NotImplementedError

    def add_chunks(self, chunks: Sequence[IndexableChunk]) -> int:
        raise NotImplementedError

    def add_embeddings(self, vectors: Sequence[tuple[str, Sequence[float]]]) -> int:
        raise NotImplementedError

    def add_nodes(
        self,
        nodes: Sequence[IndexableNode],
        *,
        embedding_model: str,
        embedding_model_revision: str,
        embedding_dimension: int,
    ) -> int:
        raise NotImplementedError

    def add_node_embeddings(self, vectors: Sequence[tuple[str, Sequence[float]]]) -> int:
        raise NotImplementedError

    def record_embedding_model(self, *, model_id: str, dimension: int) -> None:
        raise NotImplementedError

    def metadata(self) -> Mapping[str, object]:
        return {}


@final
class _NothingIsWithheld:
    """A canonical store that has withdrawn nothing.

    The depth loop is being read here for what it *keeps*, not for what it
    hides, so withholding anything would put two effects on one measurement.
    """

    def cleared(self, ranked: Sequence[Ranked]) -> tuple[Ranked, ...]:
        return tuple(ranked)

    def at_moment(self, ranked: Sequence[Ranked]) -> tuple[Ranked, ...]:
        """No `asOf` in this file's use of it: nothing is pinned either."""
        return tuple(ranked)


def _search() -> SearchOutcome:
    """One ordinary search over two thirty-one-row rankings."""
    index = _TwoOpinions(
        lexical=_ranking(LEXICAL, opens_with=SOLO),
        substring=_ranking(SUBSTRING),
    )
    service = RetrievalService(index)

    return service.search(SearchRequest(query="gateway", project_id="demo"), _NothingIsWithheld())


def test_a_document_both_retrievers_ranked_thirtieth_still_reaches_the_fusion() -> None:
    """FR-R2. The relevance claim ``CANDIDATE_DEPTH`` is generous for.

    Fetching fifty rows per retriever costs a deeper read on every query, and the
    stated return on that cost is agreement found deep in both lists: "a document
    the dense retriever ranked 30th cannot demonstrate agreement if only 10 were
    asked for". A depth that no longer reaches thirty makes the sentence false
    and the extra rows pointless, and nothing in the suite noticed — the depth
    was cut by a factor of ten and every test passed, because each one sized its
    fixture from the constant.

    Both halves are asserted. That the row *arrives* is the depth's doing; that
    it arrives labelled ``foundBy`` both retrievers is what "demonstrate
    agreement" means, and it is the label a caller reads to decide how much to
    trust a hit (FR-R5).
    """
    outcome = _search()

    found = {candidate.chunk_id: candidate for candidate in outcome.candidates}
    assert f"{AGREED}#0" in found, "a depth shallower than thirty cannot see it at all"
    assert found[f"{AGREED}#0"].found_by == (LEXICAL, SUBSTRING)
    assert found[f"{AGREED}#0"].ranks == {LEXICAL: RANKED_AT, SUBSTRING: RANKED_AT}


def test_the_fixture_places_the_agreed_row_where_it_claims_to() -> None:
    """Guards the guard: the two rankings must really disagree everywhere else.

    If the padding were shared, the row under test would not be the only
    agreement, and the test above could pass on a document that reached the
    fusion for an entirely different reason. Asserted against the rankings
    themselves rather than the outcome, because that is where the mistake would
    be made.
    """
    lexical = _ranking(LEXICAL, opens_with=SOLO)
    substring = _ranking(SUBSTRING)

    shared = {row.chunk_id for row in lexical} & {row.chunk_id for row in substring}

    assert shared == {f"{AGREED}#0"}, "exactly one row may be common to both retrievers"
    assert lexical[RANKED_AT - 1].chunk_id == f"{AGREED}#0"
    assert substring[RANKED_AT - 1].chunk_id == f"{AGREED}#0"
    assert lexical[0].chunk_id == f"{SOLO}#0", "the single strong hit it has to outweigh"
