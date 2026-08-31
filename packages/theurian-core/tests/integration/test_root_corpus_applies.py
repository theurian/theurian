"""The committed root corpus applies through the real engine (#416).

**What this closes.** Nothing in the suite applied the tracked root
``.theurian/`` corpus before this file:
``git grep -n "load_migrations(" -- packages/theurian-core/tests`` (84 hits, 9
files, measured 2026-08-31) loads either ``examples/sample-project`` or a
synthetic ``tmp_path`` fixture in every one of them. A corpus that only ever
meets schema validation and this suite's own YAML arithmetic can still be
unappliable -- and was: the adversarial round's three perturbations of the
ADR-0013 re-seed migration (``expectedRevision`` set to a well-formed ULID no
revision holds, the field deleted outright, the migration's inner ``id``
rewritten to sort before the item's earlier revision) each survived the
*whole* suite, ``theurian migrate validate`` included, while making
``theurian migrate apply`` refuse **all 27 migrations, transactionally** --
one failing operation rolls back the entire run (ADR-0006), so a fresh
install lands zero knowledge items, not 26.

**Why apply is strictly stronger than schema validity.** The published schema
checks *shape*: a migration document parses, its fields match their types, an
``expectedRevision`` looks like a ULID or is absent. It says nothing about
whether the *sequence* of operations, replayed against a store that starts
empty and accumulates state one migration at a time, stays jointly
consistent -- an ``expectedRevision`` naming a revision the chain has not
produced yet, two migrations racing to pin the same ``contentFile``, an alias
colliding with a live item id, a scope no migration is allowed to declare.
``test_dogfood_corpus_governance.py`` now reconstructs one such invariant by
hand (``test_every_expected_revision_names_the_chain_the_migrations_construct``),
which is real value for exactly that invariant and no protection for the
others: every whole-set guard ``theurian migrate apply`` runs
(``run_static_migration_guards``, then the engine's own transaction) is a
family this test does not have to re-derive, because it runs the real thing.
That is the general guard the static rule cannot be: whatever the engine
enforces that nobody has hand-modelled yet, this still catches, the day it is
added to the engine.

**Population, and why it is narrower than the governance module's.** Loads
the real directory listing, but only after confirming -- via ``git``, never
the ``tools/mutate.py`` manifest -- that the listing holds nothing git does
not track: :func:`load_migrations` reads every ``.yaml`` file physically
present in ``.theurian/migrations/`` with no regard for tracking, so an
untracked local draft sitting there on a contributor's own machine would
otherwise be tested as if it were the committed corpus. A ``tools/mutate.py``
copy carries a manifest and *could* answer from it, but this test's subject is
a real SQLite database and a real transaction -- work, not YAML arithmetic --
and running it once per worker tree for every mutation in a batch that
touches nothing under ``.theurian/`` would tax the harness for no return. So
it skips loudly there rather than passing vacuously: the manifest answer is
refused on purpose, not because it would be wrong.
"""

from __future__ import annotations

import shutil
import subprocess
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pytest
from fakes.clock import FrozenClock

from theurian.application.project_service import ProjectPaths, resolve_state_hash
from theurian.cli.context import schema_root
from theurian.cli.migration_pipeline import apply_migration_set
from theurian.domain.identifiers import ItemId, ProjectId
from theurian.domain.migration import MIGRATION_ENGINE_VERSION, current_revision_in
from theurian.domain.project import DEFAULT_KNOWLEDGE_DIRECTORY, Project
from theurian.infrastructure.filesystem.migration_loader import load_migrations
from theurian.infrastructure.sqlite.connection import create_database, open_read_connection
from theurian.infrastructure.sqlite.schema import SCHEMA_VERSION

pytestmark = pytest.mark.integration

#: ``parents[4]`` is ``.../tests/integration/`` -> ``tests`` -> ``theurian-core``
#: -> ``packages`` -> repo root, the same reckoning
#: ``test_dogfood_corpus_governance.py`` uses.
REPO_ROOT: Final = Path(__file__).resolve().parents[4]
MIGRATIONS_DIRECTORY: Final = REPO_ROOT / ".theurian" / "migrations"

