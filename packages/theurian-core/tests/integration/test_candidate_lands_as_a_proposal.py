"""A generated candidate is a proposal the shipped commands accept (ADR-0033).

ADR-0033 decision 1 routes the candidate "through ``ProposalService.draft()``
into an ordinary proposal directory -- ADR-0013 point 2's shape and nothing
special", and Compliance owes the test that says *ordinary* is true of the thing
that lands: **one ``theurian propose accept`` accepts it**, its ``trustLevel``
is ``inferred`` on the migration the acceptance moves into the set and in the
state that migration applies to, and ``author`` never crosses
``evidence.agentId``.

What this adds to the two files beside it
-----------------------------------------
``test_candidate_generation_on_disk.py`` holds ``trustLevel`` and the
author/agent split on the **drafted** migration, over a generator it composes
itself. That is the mapping's half. This is the other half, and the difference
is the two shipped commands in between: a proposal is only *ordinary* if the
acceptance path takes it -- the secret scan, the containment check and the dry
replay ``propose accept`` runs before anything moves -- and if the value
survives the move into ``.theurian/migrations/`` and then the apply. A drafted
migration nothing accepts proves the writer, not the contract.

``test_candidate_generation_wire.py`` drives the tool and finds the directory.
This drives the tool and then spends it.

Everything is the shipped path: the tool over the real transport, then
``theurian propose accept`` and ``theurian migrate apply`` through the CLI's own
Typer app, then ``knowledge.get`` over the transport again to read what the
acceptance actually landed. Nothing here touches the developer's machine -- the
project, its git repository and its data directory are ``review_candidate_project``'s,
under ``tmp_path``, and no service is registered and no port is bound.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Final

import pytest
import review_candidate_fixtures as corpus
import yaml
from review_candidate_project import ServedProject, run_cli, served_project

from theurian.daemon.runner import build_server

from mcp_wire_session import mcp_session  # isort: skip

pytestmark = pytest.mark.integration

TOOL: Final = "review.generateKnowledgeCandidate"

ITEM_ID: Final = "reliability.retry-lock-order"
BODY: Final = "Acquire locks after reads, never before, in retry-eligible paths.\n"

#: The human the migration schema's ``author`` means, and the agent
#: ``evidence.json`` records. Distinct strings that share no substring, so an
#: assertion that one is not the other cannot pass by accident.
HUMAN_AUTHOR: Final = "dana@example.com"
AGENT_ID: Final = "claude-code"


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ServedProject]:
    """The served project, built by ``review_candidate_project``.

    ``records`` and ``withheld`` are stated rather than defaulted, as the wire
    module's are: this module's subject is what a *visible* thread produces, so
    the corpus is the shipped one and the withholding posture is nothing
    withheld. ADR-0033 decision 5's two corpora are
    ``test_candidate_generation_absence_proof.py``'s.
    """
    yield from served_project(
        tmp_path, monkeypatch, records=corpus.evidence_records(), withheld=frozenset()
    )


def _generate(project: ServedProject, tmp_path: Path) -> dict[str, Any]:
    """Generate one candidate over the wire and answer the tool's own payload."""
    arguments = {
        "projectId": "demo",
        "repository": corpus.REPOSITORY,
        "recordKey": corpus.THREAD_SATISFYING,
        "fixCommit": project.verifying,
        "itemId": ITEM_ID,
        "title": "Acquire locks after reads in retry-eligible paths",
        "body": BODY,
        "kind": "convention",
        "category": "reliability-rule",
        "owner": "platform-team",
        "author": HUMAN_AUTHOR,
        "description": "Generalise the deadlock thread into a locking rule",
        "evidence": {
            "agentId": AGENT_ID,
            "taskId": "task-431",
            "model": "claude-opus-5",
            "reasoning": "The thread settled the lock ordering; this generalises it.",
        },
        "sourceAnchors": [
            {
                "provider": "github",
                "sourceUri": (
                    f"https://github.com/{corpus.REPOSITORY}/pull/"
                    f"{corpus.PULL_REQUEST_CI_PASSED}#discussion_r1"
                ),
                "repository": corpus.REPOSITORY,
                "filePath": corpus.FILE_PATH,
            }
        ],
    }
    with mcp_session(build_server(project.registry), tmp_path / "daemon") as call:
        answer = call(TOOL, arguments)

    result = answer["result"]
    assert result["isError"] is False, result
    payload: dict[str, Any] = result["structuredContent"]
    return payload


