"""T-19's derived-artifact families and its findings serve check, held to the code.

``docs/security/threat-model.md``'s T-19 entry is the record a reader consults to
learn **what** this installation vouches for and **where** that vouching is
enforced. It enumerates the derived database families that live under
`.theurian/state/`, spells how many there are, and names the symbol that gates
the newest one's serve path. All three are facts about today's code written into
a durable security record, and the entry has already been wrong about them once:
until https://github.com/theurian/theurian/pull/504 it described **two** families
while :class:`~theurian.application.project_service.BuildProvenance` had grown a
third (``findings``, ADR-0029's serving slice), so a reader checking which
artifacts the provenance control covers would have concluded the findings store
was outside it.

**Both sides are derived, and they are written independently.** The fact side is
the set of artifact families ``BuildProvenance`` actually exposes -- one
``record_<family>`` writer and one ``has_<family>`` reader per family -- read off
the live class, so a fourth family takes this module RED at the moment it lands,
which is the moment T-19 has to be rewritten. The prose side is read out of the
entry: the number word it spells and the artifact names it enumerates. Neither
side is parsed from the other, because a pin that read its expected count out of
the sentence it checks would agree with that sentence by construction and measure
nothing.

**What it holds.** (1) T-19's enumeration paragraph names every family
``BuildProvenance`` records, by the ``theurian-<family>-`` filename prefix it
enumerates them under; (2) the number word it spells equals how many there are;
(3) every
``BuildProvenance.<member>`` the entry cites is a real member of the class, and
the findings family's serve-side reader is among them -- the sentence that says
the provenance gate reaches the third family is the one T-19 gained on this
branch, and it is the one a reader takes as evidence that `review.findings` is
covered. It goes RED both ways round: a fourth family added to the class while
the entry still says *three*, and prose reworded back to describing two.

**What it does not hold.** That the entry *describes* any family correctly --
naming an artifact prefix is not saying anything true about it -- nor that the
serve path it names actually calls the member it cites; that is a property of
``mcp/tools.py``, pinned behaviourally by
``tests/integration/test_review_findings_tool.py``. Nor does it cover T-19's
residual, delivery-independence or laundering paragraphs, which make their own
claims and have no pin here.

**The naming key is a convention, and a RED on it is not automatically a prose
defect.** Each family's artifacts are filed under ``theurian-<family>-*.sqlite``
(``ProjectPaths.database_for``, ``index_for``, ``findings_for``), which is how
T-19 enumerates them, so the family token derived from the class is also the
string the entry must carry. A future family filed under some other name takes
this RED with the entry innocent, and the answer then is to say in the entry how
that family is named -- not to delete the pin.

Pure in the sense the other claim pins are: one document read as text and one
class read for its member names, no database, socket or temporary directory.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Final

import pytest
from threat_model_claims import SPELLED_NUMBERS, WORD_FOR_COUNT, entry, prose

from theurian.application.project_service import BuildProvenance

pytestmark = pytest.mark.unit

#: The entry this module reads. Sliced by ``threat_model_claims.entry``, which is
#: where the anchoring rules and the reason for them live.
_THREAT_ID: Final = "T-19"

#: The two halves of an artifact family on :class:`BuildProvenance`: the build
#: side that records one, and the serve side that asks whether this installation
#: did. A family is a name that has both, and :func:`artifact_families` fails
#: loudly on one that has only one half -- a recorder with no reader is an
#: artifact nothing gates, and a reader with no recorder gates an artifact
#: nothing can ever produce.
_RECORDER: Final = "record_"
_CHECKER: Final = "has_"

#: How T-19 spells the size of the family set, as the phrase rather than as a
#: bare number word. The entry carries other spelled numbers -- the control
#: paragraph says "all three families" and the closure paragraph counts an
#: "eight-query battery" -- so a key matching any spelled number would read
#: whichever came first. The caller asserts exactly one match, so a rewrite that
#: made the key ambiguous fails naming both rather than silently reading one.
_SPELLED_FAMILY_COUNT: Final = re.compile(r"\bthe ([a-z]+) database families\b")

#: A Markdown block boundary: one blank line, whatever whitespace it carries.
#: The enumeration is scoped to its own paragraph rather than to the whole entry
#: deliberately -- see :func:`_enumeration_block`.
_BLOCK_BREAK: Final = re.compile(r"\n[ \t]*\n")

#: A ``BuildProvenance`` member cited in the entry, read from the **raw** entry so
#: the backticks are still there: an unbacktracked "BuildProvenance.has_findings"
#: in running prose is not the citation this checks, and matching one would let a
#: sentence that merely mentions the class satisfy a pin about what it cites.
_CITED_MEMBER: Final = re.compile(r"`BuildProvenance\.([A-Za-z_][A-Za-z0-9_]*)`")

#: The family whose serve check T-19 gained on this branch, and the one whose
#: citation this module requires. Spelled out rather than derived, because
#: nothing in ``BuildProvenance`` says which family `review.findings` serves --
#: but guarded by a premise below that it is still one of the live families, so a
#: rename fails naming itself instead of silently checking a family that no
#: longer exists.
_FINDINGS_FAMILY: Final = "findings"

#: How the entry writes an artifact family's files. See the module docstring:
#: this is a convention T-19 enumerates the families under, not something the
#: class states about itself.
_ARTIFACT_PREFIX: Final = "theurian-{family}-"


def artifact_families(namespace: Iterable[str]) -> tuple[str, ...]:
    """The artifact families *namespace* exposes, one per matched record/has pair.

    Sorted, so the failure messages built from it do not depend on iteration
    order. Takes a namespace rather than reading the class itself so the
    derivation can be exercised against a synthetic member list -- which is how
    the "a fourth family reddens this" claim was demonstrated without waiting for
    a fourth family to exist.

    Private members are excluded by the prefixes themselves: ``_record``, the
    shared writer both recorders call, does not start with ``record_``.
    """
    recorders = {name.removeprefix(_RECORDER) for name in namespace if name.startswith(_RECORDER)}
    checkers = {name.removeprefix(_CHECKER) for name in namespace if name.startswith(_CHECKER)}

    assert recorders == checkers, (
        f"`BuildProvenance` records {sorted(recorders)} and checks {sorted(checkers)}. "
        f"Every artifact family needs both halves -- a `record_*` with no `has_*` is "
        f"an artifact no serve path gates, and a `has_*` with no `record_*` gates one "
        f"nothing can ever produce -- and until they agree there is no single set for "
        f"T-19 to be held against"
    )
    return tuple(sorted(recorders))


def _enumeration_block() -> str:
    """T-19's one paragraph that enumerates the families, normalised.

    Scoped to the paragraph rather than to the whole entry, because the entry
    names the newest family's artifact elsewhere too: the paragraph recording
    ADR-0029's window quotes a fabricated ``theurian-findings-local.sqlite``, and
    the reproduction paragraph names it again. An entry-wide scan would let those
    satisfy the enumeration arm, so a rewrite that dropped ``findings`` from the
    list a reader actually reads would pass on the strength of a sentence about
    an attack.

    Located by the count phrase, which is what says the paragraph *is* the
    enumeration -- not by a family name, since a key naming one member would stop
    matching exactly when someone rewrote the list, and the paragraph would drop
    out of the population rather than fail.
    """
    blocks = [prose(block) for block in _BLOCK_BREAK.split(entry(_THREAT_ID)) if block.strip()]
    carrying = [block for block in blocks if _SPELLED_FAMILY_COUNT.search(block)]

    assert len(carrying) == 1, (
        f"`{_SPELLED_FAMILY_COUNT.pattern}` identifies {len(carrying)} of T-19's "
        f"{len(blocks)} paragraphs, expected 1. Zero means the enumeration was "
        f"reworded past its own key and everything below would pass over nothing; "
        f"more than one means what is read below is text this module never chose"
    )
    return carrying[0]


def test_t19_names_every_derived_artifact_family_and_spells_how_many() -> None:
    """RED means T-19 and ``BuildProvenance`` disagree about what is vouched for.

    T-19 tells a reader deciding whether to trust a repository's derived state
    which artifacts the provenance control covers. That list was short by one for
    the length of the branch that added ``findings``: the entry described two
    families while the class recorded three, and the entry is the only place a
    reader can learn the answer without reading ``project_service.py``.

    So the assertion is an equality between two independently written things, the
    ``test_setup_claims.py`` shape: what the entry enumerates and spells, and what
    the class exposes. It fails whichever one moves -- a fourth family landing is
    not a defect in the product, it is the moment this record has to be rewritten,
    and the failure message says which word it should then carry.

    The premises come first. A class with no families at all would make every
    membership check below vacuous, an entry whose enumeration can no longer be
    located fails inside :func:`_enumeration_block`, and a paragraph spelling its
    count twice leaves the count arm unable to say which figure is the claim. Each
    fails naming itself rather than arriving at the comparison as a bare mismatch.
    """
    families = artifact_families(dir(BuildProvenance))
    text = _enumeration_block()

    assert families, (
        "`BuildProvenance` exposes no `record_*`/`has_*` family at all, so every "
        "check below would pass over nothing and the count would be about a set "
        "this test never read"
    )
    spelled = _SPELLED_FAMILY_COUNT.findall(text)
    assert len(spelled) == 1, (
        f"T-19's enumeration states how many derived database families there are "
        f"{len(spelled)} times, expected once, so this pin cannot say which figure "
        f"is the claim: {spelled}"
    )
    assert spelled[0] in SPELLED_NUMBERS, (
        f"T-19 spells its family count as `{spelled[0]}`, which is not a number this "
        f"pin can read; the entry has to say how many, or the count is back to being "
        f"a claim nobody can check"
    )

    unnamed = [family for family in families if _ARTIFACT_PREFIX.format(family=family) not in text]

    assert not unnamed, (
        f"T-19 does not name {unnamed}, which `BuildProvenance` records as a derived "
        f"artifact family this installation vouches for. The entry enumerates the "
        f"families and a reader takes that list as complete, so an unnamed one reads "
        f"as an artifact the provenance control does not cover: {text[:400]}"
    )
    should_carry = WORD_FOR_COUNT.get(len(families), "no word this pin can spell")
    assert SPELLED_NUMBERS[spelled[0]] == len(families), (
        f"T-19 describes `{spelled[0]}` derived database families; `BuildProvenance` "
        f"exposes {len(families)} ({list(families)}). Whichever side moved, the record "
        f"and the class have to be brought back into step, and the word the entry "
        f"should carry is `{should_carry}`"
    )


def test_t19_cites_the_findings_serve_check_and_every_cited_member_exists() -> None:
    """RED means T-19 names a gate that is not there, or stopped naming the one that is.

    The sentence this holds is the one T-19 gained with ADR-0029's serving slice:
    that `review.findings` refuses a store this installation did not build,
    ``BuildProvenance.has_findings``, checked before the store is constructed. A
    reader auditing whether the third family is actually gated stops at that
    citation -- it is the entry's evidence, and a citation naming a member that no
    longer exists is evidence of nothing.

    Two things are asserted, and they fail for different reasons. Every
    ``BuildProvenance.<member>`` the entry cites must be a real member, so a rename
    in the class reddens here rather than leaving the record pointing at a symbol
    nobody can find; and the findings family's own reader must be among the
    citations, so a rewrite that dropped the sentence -- leaving T-19 describing
    three families and evidence for two -- does not pass quietly.
    """
    families = artifact_families(dir(BuildProvenance))
    cited = set(_CITED_MEMBER.findall(entry(_THREAT_ID)))

    assert _FINDINGS_FAMILY in families, (
        f"`{_FINDINGS_FAMILY}` is no longer one of `BuildProvenance`'s families "
        f"({list(families)}), so this module would be checking T-19's citation of a "
        f"gate that no longer gates anything; rename the constant with the family"
    )
    assert cited, (
        "T-19 cites no `BuildProvenance.<member>` at all, so the membership check "
        "below would pass over nothing -- and the entry's evidence that the "
        "provenance gate reaches every family it lists has gone with it"
    )

    missing = sorted(name for name in cited if not hasattr(BuildProvenance, name))

    assert not missing, (
        f"T-19 cites `BuildProvenance.{missing}`, which the class does not have. The "
        f"citation is what a reader auditing the control follows, so a record naming "
        f"a symbol that was renamed or removed is evidence of nothing"
    )
    assert f"{_CHECKER}{_FINDINGS_FAMILY}" in cited, (
        f"T-19 no longer cites `BuildProvenance.{_CHECKER}{_FINDINGS_FAMILY}`, the "
        f"serve-side check for the `{_FINDINGS_FAMILY}` family; it cites {sorted(cited)}. "
        f"That sentence is the entry's only evidence that the provenance gate reaches "
        f"the family ADR-0029's serving slice added"
    )
