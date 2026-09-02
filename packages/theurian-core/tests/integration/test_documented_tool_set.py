"""The records that enumerate the callable MCP tools, held against the built server.

**Why this file exists.** Four documents stated a *count* of MCP tools — "five
read-only tools" — and every one of them went false the moment `review.findings`
was registered (ADR-0029 phase-2 slice-3). A count of a live registry written into
prose has no way to notice the registry moving, and this project has now paid for
that shape once per document. The class is closed two ways at once, and each site
took whichever fits it:

- where the count carried no weight, it is **gone**. ``README.md``'s "Agents read,
  propose, and never approve" bullet, ``docs/index.md``'s write-boundary
  paragraph, and ``docs/roadmap.md``'s Phase-0 principle row each assert that the
  tools are *read-only* — a claim
  ``test_mcp_tools.py::test_no_registered_tool_can_reach_a_canonical_write``
  already holds over the bytecode of every registered tool. The number added
  nothing to it and was one more thing to keep true, so those sentences say
  "every" and carry no count at all;
- where the enumeration **is** the content — a client reading ``README.md``'s
  *Works with* section or ``docs/protocol/mcp-tools.md``'s opening to learn what
  it may call — the list stays and is derived here rather than trusted.

**What is derived, and from where.** The names come from the tool manager of the
server ``daemon/runner.build_server`` actually constructs, not from a constant in
this file and not from a scan of ``mcp/tools.py``: a tool registered by any
mechanism — a decorator, an ``add_tool`` call, a name assembled at runtime —
appears there. So the pin fails on the *registry* moving, which is the event the
prose has no other way to hear about.

**Both halves, per record.** The set of names, and the number word the sentence
spells. Either alone leaves a true-looking record: a list that gained an entry
while the word stayed "five" reads as a typo rather than as a wrong claim, and a
word bumped to "six" over five names is a count of nothing. The number word is
translated from ``len(registered)`` through :data:`_NUMBER_WORDS`, so a seventh
tool reddens with the word the record should now use rather than with a diff.

**Reach, stated as narrowly as it is true.** This module reads exactly two named
files and walks no tree — it is not a repo-wide checker and has no corpus
membership question. It holds *which tools exist* and *how many*; it does not
hold what any of them does, what its arguments are, or that the surrounding prose
describing it is accurate. ``docs/protocol/mcp-tools.md``'s per-tool sections are
their own records with their own pins (the ``review.findings`` limit row is
``test_review_findings_tool.py``'s), and a document that listed the six names
correctly under an entirely wrong description would pass here.

Hermetic: the server is built against an empty registry under a redirected
``THEURIAN_DATA_DIR``. The registered tool *set* does not depend on any project's
contents, so no project is needed and no socket is opened.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Final

import pytest

from theurian.application.project_service import ProjectRegistry
from theurian.daemon.runner import build_server

pytestmark = pytest.mark.integration

REPO_ROOT: Final = Path(__file__).resolve().parents[4]

README: Final = REPO_ROOT / "README.md"
MCP_TOOLS: Final = REPO_ROOT / "docs" / "protocol" / "mcp-tools.md"

#: How each record spells the size of the tool set, one word per count. The count
#: itself is recomputed from the built server; this only translates it, so a RED
#: names the word the record should now carry instead of leaving an editor to
#: work it out.
_NUMBER_WORDS: Final = {
    1: "one",
    2: "two",
    3: "three",
    4: "four",
    5: "five",
    6: "six",
    7: "seven",
    8: "eight",
    9: "nine",
    10: "ten",
    11: "eleven",
    12: "twelve",
}

#: An MCP tool name inside backticks: ``namespace.member``. Anchored on both
#: backticks, so a backticked URL — ``http://127.0.0.1:7419/mcp`` sits in the very
#: sentence this reads — cannot contribute a fragment of itself as a tool name.
_TOOL_NAME: Final = re.compile(r"`([a-z][a-z0-9]*\.[a-z][a-zA-Z0-9]*)`")

#: The README sentence that enumerates the set, keyed on the phrase that names
#: what the enumeration *is*. Not keyed on any tool name: a key naming one member
#: would stop matching exactly when someone rewrote the list, and the sentence
#: would drop out of the population rather than fail.
README_KEY: Final = "read-only tools"

#: The protocol document's opening claim, same rule. The bullet list that follows
#: it is the block after this one.
MCP_TOOLS_KEY: Final = "callable mcp tools"

#: A sentence boundary in whitespace-collapsed prose: a full stop or a semicolon
#: followed by a space. ``127.0.0.1:7419`` survives it — those periods carry no
#: following space — which is why the URL in the README sentence does not split
#: the claim in half.
_SENTENCE_BREAK: Final = re.compile(r"(?<=[.;])\s")


@pytest.fixture
def empty_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ProjectRegistry]:
    """A registry over a redirected, empty data dir — no real project touched."""
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(tmp_path / "datadir"))
    yield ProjectRegistry.default(tmp_path / "datadir")


def _registered(registry: ProjectRegistry) -> set[str]:
    """Every tool name the built server registers, however it was registered."""
    server = build_server(registry)

    # The private manager, deliberately: `list_tools` on the public surface
    # returns wire schemas, and what is needed here is the names.
    return {tool.name for tool in server._tool_manager.list_tools()}


def _collapsed(text: str) -> str:
    """Lowercased, with runs of whitespace flattened — both records soft-wrap."""
    return " ".join(text.lower().split())


def _blocks(text: str) -> tuple[str, ...]:
    """Every Markdown block of *text*, raw, in document order."""
    return tuple(block for block in re.split(r"\n[ \t]*\n", text) if block.strip())


def _the_one_block_carrying(blocks: tuple[str, ...], key: str, *, record: str) -> int:
    """The index of the single block matching *key*, failing differently on 0 and many.

    Zero means the anchor was rewritten past itself and every assertion downstream
    would pass over nothing; many means the key stopped identifying one paragraph,
    so "the list after it" would be whichever copy came first.
    """
    matches = [index for index, block in enumerate(blocks) if key in _collapsed(block)]

    assert len(matches) == 1, (
        f"{record} is not findable as exactly one block keyed on `{key}`: found {len(matches)}"
    )
    return matches[0]


def _readme_sentence() -> str:
    """The one *Works with* sentence that enumerates the callable tools."""
    blocks = _blocks(README.read_text(encoding="utf-8"))
    index = _the_one_block_carrying(blocks, README_KEY, record="README's tool enumeration")
    sentences = [
        sentence
        for sentence in _SENTENCE_BREAK.split(_collapsed(blocks[index]))
        if README_KEY in sentence
    ]

    assert len(sentences) == 1, (
        f"README's tool-enumeration block carries {len(sentences)} sentences saying "
        f"`{README_KEY}`; the pin cannot say which one enumerates the set"
    )
    return sentences[0]


def _mcp_tools_list() -> tuple[str, str]:
    """The protocol document's opening claim and the bullet list under it."""
    blocks = _blocks(MCP_TOOLS.read_text(encoding="utf-8"))
    index = _the_one_block_carrying(blocks, MCP_TOOLS_KEY, record="mcp-tools.md's tool enumeration")

    assert index + 1 < len(blocks), "mcp-tools.md's tool enumeration is the last block; no list"
    listed = blocks[index + 1]
    assert listed.lstrip().startswith("- "), (
        f"the block after mcp-tools.md's `{MCP_TOOLS_KEY}` claim is not a list, so the "
        f"names it promises are somewhere else: {listed[:80]!r}"
    )
    return _collapsed(blocks[index]), listed


