"""CLI commands, invoked in-process.

The e2e suite runs the installed binary and proves packaging works. These run
the same commands through Typer's runner: faster, measurable by coverage, and
able to assert on the exact JSON a caller receives.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from theurian.application.project_service import ProjectError, ProjectRegistry
from theurian.cli.main import app

pytestmark = pytest.mark.integration

runner = CliRunner()

EXIT_STATE_ERROR = 4

MIGRATION_ID = "01K1AAAAAA01234567890ABCDE"
REVISION_ID = "01K1AAAREV01234567890ABCDE"
BODY = "# Authentication policy\n\nEvery call carries a signed token.\n"

MIGRATION = f"""apiVersion: theurian.dev/v1
id: {MIGRATION_ID}
createdAt: 2026-08-02T10:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.auth-policy
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.auth-policy
    revisionId: {REVISION_ID}
    contentFile: ../knowledge/architecture/auth-policy.md
    metadata:
      title: Authentication policy
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/auth-policy.md
"""


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    root = tmp_path / "demo"
    root.mkdir()
    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603

    monkeypatch.setenv("THEURIAN_DATA_DIR", str(tmp_path / "datadir"))
    monkeypatch.chdir(root)
    yield root


def _invoke(*args: str) -> tuple[int, dict[str, Any]]:
    """Run a command and parse its JSON.

    ``mix_stderr=False`` matters: the CLI keeps stdout a clean machine channel
    and puts errors on stderr, and a test that merged them could not tell.
    """
    result = runner.invoke(app, [*args, "--json"], catch_exceptions=False)
    stream = result.stdout if result.exit_code == 0 else (result.stderr or result.stdout)
    return result.exit_code, json.loads(stream) if stream.strip() else {}


def _write_migration(root: Path, migration: str = MIGRATION, body: str = BODY) -> None:
    (root / ".theurian/knowledge/architecture/auth-policy.md").write_text(body)
    (root / f".theurian/migrations/{MIGRATION_ID}-add-auth-policy.yaml").write_text(migration)


# -- init ------------------------------------------------------------------


def test_init_creates_the_layout(project: Path) -> None:
    code, payload = _invoke("init")
    assert code == 0
    assert payload["changed"]
    assert (project / ".theurian/migrations").is_dir()
    assert (project / ".theurian/knowledge/architecture").is_dir()


def test_init_is_idempotent(project: Path) -> None:
    _invoke("init")
    code, payload = _invoke("init")
    assert code == 0
    assert not payload["changed"]
    assert payload["createdPaths"] == []


def test_init_appends_the_gitignore_block_once(project: Path) -> None:
    """SEC-18: re-running rewrites only Theurian's own marked block."""
    (project / ".gitignore").write_text("# a rule the user wrote\n*.log\n")

    _invoke("init")
    _invoke("init")

    content = (project / ".gitignore").read_text()
    assert content.count("# >>> theurian >>>") == 1
    assert "*.log" in content, "the user's own rules must survive"


def test_init_outside_a_git_repository_fails_clearly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(tmp_path / "datadir"))

    code, payload = _invoke("init")
    assert code == 1
    assert "not inside a Git repository" in payload["error"]


# -- project ---------------------------------------------------------------


def test_register_then_list(project: Path) -> None:
    _invoke("init")
    code, registered = _invoke("project", "register")
    assert code == 0
    assert registered["projectId"] == "demo"

    _, listed = _invoke("project", "list")
    assert listed["count"] == 1


def test_register_is_idempotent(project: Path) -> None:
    """FR-L2. The first registration time is preserved, not refreshed."""
    _invoke("init")
    _invoke("project", "register")
    _, again = _invoke("project", "register")
    assert not again["changed"]


def test_unregister_reports_that_knowledge_survives(project: Path) -> None:
    _invoke("init")
    _invoke("project", "register")

    _, removed = _invoke("project", "unregister", "demo")
    assert removed["removed"]
    assert removed["knowledgePreserved"]

    _, again = _invoke("project", "unregister", "demo")
    assert not again["removed"], "removing a missing project is not an error"


def test_status_reports_an_unbuilt_state(project: Path) -> None:
    _invoke("init")
    _write_migration(project)

    _, status = _invoke("project", "status")
    assert status["migrationCount"] == 1
    assert not status["stateBuilt"]
    assert status["indexStale"]


def test_status_outside_a_repository_reports_unregistered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(tmp_path / "datadir"))

    code, status = _invoke("project", "status")
    assert code == 0, "status must report, not fail, outside a project"
    assert not status["registered"]


