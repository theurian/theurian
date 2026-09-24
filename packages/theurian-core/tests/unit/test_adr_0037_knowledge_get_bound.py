"""``knowledge.get``'s additions: the second instrument of ADR-0037's bound.

Decision 7 bounds what the OKF projection may publish at the union of two
measured payloads. ``test_adr_0037_claims.py`` pins the first,
``result_payload``'s published key set; ``test_adr_0037_emission_walk.py``
licenses each emission against one or the other. This module holds the second --
the four keys ``knowledge.get`` adds on top of that shape -- and, one layer
down, the teeth of the reader that measures it.

**The reader is the part worth testing here.** ``knowledge_get`` adds its keys
through four ``payload[<str>] = ...`` lines, and a reader scoped to that
spelling would report the same four while a ``payload.update({...})`` beside
them served a fifth. That is the ADR's own defect one layer down: an instrument
scoped to the shape somebody happened to use. So the reader
(``adr_0037_support.payload_mutations``) is total -- it names the keys a shape
adds, or reports the shape -- and both halves are driven below against synthetic
bodies, because the shipped tool exercises one arm of it.

Pure: it reads ``mcp/tools.py``'s AST and parses two string literals. No
database, no socket, no temporary directory.
"""

from __future__ import annotations

import ast
import textwrap
from typing import Final

import pytest
from adr_0037_support import (
    assert_the_adr_states,
    knowledge_get,
    knowledge_get_additions,
    payload_mutations,
)

pytestmark = pytest.mark.unit

#: ``knowledge.get``'s additions to ``result_payload``'s shape, as decision 7's
#: bound names them.
KNOWLEDGE_GET_ADDITIONS: Final[frozenset[str]] = frozenset(
    {"body", "relations", "structured", "integrity"}
)


def test_knowledge_get_adds_exactly_the_four_keys_the_bound_names() -> None:
    """The second instrument, measured, because the bound is only as true as it is.

    ADR-0037 names these four in prose and draws half its disclosure bound on
    them. A fifth addition widens what the export may draw from without anyone
    deciding to widen it; a removed one -- ``relations`` above all -- leaves
    ``theurian_relations`` exporting a triple the serve path no longer hands out.
    """
    additions = knowledge_get_additions()

    assert set(additions) == KNOWLEDGE_GET_ADDITIONS, (
        f"`knowledge.get` adds {sorted(additions)} to `result_payload`'s shape. "
        f"ADR-0037 draws half of decision 7's bound on exactly "
        f"{sorted(KNOWLEDGE_GET_ADDITIONS)}: a NEW addition widens the bound with no "
        f"recorded decision, a MISSING one unlicenses whatever decision 7 projects "
        f"from it. Move the bound paragraph in the same change."
    )
    assert len(additions) == len(KNOWLEDGE_GET_ADDITIONS), (
        f"`knowledge.get` adds {additions}, with a key written twice. The set "
        f"comparison above cannot see it, and `exactly four` is a count."
    )


def test_no_shape_in_knowledge_get_adds_to_the_payload_unread() -> None:
    """The measurement is only a bound if nothing adds a key behind its back.

    ``knowledge_get`` adds keys through four ``payload[<str>] = ...`` lines.
    Python has at least five other ways to put a key in that dict, and a reader
    that saw only the subscript form would report the same four while the tool
    served a fifth. So the reader reports what it could not name, and this
    asserts the report is empty.
    """
    unrecognized = payload_mutations(knowledge_get()).unrecognized

    assert unrecognized == (), (
        f"`knowledge_get` touches `payload` in ways the reader cannot name: "
        f"{list(unrecognized)}. Each is a line that may put a key in the response "
        f"without ADR-0037 decision 7's disclosure bound accounting for it. Teach the "
        f"reader the shape -- or, if the line only reads the payload, say so there."
    )


