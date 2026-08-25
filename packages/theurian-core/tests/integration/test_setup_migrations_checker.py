"""The composition root's half of `migrations-valid` (#91).

``tests/integration/test_probe_migrations_validate.py`` holds the probe to the
verdict it publishes, and injects a checker to do it. That leaves one thing
unmeasured, and it is the half that decides whether `doctor` and `theurian
migrate validate` can disagree: the checker
``cli/setup_commands.build_context`` actually wires in.

``migrate validate`` is a load **plus three whole-set guards** --
``refuse_unenforceable_scope`` (issue #63), ``refuse_duplicate_content_files``
(issue #210) and ``refuse_alias_item_id_collision`` (T-21). A wrapper that
stopped at the load would report ``satisfied`` for every set those three refuse,
which is the same defect #91 is about with a smaller blast radius, and no
injection-based test can see it: the injected checker *is* the thing that would
be wrong.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from theurian.application.project_service import ProjectPaths
from theurian.cli.context import schema_root
from theurian.cli.setup_commands import _check_migrations
from theurian.domain.errors import UnenforceableScopeError
from theurian.infrastructure.filesystem.migration_loader import load_migrations

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[4]

#: A set the product itself loads: two migrations, a body file each, digests
#: pinned. Copied rather than used in place, so nothing here writes into the
#: repository's own tree.
SAMPLE_PROJECT = REPO_ROOT / "examples" / "sample-project"

#: A tenant `refuse_unenforceable_scope` refuses. The field is schema-valid --
#: any non-empty string up to 128 characters -- so a set carrying it *loads*, and
#: only the guard stops it. That is what makes it the right shape for the test
#: below: a load-only wrapper would call this set healthy.
_UNENFORCED_TENANT = "  tenantId: acme-holdings\n"


def _sample(tmp_path: Path, name: str = "repo") -> Path:
    root = tmp_path / name
    shutil.copytree(SAMPLE_PROJECT, root)
    return root


def _loaded_count(root: Path) -> int:
    paths = ProjectPaths.of(root)
    return len(load_migrations(paths.root, paths.migrations, schema_root()).migration_set)


def test_the_count_is_the_one_the_loader_reads(tmp_path: Path) -> None:
    """Against a second, independent load rather than against a literal.

    A wrapper publishing ``0``, ``len(...) + 1``, or the ``glob("*.yaml")`` count
    the probe used to take fails here whatever the fixture happens to hold. The
    guard on the fixture is not decoration: ``load_migrations`` answers a
    directory it cannot find with an empty set rather than raising, so a copy
    that landed in the wrong place would leave this asserting 0 == 0.
    """
    root = _sample(tmp_path)
    expected = _loaded_count(root)
    assert expected >= 2, "the fixture has to hold more than one migration"

    check = _check_migrations(root)

    assert check.failure is None
    assert check.count == expected


def test_a_set_the_loader_accepts_and_a_guard_refuses_comes_back_as_a_failure(
    tmp_path: Path,
) -> None:
    """The load is not the whole of `migrate validate`, and this is the difference.

    The edit adds a ``tenantId`` the schema accepts and
    ``refuse_unenforceable_scope`` does not, so the set reaches the guard: the
    assertion below proves that by loading it separately and finding no refusal
    there. Without that half, a wrapper whose *loader* had started rejecting the
    file for some unrelated reason would pass this test while the guards went
    unrun.
    """
    root = _sample(tmp_path)
    migration = next(iter(sorted(ProjectPaths.of(root).migrations.glob("*.yaml"))))
    migration.write_text(
        migration.read_text(encoding="utf-8").replace(
            "      sensitivity: internal\n",
            f"      sensitivity: internal\n    {_UNENFORCED_TENANT}",
            1,
        ),
        encoding="utf-8",
    )
    assert _loaded_count(root) >= 2, "the loader still accepts this set; only a guard refuses it"

    check = _check_migrations(root)

    assert isinstance(check.failure, UnenforceableScopeError)
    assert check.count == 0, "nothing was validated, so there is no number to publish"


def test_a_repository_reached_through_a_symlink_is_checked_rather_than_crashing(
    tmp_path: Path,
) -> None:
    """``paths.root``, never the root that arrived (measured).

    ``ProjectPaths.of`` resolves symlinks. Passing ``load_migrations`` the
    resolved *migrations* directory beside the unresolved project root makes its
    own containment check raise ``ValueError: ... is not in the subpath`` -- not
    a refusal and not a verdict, so it escapes the documented families the
    wrapper catches and reaches the reader as ``SetupService._probe``'s "Could
    not check migrations-valid".

    Not a contrived shape: ``/var`` is a symlink to ``/private/var`` on macOS, so
    any repository under a ``/var`` path arrives this way.
    """
    root = _sample(tmp_path, "real")
    link = tmp_path / "through-a-symlink"
    link.symlink_to(root, target_is_directory=True)

    check = _check_migrations(link)

    assert check.failure is None, (
        f"the wrapper mixed a resolved path with an unresolved one: {check.failure}"
    )
    assert check.count == _loaded_count(root)
