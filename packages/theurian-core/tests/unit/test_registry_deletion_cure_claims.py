"""The delete-the-registry cure, arm by arm: the bytes it ships as and the guard's own teeth.

Two things are pinned here, and the second is why the first exists.

**The bytes.** ``registry_deletion_remedy`` renders one of four texts, one per
member of ``RegistryFailureArm``, and every one of them is compared byte-exact
against ``registry_deletion_cure_claims.THE_PINNED_CURES``. That module's
docstring carries the closure argument in full; the short form is that a shape
check over prose is a blacklist of spellings, prose paraphrases, and byte
equality is what makes a rewritten cure impossible to ship without a human
reading it.

**The guard.** The shape properties were measured against mutation and six false
cures walked past them: a costless claim spelled "regenerated automatically ...
so removal is harmless", another spelled "a throwaway cache", a cost stated six
sentences from the instruction that offered the deletion, a cure that invites
nothing at all but contains the letters ``read`` inside ``spread``, a cure whose
only invitation verb is the ``read`` its own clause denies ("neither read nor
deleted"), and one whose only cost-shaped phrase is "every project-scoped tool".
The first two are refused only by the byte pin -- which is the concrete form of
this module's argument for having one. The other four were each a real defect in
a shape check, one apiece: ``deletion_windows`` took one span from the first
deletion mention to the last, ``INSPECTION_INVITATIONS`` was matched without word
boundaries, it was then matched without regard to whether the sentence *invited*
anything, and ``THE_COST_OF_DELETING_THE_REGISTRY`` was matched as raw
substrings. All four are fixed rather than accepted. All six cures are rendered
here as literals and driven through the assertion that must now refuse each, so
the guard is tested rather than trusted -- a check nobody has ever seen fail is a
claim, not a control.

**The arms are wiring as well as text**, and the wiring is held elsewhere: which
condition reaches which arm is
``tests/integration/test_registry_cure_execution.py``, which plants each
condition, follows the arm's own instructions, and measures whether the reader
gets back to a readable registry. This module is the texts themselves and the
one refusal that has no condition behind it -- an ``arm`` that is not a member
at all.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest
from registry_deletion_cure_claims import (
    THE_PINNED_CURES,
    assert_a_deletion_cure_invites_inspection_before_the_deletion,
    assert_a_deletion_cure_names_what_the_deletion_costs,
    assert_a_registry_cure_is_the_pinned_text,
    assert_registry_deletion_cure_shape,
    the_pinned_lead,
)

from theurian.application.project_service import RegistryFailureArm, registry_deletion_remedy

pytestmark = pytest.mark.unit

#: One fixed path for every rendering here, so a byte pin can be a literal. The
#: production text interpolates the path and its parent and nothing else, which
#: is what makes a fixed path enough to pin the whole rendering.
THE_REGISTRY = Path("/data/projects.json")


@pytest.mark.parametrize("arm", list(RegistryFailureArm), ids=lambda arm: str(arm.value))
def test_each_registry_failure_arm_renders_the_cure_it_is_pinned_to(
    arm: RegistryFailureArm,
) -> None:
    """The lower layer of the guard, over every arm a caller can ask for.

    Shapes first, then bytes, and the order is the point. The shapes say what the
    text is *for* -- no costless claim, the cost beside the deletion, an
    invitation before the destruction, a typeable way back -- and they are what a
    newly written arm has to clear at the moment somebody pins it. The equality
    below cannot say any of that; what it can say is that the sentence a reader
    receives is the sentence somebody read. A paraphrase that satisfies all four
    shapes and still promises a free deletion fails here and nowhere else.
    """
    rendered = registry_deletion_remedy(THE_REGISTRY, arm)

    assert_registry_deletion_cure_shape(rendered, where=f"the {arm.value} cure")
    assert_a_registry_cure_is_the_pinned_text(
        rendered, arm=arm.value, path=THE_REGISTRY, where=f"the {arm.value} cure"
    )


def test_every_arm_of_the_cure_is_pinned_so_a_fifth_one_cannot_arrive_unread() -> None:
    """A new arm with no pin would be a shipped text nobody ever compared.

    ``RegistryFailureArm``'s exhaustive ``match`` makes mypy refuse a fifth
    member with no lead; nothing in production makes anyone *pin* that lead. This
    is that half: the pin table's keys are the member set, so an arm added
    without a byte pin fails here rather than rendering unchecked through the
    parametrize above, which would simply not have a case for it.
    """
    assert set(THE_PINNED_CURES) == {arm.value for arm in RegistryFailureArm}, (
        "every arm the production enum can dispatch to must have a byte pin here, and a pin "
        "with no arm is a text that no longer ships"
    )


@dataclass(frozen=True)
class _ArmLead:
    """What one arm's opening sentences must do, as intent rather than as bytes.

    **These checks fail on their own**, and one of them did. They read the
    *rendered* lead rather than the pin table, so they are only in agreement with
    the byte pin while somebody keeps them in agreement: the round-two rewrite of
    the ``unknown`` arm re-pinned cleanly and left this table's then-current
    ``read-with-a-conditional-restore`` classification -- since replaced by the
    one below -- asserting a ``chmod`` and an " if " that the new lead has neither
    of. What this table adds is the half
    the byte pin cannot hold -- the pin records *that* the text is this text and
    never *why*, and at the moment somebody re-pins a rewritten arm this is the
    statement of what the rewrite may not quietly drop.
    """

    #: The word the cure opens with -- its first imperative.
    opens_with: str
    #: How the reader is let in, and the field the arms exist to vary. One of
    #: ``unconditional-inspection``, where the bytes are already in front of
    #: them; ``restore-then-inspect``, where a ``chmod`` has to come first and
    #: the inspection waits behind it; or
    #: ``read-with-the-cause-named-elsewhere``, where this module cannot name the
    #: condition at all, so the lead prescribes nothing and sends the reader to
    #: the message the cure travels with.
    lets_the_reader_in_by: str


#: Which lead belongs to which arm. The two unreadable arms are the ones issue
#: #381's round found: a cure opening "Inspect this file" was published beside a
#: message saying the same file "cannot be opened".
_THE_ARM_LEADS = {
    RegistryFailureArm.UNPARSABLE: _ArmLead(
        opens_with="Inspect", lets_the_reader_in_by="unconditional-inspection"
    ),
    RegistryFailureArm.FILE_UNREADABLE: _ArmLead(
        opens_with="Restore", lets_the_reader_in_by="restore-then-inspect"
    ),
    RegistryFailureArm.DIRECTORY_UNREADABLE: _ArmLead(
        opens_with="Restore", lets_the_reader_in_by="restore-then-inspect"
    ),
    RegistryFailureArm.UNKNOWN: _ArmLead(
        opens_with="Read", lets_the_reader_in_by="read-with-the-cause-named-elsewhere"
    ),
}


@pytest.mark.parametrize("arm", list(RegistryFailureArm), ids=lambda arm: str(arm.value))
def test_only_the_arm_whose_reader_can_open_the_file_opens_by_telling_them_to(
    arm: RegistryFailureArm,
) -> None:
    """The distinction the arms exist for: an invitation the reader can accept.

    A cure that opens "Inspect {path}" is an instruction the reader can follow
    only where the bytes are in front of them. On the two arms that arrive with
    the file or its directory at mode ``000`` it is the message contradicting
    itself, so those lead with the ``chmod`` that makes the inspection possible
    and put the inspection behind it.

    ``UNKNOWN`` names no cause at all, and that is what changed with the errno
    split. Its lead used to offer the ``chmod`` under a condition -- "if the file
    or its directory refuses to open" -- which was defensible while the arm
    rendered only for the CLI ``default``. Now that every non-``EACCES`` refusal
    routes here, that antecedent is *true* for a reader whose registry path holds
    a directory, while the ``chmod`` cures nothing they have: a false cause
    wearing a conditional. So the lead prescribes nothing and points at the
    message the cure travels with, and this case asserts both halves.
    """
    expected = _THE_ARM_LEADS[arm]

    lead = the_pinned_lead(arm.value, THE_REGISTRY)
    rendered = registry_deletion_remedy(THE_REGISTRY, arm)

    assert rendered.startswith(lead), "the pinned lead must be this arm's actual opening"
    assert lead.split()[0] == expected.opens_with, (
        f"the {arm.value} arm's first imperative decides what a reader who stops after one "
        f"sentence does: {lead!r}"
    )
    match expected.lets_the_reader_in_by:
        case "unconditional-inspection":
            assert "chmod" not in lead, (
                f"this reader's bytes are already legible, so an access restoration would be "
                f"an instruction with nothing to undo: {lead!r}"
            )
        case "restore-then-inspect":
            assert lead.index("chmod") < lead.lower().index("inspect"), (
                f"the {arm.value} arm arrives with the file unopenable, so the restoration has "
                f"to precede the inspection or the reader is told to read what the payload "
                f"beside this says cannot be opened: {lead!r}"
            )
        case "read-with-the-cause-named-elsewhere":
            assert "chmod" not in lead, (
                f"this arm is what a non-EACCES refusal lands on -- a directory at the "
                f"registry path, a name the filesystem will not take -- and none of them has a "
                f"mode to restore, so naming one states a cause that is false for the reader "
                f"who is actually here: {lead!r}"
            )
            assert "message beside this remedy" in lead, (
                f"prescribing nothing is only honest if the reader is told where the cause *is* "
                f"named; both surfaces that render this arm publish that message beside it: "
                f"{lead!r}"
            )
        case unclassified:  # pragma: no cover - a fifth arm has to be classified
            # Unreachable from this parametrize, which is why it carries no
            # coverage: a fifth `RegistryFailureArm` member fails earlier and
            # louder -- `_THE_ARM_LEADS[arm]` above raises `KeyError` before this
            # `match` is entered, and `the_pinned_lead` raises another off
            # `THE_PINNED_CURE_LEADS`. What this arm catches is the other
            # direction: a member left in the table with a classification string
            # nobody wrote a case for.
            raise AssertionError(f"{arm.value} has no lead classification: {unclassified!r}")


def test_only_the_directory_arm_says_the_deletion_itself_is_blocked() -> None:
    """The two unreadable arms differ on more than the ``chmod``, and it is measured.

    ``test_a_mode_000_registry_is_still_removable_through_a_traversable_parent``
    in ``test_project_registry_errors.py`` is the measurement: unlinking needs
    write and search on the *directory*, not read on the file. So a registry at
    mode ``000`` inside a traversable data directory can be deleted exactly as
    the cure's tail offers, and telling that reader the deletion is blocked would
    be false. One level up it *is* blocked, and a cure that ended with "delete it
    and re-register" without saying so would send the reader at an instruction
    their filesystem refuses.
    """
    directory = the_pinned_lead(RegistryFailureArm.DIRECTORY_UNREADABLE.value, THE_REGISTRY)
    unreadable_file = the_pinned_lead(RegistryFailureArm.FILE_UNREADABLE.value, THE_REGISTRY)

    assert "neither read nor deleted" in directory, (
        "the deletion the tail goes on to offer cannot be performed through an untraversable "
        "parent, and the arm that knows it is the one that must say so"
    )
    assert "delet" not in unreadable_file.lower(), (
        "a mode-000 file is removable through a traversable parent, so this arm's lead makes "
        "no claim about the deletion at all -- the tail's offer stands unqualified"
    )


def test_a_string_that_is_not_an_arm_is_refused_rather_than_rendered_as_a_cure() -> None:
    """The half of the exhaustiveness argument mypy does not reach.

    ``RegistryFailureArm`` is a :class:`~enum.StrEnum`, so a caller outside the
    type checker's reach can hand :func:`registry_deletion_remedy` a bare string.
    A member's *value* routes exactly as the member does -- the ``match``'s
    patterns compare by equality -- and that is asserted here too, because it is
    what makes ``the_pinned_cure``'s string keys and this suite's ``arm.value``
    calls legitimate. Anything else used to take no case at all: the function
    returned ``None`` from its ``match`` and the caller's f-string rendered the
    literal word ``None`` in front of an offer to delete the reader's registry.

    Asserted on the message rather than only on the type, because a ``ValueError``
    raised for some unrelated reason would satisfy the raise alone.
    """
    by_value = registry_deletion_remedy(THE_REGISTRY, cast(RegistryFailureArm, "unparsable"))

    assert by_value == registry_deletion_remedy(THE_REGISTRY, RegistryFailureArm.UNPARSABLE), (
        "a member's value must route as the member does, which is what lets the pin table be "
        "keyed by string"
    )

    with pytest.raises(ValueError, match="is not a RegistryFailureArm") as excinfo:
        registry_deletion_remedy(THE_REGISTRY, cast(RegistryFailureArm, "file-unreadble"))

    assert "delete" not in str(excinfo.value).lower(), (
        "the refusal replaces the cure rather than accompanying it -- a typo must not reach a "
        "reader as an offer to delete anything"
    )


# -- the guard's own teeth: cures that walked past it, refused here ----------
#
# Each text below is a hand-rendered cure over `THE_REGISTRY`, written to be
# plausible rather than absurd, and each corresponds to a mutation that survived
# a run of the suite. `refused_by` names the single assertion that must now
# refuse it, so the case says which layer catches it -- and the two that name
# the byte pin are the concrete argument for why the shapes are not the guard.


@dataclass(frozen=True)
class _AFalseCure:
    """A cure that must not survive, and the one assertion that must refuse it."""

    #: What the false text claims, in the reader's terms.
    claim: str
    #: The rendered cure, as a caller would receive it.
    text: str
    #: The assertion this text must fail, called with the text alone.
    refused_by: Callable[[str], None]
    #: A fragment of the failure message, so a check cannot pass on the wrong
    #: assertion firing for the wrong reason.
    message_names: str


def _the_byte_pin(text: str) -> None:
    assert_a_registry_cure_is_the_pinned_text(
        text, arm=RegistryFailureArm.UNPARSABLE.value, path=THE_REGISTRY, where="the planted cure"
    )


def _the_cost_check(text: str) -> None:
    assert_a_deletion_cure_names_what_the_deletion_costs(text, where="the planted cure")


def _the_invitation_check(text: str) -> None:
    assert_a_deletion_cure_invites_inspection_before_the_deletion(text, where="the planted cure")


_THE_CURES_THAT_WALKED_PAST_THE_SHAPES = [
    _AFalseCure(
        claim="the registry regenerates itself, so removing it is harmless",
        text=(
            "Inspect /data/projects.json before removing it -- the roots it lists are legible "
            "by eye even where its top level is not something this build can read. The file "
            "records every project you have registered, but it is regenerated automatically "
            "the next time a project is registered, so removal is harmless. Once you have "
            "read out the roots you need, delete it and re-register each project with "
            "`theurian project register`."
        ),
        refused_by=_the_byte_pin,
        message_names="no longer the pinned text",
    ),
    _AFalseCure(
        claim="the registry is a throwaway cache",
        text=(
            "Inspect /data/projects.json before removing it -- the roots it lists are legible "
            "by eye even where its top level is not something this build can read. It is a "
            "throwaway cache of every project you have registered, rebuilt as soon as you "
            "register again. Once you have read out the roots you need, delete it and "
            "re-register each project with `theurian project register`."
        ),
        refused_by=_the_byte_pin,
        message_names="no longer the pinned text",
    ),
    _AFalseCure(
        claim="the cost is stated, six sentences from the instruction that offers the deletion",
        text=(
            "Inspect /data/projects.json before removing it. Deleting it is the only cure for "
            "a top level this build cannot read. The roots it lists are legible by eye. A "
            "hand edit is the only way a file in this directory becomes unparsable. The file "
            "records every project you have registered, and re-registering stamps today's "
            "date over each entry's original registeredAt. A registry that will not parse "
            "blocks every project-scoped tool. Nothing else on this machine reads it. Once "
            "you have read out the roots you need, delete it and re-register each project "
            "with `theurian project register`."
        ),
        refused_by=_the_cost_check,
        message_names="without naming what that removes",
    ),
    _AFalseCure(
        claim="the reader is never invited to look, and 'spread' carried the old 'read ' token",
        text=(
            "Registrations are spread across this file and nowhere else, so removing it "
            "unregisters every project you have registered, not only this one. Delete it and "
            "re-register each project with `theurian project register`."
        ),
        refused_by=_the_invitation_check,
        message_names="never invites the reader to look",
    ),
    _AFalseCure(
        claim="the only invitation is the negated 'read' the same clause denies",
        text=(
            "Restore access to /data first -- `chmod u+rwx` on it, and `chmod u+rx` on every "
            "directory above it -- because this process could not look inside that directory, "
            "and while it cannot, /data/projects.json can be neither read nor deleted. The "
            "file records every project you have registered, so deleting it unregisters all "
            "of them, not only this one, and re-registering stamps today's date over each "
            "entry's original registeredAt. Once you have read out the roots you need, delete "
            "it and re-register each project with `theurian project register`."
        ),
        refused_by=_the_invitation_check,
        message_names="never invites the reader to look",
    ),
    _AFalseCure(
        claim="the only 'every project' in the deletion's window is 'every project-scoped tool'",
        text=(
            "Inspect /data/projects.json before removing it -- the roots it lists are legible "
            "by eye. A registry that will not parse blocks every project-scoped tool, so "
            "delete it and re-register each project with `theurian project register`."
        ),
        refused_by=_the_cost_check,
        message_names="without naming what that removes",
    ),
]


@pytest.mark.parametrize(
    "case",
    _THE_CURES_THAT_WALKED_PAST_THE_SHAPES,
    ids=[
        "regenerated-automatically",
        "throwaway-cache",
        "distant-cost",
        "no-invitation",
        "negated-invitation",
        "project-scoped-tool",
    ],
)
def test_a_cure_that_survived_the_suite_is_refused_by_the_guard_it_walked_past(
    case: _AFalseCure,
) -> None:
    """Every check here has been seen to fail, on the text that made it necessary.

    The first two are paraphrased costless-removal claims. Both hold all four
    shape properties -- neither contains a phrase ``COSTLESS_REMOVAL_CLAIMS``
    lists, both name the cost beside the deletion, both invite inspection first,
    both keep the re-register invocation -- and both are false about a file that
    is the enumeration of every registration. That they are refused *only* by the
    byte pin is the whole argument for having one.

    The third is a real defect in the shape check rather than a limit of it:
    ``deletion_windows`` took a single span from the first deletion mention to
    the last, which is every sentence between them, so a cost sentence anywhere
    in the middle satisfied a proximity check with no proximity. Per-mention
    windows refuse it.

    The fourth opens "Registrations are spread across ..." and invites nothing.
    It cleared the inspection property on the letters ``read`` inside
    ``spread``, because the invitation set was matched as plain substrings; the
    patterns are word-bounded now.

    The last two are round two's, and each was one shipped arm with one sentence
    changed -- which is what makes them the plausible fifth arm rather than an
    absurdity. The fifth is the ``directory-unreadable`` cure **as it shipped at
    2d1f60c2** with "Then inspect it." removed: its only remaining invitation verb
    is the ``read`` inside "can be neither read nor **deleted**", which denies the
    reading it mentions and sits nine characters before a deletion it also denies,
    so the whole property used to be satisfied inside one negated clause. The
    sixth states a consequence rather than a cost -- "blocks every project-scoped
    tool" -- and cleared the cost property on the letters of "every project"
    inside "project-scoped".

    **All six are held at the text that made them necessary, not re-pinned when
    the arms move.** A planted cure is the input a check was written against, so
    rewriting it to track the shipped text would silently change what the check is
    measured on. Measured on the fifth: rebuilt on today's lead and tail it is
    still refused, but by the *order* end -- "names the deletion before it names
    the inspection" -- rather than by the invitation end this case exists to
    exercise, because the tail's recovery now opens "So read out every entry's
    projectId ...", which is an invitation and lands after the cost sentence. The
    case would go RED for a reason that is not its own, and the property it guards
    would stop being measured at all.
    """
    with pytest.raises(AssertionError) as excinfo:
        case.refused_by(case.text)

    assert case.message_names in str(excinfo.value), (
        f"the cure claiming {case.claim!r} must be refused for the reason this case names, "
        f"not incidentally by some other assertion: {excinfo.value}"
    )
