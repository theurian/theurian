"""What ``theurian index status`` reports, computed (FR-R2).

Split out of :mod:`theurian.cli.index_commands`, which holds the command itself
and stays the module ``tests/unit/test_resolve_context_call_sites.py`` pins
``index_status`` to. What moved here is everything the command *derives* before
it emits: the published build's schema version, this deployment's disclosure
flavor against the build's, and which single command to name.

One rule governs all of them and is worth stating once rather than three times:
**a helper here never raises.** Every input is derived, git-ignored and unsigned
(SEC-7) or is a file an operator can chmod out from under the process, and a
status command that ends in a traceback is a status command at the one moment it
was needed. Each answers with a value that says what could not be established.
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
from theurian.application.project_service import INDEX_POINTER_REMEDY
from theurian.domain.errors import TheurianError
from theurian.infrastructure.secrets.file_store import default_data_dir
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


def index_schema_version(paths: ProjectPaths, published: dict[str, Any] | None) -> int | None:
    """The schema version of the published build, or ``None`` if there is none.

    A pointer naming a path outside the project, or a file that has since been
    deleted, is a status to report rather than a command to fail -- so both
    answer 0, which is "unknowable" and not "version zero".
    """
    if published is None:
        return None
    try:
        path = paths.index_for(str(published.get("indexBuildId", "")))
    except TheurianError:
        return 0
    return SqliteIndexStore(path).schema_version() if path.is_file() else 0


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
