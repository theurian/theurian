"""``writeTools``'s published meaning: coupled to the registrations, and narrated
by ten records (ADR-0032 decision 5).

``writeTools`` is a security claim published on ``system.capabilities``, not a
label: ``false`` says no write-intent tool a client may call exists, and
``docs/index.md`` tells a reader to take exactly that from it. ADR-0032 decision
5 ties the value to the first registration -- flipped in the *same* commit and in
both directions, which is ADR-0026's capability honesty (a flag may not move
ahead of its feature, nor lag behind it). Two claims follow from that, and they
fail in different ways.

**The value couples to the registration.** ADR-0032's *Context* measured that
only two assertions move on a value flip, and none coupled the value to the
registration -- so a commit could flip the flag while registering no tool, or
register a tool while the flag still read ``false``, and the conformance suite
stayed green either way. That second case is the dangerous one: a registered
write path published as absent.
:func:`test_writetools_reads_true_exactly_when_a_write_intent_tool_is_registered`
pins the *coupling*, not the new value. Both facts are read from
``mcp/tools.py``'s own source -- the value a contributor wrote into the
capability dict, and the tool ``name``s a contributor registered through the one
``_tool`` seam.

**Ten records narrate the value, both ways round.** The flip falsified every
record that described a read-only MCP surface, and the two commits that corrected
them -- the registration commit and the documentation commit after it -- shipped
with nothing holding the correction. That is the state a re-tensing lands in: the
wording is freshly right, nobody rereads what was just written, and the next
rewrite has no reason to keep it. Every one of these ten could be reworded back
to the read-only era with the coupling arm above still green, because that arm
reaches no prose file at all. This is the ratchet
``test_review_ingestion_flag_claims.py`` built for its own flag, built here for
this one. The last four were added after the first six: the two commits that
corrected them did not reach four more records -- ADR-0026's *Compliance* section,
this module's own subject file twice over (its module docstring and its
``@_tool``-count comment in ``daemon/server.py``), and ADR-0029's runtime
companion -- and each stood unpinned until this row was written.

========================================= ======================================================
Record                                    What the flip falsified in it
========================================= ======================================================
``mcp/tools.py``'s capability comment     the narration the flip *added*, beside the value
``mcp/tools.py``'s module docstring       the *Read-only* heading and the Milestone-3 sentence
``README.md``                             four narrations, from *Alongside* to *Works with*
``docs/index.md``                         the AI-proposes lead and the enforcement note
``docs/protocol/mcp-tools.md``            the runtime-boundary paragraph and the write section
``docs/roadmap.md``                       six, from the §0 preamble to the §6 principle row
``docs/security/threat-model.md``         T-12's *Controls* sentence
``docs/adr/0026``'s *Compliance* section  the two ``writeTools: false`` sentences re-tensed
``daemon/server.py``'s tool-count comment the read-only framing and the ``mcp/tools.py:7`` count
``docs/adr/0029``                         the *known read-only tool set* clause
========================================= ======================================================

A prose pin alone would keep the ``true``-era wording against a build that had
flipped back; a value pin alone would keep the value against records still
describing the read-only era. So the fact side is
:func:`_published_flag` -- the same value the coupling arm reads, out of
``mcp/tools.py``'s own capability dict -- and it *selects* which wording each
record must carry and which it must not. Flip the flag and all ten are RED until
they are re-tensed; re-tense one backwards and it is RED until the flag moves.

The daemon comment's ``mcp/tools.py:7`` fragment is forbidden as *read-only-era
framing*, not as a count: the live tool count is pinned structurally, from the
tree rather than from the prose, by
``test_transport_body_cap.py::test_the_server_comment_states_the_live_at_tool_registration_count``.

**Narrates, not mentions.** Many records name this flag; these ten tell a reader
what its value *is* and reason from it, which is what makes them false rather
than merely dated when it moves. Two kinds are deliberately **not** here. A
*dated reading* is correct as history: ``docs/roadmap.md`` carries
``| `writeTools` | `false` |`` in two tables anchored to commit ``f702736``, and
nothing below forbids those cells -- every ``false``-era fragment quoted here is
prose. An ADR's *Context* and *Decision* sections record what was true when the
decision was taken, and are not in this population. An ADR's *Compliance* section
is the opposite -- it is maintained rather than frozen, re-tensed as each flag it
lists moves (this slice's flip edited ADR-0026's *Compliance* narration and
ADR-0029's runtime-companion clause), which is why those two are narrations this
pin holds where the *Context* sentences are not.

**The population is enumerated rather than derived -- an eleventh narration would
not redden this**, and that is the honest bound. The answer is to add it here in
the change that writes it. A derived population, keyed on the narrating spellings
across the whole corpus, is the shape that would not need the edit;
``test_review_ingestion_flag_claims.py`` records the same bound and the same cost
after three rounds added a row to it after the fact. This enumerated pin is a
stopgap for the class closure tracked in #706 -- the shared-flags
derived-capability-narration sweep, under which no tracked-prose narration
disagrees with the live flag -- so the next reader knows enumeration is not the
closure.

**Each record is read the way its own reader reads it.** A Python comment through
``tokenize``, because comments are discarded before a syntax tree exists; a module
docstring through the syntax tree, because ``mcp/tools.py`` states its
``**No canonical write.**`` paragraph as the docstring at the top of the file
while its ``true``-era capability narration is a ``#`` comment beside the value --
two records in one file, each read the way it is written; Markdown as prose with
its block quoting removed and its soft wraps flattened, because every one of these
documents wraps mid-sentence and a quotation that spans a line break reports
itself missing otherwise.

**What this does not hold.** That the flag's value is *correct behaviour* -- that
the daemon really registers the tools and answers ``true`` over the wire. That is
``tests/integration/test_mcp_tools.py``'s
``test_capabilities_report_what_is_and_is_not_built`` and the built-server facade
walk beside ``test_no_registered_tool_can_reach_a_canonical_write``. Every arm
here would pass against a daemon that registered nothing and declared ``true``.

Pure: it reads nine repository files -- ``mcp/tools.py`` three times, for the
coupling value, its capability comment and its module docstring -- and opens no
database, no socket and no temporary directory.
"""

