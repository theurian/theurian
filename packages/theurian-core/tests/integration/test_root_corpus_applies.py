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
``theurian migrate apply`` refuse **all 27 migrations the corpus then held,
transactionally** -- one failing operation rolls back the entire run
(ADR-0006), so a fresh install lands zero knowledge items, not 26.

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

**A third, content-shaped check, and why the first two are not enough.** The
adversarial re-confirmation reproduced the gap directly: ``git revert
--no-commit d515bef`` (the ADR-0013 re-seed's *payload* -- the body and the
migration's ``contentSha256``, not its ``expectedRevision`` pin) leaves the
whole suite green at the same test count, because both the revision-id
equality above and the static chain rule key on *identifiers*, and a reverted
payload still carries the seed's own identifiers. Content, not an id, is what
actually moved; the applied store's current body for the re-seeded item is
therefore also checked for the corrected claim (``#414`` and the wording it
introduced) and against neither retracted pattern the source ADR corrected
(``reports proposal age``, ``warns past a threshold``). Content-shaped rather
than revision-id-shaped, deliberately: an id-keyed check stops distinguishing
the correction from the retraction the moment either is reverted while ids
stay pinned, and a *future*, legitimate re-seed changes the current revision
id again without this test's other assertions changing at all.

**The same class recurs, pre-empted rather than re-found.** #199 unit C's
second wave (#471) re-seeds three more items the same way #416 re-seeded
ADR-0013 -- ``propose``/``accept`` through the real write path -- and the
#440 round's ADV-RC MEDIUM-1 lesson generalises immediately: reverting any of
the three wave commits (payload, not ``expectedRevision``) would leave this
whole suite green at the same test count, for the identical reason the
ADR-0013 revert did. So each of the three gets its own content-shaped pin
below, pre-emptively, rather than waiting for a round to rediscover the same
class a fourth time.

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
#: ``MINIMUM_MIGRATIONS`` (26) as an independent measurement, not an import.
#:
#: **The two floors coincide by history, not by mechanism** -- the same
#: account ``tools/corpus_drift.py``'s own floor constant records for its
#: pair with ``MINIMUM_MIGRATIONS``: both were set to 26 the same day
#: (2026-08-20, the ``dogfood/dev7-corpus`` seeding), and "the same number
#: and the same shape" describes that day, not a claim that the two
#: populations stay equal. They do not, and the mechanism is the opposite of
#: a coincidence: a re-seed is a second migration, and a second revision,
#: over an item that already exists, so it grows the migration count
#: without growing the item count -- which is *why* the two populations
#: diverge even though the two floor constants do not. Measured 2026-09-01
#: at 7b2ca67: four items now carry two revisions each -- the ADR-0013
#: re-seed (#416) plus the three #199 unit C second-wave re-seeds (#471,
#: :data:`_SECOND_WAVE_MARKERS`) -- and the corpus still holds exactly 26
#: distinct items.
MINIMUM_KNOWLEDGE_ITEMS: Final = 26

#: The item the #416 re-seed gave a second revision -- the one member of this
#: corpus a from-empty apply must resolve to something other than its own
#: first ``upsertRevision``.
_RESEEDED_ITEM: Final = ItemId("architecture.ai-writes-produce-proposals")

#: #199 unit C's second wave (#471): three more items re-seeded through
#: propose/accept, and a literal string each corrected body carries that
#: neither the original seed nor a partially-corrected intermediate state
#: does -- pre-empted from the ADV-RC MEDIUM-1 class the #440 round found
#: (a reverted re-seed payload leaves the whole suite green at the same
#: test count, because a revision-id check alone cannot tell a correction
#: from a reverted one).
#:
#: **Three-point measured, not two -- this round's own lesson.** A round-one
#: version of this pin keyed on ``write.lock``/``ADR-0025``, measured only
#: seed-vs-current. Both the code and adversarial reviewers independently
#: demonstrated that a *two*-point marker passes a re-seed drafted from an
#: **intermediate** source commit -- one where the item's ADR had already
#: moved past the seed but had not yet reached the correction the marker was
#: meant to pin -- because the general vocabulary (``write.lock``,
#: ``ADR-0025``) was already present pre-correction for an unrelated reason.
#: The fix: key each marker on the correction's own issue reference instead
#: of a word from its prose, and measure three points -- seed, every
#: intermediate correction commit in the item's ``docs/adr/`` history,
#: current -- not just the two ends.
#:
#: Wrap-safe single tokens, deliberately: an issue reference like ``#436``
#: cannot split across a Markdown line wrap the way a multi-word phrase can
#: (the ``#414``/"owed, not shipped" trap the ADR-0013 check above already
#: works around by checking two substrings rather than one contiguous
#: phrase).
#:
#: **No clean negative twin for any of the three.** A ``not in`` pin needs a
#: token present in the superseded body and absent from current *and* from
#: every intermediate -- and this corpus's ADR-amendment convention quotes
#: the retracted claim verbatim inside its own correction note, so the
#: retracted wording reappears in the corrected text and fails to
#: discriminate. Measured: "on the state database" (0018's retracted lock
#: claim) appears 0 times in the superseded body and 1 time in the current
#: one, quoted as part of the correction; "an operator cannot yet move it"
#: (0008's retracted config claim) appears once in both; "reads either back
#: today" (0024's retracted claim) appears once in both. No twin added for
#: any of the three; each item is pinned by its correction marker alone.
#:
#: Measured 2026-09-01, ``grep -cF <marker>`` -- ``-F``, literal, named
#: because it is not the instrument the round-one version used: plain
#: ``grep -c "write.lock"`` treats the ``.`` as "any character" and reports
#: 8 for a body a literal count reports 3 for. Three points each --
#: seed, intermediate, current:
#:
#: - ``architecture.single-writer-synchronous-in-m1`` / ``#436``: 0 (seed,
#:   ``2a98d4c``) / 0 (intermediate, ``14dd466`` -- #432, the lock-file
#:   claim's *own* correction commit, moved the claim but not yet to the
#:   wording #436 later added) / 5 (current, ``5a9a1e5``, this item's #471
#:   anchor).
#: - ``architecture.raptor-forest`` / ``#426``: 0 (seed, ``2a98d4c``) / 0
#:   (intermediate, ``b857c1a`` -- #119/#352's sensitivity-enforcement
#:   change, the commit the withdrawn ``ADR-0025`` marker had mis-attributed
#:   to #448) / 2 (current, ``3749581``, this item's #471 anchor).
#: - ``architecture.a-purge-is-a-build`` / ``#426``: 0 (seed, ``2a98d4c``) /
#:   0 (intermediate, ``b857c1a``, same commit as above) / 1 (current,
#:   ``3749581``, this item's #471 anchor).
_SECOND_WAVE_MARKERS: Final[tuple[tuple[ItemId, str], ...]] = (
    (ItemId("architecture.single-writer-synchronous-in-m1"), "#436"),
    (ItemId("architecture.raptor-forest"), "#426"),
    (ItemId("architecture.a-purge-is-a-build"), "#426"),
)

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
    # `name.endswith(".yaml")`, matching the loader's own filter exactly
    # (`migration_loader.py`'s `candidates` line): `path.suffix` would miss a
    # bare `.yaml` filename (`Path(".yaml").suffix == ""`), which the loader
    # still accepts.
    on_disk_names = {
        path.name for path in MIGRATIONS_DIRECTORY.iterdir() if path.name.endswith(".yaml")
    }
    untracked = sorted(on_disk_names - tracked_names)
    if untracked:
        pytest.skip(
            f"{MIGRATIONS_DIRECTORY} holds .yaml file(s) git does not track: {untracked}. "
            f"`load_migrations` reads the whole directory regardless of tracking, so this "
            f"would apply a local draft as if it were the committed corpus. Commit it, "
            f"remove it, or move it elsewhere before running this test."
        )


def _current_body(database: Path, project_id: ProjectId, item_id: ItemId) -> str:
    """The applied store's current body for ``item_id``.

    The same two-hop read the ADR-0013 check below performs inline --
    ``knowledge_items`` for the pointer, ``knowledge_revisions`` for what it
    points at -- pulled out once so each of the second-wave pins is two
    lines: fetch the body, assert its marker. Not used by the ADR-0013 check
    itself, which additionally cross-checks the pointer against
    :func:`current_revision_in` and stays as originally written rather than
    being rewired through a helper introduced for a later item.
    """
    with closing(open_read_connection(database)) as connection:
        row = connection.execute(
            "SELECT current_revision_id FROM knowledge_items WHERE project_id = ? AND item_id = ?",
            (project_id.value, item_id.value),
        ).fetchone()
        assert row is not None, (
            f"{item_id.value} has no row in knowledge_items after applying. Every "
            f"migration that names it as a createItem target should have created it."
        )
        current_revision = row["current_revision_id"]
        assert current_revision is not None, (
            f"{item_id.value} has no current revision after applying, so there is no "
            f"body to check its content."
        )
        body_row = connection.execute(
            "SELECT body FROM knowledge_revisions WHERE project_id = ? AND revision_id = ?",
            (project_id.value, current_revision),
        ).fetchone()
        assert body_row is not None, (
            f"knowledge_revisions holds no row for {current_revision!r}, the revision "
            f"knowledge_items.current_revision_id just named for {item_id.value}. A "
            f"pointer with nothing behind it is a store the engine's own write "
            f"transaction should never produce."
        )
        return str(body_row["body"])


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

    Three cheap checks after the apply, not a full read-model comparison: the
    applied count matches the migration count with nothing skipped (a skip on
    an empty store is itself a defect -- every migration should be new), the
    surfaceable item count clears the floor, the re-seeded item's
    ``current_revision_id`` is exactly what :func:`current_revision_in` -- the
    same pure function ``propose accept``'s pre-check calls -- derives from
    the loaded set's own application order (the one the static chain rule now
    also holds; this is its dynamic twin, unmodelled and reading the real
    engine's own answer), and the applied body's *content* carries the
    correction rather than merely being reachable at the expected id -- see
    the module docstring's third check for why an id match alone is not
    enough (ADV-RC MEDIUM-1).

    A fourth family, added for #199 unit C's second wave (#471): the same
    content-shaped pin, once per :data:`_SECOND_WAVE_MARKERS` entry, run
    pre-emptively rather than waiting for a round to reproduce ADV-RC
    MEDIUM-1 a second time against a different item. Each reverts its own
    item's re-seed commit RED and nothing else's -- see the entries'
    docstring for the measured three-point (seed / intermediate / current)
    discrimination each correction-keyed marker was chosen for, and for why
    the round's own first version of this pin (a two-point, general-word
    marker) is not what ships here.
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
        f"{_RESEEDED_ITEM.value} has no row in knowledge_items after applying. Every seed "
        f"migration creates this item via createItem, so its absence means the migrations "
        f"this test loaded never ran at all -- not, by itself, anything about whether the "
        f"#416 correction is present. The content check below carries that claim."
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

    assert current_revision is not None, (
        f"{_RESEEDED_ITEM.value} has no current revision after applying, so there is no body "
        f"to check for the #416 correction."
    )
    with closing(open_read_connection(database)) as connection:
        body_row = connection.execute(
            "SELECT body FROM knowledge_revisions WHERE project_id = ? AND revision_id = ?",
            (project.project_id.value, current_revision),
        ).fetchone()
    assert body_row is not None, (
        f"knowledge_revisions holds no row for {current_revision!r}, the revision "
        f"knowledge_items.current_revision_id just named. A pointer with nothing behind it is "
        f"a store the engine's own write transaction should never produce."
    )
    body = body_row["body"]

    # `#414` and `owed, not` never wrap onto separate lines within themselves (unlike `owed,
    # not shipped`, which does in the source Markdown), so each is checked as its own
    # substring rather than as one contiguous phrase spanning the wrap.
    assert "#414" in body and "owed, not" in body, (
        f"the applied body for {_RESEEDED_ITEM.value} (revision {current_revision}) does not "
        f"carry the #414 correction ('#414' and 'owed, not' both expected as substrings). "
        f"`git revert --no-commit d515bef` -- the re-seed's payload, not its expectedRevision "
        f"pin -- leaves every other assertion in this test green at the same revision id; "
        f"this is the check ADV-RC MEDIUM-1 asked for that would not."
    )
    assert "reports proposal age" not in body, (
        f"the applied body for {_RESEEDED_ITEM.value} (revision {current_revision}) still "
        f"carries the retracted claim 'reports proposal age' (#252)."
    )
    assert "warns past a threshold" not in body, (
        f"the applied body for {_RESEEDED_ITEM.value} (revision {current_revision}) still "
        f"carries the retracted claim 'warns past a threshold' (#252)."
    )

    # Pre-empted from the same class: #199 unit C's second wave (#471)
    # re-seeded three more items through the real write path, and reverting
    # any one wave commit's payload would leave the suite green at the same
    # test count for the identical reason the ADR-0013 revert above did --
    # see :data:`_SECOND_WAVE_MARKERS`'s docstring for the measured
    # three-point (seed / intermediate / current) discrimination behind
    # each correction-keyed marker.
    for item_id, marker in _SECOND_WAVE_MARKERS:
        second_wave_body = _current_body(database, project.project_id, item_id)
        assert marker in second_wave_body, (
            f"the applied body for {item_id.value} does not carry {marker!r}. Reverting "
            f"this item's #471 re-seed payload -- the body, not its expectedRevision pin -- "
            f"would leave this assertion the only one in this test file to notice, per the "
            f"ADV-RC MEDIUM-1 class the #440 round found."
        )