# -- project list, when the registry file is not what it should be ----------
#
# `project list` is the command every other surface names when it wants a user
# to go and look -- `project register`'s and `ids_for_root`'s remedies both send
# them here -- so it is the one command that must survive a registry it cannot
# fully read. A skipped entry it did not report was a project that vanished in
# silence, and a file it could not parse at all reached the user as a Rich
# traceback from the very command the remedy told them to run.


@pytest.fixture
def registry_path(project: Path) -> Path:
    """The per-user registry file this test's ``THEURIAN_DATA_DIR`` points at.

    Written to directly, because no supported command can produce a malformed
    entry -- the file lives in the user's home directory and a hand edit is the
    only way in, which is exactly why these branches exist.
    """
    path = project.parent / "datadir" / "projects.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def test_list_reports_an_empty_unreadable_set_rather_than_omitting_the_field(
    project: Path,
) -> None:
    """CP-2. The field is part of the shape, not a flag that appears on trouble.

    A consumer that has to branch on whether a key is present will eventually
    forget to, and the day it forgets is the day the key is there -- when
    something is broken. Asserted as ``== []``, not as ``in listed``: a value of
    ``None``, or a string, would satisfy "the key exists" while breaking every
    caller that iterates it.
    """
    _invoke("init")
    _invoke("project", "register")

    _, listed = _invoke("project", "list")

    assert listed["unreadable"] == []
    assert "remedy" not in listed, "a healthy registry must not print a cure for nothing"


def test_list_names_the_unreadable_id_so_the_remedy_can_be_typed(
    project: Path, registry_path: Path
) -> None:
    """The id `project unregister` needs is the id only this command can show.

    Every other surface -- `project register`, `resolve_context`, `index status`
    -- reports an unreadable entry by telling the user to run
    ``theurian project unregister <id>`` and to find ``<id>`` here. Counting the
    readable entries and silently dropping the rest, which is what `load` alone
    does, made that remedy untypable and the project itself invisible.

    ``count`` deliberately still reports only what is readable: the entry is
    named under ``unreadable``, not padded into ``projects`` as a registration
    the command cannot actually describe.
    """
    registry_path.write_text(
        json.dumps(
            {
                "demo": {"rootPath": str(project), "defaultBranch": "main"},
                "hand-edited": {"defaultBranch": "main"},
            }
        )
    )

    code, listed = _invoke("project", "list")

    assert code == 0
    assert listed["unreadable"] == ["hand-edited"]
    assert listed["count"] == 1, "the unreadable entry is named, not counted as a project"
    assert [p["projectId"] for p in listed["projects"]] == ["demo"]
    assert "theurian project unregister" in listed["remedy"]


@pytest.mark.parametrize(
    ("corruption", "content", "expected"),
    [
        ("truncated JSON", b'{"demo": {"rootPath"', "cannot be read as JSON"),
        ("a JSON array", b"[]", "must hold a JSON object"),
        ("arbitrary bytes", b"\xff\xfe\x00\x01theurian", "cannot be read as JSON"),
    ],
    ids=["truncated-json", "json-array", "arbitrary-bytes"],
)
def test_list_reports_a_registry_it_cannot_parse_instead_of_raising(
    registry_path: Path, corruption: str, content: bytes, expected: str
) -> None:
    """CP-2, and the loop that had no exit.

    None of these can be recovered from entry by entry -- without a dict of ids
    there is nothing to partition -- so the whole file is refused. It still has
    to arrive as the ``{error, remedy}`` contract at exit 1: this command is
    where every other remedy sends the user, so a traceback here left them with
    a broken registry and no working way to inspect it.

    ``arbitrary bytes`` is the case that hid behind the other two. A registry of
    binary -- a partial overwrite, a restored file -- raises
    ``UnicodeDecodeError`` at ``read_text``, which is a ``ValueError`` and *not*
    a ``JSONDecodeError``, so it sailed past a handler that caught only the
    latter. ``catch_exceptions=False`` is what makes that a failure here rather
    than a silently swallowed exit code.
    """
    registry_path.write_bytes(content)

    code, payload = _invoke("project", "list")

    assert code == 1, f"{corruption} must be reported, not raised"
    assert expected in payload["error"]
    assert str(registry_path) in payload["error"], "the file to fix is named"
    assert "theurian project register" in payload["remedy"], (
        "the remedy is delete-and-re-register; a message with no way out is why this branch exists"
    )


