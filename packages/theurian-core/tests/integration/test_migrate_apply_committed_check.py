"""``migrate apply`` refuses a migration that is not committed at HEAD (ADR-0034, T-15).

The merge is this project's approval model (ADR-0013 point 4). ADR-0034 decision 1
makes ``migrate apply`` refuse, by default, a migration file that is not
**committed** -- tracked by git *and* whose applied bytes hash, under the path's
gitattributes, to the id committed at ``HEAD`` -- so a file that was never reviewed
cannot become knowledge, and slice B4's protocol write path cannot slip a migration
past the human. This module drives that refusal through the real CLI, which is where
:func:`_refuse_an_uncommitted_migration` is seated (before ``create_database``,
decision 4).

**These tests are the sole committed proof that the check can fail.** Every other
``migrate apply`` harness in the suite commits its migration first (the
``commit_migrations`` helper), so the check only ever sees ``COMMITTED`` there and
survives its own deletion. This module manages git explicitly and never calls
that helper: the staged and edited cases below are the inputs the check must
refuse, and they go RED the moment the refusal is short-circuited or the predicate
drifts to the weaker *tracked-only* check (proven by mutation in the commit body).

Three driving cases separate the predicate from its two weaker candidates
(decision 1's table); two would not:

* a committed, unmodified migration applies (the baseline);
* a **staged-but-never-committed** migration refuses -- distinguishes the
  predicate from *tracked* (``git add`` with no commit is tracked and reviews
  nothing);
* a **committed-then-edited** migration refuses -- distinguishes it from
  *committed anywhere in history* (the approved bytes are in ``HEAD``, the bytes
  that would apply are not). This is the case that reddens if the implementation
  drifts to the weaker check.

Four further cases pin the blob-id mechanism and its refusal message:

* under ``.gitattributes`` ``*.yaml text eol=lf``, a **CRLF-authored, committed,
  git-clean** migration *applies* -- the id comparison normalizes both sides, so
  the earlier content-hash shape's false ``MODIFIED`` (which refused every text
  migration on the Windows default) is gone (the code-review HIGH's regression);
* the same normalization must not over-normalize: a committed CRLF migration whose
  content is then **genuinely edited** still refuses, so eol handling never masks a
  real drift;
* a **mixed set** -- one migration committed, a later one staged but never
  committed -- refuses and names the uncommitted one, so the loop checks every
  migration rather than stopping at the first committed one;
* the refusal **names the offending file**, so an agent is told which migration to
  commit.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Final

import pytest
from migration_fixtures import body_pin
from typer.testing import CliRunner

from theurian.cli.commands import EXIT_STATE_ERROR
from theurian.cli.main import app

pytestmark = pytest.mark.integration

runner = CliRunner()

MIGRATION_ID: Final = "01K1AAAAAA01234567890ABCDE"
REVISION_ID: Final = "01K1AAAREV01234567890ABCDE"
BODY: Final = "# Authentication policy\n\nEvery call carries a signed token.\n"

#: The migration file's project-relative path, a module constant so the write, the
#: ``git add`` and the edit below cannot drift on the spelling.
MIGRATION_RELPATH: Final = f".theurian/migrations/{MIGRATION_ID}-auth.yaml"

MIGRATION: Final = f"""apiVersion: theurian.dev/v1
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
    contentSha256: {body_pin(BODY)}
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

#: A second migration that ``dependsOn`` the first, so the set's topological order
#: (Kahn's, ULID tie-break) always checks the first before it. It carries a
#: ``createItem`` only -- no body file needed -- and a distinct ULID (Crockford
#: base32: no I/L/O/U). The mixed-set test commits the first and leaves this one
#: staged, so the refusal must reach *this* migration, not stop at the committed one.
SECOND_MIGRATION_ID: Final = "01K1BBBBBB01234567890ABCDE"
SECOND_MIGRATION_RELPATH: Final = f".theurian/migrations/{SECOND_MIGRATION_ID}-second.yaml"
SECOND_MIGRATION: Final = f"""apiVersion: theurian.dev/v1
id: {SECOND_MIGRATION_ID}
createdAt: 2026-08-02T11:00:00+09:00
author: engineer@example.com
dependsOn:
  - {MIGRATION_ID}
operations:
  - op: createItem
    itemId: architecture.second-policy
    kind: architecture
    namespace: backend
    owner: platform-team
"""

