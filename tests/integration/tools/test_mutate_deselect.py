"""``--deselect`` subtracts from the control and the mutations alike (#566).

The batch's whole claim rests on the control and the mutation runs walking the
*same* suite: a KILLED verdict means "this suite goes red under this mutation
and green without it", and a control that walked a different selection is a
baseline for a question nobody asked. So the one option that removes tests has
to remove them from both, and that is not observable from a batch's output --
which is why it is driven here through a real subprocess rather than asserted
about the code.

``uv`` resolves to a throwaway script on ``PATH`` that records the argv it was
handed and prints a summary pytest would recognise, so ``_run_suite``,
``_run_control`` and ``_run_mutation`` all run unmodified.

Also covers what the run *says* it did: a summary reporting "0 SURVIVED" while
three tests were subtracted from every run is a stronger claim than the batch
made, and the summary is what gets pasted into a review comment.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Callable
from pathlib import Path

import mutate
import pytest
from mutate_edits import Mutation
from mutate_run import Options, Outcome, _run_one

pytestmark = pytest.mark.integration

_CONTROL_LABEL = "__control__"

#: Never created or opened -- ``_describe_prepared`` only prints paths.
_A_TREE = Path("theurian-mutate-work") / "tree-0"


def _install_recording_uv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Put a ``uv`` on ``PATH`` that logs its argv and reports a green suite."""
    log = tmp_path / "argv.log"
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir(exist_ok=True)
    script = fake_bin / "uv"
    script.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$@" >> "{log}"\n'
        f'printf "%s\\n" "--END-OF-RUN--" >> "{log}"\n'
        'printf "1 passed in 0.01s\\n"\n',
        encoding="utf-8",
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")
    return log


def _recorded_runs(log: Path) -> list[list[str]]:
    runs: list[list[str]] = []
    current: list[str] = []
    for line in log.read_text(encoding="utf-8").splitlines():
        if line == "--END-OF-RUN--":
            runs.append(current)
            current = []
            continue
        current.append(line)
    return runs


def _execute_returning(*outcomes: Outcome) -> Callable[..., list[Outcome]]:
    """A stand-in for ``_execute``: no trees, no subprocesses, just the verdicts.

    Everything that decides the exit code and the printed remedy runs
    unmodified -- only the expensive tree build is replaced.
    """

    def _execute(mutations: tuple[Mutation, ...], options: Options) -> list[Outcome]:
        del mutations, options
        return list(outcomes)

    return _execute


def _capture_into(
    captured: dict[str, Options],
) -> Callable[[argparse.Namespace, Options], int]:
    """A stand-in for ``_verdict_mode`` that records the ``Options`` ``main`` built."""

    def _verdict_mode(args: argparse.Namespace, options: Options) -> int:
        del args
        captured["options"] = options
        return 0

    return _verdict_mode


def _options(*, deselect: tuple[str, ...] = (), timeout: int = 60) -> Options:
    return Options(
        workers=1,
        fail_fast=True,
        control=False,
        timeout=timeout,
        keep_trees=False,
        json_path=None,
        work_dir=None,
        deselect=deselect,
    )


def test_the_control_and_the_mutation_are_handed_the_same_deselections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A deselection reaching only one of the two silently voids the comparison.

    This is the defect the option could most easily introduce: subtract a test
    from the mutation runs alone and every mutation that test would have caught
    comes back SURVIVED, with a green control standing behind it. Subtract it
    from the control alone and a machine-broken test turns every mutation
    KILLED for a reason that has nothing to do with the mutation.
    """
    log = _install_recording_uv(tmp_path, monkeypatch)
    target = tmp_path / "target.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")
    options = _options(deselect=("tests/e2e/test_bare_install.py::test_it",))
    cache = tmp_path / "uvcache"

    _run_one(tmp_path, Mutation(_CONTROL_LABEL, None, "", ""), options, cache)
    _run_one(tmp_path, Mutation("mutated", "target.py", "VALUE = 1", "VALUE = 2"), options, cache)

    control_argv, mutation_argv = _recorded_runs(log)
    assert control_argv == mutation_argv, "the baseline must walk the selection the mutations do"
    assert "tests/e2e/test_bare_install.py::test_it" in control_argv


def test_an_ordinary_batch_hands_pytest_no_deselection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every verdict this repository has recorded came from a whole-suite run.

    The regression side: a default that subtracted anything would quietly
    weaken every future batch, and no output would say so.
    """
    log = _install_recording_uv(tmp_path, monkeypatch)

    _run_one(tmp_path, Mutation(_CONTROL_LABEL, None, "", ""), _options(), tmp_path / "uvcache")

    (control_argv,) = _recorded_runs(log)
    assert "--deselect" not in control_argv


