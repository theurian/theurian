"""What a cure that offers to delete the project registry ships as, and may claim.

**A costless-removal claim is a shape claim, and the registry is the wrong
shape for it.** PR #596 closed a family with four cross-seam recurrences: a
remedy telling an operator that removing something "loses nothing", rendered
over an artefact that holds bytes or names. ``projects.json`` is exactly such an
artefact -- it *is* the enumeration of every project's registration, and neither
that enumeration nor an entry's ``registeredAt`` can be reconstructed from any
project's own ``.theurian/``. The cure this module guards once closed with "it
is derived and holds nothing that is not also recoverable from each project's
own .theurian/", promising a removal that costs nothing over data that is held
nowhere else (issue #381).

**One cure, four arms, and both layers of the guard are here.**
``registry_deletion_remedy`` (``application/project_service.py``) composes a
per-arm lead -- one member of ``RegistryFailureArm`` per condition the reader
can be standing in -- with a shared cost sentence and a shared recovery
sentence. Every surface renders that one function: the four raises inside
``ProjectRegistry``, which reach a caller as ``exc.remedy``, and
``cli/commands.py``, which passes ``RegistryFailureArm.UNKNOWN`` as
``_context_remedy``'s ``default`` at the two surfaces that read the whole
registry (``_RegistryRead.failure_fields`` and ``project list``). Until
``30e460b9`` the CLI spelled a near-duplicate of the cure itself, which is the
seam this family recurs at; there is one text now, and this module pins it.

**Byte equality is the lower layer, and the shapes are the upper one.**

1. :data:`THE_PINNED_CURES` holds all four arms verbatim, with ``{path}`` and
   ``{parent}`` as the only holes. ``tests/unit/test_registry_deletion_cure_claims.py``
   renders every member of ``RegistryFailureArm`` against them.
2. Four shape properties, asserted over the rendered text: it does not claim the
   removal is costless (:data:`COSTLESS_REMOVAL_CLAIMS`), it names what deleting
   the file costs beside a sentence that offers the deletion
   (:data:`THE_COST_OF_DELETING_THE_REGISTRY`), it invites inspection before it
   names the deletion (:data:`INSPECTION_INVITATIONS`), and it leaves the reader
   a typeable way back (:data:`RE_REGISTER_INVOCATION`).

**The closure argument, and why the shapes alone are not it.** A shape check
over prose is a blacklist of spellings, and prose paraphrases. "It is
regenerated automatically, so removal is harmless" and "a throwaway cache" are
both costless-removal claims about the one file that enumerates every
registration, and neither contains a phrase :data:`COSTLESS_REMOVAL_CLAIMS`
lists: planted in the cure, each passed every check in this repository. So the
guard that closes the class is byte equality, not the shapes -- **no false claim
about the registry can ship in these texts without a human reading it, because
the texts are pinned byte for byte**. Any edit to any arm, paraphrase or single
word, fails the equality pin and has to be re-pinned by hand, and the re-pinning
is where the new sentence gets read. The pin is exhaustive over
``RegistryFailureArm``, so a fifth arm cannot arrive without one. The shape
properties are consequently *not* the guard against a changed text; they are the
record of what a rewrite has to preserve, and the gate a newly pinned text
clears at the moment it is pinned -- which is the only moment a checker gets to
judge prose nobody has judged yet.

``each project`` is deliberately **not** a cost phrase. Every one of these cures
already says "re-register each project", so a check that accepted it would hold
whatever the implementation did -- the always-true half of an ``assert a or b``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Final

#: Phrasings that promise the removal costs the reader nothing. Matched against a
#: lowercased haystack, so a sentence-initial capital cannot slip one past.
#:
#: ``it is derived`` is here beside the outright promises because it is the
#: *premise* the shipped claim rested on, and it is false of this file on its own
#: terms: nothing derives the registry, and no command can regenerate it.
#:
#: Incomplete by construction -- a paraphrase is one thesaurus lookup away, which
#: is why the module docstring's closure argument rests on
#: :data:`THE_PINNED_CURES` and not on this tuple. What this tuple is for is the
#: text nobody has pinned yet.
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

#: Invitations to look before destroying, as patterns rather than as substrings.
#: The word boundary is the whole point: ``"read "`` as a plain substring is
#: satisfied by ``"Registrations are spread across ..."``, so a cure that never
#: invites anything cleared this check on the letters inside another word.
INSPECTION_INVITATIONS: Final = (
    r"\binspect\w*",
    r"\blook\s+at\b",
    r"\bread\w*",
    r"\bopen\w*",
    r"\bexamine\w*",
)

#: The way back, and the half of the cure that has to survive every rewrite: a
#: reader told to delete a file and given no invocation has no way back. Read by
#: name across the suite -- ``git grep -n "re-register each project with"
#: packages/theurian-core/tests`` enumerates the assertions, and they are the
#: population, not a number recorded here.
RE_REGISTER_INVOCATION: Final = "re-register each project with `theurian project register`"

#: Any spelling of the destructive verb: "Delete", "deleting", "deleted".
_DELETION = "delet"

#: A sentence ends at a full stop or a semicolon followed by whitespace. The
#: semicolon matters: the remedy issue #381 replaced joined its instruction and
#: its costless claim with one.
_SENTENCE_BREAK: Final = re.compile(r"(?<=[.;])\s+")

#: The cost and the recovery, shared by every arm because neither depends on how
#: the reader arrived. Split out here for the same reason the production module
#: splits it: a tail that is one text in the source and four transcriptions in
#: the pin drifts between the transcriptions.
THE_SHARED_CURE_TAIL: Final = (
    "The file records every project you have registered, so deleting it unregisters all "
    "of them, not only this one, and re-registering stamps today's date over each entry's "
    "original registeredAt. Once you have read out the roots you need, delete it and "
    "re-register each project with `theurian project register`."
)

#: The first sentences of each arm -- the half that differs, and the half the
#: arm exists to decide. Keyed by ``RegistryFailureArm``'s value, with ``{path}``
#: and ``{parent}`` as the only holes.
#:
#: Keyed by the string rather than by the enum member so this module stays
#: readable without importing the production one; the test that walks it imports
#: ``RegistryFailureArm`` and asserts the key set *is* the member set, so an arm
#: added without a pin fails there rather than being skipped here.
THE_PINNED_CURE_LEADS: Final[Mapping[str, str]] = {
    "unparsable": (
        "Inspect {path} before removing it -- the roots it lists are legible by eye "
        "even where its top level is not something this build can read."
    ),
    "file-unreadable": (
        "Restore read access to {path} first -- `chmod u+r` on it -- because this "
        "process could not open the file, so nothing in it can be read before it is "
        "destroyed. Then inspect it."
    ),
    "directory-unreadable": (
        "Restore access to {parent} first -- `chmod u+rx` on it and on every "
        "directory above it -- because this process could not look inside that "
        "directory, and while it cannot, {path} can be neither read nor deleted. "
        "Then inspect it."
    ),
    "unknown": (
        "Read {path} before removing it, restoring access first if the file or its "
        "directory refuses to open -- `chmod u+r` on the file, `chmod u+rx` on "
        "{parent}."
    ),
}

#: Every shipped cure, byte for byte: the arm's lead, one space, the shared tail.
#: The join is part of the pin -- a second space, or a reordered tail, fails it.
THE_PINNED_CURES: Final[Mapping[str, str]] = {
    arm: f"{lead} {THE_SHARED_CURE_TAIL}" for arm, lead in THE_PINNED_CURE_LEADS.items()
}


def the_pinned_lead(arm: str, path: Path) -> str:
    """The pinned lead for one arm, with the two path holes filled."""
    return THE_PINNED_CURE_LEADS[arm].format(path=path, parent=path.parent)


def the_pinned_cure(arm: str, path: Path) -> str:
    """The pinned text for one arm, with the two path holes filled.

    ``{parent}`` is filled from ``path.parent`` because that is what the cure
    means by it -- the data directory the file sits in, which is the thing the
    ``directory-unreadable`` arm asks the reader to ``chmod``.
    """
    return THE_PINNED_CURES[arm].format(path=path, parent=path.parent)


def assert_a_registry_cure_is_the_pinned_text(
    text: str, *, arm: str, path: Path, where: str
) -> None:
    """The lower layer: the rendered cure is the pinned bytes, or a human reads it.

    Deliberately not a shape. The shapes above are a blacklist of spellings and
    prose paraphrases past a blacklist -- this is what makes a *changed* text
    impossible to ship unread, whatever it now says. Failing here is not by
    itself a defect report: it says the text moved, and the fix is to read the
    new sentence against the four properties and then re-pin it by hand.
    """
    expected = the_pinned_cure(arm, path)
    assert text == expected, (
        f"{where} is no longer the pinned text for the {arm!r} arm. This pin is the guard "
        f"that a rewritten cure gets read by a human before it ships: check the new text "
        f"against every property in this module -- costless claim, cost beside the deletion, "
        f"inspection before the deletion, the re-register invocation -- and then re-pin it "
        f"here.\nexpected: {expected!r}\n     got: {text!r}"
    )


def _sentences(text: str) -> list[str]:
    return [sentence for sentence in _SENTENCE_BREAK.split(text.strip()) if sentence]


def deletion_windows(text: str) -> list[str]:
    """One window per sentence mentioning deletion: that sentence and its neighbours.

    A cost stated three paragraphs from the destructive instruction is not a
    warning attached to the act, so the check is a proximity one. One sentence of
    slack on each side is what leaves the author the choice between "Deleting it
    unregisters every project" and "Every project's registration lives here, so
    deleting it ...".

    **Per mention, never a single span from the first to the last.** This used to
    return ``sentences[min(offering) - 1 : max(offering) + 2]``, which is the same
    window only while exactly one sentence mentions deletion. Every shipped arm
    mentions it at least twice -- the cost sentence and the recovery sentence --
    so that slice covered everything between them, and a cure with its cost six
    sentences away from its instruction passed a proximity check by having no
    proximity at all. A mutant shaped that way survived the full suite.

    Raises rather than returning an empty list when nothing mentions deletion: a
    cure with no deletion in it would pass a cost check vacuously, and these
    cures all keep the delete-and-re-register recovery.
    """
    sentences = _sentences(text)
    offering = [i for i, sentence in enumerate(sentences) if _DELETION in sentence.lower()]
    if not offering:
        raise AssertionError(
            f"no sentence offers a deletion, so there is no destructive claim to check "
            f"the cost of: {text!r}"
        )
    return [" ".join(sentences[max(i - 1, 0) : i + 2]) for i in offering]


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
    """Property 2: the cost is stated beside a sentence that offers the deletion.

    Beside *one* of them, not beside every one. The ``directory-unreadable`` arm
    opens by saying the file "can be neither read nor deleted" -- a mention of
    deletion that is not an offer of one, and demanding the cost there would make
    an honest sentence fail.
    """
    windows = deletion_windows(text)
    assert any(
        cost in window.lower() for window in windows for cost in THE_COST_OF_DELETING_THE_REGISTRY
    ), (
        f"{where} offers to delete the registry without naming what that removes -- it "
        f"unregisters every project on the machine, not only this one. Expected one of "
        f"{THE_COST_OF_DELETING_THE_REGISTRY} beside a deletion, got the windows: {windows!r}"
    )


def assert_a_deletion_cure_invites_inspection_before_the_deletion(text: str, *, where: str) -> None:
    """Property 3: the reader is invited to look before they are told to destroy.

    Not "leads with inspection", which is what this asserted while one text
    served every arm. Two arms cannot honestly lead with it: at a registry at
    mode ``000`` nothing can be read until a ``chmod`` undoes that, and the arm
    for an untraversable data directory has to say so before it says anything
    about the file. What every arm does hold is the *order* -- an invitation to
    look reaches the reader before the deletion does. Which arm leads with which
    sentence is pinned per arm in
    ``tests/unit/test_registry_deletion_cure_claims.py``.
    """
    lowered = text.strip().lower()
    assert not lowered.startswith(_DELETION), (
        f"{where} opens with the destructive act; a cure for a file whose contents may be "
        f"salvageable has to offer inspection first: {text!r}"
    )
    invited = [
        found.start()
        for pattern in INSPECTION_INVITATIONS
        if (found := re.search(pattern, lowered)) is not None
    ]
    assert invited, (
        f"{where} never invites the reader to look at the file before deleting it; expected "
        f"one of {INSPECTION_INVITATIONS}: {text!r}"
    )
    assert min(invited) < lowered.find(_DELETION), (
        f"{where} names the deletion before it names the inspection, so a reader who stops "
        f"at the first instruction destroys the file: {text!r}"
    )


def assert_a_deletion_cure_names_the_way_back(text: str, *, where: str) -> None:
    """Property 4: the deletion is offered with the invocation that undoes it.

    Held here rather than beside one caller because it is a property of the cure,
    not of the surface rendering it: a reader handed "delete this file" and no
    command has been given a loss with no recovery, whichever arm they arrived
    on.
    """
    assert RE_REGISTER_INVOCATION in text, (
        f"{where} must keep the recovery typeable -- pins across the suite read this exact "
        f"invocation, and a cure that only says 'delete it' has no way out: {text!r}"
    )


def assert_registry_deletion_cure_shape(text: str, *, where: str) -> None:
    """All four properties, for a cure whose subject is the registry file."""
    assert_a_deletion_cure_does_not_claim_it_is_costless(text, where=where)
    assert_a_deletion_cure_names_what_the_deletion_costs(text, where=where)
    assert_a_deletion_cure_invites_inspection_before_the_deletion(text, where=where)
    assert_a_deletion_cure_names_the_way_back(text, where=where)