from __future__ import annotations

import ast
import pathlib
import re
import tokenize
from collections.abc import Callable
from typing import Final

import pytest
from write_lock_claims import REPO_ROOT

pytestmark = pytest.mark.unit

_TOOLS: Final = REPO_ROOT / "packages/theurian-core/src/theurian/mcp/tools.py"
_README: Final = REPO_ROOT / "README.md"
_DOCS_INDEX: Final = REPO_ROOT / "docs/index.md"
_PROTOCOL_DOC: Final = REPO_ROOT / "docs/protocol/mcp-tools.md"
_ROADMAP: Final = REPO_ROOT / "docs/roadmap.md"
_THREAT_MODEL: Final = REPO_ROOT / "docs/security/threat-model.md"
_BOUNDARY_ADR: Final = REPO_ROOT / "docs/adr/0026-evidence-plane-not-control-plane.md"
_DAEMON_SERVER: Final = REPO_ROOT / "packages/theurian-core/src/theurian/daemon/server.py"
_FINDINGS_ADR: Final = REPO_ROOT / "docs/adr/0029-review-findings-are-governed-knowledge.md"

#: The flag whose value this module couples to a registration.
_FLAG: Final = "writeTools"

#: The write-intent tools, named rather than derived. ADR-0032 registers exactly
#: these two at slice B4; ``review.generateKnowledgeCandidate`` (ADR-0033) arrives
#: additively at B5 with no flag change, because ``writeTools`` answers *whether
#: any write-intent tool exists*, not *how many*. A third write-intent tool joins
#: this set by a deliberate edit here, in the change that registers it.
_WRITE_INTENT_TOOL_NAMES: Final = frozenset(
    {"knowledge.proposeChange", "knowledge.generateMigrationDraft"}
)


def _tools_tree() -> ast.Module:
    return ast.parse(_TOOLS.read_text(encoding="utf-8"), filename=_TOOLS.name)


