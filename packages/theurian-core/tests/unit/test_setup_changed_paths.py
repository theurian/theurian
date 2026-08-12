"""The order ``changed_paths`` is published in (#47, FR-L2).

``SetupReport.changed_paths`` is accumulated step by step and funnelled through
:func:`theurian.application.setup_service._unique`, whose docstring names
*first-seen order* as the contract. That contract has two readers: an operator
reading a halted run's leftovers on their terminal, and anything that diffs two
runs' ``changedPaths`` arrays.

**Why this is a unit test and not an assertion on a real run.** Every path a
real run accumulates -- ``~/.theurian``, ``auth/mcp-token``, ``env``,
``setup-journal.jsonl`` -- happens to be in sorted order already, measured
across a cold run, a halt on ``env-reference`` and a halt on ``token``. So
``tuple(sorted(set(paths)))`` produces exactly what the correct implementation
produces on every scenario the integration suite can build, and no assertion on
a report can tell the two apart. Only chosen input can.
"""

from __future__ import annotations

import pytest

from theurian.application.setup_service import _unique

pytestmark = pytest.mark.unit

#: Three paths whose first-seen order below is deliberately *not* their sorted
#: order, shaped like the ones setup really accumulates.
_DATA_DIR = "/home/u/.theurian"
_CREDENTIAL = "/home/u/.theurian/auth/mcp-token"
_ENV = "/home/u/.theurian/env"


def test_the_changed_paths_keep_the_order_they_were_first_written_in() -> None:
    """Two orderings of the same three files each keep their own order.

    This is the assertion, and its shape is what makes it work. An
    implementation that ignores input order -- ``tuple(sorted(set(paths)))``, or
    ``tuple(set(paths))`` -- returns *the same* tuple for both calls, because
    both calls hold the same set. Two different expectations cannot both be met
    by one answer, so any order-insensitive implementation fails here whatever
    permutation it happens to emit, and the ``set`` case fails without depending
    on the process's hash seed.

    Asserting a single ordering would not do that: ``sorted`` agrees with
    first-seen order whenever the input arrives sorted, and ``set`` agrees with
    it about one time in six.

    Both mutations were measured against the whole suite and survived, and
    ``tuple(set(paths))`` additionally makes ``changedPaths`` differ between two
    runs on the same machine.
    """
    forwards = (_DATA_DIR, _CREDENTIAL, _ENV)
    backwards = (_ENV, _CREDENTIAL, _DATA_DIR)

    assert _unique(forwards) == forwards
    assert _unique(backwards) == backwards


def test_a_path_two_steps_wrote_is_kept_where_it_was_first_written() -> None:
    """The de-duplication half, on the pair that motivated the funnel.

    ``token`` and ``token-storage`` both declare ``auth/mcp-token``. Collapsing
    the repeat is what stops an operator reading two leftovers to chase where
    there is one -- and the surviving entry belongs at the position of the step
    that created the file, not of the one that re-declared it.
    """
    accumulated = (_DATA_DIR, _CREDENTIAL, _CREDENTIAL, _ENV)

    assert _unique(accumulated) == (_DATA_DIR, _CREDENTIAL, _ENV)


# There is deliberately no test here that ``_unique(()) == ()``. It was written
# and then removed: ``dict.fromkeys``, ``sorted(set(...))``, ``set(...)`` and a
# bare pass-through all answer ``()`` for empty input, so no mutation of this
# function can make it fail. What it looked like it was holding -- a second run
# publishing no changed paths at all (§6.3) -- is held observably, and killably,
# by `test_a_second_run_changes_nothing` in
# ``tests/integration/test_setup_service.py``.
