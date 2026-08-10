"""What ``cleared[:CANDIDATE_DEPTH]`` holds, on a corpus deep enough to need it.

The closure argument this milestone rests on is stated in one line in
:mod:`theurian.application.retrieval_service`: **one query against two corpora —
an index holding documents the caller may not read, and an index that never held
them — must produce the same answer** (SEC-13, T-15). The line that carries it in
the code is the cut at the end of
:meth:`~theurian.application.retrieval_service.RetrievalService._visible_ranking`::

    return cleared[:CANDIDATE_DEPTH]

Replacing it with ``return cleared`` left **all 1,407 tests that existed before
this file passing**, while reopening the leak class the milestone closed: measured
across two corpora differing only in whether ten withdrawn documents remained in
the index, a *visible* document's ``fusedScore`` moved from ``0.01639344`` to
``0.02284506`` and its ``foundBy`` from ``['substring']`` to ``['lexical',
'substring']``. The withdrawn rows had taken ten of the word index's top hundred
slots and pushed it out of reach. That mutation now fails here, in six of the six
parametrisations below and nowhere else in the suite.

**Why the integration fixture cannot see that, and this file exists.**
``three_indexes`` (``tests/integration/test_mcp_tools.py``) builds 56 chunks
against a :data:`FIRST_PASS_DEPTH` of 100, so every retriever is *exhausted* on
its first pass and the cut trims 55 rows to 50 identically in both corpora. The
cut separates two corpora only where a retriever returns more cleared rows than
:data:`CANDIDATE_DEPTH` — that is, where the matching chunks outnumber
:data:`FIRST_PASS_DEPTH`. This corpus does: :data:`LEXICAL_MATCHES` rows against a
first pass of a hundred.

**Where the difference lands is designed, not hoped for.** A row that drops off
the tail of a fused ranking is invisible to a caller, who never sees past
``MAX_RESULTS``. So :data:`DEEP` is a *visible* document the substring retriever
ranks first and the word index ranks ninety-fifth: whether the word index reaches
it decides its ``fusedScore`` and its ``foundBy``, and it is published at the head
of the ranking either way. The equality asserted below is therefore an equality a
caller can read off a response, not one buried past the cut.

Pure: the index is a fake, the visibility is a fake, and no file is opened. The
corpus shape is a fake's business — what is real is
:meth:`RetrievalService.search`, the depth loop, the cut, the fusion and
``diversify``, all of which run here exactly as they run against SQLite.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, final

import pytest
from fakes import truncating, whole

from theurian.application.retrieval_service import (
    CANDIDATE_DEPTH,
    FIRST_PASS_DEPTH,
    RetrievalService,
    SearchOutcome,
    SearchRequest,
)
from theurian.domain.chunking import IndexableChunk
from theurian.domain.ranking import Ranked, RetrieverPage

pytestmark = pytest.mark.unit

#: How many rows the word index has to offer. Four times
#: :data:`FIRST_PASS_DEPTH`, so the first pass truncates and the deepest pass
#: reached below still leaves rows unread — a retriever that ran out would exit
#: the loop for a reason that has nothing to do with the cut.
LEXICAL_MATCHES = 400

WITHHELD = "withheld"
VISIBLE = "visible"

#: The visible document the two retrievers disagree about, by ordinal in the
#: word index. Deep enough that ten withdrawn rows above it push it past a
#: hundred-row first pass, shallow enough that a hundred-row pass over a corpus
#: with nothing withheld still reaches it. Both edges matter: outside that band
#: the two corpora agree about it and the mutation this file exists for goes
#: unnoticed.
DEEP_ORDINAL = 94

#: Withheld counts that must all leave the answer untouched. Chosen to walk the
#: depth loop rather than to bracket it: 1 and 10 are answered in one pass, 99
#: sits on the last row a first pass can absorb, and 150 and 349 force a second
#: and a third. The leak, when it is present, is present at every one of them.
WITHHELD_COUNTS = (1, 10, 60, 99, 150, 349)

#: The count the measurement in the module docstring was taken at, used where a
#: single corpus is needed rather than the whole ladder.
HEADLINE_WITHHELD = 10


def _row(item: str) -> Ranked:
    """One retriever's opinion about one chunk, in its own document.

    A distinct ``item_id`` per row: ``diversify`` caps how many chunks one
    document may contribute, and rows sharing a document would be dropped by that
    cap rather than by the cut this file is about.
    """
    return Ranked(chunk_id=f"{item}#0", item_id=item, revision_id=f"rev-{item}")


def _visible(ordinal: int) -> str:
    return f"{VISIBLE}-{ordinal:04d}"


DEEP = _visible(DEEP_ORDINAL)
DEEP_CHUNK = f"{DEEP}#0"


def _lexical_ranking(withheld: int) -> tuple[Ranked, ...]:
    """The word index's answer: ``withheld`` withdrawn rows, then the visible ones.

    Withdrawn rows on top because that is the shape the gate exists for — a
    document retracted after the index was built still ranks where the index put
    it, and a query written to match it ranks it high.
    """
    return tuple(_row(f"{WITHHELD}-{n:04d}") for n in range(withheld)) + tuple(
        _row(_visible(n)) for n in range(LEXICAL_MATCHES)
    )


#: The substring retriever's whole answer, identical in both corpora.
#:
#: Two rows and no filler, because this retriever is not where the difference is:
#: it exists to put :data:`DEEP` at the head of the fusion, so that what the word
#: index does or does not know about that document is visible in a published
#: field rather than in the tail of a ranking nobody reads.
SUBSTRING_RANKING = (_row(DEEP), _row(_visible(0)))


@final
class _TwoRankings:
    """A word index that truncates at ``limit``, and a fixed substring ranking.

    ``limit`` is honoured the way SQL honours it — fewer rows come back only when
    there are fewer to give — because the depth loop reads a short answer as
    "that is all there is". A fake that ignored ``limit`` would exit the loop for
    a reason the real store would not.
    """

    def __init__(self, withheld: int) -> None:
        self._lexical = _lexical_ranking(withheld)

    def search_lexical(
        self,
        query: str,  # noqa: ARG002 - named by the port; the corpus answers, not the query
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
        return truncating(SUBSTRING_RANKING, limit)

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
    ) -> int:
        raise NotImplementedError

    def add_chunks(self, chunks: Sequence[IndexableChunk]) -> int:
        raise NotImplementedError

    def add_embeddings(self, vectors: Sequence[tuple[str, Sequence[float]]]) -> int:
        raise NotImplementedError

    def record_embedding_model(self, *, model_id: str, dimension: int) -> None:
        raise NotImplementedError

    def metadata(self) -> Mapping[str, object]:
        return {}


@final
class _WithoutTheWithheld:
    """The canonical store's answer once those documents have been withdrawn."""

    def cleared(self, ranked: Sequence[Ranked]) -> tuple[Ranked, ...]:
        return tuple(row for row in ranked if not row.item_id.startswith(WITHHELD))

    def at_moment(self, ranked: Sequence[Ranked]) -> tuple[Ranked, ...]:
        """No `asOf` in this file's scope: nothing is pinned, so nothing moves."""
        return tuple(ranked)


