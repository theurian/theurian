"""What decides whether a review search store is published at all (#636).

``ReviewSearchBuilder.build`` makes one decision no row of its transition table
covers: whether a *store* is published when the load it assembled is empty. An
empty load is two opposite things -- a stale build about to replace a serving
store with nothing, and an operator exercising ADR-0030 decision 3's only
retention remedy -- and the code's whole answer is **which capture the guard is
keyed on**. Keyed on the read-time projection it got both ends wrong at once: it
refused the deletion and it let a build that read nothing empty a store a landing
had just filled (#636's two faces). Keyed on ``at_the_publish`` it separates them.

That key is one identifier, and an identifier is the cheapest thing in a file to
change back. So it is pinned from both sides, and the two fail differently on
purpose:

- **the fact side** reads the guard out of the syntax tree and asks what its
  condition names. It is blind to every word of prose, and it would pass against
  a module whose docstring had been rewritten to describe the opposite decision;
- **the prose side** reads the recorded decision and the wording it replaced. It
  is blind to behaviour, and it would pass against a build that had been keyed
  back on the read while the paragraph still said otherwise.

That split is the one ``test_review_ingest_changelog_claims.py`` and
``test_adr_0030_claims.py`` record at length. Each failure message below names
the other half, because a RED on one side alone says which of the two moved.

**Both directions carry a positive control.** A pin that asserts a name is
present, and a pin that asserts a sentence is present, both read identically from
the outside whether they are looking or not. So the reverted guard is parsed and
handed to the same checker, and each superseded sentence is planted into a copy
of the module text and handed to the same checker. Nothing under ``src/`` is
written.

Pure: it reads one repository file, as text and as a syntax tree, and opens no
database, no socket and no temporary directory.
"""

from __future__ import annotations

import ast
from typing import Final

import pytest
from write_lock_claims import REPO_ROOT

pytestmark = pytest.mark.unit

BUILDER: Final = (
    REPO_ROOT / "packages/theurian-core/src/theurian/application/review_search_builder.py"
)

#: The capture the decision is keyed on: the listing taken **inside** the write
#: section, immediately before the publish. Spelled once, for both the fact arm
#: and the failure messages.
_PUBLISH_TIME_CAPTURE: Final = "at_the_publish"

#: Everything in ``build``'s own scope that describes what the **read** found, and
#: nothing else. The recorded decision is that the guard consults none of them:
#: ``entries`` is the read, ``kept`` is the read minus the withheld set,
#: ``projected`` is ``kept`` as rows, and ``before_the_read`` is the listing taken
#: on the read's own side of the window.
#:
#: ``load`` is deliberately absent: it is what survived revalidation against
#: *both* captures, so naming it is not consulting the read alone. So is
#: ``every_record_withheld``, which is the withholding outcome the decision is
#: keyed on beside the capture.
_WHAT_THE_READ_ALONE_SAW: Final = frozenset({"entries", "kept", "projected", "before_the_read"})

#: The refusal the guard raises, named here so the locator below keys on what the
#: guard *does* rather than on where it sits. A guard found by position would be
#: found in the wrong place the first time a line moved.
_RACE_REFUSAL: Final = "_emptied_by_a_race"


def _flattened(text: str) -> str:
    """*text* with its soft wraps flattened, markup and case preserved.

    The recorded decision is a docstring sentence and wraps across three source
    lines, so an unflattened search reports it missing against a module that
    carries it word for word.
    """
    return " ".join(text.split())


