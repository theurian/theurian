"""What ADR-0013 says about proposal ageing, against what ``knowledge.status`` serves.

ADR-0013's Consequences used to say, in the present tense, that
``knowledge.status`` reports proposal age and that ``doctor`` warns past a
threshold. Neither half had an implementation: the response schema names no
proposal field, and ``doctor_command`` reads ``.theurian/proposals/`` nowhere.
The bullet now reads as owed rather than shipped and names
https://github.com/theurian/theurian/issues/414, which owns building both
(https://github.com/theurian/theurian/issues/252).

**The correction was landed with nothing holding it in either direction**, which
is the gap this module closes. Two things can each make the ADR false again, and
they fail here separately:

- **Drift back.** Someone rewrites the bullet in the present tense, or adds the
  claim back elsewhere in the file, and a durable architectural record asserts a
  mechanism that does not exist -- the class #252 belongs to, alongside the #198
  and #129 corrections.
- **#414 lands.** The report is built, ``knowledge.status`` grows a field whose
  name says so, and the ADR goes on calling it owed. The fact half below goes RED
  on that schema change, so the bullet is updated by the change that makes it
  wrong rather than by whoever notices later -- within the reach stated below,
  which is narrower than "the report ships".

This is the pattern ``test_setup_claims.py`` states: pin fact and prose to each
other in both directions, so neither can move without the other. What differs is
where the fact lives. Setup's claims are held against a step table this process
can call; a proposal-age *report* has no code to probe, so its absence is read
off the published contract -- ``schemas/mcp/knowledge-status-response.schema.json``,
which sets ``additionalProperties: false`` and is validated against a real
``knowledge.status`` response by ``tests/integration/test_wire_contract.py``. The
field names are read from that file rather than restated here, so the check holds
for whatever the response grows next.

**What the fact side actually enforces, which is narrower than that.** It matches
field *names*, so:

- A proposal-**named** field fails this module however deeply it is nested.
  Measured: ``proposalAge`` at the top level, and ``oldestProposalAgeDays`` under
  ``integrity``, whose own key does not match -- both RED.
- A proposal-**age** field under any other name does not. Measured:
  ``oldestUnreviewedDays``, added as a required top-level field, leaves all three
  tests green. #414 can ship the report under a name like that and only the prose
  pin would still be asserting the bullet is owed.
- The ``doctor`` half has no fact-side contract at all. Nothing published
  describes it, so the prose pin alone holds it, and no perturbation of a schema
  can turn that half RED.

Closing the second and third would mean guessing names and mechanisms #414 has
not chosen -- the same defect as pinning grammar, one level down. The reach is
recorded here instead, because a reader deciding whether this module covers them
must not be told it covers more than it does.

**Two named files, and the corpus twin is deliberately not one of them.**
``.theurian/knowledge/architecture/ai-writes-produce-proposals.<ulid>.md`` is a
governed snapshot of this ADR and still carries the uncorrected sentence. That is
not drift and must not be repaired by widening a scan here: the dogfood corpus is
held byte-identical to its source anchor commit by
``test_dogfood_corpus_governance.py::test_every_pinned_body_is_byte_identical_to_its_source_anchor_commit``,
so the snapshot is correct *for its anchor* and only a new revision can move it.
A repo-wide walker over this wording would go RED on that file on the day it was
written. Recorded rather than closed, and recorded here because a reader who
greps the tree for the old sentence finds it and needs to know why it stays.

**Neither half is a closure argument.** The prose test is a regression pin over
the one wording this claim has actually taken, and a rule that pins grammar
always has a next grammar. The fact test is exact for the schema and says nothing
about ``doctor``, whose half of the bullet no published contract describes.
"""

from __future__ import annotations

import json
import pathlib
import re
from typing import Final

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]

ADR_0013 = REPO_ROOT / "docs" / "adr" / "0013-ai-writes-produce-proposals.md"
STATUS_RESPONSE_SCHEMA = REPO_ROOT / "schemas" / "mcp" / "knowledge-status-response.schema.json"

#: The corrected bullet, as one sentence rather than as the two lines the file
#: wraps it onto. Compared after :func:`_collapsed`, because the claim spans a
#: line break -- "are owed, not\n  shipped" -- and a substring search over the raw
#: text passes while the sentence is being rewritten around it.
OWED_CLAIM: Final = (
    "a proposal-age report in `knowledge.status` and a `doctor` warning "
    "past a threshold are owed, not shipped"
)

