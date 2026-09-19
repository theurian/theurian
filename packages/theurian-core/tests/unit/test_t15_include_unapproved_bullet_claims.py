"""T-15's `includeUnapproved` bullet, recomputed from the gate it describes (#719).

The bullet said a ``draft`` or **``rejected``** body is reachable by a caller who
passes ``includeUnapproved`` -- a description of the gate as weaker than it is,
since ``REJECTED`` is deliberately outside :data:`SURFACEABLE_STATUSES` and
``may_surface`` refuses it *before* it reads the flag. #657 found it and #720
corrected it to ``{draft, proposed}``; nothing read the prose, so the same drift
could land again in silence. The enumeration's fact side is pinned
(``test_schemas.py::test_only_surfaceable_statuses_are_published`` holds the
published schema's ``status`` enum equal to the live set), and a schema pin says
nothing about what a document claims.

So the three status sets this bullet writes out are recomputed here from
:class:`KnowledgeStatus` and :data:`SURFACEABLE_STATUSES` and compared with what
the bullet enumerates. Each comparison is an equality, so it fails in both
directions: a status added to the live set and one taken out of it, and equally a
sentence that names a status the set does not hold. The three sets partition the
enum, which is asserted rather than assumed -- a status added to
:class:`KnowledgeStatus` joins one of the two expectations whichever side it lands
on, so the bullet cannot fall silent about one.

**Reach: this one bullet, and only its enumerations.** T-15 is long and this
module reads the single top-level bullet anchored on *Two parts of the store sit
outside that population*; the rest of the entry -- the scan population, the
merge residual (``test_t15_merge_residual_claims.py``), the propose-time and
hand-written-migration asides -- is out of scope here. Within the bullet it holds
*which statuses are named where*, not whether the surrounding reasoning is
faithful: that the T-17 argument for the first part is sound, or that the
supersede-then-rotate remedy is the right one, is a reading no key reaches.

Pure: one document read as text, two enums imported. No I/O of any other kind.
"""

from __future__ import annotations

import re
from typing import Final

import pytest
from threat_model_claims import THREAT_MODEL, entry_in, prose

from theurian.domain.enums import SURFACEABLE_STATUSES, KnowledgeStatus

pytestmark = pytest.mark.unit

#: Every status the domain defines, and the surfaceable subset, as the document
#: spells them.
_EVERY_STATUS: Final = frozenset(status.value for status in KnowledgeStatus)
_SURFACEABLE: Final = frozenset(status.value for status in SURFACEABLE_STATUSES)

#: What the bullet's first part enumerates: the statuses a caller reaches *only*
#: by passing the flag. ``approved`` is subtracted because it needs no flag, which
#: is the whole distinction the sentence draws.
_REACHED_WITH_THE_FLAG: Final = _SURFACEABLE - {KnowledgeStatus.APPROVED.value}

#: What the bullet's second part enumerates: the statuses ``may_surface`` refuses
#: before it reads the flag, so no value of it reaches them.
_REACHED_BY_NO_FLAG: Final = _EVERY_STATUS - _SURFACEABLE

_BULLET_START: Final = re.compile(r"(?m)^- ")

#: A stable topic phrase, never one of the enumerations below: a locator keyed on
#: the text a pin watches stops matching exactly when that text drifts, and the
#: pin then reads nothing and reports it as safety.
_ANCHOR: Final = "Two parts of the store sit outside that population"

#: The three enumerations, each keyed on the clause that gives it its meaning
#: rather than on a bare list of words -- ``rejected`` appears in this bullet on
#: both sides of the gate, so a key that matched any mention of it would report
#: the corrected text and the drifted text alike.
_REACHED_WITH_THE_FLAG_CLAUSE: Final = re.compile(
    r"\ba ([a-z, ]+?) body is reachable by a caller who passes includeunapproved"
)
_REACHED_BY_NO_FLAG_CLAUSE: Final = re.compile(
    r"no value of includeunapproved reaches a ([a-z, ]+?) body"
)
_THE_SET_ITSELF: Final = re.compile(r"surfaceable_statuses is \{([a-z, ]+?)\}")

_SEPARATORS: Final = re.compile(r",|\bor\b")