def _build_method(tree: ast.Module) -> ast.FunctionDef:
    """``ReviewSearchBuilder.build``, from the syntax tree.

    Reached through the class rather than by name alone, because ``build`` is a
    name several application services use and a module-level function of that
    name would be picked up by a looser search.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "ReviewSearchBuilder":
            for child in node.body:
                if isinstance(child, ast.FunctionDef) and child.name == "build":
                    return child
    raise AssertionError(
        "review_search_builder.py declares no `ReviewSearchBuilder.build`, so this "
        "module is reading a file whose shape it does not recognise. The decision "
        "pinned here lives in that method; find where it moved before relaxing this."
    )


def _raises_the_race_refusal(statement: ast.stmt) -> bool:
    """Whether *statement* is the ``raise`` that publishes the empty-load refusal."""
    return isinstance(statement, ast.Raise) and any(
        isinstance(node, ast.Name) and node.id == _RACE_REFUSAL for node in ast.walk(statement)
    )


def _empty_publish_guard(tree: ast.Module) -> ast.If:
    """The one ``if`` in ``build`` whose body raises the empty-load refusal.

    Keyed on the refusal it raises and on its **own** body rather than on a walk,
    so a nested conditional added inside the guard later does not make two
    candidates out of one.
    """
    guards = [
        node
        for node in ast.walk(_build_method(tree))
        if isinstance(node, ast.If)
        if any(_raises_the_race_refusal(statement) for statement in node.body)
    ]

    assert len(guards) == 1, (
        f"`ReviewSearchBuilder.build` has {len(guards)} conditionals raising "
        f"`{_RACE_REFUSAL}`, expected 1. Zero means the guard was removed or "
        f"restructured past this reader -- in which case nothing decides whether an "
        f"empty load replaces a serving store, which is #636's face 1 restored. More "
        f"than one means the decision is spread across branches and this pin can no "
        f"longer say which one it read."
    )
    return guards[0]


def _assert_the_guard_is_keyed_on_the_publish(guard: ast.If) -> None:
    """The fact check, in one place so its positive control drives the same path.

    Written as a helper rather than inline for the reason
    ``test_review_ingest_changelog_claims.py`` records: a control that re-derived
    an approximation of the comparison could pass while the real one had stopped
    looking.
    """
    named = {node.id for node in ast.walk(guard.test) if isinstance(node, ast.Name)}

    assert named, (
        f"the guard's condition -- `{ast.unparse(guard.test)}` -- names no variable at "
        f"all, so it is a constant and the decision is not a decision"
    )
    assert _PUBLISH_TIME_CAPTURE in named, (
        f"the empty-publish guard is keyed on {sorted(named)} and not on "
        f"`{_PUBLISH_TIME_CAPTURE}`: `{ast.unparse(guard.test)}`.\n\n"
        f"This is the FACT half of #636's fix. That capture is the listing taken "
        f"inside the write section, and it is the only thing that separates a stale "
        f"build about to empty a serving store from an operator who deleted the corpus "
        f"on purpose. Keyed on anything the read saw, both faces come back: a build "
        f"that read nothing publishes its empty store over a landing that just "
        f"finished, and a whole-corpus deletion is refused and then undone by the next "
        f"rebuild.\n\n"
        f"The record half is `test_the_build_records_the_decision_its_guard_makes` in "
        f"this module. If that is GREEN too, the paragraph now describes a decision "
        f"the code does not make."
    )
    consulted = named & _WHAT_THE_READ_ALONE_SAW
    assert not consulted, (
        f"the empty-publish guard consults {sorted(consulted)} -- what the read alone "
        f"saw -- in `{ast.unparse(guard.test)}`.\n\n"
        f"`build`'s own docstring states the decision is a function of the "
        f"publish-time capture and the withholding outcome *and of nothing the read "
        f"alone saw*. A condition that also reads the projection is the pre-#636 key "
        f"wearing an extra conjunct: it still refuses a corpus an operator emptied, "
        f"because that build's projection is non-empty."
    )


def test_the_empty_publish_guard_is_keyed_on_the_publish_time_capture() -> None:
    """RED means #636's two faces are reachable again, whatever the prose says.

    The decision that separates them is one identifier in one condition, and no
    behavioural test can say *which* identifier carries it: a guard keyed on the
    publish-time capture and one keyed on the projection agree on every ordinary
    build, and disagree only in the two windows #636 measured. Those windows have
    driving cases of their own --
    ``tests/integration/test_review_build_empty_publish.py`` through the shipped
    CLI, and ``test_review_search_builder.py``'s two rows through injected
    collaborators -- and this is the structural half: it says the anchor those
    cases rest on is still the anchor, in the file, rather than re-deriving it
    from behaviour.

    **What it holds is the condition's *names*, and that is a floor rather than a
    proof** (round one, adversarial). The check reads the identifiers in the
    ``if``'s test, so a rekey written *into* that expression is caught -- and one
    written a line above it is not: hoisting ``read_time_key = bool(projected)``
    and conjoining the name restores the pre-#636 behaviour while the condition
    still names ``at_the_publish`` and names nothing on the read list. That
    mutation was run, and only the driving cases went RED. So this arm and those
    cases are not two views of one claim: this one says the anchor is still
    written where the decision is taken, and they say the decision is still the
    right one. Neither is redundant and neither is sufficient.
    """
    tree = ast.parse(BUILDER.read_text(encoding="utf-8"), filename=BUILDER.name)

    _assert_the_guard_is_keyed_on_the_publish(_empty_publish_guard(tree))


#: The guard as it stood before #636, parsed rather than described.
#:
#: The whole pre-fix condition, not a stand-in for it: what the control has to
#: demonstrate is that this checker reports *that* key, and a synthetic condition
#: invented here could differ from it in the one way that matters.
_THE_REVERTED_GUARD: Final = """
class ReviewSearchBuilder:
    def build(self, request):
        with self._write_section():
            at_the_publish = self._list_evidence_fingerprints()
            load = ReviewSearchLoad(records=())
            every_record_withheld = bool(entries) and not kept
            if projected and not load.records and not every_record_withheld:
                raise ReviewSearchBuildError(
                    _emptied_by_a_race(read=len(entries), could_have_published=len(projected)),
                    remedy=_RACE_CURE,
                )