def _spelled_count(sentence: str, *, record: str) -> int:
    """The number word the record spells, as an integer.

    Read out of the sentence rather than matched against an expected word, so the
    failure below can say what the record claims *and* what the registry holds
    instead of only that the two differ.
    """
    words = {word for word in _NUMBER_WORDS.values() if re.search(rf"\b{word}\b", sentence)}

    assert len(words) == 1, (
        f"{record} spells {sorted(words)} as a count; exactly one number word must "
        f"state how many tools there are, or a reader cannot tell which is the claim"
    )
    spelled = words.pop()
    return next(count for count, word in _NUMBER_WORDS.items() if word == spelled)


def test_the_readme_enumerates_exactly_the_tools_the_built_server_registers(
    empty_registry: ProjectRegistry,
) -> None:
    """RED means the README tells a client it gets a tool set this build does not serve.

    The *Works with* section is where a client author reads what speaking MCP to
    this daemon buys them, so a name missing from it is a shipped tool nobody
    calls, and a name that is there and not registered is a client written against
    a tool that answers nothing.

    Both halves are asserted. The set, because that is the content; and the number
    word, because a list that grew while the word stood still reads as a typo
    rather than as a false claim — and because the word is the part that has now
    gone stale in four documents at once.
    """
    registered = _registered(empty_registry)
    sentence = _readme_sentence()

    assert registered, "the built server registers no tools; the comparison below is vacuous"
    assert set(_TOOL_NAME.findall(sentence)) == registered, (
        f"README's *Works with* section lists {sorted(set(_TOOL_NAME.findall(sentence)))} and "
        f"this build registers {sorted(registered)}. A client reads that sentence to learn "
        f"what it may call."
    )
    assert _spelled_count(sentence, record="README's *Works with* sentence") == len(registered), (
        f"README says there are {_spelled_count(sentence, record='README')} read-only tools "
        f"and this build registers {len(registered)}; the sentence should say "
        f"`{_NUMBER_WORDS[len(registered)]}`"
    )