def _published_flag(tree: ast.Module) -> object:
    """The value ``mcp/tools.py`` publishes for :data:`_FLAG`, read from its tree.

    From the source rather than by calling the tool, for
    ``test_review_ingestion_flag_claims.py``'s reason: calling it needs a server
    and an event loop, and what is wanted here is the constant a contributor
    wrote. A value that is computed rather than written is refused -- a
    deployment-dependent ``writeTools`` would be a different contract from the
    build-constant one ADR-0032 decision 5 describes.
    """
    published = [
        value
        for node in ast.walk(tree)
        if isinstance(node, ast.Dict)
        for key, value in zip(node.keys, node.values, strict=True)
        if isinstance(key, ast.Constant) and key.value == _FLAG
    ]

    assert len(published) == 1, (
        f"{_TOOLS.name} assigns `{_FLAG}` in {len(published)} dict literals, expected 1. "
        f"Zero means the capability block was rewritten into a shape this reader does not "
        f"see, and the coupling below would then be about nothing"
    )
    value = published[0]
    assert isinstance(value, ast.Constant) and isinstance(value.value, bool), (
        f"`{_FLAG}` is published as `{ast.unparse(value)}`, which is not a written boolean. "
        f"A capability whose value depends on deployment state is a different contract from "
        f"the build constant ADR-0032 decision 5 couples to the registration"
    )
    return value.value