def test_the_summary_names_every_node_id_it_left_out(capsys: pytest.CaptureFixture[str]) -> None:
    """A verdict block is what survives into a PR, so it must carry the caveat.

    "12 KILLED, 0 SURVIVED" over a run that subtracted three tests is a claim
    the batch did not make. Naming the ids is also what lets a reader tell a
    batch that stepped around a broken environment from one that stepped around
    the test that would have caught the mutation.
    """
    outcome = Outcome(
        label="mutated", verdict="KILLED", suite_green=False, seconds=1.0, summary="1 failed"
    )
    ids = ("tests/e2e/test_bare_install.py::test_it", "tests/e2e/test_daemon.py::test_status")

    mutate._report_summary([outcome], 12.5, _options(deselect=ids))

    printed = capsys.readouterr().out
    assert all(node_id in printed for node_id in ids)


def test_a_summary_with_nothing_deselected_gains_no_extra_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The ordinary batch's output must not move.

    Named separately because the caveat line is only readable as a caveat while
    it is rare; printed on every run it becomes part of the furniture.
    """
    outcome = Outcome(
        label="mutated", verdict="KILLED", suite_green=False, seconds=1.0, summary="1 failed"
    )

    mutate._report_summary([outcome], 12.5, _options())

    assert "deselected" not in capsys.readouterr().out


def test_a_control_that_ran_out_of_clock_is_not_reported_as_a_red_baseline(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The two exits are both 2 and their remedies are opposite.

    A control that hung produces ``ERROR`` -- there is no baseline to be red or
    green -- and until this line existed the run printed nothing at all about
    it, so the reader had a batch of HUNG mutations above a silent exit 2. The
    remedy for a red tree is to fix the tree; the remedy here is more seconds or
    fewer workers, and the run now says which one it is.
    """
    control = Outcome(
        label=_CONTROL_LABEL,
        verdict="ERROR",
        suite_green=None,
        seconds=1800.0,
        summary="timed out after 1800s (no failing test); last line: ......[ 43%]",
        timed_out=True,
    )
    monkeypatch.setattr(mutate, "_execute", _execute_returning(control))
    args = argparse.Namespace(
        spec=None,
        file="tools/mutate.py",
        old="unused-anchor",
        new="unused-replacement",
        old_file=None,
        new_file=None,
        label="timed-out-control",
    )

    exit_code = mutate._verdict_mode(args, _options(timeout=1800))

    printed = capsys.readouterr().out
    assert exit_code == 2
    assert "the clock ran out" in printed
    assert "the unmutated control was RED" not in printed


