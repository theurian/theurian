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
    An implementation that *prompts a model* discharges that by wrapping source
    text in a delimited untrusted region and never interpolating it into a
    system-role message. One that does not prompt anything discharges it by
    construction, having no instruction channel to confuse: the extractive
    default selects sentences from the children and never builds a prompt at
    all, so the obligation lands on the first abstractive adapter rather than
    on this one.
    """

    @property
    def model_id(self) -> str: ...

    @property
    def model_revision(self) -> str: ...

    @property
    def prompt_hash(self) -> str:
        """Hash of whatever decides what this implementation produces.

        For an implementation that prompts a model, that is the prompt. For one
        that builds no prompt, it is the identifier of its selection semantics:
        ``ExtractiveSummarizer`` hashes its ``SEMANTICS_VERSION``, because it
        has no prompt to hash and a summary node still has to know when the
        thing that produced it changed.

        Persisted per node so a change to either marks existing summaries stale
        deterministically rather than leaving an index that mixes two
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