#: A lower bound, not an exact count -- the corpus is expected to grow, and
#: every item a future migration adds is fully governed whether or not this
#: number is ever updated. Mirrors ``test_dogfood_corpus_governance.py``'s
#: ``MINIMUM_MIGRATIONS`` (26) as an independent measurement, not an import:
#: the two floors happen to share a value today only because the corpus's one
#: multi-revision item (the ADR-0013 re-seed) has no multi-revision sibling
#: yet, and a second one would grow migrations without growing items.
MINIMUM_KNOWLEDGE_ITEMS: Final = 26

#: The item the #416 re-seed gave a second revision -- the one member of this
#: corpus a from-empty apply must resolve to something other than its own
#: first ``upsertRevision``.
_RESEEDED_ITEM: Final = ItemId("architecture.ai-writes-produce-proposals")

#: Frozen rather than ``datetime.now()``: a project row's ``registered_at`` is
#: metadata this test never reads back, but ``Project.__post_init__`` still
#: requires it timezone-aware, and a wall-clock value would make a rerun's
#: database differ from this run's for a field nothing here compares.
_REGISTERED_AT: Final = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


def _skip_unless_git_confirms_the_migrations_directory_holds_only_tracked_files() -> None:
    """Refuse to run over anything ``git`` cannot vouch for.

    Two distinct refusals, both loud:

    - No git, or git cannot answer for this checkout (a ``tools/mutate.py``
      copy, which carries no ``.git`` on purpose -- see the module docstring
      for why this test does not fall back to the copy's manifest the way
      ``test_dogfood_corpus_governance.py``'s ``_tracked()`` does).
    - Git answers, but the directory holds a ``.yaml`` file it does not
      track. :func:`load_migrations` reads the whole directory with no
      regard for tracking, so an uncommitted local draft would otherwise be
      loaded and applied as if it were part of the corpus this PR ships.
    """
    git = shutil.which("git")
    if git is None:
        pytest.skip(
            f"no git on this machine: nothing here can confirm {MIGRATIONS_DIRECTORY} holds "
            f"only tracked files, so this test declines to load it and report a false answer"
        )

    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell, no caller input
        [
            git,
            "-c",
            f"safe.directory={REPO_ROOT}",
            "-C",
            str(REPO_ROOT),
            "ls-files",
            "-z",
            "--",
            str(MIGRATIONS_DIRECTORY.relative_to(REPO_ROOT)),
        ],
        capture_output=True,
        check=False,
        timeout=30,
    )
    if completed.returncode != 0:
        pytest.skip(
            f"`git ls-files` could not answer for {REPO_ROOT} (exit {completed.returncode}: "
            f"{completed.stderr.decode('utf-8', 'replace').strip()}). Expected inside a "
            f"`tools/mutate.py` copy; see the module docstring for why this test skips there "
            f"rather than falling back to the copy's manifest."
        )

    tracked_names = {
        entry.rsplit("/", 1)[-1]
        for entry in completed.stdout.decode("utf-8", "surrogateescape").split("\0")
        if entry
    }
    on_disk_names = {path.name for path in MIGRATIONS_DIRECTORY.iterdir() if path.suffix == ".yaml"}
    untracked = sorted(on_disk_names - tracked_names)
    if untracked:
        pytest.skip(
            f"{MIGRATIONS_DIRECTORY} holds .yaml file(s) git does not track: {untracked}. "
            f"`load_migrations` reads the whole directory regardless of tracking, so this "
            f"would apply a local draft as if it were the committed corpus. Commit it, "
            f"remove it, or move it elsewhere before running this test."
        )


