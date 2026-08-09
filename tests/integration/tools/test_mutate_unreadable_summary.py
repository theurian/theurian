"""Issue #50: a truncated run must not be reported KILLED.

Mutation ``C7`` came back KILLED under ``--workers 4``. Its summary line was a
progress marker, ``......[100%]``, with no ``FAILED`` line anywhere in the
output -- a run cut off under parallelism, not a suite that went red. A rerun
alone reported SURVIVED. "The error runs in the direction this harness exists
to avoid": a surviving mutation reported as killed claims something is pinned
when it is not.

Each test spawns a real subprocess: ``_uv()`` resolves to a throwaway shell
script on ``PATH`` that prints exactly the output being tested and exits with
a chosen status, so ``_run_suite``'s real capture and ``_run_one``'s real
verdict logic both run unmodified -- only the external ``uv`` binary is
swapped for a fixture.
"""

from __future__ import annotations

import os
from pathlib import Path

import mutate
import pytest
from mutate_edits import Mutation

pytestmark = pytest.mark.integration


def _install_fake_uv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: str) -> None:
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir(exist_ok=True)
    target = fake_bin / "uv"
    target.write_text(script, encoding="utf-8")
    target.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")


def _options(*, control: bool = False) -> mutate.Options:
    return mutate.Options(
        workers=1,
        fail_fast=True,
        control=control,
        timeout=30,
        keep_trees=False,
        json_path=None,
        work_dir=None,
    )


def test_a_truncated_run_is_reported_error_not_killed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The literal reproduction from issue #50, replayed against a real subprocess.

    Progress marker, no recognisable summary, nonzero exit -- exactly the shape
    a worker killed under parallelism produces. Trusting the exit code alone
    would report KILLED for a mutation nothing actually caught.
    """
    _install_fake_uv(tmp_path, monkeypatch, "#!/bin/sh\nprintf '......[100%%]\\n'\nexit 1\n")
    target = tmp_path / "target.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")
    mutation = Mutation(label="truncated", path="target.py", old="VALUE = 1", new="VALUE = 2")

    outcome = mutate._run_one(tmp_path, mutation, _options(), tmp_path / "uvcache")

    assert outcome.verdict == "ERROR"
    assert outcome.digests, "digests must survive an unreadable-summary ERROR, not just a hang"


def test_a_genuine_failure_is_still_reported_killed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression guard: a real failing summary must not be swept into ERROR too.

    Protects against an overcorrection -- if the recognised-summary check ever
    rejected everything, this would silently stop reporting KILLED at all.
    """
    _install_fake_uv(
        tmp_path, monkeypatch, "#!/bin/sh\nprintf '2 failed, 3 passed in 0.01s\\n'\nexit 1\n"
    )
    target = tmp_path / "target.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")
    mutation = Mutation(label="real-failure", path="target.py", old="VALUE = 1", new="VALUE = 2")

    outcome = mutate._run_one(tmp_path, mutation, _options(), tmp_path / "uvcache")

    assert outcome.verdict == "KILLED"


def test_a_genuine_pass_is_still_reported_survived(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression guard: an ordinary SURVIVED must not be reclassified either."""
    _install_fake_uv(tmp_path, monkeypatch, "#!/bin/sh\nprintf '5 passed in 0.01s\\n'\nexit 0\n")
    target = tmp_path / "target.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")
    mutation = Mutation(label="real-pass", path="target.py", old="VALUE = 1", new="VALUE = 2")

    outcome = mutate._run_one(tmp_path, mutation, _options(), tmp_path / "uvcache")

    assert outcome.verdict == "SURVIVED"


def test_a_truncated_control_run_is_reported_error_not_control_red(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control branch has the same truncation hazard as a mutation run.

    A truncated control misreported as ``control-red`` halts the whole batch
    with "no KILLED verdict here means anything" -- not silently wrong, but
    the wrong diagnosis. It must be ERROR, matching the mutation-branch fix.
    """
    _install_fake_uv(tmp_path, monkeypatch, "#!/bin/sh\nprintf '......[100%%]\\n'\nexit 1\n")
    control = Mutation(label=mutate._CONTROL_LABEL, path=None, old="", new="")

    outcome = mutate._run_one(tmp_path, control, _options(control=True), tmp_path / "uvcache")

    assert outcome.verdict == "ERROR"


def test_a_genuine_control_red_is_still_reported_control_red(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression guard: a real failing baseline must not become ERROR either."""
    _install_fake_uv(
        tmp_path, monkeypatch, "#!/bin/sh\nprintf '1 failed, 5 passed in 0.01s\\n'\nexit 1\n"
    )
    control = Mutation(label=mutate._CONTROL_LABEL, path=None, old="", new="")

    outcome = mutate._run_one(tmp_path, control, _options(control=True), tmp_path / "uvcache")

    assert outcome.verdict == "control-red"
