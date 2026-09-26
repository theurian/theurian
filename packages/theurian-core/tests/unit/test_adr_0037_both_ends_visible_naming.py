"""ADR-0037 decision 4's self-correction: `_both_ends_visible` is `both_ends_visible`, public.

Decision 4's paragraph on the application-layer twin of the visibility gate
names the function `application/index_builder.py::_both_ends_visible` -- its
name when the paragraph was written. Slice S2's amendment corrects the one word
that moved: the function is public since this slice, `both_ends_visible`, no
leading underscore, because `okf_export.py` imports it rather than restating the
gate. The original paragraph is left standing above the amendment rather than
edited in place -- an accepted ADR is corrected by addition, and the amendment's
own sentence ("The paragraph above spells it `_both_ends_visible`") depends on
that paragraph still carrying the stale name.

**Reach.** This holds that the ADR still carries both names in their assigned
roles -- the original paragraph's now-stale private spelling, and the
amendment's correction to the live public one -- and that the function's own
definition agrees: public, importable, with no private twin left behind. It
does not hold that `both_ends_visible` computes the right answer; that is
`test_adr_0037_emission_walk.py`'s and `okf_export.py`'s own import.
"""

from __future__ import annotations

import pytest
from adr_0037_support import assert_the_adr_states

from theurian.application import index_builder

pytestmark = pytest.mark.unit


def test_the_adr_still_carries_the_original_paragraph_the_amendment_points_back_at() -> None:
    """RED means the stale name was edited out of the original paragraph in place.

    An accepted ADR is corrected by amendment, not by rewriting the paragraph it
    corrects: decision 4's original sentence stays exactly as written, and the
    amendment below it names what moved. Editing the original in place -- so it
    already read `both_ends_visible` -- would make the amendment's own "The
    paragraph above spells it `_both_ends_visible`" a citation of a sentence that
    no longer says that.
    """
    assert_the_adr_states(
        "application/index_builder.py::_both_ends_visible",
        because=(
            "This is decision 4's original naming of the application-layer gate, "
            "left in place under the amendment. Slice S2's correction is written as "
            "an addition that names this exact stale spelling; removing it from the "
            "original sentence breaks that citation rather than fixing anything."
        ),
    )
    assert_the_adr_states(
        "The paragraph above spells it _both_ends_visible,",
        because=(
            "The amendment's own pointer back at the sentence above. Without it, a "
            "reader has no way to tell which paragraph the correction is about."
        ),
    )
    assert_the_adr_states(
        "which was its name when this ADR was written and is a name nothing defines now.",
        because=(
            "The amendment states plainly that the private spelling is retired, not "
            "merely superseded. If a future rename brings `_both_ends_visible` back "
            "as something else's name, this sentence is what has to be revisited "
            "rather than silently made true again."
        ),
    )


def test_the_adr_still_names_both_ends_visible_as_the_live_public_spelling() -> None:
    """RED means the amendment's correction reverted, or the symbol moved again.

    This is the amendment's own fix: the one name in decision 4's original
    sentence that moved, and why -- `okf_export.py` imports it rather than
    restating the gate, which is what makes it public rather than a name S2 could
    have kept private.
    """
    assert_the_adr_states(
        "application/index_builder.py::both_ends_visible, public since this slice",
        because=(
            "This is the amendment's correction itself. Losing it leaves 'One name "
            "in the sentence above has moved' with nothing saying what it moved to."
        ),
    )
    assert_the_adr_states(
        "because okf_export.py imports it rather than restating it",
        because=(
            "The reason the rename happened at all: a second module needed the same "
            "gate. Without this clause the amendment asserts a rename with no "
            "motive, and a reader cannot tell whether re-privatizing it is safe."
        ),
    )


def test_both_ends_visible_is_public_and_no_private_twin_survives() -> None:
    """RED means the fact side the amendment cites moved: renamed, or re-privatized.

    `okf_export.py` imports `both_ends_visible` by name, so the function has to be
    both exported and the *only* spelling -- a leftover `_both_ends_visible`
    beside it would mean the export walk and this ADR's citation are reading two
    different things.
    """
    assert "both_ends_visible" in index_builder.__all__, (
        "`index_builder.__all__` no longer exports `both_ends_visible`. ADR-0037's "
        "decision 4 amendment cites it as public since slice S2, because "
        "`okf_export.py` imports it rather than restating the gate; re-privatizing "
        "it breaks that import and this citation together."
    )
    assert hasattr(index_builder, "both_ends_visible"), (
        "`index_builder.both_ends_visible` no longer exists as a module attribute, "
        "though `__all__` still names it."
    )
    assert not hasattr(index_builder, "_both_ends_visible"), (
        "`index_builder._both_ends_visible` exists again. ADR-0037's amendment "
        "records that this was the function's name until slice S2's rename and "
        "calls it 'a name nothing defines now'; a private twin reappearing makes "
        "that sentence false, whether by reverting the rename or by a second, "
        "unrelated function reusing the old name."
    )
