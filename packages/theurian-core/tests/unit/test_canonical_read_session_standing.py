"""ADR-0003's open question on `CanonicalReadSession`: its facts and records (#832, #865).

Round one found the row's headline ("mostly a narrowing of `CanonicalStore`, not a
second substitution point") and last sentence ("what an operator substitutes is
still a `CanonicalStore` adapter") stated against facts that had moved: the session
widens the port by three reads, the two gates make three of their four session
reads through them, and an adapter of exactly the port's members is not an instance
of it. Those facts, and the standing as an open question decided on #865, are what
the records now state; they assert neither the old conclusion nor its opposite.

**Held.** Facts: the session is not in `ALL_PORTS`; a `CanonicalStore`-only adapter
is not an instance and a complete one is; the gate path's session reads, derived
from `_relation_is_visible` and every method of `CanonicalVisibility` and of
`ResultGate` (the scopes `gate_read_facts.gates()` walks), are four, three of them
widening, split as search {`get_item_metadata`, `get_item_exact`, `get_revision`}
and `_relation_is_visible` {`get_item_exact_metadata`}; `Callable[[Path],
CanonicalReadSession]` is the only session-factory annotation in `src`; and
`_relation_is_visible` has one call site, in `knowledge_get` (those two scans
live in `test_adr_0038_gate_read_facts.py`, whose pin glob allows the scan
helpers). Prose: the ADR-0003
row opens with the exact phrase "An open question, recorded here rather than
settled." and links #865, the class docstring opens with "Not in the register, and
whether it belongs there is open.", neither says the question is settled, none of
the four records of the standing states the opposite judgement (`_OPPOSITE`), the
`IndexBuildSession` row follows it there, and the row and the class docstring each
name the derived reads, per path.

When #865 settles the standing, either answer reddens a fact (joining the register)
or a prose pin (the exact phrase goes), so the record cannot be left behind.

**Known-weak halves.** The gate-path sentence is located by the phrase "the SEC-13
gate path reads through"; rewording it reads as "record not found". A read reached
through a function outside those scopes is not in the derived set. `_SETTLED` and
`_OPPOSITE` are phrase lists: a settled or opposite judgement in other words passes
them.
"""

from __future__ import annotations

import ast
import inspect
import re
from typing import Final

import pytest
from gate_read_facts import (
    READ_NAMES,
    REPO_ROOT,
    RETRIEVAL_FILE,
    SESSION_MEMBERS,
    TOOLS_FILE,
    VISIBILITY_FILE,
    callees,
    function,
    klass,
    tree,
)
from gate_read_rules import _block
from record_sentences import flat, from_anchor, not_found, sentence_with

from theurian.domain import ports
from theurian.domain.ports import canonical_store
from theurian.domain.ports.canonical_store import CanonicalReadSession, CanonicalStore

pytestmark = pytest.mark.unit

ADR_0003: Final = REPO_ROOT / "docs" / "adr" / "0003-ports-and-adapters.md"
ROADMAP: Final = REPO_ROOT / "docs" / "roadmap.md"
_NUMBER_WORDS: Final = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five"}


def _members(protocol: type) -> frozenset[str]:
    return frozenset(name for name in dir(protocol) if not name.startswith("_"))


def _widening() -> frozenset[str]:
    return _members(CanonicalReadSession) - _members(CanonicalStore)


def _session_reads(*scopes: ast.AST) -> frozenset[str]:
    return frozenset().union(*(callees(scope) for scope in scopes)) & SESSION_MEMBERS


def _search_path_reads() -> frozenset[str]:
    """The session members called anywhere in `CanonicalVisibility` or `ResultGate`."""
    return _session_reads(
        klass(tree(VISIBILITY_FILE), "CanonicalVisibility"),
        klass(tree(RETRIEVAL_FILE), "ResultGate"),
    )


def _relation_path_reads() -> frozenset[str]:
    return _session_reads(function(tree(TOOLS_FILE), "_relation_is_visible"))


def _gate_path_reads() -> frozenset[str]:
    return _search_path_reads() | _relation_path_reads()


def _noop(*_arguments: object, **_keywords: object) -> None:
    return None


def _spans(text: str) -> set[str]:
    return set(re.findall(r"`+([A-Za-z_][A-Za-z0-9_]*)`+", text))


