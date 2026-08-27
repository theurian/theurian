"""``theurian findings build`` through the real CLI (ADR-0029 phase-2 slice-2).

The composition root (``cli/findings_commands.py``) had no behavioural test at
all before this file: every published report value -- the two counts, the
parser stamp, the store path, ``built`` -- was unpinned, and the two write-path
error conversions FIX-1 (commit 8775b5c) and FIX-2 (commit f75f2d4) landed with
no test driving the escape they closed. Written because a mutation run found
five survivors in ``cli/findings_commands.py`` and ``application/findings_builder.py``
and nothing else (measured @98f11bc, 2026-08-28): dropping the store-id constant,
pointing the trailer source at ``.theurian`` instead of the project root,
reporting ``built: False`` on success, swapping the two counts, and freezing the
parser stamp all left the whole suite green.

Drives a real, hermetic git repository -- a bare origin and a working clone that
serves as the project root, exactly as ``tests/integration/test_findings_builder.py``
builds one for the standalone builder -- through the real Typer CLI
(:mod:`typer.testing`), never through :class:`FindingsBuilder` directly. No
``theurian init`` runs in the fixture: ``findings build`` needs no ``.theurian``
to exist, and its absence is itself load-bearing for one test below.

**Hermetic means every git invocation ignores the developer's real
configuration, not only the ones that set a commit identity.** ``_git`` pins
``GIT_CONFIG_GLOBAL``/``GIT_CONFIG_SYSTEM`` to ``os.devnull`` for every call --
``init`` and ``clone`` read global config too, not only ``commit`` -- because a
round-two review measured that without it, every fixture commit merged
``**os.environ`` and nothing else, so a developer's real ``~/.gitconfig`` with
``commit.gpgsign = true`` made this file's own commits sign with their live
key: a passphrase or hardware-token prompt with no test invoking one.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from theurian.application.project_service import ProjectPaths
from theurian.cli.main import app
from theurian.domain.review_finding import PARSER_STAMP
from theurian.infrastructure.git.trailer_source import GitTrailerFindingSource
from theurian.infrastructure.sqlite.findings_schema import FINDINGS_DDL
from theurian.infrastructure.sqlite.findings_store import (
    FindingsStoreError,
    SqliteReviewFindingStore,
)

pytestmark = pytest.mark.integration

runner = CliRunner()

#: A mode-based refusal cannot be observed running as root -- root ignores every
#: permission bit -- so the read-only-state test is skipped there, the same
#: guard ``test_setup_partial_failure.py`` uses for the identical reason.
_CANNOT_BE_REFUSED_BY_A_MODE = sys.platform == "win32" or os.geteuid() == 0

_NEEDS_SYMLINKS = pytest.mark.skipif(
    sys.platform == "win32", reason="symlinks need privileges on Windows"
)


def _git(root: Path, *args: str, env: dict[str, str] | None = None) -> str:
    """Run one git command as this fixture's isolated actor.

    ``GIT_CONFIG_GLOBAL``/``GIT_CONFIG_SYSTEM`` are pinned to ``os.devnull`` for
    every call here, not only the identity-bearing ``commit`` -- ``init`` and
    ``clone`` read global config too (a ``core.hooksPath`` or a clone template
    would otherwise run under the developer's real settings). Applied after
    ``env`` is merged, so it cannot be overridden by a caller that forgets it;
    the same pattern ``tests/integration/test_propose_cli.py`` and
    ``tests/unit/test_command_population.py`` use for the identical reason.
    """
    result = subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607 - git resolved via PATH, args are test-controlled
        cwd=root,
        check=True,
        capture_output=True,
        env={
            **(env if env is not None else os.environ),
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
        },
    )
    return result.stdout.decode("utf-8")


def _identity_env(when: str) -> dict[str, str]:
    return {
        **os.environ,
        "GIT_AUTHOR_NAME": "Tester",
        "GIT_AUTHOR_EMAIL": "tester@example.com",
        "GIT_COMMITTER_NAME": "Tester",
        "GIT_COMMITTER_EMAIL": "tester@example.com",
        "GIT_AUTHOR_DATE": when,
        "GIT_COMMITTER_DATE": when,
    }


def _commit(root: Path, subject: str, *trailers: str, when: str = "2026-03-01T12:00:00") -> None:
    message = subject if not trailers else subject + "\n\n" + "\n".join(trailers)
    _git(root, "commit", "--allow-empty", "-m", message, env=_identity_env(when))


def _publish(root: Path) -> None:
    """Push ``main`` and refresh ``refs/remotes/origin/main``, the one ref the source reads."""
    _git(root, "push", "origin", "main")
    _git(root, "fetch", "origin")


def _invoke(*args: str) -> tuple[int, dict[str, Any]]:
    result = runner.invoke(app, [*args, "--json"], catch_exceptions=False)
    stream = result.stdout or result.stderr or ""
    return result.exit_code, json.loads(stream) if stream.strip() else {}


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A bare origin and a working clone that is the CLI's project root.

    No ``theurian init``: ``findings build`` reads ``refs/remotes/origin/main``
    through :class:`ProjectPaths.of`, which tolerates a project with no
    ``.theurian`` yet, and every test here authors its own commits before
    invoking the command.
    """
    origin = tmp_path / "origin.git"
    root = tmp_path / "demo"
    _git(tmp_path, "init", "--bare", "-b", "main", str(origin))
    _git(tmp_path, "clone", str(origin), str(root))
    _git(root, "config", "user.name", "Tester")
    _git(root, "config", "user.email", "tester@example.com")

    monkeypatch.setenv("THEURIAN_DATA_DIR", str(tmp_path / "datadir"))
    monkeypatch.chdir(root)
    yield root


def test_a_json_build_reports_the_real_asymmetric_counts_and_the_live_parser_stamp(
    project: Path,
) -> None:
    """Every field the report publishes is driven, and the two counts are asymmetric on purpose.

    Two accepted trailers (across two commits) and one rejected trailer (an
    unknown reviewer token) -- 2 versus 1, not equal -- so a swap of the two
    counts (``builder-report-swaps-the-two-counts``) moves this assertion
    instead of leaving it accidentally unchanged. Kills:
    ``cli-store-id-is-not-local`` (the exact ``storePath`` suffix),
    ``cli-reports-built-false`` (``built`` pinned ``True``),
    ``builder-report-swaps-the-two-counts`` (2 vs 1, not equal),
    ``builder-reports-a-frozen-parser-stamp`` (pinned to the live
    :data:`PARSER_STAMP`, never a literal).
    """
    _commit(project, "fix: first change (#1)", "Review-Finding: code-review HIGH — first finding")
    _commit(
        project,
        "fix: second change (#2)",
        "Review-Finding: security MEDIUM — second finding",
        "Review-Finding: bogus LOW — an unknown reviewer",
    )
    _publish(project)

    code, payload = _invoke("findings", "build")

    assert code == 0, payload
    assert payload["built"] is True
    assert payload["findings"] == 2
    assert payload["rejected"] == 1
    assert payload["parserStamp"] == PARSER_STAMP
    assert payload["storePath"].endswith("theurian-findings-local.sqlite")
    store_path = Path(payload["storePath"])
    assert store_path.is_file(), "the report names a store the build did not actually write"
    assert store_path == ProjectPaths.of(project).findings_for("local")


def test_findings_build_constructs_the_git_source_from_the_project_root(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``findings build`` points the trailer source at ``paths.root``, not ``paths.knowledge_dir``.

    Kills ``cli-reads-git-from-the-knowledge-dir``. ``.theurian`` is pre-created
    here as an ordinary, empty, *existing* directory -- deliberately, because
    ``.theurian`` is a subdirectory of the same git working tree, not a separate
    repository: git commands run with either directory as ``cwd`` read identical
    history, so a test that let ``.theurian`` stay absent (as every other test in
    this file does) would only catch this mutation through a coincidental
    ``FileNotFoundError`` on a nonexistent ``cwd``, not through the property
    itself. Pre-creating the directory removes that coincidence, so the only
    thing this test can catch is the argument the CLI actually constructs the
    source with -- captured directly off the real constructor.
    """
    (project / ".theurian").mkdir()
    captured: list[Path] = []
    real_init = GitTrailerFindingSource.__init__

    def _recording_init(self: GitTrailerFindingSource, repo_root: Path) -> None:
        captured.append(repo_root)
        real_init(self, repo_root)

    monkeypatch.setattr(GitTrailerFindingSource, "__init__", _recording_init)
    _commit(project, "fix: a change (#1)", "Review-Finding: security HIGH — reads the root")
    _publish(project)

    code, payload = _invoke("findings", "build")

    assert code == 0, payload
    assert captured == [project.resolve()], (
        f"the trailer source was constructed with {captured}, expected "
        f"[{project.resolve()}] (the project root, not .theurian)"
    )


def test_a_second_build_over_unchanged_history_reports_the_same_and_stays_logically_identical(
    project: Path,
) -> None:
    """AC-2 at the CLI: idempotency is observable in both the report and the store.

    No field in the published report is time- or run-dependent (the store's own
    ``built_at`` timestamp is recorded inside the SQLite file, never in this
    JSON), so two builds over the same history report byte-identically -- and the
    store's content dump, the stronger check, agrees too.
    """
    _commit(project, "fix: a change (#1)", "Review-Finding: adversarial MEDIUM — a finding")
    _publish(project)

    first_code, first_payload = _invoke("findings", "build")
    assert first_code == 0, first_payload
    store = SqliteReviewFindingStore(Path(first_payload["storePath"]))
    first_dump = store.dump()

    second_code, second_payload = _invoke("findings", "build")

    assert second_code == 0, second_payload
    assert second_payload == first_payload
    assert store.dump() == first_dump


@pytest.mark.skipif(
    _CANNOT_BE_REFUSED_BY_A_MODE,
    reason="POSIX permission bits, and root is refused by none of them",
)
def test_a_read_only_state_directory_is_reported_as_a_graded_write_failure(project: Path) -> None:
    """FIX-1 (8775b5c): a bare ``PermissionError`` from ``unlink`` becomes the JSON contract.

    Measured (round-2 review): the original shape of this test -- a *fresh*
    ``.theurian/state`` (no prior store file) chmod'd read-only before any build
    ever ran -- does not exercise the ``OSError`` arm FIX-1 added at all.
    ``mkdir(exist_ok=True)`` is a no-op on a directory that already exists and
    ``unlink(missing_ok=True)`` is a no-op with nothing to unlink, so the refusal
    only ever surfaces once ``sqlite3.connect`` tries to open the (unwritable)
    file, as ``sqlite3.OperationalError`` -- already a ``sqlite3.Error`` subclass
    the pre-fix ``except sqlite3.Error`` alone caught, so this test drove the
    same arm before and after commit 8775b5c.

    The genuine ``OSError`` arm needs a store file that already exists: the
    directory entry itself is then what ``unlink`` must remove, and *that* raises
    a bare ``PermissionError`` (not a ``sqlite3.Error``) when its parent
    directory is read-only. So a build runs once successfully first -- the store
    file now exists -- and only then is ``.theurian/state`` stripped of write
    permission, before the second build that this test actually drives.
    """
    _commit(project, "fix: a change (#1)", "Review-Finding: security HIGH — a finding")
    _publish(project)
    first_code, first_payload = _invoke("findings", "build")
    assert first_code == 0, (
        f"fixture premise: a store must exist before the read-only state dir is driven, got "
        f"{first_payload}"
    )
    state_dir = project / ".theurian" / "state"
    state_dir.chmod(0o500)
    try:
        code, payload = _invoke("findings", "build")
    finally:
        state_dir.chmod(0o700)

    assert code == 1, payload
    assert set(payload) == {"error", "remedy"}, (
        f"a write failure must arrive as the graded {{error, remedy}} contract, not a raw "
        f"traceback; got {sorted(payload)}"
    )
    assert "writable" in payload["remedy"], payload["remedy"]


def test_a_write_side_permission_error_is_converted_to_the_graded_contract_under_root_too(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FIX-1 (commit 8775b5c): the ``OSError`` conversion is proven on every runner, root included.

    The chmod sibling above cannot run as root -- POSIX permission bits refuse
    nobody there -- so it is skipped on the offline CI job, which runs as root,
    leaving the ``OSError`` arm unproven exactly where the suite always runs.
    ``Path.unlink`` is monkeypatched to raise ``PermissionError`` directly, the
    same fault-injection shape ``tests/integration/test_index_gc_cli.py``'s
    ``test_gc_converts_a_reclaim_permission_error_into_its_own_contract`` uses
    for the identical portability reason. Filtered to the store's own file-name
    prefix so pytest's own bookkeeping ``unlink`` calls are untouched.
    """
    _commit(project, "fix: a change (#1)", "Review-Finding: security HIGH — a finding")
    _publish(project)
    real_unlink = Path.unlink

    def _refuse_to_unlink_the_store(self: Path, *args: object, **kwargs: object) -> None:
        if self.name.startswith("theurian-findings-"):
            raise PermissionError(13, "Permission denied")
        real_unlink(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "unlink", _refuse_to_unlink_the_store)

    code, payload = _invoke("findings", "build")

    assert code == 1, payload
    assert set(payload) == {"error", "remedy"}, (
        f"an OS refusal on the write path must arrive as the graded {{error, remedy}} contract, "
        f"not a raw traceback; got {sorted(payload)}"
    )
    assert "writable" in payload["remedy"], payload["remedy"]


@_NEEDS_SYMLINKS
def test_a_symlinked_store_leaf_escaping_the_tree_is_refused_and_writes_nothing_outside(
    project: Path, tmp_path: Path
) -> None:
    """FIX-1 (commit 8775b5c): ``findings_for``'s ``ProjectError`` is caught, not a raw escape.

    Before the fix, ``paths.findings_for(...)`` sat outside the command's own
    ``try``, so the ``ProjectError`` it can raise (``_contained`` refusing an
    escaping symlink) bypassed the ``except TheurianError`` handler. The store
    leaf is replaced by a symlink pointing outside the project *before* the
    build runs, so ``findings_for`` refuses before anything is written -- and
    the outside directory is asserted empty, not merely that the command failed.
    """
    _commit(project, "fix: a change (#1)", "Review-Finding: security HIGH — a finding")
    _publish(project)
    outside = tmp_path / "outside"
    outside.mkdir()
    state_dir = project / ".theurian" / "state"
    state_dir.mkdir(parents=True)
    (state_dir / "theurian-findings-local.sqlite").symlink_to(outside / "leak.sqlite")

    code, payload = _invoke("findings", "build")

    assert code == 1, payload
    assert set(payload) == {"error", "remedy"}, (
        f"an escaping store path must arrive as the graded {{error, remedy}} contract, not a "
        f"raw traceback; got {sorted(payload)}"
    )
    assert "outside" in payload["error"], payload["error"]
    assert list(outside.iterdir()) == [], "the escape wrote something outside the project tree"


def test_dump_raises_on_a_half_built_store_instead_of_reading_it_empty(tmp_path: Path) -> None:
    """FIX-2 (f75f2d4): a half-built store -- schema landed, no metadata row -- is not read empty.

    ``replace_all``'s ``executescript`` commits the DDL before the data
    transaction that lands the rows and the metadata row share, so a crash in
    that exact window leaves well-formed, empty tables and no metadata row.
    There is no way to crash ``replace_all`` mid-transaction from outside, so
    the window is built directly: the DDL runs, nothing else does.
    """
    store_path = tmp_path / "state" / "theurian-findings-half.sqlite"
    store_path.parent.mkdir(parents=True)
    with closing(sqlite3.connect(store_path)) as connection:
        connection.executescript(FINDINGS_DDL)
        connection.commit()

    store = SqliteReviewFindingStore(store_path)

    assert store.stamp() is None, "an unreadable stamp is the correct answer for a half-built file"
    with pytest.raises(FindingsStoreError, match="half-built"):
        store.dump()
