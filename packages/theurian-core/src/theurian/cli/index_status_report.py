"""What ``theurian index status`` reports, computed (FR-R2).

Split out of :mod:`theurian.cli.index_commands`, which holds the command itself
and stays the module ``tests/unit/test_resolve_context_call_sites.py`` pins
``index_status`` to. What moved here is everything the command *derives* before
it emits: the published build's schema version, this deployment's disclosure
flavor against the build's, which single command to name -- and, since issue
#100, the staleness verdict itself, which ``theurian project status`` publishes
as ``indexStale`` from :func:`index_staleness` rather than deriving a second
answer of its own.

One rule governs all of them and is worth stating once rather than three times:
**a helper here never raises.** Every input is derived, git-ignored and unsigned
(SEC-7) or is a file an operator can chmod out from under the process, and a
status command that ends in a traceback is a status command at the one moment it
was needed. Each answers with a value that says what could not be established.

This is a CLI module and not an application one because the verdict is composed
out of concrete adapters -- the deployment's serving profile under
:func:`default_data_dir`, and :class:`SqliteIndexStore` over the published build
-- which only a composition root may name (ADR-0003). Both consumers are CLI
commands, so the shared computation lives at the layer they share.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from theurian.application.authorization import (
    ProfileVerdict,
    StaticAuthorizationProvider,
    decode_sensitivities,
    encode_sensitivities,
    load_serving_profile,
    recorded_flavor_verdict,
)
from theurian.application.project_service import (
    INDEX_POINTER_REMEDY,
    read_active_index_pointer,
)
from theurian.domain.errors import TheurianError
from theurian.infrastructure.secrets.file_store import default_data_dir
from theurian.infrastructure.sqlite.index_schema import INDEX_SCHEMA_VERSION
from theurian.infrastructure.sqlite.index_store import SqliteIndexStore

if TYPE_CHECKING:
    from theurian.application.project_service import ProjectPaths
    from theurian.domain.enums import Sensitivity


@dataclass(frozen=True, slots=True)
class ProfileState:
    """What this deployment serves, what the published build holds, and the gap.

    A record rather than five locals, so ``index_status`` reads as one line about
    the disclosure axis beside its one line about each other axis -- and so the
    fields cannot be assembled two different ways by a later edit.
    """

    #: The keys ``index status`` publishes for this axis, ready to merge.
    payload: dict[str, Any]
    #: Whether this axis alone makes the published build unusable.
    stale: bool
    #: What to run when it is the profile itself that cannot be read, or ``""``.
    #: Ahead of every other remedy, because `theurian index build` refuses on
    #: exactly the same refusal: telling an operator to rebuild would name a
    #: command that cannot run until this is fixed.
    remedy: str


def profile_state(published: dict[str, Any] | None) -> ProfileState:
    """Compare the published build's disclosure flavor against the one in force.

    Reads the ceiling through the same
    :func:`~theurian.application.authorization.load_serving_profile` the build
    and the daemon use, and judges it with the same
    :func:`~theurian.application.authorization.recorded_flavor_verdict` the
    ranked path stands an index aside on -- so a build ``knowledge.search``
    refuses cannot be reported fresh here.

    A refused profile is published as its own message and remedy rather than
    raised: this is an operator at their own terminal, which is the reader
    ``mcp/search.py``'s ``_PROFILE_MISMATCH`` note defers the level names to.
    """
    try:
        served: frozenset[Sensitivity] | None = (
            StaticAuthorizationProvider(load_serving_profile(default_data_dir()))
            .deployment_grant()
            .sensitivities
        )
        fault = ""
        remedy = ""
    except TheurianError as exc:
        served = None
        fault = str(exc)
        remedy = exc.remedy or "Run `theurian doctor`."

    recorded = (published or {}).get("indexedSensitivities")
    verdict = None if served is None else recorded_flavor_verdict(recorded, served=served)
    return ProfileState(
        payload={
            "servedSensitivities": None if served is None else encode_sensitivities(served),
            "indexedSensitivities": _decoded_flavor(recorded),
            "profileMismatch": verdict is ProfileVerdict.MISMATCH,
            # Only meaningful once something is published: a project that has
            # never built has no pointer to have recorded anything, and
            # `published is None` already carries that.
            "profileUnrecorded": published is not None and verdict is ProfileVerdict.UNRECORDED,
            "profileUnreadable": fault,
        },
        # An unreadable profile makes the comparison impossible, and a build that
        # cannot be shown to match the deployment is not one this command may
        # call fresh -- the same reasoning `orphaned` applies to a pointer that
        # records no project id.
        stale=served is None or (published is not None and verdict is not ProfileVerdict.MATCHES),
        remedy=remedy,
    )


def _decoded_flavor(recorded: object) -> list[str] | None:
    """The levels a pointer recorded, written back in disclosure order.

    ``None`` when the value names none, which is what ``profileUnrecorded``
    reports as a flag: a list is published only when there is one to publish, so
    a reader never has to tell an empty list from an unreadable value.
    """
    levels = decode_sensitivities(recorded)
    return None if levels is None else encode_sensitivities(levels)


@dataclass(frozen=True, slots=True)
class IndexStaleness:
    """Whether the published index build may still be served, and what decided it.

    One computation, two commands (issue #100). ``theurian index status``
    publishes the whole breakdown; ``theurian project status`` publishes the
    verdict alone, as ``indexStale``.

    Until this record existed the second derived its own answer from the
    *canonical* state pointer -- ``active is None or active.state_hash !=
    context.state_hash``, which asks whether ``migrate apply`` is up to date --
    under a name about the index, and never opened ``active-index.json`` at all.
    A project that had never built an index was told ``indexStale: false`` in the
    same second ``index status`` told it ``built: false, stale: true``; and once
    a further migration was applied over a published build the canonical pointer
    was current again, so the old expression answered ``false`` at exactly the
    moment the build fell behind.
    """

    #: The verdict both commands publish. ``True`` when the published build
    #: cannot be served as it stands, on any axis below.
    stale: bool
    #: The keys ``index status`` publishes about the index itself, ready to
    #: merge. Not what ``project status`` reports -- it takes ``stale`` alone.
    payload: dict[str, Any]
    #: The four axes :func:`remedy_for` needs. Typed fields rather than lookups
    #: into ``payload`` above, so the caller names what it is passing instead of
    #: reading its own published dictionary back out.
    pointer_corrupt: bool
    orphaned: bool
    purge_failed: bool
    #: What to run when the serving profile itself cannot be read, or ``""``.
    profile_remedy: str


def index_staleness(
    paths: ProjectPaths, *, project_id: str, current_state_hash: str
) -> IndexStaleness:
    """Judge the published index build against the knowledge as it is now.

    **The invariant that governs every axis: whatever the ranked path refuses,
    this reports as stale.** Each one below is a state ``knowledge.search``
    stands the build aside for, so a verdict missing one would answer "fresh,
    nothing to do" about a file a search had just declined to read.

    Never raises, per this module's rule. Every input is a derived, git-ignored,
    unsigned file (SEC-7): the pointer read distinguishes its own failures
    without throwing, the schema probe answers 0 for anything it cannot
    interpret, and an unreadable serving profile becomes a stale verdict with its
    own remedy.

    ``needs_apply`` -- whether canonical state is itself behind the migrations --
    is deliberately *not* an axis here. It is a fact about the state database,
    not about the index, and it enters only :func:`remedy_for`'s ordering, where
    it decides that applying must precede any rebuild. Folding it in is the
    conflation issue #100 is about, running the other way.
    """
    pointer = read_active_index_pointer(paths)
    published = dict(pointer.payload) if pointer.payload is not None else None
    indexed = (published or {}).get("stateHash")
    # An index whose schema this build does not understand is unusable no matter
    # how fresh its state hash is, and retrieval already falls back for it.
    schema = index_schema_version(paths, published)
    # Chunks are stamped with the project id that built them, so an index built
    # for another id answers every query with nothing while reporting itself
    # indexed. A pointer written before this field existed cannot be checked, so
    # it counts as orphaned: one rebuild makes it verifiable, and claiming
    # freshness that was never established is what this exists to avoid.
    index_project = (published or {}).get("projectId")
    orphaned = published is not None and index_project != project_id
    profile = profile_state(published)
    # A build whose withdrawal purge failed still holds rows the withdrawal
    # removed from canonical state, and `knowledge.search` stands it aside whole
    # rather than serving them (GHSA-97q9-xxfg-33r6). Its own axis, independently
    # of the state-hash comparison -- which is `true` here anyway because a purge
    # follows a migration, but need not be: a taint written against an otherwise
    # fresh build must still read stale. Truthiness rather than `is True` because
    # the pointer is unsigned (SEC-7): any value a hand edit leaves under the key
    # is read exactly as the serve path's own `if published.get("purgeFailed")`
    # reads it.
    purge_failed = bool((published or {}).get("purgeFailed"))
    return IndexStaleness(
        stale=(
            published is None
            or indexed != current_state_hash
            or schema != INDEX_SCHEMA_VERSION
            or orphaned
            or profile.stale
            or purge_failed
        ),
        payload={
            "built": published is not None,
            "indexPointerCorrupt": pointer.unreadable,
            "indexBuildId": (published or {}).get("indexBuildId"),
            "indexStateHash": indexed,
            "indexProjectId": index_project,
            "indexSchemaVersion": schema,
            "expectedIndexSchemaVersion": INDEX_SCHEMA_VERSION,
            "orphaned": orphaned,
            # Always present, `false` on a healthy build, so a reader never
            # branches on the key's absence -- the discipline every other field
            # here holds to, and what lets a client tell "one migration behind"
            # (`stale` alone) from "still holds withdrawn rows" (`purgeFailed`).
            "purgeFailed": purge_failed,
            **profile.payload,
        },
        pointer_corrupt=pointer.unreadable,
        orphaned=orphaned,
        purge_failed=purge_failed,
        profile_remedy=profile.remedy,
    )


def index_schema_version(paths: ProjectPaths, published: dict[str, Any] | None) -> int | None:
    """The schema version of the published build, or ``None`` if there is none.

    A pointer naming a path outside the project, or a file that has since been
    deleted, is a status to report rather than a command to fail -- so both
    answer 0, which is "unknowable" and not "version zero".

    **The ``is_file()`` probe is inside the ``try``, and that is the whole
    guard.** ``Path.is_file()`` swallows only the errnos ``pathlib`` lists as
    "this is not a file", and ``ENAMETOOLONG`` is not among them; ``index_for``
    cannot convert it either, because ``Path.resolve()`` in non-strict mode
    never stats, so it hands back a name the OS has not been asked about yet.
    Measured through the real CLI: an ``indexBuildId`` of 234 characters or more
    -- 15 + 234 + 7 exceeds a 255-byte ``NAME_MAX`` -- ended ``theurian index
    status`` *and* ``theurian project status`` in a bare ``OSError`` at exit 1
    with empty stdout, from a pointer that is derived, git-ignored and unsigned
    (SEC-7) and so is whatever a local process left behind. 233 answered.
    ``OSError`` is caught beside ``TheurianError`` rather than narrowed to one
    errno: every one of them means the same thing here, which is that this
    file's version could not be established.
    """
    if published is None:
        return None
    try:
        path = paths.index_for(str(published.get("indexBuildId", "")))
        return SqliteIndexStore(path).schema_version() if path.is_file() else 0
    except (TheurianError, OSError):
        return 0


def remedy_for(  # noqa: PLR0911, PLR0913 - one keyword per axis, one return per named remedy
    *,
    stale: bool,
    needs_apply: bool,
    orphaned: bool,
    pointer_corrupt: bool,
    purge_failed: bool,
    profile_remedy: str,
) -> str:
    """The next command to run, in the order it has to be run in.

    An unreadable serving profile is named first, ahead of the corrupt pointer:
    `theurian index build` reads that file too and refuses on the same refusal
    (``index_commands._deployment_grant``), so every remedy below it names a
    command that cannot run until this one has been carried out.

    A corrupt pointer is named next, ahead of even ``orphaned``: it is the one
    case where "run `theurian index build`" alone understates what happened, and
    it is the exact remedy ``knowledge.search`` already gives an agent for the
    same file (:data:`~theurian.application.project_service.INDEX_POINTER_REMEDY`)
    -- the two surfaces must agree, not merely both suggest a rebuild.

    Indexing before applying would build from a database that is itself behind,
    producing a fresh-looking index of stale knowledge. An orphaned index is
    named next because the rebuild it asks for subsumes both other remedies.

    A failed purge is named after ``needs_apply`` and before the plain ``stale``
    arm (GHSA-97q9-xxfg-33r6). ``needs_apply`` still comes first: if the database
    is itself behind, applying must precede any rebuild. But a purge-failed build
    is served by nothing (``mcp.search._published_index`` stands it aside whole),
    so its remedy has to say *why* the index is unusable -- it still holds rows a
    withdrawal removed -- rather than the bare rebuild ``stale`` prints, which
    reads as an ordinary refresh. One rebuild re-derives a clean build from
    canonical state and clears the taint.

    A profile *mismatch* takes no arm of its own. One rebuild under the ceiling
    in force is the whole cure, which is what the ``stale`` arm already says --
    and an arm that repeated it would be a second place for the sentence to
    drift from ``_PROFILE_MISMATCH``'s.
    """
    if profile_remedy:
        return profile_remedy
    if pointer_corrupt:
        return INDEX_POINTER_REMEDY
    if orphaned:
        return (
            "This index was built for a different project id. Run `theurian index build`; "
            "if it refuses, the canonical rows carry the other id too -- delete "
            ".theurian/state/ and run `theurian migrate apply` first."
        )
    if needs_apply:
        return "Run `theurian migrate apply`, then `theurian index build`."
    if purge_failed:
        return (
            "This index still holds rows a withdrawal removed from the knowledge state, "
            "because the purge that follows a withdrawal did not complete. Run "
            "`theurian index build` to produce a clean build; the index is derived, so "
            "nothing is lost."
        )
    if stale:
        return "Run `theurian index build`."
    return ""
