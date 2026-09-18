"""The three surfaces describing ``review.generateKnowledgeCandidate`` agree (decision 2).

The tool keeps its published name, because a tool name is a wire contract and
ADR-0030 rejected renaming a published one. The cost of keeping it is that
**"generate" must not be readable as "Theurian summarized"**, and decision 2 pays
that cost in prose rather than in a rename: the tool's own description, its
published input schema's description and the ADR's own table say the same thing.

Slice B5 owes the property *that they agree*, and the ADR is explicit about the
limit: "Whether the wording is faithful is a reading and no mechanical check
reaches it; what a test can hold is that the surfaces do not diverge from each
other." So this module holds divergence and nothing more. A build whose three
surfaces all carried a *wrong* shared sentence would pass here; what it cannot do
is carry the sentence in one place and a softer one in another, which is how a
capability claim drifts -- the reader of a ``tools/list`` response never opens the
ADR.

**Why a false claim here is not cosmetic.** ``system.capabilities`` reports
``writeTools: true`` and this tool is one of the three it is true because of. A
client that reads *generate a knowledge candidate* as *Theurian will write the
rule* hands the caller's own judgement to a product that makes none, and the
result reaches a human reviewer labelled as something it is not. ADR-0026's
false-capability defect is the family.

**The claim is one sentence pair, carried verbatim.** Not a paraphrase check:
three paraphrases of one claim are three claims a year later. It is written
without a dash, a backtick or a quote so that the identical bytes survive Markdown
prose, a JSON string and a Python string literal, and the comparison flattens soft
wraps because every one of those three wraps.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any, Final

import pytest
from mcp.server import MCPServer

from theurian.application.project_service import ProjectRegistry
from theurian.daemon.runner import build_server

pytestmark = pytest.mark.integration

REPO_ROOT: Final = pathlib.Path(__file__).resolve().parents[4]

ADR: Final = REPO_ROOT / "docs/adr/0033-knowledge-candidate-generation.md"
SCHEMA: Final = REPO_ROOT / "schemas/mcp/review-generate-knowledge-candidate-input.schema.json"

TOOL: Final = "review.generateKnowledgeCandidate"

#: A registered tool that describes a *different* write path, used as the control:
#: it must not carry the claim, or the containment test below would be satisfied by
#: any text at all.
OTHER_WRITE_TOOL: Final = "knowledge.proposeChange"

#: The claim decision 2 requires all three surfaces to make, in the words all three
#: carry.
#:
#: **Verbatim across three formats, which is why it is spelled the way it is.** No
#: em dash (Markdown prose uses one and a Python literal does not), no backtick
#: (the ADR would render one and a tool description is plain text), no quote
#: character (it crosses a JSON string). What is left is two plain sentences that
#: are the same bytes in ``docs/adr/0033-...md``, in the published schema's
#: ``description`` and in the ``description=`` of the tool's own registration.
NAME_HONESTY_CLAIM: Final = (
    "Theurian does not author the generalization. "
    "The caller supplies the title, the body, the kind and the category; "
    "Theurian verifies the promotion gate and packages the result."
)

#: The characters at least one of the claim's three formats transforms, so none of
#: them may appear in it. Spelled as escapes: written literally, this very line is
#: what ``ruff``'s RUF001 reports as ambiguous, which is the same property from the
#: other side.
_TRANSFORMED_CHARACTERS: Final = (
    "`"  # renders in Markdown, plain text everywhere else
    '"'  # escaped by JSON
    "'"
    "\u2014\u2013"  # em and en dash, which a Markdown editor substitutes for `--`
    "\u2018\u2019\u201c\u201d"  # smart quotes, likewise
)


@pytest.fixture(scope="module")
def server() -> MCPServer:
    """One built server for the whole module: every arm here is a pure read.

    The registry path is a name that is never created -- ``ProjectRegistry.load``
    treats an absent file as an empty registry -- so this touches no temporary
    directory and opens no socket. A tool's *description* does not depend on any
    project's contents.
    """
    return build_server(ProjectRegistry(path=pathlib.Path("/nonexistent/theurian-honesty.json")))


def _flattened(text: str) -> str:
    """``text`` with its soft wraps flattened, case and markup preserved.

    All three records wrap, and the ADR wraps in the middle of the sentence being
    quoted. An unflattened search finds the claim in whichever surface happens not
    to have wrapped there and reports the rest as having dropped it.
    """
    return " ".join(text.split())


def _tool_description(server: MCPServer, name: str) -> str:
    """One registered tool's published description, off the built server.

    From the registry rather than from ``mcp/tools.py``'s source, because what a
    client reads is what the server publishes: a description assembled at
    registration time, or moved to another seam, is still the thing under test.
    """
    found = [tool for tool in server._tool_manager.list_tools() if tool.name == name]

    assert len(found) == 1, (
        f"`{name}` is registered {len(found)} times, expected once. With none of them "
        f"this module asserts its claim against an empty string and reports a clean "
        f"record whatever any surface says"
    )
    return _flattened(found[0].description or "")


def _schema_description() -> str:
    """The published input schema's own top-level ``description``, flattened."""
    assert SCHEMA.exists(), (
        f"{SCHEMA.name} is not published, so the surface a client reads alongside "
        f"`tools/list` does not exist and the agreement below is about two records "
        f"rather than three."
    )
    document: Any = json.loads(SCHEMA.read_text(encoding="utf-8"))
    return _flattened(str(document.get("description", "")))