#: The issue that owns building both halves. Asserted inside the same sentence as
#: :data:`OWED_CLAIM`: an owed item with no owner named is how "owed" becomes
#: permanent, and the bullet is the only place ADR-0013 says who owes it.
OWED_BY: Final = "issues/414"

#: The report half of the retracted claim -- "reports proposal age", with up to
#: three words in between so "reports the proposal age" is caught too.
#:
#: **The plural is not optional, and that is the whole of the difference.**
#: ``\breports?\b`` also matched the bare ``report``, so the owed future tense
#: #252's own body proposes -- "`knowledge.status` will report proposal age when
#: the write tools land" -- fired the negative test with a message saying the ADR
#: claims it *already* reports. Measured against the compiled pattern. The finite
#: ``reports`` is the claim this module refuses; ``will report`` is the thing the
#: bullet is allowed to say, and a pin that punishes the correct wording teaches
#: the next author to delete the pin.
_ALREADY_REPORTS_PROPOSAL_AGE: Final = re.compile(r"\breports\b(?:\s+\w+){0,3}?\s+proposal[ -]age")

#: The ``doctor`` half. ``warns``, not ``warning``: the corrected bullet says "a
#: `doctor` warning past a threshold **are owed**", so the noun is what the ADR
#: is supposed to contain and the finite verb is the claim it must not make.
_DOCTOR_ALREADY_WARNS: Final = re.compile(r"`doctor`\s+warns\b")

#: The end of a sentence, which is not every period: the bullet closes on a
#: Markdown link, and ``https://github.com/...`` carries two dots that end
#: nothing. Splitting on a bare ``.`` cut the sentence at ``github`` and reported
#: the issue reference missing while it was three words away -- measured, on the
#: corrected file this module was written against.
_SENTENCE_END: Final = re.compile(r"\.(?=\s|$)")

#: Any field whose name concerns proposals. Matched case-insensitively against
#: names the schema declares, so ``proposalAge``, ``oldestProposal`` and
#: ``proposals`` all count. It does not match the existing ``proposed`` key of
#: ``itemsByStatus``, which is a status count and not a report of age.
_PROPOSAL_FIELD: Final = re.compile(r"proposal", re.IGNORECASE)


def _collapsed(text: str) -> str:
    """Lowercased with runs of whitespace flattened to single spaces."""
    return " ".join(text.lower().split())


def _declared_property_names(schema: object) -> list[str]:
    """Every name declared under a ``properties`` object, at any depth.

    Recursive rather than a read of the top level, because a proposal-age report
    can arrive nested -- under a new ``proposals`` object, or beside the fields of
    an existing one -- and a top-level-only scan would call that absence.

    Only the keys of a ``properties`` mapping are collected. Descriptions are not
    read: this module is about which fields ship, and every one of these schemas
    describes proposals in prose it is right to keep.
    """
    names: list[str] = []
    if isinstance(schema, dict):
        for key, value in schema.items():
            if key == "properties" and isinstance(value, dict):
                names.extend(str(name) for name in value)
                for subschema in value.values():
                    names.extend(_declared_property_names(subschema))
            else:
                names.extend(_declared_property_names(value))
    elif isinstance(schema, list):
        for value in schema:
            names.extend(_declared_property_names(value))
    return names


# -- The prose: ADR-0013's Consequences -------------------------------------


def test_adr_0013_says_the_proposal_age_report_is_owed_rather_than_shipped() -> None:
    """RED means the corrected bullet is gone -- #252 undone, or reworded past it.

    The positive half of the pin. It is not the negative one restated: a rewrite
    that drops the bullet entirely, or that softens it to "proposals can
    accumulate unreviewed" with no statement of what is owed, makes no false
    claim and would pass
    :func:`test_adr_0013_does_not_claim_either_half_of_the_report_already_ships`
    while leaving ADR-0013 silent about a component it is the record for.

    Read after :func:`_collapsed` because the sentence wraps across two lines in
    the file, so the substring exists only once the wrap is flattened.
    """
    text = _collapsed(ADR_0013.read_text(encoding="utf-8"))

    assert OWED_CLAIM in text, (
        "ADR-0013 no longer states that the proposal-age report and the `doctor` "
        "warning are owed rather than shipped"
    )
    rest = text[text.index(OWED_CLAIM) + len(OWED_CLAIM) :]
    rest_of_sentence = _SENTENCE_END.split(rest, maxsplit=1)[0]
    assert OWED_BY in rest_of_sentence, (
        f"the bullet says the report is owed without naming the issue that owes "
        f"it: {OWED_CLAIM}{rest_of_sentence}"
    )


