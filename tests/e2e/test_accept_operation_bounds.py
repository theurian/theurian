"""#306: ``propose accept``'s operation-count cap fires before any body is read.

Reproduces the measured shape directly: many ``upsertRevision`` operations
naming one shared ``contentFile``, well above the cap
``proposal_service.MAX_UPSERT_OPERATIONS`` enforces. Before the fix, resident
memory grew ~0.53 MB per operation (measured on main, a 512 KB shared body) --
the size cap on a single file held while the *aggregate* did not, and the
refusal that eventually fired came after ``_body_moves`` had already read every
copy. This drives the real installed binary and measures the child process's
own peak RSS, because that is the property the fix claims and a unit test
cannot observe: not merely that the migration is refused, but that it is
refused *before* the cost is spent.

The deterministic, CI-run twin of this proof --
``test_accept_refuses_a_proposal_past_the_operation_cap`` in
``test_proposal_service.py`` -- monkeypatches ``_body_moves`` to assert it is
never entered once the cap is exceeded. This file measures the same property
by an independent method (real memory, not a patched call), and it is marked
``e2e`` because CI's own jobs run every other suite under ``-m "not e2e"``
(``.github/workflows/core.yml``) -- this one is exercised by the mandated full
local gate (``uv run pytest -q``, no marker filter) and by a developer running
``-m e2e`` directly.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

THEURIAN = shutil.which("theurian")

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(THEURIAN is None, reason="theurian is not installed on PATH"),
]

#: Ten times the shipped cap (``proposal_service.MAX_UPSERT_OPERATIONS`` = 500).
#: Not imported from the source constant -- this file drives only the installed
#: binary, never the Python package in-process -- so the margin is deliberately
#: wide: any cap this project would plausibly ship is well below 5,000, and the
#: pre-fix growth at this count (~2.6 GB at a comparable body size) makes the
#: flat/not-flat distinction robust to ordinary process-measurement noise.
OPERATION_COUNT_WELL_ABOVE_THE_CAP = 5_000

#: Large enough that a resident copy per operation is visible over normal
#: process noise (tens of MB), small enough that 5,000 copies of the *document
#: entry naming it* stay under the migration file's own ``MAX_YAML_BYTES`` cap
#: (4 MiB) -- the padded migration must still be readable as one file, or this
#: test would measure that cap instead of the one it targets.
SHARED_BODY_BYTES = 256 * 1024

#: How far a peak-RSS measurement may exceed the 1-op baseline and still count
#: as "flat". Generous against interpreter/venv startup noise and page-cache
#: state, and two orders of magnitude below the ~2.6 GB the unfixed code would
#: add at ``OPERATION_COUNT_WELL_ABOVE_THE_CAP``.
FLAT_SLACK_KIB = 250_000

_ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"


def _git_project(root: Path) -> None:
    """A real Git repository with ``theurian init`` already run.

    Mirrors ``tests/e2e/test_migration_workflow.py``'s ``project`` fixture:
    only ``THEURIAN_DATA_DIR`` is redirected. ``propose``/``propose accept``
    write nothing under ``$HOME`` and register no service (unlike ``setup``),
    so nothing here needs the broader redirection that command would.
    """
    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
        ["git", "config", "commit.gpgsign", "false"],
    ):
        subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603
    assert THEURIAN is not None
    subprocess.run(  # noqa: S603
        [THEURIAN, "init"], cwd=root, check=True, capture_output=True, text=True, timeout=30
    )


def _draft_naming(root: Path, body: Path) -> tuple[str, Path]:
    """Draft one proposal naming ``body``; return ``(proposal_id, migration_file)``."""
    assert THEURIAN is not None
    subprocess.run(  # noqa: S603
        [
            THEURIAN,
            "propose",
            "--item-id",
            "architecture.repro-306",
            "--title",
            "repro",
            "--kind",
            "architecture",
            "--owner",
            "platform",
            "--author",
            "repro",
            "--description",
            "d",
            "--body-file",
            str(body),
            "--authored-here",
            "--agent-id",
            "a",
            "--task-id",
            "t",
            "--model",
            "m",
            "--reasoning",
            "r",
        ],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    proposals = sorted(p for p in (root / ".theurian" / "proposals").iterdir() if p.is_dir())
    assert len(proposals) == 1, f"expected exactly one drafted proposal, found {proposals}"
    proposal_dir = proposals[0]
    migration_files = list(proposal_dir.glob("*.yaml"))
    assert len(migration_files) == 1, (
        f"expected exactly one migration file, found {migration_files}"
    )
    return proposal_dir.name, migration_files[0]


def _pad_with_shared_content_file(migration_file: Path, extra_ops: int) -> None:
    """Clone the migration's own ``upsertRevision`` op ``extra_ops`` times.

    Every clone names the SAME ``contentFile`` as the base operation -- the
    exact amplification shape #306 measured -- with a unique ``revisionId`` so
    the document is not one operation trivially repeated. None of this needs
    to be schema-valid: the operation-count cap this test targets runs on the
    raw parsed document, before schema validation is ever reached.
    """
    document = yaml.safe_load(migration_file.read_text(encoding="utf-8"))
    operations = document["operations"]
    base = next(op for op in operations if op.get("op") == "upsertRevision")
    clones = []
    for i in range(extra_ops):
        clone = dict(base)
        clone["metadata"] = dict(base["metadata"])
        rid = str(base["revisionId"])
        clone["revisionId"] = rid[:-2] + _ALPHABET[(i // 32) % 32] + _ALPHABET[i % 32]
        clones.append(clone)
    document["operations"] = [*operations, *clones]
    migration_file.write_text(
        yaml.safe_dump(document, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )


def _peak_rss_kib(argv: list[str], cwd: Path) -> tuple[int, int, str, str]:
    """Run ``argv`` as the lone child of a throwaway interpreter; return its own peak RSS.

    Not ``resource.getrusage(RUSAGE_CHILDREN)`` read directly by this test
    process: that counter is a running *maximum* over every child the process
    has ever reaped, so measuring the small baseline first and the large case
    second would report the large case's figure for both. A fresh interpreter
    with exactly one child sidesteps that -- its own ``RUSAGE_CHILDREN`` can
    only be that one child's.

    Returns ``(peak_kib, returncode, stdout, stderr)`` of the *inner* process.
    """
    script = (
        "import json, resource, subprocess, sys\n"
        "result = subprocess.run(sys.argv[1:], capture_output=True, text=True)\n"
        "usage = resource.getrusage(resource.RUSAGE_CHILDREN)\n"
        "sys.stdout.write(json.dumps({\n"
        "    'returncode': result.returncode,\n"
        "    'ru_maxrss': usage.ru_maxrss,\n"
        "    'stdout': result.stdout,\n"
        "    'stderr': result.stderr,\n"
        "}))\n"
    )
    completed = subprocess.run(  # noqa: S603
        [sys.executable, "-c", script, *argv],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    payload: dict[str, Any] = json.loads(completed.stdout)
    raw_rss: int = payload["ru_maxrss"]
    # macOS reports `ru_maxrss` in bytes; Linux (glibc and musl both) reports
    # it in KiB. Normalised to KiB here so `FLAT_SLACK_KIB` reads the same on
    # both -- the CI matrix this project targets includes both.
    peak_kib = raw_rss // 1024 if sys.platform == "darwin" else raw_rss
    return peak_kib, payload["returncode"], payload["stdout"], payload["stderr"]


def test_accept_peak_rss_stays_flat_well_above_the_operation_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-7: N operations naming one body, well above the cap, cost no more than one.

    Two independent proposals, each in its own project: a 1-op baseline that
    is accepted successfully, and an
    ``OPERATION_COUNT_WELL_ABOVE_THE_CAP``-op proposal that is refused. If the
    refusal fires before ``_body_moves`` runs, peak RSS for the large case
    stays within ``FLAT_SLACK_KIB`` of the baseline regardless of how many
    operations name the shared body -- that is the property #306 asks for,
    not merely that the oversized migration is eventually refused.
    """
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(tmp_path / "datadir"))
    body = tmp_path / "body.md"
    body.write_bytes(b"x" * SHARED_BODY_BYTES)

    baseline_root = tmp_path / "baseline"
    baseline_root.mkdir()
    _git_project(baseline_root)
    baseline_id, _ = _draft_naming(baseline_root, body)
    assert THEURIAN is not None
    baseline_kib, baseline_rc, _, baseline_stderr = _peak_rss_kib(
        [THEURIAN, "propose", "accept", baseline_id, "--json"], baseline_root
    )
    assert baseline_rc == 0, f"the 1-op baseline must accept cleanly: {baseline_stderr}"

    large_root = tmp_path / "large"
    large_root.mkdir()
    _git_project(large_root)
    large_id, migration_file = _draft_naming(large_root, body)
    starting_ops = len(yaml.safe_load(migration_file.read_text(encoding="utf-8"))["operations"])
    _pad_with_shared_content_file(migration_file, OPERATION_COUNT_WELL_ABOVE_THE_CAP - starting_ops)
    migration_bytes = migration_file.stat().st_size
    assert migration_bytes < 4 * 1024 * 1024, (
        f"the padded migration is {migration_bytes} bytes -- large enough to trip "
        "MAX_YAML_BYTES before the operation-count cap, which would measure the wrong guard"
    )

    large_kib, large_rc, _, large_stderr = _peak_rss_kib(
        [THEURIAN, "propose", "accept", large_id, "--json"], large_root
    )
    assert large_rc != 0, "a migration this far past the cap must be refused"
    error = json.loads(large_stderr)
    assert "operations" in error["error"], error
    assert error["remedy"], "the refusal must name a remedy"

    assert large_kib <= baseline_kib + FLAT_SLACK_KIB, (
        f"peak RSS grew from {baseline_kib} KiB (1 op) to {large_kib} KiB "
        f"({OPERATION_COUNT_WELL_ABOVE_THE_CAP} ops naming one shared body) -- "
        "the operation cap did not fire before _body_moves could materialise the bodies"
    )
