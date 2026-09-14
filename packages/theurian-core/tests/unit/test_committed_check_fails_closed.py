"""``CommittedMigrationCheck`` and the apply refusal fail *closed* (ADR-0034, T-15).

Decision 1's predicate is fail-closed: an apply that cannot *prove* a migration
committed at ``HEAD`` must refuse it, never wave it through. The adapter's integration
tests (``test_committed_migration_check_adapter.py``,
``test_migrate_apply_committed_check.py``) drive that predicate against a real,
present git, so three defensive branches never execute there and survive their own
deletion:

* ``_head_blob`` returning ``None`` when the git binary did not resolve at
  construction (``self._git is None``);
* ``_head_blob`` returning ``None`` when the ``git cat-file`` spawn raises
  ``OSError`` or times out;
* ``_refuse_an_uncommitted_migration`` treating a ``source_path``-less migration as
  ``NOT_TRACKED`` rather than ``COMMITTED``.

Each is correct on ``HEAD`` -- every branch fails closed -- but *unproven*: a
mutation that turns any of them fail-*open* (return ``b""`` -> a spurious
``MODIFIED``/``COMMITTED``, or classify a fileless migration ``COMMITTED``) leaves
the suite green. These tests pin the closed outcome so those mutations are killed.
The commit body records the exact three mutations and their now-RED output.

They are unit tests because each defensive path is reached by *starving* the real
dependency, not exercising it: git is mocked absent or made to raise, and the
fileless case short-circuits before any git call. No real subprocess is spawned.
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pytest
import typer

from theurian.application.project_service import ProjectPaths
from theurian.cli.commands import EXIT_STATE_ERROR, _refuse_an_uncommitted_migration
from theurian.cli.context import CommandContext
from theurian.domain.enums import KnowledgeKind
from theurian.domain.identifiers import ItemId, MigrationId, ProjectId
from theurian.domain.migration import (
    CreateItem,
    LoadedMigrations,
    Migration,
    MigrationSet,
)
from theurian.domain.state import StateHash
from theurian.domain.values import ContentHash
from theurian.infrastructure.determinism import SystemClock, UlidGenerator
from theurian.infrastructure.git import committed_check
from theurian.infrastructure.git.committed_check import (
    CommittedMigrationCheck,
    HeadComparison,
)

pytestmark = pytest.mark.unit

#: A checksum that cannot collide with the digest of empty bytes -- the value a
#: fail-open ``_head_blob`` would hand back. It anchors the assertion that a
#: ``b""`` return would be classified as *some* comparison other than
#: ``NOT_TRACKED``, so the fail-closed pin has teeth against the ``b""`` mutant.
_CHECKSUM: Final = ContentHash.of_bytes(b"the migration file's committed bytes")

#: A valid Crockford-base32 ULID (no I/L/O/U); reused from the adapter's fixtures.
_MIGRATION_ID: Final = "01K1AAAAAA01234567890ABCDE"


def test_a_check_whose_git_binary_did_not_resolve_reads_not_tracked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``git`` cannot be resolved at construction, every file is ``NOT_TRACKED``.

    ``__init__`` resolves ``git`` through ``shutil.which`` once; a ``None`` means it
    vanished mid-command (the module docstring's case). ``_head_blob`` then returns
    ``None`` without spawning anything, so the verdict is ``NOT_TRACKED`` and the
    caller refuses -- the fail-closed outcome the adapter promises.

    The adapter's integration tests all run with a present git, so this branch is
    never taken there. RED if ``if self._git is None: return None`` is mutated to
    ``return b""``: the empty blob's digest differs from ``_CHECKSUM``, so the
    verdict would flip to ``MODIFIED`` -- a fabricated "committed once, edited
    since" answer for a tree where git could not even be run.
    """
    # `committed_check` does `import shutil`, so patching the shared module object
    # is patching the exact `shutil.which` the adapter calls.
    monkeypatch.setattr(shutil, "which", lambda *_a, **_k: None)
    check = CommittedMigrationCheck(Path("/does/not/matter"))

    verdict = check.compare_to_head(".theurian/migrations/x.yaml", _CHECKSUM)

    assert verdict is HeadComparison.NOT_TRACKED, (
        "a check whose git binary did not resolve must fail closed to NOT_TRACKED, "
        "never hand back empty bytes that read as a real comparison"
    )


