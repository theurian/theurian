"""``theurian propose`` -- draft a knowledge change, and accept one (ADR-0013).

A composition root: this is where :class:`ProposalService` meets the published
JSON Schemas, the system clock and the id generator (ADR-0003). Milestone 7's
write-intent MCP tools call that same service, which is why nothing about
*packaging* a proposal lives in this file.

**Shape.** ``propose`` is a group whose callback runs when no verb follows it,
so the drafting command is spelled ``theurian propose`` -- the invocation
ADR-0013 §4 names -- while ``accept`` stays a verb under it. The cost of that
shape is that Click parses the group's options before it knows whether a verb
follows, so none of them can be declared ``required``: each carries "(required)"
in its own help text instead, and the callback reports *every* missing one at
once. An agent that has to re-invoke once per missing flag spends a turn each
time, which is the failure this surface is shaped against.

**Non-interactive by construction.** Nothing here prompts. Every input is an
option, the body arrives as a file, and the result names every path written.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Final, NoReturn

import typer

from theurian.application.proposal_service import (
    DraftedProposal,
    MigrationNameTakenError,
    ProposalError,
    ProposalRequest,
    ProposalService,
)
from theurian.cli.context import CommandContext, schema_root
from theurian.domain.enums import KnowledgeKind
from theurian.domain.errors import TheurianError
from theurian.domain.identifiers import AgentId, ItemId, ProposalId, RevisionId, TaskId
from theurian.domain.knowledge import AUTHORED_IN_THEURIAN, SourceAnchor
from theurian.domain.migration import current_revision_in
from theurian.domain.proposal import Evidence
from theurian.domain.values import JSON, MARKDOWN, YAML, MediaType
from theurian.infrastructure.filesystem.migration_loader import validate_migration_document
from theurian.security.paths import MAX_SOURCE_FILE_BYTES

#: Exit code for an invocation that cannot be used as given: a missing option, an
#: identifier that is not one, a body file that is not there. Click exits 2 for a
#: missing required option, and every one of these would have been exactly that
#: had the group shape allowed the options to be declared required.
EXIT_INVALID_INPUT: Final = 2

#: Body formats a proposal may carry, keyed by the extension the caller wrote.
#: Read from the file name rather than taken as its own option so the extension
#: and the declared ``contentType`` cannot disagree -- a body written as ``.md``
#: while its revision declares JSON is re-read as prose by the ingestion walk,
#: which is a silent loss rather than a refusal.
_CONTENT_TYPES: Final = {
    ".md": MARKDOWN,
    ".markdown": MARKDOWN,
    ".json": JSON,
    ".yaml": YAML,
    ".yml": YAML,
}

#: The options only the draft takes. Named once, because the callback both
#: requires them and refuses them when a verb follows.
_REQUIRED: Final = (
    "--item-id",
    "--title",
    "--kind",
    "--owner",
    "--author",
    "--description",
    "--body-file",
    "--agent-id",
    "--task-id",
    "--model",
    "--reasoning",
)

propose_app = typer.Typer(
    help="Draft a knowledge change as a reviewable proposal, or accept one.",
    no_args_is_help=True,
)

JsonOption = Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")]


@dataclass(frozen=True, slots=True)
class _Inputs:
    """One draft's options, after the missing-option check has passed.

    The callback has to take them one at a time -- Typer builds the parser from
    its signature -- and everything downstream is better off with one argument
    that cannot be assembled half-filled.
    """

    item_id: str
    title: str
    kind: KnowledgeKind
    owner: str
    author: str
    description: str
    body_file: Path
    namespace: str | None
    expected_revision: str | None
    anchor: SourceAnchor | None
    authored_here: bool
    agent_id: str
    task_id: str
    model: str
    reasoning: str


@propose_app.callback(invoke_without_command=True)
def propose_draft(  # noqa: PLR0913 -- one option per migration field, all keyword-only
    ctx: typer.Context,
    *,
    item_id: Annotated[
        str | None,
        typer.Option("--item-id", help="Knowledge id, e.g. architecture.retry-policy (required)."),
    ] = None,
    title: Annotated[
        str | None, typer.Option("--title", help="Human title for the revision (required).")
    ] = None,
    kind: Annotated[
        KnowledgeKind | None, typer.Option("--kind", help="What sort of knowledge (required).")
    ] = None,
    owner: Annotated[
        str | None, typer.Option("--owner", help="Team that owns this knowledge (required).")
    ] = None,
    author: Annotated[
        str | None,
        typer.Option("--author", help="The human who will own the change (required)."),
    ] = None,
    description: Annotated[
        str | None,
        typer.Option("--description", help="Why the change is being made (required)."),
    ] = None,
    body_file: Annotated[
        Path | None,
        typer.Option("--body-file", help="File holding the body: .md, .json or .yaml (required)."),
    ] = None,
    namespace: Annotated[
        str | None,
        typer.Option("--namespace", help="Namespace to record. Defaults to the item id's own."),
    ] = None,
    expected_revision: Annotated[
        str | None,
        typer.Option(
            "--expected-revision",
            help="Current revision id of an existing item. Pass it to propose an update.",
        ),
    ] = None,
    source_provider: Annotated[
        str, typer.Option("--source-provider", help="Anchor provider, e.g. git or github.")
    ] = "git",
    source_uri: Annotated[
        str | None,
        typer.Option(
            "--source-uri",
            help="Where this knowledge came from. Required unless --authored-here.",
        ),
    ] = None,
    source_commit: Annotated[
        str | None, typer.Option("--source-commit", help="Commit the anchor pins, if any.")
    ] = None,
    source_path: Annotated[
        str | None, typer.Option("--source-path", help="File the anchor names, if any.")
    ] = None,
    authored_here: Annotated[
        bool,
        typer.Option(
            "--authored-here",
            help="Declare that this knowledge originates in Theurian and has no external source.",
        ),
    ] = False,
    agent_id: Annotated[
        str | None, typer.Option("--agent-id", help="Which agent drafted this (required).")
    ] = None,
    task_id: Annotated[
        str | None, typer.Option("--task-id", help="Unit of work it came out of (required).")
    ] = None,
    model: Annotated[
        str | None, typer.Option("--model", help="Model identity that produced it (required).")
    ] = None,
    reasoning: Annotated[
        str | None,
        typer.Option("--reasoning", help="Why the evidence supports the claim (required)."),
    ] = None,
    as_json: JsonOption = False,
) -> None:
    """Draft a knowledge change as a proposal a human can review.

    Writes ``.theurian/proposals/<proposal-id>/`` and nothing else: the
    migration, under the name it keeps once accepted; the body in its native
    format; and ``evidence.json``. Approved knowledge is not touched. There is
    no CLI or MCP surface that approves anything, because approval is a human
    merging a pull request (ADR-0013).

    The generated migration pins its body's digest, and states which revision it
    replaces when ``--expected-revision`` says this is an update. Both fields are
    optional to the schema and neither is omitted here: the generator has the
    body in hand, and an update that does not name the revision it replaces is
    the race #210 describes.

    An update to an item that already exists **must** carry ``--expected-revision``.
    The generator derives the item's current revision from the approved migration
    set -- which is the canonical state -- and refuses a draft that would produce
    an unguarded update, rather than emitting one that validates and then fails at
    ``migrate apply`` after the pull request has merged (#210). ``--expected-revision``
    on an item that does not exist yet is refused for the same reason: a first
    revision has nothing to replace.
    """
    provided: dict[str, object] = {
        "--item-id": item_id,
        "--title": title,
        "--kind": kind,
        "--owner": owner,
        "--author": author,
        "--description": description,
        "--body-file": body_file,
        "--namespace": namespace,
        "--expected-revision": expected_revision,
        "--source-uri": source_uri,
        "--source-commit": source_commit,
        "--source-path": source_path,
        "--agent-id": agent_id,
        "--task-id": task_id,
        "--model": model,
        "--reasoning": reasoning,
        # These two have non-``None`` defaults, so "was it passed" is "does it
        # differ from the default": a bare ``git`` or ``False`` is indistinguishable
        # from omission and is not treated as a stray option handed to a verb.
        "--source-provider": source_provider if source_provider != "git" else None,
        "--authored-here": authored_here or None,
    }
    if ctx.invoked_subcommand is not None:
        _refuse_stray_options(provided, subcommand=ctx.invoked_subcommand, as_json=as_json)
        return

    missing = [name for name in _REQUIRED if not provided[name]]
    if missing:
        _refuse(
            f"theurian propose needs {', '.join(missing)}.",
            remedy="Pass every option named above. Run this command with --help for what "
            "each one carries.",
            as_json=as_json,
        )

    _draft(
        _Inputs(
            item_id=_present(item_id),
            title=_present(title),
            kind=_present(kind),
            owner=_present(owner),
            author=_present(author),
            description=_present(description),
            body_file=_present(body_file),
            namespace=namespace,
            expected_revision=expected_revision,
            anchor=_anchor(source_provider, source_uri, source_commit, source_path),
            authored_here=authored_here,
            agent_id=_present(agent_id),
            task_id=_present(task_id),
            model=_present(model),
            reasoning=_present(reasoning),
        ),
        as_json=as_json,
    )


@propose_app.command("accept")
def propose_accept(
    proposal_id: Annotated[str, typer.Argument(help="Proposal id: the directory's own name.")],
    as_json: JsonOption = False,
) -> None:
    """Move an accepted proposal's files into place. Never the judgement.

    The migration goes to ``.theurian/migrations/`` under the name it already
    has, and the body to the path its ``contentFile`` names. Nothing is
    validated beyond what the move needs, nothing is applied, and nothing is
    approved -- approval is the pull request, and this command runs before it.

    The two moves are not symmetric. The migration file may never land on an
    existing name: the name carries the migration's id, so a collision means
    that migration is already in place. The body file *may* replace what is at
    its path, because on an update to existing knowledge that is the intent.

    Exit codes: 0 moved, 1 no such proposal, 2 malformed id, 4 that migration is
    already in place.
    """
    from theurian.cli.commands import (  # noqa: PLC0415 - cycle
        EXIT_STATE_ERROR,
        _emit,
        _fail,
        _require_project,
    )

    try:
        parsed = ProposalId.parse(proposal_id)
    except TheurianError as exc:
        _refuse(
            str(exc),
            remedy="A proposal id is the 26-character ULID naming its directory under "
            ".theurian/proposals/.",
            as_json=as_json,
        )

    context, _ = _require_project(as_json)
    try:
        accepted = _service(context).accept(parsed)
    except MigrationNameTakenError as exc:
        _fail(str(exc), remedy=exc.remedy, as_json=as_json, code=EXIT_STATE_ERROR)
        return
    except ProposalError as exc:
        _fail(str(exc), remedy=exc.remedy, as_json=as_json, code=1)
        return
    except TheurianError as exc:
        # A `contentFile` that leaves the project is the case that reaches here:
        # the proposal directory is committed, so its migration is input from
        # whoever can commit, and SEC-7 refuses the path rather than writing it.
        _fail(
            str(exc),
            remedy="Correct the contentFile the migration names, then accept it again.",
            as_json=as_json,
            code=1,
        )
        return

    root = context.paths.root
    _emit(
        {
            "proposalId": accepted.proposal_id.value,
            "migrationFile": _relative(accepted.migration.destination, root),
            "bodyFiles": [_relative(move.destination, root) for move in accepted.bodies],
            "replacedBodies": [
                _relative(move.destination, root) for move in accepted.bodies if move.replaced
            ],
            "nextSteps": list(_ACCEPT_STEPS),
        },
        as_json=as_json,
    )


#: What a caller does next, and the one thing about it that surprises people:
#: `migrate validate` is schema conformance and nothing else. The invariants
#: `migrate apply` enforces -- a revision's source anchor, a reused revision id
#: -- are checked after the pull request has already merged (#36).
_ACCEPT_STEPS: Final = (
    "Review the diff, then open a pull request with the proposal directory in it. "
    "The merge is the approval.",
    "`theurian migrate validate --json` checks schema conformance only. It does not "
    "prove the migration will apply: source anchors and revision-id reuse are checked "
    "by `theurian migrate apply`, after the pull request has merged.",
    "Once it has merged: `theurian migrate apply --json`, then "
    "`theurian index build --json`, or the knowledge just approved is not searchable.",
)

#: The steps that follow a draft. The judgement and the moves are the human's;
#: `propose accept` automates the moves and nothing else (ADR-0013 point 4).
_DRAFT_STEPS: Final = (
    "Read the proposal. Nothing has been approved and nothing has moved.",
    "If you agree: `theurian propose accept <proposal-id>` moves the migration and the "
    "body into place. That is the file moves, not the approval.",
    "`theurian migrate validate --json` reads .theurian/migrations/ only, so it reports "
    "nothing at all while a proposal is still under .theurian/proposals/.",
    "Validation is schema conformance and nothing more. The invariants "
    "`theurian migrate apply` enforces -- a revision's source anchor, a reused revision "
    "id -- are checked after the pull request has merged, not before it.",
)


def _draft(inputs: _Inputs, *, as_json: bool) -> None:
    """Read the body, build the request, and report what was written."""
    from theurian.cli.commands import _emit, _fail, _require_project  # noqa: PLC0415 - cycle

    body, content_type = _read_body(inputs.body_file, as_json=as_json)
    context, _ = _require_project(as_json)

    try:
        drafted = _service(context).draft(_request(inputs, body=body, content_type=content_type))
    except ProposalError as exc:
        _fail(str(exc), remedy=exc.remedy, as_json=as_json, code=EXIT_INVALID_INPUT)
        return
    except TheurianError as exc:
        # Every remaining refusal is an option the domain would not accept: an
        # item id that is not dotted kebab-case, a revision id that is not a
        # ULID, evidence that evidences nothing.
        _fail(
            str(exc),
            remedy="Correct the option the message names, then run this command again.",
            as_json=as_json,
            code=EXIT_INVALID_INPUT,
        )
        return

    _emit(_drafted_payload(drafted, context), as_json=as_json)


def _request(inputs: _Inputs, *, body: str, content_type: MediaType) -> ProposalRequest:
    """Turn option strings into the values the service takes.

    Identifiers are parsed here rather than inside the service, so a malformed
    ``--expected-revision`` is reported as a bad option rather than as a
    packaging failure.
    """
    anchors = () if inputs.anchor is None else (inputs.anchor,)
    evidence = Evidence(
        agent_id=AgentId(inputs.agent_id),
        task_id=TaskId(inputs.task_id),
        model=inputs.model,
        reasoning=inputs.reasoning,
        # The same anchor the revision records, written to a different file for
        # a different reader. `evidence.json` is read by the humans reviewing
        # the pull request and never by Core, while `metadata.sourceAnchors` is
        # what `theurian migrate apply` enforces (INV-8). Neither substitutes
        # for the other, which is why they stay separate fields here and
        # separate files on disk even when this surface fills both from one
        # option.
        anchors=anchors,
    )
    return ProposalRequest(
        item_id=ItemId(inputs.item_id),
        title=inputs.title,
        kind=inputs.kind,
        owner=inputs.owner,
        author=inputs.author,
        description=inputs.description,
        body=body,
        content_type=content_type,
        evidence=evidence,
        source_anchors=anchors,
        labels=(AUTHORED_IN_THEURIAN,) if inputs.authored_here else (),
        namespace=inputs.namespace,
        expected_revision=(
            None if inputs.expected_revision is None else RevisionId.parse(inputs.expected_revision)
        ),
    )


def _anchor(
    provider: str, uri: str | None, commit: str | None, file_path: str | None
) -> SourceAnchor | None:
    """The one anchor this surface builds, or nothing.

    One rather than a repeatable structured option: INV-8 needs one, an agent
    citing a commit or a review thread has one, and a reviewer can add more by
    hand before merging. The alternative is a repeatable ``key=value`` flag with
    a parser of its own, which nothing has asked for yet.
    """
    if uri is None:
        return None
    return SourceAnchor(provider=provider, source_uri=uri, commit_sha=commit, file_path=file_path)


def _read_body(path: Path, *, as_json: bool) -> tuple[str, MediaType]:
    """Read the body and settle its format, or refuse with the reason.

    This command's own file IO answers for itself rather than unwinding. A
    missing file, an unreadable one, a non-UTF-8 body, an oversized one and an
    extension nothing maps are five different mistakes with five different
    cures, and a traceback names none of them.
    """
    content_type = _CONTENT_TYPES.get(path.suffix.lower())
    if content_type is None:
        _refuse(
            f"{path.name} is not a body format a proposal can carry.",
            remedy="Write the body as .md, .json or .yaml, and pass that file.",
            as_json=as_json,
        )

    try:
        if path.stat().st_size > MAX_SOURCE_FILE_BYTES:
            _refuse(
                f"{path.name} is larger than {MAX_SOURCE_FILE_BYTES} bytes.",
                remedy="Split it into knowledge items a reviewer can read.",
                as_json=as_json,
            )
        body = path.read_text(encoding="utf-8")
    except OSError as exc:
        _refuse(
            f"{path} could not be read: {exc.strerror or exc}.",
            remedy="Write the body to a file first, then pass its path as --body-file.",
            as_json=as_json,
        )
    except UnicodeDecodeError:
        _refuse(
            f"{path.name} is not valid UTF-8.",
            remedy="Knowledge is text. Re-encode the body as UTF-8.",
            as_json=as_json,
        )

    return body, content_type


def _service(context: CommandContext) -> ProposalService:
    """Wire the service. The schema check is an adapter, injected (ADR-0003).

    The current-revision lookup reads the approved migration set, which is the
    canonical state (FR-K4), so the generator can require ``--expected-revision``
    on a known item without opening the state database.
    """
    schemas = schema_root()
    migrations = context.loaded.migration_set
    return ProposalService(
        paths=context.paths,
        clock=context.clock,
        ids=context.ids,
        validate=lambda document: validate_migration_document(document, schemas),
        current_revision=lambda item_id: current_revision_in(migrations, item_id),
    )


def _drafted_payload(drafted: DraftedProposal, context: CommandContext) -> dict[str, object]:
    root = context.paths.root
    return {
        "proposalId": drafted.proposal_id.value,
        "proposalDirectory": _relative(drafted.directory, root),
        "migrationId": drafted.migration_id.value,
        "migrationFile": drafted.migration_file.name,
        "revisionId": drafted.revision_id.value,
        "expectedRevision": (
            None if drafted.expected_revision is None else drafted.expected_revision.value
        ),
        "bodyFile": _relative(drafted.body_file, root),
        "evidenceFile": _relative(drafted.evidence_file, root),
        "contentFile": drafted.content_file,
        "contentSha256": drafted.content_sha256.value,
        "bodyDestination": _relative(drafted.body_destination, root),
        "nextSteps": list(_DRAFT_STEPS),
    }


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:  # pragma: no cover - every path here is built from the root
        return str(path)


def _refuse_stray_options(provided: dict[str, object], *, subcommand: str, as_json: bool) -> None:
    """Refuse draft options handed to a verb that does not take them.

    Click parses a group's options whatever follows them, so
    ``theurian propose --title X accept <id>`` parses and the title is dropped.
    Dropping it silently reports success for a change nobody made.
    """
    stray = sorted(name for name, value in provided.items() if value)
    if not stray:
        return
    _refuse(
        f"{', '.join(stray)} belongs to the draft, not to the {subcommand} verb.",
        remedy="Drop the option, or draft the proposal in its own invocation.",
        as_json=as_json,
    )


def _refuse(message: str, *, remedy: str, as_json: bool) -> NoReturn:
    """Report a bad invocation and exit 2.

    ``_fail`` already raises, and the raise below is what says so to a type
    checker: without it every caller needs an ``assert`` or a dead ``return``
    to convince one that the value it was about to use exists.
    """
    from theurian.cli.commands import _fail  # noqa: PLC0415 - cycle

    _fail(message, remedy=remedy, as_json=as_json, code=EXIT_INVALID_INPUT)
    raise typer.Exit(EXIT_INVALID_INPUT)  # pragma: no cover - `_fail` raised first


def _present[T](value: T | None) -> T:
    """Narrow a value the missing-option check has already proved is there."""
    if value is None:  # pragma: no cover - `missing` reports it before this runs
        raise typer.Exit(EXIT_INVALID_INPUT)
    return value
