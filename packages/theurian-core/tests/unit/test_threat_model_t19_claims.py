"""T-19's artifact families, its findings serve check and its residual, held to the code.

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

**The residual is held too, and it is a different kind of claim.** T-19's
residual paragraph says provenance vouches for a *hash*, which is true of the two
families whose file names carry the very id the serve path checks and is not true
of the third: ``theurian findings build`` writes one store under the constant
``FINDINGS_STORE_ID``, so ``BuildProvenance.has_findings`` records a per-root
boolean and the replacement window is not narrowed there by an id mismatch.
https://github.com/theurian/theurian/pull/504 added that qualification. (4) The
paragraph must keep it, and (5) the code must keep making it true -- the serve
gate asks about the shared module constant rather than a per-build id, and the
constant is a written literal rather than something computed. The day the
findings family gains a per-build id, the qualification is false and has to move;
that is the day this goes RED.

**What it does not hold.** That the entry *describes* any family correctly --
naming an artifact prefix is not saying anything true about it -- nor that the
serve path it names actually calls the member it cites; that is a property of
``mcp/tools.py``, pinned behaviourally by
``tests/integration/test_review_findings_tool.py``. The residual arms read the
call site rather than run it, for the same reason: that a store this installation
did not build is really refused is behaviour, pinned by
``test_review_findings_tool.py`` and
``test_findings_store_reads_are_governed.py``. Nor does it cover T-19's
delivery-independence or laundering paragraphs, which make their own claims and
have no pin here.

**The naming key is a convention, and a RED on it is not automatically a prose
defect.** Each family's artifacts are filed under ``theurian-<family>-*.sqlite``
(``ProjectPaths.database_for``, ``index_for``, ``findings_for``), which is how
T-19 enumerates them, so the family token derived from the class is also the
string the entry must carry. A future family filed under some other name takes
this RED with the entry innocent, and the answer then is to say in the entry how
that family is named -- not to delete the pin.

Pure in the sense the other claim pins are: one document and two source modules
read as text, and one class read for its member names -- no database, socket or
temporary directory.
"""

from __future__ import annotations

import ast
import pathlib
import re
from collections.abc import Iterable
from typing import Final

import pytest
from threat_model_claims import SPELLED_NUMBERS, WORD_FOR_COUNT, entry, prose
from write_lock_claims import REPO_ROOT

from theurian.application.project_service import FINDINGS_STORE_ID, BuildProvenance

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

#: What identifies T-19's residual paragraph. Keyed on the phrase that says what
#: the paragraph *is*, not on anything it says about the findings family: a key
#: naming the qualification would be satisfied by its own subject, so deleting the
#: qualification would drop the paragraph out of the population rather than fail.
_RESIDUAL_ANCHOR: Final = "residual, recorded rather than closed"

#: The general claim the findings qualification qualifies. Asserted as a premise
#: so the arm below is never checking an exception to a rule the entry has stopped
#: stating -- a paragraph that no longer says provenance vouches for a hash needs
#: its findings sentence rewritten, not preserved.
_HASH_CLAIM: Final = "vouches for a hash"

#: What the residual has to say the findings family's provenance record *is*.
#: This is the difference from the other two families and the whole of the
#: correction: a boolean per project root, not an id that a substituted build
#: would fail to match.
_PER_ROOT_RECORD: Final = "per-root boolean"

#: The constant ``theurian findings build`` writes the store under, by name.
#: Spelled out rather than derived -- a Python identifier is not something the
#: value knows about itself -- and guarded below by reading it out of the module
#: that defines it, so a rename fails naming itself.
_STORE_ID_CONSTANT: Final = "FINDINGS_STORE_ID"

#: The two modules the residual arms read as source. The serve gate is in
#: ``mcp/tools.py``; the constant it passes is defined in ``project_service.py``,
#: which is also where a per-build id would have to appear.
_TOOLS_MODULE: Final = REPO_ROOT / "packages/theurian-core/src/theurian/mcp/tools.py"
_PROJECT_SERVICE_MODULE: Final = (
    REPO_ROOT / "packages/theurian-core/src/theurian/application/project_service.py"
)

#: Where ``mcp/tools.py`` has to be importing the constant from for the name at
#: the call site to be the shared one. Without this, a module-local
#: ``FINDINGS_STORE_ID = "local"`` would satisfy the call-site arm while being a
#: second spelling that drifts from the writer's.
_STORE_ID_HOME: Final = "theurian.application.project_service"


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