def test_the_read_session_is_outside_the_register_and_a_store_only_adapter_is_not_one() -> None:
    """RED means a fact ADR-0003's open question rests on has moved, so its records must move.

    #865 decides whether the session joins `ALL_PORTS`. Either answer reddens
    this: joining makes the first assertion false, and settling it outside makes
    the prose pins below false. The last two assertions are the facts the records
    state about it, with no judgement of what they make the session: `CanonicalStore`'s
    members alone do not satisfy it, and a complete adapter does.
    """
    store_only = type("StoreOnly", (), dict.fromkeys(_members(CanonicalStore), _noop))
    complete = type(
        "Complete",
        (),
        dict.fromkeys({*_members(CanonicalReadSession), "__enter__", "__exit__"}, _noop),
    )

    assert CanonicalReadSession not in ports.ALL_PORTS, (
        "`CanonicalReadSession` is in `ALL_PORTS`: #865 settled it, so ADR-0003's row and the "
        "class docstring say 'open' about a question that is closed"
    )
    assert _widening() == {"get_item_exact", "get_item_metadata", "get_item_exact_metadata"}, (
        f"the session's widening reads are {sorted(_widening())}"
    )
    assert not isinstance(store_only(), CanonicalReadSession), (
        "an adapter of exactly `CanonicalStore`'s members satisfies the session: it is a narrowing"
    )
    assert isinstance(complete(), CanonicalReadSession), (
        "control: an adapter with every member is not recognised, so the check above proves nothing"
    )


def test_the_gate_path_reads_three_widening_reads_of_four_and_never_the_narrowed_get_item() -> None:
    """RED means the gate path's session reads, or how many widen, differ from the records'."""
    path = _gate_path_reads()

    assert path == {
        "get_item_metadata",
        "get_item_exact",
        "get_item_exact_metadata",
        "get_revision",
    }, f"the gate path reads through {sorted(path)}"
    assert path & _widening() == {
        "get_item_metadata",
        "get_item_exact",
        "get_item_exact_metadata",
    }, f"the gate path's widening reads are {sorted(path & _widening())}"
    assert "get_item" not in path, "the gate path calls the narrowed `get_item`"


def _adr_row(name: str) -> str:
    rows = [
        line.strip()
        for line in ADR_0003.read_text(encoding="utf-8").splitlines()
        if line.lstrip().startswith(f"> | `{name}` |")
    ]
    if len(rows) != 1:
        not_found(f"{len(rows)} rows of ADR-0003 start `{name}`")
    return rows[0]


#: The exact bold phrases that record the standing as open; #865 is what removes them.
_ROW_OPENS: Final = "**An open question, recorded here rather than settled.**"
_DOC_OPENS: Final = "**Not in the register, and whether it belongs there is open.**"


def _gate_path_clause(text: str) -> str:
    return from_anchor(text, "the SEC-13 gate path reads through").split(";")[0]


def test_the_open_question_is_recorded_in_the_adr_row_and_the_class_docstring_with_its_issue() -> (
    None
):
    """RED means a record of the session's standing says settled, or cites another issue (#865).

    Located by the row's key and the docstring's own sentence; each must open by
    calling the standing open and link #865. The gate-path sentence in each names
    exactly the reads the AST finds and spells how many of them widen.
    """
    row = _adr_row("CanonicalReadSession")
    cell = row.split("|", 2)[2].strip()
    doc = inspect.getdoc(CanonicalReadSession) or ""
    module_doc = inspect.getdoc(canonical_store) or ""
    issue = "https://github.com/theurian/theurian/issues/865"
    reads = _gate_path_reads()
    widening_count = _NUMBER_WORDS[len(reads & _widening())]

    assert cell.startswith(_ROW_OPENS), (
        "ADR-0003's `CanonicalReadSession` row no longer opens with the exact phrase "
        "that records the standing as open"
    )
    assert issue in row, "the row does not link #865"
    assert issue in _adr_row("IndexBuildSession"), (
        "the `IndexBuildSession` row does not follow it to #865"
    )
    assert _DOC_OPENS in doc, (
        "the class docstring no longer opens with the exact phrase that records the standing "
        "as open"
    )
    assert "issues/865" in doc, "the class docstring does not link #865"
    assert re.search(r"\bis open\b", module_doc) and "#865" in module_doc, (
        "the module docstring does not say the question is open and cite #865"
    )
    for label, text in (("the row", row), ("the class docstring", doc)):
        clause = _gate_path_clause(text)
        assert _spans(clause) & READ_NAMES == reads | {"get_item"}, (
            f"{label} names {sorted(_spans(clause) & READ_NAMES)} on the gate path; the AST finds "
            f"{sorted(reads)}, and `get_item` is named as the read with no caller"
        )
    assert f"{widening_count} of them widening" in flat(_gate_path_clause(row)), (
        f"the row does not spell how many gate-path reads widen ({widening_count})"
    )


