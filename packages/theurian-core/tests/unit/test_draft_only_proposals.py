"""The draft-only facade and the v1 operation-set partition (ADR-0032 decisions 3, 8).

The facade is B4's core security control: the write-intent tools hold an object
that *cannot* reach an approved-state write, and this pins that as a structural
property rather than a promise. The built-server structural walk and the
planted-``accept``-goes-RED driving test are cluster 3's; these are the
in-isolation properties -- that the facade forwards the two draft entries and
exposes nothing that moves approved state, and that the v1 operation set is a
deliberate partition of ``OperationKind``.
"""

from __future__ import annotations

import types
from collections.abc import Mapping
from pathlib import Path

import pytest

from theurian.application.draft_only_proposals import DraftOnlyProposals, DraftSurface
from theurian.application.proposal_service import (
    _REFUSED_TO_CLI,
    _REFUSED_TO_CONTENT_PATH,
    V1_OPERATION_KINDS,
    DraftedMigration,
    DraftedProposal,
    ProposalError,
    ProposalRequest,
    _refuse_operations_outside_the_v1_set,
)
from theurian.domain.enums import KnowledgeKind
from theurian.domain.identifiers import (
    AgentId,
    ItemId,
    MigrationId,
    ProposalId,
    RevisionId,
    TaskId,
)
from theurian.domain.knowledge import AUTHORED_IN_THEURIAN
from theurian.domain.migration import OperationKind
from theurian.domain.proposal import Evidence
from theurian.domain.values import MARKDOWN, ContentHash

_ULID = "01BX5ZZKBKACTAV9WEVGEMMVRZ"

_DRAFTED_PROPOSAL = DraftedProposal(
    proposal_id=ProposalId(_ULID),
    directory=Path("/proposals") / _ULID,
    migration_id=MigrationId(_ULID),
    migration_file=Path("/proposals") / _ULID / f"{_ULID}-x.yaml",
    revision_id=RevisionId(_ULID),
    expected_revision=None,
    body_file=Path("/proposals") / _ULID / "body.md",
    evidence_file=Path("/proposals") / _ULID / "evidence.json",
    content_file="../knowledge/x.md",
    content_sha256=ContentHash.of_bytes(b"x"),
    body_destination=Path("/knowledge/x.md"),
)

_DRAFTED_MIGRATION = DraftedMigration(
    proposal_id=ProposalId(_ULID),
    directory=Path("/proposals") / _ULID,
    migration_id=MigrationId(_ULID),
    migration_file=Path("/proposals") / _ULID / f"{_ULID}-x.yaml",
    evidence_file=Path("/proposals") / _ULID / "evidence.json",
    operations=(OperationKind.DEPRECATE_ITEM,),
)


class _SpyService:
    """A stand-in with the two draft entries the facade needs -- and the two
    approved-state movers it must never surface. If the facade held the service,
    ``accept``/``_commit`` would ride out on it, which is the reachability these
    tests deny.
    """

    def __init__(self) -> None:
        self.draft_calls: list[tuple[ProposalRequest, bool]] = []
        self.document_calls: list[tuple[Mapping[str, object], Evidence, bool]] = []

    def draft(self, request: ProposalRequest, *, local: bool = False) -> DraftedProposal:
        self.draft_calls.append((request, local))
        return _DRAFTED_PROPOSAL

    def draft_from_document(
        self, document: Mapping[str, object], *, evidence: Evidence, local: bool = False
    ) -> DraftedMigration:
        self.document_calls.append((document, evidence, local))
        return _DRAFTED_MIGRATION

    def accept(self, proposal_id: ProposalId) -> None:  # noqa: ARG002 - spy, must stay unreachable
        raise AssertionError("accept was reached through the draft-only facade")  # pragma: no cover

    def _commit(self) -> None:  # pragma: no cover - must be unreachable
        raise AssertionError("_commit was reached through the draft-only facade")


def _facade() -> tuple[DraftOnlyProposals, _SpyService]:
    spy = _SpyService()
    # `_SpyService` satisfies the `DraftSurface` protocol structurally; the
    # annotation documents the contract the facade constructor holds.
    surface: DraftSurface = spy
    return DraftOnlyProposals(surface), spy


def test_the_facade_exposes_exactly_the_two_draft_entries() -> None:
    facade, _ = _facade()
    assert callable(facade.draft)
    assert callable(facade.draft_from_document)


@pytest.mark.parametrize("mover", ["accept", "_commit", "_service"])
def test_the_facade_does_not_expose_an_approved_state_mover(mover: str) -> None:
    facade, _ = _facade()
    assert not hasattr(facade, mover), (
        f"{mover} is reachable on the facade -- the write-intent tools could reach an "
        "approved-state write through it (ADR-0032 decision 8)"
    )


def test_the_facade_holds_no_bound_method_back_to_the_service() -> None:
    # The captured entries are plain functions, not bound methods: a bound
    # `service.accept` would carry `__self__` back to a canonical writer, so the
    # facade stores closures whose service lives in a cell no attribute walk
    # enumerates.
    facade, _ = _facade()
    for entry in (facade._draft, facade._draft_from_document):
        assert isinstance(entry, types.FunctionType)
        assert not hasattr(entry, "__self__"), (
            "a stored entry exposes __self__ -- accept/_commit are one hop away through it"
        )