def _search(withheld: int) -> SearchOutcome:
    """One ordinary search against a corpus withholding ``withheld`` top rows.

    ``per_item=1`` because that is what ``theurian.mcp.search`` fixes: one result
    per document, collapsed in the ranking rather than on the results.
    """
    service = RetrievalService(_TwoRankings(withheld))

    return service.search(
        SearchRequest(query="gateway", project_id="demo", per_item=1), _WithoutTheWithheld()
    )


def _published(outcome: SearchOutcome) -> list[dict[str, Any]]:
    """Everything a response is built out of, for one candidate list.

    Every field here reaches the wire: ``fusedScore`` and ``foundBy`` are
    published per hit, the order decides positions, and the passage becomes the
    ``excerpt``. Compared as a whole rather than field by field, because the two
    faces this closed last were *which rows* reached a field and *which paragraph*
    of a visible document was excerpted — neither of which any single field
    assertion states.
    """
    return [
        {
            "chunk_id": candidate.chunk_id,
            "item_id": candidate.item_id,
            "fused_score": candidate.fused_score,
            "found_by": list(candidate.found_by),
            "ranks": dict(candidate.ranks),
            "passage": outcome.passages.get(candidate.chunk_id),
        }
        for candidate in outcome.candidates
    ]


def _first_pass(withheld: int) -> tuple[Ranked, ...]:
    """What the word index hands back to one pass of the depth loop."""
    return (
        _TwoRankings(withheld)
        .search_lexical(
            "gateway", project_id="demo", limit=FIRST_PASS_DEPTH, include_unapproved=False
        )
        .rows
    )


