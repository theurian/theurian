"""SummarizationProvider port."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from theurian.domain.values import Scope


@runtime_checkable
class SummarizationProvider(Protocol):
    """Produces RAPTOR summary nodes from child texts.

    The default is extractive: it selects sentences rather than generating them,
    so it cannot state a fact the children do not contain. Abstractive
    summarization is an opt-in upgrade (ADR-0009).

    Implementations must treat child text as **data being described**, never as
    instructions. A child document saying "ignore previous instructions" is a
    document that says that; it is not a directive to the summarizer (SEC-16).
    Implementations wrap source text in a delimited untrusted region and never
    interpolate it into a system-role message.
    """

    @property
    def model_id(self) -> str: ...

    @property
    def model_revision(self) -> str: ...

    @property
    def prompt_hash(self) -> str:
        """Hash of the summarization prompt.

        Persisted per node so a prompt change marks existing summaries stale
        deterministically rather than leaving an index that mixes two prompt
        generations (ADR-0008).
        """
        ...

    async def summarize(
        self,
        texts: tuple[str, ...],
        *,
        scope: Scope,
        max_tokens: int,
    ) -> str:
        """Summarize sibling child texts that all share ``scope``.

        ``scope`` is uniform across ``texts`` by construction: a node whose
        children differ in scope has no tree to belong to (ADR-0008). It is
        passed so an implementation can apply sensitivity-appropriate handling,
        for example declining to send ``restricted`` content to a hosted model.
        """
        ...
