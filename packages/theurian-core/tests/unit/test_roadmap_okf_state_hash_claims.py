"""The roadmap's corrected claim about the context package's stamp (ADR-0037 §9 candidate 8).

``docs/roadmap.md``'s Phase F ② row proposed a context-package export "stamped
with a generated-artifact label and the `stateHash` at generation". ADR-0037
declined that stamp -- a project-wide state hash covers the whole working tree
(ADR-0016), so a shippable artifact that omits withheld rows would still vary
with content its recipient may not read -- and shipped `theurian_bundle_digest`
instead: a digest over the bundle's own files. The roadmap keeps the retired
proposal sentence rather than deleting it, by its own stated convention (the
sentence right after it: "kept as the record of what was decided against"), so
this module holds two things apart: the retired sentence surviving as
*history*, and the correction being *true of the code*.

**Reach.** This holds the three sentences a search for `stateHash` beside
`bundle` turns up in the roadmap today, each pinned to keep the historicizing or
correcting clause beside it, plus the codec fact those correcting sentences
assert. It is **not** a general detector for a stateHash-into-the-bundle claim
reworded into new prose elsewhere in the document -- see `test_roadmap_claims.py`
for what building that costs -- and it does **not** hold the unrelated
`stateHash` field `knowledge.search` publishes on every response (the
Reproducibility row): that is a different feature under the same word, and this
module's fragments never coincide with it because none of them mention
`bundle`.

Pure: one document and one module read as text, no database, socket or
temporary directory.
"""

from __future__ import annotations

from typing import Final

import pytest
from adr_0037_support import REPO_ROOT, collapsed

from theurian.application.okf_codec import OKF_KEYS, THEURIAN_BUNDLE_DIGEST, THEURIAN_KEYS

pytestmark = pytest.mark.unit

ROADMAP: Final = REPO_ROOT / "docs" / "roadmap.md"


def _roadmap() -> str:
    return collapsed(ROADMAP.read_text(encoding="utf-8"))


def test_the_roadmap_still_marks_the_state_hash_proposal_as_retired_history() -> None:
    """RED means the surviving proposal sentence would now read as a live claim.

    The Goal/contents row keeps "stamped with a generated-artifact label and the
    `stateHash` at generation" verbatim, by the file's own convention of amending
    rather than editing history away -- and the very next clause says so:
    "the sentence before this one is the proposal as it stood, kept as the record
    of what was decided against." Losing that clause while the proposal sentence
    stays turns a recorded rejection back into what looks like the shipped
    design.
    """
    text = _roadmap()
    proposal = "stamped with a generated-artifact label and the stateHash at generation"
    marker = (
        "the sentence before this one is the proposal as it stood, kept as the "
        "record of what was decided against"
    )

    assert proposal in text, (
        "docs/roadmap.md's Phase F row no longer states the retired proposal "
        "sentence this pin holds as history. If it was deleted outright, the "
        "marker sentence below has nothing to point back at and should go with "
        "it; if it was reworded, this pin and the marker both need to move to "
        "the new wording."
    )
    assert marker in text, (
        "docs/roadmap.md's Phase F row no longer marks the proposal sentence as "
        "'kept as the record of what was decided against'. Without it, the "
        "surviving `stateHash` proposal sentence just above reads as a live "
        "description of the shipped export rather than as the design ADR-0037 "
        "decided against."
    )


def test_the_roadmap_still_states_the_as_shipped_state_hash_correction() -> None:
    """RED means the shipped correction reverted, or was reworded past checkability.

    Two sentences carry the correction: the Security row's "as shipped, by the
    label and `theurian_bundle_digest`" (replacing the declined `stateHash`), and
    §9 candidate 8's closing "the stamp is a digest over the bundle's own files
    instead." Either alone leaves half the correction unstated -- the first names
    the shipped key, the second says what kind of value it is.
    """
    text = _roadmap()

    assert "as shipped, by the label and theurian_bundle_digest" in text, (
        "docs/roadmap.md no longer states 'as shipped, by the label and "
        "`theurian_bundle_digest`'. This is the Security row's correction of the "
        "declined `stateHash` residual (T-27); without it the row still names the "
        "risk against a stamp the code does not carry."
    )
    assert "The stamp is a digest over the bundle's own files instead." in text, (
        "docs/roadmap.md's §9 candidate 8 no longer closes with 'The stamp is a "
        "digest over the bundle's own files instead.' This is the sentence that "
        "says what `theurian_bundle_digest` actually is, as opposed to the "
        "project-wide `stateHash` the row proposed and ADR-0037 declined."
    )


def test_theurian_bundle_digest_is_the_shipped_key_and_no_bundle_key_aliases_state_hash() -> None:
    """The fact half: the contract the day a producer extension adds a state-hash-shaped key.

    Read from the live constants rather than copied, so a renamed digest key or a
    new key that reintroduces a working-tree state hash under another spelling
    reddens here rather than only in the roadmap's prose. ``"state" in key and
    "hash" in key`` (case-insensitive) is a deliberate over-approximation -- it
    would also catch an unrelated key that happens to carry both words -- chosen
    because the failure this guards is a state-hash-shaped key arriving under any
    spelling, not one exact string.
    """
    all_keys = frozenset(THEURIAN_KEYS) | frozenset(OKF_KEYS)

    assert THEURIAN_BUNDLE_DIGEST in THEURIAN_KEYS, (
        f"`{THEURIAN_BUNDLE_DIGEST}` is no longer one of `THEURIAN_KEYS`. The "
        f"roadmap's correction names this key as what the export carries instead "
        f"of the declined `stateHash` stamp; if it moved or was renamed, the "
        f"roadmap's 'as shipped' sentence has to move with it."
    )
    aliasing = sorted(key for key in all_keys if "state" in key.lower() and "hash" in key.lower())

    assert not aliasing, (
        f"a bundle key now names or aliases a state hash: {aliasing}. ADR-0037 "
        f"declined the `stateHash` stamp because a project-wide state hash covers "
        f"the whole working tree and would vary with rows a recipient may not "
        f"read (T-27); a key reintroducing that under any spelling is the day the "
        f"roadmap's correction and the threat model's residual both have to move."
    )
