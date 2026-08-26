"""``migrations-valid`` counted files; it never opened one (#91).

The step reported ``satisfied`` -- "N migration(s) found. Run `theurian migrate
validate` to check them." -- for any directory at all, because its whole check
was ``len(list(paths.migrations.glob("*.yaml")))``. A directory holding one file
of nonsense read as converged, and `doctor` exits 0 while every ``theurian
migrate`` against that project refuses.

Three things were wrong at once, and only the first is about wording:

1. **The status was a lie by name.** ``satisfied`` is the verdict nobody
   re-reads, and this one was reached without the file being opened.
2. **The count was nobody's count.** ``glob("*.yaml")`` is not the loader's
   enumeration -- ``.yml`` is not a migration to the loader, and a symlinked or
   unreadable entry is a refusal rather than a file -- so `doctor` and `migrate
   validate` could report different numbers for the same directory. A ``count =
   0`` mutation survived the whole suite.
3. **Nothing was checked, so nothing could be withheld.** Once the probe reads
   an author's file, a refusal quotes it, and a refusal's message is not
   Theurian's to publish (O-3, SEC-6).

The fix is not for the probe to grow a loader: the application layer does not
import the infrastructure one (ADR-0003). ``SetupContext.check_migrations`` is
injected by the composition root, and these tests inject
:func:`setup_migrations.checked_by_the_loader`, which makes the same call
``cli/context.resolve_context`` makes.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest
from fakes.setup import FakeMcpConfig, FakeService
from setup_migrations import checked_by_the_loader

from theurian.application.project_service import ProjectPaths
from theurian.application.setup_context import MigrationsCheck, SetupContext
from theurian.application.setup_steps import probe_migrations
from theurian.cli.context import schema_root
from theurian.domain.setup import StepStatus
from theurian.infrastructure.claude.mcp_config import ConnectionSpec
from theurian.infrastructure.filesystem.migration_loader import load_migrations
from theurian.infrastructure.secrets.file_store import FileSecretStore

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[4]

#: The bundled example, which `tests/unit/test_examples.py` already holds to
#: loading through the product's own call. Copied rather than probed in place,
#: so nothing here can write into the repository's own tree.
SAMPLE_PROJECT = REPO_ROOT / "examples" / "sample-project"

#: The offline CI job runs as root, where `chmod 0o000` denies nothing and a
#: deny-mode test measures the opposite of what it says.
_CANNOT_BE_REFUSED_BY_A_MODE = sys.platform == "win32" or os.geteuid() == 0

#: Distinctive enough that no assertion passes on a coincidental substring. It
#: goes in *key* position of an unparseable document, because that is the shape
#: whose refusal quotes the offending line back -- a schema failure names the
#: absent properties and echoes nothing, so a seed there would prove nothing
#: about withholding.
SENTINEL = "SentinelMigrationKeyWWWW"


def _context(tmp_path: Path, root: Path | None, **overrides: object) -> SetupContext:
    data_dir = tmp_path / "home" / ".theurian"
    defaults: dict[str, object] = {
        "home": tmp_path / "home",
        "data_dir": data_dir,
        "port": 7419,
        "project_root": root,
        "connection": ConnectionSpec(port=7419),
        "mcp_config": FakeMcpConfig(),
        "secrets": FileSecretStore(data_dir),
        "health": lambda: None,
        "service": FakeService(),
        "executable": "",
        "check_migrations": checked_by_the_loader,
    }
    return SetupContext(**{**defaults, **overrides})  # type: ignore[arg-type]


def _project(tmp_path: Path, name: str = "repo") -> Path:
    root = tmp_path / name
    (root / ".theurian" / "migrations").mkdir(parents=True)
    return root


def _refused(root: Path) -> Path:
    """A migrations directory holding one file that does not parse."""
    migrations = ProjectPaths.of(root).migrations
    (migrations / "0001-broken.yaml").write_text(f"a: {SENTINEL}: c\n", encoding="utf-8")
    return migrations


# -- A set that does not load ------------------------------------------------


def test_migrations_that_do_not_parse_are_not_reported_as_valid(tmp_path: Path) -> None:
    """The measured face: one unparseable file, reported ``satisfied`` (#91).

    ``migrate validate`` against this directory exits non-zero, and `doctor`
    said the step was converged. The two commands now consult the same loader,
    so they cannot disagree about the same files.

    The step names no ``paths``. It writes nothing -- migrations are Git-tracked
    authored content and setup has no business editing them -- and a path in
    that field is read as "setup would touch this" by the plan and by
    ``changedPaths``. A mutation adding the directory there survived the whole
    suite once already.
    """
    root = _project(tmp_path)
    migrations = _refused(root)

    step = probe_migrations(_context(tmp_path, root))

    assert step.status is StepStatus.MISSING
    assert step.summary == f"The migrations in {migrations} do not validate."
    assert step.action == (
        "Fix the file it names; `theurian migrate validate` prints the full refusal."
    )
    assert step.paths == ()


def test_the_refusal_reaches_the_operators_own_terminal(tmp_path: Path) -> None:
    """The positive control for the withholding test below.

    Without it, "the sentinel is absent from a published report" would pass for
    a detail that never carried anything -- including one that simply says
    nothing at all. The person who has to fix the file is reading their own
    screen, and that is where the loader's message belongs in full.
    """
    root = _project(tmp_path)
    _refused(root)

    step = probe_migrations(_context(tmp_path, root))

    assert step.detail.startswith("MigrationError: ")
    assert SENTINEL in step.detail, "the loader's own message quotes the offending line"


def test_a_refusal_is_withheld_from_a_report_meant_to_be_shared(tmp_path: Path) -> None:
    """O-3/SEC-6: an exception carries whatever raised it, and this one carries YAML.

    ``doctor --report`` exists to be pasted into a public issue. The message
    here is a parser error quoting a line of somebody's migration file, and
    nothing bounds what that line holds -- a URL with a credential in it is a
    perfectly ordinary thing to find in a `description:`. The type is what a
    reader of the issue acts on; the message stays on the terminal.
    """
    root = _project(tmp_path)
    _refused(root)

    step = probe_migrations(_context(tmp_path, root, for_publication=True))

    assert SENTINEL not in step.detail
    assert step.detail.startswith("MigrationError.")
    assert "withheld" in step.detail


# -- A set that loads --------------------------------------------------------


def test_a_valid_set_is_reported_with_the_number_of_migrations_the_loader_read(
    tmp_path: Path,
) -> None:
    """The count is the loader's, and the sentence says what was actually done.

    The bundled example is used because it is a set the product itself loads --
    two migrations, a body file each, digests pinned. The count is asserted
    against a *second, independent* load in the test rather than against a
    literal, so a probe that published ``0``, or ``len(...) + 1``, or the
    ``glob`` count it used to use, fails here whatever the fixture holds.

    The fixture guard is not optional: ``load_migrations`` answers a directory
    it cannot find with an empty set rather than raising, so a copy that landed
    in the wrong place would leave this asserting "0 migration(s)" against a
    probe that also said 0.
    """
    root = tmp_path / "repo"
    shutil.copytree(SAMPLE_PROJECT, root)
    paths = ProjectPaths.of(root)
    loaded = load_migrations(paths.root, paths.migrations, schema_root())
    assert len(loaded.migration_set) >= 2, (
        "the fixture has to hold more than one migration, or a probe publishing "
        "a constant 1 passes this"
    )

    step = probe_migrations(_context(tmp_path, root))

    assert step.status is StepStatus.SATISFIED
    assert step.summary == f"{len(loaded.migration_set)} migration(s) parse and validate."
    assert step.action == ""


def test_the_published_count_is_the_checkers_and_not_one_the_probe_recomputes(
    tmp_path: Path,
) -> None:
    """A directory of one file, a checker reporting seven: the step must say seven.

    This is what stops the probe quietly keeping its own enumeration beside the
    injected one. ``glob("*.yaml")`` here answers 1, and the two numbers can
    only differ if the probe is still counting for itself.
    """
    root = _project(tmp_path)
    (ProjectPaths.of(root).migrations / "0001-anything.yaml").write_text("{}\n", encoding="utf-8")

    step = probe_migrations(
        _context(tmp_path, root, check_migrations=lambda _: MigrationsCheck(count=7, failure=None))
    )

    assert step.status is StepStatus.SATISFIED
    assert step.summary == "7 migration(s) parse and validate."


def test_a_directory_holding_only_yml_files_reports_the_loaders_own_enumeration(
    tmp_path: Path,
) -> None:
    """``.yml`` is not a migration -- and that is the loader's contract, not this step's.

    ``load_migrations`` enumerates ``*.yaml`` only, so a directory of ``.yml``
    files loads zero migrations and every ``theurian migrate`` ignores them. The
    old probe globbed the same pattern and reached the same number by
    coincidence; the point of the arm is that the number now comes from the one
    place that decides it, so the two commands cannot drift apart.
    """
    root = _project(tmp_path)
    (ProjectPaths.of(root).migrations / "0001-ignored.yml").write_text(
        "apiVersion: theurian.dev/v1\n", encoding="utf-8"
    )

    step = probe_migrations(_context(tmp_path, root))

    assert step.status is StepStatus.SATISFIED
    assert step.summary == "0 migration(s) parse and validate."


# -- The one arm that reads nothing ------------------------------------------


def _must_not_be_called(root: Path) -> MigrationsCheck:
    raise AssertionError(f"the probe loaded migrations for {root}, which it has no reason to do")


def test_outside_a_git_repository_nothing_is_loaded(tmp_path: Path) -> None:
    """No project, no migrations directory to name, and nothing to open."""
    step = probe_migrations(_context(tmp_path, None, check_migrations=_must_not_be_called))

    assert step.status is StepStatus.NOT_APPLICABLE
    assert step.summary == "Not inside a Git repository."


def test_a_project_with_no_migrations_directory_is_asked_of_the_checker_too(
    tmp_path: Path,
) -> None:
    """The state right after `theurian init` in a repository that has none.

    The checker *is* called here, and that is the change: an ``is_dir()`` ahead
    of it was a second discovery predicate the loader does not share, and the
    three tests below measure where the two disagreed. The wording still splits
    -- "no migrations directory" and "a migrations directory that loads zero"
    are different things to be told -- but the split is now made *after* the
    loader has had its say, so it can only choose between two green sentences.

    The checker asserts it was reached rather than being the stub, or this test
    would pass for a probe that had kept the gate.
    """
    root = tmp_path / "repo"
    (root / ".theurian").mkdir(parents=True)
    asked: list[Path] = []

    def _recording(checked: Path) -> MigrationsCheck:
        asked.append(checked)
        return MigrationsCheck(count=0, failure=None)

    step = probe_migrations(_context(tmp_path, root, check_migrations=_recording))

    assert asked == [root], "the loader, not a second predicate, decides the verdict"
    assert step.status is StepStatus.NOT_APPLICABLE
    assert step.summary == "No migrations directory yet."


# -- Where the pre-gate and the loader disagreed ------------------------------


def test_a_dangling_migrations_symlink_is_a_refusal_and_not_an_absent_directory(
    tmp_path: Path,
) -> None:
    """Measured: ``not-applicable`` from `doctor`, exit 4 from `migrate validate`.

    ``is_dir()`` follows the link and answers False for a dangling one, so the
    probe reported "No migrations directory yet." for a tree the loader refuses
    with ``MigrationsDirectoryUnreadableError``. That is #91's divergence in the
    one shape #91's own fix reintroduced: `doctor` exits 0 for a project every
    ``theurian migrate`` stops on.
    """
    root = tmp_path / "repo"
    (root / ".theurian").mkdir(parents=True)
    ProjectPaths.of(root).migrations.symlink_to(tmp_path / "nowhere")

    step = probe_migrations(_context(tmp_path, root))

    assert step.status is StepStatus.MISSING
    assert step.summary == f"The migrations in {ProjectPaths.of(root).migrations} do not validate."
    assert step.detail.startswith("MigrationsDirectoryUnreadableError: ")


def test_a_migrations_symlink_loop_is_a_refusal_and_not_an_absent_directory(
    tmp_path: Path,
) -> None:
    """The same split through ``ELOOP`` rather than ``ENOENT``.

    A separate test rather than a parameter of the one above, because the two
    reach ``is_dir()``'s False through different errnos -- both of which pathlib
    swallows -- and a fix that special-cased only the dangling link would still
    call this converged.
    """
    root = tmp_path / "repo"
    (root / ".theurian").mkdir(parents=True)
    migrations = ProjectPaths.of(root).migrations
    migrations.symlink_to(migrations)

    step = probe_migrations(_context(tmp_path, root))

    assert step.status is StepStatus.MISSING
    assert step.summary == f"The migrations in {migrations} do not validate."


@pytest.mark.skipif(_CANNOT_BE_REFUSED_BY_A_MODE, reason="chmod denies nothing to root")
def test_a_theurian_directory_that_denies_traversal_is_a_refusal_the_step_reports(
    tmp_path: Path,
) -> None:
    """The third split, and the one the gate did not merely get wrong but *escaped*.

    ``is_dir()`` raises ``PermissionError`` on ``EACCES`` -- pathlib ignores
    ``ENOENT``, ``ENOTDIR``, ``EBADF`` and ``ELOOP``, and not this -- so the
    exception was raised *before* the checker ran, outside the
    ``_MIGRATION_REFUSALS`` net the composition root wraps the load in. It
    reached the reader through ``SetupService._probe`` as CONFLICTING "Could not
    check migrations-valid", which stops setup to ask for consent, on a tree
    ``migrate validate`` simply refuses. Asking the checker first turns it back
    into the verdict it is.
    """
    root = tmp_path / "repo"
    (root / ".theurian" / "migrations").mkdir(parents=True)
    (root / ".theurian").chmod(0o000)
    try:
        step = probe_migrations(_context(tmp_path, root))
    finally:
        (root / ".theurian").chmod(0o700)

    assert step.status is StepStatus.MISSING
    assert step.summary == f"The migrations in {ProjectPaths.of(root).migrations} do not validate."
    assert step.detail.startswith("MigrationsDirectoryUnreadableError: ")
