"""``theurian okf`` -- the Open Knowledge Format interchange (ADR-0037).

A composition root: where the abstract exporter meets the SQLite canonical
store and the operator's own serving profile, and where the gated import
meets ``ProposalService`` and the published JSON Schemas -- the same wiring
``propose_commands.py`` uses (ADR-0003). Nothing below this module names a
concrete adapter beyond these.

**A write/maintenance path, like ``index build`` and ``findings build``, and
it serves nothing.** What ``export`` publishes on stdout is a path, three
counts and a digest over the bundle's own files -- no title, no body, no
relation note and no item id. What it *writes* is a bundle, and that is a
distributable copy of approved knowledge: the population is
``application/okf_export.py``'s, gated on the same two predicates the serve
path uses, and no option here widens it. ``import`` is a gated on-ramp to the
same write path ``theurian propose`` uses -- every drafted proposal lands
under ``.theurian/proposals/`` and reaches approved state only through
``theurian propose accept``, a pull request, and a human merge (ADR-0013),
unchanged.

The sub-app is intentionally minimal and generic, so both verbs join ``okf``
here rather than splitting into two modules.

Help text is printed, not interpreted: ``cli/main.py`` passes
``rich_markup_mode=None``, so a square bracket in a docstring reaches the
screen instead of being read as a Rich style tag.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Final

import typer

from theurian.application.authorization import (
    AuthorizationGrant,
    StaticAuthorizationProvider,
    load_serving_profile,
)
from theurian.application.draft_only_proposals import DraftOnlyProposals
from theurian.application.okf_export import (
    OkfExporter,
    OkfExportError,
    OkfExportRequest,
    canonical_export_target,
)
from theurian.application.okf_import import (
    ImportRefusal,
    OkfImportError,
    OkfImportRequest,
    OkfImportResult,
    OkfImportService,
)
from theurian.application.project_service import (
    UNBUILT_STATE_REMEDY,
    BuildProvenance,
    ProjectPathEscapeError,
    ProjectPaths,
)
from theurian.application.proposal_service import ProposalService
from theurian.cli.context import CommandContext, schema_root
from theurian.cli.migration_pipeline import rehearse_migration_set
from theurian.domain.errors import TheurianError
from theurian.domain.identifiers import AgentId, TaskId
from theurian.domain.migration import current_revision_in
from theurian.domain.proposal import Evidence
from theurian.domain.state import ActiveState
from theurian.infrastructure.filesystem.migration_loader import validate_migration_document
from theurian.infrastructure.secrets.file_store import default_data_dir
from theurian.infrastructure.sqlite.store import SqliteCanonicalStore

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


@okf_app.command("export")
def okf_export(  # noqa: PLR0911 -- one early return per distinguishable failure shape, the
    # precedent `index_build` sets: an unbuilt project, state this install did not build, an
    # unreadable serving profile, a pointer naming a database outside the tree, and the three
    # ways the export itself refuses. Folding them into one `try` would publish the target's
    # cure for a damaged database (#525's shape).
    directory: Annotated[
        Path,
        typer.Argument(
            help=(
                "Where to write the bundle. Created if absent; refused if it already "
                "holds anything."
            )
        ),
    ],
    as_json: JsonOption = False,
) -> None:
    """Export this project's approved knowledge as an OKF bundle (ADR-0037).

    The bundle is Open Knowledge Format v0.2: a directory tree of Markdown files
    with YAML front matter, one concept document per knowledge item, an `index.md`
    in every directory, and a `theurian-bundle.md` manifest at the root carrying a
    digest over every other file in the tree.

    It holds exactly what this deployment serves by default — `approved` items at
    or below the sensitivity ceiling in your serving profile — and no option
    widens that. A bundle is the easiest artifact to forward, so a reader who may
    not see a row through `knowledge.search` may not see it here either.

    A bundle is a point-in-time copy that no later change reaches. A withdrawal, a
    correction or the removal of a secret applies to this deployment and to the
    indexes built here; it does not propagate to a copy someone already holds, and
    nothing in a bundle can be updated in place. The manifest says so to whoever
    holds it. Regenerate and compare `theurian_bundle_digest` rather than trusting
    an old bundle: two exports of one unchanged state produce byte-identical trees.

    The target directory must be empty or absent. Merging a new bundle into an old
    one would leave members of that export behind — including concepts whose rows
    have since been withdrawn — and make the digest describe a tree that is not
    there, so a target holding anything is refused rather than merged.
    """
    from theurian.cli.commands import (  # noqa: PLC0415 - cycle
        EXIT_STATE_ERROR,
        _emit,
        _fail,
        _read_active,
        _require_project,
    )

    context, _ = _require_project(as_json)
    paths = context.paths
    active = _read_active(paths, as_json)
    if active is None:
        _fail(
            "This project has no built knowledge state, so there is nothing to export.",
            remedy="Run `theurian migrate apply` first.",
            as_json=as_json,
            code=1,
        )
        return
    # Refuse to export *from* canonical state this installation did not build
    # (ADR-0004, SEC-7), which is the precondition `index build` takes for the
    # same reason one artifact over: a doctored `.theurian/state/` shipped in a
    # repository would otherwise be projected into a bundle that names Theurian
    # as its producer and is handed to somebody else.
    if not BuildProvenance.default().has_state(paths.root, str(active.state_hash)):
        _fail(
            "This project's canonical state was not built by this Theurian installation, so "
            "a bundle exported from it cannot be trusted. It was delivered with the project "
            "rather than rebuilt here from the Git-tracked migrations (ADR-0004).",
            remedy=UNBUILT_STATE_REMEDY,
            as_json=as_json,
            code=1,
        )
        return
    grant = _deployment_grant(as_json)
    if grant is None:
        return
    database = _the_state_database(paths, active, as_json=as_json)
    if database is None:
        return

    request = OkfExportRequest(
        database=database,
        output_directory=directory,
        project_id=context.project_id.value,
        visible_sensitivities=grant.sensitivities,
    )
    try:
        report = OkfExporter(store_factory=SqliteCanonicalStore).export(request)
    except OkfExportError as exc:
        _fail(str(exc), remedy=exc.remedy, as_json=as_json, code=1)
        return
    except TheurianError as exc:
        # The damaged-state family: an unreadable cell, a contended database, an
        # item pointing at another item's revision. Each carries its own cure
        # where it has one, and `EXIT_STATE_ERROR` is what this CLI means by "a
        # knowledge-state problem the user must repair".
        _fail(
            str(exc),
            remedy=exc.remedy or "Run `theurian doctor`.",
            as_json=as_json,
            code=EXIT_STATE_ERROR,
        )
        return
    except OSError as exc:
        # The type name and never the message: an `OSError`'s `str` appends the
        # filename, and the remedy below already names the directory once.
        #
        # `canonical_export_target`, not `directory`: every guard already ran
        # against the canonical path, so a target reached through an ancestor
        # link would otherwise get a cure naming the link rather than where the
        # bundle actually landed (round five, adversarial MEDIUM, L-3).
        #
        # `ls -la` is offered only where there is something to list, and after the
        # atomic publish what it lists is never this run's: every byte goes into a
        # staging directory and reaches the target through one rename, so an
        # `OSError` here failed before that rename and whatever the listing shows
        # was already at the path. A cure that sent an operator to list a path that
        # does not exist would answer them with a second error and say nothing
        # about the first. `is_symlink` as well as `exists`, because a dangling
        # link is at the path while `exists` follows it and answers False.
        target = canonical_export_target(directory)
        left = (
            f"`ls -la {target}` shows what is at it now"
            if _exists_or_is_a_symbolic_link(target)
            else f"nothing was left at {target}"
        )
        _fail(
            f"The bundle could not be written ({type(exc).__name__}), so none was published.",
            remedy=(
                f"Make sure {target} is writable and has room, then export again into an "
                f"empty or new directory -- {left}. A bundle is regenerated rather than "
                f"repaired."
            ),
            as_json=as_json,
            code=1,
        )
        return

    _emit(report, as_json=as_json)


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


def _exists_or_is_a_symbolic_link(path: Path) -> bool:
    """``path.exists() or path.is_symlink()``, answering ``False`` rather than raising.

    Both calls are already inside an ``except OSError`` handler that is choosing
    the export's own recovery wording; a name the filesystem cannot even answer
    about -- ``ENAMETOOLONG``, which neither ``exists()`` nor ``is_symlink()``
    swallows -- used to raise the same error a second time, from inside the
    handler meant to recover from the first (round five, adversarial MEDIUM).
    """
    try:
        return path.exists() or path.is_symlink()
    except OSError:
        return False


def _the_state_database(paths: ProjectPaths, active: ActiveState, *, as_json: bool) -> Path | None:
    """The canonical store this pointer names, or ``None`` once the refusal is published.

    ``cli/index_commands.py``'s helper of the same name, for the same reason: the
    filename comes out of a derived, git-ignored pointer, so the join goes through
    the containment chokepoint rather than being built here (round two,
    security H-1 on that command).
    """
    from theurian.cli.commands import _fail, _fail_a_path_escape  # noqa: PLC0415 - cycle

    try:
        return paths.state_database_named(active.database_filename)
    except ProjectPathEscapeError as exc:
        _fail_a_path_escape(exc, as_json=as_json)
        return None
    except TheurianError as exc:
        _fail(str(exc), remedy=exc.remedy or UNBUILT_STATE_REMEDY, as_json=as_json, code=1)
        return None


def _deployment_grant(as_json: bool) -> AuthorizationGrant | None:
    """What this deployment serves, or ``None`` once the refusal has been reported.

    Resolved through the same :class:`StaticAuthorizationProvider` the daemon
    composes and out of the same operator-owned data directory, so a bundle and
    the deployment that produced it cannot expand one declared ceiling two
    different ways -- ``cli/index_commands.py::_deployment_grant``'s reason, and
    sharper here: an index row's text is in a file on this machine, while a
    bundle's is in a file somebody else holds.

    An unreadable profile refuses the export rather than defaulting, for the same
    reason: a malformed ceiling that fell back to a default would put *more* into
    a distributable copy than its operator asked for.
    """
    from theurian.cli.commands import _fail  # noqa: PLC0415 - cycle

    try:
        profile = load_serving_profile(default_data_dir())
    except TheurianError as exc:
        _fail(str(exc), remedy=exc.remedy or "Run `theurian doctor`.", as_json=as_json, code=1)
        return None
    return StaticAuthorizationProvider(profile).deployment_grant()


__all__ = ["okf_app", "okf_export"]