def _registered_tool_names(tree: ast.Module) -> frozenset[str]:
    """Every tool ``name`` registered through the ``_tool`` seam, read from the tree.

    The one seam is ``_tool`` (``mcp/tools.py``); a tool registered any other way
    is caught by ``test_tool_error_type_contract.py`` and
    ``test_every_registered_tool_goes_through_the_forwarding_seam``. Reading the
    ``name=`` of each ``_tool(...)`` call is therefore the source-level answer to
    "which tools does this build register", and a write-intent registration is one
    of these ``name``s landing in :data:`_WRITE_INTENT_TOOL_NAMES`.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        if node.func.id != "_tool":
            continue
        for keyword in node.keywords:
            if keyword.arg == "name" and isinstance(keyword.value, ast.Constant):
                value = keyword.value.value
                if isinstance(value, str):
                    names.add(value)
    return frozenset(names)


def _assert_flag_couples_to_registration(published: object, registered: frozenset[str]) -> None:
    """The coupling check, in one place so the control drives the same path.

    Takes the published value and the registered write-intent set and demands they
    agree in *both* directions -- which is what makes the pin bidirectional and
    what a single build cannot exercise on its own, since on any given day the flag
    holds one value.
    """
    write_intent = registered & _WRITE_INTENT_TOOL_NAMES

    if published is True:
        assert write_intent, (
            f"`{_FLAG}` reads true but no write-intent tool is registered "
            f"(registered: {sorted(registered)}). ADR-0026's capability honesty: a flag "
            f"may not be flipped ahead of the feature it advertises"
        )
    else:
        assert not write_intent, (
            f"`{_FLAG}` reads {published!r} while these write-intent tools are registered: "
            f"{sorted(write_intent)}. That is a registered write path published as absent -- "
            f"the flag must flip in the same commit as the first registration (ADR-0032 "
            f"decision 5)"
        )


def test_writetools_reads_true_exactly_when_a_write_intent_tool_is_registered() -> None:
    """RED means the flag and the write-intent registrations disagree in ``mcp/tools.py``.

    The failure this closes: a commit flips ``writeTools`` while registering no
    tool, or registers a write-intent tool while the flag still reads ``false`` --
    a half-moved flag the conformance suite stayed green through (ADR-0032
    *Context*, *Compliance*). Both facts come from the one source file, so a move
    to either one alone reddens this.

    Not vacuous: the registered set is asserted non-empty first, or a scan that
    stopped finding ``_tool`` calls would make the ``false`` branch pass by seeing
    no write-intent tool at all.
    """
    tree = _tools_tree()
    registered = _registered_tool_names(tree)

    assert registered, (
        "the `_tool` scan found no registered tool at all, so this coupling would pass by "
        "seeing an empty registration -- the scan or the seam has moved"
    )
    _assert_flag_couples_to_registration(_published_flag(tree), registered)


def test_the_coupling_checker_demands_the_other_state_when_either_side_moves() -> None:
    """The positive control for a pin whose two directions cannot both be observed.

    The arm above runs against one build at a time -- one flag value, one
    registration -- so only one direction of the coupling is exercised on any
    given day and the other is a claim about what would happen. That is the shape
    that quietly stops working: an unexercised branch could compare the wrong way
    round and nothing would say so until the flag moved, which is once.

    So both directions are driven here against synthetic inputs. The two
    half-moved states must each raise -- a flag flipped ahead of its feature, and a
    tool registered while the flag lags -- and the two consistent states must each
    pass. Nothing in ``src/`` decides the outcome; the inputs are written here.
    """
    proposal = frozenset({"knowledge.proposeChange"})

    # Consistent: both eras pass.
    _assert_flag_couples_to_registration(True, proposal)
    _assert_flag_couples_to_registration(False, frozenset())

    # Half-moved: the flag flipped ahead of the feature.
    with pytest.raises(AssertionError, match="flipped ahead of the feature"):
        _assert_flag_couples_to_registration(True, frozenset())

    # Half-moved: a write-intent tool registered while the flag still reads false.
    with pytest.raises(AssertionError, match="published as absent"):
        _assert_flag_couples_to_registration(False, proposal)


# ---------------------------------------------------------------------------
# The prose side: the records that narrate the value
# ---------------------------------------------------------------------------


def _flattened(text: str) -> str:
    """*text* with its soft wraps flattened, markup and case preserved.

    Every record below wraps, and most of them wrap in the middle of the sentence
    being quoted -- ``docs/index.md`` between "reports" and "`writeTools: true`",
    ``README.md`` between "no MCP tool" and "writes approved knowledge". An
    unflattened search finds some of them and reports the rest as having dropped
    the sentence.
    """
    return " ".join(text.split())


def _comment_text(source: pathlib.Path) -> str:
    """Every comment in *source*, joined, with the markers removed.

    Tokenized rather than pattern-matched off the lines, because a ``#`` inside a
    string literal is not a comment and a comment block is what a reader sees as
    one paragraph. A syntax tree cannot answer this at all: comments are discarded
    before the tree exists, which is why :func:`_published_flag`'s reader cannot
    be reused for the record sitting directly above the value it reads.
    """
    with source.open("rb") as handle:
        tokens = tokenize.tokenize(handle.readline)
        return _flattened(
            " ".join(
                token.string.lstrip("#").strip()
                for token in tokens
                if token.type == tokenize.COMMENT
            )
        )


def _markdown_prose(source: pathlib.Path) -> str:
    """*source* as prose, with block quoting removed before the wraps are flattened.

    A Markdown document is read as text, but not as raw text: a sentence inside a
    ``>`` block quote keeps a ``>`` in the middle of it once the wraps are
    flattened, so every quotation spanning a line break there reports itself
    missing. What a reader sees is the quoted prose, and that is what the
    quotation has to be checked against.
    """
    return _flattened(
        " ".join(
            line.lstrip().removeprefix(">").lstrip() if line.lstrip().startswith(">") else line
            for line in source.read_text(encoding="utf-8").splitlines()
        )
    )


def _module_docstring(source: pathlib.Path) -> str:
    """*source*'s module docstring, flattened.

    Read out of the syntax tree rather than by importing the module, so this pin
    does not depend on ``mcp/tools.py`` importing cleanly, and so a docstring
    stripped by ``-O`` at some future runtime is not what is being checked. It is a
    different reader from :func:`_comment_text` on the same file:
    ``mcp/tools.py``'s ``**No canonical write.**`` paragraph is the docstring at
    the top of the file, while its ``true``-era capability narration is a ``#``
    comment beside the value -- two records in one file, each read the way it is
    written.
    """
    docstring = ast.get_docstring(ast.parse(source.read_text(encoding="utf-8")))
    assert docstring, (
        f"{source.name} has no module docstring, so the row that reads it would assert its "
        f"sentences against nothing and pass vacuously"
    )
    return _flattened(docstring)


#: Each record that **narrates** the flag's value, what it must say, and what it
#: must not, per published value.
#:
#: One row per document, with every narration in that document in its tuple, so a
#: RED message quotes the sentence that moved rather than naming the file. The
#: fragments are long enough to be unambiguous in documents that mention this flag
#: many times over.
#:
#: **The ``false`` column is not hypothetical wording**: except where noted it is
#: what each record really said at ``4f170b6b`` -- the merge base of the slice that
#: flipped the flag -- quoted rather than invented. A branch sha is orphaned by the
#: squash that merges it and a quotation survives, which is the citation rule
#: ``test_review_ingest_changelog_claims.py`` records.
#:
#: **The first row's ``false`` column is empty, and that is the exception the rule
#: above allows for.** The flip did not *re-tense* a comment beside the value; it
#: *added* one, so there is no ``false``-era comment to quote and inventing one
#: would be the fifth spelling this module refuses to create. Under ``false`` the
#: row therefore demands no particular wording and forbids the ``true``-era
#: sentences, which is the half that catches a flip back leaving them behind.
#: :func:`test_the_narration_checker_demands_the_other_era_when_the_flag_moves`
#: drives that asymmetric row explicitly rather than leaving the empty column to a
#: reader's assumption.
_ERA_NARRATIONS: Final[tuple[tuple[str, str, tuple[str, ...], tuple[str, ...]], ...]] = (
    (
        "mcp/tools.py (the capability comment)",
        "tools-comment",
        (
            "**The narrowed meaning, now that it is `true`: a write-intent tool exists "
            "that a client may call.**",
            "It does **not** say a client may write approved knowledge",
        ),
        (),
    ),
    (
        "README.md",
        "readme",
        (
            "Most of the MCP tools it exposes read; the two write-intent ones emit a "
            "proposal for a human to review, and no tool writes approved knowledge",
            "reach no approved-state write, so `system.capabilities` reports "
            "`writeTools: true` beside a note that no MCP tool writes approved knowledge",
            "`system.capabilities` reports `writeTools: true` — the write-intent *MCP* "
            "tools `knowledge.proposeChange` and `knowledge.generateMigrationDraft` exist "
            "(ADR-0032)",
            "gets the same nine tools this daemon exposes",
            "Most read; the `knowledge.proposeChange` and "
            "`knowledge.generateMigrationDraft` write-intent tools emit a proposal a human "
            "reviews and merges, never approved knowledge",
        ),
        (
            "serving it through a read-only MCP surface",
            "Every MCP tool is read-only, and `system.capabilities` reports `writeTools: false`.",
            "`system.capabilities` reports `writeTools: false` — no write-intent *MCP* tool "
            "exists, so proposing is the `theurian propose` CLI's job today",
            "gets the same seven read-only tools",
        ),
    ),
    (
        "docs/index.md",
        "docs-index",
        (
            "The write-intent tools a client can call — `knowledge.proposeChange` and "
            "`knowledge.generateMigrationDraft` — emit a proposal a human reviews and "
            "merges, never approved state, and `system.capabilities` reports "
            "`writeTools: true`",
            "Over MCP, the two write-intent tools draft through the same `ProposalService` "
            "and land the same proposal directory",
            "That no MCP tool writes approved knowledge is enforced structurally: the "
            "write-intent tools are handed a draft-only facade whose reachable surface is "
            "the two draft entries alone, so the accept path is not reachable from a tool "
            "at all (ADR-0032 decision 8).",
        ),
        (
            "Every tool a client can call today is read-only, and `system.capabilities` "
            "reports `writeTools: false`.",
            "Write-intent MCP tools are designed and not built",
            "That no MCP tool writes approved knowledge is enforced — a test walks the "
            "bytecode of every registered tool to hold it.",
        ),
    ),
    (
        "docs/protocol/mcp-tools.md",
        "protocol-doc",
        (
            "and `writeTools: true`, because the write-intent tools "
            "`knowledge.proposeChange` and `knowledge.generateMigrationDraft` are "
            "registered (ADR-0032)",
            "`system.capabilities` reports `writeTools: true` because the write-intent "
            "tools are registered (ADR-0032)",
        ),
        (
            "beside `writeTools: false` and `traceability: false`, which mean the "
            "write-intent and traceability tools described below are designed protocol "
            "shape, not callable tools in the current server",
            "There is no MCP path to approved state today — not a flag, not a permission.",
            "`system.capabilities` reports `writeTools: false`, and a test enumerates every "
            "registered tool and asserts none reaches a canonical write",
        ),
    ),
    (
        "docs/roadmap.md",
        "roadmap",
        (
            "**The write-intent MCP tools left it second** — `knowledge.proposeChange` and "
            "`knowledge.generateMigrationDraft` register as of",
            "**`writeTools` moved after that reading, on the Phase B slice-B4 branch",
            "**Every tool it exposes was read-only until ADR-0032**; the two write-intent "
            "tools it now exposes write a proposal directory and nothing else",
            "there are two write tools there, and the guarantee is that neither reaches an "
            "**approved-state** write",
            "**The agent write path** — no longer the `theurian propose` CLI only.",
            "The write-intent MCP tools (`writeTools: true` since ADR-0032) emit a proposal "
            "a human reviews and merges and reach no approved-state write",
        ),
        (
            "Neither does any write-intent MCP tool, nor an evaluation harness.",
            "MCP is Streamable HTTP, and every tool it exposes is read-only.",
            "no write tool exists there, and a bytecode-walk test over every registered "
            "tool holds that none reaches a canonical write",
            "**The agent write path** — the `theurian propose` CLI only.",
            "No write-intent MCP tool exists (`writeTools: false`), and a test walks the "
            "bytecode of every registered tool to hold that none reaches a canonical write",
        ),
    ),
    (
        "docs/security/threat-model.md (T-12)",
        "threat-model",
        (
            "The two write-intent tools (`knowledge.proposeChange`, "
            "`knowledge.generateMigrationDraft`) emit proposal files, and the control that "
            'holds "no tool reaches approved state" is a **structural** one: they are '
            "handed a draft-only facade",
        ),
        (
            "Write-intent tools emit proposal files. A test enumerates every registered "
            "tool and asserts none reaches a canonical write.",
        ),
    ),
    (
        "docs/adr/0026-evidence-plane-not-control-plane.md (Compliance)",
        "boundary-adr",
        (
            "`writeTools: true` since slice B4 registered the two write-intent tools",
            "the two tools are handed a draft-only facade whose reachable surface is the two "
            "draft entries alone, so the accept path is not reachable from a tool at all "
            "(`application/draft_only_proposals.py`, ADR-0032 decision 8)",
            "`writeTools: true` is that case once more",
        ),
        (
            "`system.capabilities` reports `writeTools: false`, and",
            "The other capability flags — `traceability: false`, and `writeTools: false` beside it",
        ),
    ),
    (
        "mcp/tools.py (the module docstring)",
        "tools-docstring",
        (
            "**No canonical write.**",
            "Slice B4 registered two write-intent tools here -- ``knowledge.proposeChange`` "
            "and ``knowledge.generateMigrationDraft`` -- and they emit proposal files a human "
            "reviews and merges rather than mutating approved state; a draft-only facade holds "
            "that they reach no approved-state write (ADR-0032 decision 8).",
        ),
        (
            "**Read-only.** Nothing in this module reaches a canonical write.",
            "Milestone 3 ships no write-intent tools at all, and when they arrive",
        ),
    ),
    (
        "daemon/server.py (the @_tool-count comment)",
        "daemon-comment",
        (
            "the seven read-side tools sit far below this",
            "write-intent tools B4 registered, ``knowledge.proposeChange`` and",
        ),
        (
            "every tool registered today is read-side",
            "answers ``mcp/tools.py:7``",
        ),
    ),
    (
        "docs/adr/0029-review-findings-are-governed-knowledge.md",
        "findings-adr",
        (
            "registers exactly the known tool set (the read-side tools plus the two "
            "write-intent tools slice B4 registered, `KNOWN_TOOL_NAMES`)",
        ),
        ("registers exactly the known read-only tool set",),
    ),
)


#: One extractor per narrating record, keyed by the row's own reader name.
#:
#: A mapping rather than a chain of returns, for
#: ``test_review_ingestion_flag_claims.py``'s reason: a missing key raises here,
#: naming the reader, where a trailing ``return`` would silently hand a new row the
#: *last* extractor's text -- a row asserting its sentences against a file it does
#: not describe, and green for the wrong reason.
_ERA_READERS: Final[dict[str, Callable[[], str]]] = {
    "tools-comment": lambda: _comment_text(_TOOLS),
    "tools-docstring": lambda: _module_docstring(_TOOLS),
    "readme": lambda: _markdown_prose(_README),
    "docs-index": lambda: _markdown_prose(_DOCS_INDEX),
    "protocol-doc": lambda: _markdown_prose(_PROTOCOL_DOC),
    "roadmap": lambda: _markdown_prose(_ROADMAP),
    "threat-model": lambda: _markdown_prose(_THREAT_MODEL),
    "boundary-adr": lambda: _markdown_prose(_BOUNDARY_ADR),
    "daemon-comment": lambda: _comment_text(_DAEMON_SERVER),
    "findings-adr": lambda: _markdown_prose(_FINDINGS_ADR),
}


def _era_text(reader: str) -> str:
    """The text of one narrating record, read the way its own reader reads it."""
    assert reader in _ERA_READERS, (
        f"`{reader}` names no extractor, so the row that asked for it would be asserting "
        f"its sentences against another record's text. The readers are {sorted(_ERA_READERS)}"
    )
    return _ERA_READERS[reader]()


def _assert_states_the_era(label: str, text: str, published: object) -> None:
    """The narration check, in one place so its control drives the same path.

    Takes the published value and *selects* the wording from it, which is what
    makes the pin bidirectional: the same helper demands the read-only-era
    sentences of a build that publishes ``false``, so a flip back is RED until the
    records are re-tensed with it.
    """
    row = next(case for case in _ERA_NARRATIONS if case[0] == label)
    _name, _reader, on_true, on_false = row
    required, forbidden = (on_true, on_false) if published is True else (on_false, on_true)

    missing = [sentence for sentence in required if sentence not in text]
    stale = [sentence for sentence in forbidden if sentence in text]

    assert not missing and not stale, (
        f"{label} does not narrate `{_FLAG}: {str(published).lower()}`, which is what "
        f"`{_TOOLS.name}` publishes.\n"
        + "".join(f"\n  missing: {sentence}" for sentence in missing)
        + "".join(f"\n  stale:   {sentence}" for sentence in stale)
        + "\n\nIf the flag moved, this record is re-tensed in the same commit: it is one of "
        "the ten that narrate the value rather than merely mention it, and a record "
        "describing an era the build has left is read as current by everyone who opens it. "
        "If the flag did not move, the record drifted and the wording is what gets restored."
    )


@pytest.mark.parametrize(
    ("label", "reader"),
    [(case[0], case[1]) for case in _ERA_NARRATIONS],
    ids=[case[1] for case in _ERA_NARRATIONS],
)
def test_each_record_narrates_the_flag_the_capability_dict_publishes(
    label: str, reader: str
) -> None:
    """RED means a record describes an MCP surface this build has left.

    The failure this closes is the one the coupling arm above cannot see: it reads
    ``mcp/tools.py`` and nothing else, so every prose record that stated the
    read-only era could be restored to its ``false``-era wording with the whole
    suite green. Two commits corrected the first six and a later documentation
    commit corrected four more, and none left anything holding the correction,
    which is exactly the route ``test_review_ingestion_flag_claims.py`` was built
    to close for its own flag.

    The pin is bidirectional because either side can move. A record reworded back
    to the read-only era is RED against a build that publishes ``true``; a build
    that flips the flag back is RED against records still saying ``true``. Neither
    half alone would catch the other's failure.

    What decides which wording is demanded is the value read out of
    ``mcp/tools.py``'s own capability dict -- :func:`_published_flag`, the same
    reader the coupling arm uses -- so the expectation is never written twice and
    never hardcoded to today's value.
    """
    published = _published_flag(_tools_tree())
    text = _era_text(reader)

    assert text, (
        f"{label}'s extractor returned nothing, so every sentence below would report "
        f"itself missing and every forbidden one absent. The record moved, or its reader "
        f"stopped working -- settle which before touching the wording"
    )
    _assert_states_the_era(label, text, published)


def test_the_narration_checker_demands_the_other_era_when_the_flag_moves() -> None:
    """The positive control for a pin whose two directions cannot both be observed.

    The arm above runs against one published value at a time, so on any given day
    only one of its two directions is exercised and the other is a claim about what
    would happen. That is exactly the shape that quietly stops working: the
    unexercised branch could select the wrong column, compare against nothing, or
    have been written the wrong way round, and nothing would say so until the flag
    moved -- which is once.

    So both directions are driven here against synthetic text. The live wording is
    asked about under the *opposite* published value and must be reported stale;
    the read-only-era wording is asked about under ``True`` and must be reported
    missing; and the asymmetric first row -- the one whose ``false`` column is
    empty because the flip added its narration rather than re-tensing one -- is
    driven too, so the empty column is proven to forbid the ``true``-era sentences
    rather than to pass vacuously.
    """
    label, reader, _on_true, on_false = _ERA_NARRATIONS[1]
    live = _era_text(reader)

    with pytest.raises(AssertionError, match=re.escape("stale")):
        _assert_states_the_era(label, live, published=False)
    with pytest.raises(AssertionError, match=re.escape("missing")):
        _assert_states_the_era(label, " ".join(on_false), published=True)

    # The asymmetric row: an empty `false` column still forbids the `true` era.
    empty_column_label, empty_column_reader, _on_true, empty = _ERA_NARRATIONS[0]
    assert not empty, "the first row's `false` column is no longer the empty one this drives"
    with pytest.raises(AssertionError, match=re.escape("stale")):
        _assert_states_the_era(empty_column_label, _era_text(empty_column_reader), published=False)
