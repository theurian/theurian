"""ADR-0003's open question on `CanonicalReadSession`: its facts and records (#832, #865).

Round one found the row's headline ("mostly a narrowing of `CanonicalStore`, not a
second substitution point") and last sentence ("what an operator substitutes is
still a `CanonicalStore` adapter") false: the session widens the port by three
reads, the gate path makes three of its four session reads through them, and an
adapter of exactly the port's members is not an instance of it. The standing is
recorded as an open question, decided on #865.

**Held.** Facts: the session is not in `ALL_PORTS`; a `CanonicalStore`-only adapter
is not an instance and a complete one is; the gate path's session reads, derived by
`gate_read_facts` from `_relation_is_visible`, every method of `CanonicalVisibility`
and `ResultGate._surfaced`, are four, three of them widening. Prose: the ADR-0003
row opens by calling the standing open and links #865, the `IndexBuildSession` row
follows it there, the class docstring and the module docstring say it is open, and
the row and the class docstring each name exactly the derived gate-path reads.

When #865 settles the standing, either answer reddens a fact (joining the register)
or a prose pin (the row stops saying "open"), so the record cannot be left behind.

**Known-weak halves.** The gate-path sentence is located by the phrase "the SEC-13
gate path reads through"; rewording it reads as "record not found". A read reached
through a function outside those scopes is not in the derived set.
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
    methods,
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


def _gate_path_reads() -> frozenset[str]:
    """The session members called by the relation gate, `CanonicalVisibility` and `_surfaced`."""
    scopes: list[ast.AST] = [
        function(tree(TOOLS_FILE), "_relation_is_visible"),
        klass(tree(VISIBILITY_FILE), "CanonicalVisibility"),
        methods(klass(tree(RETRIEVAL_FILE), "ResultGate"))["_surfaced"],
    ]
    return frozenset().union(*(callees(scope) for scope in scopes)) & SESSION_MEMBERS


def _noop(*_arguments: object, **_keywords: object) -> None:
    return None


def _spans(text: str) -> set[str]:
    return set(re.findall(r"`+([A-Za-z_][A-Za-z0-9_]*)`+", text))


def test_the_read_session_is_outside_the_register_and_a_store_only_adapter_is_not_one() -> None:
    """RED means a fact ADR-0003's open question rests on has moved, so its records must move.

    #865 decides whether the session joins `ALL_PORTS`. Either answer reddens
    this: joining makes the first assertion false, and settling it outside makes
    the prose pins below false. `CanonicalStore`'s members alone do not satisfy the
    session, so what an operator substitutes is more than a `CanonicalStore`
    adapter, which is what the row's headline and last sentence used to deny.
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

    assert cell.startswith("**An open question"), (
        "ADR-0003's `CanonicalReadSession` row no longer opens by calling its standing open"
    )
    assert issue in row, "the row does not link #865"
    assert issue in _adr_row("IndexBuildSession"), (
        "the `IndexBuildSession` row does not follow it to #865"
    )
    assert re.search(r"\bopen\b", sentence_with(doc, r"Not in the register")), (
        "the class docstring's sentence on the register does not say the question is open"
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