def _document(path: Path) -> dict[str, Any]:
    parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict), f"{path} is not a migration document: {parsed!r}"
    return parsed


def _upsert(document: dict[str, Any]) -> dict[str, Any]:
    operations = document["operations"]
    assert isinstance(operations, list)
    found: dict[str, Any] = next(op for op in operations if op["op"] == "upsertRevision")
    return found


def test_the_shipped_propose_accept_accepts_a_generated_candidate(
    project: ServedProject, tmp_path: Path
) -> None:
    """ADR-0033 decision 1: *an ordinary proposal directory, and nothing special*.

    The acceptance is where that claim is cashed. ``propose accept`` runs the
    secret scan over everything it would land, resolves the ``contentFile``
    through the containment check, and replays the whole migration set with this
    proposal in it against a throwaway store before a single file moves
    (ADR-0027). A proposal that is ordinary in shape but fails any of those is
    not one a reviewer can merge, and nothing before this point would have said
    so.

    The move itself is asserted on all three of its parts, because a partial one
    is the failure that reads as success: the migration lands in the set under
    the name it already had, the body lands where its ``contentFile`` pointed,
    and the proposal's own copies are consumed so a second acceptance cannot
    find them.

    **What stays behind is ``evidence.json``, and that is a recorded decision,
    not a leftover** (#361). ``_remove_proposal_sources`` deletes the migration
    and the bodies and deliberately keeps the evidence record, because reading
    it is how a re-accept is told from a first one -- the question whose wrong
    answer mints a duplicate migration (#89). The exact remaining **file** set is
    pinned rather than the directory's absence, so that decision is held here
    instead of being something a reader has to rediscover.

    Files, not entries: the body's own parent directory inside the proposal
    survives the unlink empty, because ``_remove_proposal_sources`` removes
    files and not the directories they sat in. That is residue rather than a
    second acceptance -- nothing re-acceptable is in it -- and it is not this
    path's: every proposal carrying a body lands it under a directory
    ``body_relative_path`` derives, so any ``theurian propose`` leaves the same
    shape behind.
    """
    payload = _generate(project, tmp_path)
    proposal_id = payload["proposalId"]
    proposal = project.root / ".theurian/proposals" / proposal_id
    landed_migration = project.root / ".theurian/migrations" / payload["migrationFile"]
    landed_body = project.root / payload["bodyDestination"]

    assert proposal_id in project.proposals(), payload
    assert not landed_migration.exists(), "the migration is in the set before the acceptance"

    run_cli("propose", "accept", proposal_id)

    assert landed_migration.is_file(), (
        f"`propose accept` did not move the migration into the set: {landed_migration} is absent"
    )
    assert landed_body.read_text(encoding="utf-8") == BODY, (
        f"the body did not land at the path its `contentFile` named ({landed_body}), "
        f"or it landed with text the caller did not write"
    )
    remaining = {
        found.relative_to(proposal).as_posix() for found in proposal.rglob("*") if found.is_file()
    }
    assert remaining == {"evidence.json"}, (
        f"the proposal directory still holds {sorted(remaining)} after its acceptance. "
        f"The migration and the body are consumed; the evidence record stays, because "
        f"it is what tells a re-accept from a first one (#361). A surviving migration "
        f"copy is a second acceptance waiting to mint a duplicate."
    )


