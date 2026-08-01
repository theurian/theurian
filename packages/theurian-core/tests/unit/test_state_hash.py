"""State hash determinism (ADR-0007, ADR-0016, ADR-0017).

Every guarantee that rests on the state hash -- O(1) branch switching,
reproducible pinned snapshots, cache correctness -- reduces to one property:
identical inputs produce an identical hash anywhere, in any process, in any
discovery order. These tests exist to keep that true.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from theurian.domain.errors import DomainError
from theurian.domain.identifiers import MigrationId
from theurian.domain.state import StateHash, StateInputs, compute_state_hash
from theurian.domain.values import ContentHash

MIGRATION_A = MigrationId("01K1AAAAAA01234567890ABCDE")
MIGRATION_B = MigrationId("01K1BBBBBB01234567890ABCDE")

CHECKSUM_A = ContentHash.of_text("migration a")
CHECKSUM_B = ContentHash.of_text("migration b")
CONTENT_1 = ContentHash.of_text("body one")
CONTENT_2 = ContentHash.of_text("body two")


def _inputs(**overrides: object) -> StateInputs:
    base: dict[str, object] = {
        "migrations": ((MIGRATION_A, CHECKSUM_A), (MIGRATION_B, CHECKSUM_B)),
        "content_checksums": (CONTENT_1, CONTENT_2),
        "schema_version": 1,
        "engine_version": 1,
    }
    base.update(overrides)
    return StateInputs(**base)  # type: ignore[arg-type]


# -- The golden vector -----------------------------------------------------

#: Committed on purpose. If this changes, every user's cached state silently
#: becomes unreachable, so a change must be a deliberate act with a schema or
#: engine version bump -- not an accident of refactoring the hashing code.
GOLDEN_HASH = "5dc7b325efc2ed505a8f037b6b121d69e6c238f774df9125bb0eaedad1cc929d"


def test_golden_vector_is_stable() -> None:
    """The hash of a fixed input set never changes without a version bump."""
    actual = compute_state_hash(_inputs())
    assert actual.value.value == GOLDEN_HASH, (
        "The state hash algorithm changed. Every cached state on every machine "
        "just became unreachable. If this was deliberate, bump SCHEMA_VERSION or "
        "MIGRATION_ENGINE_VERSION and update GOLDEN_HASH."
    )


def test_hash_is_stable_across_processes() -> None:
    """Guards against a hash that depends on `PYTHONHASHSEED`.

    Iterating a set or dict without sorting produces a per-process order. That
    bug is invisible within one process and catastrophic across machines, so it
    is checked in a genuinely separate interpreter.
    """
    program = (
        "from theurian.domain.state import StateInputs, compute_state_hash;"
        "from theurian.domain.identifiers import MigrationId;"
        "from theurian.domain.values import ContentHash;"
        "print(compute_state_hash(StateInputs("
        f"  migrations=((MigrationId('{MIGRATION_A}'), ContentHash('{CHECKSUM_A}')),"
        f"              (MigrationId('{MIGRATION_B}'), ContentHash('{CHECKSUM_B}'))),"
        f"  content_checksums=(ContentHash('{CONTENT_1}'), ContentHash('{CONTENT_2}')),"
        "   schema_version=1, engine_version=1)))"
    )

    seeds = ["0", "1", "12345"]
    results = {
        subprocess.run(  # noqa: S603 - fixed argv
            [sys.executable, "-c", program],
            capture_output=True,
            text=True,
            check=True,
            env={"PYTHONHASHSEED": seed, "PATH": "/usr/bin:/bin"},
        ).stdout.strip()
        for seed in seeds
    }
    assert len(results) == 1, f"hash varies with PYTHONHASHSEED: {results}"
    assert results.pop() == GOLDEN_HASH


# -- Order independence ----------------------------------------------------


def test_migration_order_does_not_matter() -> None:
    """Filesystem enumeration order must not leak into the hash."""
    forward = compute_state_hash(
        _inputs(migrations=((MIGRATION_A, CHECKSUM_A), (MIGRATION_B, CHECKSUM_B)))
    )
    reverse = compute_state_hash(
        _inputs(migrations=((MIGRATION_B, CHECKSUM_B), (MIGRATION_A, CHECKSUM_A)))
    )
    assert forward == reverse


def test_content_checksum_order_does_not_matter() -> None:
    forward = compute_state_hash(_inputs(content_checksums=(CONTENT_1, CONTENT_2)))
    reverse = compute_state_hash(_inputs(content_checksums=(CONTENT_2, CONTENT_1)))
    assert forward == reverse


# -- Sensitivity -----------------------------------------------------------


def test_a_changed_migration_changes_the_hash() -> None:
    changed = compute_state_hash(
        _inputs(
            migrations=((MIGRATION_A, ContentHash.of_text("edited")), (MIGRATION_B, CHECKSUM_B))
        )
    )
    assert changed != compute_state_hash(_inputs())


def test_a_changed_body_changes_the_hash() -> None:
    """ADR-0016: editing a content file forks a new state."""
    changed = compute_state_hash(
        _inputs(content_checksums=(ContentHash.of_text("edited body"), CONTENT_2))
    )
    assert changed != compute_state_hash(_inputs())


def test_an_added_migration_changes_the_hash() -> None:
    changed = compute_state_hash(_inputs(migrations=((MIGRATION_A, CHECKSUM_A),)))
    assert changed != compute_state_hash(_inputs())


def test_schema_version_changes_the_hash() -> None:
    """ADR-0017: a schema bump invalidates every existing state database."""
    assert compute_state_hash(_inputs(schema_version=2)) != compute_state_hash(_inputs())


def test_engine_version_changes_the_hash() -> None:
    """An engine change must invalidate cached state, not reinterpret it."""
    assert compute_state_hash(_inputs(engine_version=2)) != compute_state_hash(_inputs())


def test_migration_id_and_checksum_are_not_interchangeable() -> None:
    """Guards the field separator.

    Without one, ``(id, checksum)`` and a shifted pairing would serialise to the
    same bytes.
    """
    swapped = compute_state_hash(
        _inputs(migrations=((MIGRATION_A, CHECKSUM_B), (MIGRATION_B, CHECKSUM_A)))
    )
    assert swapped != compute_state_hash(_inputs())


def test_empty_state_has_a_hash() -> None:
    """A project with no migrations still has a well-defined state."""
    empty = compute_state_hash(_inputs(migrations=(), content_checksums=()))
    assert len(empty.value.value) == 64
    assert empty != compute_state_hash(_inputs())


# -- Validation ------------------------------------------------------------


def test_duplicate_migration_ids_are_rejected() -> None:
    with pytest.raises(DomainError, match="Duplicate migration ids"):
        _inputs(migrations=((MIGRATION_A, CHECKSUM_A), (MIGRATION_A, CHECKSUM_B)))


@pytest.mark.parametrize("version", [0, -1])
def test_non_positive_versions_are_rejected(version: int) -> None:
    with pytest.raises(DomainError):
        _inputs(schema_version=version)
    with pytest.raises(DomainError):
        _inputs(engine_version=version)


# -- Presentation ----------------------------------------------------------


def test_database_filename_is_derived_from_the_hash() -> None:
    state = compute_state_hash(_inputs())
    assert state.database_filename == f"theurian-state-{state.short}.sqlite"
    assert len(state.short) == 12


def test_two_distinct_states_get_distinct_filenames() -> None:
    a = compute_state_hash(_inputs())
    b = compute_state_hash(_inputs(schema_version=2))
    assert a.database_filename != b.database_filename


def test_state_hash_renders_as_its_full_digest() -> None:
    state = StateHash(ContentHash.of_text("x"))
    assert str(state) == ContentHash.of_text("x").value
