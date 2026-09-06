"""The composition root's half of the two steps that read a migration set.

``migrations-valid`` (#91) is the older half and most of this file;
``initial-index`` (#451) is the second, at the bottom. Both are here for one
reason: ``cli/setup_commands.build_context`` wires a reader into the context,
and no test that *injects* its own reader can see that wiring being wrong --
the injected reader is the thing that would be wrong.

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
import subprocess
from pathlib import Path

import pytest
from fakes.setup import FakeMcpConfig, FakeService

from theurian.application.project_service import ProjectError, ProjectPaths, resolve_state_hash
from theurian.application.setup_context import SetupContext
from theurian.application.setup_service import SetupRequest, SetupService
from theurian.application.setup_steps import (
    SCHEMAS_UNUSABLE_ACTION,
    SCHEMAS_UNUSABLE_SUMMARY,
    Step,
    probe_initial_index,
    probe_migrations,
)
from theurian.cli.context import resolve_context, schema_root
from theurian.cli.setup_commands import _check_migrations, _current_state_hash, _redacted
from theurian.domain.errors import (
    AliasItemCollisionError,
    DuplicateContentFileError,
    InvalidIdentifierError,
    IrregularSourceFileError,
    MigrationError,
    SchemaUnreadableError,
    TheurianError,
    UnenforceableScopeError,
)
from theurian.domain.setup import StepId, StepStatus
from theurian.domain.state import StateHash
from theurian.infrastructure.claude.mcp_config import ConnectionSpec
from theurian.infrastructure.filesystem.migration_loader import load_migrations
from theurian.infrastructure.secrets.file_store import FileSecretStore
from theurian.infrastructure.sqlite.schema import SCHEMA_VERSION

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


def _context(tmp_path: Path, root: Path, *, for_publication: bool = False) -> SetupContext:
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
        for_publication=for_publication,
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


# -- #529: which side refused, and what the shared report says about it -------
#
# Two things can refuse this load, and only one of them is the operator's. A
# build that cannot locate or read the JSON Schemas it publishes was reported as
# "The migrations in <dir> do not validate.", action "Fix the file it names" --
# and under ``doctor --report`` that was the entire message, because
# ``failure_detail`` publishes a type name there and nothing else. So the
# shareable copy carried the misattribution with no cause to correct it.
#
# The faults below are provoked at production's own raise sites rather than by
# monkeypatching the checker: face 1 makes ``_schema_candidate_exists`` answer
# False for both candidates, which is what ``schema_root()`` raises on, and face
# 2 points the real loader at a real schema file it cannot parse. Each asserts
# the production call refuses *first*, so a fixture that failed to provoke it
# fails here rather than passing through the wrong arm.


def _a_schema_tree_that_cannot_be_parsed(tmp_path: Path) -> Path:
    """A schema root whose ``migration.schema.json`` is not JSON.

    The loader translates that at its validate seam into
    ``SchemaUnreadableError`` -- "a candidate was found, but using it failed" --
    which is install integrity and not migration content.
    """
    broken = tmp_path / "broken-schemas" / "migrations"
    broken.mkdir(parents=True)
    (broken / "migration.schema.json").write_text("{ not json", encoding="utf-8")
    return broken.parent


def test_no_schema_candidate_at_all_is_the_installations_fault_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Face 1: ``schema_root()`` locates neither candidate, so nothing was read.

    The ``ProjectError`` it raises says "This build is incomplete; reinstall
    theurian", and until #529 the step that caught it published "The migrations
    in <dir> do not validate." with the action "Fix the file it names" -- sending
    the author to YAML the load never opened.

    The probe assertions name the constants rather than repeating their text, so
    a wording change moves one place; the two negatives beneath them are what a
    constant cannot say: the sentence must not blame the migrations, and must
    not name their directory as the thing to go and edit.
    """
    root = _sample(tmp_path)
    monkeypatch.setattr("theurian.cli.context._schema_candidate_exists", lambda _c: False)
    with pytest.raises(ProjectError):
        schema_root()

    check = _check_migrations(root)

    assert isinstance(check.failure, ProjectError)
    assert check.schemas_unusable is True
    assert check.count == 0

    step = probe_migrations(_context(tmp_path, root))

    assert step.status is StepStatus.MISSING
    assert step.summary == SCHEMAS_UNUSABLE_SUMMARY
    assert step.action == SCHEMAS_UNUSABLE_ACTION
    assert "do not validate" not in step.summary
    assert str(ProjectPaths.of(root).migrations) not in step.summary + step.action