# -- the same unreadable file, reached through the other two commands -------
#
# The fix above taught `project list` to report a registry it cannot parse.
# It never reached the two commands beside it, and both are on the remedy chain
# every other surface prints: `project status` is what a confused user runs
# first, and `project unregister <id>` is where the chain ends. A traceback at
# the first and a wrong cure at the last leave that chain broken at both ends.
#
# The corruption shapes are the three above, restated rather than shared,
# because "these three shapes reach this command" is the claim: a shared
# parameter list that lost one would quietly narrow every test using it.

REGISTRY_CORRUPTIONS = [
    ("truncated JSON", b'{"demo": {"rootPath"', "cannot be read as JSON"),
    ("a JSON array", b"[]", "must hold a JSON object"),
    ("arbitrary bytes", b"\xff\xfe\x00\x01theurian", "cannot be read as JSON"),
]


@pytest.mark.parametrize(
    ("corruption", "content", "expected"),
    REGISTRY_CORRUPTIONS,
    ids=["truncated-json", "json-array", "arbitrary-bytes"],
)
def test_status_reports_a_registry_it_cannot_parse_instead_of_raising(
    registry_path: Path, corruption: str, content: bytes, expected: str
) -> None:
    """The ``--json`` contract has to hold on the command people run when lost.

    ``status``'s handler for a failed ``resolve_context`` asks the registry for
    its unreadable ids -- and on a file that is not JSON at all, that read raises
    the very exception the handler is inside. Measured before the fix: exit 1,
    stdout zero bytes, a Rich traceback, on all three shapes. A caller parsing
    ``--json`` gets nothing to parse, from the command every remedy sends them
    to first.

    Exit 0 is deliberate and matches the rest of this command: ``status``
    answers for directories that are not projects at all, so "cannot tell" is a
    status rather than a command failure -- and ``registered: null`` is already
    its value for exactly that.

    ``unreadable`` is ``[]`` here and that is not a claim that nothing is
    broken: without a JSON object there is no set of ids to partition, so the
    list is empty because it could not be computed. ``reason`` and ``remedy``
    carry the whole-file failure, and the field stays present because a
    consumer that has to branch on key presence eventually forgets to.
    """
    registry_path.write_bytes(content)

    code, payload = _invoke("project", "status")

    assert code == 0, f"{corruption} must be reported as a status, not raised"
    assert payload["registered"] is None, "the registry cannot say, and False would be a guess"
    assert expected in payload["reason"]
    assert "re-register each project with `theurian project register`" in payload["remedy"], (
        "the whole-file failure has one reliable cure, and this is the command that must print it"
    )
    assert payload["unreadable"] == []


def test_unregister_names_the_unreadable_file_rather_than_blaming_the_id(
    registry_path: Path,
) -> None:
    """The last link of the remedy chain, and the one that pointed nowhere.

    ``project list``, ``project status``, ``probe_project_registered``,
    ``project register`` and every MCP tool name
    ``theurian project unregister <id>`` as the cure for a broken registry. When
    the file cannot be read at all, this command answered "Check the project id
    with `theurian project list`" -- and the id is not the problem, ``list``
    fails on the same file, and the user is returned to where they started.

    One corruption shape rather than three: what varies between them is the
    message, which the ``project list`` tests above already pin. What is
    asserted here is the remedy, which does not vary.
    """
    registry_path.write_bytes(b'{"demo": {"rootPath"')

    code, payload = _invoke("project", "unregister", "demo")

    assert code == 1
    assert "re-register each project with `theurian project register`" in payload["remedy"]
    assert "Check the project id" not in payload["remedy"], (
        "the id is not what is wrong, and `project list` cannot read this file either"
    )


def test_status_outside_a_repository_keeps_a_certain_answer_on_a_wholly_corrupt_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unrelated ambiguity must not weaken an answer that is not ambiguous.

    A directory outside a Git working tree is not a project whatever the
    registry says: there is no root for any registration to name, so no failure
    to read that file could possibly be about *this* directory. ``registered``
    stays ``False`` -- the honest answer -- rather than being dragged to ``None``
    by a file the question does not depend on.

    Pinned because the tempting simplification is "the registry is broken, so
    nothing can be known", and it is wrong in exactly this one case. The
    in-repository counterpart, where ``None`` *is* correct, is
    ``test_status_reports_a_registry_it_cannot_parse_instead_of_raising``
    above; without this pair, either behaviour alone looks like the rule.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(tmp_path / "datadir"))
    path = tmp_path / "datadir" / "projects.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(b'{"demo": {"rootPath"')

    code, payload = _invoke("project", "status")

    assert code == 0
    assert "not inside a Git repository" in payload["reason"], "the fixture must be outside one"
    assert payload["registered"] is False
    assert payload["unreadable"] == []


