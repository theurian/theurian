"""Tainting ``active-index.json`` when a purge fails (GHSA-97q9-xxfg-33r6).

When a withdrawal's or a reclassification's index purge fails, the withdrawn
rows stay in the published build and the build stays published
(``withdrawal_purge.publish_purge_for_withdrawal``). Nothing in the pointer said
so, so ``knowledge.search`` went on ranking visible rows against withheld text
and a ``--raptor`` build handed that text to a caller verbatim through a visible
sibling's ``raptorPath``. The record that closes it is a single optional boolean
on the pointer, and this file is about the two functions that write it.

**Why the id has to be checked, and why that is the whole of this file.** The
purge does not hold the index-write lock (ADR-0022, #113): a concurrent
``theurian index build`` may publish a clean build in the window between the
purge failing and the taint being written. Tainting whatever the pointer happens
to name at that moment would mark the *clean* build unusable and send retrieval
onto the unranked scan until someone rebuilt again -- a self-inflicted outage
with no symptom but worse results. So the taint is conditional on the pointer
still naming the build the purge was copying, and ``mark_active_index_purge_failed``
reports whether it applied instead of assuming it did.

**And why it never raises.** Its caller is already inside
``publish_purge_for_withdrawal``'s failure path, handling one exception; a second
one there would turn a reported purge failure into a failed ``migrate apply``,
for a migration that is already committed. Every way the file can refuse -- no
pointer, an unreadable one, a directory the process cannot write -- answers
``False``.

Under ``tests/unit`` beside ``test_project_registry_errors.py``, which is the
same shape: one derived pointer file under ``tmp_path``, read and written through
the production functions, with the failure modes driven by real modes rather than
by a mocked filesystem.

**Both functions are reached through the module rather than imported by name,
and that is not a style preference.** This file was written before either
existed, and ``from ... import mark_active_index_purge_failed`` would then raise
at *collection* -- where one error aborts the entire pytest run and takes every
unrelated test in the repository with it. Through the module each test fails on
its own assertion with its own message, the rest of the suite still runs, and
mypy still reports the missing attribute. Nothing here changes when the fix
lands.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from theurian.application import project_service
from theurian.application.project_service import ProjectPaths
from theurian.domain.enums import Sensitivity

pytestmark = pytest.mark.unit


#: The offline CI job runs as root, where a mode denies nothing and the
#: unwritable-directory test below would report the mode it was given while the
#: write succeeded anyway. The same guard the project registry and the serving
#: profile carry for the same reason.
_CANNOT_BE_REFUSED_BY_A_MODE = sys.platform == "win32" or os.geteuid() == 0

#: Two build ids, so "the pointer still names the build the purge was copying"
#: can be distinguished from "the pointer names a build". Crockford base32, 26
#: characters, and pinned as literals so ``tests/unit/test_test_fixtures.py``
#: checks the charset.
BUILD: str = "01K1SRCAAA01234567890ABCDE"
ANOTHER_BUILD: str = "01K1SRCBBB01234567890ABCDE"

STATE_HASH: str = "a" * 64
PROJECT: str = "demo"

#: A flavor that is neither empty nor every level, so a taint that dropped or
#: widened it is visible. The pointer is the only record of which ceiling a build
#: ran under, and ``mcp.search._published_index`` stands a build aside when it
#: cannot be shown to match the grant in force -- so a taint that mangled this
#: would degrade every query while reporting a different reason than the one it
#: caused.
INDEXED: frozenset[Sensitivity] = frozenset({Sensitivity.PUBLIC, Sensitivity.INTERNAL})


def _paths(tmp_path: Path) -> ProjectPaths:
    paths = ProjectPaths.of(tmp_path)
    paths.state.mkdir(parents=True, exist_ok=True)
    return paths


def _publish(paths: ProjectPaths, *, build_id: str = BUILD, purge_failed: bool = False) -> None:
    """Point the project at a build through the production writer."""
    project_service.write_active_index_pointer(
        paths,
        index_build_id=build_id,
        state_hash=STATE_HASH,
        project_id=PROJECT,
        indexes_unapproved=False,
        indexed_sensitivities=INDEXED,
        purge_failed=purge_failed,
    )


def _payload(paths: ProjectPaths) -> dict[str, object]:
    payload = project_service.read_active_index_pointer(paths).payload
    assert payload is not None, "the pointer does not name a usable build"
    return dict(payload)


def _without_the_taint(payload: dict[str, object]) -> dict[str, object]:
    """A pointer's payload with ``purgeFailed`` dropped, so the rest can be compared.

    Dropped from *both* sides of the comparison rather than only from the tainted
    one, because the field is optional: "absent" and "present and ``false``" are
    both untainted, and a comparison that only stripped one side would pin which
    of the two the writer chooses -- a formatting decision nothing downstream can
    observe, since every reader tests it for truthiness.
    """
    return {key: value for key, value in payload.items() if key != "purgeFailed"}


# -- The field the pointer gains ---------------------------------------------


def test_an_ordinary_publish_is_not_tainted(tmp_path: Path) -> None:
    """``theurian index build`` and a successful purge must publish a clean build.

    The default is what almost every write takes, so it is the one that decides
    whether the fix is a fix or an outage: a writer that recorded the taint
    unconditionally would stand *every* build aside, and the only symptom would be
    that ranked retrieval quietly stopped happening.

    Read through :func:`read_active_index_pointer` rather than off the JSON, and
    asserted falsy rather than ``is False``, because the field is optional --
    absent means "no purge has failed against this build", which is what every
    pointer written before this field existed says.
    """
    paths = _paths(tmp_path)

    _publish(paths)

    assert not _payload(paths).get("purgeFailed"), (
        "an ordinary publish recorded a purge failure, which stands the fresh build aside "
        "from every query"
    )


def test_a_publish_for_a_failed_purge_records_the_taint(tmp_path: Path) -> None:
    """The record itself: one boolean on the pointer, readable by both surfaces.

    ``mcp.search._published_index`` and ``theurian index status`` each read this
    key back, so the value has to be exactly ``true`` rather than merely
    truthy-ish -- a string ``"true"``, for instance, is truthy in Python and is
    not what a JSON contract may publish.
    """
    paths = _paths(tmp_path)

    _publish(paths, purge_failed=True)

    assert _payload(paths)["purgeFailed"] is True, (
        "a pointer written for a failed purge does not say so, so nothing downstream can "
        "stand the stale build aside"
    )


def test_republishing_clears_a_previous_taint(tmp_path: Path) -> None:
    """The taint belongs to a build, not to a project.

    ``theurian index build`` writes a fresh pointer through this same function,
    and that rebuild is the remedy every failure message names -- ``migrate
    apply``'s ``indexPurge.remedy``, ``index status``' remedy, and the search
    fallback's note all say to run it. If the writer merged the previous
    pointer's flag forward, or defaulted to preserving it, the remedy would not
    work and the project would stay on the unranked scan permanently.
    """
    paths = _paths(tmp_path)
    _publish(paths, purge_failed=True)
    assert _payload(paths)["purgeFailed"] is True, "the arrangement did not taint anything"

    _publish(paths, build_id=ANOTHER_BUILD)

    assert not _payload(paths).get("purgeFailed"), (
        "a fresh publish carried the previous build's purge failure forward, so the rebuild "
        "every remedy names does not restore ranked retrieval"
    )


# -- Which build the taint may mark ------------------------------------------


def test_the_taint_marks_only_the_build_that_failed_to_purge(tmp_path: Path) -> None:
    """The concurrency check, and the reason this function returns a bool at all.

    The purge takes no index-write lock, so a ``theurian index build`` running
    beside it can publish a clean build between the purge raising and the taint
    being written. Marking whatever the pointer names at that moment would
    condemn the clean build, and the only visible effect would be that ranked
    retrieval stopped -- so the taint applies only when the pointer still names
    the build the purge was copying.

    Both directions are one test because either alone passes for a wrong
    implementation. A function that never writes satisfies the mismatch half; one
    that always writes satisfies the match half. Together they pin the condition.

    The mismatch half is asserted over the file's **bytes**, not over its parsed
    fields: "left untouched" is a stronger claim than "still says the same
    things", and a taint that rewrote the pointer with equal values would still
    be a write racing the publish that just happened.
    """
    paths = _paths(tmp_path)
    _publish(paths)
    untouched = paths.active_index_pointer.read_bytes()
    original = _payload(paths)

    moved_on = project_service.mark_active_index_purge_failed(
        paths, expected_build_id=ANOTHER_BUILD
    )

    assert moved_on is False, (
        "the taint reported success against a build the pointer does not name, so its caller "
        "cannot tell a condemned stale build from a condemned fresh one"
    )
    assert paths.active_index_pointer.read_bytes() == untouched, (
        "the taint rewrote a pointer naming a different build -- a concurrent `index build`'s "
        "clean publish would be marked unusable"
    )

    applied = project_service.mark_active_index_purge_failed(paths, expected_build_id=BUILD)

    assert applied is True, "the taint did not apply to the build it was written against"
    tainted = _payload(paths)
    assert tainted["purgeFailed"] is True, f"the taint was not recorded: {tainted}"
    assert _without_the_taint(tainted) == _without_the_taint(original), (
        f"tainting the pointer changed something else about the published build: "
        f"{original} -> {tainted}"
    )


def test_a_project_with_no_published_index_is_not_given_a_pointer(tmp_path: Path) -> None:
    """A failed purge must not invent a published build.

    ``publish_purge_for_withdrawal`` returns ``no-published-index`` long before it
    could fail, so this is not reachable through it today -- which is exactly why
    it is pinned here. A pointer written from nothing would name a build id no
    file backs, and every read surface would then report ``index-file-missing``
    for a project whose real state is "never built": the wrong remedy, and the
    failure mode ``ActiveIndexPointer`` exists to keep apart.
    """
    paths = _paths(tmp_path)
    assert not paths.active_index_pointer.exists(), "the arrangement already has a pointer"

    applied = project_service.mark_active_index_purge_failed(paths, expected_build_id=BUILD)

    assert applied is False, "the taint claimed to mark a build in a project that has none"
    assert not paths.active_index_pointer.exists(), (
        "the taint created a pointer naming a build that was never published"
    )


def test_a_pointer_that_cannot_be_read_is_left_alone(tmp_path: Path) -> None:
    """An unreadable pointer names no build, so no build may be marked.

    A pointer that does not parse is already one ``knowledge.search`` stands
    aside from (``index-pointer-invalid``) and one ``index status`` reports as
    corrupt, each with its own remedy: delete the file, then rebuild. Overwriting
    it here would replace a diagnosable state with a plausible-looking pointer
    naming a build id this function was handed rather than one anything
    published, and both surfaces would then send the operator after the wrong
    thing.
    """
    paths = _paths(tmp_path)
    paths.active_index_pointer.write_text("{ this is not json", encoding="utf-8")
    corrupt = paths.active_index_pointer.read_bytes()

    applied = project_service.mark_active_index_purge_failed(paths, expected_build_id=BUILD)

    assert applied is False, "the taint claimed to mark a build a corrupt pointer never named"
    assert paths.active_index_pointer.read_bytes() == corrupt, (
        "the taint overwrote a corrupt pointer, replacing a reported fault with a fabricated "
        "published build"
    )


def test_a_pointer_whose_recorded_flavor_cannot_be_decoded_is_left_alone(
    tmp_path: Path,
) -> None:
    """An undecodable ``indexedSensitivities`` names no known ceiling, so there is
    no build here to protect and the taint must refuse without raising.

    The pointer is derived, git-ignored and unsigned (SEC-7): any local process
    can leave a value here that ``decode_sensitivities`` cannot read -- a level
    word this deployment does not know, or a build recorded under a ceiling since
    removed from ``DISCLOSURE_ORDER``. ``mcp.search._published_index`` already
    stands such a build aside on the flavor axis and degrades every query to the
    unranked canonical scan, so it serves the withdrawn rows to nobody and there
    is nothing left for the taint to close.

    This is not a decorative guard, and the mutation that proves it is removing
    the ``if indexed is None: return False`` line: without it,
    ``mark_active_index_purge_failed`` hands ``None`` to
    ``write_active_index_pointer(indexed_sensitivities=...)``, whose
    ``encode_sensitivities(None)`` raises ``TypeError`` -- which is outside the
    ``except OSError`` this function narrows itself to, so it escapes. That breaks
    the never-raises contract from inside ``publish_purge_for_withdrawal``'s own
    failure path, the one place a second exception turns a reported purge failure
    into a traceback for a ``migrate apply`` that already succeeded. So this pins
    both halves at once: it returns ``False``, and it does so without raising and
    without touching the file.

    The pointer still names ``BUILD`` and still has a valid ``indexBuildId``, so
    the refusal here is the flavor guard specifically -- not the no-pointer, the
    corrupt-pointer, or the wrong-build refusals the tests above already pin. Only
    ``indexedSensitivities`` is made undecodable.
    """
    paths = _paths(tmp_path)
    _publish(paths)
    corrupted = json.loads(paths.active_index_pointer.read_text(encoding="utf-8"))
    corrupted["indexedSensitivities"] = ["nonsense"]
    paths.active_index_pointer.write_text(json.dumps(corrupted, indent=2), encoding="utf-8")
    before = paths.active_index_pointer.read_bytes()

    applied = project_service.mark_active_index_purge_failed(paths, expected_build_id=BUILD)

    assert applied is False, (
        "the taint claimed success against a build whose recorded flavor cannot be read, so "
        "its caller cannot tell an already-stood-aside build from a freshly condemned one"
    )
    assert paths.active_index_pointer.read_bytes() == before, (
        "the pointer changed for a build with an undecodable flavor -- and writing it at all "
        "would have passed None to encode_sensitivities and raised, breaking never-raises"
    )


@pytest.mark.skipif(_CANNOT_BE_REFUSED_BY_A_MODE, reason="POSIX permission bits, and not as root")
def test_a_taint_that_cannot_be_written_reports_failure_instead_of_raising(
    tmp_path: Path,
) -> None:
    """Never raises: its only caller is already handling an exception.

    ``publish_purge_for_withdrawal`` calls this from inside the ``except`` block
    that turns a purge failure into a *reported* outcome, precisely so that
    ``migrate apply`` does not report itself failed for a migration that is
    already committed. An ``OSError`` escaping here would undo that -- the
    operator would see a traceback for an apply that worked, and the one message
    naming the still-stale build (``PURGE_FAILED_REMEDY``) would never be
    printed.

    Driven with a real mode on the state directory rather than a patched
    ``os.replace``, so it covers whichever of the write and the rename actually
    refuses.
    """
    paths = _paths(tmp_path)
    _publish(paths)
    before = paths.active_index_pointer.read_bytes()
    os.chmod(paths.state, 0o500)

    try:
        applied = project_service.mark_active_index_purge_failed(paths, expected_build_id=BUILD)
    finally:
        os.chmod(paths.state, 0o700)

    assert applied is False, (
        "a taint that could not be written reported success, so the caller would report a "
        "stale build as condemned when it is still being served"
    )
    assert paths.active_index_pointer.read_bytes() == before, (
        "the pointer changed despite the write being refused"
    )


# -- What the file actually holds --------------------------------------------


def test_the_taint_is_json_the_pointer_contract_can_carry(tmp_path: Path) -> None:
    """The value on disk is a JSON boolean, not a truthy stand-in.

    ``active-index.json`` is read by two independent surfaces and by whatever a
    future consumer adds; ``_published_index`` tests it with a truthiness check,
    which would accept the string ``"false"`` as a taint. Pinning the on-disk type
    keeps that check honest, and is asserted here rather than through
    :func:`read_active_index_pointer` because the parsed payload cannot tell a
    JSON ``true`` from a Python ``True`` written by something else.
    """
    paths = _paths(tmp_path)

    _publish(paths, purge_failed=True)

    raw = json.loads(paths.active_index_pointer.read_text(encoding="utf-8"))
    assert raw["purgeFailed"] is True, f"purgeFailed is not a JSON boolean: {raw!r}"