_STATE_DATABASE_GLOB: Final = "theurian-state-*.sqlite"


def _git(root: Path, *args: str) -> None:
    """Run one git command in *root*, failing loudly on a non-zero exit."""
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)  # noqa: S603, S607


def _invoke(*args: str) -> tuple[int, dict[str, Any]]:
    """Run ``theurian <args> --json`` in-process and parse the document it emits.

    Deliberately does **not** commit the migration first, unlike the shared
    ``commit_migrations`` harness the rest of the suite uses: what is committed and
    what is not is exactly what each test controls here.
    """
    result = runner.invoke(app, [*args, "--json"], catch_exceptions=False)
    stream = result.stdout if result.exit_code == 0 else (result.stderr or result.stdout)
    return result.exit_code, json.loads(stream) if stream.strip() else {}


def _state_databases(root: Path) -> list[Path]:
    """Every canonical-state database on disk under ``.theurian/state/``."""
    state = root / ".theurian" / "state"
    return sorted(state.glob(_STATE_DATABASE_GLOB)) if state.is_dir() else []


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A registered project whose migration is on disk but **not yet committed**.

    ``HOME`` and ``THEURIAN_DATA_DIR`` are redirected through ``monkeypatch`` so
    nothing touches the developer's machine, and the working directory is the
    project (the CLI resolves a project from ``cwd``). An initial commit lands the
    body and ``.gitignore`` but **not** the migration, so ``HEAD`` exists and each
    test decides the migration's committed status on its own -- which is what lets
    the staged case be *tracked but not at HEAD* rather than *repository with no
    commits at all*.
    """
    root = tmp_path / "demo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "config", "commit.gpgsign", "false")

    monkeypatch.setenv("THEURIAN_DATA_DIR", str(tmp_path / "datadir"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(root)

    assert _invoke("init")[0] == 0
    assert _invoke("project", "register")[0] == 0
    (root / ".theurian" / "knowledge" / "architecture" / "auth-policy.md").write_text(BODY)
    (root / MIGRATION_RELPATH).write_text(MIGRATION)

    # Commit everything except the migration, so HEAD exists and the migration is
    # untracked until a test tracks it. `.theurian/knowledge/` is Git-tracked
    # (ADR-0004); the migration under `.theurian/migrations/` is added per test.
    _git(root, "add", ".gitignore", ".theurian/knowledge")
    _git(root, "commit", "-q", "-m", "init")
    return root


@pytest.fixture
def eol_lf_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A registered project whose migration is **committed CRLF under ``eol=lf``**.

    ``.gitattributes`` declares ``*.yaml text eol=lf``, and the migration is authored
    with CRLF line endings and committed. Git's clean filter normalizes the stored
    blob to LF, so the working-tree bytes (CRLF) differ from the committed blob (LF)
    while ``git status`` reports the tree clean -- the exact shape that the earlier
    content-hash check read as a false ``MODIFIED`` and that refused every text
    migration on the Windows default (``core.autocrlf``). The body ``.md`` is left
    LF, unaffected by the ``*.yaml`` attribute, so its ``contentSha256`` still
    matches. Redirects ``HOME``/``THEURIAN_DATA_DIR`` like ``project``.
    """
    root = tmp_path / "demo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "config", "commit.gpgsign", "false")

    monkeypatch.setenv("THEURIAN_DATA_DIR", str(tmp_path / "datadir"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.chdir(root)

    assert _invoke("init")[0] == 0
    assert _invoke("project", "register")[0] == 0
    (root / ".gitattributes").write_text("*.yaml text eol=lf\n")
    (root / ".theurian" / "knowledge" / "architecture" / "auth-policy.md").write_text(BODY)
    # Author the migration with CRLF endings; git stores it LF (the eol=lf clean
    # filter), so the committed blob and the worktree bytes differ by line ending.
    (root / MIGRATION_RELPATH).write_bytes(MIGRATION.replace("\n", "\r\n").encode("utf-8"))

    _git(root, "add", ".gitattributes", ".theurian/knowledge", MIGRATION_RELPATH)
    _git(root, "commit", "-q", "-m", "init with a CRLF migration under eol=lf")
    return root


def _assert_uncommitted_refusal(
    exit_code: int, payload: dict[str, Any], *, headline: str, reason: str
) -> None:
    """The refusal is the committed-check's own (ADR-0034), not some other guard.

    Pins the exit code, that the message carries the *headline* split by verdict (a
    ``MODIFIED`` file "differs from the version committed at HEAD"; a ``NOT_TRACKED``
    file "is not committed"), the *reason* half distinguishing the two refusing
    predicates, the shared "approval model is the merge" clause, and that the remedy
    names ``--allow-uncommitted`` -- so a green here cannot be an unrelated failure
    (a bad ULID, a missing body) wearing the same exit code.
    """
    assert exit_code == EXIT_STATE_ERROR, payload
    assert headline in payload.get("error", ""), payload
    assert reason in payload["error"], payload
    assert "approval model is the merge" in payload["error"], payload
    assert "--allow-uncommitted" in payload.get("remedy", ""), payload


def test_a_committed_unmodified_migration_applies(project: Path) -> None:
    """The baseline: the one predicate outcome that lets an apply proceed.

    A migration that is tracked and byte-identical to ``HEAD`` is ``COMMITTED``, so
    ``migrate apply`` runs and builds canonical state. Without it the two refusing
    cases below would be evidence only that the command refuses *everything*, not
    that it refuses the uncommitted ones and admits the committed one.
    """
    _git(project, "add", MIGRATION_RELPATH)
    _git(project, "commit", "-q", "-m", "add migration")

    exit_code, payload = _invoke("migrate", "apply")

    assert exit_code == 0, payload
    assert _state_databases(project), (
        "a committed migration must apply and leave a canonical-state database behind"
    )


def test_a_staged_but_never_committed_migration_is_refused(project: Path) -> None:
    """``git add`` with no commit reviews nothing, so the apply refuses (decision 1).

    This is the case that distinguishes the predicate from *tracked*: the file is
    in the index but not at ``HEAD``, so ``git rev-parse --verify --quiet
    HEAD:<path>`` finds no committed id and the check reads ``NOT_TRACKED``. RED if
    the refusal is removed, or if the predicate is relaxed to accept a
    merely-tracked file.
    """
    _git(project, "add", MIGRATION_RELPATH)  # staged, never committed

    exit_code, payload = _invoke("migrate", "apply")

    _assert_uncommitted_refusal(
        exit_code,
        payload,
        headline="is not committed",
        reason="git does not track it at HEAD, so it was never committed",
    )


def test_a_committed_then_edited_migration_is_refused(project: Path) -> None:
    """The bytes that would apply are not the bytes that were approved (decision 1).

    Committed once and edited since: the approved bytes are in ``HEAD``, the
    working-tree bytes the loader reads are not, so their applied id differs from the
    committed id and the check reads ``MODIFIED``. This is the case that reddens if
    the implementation drifts to the weaker *committed-anywhere-in-history*
    predicate -- that predicate would find the approved bytes in history and wave the
    edited file through.
    """
    _git(project, "add", MIGRATION_RELPATH)
    _git(project, "commit", "-q", "-m", "add migration")
    # Edit the working tree after the commit. A trailing comment keeps the document
    # schema-valid (so `resolve_context` still loads it) while changing its bytes,
    # so the applied blob id no longer matches the version at HEAD.
    migration_path = project / MIGRATION_RELPATH
    migration_path.write_text(migration_path.read_text() + "# edited after the commit\n")

    exit_code, payload = _invoke("migrate", "apply")

    _assert_uncommitted_refusal(
        exit_code,
        payload,
        headline="differs from the version committed at HEAD",
        reason="the bytes that would apply are not the committed ones",
    )


def test_a_staged_migration_applies_under_the_escape_hatch(project: Path) -> None:
    """``--allow-uncommitted`` restores the old behaviour for the staged case (decision 2).

    The escape hatch exists for development and recovery. RED if the
    ``if not allow_uncommitted`` skip is removed -- the flag would then refuse the
    same file the default path refuses, and the hatch would be no hatch.
    """
    _git(project, "add", MIGRATION_RELPATH)  # staged, never committed

    exit_code, payload = _invoke("migrate", "apply", "--allow-uncommitted")

    assert exit_code == 0, payload
    assert _state_databases(project), (
        "--allow-uncommitted must let a staged migration apply and build state"
    )


def test_a_committed_then_edited_migration_applies_under_the_escape_hatch(project: Path) -> None:
    """The escape hatch restores the edited case too (decision 2).

    The second refusing predicate applies under the flag, for the same reason the
    staged one does. RED under the same skip-removal mutation.
    """
    _git(project, "add", MIGRATION_RELPATH)
    _git(project, "commit", "-q", "-m", "add migration")
    migration_path = project / MIGRATION_RELPATH
    migration_path.write_text(migration_path.read_text() + "# edited after the commit\n")

    exit_code, payload = _invoke("migrate", "apply", "--allow-uncommitted")

    assert exit_code == 0, payload
    assert _state_databases(project), (
        "--allow-uncommitted must let a committed-then-edited migration apply"
    )


def test_the_refusal_remedy_names_the_flag(project: Path) -> None:
    """The failure mode is a refusal with a runnable remedy that names the flag (decision 4).

    An agent or a script that moved a file into ``.theurian/migrations/`` is told
    what is missing and how to override it deliberately. The remedy naming
    ``--allow-uncommitted`` -- a flag, not a config key -- is what keeps the skip
    visible in the command that ran (decision 2). RED if the remedy stops naming
    the flag, or if the refusal never fires.
    """
    _git(project, "add", MIGRATION_RELPATH)  # staged, never committed

    exit_code, payload = _invoke("migrate", "apply")

    assert exit_code == EXIT_STATE_ERROR, payload
    assert "--allow-uncommitted" in payload["remedy"], payload
    # It is named as a flag on a command, not as a configuration key, which is the
    # whole content of decision 2.
    assert "migrate apply --allow-uncommitted" in payload["remedy"], payload


def test_a_refused_apply_leaves_no_database_behind(project: Path) -> None:
    """A refused apply writes no canonical-state database (decision 4's seat).

    The check is seated before ``create_database``, so an uncommitted migration is
    refused with nothing created -- the property #63/#210/T-21 established for the
    refusals already in that band, extended to this one rather than assumed to
    carry. RED if the refusal is moved after ``create_database`` (or removed): the
    apply would then build state before or instead of refusing.
    """
    _git(project, "add", MIGRATION_RELPATH)  # staged, never committed
    assert not _state_databases(project), "the fixture must start with no state database"

    exit_code, _ = _invoke("migrate", "apply")

    assert exit_code == EXIT_STATE_ERROR
    assert _state_databases(project) == [], (
        "a refused (uncommitted) apply must leave no canonical-state database behind"
    )


# -- the blob-id mechanism: git's normalization is on both sides -------------------


def test_a_committed_crlf_migration_under_eol_lf_applies(eol_lf_project: Path) -> None:
    """A committed, git-clean CRLF migration applies under ``eol=lf`` (code-review HIGH).

    The migration is committed and the tree is clean under its ``*.yaml text eol=lf``
    attribute, but its working-tree bytes are CRLF while the stored blob is LF. The
    earlier content-hash check hashed the CRLF working-tree bytes, found they differ
    from the LF blob, and refused a *committed* migration with a false ``MODIFIED`` --
    which refused every text migration on the Windows ``core.autocrlf`` default. The
    blob-id check hashes the applied bytes through ``git hash-object --path=``, which
    applies the same ``eol=lf`` clean filter git applied on commit, so the applied id
    equals the committed id and the migration applies.

    RED before the fix (and RED under the ``--path=``-dropping mutation recorded in
    the commit body): a raw-content comparison reads ``MODIFIED`` and refuses, so the
    apply exits non-zero and builds no state.
    """
    exit_code, payload = _invoke("migrate", "apply")

    assert exit_code == 0, payload
    assert _state_databases(eol_lf_project), (
        "a committed, git-clean CRLF migration under eol=lf must apply -- its applied "
        "id equals HEAD's once git's normalization is on both sides of the comparison"
    )


def test_a_committed_migration_edited_under_eol_lf_still_refuses(eol_lf_project: Path) -> None:
    """EOL normalization must not over-normalize: a real content edit still refuses.

    The fix normalizes line endings so a CRLF-vs-LF difference is not a refusal. It
    must not go further and mask a genuine content change. The committed CRLF
    migration is edited to change its ``author`` -- a real content drift, not just an
    EOL flip -- while keeping CRLF endings. ``hash-object --path=`` normalizes the
    endings but the changed bytes still hash to a different id than ``HEAD``, so the
    check reads ``MODIFIED`` and refuses.

    RED if the comparison is mutated to always-``COMMITTED`` (``return
    HeadComparison.COMMITTED`` in place of the ``MODIFIED`` verdict): the edited
    migration would then apply, which is the one way the EOL fix could go wrong -- too
    lax. Proven by that mutation in the commit body.
    """
    edited = MIGRATION.replace("author: engineer@example.com", "author: attacker@example.com")
    (eol_lf_project / MIGRATION_RELPATH).write_bytes(edited.replace("\n", "\r\n").encode("utf-8"))

    exit_code, payload = _invoke("migrate", "apply")

    _assert_uncommitted_refusal(
        exit_code,
        payload,
        headline="differs from the version committed at HEAD",
        reason="the bytes that would apply are not the committed ones",
    )
    assert _state_databases(eol_lf_project) == [], (
        "a genuinely edited migration must refuse and build no state, even when only "
        "its line endings are normalized"
    )


# -- the loop checks every migration, and the refusal names the offender -----------


def test_a_mixed_set_refuses_when_a_later_migration_is_uncommitted(project: Path) -> None:
    """A committed migration followed by a staged one refuses, naming the staged one.

    The set is two migrations: the first committed, the second (``dependsOn`` the
    first, so it is checked *after* it) staged but never committed. ``migrate apply``
    must check every migration, reach the uncommitted second one, and refuse -- naming
    it, not the committed first one -- with no state built.

    RED if ``_refuse_an_uncommitted_migration``'s ``if comparison is
    HeadComparison.COMMITTED: continue`` is mutated to ``return``: the loop would stop
    at the committed first migration and never check the second, so the apply would
    proceed on an uncommitted migration -- a real fail-open. Proven by that mutation
    in the commit body.
    """
    (project / SECOND_MIGRATION_RELPATH).write_text(SECOND_MIGRATION)
    _git(project, "add", MIGRATION_RELPATH)
    _git(project, "commit", "-q", "-m", "add the first migration")
    _git(project, "add", SECOND_MIGRATION_RELPATH)  # staged, never committed

    exit_code, payload = _invoke("migrate", "apply")

    _assert_uncommitted_refusal(
        exit_code,
        payload,
        headline="is not committed",
        reason="git does not track it at HEAD, so it was never committed",
    )
    assert SECOND_MIGRATION_RELPATH in payload["error"], (
        "the refusal must name the uncommitted second migration, not stop at the "
        "committed first one"
    )
    assert MIGRATION_RELPATH not in payload["error"], (
        "the committed first migration is not the offender and must not be named"
    )
    assert _state_databases(project) == [], "a refused mixed-set apply builds no state"


def test_the_refusal_names_the_offending_migration_file(project: Path) -> None:
    """The refusal message carries the offending migration's project-relative path.

    An agent or script that dropped a file into ``.theurian/migrations/`` is told
    *which* migration to commit. The label is the migration's ``source_path``.

    RED if ``label = source_path if source_path is not None else ...`` is mutated to a
    constant such as ``label = "unknown-migration"``: the offending file's path would
    vanish from the message and the reader would not know what to commit. Proven by
    that mutation in the commit body.
    """
    _git(project, "add", MIGRATION_RELPATH)  # staged, never committed

    exit_code, payload = _invoke("migrate", "apply")

    assert exit_code == EXIT_STATE_ERROR, payload
    assert MIGRATION_RELPATH in payload["error"], (
        f"the refusal must name the offending migration {MIGRATION_RELPATH!r}; got "
        f"{payload.get('error')!r}"
    )
