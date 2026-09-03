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
    AcceptedProposal,
    ApprovedSetUnusableError,
    ChangeAlreadyInPlaceError,
    DraftedProposal,
    ProposalError,
    ProposalRequest,
    ProposalService,
)
from theurian.cli.context import CommandContext, schema_root
from theurian.cli.migration_pipeline import rehearse_migration_set
from theurian.domain.enums import KnowledgeKind, Sensitivity, TrustLevel
from theurian.domain.errors import TheurianError
from theurian.domain.identifiers import AgentId, ItemId, ProposalId, RevisionId, TaskId
from theurian.domain.knowledge import AUTHORED_IN_THEURIAN, SourceAnchor
from theurian.domain.migration import (
    DEFAULT_SENSITIVITY,
    DEFAULT_TRUST_LEVEL,
    current_revision_in,
)
from theurian.domain.proposal import Evidence
from theurian.domain.values import JSON, MARKDOWN, YAML, MediaType
from theurian.infrastructure.filesystem.migration_loader import validate_migration_document
from theurian.security.paths import MAX_SOURCE_FILE_BYTES

#: Exit code for an invocation that cannot be used as given: a missing option, an
#: identifier that is not one, a body file that is not there. Click exits 2 for a
#: missing required option, and every one of these would have been exactly that
#: had the group shape allowed the options to be declared required.
EXIT_INVALID_INPUT: Final = 2

