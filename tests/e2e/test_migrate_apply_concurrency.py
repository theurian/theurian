"""Two concurrent `migrate apply` invocations, driven through the real CLI (#468).

ADR-0018's Decision point 2 records: "Two concurrent `theurian migrate apply`
invocations serialise; the loser waits, then observes the other's work and
becomes a no-op (idempotence, FR-K8)." Measured false by
`theurian-adversarial-review` on PR #446 round 2 (2026-08-31): the advisory
lock (`write_lock`, held inside `write_transaction`) covers the migration
content write only. `create_database` (`cli/commands.py:1328`) runs *before*
`apply_migration_set` opens that transaction, and `write_active_state`
(`cli/commands.py:1403`) runs *after* it closes -- both outside the lock.

Reproduced here against a real pair of OS processes and a real SQLite file,
because the failure is exactly the shape no in-process test can see: two
processes racing one `sqlite3.connect()` on the same path. `create_database`
checks `database_path.exists()` and then unconditionally opens a connection,
which itself creates the file -- so two processes that both pass the outer
`if not database.exists():` check in `cli/commands.py:1327` both reach
`create_database`, and whichever loses the OS-level race hits an unhandled
`sqlite3.OperationalError` there. Confirmed live at commit e9a074d over three
runs of this test (5/16, 6/16, then 8/16 of the sixteen processes across the
eight pairs) with all three shapes #468 records: `OperationalError: database
is locked`, `OperationalError: disk I/O error`, and `OperationalError: table
schema_metadata already exists` -- each escaping Typer as a traceback
(Rich-rendered here, since `rich` is present as an installed extra) carrying
this worktree's absolute source paths, exit 1, with an *empty* `--json`
stdout. A fourth run of `migrate status` (AC-2) also surfaced the deeper fault
underneath: a database left at `schema_version 0` -- corrupted, not merely a
crashed process. See the driving-test report for the pasted output.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

import pytest
from migration_fixtures import body_pin

THEURIAN = shutil.which("theurian")

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(THEURIAN is None, reason="theurian is not installed on PATH"),
]

#: #468's own acceptance baseline: eight pairs of concurrent `migrate apply`
#: runs, measured 4/8 crashing at 249fce1 and 5/16-8/16 processes across three
#: runs of this test at e9a074d (see the module docstring). Kept as a loop
#: count, not a target crash count -- the race is timing-dependent, so a pair
#: that happens not to race must still satisfy the invariant below, and no
#: specific crash tally is asserted.
PAIR_COUNT = 8

MIGRATION_ID = "01K1CCAAAA01234567890ABCDE"
REVISION_ID = "01K1CCAREV01234567890ABCDE"
BODY = "# Concurrency policy\n\nTwo writers must never corrupt one database.\n"

MIGRATION = f"""apiVersion: theurian.dev/v1
id: {MIGRATION_ID}
createdAt: 2026-08-31T10:00:00+09:00
author: engineer@example.com
description: Record the concurrency policy.
operations:
  - op: createItem
    itemId: architecture.concurrency-policy
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.concurrency-policy
    revisionId: {REVISION_ID}
    contentFile: ../knowledge/architecture/concurrency-policy.md
    contentSha256: {body_pin(BODY)}
    metadata:
      title: Concurrency policy
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/concurrency-policy.md
"""


def _init_project(root: Path, env: dict[str, str]) -> None:
    """A fresh Git repository with one committed migration, ready to apply."""
    root.mkdir(parents=True)
    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
        ["git", "config", "commit.gpgsign", "false"],
    ):
        subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603

    assert THEURIAN is not None
    subprocess.run(  # noqa: S603
        [THEURIAN, "init", "--json"], cwd=root, env=env, check=True, capture_output=True, timeout=60
    )
    (root / ".theurian/knowledge/architecture").mkdir(parents=True, exist_ok=True)
    (root / ".theurian/knowledge/architecture/concurrency-policy.md").write_text(BODY)
    (root / f".theurian/migrations/{MIGRATION_ID}-concurrency.yaml").write_text(MIGRATION)


def _apply(root: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    assert THEURIAN is not None
    return subprocess.run(  # noqa: S603
        [THEURIAN, "migrate", "apply", "--json"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _run_concurrent_pair(
    root: Path, env: dict[str, str]
) -> tuple[subprocess.CompletedProcess[str], subprocess.CompletedProcess[str]]:
    """Start two `migrate apply --json` processes as close together as this
    harness allows: both threads block on a two-party barrier immediately
    before the `subprocess.run` call that launches each process, so neither
    can begin its own process start until the other is scheduled and waiting.
    """
    barrier = threading.Barrier(2)
    results: list[subprocess.CompletedProcess[str] | None] = [None, None]

    def worker(slot: int) -> None:
        barrier.wait()
        results[slot] = _apply(root, env)

    threads = [threading.Thread(target=worker, args=(slot,)) for slot in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    first, second = results
    assert first is not None
    assert second is not None
    return first, second


def _is_crash(result: subprocess.CompletedProcess[str]) -> bool:
    """An unhandled exception escaped the CLI instead of a defined outcome.

    Matched on the literal marker a plain Python traceback and Typer's
    Rich-rendered one (installed here as an extra) both carry -- confirmed
    against a live crash of all three #468 shapes, see the module docstring.
    """
    return "Traceback (most recent call last)" in result.stderr


def _classify_success(result: subprocess.CompletedProcess[str]) -> str:
    """``result`` given `result.returncode == 0` and a non-empty stdout.

    ``loser-noop`` additionally requires ``databaseCreated is False`` (#478
    round one): without it, a raced pair where BOTH processes created the
    database -- the HIGH-1 shape the round found, where the loser observed
    nothing and rebuilt the winner's live database instead -- would still
    read as ``{"winner", "loser-noop"}`` and pass AC-1, because neither
    `applied`/`skipped`/`changed` moves when the content each process writes
    is identical. `databaseCreated` is the one field this label was missing
    that actually distinguishes "observed" from "recreated".
    """
    payload: dict[str, Any] = json.loads(result.stdout)
    if payload.get("applied") == [MIGRATION_ID] and payload.get("changed") is True:
        return "winner"
    if (
        payload.get("applied") == []
        and payload.get("skipped") == [MIGRATION_ID]
        and payload.get("changed") is False
        and payload.get("databaseCreated") is False
    ):
        return "loser-noop"
    return f"unexpected-success:{result.stdout.strip()[:200]}"


def _classify_failure(result: subprocess.CompletedProcess[str]) -> str:
    """``result`` given a nonzero `result.returncode`.

    `_fail` (cli/commands.py) writes `{"error": ..., "remedy": ...}` to stderr
    and leaves stdout clean under `--json` -- the CLI's own contract, pinned by
    test_migration_workflow.py::test_errors_go_to_stderr_leaving_stdout_clean.
    """
    if result.stdout != "":
        return f"unexpected-stdout-on-failure:{result.stdout.strip()[:200]}"
    try:
        error_payload: dict[str, Any] = json.loads(result.stderr)
    except json.JSONDecodeError:
        return f"unparseable-failure:{result.stderr.strip()[:200]}"
    if "error" in error_payload and "remedy" in error_payload:
        return "loser-fail-envelope"
    return f"malformed-fail-envelope:{result.stderr.strip()[:200]}"


def _classify(result: subprocess.CompletedProcess[str]) -> str:
    """Which of AC-1's defined outcomes ``result`` is, or a diagnostic label.

    Only ``"winner"``, ``"loser-noop"`` and ``"loser-fail-envelope"`` are
    outcomes AC-1 allows; everything else -- ``"crash"`` included -- is a
    label the assertion below rejects, carrying enough of the payload to
    diagnose it without a second run.
    """
    if _is_crash(result):
        return "crash"
    if result.returncode == 0 and result.stdout.strip():
        return _classify_success(result)
    if result.returncode != 0:
        return _classify_failure(result)
    return (
        f"unclassified:rc={result.returncode} "
        f"stdout={result.stdout.strip()[:120]!r} stderr={result.stderr.strip()[:120]!r}"
    )


#: The only two shapes a pair may land in under AC-1: one process applies, and
#: the other either observed the winner's work (idempotent no-op, FR-K8) or
#: refused cleanly through `_fail`. Order-independent -- either process may
#: win the OS-level race.
_ALLOWED_PAIR_SHAPES = ({"winner", "loser-noop"}, {"winner", "loser-fail-envelope"})


def test_a_raced_migrate_apply_pair_never_crashes_and_leaves_consistent_state(
    tmp_path: Path,
) -> None:
    """AC-1 and AC-2 (#468): a raced `migrate apply` pair serialises or fails
    cleanly, and the project is left consistent either way.

    ADR-0018 records that two concurrent `migrate apply` invocations serialise
    -- the loser observes the winner's work and becomes a no-op (FR-K8) -- and
    that this makes "an editor plugin and a terminal" a safe scenario. Measured
    false: `create_database` and `write_active_state` both write outside
    `write_lock`, so a raced invocation can crash with an unhandled
    `sqlite3.OperationalError` instead of taking either defined outcome (#468).

    Run as `PAIR_COUNT` sequential pairs against fresh, minimal projects (the
    issue's own acceptance baseline) rather than asserting a specific crash
    count: the race window is timing-dependent, so this goes red only when a
    crash shape actually appears, and a pair that happens not to race must
    still satisfy the invariant.
    """
    ac1_failures: list[str] = []
    ac2_failures: list[str] = []
    crash_details: list[str] = []

    for index in range(PAIR_COUNT):
        root = tmp_path / f"pair-{index}" / "demo"
        env = {**os.environ, "THEURIAN_DATA_DIR": str(tmp_path / f"pair-{index}" / "datadir")}
        _init_project(root, env)

        first, second = _run_concurrent_pair(root, env)
        shapes = {_classify(first), _classify(second)}

        # -- AC-1: both processes exit through a defined outcome ------------
        if shapes not in _ALLOWED_PAIR_SHAPES:
            ac1_failures.append(f"pair {index}: {sorted(shapes)}")
            for label, result in (("first", first), ("second", second)):
                if _is_crash(result):
                    crash_details.append(
                        f"--- pair {index} {label} (rc={result.returncode}) ---\n"
                        f"stdout={result.stdout!r}\n"
                        f"stderr (tail):\n{result.stderr[-1500:]}"
                    )

        # -- AC-2: the project is left consistent, win or lose ---------------
        # Deliberately never skipped, even when AC-1 already failed for this
        # pair: a pair with no visible crash can still have corrupted the
        # database underneath (see the driving-test report -- `create_database`
        # and a loser's `_configure` interleave on the same file with no lock),
        # so AC-2 is its own check, not a consequence of AC-1 passing.
        status = subprocess.run(  # noqa: S603
            [str(THEURIAN), "migrate", "status", "--json"],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if status.returncode != 0:
            ac2_failures.append(
                f"pair {index}: `migrate status` failed after the raced pair "
                f"(shapes={sorted(shapes)}): {status.stdout!r} {status.stderr!r}"
            )
            continue

        status_payload = json.loads(status.stdout)
        if not (
            status_payload.get("stateBuilt") is True
            and status_payload.get("applied") == 1
            and status_payload.get("pending") == 0
        ):
            ac2_failures.append(
                f"pair {index}: inconsistent `migrate status` after the raced pair "
                f"(shapes={sorted(shapes)}): {status_payload}"
            )
            continue

        active_pointer_path = root / ".theurian/state/active.json"
        if not active_pointer_path.exists():
            ac2_failures.append(
                f"pair {index}: no active pointer after the raced pair (shapes={sorted(shapes)})"
            )
            continue

        active_pointer = json.loads(active_pointer_path.read_text())
        database_path = root / ".theurian/state" / active_pointer["databaseFilename"]
        if not database_path.exists():
            ac2_failures.append(
                f"pair {index}: the active pointer names {database_path}, which does not "
                f"exist (shapes={sorted(shapes)})"
            )

    detail = "\n\n".join(crash_details) if crash_details else "no crash shape observed"
    assert not ac1_failures and not ac2_failures, (
        f"AC-1 violated in {len(ac1_failures)}/{PAIR_COUNT} pairs (exactly one winner, the "
        f"other a clean no-op or a clean `_fail` envelope expected):\n"
        + "\n".join(ac1_failures)
        + f"\n\nAC-2 violated in {len(ac2_failures)}/{PAIR_COUNT} pairs (state must stay "
        "consistent after the pair, win or lose):\n" + "\n".join(ac2_failures) + f"\n\n{detail}"
    )