#: What the row and the class docstring concluded before #832, in the words they used.
_OLD_CONCLUSION: Final = (
    r"Mostly a narrowing",
    r"not a second substitution point",
    r"opens no boundary",
    r"still an? (?::class:)?`CanonicalStore` adapter",
)


def test_no_standing_record_still_concludes_that_the_session_opens_no_boundary() -> None:
    """RED means a record of the session's standing says again what round one found false.

    The row, its `IndexBuildSession` neighbour, the class docstring and the module
    docstring are read; ADR-0003's amendment and the roadmap's discharge bullet
    quote the old conclusion as history and are not. Reverting the class
    docstring's closing sentence ("what an operator substitutes is still a
    `CanonicalStore` adapter ... opens no boundary") survived the first pin
    (hunk-revert measurement, this file's round).
    """
    old = (
        "What an operator substitutes is still a `CanonicalStore` adapter either way, so "
        "this opens no boundary the register does not already govern."
    )
    records = {
        "the ADR-0003 row": _adr_row("CanonicalReadSession"),
        "the IndexBuildSession row": _adr_row("IndexBuildSession"),
        "the class docstring": inspect.getdoc(CanonicalReadSession) or "",
        "the module docstring": inspect.getdoc(canonical_store) or "",
    }

    found = {
        label: [p for p in _OLD_CONCLUSION if re.search(p, flat(text), re.IGNORECASE)]
        for label, text in records.items()
    }

    assert [p for p in _OLD_CONCLUSION if re.search(p, old)], (
        "control: the patterns do not see the pre-fix sentence"
    )
    assert not any(found.values()), f"a record repeats the pre-fix conclusion: {found}"


def test_the_roadmaps_reopened_bullet_links_the_issue_and_counts_the_gate_path_reads() -> None:
    """RED means the roadmap's note that the discharge fails cites another issue or count."""
    block = _block(ROADMAP.read_text(encoding="utf-8"), "**Reopened in [#832]")
    count = _NUMBER_WORDS[len(_gate_path_reads() & _widening())]

    assert "issues/865" in block, "the roadmap's reopened bullet does not link #865"
    assert f"through {count} session reads" in flat(block), (
        f"the roadmap's reopened bullet does not spell {count} session reads on the gate path"
    )


# -- The standing is left to #865: neither settled nor opposite --------------------

#: Wording that settles the question #865 owns, each in the form a settled record would use.
_SETTLED: Final = (
    r"settled it",
    r"no longer open",
    r"keeps it out",
    r"(?<!whether it )(?<!whether that )\bjoins the register",
)

#: Wording of the opposite judgement: the headline's negation, which round two found the
#: rewrite had asserted instead of leaving to #865.
_OPPOSITE: Final = (
    r"not a narrowing",
    r"more than a CanonicalStore adapter",
    r"substitutes more",
    r"does not hold",
    r"conclusion (?:is |was )?false",
    r"did not hold",
)

#: One sentence each pattern must match, so a pattern that matches nothing cannot pass.
_EXAMPLES: Final = {
    r"settled it": "#865, which settled it, keeps the session out.",
    r"no longer open": "Whether it belongs there is no longer open.",
    r"keeps it out": "#865 keeps it out of the register.",
    r"(?<!whether it )(?<!whether that )\bjoins the register": "It joins the register.",
    r"not a narrowing": "Where it matters it is not a narrowing.",
    r"more than a CanonicalStore adapter": (
        "What an operator substitutes is therefore more than a `CanonicalStore` adapter."
    ),
    r"substitutes more": "An operator substitutes more than the port.",
    r"does not hold": "That conclusion does not hold.",
    r"conclusion (?:is |was )?false": "Three reads make that conclusion false.",
    r"did not hold": "The standing did not hold.",
}