@pytest.mark.parametrize(
    "withheld", WITHHELD_COUNTS, ids=[f"{count}-withheld" for count in WITHHELD_COUNTS]
)
def test_a_withheld_document_changes_nothing_the_ranking_publishes(withheld: int) -> None:
    """SEC-13, T-15. The closure argument, on the corpus shape that can break it.

    One query, two corpora: one whose index holds rows the caller may not read,
    one that never held them. Every value the response is built from must be
    equal — the identity and order of every candidate, its ``fusedScore``, its
    ``foundBy``, the ranks behind it and the passage that becomes its excerpt.

    The cut is the only thing that makes this true here. Without it the visible
    rows a retriever hands to the fusion are *however many survived the gate*,
    which is a function of what was withheld: at ten withdrawn rows the word
    index reaches ``visible-0089`` in the probe corpus and ``visible-0099`` in the
    one that never held them, and :data:`DEEP` — a document the caller may read,
    published at the head of the ranking by the substring retriever — is inside
    one and outside the other. Its ``foundBy`` and its ``fusedScore`` then answer
    a question about a document the caller cannot see, one query at a time.

    Parametrised across the depth loop rather than at one point: a corpus that
    needs two or three passes reaches the cut with a different ``cleared`` each
    time, and the equality has to survive all of them.
    """
    probe = _search(withheld=withheld)
    absent = _search(withheld=0)

    assert probe.candidates, "a comparison of two empty rankings proves nothing"
    assert DEEP_CHUNK in {candidate.chunk_id for candidate in absent.candidates[:5]}, (
        "the document the corpora can disagree about must be published near the "
        "head, or this compares two tails no caller reads"
    )
    assert _published(probe) == _published(absent)


def test_the_corpus_outlasts_a_first_pass_and_hides_the_deep_document_behind_it() -> None:
    """Guards the guard: proves this fixture can violate the invariant.

    Three preconditions, none of which any assertion above would notice losing.
    The word index must **truncate** at :data:`FIRST_PASS_DEPTH` — a retriever
    that hands back everything it has makes the cut a no-op and every equality
    above vacuous, which is exactly the state ``three_indexes`` is in and the
    reason this file exists. The withdrawn rows must sit **inside** that first
    pass, or they displace nothing. And :data:`DEEP` must fall on opposite sides
    of it in the two corpora, because that is the whole mechanism: the same pass
    depth reaches a different visible row when ten of the rows it read are
    withheld.

    Read off the fake's rankings rather than off an outcome, because that is
    where the mistake would be made — and read at :data:`FIRST_PASS_DEPTH`, the
    depth the pipeline actually asks for, rather than at :data:`CANDIDATE_DEPTH`,
    which is what it keeps.
    """
    probe = _first_pass(withheld=HEADLINE_WITHHELD)
    absent = _first_pass(withheld=0)

    assert len(probe) == len(absent) == FIRST_PASS_DEPTH, (
        "the word index must truncate at the first pass, or the cut trims nothing"
    )
    assert sum(1 for row in probe if row.item_id.startswith(WITHHELD)) == HEADLINE_WITHHELD, (
        "every withdrawn row must be read by the first pass, or none is displacing anything"
    )
    assert DEEP not in [row.item_id for row in probe]
    assert DEEP in [row.item_id for row in absent], (
        "the two corpora must disagree about this document at the pass depth, or "
        "there is nothing for the cut to hide"
    )
    assert CANDIDATE_DEPTH < FIRST_PASS_DEPTH, (
        "a cut that keeps everything a pass reads cuts nothing"
    )
