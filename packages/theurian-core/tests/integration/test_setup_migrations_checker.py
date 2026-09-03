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

The other half of the wrapper's contract is **which failures it treats as a
verdict about the files** rather than letting escape to ``SetupService._probe``,
whose net answers "Could not check migrations-valid" as a *conflict* -- a status
that stops setup to ask for consent. Anything ``migrate validate`` refuses on
belongs on this side of that line.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from fakes.setup import FakeMcpConfig, FakeService

from theurian.application.project_service import ProjectPaths
from theurian.application.setup_context import SetupContext
from theurian.application.setup_steps import probe_migrations
from theurian.cli.context import schema_root
from theurian.cli.setup_commands import _check_migrations, _current_state_hash
from theurian.domain.errors import (
    AliasItemCollisionError,
    DuplicateContentFileError,
    InvalidIdentifierError,
    IrregularSourceFileError,
    TheurianError,
    UnenforceableScopeError,
)
from theurian.domain.setup import StepStatus
from theurian.infrastructure.claude.mcp_config import ConnectionSpec
from theurian.infrastructure.filesystem.migration_loader import load_migrations
from theurian.infrastructure.secrets.file_store import FileSecretStore

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


def _context(tmp_path: Path, root: Path) -> SetupContext:
    """A context wired to the real readers, which is what these tests are about.

    Both of the composition root's migration readers, not only the checker: they
    load the same set through the same call, and a context holding one real and
    one stubbed would let the two answer about different files.
    """
    data_dir = tmp_path / "home" / ".theurian"
    return SetupContext(
        home=tmp_path / "home",
        data_dir=data_dir,
        port=7419,
        project_root=root,
        connection=ConnectionSpec(port=7419),
        mcp_config=FakeMcpConfig(),
        secrets=FileSecretStore(data_dir),
        health=lambda: None,
        service=FakeService(),
        executable="",
        check_migrations=_check_migrations,
        current_state_hash=_current_state_hash,
    )


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


#: The auth-policy body the first migration pins, and its digest -- reused to
#: point the second migration's revision at the *same* file, which is what
#: `refuse_duplicate_content_files` refuses (one file cannot back two revisions).
_AUTH_POLICY_BODY = "../knowledge/architecture/auth-policy.md"
_AUTH_POLICY_SHA = "9cfd9b19030da602ea3339ef6f65ac176ce776c0c467a98bf9d11639241dc69f"  # gitleaks:allow  # noqa: E501


def test_a_duplicate_content_file_set_is_a_verdict_and_not_a_broken_probe(tmp_path: Path) -> None:
    """Two revisions backed by one body file: the second whole-set guard refuses it.

    The load is not the whole of `migrate validate`, and the scope test above only
    proves the *first* guard runs through the real checker. This drives the second
    (``refuse_duplicate_content_files``, issue #210) the same way: the
    order-cancellation revision is repointed at the auth-policy body, digest and
    all, so both revisions resolve to one inode. The loader accepts that -- the
    pins match the bytes -- and only the whole-set guard stops it, so a checker
    that ran the load without the guards would call this set healthy.
    """
    root = _sample(tmp_path)
    migrations = sorted(ProjectPaths.of(root).migrations.glob("*.yaml"))
    second = migrations[1]
    text = second.read_text(encoding="utf-8")
    text = text.replace("../knowledge/domain/order-cancellation.md", _AUTH_POLICY_BODY, 1)
    text = text.replace(
        "contentSha256: 08bb9731aae7158a5d81796f3218e0f1b34ae2e46053ca71bace1fe9c5e9f1a7",
        f"contentSha256: {_AUTH_POLICY_SHA}",
        1,
    )
    second.write_text(text, encoding="utf-8")
    assert _loaded_count(root) >= 2, "the loader still accepts this set; only a guard refuses it"

    check = _check_migrations(root)

    assert isinstance(check.failure, DuplicateContentFileError)
    assert check.count == 0, "nothing was validated, so there is no number to publish"


def test_an_alias_colliding_with_a_live_item_is_a_verdict_and_not_a_broken_probe(
    tmp_path: Path,
) -> None:
    """An addAlias key equal to a live item id: the third whole-set guard refuses it.

    The third guard (``refuse_alias_item_id_collision``, SEC-13/T-21) driven
    through the real checker. ``architecture.auth-policy`` ends ``approved`` -- a
    live, non-deprecated item -- and an ``addAlias`` keyed on that id would let a
    lookup for it resolve through the alias to another item. The schema accepts the
    op, so the loader accepts the set; only the guard stops it.
    """
    root = _sample(tmp_path)
    migrations = sorted(ProjectPaths.of(root).migrations.glob("*.yaml"))
    second = migrations[1]
    second.write_text(
        second.read_text(encoding="utf-8")
        + "\n  - op: addAlias\n"
        + "    alias: architecture.auth-policy\n"
        + "    itemId: domain.order-cancellation\n",
        encoding="utf-8",
    )
    assert _loaded_count(root) >= 2, "the loader still accepts this set; only a guard refuses it"

    check = _check_migrations(root)

    assert isinstance(check.failure, AliasItemCollisionError)
    assert check.count == 0, "nothing was validated, so there is no number to publish"