def _adr_prose() -> str:
    """ADR-0033 as prose, flattened, with block quoting removed.

    The block-quote strip is not cosmetic: decision 3's amendment is a ``>`` block,
    and a sentence inside one keeps a ``>`` in the middle of it once the wraps are
    flattened. Applied to the whole document so a later editor moving the claim
    into or out of an amendment block does not move it out of this pin.
    """
    return _flattened(
        " ".join(
            line.lstrip().removeprefix(">").lstrip() if line.lstrip().startswith(">") else line
            for line in ADR.read_text(encoding="utf-8").splitlines()
        )
    )


def test_the_tool_description_states_that_theurian_does_not_author_the_generalization(
    server: MCPServer,
) -> None:
    """RED means the surface a client actually reads does not make the claim.

    ``tools/list`` is where an agent decides what a tool does, and the name alone
    reads as *Theurian generates the candidate*. Decision 2 keeps the name and
    requires the description to carry the split instead, so this is the one of the
    three surfaces whose absence changes what a caller believes at call time.
    """
    description = _tool_description(server, TOOL)

    assert description, f"`{TOOL}` publishes an empty description; there is nothing to agree"
    assert NAME_HONESTY_CLAIM in description, (
        f"`{TOOL}`'s published description does not carry the name-honesty claim.\n\n"
        f"  expected to contain: {NAME_HONESTY_CLAIM}\n"
        f"  published:           {description}\n\n"
        f"ADR-0033 decision 2 keeps the published name and pays for it here: without "
        f"this sentence, `generate` reads as `Theurian summarized`, which is a "
        f"capability this product does not have (ADR-0026)."
    )


def test_the_published_schema_states_the_same_split_as_the_tool_description() -> None:
    """RED means the two runtime surfaces describe the tool differently.

    The schema's ``description`` is what a client reads when it inspects the
    contract rather than the listing, and it is the surface a caller consults while
    deciding what to put in ``title`` and ``body`` -- which is precisely the field
    the claim is about.
    """
    description = _schema_description()

    assert description, f"{SCHEMA.name} publishes an empty description"
    assert NAME_HONESTY_CLAIM in description, (
        f"{SCHEMA.name}'s description does not carry the name-honesty claim.\n\n"
        f"  expected to contain: {NAME_HONESTY_CLAIM}\n"
        f"  published:           {description}"
    )


def test_the_adr_states_the_claim_its_two_runtime_surfaces_carry() -> None:
    """RED means the decision and the thing it decided have come apart.

    The ADR is the authority the two runtime surfaces are copies of, and it is the
    half that drifts silently: a rewrite of decision 2's prose leaves both runtime
    surfaces green while the record they were copied from no longer says it. That
    is the shape ``test_adr_0030_claims.py`` records for its own scans -- a fact
    side and a prose side, each blind to the other, and neither worth having alone.
    """
    prose = _adr_prose()

    assert prose, "ADR-0033 read as an empty document; every claim below would pass vacuously"
    assert NAME_HONESTY_CLAIM in prose, (
        f"ADR-0033 no longer states the claim its tool description and input schema "
        f"carry.\n\n"
        f"  expected to contain: {NAME_HONESTY_CLAIM}\n\n"
        f"Decision 2 is where the name honesty lives now that the name is not being "
        f"changed. Restore the sentence, or move it on all three surfaces together."
    )


def test_a_different_write_tool_does_not_carry_the_claim(server: MCPServer) -> None:
    """The control for three containment checks, which would otherwise pass on anything.

    ``in`` over a long string is a weak assertion: a claim spelled so loosely that
    every description contains it would make all three arms green while holding
    nothing. ``knowledge.proposeChange`` is a registered write-intent tool whose
    caller also supplies a title and a body, and it is *not* under decision 2 --
    Theurian authors nothing there either, but the claim is about a tool whose name
    says *generate*. So it must not carry this sentence, and that it does not is
    what makes the three arms above discriminate.
    """
    description = _tool_description(server, OTHER_WRITE_TOOL)

    assert description, f"`{OTHER_WRITE_TOOL}` publishes an empty description"
    assert NAME_HONESTY_CLAIM not in description, (
        f"`{OTHER_WRITE_TOOL}` carries the claim this module holds `{TOOL}` to, so the "
        f"containment checks above are satisfied by any registered description and "
        f"discriminate nothing. Either the claim has been spelled too loosely, or it "
        f"has been pasted onto a tool decision 2 does not govern."
    )


def test_the_claim_survives_all_three_of_the_formats_it_is_written_in() -> None:
    """The premise every arm rests on: the same bytes really do cross three formats.

    The claim is compared verbatim against Markdown prose, a JSON string and a
    Python string literal. A character that one of those three transforms -- an em
    dash a Markdown editor substitutes, a backtick that renders, a quote that JSON
    escapes -- would leave the surfaces genuinely agreeing while one arm reported
    the claim missing, and the repair a reader reaches for is to weaken the claim.

    Driven rather than asserted about, so a future edit that reintroduces such a
    character is RED here with the reason rather than RED somewhere else with a
    puzzle.
    """
    forbidden = set(_TRANSFORMED_CHARACTERS)

    assert not (set(NAME_HONESTY_CLAIM) & forbidden), (
        f"the claim carries {sorted(set(NAME_HONESTY_CLAIM) & forbidden)}, which one of "
        f"its three formats transforms. Spell it in characters Markdown, JSON and "
        f"Python all leave alone."
    )
    assert json.loads(json.dumps(NAME_HONESTY_CLAIM)) == NAME_HONESTY_CLAIM
    assert _flattened(NAME_HONESTY_CLAIM) == NAME_HONESTY_CLAIM, (
        "the claim carries a line break or a double space, so a flattened surface "
        "cannot contain it verbatim"
    )