def test_the_accepted_candidates_trust_level_is_inferred_on_the_migration_and_in_the_state(
    project: ServedProject, tmp_path: Path
) -> None:
    """ADR-0033 decision 1: ``trustLevel: inferred``, held past the two commands.

    ``KnowledgeCandidate.trust_level`` is ``init=False``, so the in-memory value
    cannot be wrong; the file can, and so can everything after it. This follows
    the value through both places it could quietly become ``unverified``: the
    migration as it sits **in the applied set**, and the item ``knowledge.get``
    serves once that set has been applied.

    Reading it back through the tool rather than off the database is the point.
    ``inferred`` is a claim a reviewer acts on -- it says a machine proposed this
    and a human has not yet graded it -- and the surface that makes the claim to
    an agent is this one. A migration that said ``inferred`` while the served
    item said something else would be the same defect one layer later.

    ``includeUnapproved`` is deliberately **not** passed: a drafted migration
    writes ``status: approved`` because the merge is the approval (ADR-0013), so
    the item is reachable on the ordinary path, and a test that reached for the
    flag would be hiding a status regression behind it.
    """
    payload = _generate(project, tmp_path)
    run_cli("propose", "accept", payload["proposalId"])
    run_cli("migrate", "apply")

    document = _document(project.root / ".theurian/migrations" / payload["migrationFile"])
    with mcp_session(build_server(project.registry), tmp_path / "read") as call:
        answer = call("knowledge.get", {"projectId": "demo", "itemId": ITEM_ID})

    assert _upsert(document)["metadata"]["trustLevel"] == "inferred", (
        f"the migration in the applied set records trust level "
        f"{_upsert(document)['metadata'].get('trustLevel')!r}. A candidate cannot be "
        f"constructed with any other value, so this is the mapping or the move "
        f"dropping it -- and an absent key is applied as `unverified`, which claims "
        f"less trust than the candidate carried."
    )
    result = answer["result"]
    assert result["isError"] is False, result
    served = result["structuredContent"]
    assert served["itemId"] == ITEM_ID, served
    assert served["trustLevel"] == "inferred", (
        f"the accepted item is served as {served['trustLevel']!r}. The written "
        f"migration and the state a caller reads must agree, or the trust label the "
        f"reviewer graded is not the one the agent is handed."
    )


def test_the_accepted_migration_names_the_human_and_the_proposal_named_the_agent(
    project: ServedProject, tmp_path: Path
) -> None:
    """The migration schema's ``author`` rule, held across the acceptance.

    The schema defines ``author`` as the human who authored the change and says
    agent-generated proposals record the agent separately in ``evidence.json``;
    ADR-0033 decision 1 records that the two are never filled from each other.
    An agent id where a reviewer looks for a person makes an unattributable
    change look attributed, and a person's address in the provenance record
    names a human as the run.

    Both are read where each survives: ``evidence.json`` lives in the proposal
    directory and is consumed by the acceptance, so it is read before; the
    migration is read **after**, out of the applied set, because that is the
    copy a reviewer opens in the pull request. The last assertion is the one
    that makes the pair mean something -- the agent's id must not appear
    anywhere in the migration, so a build that filled ``author`` from the
    evidence would redden even if it also happened to write the human's address
    somewhere else in the document.
    """
    payload = _generate(project, tmp_path)
    proposal = project.root / ".theurian/proposals" / payload["proposalId"]
    evidence = json.loads((proposal / "evidence.json").read_text(encoding="utf-8"))

    run_cli("propose", "accept", payload["proposalId"])

    document = _document(project.root / ".theurian/migrations" / payload["migrationFile"])

    assert evidence["agentId"] == AGENT_ID, evidence
    assert document["author"] == HUMAN_AUTHOR, (
        f"the accepted migration names author {document['author']!r}; the submission "
        f"named {HUMAN_AUTHOR!r} as the human and {AGENT_ID!r} as the agent"
    )
    assert AGENT_ID not in yaml.safe_dump(document), (
        f"the agent id {AGENT_ID!r} appears in the accepted migration. It belongs in "
        f"`evidence.json`, which records the run; the migration records the human "
        f"whose name a reviewer reads as the author of the change."
    )
