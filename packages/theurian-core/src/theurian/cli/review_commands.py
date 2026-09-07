"""``theurian review`` -- land review evidence on disk (ADR-0030 decisions 1--4).

A composition root: where the abstract :class:`ReviewProvider` and the abstract
landing service meet the concrete ``gh`` adapter and the evidence files under
``.theurian/review/`` (ADR-0003). Nothing below this module names either.

``review ingest`` is a **write/maintenance** path, like ``index build`` and
``findings build``. It fetches, screens and lands, and reports **counts and
identities**: a repository, a pull-request number, a provider node id, a field
name, a refusal grade. No title, no body, no comment text and no participant
name reaches stdout, and a secret-scan finding carries only the four-character
redacted prefix :class:`~theurian.security.content_secrets.SecretFinding` bounds
it to. Serving review evidence is a later slice with its own disclosure round;
this is the write boundary.

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
from collections.abc import Sequence
from typing import Annotated, Final

import typer

from theurian.application.project_service import ProjectPathEscapeError, ProjectPaths
from theurian.application.review_ingest_service import (
    LandedRecord,
    LandRecords,
    ReadLandedKeys,
    ReviewIngestReport,
    ReviewIngestRequest,
    ReviewIngestService,
)
from theurian.domain.errors import TheurianError
from theurian.infrastructure.github import GitHubReviewProvider
from theurian.infrastructure.github.limits import MAX_PULL_REQUESTS, PAGE_SIZE
from theurian.infrastructure.review_evidence import (
    EvidenceRecord,
    IngestionRun,
    ReviewEvidenceStore,
    new_ingestion_run,
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

    Exit codes: 0 when the run was clean, 1 when any record was withheld or any
    pull request could not be read, 4 when a path under .theurian/ could not be
    proved to stay inside the working tree.
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
    _emit(_payload(report), as_json=as_json)
    if not report.clean:
        # Emitted first and *then* non-zero: the counts of what did land are the
        # operator's answer to "what do I still have", and a caller scripting
        # this reads the same document whichever way the run went.
        raise typer.Exit(1)


def _payload(report: ReviewIngestReport) -> dict[str, object]:
    """The run, as the published document.

    Identities and counts. ``refused`` and ``skipped`` name records and pull
    requests; ``findings`` names where a secret-shaped string was and which
    family it matched, through
    :meth:`~theurian.security.content_secrets.SecretFinding.describe`, whose
    redacted prefix is bounded on the type rather than here.
    """
    return {
        "repository": report.repository,
        "clean": report.clean,
        "secretScanPolicy": report.policy,
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
