"""``theurian index`` — build and inspect the retrieval index (FR-R2).

A composition root: this is where the abstract builder meets the SQLite store,
the SQLite index, and the default embedder (ADR-0003).

Building writes a *new* file and then swaps a pointer, and the old build stays on
disk until `theurian index gc` reclaims it.

**That sentence was false from Milestone 5 until now, and it is worth saying
which part.** It claimed a search running across a rebuild "keeps reading a
consistent index rather than one being written underneath it". Publishing reaped
every build the pointer did not name, so the old file was gone before the command
returned; and `SqliteIndexStore` opened a connection per call, so there was no
reader for the old file to stay consistent *for*. The prose described a guarantee
before the mechanism existed to make it true, which is how it survived the
amendment that withdrew it (ADR-0022 point 6).

Both halves now exist. Publishing no longer deletes, and reclaiming is
`theurian index gc`; a search holds one read connection for the duration of a
request (`SqliteIndexStore.session`, ADR-0024 point 7), so a request already in
flight finishes against the build it started on even if `gc` unlinks it
underneath.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Annotated, Any, Final

import typer

from theurian.application.authorization import (
    AuthorizationGrant,
    StaticAuthorizationProvider,
    load_serving_profile,
)
from theurian.application.forest_builder import ForestBuilder
from theurian.application.index_builder import IndexBuilder, IndexRequest
from theurian.application.index_secret_scan import (
    LANDED_SECRET_REMEDY,
    write_index_secret_scan,
)
from theurian.application.project_service import (
    INDEX_POINTER_REMEDY,
    UNBUILT_STATE_REMEDY,
    BuildProvenance,
    ProjectPathEscapeError,
    ProjectPaths,
    read_active_index_pointer,
    write_active_index_pointer,
)
from theurian.cli.index_status_report import (
    index_staleness,
    remedy_for,
)
from theurian.domain.context import RequestContext
from theurian.domain.enums import SURFACEABLE_STATUSES, KnowledgeStatus, Sensitivity
from theurian.domain.errors import TheurianError
from theurian.domain.state import ActiveState
from theurian.infrastructure.embedding import HashingEmbedding
from theurian.infrastructure.raptor.extractive import ExtractiveSummarizer
from theurian.infrastructure.secrets.file_store import default_data_dir
from theurian.infrastructure.sqlite.index_store import SqliteIndexStore, fts5_available
from theurian.infrastructure.sqlite.store import SqliteCanonicalStore
from theurian.security.project_config import SecretScanPolicy, read_secret_scan_policy

index_app = typer.Typer(help="Build and inspect the retrieval index.", no_args_is_help=True)

JsonOption = Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")]

#: What ``theurian index build`` exits with when it published an index that
#: appears to carry a secret and ``security.secretScan`` is ``block`` (SEC-11,
#: #329).
#:
#: **Distinct from 1 because the two outcomes are opposite**, and a pipeline has
#: to be able to tell them apart: exit 1 from this command means nothing was
#: published and retrieval still uses the previous build, while this means a
#: *complete* index was published and something in it needs rotating. Collapsing
#: them would make a CI job that stops on a secret also stop on a corrupt state
#: database, and vice versa. Beside ``EXIT_STATE_ERROR`` (4) and
#: ``EXIT_NEEDS_CONSENT`` (5), each declared in the module that owns it.
EXIT_SECRET_FOUND = 6

#: Cure for an OS refusal *publishing* the pointer, met after the build itself
#: succeeded (#525).
#:
#: Names the pointer rather than the index, because that is what is wrong: the
#: build is complete and on disk under its final name, and the only thing that
#: did not happen is the atomic swap that would make retrieval read it. Sending
#: the reader to rebuild would spend the corpus scan again on a file that is
#: already correct, and land at the identical refusal.
#:
#: ``active-index.json`` is derived (ADR-0004) and a directory or an unwritable
#: mode at its path is something a person put there, so the instruction is to
#: clear the path -- never to delete `.theurian/state/`, which holds the
#: canonical database beside it.
_UNWRITABLE_INDEX_POINTER_REMEDY: Final = (
    "Make sure `.theurian/state/active-index.json` is a writable file or absent -- a "
    "directory or an unreadable mode at that path is what refuses the swap -- then run "
    "`theurian index build` again. The build that just ran is still on disk and nothing "
    "authored is at risk."
)


@index_app.command("build")
def index_build(  # noqa: PLR0911 -- one early return per distinguishable failure shape, the
    # precedent `migrate_apply` sets: three preconditions, an empty build, and the two ways
    # publishing can be refused. Folding the last three into one `try` would make an `OSError`
    # from the provenance record read as "the pointer could not be written" (#525).
    include_unapproved: Annotated[
        bool,
        typer.Option(
            "--include-unapproved",
            help="Also index drafts and proposals. Never indexes rejected knowledge.",
        ),
    ] = False,
    no_embeddings: Annotated[
        bool,
        typer.Option("--no-embeddings", help="Build lexical search only."),
    ] = False,
    raptor: Annotated[
        bool,
        typer.Option(
            "--raptor",
            help="Also derive a RAPTOR summary forest over what was indexed.",
        ),
    ] = False,
    as_json: JsonOption = False,
) -> None:
    """Build a retrieval index from this project's canonical state.

    The index is derived and disposable (ADR-0004). Deleting it costs a rebuild,
    never data — which is why it is a separate file from the canonical store.

    ``--raptor`` derives the summary forest ADR-0008 describes: one node per
    document, per kind, and per scope, each built from the chunks or nodes below
    it. It is opt-in because decision 10 says so — a capability whose acceptance
    tests are owed and whose build cost is unmeasured must not arrive as the side
    effect of an upgrade — and the guarantee that buys is hard rather than
    filtered: without the flag the build writes zero node rows.

    A build that finds nothing to index while the canonical state holds knowledge
    is refused rather than published. Publishing it would put a *correct-looking*
    empty index in place: every later search answers ``count: 0`` with
    ``indexed: true``, and ``theurian index status`` reports nothing to do. That
    is the shape a project-id mismatch takes, and this refusal is what turns it
    from silent into a message naming the ids involved.

    Every body it indexes is scanned for secrets under `security.secretScan` in
    `.theurian/config.yaml`, which is `block` when the key is absent (SEC-11). A
    finding does not stop the build: the content is already in the canonical
    store and already served by `knowledge.search` and `knowledge.get`, so
    refusing to publish would deny ranking without hiding anything. Under `block`
    the index is published and the command exits 6, and `theurian doctor` goes on
    reporting it until a build finds nothing; under `warn` it is reported and the
    exit stays 0; under `off` nothing is scanned. Getting a landed secret out
    means rotating it and then removing it from the corpus by the route its
    channel needs: a new `upsertRevision` for a body, a title or a source anchor,
    `removeRelation` for a note on an edge, `deprecateItem` for any of them.
    """
    from theurian.cli.commands import (  # noqa: PLC0415 - cycle
        EXIT_STATE_ERROR,
        _emit,
        _fail,
        _fail_a_path_escape,
        _require_project,
    )

    # The second value is the state database, not the repository root. The
    # context already carries resolved paths, so deriving them again from a
    # database path is both redundant and wrong.
    context, _ = _require_project(as_json)
    paths = context.paths
    active = _require_buildable_state(paths, as_json)
    if active is None:
        return
    grant = _deployment_grant(as_json)
    if grant is None:
        return
    policy = _secret_scan_policy(paths, as_json)
    if policy is None:
        return

    # The context already carries the generator every other command uses, so
    # index build ids sort alongside migration and revision ids.
    index_build_id = context.ids.new_ulid().value
    builder = IndexBuilder(
        store_factory=SqliteCanonicalStore,
        index_factory=SqliteIndexStore,
        embedder=None if no_embeddings else HashingEmbedding(),
        # Composed whether or not `--raptor` was passed, and the request decides
        # whether it runs. `ExtractiveSummarizer` holds no state, opens nothing
        # and reaches no network (ADR-0008 decision 7, OSS-15), so constructing
        # one costs nothing -- while a builder wired only on the flag would make
        # "was a summariser configured" a second thing the flag means.
        forest_builder=ForestBuilder(summarizer=ExtractiveSummarizer()),
    )
    # Built under a name `theurian index gc` does not reap, then renamed into
    # place. `gc` reclaims every build the pointer does not name, and a build in
    # progress is not yet named by it, so without this the two race -- and the
    # loser is the build, silently, mid-write. `os.replace` is atomic on POSIX,
    # so a file under the completed name is complete by construction. The purge
    # writes under the same discipline (`index_purge.purge_into`).
    final_path = paths.index_for(index_build_id)
    request = IndexRequest(
        database=paths.state / active.database_filename,
        index_path=Path(f"{final_path}.building"),
        project_id=context.project_id.value,
        state_hash=str(active.state_hash),
        index_build_id=index_build_id,
        visible_sensitivities=grant.sensitivities,
        secret_scan=policy,
        include_unapproved=include_unapproved,
        raptor=raptor,
    )

    report = _run_build(builder, request, active, as_json=as_json)
    if report is None or _refuse_if_empty(
        report, request, context.project_id.value, as_json=as_json
    ):
        return
    os.replace(request.index_path, final_path)  # noqa: PTH105 - the atomic primitive
    report = {**report, "indexPath": str(final_path)}

    # Both refusals the pointer swap can make are graded here, because neither is
    # a `TheurianError` the command's callees convert and both left `--json` with
    # a Rich traceback and an empty machine channel (#525). The build has already
    # been renamed into place at this point, so what is being reported is "the
    # index exists and is not published", which is why the remedy names the
    # pointer rather than the corpus.
    try:
        _publish(
            paths,
            index_build_id=index_build_id,
            state_hash=str(active.state_hash),
            project_id=context.project_id.value,
            indexes_unapproved=include_unapproved,
            indexed_sensitivities=grant.sensitivities,
        )
    except ProjectPathEscapeError as exc:
        _fail_a_path_escape(exc, as_json=as_json)
        return
    except OSError as exc:
        # A directory at `.theurian/state/active-index.json` is not a containment
        # failure -- nothing escapes the tree, and the chokepoint correctly waves
        # it through -- but it reaches the same `--json` surface and owes the same
        # document. The type name and never the message: an `OSError`'s
        # `strerror` carries the operator's absolute paths, the rule
        # `_record_the_scan`'s warning already keeps.
        _fail(
            f"The index was built, but the pointer that publishes it could not be written "
            f"({type(exc).__name__}), so retrieval is still reading the previous build.",
            remedy=_UNWRITABLE_INDEX_POINTER_REMEDY,
            as_json=as_json,
            code=EXIT_STATE_ERROR,
        )
        return
    # Record that this installation built this index, out of the repository tree,
    # the instant it is published (ADR-0004, SEC-7). The serve-side index gate
    # stands aside any build id it does not find here, so this is what lets the
    # ranked path use the build that was just published.
    BuildProvenance.default().record_index(paths.root, index_build_id)
    findings: list[str] = list(report["secretFindings"])
    try:
        warning = _record_the_scan(
            paths, index_build_id=index_build_id, policy=policy, findings=len(findings)
        )
    except ProjectPathEscapeError as exc:
        # Deliberately *not* folded into `_record_the_scan`'s degrade-to-a-warning
        # arm, whose whole argument is that an incidental write failure must not
        # replace the findings the caller is about to read. A record path that
        # leaves the working tree is not incidental: it is the same doctored
        # `.theurian/state/` every other refusal in this class is about, and
        # reporting it as a footnote on a successful build understates a tree the
        # operator has to repair before any of this means anything.
        _fail_a_path_escape(exc, as_json=as_json)
        return
    if findings:
        # A remedy on a success result, the shape `AcceptedProposal
        # .cleanup_remedy` already has: the build did publish, and telling the
        # operator otherwise would send them to rebuild something that is fine.
        report = {**report, "remedy": LANDED_SECRET_REMEDY}
    if warning is not None:
        report = {**report, "recordWarning": warning}
    # Publishing does not reclaim (ADR-0024 point 6). Reaping the previous build
    # here is what made ADR-0022's "the previous build is not deleted" false, and
    # measured against a reader it cost 2,627 errors against 40 answered searches
    # in 1.5 seconds. `theurian index gc` reclaims, explicitly.
    _emit({**report, "published": True}, as_json=as_json)
    if policy is SecretScanPolicy.BLOCK and findings:
        # Emitted first, and *then* non-zero. The index is published and the
        # report is the operator's only account of what was found, so `_fail`'s
        # shape -- which replaces the payload with `{error, remedy}` on stderr --
        # would hide the very thing that has to be read. This is the signal
        # posture #329 settled: halting a build does not un-disclose a body the
        # canonical store already serves, so `block` is loud rather than
        # obstructive.
        raise typer.Exit(EXIT_SECRET_FOUND)


def _record_the_scan(
    paths: ProjectPaths, *, index_build_id: str, policy: SecretScanPolicy, findings: int
) -> str | None:
    """Write this build's scan record, or say why the report is its only account.

    Written on every publish, clean ones included: this is what clears a previous
    ``degraded`` as well as what raises a new one, and a record kept only on
    trouble would leave the last bad verdict standing over a fixed corpus.

    **A failure here degrades to a warning and never to a traceback**, because of
    where in the command it sits. The index is already published and the pointer
    already swapped; the findings the caller is about to read are the only account
    of what was found, and an unhandled ``OSError`` in this window replaced them
    with a Rich traceback and *empty stdout* -- reproduced round 1 with the record
    path replaced by a directory, and reachable in the field through ENOSPC. So
    the build reports what it found, says the signal will not survive the
    terminal, and still exits on the policy.

    The exception's type name, never its message: an ``OSError``'s ``strerror``
    carries the operator's absolute paths, which no payload here puts in (the rule
    ``_purge_fields``' failure reason already holds). ``theurian doctor`` answers
    ``unrecorded`` for this build afterwards -- honest ignorance, and never a clean
    bill -- which is what makes degrading safe rather than convenient.
    """
    try:
        write_index_secret_scan(
            paths, index_build_id=index_build_id, policy=policy, findings=findings
        )
    except OSError as exc:
        return (
            f"The index published, but this build's secret-scan record could not be written "
            f"({type(exc).__name__}), so `theurian doctor` will report `unrecorded` for it "
            f"rather than what is listed above. Make `.theurian/state/` writable and run "
            f"`theurian index build` again to record the verdict."
        )
    return None


def _require_buildable_state(paths: ProjectPaths, as_json: bool) -> ActiveState | None:
    """Preconditions for a build: applied canonical state, and FTS5 support.

    Split out so `index_build` reads as build-then-publish, with everything
    that can refuse the attempt before it starts gathered in one place.
    """
    from theurian.cli.commands import _fail, _read_active  # noqa: PLC0415 - cycle

    # `_read_active`, not `read_active_state`: an unreadable pointer and an
    # absent one need different cures, and this function's own refusal below --
    # "run `theurian migrate apply` first" -- is the wrong one for a file that
    # has to be deleted before applying can help.
    active = _read_active(paths, as_json)
    if active is None:
        _fail(
            "This project has no built knowledge state, so there is nothing to index.",
            remedy="Run `theurian migrate apply` first.",
            as_json=as_json,
            code=1,
        )
        return None

    if not fts5_available():
        _fail(
            "This Python's SQLite was built without FTS5, so lexical search is unavailable.",
            remedy="Install Python from python.org or your distribution's python3 package.",
            as_json=as_json,
            code=1,
        )
        return None

    # Refuse to build an index *from* canonical state this installation did not
    # produce (ADR-0004, SEC-7). Without this, a doctored `.theurian/state/`
    # shipped in a repository launders through the build: `index build` reads its
    # rows, writes them into a fresh index, and records *that* index as this
    # install's -- so the serve-side index gate would then vouch for it. The
    # canonical state must be one this install built before anything is derived
    # from it.
    if not BuildProvenance.default().has_state(paths.root, str(active.state_hash)):
        _fail(
            "This project's canonical state was not built by this Theurian installation, so "
            "an index built from it cannot be trusted. It was delivered with the project "
            "rather than rebuilt here from the Git-tracked migrations (ADR-0004).",
            remedy=UNBUILT_STATE_REMEDY,
            as_json=as_json,
            code=1,
        )
        return None

    return active


def _deployment_grant(as_json: bool) -> AuthorizationGrant | None:
    """What this deployment serves, or ``None`` once the refusal has been reported.

    Resolved through the same :class:`StaticAuthorizationProvider` the daemon
    composes (``daemon.runner.serve``) and out of the same operator-owned data
    directory, so a build and the daemon that will serve it cannot expand one
    declared ceiling two different ways. A build is a *write*, and what it writes
    is what the FTS5 collection statistics are computed over, so the two agreeing
    is what makes the exclusion worth anything (ADR-0025 part 1).

    An unreadable profile refuses the build rather than defaulting, for the reason
    :func:`~theurian.application.authorization.load_serving_profile` gives about
    the daemon's start: a malformed ceiling that fell back to this build's default
    would write *more* into the index than its operator asked for, silently -- and
    an index row's text is in the file whatever a later query does with it. Its
    own ``remedy`` names the file and the words that belong in it, which is why it
    is preferred here over the generic one.
    """
    from theurian.cli.commands import _fail  # noqa: PLC0415 - cycle

    try:
        profile = load_serving_profile(default_data_dir())
    except TheurianError as exc:
        _fail(
            str(exc),
            remedy=exc.remedy or "Run `theurian doctor`.",
            as_json=as_json,
            code=1,
        )
        return None
    return StaticAuthorizationProvider(profile).deployment_grant()


def _secret_scan_policy(paths: ProjectPaths, as_json: bool) -> SecretScanPolicy | None:
    """What this project does about a secret in what it serves (SEC-11, #329).

    The same reader ``propose accept`` uses, on the same key, so the two SEC-11
    controls cannot end up meaning different things by the same configuration.
    Absent means ``block`` and unrecognised means refuse -- both rules belong to
    :func:`~theurian.security.project_config.read_secret_scan_policy`, and neither
    is re-decided here.

    Read at the composition root rather than inside the builder, unlike the accept
    path, and the difference is what the two layers can reach. ``ProposalService``
    holds a ``ProjectPaths`` and so can find ``.theurian/config.yaml`` itself,
    which is what makes the control impossible for a caller to omit. A build is
    addressed by a *database path* and has no project root at all, so the
    requirement moves onto ``IndexRequest.secret_scan``, which has no default and
    therefore cannot be forgotten either.

    Read before the build starts, beside the other two preconditions, so a
    configuration file the build cannot act on refuses on the first build rather
    than after the corpus has been read. Its own ``remedy`` names the file and the
    three values, which is why it is preferred over a generic one.
    """
    from theurian.cli.commands import _fail, _fail_a_path_escape  # noqa: PLC0415 - cycle

    try:
        return read_secret_scan_policy(paths.root, paths.config)
    except ProjectPathEscapeError as exc:
        # `paths.config` is resolved on the way in, so a `.theurian/config.yaml`
        # that leaves the working tree refuses here rather than inside the
        # reader. That is a doctored tree, not a configuration this command can
        # be told to fix, and it takes the containment class's one grading
        # (#525) -- the generic branch below graded it 1 while the same tree's
        # state-database face graded 4.
        _fail_a_path_escape(exc, as_json=as_json)
        return None
    except TheurianError as exc:
        _fail(
            str(exc),
            remedy=exc.remedy or "Run `theurian doctor`.",
            as_json=as_json,
            code=1,
        )
        return None


def _run_build(
    builder: IndexBuilder, request: IndexRequest, active: ActiveState, *, as_json: bool
) -> dict[str, Any] | None:
    """Run the build, converting a broken canonical store into ``{error, remedy}``.

    A half-built index is worse than none: it looks complete and is not, and
    only the build knows it failed. Nothing is published on either failure
    branch below, so retrieval keeps using the previous build -- which is why
    the partial file is unlinked before reporting, not after.
    """
    from theurian.cli.commands import _fail  # noqa: PLC0415 - cycle

    try:
        return builder.build(request)
    except (TheurianError, sqlite3.Error, OSError) as exc:
        # A corrupt or unreadable state database used to escape as a raw
        # `sqlite3.DatabaseError`: exit 1, empty stdout, a traceback at the user,
        # and none of the `{"error", "remedy"}` shape every other command
        # promises (CP-2) -- in exactly the situation that is recoverable,
        # because canonical state rebuilds from Git-tracked migrations.
        request.index_path.unlink(missing_ok=True)
        _fail(
            f"Building the index failed: {exc}",
            remedy=(
                f"Nothing was published, so retrieval still uses the previous index. If the "
                f"state database is unreadable, delete .theurian/state/"
                f"{active.database_filename} and run `theurian migrate apply`, then retry; "
                f"canonical state rebuilds from Git-tracked migrations."
            ),
            as_json=as_json,
            code=1,
        )
        return None
    except Exception:
        request.index_path.unlink(missing_ok=True)
        raise


def _refuse_if_empty(
    report: dict[str, Any], request: IndexRequest, project_id: str, *, as_json: bool
) -> bool:
    """Refuse to publish a build that indexed nothing while the store holds
    knowledge, and report whether it was refused.

    Publishing it would put a *correct-looking* empty index in place: every
    later search answers ``count: 0`` with ``indexed: true``, and ``theurian
    index status`` reports nothing to do -- the shape a project-id mismatch
    takes, indistinguishable from a project that simply has no knowledge yet.
    """
    from theurian.cli.commands import _fail  # noqa: PLC0415 - cycle

    if report["chunks"] != 0:
        return False
    try:
        available = _indexable_items(
            request.database,
            include_unapproved=request.include_unapproved,
            visible_sensitivities=request.visible_sensitivities,
        )
    except TheurianError as exc:
        # A second read session over the same file, so `_run_build`'s conversion
        # one function above does not cover it -- and it reaches rows the build
        # itself never touches. Measured against the real CLI on a project whose
        # only knowledge is `draft`, so the build indexes zero chunks and this
        # line runs: damaging `projects.registered_at` or `projects.root_path`
        # ended `theurian index build --json` in a Rich traceback, exit 1, empty
        # stdout, and the cell published through `__cause__` -- the same escape
        # the `migrate` commands were just converted for.
        request.index_path.unlink(missing_ok=True)
        _fail(
            f"This build indexed nothing, and the canonical state could not say whether "
            f"that is correct: {exc}",
            remedy=(
                "Nothing was published, so retrieval still uses the previous index. Delete "
                ".theurian/state/ and run `theurian migrate apply`, then retry; canonical "
                "state rebuilds from Git-tracked migrations."
            ),
            as_json=as_json,
            code=1,
        )
        return True
    if not available:
        return False
    request.index_path.unlink(missing_ok=True)
    _fail(
        f"Indexing produced no chunks, but the canonical state holds knowledge "
        f"({_render_counts(available)}). This build indexed {project_id!r}.",
        remedy=_empty_build_remedy(project_id, available),
        as_json=as_json,
        code=1,
    )
    return True


@index_app.command("status")
def index_status(as_json: JsonOption = False) -> None:
    """Report whether the index still reflects the project's knowledge.

    A stale index is a correctness problem wearing the costume of a relevance
    problem: searches keep working, and quietly answer from knowledge that has
    changed.

    Three hashes matter and they can all differ, so all three are reported:

    - ``currentStateHash`` — derived from the migrations and their content,
      i.e. what the knowledge *is* right now;
    - ``builtStateHash`` — what the canonical database actually holds;
    - ``indexStateHash`` — what the index was built from.

    Comparing only the last two would call an index fresh whenever the database
    was equally out of date, which is precisely when a person most needs to be
    told otherwise.

    A pointer file that exists but does not name a usable build -- truncated
    JSON, a JSON array, an object without ``indexBuildId``, arbitrary bytes --
    is reported as ``indexPointerCorrupt`` rather than folded into "never
    built". ``knowledge.search`` already tells an agent this exact file is
    unreadable and names it for deletion (``index-pointer-invalid``); reporting
    "no built state" here for the same file would send a person to run
    ``theurian migrate apply`` for state that was never the problem, while the
    corrupt file sat on disk the whole time.

    **The invariant that governs all of it: whatever the ranked path refuses,
    this command reports as stale.** It was stated above about the schema
    version and held about nothing else. A build whose recorded disclosure
    flavor is not the one in force degrades every ``knowledge.search`` to an
    unranked scan with ``indexed: false`` (``serving-profile-mismatch``), and
    this command answered ``stale: false`` with an empty remedy -- while
    ``mcp/search.py``'s own note for that fallback says an operator would be told
    *here*. The levels are named on both sides, because that is exactly what the
    fallback withholds from an agent and defers to this terminal.

    The invariant now lives in :func:`~theurian.cli.index_status_report.
    index_staleness`, and this command re-derives every index-side field it
    publishes from that one call rather than judging the axes itself.
    ``theurian project status`` publishes the same verdict as ``indexStale``
    from the same call, which is what issue #100 is about: two surfaces
    answering one fact used to compute it twice, and one of them computed it
    from the wrong file.
    """
    from theurian.cli.commands import (  # noqa: PLC0415 - cycle
        _emit,
        _fail_a_path_escape,
        _read_active,
        _require_project,
    )

    context, _ = _require_project(as_json)
    paths = context.paths
    # Converted here as well as inside `_require_project`, which has already read
    # the same file: the two reads straddle a window in which `migrate apply` --
    # or the very deletion this command's remedy asks for -- replaces the
    # pointer, and a raise landing in it would cost the whole payload.
    active = _read_active(paths, as_json)

    current = str(context.state_hash)
    built = str(active.state_hash) if active else None
    # About the state database, not the index, which is why it is computed here
    # and not inside `index_staleness`: it makes no build stale. This command
    # publishes it as `knowledgeNotApplied` below and hands it to `remedy_for`,
    # where it decides that applying must precede any rebuild -- neither of which
    # is a staleness axis, and both of which belong to the command that owns the
    # canonical-state half of this payload.
    needs_apply = built != current

    # The verdict resolves `active_index_pointer`, so a doctored `.theurian/
    # state/` refuses here rather than reaching the payload -- and the refusal is
    # a document with the class's one exit code, not the Rich traceback with an
    # empty machine channel it published at `491bded6` (#525). This command's
    # contract is to *report* on a broken index, and it keeps it for every shape
    # of broken pointer the reader can parse; a path that leaves the working tree
    # is not one of those, because nothing here can say what it points at.
    try:
        index = index_staleness(
            paths, project_id=context.project_id.value, current_state_hash=current
        )
    except ProjectPathEscapeError as exc:
        _fail_a_path_escape(exc, as_json=as_json)
        return

    _emit(
        {
            **index.payload,
            "builtStateHash": built,
            "currentStateHash": current,
            "projectId": context.project_id.value,
            "stale": index.stale,
            "knowledgeNotApplied": needs_apply,
            "remedy": remedy_for(
                stale=index.stale,
                needs_apply=needs_apply,
                orphaned=index.orphaned,
                pointer_corrupt=index.pointer_corrupt,
                purge_failed=index.purge_failed,
                profile_remedy=index.profile_remedy,
            ),
        },
        as_json=as_json,
    )


#: Filenames `theurian index gc` will consider at all.
#:
#: **The prefix is the whole safety property, not a tidiness convention.**
#: `theurian-state-<hash>.sqlite` -- the canonical store, which is *not* derived
#: and *not* disposable -- lives in the same directory. A glob that lost this
#: prefix would reclaim it, and the suite stayed green when exactly that mutation
#: was applied. ADR-0022 point 2 says the prefix is load-bearing for the same
#: reason, one layer up: "a glob that could not tell them apart would hand a
#: retrieval index to the canonical store".
INDEX_FILENAME_PREFIX: Final = "theurian-index-"


@index_app.command("gc")
def index_gc(
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Report what would be reclaimed, and reclaim nothing."),
    ] = False,
    as_json: JsonOption = False,
) -> None:
    """Delete index builds the published pointer does not name.

    Publishing a build no longer deletes the one it replaced (ADR-0024 point 6),
    so builds accumulate: measured on an 800-document corpus, ten publishes leave
    ten files and 246.0 MB where one build is 24.5 MB. This is what reclaims
    them, and it is explicit for the reason ADR-0007 and ADR-0017 already give
    for state databases -- automatic deletion of a file something may still be
    reading is the failure this command exists to avoid, not the service it
    provides.

    **What a search in flight is guaranteed, and from when.** A request that has
    already entered `SqliteIndexStore.session` holds an open descriptor, and on
    POSIX that keeps the file readable after its name is gone: measured, one
    request of four index reads with the unlink landing after the first answered
    4 of 4 inside a session against 1 of 4 with a connection per call. The
    guarantee starts at session acquisition, not at the moment the request
    arrived -- a request reaped between resolving the pointer and acquiring its
    connection has no descriptor to protect, and degrades to the substring-scan
    fallback rather than failing. A request that *starts* after the reap resolves
    the pointer to the published build, which is never reclaimed here.

    Four things are never reclaimed:

    - the build the pointer names, and a `gc` run at all is refused when that
      build's file is missing -- a pointer aimed at nothing is a broken project,
      and treating every real build as unreferenced is the worst possible reading
      of it;
    - any build whose id sorts **above** the published one, which is a build that
      started later and has not published yet;
    - anything under a `.building` suffix, which is a writer that has not renamed
      into place -- or the leftovers of one that crashed, which this cannot tell
      apart and so reports rather than deletes;
    - everything, when the pointer cannot be read at all, because a pointer this
      command cannot parse is not evidence that any particular build is
      unreferenced.
    """
    from theurian.cli.commands import (  # noqa: PLC0415 - cycle
        _emit,
        _fail,
        _fail_a_path_escape,
        _require_project,
    )

    context, _ = _require_project(as_json)
    paths = context.paths
    # `read_active_index_pointer` absorbs every way the pointer's *contents* can
    # be wrong -- that is what `unreadable` below reports -- but it resolves the
    # path first, and a path that leaves the working tree is not a contents
    # problem. Left uncaught it ended this command in a Rich traceback with an
    # empty machine channel (#525); refusing here is the same answer the
    # unreadable branch gives, with the class's own grading and cure.
    try:
        pointer = read_active_index_pointer(paths)
    except ProjectPathEscapeError as exc:
        _fail_a_path_escape(exc, as_json=as_json)
        return
    if pointer.unreadable:
        _fail(
            "This project's active index pointer cannot be read, so nothing can be shown to "
            "be unreferenced.",
            remedy=INDEX_POINTER_REMEDY,
            as_json=as_json,
            code=1,
        )
        return

    published = str((pointer.payload or {}).get("indexBuildId", ""))
    if published and not paths.index_for(published).is_file():
        _fail(
            f"The published index pointer names build {published}, and that build's file is "
            f"not there. Nothing was reclaimed.",
            remedy=(
                "Run `theurian index build` to publish a build that exists. Reclaiming now "
                "would delete every build on disk, because none of them is the published one."
            ),
            as_json=as_json,
            code=1,
        )
        return

    reclaimable = _reclaimable(paths, published=published)
    stranded = sorted(path.name for path in paths.state.glob(f"{INDEX_FILENAME_PREFIX}*.building"))
    freed = sum(path.stat().st_size for path in reclaimable if path.is_file())
    if not dry_run and not _reclaim(reclaimable, as_json=as_json):
        return

    _emit(
        {
            "publishedIndexBuildId": published or None,
            "reclaimed": sorted(path.name for path in reclaimable),
            "bytesReclaimed": freed,
            # Reported, never deleted. A `.building` file is a live writer or a
            # crash's leftovers and this cannot tell them apart, so telling the
            # operator what is stranded is the honest half of the job; reclaiming
            # it needs an age or liveness heuristic that does not exist yet.
            "strandedBuilding": stranded,
            "dryRun": dry_run,
        },
        as_json=as_json,
    )


def _reclaim(paths_to_remove: list[Path], *, as_json: bool) -> bool:
    """Unlink each file, converting an OS refusal into this command's contract.

    Returns whether the run may report success.

    An unguarded loop is the CP-2 escape `_refuse_if_empty` was built for: a
    read-only state directory raised a bare `PermissionError` through Typer as a
    Rich traceback, exit 1, and **empty stdout under `--json`** -- for a
    condition whose remedy is one `chmod`. `IsADirectoryError` did the same for a
    directory named like a build.
    """
    from theurian.cli.commands import _fail  # noqa: PLC0415 - cycle

    for path in paths_to_remove:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            _fail(
                f"Reclaiming {path.name} failed: {exc.strerror or exc}.",
                remedy=(
                    "Check that .theurian/state/ is writable and holds no directory named "
                    "like an index build, then run `theurian index gc` again. Nothing else "
                    "was reclaimed."
                ),
                as_json=as_json,
                code=1,
            )
            return False
    return True


def _reclaimable(paths: ProjectPaths, *, published: str) -> list[Path]:
    """Index files the pointer does not name, and their sidecars.

    **Two mechanisms, because neither covers the other's window**, and keeping
    only the second is what a review caught:

    - the `.building` suffix covers a writer that has not finished. Both the
      builder and the purge write there and `os.replace` into position, so a file
      under the completed name is complete by construction. The glob is
      `*.sqlite` and not `*.sqlite*` for exactly this reason.
    - **ULID ordering covers a writer that has finished and not yet published.**
      `index_build` renames into place and *then* writes the pointer, and a `gc`
      landing between those two reclaimed the file, after which the pointer named
      nothing. Reproduced against the real CLI: 8 of 12 runs published a pointer
      to a reclaimed build. `.building` cannot see this window -- the file is
      already under its final name -- so the rule the old `_reclaim` carried is
      restored beside it.

    **Neither is complete alone, and in the shipped default there is no residual
    at all.** What bounds it is that both index producers are CLI commands and a
    person runs one at a time -- ADR-0018's own "serialised by nothing but the
    fact that a person runs it" -- so only one build or purge is in flight and
    the ULID rule is exact: a finished build's id is strictly above the published
    one until it publishes.

    This paragraph said "the daemon serialises every write through one lock
    (ADR-0018)" until PR #498's round-one review, and both halves were wrong. The
    daemon runs **no** build and no purge: `IndexBuilder`, `derive_purged` and
    `publish_purge_for_withdrawal` appear nowhere under `daemon/` or `mcp/`
    (measured 2026-09-02; the same key returns twelve files elsewhere under
    `src/`, which is the control that says it can match). And there is no index
    write lock for them to be serialised by -- ADR-0024 decision 4's dated
    correction records that no index write path takes one, and that the eleven
    writable opens across `index_store.py` and `index_purge.py` are serialised
    against each other by nothing. The lock ADR-0018 names guards the *state*
    databases.

    A residual appears only under the *unsupported* configuration of concurrent direct-CLI
    builds against one project, and even then it is narrow -- a build reclaimed in
    the write-to-publish window must have an id sorting **below** the published
    one, which means another build minted a later id and published first while
    this one was between its `os.replace` and its `_publish`. The only writers are
    `index build` and the purge, both of which take the `.building` path, so an
    unfinished build is never a candidate regardless. Recorded rather than closed
    because the supported deployment does not reach it, and closing it for the
    unsupported one is the single-writer interface ADR-0018 still owes the index.

    Sidecars are matched explicitly rather than by widening the glob, because
    `-wal` and `-shm` are the only two that exist and `*` would take `.building`
    with them.

    An empty `published` reclaims nothing. A project with no pointer has no
    build that is *known* to be unreferenced -- every file on disk might be the
    one a rebuild is about to name -- and deleting on that basis is how a `gc`
    turns "you have not built an index yet" into "your index is gone".
    """
    if not published:
        return []

    reclaimable: list[Path] = []
    for build in sorted(paths.state.glob(f"{INDEX_FILENAME_PREFIX}*.sqlite")):
        build_id = build.name[len(INDEX_FILENAME_PREFIX) : -len(".sqlite")]
        if build_id >= published:
            # `>=` rather than `!=`: equal is the published build, and greater is
            # a build that started later and may be about to publish.
            continue
        reclaimable.append(build)
        reclaimable.extend(
            sidecar for sidecar in (Path(f"{build}-wal"), Path(f"{build}-shm")) if sidecar.exists()
        )
    return reclaimable


def _publish(  # noqa: PLR0913 - the pointer's field set, forwarded whole
    paths: ProjectPaths,
    *,
    index_build_id: str,
    state_hash: str,
    project_id: str,
    indexes_unapproved: bool,
    indexed_sensitivities: frozenset[Sensitivity],
) -> None:
    """Point retrieval at a finished build, atomically.

    A thin alias over :func:`~theurian.application.project_service.
    write_active_index_pointer`, kept so this module reads as build-then-publish
    and so the pointer swap has one implementation shared with the
    withdrawal-triggered purge. The reasoning lives at the definition.
    """
    write_active_index_pointer(
        paths,
        index_build_id=index_build_id,
        state_hash=state_hash,
        project_id=project_id,
        indexes_unapproved=indexes_unapproved,
        indexed_sensitivities=indexed_sensitivities,
    )


def _indexable_items(
    database: Path, *, include_unapproved: bool, visible_sensitivities: frozenset[Sensitivity]
) -> dict[str, int]:
    """How many items each project in the canonical store offered this build.

    Deliberately repeats ``IndexBuilder``'s selection rule rather than sharing
    it. The builder reports what it indexed; this reports what was there to be
    indexed, and the whole value of the pair is in noticing when the two
    disagree — which one shared implementation could not do. That is also why
    both terms are written out here instead of calling ``may_surface`` and
    ``may_disclose``: a copy that shares the gate is not a second derivation, and
    ``tests/unit/test_gate_call_sites.py`` accounts for this function's absence
    from both enumerations on exactly that ground.

    **The sensitivity term is not optional.** Without it a deployment whose
    ceiling excludes its whole corpus builds an index that is correctly empty and
    is then told its canonical state is broken, with a remedy about project ids —
    the failure ``test_a_project_holding_only_retired_knowledge_builds_an_empty
    _index`` already pins on the status axis.

    Keyed by project id and ordered by it, because the answer reaches an error
    message that has to read the same way twice.
    """
    counts: dict[str, int] = {}
    with SqliteCanonicalStore(database) as store:
        for project in store.list_projects():
            offered = sum(
                1
                for item in store.list_items(RequestContext(project_id=project.project_id))
                if item.current_revision_id is not None
                and item.status in SURFACEABLE_STATUSES
                and (include_unapproved or item.status is KnowledgeStatus.APPROVED)
                and item.sensitivity in visible_sensitivities
            )
            if offered:
                counts[project.project_id.value] = offered
    return counts


def _render_counts(counts: dict[str, int]) -> str:
    return ", ".join(f"{pid!r}: {n} item{'' if n == 1 else 's'}" for pid, n in counts.items())


def _empty_build_remedy(project_id: str, available: dict[str, int]) -> str:
    """Name the cause, which is almost always an id that changed under the data.

    The store keeps its rows per project id, so knowledge applied under one id is
    invisible to a build running under another. Re-applying does not fix it:
    ``migrate apply`` is idempotent per project, and the revision rows it would
    need are already spoken for by the first id.
    """
    others = [pid for pid in available if pid != project_id]
    if others:
        return (
            f"This repository's knowledge was applied under "
            f"{', '.join(repr(pid) for pid in others)}. Register the repository under one "
            f"id, delete .theurian/state/, and run `theurian migrate apply` followed by "
            f"`theurian index build`."
        )
    return (
        "Items exist under this id but none of them resolved to a revision, which means "
        "the state database was written under another project id. Delete .theurian/state/ "
        "and run `theurian migrate apply`, then retry."
    )
