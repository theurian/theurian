"""The shape a cure that offers to delete the project registry must hold.

**A costless-removal claim is a shape claim, and the registry is the wrong
shape for it.** PR #596 closed a family with four cross-seam recurrences: a
remedy telling an operator that removing something "loses nothing", rendered
over an artefact that holds bytes or names. ``projects.json`` is exactly such an
artefact -- it *is* the enumeration of every project's registration, and neither
that enumeration nor an entry's ``registeredAt`` can be reconstructed from any
project's own ``.theurian/``. So the sentence
``_registry_reset_remedy`` shipped -- "it is derived and holds nothing that is
not also recoverable from each project's own .theurian/" -- promised a removal
that costs nothing over data that is held nowhere else (issue #381).

**Three properties, asserted as shapes rather than as bytes**, so the prose stays
the author's:

1. it does not claim the removal is costless (:data:`COSTLESS_REMOVAL_CLAIMS`),
2. it names what deleting the file costs, beside the sentence that offers the
   deletion (:data:`THE_COST_OF_DELETING_THE_REGISTRY`),
3. it leads with inspection rather than with the destructive act
   (:data:`INSPECTION_INVITATIONS`).

**Shared rather than restated at each pin, because the seam is where this family
recurs.** Two cure texts offer to delete this file, and they live in different
layers. ``_registry_reset_remedy`` (``application/project_service.py``) is
carried by four raises inside ``ProjectRegistry`` and reaches a caller as
``exc.remedy``. ``_registry_default_remedy`` (``cli/commands.py``) is
``_context_remedy``'s ``default`` at the two surfaces that read the whole
registry -- ``_RegistryRead.failure_fields``, which ``project status`` calls on
each of its two branches, and ``project list``. Issue #381 unified the second of
those out of two default strings written separately at those two call sites,
moving the claim inside the cure instead of restating it where the cure is
chosen: PR #596's own fix for this family, applied at a new seam. A shape
written out twice in two files drifts on the next surface, which is precisely
how the #596 family reached four faces.

``each project`` is deliberately **not** a cost phrase. Every one of these cures
already says "re-register each project", so a check that accepted it would hold
whatever the implementation did -- the always-true half of an ``assert a or b``.
"""

from __future__ import annotations

import re
from typing import Final

#: Phrasings that promise the removal costs the reader nothing. Matched against a
#: lowercased haystack, so a sentence-initial capital cannot slip one past.
#:
#: ``it is derived`` is here beside the outright promises because it is the
#: *premise* the shipped claim rested on, and it is false of this file on its own
#: terms: nothing derives the registry, and no command can regenerate it.
COSTLESS_REMOVAL_CLAIMS: Final = (
    "holds nothing that is not also recoverable",
    "recoverable from each",
    "it is derived",
    "nothing is lost",
    "nothing authored is lost",
    "loses nothing",
    "losing nothing",
    "costs nothing",
    "no data is lost",
    "safe to delete",
)

#: Spellings that name what deleting the registry actually costs: it removes the
#: registration of *every* project on the machine, not only this one. Any one of
#: them satisfies the check -- they are alternative wordings of one fact, and
#: none of them is present in a cure that omits the cost.
THE_COST_OF_DELETING_THE_REGISTRY: Final = (
    "every project",
    "every registered project",
    "all registered projects",
    "all of them",
    "each registered project",
)

#: Openings that invite the reader to look before they destroy. The cure must
#: reach one of these before it reaches the deletion.
INSPECTION_INVITATIONS: Final = ("inspect", "look at", "read ", "open ", "examine")

#: Any spelling of the destructive verb: "Delete", "deleting", "deleted".
_DELETION = "delet"

#: A sentence ends at a full stop or a semicolon followed by whitespace. The
#: semicolon matters: the shipped remedy joins its instruction and its costless
#: claim with one.
_SENTENCE_BREAK: Final = re.compile(r"(?<=[.;])\s+")


def _sentences(text: str) -> list[str]:
    return [sentence for sentence in _SENTENCE_BREAK.split(text.strip()) if sentence]


def deletion_window(text: str) -> str:
    """The sentence that offers the deletion, plus the one on either side of it.

    A cost stated three paragraphs from the destructive instruction is not a
    warning attached to the act, so the check is a proximity one. One sentence of
    slack on each side is what leaves the author the choice between "Deleting it
    unregisters every project" and "Every project's registration lives here, so
    deleting it ...".

    Raises rather than returning an empty window when nothing mentions deletion:
    a cure with no deletion in it would pass a cost check vacuously, and these
    cures all keep the delete-and-re-register recovery.
    """
    sentences = _sentences(text)
    offering = [i for i, sentence in enumerate(sentences) if _DELETION in sentence.lower()]
    if not offering:
        raise AssertionError(
            f"no sentence offers a deletion, so there is no destructive claim to check "
            f"the cost of: {text!r}"
        )
    return " ".join(sentences[max(min(offering) - 1, 0) : max(offering) + 2])


def assert_a_deletion_cure_does_not_claim_it_is_costless(text: str, *, where: str) -> None:
    """Property 1: no promise that removal loses nothing."""
    lowered = text.lower()
    for claim in COSTLESS_REMOVAL_CLAIMS:
        assert claim not in lowered, (
            f"{where} claims a costless removal ({claim!r}) over the file that holds every "
            f"project's registration; the enumeration and each entry's registeredAt are "
            f"recoverable from no project's own .theurian/ (issue #381, the PR #596 family): "
            f"{text!r}"
        )


def assert_a_deletion_cure_names_what_the_deletion_costs(text: str, *, where: str) -> None:
    """Property 2: the cost is stated beside the sentence offering the deletion."""
    window = deletion_window(text).lower()
    assert any(cost in window for cost in THE_COST_OF_DELETING_THE_REGISTRY), (
        f"{where} offers to delete the registry without naming what that removes -- it "
        f"unregisters every project on the machine, not only this one. Expected one of "
        f"{THE_COST_OF_DELETING_THE_REGISTRY} beside the deletion, got: {window!r}"
    )


def assert_a_deletion_cure_leads_with_inspection(text: str, *, where: str) -> None:
    """Property 3: the reader is invited to look before they are told to destroy."""
    lowered = text.strip().lower()
    assert not lowered.startswith(_DELETION), (
        f"{where} opens with the destructive act; a cure for a file whose contents may be "
        f"salvageable has to offer inspection first: {text!r}"
    )
    invited = [lowered.find(verb) for verb in INSPECTION_INVITATIONS if verb in lowered]
    assert invited, (
        f"{where} never invites the reader to look at the file before deleting it; expected "
        f"one of {INSPECTION_INVITATIONS}: {text!r}"
    )
    assert min(invited) < lowered.find(_DELETION), (
        f"{where} names the deletion before it names the inspection, so a reader who stops "
        f"at the first instruction destroys the file: {text!r}"
    )


def assert_registry_deletion_cure_shape(text: str, *, where: str) -> None:
    """All three properties, for a cure whose subject is the registry file."""
    assert_a_deletion_cure_does_not_claim_it_is_costless(text, where=where)
    assert_a_deletion_cure_names_what_the_deletion_costs(text, where=where)
    assert_a_deletion_cure_leads_with_inspection(text, where=where)