@pytest.mark.skipif(
    not hasattr(os, "mkfifo"),
    reason="a FIFO is the cheapest irregular file, and this platform has no mkfifo",
)
def test_an_irregular_content_file_is_a_verdict_and_not_a_broken_probe(tmp_path: Path) -> None:
    """A FIFO ``contentFile``: `migrate validate` refuses, so `doctor` must too (#91, #215).

    ``read_source_file`` refuses a file whose ``st_size`` bounds nothing before it
    opens anything -- a FIFO reports 0, passes the byte cap, and then blocks in
    ``open()`` until a writer appears. The loader re-raises that as
    ``IrregularSourceFileError``, which is a ``SecurityError`` and so is caught
    by none of the ``MigrationError`` branches.

    It was left out of the wrapper's refusal set because ``load_migrations``'
    ``Raises`` did not list it, and the consequence is the exact divergence #91
    exists to close, one symbol wide: the exception escaped, ``SetupService._probe``
    turned it into CONFLICTING "Could not check migrations-valid", and setup
    stopped to ask for consent -- on a directory ``theurian migrate validate``
    simply refuses.

    The load is asserted to refuse first, so a fixture whose FIFO never reached
    the read would fail here rather than pass for the wrong reason. Nothing in
    this test opens the FIFO, and nothing may: an ``open()`` on it with no writer
    is the hang the refusal exists to prevent.
    """
    root = _sample(tmp_path)
    body = root / ".theurian" / "knowledge" / "architecture" / "auth-policy.md"
    body.unlink()
    os.mkfifo(body)
    with pytest.raises(IrregularSourceFileError):
        _loaded_count(root)

    check = _check_migrations(root)

    assert isinstance(check.failure, IrregularSourceFileError)
    assert check.count == 0

    step = probe_migrations(_context(tmp_path, root))

    assert step.status is StepStatus.MISSING, (
        "an escaped refusal reaches the reader as a conflict, which stops setup for consent"
    )
    assert step.summary == f"The migrations in {ProjectPaths.of(root).migrations} do not validate."
    assert step.action == (
        "Fix the file it names; `theurian migrate validate` prints the full refusal."
    )


def test_a_trailing_newline_identifier_is_a_verdict_and_not_a_broken_probe(tmp_path: Path) -> None:
    """A block-scalar ``id``: `migrate validate` refuses, so `doctor` must too (#91).

    An ``id: |`` block scalar yields ``<ULID>\\n``. The schema's ULID ``pattern`` is
    ``$``-anchored, and Python's ``re`` -- which `jsonschema` uses -- matches ``$``
    immediately before a trailing newline, so the document *validates*; only
    ``MigrationId``'s ``\\Z``-anchored check refuses it, raising
    ``InvalidIdentifierError`` (a ``DomainError``, hence a ``TheurianError``).

    That family was omitted from the old hand-listed catch tuple, so the exception
    escaped the checker and reached the reader as ``SetupService._probe``'s
    CONFLICTING "Could not check migrations-valid" -- setup stopping to ask for
    consent on a directory ``theurian migrate validate`` simply refuses. This is
    the third instance of #91's class, and it is closed by catching
    ``TheurianError`` -- exactly the set `migrate validate` refuses on -- rather
    than a subset built from ``load_migrations``' ``Raises`` docstring.

    The load is asserted to refuse first, so a fixture whose ``id`` never reached
    the constructor would fail here rather than pass for the wrong reason.
    """
    root = _sample(tmp_path)
    migration = next(iter(sorted(ProjectPaths.of(root).migrations.glob("*.yaml"))))
    text = migration.read_text(encoding="utf-8")
    original = "id: 01K1ABCXYZ01234567890ABCDE\n"
    assert original in text, "the fixture's first migration is the one this mutates"
    migration.write_text(
        text.replace(original, "id: |\n  01K1ABCXYZ01234567890ABCDE\n", 1), encoding="utf-8"
    )
    with pytest.raises(InvalidIdentifierError):
        _loaded_count(root)

    check = _check_migrations(root)

    assert isinstance(check.failure, InvalidIdentifierError)
    assert check.count == 0

    step = probe_migrations(_context(tmp_path, root))

    assert step.status is StepStatus.MISSING, (
        "an escaped refusal reaches the reader as a conflict, which stops setup for consent"
    )
    assert step.summary == f"The migrations in {ProjectPaths.of(root).migrations} do not validate."


def test_any_theurian_error_from_the_load_is_a_verdict_not_an_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The class, not the symbol -- which is what closes the next unknown family.

    The trailing-newline case above is one member of the set `migrate validate`
    refuses on; this pins that the checker catches the whole ``TheurianError``
    *family*, not a hand-listed subset of it. A ``TheurianError`` subtype a future
    loader raises -- one no catch list here names -- must come back as a failure
    rather than escaping to ``SetupService._probe``'s generic net and re-opening
    #91's divergence, which is precisely how the tuple this replaces was wrong
    twice (``IrregularSourceFileError``, then ``InvalidIdentifierError``).

    Monkeypatched at the loader the checker actually calls, because the point is
    the catch clause, not any particular way of provoking it.
    """
    root = _sample(tmp_path)
    sentinel = TheurianError("a loader error no hand-listed catch set names")

    def _raise(*_args: object, **_kwargs: object) -> None:
        raise sentinel

    monkeypatch.setattr("theurian.cli.setup_commands.load_migrations", _raise)

    check = _check_migrations(root)

    assert check.failure is sentinel, "the family is caught, not a hand-listed subset of it"
    assert check.count == 0


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