def test_a_schema_the_loader_cannot_use_is_the_installations_fault_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Face 2: a schema that is present and unusable, refused inside the load.

    A separate test rather than a parameter of the one above, because the two
    fail at different points -- one before ``load_migrations`` is called at all,
    this one where the validator is built, after the migrations directory has
    been listed -- and a fix that keyed only on ``ProjectError`` would still
    misattribute this one.

    This fixture opens no migration file (measured: zero ``read_source_file``
    calls). A third point does, and it is why :data:`SCHEMAS_UNUSABLE_ACTION`
    says the files are *not implicated* rather than that they were never read:
    an unresolvable ``$ref`` reaches ``SchemaUnreadableError`` from inside
    ``validator.validate(document)``, with that document already read.
    """
    root = _sample(tmp_path)
    broken = _a_schema_tree_that_cannot_be_parsed(tmp_path)
    monkeypatch.setattr("theurian.cli.setup_commands.schema_root", lambda: broken)
    paths = ProjectPaths.of(root)
    with pytest.raises(SchemaUnreadableError):
        load_migrations(paths.root, paths.migrations, broken)

    check = _check_migrations(root)

    assert isinstance(check.failure, SchemaUnreadableError)
    assert check.schemas_unusable is True

    step = probe_migrations(_context(tmp_path, root))

    assert step.status is StepStatus.MISSING
    assert step.summary == SCHEMAS_UNUSABLE_SUMMARY
    assert step.action == SCHEMAS_UNUSABLE_ACTION


def test_migrations_the_operator_broke_keep_naming_the_migrations(tmp_path: Path) -> None:
    """The control: the split must not widen, and this is the arm it must leave alone.

    Without it, a checker that set ``schemas_unusable`` unconditionally -- or a
    probe that took the reinstall arm for every failure -- passes both tests
    above while telling every author with a typo to reinstall Theurian.
    """
    root = _sample(tmp_path)
    (ProjectPaths.of(root).migrations / "0002-broken.yaml").touch()

    check = _check_migrations(root)

    assert isinstance(check.failure, MigrationError)
    assert check.schemas_unusable is False

    step = probe_migrations(_context(tmp_path, root))

    assert step.summary == f"The migrations in {ProjectPaths.of(root).migrations} do not validate."
    assert step.action == (
        "Fix the file it names; `theurian migrate validate` prints the full refusal."
    )


def _published_migrations_step(context: SetupContext) -> dict[str, object]:
    """The ``migrations-valid`` entry of a ``doctor --report`` payload.

    Produced the way ``doctor_command`` produces it -- ``SetupService``, then
    ``to_json``, then ``_redacted`` -- because the report surface is the half of
    #529 that had no rescue at all, and a test reading ``SetupStep`` fields
    directly would not have seen it.
    """
    steps = (Step(StepId.MIGRATIONS_VALID, probe_migrations, None, critical=False),)
    report = SetupService(context, steps).run(SetupRequest(dry_run=True))
    published = _redacted(report.to_json(), context)
    steps_published: list[dict[str, object]] = published["steps"]
    entries = [s for s in steps_published if s["id"] == StepId.MIGRATIONS_VALID.value]
    assert len(entries) == 1, f"one migrations-valid entry, got {entries}"
    return entries[0]


def test_the_shared_report_carries_the_reinstall_cause_and_not_only_a_type_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second face of #529, driven at the surface it survived on.

    ``failure_detail`` still publishes a bare ``ProjectError.`` here, and that is
    correct -- an exception carries whatever raised it, and this one carries an
    absolute installation path (O-3, SEC-6). What changed is that the rescue no
    longer lives there: ``summary`` and ``action`` are Theurian's own sentences,
    they travel into the payload, and they name the installation and a command
    that repairs it.

    The last two assertions are the control that makes the first three mean
    something: on the same surface, with an operator's own broken migration, the
    payload says the opposite -- so a step that had simply started saying
    "reinstall" for everything fails here.
    """
    root = _sample(tmp_path)
    monkeypatch.setattr("theurian.cli.context._schema_candidate_exists", lambda _c: False)

    entry = _published_migrations_step(_context(tmp_path, root, for_publication=True))

    assert entry["summary"] == SCHEMAS_UNUSABLE_SUMMARY
    assert entry["action"] == SCHEMAS_UNUSABLE_ACTION
    assert entry["detail"] == (
        "ProjectError. The message is withheld from a shared report because an "
        "exception carries whatever raised it; run `theurian doctor` without "
        "--report to see it."
    ), "the withholding control is unchanged; the rescue moved, it was not widened"

    monkeypatch.undo()
    (ProjectPaths.of(root).migrations / "0002-broken.yaml").touch()

    operators = _published_migrations_step(_context(tmp_path, root, for_publication=True))

    assert operators["summary"] != SCHEMAS_UNUSABLE_SUMMARY
    assert "do not validate" in str(operators["summary"])


