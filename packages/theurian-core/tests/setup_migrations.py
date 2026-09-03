"""What a test hands the two `SetupContext` fields that read a migration set.

Both are injected rather than imported by the probes: reading a migration set
means loading YAML off disk against the published JSON Schemas, and the
application layer does not reach for the infrastructure loader (ADR-0003). The
composition root in ``cli/setup_commands.py`` supplies the real ones; a test
supplies one of these, and **which one it picks decides what the test is
measuring**.

``check_migrations`` -- does this set validate? (#91)

- :func:`unchecked_migrations` reports a healthy set without reading anything.
  For contexts whose subject is some *other* step. A test about
  ``migrations-valid`` that used it would be green whatever the probe did --
  that is the fixture-never-reaches-the-branch failure, and it is worth naming
  because the stub is the convenient one.
- :func:`checked_by_the_loader` is the real thing, and reads the real files.

``current_state_hash`` -- which state is this project *at*? (#451)

- :func:`state_hash_from_the_loader` is the real thing, and the only one here:
  a stub answering a fabricated hash makes every context report "no knowledge
  state built yet" whatever is on disk, which is the branch ``initial-index``
  was stuck on for two milestones and the reason #451 was invisible.

Both fields are deliberately non-defaulted on :class:`SetupContext`: a probe
that silently fell back to "no migrations to check" would report ``satisfied``
on every machine whose composition root forgot to wire it, which is the defect
#91 is about wearing a different hat, and one that fell back to "no state hash"
would publish "cannot tell" on every one of them.
"""

from __future__ import annotations

from pathlib import Path

from theurian.application.project_service import ProjectPaths, resolve_state_hash
from theurian.application.setup_context import MigrationsCheck
from theurian.cli.context import schema_root
from theurian.domain.errors import TheurianError
from theurian.domain.state import StateHash
from theurian.infrastructure.filesystem.migration_loader import load_migrations
from theurian.infrastructure.sqlite.schema import SCHEMA_VERSION


def unchecked_migrations(_root: Path) -> MigrationsCheck:
    """A checker that looks at nothing and reports nothing wrong.

    ``count=0`` rather than a plausible number, so a test that accidentally
    depends on this one says something visibly odd -- "0 migration(s) parse and
    validate" beside a directory holding three -- instead of reading as a
    measurement.
    """
    return MigrationsCheck(count=0, failure=None)


def checked_by_the_loader(root: Path) -> MigrationsCheck:
    """Load the project's migrations the way every ``theurian migrate`` does.

    ``load_migrations(paths.root, paths.migrations, schema_root())`` is the call
    ``cli/context.resolve_context`` makes, so what this accepts is what the
    product accepts: parsing, schema conformance, ``contentFile`` containment,
    the ``contentSha256`` pin against the bytes on disk, and the application
    order.

    **It is the load half only.** ``theurian migrate validate`` also runs
    ``run_static_migration_guards`` over the loaded set, and this helper does
    not. A document those guards refuse but the loader accepts is therefore
    *not* covered by the tests that use this -- see the report accompanying
    these tests.

    **The refusal net is production's ``TheurianError``, not a wider or narrower
    restatement.** This caught ``Exception``, which is wider than the set the real
    ``_check_migrations`` catches -- so a family that escapes production and
    reaches the operator as CONFLICTING "Could not check migrations-valid" came
    back through the double as an ordinary MISSING verdict, and no test using this
    could see the difference. That is #91's divergence living inside the fixture
    built to measure it. It tracks ``_check_migrations``' own catch clause, which
    is ``TheurianError`` -- exactly the set `migrate validate` refuses on -- so the
    double neither swallows a real bug nor misses a verdict.
    """
    paths = ProjectPaths.of(root)
    try:
        loaded = load_migrations(paths.root, paths.migrations, schema_root())
    except TheurianError as exc:  # what the probe has to report rather than raise
        return MigrationsCheck(count=0, failure=exc)
    return MigrationsCheck(count=len(loaded.migration_set), failure=None)


def state_hash_from_the_loader(root: Path) -> StateHash | None:
    """Resolve the state on disk exactly as ``cli/setup_commands`` does.

    A copy of ``_current_state_hash`` rather than a call into it, for the reason
    :func:`checked_by_the_loader` is a copy of ``_check_migrations``: what a test
    hands the context has to be a double a test can vary, and importing the
    composition root's private helper into every fixture would make the wiring
    untestable from the outside. ``tests/integration/test_setup_migrations_checker.py``
    exercises the real one directly, which is the other half of that split.

    ``None`` for a set that does not load, which is what ``_converged_repository``
    in ``test_setup_service.py`` reaches: its ``0001-initial.yaml`` is an empty
    file, and an empty document is not a mapping. That is a fixture and also the
    tripwire -- ``initial-index`` must publish an answer there rather than raise.
    """
    paths = ProjectPaths.of(root)
    try:
        loaded = load_migrations(paths.root, paths.migrations, schema_root())
    except TheurianError:
        return None
    return resolve_state_hash(loaded, SCHEMA_VERSION)