def _plain(text: str) -> str:
    return re.sub(r"[`*]|:class:", "", flat(text))


def _quoted_paragraph(path: object, anchor: str) -> str:
    """The Markdown quote lines from the one line carrying *anchor* to the next bare `>` line."""
    lines = path.read_text(encoding="utf-8").splitlines()  # type: ignore[attr-defined]
    starts = [i for i, line in enumerate(lines) if anchor in line]
    if len(starts) != 1:
        not_found(f"{len(starts)} lines carry {anchor!r}")
    found: list[str] = []
    for line in lines[starts[0] :]:
        content = line.lstrip().removeprefix(">").strip()
        if not line.lstrip().startswith(">") or not content:
            break
        found.append(content)
    return "\n".join(found)


def _standing_records() -> dict[str, str]:
    return {
        "the ADR-0003 row": _adr_row("CanonicalReadSession"),
        "the class docstring": inspect.getdoc(CanonicalReadSession) or "",
    }


def _opposite_scopes() -> dict[str, str]:
    return _standing_records() | {
        "the roadmap's reopened bullet": _quoted_paragraph(ROADMAP, "**Reopened in [#832]"),
        "the ADR-0003 amendment's second paragraph": _quoted_paragraph(
            ADR_0003, "A reviewer did read the standing"
        ),
    }


@pytest.mark.parametrize("pattern", [*_SETTLED, *_OPPOSITE])
def test_no_record_of_the_standing_states_a_judgement_either_way(pattern: str) -> None:
    """RED means a record of the session's standing states a judgement #865 owns.

    `_SETTLED` is held over the row and the class docstring, which say the question
    is open; `_OPPOSITE` over those two, the roadmap's reopened bullet and the
    amendment's second paragraph, which state the facts and leave what they make
    the session to #865. Each pattern is driven over a sentence it must match
    first, beside the scopes it must not match.
    """
    scopes = _opposite_scopes() if pattern in _OPPOSITE else _standing_records()

    found = [label for label, text in scopes.items() if re.search(pattern, _plain(text), re.I)]

    assert re.search(pattern, _plain(_EXAMPLES[pattern]), re.I), (
        f"control: {pattern!r} does not match the sentence it exists for"
    )
    assert found == [], f"{found} state a judgement matching {pattern!r}"


def test_the_two_gates_read_through_the_session_with_the_reads_each_record_assigns() -> None:
    """RED means the per-path read sets the records state have moved (#832).

    Search, through `CanonicalVisibility` and `ResultGate` (the scopes
    `gate_read_facts.gates()` walks), reads three members, two of them widening;
    `_relation_is_visible` reads one. Not seen: a read reached through a function
    outside those scopes.
    """
    assert _search_path_reads() == {"get_item_metadata", "get_item_exact", "get_revision"}, (
        f"search reads through {sorted(_search_path_reads())}"
    )
    assert _search_path_reads() & _widening() == {"get_item_metadata", "get_item_exact"}, (
        f"search's widening reads are {sorted(_search_path_reads() & _widening())}"
    )
    assert _relation_path_reads() == {"get_item_exact_metadata"}, (
        f"`_relation_is_visible` reads through {sorted(_relation_path_reads())}"
    )


def test_each_record_names_search_and_the_relation_gate_their_own_reads() -> None:
    """RED means the row or the class docstring names another read for one of the two gates."""
    widening_count = _NUMBER_WORDS[len(_search_path_reads() & _widening())]

    for label, text in _standing_records().items():
        search = sentence_with(text, r"search reads")
        relation = sentence_with(text, r"_relation_is_visible\W+is typed")

        assert _spans(search) & READ_NAMES == _search_path_reads(), (
            f"{label} names {sorted(_spans(search) & READ_NAMES)} as search's reads"
        )
        assert _spans(relation) & READ_NAMES == _relation_path_reads(), (
            f"{label} names {sorted(_spans(relation) & READ_NAMES)} as the relation gate's read"
        )
    assert f"{widening_count} of them widening" in flat(
        sentence_with(_adr_row("CanonicalReadSession"), r"search reads")
    ), f"the row does not spell how many of search's reads widen ({widening_count})"
