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

Which arm each of those raises reaches is not the ``except`` clause's to decide:
the two ``OSError`` clauses hand their errno to ``_arm_for_a_refused_registry``,
which gives back the permission-shaped arm for ``EACCES`` and
``RegistryFailureArm.UNKNOWN`` for everything else. The table of conditions that
routing produces, and a following of each arm's instructions to see whether the
reader gets out, are
``tests/integration/test_registry_cure_execution.py``.

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
``RegistryFailureArm``, so a fifth arm cannot arrive without one.

**How far the shapes reach, measured rather than claimed.** They were described
here as "the gate a newly pinned text clears at the moment it is pinned", and
round two refuted that limb by attacking each property in turn: three of the
four were bypassable as written. Property 1 loses to a paraphrase (round one's
two planted cures). Property 2 read its cost phrases as raw substrings, so
"blocks every project-scoped tool" -- which names no cost -- satisfied "every
project". Property 3 read both of its ends as raw substrings too, so the negated
"can be neither read nor deleted" supplied an invitation nine characters before
a deletion, and the arm's real "Then inspect it." could be deleted with the
property still passing. Property 4 was not attacked the same way, and could not
be: it demands one literal, so what it constrains is that one sentence and
nothing else about the text around it.

Both repairable ones are repaired here, and each repair is driven by a planted
cure in ``tests/unit/test_registry_deletion_cure_claims.py`` that passed before
it and is refused after: the cost phrases are matched as whole words
(:func:`_as_a_whole_phrase`), and property 3's invitation end is a sentence that
*instructs* the reader to look rather than any occurrence of one of the verbs
(:func:`_invites_inspection`). Its deletion end changed too
(:func:`offers_a_deletion`), and that half refuses nothing -- it is the
loosening the tightened invitation end needs in order to leave the honest
``directory-unreadable`` arm passing, which is measured at the function itself
rather than by a planted cure, because no planted cure could show it. Property 1
is not repairable in kind, and stays as the tuple of spellings it is.

What that buys is bounded and worth stating plainly: **the shapes gate a newly
written text only as far as their patterns reach**, and a text that clears all
four may still be false. They are the record of what a rewrite has to preserve.
The guard that a *changed* text is read by a human is the byte pin, and it is
the only one of the two that no paraphrase gets past.

``each project`` is deliberately **not** a cost phrase. Every one of these cures
already says "re-register each project", so a check that accepted it would hold
whatever the implementation did -- the always-true half of an ``assert a or b``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Final


def _as_a_whole_phrase(phrase: str) -> re.Pattern[str]:
    """``phrase``, matched only where the text says it and not merely spells it.

    ``"every project"`` as a plain substring is satisfied by ``"every
    project-scoped tool"``, which names no cost at all -- a phrase check that
    accepts it is reading letters rather than words, and round two measured a
    cure passing the cost property on exactly that. The trailing lookahead is
    ``[\\w-]`` rather than ``\\b`` because ``\\b`` matches *before* a hyphen:
    ``"project"`` and ``"project-scoped"`` are indistinguishable to it.

    Compiled against a lowercased haystack, so a sentence-initial capital cannot
    slip one past.
    """
    return re.compile(rf"(?<![\w-])({re.escape(phrase)})(?![\w-])")


#: Phrasings that promise the removal costs the reader nothing.
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

