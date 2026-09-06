"""The ``--json`` artifact must carry the caveat, not only the verdicts (#566).

A batch that subtracted tests with ``--deselect`` used to write a file holding
the verdicts alone, so "0 SURVIVED" persisted as a stronger claim than the run
made. The only record of the subtraction was a terminal line, which has scrolled
away by the time anyone opens the JSON — and the JSON is what gets attached to a
review, replayed weeks later, and diffed against a rerun.

The document is therefore an object with ``options`` beside ``outcomes`` rather
than the bare list it used to be. Nothing in this repository read the old shape
(``git grep -n '_persist\\|asdict(outcome)' -- tools tests packages`` finds only
the function and its one call site), which is what made the change available.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import mutate
import pytest
from mutate_run import Options, Outcome

pytestmark = pytest.mark.integration


def _options(json_path: Path, *, deselect: tuple[str, ...] = ()) -> Options:
    return Options(
        workers=3,
        fail_fast=True,
        control=True,
        timeout=2460,
        keep_trees=False,
        json_path=json_path,
        work_dir=None,
        with_git=True,
        deselect=deselect,
    )


def _written(tmp_path: Path, deselect: tuple[str, ...] = ()) -> dict[str, Any]:
    target = tmp_path / "results.json"
    outcome = Outcome(
        label="mutated", verdict="KILLED", suite_green=False, seconds=1.0, summary="1 failed"
    )
    mutate._persist([outcome], _options(target, deselect=deselect))
    loaded = json.loads(target.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict), "the artifact is an object, not the bare list it once was"
    return loaded


def test_the_artifact_records_the_node_ids_the_batch_left_out(tmp_path: Path) -> None:
    """The caveat has to outlive the terminal the summary was printed to.

    This is the whole point of the change: a verdict is only readable against
    the selection that produced it, and the JSON is the copy that survives.
    """
    ids = ("packages/x/tests/test_a.py::test_it", "packages/y/tests/test_b.py::test_other")

    document = _written(tmp_path, deselect=ids)

    assert document["options"]["deselect"] == list(ids)


def test_the_artifact_still_carries_every_verdict(tmp_path: Path) -> None:
    """Regression: moving the list under a key must not drop or reshape it.

    Each outcome keeps its own fields — the verdict a reader came for, and the
    ``timedOut``/``failures`` detail that says whether it means anything.
    """
    document = _written(tmp_path)

    outcomes = document["outcomes"]
    assert outcomes == [
        {
            "label": "mutated",
            "verdict": "KILLED",
            "suite_green": False,
            "seconds": 1.0,
            "summary": "1 failed",
            "failures": [],
            "detail": "",
            "digests": {},
            "timed_out": False,
        }
    ]


def test_the_artifact_records_the_flags_that_decide_what_a_verdict_means(
    tmp_path: Path,
) -> None:
    """A verdict replayed without its run's shape is not reproducible.

    ``--with-git`` decides whether four corpus rules ran at all, ``--no-control``
    whether there was a baseline, ``--timeout`` whether a HUNG is a hang or a
    clock. Each one changes how the same verdict should be read.
    """
    document = _written(tmp_path)

    assert document["options"] == {
        "workers": 3,
        "failFast": True,
        "control": True,
        "timeout": 2460,
        "withGit": True,
        "deselect": [],
    }


def test_where_the_run_put_its_files_is_not_recorded_as_a_verdict_qualifier(
    tmp_path: Path,
) -> None:
    """``json_path``, ``work_dir`` and ``keep_trees`` change nothing about a verdict.

    Named so the options block stays the set a reader must weigh rather than a
    dump of the dataclass: a block that grows to hold everything stops being
    read, and ``json_path`` in particular is a machine-local absolute path that
    would travel into every pasted artifact.
    """
    document = _written(tmp_path)

    assert set(document["options"]) == {
        "workers",
        "failFast",
        "control",
        "timeout",
        "withGit",
        "deselect",
    }


def test_no_artifact_is_written_when_none_was_asked_for(tmp_path: Path) -> None:
    """Regression guard: ``--json`` is opt-in and must stay so."""
    outcome = Outcome(
        label="mutated", verdict="KILLED", suite_green=False, seconds=1.0, summary="1 failed"
    )
    options = _options(tmp_path / "unused.json")

    mutate._persist([outcome], Options(**{**vars(options), "json_path": None}))

    assert list(tmp_path.iterdir()) == []
