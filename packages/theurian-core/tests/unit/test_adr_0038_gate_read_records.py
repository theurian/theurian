"""The records that name the reads each gate makes, held to the call sites (#832).

**The class.** Prose names a read by symbol, and nothing derived that prose from
the call site. `_relation_is_visible` moved from `get_item_exact` to
`get_item_exact_metadata` in 0.2.3 (T-26), and the records kept saying the old
one. The post-gate body reads were then written as one reader where three gates
have four, so a record that read right for one gate was false for another. Every
set a record is held to is derived from `src` by `gate_read_facts` and no table
row spells the read it expects, except the two records named below.

**Two kinds of record, two rules.**

- *R records* describe the read `_relation_is_visible` decides on. The named
  read must be the derived one (`_first`, `_last`). Two of them, the normative
  Security row and the dated T-21 Amended block, are held to the fixed name
  `FIXED_R` instead, because a record of what a future traversal must do, or of
  what 0.2.3 did, is not falsified by a later change to the gate. A separate test
  ties `FIXED_R` to the live call, so a regression in the code blames the code.
- *Post-gate records* describe what a gate reads after it clears a row. Each is
  held, per `PostGate`, to the checks `gate_read_rules` states: the body readers
  named in its scope equal the derived readers of the gate it describes, it names
  the gate's call sites, a clause naming a call site names only that site's
  reads, and its scope spells none of the phrases the pre-fix records used. The
  scope is the whole record or, where a docstring also describes `get_item` and the
  like, the sentences from an anchor.

**The keys, and their known-weak halves.** Sentences end where `record_sentences`
says, and a clause where it says. The name set is `gate_read_facts.READ_NAMES`: the
session members and `current_revision`. A record is located by a claim-free
anchor, and a missing one says "record not found", never "claim false". What the
rules cannot see:

- `_first` and `_last` are mirror rules. `_first` accepts a sentence that names the
  derived read first however it frames it (adversarial U10: "never the body-free
  `get_item_exact_metadata`, always the joined `get_item_exact`" passes), and
  `_last` one that names it last (U8: "through `get_item_exact` and never through its
  body-free form `get_item_exact_metadata`" passes). Each also accepts a stale name
  on the other side of the sentence.
- The post-gate clause rule is exclusion within a clause, so two call sites in one
  clause may swap their reads; the clause cuts are in `gate_read_rules`.
- A record's scope spells none of the phrases in `_FORBIDDEN`; a false
  claim in other words passes.
- A stale claim that names no read is held only where a rule reads its word: the
  Tests row's "body-free", through a negation window of twenty characters.
- A recorded file gaining a line in a record no table names (adversarial N1: a new
  stale line in the roadmap), any record not in the tables below, and the prose of
  `test_result_gate_session.py`, whose module docstring names reads and which no
  pin holds.

`test_adr_0038_joined_read_population.py` is the ratchet over the rest of the
tree, and it is line-keyed. The Control, part A paragraph of T-21 is the
0.1.0.dev6 record and is expected to name `get_item_exact`; only its `Amended in
#832` block is held.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

import pytest
from adr_0038_support import (
    KNOWLEDGE,
    PORT,
    ROADMAP,
    SRC,
    STORE,
    THREAT_MODEL,
)
from gate_read_facts import (
    READ_NAMES,
    TOOLS_FILE,
    Gate,
    body_readers,
    callees,
    combined,
    function,
    gates,
    joined_readers,
    tree,
)
from gate_read_rules import (
    Facts,
    PostGate,
    _block,
    _bullet,
    _comment,
    _comment_above,
    _doc,
    _lead,
    _module_doc,
    _names,
    _problems,
    _row,
)
from record_sentences import from_anchor, sentence_containing

pytestmark = pytest.mark.unit

VISIBILITY: Final = SRC + "application/visibility.py"
INDEX_BUILDER: Final = SRC + "application/index_builder.py"
ALIAS_GUARDS: Final = SRC + "application/migration_alias_guards.py"

#: The relation gate's read as the normative and dated records name it.
FIXED_R: Final = "get_item_exact_metadata"

#: The `get_item*` members, which is all the R rules read.
_ITEM_READS: Final = frozenset(name for name in READ_NAMES if name.startswith("get_item"))


def _gate_read() -> str:
    read = gates()["relation"].read
    assert len(read) == 1, f"`_relation_is_visible` makes {sorted(read)}, not exactly one read"
    return next(iter(read))


def _roadmap() -> str:
    return ROADMAP.read_text(encoding="utf-8")


def _threat_model() -> str:
    return THREAT_MODEL.read_text(encoding="utf-8")


# -- R records: the read the relation gate decides on -----------------------------


def _first(text: str, anchor: str, read: str) -> str | None:
    names = _names(from_anchor(text, anchor), _ITEM_READS)
    return None if names and names[0] == read else f"first read after {anchor!r} is {names[:1]}"


def _last(text: str, anchor: str, read: str) -> str | None:
    names = _names(from_anchor(text, anchor), _ITEM_READS)
    return None if names and names[-1] == read else f"last read after {anchor!r} is {names[-1:]}"


@dataclass(frozen=True)
class Record:
    label: str
    text: Callable[[], str]
    anchor: str
    rule: Callable[[str, str, str], str | None]
    #: `None` holds the record to the derived read; a name holds it to that name.
    fixed: str | None = None


_GATE: Final = (
    Record(
        "roadmap Phase C Security row",
        lambda: _row(_roadmap(), "| **Security** |", "A graph response is a new disclosure family"),
        "the visibility decision on each hop's endpoint",
        _first,
        fixed=FIXED_R,
    ),
    Record(
        "roadmap section 1 T-21 bullet",
        lambda: _block(_roadmap(), "T-21 was closed by two fixes"),
        "each endpoint is",
        _last,
    ),
    Record(
        "threat model T-21 Amended in #832 block",
        lambda: _block(_threat_model(), "> **Amended in [#832]"),
        "`_relation_is_visible`",
        _first,
        fixed=FIXED_R,
    ),
    Record(
        "threat model T-21 register row",
        lambda: _row(_threat_model(), "| T-21 |", "An alias key colliding"),
        "non-resolving",
        _last,
    ),
    Record(
        "CanonicalReadSession.get_item_exact docstring",
        lambda: _doc(PORT, "get_item_exact", "CanonicalReadSession"),
        "_relation_is_visible",
        _first,
    ),
    Record(
        "SqliteCanonicalStore.get_item_exact docstring",
        lambda: _doc(STORE, "get_item_exact", "SqliteCanonicalStore"),
        "`_relation_is_visible`",
        _first,
    ),
    Record(
        "migration_alias_guards module docstring",
        lambda: _module_doc(ALIAS_GUARDS),
        "``_relation_is_visible``",
        _first,
    ),
    Record(
        "index_builder visible-set comment",
        lambda: _comment(INDEX_BUILDER, "_relation_is_visible"),
        "_relation_is_visible",
        _first,
    ),
    Record(
        "index_builder relation-count docstring",
        lambda: _doc(INDEX_BUILDER, "_relation_secrets"),
        "the same answer",
        _first,
    ),
)


@pytest.mark.parametrize("record", _GATE, ids=[r.label for r in _GATE])
def test_a_record_names_the_read_the_relation_gate_makes(record: Record) -> None:
    """RED means a record names another read as the one `_relation_is_visible` makes (#832).

    Most records are held to the read derived from the call site, so moving the
    gate to another read reddens them all at once. The two held to `FIXED_R` are
    the normative and the dated ones; they redden only if their own text changes.
    """
    expected = record.fixed or _gate_read()
    text = record.text()

    failure = record.rule(text, record.anchor, expected)

    assert text.strip(), f"record not found: {record.label} is empty"
    assert failure is None, f"{record.label} does not name {expected}: {failure}"


def test_the_fixed_read_name_is_the_read_the_relation_gate_makes() -> None:
    """RED means the relation gate no longer reads through `FIXED_R`, and only this test blames it.

    The records held to `FIXED_R` state what a per-hop gate must do and what 0.2.3
    did. A gate reverted to the joined read leaves them true, so it is the code
    that is wrong, and this is the assertion that says so.
    """
    read = _gate_read()

    assert read == FIXED_R, (
        f"`_relation_is_visible` reads through {read}; the normative Security row and the "
        f"dated T-21 block still say {FIXED_R}, which is what the code must go back to"
    )


# -- Post-gate records: what a gate reads after it clears a row -------------------


def _content_check() -> Gate:
    return gates()["search"].narrowed_to(joined_readers())


def _empty() -> Gate:
    return Gate("none", frozenset(), frozenset(), {})


def _knowledge_get_and_search() -> Gate:
    return combined("knowledge.get", "search")


def _t26() -> str:
    return _block(_threat_model(), "**The fix: gate on metadata")


def _t23_control() -> str:
    return _block(
        _threat_model(), "**Control — served-content identity at the serve gate, both sides.**"
    )


def _t23_not_null() -> str:
    return _block(_threat_model(), "**The fail-closed-on-`None` handling relies on")


_POST_GATE: Final = (
    PostGate("T-26 fix lead", lambda: _lead(_t26()), _empty, sites="none"),
    PostGate(
        "T-26 fix bullet, knowledge.get",
        lambda: _bullet(_t26(), "**`knowledge.get`**"),
        lambda: gates()["knowledge.get"],
    ),
    PostGate(
        "T-26 fix bullet, search",
        lambda: _bullet(_t26(), "**Search**"),
        lambda: gates()["search"],
    ),
    PostGate(
        "T-26 fix bullet, _relation_is_visible",
        lambda: _bullet(_t26(), "**`_relation_is_visible`**"),
        lambda: gates()["relation"],
    ),
    PostGate(
        "T-26 register row",
        lambda: _row(_threat_model(), "| T-26 |", "A canonical read materialises"),
        _empty,
        exact=False,
        sites="none",
    ),
    PostGate(
        "T-23 Control paragraph",
        _t23_control,
        _content_check,
        names_gate_read=True,
    ),
    PostGate("T-23 NOT NULL paragraph", _t23_not_null, _content_check, sites="none"),
    PostGate(
        "T-21 Amended in #832 block",
        lambda: _block(_threat_model(), "> **Amended in [#832]"),
        _content_check,
        exact=False,
        contrast=True,
    ),
    PostGate(
        "KnowledgeItem.current_served_content_sha256 field comment",
        lambda: _comment_above(KNOWLEDGE, "current_served_content_sha256: ContentHash | None"),
        _content_check,
        exact=False,
    ),
    PostGate(
        "KnowledgeItem.with_revision comment",
        lambda: _comment(KNOWLEDGE, "is deliberately not set here"),
        _content_check,
        exact=False,
    ),
    PostGate(
        "CanonicalVisibility._served memo comment",
        lambda: _comment(VISIBILITY, "Memoised per item so"),
        lambda: gates()["search"],
        sites="some",
    ),
    PostGate(
        "CanonicalVisibility.cleared docstring, the reads",
        lambda: _doc(VISIBILITY, "cleared", "CanonicalVisibility"),
        lambda: gates()["search"],
        at="the body is read",
        span=2,
    ),
    PostGate(
        "CanonicalVisibility.cleared docstring, the count",
        lambda: _doc(VISIBILITY, "cleared", "CanonicalVisibility"),
        _content_check,
        at="(The body-carrying",
        sites="none",
    ),
    PostGate(
        "CanonicalVisibility._may_surface None comment",
        lambda: _comment(VISIBILITY, "a check that cannot be performed"),
        _content_check,
        sites="none",
    ),
    PostGate(
        "CanonicalReadSession.get_item_metadata docstring",
        lambda: _doc(PORT, "get_item_metadata", "CanonicalReadSession"),
        _knowledge_get_and_search,
        at="a body is read only after",
    ),
    PostGate(
        "SqliteCanonicalStore.get_item_metadata docstring",
        lambda: _doc(STORE, "get_item_metadata", "SqliteCanonicalStore"),
        _knowledge_get_and_search,
        at="a body is read only after",
        names_relation_read=True,
    ),
    PostGate(
        "CanonicalReadSession.get_item_exact docstring",
        lambda: _doc(PORT, "get_item_exact", "CanonicalReadSession"),
        _content_check,
        exact=False,
    ),
    PostGate(
        "SqliteCanonicalStore.get_item_exact docstring",
        lambda: _doc(STORE, "get_item_exact", "SqliteCanonicalStore"),
        _content_check,
        exact=False,
    ),
    PostGate(
        "_item_with_current_content_from_row docstring, the joined reads",
        lambda: _doc(STORE, "_item_with_current_content_from_row"),
        _content_check,
        at="The joined reads",
        exact=False,
    ),
    PostGate(
        "_ITEM_WITH_CURRENT_CONTENT_SQL comment",
        lambda: _comment_above(STORE, "_ITEM_WITH_CURRENT_CONTENT_SQL: Final"),
        _content_check,
        exact=False,
    ),
)


@pytest.mark.parametrize("record", _POST_GATE, ids=[r.label for r in _POST_GATE])
def test_a_record_names_the_reads_its_gate_makes_after_it_clears_a_row(record: PostGate) -> None:
    """RED means a record names the wrong read, or call site, as a gate's body read (#832).

    `knowledge.get` reads a body through `current_revision`, and search through two
    call sites: `_served_item`'s joined `get_item_exact`, for every row that
    clears, and `_surfaced`'s `get_revision`, for the first `limit`. The records
    said one reader and "once"; each is now held to the readers and call sites
    derived for the gate it describes (`gate_read_facts.gates`).
    """
    text = record.text()
    assert text.strip(), f"record not found: {record.label} is empty"

    problems = _problems(
        text,
        record,
        record.gate(),
        Facts(gates(), body_readers(), joined_readers()),
    )

    assert not problems, f"{record.label}: " + "; ".join(problems)


# -- The Tests row: "body-free" held to whether the gate's read is ----------------

_NEGATED: Final = re.compile(r"\b(?:not|never|no)\b.{0,20}body-free")


def _row_claims_body_free(row: str) -> bool:
    """Whether the row asserts a body-free read: the word, not negated within twenty characters."""
    return "body-free" in row and _NEGATED.search(row) is None


def _agrees(row: str, read: str) -> bool:
    return _row_claims_body_free(row) is (read not in body_readers())


def test_the_t21_block_names_the_reachability_read_the_lookups_make() -> None:
    """RED means the block names another read as the one the lookups resolve through.

    The sentence says Part A's reachability read is the bodyless `get_item_metadata`
    and that nothing in `src/` calls the session's `get_item`. The first is derived
    from `knowledge_get` and `CanonicalVisibility._lookup`; the second is held by
    `test_adr_0038_gate_read_facts.py`, so here `get_item` is only required to be
    the one other read the sentence names.
    """
    block = _block(_threat_model(), "> **Amended in [#832]")
    sentence = sentence_containing(block, "reachability read moved")

    names = set(_names(sentence))

    assert names == gates()["knowledge.get"].read | gates()["search"].read | {"get_item"}, (
        f"the Amended block's reachability sentence names {sorted(names)}"
    )


def test_the_roadmap_tests_row_says_body_free_exactly_when_the_gate_read_is() -> None:
    """RED means the Tests row promises a body-free hop read the gate does not make (T-26).

    Both directions are driven through the same predicate: the live row against
    the live read, and against the joined read, which selects a body and so must
    disagree. Negation is read within twenty characters of the word (`not`,
    `never`, `no`); a longer detour, or a negation spelled another way, is not seen.
    """
    row = _row(_roadmap(), "| **Tests** |", "Equality extension")

    assert _agrees(row, _gate_read()), (
        f"the Tests row and the gate's read ({_gate_read()}) disagree on whether the hop read "
        "is body-free"
    )
    assert not _agrees(row, "get_item_exact"), (
        "the row's claim also agrees with the joined read, so the comparison cannot fail"
    )
    assert not _row_claims_body_free("goes through a read which need not be body-free"), (
        "a negated 'body-free' must not count as the claim (adversarial U11)"
    )
    assert _row_claims_body_free("goes through the non-resolving, body-free read path"), (
        "control: the row's own wording is the claim"
    )


# -- The Phase C Security row: the properties an equivalent must keep -------------


def test_the_security_row_names_the_properties_of_the_relation_gate_an_equivalent_must_keep() -> (
    None
):
    """RED means the row asks a per-hop gate for properties the relation gate does not have (#832).

    ADR-0038's *Positive* lists three properties beside the two the row already
    named: both endpoints judged on status and sensitivity, a missing endpoint
    withheld, and the read scoped to the project. The prose side is read here and
    the fact side is held by pins that already exist:
    `test_adr_0038_model.py::test_the_relation_gate_reads_both_endpoints_through_the_non_resolving_metadata_read`
    (the loop over both endpoints, `may_surface` and `may_disclose` called) and
    `::test_the_relation_gate_withholds_a_missing_endpoint_and_reads_within_the_project`
    (a `None` endpoint returns False, the scoped argument tuple). Not held: that a
    future traversal keeps them; only that the row asks.
    """
    row = _row(_roadmap(), "| **Security** |", "A graph response is a new disclosure family")
    asked = from_anchor(row, "An equivalent must also keep")
    relation = gates()["relation"]

    assert {"may_surface", "may_disclose"} <= callees(
        function(tree(TOOLS_FILE), "_relation_is_visible")
    ), "the relation gate no longer calls both axes, which the row asks a hop to keep"
    assert "`may_surface`" in asked and "`may_disclose`" in asked, (
        "the row does not name both axes the relation gate judges an endpoint on"
    )
    assert "a missing endpoint withheld" in asked, (
        "the row does not ask for a missing endpoint withheld"
    )
    assert "`_ITEM_METADATA_SQL`'s `project_id`" in asked, (
        "the row does not name the project scope, and where the gate takes it from"
    )
    assert relation.sites == {"_relation_is_visible"}, (
        "the relation gate moved off `_relation_is_visible`"
    )
