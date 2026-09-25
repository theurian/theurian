"""The two durable-record corrections docs commit 9bc4226c made, held in place.

ADR-0037's *What this does not close* item 4 said the T-3 threat-model entry
was owed "alongside ADR-0033's and ADR-0035's owed entries" -- wrong, because
ADR-0033's own entry had already landed in Phase B slice B5 (2026-09-19,
[PR #744](https://github.com/theurian/theurian/pull/744)), before ADR-0037
was even written. The slice S3 amendment corrects this: the OKF import is
T-3's **third** arrival route, after the retrieval route and ADR-0033's
candidate route, not its second. `docs/security/threat-model.md`'s T-3 entry
was written to match.

**This module's fact-side reach is limited, and that is stated rather than
implied.** There is no live constant anywhere in the tree for "how many
routes T-3 has" -- a route is a paragraph of prose naming a mechanism, not a
member of an enum or a populated list this suite can enumerate and count.
So the fact side here is **not** a computed count; it is a **cross-document
consistency check**: ADR-0037's amendment cites ADR-0033's *Compliance*
section as recording the candidate-path entry "written", and this module
reads ADR-0033 directly and asserts that citation is still true of it. Two
documents drifting in the same wrong direction together would still pass
every test below -- what these pins catch is one of the two reverting while
the other does not, or the correction being lost from either.
"""

from __future__ import annotations

import pytest
from adr_0037_support import ADR_0037, assert_the_adr_states, collapsed
from threat_model_claims import entry, prose

pytestmark = pytest.mark.unit

#: ADR-0033 is read directly for the cross-document check -- no shared support
#: module reads it today, and this is its only fragment.
_ADR_0033 = ADR_0037.parent / "0033-knowledge-candidate-generation.md"


def test_the_adr_still_states_adr_0033s_entry_was_not_owed_when_it_was_written() -> None:
    """RED means the wrong premise crept back into ADR-0037's own record.

    This is the sentence that makes item 4's *"alongside ADR-0033's and
    ADR-0035's owed entries"* an acknowledged past error rather than a
    standing claim: the amendment block leaves that sentence in place above
    it and corrects it here instead, because what it got wrong was the
    record it read, not the obligation it recorded. Losing this sentence
    reopens exactly that: a reader of item 4 alone would believe ADR-0033's
    T-3 entry is still due.
    """
    assert_the_adr_states(
        "ADR-0033's entry was not owed when this ADR was written.",
        because=(
            "Without this correction, item 4's original 'owed alongside ADR-0033's "
            "and ADR-0035's owed entries' stands as the only record of the fact, and "
            "it has been false since Phase B slice B5 landed on 2026-09-19 -- before "
            "this ADR was even written."
        ),
    )


def test_the_threat_model_still_states_the_okf_import_is_t3s_third_route() -> None:
    """RED means T-3's route count reverted to what ADR-0037 already corrected.

    The OKF import's own arrival-route paragraph names its position expressly
    -- third, after the retrieval route and ADR-0033's candidate route -- which
    is the fact ADR-0037's amendment states and this entry has to agree with.
    Reverting the word alone, with nothing else in the paragraph moved, is
    exactly the drift this pin exists to catch: the surrounding prose (the
    controls, the tests that drive them) would still read correctly while the
    one word that counts routes went back to being wrong.
    """
    fragment = (
        "the okf import is the third route into this entry, added in the okf campaign's slice s3"
    )

    assert fragment in prose(entry("T-3")), (
        f"T-3's entry no longer states:\n\n  {fragment}\n\n"
        f"ADR-0037's amendment (What this does not close, item 4) records the OKF "
        f"import as T-3's third route -- after the retrieval route and ADR-0033's "
        f"candidate route -- because the candidate route landed in Phase B slice B5 "
        f"before this ADR was written. If the route count changed for a real reason "
        f"(a route was added, removed, or renumbered), ADR-0037's amendment and this "
        f"entry move together; if not, restore the word."
    )


def test_the_correction_and_adr_0033s_compliance_still_agree_the_entry_landed() -> None:
    """Cross-document consistency, not a measurement of which document is right.

    ADR-0037's amendment cites ADR-0033's own *Compliance* section as the
    record that the candidate-path T-3 entry landed; this asserts that
    citation is still true by reading ADR-0033 directly. See the module
    docstring for why this is the fact side's whole reach: nothing here
    counts T-3's routes, and both documents could in principle drift the
    same wrong way together without this failing. What it catches is the
    two records disagreeing -- one reverted, the other not.
    """
    assert_the_adr_states(
        "Phase B slice B5 on 2026-09-19 and ADR-0033's own *Compliance* records it as",
        because=(
            "This is ADR-0037's own citation of ADR-0033's record. If it goes, the "
            "correction rests on nothing a reader of this ADR alone can check."
        ),
    )

    landed_fragment = "now names the candidate path as its second injection route and states where"
    adr_0033_text = collapsed(_ADR_0033.read_text(encoding="utf-8"))

    assert collapsed(landed_fragment) in adr_0033_text, (
        f"ADR-0033's Compliance section no longer states:\n\n  {landed_fragment}\n\n"
        f"This is the amendment ADR-0037 cites as recording its T-3 entry 'written'. "
        f"Without it in ADR-0033, ADR-0037's citation points at nothing, and the two "
        f"documents' route counts (ADR-0033: candidate is second; ADR-0037: OKF import "
        f"is third) are no longer both anchored in a landed record."
    )