# -- The composition root's other reader: which state is this at (#451) ------
#
# ``_current_state_hash`` is the second thing ``build_context`` wires, and it
# decides `initial-index`'s whole sentence. Round one measured that no test
# called it: a ``raise`` on its first line, an unconditional ``return None``, a
# ``SCHEMA_VERSION + 1``, and a catch narrowed to ``FileNotFoundError`` each
# left the full suite green, and reintroducing #451's own predicate into it
# passed 5132 tests while the real CLI reproduced the defect. Every driving test
# reached `initial-index` through ``setup_migrations.state_hash_from_the_loader``
# -- the hand-written double -- so what was measured was the copy.
#
# That is the same gap this file was opened for, one field over: an
# injection-based test cannot see a wrong wrapper, because the injected wrapper
# *is* the thing that would be wrong. So these four drive the real one, and the
# expectations come from somewhere other than the function under test.


def _state_hash_the_loader_resolves(root: Path) -> StateHash:
    """A second, independent resolve -- what :func:`_loaded_count` is to the count.

    Deliberately not ``setup_migrations.state_hash_from_the_loader``: that double
    is what stood in for production everywhere, and a test proving production
    right must not take its expectation from the stand-in.
    """
    paths = ProjectPaths.of(root)
    loaded = load_migrations(paths.root, paths.migrations, schema_root())
    return resolve_state_hash(loaded, SCHEMA_VERSION)


def _a_repository(root: Path) -> Path:
    """Make ``root`` a Git working tree, which is what scopes a project."""
    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603
    return root


def test_the_wired_resolver_names_the_state_hash_project_status_addresses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`doctor` and `theurian project status` must address one database (#451).

    The two commands disagreed about one working tree because they answered
    different questions, and the fix is only worth anything if they now resolve
    the *same* hash. So the expectation is `project status`' own resolver --
    ``resolve_context``, which is where ``state_hash`` comes from in
    ``project_status`` -- rather than a second copy of the arithmetic written
    here. A copy would agree with a wrapper that had drifted; the other command
    cannot.

    That is what makes a schema version bumped in one place and not the other
    visible: both readers hash the same loaded set, so nothing but the wiring
    can separate them.

    ``HOME`` and ``THEURIAN_DATA_DIR`` are redirected because ``resolve_context``
    consults the per-user project registry, which lives under the second and
    falls back to the first.
    """
    root = _a_repository(_sample(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(tmp_path / "home" / ".theurian"))

    resolved = _current_state_hash(root)

    assert resolved is not None, "the fixture's set loads, so there is a hash to compare"
    assert resolved == resolve_context(root).state_hash


def test_the_step_reports_the_state_built_through_the_resolver_the_cli_wires(
    tmp_path: Path,
) -> None:
    """The built arm, reached through the composition root rather than a double.

    The database is placed at the path an *independent* load resolves, so a
    resolver that hashed a different schema version, or answered ``None``, sends
    the step to a file this test never created and the sentence changes.
    """
    root = _sample(tmp_path)
    database = ProjectPaths.of(root).database_for(_state_hash_the_loader_resolves(root))
    database.parent.mkdir(parents=True, exist_ok=True)
    database.touch()

    step = probe_initial_index(_context(tmp_path, root))

    assert step.status is StepStatus.NOT_APPLICABLE
    assert step.summary == "Knowledge state is built."


def test_the_step_names_the_apply_remedy_through_the_resolver_the_cli_wires(
    tmp_path: Path,
) -> None:
    """The remedy arm: a set that loads, with nothing applied for it yet.

    The arm #451 made unreachable, driven here through the real resolver. The
    guard is not decoration -- with the database present this state is the test
    above, and the two would report the same sentence for opposite reasons.
    """
    root = _sample(tmp_path)
    database = ProjectPaths.of(root).database_for(_state_hash_the_loader_resolves(root))
    assert not database.exists(), "nothing has been applied here; that is the state under test"

    step = probe_initial_index(_context(tmp_path, root))

    assert step.status is StepStatus.NOT_APPLICABLE
    assert step.summary == "No knowledge state built yet. Run `theurian migrate apply`."


def test_a_set_the_wired_resolver_cannot_read_makes_the_step_say_so(tmp_path: Path) -> None:
    """A refused load is an answer here, not an escape and not "not built".

    ``_current_state_hash`` catches the whole ``TheurianError`` family for the
    reason ``_check_migrations`` does, and narrowing that catch is the mistake
    #91 made twice: the exception escapes to ``SetupService._probe``, which
    publishes CONFLICTING "Could not check initial-index." and stops setup to ask
    for consent -- on a directory `theurian migrate validate` simply refuses.
    Probed directly here, with no net underneath, so an escape fails the test
    rather than being reworded by the runner.

    The load is asserted to refuse first, so a fixture whose broken file never
    reached the parser would pass this for the wrong reason.
    """
    root = _sample(tmp_path)
    (ProjectPaths.of(root).migrations / "0002-broken.yaml").touch()
    with pytest.raises(MigrationError):
        _loaded_count(root)

    step = probe_initial_index(_context(tmp_path, root))

    assert step.status is StepStatus.NOT_APPLICABLE
    assert step.summary == (
        "Cannot tell what state this project is at: its migration set could not "
        "be read. Run `theurian migrate validate`, which prints why."
    )