def test_status_says_it_cannot_know_when_the_registry_breaks_between_its_two_reads(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The resolved path's own version of "cannot know", which nothing reached.

    ``resolve_context`` asks the registry which project this root is, and the
    payload asks it again for the ``registered`` flag and the unreadable set.
    "A moment ago" is not "now": the file lives in the user's home directory,
    another ``theurian`` process shares it, and the product's own remedies tell
    people to edit it. A raise on the second read cost this command its entire
    ``--json`` payload for a file it consults for one field.

    Forced with a monkeypatch rather than a real race, because a race is not a
    fixture. What is being tested is the *handling*, and a test that has to win
    a timing lottery to reach it is a test that mostly does not.

    ``registered`` becomes ``None`` and nothing else is lost: the state hash,
    the migration count and the freshness all come from the project's own
    ``.theurian/``, which the registry has nothing to do with. A handler that
    turned the whole payload into an error would throw away every field that was
    still perfectly knowable.
    """
    _invoke("init")
    _write_migration(project)
    _invoke("project", "register")

    def _explode(self: object) -> dict[str, dict[str, str]]:
        raise ProjectError("hand-edited between reads", remedy="Delete it and re-register.")

    monkeypatch.setattr(ProjectRegistry, "load", _explode)

    code, payload = _invoke("project", "status")

    assert code == 0
    assert payload["registered"] is None, (
        "the file cannot be searched, and False would claim it was"
    )
    assert "hand-edited between reads" in payload["reason"]
    assert payload["remedy"] == "Delete it and re-register."
    assert payload["migrationCount"] == 1, "a field the registry has nothing to do with survives"
    assert payload["stateHash"], "and so does the one every other command compares against"


def test_unregister_does_not_refuse_an_id_for_its_shape(project: Path) -> None:
    """The escape command has to be able to name what broke the registry.

    This used to assert the opposite, and the opposite was the defect. Parsing
    the argument as a ``ProjectId`` first made this command refuse exactly the
    entries it exists to remove: a registry key is whatever a hand edit left
    behind, and ``theurian project unregister 'Team One/API'`` answered "Check
    the project id with `theurian project list`" -- the listing that had just
    printed it. Removing a key needs no id semantics; only writing one does.

    An id that is not a slug is now looked up like any other key. This one is
    absent from the file, so nothing is removed, at the exit code every other
    absent id already gets.

    The remedy branch this replaced is still pinned, from the side that can
    actually reach it:
    ``test_unregister_names_the_unreadable_file_rather_than_blaming_the_id``
    asserts the registry's own cure wins over ``_context_remedy``'s default.
    """
    _invoke("init")
    _invoke("project", "register")

    code, payload = _invoke("project", "unregister", "Not A Slug")

    assert code == 0, "a key absent from the file is not an error, whatever it looks like"
    assert payload["removed"] is False


# -- migrate ---------------------------------------------------------------


def test_validate_reports_the_application_order(project: Path) -> None:
    _invoke("init")
    _write_migration(project)

    _, validated = _invoke("migrate", "validate")
    assert validated["valid"]
    assert validated["applicationOrder"] == [MIGRATION_ID]


def test_apply_then_status(project: Path) -> None:
    _invoke("init")
    _write_migration(project)

    code, applied = _invoke("migrate", "apply")
    assert code == 0
    assert applied["applied"] == [MIGRATION_ID]
    assert applied["operationsApplied"] == 2

    _, status = _invoke("migrate", "status")
    assert status["applied"] == 1
    assert status["pending"] == 0


def test_apply_is_idempotent(project: Path) -> None:
    _invoke("init")
    _write_migration(project)
    _invoke("migrate", "apply")

    _, second = _invoke("migrate", "apply")
    assert second["applied"] == []
    assert not second["changed"]


def test_status_on_an_unbuilt_state_lists_everything_as_pending(project: Path) -> None:
    _invoke("init")
    _write_migration(project)

    _, status = _invoke("migrate", "status")
    assert not status["stateBuilt"]
    assert status["pending"] == 1
    assert status["pendingIds"] == [MIGRATION_ID]


def test_editing_an_applied_migration_is_fatal(project: Path) -> None:
    """ADR-0016: checked against the previously active state."""
    _invoke("init")
    _write_migration(project)
    _invoke("migrate", "apply")

    path = project / f".theurian/migrations/{MIGRATION_ID}-add-auth-policy.yaml"
    path.write_text(path.read_text() + "  # edited after apply\n")

    code, error = _invoke("migrate", "status")
    assert code == EXIT_STATE_ERROR
    assert "never be edited" in error["error"]


def test_a_revision_conflict_is_reported_not_merged(project: Path) -> None:
    """ADR-0006. The remedy must say a human decides, not the tool."""
    _invoke("init")
    _write_migration(project)
    _invoke("migrate", "apply")

    # No I, L, O, or U: those are excluded from Crockford base32.
    stale = "01K1STAAAA01234567890ABCDE"
    second = "01K1BBBBBB01234567890ABCDE"
    (project / f".theurian/migrations/{second}-conflict.yaml").write_text(
        f"""apiVersion: theurian.dev/v1
id: {second}
createdAt: 2026-08-02T11:00:00+09:00
author: other@example.com
operations:
  - op: upsertRevision
    itemId: architecture.auth-policy
    revisionId: 01K1BBBREV01234567890ABCDE
    expectedRevision: {stale}
    contentFile: ../knowledge/architecture/auth-policy.md
    metadata:
      title: Authentication policy
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/auth-policy.md
"""
    )

    code, error = _invoke("migrate", "apply")
    assert code == EXIT_STATE_ERROR
    assert "Revision conflict" in error["error"]
    assert "does not merge knowledge automatically" in error["remedy"]


def test_a_malformed_migration_names_the_offending_field(project: Path) -> None:
    _invoke("init")
    (project / f".theurian/migrations/{MIGRATION_ID}-bad.yaml").write_text(
        f"""apiVersion: theurian.dev/v1
id: {MIGRATION_ID}
createdAt: 2026-08-02T10:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.auth-policy
"""
    )

    code, error = _invoke("migrate", "validate")
    assert code == EXIT_STATE_ERROR
    assert "is invalid at" in error["error"]


def test_a_naive_timestamp_is_rejected(project: Path) -> None:
    """A naive timestamp compares wrong across a DST boundary, and validity
    windows depend on those comparisons."""
    _invoke("init")
    (project / f".theurian/migrations/{MIGRATION_ID}-naive.yaml").write_text(
        f"""apiVersion: theurian.dev/v1
id: {MIGRATION_ID}
createdAt: "2026-08-02T10:00:00"
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.auth-policy
    kind: architecture
    namespace: backend
    owner: platform-team
"""
    )

    code, error = _invoke("migrate", "validate")
    assert code == EXIT_STATE_ERROR
    assert "offset" in error["error"] or "invalid at" in error["error"]


def test_an_unknown_api_version_is_rejected(project: Path) -> None:
    _invoke("init")
    (project / f".theurian/migrations/{MIGRATION_ID}-future.yaml").write_text(
        f"""apiVersion: theurian.dev/v2
id: {MIGRATION_ID}
createdAt: 2026-08-02T10:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.auth-policy
    kind: architecture
    namespace: backend
    owner: platform-team
"""
    )

    code, error = _invoke("migrate", "validate")
    assert code == EXIT_STATE_ERROR
    assert "invalid at" in error["error"] or "apiVersion" in error["error"]


def test_an_empty_project_validates(project: Path) -> None:
    """A project with no migrations is valid, not broken."""
    _invoke("init")
    code, validated = _invoke("migrate", "validate")
    assert code == 0
    assert validated["migrationCount"] == 0


# -- issue #63: tenantId/aclGroup nothing can yet enforce -------------------


def _migration_with_scope(tenant_id: str | None = None, acl_group: str | None = None) -> str:
    lines = [f"      tenantId: {tenant_id}"] if tenant_id is not None else []
    if acl_group is not None:
        lines.append(f"      aclGroup: {acl_group}")
    insertion = "\n".join((*lines, "      sourceAnchors:"))
    return MIGRATION.replace("      sourceAnchors:", insertion)


def test_default_tenant_and_acl_group_apply_cleanly_end_to_end(project: Path) -> None:
    """Negative control: default scope is not refused, at either command."""
    _invoke("init")
    _write_migration(
        project, migration=_migration_with_scope(tenant_id="local", acl_group="default")
    )

    validate_code, _ = _invoke("migrate", "validate")
    apply_code, applied = _invoke("migrate", "apply")

    assert validate_code == 0
    assert apply_code == 0
    assert applied["applied"] == [MIGRATION_ID]


def test_validate_and_apply_refuse_an_unenforceable_tenant_identically(project: Path) -> None:
    """Issue #63's MEDIUM-1. Nothing below the CLI pinned that `migrate
    validate`'s call to `refuse_unenforceable_scope`, or `migrate apply`'s
    dedicated `except UnenforceableScopeError` clause, actually runs:
    deleting either line from `cli/commands.py` left the full test suite
    green (mutation-verified). Both assertions below must go RED if either
    is removed -- exit code and remedy text are pinned against known values,
    not only against each other, since two commands that agree by both
    falling back to the *same wrong* text would still pass an equality-only
    check.
    """
    _invoke("init")
    _write_migration(project, migration=_migration_with_scope(tenant_id="acme-corp"))

    validate_code, validate_error = _invoke("migrate", "validate")
    apply_code, apply_error = _invoke("migrate", "apply")

    assert validate_code == EXIT_STATE_ERROR
    assert apply_code == EXIT_STATE_ERROR
    assert validate_error["error"] == apply_error["error"]
    assert validate_error["remedy"] == apply_error["remedy"]
    assert "tenantId" in validate_error["remedy"]
    assert "'local'" in validate_error["remedy"]
    assert "#63" in validate_error["remedy"]
    assert validate_error["remedy"] != "Fix the migration set, then retry."


def test_a_refused_apply_leaves_no_database_file(project: Path) -> None:
    """Issue #63 LOW: the refusal is checked before `create_database`, so a
    refused `apply` costs the same as a refused `validate` -- nothing."""
    _invoke("init")
    _write_migration(project, migration=_migration_with_scope(tenant_id="acme-corp"))

    code, _ = _invoke("migrate", "apply")

    assert code == EXIT_STATE_ERROR
    assert list((project / ".theurian/state").glob("*.sqlite")) == []


def test_status_reports_refused_ids_without_gating(project: Path) -> None:
    """Issue #63's MEDIUM-3: `migrate status` is observation, not a gate, so
    it keeps exit 0 -- but the same statically decidable property must be
    visible here too, via `refusedIds`."""
    _invoke("init")
    _write_migration(project, migration=_migration_with_scope(tenant_id="acme-corp"))

    code, status = _invoke("migrate", "status")

    assert code == 0
    assert status["refusedIds"] == [MIGRATION_ID]
    assert status["pendingIds"] == [MIGRATION_ID]


def test_status_reports_no_refused_ids_for_a_clean_set(project: Path) -> None:
    _invoke("init")
    _write_migration(project)
    code, status = _invoke("migrate", "status")
    assert code == 0
    assert status["refusedIds"] == []


def test_an_already_applied_foreign_tenant_gets_a_remedy_that_actually_works(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #63's HIGH-1. A revision applied by an earlier build that did not
    refuse it (only possible on `0.1.0.dev0`/`0.1.0.dev1`) must not get the
    "just edit the field" remedy: editing an already-applied migration's file
    changes its checksum and trips FR-K5's tamper-evidence check instead,
    whose own remedy says to restore the file -- looping the reader between
    two contradictory errors with no documented way out.

    Simulated by disabling the refusal for one seeding `apply`, standing in
    for the earlier, unrefusing build that produced this exact state.

    The seeding patch uses `monkeypatch.context()`, a *separate* scoped
    `MonkeyPatch`, rather than calling `.undo()` on the fixture-provided
    `monkeypatch` directly: the `project` fixture above also patches through
    that same instance (`chdir` into the temp project, `setenv` for
    `THEURIAN_DATA_DIR`), and `.undo()` reverts every patch it has recorded,
    not only this test's two -- it was caught here reverting the working
    directory back to wherever pytest was invoked from mid-test, which is
    exactly the real checkout the isolation rules in this repository exist to
    keep the CLI away from. `migrate validate` never writes, so nothing was
    written by the mistake, but the harness itself must not depend on that.
    """
    _invoke("init")
    _write_migration(project, migration=_migration_with_scope(tenant_id="acme-corp"))

    with monkeypatch.context() as seed:
        seed.setattr(
            "theurian.application.migration_engine.refuse_unenforceable_scope", lambda _ms: None
        )
        seed.setattr("theurian.cli.commands.refuse_unenforceable_scope", lambda _ms: None)
        seed_code, seeded = _invoke("migrate", "apply")
    assert seed_code == 0, "fixture setup failed: the seeding apply itself was refused"
    assert seeded["applied"] == [MIGRATION_ID]

    validate_code, validate_error = _invoke("migrate", "validate")
    apply_code, apply_error = _invoke("migrate", "apply")

    assert validate_code == EXIT_STATE_ERROR
    assert apply_code == EXIT_STATE_ERROR
    assert validate_error["remedy"] == apply_error["remedy"]
    assert ".theurian/state" in validate_error["remedy"]
    assert "FR-K4" in validate_error["remedy"]
    # Must NOT be the unapplied-case remedy: that exact text is what HIGH-1
    # found looping the reader between two contradictory errors, since
    # editing an applied migration's file trips the checksum guard instead.
    assert "then retry" not in validate_error["remedy"]

    # And the working procedure the remedy describes must actually work:
    # edit every offending field to the default, delete state, reapply.
    (project / f".theurian/migrations/{MIGRATION_ID}-add-auth-policy.yaml").write_text(
        _migration_with_scope(tenant_id="local")
    )
    shutil.rmtree(project / ".theurian/state")
    recovered_code, recovered = _invoke("migrate", "apply")
    assert recovered_code == 0
    assert recovered["applied"] == [MIGRATION_ID]


def test_a_never_applied_tenant_gets_the_unapplied_remedy(project: Path) -> None:
    """The other branch of `_unenforceable_scope_remedy`, pinned on its own.

    Both remedy texts mention `tenantId`, `'local'`, and issue #63 -- a check
    that only looks for those substrings cannot tell them apart, and cannot
    catch a mutant that always returns the applied-case remedy. `"then
    retry"` appears only in the unapplied text; `.theurian/state` and
    `FR-K4` appear only in the applied one.
    """
    _invoke("init")
    _write_migration(project, migration=_migration_with_scope(tenant_id="acme-corp"))

    code, error = _invoke("migrate", "validate")

    assert code == EXIT_STATE_ERROR
    assert "then retry" in error["remedy"]
    assert ".theurian/state" not in error["remedy"]
    assert "FR-K4" not in error["remedy"]


_SECOND_MIGRATION_ID = "01K1BBBBBB01234567890ABCDE"
_SECOND_REVISION_ID = "01K1BBBREV01234567890ABCDE"
_SECOND_MIGRATION = f"""apiVersion: theurian.dev/v1
id: {_SECOND_MIGRATION_ID}
createdAt: 2026-08-02T11:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.second-policy
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.second-policy
    revisionId: {_SECOND_REVISION_ID}
    contentFile: ../knowledge/architecture/second-policy.md
    metadata:
      title: Second policy
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/second-policy.md
"""


def test_an_already_applied_foreign_tenant_survives_a_state_hash_shift(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #63's HIGH-1, recurred.

    `_applied_migration_ids` used to check only `database_for(current_hash)`
    -- correct only while the applied migration is the *entire* set. Adding
    one clean, pending migration afterward (issue #63's actual upgrade path,
    not an edge case) shifts the state hash (ADR-0016): the database that
    recorded the foreign tenant sits at the *old* hash's filename, which a
    current-hash-only lookup never finds. The migration then read as
    unapplied, and the routine "edit the field, then retry" remedy was
    printed -- following it edits an *applied* migration, which trips FR-K5's
    checksum guard instead, whose own remedy says to restore the file. Back
    to the scope refusal: the exact loop HIGH-1 was supposed to close.

    `test_an_already_applied_foreign_tenant_gets_a_remedy_that_actually_works`
    above did not catch this because its fixture has zero pending migrations,
    so the current hash always equals the apply-time hash and
    `database_for()` always finds the seeded database by luck.
    """
    _invoke("init")
    _write_migration(project, migration=_migration_with_scope(tenant_id="acme-corp"))

    with monkeypatch.context() as seed:
        seed.setattr(
            "theurian.application.migration_engine.refuse_unenforceable_scope", lambda _ms: None
        )
        seed.setattr("theurian.cli.commands.refuse_unenforceable_scope", lambda _ms: None)
        seed_code, seeded = _invoke("migrate", "apply")
    assert seed_code == 0, "fixture setup failed: the seeding apply itself was refused"
    assert seeded["applied"] == [MIGRATION_ID]

    # A clean, pending migration -- this shifts the state hash (ADR-0016), so
    # `database_for(context.state_hash)` no longer names the database the
    # seeding apply above just built.
    (project / ".theurian/knowledge/architecture/second-policy.md").write_text("# Second\n")
    (project / f".theurian/migrations/{_SECOND_MIGRATION_ID}-second.yaml").write_text(
        _SECOND_MIGRATION
    )

    validate_code, validate_error = _invoke("migrate", "validate")
    apply_code, apply_error = _invoke("migrate", "apply")

    assert validate_code == EXIT_STATE_ERROR
    assert apply_code == EXIT_STATE_ERROR
    assert validate_error["remedy"] == apply_error["remedy"]
    # Must be the applied-case remedy (state-rebuild), not the unapplied one
    # -- this exact selection is what regressed.
    assert ".theurian/state" in validate_error["remedy"]
    assert "FR-K4" in validate_error["remedy"]
    assert "then retry" not in validate_error["remedy"]

    # And the procedure it describes must still work end to end, with the
    # second, clean migration also applying.
    (project / f".theurian/migrations/{MIGRATION_ID}-add-auth-policy.yaml").write_text(
        _migration_with_scope(tenant_id="local")
    )
    shutil.rmtree(project / ".theurian/state")
    recovered_code, recovered = _invoke("migrate", "apply")
    assert recovered_code == 0
    assert sorted(recovered["applied"]) == sorted([MIGRATION_ID, _SECOND_MIGRATION_ID])


# ==========================================================================
# ingest
# ==========================================================================


MARKDOWN_DOC = """---
status: approved
reviewers: [alice]
---

# Authentication policy

Every call carries a signed token.
"""

OPENAPI_DOC = """openapi: 3.1.0
info:
  title: Orders API
  version: "1.0"
paths:
  /orders:
    get:
      operationId: listOrders
      responses:
        "200": {description: OK}
"""


def _write_sources(root: Path) -> None:
    (root / ".theurian/knowledge/architecture/auth.md").write_text(MARKDOWN_DOC)
    (root / ".theurian/specifications/api.yaml").write_text(OPENAPI_DOC)


def test_ingest_normalizes_every_format(project: Path) -> None:
    _invoke("init")
    _write_sources(project)

    code, report = _invoke("ingest")

    assert code == 0
    assert report["ingested"] == 2
    assert report["succeeded"]
    assert {d["parser"] for d in report["documents"]} == {"markdown", "openapi"}


def test_ingest_is_incremental(project: Path) -> None:
    """Touching a file without changing it costs one hash, not a reparse."""
    _invoke("init")
    _write_sources(project)
    _invoke("ingest")

    code, second = _invoke("ingest")

    assert code == 0
    assert second["ingested"] == 0
    assert second["unchanged"] == 2


def test_ingest_reports_a_governed_front_matter_key(project: Path) -> None:
    """ADR-0019: a silently ignored `status: approved` is the case where an
    author believes something is approved and it is not."""
    _invoke("init")
    _write_sources(project)

    _, report = _invoke("ingest")

    codes = {w["code"] for w in report["warnings"]}
    assert codes == {"front-matter-governed-field"}


def test_a_parse_failure_is_reported_without_losing_the_rest(project: Path) -> None:
    _invoke("init")
    _write_sources(project)
    (project / ".theurian/specifications/broken.yaml").write_text("key: [unclosed\n")

    code, report = _invoke("ingest")

    assert code == EXIT_STATE_ERROR, "a partial run is not a clean run"
    assert report["ingested"] == 2, "the good documents still got in"
    assert report["failed"] == 1


def test_a_corrupt_manifest_costs_a_reparse_not_a_failure(project: Path) -> None:
    """The manifest is a derived cache. Refusing to run would let a disposable
    file block the command."""
    _invoke("init")
    _write_sources(project)
    _invoke("ingest")

    (project / ".theurian/cache/ingestion.json").write_text("{ not json")

    code, report = _invoke("ingest")

    assert code == 0
    assert report["ingested"] == 2


def test_ingest_writes_a_manifest_under_the_derived_cache(project: Path) -> None:
    """ADR-0004: the manifest is derived, so it belongs somewhere git-ignored."""
    _invoke("init")
    _write_sources(project)
    _invoke("ingest")

    manifest = project / ".theurian/cache/ingestion.json"
    assert manifest.is_file()
    assert ".theurian/cache/" in (project / ".gitignore").read_text()