def test_the_protocol_document_lists_exactly_the_tools_the_built_server_registers(
    empty_registry: ProjectRegistry,
) -> None:
    """RED means the protocol reference disagrees with the server it documents.

    ``docs/protocol/mcp-tools.md`` opens by stating how many tools Core registers
    and naming them, and everything below that opening is written as a contract.
    A tool absent from the list is undocumented protocol surface; one listed and
    unregistered is documented protocol surface that does not exist.

    The list is taken as the block *after* the claim rather than by searching the
    file for tool names, because the rest of that document names planned tools
    too — ``review.search``, ``knowledge.trace`` — and a file-wide scan would
    report those as registrations the server is missing.
    """
    registered = _registered(empty_registry)
    claim, listed = _mcp_tools_list()

    assert registered, "the built server registers no tools; the comparison below is vacuous"
    assert set(_TOOL_NAME.findall(listed)) == registered, (
        f"docs/protocol/mcp-tools.md lists {sorted(set(_TOOL_NAME.findall(listed)))} as the "
        f"callable tools and this build registers {sorted(registered)}"
    )
    assert _spelled_count(claim, record="mcp-tools.md's opening claim") == len(registered), (
        f"docs/protocol/mcp-tools.md says Core registers "
        f"{_spelled_count(claim, record='mcp-tools.md')} callable tools and this build "
        f"registers {len(registered)}; the claim should say `{_NUMBER_WORDS[len(registered)]}`"
    )


def test_the_count_this_pin_can_spell_covers_the_set_the_server_registers(
    empty_registry: ProjectRegistry,
) -> None:
    """RED means the pin above would raise a lookup error instead of a claim failure.

    :data:`_NUMBER_WORDS` stops at twelve. Past that, translating ``len(registered)``
    into the word a record should carry fails with a ``KeyError`` from inside an
    assertion message — which reports the pin as broken rather than the record as
    wrong. Asserted separately so that a thirteenth tool gets a sentence telling
    whoever added it what to extend.
    """
    registered = _registered(empty_registry)

    assert len(registered) in _NUMBER_WORDS, (
        f"this build registers {len(registered)} tools, past the {max(_NUMBER_WORDS)} this "
        f"module can spell; extend `_NUMBER_WORDS` before the pins above can name the word "
        f"the records should carry"
    )