"""


def test_the_key_pin_reports_a_guard_keyed_back_on_the_read() -> None:
    """The positive control for the fact direction: the arm above can go RED.

    An assertion that a name appears in a condition passes perfectly against a
    locator that stopped finding the condition, or against a check widened to
    something every expression satisfies -- and it passes most convincingly at the
    moment it stops working. So the pre-fix guard is parsed and handed to the same
    locator and the same checker, and both are required to report it.

    The locator is exercised here too, which is the second thing this buys: it has
    to find a guard in a tree it has never seen, so a locator that had silently
    become "return the first ``if`` in the file" fails on the premise below rather
    than on the assertion it was written for.
    """
    reverted = _empty_publish_guard(ast.parse(_THE_REVERTED_GUARD))

    assert ast.unparse(reverted.test).startswith("projected"), (
        "the control's own guard is not the pre-#636 one, so a RED below would be "
        "about something other than the key this pin watches"
    )
    with pytest.raises(AssertionError, match=_PUBLISH_TIME_CAPTURE):
        _assert_the_guard_is_keyed_on_the_publish(reverted)


#: The decision, as ``build`` records it. One sentence, because what a reader has
#: to be told is which two things decide -- the rest of the paragraph is the case
#: table, which the arms above and the integration cases hold as behaviour.
_THE_RECORDED_DECISION: Final = (
    "**So the decision is a function of the publish-time capture and the "
    "withholding outcome, and of nothing the read alone saw.**"
)

#: What the module said before the fix, quoted rather than invented, with why each
#: one may not come back.
#:
#: **A quotation and not a commit**: a branch sha is orphaned by the squash that
#: merges it and a fresh clone answers ``fatal: bad object`` over a citation that
#: reads like a working one, which is the rule
#: ``test_review_ingest_changelog_claims.py`` records.
#:
#: The last two rows are the issue reference, and they are the **disposition
#: phrasings** rather than the link. ``#636`` stood in this file as a *live*
#: citation -- "owns the fix" twice, and "until it lands" once -- each describing
#: a defect the build still had.
#:
#: **The link alone was the row until round one, and it over-pinned** (code review
#: and adversarial, same face). A record may cite a closed issue perfectly
#: honestly: "the read-time key, which #636 recorded as a defect" is a sentence
#: this file could reasonably carry, and it would have reddened here under a
#: message telling its author the paragraph had been reverted -- a pin that is
#: wrong *and* misdiagnoses. The trade is stated rather than hidden: what is
#: forbidden now is the phrasing that says the defect is **open**, so a live
#: citation spelled some third way is not caught and the sibling rows above are
#: what stand in that case. That is the direction to err in -- a pin that cries
#: wolf is one the next author deletes.
_SUPERSEDED_WORDING: Final[tuple[tuple[str, str], ...]] = (
    (
        "the read-time key, stated as the rule",
        "The condition is **read some, kept none**",
    ),
    (
        "the heading that made an empty keep a refusal whatever the corpus did",
        "**A build that kept nothing it read publishes nothing at all.**",
    ),
    (
        "the citation that says the defect is still open",
        "[#636](https://github.com/theurian/theurian/issues/636) owns the fix",
    ),
    (
        "the sentence that told a reader to wait for the fix",
        "Until it lands, the two bullets above are what this guard does",
    ),
)


def _assert_the_module_records_the_decision(text: str) -> None:
    """The prose check, in one place so its control drives the same path."""
    assert _THE_RECORDED_DECISION in text, (
        f"review_search_builder.py no longer records:\n\n  {_THE_RECORDED_DECISION}\n\n"
        f"This is the RECORD half of #636's fix, and it is blind to behaviour. A "
        f"reader who opens this module to find out what an empty load does is reading "
        f"that paragraph; without it the case table below it is a list with no stated "
        f"key, and the next change to the guard has nothing to be measured against.\n\n"
        f"The behaviour half is "
        f"`test_the_empty_publish_guard_is_keyed_on_the_publish_time_capture` in this "
        f"module. GREEN there means the code is right and this sentence is what gets "
        f"restored."
    )
    for label, superseded in _SUPERSEDED_WORDING:
        assert superseded not in text, (
            f"review_search_builder.py carries {label} again:\n\n  {superseded}\n\n"
            f"That wording describes the build before #636, where a corpus an operator "
            f"emptied whole was refused and a build that read nothing published its "
            f"empty store over a landing that had just finished. If the guard really "
            f"moved back, the FACT half is RED too and the code is what gets restored."
        )


def test_the_build_records_the_decision_its_guard_makes() -> None:
    """RED means the module describes an era its own guard has left.

    The paragraph this holds was written in the same commit as the fix, which is
    exactly the wording most easily lost in the next rewrite: it is freshly right,
    nobody rereads what was just written, and the sentences it replaced were in
    this file for two releases.

    Both directions, because either can move on its own. The sentence has to be
    there, and none of the three superseded ones may come back -- including the
    issue link, which cited ``#636`` as a defect this build still had.
    """
    _assert_the_module_records_the_decision(_flattened(BUILDER.read_text(encoding="utf-8")))


@pytest.mark.parametrize(
    ("label", "superseded"),
    _SUPERSEDED_WORDING,
    ids=[case[0] for case in _SUPERSEDED_WORDING],
)
def test_the_prose_pin_reports_each_superseded_sentence(label: str, superseded: str) -> None:
    """The positive control for the record direction: each row can go RED.

    A row that asserts a sentence is *absent* is satisfied by a file nobody is
    reading, by a fragment that was never spelled the way the file spells it, and
    by a checker that stopped looking -- all three silently. So each superseded
    sentence is planted into a copy of the live module text and the same checker
    is asked about it.

    The plant is in memory and ``src/`` is never written. The guard before it is
    what makes the row mean something: the sentence must not already be there, or
    the plant is a no-op and the RED below would be the pin's own arm firing.
    """
    live = _flattened(BUILDER.read_text(encoding="utf-8"))

    assert superseded not in live, (
        f"{label}: the module already carries this sentence, so the plant below "
        f"changes nothing -- and `test_the_build_records_the_decision_its_guard_makes` "
        f"is the RED that matters"
    )
    with pytest.raises(AssertionError, match="carries"):
        _assert_the_module_records_the_decision(f"{live} {superseded}")
