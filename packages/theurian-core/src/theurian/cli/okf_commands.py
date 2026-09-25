"""``theurian okf`` -- the Open Knowledge Format interchange (ADR-0037).

A composition root: where the gated import meets ``ProposalService`` and the
published JSON Schemas, the same wiring ``propose_commands.py`` uses
(ADR-0003). Nothing below this module names a concrete adapter beyond these.

**A write/maintenance path, like ``index build`` and ``findings build``, and
it serves nothing.** ``import`` is a gated on-ramp to the same write path
``theurian propose`` uses -- every drafted proposal lands under
``.theurian/proposals/`` and reaches approved state only through ``theurian
propose accept``, a pull request, and a human merge (ADR-0013), unchanged.

Shared with the export verb: the sub-app is intentionally minimal and
generic, so an ``export`` command joins ``okf`` here rather than starting a
second module.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Final

import typer

from theurian.application.draft_only_proposals import DraftOnlyProposals
from theurian.application.okf_import import (
    ImportRefusal,
    OkfImportError,
    OkfImportRequest,
    OkfImportResult,
    OkfImportService,
)
from theurian.application.project_service import ProjectPathEscapeError
from theurian.application.proposal_service import ProposalService
from theurian.cli.context import CommandContext, schema_root
from theurian.cli.migration_pipeline import rehearse_migration_set
from theurian.domain.errors import TheurianError
from theurian.domain.identifiers import AgentId, TaskId
from theurian.domain.migration import current_revision_in
from theurian.domain.proposal import Evidence
from theurian.infrastructure.filesystem.migration_loader import validate_migration_document

#: Exit code for an invocation that cannot be used as given, matching
#: ``propose_commands.py``'s own (the group shape that motivates it there does
#: not apply here, but the code is the CLI's published meaning for this class
#: of failure, not a per-command choice).
EXIT_INVALID_INPUT: Final = 2

okf_app = typer.Typer(
    help="Export or import this project's knowledge in Open Knowledge Format.",
    no_args_is_help=True,
)

JsonOption = Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")]


@dataclass(frozen=True, slots=True)
class _Inputs:
    bundle: Path
    owner: str
    author: str
    agent_id: str
    task_id: str
    model: str
    reasoning: str
    item: tuple[str, ...]


@okf_app.command("import")
def okf_import(  # noqa: PLR0913 -- one option per drafted field, all keyword-only
    bundle: Annotated[
        Path, typer.Argument(help="Path to an unpacked OKF bundle directory, already on disk.")
    ],
    *,
    owner: Annotated[
        str, typer.Option("--owner", help="Accountable party recorded on every drafted item.")
    ],
    author: Annotated[
        str, typer.Option("--author", help="Human author recorded on every drafted migration.")
    ],
    agent_id: Annotated[str, typer.Option("--agent-id", help="Which agent ran this import.")],
    task_id: Annotated[
        str, typer.Option("--task-id", help="Unit of work this import came out of.")
    ],
    model: Annotated[str, typer.Option("--model", help="Model that ran this import.")],
    reasoning: Annotated[
        str, typer.Option("--reasoning", help="Why this bundle is being imported.")
    ],
    item: Annotated[
        list[str] | None,
        typer.Option(
            "--item",
            help=(
                "Import only this concept's item id, matched by its bundle path first. Repeatable."
            ),
        ),
    ] = None,
    as_json: JsonOption = False,
) -> None:
    """Draft a reviewable proposal per concept from a local OKF bundle.

    Every admitted concept becomes its own proposal, capped at
    `TrustLevel.INFERRED` (ADR-0037 decision 6): an import is untrusted front
    matter, never governance, and a human reviewer is what raises the trust
    that merging the resulting pull request grants. A bundle's
    `theurian_relations` keys become one further proposal of `addRelation`
    operations, if any exist; a bare Markdown link never does. No proposal
    reaches approved state directly -- `theurian propose accept`, a pull
    request and a human merge are what do that, exactly as for any other
    proposal.

    A bad path inside the bundle -- a `theurian_body_file` escaping the
    bundle, or a symlink leading outside it -- refuses only that reference,
    reported by its front-matter key and the literal string the bundle wrote,
    never the path it resolved to; the rest of the bundle still drafts.
    Nothing here fetches anything: the bundle is a local directory already on
    disk, and no URL or registry reference is ever followed.
    """
    from theurian.cli.commands import (  # noqa: PLC0415 - cycle
        _emit,
        _fail,
        _fail_a_path_escape,
        _require_project,
    )

    inputs = _Inputs(
        bundle=bundle,
        owner=owner,
        author=author,
        agent_id=agent_id,
        task_id=task_id,
        model=model,
        reasoning=reasoning,
        item=tuple(item or ()),
    )
    context, _ = _require_project(as_json)

    try:
        result = _service(context).import_bundle(_request(inputs))
    except ProjectPathEscapeError as exc:
        _fail_a_path_escape(exc, as_json=as_json)
        return
    except OkfImportError as exc:
        _fail(str(exc), remedy=exc.remedy, as_json=as_json, code=EXIT_INVALID_INPUT)
        return
    except TheurianError as exc:
        _fail(
            str(exc),
            remedy="Correct the option the message names, then run this command again.",
            as_json=as_json,
            code=EXIT_INVALID_INPUT,
        )
        return

    payload = _result_payload(result)
    if not as_json:
        # `_emit`'s text renderer prints a list entry with `str()`, which
        # reads as a Python dict repr for a refusal; a formatted line is what
        # a terminal reader wants instead. The JSON payload keeps the
        # structured form.
        payload["refusals"] = [_refusal_line(refusal) for refusal in result.refusals]
    _emit(payload, as_json=as_json)


def _service(context: CommandContext) -> OkfImportService:
    """Wire the service. The schema check is an adapter, injected (ADR-0003).

    The same construction ``propose_commands.py``'s own ``_service`` uses:
    `theurian propose accept` and this import must never disagree about
    identifiers, digests or the landed set (ADR-0003, #253).
    """
    schemas = schema_root()
    migrations = context.loaded.migration_set
    service = ProposalService(
        paths=context.paths,
        project_id=context.project_id,
        clock=context.clock,
        ids=context.ids,
        validate=lambda document: validate_migration_document(document, schemas),
        current_revision=lambda item_id: current_revision_in(migrations, item_id),
        landed_migration=migrations.get,
        landed_migrations=lambda: migrations,
        rehearse=lambda candidate: rehearse_migration_set(candidate, clock=context.clock),
    )
    return OkfImportService(drafts=DraftOnlyProposals(service))


def _request(inputs: _Inputs) -> OkfImportRequest:
    return OkfImportRequest(
        root=inputs.bundle,
        owner=inputs.owner,
        author=inputs.author,
        evidence=Evidence(
            agent_id=AgentId(inputs.agent_id),
            task_id=TaskId(inputs.task_id),
            model=inputs.model,
            reasoning=inputs.reasoning,
            anchors=(),
        ),
        item_filter=frozenset(inputs.item),
    )


def _refusal_payload(refusal: ImportRefusal) -> dict[str, object]:
    return {"kind": refusal.kind, "key": refusal.key, "literal": refusal.literal}


def _refusal_line(refusal: ImportRefusal) -> str:
    return f"{refusal.kind} {refusal.key}: {refusal.literal}"


def _refusals_by_kind(refusals: tuple[ImportRefusal, ...]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for refusal in refusals:
        counts[refusal.kind] = counts.get(refusal.kind, 0) + 1
    return counts


def _result_payload(result: OkfImportResult) -> dict[str, object]:
    proposal_ids = [proposal.proposal.proposal_id.value for proposal in result.concepts_admitted]
    if result.relations_proposal is not None:
        proposal_ids.append(result.relations_proposal.proposal_id.value)
    return {
        "proposalIds": proposal_ids,
        "conceptsAdmitted": len(result.concepts_admitted),
        "refusalsByKind": _refusals_by_kind(result.refusals),
        "refusals": [_refusal_payload(refusal) for refusal in result.refusals],
    }


__all__ = ["okf_app"]