def _bullet() -> str:
    """The one T-15 bullet this module holds, raw.

    Split on a line-start ``- `` so the bullet's two-space-indented continuation
    lines -- which carry two of the three enumerations -- stay with the bullet
    they belong to rather than opening a new one.
    """
    entry = entry_in(THREAT_MODEL.read_text(encoding="utf-8"), "T-15")
    bullets = [bullet for bullet in _BULLET_START.split(entry) if _ANCHOR in bullet]

    assert len(bullets) == 1, (
        f"`{_ANCHOR}` identifies {len(bullets)} top-level bullets of T-15, expected 1. "
        f"Zero means the bullet was reworded past its own anchor and every arm here "
        f"would read an empty string; more than one means what is read below is text "
        f"this module never chose"
    )
    return bullets[0]


def _statuses_named(clause: re.Pattern[str], passage: str) -> frozenset[str]:
    """The status values *clause* enumerates in *passage*, normalised first.

    Normalising is not optional: the bullet soft-wraps through the middle of its
    own enumeration (the retired sentence wrapped ``A `draft`\\n  or `rejected```)
    and writes every status in a code span, so a key over raw bytes would pass
    over the words it exists to read.

    Every word must be a live :class:`KnowledgeStatus` value. A sentence naming
    something else is not a looser version of this claim -- it is a claim about a
    status this build does not have, and it fails here rather than being folded
    into a set comparison as a stray member.
    """
    matches = clause.findall(prose(passage))

    assert len(matches) == 1, (
        f"`{clause.pattern}` matches the bullet {len(matches)} times, expected 1. Zero "
        f"means the sentence carrying this enumeration was reworded past the key, so "
        f"the comparison below would read nothing; more than one means the bullet now "
        f"states it twice and they can disagree"
    )
    named = frozenset(word.strip() for word in _SEPARATORS.split(matches[0]) if word.strip())
    unknown = sorted(named - _EVERY_STATUS)

    assert not unknown, (
        f"the bullet enumerates {unknown}, which {'are' if len(unknown) > 1 else 'is'} "
        f"not a KnowledgeStatus value; the live statuses are {sorted(_EVERY_STATUS)}"
    )
    return named


def test_the_bullet_enumerates_exactly_the_statuses_the_flag_reaches() -> None:
    """RED when the flag-reachable sentence and ``SURFACEABLE_STATUSES`` disagree.

    This is the sentence that said ``rejected`` (#657): it tells an operator which
    unapproved bodies a caller can pull out of an index built with
    ``--include-unapproved``, and naming a status the gate refuses describes the
    gate as weaker than it is. Recomputed rather than quoted, so it also fails the
    other way -- a status added to or removed from the live set leaves the bullet
    enumerating a population that no longer exists.
    """
    named = _statuses_named(_REACHED_WITH_THE_FLAG_CLAUSE, _bullet())

    assert named == _REACHED_WITH_THE_FLAG, (
        f"the bullet says a {' or '.join(sorted(named))} body is reachable by passing "
        f"includeUnapproved; SURFACEABLE_STATUSES minus `approved` is "
        f"{sorted(_REACHED_WITH_THE_FLAG)}. `may_surface` refuses anything outside "
        f"SURFACEABLE_STATUSES before it reads the flag, so a status named here that "
        f"the set does not hold describes the gate as weaker than it is (#657), and a "
        f"status the set holds but the bullet omits leaves an operator unaware of a "
        f"body their own `--include-unapproved` build serves"
    )


def test_the_bullet_enumerates_exactly_the_statuses_no_flag_reaches() -> None:
    """RED when the withheld-side sentence and the live enum disagree.

    The other half of the same correction, and the one a reversion would empty:
    moving ``rejected`` back to the first part means dropping it from here. Held
    against the complement of :data:`SURFACEABLE_STATUSES` over the whole
    :class:`KnowledgeStatus`, so a newly retired status has to be named here too
    rather than quietly reading as reachable.
    """
    named = _statuses_named(_REACHED_BY_NO_FLAG_CLAUSE, _bullet())

    assert named == _REACHED_BY_NO_FLAG, (
        f"the bullet says no value of includeUnapproved reaches a "
        f"{' or '.join(sorted(named))} body; the statuses outside SURFACEABLE_STATUSES "
        f"are {sorted(_REACHED_BY_NO_FLAG)}. A status missing here is one the document "
        f"leaves a reader to assume the flag reaches"
    )