def _residual_block() -> str:
    """T-19's one residual paragraph, normalised.

    Scoped to the paragraph for the reason :func:`_enumeration_block` is: the
    entry names ``FINDINGS_STORE_ID`` in its opening paragraph too, where it
    explains why no pointer names the findings store. An entry-wide scan would let
    that sentence stand in for the residual's qualification, so deleting the
    qualification -- which is the whole of what this holds -- would pass on the
    strength of a sentence about pointers.
    """
    blocks = [prose(block) for block in _BLOCK_BREAK.split(entry(_THREAT_ID)) if block.strip()]
    carrying = [block for block in blocks if _RESIDUAL_ANCHOR in block]

    assert len(carrying) == 1, (
        f"`{_RESIDUAL_ANCHOR}` identifies {len(carrying)} of T-19's {len(blocks)} "
        f"paragraphs, expected 1. Zero means the residual was reworded past its own "
        f"anchor and everything below would pass over nothing; more than one means "
        f"what is read below is text this module never chose"
    )
    return carrying[0]


def _parsed(source: pathlib.Path) -> ast.Module:
    """*source* as a syntax tree.

    Read as source rather than imported, and for a different reason than
    ``test_threat_model_t7_claims.py``'s AST reader has: what is checked below is
    *which expression appears at a call site*, and importing the module yields the
    call's result, never the call. A running import would also make a pin over the
    daemon's tool surface depend on that surface importing cleanly.
    """
    return ast.parse(source.read_text(encoding="utf-8"), filename=str(source))


def _calls_to_method(tree: ast.Module, method: str) -> list[ast.Call]:
    """Every ``<something>.method(...)`` call in *tree*, in source order.

    Matched on the attribute name alone. The receiver is deliberately not
    constrained: the pin is that whatever asks this question asks it about the
    shared constant, and a check that only recognised one spelling of the receiver
    would stop seeing the call the moment it was reached through another name.
    """
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == method
    ]


def _imports_name_from(tree: ast.Module, module: str, name: str) -> bool:
    """Whether *tree* binds *name* by importing it from *module*."""
    return any(
        node.module == module and any(alias.name == name for alias in node.names)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    )


def _module_level_literal(source: pathlib.Path, name: str) -> object:
    """The literal value assigned to *name* at *source*'s top level.

    Both ``name: Final = ...`` and a bare ``name = ...`` are accepted, so the pin
    fails on the claim rather than on an annotation style someone changed.

    ``test_threat_model_t7_claims.py`` reads a top-level assignment the same way
    and accepts a different shape. Neither reader is in ``threat_model_claims``
    because a shared one would have to be general enough for both, which is more
    surface than either entry needs -- and the shape each accepts *is* the claim it
    holds, so generalising it would delete the assertion.
    """
    assigned: list[ast.expr] = []
    for node in _parsed(source).body:
        if isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == name and node.value:
                assigned.append(node.value)
        elif isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            assigned.append(node.value)

    assert len(assigned) == 1, (
        f"`{name}` is assigned {len(assigned)} times at the top level of "
        f"{source.name}, expected once. Zero means it was renamed, moved or is now "
        f"computed rather than written, and this module can no longer read the "
        f"constant it says it checks"
    )
    value = assigned[0]
    assert isinstance(value, ast.Constant), (
        f"`{name}` is assigned a `{type(value).__name__}` at {source.name}'s top "
        f"level, not a written literal. T-19's residual turns on this being a "
        f"constant: a computed or per-build id is exactly what would make "
        f"`BuildProvenance.has_findings` record something other than a per-root "
        f"boolean, and the paragraph would then be describing the wrong control"
    )
    return value.value


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


def test_t19s_residual_says_the_findings_family_is_keyed_by_a_constant_not_a_hash() -> None:
    """RED means T-19 is back to vouching for a hash the findings family has not got.

    The residual is where a reader learns what provenance does *not* protect
    against, and it reasons from an id: a database swapped in after this
    installation built the matching hash is out of scope, because a substitution
    under a different id finds no record. That reasoning does not carry to the
    findings family. One store is written under one constant, so what
    ``has_findings`` records is a boolean per project root -- true for whatever
    bytes later occupy that name -- and the id mismatch that narrows the window for
    the other two families narrows nothing here.

    Losing the qualification is not a wording regression: it leaves a reader
    believing the findings store's post-build replacement window is as narrow as
    the index's, and sizing their trust in a repository's derived state on that.
    The paragraph is located by what it *is*, and its general claim is asserted as
    a premise, so an exception is never checked against a rule the entry stopped
    stating.
    """
    text = _residual_block()

    assert _HASH_CLAIM in text, (
        f"T-19's residual no longer says provenance `{_HASH_CLAIM}`, so the "
        f"qualification checked below has nothing to qualify. Whatever the paragraph "
        f"says instead has to be reconciled with the findings family by hand -- this "
        f"pin cannot tell whether the exception still applies: {text[:400]}"
    )

    assert _STORE_ID_CONSTANT.lower() in text, (
        f"T-19's residual no longer names `{_STORE_ID_CONSTANT}`, the constant "
        f"`theurian findings build` writes the store under. Without it the paragraph "
        f"reads as if every family's provenance were keyed by a hash a substitution "
        f"would fail to match, which is not true of the findings store: {text[:400]}"
    )
    assert _PER_ROOT_RECORD in text, (
        f"T-19's residual no longer records that `BuildProvenance.{_CHECKER}"
        f"{_FINDINGS_FAMILY}` holds a `{_PER_ROOT_RECORD}`. That is the whole "
        f"difference from the other two families -- their file names carry the id the "
        f"serve path checks, so a substitution under a different id finds no record, "
        f"and here there is no id to differ: {text[:400]}"
    )