def test_no_reachable_attribute_of_the_facade_is_named_for_an_approved_state_mover() -> None:
    facade, _ = _facade()
    reachable = {name for name in dir(facade) if not name.startswith("__")}
    assert reachable == {"_draft", "_draft_from_document", "draft", "draft_from_document"}, (
        f"the facade's reachable surface grew beyond the two draft entries: {reachable}"
    )


def test_the_facade_forwards_draft_to_the_service() -> None:
    facade, spy = _facade()
    request = _proposal_request()

    result = facade.draft(request, local=True)

    assert result is _DRAFTED_PROPOSAL
    assert spy.draft_calls == [(request, True)]


def test_the_facade_forwards_draft_from_document_to_the_service() -> None:
    facade, spy = _facade()
    document: Mapping[str, object] = {"operations": [{"op": "deprecateItem", "itemId": "a.b"}]}
    evidence = _evidence()

    result = facade.draft_from_document(document, evidence=evidence, local=True)

    assert result is _DRAFTED_MIGRATION
    assert spy.document_calls == [(document, evidence, True)]


def test_the_v1_operation_set_partitions_operation_kind() -> None:
    # The refused sets and the admitted set are a *partition* of OperationKind:
    # every kind is classified exactly once. A fifteenth kind added to the enum
    # lands in none of them and reddens here, forcing a deliberate admit-or-refuse
    # rather than an admit-by-omission (ADR-0032 decision 3).
    refused = _REFUSED_TO_CONTENT_PATH | _REFUSED_TO_CLI
    all_kinds = frozenset(OperationKind)
    content = {OperationKind.CREATE_ITEM, OperationKind.UPSERT_REVISION}
    cli = {OperationKind.CHANGE_SENSITIVITY, OperationKind.RESTORE_ITEM}
    assert V1_OPERATION_KINDS.isdisjoint(refused)
    assert _REFUSED_TO_CONTENT_PATH.isdisjoint(_REFUSED_TO_CLI)
    assert V1_OPERATION_KINDS | refused == all_kinds
    assert len(V1_OPERATION_KINDS) == 10
    assert content == _REFUSED_TO_CONTENT_PATH
    assert cli == _REFUSED_TO_CLI


def test_a_kind_routed_into_no_set_is_refused_fail_closed_not_redirected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RED means an unrouted OperationKind is silently dropped instead of refused.

    The partition test above keeps every current kind in exactly one of the three
    sets, so no kind reaches the fail-closed ``raise`` at the tail of
    :func:`_refuse_operations_outside_the_v1_set` today -- which is why a pre-empt
    mutation of that ``raise`` to ``continue`` survived the whole suite: the branch
    is unreachable by construction, and an unreachable ``raise`` and an unreachable
    ``continue`` are the same behaviour. This drives it by construction. It drops
    one admitted kind out of ``V1_OPERATION_KINDS`` so that kind routes into none of
    the three sets -- exactly what an unrouted fifteenth ``OperationKind`` would do
    -- and feeds a document carrying it.

    The refusal must be the fail-closed one, told apart from its two redirect
    siblings by the message it carries: the generic ``does not carry {op} in v1.``
    with the ``theurian migrate apply`` remedy, not the content-path redirect
    (which speaks of a body the kind moves) nor the CLI redirect (which speaks of a
    read boundary it changes). Both siblings name an onward path an unrouted kind
    has none of, so asserting the exact message is what proves the fail-closed arm
    fired rather than one of them -- and under the ``continue`` mutation the kind is
    dropped, no error is raised, and this goes RED (ADR-0032 decision 3).
    """
    unrouted = OperationKind.DEPRECATE_ITEM
    monkeypatch.setattr(
        "theurian.application.proposal_service.V1_OPERATION_KINDS",
        V1_OPERATION_KINDS - {unrouted},
    )
    document: Mapping[str, object] = {"operations": [{"op": unrouted.value, "itemId": "a.b"}]}

    with pytest.raises(ProposalError) as caught:
        _refuse_operations_outside_the_v1_set(document)

    error = caught.value
    assert str(error) == f"generateMigrationDraft does not carry {unrouted.value} in v1.", (
        "the unrouted kind was refused, but with a redirect sibling's message rather than the "
        "fail-closed one -- a future kind would be sent to a content or CLI path it does not "
        "belong to"
    )
    assert error.remedy == (
        f"Author the {unrouted.value} operation as a migration and apply it with "
        "`theurian migrate apply` once a human has reviewed it."
    ), error.remedy


def _evidence() -> Evidence:
    return Evidence(
        agent_id=AgentId("claude-code"),
        task_id=TaskId("task-1"),
        model="claude-opus-5",
        reasoning="The v1 set carries this operation.",
        anchors=(),
    )


def _proposal_request() -> ProposalRequest:
    return ProposalRequest(
        item_id=ItemId("architecture.retry-policy"),
        title="Retry policy",
        kind=KnowledgeKind.ARCHITECTURE,
        owner="platform-team",
        author="platform-team@example.com",
        description="Record the retry budget.",
        body="# Retry policy\n\nThree attempts.\n",
        content_type=MARKDOWN,
        evidence=_evidence(),
        labels=(AUTHORED_IN_THEURIAN,),
    )