#: The C0 control block (below U+0020) and DEL (U+007F). A label carrying one of
#: these corrupts the reviewed migration text -- a newline splits it across
#: lines, a NUL truncates it -- and the schema's ``labels.items`` does not forbid
#: them the way ``title``/``owner`` do, so ``propose`` refuses them at draft
#: time (#249).
_C0_CEILING: Final = 0x20
_DEL: Final = 0x7F

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
    trust_level: TrustLevel | None
    sensitivity: Sensitivity | None
    scope_paths: tuple[str, ...]
    labels: tuple[str, ...]
    agent_id: str
    task_id: str
    model: str
    reasoning: str
    local: bool


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
    trust_level: Annotated[
        TrustLevel | None,
        typer.Option(
            "--trust-level",
            help="How much scrutiny this content has had. Omitted, the revision loads as "
            "'unverified' -- honest for an agent draft, wrong for reviewed knowledge.",
        ),
    ] = None,
    sensitivity: Annotated[
        Sensitivity | None,
        typer.Option(
            "--sensitivity",
            help="Disclosure class. Omitted, the revision loads as 'internal'.",
        ),
    ] = None,
    scope_path: Annotated[
        list[str] | None,
        typer.Option(
            "--scope-path",
            help="Glob this knowledge governs, e.g. src/**. Repeatable; the order is kept.",
        ),
    ] = None,
    label: Annotated[
        list[str] | None,
        typer.Option(
            "--label",
            help="Free-form label to group this knowledge by. Repeatable; the order is kept.",
        ),
    ] = None,
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
    local: Annotated[
        bool,
        typer.Option(
            "--local",
            help="Draft under .theurian/proposals-local/ instead. `theurian init` git-ignores "
            "that directory, so the proposal stays on this machine: nothing to commit by "
            "accident, and nothing travels to a clone. `git clean -xdf` deletes it.",
        ),
    ] = False,
    as_json: JsonOption = False,
) -> None:
    """Draft a knowledge change as a proposal a human can review.

    Writes ``.theurian/proposals/<proposal-id>/`` and nothing else: the
    migration, under the name it keeps once accepted; the body in its native
    format; and ``evidence.json``. Approved knowledge is not touched. There is
    no CLI or MCP surface that approves anything, because approval is a human
    merging a pull request (ADR-0013).

    ``--local`` writes the same three files under
    ``.theurian/proposals-local/<proposal-id>/`` instead. That directory is in
    the ignore block ``theurian init`` writes, which every clone inherits, so a
    draft whose content must not leave this machine cannot be committed by an
    absent-minded ``git add -A`` (ADR-0028). Only the parent differs:
    ``theurian propose accept`` reads both locations, through the same code, and
    refuses an id that is in both rather than choosing.

    The generated migration pins its body's digest -- schema-required since
    ADR-0027 decision 1 -- and states which revision it replaces when
    ``--expected-revision`` says this is an update. ``expectedRevision`` is the
    one of the two the schema still leaves optional, and it is not omitted here
    either: an update that does not name the revision it replaces is the race
    #210 describes.

    An update to an item that already exists **must** carry ``--expected-revision``.
    The generator derives the item's current revision from the approved migration
    set -- which is the canonical state -- and refuses a draft that would produce
    an unguarded update, rather than emitting one ``theurian propose accept``
    would then refuse for conflicting with the revision in place (#210).
    ``--expected-revision`` on an item that does not exist yet is refused for the
    same reason: a first revision has nothing to replace.
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
        "--trust-level": trust_level,
        "--sensitivity": sensitivity,
        # Repeatable options arrive as ``None`` when omitted and a list when
        # given, so an empty draft leaves them out of the stray check while a
        # populated one is caught -- the same "was it passed" test the scalars use.
        "--scope-path": scope_path,
        "--label": label,
        "--agent-id": agent_id,
        "--task-id": task_id,
        "--model": model,
        "--reasoning": reasoning,
        # These three have non-``None`` defaults, so "was it passed" is "does it
        # differ from the default": a bare ``git`` or ``False`` is indistinguishable
        # from omission and is not treated as a stray option handed to a verb.
        "--source-provider": source_provider if source_provider != "git" else None,
        "--authored-here": authored_here or None,
        # `--local` belongs to the draft, so `propose --local accept <id>` is a
        # stray option and not a request to accept from the local directory:
        # `accept` reads both locations by itself, and honouring it there would
        # be a precedence rule ADR-0028 refuses to have.
        "--local": local or None,
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

    scope_paths = tuple(scope_path or ())
    labels = tuple(label or ())
    _refuse_blank_scope_paths(scope_paths, as_json=as_json)
    _refuse_unusable_labels(labels, as_json=as_json)

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
            trust_level=trust_level,
            sensitivity=sensitivity,
            scope_paths=scope_paths,
            labels=labels,
            agent_id=_present(agent_id),
            task_id=_present(task_id),
            model=_present(model),
            reasoning=_present(reasoning),
            local=local,
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
    has, and the body to the path its ``contentFile`` names. Nothing is applied
    and nothing is approved -- approval is the pull request, and this command
    runs before it.

    **The proposal is looked up in both locations** -- ``.theurian/proposals/``
    and the git-ignored ``.theurian/proposals-local/`` that ``theurian propose
    --local`` writes -- through one implementation, with the same symlink
    refusal, containment check and size cap (ADR-0028, SEC-7). There is no flag
    that picks one: an id present in *both* is refused naming both paths, and
    never resolved by precedence, because the two directories can hold different
    bytes and choosing silently would accept one while the author was reading
    the other.

    **What is checked before anything moves**: that nothing it would land carries
    a secret, and that the project's migration set, with this proposal in it, still
    survives the pipeline ``theurian migrate apply`` runs -- the published
    schema, the whole-set guards, and a dry replay against a throwaway store
    that catches the invariants only applying can check, a revision's source
    anchor and a reused revision id among them. If either refuses, the
    acceptance is refused and **nothing is consumed**: the proposal directory is
    left exactly as it was, so the change can be corrected and accepted rather
    than re-drafted from nothing (ADR-0027, #307).

    **Secret scanning** (SEC-11) runs first, over everything the acceptance
    would land: the bodies, the migration document's author-written field
    values, the migration file's own bytes -- a YAML comment included -- its
    filename, and the path each body lands at. Under the policy
    ``security.secretScan`` selects in ``.theurian/config.yaml``: ``block``,
    which is also what an absent key or an absent file selects, refuses the
    acceptance; ``warn`` proceeds and reports what it found on the result;
    ``off`` skips it. The detector is best effort: it matches known credential
    shapes and flags strings that look randomly generated. It is not a
    repository secret scanner and not a substitute for one. It will produce
    false positives, and the escape hatch for one is the policy key, which the
    refusal's own remedy names.

    The two moves are not symmetric. The migration file may never land on an
    existing name: the name carries the migration's id, so a collision means
    that migration is already in place. The body file *may* replace what is at
    its path, because on an update to existing knowledge that is the intent.

    Exit codes: 0 the files moved -- and, if the migration and bodies landed but
    the proposal's own source files could not then be removed (a read-only
    proposal directory), a ``remedy`` naming the leftover; the move still
    succeeded, so this is not a failure; 1 this proposal could not be used as it
    stands -- no such proposal, one directory of that id in each of the two
    proposal locations, a draft interrupted before its migration was
    written, a proposal directory or a file in it the filesystem refuses to list,
    examine or read, a contentFile the filesystem cannot resolve or the security
    layer refuses, anything it would land -- a body, a migration field, the
    migration's own bytes, its filename or a body's path -- that appears to carry a
    secret while the policy is
    ``block``, a ``.theurian/config.yaml`` that cannot be read or names a
    ``security.secretScan`` value this build does not recognise, or a migration
    that does not satisfy the schema or would not apply; 2 the id is not a ULID; 4 the
    project's knowledge state refuses the move -- this proposal was accepted
    before, that migration id is already in ``.theurian/migrations/``, or the
    approved migration set does not resolve or does not apply (it is unreadable,
    tampered, or internally inconsistent) with or without this proposal.

    **4 means "read the knowledge state before doing anything", not "already
    done".** Its migration-set case -- raised while resolving the project, so
    before this command dispatches at all, or by the pre-check when the set turns
    out not to apply on its own -- leaves the proposal undelivered, so a caller
    that treats 4 as "already accepted, skip it" abandons it, and one that treats
    it as "re-draft" mints a duplicate for a fault the proposal does not have
    (#89). 1 normally means nothing landed and drafting again is the recovery;
    normally, because a part-way write is rolled back on a best-effort basis, and
    a rollback that itself fails leaves a body in ``.theurian/knowledge/`` while
    this still reports the original failure -- and because a read the filesystem
    refused exits 1 without having established anything either way, so its own
    remedy sends the reader to ``.theurian/migrations/`` before re-drafting
    rather than straight to a second draft (#227).

    **A ``.theurian/config.yaml`` fault also exits 1, though the proposal is not
    at fault** -- the same "proposal is fine" property
    :class:`~theurian.application.proposal_service.ApprovedSetUnusableError`
    carries into exit 4. It deliberately does not share that 4: exit 4 is
    anchored to the project's *knowledge state* -- the approved migration set, a
    proposal already accepted -- and a configuration fault is not one, so widening
    4 to cover it would blur what "read the knowledge state" means. Exit 1 is the
    bucket for "this proposal could not be used as it stands, and the remedy names
    the fix", which is exactly what a config fault is: its own remedy names
    ``config.yaml`` and the three ``secretScan`` values rather than sending the
    author to draft again, the same honesty the refused read above relies on,
    where the remedy carries the action.
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
            ".theurian/proposals/ or .theurian/proposals-local/.",
            as_json=as_json,
        )

    context, _ = _require_project(as_json)
    try:
        accepted = _service(context).accept(parsed)
    except ChangeAlreadyInPlaceError as exc:
        # Both faces of "already in place" -- a taken migration name and a
        # proposal whose migration has already moved out -- are knowledge state,
        # not a lookup failure, so both take the code reserved for state. The
        # second used to exit 1 beside "no such proposal", which is the exit code
        # the help text has always documented as 4 (#254).
        _fail(str(exc), remedy=exc.remedy, as_json=as_json, code=EXIT_STATE_ERROR)
        return
    except ApprovedSetUnusableError as exc:
        # The pre-check found `.theurian/migrations/` unusable on its own, so the
        # proposal is not the cause. Exit 1's contract is "nothing landed, and
        # drafting again is the recovery", and the second half is false here:
        # re-drafting mints a duplicate for a fault the proposal does not have
        # (#89). This is the third case the help text's 4 already covers -- an
        # approved migration set that does not resolve -- reached one stage later.
        _fail(str(exc), remedy=exc.remedy, as_json=as_json, code=EXIT_STATE_ERROR)
        return
    except ProposalError as exc:
        _fail(str(exc), remedy=exc.remedy, as_json=as_json, code=1)
        return
    except TheurianError as exc:
        # A `contentFile` that leaves the project is the case this text was
        # written for: the proposal directory is committed, so its migration is
        # input from whoever can commit, and SEC-7 refuses the path rather than
        # writing it. It is not the only case that arrives, and it was published
        # for all of them -- a proposal *body* that is a FIFO is refused here
        # too; now that the pre-check reads the published schema a
        # `SchemaUnreadableError` ("reinstall theurian") reaches here as well;
        # and a `ProjectConfigError` does too, when `.theurian/config.yaml` is
        # unreadable or names a `security.secretScan` value this build does not
        # recognise (SEC-11) -- it arrives here rather than as a `ProposalError`
        # deliberately, because the proposal is fine and its author has nothing
        # to correct, and its own remedy names the file and the three values. In
        # none of these is the `contentFile` what is wrong. So the error's own
        # remedy wins where it has one, and this text is the fallback for a
        # refusal that carries none: the preference `TheurianError.remedy` and
        # `cli/commands.py::_context_remedy` already document, applied at the one
        # composition root that was overwriting it (#227, #205).
        _fail(
            str(exc),
            remedy=exc.remedy
            or "Correct the contentFile the migration names, then accept it again.",
            as_json=as_json,
            code=1,
        )
        return

    root = context.paths.root
    # `migrationFile` and `bodyFiles` name landed paths at full length, and that
    # is deliberate rather than an oversight of #360's redaction. #360 is about
    # *refusals*, where the echo is gratuitous: the message is about a name
    # collision or a missing file and reprints a credential on its way out. This
    # is a success payload whose entire job is to say what was written, and a
    # redacted path reports nothing -- the author cannot find the file they were
    # just told about. Reaching it needs `warn`, because `block` refuses a
    # secret-shaped landed path before anything moves (`_AT_BODY_PATH`), and
    # under `warn` the same string is already published beside it in
    # `secretFindings` with the rotate step above it. Non-disclosing either way:
    # the caller is the maintainer who wrote the path.
    payload: dict[str, object] = {
        "proposalId": accepted.proposal_id.value,
        "migrationFile": _relative(accepted.migration.destination, root),
        "bodyFiles": [_relative(move.destination, root) for move in accepted.bodies],
        "replacedBodies": [
            _relative(move.destination, root) for move in accepted.bodies if move.replaced
        ],
        # Part of the shape rather than a field that appears on trouble: an
        # empty list says the scan ran and found nothing, and a caller that only
        # ever sees a key when something is wrong learns not to read it. The
        # list is empty under `off` too, where nothing was scanned -- which is
        # exactly what the policy beside it distinguishes (SEC-11).
        "secretScanPolicy": accepted.secret_scan.policy.value,
        "secretFindings": [f.describe() for f in accepted.secret_scan.findings],
        "nextSteps": _accept_steps(accepted),
    }
    # Set only when the move landed but the proposal's own source files could not
    # then be removed: the acceptance succeeded, and this names the leftover so it
    # does not read as a failed run the caller re-drafts (#89).
    if accepted.cleanup_remedy is not None:
        payload["remedy"] = accepted.cleanup_remedy
    _emit(payload, as_json=as_json)


# `secretFindings` is a list of *rendered lines*, and not the list of mappings
# `ingest --json` publishes for its `failures` and `warnings`. Measured against
# the real CLI: `_render` prints a list entry through
# `escape_terminal_controls`, which stringifies a mapping with `repr`, so a
# mapping here reaches a terminal as
# `{'location': 'architecture/...', 'family': 'high-entropy-token', ...}`. That
# is tolerable for an ingest report nobody has to act on, and it is not tolerable
# for the one output whose entire purpose is that a person reads it before
# opening a pull request -- `warn`'s whole contract is "accepted, now go and
# look". The line is `<location>:<line>:<column>: <family> (<prefix>)`, the shape
# every compiler and linter emits, so a `--json` consumer can still split it and
# a human can paste it into an editor.
#
# A finding location is one of a fixed set of channels (#336, #349, #361): a
# body's content or its landed path, a field of the migration document, the
# migration's own bytes, its filename, or the evidence record. Every one is built
# from literals in `proposal_service.py` -- a channel name with an integer index,
# or a document field path assembled from module literals -- and carries no
# contributor or scanned text, so none can smuggle author-controlled characters
# through the human sink (`_render`/`escape_terminal_controls`) or the JSON sink
# (`json.dumps`). The refusal *messages* elsewhere on the accept path hold the
# same discipline as of #360, through `proposal_service._bounded`.


#: What a caller does next, and the one thing about it that surprises people:
#: what this acceptance proved is not what `migrate validate` proves. The
#: acceptance replayed the whole set, so the apply-time invariants hold for what
#: is now in `.theurian/migrations/` (ADR-0027 decision 2); `migrate validate` is
#: still schema conformance plus the statically decidable set guards, by recorded
#: design (#36), so it is not the thing that re-establishes that after a hand
#: edit.
_ACCEPT_STEPS: Final = (
    "Review the diff, then open a pull request with the proposal directory in it. "
    "The merge is the approval.",
    "This acceptance already proved the set applies: the migration and every one in "
    "`.theurian/migrations/` were replayed together, source anchors and revision-id "
    "reuse included. `theurian migrate validate --json` re-checks schema conformance "
    "and the whole-set guards, and does not replay.",
    "Once it has merged: `theurian migrate apply --json`, then "
    "`theurian index build --json`, or the knowledge just approved is not searchable.",
)

#: What replaces the first accept step for a proposal drafted with `--local`.
#: The step it replaces tells the author to put the proposal directory in the
#: pull request, and for a local one that instruction cannot be followed without
#: `git add -f` -- which is the publication `--local` was chosen to prevent
#: (ADR-0028). The migration and the body have left the ignored directory by
#: now, so the pull request is complete without it; what stays behind is
#: `evidence.json`, and saying so is what stops a reviewer looking for a file
#: that was never going to arrive.
_LOCAL_ACCEPT_FIRST_STEP: Final = (
    "Review the diff, then open a pull request with the migration and the body in it. "
    "The merge is the approval. This proposal was drafted under "
    ".theurian/proposals-local/, which is git-ignored, so what is left there -- "
    "evidence.json -- stays on this machine and is not part of the pull request."
)


#: The step a ``warn`` acceptance that landed a flagged value gets, ahead of
#: everything else. Under ``warn`` the acceptance succeeds (exit 0) and the
#: findings ride on ``secretFindings`` -- but with no next step, the report told
#: the author to open a pull request over content the scan believes carries a
#: live credential (code-review M-4, adversarial M-3). The wording is
#: :meth:`~theurian.application.proposal_service.ProposalService._secret_refusal`'s
#: rotate advice, in the tense the landed case needs: the value is already in
#: ``.theurian/knowledge/`` or ``.theurian/migrations/`` rather than still in the
#: proposal. It names no single file, because a finding may sit in a body, a
#: migration field, the migration's own bytes, its filename or a body's path
#: (#336, #349) and only ``secretFindings`` knows which.
_ROTATE_ADVICE_STEP: Final = (
    "The secret scan flagged something this acceptance landed (security.secretScan is `warn`, "
    "so it proceeded). Treat each flagged value as exposed and rotate it -- it is now in the "
    "working tree, and in Git history once this is committed. The findings, with their "
    "locations, are in `secretFindings`. If any is a false positive, no action is needed for "
    "it."
)


def _accept_steps(accepted: AcceptedProposal) -> list[str]:
    """The accept steps, with the first one corrected for a local proposal and a
    rotate step prepended when ``warn`` landed a flagged body.

    Only the first of the standing steps differs for a local proposal, because
    only it names the proposal directory. Built from :data:`_ACCEPT_STEPS`' own
    tail rather than restated, so the two lists cannot drift where they agree.

    A ``warn`` finding prepends :data:`_ROTATE_ADVICE_STEP`: the acceptance
    succeeded and the exit code says nothing is wrong, so the rotate instruction
    has to live in the steps or it lives nowhere (findings are non-empty only
    under ``warn`` -- ``block`` refuses and ``off`` scans nothing).
    """
    standing = (
        list(_ACCEPT_STEPS)
        if not accepted.local
        else [_LOCAL_ACCEPT_FIRST_STEP, *_ACCEPT_STEPS[1:]]
    )
    if accepted.secret_scan.findings:
        return [_ROTATE_ADVICE_STEP, *standing]
    return standing


#: The steps that follow a draft. The judgement and the moves are the human's;
#: `propose accept` automates the moves and the check that they are safe to make,
#: never the approval (ADR-0013 point 4, ADR-0027 decision 2).
#:
#: The third step names no proposal directory, deliberately. It used to say
#: "while a proposal is still under .theurian/proposals/", which is the wrong
#: half of the sentence and became false outright for a `--local` draft: what
#: `migrate validate` reads is the *landed* set, so the fact that decides its
#: answer is where the migration is, not where the proposal is. The draft's own
#: `proposalDirectory` field reports the location (ADR-0028).
_DRAFT_STEPS: Final = (
    "Read the proposal. Nothing has been approved and nothing has moved.",
    "If you agree: `theurian propose accept <proposal-id>` moves the migration and the "
    "body into place. That is the file moves, not the approval.",
    "`theurian migrate validate --json` reads .theurian/migrations/ only, so it reports "
    "nothing at all until this proposal's migration has been moved there.",
    "The invariants `theurian migrate apply` enforces -- a revision's source anchor, a "
    "reused revision id -- are checked by `theurian propose accept`, before it moves "
    "anything. A proposal that would not apply is refused with nothing consumed.",
)

#: The step a `--local` draft gets and an ordinary one does not. It states the
#: two properties the flag bought and the one it cost, because all three are
#: surprising to someone reading a path that is not where proposals normally go
#: (ADR-0028: the availability residual is accepted, not fixed).
_LOCAL_DRAFT_STEP: Final = (
    "This proposal is under .theurian/proposals-local/, which this draft made sure the "
    "Theurian ignore block covers: it will not appear in `git status`, and it does not "
    "travel to a clone or into a pull request. `git clean -xdf` deletes it, so this is a "
    "copy and not the only home for anything you need to keep."
)


def _refuse_blank_scope_paths(scope_paths: tuple[str, ...], *, as_json: bool) -> None:
    """Refuse a ``--scope-path`` that names no path.

    ``revisionMetadata.scope.paths`` items carry no ``minLength``, so ``""`` or a
    whitespace-only value stages a scope entry that matches nothing and reads in
    review as an authoring slip. Refuse it here -- naming the option, staging
    nothing -- rather than packaging a glob that can never apply (#249).
    """
    if any(not path.strip() for path in scope_paths):
        _refuse(
            "--scope-path was given an empty path.",
            remedy="Pass a path glob such as docs/**, or drop --scope-path.",
            as_json=as_json,
        )


def _refuse_unusable_labels(labels: tuple[str, ...], *, as_json: bool) -> None:
    """Refuse an empty ``--label`` or one bearing a control character.

    ``revisionMetadata.labels.items`` carry only ``maxLength``, unlike ``title``
    and ``owner`` whose ``pattern`` forbids control characters. #249 first opened
    this arbitrary-label path, so it closes the two values that corrupt the
    reviewed migration text: an empty label groups by nothing, and a C0 or DEL
    control character -- a newline splits the label across lines, a NUL truncates
    it -- makes the YAML read as something other than what was passed. The public
    schema is unchanged here; this is the CLI refusing before it stages (#249).
    """
    for label in labels:
        if not label:
            _refuse(
                "--label was given an empty value.",
                remedy="Pass a non-empty label, or drop --label.",
                as_json=as_json,
            )
        if any(ord(char) < _C0_CEILING or ord(char) == _DEL for char in label):
            _refuse(
                "--label contains a control character.",
                remedy="Pass a label of printable text, or drop --label.",
                as_json=as_json,
            )


def _draft(inputs: _Inputs, *, as_json: bool) -> None:
    """Read the body, build the request, and report what was written."""
    from theurian.cli.commands import _emit, _fail, _require_project  # noqa: PLC0415 - cycle

    body, content_type = _read_body(inputs.body_file, as_json=as_json)
    context, _ = _require_project(as_json)

    try:
        drafted = _service(context).draft(
            _request(inputs, body=body, content_type=content_type), local=inputs.local
        )
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

    _emit(_drafted_payload(drafted, context, inputs), as_json=as_json)


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
        # a different reader. The anchors in `evidence.json` are read by the
        # humans reviewing the pull request and by no code path, while
        # `metadata.sourceAnchors` is what `theurian migrate apply` enforces
        # (INV-8). Neither substitutes for the other, which is why they stay
        # separate fields here and separate files on disk even when this surface
        # fills both from one option.
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
        labels=_merge_labels(inputs.labels, authored_here=inputs.authored_here),
        scope_paths=inputs.scope_paths,
        trust_level=inputs.trust_level,
        sensitivity=inputs.sensitivity,
        namespace=inputs.namespace,
        expected_revision=(
            None if inputs.expected_revision is None else RevisionId.parse(inputs.expected_revision)
        ),
    )


def _merge_labels(labels: tuple[str, ...], *, authored_here: bool) -> tuple[str, ...]:
    """The caller's labels and the authored-here label, deduplicated in order.

    ``revisionMetadata.labels`` is ``uniqueItems``, so a caller who passes
    ``--label authored-in-theurian`` beside ``--authored-here`` must not produce a
    document the generator's own validation then rejects. ``--authored-here`` is
    INV-8's declaration for source-less knowledge, not a synonym for the label, so
    it is added rather than assumed and never dropped. First-seen order is kept
    because the migration is reviewed as text. ``dict.fromkeys`` is the dedupe:
    it preserves insertion order and collapses the repeat.
    """
    ordered = (*labels, AUTHORED_IN_THEURIAN) if authored_here else labels
    return tuple(dict.fromkeys(ordered))


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
        project_id=context.project_id,
        clock=context.clock,
        ids=context.ids,
        validate=lambda document: validate_migration_document(document, schemas),
        current_revision=lambda item_id: current_revision_in(migrations, item_id),
        # The landed-migration lookup is this same MigrationSet's own `_by_id`
        # (keyed by inner id), so `propose accept` cannot disagree with
        # `migrate validate`/`apply` about what is in place (ADR-0003, #253).
        landed_migration=migrations.get,
        # And the same set handed over whole, for the pin guard, whose question
        # is not keyed by id: "does any migration already in place pin the bytes
        # at this path?". It used to answer that by globbing
        # `.theurian/migrations/*.yaml` itself and skipping symlinked entries the
        # loader follows, so a pin held by a relocated migration was invisible to
        # it and `accept` overwrote the body the set validates against (#234).
        landed_migrations=lambda: migrations,
        # The accept pre-check's dry replay. `rehearse_migration_set` reaches the
        # engine through the same `apply_migration_set` `migrate apply` calls,
        # which is ADR-0027 decision 2's hard condition: the two cannot disagree
        # about whether a set is usable, because there is one pipeline, not two.
        rehearse=lambda candidate: rehearse_migration_set(candidate, clock=context.clock),
    )


def _drafted_payload(
    drafted: DraftedProposal, context: CommandContext, inputs: _Inputs
) -> dict[str, object]:
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
        "nextSteps": _draft_steps(inputs),
    }


def _draft_steps(inputs: _Inputs) -> list[str]:
    """The next-steps list, with the governed-defaults warning first when owed.

    #249: an omitted ``--trust-level`` or ``--sensitivity`` is not an error --
    the schema defaults are the honest answer for an agent's unreviewed draft --
    but the loader fills ``unverified``/``internal`` silently, and both are
    published on every retrieval result. The migration is deliberately left
    without those keys (writing them would state a judgement the caller never
    made, and would break the compatibility pin), so the surfacing lives here:
    naming the default the revision will publish, and how to set it, is what
    keeps a reviewed, public ADR from acquiring ``unverified``/``internal`` with
    nothing telling the caller.

    The ``--local`` step follows the same warning-first ordering and for the same
    reason: it says what a reader would otherwise have to infer from a path.
    """
    leading = [
        step
        for step in (
            _governed_defaults_note(inputs.trust_level, inputs.sensitivity),
            _LOCAL_DRAFT_STEP if inputs.local else None,
        )
        if step is not None
    ]
    return [*leading, *_DRAFT_STEPS]


def _governed_defaults_note(
    trust_level: TrustLevel | None, sensitivity: Sensitivity | None
) -> str | None:
    """Name the governed fields left unset and the default each will publish.

    ``None`` when both were given: there is then no default to warn about. The
    defaults are read from :data:`DEFAULT_TRUST_LEVEL` and
    :data:`DEFAULT_SENSITIVITY` -- the same constants the loader's ``.get(...)``
    fallbacks apply when a migration omits these keys -- so the value named here
    and the value the revision will publish are one definition, not two that
    happen to agree.
    """
    omitted = [
        (field, default, option)
        for value, field, default, option in (
            (trust_level, "trustLevel", DEFAULT_TRUST_LEVEL.value, "--trust-level"),
            (sensitivity, "sensitivity", DEFAULT_SENSITIVITY.value, "--sensitivity"),
        )
        if value is None
    ]
    if not omitted:
        return None
    plural = len(omitted) > 1
    fields = " and ".join(field for field, _, _ in omitted)
    defaults = " and ".join(f"{field}: {default}" for field, default, _ in omitted)
    options = " and ".join(option for _, _, option in omitted)
    return (
        f"{fields} {'were' if plural else 'was'} not set, so this revision will publish "
        f"{defaults} -- the schema default{'s' if plural else ''} -- on every knowledge.search "
        f"and knowledge.get result. If that is not right for this knowledge, re-draft with "
        f"{options}."
    )


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