def test_the_committed_root_corpus_applies_cleanly_to_an_empty_store(tmp_path: Path) -> None:
    """A from-empty apply of every tracked migration lands the corpus with none refused.

    ADR-0005 rule 8 -- applying all migrations to an empty store reproduces the
    full canonical state -- is the claim this exercises directly: the real
    loader, the real whole-set guards, the real engine, one write transaction,
    exactly the pipeline ``theurian migrate apply`` runs
    (``theurian.cli.migration_pipeline.apply_migration_set``, the composition
    root ADR-0027 decision 2 names as the single definition both ``migrate
    apply`` and ``propose accept``'s rehearsal reach). ``database`` and
    ``write_lock`` are ``tmp_path`` paths passed explicitly rather than
    derived from :class:`ProjectPaths` -- the state database, write lock and
    active pointer a real project would compute all live under the real
    ``.theurian/``, and this test writes none of them.

    Two cheap checks after the apply, not a full read-model comparison: the
    applied count matches the migration count with nothing skipped (a skip on
    an empty store is itself a defect -- every migration should be new), the
    surfaceable item count clears the floor, and the re-seeded item's
    ``current_revision_id`` is exactly what :func:`current_revision_in` -- the
    same pure function ``propose accept``'s pre-check calls -- derives from
    the loaded set's own application order. That last one is the one the
    static chain rule now also holds; this is its dynamic twin, unmodelled and
    reading the real engine's own answer.
    """
    _skip_unless_git_confirms_the_migrations_directory_holds_only_tracked_files()

    paths = ProjectPaths.of(REPO_ROOT)
    loaded = load_migrations(paths.root, paths.migrations, schema_root())

    project = Project(
        project_id=ProjectId("root-corpus-applicability-test"),
        root_path=str(REPO_ROOT),
        repository_url=None,
        default_branch="main",
        knowledge_directory=DEFAULT_KNOWLEDGE_DIRECTORY,
        registered_at=_REGISTERED_AT,
    )

    state_hash = resolve_state_hash(loaded, SCHEMA_VERSION)
    database = tmp_path / "state.sqlite"
    create_database(database, str(state_hash), MIGRATION_ENGINE_VERSION)

    report = apply_migration_set(
        database=database,
        write_lock=tmp_path / "write.lock",
        project=project,
        loaded=loaded,
        clock=FrozenClock(),
        database_created=True,
    )

    assert not report.skipped and len(report.applied) == len(loaded.migration_set), (
        f"applying the committed corpus to an empty store applied "
        f"{len(report.applied)}/{len(loaded.migration_set)} migrations and skipped "
        f"{[str(migration_id) for migration_id in report.skipped]}. Every migration should be "
        f"new against an empty database; a skip here means the engine considered one "
        f"already applied before this test ever wrote to it."
    )

    with closing(open_read_connection(database)) as connection:
        (item_count,) = connection.execute(
            "SELECT COUNT(*) FROM knowledge_items WHERE project_id = ?",
            (project.project_id.value,),
        ).fetchone()
        row = connection.execute(
            "SELECT current_revision_id FROM knowledge_items WHERE project_id = ? AND item_id = ?",
            (project.project_id.value, _RESEEDED_ITEM.value),
        ).fetchone()

    assert item_count >= MINIMUM_KNOWLEDGE_ITEMS, (
        f"the applied store holds {item_count} knowledge items, fewer than the "
        f"{MINIMUM_KNOWLEDGE_ITEMS}-item floor this corpus has always cleared. Committed "
        f"knowledge disappearing is the one direction this floor exists to catch."
    )

    assert row is not None, (
        f"{_RESEEDED_ITEM.value} was not created by the applied migrations. The ADR-0013 "
        f"re-seed (#416) revises this exact item, so its absence means the corpus this test "
        f"loaded is not the one #416 shipped."
    )
    current_revision = row["current_revision_id"]
    expected_revision = current_revision_in(loaded.migration_set, _RESEEDED_ITEM)
    assert current_revision == (
        expected_revision.value if expected_revision is not None else None
    ), (
        f"after applying, {_RESEEDED_ITEM.value}'s current_revision_id is {current_revision!r}; "
        f"current_revision_in (the pure function propose accept's own pre-check calls) derives "
        f"{expected_revision!r} from the same loaded set's application order. A mismatch here "
        f"means the engine's replay and the production helper that reasons about it without "
        f"replaying have quietly disagreed."
    )
