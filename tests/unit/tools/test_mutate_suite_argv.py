"""``--deselect`` must reach the pytest command line, and nothing else may (#566).

The harness deliberately has no ``--tests``: a narrowed verdict run assumes the
answer to the question a mutation harness exists to ask. ``--deselect`` is the
one subtraction it allows, for a test the *machine* cannot pass -- one binding a
port a resident daemon already owns, one wanting a network. Without it such a
test turns the control ``control-red`` and voids every verdict in the batch, so
the option is not measured against a narrower run but against no run at all.

These pin the argv ``_suite_argv`` builds. The stronger claim -- that the
control run and the mutation runs get the *same* argv -- is driven through a
real subprocess in
``tests/integration/tools/test_mutate_deselect.py``.
"""

from __future__ import annotations

import pytest
from mutate_run import Options, _suite_argv

pytestmark = pytest.mark.unit


def _options(*, deselect: tuple[str, ...] = (), fail_fast: bool = True) -> Options:
    return Options(
        workers=1,
        fail_fast=fail_fast,
        control=False,
        timeout=30,
        keep_trees=False,
        json_path=None,
        work_dir=None,
        deselect=deselect,
    )


def test_a_deselected_node_id_reaches_the_pytest_command_line() -> None:
    """The option is inert unless the id is actually handed to pytest.

    An option parsed, stored and never forwarded would leave the control red
    exactly as before while the invocation and the summary both claim the test
    was skipped -- a batch that reads as narrowed and is not.
    """
    argv = _suite_argv("/usr/bin/uv", _options(deselect=("tests/e2e/test_x.py::test_y",)))

    assert "--deselect" in argv
    assert argv[argv.index("--deselect") + 1] == "tests/e2e/test_x.py::test_y"


def test_each_deselected_node_id_gets_its_own_flag() -> None:
    """pytest takes one node id per ``--deselect``; it does not split a list.

    Joining several ids into one argument makes pytest deselect a node whose id
    is the joined string -- which matches nothing -- so every id after the first
    would silently stay in the run while the summary says all of them are gone.
    """
    ids = ("tests/a.py::test_one", "tests/b.py::test_two", "tests/c.py::test_three")

    argv = _suite_argv("/usr/bin/uv", _options(deselect=ids))

    pairs = [(item, argv[i + 1]) for i, item in enumerate(argv) if item == "--deselect"]
    assert pairs == [("--deselect", node_id) for node_id in ids]


def test_an_ordinary_run_deselects_nothing() -> None:
    """The default must leave the command line the harness has always used.

    This is the regression side of the option: every verdict this repository has
    recorded was produced by a run with the whole suite selected, and a stray
    default here would silently subtract from all of them.
    """
    argv = _suite_argv("/usr/bin/uv", _options())

    assert "--deselect" not in argv


def test_the_deselections_precede_the_fail_fast_flag_and_the_uv_prefix_is_intact() -> None:
    """``-x`` stays last and ``uv run --frozen --no-sync pytest`` stays first.

    ``_PYTEST_ARGS`` documents that the plugin flags come before ``-q`` on
    purpose -- a run leading with ``-q`` volunteers itself for everyone else's
    ``pkill -f "pytest -q"``. Inserting the deselections must not disturb that
    ordering, and ``--frozen``/``--no-sync`` are what keep a tree from
    re-resolving mid-batch.
    """
    argv = _suite_argv("/usr/bin/uv", _options(deselect=("tests/a.py::test_one",)))

    assert argv[:5] == ["/usr/bin/uv", "run", "--frozen", "--no-sync", "pytest"]
    assert argv[5:7] == ["-p", "no:randomly"]
    assert argv[-1] == "-x"
    assert argv.index("--deselect") < argv.index("-x")