def test_the_payload_reader_names_every_addition_shape_it_recognizes() -> None:
    """The reader's own teeth, driven against shapes ``mcp/tools.py`` does not use.

    ``knowledge_get`` adds keys one way today, so six of the reader's arms never
    execute against the shipped source and would survive being deleted. They
    guard a *future* edit, which is the kind of guard that rots unreached, so
    they are driven here against a synthetic tool body instead.
    """
    source = textwrap.dedent("""
        def knowledge_get():
            payload = result_payload(revision, status, sensitivity, now)
            payload["subscripted"] = 1
            payload.update({"updated": 2})
            payload.update(keyworded=3)
            payload.setdefault("defaulted", 4)
            payload |= {"or_assigned": 5}
            payload = {**payload, "unpacked": 6}
            payload = payload | {"or_bound": 7}
            return payload
    """)

    measured = payload_mutations(ast.parse(source))

    assert measured.unrecognized == (), (
        f"the reader cannot name {list(measured.unrecognized)} in a body built out of "
        f"the shapes it claims to collect. Every one is a dict addition Python accepts, "
        f"so a shape reported here is one `knowledge.get` could use to widen the "
        f"disclosure bound while the measurement stayed at four keys."
    )
    assert set(measured.keys) == {
        "subscripted",
        "updated",
        "keyworded",
        "defaulted",
        "or_assigned",
        "unpacked",
        "or_bound",
    }, (
        f"the reader collected {sorted(measured.keys)} from a body adding seven keys "
        f"seven ways. A shape it silently drops is an addition outside the bound with "
        f"nothing RED to say so."
    )


def test_the_payload_reader_reports_a_shape_it_cannot_name() -> None:
    """The negative half: an unread shape is reported, never passed over.

    Without this, the test above is satisfied by a reader that recognizes
    everything -- including a helper that mutates the payload out of sight, which
    is the one shape no syntax tree can follow. Reporting is the honest answer
    there, and it has to be asserted or the fallthrough could return nothing and
    nobody would know.
    """
    source = textwrap.dedent("""
        def knowledge_get():
            payload = result_payload(revision, status, sensitivity, now)
            _decorate(payload)
            payload.pop("trustLevel")
            payload[chosen_key] = 8
            payload = dict(payload, sneaked=9)
            return payload
    """)

    measured = payload_mutations(ast.parse(source))

    assert set(measured.unrecognized) == {
        "_decorate(payload)",
        "payload.pop(...)",
        "payload[chosen_key]",
        "payload = dict(payload, sneaked=9)",
        "dict(payload, sneaked=9)",
    }, (
        f"the reader reported {sorted(measured.unrecognized)} over four lines that each "
        f"reach `payload` in a way it cannot name: a callee that may mutate it, a "
        f"removal, a computed key, and a rebind through `dict()` -- which reports twice, "
        f"as the rebind and as the argument. A shape it passes over silently is a key "
        f"the bound never accounts for."
    )
    assert "sneaked" not in measured.keys, (
        f"the reader collected {sorted(measured.keys)}. It must not *guess* a key out "
        f"of a shape it does not understand -- reporting the line is the answer, and a "
        f"guessed key would read as a measured one."
    )


def test_the_adr_still_states_the_second_instrument_it_measures() -> None:
    """The prose half: the sentence that makes ``knowledge.get`` an instrument.

    ``result_payload`` carries no relations and no whole body, so this sentence
    is the whole licence for decision 7's ``theurian_relations`` row, for the
    ``## Relations`` body section with it, and for the bundle carrying a body at
    all. A document that drops it leaves the emission walk licensing three
    emissions against a union the ADR never claims.

    The fragment stops short of the ADR's `mcp/tools.py` line citation: a
    renumbered citation beside a claim must not redden the claim.
    """
    assert_the_adr_states(
        "which are exactly four: **`body`** — the whole body, not the excerpt — "
        "**`relations`**, each `{relationType, targetItemId, note}` and each gated by "
        "`_relation_is_visible`, **`structured`**, and a conditional `integrity`.",
        because=(
            "`knowledge.get` is the second instrument of decision 7's disclosure bound. "
            "Without this sentence the four additions are not a measured bound at all, "
            "and everything the emission walk licenses against them is unlicensed."
        ),
    )
