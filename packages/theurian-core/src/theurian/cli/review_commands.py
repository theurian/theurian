"""``theurian review`` -- land review evidence, and derive from it (ADR-0030).

A composition root: where the abstract :class:`ReviewProvider`, the abstract
landing service and the abstract search builder meet the concrete ``gh`` adapter,
the evidence files under ``.theurian/review/`` and the SQLite store beside the
project's other derived state (ADR-0003). Nothing below this module names any of
them.

**Both commands here are write/maintenance paths, like ``index build`` and
``findings build``, and neither serves anything.** ``review ingest`` fetches,
screens and lands (decisions 1--4); ``review build`` re-derives the search store
from what has already landed (decision 3's derived half). Each reports **counts
and identities** -- a repository, a pull-request number, a provider node id, a
field name, a refusal grade, a row count -- and no title, no body, no comment
text and no participant name reaches stdout from either. A secret-scan finding
carries only the four-character redacted prefix
:class:`~theurian.security.content_secrets.SecretFinding` bounds it to. Serving
what the store holds is the MCP surface's, with its own disclosure round; this
module is the write boundary for both halves.

**One rebuild, called from two places** (:func:`rebuild_search_store`).
``review build`` is the operator's entry point and fetches nothing at all;
``review ingest`` calls the same function after it lands, so a run never leaves
the derived store describing the corpus as it was before it. A rebuild reads the
evidence files and writes only under ``.theurian/state/``.

**``.theurian/review/`` is source, not cache** (decision 3). Upstream comments
are editable and deletable, so a discarded local copy of a deleted comment is
data loss and no refetch recovers it -- which is why ``theurian init`` does not
write this directory into the managed ``.gitignore`` block, and why whether a
project commits its review evidence is the project's own decision. The command's
help says both, because the operator who reads only ``--help`` is the one who
would otherwise delete it to "clear the cache".

**No advance marker.** The window is the invocation's: ``--limit`` bounds how
many pull requests are read and ``--since`` skips the ones already ingested.
Nothing on disk records a pull request as seen, so a record this run skipped is
retried by the next run whose window covers it --
:mod:`theurian.application.review_ingest_service` records why that is a decision
rather than an omission.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Final

import typer

from theurian.application.findings_builder import WriteSection
from theurian.application.project_service import (
    REVIEW_SEARCH_STORE_ID,
    BuildProvenance,
    ProjectPathEscapeError,
    ProjectPaths,
)
from theurian.application.review_ingest_service import (
    LandedRecord,
    LandRecords,
    ReadLandedKeys,
    ReviewIngestReport,
    ReviewIngestRequest,
    ReviewIngestService,
)
from theurian.application.review_search_builder import (
    EvidenceEntry,
    ListEvidenceFingerprints,
    ReadEvidence,
    ReviewSearchBuilder,
    ReviewSearchBuildRequest,
)
from theurian.domain.errors import TheurianError
from theurian.infrastructure.github import GitHubReviewProvider
from theurian.infrastructure.github.limits import MAX_PULL_REQUESTS, PAGE_SIZE
from theurian.infrastructure.review_evidence import (
    EvidenceReader,
    EvidenceRecord,
    IngestionRun,
    ReviewEvidenceStore,
    new_ingestion_run,
)
from theurian.infrastructure.sqlite.connection import WriteLock
from theurian.infrastructure.sqlite.review_search_store import (
    ReviewSearchStoreError,
    SqliteReviewSearchStore,
)

review_app = typer.Typer(
    help="Ingest review evidence from a code-review provider.", no_args_is_help=True
)

JsonOption = Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")]

#: How many pull requests a run reads when the caller names no bound.
#:
#: One page of the pull-request listing, derived from the adapter's own page size
#: rather than chosen: the listing asks for ``min(PAGE_SIZE, limit)``, so this is
#: the largest default that costs a single request there. Well inside
#: :data:`~theurian.infrastructure.github.limits.MAX_PULL_REQUESTS`, which is the
#: bound a caller can raise ``--limit`` to.
DEFAULT_PULL_REQUESTS: Final = PAGE_SIZE

#: The cure when the command could not run at all and the failure carries none of
#: its own. Every refusal on this path *does* carry one -- the adapter's graded
#: envelope looks its remedy up by grade, and the evidence store's constructor
#: refuses an empty one -- so this is the backstop for a ``TheurianError`` raised
#: somewhere neither of those covers.
_GENERIC_REMEDY: Final = "Run `theurian doctor`."

#: The remedy for an OS refusal *acquiring* the write lock the rebuild takes.
#: Kept for the reason ``findings_commands._LOCK_ACQUIRE_REMEDY`` is kept: both
#: calls ``WriteLock.held`` makes before it has a descriptor now convert their own
#: ``OSError`` into a ``TheurianError`` carrying a better cure, so nothing in the
#: acquisition reaches this text today -- and deleting it is what would make the
#: next bare ``OSError`` a traceback again. Names the precondition first, with the
#: retry as the trailing clause.
_LOCK_ACQUIRE_REMEDY: Final = (
    "Check that .theurian/ is writable and there is free disk space, then retry "
    "`theurian review build`."
)

#: The remedy for an OS refusal *recording* the build in this installation's
#: provenance file. That file lives in ``THEURIAN_DATA_DIR`` -- outside the
#: repository, which is the whole point of it (ADR-0004, SEC-7) -- so the
#: precondition to fix is a different directory than every other failure these
#: commands report, and naming ``.theurian/`` here would send a reader to the
#: wrong one.
_PROVENANCE_REMEDY: Final = (
    "Check that the Theurian data directory (THEURIAN_DATA_DIR, or ~/.theurian) is "
    "writable and there is free disk space, then retry `theurian review build`."
)


def evidence_lander(store: ReviewEvidenceStore, run: IngestionRun) -> LandRecords:
    """Bind the evidence store as the landing service's writer.

    The four-field copy from :class:`LandedRecord` onto the store's own record is
    the whole of the mapping ADR-0003 keeps in a composition root: the service
    describes a record it screened, the store decides where that record lives,
    and neither type reaches into the other's layer.

    One call per run, so the store's collision guard -- two records claiming one
    path -- ranges over the **whole** run rather than over whichever slice of it
    a per-pull-request call happened to carry.
    """

    def land(records: Sequence[LandedRecord]) -> tuple[str, ...]:
        return store.write(
            [
                EvidenceRecord(
                    provider=record.provider,
                    repository=record.repository,
                    anchor=record.anchor,
                    payload=record.payload,
                )
                for record in records
            ],
            run=run,
        )

    return land


def landed_keys(store: ReviewEvidenceStore, repository: str) -> ReadLandedKeys:
    """Bind the evidence store as the reader of what this repository already has.

    The comparison is **case-folded**, which is the equality the adapter itself
    uses when it checks GitHub's answer against the allowlisted entry: GitHub
    treats owner and repository names case-insensitively, and a byte comparison
    would report every record of a differently-cased entry as one this run did
    not land.

    Reads every record rather than listing paths, which costs a parse per file
    and buys two things: a file this build cannot read refuses the run with a
    remedy naming it, rather than being silently absent from the count, and the
    reader half of the evidence store is exercised by the shipped write path.
    """

    def keys() -> frozenset[str]:
        return frozenset(
            stored.relative_path
            for stored in store.read_all()
            if stored.record.repository.casefold() == repository.casefold()
        )

    return keys


def evidence_entries(store: ReviewEvidenceStore) -> ReadEvidence:
    """Bind the evidence store as the search builder's source.

    The nine-field copy from the store's own :class:`StoredRecord` onto the
    builder's :class:`EvidenceEntry` is the mapping ADR-0003 keeps in a
    composition root, and it is the mirror of :func:`evidence_lander`'s four:
    the store decides where a record lives and what its key is, the builder
    describes a record it is projecting, and neither type reaches into the
    other's layer.

    ``read_all`` is the only seam used that *reads a record*, deliberately: the
    evidence package's reader is reached through the store's own method rather
    than constructed here, so there stays exactly one way this product reads a
    landed record and one place its refusals are worded.
    :func:`evidence_fingerprints` is the sibling that constructs the reader
    directly, and it may because it opens no file at all.
    """

    def read() -> tuple[EvidenceEntry, ...]:
        return tuple(
            EvidenceEntry(
                relative_path=stored.relative_path,
                record_key=stored.record.record_key,
                kind=stored.record.kind.value,
                provider=stored.record.provider,
                repository=stored.record.repository,
                anchor=stored.record.anchor,
                payload=stored.record.payload,
                last_seen_run_id=stored.last_seen.run_id,
                last_seen_at=stored.last_seen.observed_at,
            )
            for stored in store.read_all()
        )

    return read


def evidence_fingerprints(review_root: Path) -> ListEvidenceFingerprints:
    """Bind the evidence reader's own walk as the build's revalidation.

    :func:`evidence_entries`' companion, and the reason the two are separate: this
    one is called twice around the read -- once before it and once with the
    project's write lock held, immediately before the publish -- to drop any
    record whose file went away, was rewritten, or stopped being a file in
    between. It must therefore be a *listing* -- ``fingerprints`` opens no file
    and answers each leaf from its inode -- where the other is a parse per record.

    Bound to :class:`EvidenceReader`'s walk rather than to a listing written here,
    so the set a publish is checked against is the set a re-read would enumerate.
    A second walk in this module would have to repeat which directory depth counts,
    which kind directories are records and which suffix a leaf must carry, and the
    first time one of those drifted the build would either resurrect a deleted
    record or drop a live one.

    **This return annotation is what keeps the two spellings of a fingerprint from
    drifting.** The reader names ``(mtime_ns, size, st_ino, is a regular file)`` in
    its own module and this layer names it in the application's, and neither
    imports the other because a port and its adapter meet at a composition root
    (ADR-0003). A slot that changed type or arity on one side is a type error on
    this line -- which is how the inode slot was added to both at once.

    ``review_root`` is the same ``ProjectPaths.review`` the store is built on,
    already proved contained inside the project -- the precondition
    :class:`EvidenceReader` states in its own ``Args``.
    """
    return EvidenceReader(review_root).fingerprints


def _lock_write_section(lock_path: Path) -> WriteSection:
    """A write-section factory whose lock-acquisition ``OSError`` arrives graded.

    ``findings_commands._lock_write_section``'s twin, converting into this
    package's own error class so a failure carries the review rebuild's cure
    rather than the findings one. The ``except OSError`` spans acquisition, body
    **and** release; each phase is quiet for its own reason.

    **The acquisition and release reasons are the twin's**, unchanged, and are
    recorded in full there: both calls ``WriteLock.held`` makes before it has a
    descriptor convert their own ``OSError`` into a ``TheurianError`` naming the
    lock file, and the release clauses run after the publish is already durable.

    **The body reason is this section's own, because this body is not the twin's.**
    The findings section says "the one thing run inside is ``replace_all``"; this
    one runs **two** calls, and neither lets a bare ``OSError`` out:

    - :meth:`~theurian.infrastructure.review_evidence.reader.EvidenceReader.fingerprints`,
      the publish-time half of the builder's revalidation. Its directory walk
      converts its own ``OSError`` into a ``ReviewEvidenceError`` -- a
      ``TheurianError``, so it passes this handler untouched and reaches the
      command's own ``except TheurianError`` with a cure about the review
      directory rather than about the lock. The per-leaf ``stat`` raises nothing
      at all: it answers a sentinel, so one unreadable leaf cannot refuse a
      listing taken under the lock.
    - ``SqliteReviewSearchStore.replace_all``, which converts the complement of
      ``TheurianError`` before any of it escapes.

    A ``WriteLockTimeoutError`` is a ``TheurianError`` and not an ``OSError``, so
    it passes straight through with the lock-specific remedy #404 R1-5 gave it --
    the twin's sentence, and still true here.
    """

    @contextmanager
    def section() -> Iterator[None]:
        try:
            with WriteLock(lock_path).held():
                yield
        except OSError as exc:
            raise ReviewSearchStoreError(
                f"acquiring the write lock at {lock_path.name}: {exc}",
                remedy=_LOCK_ACQUIRE_REMEDY,
            ) from exc

    return section


def rebuild_search_store(paths: ProjectPaths) -> dict[str, object]:
    """Rebuild the derived search store from whatever is under ``.theurian/review/``.

    The composition root for ADR-0030 slice 3's build, shared by the two commands
    that need it: ``review build``, which is the operator's way to (re)build
    without fetching anything, and ``review ingest``, which calls it after landing
    so the derived store is not left describing the corpus as it was before the
    run.

    ``frozenset()`` is passed for the withheld set, and it is passed *explicitly*
    because the request type has no default (see
    :class:`~theurian.application.review_search_builder.ReviewSearchBuildRequest`).
    v1's scope is public allowlisted repositories, so there is nothing to withhold;
    #575 owns the setter that computes a real set from advisory state, and nothing
    here may derive one from a label, a category or a body (ADR-0019, ADR-0030
    decision 3).

    Every path is resolved through :class:`ProjectPaths`, so an escaping
    ``.theurian/state`` or ``.theurian/review`` refuses here, before a store is
    opened and before the write lock is taken.

    The provenance record is written the instant the rebuild returns, out of the
    repository tree (ADR-0004, SEC-7): a serving surface stands aside a store this
    installation did not build, so this call is what makes the store just built
    servable -- and what keeps one that arrived with a clone, force-added past the
    ignore, unservable however well-formed it is. A failure here is an ``OSError``
    the callers grade, because a store nothing will serve is a failed build rather
    than a success.
    """
    store_path = paths.review_search_for(REVIEW_SEARCH_STORE_ID)
    builder = ReviewSearchBuilder(
        read_evidence=evidence_entries(ReviewEvidenceStore(paths.review)),
        list_evidence_fingerprints=evidence_fingerprints(paths.review),
        write=SqliteReviewSearchStore(store_path).replace_all,
        write_section=_lock_write_section(paths.write_lock),
    )
    report = builder.build(ReviewSearchBuildRequest(withheld_record_keys=frozenset()))
    BuildProvenance.default().record_review(paths.root, REVIEW_SEARCH_STORE_ID)
    return {**report, "storePath": str(store_path)}


def build_ingest_service(
    paths: ProjectPaths, *, repository: str, run: IngestionRun
) -> ReviewIngestService:
    """The ingest pipeline, composed exactly as :func:`review_ingest` composes it.

    Factored out so a test can build the real object graph without driving Typer
    -- ``tests/integration/test_review_ingest_is_model_free.py`` walks what this
    returns to hold FR-V5 over the built pipeline, and a walk over a graph the
    command does not actually build would prove nothing about the command.

    The environment is read here rather than taken as an argument: it is the
    parent whose three ``gh`` configuration variables the child inherits and
    whose ``PATH`` locates the binary, and a composition root reading the process
    environment is what it is for.

    Every path is resolved through :class:`ProjectPaths`, so an escaping
    ``.theurian/review`` or ``.theurian/config.yaml`` refuses **here**, before a
    provider is asked for anything and before any process could be spawned.
    """
    store = ReviewEvidenceStore(paths.review)
    return ReviewIngestService(
        provider=GitHubReviewProvider(
            project_root=paths.root,
            config_file=paths.config,
            parent_environment=os.environ,
        ),
        land=evidence_lander(store, run),
        read_landed=landed_keys(store, repository),
        root=paths.root,
        config_file=paths.config,
    )


@review_app.command("ingest")
def review_ingest(
    repository: Annotated[
        str,
        typer.Argument(
            metavar="OWNER/REPO",
            help=(
                "The repository to ingest, as GitHub resolves it. It must be listed "
                "in .theurian/config.yaml; a repository the list does not name is "
                "refused before any process is spawned."
            ),
        ),
    ],
    limit: Annotated[
        int,
        typer.Option(
            "--limit",
            help=(
                f"How many pull requests to read, newest first. "
                f"At least 1 and at most {MAX_PULL_REQUESTS}."
            ),
        ),
    ] = DEFAULT_PULL_REQUESTS,
    since: Annotated[
        int | None,
        typer.Option(
            "--since",
            help=(
                "Stop at this pull-request number, so a re-run does not refetch "
                "history it already has."
            ),
        ),
    ] = None,
    as_json: JsonOption = False,
) -> None:
    """Fetch a repository's review history and land it under .theurian/review/.

    Pull requests, their top-level reviews and their review threads are written
    as structured JSON, one record per file. Every record is screened before it
    becomes a file: participant display names are replaced when the project asks
    for that (R-12), and the project's secret-scan policy decides whether a
    record carrying a secret-shaped string is withheld, landed with a report, or
    not scanned at all (SEC-11).

    These files are the **source**, not a cache. Upstream comments can be edited
    and deleted, so deleting this directory is data loss rather than a rebuild --
    no refetch recovers a comment that is gone. Theurian does not git-ignore it;
    whether the project commits its review evidence is the project's decision.

    Exit codes: 0 when the run was clean -- which **includes** a run whose scan
    found a secret under the `warn` policy, because `warn` is the project
    recording that a finding is reported and the record lands anyway; the
    published document sets `secretsWarned` to true there, so `clean` alone is
    not what a caller has to notice it by. 1 carries **the run document, or
    `{error, remedy}`, or both**: the run document alone, on stdout, when the run
    happened and was not clean -- a record `block` withheld, or a pull request the
    listing or a fetch could not read; `{error, remedy}` alone, on stderr, when
    the command refused before any report existed -- the repository is not in the
    allowlist, resolves as private, resolves to a different name, the `gh`
    configuration carries a transport override, `gh` is missing, below the
    recorded version floor or unauthenticated, a recorded bound was reached,
    `.theurian/config.yaml` cannot be read or names a value this build does not
    recognise, or a file already under `.theurian/review/` cannot be read or
    written; and **both** when the records landed and the rebuild that follows
    them did not. The `{error, remedy}` shape carries no `clean` field at all, so
    a caller scripting `--json | jq .clean` has to allow for a run that published
    nothing on stdout. 4 when a path under `.theurian/` could not be proved to
    stay inside the working tree -- carrying the run document too, if the escape
    was met by the rebuild rather than before the fetch.

    The run document carries a `searchStore` block: the derived store is rebuilt
    from every landed record once the run has finished landing, so a search sees
    this run's records without a second command. A failure to rebuild it
    publishes the run document first and refuses after it, with no `searchStore`
    block -- the evidence is durable before the rebuild starts, so the counts of
    what landed are still the answer to "what do I have", and `theurian review
    build` re-runs just that half. That holds for a rebuild fault this command
    does not grade as well: the run document is published whatever the failure
    was, and a fault carrying no cure is reported as the defect it is rather than
    dressed up as a refusal.
    """
    from theurian.cli.commands import (  # noqa: PLC0415 - cycle
        _emit,
        _fail,
        _fail_a_path_escape,
        _require_project,
    )

    context, _ = _require_project(as_json)
    try:
        # The whole composition is inside the `try`, for the reason `findings
        # build` records: `ProjectPaths.review` and `.config` route through the
        # containment chokepoint and can each raise, so a service composed
        # outside this handler would publish a traceback instead of the graded
        # refusal.
        service = build_ingest_service(
            context.paths,
            repository=repository,
            run=new_ingestion_run(clock=context.clock, ids=context.ids),
        )
        report = asyncio.run(
            service.run(
                ReviewIngestRequest(
                    project_id=context.project_id,
                    repository=repository,
                    limit=limit,
                    since_number=since,
                )
            )
        )
    except ProjectPathEscapeError as exc:
        _fail_a_path_escape(exc, as_json=as_json)
        return
    except TheurianError as exc:
        # `limit` is deliberately unbounded by Typer so that it arrives here: the
        # adapter refuses a limit below one or above its recorded cap with a
        # graded envelope whose remedy names the bound and how to change it,
        # which is a better answer than Click's usage error and is the only way
        # `LIMIT_EXCEEDED`'s recorded cure reaches an operator at all.
        _fail(str(exc), remedy=exc.remedy or _GENERIC_REMEDY, as_json=as_json, code=1)
        return

    # After landing, never before or instead of it -- and in a `try` of its own,
    # which is the difference between the two halves rather than a style choice.
    # Above this line nothing is durable, so a failure means the command could not
    # run and there is nothing to report. Below it the evidence files are the
    # source and are already on disk (ADR-0030 decision 3), so a rebuild that
    # fails costs a stale derived store and no evidence -- and the run's counts
    # are the only record of what just happened. `secretsWarned` and `findings`
    # in particular exist nowhere else: a `warn` run lands the flagged record and
    # no later command recomputes that it did.
    #
    # **The emit is owed by every exit from the rebuild, and a `finally` is what
    # owes it** -- not the three arms below, which is how it was written until
    # #630's HIGH-1. Enumerated arms publish the document for the exception
    # classes somebody has already met: a `ValueError` out of `int` was outside
    # all three, so the run document went with it. The inner `try` has no handler
    # at all, so an unenumerated exception still leaves this function loudly --
    # a defect in this process is not an operator-facing refusal, the reason
    # `test_review_evidence_exception_keys.py` records for not widening any of
    # these arms to `except Exception` -- but it leaves *after* the document is
    # out. `document` is built before the rebuild starts because `_payload` is a
    # pure function of the report `service.run` already returned, so what the
    # `finally` publishes is the whole landing report and never a half-built one;
    # the rebuild takes `paths` and nothing else, so it mutates neither `report`
    # nor `document` and its only contribution is one key added to a copy.
    # `test_a_rebuild_defect_outside_every_graded_arm_still_publishes_the_run_document`
    # is what goes RED when either half of that stops holding.
    document = _payload(report)
    search: dict[str, object] | None = None
    try:
        try:
            search = rebuild_search_store(context.paths)
        finally:
            # `search is None` exactly when the rebuild did not return: the block
            # describes a store that exists, so a failed rebuild publishes the
            # document without it rather than with an empty one.
            _emit(
                document if search is None else {**document, "searchStore": search},
                as_json=as_json,
            )
    except ProjectPathEscapeError as exc:
        _fail_a_path_escape(exc, as_json=as_json)
        return
    except TheurianError as exc:
        _fail(
            f"The records landed under .theurian/review/, but the search store could "
            f"not be rebuilt from them ({exc}), so a review search will not see what "
            f"this run landed until the rebuild succeeds.",
            remedy=exc.remedy or _GENERIC_REMEDY,
            as_json=as_json,
            code=1,
        )
        return
    except OSError as exc:
        # Only the provenance write raises a bare `OSError` on this path: the
        # store's own write converts its own, and the lock's are converted by
        # `_lock_write_section`. Its sentence differs from the arm above in the
        # clause that matters -- the store *was* rebuilt here, and what is missing
        # is this installation's record that it built it.
        _fail(
            f"The records landed and the search store was rebuilt, but this "
            f"installation could not record that it built it ({exc}), so a review "
            f"search will refuse to serve it.",
            remedy=_PROVENANCE_REMEDY,
            as_json=as_json,
            code=1,
        )
        return
    if not report.clean:
        # Emitted first and *then* non-zero: the counts of what did land are the
        # operator's answer to "what do I still have", and a caller scripting
        # this reads the same document whichever way the run went.
        raise typer.Exit(1)


@review_app.command("build")
def review_build(as_json: JsonOption = False) -> None:
    """Rebuild the review search store from the evidence already on disk.

    Reads every record under .theurian/review/ and lands them wholesale in a
    derived SQLite store beside the project's other state. It fetches nothing: no
    `gh` is spawned, no repository is contacted, and no allowlist is consulted, so
    this is the command to run on a clone that already carries its project's
    review evidence.

    Deleting the search store costs a rebuild, not data (ADR-0004): the evidence
    files are the source and this command re-derives everything from them. The
    reverse is not true -- deleting a file under .theurian/review/ is data loss no
    refetch recovers -- so nothing here writes to, moves or removes one.

    `theurian review ingest` runs this same rebuild after it lands, so a project
    that only ever ingests never needs to call it; it exists for the clone that
    received evidence through git, and for the operator whose store was damaged or
    thrown away.

    Exit codes: 0 when the store was rebuilt -- including a rebuild that publishes
    an *empty* store, which is what a corpus that is gone when the build reaches its
    publish gets. Deleting files under .theurian/review/ is the only retention
    remedy there is, so a rebuild that refused there would go on serving the records
    you removed. 1 when
    the store was not rebuilt -- a record this build cannot store (the message
    names the file), a corpus that is still there when this build reaches its
    publish while the build has nothing left it can publish from it (its whole read
    went stale, or it read nothing while records were landing; either way something
    else was writing .theurian/review/ underneath it -- another theurian run, or a
    git checkout or pull over the tracked directory -- so let that finish and re-run
    this one), an unwritable .theurian/state, another process holding the write lock
    past the timeout, or a provenance record this installation could not write. 4
    when a path under .theurian/ could not be proved to stay inside the working
    tree.
    """
    from theurian.cli.commands import (  # noqa: PLC0415 - cycle
        _emit,
        _fail,
        _fail_a_path_escape,
        _require_project,
    )

    context, _ = _require_project(as_json)
    try:
        # The whole composition is inside the `try`, for the reason `review ingest`
        # and `findings build` both record: `review_search_for`, `review` and
        # `write_lock` each route through the containment chokepoint and can each
        # raise, so anything composed outside this handler would publish a
        # traceback instead of the graded refusal.
        report = rebuild_search_store(context.paths)
    except ProjectPathEscapeError as exc:
        _fail_a_path_escape(exc, as_json=as_json)
        return
    except TheurianError as exc:
        _fail(str(exc), remedy=exc.remedy or _GENERIC_REMEDY, as_json=as_json, code=1)
        return
    except OSError as exc:
        _fail(
            f"The store was rebuilt, but this installation could not record that it "
            f"built it ({exc}), so a review search will refuse to serve it.",
            remedy=_PROVENANCE_REMEDY,
            as_json=as_json,
            code=1,
        )
        return
    _emit({**report, "built": True}, as_json=as_json)


def _payload(report: ReviewIngestReport) -> dict[str, object]:
    """The run, as the published document.

    Identities and counts. ``refused`` and ``skipped`` name records and pull
    requests; ``findings`` names where a secret-shaped string was and which
    family it matched, through
    :meth:`~theurian.security.content_secrets.SecretFinding.describe`, whose
    redacted prefix is bounded on the type rather than here.

    ``secretsWarned`` is the third boolean, and it is published because the
    other two are silent about the one state that matters most: a ``warn`` run
    that found a secret is ``clean``, refuses nothing and exits zero, having
    written that finding into a file. See
    :attr:`~theurian.application.review_ingest_service.ReviewIngestReport.secrets_warned`.
    """
    return {
        "repository": report.repository,
        "clean": report.clean,
        "secretScanPolicy": report.policy,
        "secretsWarned": report.secrets_warned,
        "participantNamesRedacted": report.redacted,
        "landed": {
            "pullRequests": report.pull_requests,
            "reviewSubmissions": report.review_submissions,
            "reviewThreads": report.review_threads,
            "total": report.landed,
        },
        "new": report.new,
        "updated": report.updated,
        "kept": report.kept,
        "refused": [identity.describe() for identity in report.refused],
        "findings": [finding.describe() for finding in report.findings],
        "skipped": [skip.describe() for skip in report.skipped],
    }
