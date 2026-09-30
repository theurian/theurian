"""The rules `gate_read_rules` holds a record by, driven over synthetic text (#832).

Every rule runs against a sentence it must accept and the shape it exists to
reject, because a rule that has only ever met the tree's own prose cannot be told
from one that reddens on everything. The tree's records are held in
`test_adr_0038_gate_read_records.py`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Final

import pytest
from gate_read_facts import Gate
from gate_read_rules import Facts, PostGate, _block, _comment, _doc, _problems
from record_sentences import (
    SENTENCE_END,
    from_anchor,
    sentence_containing,
    sentences,
)

pytestmark = pytest.mark.unit

_VISIBILITY: Final = "packages/theurian-core/src/theurian/application/visibility.py"

# -- Driving the rules: each shape they exist for, beside the shape that must pass --

_SYNTHETIC_SEARCH: Final = Gate(
    "search",
    frozenset({"get_item_metadata"}),
    frozenset({"_served_item", "_surfaced"}),
    {"_served_item": frozenset({"get_item_exact"}), "_surfaced": frozenset({"get_revision"})},
)
_SYNTHETIC_RELATION: Final = Gate(
    "relation",
    frozenset({"get_item_exact_metadata"}),
    frozenset({"_relation_is_visible"}),
    {},
    decides=True,
)
_SYNTHETIC: Final = {"search": _SYNTHETIC_SEARCH, "relation": _SYNTHETIC_RELATION}
_GOOD: Final = (
    "A body is read after the gate clears: `_served_item` reads `get_item_exact`, and "
    "`_surfaced` reads `get_revision`; `_relation_is_visible` reads `get_item_exact_metadata`."
)


def _drive(text: str) -> list[str]:
    return _problems(
        text,
        PostGate("synthetic", lambda: text, lambda: _SYNTHETIC_SEARCH),
        _SYNTHETIC_SEARCH,
        Facts(
            _SYNTHETIC,
            frozenset({"get_item", "get_item_exact", "get_revision"}),
            frozenset({"get_item", "get_item_exact"}),
        ),
    )


@pytest.mark.parametrize(
    ("what", "text", "fragment"),
    [
        (
            "a wrong reader for the content check",
            _GOOD.replace(
                "`_served_item` reads `get_item_exact`", "`_served_item` reads `get_item`"
            ),
            "names the body readers",
        ),
        ("a missing call site", _GOOD.replace("`_surfaced`", "it"), "names ['_served_item']"),
        (
            "the readers swapped between the two call sites",
            "A body is read after the gate clears: `_served_item` reads `get_revision`, and "
            "`_surfaced` reads `get_item_exact`.",
            "a clause naming",
        ),
        (
            "the relation gate named with the joined read",
            _GOOD.replace(
                "`_relation_is_visible` reads `get_item_exact_metadata`",
                "`_relation_is_visible` reads `get_item_exact`",
            ),
            "a clause naming ['_relation_is_visible']",
        ),
        ("the word once", _GOOD.replace("is read after", "is read once after"), "spells"),
        (
            "the content check spelled by its role",
            _GOOD.replace("`_served_item` reads", "the GHSA-3f65 content check reads"),
            "names ['_surfaced']",
        ),
    ],
)
def test_the_post_gate_rules_go_red_on_each_shape_they_exist_for(
    what: str, text: str, fragment: str
) -> None:
    """A rule that cannot fail is what this file prevents; each is driven both ways."""
    assert _drive(_GOOD) == [], "control: the correct sentence must satisfy every rule"

    problems = _drive(text)

    assert any(fragment in problem for problem in problems), (
        f"{what}: no problem contains {fragment!r}; got {problems}"
    )


def test_a_role_phrase_names_the_content_check_as_well_as_its_symbol() -> None:
    """The content check is often named by its role; a swap there is a swap (adversarial U16)."""
    swapped = (
        "A body is read after the gate clears, through `get_revision` by the GHSA-3f65 "
        "content check, and through `get_item_exact` by `_surfaced`."
    )

    problems = _drive(swapped)

    assert any("a clause naming" in problem for problem in problems), (
        f"a swapped role phrase went unread: {problems}"
    )


@pytest.mark.parametrize(
    "locate",
    [
        lambda: _doc(_VISIBILITY, "cleared_rows", "CanonicalVisibility"),
        lambda: _doc(_VISIBILITY, "cleared", "NoSuchClass"),
        lambda: _block("one block\n\nanother", "an anchor no block carries"),
        lambda: _comment(_VISIBILITY, "a comment no run carries"),
        lambda: from_anchor("one sentence.", "absent"),
        lambda: sentence_containing("one sentence.", "absent"),
    ],
    ids=["function", "class", "block", "comment", "anchor", "sentence"],
)
def test_a_locator_that_finds_nothing_says_record_not_found(locate: Callable[[], object]) -> None:
    """A renamed function must read as a missing record, never as a false claim (code M2)."""
    with pytest.raises(pytest.fail.Exception, match="record not found"):
        locate()


def test_a_sentence_ends_at_each_boundary_the_shared_key_names() -> None:
    """The boundary is one definition for this module and `test_ports.py` (#832 LOW)."""
    text = "One **bold.** Two (paren.) Three *ital.* Four. 0.2.3 stays whole. End"

    assert sentences(text) == [
        "One **bold.**",
        "Two (paren.)",
        "Three *ital.*",
        "Four.",
        "0.2.3 stays whole.",
        "End",
    ], "the boundary reads a full stop followed by space or the end, with `**`, `)` or `*`"
    assert SENTENCE_END.search("a.b") is None, "a full stop inside a token is not a boundary"
