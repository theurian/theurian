"""``writeTools`` couples to the write-intent tool registrations (ADR-0032 decision 5).

``writeTools`` is a security claim published on ``system.capabilities``, not a
label: ``false`` says no write-intent tool a client may call exists, and
``docs/index.md`` tells a reader to take exactly that from it. ADR-0032 decision
5 ties the value to the first registration -- flipped in the *same* commit and in
both directions, which is ADR-0026's capability honesty (a flag may not move
ahead of its feature, nor lag behind it).

**Before this, the value could land half-moved.** ADR-0032's *Context* measured
that only two assertions move on a value flip, and none coupled the value to the
registration -- so a commit could flip the flag while registering no tool, or
register a tool while the flag still read ``false``, and the conformance suite
stayed green either way. That second case is the dangerous one: a registered
write path published as absent.

This pins the *coupling*, not the new value. Both facts are read from
``mcp/tools.py``'s own source -- the value a contributor wrote into the
capability dict, and the tool ``name``s a contributor registered through the one
``_tool`` seam. ``writeTools`` reads ``true`` exactly when a write-intent tool is
registered; move one without the other and it is RED. The shape and its
bidirectional control mirror
``test_review_ingestion_flag_claims.py``: a single checker takes both facts and
demands their agreement, and a control drives both directions -- including the
one a given build cannot exercise on its own -- against synthetic inputs.

**What this does not hold.** That the flag's value is *correct behaviour* -- that
the daemon really registers the tools and answers ``true`` over the wire. That is
``tests/integration/test_mcp_tools.py``'s
``test_capabilities_report_what_is_and_is_not_built`` and the built-server facade
walk beside ``test_no_registered_tool_can_reach_a_canonical_write``. This is the
source-level coupling that stops a half-moved flag from merging.

Pure: it reads one repository file through its syntax tree and opens no database,
no socket and no temporary directory.
"""

from __future__ import annotations

import ast
from typing import Final

import pytest
from write_lock_claims import REPO_ROOT

pytestmark = pytest.mark.unit

_TOOLS: Final = REPO_ROOT / "packages/theurian-core/src/theurian/mcp/tools.py"

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
