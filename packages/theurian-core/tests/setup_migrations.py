"""What a test hands `SetupContext.check_migrations` (#91).

The field is injected rather than imported by the probe: validating a migration
set means loading YAML off disk against the published JSON Schemas, and the
application layer does not reach for the infrastructure loader (ADR-0003). The
composition root in ``cli/setup_commands.py`` supplies the real one; a test
supplies one of these two, and **which one it picks decides what the test is
measuring**.

- :func:`unchecked_migrations` reports a healthy set without reading anything.
  For contexts whose subject is some *other* step. A test about
  ``migrations-valid`` that used it would be green whatever the probe did --
  that is the fixture-never-reaches-the-branch failure, and it is worth naming
  because the stub is the convenient one.
- :func:`checked_by_the_loader` is the real thing, and reads the real files.

The field is deliberately non-defaulted on :class:`SetupContext`: a probe that
silently fell back to "no migrations to check" would report ``satisfied`` on
every machine whose composition root forgot to wire it, which is the defect #91
is about wearing a different hat.
"""

from __future__ import annotations

from pathlib import Path

from theurian.application.project_service import ProjectPaths
from theurian.application.setup_context import MigrationsCheck
from theurian.cli.context import schema_root
from theurian.infrastructure.filesystem.migration_loader import load_migrations


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
    ``refuse_unenforceable_scope`` and the two collision guards in
    ``cli/commands.py`` over the loaded set, and this helper does not. A
    document those refuse but the loader accepts is therefore *not* covered by
    the tests that use this -- see the report accompanying these tests.
    """
    paths = ProjectPaths.of(root)
    try:
        loaded = load_migrations(paths.root, paths.migrations, schema_root())
    except Exception as exc:  # what the probe has to report rather than raise
        return MigrationsCheck(count=0, failure=exc)
    return MigrationsCheck(count=len(loaded.migration_set), failure=None)
