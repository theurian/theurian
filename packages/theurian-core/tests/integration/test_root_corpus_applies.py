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

**The same class recurs, and every re-seed pays the same toll.** #199 unit C's
second wave (#471) re-seeded three more items the same way #416 re-seeded
ADR-0013 -- ``propose``/``accept`` through the real write path -- and #315's
drift sweep re-seeded eleven more. The #440 round's ADV-RC MEDIUM-1 lesson
generalises to every one of them, but **only at one revert depth**, and saying
so precisely is the point -- an earlier version of this paragraph claimed the
whole class was invisible to everything else, and the adversarial round
measured that false. Three depths, measured 2026-09-02:

- **body + ``contentSha256``.** Caught already, by
  ``test_dogfood_corpus_governance.py``'s
  ``test_every_pinned_body_is_byte_identical_to_its_source_anchor_commit``: the
  body no longer matches the blob at the anchor its own migration still names.
- **\\+ the migration's ``sourceAnchors[].commitSha``.** Still caught, by
  ``test_every_evidence_anchor_is_one_a_committed_migration_also_names``: the
  evidence file names an anchor no migration does any more.
- **\\+ the matching ``evidence.json`` ``commitSha``.** Nothing else notices.
  Every remaining rule compares author-supplied values against themselves -- a
  body against the digest its own migration declares, that body against the
  blob at the anchor that same migration now names -- and a three-file
  coordinated revert is internally consistent at all of them.

That third shape is what :data:`_RESEED_PAYLOAD_MARKERS` below exists for, and
it is the only one it is the sole catcher of. The one rule that reads the
*current* ``docs/`` tree, ``tools/corpus_drift.py``, would see all three, but
CI runs it ``--advisory`` (``.github/workflows/core.yml``): exit 0 even on
drift, by design, so it gates nothing.

**Population, and why it is narrower than the governance module's.** The
population is ``git ls-files -- .theurian``, copied into a scratch tree and
loaded from there, so what gets applied is the *committed* corpus by
construction. :func:`load_migrations` reads every ``.yaml`` physically present
with no regard for tracking, so loading the real directory in place would
apply an untracked local draft as if it had been committed.

That used to be handled by skipping the whole test whenever an untracked
``.yaml`` sat in the directory, which put this file to sleep exactly where it
is needed most: the maintainer's dogfooding machine keeps machine-local vault
notes under ``.theurian/`` (fenced in ``.git/info/exclude``), so every
assertion here -- the twelve payload markers included -- was disabled on the
one tree where re-seeds are authored and a mis-measured marker would first be
written. Filtering serves the original concern strictly better than skipping
did: the draft is not stepped around, it is never loaded (#490 round two).

One skip remains, for the one case where there is no tracked set to filter to:
``git`` absent, or unable to answer for this checkout -- a ``tools/mutate.py``
copy, which carries no ``.git`` on purpose. That copy carries a manifest and
*could* answer from it, but this test's subject is a real SQLite database and a
real transaction -- work, not YAML arithmetic -- and running it once per worker
tree for every mutation in a batch that touches nothing under ``.theurian/``
would tax the harness for no return. So it skips loudly there rather than
passing vacuously: the manifest answer is refused on purpose, not because it
would be wrong.
"""

from __future__ import annotations

import shutil
import subprocess
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pytest
from fakes.clock import FrozenClock

from theurian.application.project_service import ProjectPaths, resolve_state_hash
from theurian.cli.context import schema_root
from theurian.cli.migration_pipeline import apply_migration_set
from theurian.domain.identifiers import ItemId, ProjectId, RevisionId
from theurian.domain.migration import (
    MIGRATION_ENGINE_VERSION,
    LoadedMigrations,
    UpsertRevision,
    current_revision_in,
)
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
#: diverge even though the two floor constants do not. **How far they have
#: diverged is not recorded here.** The live record is
#: ``EXPECTED_CORPUS_POPULATION`` in ``test_dogfood_corpus_governance.py``,
#: whose ``multi_revision_items`` and ``distinct_items`` are recomputed from
#: the tracked corpus on every run and moved deliberately in the same change
#: that moves the corpus; a second narration of the same numbers here would go
#: stale silently, which is the class #458 closed. What stays is one dated
#: point measurement, frozen at the commit named beside it: measured
#: 2026-09-01 at ``7b2ca67``, four items carried two revisions each -- the
#: ADR-0013 re-seed (#416) plus the three #199 unit C second-wave re-seeds
#: (#471) -- over 26 distinct items. #315's eleven-item drift sweep moved both
#: of those figures afterwards, and moved them in the live record.
MINIMUM_KNOWLEDGE_ITEMS: Final = 26

#: The item the #416 re-seed gave a second revision -- the one member of this
#: corpus a from-empty apply must resolve to something other than its own
#: first ``upsertRevision``.
_RESEEDED_ITEM: Final = ItemId("architecture.ai-writes-produce-proposals")


@dataclass(frozen=True, slots=True)
class _PayloadMarker:
    """A token whose occurrence count in an item's current body no earlier body shares.

    ``count`` is the whole point: see :data:`_RESEED_PAYLOAD_MARKERS` for the
    measurement that retired the membership form this replaces.
    """

    item_id: ItemId
    token: str
    count: int

    def __post_init__(self) -> None:
        """A marker that pins zero pins nothing -- it passes on a body that never had it."""
        if self.count < 1:
            raise ValueError(
                f"{self.item_id.value} pins {self.token!r} at {self.count}, but a marker "
                f"asserting an absence cannot distinguish a correct payload from a reverted "
                f"one: every body that never carried the token satisfies it. Pin a token the "
                f"current body actually carries, measured against every earlier state of its "
                f"source document."
            )


#: One entry per re-seeded item: a literal token its *current* body carries a
#: measured number of times, where no earlier state of that item's source
#: document carries it the same number of times -- pre-empted from the ADV-RC
#: MEDIUM-1 class the #440 round found (a reverted re-seed payload leaves the
#: whole suite green at the same test count, because a revision-id check alone
#: cannot tell a correction from a reverted one).
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
#: **A count, not a presence -- #315's own lesson, and the reason this
#: constant was renamed.** The #471 form asserted ``marker in body``. That
#: holds whenever the token is present *at all*, so it stops discriminating
#: the moment a later correction to the same document keeps the token while
#: changing everything around it. Measured 2026-09-02: this entry's
#: predecessor keyed ``architecture.single-writer-synchronous-in-m1`` on
#: ``#436``, which the #471 body (revision ``01M1C8VS6C7SXBWJNBV3W5QEP9``) and
#: #315's re-seed (revision ``01M1EVGVTNMDR2NQ9P49PARD1A``) both carry **5**
#: times -- the pin had silently become a two-point marker with both points on
#: the same side. ``#468``, on the same pair, counts **3** and **5**. So each
#: entry pins the number, and the assertion is equality, not membership.
#:
#: Wrap-safe tokens, deliberately: a Markdown line wrap breaks at whitespace,
#: so a token containing none cannot be split by one (the ``#414``/"owed, not
#: shipped" trap the ADR-0013 check above works around by checking two
#: substrings rather than one contiguous phrase). An issue reference is one
#: instance of that property rather than the only one, which matters because
#: several items carry no ``#NNN`` whose count is unique to their current
#: text; those are keyed on a backticked identifier or a measured figure the
#: same correction introduced. How many is not recorded here -- it moves with
#: every re-seed, and the entries below are the live answer.
#:
#: **The two twins that waited have landed.** ADR-0022
#: (``architecture.index-lives-in-its-own-database``) and ADR-0024
#: (``architecture.a-purge-is-a-build``) were each held back for a correction
#: their documents were due, and for different reasons: ADR-0022 was drifted,
#: reported by ``tools/corpus_drift.py`` on purpose, while ADR-0024 was
#: byte-identical to its source with nothing yet to re-seed. Treating those as
#: the same case was the error the paragraph here previously replaced. Both
#: corrections are now aboard -- ``d70dc219`` for ADR-0022, ``491bded6`` for
#: ADR-0024 -- so each re-seeds with one revision carrying both halves, which
#: is what waiting was for.
#:
#: **No clean negative twin for any entry, and the reason generalises.** A
#: ``not in`` pin needs a token present in the superseded body and absent from
#: current *and* from every intermediate -- and this corpus's ADR-amendment
#: convention quotes the retracted claim verbatim inside its own correction
#: note, so the retracted wording reappears in the corrected text and stops
#: discriminating. That convention applies to every ADR here, which is why no
#: entry carries a negative twin rather than only the three it was first
#: measured on. Those three, measured when the mechanism was #471's: "on the
#: state database" (0018's retracted lock claim) appears 0 times in the
#: superseded body and 1 time in the current one, quoted as part of the
#: correction; "an operator cannot yet move it" (0008's retracted config
#: claim) appears once in both; "reads either back today" (0024's retracted
#: claim) appears once in both. Every entry is pinned by its correction marker
#: alone, and the boundary rule below is what a negative twin was reaching for.
#:
#: **Instrument, named on both sides.** ``str.count`` -- *occurrences* --
#: because that is what :func:`test_the_committed_root_corpus_applies_cleanly_to_an_empty_store`
#: asserts with. ``grep -c`` counts matching *lines* and is a different
#: measurement; it happens to agree on every token below, and agreeing is not
#: being the same. The round-one version's own instrument error is why this
#: paragraph exists: plain ``grep -c "write.lock"`` treats the ``.`` as "any
#: character" and reports 8 for a body a literal count reports 3 for.
#:
#: **Every point, not three.** "Seed / intermediate / current" names the
#: shape; each token was measured against *every* commit that touched its
#: item's source document, which is a superset of it, and qualified only when
#: its count at the current point differed from its count at every one of
#: them. Each token is additionally drawn from the **added** lines of the last
#: such commit, so it names the correction whose absence a reverted payload
#: would show rather than a word that happens to be new.
#:
#: **The per-item selection matrix is not narrated here.** A row per entry of
#: item/token/count/points, hand-carried in a docstring, is the #458
#: stale-count shape exactly: a block of numbers no rule recomputes, going
#: stale on the first re-seed nobody re-narrated. The rows are archived in PR
#: #490's round-two record. What is *live* is below and in the test: the counts themselves, and
#: the boundary rule that recomputes each token's count in the revision its
#: item's latest re-seed replaced and fails when it equals the pin.
#:
#: **``raptor-forest`` re-keyed for the same reason, one commit later.** It
#: shipped ``#426`` at 2, and #487 (``f706329``) moved the document again
#: without touching that count: measured 2026-09-02, ``#426`` is 2 in both
#: the #471 body (``3749581``) and the #490 re-seed of it (``f706329``). A
#: presence check and a count keyed on ``#426`` are equally blind there, so
#: the entry moved to ``#464`` -- the issue #487's own correction cites -- at
#: 1 against 0 everywhere before. The general rule this makes concrete:
#: **a re-seed re-measures its item's marker**, because the token that
#: discriminated the previous revision need not discriminate the next one.
#: Both of those keys have since been re-measured in turn, which is that rule
#: applied rather than an exception to it; the entries below are what each
#: item is keyed on now.
_RESEED_PAYLOAD_MARKERS: Final[tuple[_PayloadMarker, ...]] = (
    _PayloadMarker(ItemId("architecture.monorepo-with-independent-artifacts"), "`Core`", 1),
    _PayloadMarker(ItemId("architecture.sqlite-is-a-derived-artifact"), "`FINDINGS_STORE_ID`", 1),
    _PayloadMarker(ItemId("architecture.yaml-knowledge-migrations"), "#245", 1),
    _PayloadMarker(ItemId("architecture.dependency-pinning-and-pre-1-0-isolation"), "`3.13`", 1),
    _PayloadMarker(ItemId("architecture.dco-over-cla"), "30/30", 1),
    _PayloadMarker(ItemId("architecture.state-hash-covers-the-working-tree"), "`contentSha256`", 1),
    _PayloadMarker(ItemId("architecture.sqlite-schema-versioning"), "#117", 3),
    _PayloadMarker(ItemId("architecture.single-writer-synchronous-in-m1"), "#468", 5),
    _PayloadMarker(ItemId("architecture.rank-fusion-over-score-normalisation"), "T-17a's", 1),
    _PayloadMarker(ItemId("architecture.raptor-forest"), "#464", 1),
    _PayloadMarker(ItemId("architecture.trigram-index-beside-the-word-index"), "#464", 2),
    _PayloadMarker(ItemId("architecture.a-purge-is-a-build"), "#426", 1),
    _PayloadMarker(ItemId("architecture.ports-and-adapters"), "#553", 1),
)

#: Frozen rather than ``datetime.now()``: a project row's ``registered_at`` is
#: metadata this test never reads back, but ``Project.__post_init__`` still
#: requires it timezone-aware, and a wall-clock value would make a rerun's
#: database differ from this run's for a field nothing here compares.
_REGISTERED_AT: Final = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


def _tracked_corpus_paths() -> tuple[str, ...]:
    """Every path git tracks under ``.theurian/``, or a skip when git cannot say.

    One refusal remains, and only one: no git, or git cannot answer for this
    checkout (a ``tools/mutate.py`` copy, which carries no ``.git`` on
    purpose -- see the module docstring for why this test does not fall back to
    the copy's manifest the way ``test_dogfood_corpus_governance.py``'s
    ``_tracked()`` does). Without git there is no tracked set to speak of, so
    there is nothing to filter *to*.

    **What used to be a second refusal is now a filter** (#490 round two).
    :func:`load_migrations` reads every ``.yaml`` physically present with no
    regard for tracking, and the old answer to that was to skip the whole test
    whenever an untracked one sat in the directory. That is dormancy exactly
    where it hurts most: the maintainer's own dogfooding machine keeps
    machine-local vault notes under ``.theurian/`` (fenced in
    ``.git/info/exclude``), so every assertion in this file -- the twelve
    payload markers included -- was disabled on the one tree where re-seeds are
    authored and where a mis-measured marker would first be committed. Copying
    the tracked set out and loading *that* serves the original concern strictly
    better: an untracked draft is not skipped around, it is never loaded.
    """
    git = shutil.which("git")
    if git is None:
        pytest.skip(
            f"no git on this machine: nothing here can name the tracked subset of "
            f"{REPO_ROOT / '.theurian'}, so this test declines to load it and report a "
            f"false answer"
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
            ".theurian",
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

    paths = tuple(
        entry for entry in completed.stdout.decode("utf-8", "surrogateescape").split("\0") if entry
    )
    assert paths, (
        f"git tracks nothing under {REPO_ROOT / '.theurian'}. The committed corpus is gone -- "
        f"which is a finding, not a reason for this test to skip and read as green."
    )
    return paths


def _committed_corpus(destination: Path) -> Path:
    """A tree holding exactly the tracked ``.theurian/`` paths, and nothing else.

    The population is ``git ls-files``, so what gets applied below is the
    committed corpus by construction rather than by a precondition someone has
    to keep true. Copies rather than symlinks: the loader refuses irregular
    entries under its own root, and a symlinked tree would be testing that
    refusal instead of the corpus.

    ``.theurian/knowledge/`` comes along because a migration's ``contentFile``
    is relative to the migrations directory (``../knowledge/...``) and the
    loader resolves it inside the root it was handed; the tracked bodies are
    exactly the ones the tracked migrations name, which
    ``test_dogfood_corpus_governance.py`` holds separately in both directions.
    """
    for relative in _tracked_corpus_paths():
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / relative, target)
    return destination


def _revision_chain(loaded: LoadedMigrations) -> dict[ItemId, tuple[RevisionId, ...]]:
    """Every item's revisions, in the loader's own application order.

    The same fold :func:`current_revision_in` performs, kept whole instead of
    collapsed to its last element: the marker rules below need the *previous*
    revision as well as the current one, and re-deriving the order from file
    names would be the wrong key -- a ``MigrationSet`` iterates in Kahn order
    over the migration documents' own ids, which a filename sort does not
    reproduce (the fold-key finding the #440 round caught).

    Cross-checked against ``current_revision_in`` at the call site, so the two
    orderings cannot silently diverge.
    """
    chain: dict[ItemId, tuple[RevisionId, ...]] = {}
    for migration in loaded.migration_set:
        for operation in migration.operations:
            if isinstance(operation, UpsertRevision):
                chain[operation.item_id] = (
                    *chain.get(operation.item_id, ()),
                    operation.revision_id,
                )
    return chain


def _bodies_by_revision(database: Path, project_id: ProjectId) -> dict[str, str]:
    """Every applied revision's body, in one connection and one query.

    One read for the whole test rather than the two-hop read once per marker:
    twelve markers each needing a current *and* a superseded body was twenty-four
    connection open/close pairs for data that does not change between them.
    Keyed by revision id, because that is what :func:`_revision_chain` hands
    back; the current-revision pointer is cross-checked separately, so nothing
    here has to re-read ``knowledge_items``.

    Superseded revisions are in this table too -- ``upsertRevision`` inserts a
    row per revision and only moves the pointer -- which is what makes the
    boundary rule below readable from the store rather than from the tree.
    """
    with closing(open_read_connection(database)) as connection:
        return {
            str(row["revision_id"]): str(row["body"])
            for row in connection.execute(
                "SELECT revision_id, body FROM knowledge_revisions WHERE project_id = ?",
                (project_id.value,),
            )
        }


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

    A fourth family, one entry per re-seeded item
    (:data:`_RESEED_PAYLOAD_MARKERS`): the same content-shaped pin, carried by
    every re-seed rather than only by the one a round happened to reproduce.
    Each turns its own item's re-seed commit RED when that commit's payload is
    reverted, and nothing else's -- see the entries' docstring for the
    per-point counts behind each token, for why an equality on the count and
    not a membership test is what ships, and for the two earlier versions of
    this pin (a two-point general-word marker, then a presence-only one) that
    do not.
    """
    corpus_root = _committed_corpus(tmp_path / "corpus")
    paths = ProjectPaths.of(corpus_root)
    loaded = load_migrations(paths.root, paths.migrations, schema_root())

    project = Project(
        project_id=ProjectId("root-corpus-applicability-test"),
        root_path=str(corpus_root),
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

    # The same class, once per re-seeded item -- see
    # :data:`_RESEED_PAYLOAD_MARKERS`'s docstring for how each token was
    # measured, and the module docstring for the one revert depth these
    # assertions are the sole catcher of.
    chain = _revision_chain(loaded)
    bodies = _bodies_by_revision(database, project.project_id)

    # _revision_chain re-folds what current_revision_in folds, so the two are
    # held to the same answer rather than trusted to agree -- the fold key is
    # exactly what the #440 round caught being wrong once already.
    disagreements = {
        item.value: (revisions[-1].value, current_revision_in(loaded.migration_set, item))
        for item, revisions in chain.items()
        if current_revision_in(loaded.migration_set, item) != revisions[-1]
    }
    assert not disagreements, (
        f"_revision_chain's last revision and current_revision_in disagree for "
        f"{disagreements}. They fold the same operations in the same order, so a "
        f"disagreement means this test's own reconstruction has drifted from the production "
        f"helper and every marker below is reading the wrong body."
    )

    # The census. Without it, deleting an entry from the tuple is invisible --
    # measured: mutation m4, dropping the last _PayloadMarker, left the whole
    # suite green. It is also what makes the *next* re-seed's missing marker
    # RED: a re-seed gives its item a second revision, so the item joins this
    # set the moment the migration lands, whether or not anyone adds a pin.
    multi_revision = {item for item, revisions in chain.items() if len(revisions) > 1}
    pinned = tuple(marker.item_id for marker in _RESEED_PAYLOAD_MARKERS)
    assert len(set(pinned)) == len(pinned), (
        f"_RESEED_PAYLOAD_MARKERS names an item twice: "
        f"{sorted({item.value for item in pinned if pinned.count(item) > 1})}. Two entries "
        f"for one item let one of them be wrong while the other passes."
    )
    accounted = set(pinned) | {_RESEEDED_ITEM}
    assert accounted == multi_revision, (
        f"the pinned set and the multi-revision set disagree. Pinned but not re-seeded: "
        f"{sorted(item.value for item in accounted - multi_revision)}; "
        f"re-seeded but unpinned: "
        f"{sorted(item.value for item in multi_revision - accounted)}. "
        f"Every item with more than one revision has had a payload replaced, so every one of "
        f"them needs a content pin -- add a _PayloadMarker measured against every point in "
        f"its source document's history. {_RESEEDED_ITEM.value} is the exception because the "
        f"#414 assertions above pin it by hand."
    )

    checked: list[ItemId] = []
    for marker in _RESEED_PAYLOAD_MARKERS:
        checked.append(marker.item_id)
        revisions = chain[marker.item_id]
        applied_body = bodies[revisions[-1].value]
        assert applied_body.count(marker.token) == marker.count, (
            f"the applied body for {marker.item_id.value} carries {marker.token!r} "
            f"{applied_body.count(marker.token)} time(s); this item's re-seed pinned it at "
            f"{marker.count}. A three-file coordinated revert of this item's payload -- body, "
            f"contentSha256 and both anchors -- would leave this assertion the only one in "
            f"the suite to notice, per the ADV-RC MEDIUM-1 class the #440 round found. If the "
            f"source document legitimately moved, re-measure the token against every point "
            f"in its history and update the count here in the same change."
        )

        # The boundary. A marker discriminates only while its count *differs*
        # across the revision it replaced, and that property decays silently:
        # `#436` on single-writer and `#426` on raptor-forest each stopped
        # discriminating when a later correction kept the token's count, and
        # both were caught by hand rather than by the suite. Reading the
        # superseded body live means the next re-seed that lands without
        # re-measuring goes RED here instead of shipping a dead pin.
        superseded_body = bodies[revisions[-2].value]
        assert superseded_body.count(marker.token) != marker.count, (
            f"{marker.item_id.value}'s marker {marker.token!r} no longer discriminates: it "
            f"counts {marker.count} in the current body and {superseded_body.count(marker.token)} "
            f"in revision {revisions[-2].value}, the one this item's latest re-seed replaced. "
            f"A revert to that body would satisfy the pin above, so the pin catches nothing. "
            f"Re-measure a token whose count at the current point differs from its count at "
            f"every earlier point of {marker.item_id.value}'s source document, and update both "
            f"the entry and its recorded measurement."
        )

    # The census proves the *list* is complete; this proves the list was
    # *walked*. Measured: replacing the loop's iterable with
    # `_RESEED_PAYLOAD_MARKERS[:0]` (mutation m7) left every assertion above
    # unexecuted and the suite green -- the census passed because it reads the
    # constant directly, not the loop. Comparing what the loop actually visited
    # against the constant is what closes that.
    assert tuple(checked) == pinned, (
        f"the marker loop visited {[item.value for item in checked]}, but "
        f"_RESEED_PAYLOAD_MARKERS names {[item.value for item in pinned]}. Every entry has to "
        f"be reached: an iterable that skips entries leaves their pins unasserted while every "
        f"other rule in this test still passes."
    )


@pytest.mark.parametrize("count", [0, -1])
def test_a_payload_marker_refuses_a_count_that_asserts_an_absence(count: int) -> None:
    """:class:`_PayloadMarker`'s own guard, driven rather than merely written.

    A guard no input reaches survives its own deletion. Measured: weakening
    ``count < 1`` to ``count < 0`` (mutation m5) left the whole suite green, and
    so did that weakening *plus* an entry pinning a token no body carries at
    zero (m6) -- the vacuous pin the guard exists to refuse, shipped green.

    Zero is the case that matters and negative is the case that proves the
    boundary is the right one: a marker pinning an absence is satisfied by every
    body that never carried the token, including the superseded one, so it
    cannot distinguish a correct payload from a reverted one -- which is the
    entire job.
    """
    with pytest.raises(ValueError, match="cannot distinguish a correct payload"):
        _PayloadMarker(ItemId("architecture.any-item"), "#404", count)