def test_the_bullet_quotes_the_surfaceable_set_as_the_domain_holds_it() -> None:
    """RED when the bullet's ``{approved, draft, proposed}`` quote goes stale.

    The two sentences above rest on this one: it is where the bullet tells a
    reader what the set *is*, and both enumerations are derived from it by hand in
    the prose. A quote that drifts makes the reasoning unfollowable even while the
    enumerations happen to stay right.
    """
    named = _statuses_named(_THE_SET_ITSELF, _bullet())

    assert named == _SURFACEABLE, (
        f"the bullet quotes SURFACEABLE_STATUSES as {sorted(named)}; the live set is "
        f"{sorted(_SURFACEABLE)} (`domain/enums.py`)"
    )


def test_the_two_enumerations_account_for_every_knowledge_status() -> None:
    """The premise the two comparisons above rest on, asserted rather than assumed.

    ``approved`` is subtracted from one expectation and belongs to neither
    enumeration -- it needs no flag. Everything else has to fall in exactly one of
    them, or a status added to :class:`KnowledgeStatus` could sit in neither and
    the bullet could stay silent about it with both arms green.
    """
    assert not (_REACHED_WITH_THE_FLAG & _REACHED_BY_NO_FLAG), (
        f"{sorted(_REACHED_WITH_THE_FLAG & _REACHED_BY_NO_FLAG)} is expected on both "
        f"sides of the gate at once, so the two arms above cannot both be satisfiable"
    )
    assert _REACHED_WITH_THE_FLAG | _REACHED_BY_NO_FLAG | {KnowledgeStatus.APPROVED.value} == (
        _EVERY_STATUS
    ), (
        f"the two enumerations plus `approved` cover "
        f"{sorted(_REACHED_WITH_THE_FLAG | _REACHED_BY_NO_FLAG | {KnowledgeStatus.APPROVED.value})}"
        f", not the live {sorted(_EVERY_STATUS)}. `approved` left SURFACEABLE_STATUSES, "
        f"or a status joined KnowledgeStatus that neither expectation reaches -- either "
        f"way the bullet can now omit a status with both arms above green"
    )


#: The sentence this bullet carried until #720, verbatim at ``900d3da4^`` --
#: including the wrap through the middle of its own enumeration, which a synthetic
#: string would miss and which is exactly what the normalisation in
#: :func:`_statuses_named` exists to fold.
_RETIRED_SENTENCE: Final = (
    "- **Two parts of the store sit outside that population, deliberately.** A `draft`\n"
    "  or `rejected` body is reachable by a caller who passes `includeUnapproved`, and\n"
    "  a **superseded revision** stays in the canonical store; neither is scanned by a\n"
    "  default build.\n"
)


def test_the_flag_reachable_key_still_reads_the_pre_657_sentence() -> None:
    """The key is held against the wording it exists to refuse, not only today's.

    Not a liveness premise: :func:`_statuses_named` refuses a key matching zero
    times, so a key gone dead reddens the arm above by itself -- measured, both
    fail together on ``assert 0 == 1``. What that leaves uncovered is a key
    *narrowed* to the shipped spelling, which still reads the current bullet and
    no longer reads #657's sentence: keyed to ``draft or proposed``, every other
    arm here stays green and this one fails alone.

    It is also the diagnosis. Both RED means the clause was reworded past the
    key; this one green beside a RED arm above means the enumeration itself
    moved, which is the reversion. The second assertion keeps that reading
    honest -- were ``rejected`` to join the live set, #657's sentence would be
    true again and this module's subject a different claim.
    """
    named = _statuses_named(_REACHED_WITH_THE_FLAG_CLAUSE, _RETIRED_SENTENCE)

    assert named == {"draft", "rejected"}, (
        f"the flag-reachable key reads {sorted(named)} out of the pre-#657 sentence, "
        f"not {{draft, rejected}}. It no longer reads the wording it exists to refuse, "
        f"so a bullet reverted to that wording would be reported as a clause the key "
        f"cannot read rather than as the status set it names:\n\n{_RETIRED_SENTENCE}"
    )
    assert named != _REACHED_WITH_THE_FLAG, (
        "`rejected` is now in SURFACEABLE_STATUSES minus `approved`, which makes the "
        "#657 sentence true again and this module's subject a different claim"
    )
