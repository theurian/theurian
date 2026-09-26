"""T-27's SEC-15 safety-triple gap, held to the entry's own wording.

``docs/security/threat-model.md``'s T-27 records that a distributed OKF bundle
carries none of SEC-15's safety triple (``contentClassification``,
``mayContainInstructions``, ``executable``) and names that gap **unowned**
rather than deferred to a milestone -- there is no issue tracking it, because
carrying the triple would be a producer-extension decision ADR-0037's decision
7 projection does not take. That is a claim a reader takes on trust unless
something keeps the sentence honest as the codec's key vocabulary grows.

**Reach.** This holds the entry's own two sentences: that no bundle file
carries the triple, and that the gap is unowned. It does not measure the codec
-- ``test_okf_codec.py``'s
``test_the_safety_triple_key_names_are_disjoint_from_every_bundle_key`` is the
fact half, over the live ``mcp.results.SAFETY`` constant and
``okf_codec.THEURIAN_KEYS``/``OKF_KEYS`` -- and it does not re-run the entry's
own ``git grep`` over the four export modules; that command is quoted in the
entry and is not a property this suite's own text scan can verify.
"""

from __future__ import annotations

import pytest
from threat_model_claims import entry, prose

pytestmark = pytest.mark.unit

_THREAT_ID = "T-27"


def test_t27_still_states_no_bundle_file_carries_the_triple_and_the_gap_is_unowned() -> None:
    """RED means either half of the gap's own record went missing.

    Two sentences, and each hides a different loss. Losing "no bundle file
    carries any of the three" leaves the disjointness `test_okf_codec.py` checks
    unexplained -- a reader would not know why the codec's key set matters to
    this entry at all. Losing "unowned rather than deferred" turns a gap nobody
    is tracking into one that reads as scheduled, which is the opposite of what
    T-27 means to say.
    """
    body = prose(entry(_THREAT_ID))

    assert "no bundle file carries any of the three" in body, (
        f"{_THREAT_ID}'s entry no longer states that no bundle file carries any "
        f"of the SEC-15 safety triple. This is the sentence "
        f"`test_okf_codec.py::test_the_safety_triple_key_names_are_disjoint_from_"
        f"every_bundle_key` exists to keep true; without it here, that fact-side "
        f"pin is checking a claim this entry no longer makes."
    )
    assert "unowned rather than deferred to a named milestone" in body, (
        f"{_THREAT_ID}'s entry no longer calls the safety-triple gap 'unowned "
        f"rather than deferred to a named milestone'. If an issue now tracks "
        f"carrying the triple into a bundle, this entry has to say so instead of "
        f"reading like a gap nobody has picked up; if it is still genuinely "
        f"unowned, restore the sentence."
    )
