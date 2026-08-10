"""``Mutation.edits``/``.paths`` are what the composite-edit capability rests on.

Issue #68: a hypothesis like "does this guard still catch the defect once the
walker is weakened" needs two edits landed together and reported as one
verdict, because each edit alone is killed by the other's absence. Every
caller downstream -- ``_apply``, ``_digest_targets``, ``_report_checkout`` --
trusts ``Mutation.edits``/``.paths`` to enumerate every edit a mutation
carries, in order. If that property drifts, a composite mutation silently
degrades to only its first edit landing, or edits land out of order, and
nothing downstream would notice.
"""

from __future__ import annotations

import mutate_edits
import pytest

pytestmark = pytest.mark.unit


def test_a_single_edit_mutation_reports_its_own_file_as_its_only_edit() -> None:
    """The common case -- one ``file``/``old``/``new`` -- must not regress.

    Why it matters: every mutation spec written before composite edits existed
    used this shape, and ``Mutation.edits`` is what ``_apply`` iterates over.
    """
    mutation = mutate_edits.Mutation(label="single", path="a.py", old="X", new="Y")

    edits = mutation.edits

    assert edits == (mutate_edits.Edit(path="a.py", old="X", new="Y"),)


def test_a_composite_mutations_edits_include_the_primary_edit_first() -> None:
    """The primary ``file``/``old``/``new`` fields must lead ``also``, in order.

    Why it matters: ``_apply`` lands edits in the order ``.edits`` yields them,
    and ``_restore_all`` undoes them in reverse. If the primary edit were not
    first, a composite mutation naming a fresh file after weakening another
    would land in an order nobody described.
    """
    also = mutate_edits.Edit(path="b.py", old="P", new="Q")
    mutation = mutate_edits.Mutation(label="composite", path="a.py", old="X", new="Y", also=(also,))

    edits = mutation.edits

    assert edits == (
        mutate_edits.Edit(path="a.py", old="X", new="Y"),
        also,
    )


def test_a_composite_mutations_paths_name_every_file_it_touches() -> None:
    """``.paths`` is what the checkout-integrity check watches (ADR: T-17 class).

    Why it matters: ``_digest_targets``/``_report_checkout`` build their
    in-scope set from ``.paths``. A composite mutation whose second file is
    missing from ``.paths`` would let that file's real-checkout movement go
    unwatched -- exactly the hazard the integrity check exists to catch.
    """
    also = mutate_edits.Edit(path="b.py", old="P", new="Q")
    mutation = mutate_edits.Mutation(label="composite", path="a.py", old="X", new="Y", also=(also,))

    assert mutation.paths == ("a.py", "b.py")


def test_a_control_mutation_carries_no_edits_and_no_paths() -> None:
    """A control has no file to mutate; ``.edits``/``.paths`` must stay empty.

    Why it matters: ``_apply`` refuses a control outright, but ``_digest_targets``
    and ``_report_checkout`` iterate ``.paths`` unconditionally over every
    mutation in a batch, control included. A non-empty ``.paths`` here would
    make the integrity check watch a path nobody chose.
    """
    control = mutate_edits.Mutation(label="__control__", path=None, old="", new="")

    assert control.is_control is True
    assert control.edits == ()
    assert control.paths == ()
