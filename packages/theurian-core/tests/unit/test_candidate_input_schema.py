"""``review.generateKnowledgeCandidate``'s published input schema, as structure (SEC-12).

ADR-0031 makes the published JSON Schema the thing SEC-12 validates against, and
ADR-0033 puts three of its own decisions *inside* that file rather than in a
handler: ``category`` is the eleven-member FR-V2 enum (decision 6), ``evidence`` is
required with the four members ADR-0013 point 5 names, and **no gate signal is a
field a caller may set** (decision 3). A handler refusal would be a different
control: it fires after the call has been admitted, and ADR-0031 decision 4 is that
a value-domain violation is answered at the wire with the key path that broke.

**What is derived here, and from what.** The field set is recomputed from
:class:`~theurian.application.candidate_generation.CandidateSubmission`, the
service seam the tool maps onto; the forbidden set from
:class:`~theurian.domain.review.PromotionGate`'s own fields; the ``category``
vocabulary from :class:`~theurian.domain.enums.ReviewCommentCategory`; the
evidence members from :class:`~theurian.domain.proposal.Evidence`; and the two
length bounds from the content write tool's own published file. Nothing below
writes a vocabulary down twice -- *pin derivations, not prose* -- so a member added
to any of those types reddens here instead of leaving the wire contract describing
the type it was transcribed from.

**What this module does not hold.** That the schema *loads* into the SEC-12
middleware and that the registered tool resolves to it:
``tests/integration/test_input_validation_dispatch.py::test_every_registered_tool_resolves_to_a_published_input_schema``
derives that over the built server for every tool at once, and a second copy here
would be checked against itself. That the published bounds equal their live
constants is ``test_input_schema_bounds.py``'s, under a population it asserts by
equality. That the published keys agree with the handler's parameters is
``test_input_schema_agreement.py``'s.

Pure: three files read as text, no database, socket or temporary directory.
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
import re
from collections.abc import Iterator
from typing import Any, Final

import pytest
from fix_commit_grammar import ADMITTED, MAX_CHARS, REFUSED, RefusedFixCommit
from jsonschema import Draft202012Validator

from theurian.application.candidate_generation import CandidateSubmission
from theurian.domain.enums import ReviewCommentCategory
from theurian.domain.proposal import Evidence
from theurian.domain.review import PromotionGate

pytestmark = pytest.mark.unit

REPO_ROOT: Final = pathlib.Path(__file__).resolve().parents[4]
MCP_SCHEMAS: Final = REPO_ROOT / "schemas" / "mcp"

SCHEMA: Final = MCP_SCHEMAS / "review-generate-knowledge-candidate-input.schema.json"

#: The content write tool's file, read for the two length bounds this tool carries
#: the same value-domain family of (ADR-0032 decision 3): a body that is inline
#: text with no path anywhere near it, and an item id bounded before it is echoed
#: back in a refusal.
PROPOSE_CHANGE_SCHEMA: Final = MCP_SCHEMAS / "knowledge-propose-change-input.schema.json"

#: The wire name, which ADR-0033 decision 2 declines to change: it is published in
#: ``docs/protocol/mcp-tools.md``'s planned-tools table, and a tool name is a wire
#: contract. Whether the *registry* and that document agree is
#: ``test_documented_tool_set.py``'s, derived from the built server.
TOOL_NAME: Final = "review.generateKnowledgeCandidate"

#: ``Evidence.anchors`` is not a member of the wire ``evidence`` object: it is
#: filled from the separate top-level ``sourceAnchors`` field, which is ADR-0032
#: decision 1's *one wire field fills two* shape and how
#: ``knowledge.proposeChange`` already spells it. So the schema publishes one key
#: the submission type has no field named after, and it is named here rather than
#: left for a reader to discover in a failure message.
ANCHORS_WIRE_FIELD: Final = "sourceAnchors"


def _camel(name: str) -> str:
    """One ``snake_case`` field name as the wire spells it."""
    head, *rest = name.split("_")
    return head + "".join(part.title() for part in rest)


def _document() -> dict[str, Any]:
    """The published schema, parsed.

    Asserted present before it is read, because the absence is the state this
    module was written in: the schema file is the registration change's, and every
    arm below would otherwise fail as a ``FileNotFoundError`` traceback rather than
    as a claim about a contract.
    """
    assert SCHEMA.exists(), (
        f"{SCHEMA.name} is not published. SEC-12 refuses any tool with no loaded "
        f"input schema (ADR-0031 decision 5), so `{TOOL_NAME}` cannot be served "
        f"until this file exists; every structural claim below is about it."
    )
    parsed: Any = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict), f"{SCHEMA.name} parsed to a {type(parsed).__name__}"
    return parsed


def _property_names(node: Any) -> Iterator[str]:
    """Every key declared under a ``properties`` object anywhere in the document.

    The whole document rather than its top level: a ``trustLevel`` nested inside
    ``evidence`` would be the same forbidden field one level down, and a walk that
    stopped at depth one would report it as absent.
    """
    if isinstance(node, dict):
        declared = node.get("properties")
        if isinstance(declared, dict):
            yield from declared
        for value in node.values():
            yield from _property_names(value)
    elif isinstance(node, list):
        for value in node:
            yield from _property_names(value)


def _validator() -> Draft202012Validator:
    """A validator over the published schema alone, with its ``$ref`` branches dropped.

    The ``allOf`` ``$ref`` to ``tool-context.schema.json`` is resolved offline by
    the middleware's own registry (``mcp/validation.py``), which needs the whole
    tree. The two behavioural arms below ask about *this file's* own keywords, so
    they drop the reference rather than rebuilding that registry -- what they would
    otherwise measure is whether ``projectId`` is present, which is a different
    claim with a different owner.
    """
    document = {key: value for key, value in _document().items() if key != "allOf"}
    return Draft202012Validator(document)


def _wire_fields() -> frozenset[str]:
    """Every field ``CandidateSubmission`` carries, as the wire spells it."""
    return frozenset(_camel(field.name) for field in dataclasses.fields(CandidateSubmission))


def _admitted_submission() -> dict[str, Any]:
    """A complete, well-formed call, which the published schema must admit.

    The baseline the two behavioural arms perturb by exactly one thing each. Every
    required field is present, so an error they report is about the perturbation
    and not about what the instance was missing anyway -- the difference between a
    closure check and a check that a partial object is incomplete.

    ``projectId`` is absent deliberately: it is required by the
    ``tool-context.schema.json`` referent, and :func:`_validator` drops the ``$ref``
    branch that would demand it.
    """
    return {
        "repository": "acme/order-service",
        "recordKey": "PRRT_kwDOaaaaaaaaaaaa",
        "fixCommit": "a" * 40,
        "itemId": "reliability.retry-lock-order",
        "title": "Acquire locks after reads",
        "body": "Acquire locks after reads, never before.\n",
        "kind": "convention",
        "category": "reliability-rule",
        "owner": "platform-team",
        "author": "dana@example.com",
        "description": "Generalise the deadlock thread into a locking rule",
        "evidence": {
            "agentId": "claude-code",
            "taskId": "task-431",
            "model": "claude-opus-5",
            "reasoning": "The thread settled the lock ordering; this generalises it.",
        },
        ANCHORS_WIRE_FIELD: [
            {
                "provider": "github",
                "sourceUri": "https://github.com/acme/order-service/pull/431#discussion_r1",
            }
        ],
    }


def test_the_published_schema_declares_the_wire_name_the_protocol_page_published() -> None:
    """RED means the file publishes a contract for some other tool, or for none.

    ``x-theurian-tool`` is the only thing that maps a file to a tool:
    ``load_input_schemas`` keys the set by it and refuses a file that declares
    none, because ``knowledge.search`` is a legal tool name and not a path
    component to round-trip through. A file under the right name declaring the
    wrong tool leaves this tool with no schema -- which SEC-12 answers by refusing
    every call to it (ADR-0031 decision 5).
    """
    declared = _document().get("x-theurian-tool")

    assert declared == TOOL_NAME, (
        f"{SCHEMA.name} declares `x-theurian-tool` {declared!r}, and the published "
        f"wire name is {TOOL_NAME!r} (ADR-0033 decision 2: the name is a wire "
        f"contract and is not renamed). The loader keys its set by this value, so a "
        f"mismatch publishes no schema for the tool and every call to it is refused."
    )


def test_the_published_schema_names_exactly_the_fields_the_submission_type_carries() -> None:
    """RED means the wire contract and the service seam describe different calls.

    Both directions cost something and they cost different things. A published key
    the submission has no field for is answered as though it had never been sent --
    ADR-0031 decision 6's *published lie*, and on a write path it means a caller
    believes it stated something about the proposal it did not. A submission field
    no key publishes is unreachable, because the schema closes with
    ``unevaluatedProperties: false``: the capability exists and nothing can call it.

    Recomputed from ``CandidateSubmission`` rather than listed, so a field added to
    the seam arrives here as a RED naming it instead of as an unpublished
    parameter. ``projectId``, ``agentId``, ``taskId`` and ``snapshotId`` are not in
    either set: they come from the ``tool-context.schema.json`` referent, and this
    compares the file's *own* ``properties``.
    """
    published = frozenset(_document().get("properties", {}))
    expected = _wire_fields() | {ANCHORS_WIRE_FIELD}

    assert published == expected, (
        f"published and unbacked by a `CandidateSubmission` field: "
        f"{sorted(published - expected)}; carried by the submission and unpublished: "
        f"{sorted(expected - published)}.\n\n"
        f"`{ANCHORS_WIRE_FIELD}` is the one expected key with no same-named field: "
        f"`Evidence.anchors` is filled from it, the way `knowledge.proposeChange` "
        f"already spells it (ADR-0032 decision 1). If the tool instead derives the "
        f"anchors from the stored record, that is a design change to ADR-0033 "
        f"decision 1's table and this pin moves with it rather than around it."
    )


def test_no_promotion_gate_signal_is_a_field_a_caller_may_set() -> None:
    """ADR-0033 decision 3: a caller-asserted gate signal is a forgeable promotion signal.

    The gate's stated job is to answer *should someone look at this?* on observed
    facts. Five signals are recomputed from the stored record, ``fix_commit_present``
    is satisfied by a verification rather than by the caller's word, and
    ``generalizable`` by the submission itself -- so **none of the seven is a wire
    field**, and the schema is where that is structural rather than a handler's
    decision to ignore a key it was sent.

    The forbidden set is read off ``PromotionGate`` live, so a signal added to the
    gate is forbidden here by existing. The walk covers nested ``properties`` too:
    a ``ciSuccessful`` inside ``evidence`` is the same forgeable signal one level
    down.

    The premise comes first. An empty forbidden set, or a document declaring no
    properties at all, would make the disjointness below hold over nothing.
    """
    forbidden = frozenset(_camel(field.name) for field in dataclasses.fields(PromotionGate))
    declared = frozenset(_property_names(_document()))

    assert forbidden, "PromotionGate declares no fields; the check below would be vacuous"
    assert declared, f"{SCHEMA.name} declares no properties at all; nothing is being checked"
    assert not (declared & forbidden), (
        f"{SCHEMA.name} publishes {sorted(declared & forbidden)} as caller input, and "
        f"each is a `PromotionGate` signal. A gate the caller fills is a gate the "
        f"caller decides, and the tool becomes a proposal generator with a "
        f"gate-shaped ceremony attached (ADR-0033 decision 3)."
    )


#: The four values ADR-0033 decision 1's table takes from somewhere other than the
#: wire, with what fixes each. Not gate signals -- those are the arm above -- and
#: not a stylistic exclusion: each one names a claim a caller could otherwise make
#: about knowledge it did not have the standing to make it about.
_NOT_CALLER_SETTABLE: Final = {
    "trustLevel": (
        "`KnowledgeCandidate.trust_level` is `field(default=INFERRED, init=False)`, so no "
        "candidate can carry another value; a wire field would let a caller claim "
        "review-level trust, which is exactly what the human reviewer grants"
    ),
    "sensitivity": (
        "fixed to `internal` by `KnowledgeCandidate.sensitivity`'s own default and never "
        "set at generation, so it is never widened and there is no review-project default "
        "to read; a wire field would let a caller widen a disclosure class from the write path"
    ),
    "contentType": (
        "fixed to `text/markdown`: a generalisation is prose and there is no file whose "
        "suffix could say otherwise (ADR-0033 decision 1, ADR-0032 decision 2)"
    ),
    "local": (
        "`CandidateGenerator.generate` drafts with `local=False` (ADR-0013 point 7): a "
        "`--local` proposal lands inside the managed ignore block, where the human "
        "review FR-V4 relies on cannot reach it"
    ),
}


@pytest.mark.parametrize(
    ("field", "why"),
    sorted(_NOT_CALLER_SETTABLE.items()),
    ids=sorted(_NOT_CALLER_SETTABLE),
)
def test_a_value_the_candidate_fixes_is_not_a_field_the_wire_offers(field: str, why: str) -> None:
    """RED means a caller can state something ADR-0033 decision 1 fixes elsewhere.

    Separate from the gate arm because the failure is different: a gate signal
    forged on the wire promotes a thread that has not earned a human's attention,
    while one of these misstates the knowledge the proposal carries -- its trust,
    its disclosure class, its media type, or whether a reviewer can see it at all.
    Parametrized so a RED names the field and the reason rather than a set
    difference.
    """
    declared = frozenset(_property_names(_document()))

    assert declared, f"{SCHEMA.name} declares no properties at all; nothing is being checked"
    assert field not in declared, (
        f"{SCHEMA.name} publishes `{field}` as caller input, and it is not the "
        f"caller's to state: {why}."
    )


def test_the_category_vocabulary_is_every_review_comment_category_and_nothing_else() -> None:
    """ADR-0033 decision 6: the eleven-member FR-V2 enum, constrained at the wire.

    ``category`` is the one vocabulary this tool closes in the schema rather than
    in the handler, and the reason is the cost asymmetry ADR-0031 decision 4
    records: a closed enum answered at the wire names the key path that broke,
    where a handler refusal for a *classification* would arrive after the record
    had been resolved and the gate recomputed.

    Recomputed from ``ReviewCommentCategory``, not transcribed. A twelfth category
    added to the enum and not to the file leaves a value the domain accepts and the
    wire refuses; one removed leaves a value the wire admits and the domain cannot
    parse.
    """
    members = frozenset(member.value for member in ReviewCommentCategory)
    published = _document().get("properties", {}).get("category", {})

    assert members, "ReviewCommentCategory has no members; the comparison would be vacuous"
    assert frozenset(published.get("enum", ())) == members, (
        f"{SCHEMA.name} constrains `category` to {sorted(published.get('enum', ()))} and "
        f"`ReviewCommentCategory` has {sorted(members)}. The document and the enum are "
        f"one vocabulary (ADR-0033 decision 6); move both in the same change."
    )


def test_evidence_is_required_with_the_four_members_a_proposal_is_rejected_without() -> None:
    """ADR-0013 point 5: a proposal with no evidence is rejected at generation.

    ``evidence`` is also what makes this tool *write-intent* over the wire: the E2E
    session derives the write-intent set from the published schemas' ``required``
    lists, so a schema that admitted an evidence-less call would quietly drop this
    tool out of the set that session claims to drive.

    The four members are recomputed from ``proposal.Evidence``, less ``anchors``,
    which the wire fills through ``sourceAnchors`` instead -- the exclusion is
    named here rather than left as a magic number, and the arm below is what holds
    the anchors half.
    """
    document = _document()
    members = frozenset(
        _camel(field.name) for field in dataclasses.fields(Evidence) if field.name != "anchors"
    )
    published = document.get("properties", {}).get("evidence", {})

    assert "evidence" in document.get("required", ()), (
        f"{SCHEMA.name} does not require `evidence`. ADR-0013 point 5 rejects a "
        f"proposal without it at generation, so an optional field means the refusal "
        f"arrives from inside `Evidence.__post_init__` with no key path -- and the "
        f"E2E write-intent derivation stops counting this tool."
    )
    assert frozenset(published.get("required", ())) == members, (
        f"{SCHEMA.name} requires {sorted(published.get('required', ()))} of `evidence` "
        f"and `proposal.Evidence` carries {sorted(members)} beside its anchors."
    )


@pytest.mark.parametrize(
    ("label", "anchors"),
    [("the key absent", None), ("an empty array", [])],
    ids=["absent", "empty"],
)
def test_a_submission_carrying_no_source_anchor_is_refused_by_the_published_schema(
    label: str, anchors: list[Any] | None
) -> None:
    """RED means an anchor-less call is refused below the surface instead of at the wire.

    ``KnowledgeCandidate.__post_init__`` raises ``InvariantViolationError`` on an
    empty ``evidence`` tuple, unconditionally -- there is no ``authored-in-theurian``
    escape here, because a generalisation of a review thread is by construction
    anchored to that thread. So a call with no source anchor cannot succeed, and the
    only question is *where* it is refused. At the wire it is a schema refusal
    naming the key and carrying ``tools/list`` as the remedy; below it, it is an
    invariant violation crossing the forwarding seam as a sentence about a domain
    type the caller has never heard of.

    Which keyword produces the refusal is left open -- ``required`` and
    ``minItems: 1`` both satisfy it -- because what matters is the answer and not
    the spelling.
    """
    validator = _validator()
    submission = _admitted_submission()
    submission.pop(ANCHORS_WIRE_FIELD)
    if anchors is not None:
        submission[ANCHORS_WIRE_FIELD] = anchors

    assert not list(validator.iter_errors(_admitted_submission())), (
        "the baseline submission this arm perturbs is itself refused, so the refusal "
        "below would be about whatever else the schema objected to rather than about "
        "the anchors"
    )
    assert list(validator.iter_errors(submission)), (
        f"the published schema admits a submission with {label} for "
        f"`{ANCHORS_WIRE_FIELD}`. That call cannot produce a candidate -- "
        f"`KnowledgeCandidate` refuses an empty evidence tuple at construction -- so "
        f"admitting it moves the refusal below the wire, where it carries no key "
        f"path and no remedy."
    )


def test_an_unknown_key_is_refused_by_the_published_schema() -> None:
    """ADR-0031 decision 1's closure, per file, because it is per file that it is written.

    Every published input schema closes with ``unevaluatedProperties: false``
    rather than ``additionalProperties: false``, which under a ``$ref`` composition
    would reject the very context fields it referenced. A file that forgot the
    closure admits any key a caller invents and answers as though it had been
    understood -- the silence SEC-12 exists to end.

    Driven as behaviour rather than asserted as a keyword, so a closure spelled some
    other way that still refuses is not a failure.

    **The baseline is asserted admitted first, and that is not ceremony.** Without
    it the perturbed instance is refused for its missing required fields whatever
    the closure says: dropping ``unevaluatedProperties`` from a draft schema left
    this arm green, which is the assertion-that-cannot-fail shape the file is here
    to avoid.
    """
    validator = _validator()
    baseline = _admitted_submission()

    assert not list(validator.iter_errors(baseline)), (
        "the baseline submission is refused by the published schema, so the arm below "
        "would be green on the missing fields rather than on the unknown key"
    )
    assert list(validator.iter_errors({**baseline, "unknownKey": 1})), (
        f"{SCHEMA.name} admits an unknown key beside a complete submission, so a "
        f"caller that misspells a field -- or invents one -- is served as though it "
        f"had sent nothing. Close the schema with `unevaluatedProperties: false` "
        f"(ADR-0031 decision 1)."
    )


@pytest.mark.parametrize("value", ADMITTED, ids=[f"{len(value)}-hex" for value in ADMITTED])
def test_the_published_schema_admits_a_full_object_name_as_a_fix_commit(value: str) -> None:
    """The admitted side of the grammar, which every refusal below is measured against.

    Both object-name widths, because a ``--object-format=sha256`` repository
    names its commits in sixty-four hex digits and a forty-only pattern would
    refuse every commit in one -- a value-domain refusal at the wire, where the
    caller is told its own correct sha does not satisfy the contract.
    """
    validator = _validator()

    assert not list(validator.iter_errors({**_admitted_submission(), "fixCommit": value})), (
        f"the published schema refuses a {len(value)}-digit object name as `fixCommit`. "
        f"The grammar admits both widths (`fix_commit_grammar.ADMITTED`), and a pattern "
        f"that refuses one of them refuses every commit in a repository that uses it."
    )


@pytest.mark.parametrize("member", REFUSED, ids=[member.label for member in REFUSED])
def test_the_published_schema_refuses_a_fix_commit_that_is_a_revision_expression(
    member: RefusedFixCommit,
) -> None:
    """SEC-12's half of the funnel: a revision expression never reaches the handler.

    ``fixCommit`` is spent against the local repository, and git's revision
    language lets a caller *describe* a commit -- by message, by branch, by
    position, by range -- rather than name one, which is the whole of what
    ADR-0033 decision 3 asks the caller to go and find. The adapter refuses these
    at entry too (``tests/integration/test_fix_commit_check_adapter.py`` drives
    this same corpus against a real repository); what this seam adds is the key
    path, so a caller that sent an expression is told **which field** broke and
    against what, instead of meeting a gate refusal that reads as *find a better
    commit* (ADR-0031 decision 4).

    **One member is admitted here and refused by the adapter, and that is
    measured rather than overlooked.** ``jsonschema`` evaluates ``pattern`` with
    Python's ``re``, whose ``$`` matches before a trailing newline; the adapter's
    ``fullmatch`` does not. ``fix_commit_grammar`` carries that verdict on the
    member itself, which is what stops this arm quietly demanding a
    Python-specific anchor in a published ECMA-262 pattern.

    The baseline is asserted admitted first: without it every member passes on
    whatever else the instance was missing, which is the shape a closure check
    fails open in.
    """
    validator = _validator()
    submission = {**_admitted_submission(), "fixCommit": member.value}

    assert not list(validator.iter_errors(_admitted_submission())), (
        "the baseline submission this arm perturbs is itself refused, so a refusal below "
        "would be about whatever else the schema objected to rather than about `fixCommit`"
    )
    refused = bool(list(validator.iter_errors(submission)))

    assert refused is member.at_the_wire, (
        f"{member.label}: the published schema "
        f"{'admits' if not refused else 'refuses'} {member.value!r} as `fixCommit` and "
        f"`fix_commit_grammar` records it as "
        f"{'refused' if member.at_the_wire else 'admitted'} here.\n\n"
        f"Why it is not an object name: {member.why}.\n\n"
        f"If the grammar moved, move the corpus, this file's `pattern` and the adapter's "
        f"entry funnel in one change -- two defences written in two regex dialects are "
        f"exactly what drifts apart unnoticed."
    )


def test_the_published_fix_commit_bound_is_the_widest_object_name_the_grammar_admits() -> None:
    """A ``maxLength`` beside the pattern, so an oversized value is refused by its length.

    The pattern alone would refuse a ten-megabyte ``fixCommit``, but only after
    the regex has been run over it. ``maxLength`` is the constraint that answers
    from the length, and it is the widest object name rather than a round number
    -- derived here from the corpus, so a grammar that grew a wider member moves
    both together.
    """
    published = _document().get("properties", {}).get("fixCommit", {})

    assert published.get("maxLength") == MAX_CHARS, (
        f"{SCHEMA.name} bounds `fixCommit` at {published.get('maxLength')!r}; the widest "
        f"object name the grammar admits is {MAX_CHARS} characters. That the number is "
        f"also the live constant the adapter enforces is `test_input_schema_bounds.py`'s."
    )


#: Each bound this tool carries the content write tool's value-domain family of,
#: and where that family is written down. The *number* is read off
#: ``knowledge-propose-change-input.schema.json`` rather than repeated here; what
#: this table says is which keys must agree, and why each one is the same question
#: on both tools.
_MIRRORED_BOUNDS: Final = {
    "itemId": (
        "both tools echo a caller's item id back in a refusal, so both bound it "
        "before the echo at `MAX_IDENTIFIER_LENGTH` -- the value `ItemId` itself refuses "
        "past"
    ),
    "body": (
        "both take the knowledge body as inline text with no path anywhere in the input "
        "(ADR-0032 decision 2), so both bound it at the transport cap in code points"
    ),
}


@pytest.mark.parametrize(
    ("field", "why"), sorted(_MIRRORED_BOUNDS.items()), ids=sorted(_MIRRORED_BOUNDS)
)
def test_a_bound_this_tool_shares_with_the_content_write_tool_is_the_same_bound(
    field: str, why: str
) -> None:
    """RED means two write-intent tools bound one kind of value at two widths.

    ADR-0032 decision 3 puts these bounds in the schema rather than in a handler,
    and ADR-0033 joins that surface additively -- so a caller that learned a limit
    from one tool and met a different one on the other has been told two things
    about the same value. That both numbers are the live constants they transcribe
    is ``test_input_schema_bounds.py``'s, under a population it asserts by
    equality; what this holds is that the two published files agree with **each
    other**, which no derived check reaches.
    """
    mirrored = json.loads(PROPOSE_CHANGE_SCHEMA.read_text(encoding="utf-8"))
    expected = mirrored["properties"][field].get("maxLength")
    published = _document().get("properties", {}).get(field, {}).get("maxLength")

    assert expected is not None, (
        f"{PROPOSE_CHANGE_SCHEMA.name} no longer bounds `{field}`, so this comparison "
        f"has nothing to mirror and would pass against any value"
    )
    assert published == expected, (
        f"{SCHEMA.name} bounds `{field}` at {published!r} and {PROPOSE_CHANGE_SCHEMA.name} "
        f"bounds it at {expected!r}: {why}."
    )


def test_the_property_walk_reaches_a_declaration_below_the_top_level() -> None:
    """The premise the two absence arms rest on, driven rather than assumed.

    Every forbidden name today would be a top-level ``properties`` key, so a walk
    simplified to that shape would stay green -- and the arms above, whose whole
    value is that a forbidden field cannot hide, would silently stop covering the
    first one written inside ``evidence`` or inside a ``sourceAnchors`` item. An
    absence check that only looks where nothing is hiding reports safety it has not
    measured.

    Driven against a synthetic document, because the published schema is not
    expected to have that shape and a premise check that waits for one fails open.
    """
    nested = {
        "properties": {
            "evidence": {"type": "object", "properties": {"trustLevel": {"type": "string"}}},
            "sourceAnchors": {"items": {"properties": {"ciSuccessful": {"type": "boolean"}}}},
        }
    }

    assert set(_property_names(nested)) == {
        "evidence",
        "sourceAnchors",
        "trustLevel",
        "ciSuccessful",
    }


def test_the_camel_case_reading_is_the_spelling_the_wire_uses() -> None:
    """The premise every derived field name above rests on.

    Three of the arms compare wire keys against ``snake_case`` dataclass fields
    through :func:`_camel`. A reading that dropped a word, lower-cased a whole
    name, or left the underscores in would make every one of those comparisons
    range over names that appear in no schema -- which the forbidden arms would
    report as *absent* and therefore as safe.
    """
    assert [
        _camel(name) for name in ("record_key", "fix_commit_present", "body", "scope_paths")
    ] == [
        "recordKey",
        "fixCommitPresent",
        "body",
        "scopePaths",
    ]


def test_no_schema_property_name_carries_an_underscore() -> None:
    """The other half of that premise, over the file itself.

    :func:`_camel` answers what a ``snake_case`` field is called on the wire; it
    cannot tell whether the file agreed to use that spelling. A schema that
    published ``record_key`` would pass every absence arm above -- ``fixCommitPresent``
    is genuinely not in it -- while the field set arm reported one difference and a
    reader repaired it by widening the expectation rather than the file.
    """
    snake = sorted(name for name in _property_names(_document()) if re.search(r"_", name))

    assert not snake, (
        f"{SCHEMA.name} publishes {snake} in `snake_case`. The MCP wire contract is "
        f"camelCase throughout (`mcp/tools.py`'s `# noqa: N803` parameters), and a "
        f"mixed file makes every derived comparison in this module read the wrong "
        f"spelling as an absence."
    )