def test_the_findings_serve_gate_is_asked_about_the_shared_store_id_constant() -> None:
    """RED means the findings family gained an id, and T-19's residual just went false.

    The residual's exception is true only while the question the serve path asks is
    keyed by a constant. The moment ``has_findings`` is asked about a per-build id
    -- a content hash, a build ulid, anything a substituted store would fail to
    match -- the findings family stops recording a per-root boolean, the window the
    paragraph describes as un-narrowed is narrowed, and the sentence has to move.
    That is a change worth catching at the call site rather than a defect: this
    fails so the record follows the code, in whichever direction the code went.

    Read from source, because what is asserted is the *expression* at the call
    site. Running the call would yield a string, and a string cannot say whether it
    came from the shared constant or from a literal typed a second time -- which is
    the drift the constant exists to prevent, and the one the writer and the reader
    would not notice until a project reported an empty store it has.

    Two premises. Exactly one place may ask the question, or "the call site" names
    nothing; and the name at that site must be bound by importing it from the
    module that defines it, or a module-local respelling would satisfy this arm
    while being the second spelling.
    """
    tree = _parsed(_TOOLS_MODULE)
    calls = _calls_to_method(tree, f"{_CHECKER}{_FINDINGS_FAMILY}")

    assert len(calls) == 1, (
        f"{_TOOLS_MODULE.name} makes {len(calls)} `{_CHECKER}{_FINDINGS_FAMILY}(...)` "
        f"calls, expected 1. Zero means the serve gate moved or was removed and this "
        f"arm would pass over nothing; more than one means the id checked below is "
        f"whichever call came first in the tree"
    )
    assert _imports_name_from(tree, _STORE_ID_HOME, _STORE_ID_CONSTANT), (
        f"{_TOOLS_MODULE.name} does not import `{_STORE_ID_CONSTANT}` from "
        f"`{_STORE_ID_HOME}`, so a name of that spelling at the call site would be a "
        f"second constant rather than the one `theurian findings build` writes under. "
        f"Two spellings drift, and the failure is silent: the read opens a path "
        f"nothing writes and reports a missing store for a project that has one"
    )

    passed = [*calls[0].args, *(keyword.value for keyword in calls[0].keywords)]
    names = {node.id for node in passed if isinstance(node, ast.Name)}
    literals = [node.value for node in passed if isinstance(node, ast.Constant)]

    assert _STORE_ID_CONSTANT in names, (
        f"the `{_CHECKER}{_FINDINGS_FAMILY}` call is not passed `{_STORE_ID_CONSTANT}`; "
        f"it is passed {sorted(names)} and {literals}. T-19's residual says this "
        f"family is keyed by a constant, which is why the record it checks is a "
        f"per-root boolean -- an id that varies makes that sentence false"
    )
    assert not literals, (
        f"the `{_CHECKER}{_FINDINGS_FAMILY}` call is passed the literal(s) {literals} "
        f"where the shared constant belongs. A respelled id reads identically and "
        f"drifts the first time either place is reworded, and the resulting failure is "
        f"a store reported missing for a project that built one"
    )


def test_the_findings_store_id_is_a_written_constant_and_not_a_per_build_value() -> None:
    """RED means the id the residual calls a constant is computed after all.

    The call-site arm above says the serve gate is handed ``FINDINGS_STORE_ID``.
    That is only half the claim: a ``FINDINGS_STORE_ID`` computed per build -- from
    a content hash, a timestamp, the head commit -- would satisfy it while turning
    the findings family into exactly the id-keyed shape the residual says it is
    not. So the definition is read as well, and it has to be a written literal.

    The value is compared to the imported one, so a definition this module can read
    but the product does not use -- a second assignment inside a branch, a name
    rebound at import -- fails here rather than leaving the AST arm describing a
    line nothing runs.
    """
    literal = _module_level_literal(_PROJECT_SERVICE_MODULE, _STORE_ID_CONSTANT)

    assert isinstance(literal, str), (
        f"`{_STORE_ID_CONSTANT}` is written as `{literal!r}`, a "
        f"{type(literal).__name__}. The findings store's file name is built from it "
        f"(`ProjectPaths.findings_for`) and the provenance record is keyed by it, so "
        f"anything but a string is a different control from the one T-19 describes"
    )

    assert literal == FINDINGS_STORE_ID, (
        f"{_PROJECT_SERVICE_MODULE.name} writes `{_STORE_ID_CONSTANT}` as "
        f"`{literal!r}` at module level, and importing it yields "
        f"`{FINDINGS_STORE_ID!r}`. The name is rebound somewhere this pin does not "
        f"read, so what the serve gate is handed is not the literal checked here"
    )