_COSTLESS_REMOVAL_PATTERNS: Final = tuple(
    (claim, _as_a_whole_phrase(claim)) for claim in COSTLESS_REMOVAL_CLAIMS
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

_THE_COST_PATTERNS: Final = tuple(
    _as_a_whole_phrase(cost) for cost in THE_COST_OF_DELETING_THE_REGISTRY
)

#: The verbs that invite a reader to look at the file, as patterns.
#:
#: **Matched only where the sentence uses one as an instruction** -- see
#: :func:`_invites_inspection`. A token found anywhere in a sentence is not an
#: invitation: ``"can be neither read nor deleted"`` denies the reading it
#: mentions, and matching ``read`` there let a cure that invites nothing satisfy
#: the order check nine characters before the deletion it also denies. Dropping
#: ``read`` and ``open`` from the set instead would be the wrong repair, because
#: the shipped ``unknown`` arm's whole invitation is the imperative ``Read
#: {path} before removing it.``: what makes an occurrence an invitation is that
#: the sentence tells the reader to do it, not which verb it picks.
INSPECTION_INVITATIONS: Final = (
    r"inspect\w*",
    r"look\s+at\b",
    r"read\w*",
    r"open\w*",
    r"examine\w*",
)

#: A sentence invites inspection when it opens with one of the verbs above, in
#: the imperative, allowing the connectives an instruction is chained with:
#: ``"Then inspect it."``, ``"Inspect {path} before removing it"`` and the
#: tail's ``"So read out every entry's projectId ... first;"`` are invitations --
#: the last one only because ``so`` is among the connectives -- while
#: ``"Restore read access to {path}"`` and ``"then delete it and re-register each
#: project with ..."`` are not: the first restores, the second is the deletion
#: offer itself.
_AN_INVITATION: Final = re.compile(
    rf"^(?:(?:then|now|next|first|so|and)\s+)*(?:{'|'.join(INSPECTION_INVITATIONS)})"
)

#: The way back, and the half of the cure that has to survive every rewrite: a
#: reader told to delete a file and given no invocation has no way back.
#:
#: Read by name across the suite under **two spellings** -- this constant and the
#: literal inside it -- so the key that enumerates the population is both::
#:
#:     git grep -n -e "re-register each project" -e RE_REGISTER_INVOCATION \
#:         packages/theurian-core/tests
#:
#: One form, spelled the same way here, in ``test_cli_commands.py`` and in
#: :data:`~theurian.application.project_service._HOW_TO_RECOVER_FROM_THE_DELETION`'s
#: own note. Matching ``re-register each project with`` instead, as two of the
#: three used to, misses the wrapped literals whose ``with`` sits on the next
#: source line and returns a different total for the same population.
#:
#: **Scope: ``packages/theurian-core/tests``.** A third spelling lives in
#: ``src`` -- ``_THE_RE_REGISTRATION_INVOCATION`` in ``cli/commands.py`` -- and is
#: deliberately outside this key. It is not another pin of the cure: it is the
#: antecedent check for a sentence that CLI layer *appends*, and it raises at
#: runtime when the cure stops carrying an invocation, so it defends itself.
#:
#: The literal alone is not that key either: it misses every assertion that reads
#: this constant instead of spelling it. The population is the *assertions* in
#: that output, by either spelling -- 10 of the 37 lines the search returns at the
#: commit this note lands in. The other 27 are the two constant definitions (here
#: and in ``test_cli_commands.py``), one import of this one, the cure's own tail
#: below, six planted-cure literals, one planted remedy in
#: ``test_session_start_hook.py``, seven lines of quoted instruction and docstring
#: across the two execution modules, and nine lines of prose and search text.
#:
#: **Count it against the tree the commit lands in, not the one you started
#: from.** ``git grep`` reads tracked files, so a note written beside a
#: still-untracked test file counts a population the commit does not have: that is
#: how this pair came out one short, missing the recovery sentence
#: ``test_registry_recovery_preserves_ids.py`` transcribes. Re-run it after
#: ``git add`` rather than trusting the pair of numbers -- both move with every
#: test added, including the one being added beside the note.
RE_REGISTER_INVOCATION: Final = "re-register each project with `theurian project register`"

#: Any spelling of the destructive verb: "Delete", "deleting", "deleted".
_DELETION = "delet"

#: Words that turn a mention of the deletion into a statement that it *cannot* be
#: done. Two shipped sentences are exactly that -- "can be neither read nor
#: deleted" and "leave the deletion below refused" -- and both are honest
#: warnings rather than offers, so a check that reads them as the offer measures
#: the order against the wrong sentence.
#:
#: A bare ``not`` is deliberately absent: the cost sentence every arm carries
#: says "unregisters all of them, **not** only this one", and reading that as a
#: denial would leave the shipped cures with no offer at all.
_A_DENIED_DELETION: Final = ("neither", "nor ", "cannot", "could not", "refused", "blocked")

#: How far either side of the deletion word a denial is looked for. The two
#: shipped denials need 17 characters before ("neither read nor ") and 17 after
#: ("ion below refused"), so 30 clears both with margin.
_DENIAL_WINDOW: Final = 30

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
    "original registeredAt. Deleting it also frees every id: `theurian project register` "
    "with no `--project-id` derives the id from the directory name, so a project registered "
    "under any other id comes back under a different one, and two checkouts whose "
    "directories share a name compete for a single id -- whichever re-registers first takes "
    "it. So read out every entry's projectId (the key it sits under) and its rootPath "
    "first; then delete it and re-register each project with `theurian project register`, "
    "passing `--project-id <its projectId>` and running it inside that rootPath. The other "
    "three fields are not restored from what you deleted: repositoryUrl and defaultBranch "
    "are re-read from Git as the tree stands then, and knowledgeDirectory comes back as the "
    "default."
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
        "Inspect {path} before removing it -- the ids and roots it lists are legible by eye "
        "even where its top level is not something this build can read."
    ),
    "file-unreadable": (
        "Restore read access to {path} first -- `chmod u+r` on it -- because this "
        "process could not open the file, so nothing in it can be read before it is "
        "destroyed. Then inspect it."
    ),
    "directory-unreadable": (
        "Restore access to {parent} first, working from the top down: `chmod u+rx` on "
        "each directory above it you cannot `cd` into, then `chmod u+rwx` on {parent} "
        "itself. This process could not reach {path} -- the refusal is at {parent} or at "
        "a directory above it, and it names the whole path rather than the component that "
        "denied it. The order matters: `chmod` on {parent} is itself refused while a "
        "directory above it denies the search. Until the refusal is lifted, {path} can be "
        "neither read nor deleted, and removing the file needs the write bit on {parent} "
        "as well as the search bit, so `u+rx` alone would restore the read and leave the "
        "deletion below refused. Then inspect it."
    ),
    "unknown": (
        "Read {path} before removing it. What refused it is named in the message "
        "beside this remedy rather than here, so read that first: it is not always "
        "a permission, and neither a directory sitting where the file belongs nor a "
        "path the filesystem will not accept is cured by a mode change."
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


def offers_a_deletion(sentence: str) -> bool:
    """Whether this sentence *offers* the deletion rather than denying it.

    A cure may mention the deletion to say it cannot be done -- the
    ``directory-unreadable`` arm does it twice, once to say the file "can be
    neither read nor deleted" and once to say that ``u+rx`` alone would "leave
    the deletion below refused". Both are warnings. Reading either as the offer
    measures the inspection order against a sentence that instructs nothing.

    **This half refuses nothing, and saying so is the point.** Ignoring the
    denials can only move the first offer *earlier*, so it can only make
    :func:`assert_a_deletion_cure_invites_inspection_before_the_deletion`
    stricter -- no false cure gets past this function that would not also get
    past a plain mention count. What it is for is the honest text: with the
    invitation end tightened to an instruction, the shipped
    ``directory-unreadable`` arm's own invitation ("Then inspect it.") arrives
    *after* both of its warnings, so a mention count fails it. Measured --
    reverting this call to ``_DELETION in sentence.lower()`` turns
    ``test_each_registry_failure_arm_renders_the_cure_it_is_pinned_to[directory-unreadable]``
    RED and leaves every planted cure refused exactly as before. The half that
    refuses the negated-invitation cure is :func:`_invites_inspection`; these two
    are a matched pair, not two guards.
    """
    lowered = sentence.lower()
    return any(
        not any(
            denial in lowered[max(found.start() - _DENIAL_WINDOW, 0) : found.end() + _DENIAL_WINDOW]
            for denial in _A_DENIED_DELETION
        )
        for found in re.finditer(_DELETION, lowered)
    )


def _invites_inspection(sentence: str) -> bool:
    """Whether this sentence tells the reader to look, rather than merely spelling a verb."""
    return _AN_INVITATION.search(sentence.strip().lower()) is not None


def assert_a_deletion_cure_does_not_claim_it_is_costless(text: str, *, where: str) -> None:
    """Property 1: no promise that removal loses nothing."""
    lowered = text.lower()
    for claim, pattern in _COSTLESS_REMOVAL_PATTERNS:
        assert pattern.search(lowered) is None, (
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
        pattern.search(window.lower()) for window in windows for pattern in _THE_COST_PATTERNS
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

    **Both ends of that order are read per sentence, and both used to be read as
    raw substrings.** The deletion end is the first sentence that *offers* the
    deletion (:func:`offers_a_deletion`) rather than the first ``delet`` in the
    text, and the invitation end is a sentence that *instructs* the reader to
    look (:func:`_invites_inspection`) rather than any occurrence of one of the
    verbs. Round two measured what the substring pair accepted: in "can be
    neither read nor deleted", ``read`` sits nine characters before ``deleted``,
    so a cure that invited nothing and denied both satisfied the whole property
    inside one negated clause -- and deleting the arm's real "Then inspect it."
    sentence left it passing.

    The two ends do different jobs, and only one of them refuses anything. The
    invitation end is the tightening: it is what turns that cure away. The
    deletion end is the loosening that the tightening requires, since the honest
    ``directory-unreadable`` arm puts its invitation after two sentences that
    mention a deletion in order to deny it. Each is measured in
    :func:`offers_a_deletion`'s own note.
    """
    assert not text.strip().lower().startswith(_DELETION), (
        f"{where} opens with the destructive act; a cure for a file whose contents may be "
        f"salvageable has to offer inspection first: {text!r}"
    )
    sentences = _sentences(text)
    offers = [i for i, sentence in enumerate(sentences) if offers_a_deletion(sentence)]
    assert offers, (
        f"{where} never offers the deletion, so there is no destructive instruction for an "
        f"invitation to precede and this property would hold vacuously: {text!r}"
    )
    invitations = [i for i, sentence in enumerate(sentences) if _invites_inspection(sentence)]
    assert invitations, (
        f"{where} never invites the reader to look at the file before deleting it -- no "
        f"sentence instructs them to, whatever else spells one of {INSPECTION_INVITATIONS}: "
        f"{text!r}"
    )
    assert min(invitations) < min(offers), (
        f"{where} names the deletion before it names the inspection, so a reader who stops "
        f"at the first instruction destroys the file. First offer: "
        f"{sentences[min(offers)]!r}; first invitation: {sentences[min(invitations)]!r}"
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
