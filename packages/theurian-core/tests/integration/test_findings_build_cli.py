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

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from typing import Any, Final

import pytest
from mcp.server.mcpserver.exceptions import ToolError as SdkToolError
from typer.testing import CliRunner

from theurian.application.project_service import (
    FINDINGS_STORE_ID,
    BuildProvenance,
    ProjectPaths,
    ProjectRegistry,
)
from theurian.cli.findings_commands import _PROVENANCE_REMEDY
from theurian.cli.main import app
from theurian.daemon.runner import build_server
from theurian.domain.review_finding import PARSER_STAMP
from theurian.infrastructure.git.trailer_source import GitTrailerFindingSource
from theurian.infrastructure.sqlite.findings_schema import FINDINGS_DDL
from theurian.infrastructure.sqlite.findings_store import (
    FindingsStoreError,
    SqliteReviewFindingStore,
)
from theurian.mcp.tools import FINDINGS_UNAVAILABLE_REFUSAL

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


def _commit_at_raw_date(root: Path, subject: str, raw_committer_date: str) -> None:
    """Commit with a ``GIT_COMMITTER_DATE`` git echoes verbatim into ``%cI``.

    ``raw_committer_date`` is git's ``@<epoch> <±hhmm>`` form, which lets a test
    author a committer date the ISO ``when`` helper cannot -- in particular the
    max-year value ``9999-12-31T23:00:00-01:00`` whose UTC shift overflows
    ``datetime`` (R1-1). The author date is set to the same value so the commit is
    reproducible.
    """
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Tester",
        "GIT_AUTHOR_EMAIL": "tester@example.com",
        "GIT_COMMITTER_NAME": "Tester",
        "GIT_COMMITTER_EMAIL": "tester@example.com",
        "GIT_AUTHOR_DATE": raw_committer_date,
        "GIT_COMMITTER_DATE": raw_committer_date,
    }
    _git(root, "commit", "--allow-empty", "-m", subject, env=env)


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


@pytest.fixture
def servable_project(project: Path, tmp_path: Path) -> ProjectRegistry:
    """The same clone, taken far enough that ``review.findings`` can resolve it.

    ``findings build`` itself needs no ``.theurian`` and no registration, which is
    why the fixture above provides neither. But the *serving* half of what this
    command produces goes through ``_resolve``, which wants a registry entry, an
    active state pointer, and ADR-0004/SEC-7 provenance on the **canonical**
    state. So the two tests that ask "and is the store this build left behind
    actually served?" need all three to be real, or a refusal would prove only
    that the project could not be resolved.

    No migration is written: ``migrate apply`` over an empty migration set still
    creates the database, publishes ``active.json`` and records the canonical
    state's provenance, which is everything ``_resolve`` reads. Keeping the
    corpus empty also keeps the two tests honest about *which* gate refused --
    there is no knowledge content here for a passing response to have come from.
    """
    for command in (["init"], ["project", "register"], ["migrate", "apply"]):
        code, payload = _invoke(*command)
        assert code == 0, f"fixture premise: `theurian {' '.join(command)}` failed: {payload}"

    return ProjectRegistry.default(tmp_path / "datadir")


def _refuse_provenance_writes(monkeypatch: pytest.MonkeyPatch) -> set[str]:
    """Make the provenance file's write raise, and return the latch that stops it.

    ``BuildProvenance._write`` writes ``provenance.json.tmp`` beside the registry
    and then ``os.replace``s it into place, so refusing that one file name is an
    unwritable ``THEURIAN_DATA_DIR`` as far as the recording path is concerned --
    and nothing else in the run is touched, which matters because the same command
    writes a store, a lock file and a working file on the way there.

    Fault-injected rather than ``chmod``-ed, so the arm is driven on every runner
    including the offline root job, where permission bits refuse nobody: the same
    portability shape ``test_a_write_side_permission_error_is_converted...`` uses
    for ``unlink``.

    The returned set is the latch. Clearing it makes the injector inert without
    ``monkeypatch.undo()``, which would also undo the fixture's ``chdir`` and
    ``THEURIAN_DATA_DIR`` -- a caller that needs to record a build *after* driving
    the failure has to be able to turn the fault off and nothing else.
    """
    refused = {"provenance.json.tmp"}
    real_write_text = Path.write_text

    def _refuse_the_provenance_write(self: Path, *args: object, **kwargs: object) -> int:
        if self.name in refused:
            raise PermissionError(13, "Permission denied")
        return real_write_text(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "write_text", _refuse_the_provenance_write)
    return refused


