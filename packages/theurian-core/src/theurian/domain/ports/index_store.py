"""IndexStore port: one retrieval index build (FR-R2, ADR-0003, ADR-0022).

**Returns values, never rows.** A ``sqlite3.Row`` crossing into the application
layer would make the ranking pipeline depend on one adapter's cursor semantics
and column names — the exact coupling the ports rule exists to prevent, and one
that ruff's banned-import check cannot catch, because no import is involved.

That is not hypothetical: the first version of this milestone typed its
collaborators as ``Any`` and did precisely that, which also defeated strict
mypy. A port with real types is what makes the rule enforceable rather than
merely stated.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol, runtime_checkable

from theurian.domain.chunking import IndexableChunk
from theurian.domain.ranking import Ranked


@runtime_checkable
class IndexStore(Protocol):
    """Writes and reads one index build."""

    def create(self, *, index_build_id: str, state_hash: str) -> None:
        """Create an empty index. Refuses to reuse an existing file.

        An index build is all-or-nothing: appending to a half-built one produces
        a file that looks complete and silently is not.
        """
        ...

    def add_chunks(self, chunks: Sequence[IndexableChunk]) -> int:
        """Insert chunks. Returns how many were written."""
        ...

    def add_embeddings(self, vectors: Sequence[tuple[str, Sequence[float]]]) -> int:
        """Store one vector per chunk. Returns how many were written."""
        ...

    def record_embedding_model(self, *, model_id: str, dimension: int) -> None:
        """Record which model produced the vectors.

        A query embedded by a different model than the corpus is comparable
        arithmetically and meaningless semantically; storing the model is what
        lets that be refused instead of scored.
        """
        ...

    def metadata(self) -> Mapping[str, object]:
        """What this build was made from and by."""
        ...

    def search_lexical(
        self,
        query: str,
        *,
        project_id: str,
        limit: int,
        include_unapproved: bool,
    ) -> tuple[Ranked, ...]:
        """Rank by term match, best first.

        Filtering happens with the match, before ranking (FR-R1). A malformed
        query returns nothing rather than raising: a search box that punishes
        punctuation is a broken search box.
        """
        ...

    def search_substring(
        self,
        query: str,
        *,
        project_id: str,
        limit: int,
        include_unapproved: bool,
    ) -> tuple[Ranked, ...]:
        """Rank by substring match, best first.

        The retriever that makes scripts without word boundaries searchable.
        """
        ...

    def search_dense(
        self,
        query_vector: Sequence[float],
        *,
        project_id: str,
        limit: int,
        include_unapproved: bool,
    ) -> tuple[Ranked, ...]:
        """Rank by vector similarity, best first.

        Returns nothing when there are no embeddings, which degrades the search
        to lexical rather than failing it.
        """
        ...

    def chunk_texts(self, chunk_ids: Sequence[str], *, project_id: str) -> Mapping[str, str]:
        """The matched passage per chunk, so a hit can show what matched.

        Scoped by project like every other read here (SEC-13). See
        :meth:`token_sizes` for why the scope is not left to the caller.
        """
        ...

    def token_sizes(self, chunk_ids: Sequence[str], *, project_id: str) -> Mapping[str, int]:
        """Token estimate per chunk, for packing to a budget (FR-R4).

        Sizes rather than texts: deciding whether a chunk fits should not
        require reading the chunk.

        ``project_id`` is required even though every id reaching here came from
        a search that was already scoped. Chunk ids are ``<revisionId>#<n>`` and
        revision ids are published in every result, so an id is guessable rather
        than opaque -- and this lookup is the step that turns an id back into
        text. A by-id read that trusts its ids is one refactor away from being
        the first unscoped read in the pipeline (SEC-13).
        """
        ...