def test_adr_0013_does_not_claim_either_half_of_the_report_already_ships() -> None:
    """RED means the present tense is back -- the sentence #252 removed, or a twin.

    The negative half, and it catches what the positive one cannot: a bullet that
    keeps the owed sentence and asserts the shipped claim somewhere else in the
    file. Both halves of the retracted claim are checked, because they were
    written together and only one of them needs to return for the ADR to describe
    a mechanism that does not exist.

    Scoped to ADR-0013 alone. The governed corpus snapshot of this document still
    carries the old sentence by design -- see the module docstring -- so widening
    this to a tree scan would report that anchor as drift.
    """
    text = _collapsed(ADR_0013.read_text(encoding="utf-8"))

    reports = _ALREADY_REPORTS_PROPOSAL_AGE.findall(text)
    assert not reports, (
        f"ADR-0013 claims `knowledge.status` already reports proposal age: {reports}"
    )

    warns = _DOCTOR_ALREADY_WARNS.findall(text)
    assert not warns, f"ADR-0013 claims `doctor` already warns past a threshold: {warns}"


# -- The fact: what `knowledge.status` publishes ----------------------------


def test_the_property_scan_descends_into_nested_schemas() -> None:
    """RED means the walk was flattened to a top-level read, un-covering nesting.

    The one assertion here driven by synthetic input rather than by the published
    schema, and it exists because the published schema cannot drive it: no name
    nested under ``knowledge.status``'s own ``properties`` matches
    :data:`_PROPOSAL_FIELD` today, and the premise guard in the test below is
    satisfied by the six top-level names alone. The descent is therefore
    load-bearing for what the module docstring claims -- a proposal-named field
    fails *however deeply it is nested* -- while nothing about the real file
    forces it to keep happening. Measured: deleting the two lines that recurse
    into a nested ``properties`` left every other test in this module green, so
    a future simplification to a top-level read would land unopposed.

    The input is a literal rather than a perturbation of the real schema so that
    it goes on driving the descent if ``integrity`` or ``itemsByStatus`` ever
    stops declaring ``properties`` of its own.
    """
    nested = {"properties": {"integrity": {"properties": {"proposalAge": {"type": "integer"}}}}}

    assert "proposalAge" in _declared_property_names(nested), (
        "the property scan no longer descends into nested `properties`, so a "
        "proposal-age field declared inside an object would pass unseen"
    )


def test_the_published_status_response_carries_no_proposal_field() -> None:
    """RED means #414 landed -- and ADR-0013 must stop calling the report owed.

    The fact half of the pin, derived from the live schema rather than from a
    list of the six keys that ship today, so it holds for whatever the response
    grows next.

    ``additionalProperties: false`` is asserted first because it is the premise:
    without it a field absent from ``properties`` could still reach the wire, and
    a scan of the declared names would be pinning a document rather than a
    contract. The ``required`` list is asserted to be non-empty and declared,
    because a walker that silently returned nothing would make the check below
    pass over an empty set and report a safety that is not there.
    """
    schema = json.loads(STATUS_RESPONSE_SCHEMA.read_text(encoding="utf-8"))

    assert schema.get("additionalProperties") is False, (
        "the status response schema accepts unknown fields, so absence from "
        "`properties` no longer says a field is unpublished"
    )
    declared = _declared_property_names(schema)
    required = schema.get("required")
    assert required, "the status response schema requires no field; this check has nothing to read"
    assert set(required) <= set(declared), (
        f"the property scan missed required fields {sorted(set(required) - set(declared))}; "
        f"it is not reading the schema this test claims to read"
    )

    proposal_fields = sorted(name for name in declared if _PROPOSAL_FIELD.search(name))
    assert not proposal_fields, (
        f"`knowledge.status` now publishes {proposal_fields}: ADR-0013's Consequences "
        f"bullet must stop saying the proposal-age report is owed, and "
        f"`OWED_CLAIM` above moves with it"
    )