def test_a_control_that_was_already_red_when_it_timed_out_is_not_told_to_raise_the_timeout(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A suite can print ``FAILED`` and *then* run past the clock (code-review HIGH-1).

    Both a clean timeout and this one set ``timed_out``, and the advisory used
    to read only that field -- so an operator whose baseline was genuinely red
    was told "the tree is not RED, the clock ran out. Raise --timeout", a remedy
    that cannot work and costs the whole timeout again to disprove. Reproduced
    against a real hanging ``uv`` that printed a ``FAILED`` line first.

    ``failures`` is the discriminator, the same one ``_hung_summary`` uses one
    layer down; this pins that the caller reads it too, and that the failing
    test's own name reaches the operator.
    """
    control = Outcome(
        label=_CONTROL_LABEL,
        verdict="ERROR",
        suite_green=None,
        seconds=1800.0,
        summary="......[ 43%]",
        failures=("FAILED packages/x/tests/test_a.py::test_it - AssertionError",),
        timed_out=True,
    )
    monkeypatch.setattr(mutate, "_execute", _execute_returning(control))
    args = argparse.Namespace(
        spec=None,
        file="tools/mutate.py",
        old="unused-anchor",
        new="unused-replacement",
        old_file=None,
        new_file=None,
        label="red-then-hung-control",
    )

    exit_code = mutate._verdict_mode(args, _options(timeout=1800))

    printed = capsys.readouterr().out
    assert exit_code == 2
    assert "raising --timeout will not fix it" in printed
    assert "the tree is not RED" not in printed
    assert "test_a.py::test_it" in printed, "the operator needs the name to act on"


def test_a_control_that_really_went_red_is_still_reported_as_red(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The other direction, so the timeout branch cannot swallow a real failure.

    A guard that reworded every exit-2 into "the clock ran out" would hide the
    case the message has always existed for, and both arrive at the same exit
    code, so nothing else would notice.
    """
    control = Outcome(
        label=_CONTROL_LABEL,
        verdict="control-red",
        suite_green=False,
        seconds=90.0,
        summary="1 failed, 5497 passed in 90.00s",
        failures=("FAILED tests/e2e/test_bare_install.py::test_it - OSError: port in use",),
    )
    monkeypatch.setattr(mutate, "_execute", _execute_returning(control))
    args = argparse.Namespace(
        spec=None,
        file="tools/mutate.py",
        old="unused-anchor",
        new="unused-replacement",
        old_file=None,
        new_file=None,
        label="red-control",
    )

    exit_code = mutate._verdict_mode(args, _options())

    printed = capsys.readouterr().out
    assert exit_code == 2
    assert "the unmutated control was RED" in printed
    assert "--deselect" in printed, "the remedy for a machine-broken test is named at the point"


def test_a_prepared_tree_says_it_is_ignoring_the_deselections(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--prepare-tree`` accepts the flag and does not honour it, so it must say so.

    Not honouring it is right: a prepared tree runs whatever selection its user
    types, and pre-subtracting would change the answer to a question this
    harness is not the one asking. Silence is what was wrong -- the flag was
    accepted, and a reader had no way to learn it did nothing.

    On stderr with the rest of the prepared-tree commentary, because stdout
    carries the tree path alone and ``$(...)`` must capture nothing else.
    """
    ids = ("packages/x/tests/test_a.py::test_it",)

    mutate._describe_prepared(_A_TREE, _A_TREE.parent, None, None, ids)

    captured = capsys.readouterr()
    assert "IGNORED" in captured.err
    assert captured.out == "", "the tree path is stdout's only line in this mode"


def test_a_prepared_tree_with_no_deselections_says_nothing_about_them(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The ordinary prepared tree's commentary must not gain a line.

    The other direction: a note printed every time is furniture, and this one
    only means something when the flag was actually passed and dropped.
    """
    mutate._describe_prepared(_A_TREE, _A_TREE.parent, None, None)

    assert "IGNORED" not in capsys.readouterr().err


def test_the_command_line_carries_repeated_deselections_into_the_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--deselect`` is repeatable, and ``main`` is where that reaches ``Options``.

    Parsed but unforwarded is the failure mode this closes: the invocation and
    the summary would both describe a subtraction that never happened.
    """
    captured: dict[str, Options] = {}
    monkeypatch.setattr(mutate, "_verdict_mode", _capture_into(captured))

    mutate.main(
        [
            "--file",
            "tools/mutate.py",
            "--old",
            "a",
            "--new",
            "b",
            "--label",
            "x",
            "--deselect",
            "tests/a.py::test_one",
            "--deselect",
            "tests/b.py::test_two",
        ]
    )

    assert captured["options"].deselect == ("tests/a.py::test_one", "tests/b.py::test_two")


def test_the_default_timeout_reaching_the_options_is_derived_from_the_workers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The scaled default is inert unless ``main`` actually applies it.

    ``argparse`` cannot compute it, because it depends on another flag; the
    derivation therefore lives in ``main`` and nothing else pins that it runs.
    """
    captured: dict[str, Options] = {}
    monkeypatch.setattr(mutate, "_verdict_mode", _capture_into(captured))

    mutate.main(
        ["--file", "tools/mutate.py", "--old", "a", "--new", "b", "--label", "x", "--workers", "4"]
    )

    assert captured["options"].timeout == mutate._default_timeout(4)
    assert captured["options"].timeout > 1800, "four workers walked past the old flat bound"


def test_an_explicit_timeout_beats_the_worker_count(monkeypatch: pytest.MonkeyPatch) -> None:
    """``--timeout`` must stay the operator's override, not a suggestion.

    The scaled default is delivered by turning ``argparse``'s default into
    ``None``, which is exactly the shape that silently discards an explicit
    value if the branch is written the other way round.
    """
    captured: dict[str, Options] = {}
    monkeypatch.setattr(mutate, "_verdict_mode", _capture_into(captured))

    mutate.main(
        [
            "--file",
            "tools/mutate.py",
            "--old",
            "a",
            "--new",
            "b",
            "--label",
            "x",
            "--workers",
            "4",
            "--timeout",
            "60",
        ]
    )

    assert captured["options"].timeout == 60