def _call_findings(registry: ProjectRegistry) -> dict[str, Any]:
    """One ``review.findings`` call, as the payload a client sees."""

    async def invoke() -> Any:
        return await build_server(registry).call_tool("review.findings", {"projectId": "demo"})

    result = asyncio.run(invoke())
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        payload: dict[str, Any] = structured
        return payload
    loaded: dict[str, Any] = json.loads(result.content[0].text)
    return loaded


def _refused_findings(registry: ProjectRegistry) -> str:
    """One ``review.findings`` call that must fail, as the message a client reads."""
    with pytest.raises(SdkToolError) as raised:
        _call_findings(registry)
    return str(raised.value)


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


def test_a_build_records_that_this_installation_produced_the_store(
    project: Path, tmp_path: Path
) -> None:
    """The write half of the provenance gate (ADR-0004, SEC-7, T-19).

    ``review.findings`` stands aside any store this installation has no record of
    building, so a build that landed the file and recorded nothing would ship an
    artifact nothing can serve -- the failure mode is silent in both directions,
    which is why both halves are driven. The record is asserted *absent* first: a
    file that already said yes would make the assertion after the build true for
    the wrong reason.
    """
    provenance = BuildProvenance.default(tmp_path / "datadir")
    root = ProjectPaths.of(project).root
    assert not provenance.has_findings(root, FINDINGS_STORE_ID), (
        "the premise: nothing has recorded a findings build for this project yet"
    )
    _commit(project, "fix: a change (#1)", "Review-Finding: code-review HIGH — a finding")
    _publish(project)

    code, payload = _invoke("findings", "build")

    assert code == 0, payload
    assert provenance.has_findings(root, FINDINGS_STORE_ID), (
        "`findings build` wrote a store this installation does not vouch for, so "
        "`review.findings` will refuse to serve what it just built"
    )