@pytest.mark.parametrize(
    "raised",
    [
        pytest.param(
            subprocess.TimeoutExpired(cmd=["git"], timeout=committed_check.GIT_TIMEOUT_SECONDS),
            id="timeout",
        ),
        pytest.param(OSError("git: could not spawn"), id="oserror"),
    ],
)
def test_a_git_spawn_that_raises_reads_not_tracked(
    monkeypatch: pytest.MonkeyPatch, raised: Exception
) -> None:
    """A ``git cat-file`` that times out or fails to spawn is ``NOT_TRACKED`` (SEC-19).

    ``_head_blob`` catches ``(OSError, subprocess.TimeoutExpired)`` and returns
    ``None`` -- a spawn that never produced bytes cannot prove a file committed, so
    the apply refuses rather than trusting an absent answer. Both arms are driven:
    the timeout (the bound the adapter sets) and the ``OSError`` (git missing, a
    broken pipe, a resource limit).

    git is mocked *present* so the resolve succeeds and control reaches the
    ``subprocess.run`` call, which is then made to raise. The adapter's integration
    tests spawn a real, fast git, so neither arm executes there. RED if
    ``except (OSError, subprocess.TimeoutExpired): return None`` is mutated to
    ``return b""``: the empty blob would read ``MODIFIED``, waving through -- or
    refusing for the wrong reason -- a migration git never actually answered for.
    """
    # git resolves (so control reaches the spawn), and the spawn then raises. Both
    # names are the shared module objects the adapter imported and calls.
    monkeypatch.setattr(shutil, "which", lambda *_a, **_k: "/usr/bin/git")

    def raise_it(*_a: object, **_k: object) -> subprocess.CompletedProcess[bytes]:
        raise raised

    monkeypatch.setattr(subprocess, "run", raise_it)
    check = CommittedMigrationCheck(Path("/repo/root"))

    verdict = check.compare_to_head(".theurian/migrations/x.yaml", _CHECKSUM)

    assert verdict is HeadComparison.NOT_TRACKED, (
        "a git spawn that raised produced no committed bytes, so the check must fail "
        "closed to NOT_TRACKED rather than treating an empty read as a comparison"
    )


def _fileless_migration() -> Migration:
    """A validated migration whose ``source_path`` is ``None``.

    The loader always sets ``source_path`` (``migration_loader.py``), so this state
    is CLI-unreachable -- but it is the exact input the defensive guard in
    ``_refuse_an_uncommitted_migration`` exists to catch: an in-memory set that no
    committed file backs, which cannot be proven committed.
    """
    return Migration(
        migration_id=MigrationId(_MIGRATION_ID),
        created_at=datetime(2026, 8, 2, 10, 0, tzinfo=UTC),
        author="engineer@example.com",
        operations=(
            CreateItem(
                item_id=ItemId("architecture.auth-policy"),
                kind_=KnowledgeKind.ARCHITECTURE,
                namespace="backend",
                owner="platform-team",
            ),
        ),
        checksum=_CHECKSUM,
        source_path=None,
    )


def _context_of(migration: Migration, root: Path) -> CommandContext:
    """A real ``CommandContext`` carrying exactly *migration*, rooted at *root*.

    ``_refuse_an_uncommitted_migration`` reads only ``context.paths.root`` (to build
    the check, which never spawns git for a ``source_path``-less migration) and
    ``context.loaded.migration_set.migrations``; the remaining fields are given
    valid-but-inert values so the frozen dataclass constructs.
    """
    return CommandContext(
        project_id=ProjectId("demo"),
        paths=ProjectPaths.of(root),
        loaded=LoadedMigrations(MigrationSet((migration,)), (), {}),
        state_hash=StateHash(ContentHash.of_text("inert")),
        clock=SystemClock(),
        ids=UlidGenerator(),
    )


def test_a_migration_with_no_source_path_is_refused_as_uncommitted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A fileless migration is treated ``NOT_TRACKED``, so the apply refuses (fail-closed).

    ``_refuse_an_uncommitted_migration``'s guard classifies a ``source_path``-less
    migration ``NOT_TRACKED`` before any git call -- an in-memory set no committed
    file backs cannot be proven committed, so it is refused rather than waved
    through. Driven at the function seam (the narrowest place the ternary under
    mutation runs) with a real ``CommandContext``: the loader never yields such a
    migration, so this is the only way to reach the branch, and a full CLI run
    cannot.

    RED if ``HeadComparison.NOT_TRACKED if source_path is None`` is mutated to
    ``HeadComparison.COMMITTED if source_path is None``: the guard would then
    fail *open* -- the fileless migration would be classified ``COMMITTED``, the
    loop would ``continue``, and the function would return without refusing,
    letting an unbacked in-memory migration apply.
    """
    context = _context_of(_fileless_migration(), tmp_path)

    with pytest.raises(typer.Exit) as caught:
        _refuse_an_uncommitted_migration(context, as_json=True)

    assert caught.value.exit_code == EXIT_STATE_ERROR
    error = capsys.readouterr().err
    assert "is not committed" in error, error
    assert "git does not track it at HEAD, so it was never committed" in error, error
    assert _MIGRATION_ID in error, (
        "with no source_path the refusal must label the migration by its id"
    )
