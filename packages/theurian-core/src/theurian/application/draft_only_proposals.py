"""A draft-only view onto :class:`ProposalService` for the write-intent MCP tools.

ADR-0032 decision 8's core security control. The write-intent tools
(``knowledge.proposeChange``, ``knowledge.generateMigrationDraft``) must be
**structurally incapable** of reaching a canonical or approved-state write:
holding an object whose reachable surface is exactly the two draft entries, and
that does not expose ``accept``, ``_commit`` or any approved-state mover -- not
as a method, and not through a stored ``ProposalService`` reference one attribute
hop away.

Structural incapacity beats a name-based blocklist: a blocklist has to enumerate
every mover and is wrong the moment one is added, while an object that never
holds ``accept`` cannot call it however the caller is spelled. The bytecode sweep
in ``tests/integration/test_mcp_tools.py`` reaches one level and so does not, by
itself, hold this property (decision 8's second bullet); this facade is what
does, and the built-server structural walk that checks it is slice B4 cluster 3's.

This facade **narrows** the surface. It does not reimplement drafting: both
entries delegate to the one :class:`ProposalService`, so ``propose accept`` and
these tools cannot come to disagree about identifiers, digests or landed sets
(ADR-0027's two-procedures-disagree defect).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, final

from theurian.application.proposal_service import (
    DraftedMigration,
    DraftedProposal,
    ProposalRequest,
)
from theurian.domain.proposal import Evidence


class DraftSurface(Protocol):
    """The two draft entries the facade forwards to, and the whole of what it names.

    :class:`ProposalService` satisfies this; so does a test double. The facade's
    constructor takes this rather than a concrete service, which is the type-level
    half of the incapacity -- the closure capture in :class:`DraftOnlyProposals`
    is the runtime half.
    """

    def draft(self, request: ProposalRequest, *, local: bool = ...) -> DraftedProposal: ...

    def draft_from_document(
        self, document: Mapping[str, object], *, evidence: Evidence, local: bool = ...
    ) -> DraftedMigration: ...


class _DraftEntry(Protocol):
    def __call__(self, request: ProposalRequest, *, local: bool = ...) -> DraftedProposal: ...


class _DocumentEntry(Protocol):
    def __call__(
        self, document: Mapping[str, object], *, evidence: Evidence, local: bool = ...
    ) -> DraftedMigration: ...


@final
class DraftOnlyProposals:
    """A facade whose reachable attribute set is the two draft entries alone.

    ``accept`` and ``_commit`` are not reachable: not as a method (this class
    declares neither), and not through a stored service. The constructor captures
    the two draft entries as **closures** rather than keeping the
    :class:`DraftSurface` -- a ``self._service = service`` attribute would put
    every mover on it one hop away, which is the anti-pattern the built-server
    walk is written to catch. The service survives only inside these closures'
    cells, which no attribute walk enumerates, so ``self._draft`` and
    ``self._draft_from_document`` are plain functions with no ``__self__`` back to
    a canonical writer.
    """

    __slots__ = ("_draft", "_draft_from_document")

    _draft: _DraftEntry
    _draft_from_document: _DocumentEntry

    def __init__(self, service: DraftSurface) -> None:
        draft = service.draft
        draft_from_document = service.draft_from_document

        def run_draft(request: ProposalRequest, *, local: bool = False) -> DraftedProposal:
            return draft(request, local=local)

        def run_draft_from_document(
            document: Mapping[str, object], *, evidence: Evidence, local: bool = False
        ) -> DraftedMigration:
            return draft_from_document(document, evidence=evidence, local=local)

        self._draft = run_draft
        self._draft_from_document = run_draft_from_document

    def draft(self, request: ProposalRequest, *, local: bool = False) -> DraftedProposal:
        """Draft a body-and-revision proposal. Delegates to :meth:`ProposalService.draft`."""
        return self._draft(request, local=local)

    def draft_from_document(
        self, document: Mapping[str, object], *, evidence: Evidence, local: bool = False
    ) -> DraftedMigration:
        """Land a caller-supplied migration document as a proposal.

        Delegates to :meth:`ProposalService.draft_from_document`, which holds the
        v1 operation-set gate.
        """
        return self._draft_from_document(document, evidence=evidence, local=local)