def test_a_build_that_cannot_record_its_provenance_reports_a_failed_build(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-19's build side: a store nothing vouches for is exit 1, not a success.

    ``findings build`` records the build in ``THEURIAN_DATA_DIR`` **inside** the
    same ``try`` that grades every other failure, and until this test landed the
    ``except OSError`` arm that converts a refusal there was driven by nothing: a
    guard no input reaches survives its own deletion. T-19 recorded the gap in as
    many words -- that arm was *asserted by no test, and stated there as read from
    the source rather than as measured* -- and that paragraph now cites this test
    instead.

    What must not happen is a **green** build whose artifact `review.findings`
    will then refuse. That is the silent shape: a caller sees ``built: true``,
    the file is on disk, and every read of it is refused forever with a message
    that says to run the build they just ran. So ``built`` is asserted *absent*,
    not merely false, and the exit code is 1.

    The remedy is the other half. Every other failure this command reports sends
    the reader to ``.theurian/``; this one is the only failure whose precondition
    is a *different* directory, so a handler that reached for the lock's remedy
    would be wrong in the one way a reader cannot recover from.
    """
    _commit(project, "fix: a change (#1)", "Review-Finding: security HIGH — a finding")
    _publish(project)
    _refuse_provenance_writes(monkeypatch)

    code, payload = _invoke("findings", "build")

    assert code == 1, payload
    assert set(payload) == {"error", "remedy"}, (
        f"a provenance-write refusal must arrive as the graded {{error, remedy}} contract, "
        f"not a raw traceback; got {sorted(payload)}"
    )
    assert "built" not in payload, (
        f"a build that could not record itself reported a build: {payload}. The artifact it "
        f"left behind is one `review.findings` refuses, so this has to read as a failure"
    )
    assert payload["remedy"] == _PROVENANCE_REMEDY, payload["remedy"]
    assert "THEURIAN_DATA_DIR" in payload["remedy"], (
        f"the remedy names the wrong directory: the provenance file lives outside the "
        f"repository, so `.theurian/` is not the precondition to fix here: {payload['remedy']}"
    )


def test_the_store_a_failed_provenance_build_left_behind_is_not_served_until_it_is_recorded(
    servable_project: ProjectRegistry, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The load-bearing half: the failed build's exit code describes a real refusal.

    The sibling above holds what the *command* reports. This holds why that report
    is the truthful one -- that the store sitting on disk afterwards is genuinely
    unservable, so "exit 1" is not a pessimistic label on a working artifact.
    Without this, the conversion could be deleted and replaced with a ``built:
    true`` and only a message would change.

    The premise is asserted first and it is the whole point: the failed build
    **did** land the file. A refusal over a store that was never written proves
    nothing about provenance, and that is the vacuous reading this test would
    otherwise have.

    The closing arm is what makes the refusal mean provenance rather than any of
    the three other states that share the constant (SEC-13 keeps one message for
    all of them). Recording the build -- through the same class the command calls,
    with the store's bytes asserted unchanged across the two calls -- turns the
    refusal into rows, so the discriminator was the record in
    ``THEURIAN_DATA_DIR`` and not the file.
    """
    _commit(project, "fix: a change (#1)", "Review-Finding: security HIGH — a finding")
    _publish(project)
    latch = _refuse_provenance_writes(monkeypatch)
    code, payload = _invoke("findings", "build")
    assert code == 1, f"premise: the provenance write has to have been refused, got {payload}"
    store_path = ProjectPaths.of(project).findings_for(FINDINGS_STORE_ID)
    assert store_path.is_file(), (
        "premise: the failed build must have left the store on disk, or `not served` "
        "below is true for the trivial reason that there is nothing to serve"
    )
    landed = store_path.read_bytes()

    refusal = _refused_findings(servable_project)

    assert FINDINGS_UNAVAILABLE_REFUSAL in refusal, refusal
    latch.clear()
    BuildProvenance.for_registry(servable_project).record_findings(
        ProjectPaths.of(project).root, FINDINGS_STORE_ID
    )
    served = _call_findings(servable_project)
    assert served["count"] == 1, (
        f"the same store stayed unservable after its build was recorded, so the refusal "
        f"above was not the provenance gate: {served}"
    )
    assert store_path.read_bytes() == landed, (
        "the store was rewritten between the two calls, so this test compared two "
        "different files rather than one file's provenance"
    )


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


def test_findings_build_blocks_on_the_projects_write_lock_held_by_another_writer(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#404 R1-6 behavioural: findings build contends the project's *own* write lock.

    The AST pin in ``test_adr_0029_claims.py`` proves the command *passes* a
    ``paths.write_lock`` expression as ``write_section``; it cannot prove the lock
    actually excludes a second writer. Here the project's real write lock is held
    by an independent handle (a second open file description -- ``flock`` contends
    per description, so no second OS process is needed), and ``findings build`` is
    driven with its acquisition timeout shortened so the block is a fast, graded
    refusal rather than the shipped 30 s wait (the reviewer measured ~25 s on the
    real timeout).

    The build must be refused with the lock-timeout remedy, proving it tried to
    take *that* file. A mutation pointing the command at any other lock path would
    not contend the held one, so the build would succeed -- which is exactly the
    "different lock file" regression this pins RED, where the substring AST pin
    cannot.
    """
    from theurian.cli import findings_commands
    from theurian.infrastructure.sqlite.connection import WriteLock

    _commit(project, "fix: a change (#1)", "Review-Finding: security HIGH — a finding")
    _publish(project)

    lock_path = ProjectPaths.of(project).write_lock
    # Shorten only the command's own acquisition, so a real contention resolves in
    # a fraction of a second instead of the shipped 30 s.
    monkeypatch.setattr(
        findings_commands,
        "WriteLock",
        lambda path: WriteLock(path, timeout=0.5),
    )

    # An independent handle on the same lock file -- the "other writer".
    other_writer = WriteLock(lock_path, timeout=0.5)
    with other_writer.held():
        code, payload = _invoke("findings", "build")

    assert code == 1, payload
    assert set(payload) == {"error", "remedy"}, payload
    assert "Wait for the other `theurian` process to finish" in payload["remedy"], payload["remedy"]
    # And once the other writer releases, the build succeeds -- so the block was the
    # lock, not a broken build.
    code, payload = _invoke("findings", "build")
    assert code == 0, payload
    assert payload["built"] is True


def test_a_first_build_whose_lock_dir_is_unwritable_is_a_graded_refusal(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R1-2: the write lock's own mkdir/open OSError arrives graded, not as a traceback.

    ``findings build`` composes the project write lock's ``held()`` context manager
    inside its ``try``, but *entering* it runs ``mkdir`` + ``open("w")`` on
    ``.theurian/runtime/`` -- a directory findings build never touched before #404
    added the lock -- and both raise a bare ``OSError``, which the command's
    ``except TheurianError`` does not catch. The read-only-state sibling above runs
    a successful build **first**, which creates ``.theurian/runtime``, so the lock's
    ``mkdir`` is a no-op there and the gap is invisible to it. Here no build has run:
    the lock's own filesystem call is the first write attempted, and it is refused.

    Fault-injected on the lock's ``mkdir`` rather than a ``chmod``, so it drives the
    arm on every runner including the offline root job -- the same portability shape
    the root sibling above uses for ``unlink``.
    """
    _commit(project, "fix: a change (#1)", "Review-Finding: security HIGH — a finding")
    _publish(project)
    real_mkdir = Path.mkdir

    def _refuse_the_runtime_dir(self: Path, *args: object, **kwargs: object) -> None:
        # The lock lives at `.theurian/runtime/write.lock`; its parent is the first
        # directory `held()` creates. Refuse exactly that, nothing else.
        if self.name == "runtime" and self.parent.name == ".theurian":
            raise PermissionError(13, "Permission denied")
        real_mkdir(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "mkdir", _refuse_the_runtime_dir)

    code, payload = _invoke("findings", "build")

    assert code == 1, payload
    assert set(payload) == {"error", "remedy"}, (
        f"the write lock's OS refusal must arrive as the graded {{error, remedy}} contract, "
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


#: How a child process starts this CLI. Not the ``theurian`` console script: it is
#: on ``PATH`` only when the suite runs through ``uv run``, and a test that
#: silently skips on a bare-venv invocation is a test that stops guarding.
_CLI_ENTRY: Final = "from theurian.cli.main import app; app()"

#: Concurrency for the race below: rounds x workers. The pre-#404 shape failed
#: 12 of 48 workers over 12x4 (measured 2026-09-02, this harness's scratch twin),
#: so 3x3 detects it with high probability while costing ~3 seconds. It is not the
#: deterministic pin -- ``test_findings_store.py``'s poller and residue tests are --
#: it is the one check that runs real OS processes against the project's real
#: advisory lock, which no in-process test can do: ``flock`` contends per open
#: file description, so a second acquisition inside one process blocks on itself.
_RACE_ROUNDS: Final = 3
_RACE_WORKERS: Final = 3


def test_concurrent_builds_all_succeed_and_leave_one_complete_store(project: Path) -> None:
    """AC-404-1: real concurrent rebuilds serialise; none crashes, and the store is whole.

    Before #404 the rebuild took no lock and wrote in place under the published
    name, so concurrent invocations tore each other's file: measured on PR #396,
    workers reported ``FindingsStoreError`` in 17-21 of 25 rounds and one iteration
    left a file with **no tables at all** under the publish name. The 12-of-48 /
    48-of-48 comparison in this file's history is the *scratch twin*'s number
    (``4 * 12`` real CLI children, the constant comment above names it), not this
    test's: **this** test runs ``_RACE_ROUNDS * _RACE_WORKERS`` = 3 * 3 = 9
    children, chosen to detect the pre-fix tearing at high probability in ~3 s
    while staying a suite-runnable regression guard. On the pre-fix shape a worker
    here fails the same way (``disk I/O error`` / ``table findings_metadata already
    exists``); on the fixed shape all 9 succeed.

    Every worker must reach a *defined* outcome -- a successful build, or a refusal
    that carries a remedy -- and the survivor must be complete and stamp-current,
    with no working file stranded beside it.

    **The children really do contend the project's advisory write lock**, which
    ``tests/unit/test_connection_claims.py``'s disclaimer now records. Measured
    2026-09-02 by instrumenting the lock's acquire loop in a scratch copy: 9
    acquisitions and 9 blocked attempts across these 3 rounds of 3, so in every
    round two children were waiting while a third held it. What they take is the
    lock object directly rather than the transactional write path, so that path's
    own cross-process wording is untouched by this file.

    This file names neither lock symbol on purpose. Both of
    ``test_connection_claims.py``'s populations are text keys over those two
    tokens, and a file that only *writes* about the lock is a false member of
    either -- the acquisition here happens inside a spawned CLI, which is the
    blindness that module records rather than a population it can read.
    """
    for index in range(6):
        _commit(
            project,
            f"fix: change {index} (#{index})",
            f"Review-Finding: security HIGH — finding {index}",
            f"Review-Finding: bogus-x LOW — a rejected line {index}",
        )
    _publish(project)

    child_env = {**os.environ, "THEURIAN_DATA_DIR": os.environ["THEURIAN_DATA_DIR"]}
    for _round in range(_RACE_ROUNDS):
        workers = [
            subprocess.Popen(  # noqa: S603
                [sys.executable, "-c", _CLI_ENTRY, "findings", "build", "--json"],
                cwd=project,
                env=child_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for _ in range(_RACE_WORKERS)
        ]
        for worker in workers:
            out, err = worker.communicate(timeout=120)
            payload = json.loads(out or err or "{}")
            assert worker.returncode == 0, (
                f"a concurrent `findings build` failed rather than serialising: {payload}"
            )
            assert payload["built"] is True
            assert payload["findings"] == 6
            assert payload["rejected"] == 6

    store = SqliteReviewFindingStore(ProjectPaths.of(project).findings_for("local"))
    dump = store.dump()
    assert len(dump.findings) == 6
    assert len(dump.rejected) == 6
    assert store.is_current()
    assert not store.building_path.exists(), "a working file was stranded beside the published one"


def test_a_max_year_negative_offset_committer_date_is_a_graded_refusal_not_a_crash(
    project: Path,
) -> None:
    """R1-1 (real CLI): a UTC-overflowing committer date must not brick the corpus.

    git emits ``9999-12-31T23:00:00-01:00`` for a crafted committer date, and
    ``astimezone(UTC)`` shifts it into year 10000 and raises ``OverflowError`` -- an
    ``ArithmeticError`` the ``except ValueError`` in ``_parse_committer_date`` did
    not catch. Before the fix, through ``findings build --json``, this reached the
    process boundary as a Rich traceback with an empty ``--json`` stdout and exit 1
    -- the D3-forbidden "one crafted commit bricks the whole corpus" shape, and the
    crafted commit carries no trailer of its own.

    The record must instead be accounted as a rejection while every valid finding
    still loads: the build succeeds, and its report counts the crafted commit
    among the rejected, not among a load that never happened. The min-year
    positive-offset mirror edge git cannot emit (a pre-year-1 epoch is refused), so
    it is driven at the seam in ``test_git_trailer_source.py``.
    """
    _commit(project, "fix: a valid one (#1)", "Review-Finding: security HIGH — a valid finding")
    # A trailer-less crafted commit whose %cI is year 9999 with a -01:00 offset.
    _commit_at_raw_date(project, "chore: far-future negative offset", "@253402300800 -0100")
    _publish(project)

    code, payload = _invoke("findings", "build")

    assert code == 0, payload
    assert payload["built"] is True
    assert payload["findings"] == 1
    # The crafted date-only commit is accounted, not lost, and did not abort.
    assert payload["rejected"] == 1


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
