"""ADR-0037 decision 7's sidecar rule: the extension it derives, and where that name may go.

The Compliance section promises the first walk by name: *the sidecar extension
rule, walked over the media types it ranges on*. What it protects is a filename.
A non-markdown body is written beside its concept document, and the extension it
gets is a total function of ``contentType`` alone -- so a structured body spelled
``.txt`` is a bundle member a consumer's tooling will not parse, and no exporter
exists yet to notice. S2 builds that exporter from the ADR's prose, which makes
the prose the thing under test.

Bidirectional where a live half exists, as in ``test_adr_0037_claims.py``: the
fact half reddens when ``domain/values.py::_STRUCTURED_MEDIA_TYPES`` moves under
the rule, the prose half when the ADR stops stating the rule the fact half
reimplements. The three arms are copied here rather than imported because the
code they will live in is S2's; a pin over a copy is worth nothing unless the
original is pinned too.

The last two pins have **no live half and say so**: they bound bytes no code
writes until S2, whose bytes battery the Compliance section assigns them to. A
prose pin standing alone earns its place exactly when the sentence it holds was
refuted once and could be restored in silence -- which is what round 3's closure
verification measured of both.

Held here:

- **The mapping**, driven over every member of the live constant against the
  extension the ADR records for it -- the two ``vnd.*`` API-description formats
  included, whose ``.txt`` is *by design*.
- **An oracle independent of the arms**, so a member added with a recorded row
  matching whatever the arms already do cannot pass.
- **The five-of-seven measurement** the criterion paragraph corrects itself
  with, and the ``text/x-yaml`` selection criterion as corrected (round 3's code
  review: the falsified draft said *the one member matching neither* ``endswith``
  *arm*, and five of the seven match neither).
- **What the refusing rule the alternatives table rejects would have cost**,
  measured through ``body_extension`` itself.
- **The concept body's bound** (decision 2's family 3) and **the link-text
  exclusion** (decision 7's sidecar bullet) -- the two sentences that say where
  the derived filename appears, and what may never stand in for it.

Pure: one Markdown file read, and shipped domain values built in memory. No
database, no socket, no temporary directory.
"""

from __future__ import annotations

import re
from typing import Final

import pytest
from adr_0037_support import assert_the_adr_states

from theurian.domain.errors import InvariantViolationError
from theurian.domain.proposal import body_extension
from theurian.domain.values import _STRUCTURED_MEDIA_TYPES, PLAIN_TEXT, MediaType

pytestmark = pytest.mark.unit


#: The extension decision 7 records for each member of
#: ``_STRUCTURED_MEDIA_TYPES``. The two ``vnd.*`` API-description formats take
#: ``.txt`` **by design**, in the ADR's own words: the same document is written
#: as JSON or as YAML, so there is no correct structured extension for them.
RECORDED_SIDECAR_EXTENSIONS: Final[dict[str, str]] = {
    "application/json": ".json",
    "application/schema+json": ".json",
    "application/vnd.oai.openapi+json": ".json",
    "application/yaml": ".yaml",
    "text/x-yaml": ".yaml",
    "application/vnd.aai.asyncapi": ".txt",
    "application/vnd.oai.openapi": ".txt",
}

#: The exact spellings decision 7's ``.yaml`` arm names beside its suffix rule.
YAML_SPELLINGS: Final[frozenset[str]] = frozenset({"application/yaml", "text/x-yaml"})

#: The members ``MediaType.is_structured``'s two ``endswith`` arms cannot see,
#: which is the population the criterion paragraph measures **five of seven** on.
#: Not the same five as :data:`REFUSED_BY_BODY_EXTENSION` below.
MATCHING_NEITHER_ENDSWITH_ARM: Final[frozenset[str]] = frozenset(
    {
        "application/json",
        "application/vnd.aai.asyncapi",
        "application/vnd.oai.openapi",
        "application/yaml",
        "text/x-yaml",
    }
)

#: The structured types ``body_extension`` has no mapping for, which is the
#: **five of seven** the alternatives table prices the rejected refusing rule at.
REFUSED_BY_BODY_EXTENSION: Final[frozenset[str]] = frozenset(
    {
        "application/schema+json",
        "application/vnd.aai.asyncapi",
        "application/vnd.oai.openapi",
        "application/vnd.oai.openapi+json",
        "text/x-yaml",
    }
)


def _sidecar_extension(media_type: str, *, yaml_spellings: frozenset[str] = YAML_SPELLINGS) -> str:
    """Decision 7's three arms, reimplemented from the rule the ADR writes.

    Reimplemented rather than imported: no exporter exists yet -- S2 owes it --
    so what this pin holds is the *rule* against the media types the tree
    actually carries. ``yaml_spellings`` is a parameter so the criterion's own
    counterfactual, the rule with ``text/x-yaml`` unnamed, is these same three
    arms minus one spelling rather than a second implementation of them.
    """
    if media_type == "application/json" or media_type.endswith("+json"):
        return ".json"
    if media_type in yaml_spellings or media_type.endswith("+yaml"):
        return ".yaml"
    return ".txt"


def _serialization_extension(media_type: str) -> str:
    """The extension the subtype's own tokens name, or ``.txt`` when they name none.

    An oracle independent of the three arms: it splits the subtype on ``+``,
    ``.`` and ``-``, so ``application/x-yaml`` and ``text/yaml`` name YAML
    however they are punctuated, while ``application/vnd.oai.openapi`` names no
    serialization at all. Decision 7's rule is right exactly where the two agree,
    and a member the arms spell as text while its own subtype says JSON or YAML
    is the drift the criterion sentence exists to catch.
    """
    tokens = set(re.split(r"[+.\-]", media_type.partition("/")[2]))
    if "json" in tokens:
        return ".json"
    if "yaml" in tokens:
        return ".yaml"
    return ".txt"


def _body_extension_refuses(media_type: str) -> bool:
    try:
        body_extension(MediaType(media_type))
    except InvariantViolationError:
        return True
    return False


def test_every_structured_media_type_takes_the_sidecar_extension_the_adr_records() -> None:
    """Decision 7's rule is total over the media types it ranges on, and S2 builds to it.

    The Compliance section promises this walk by name, and what it protects is a
    filename: a structured body spelled ``.txt`` is a bundle member a consumer's
    tooling will not parse, and one spelled ``.json`` when it is YAML is worse.
    The rule is prose in an ADR with no code behind it until S2, so nothing else
    in the tree notices a member arriving that the arms get wrong.

    Driven over **every** member of the live constant rather than over a chosen
    few: the expected answers are recorded from the ADR, so a member added to
    ``_STRUCTURED_MEDIA_TYPES`` has no recorded extension and reddens here,
    which is the coordinated change the criterion paragraph asks for. The two
    ``.txt`` rows are load-bearing in the other direction -- they are the ADR's
    *by design*, not a gap, so a later "fix" sending
    ``application/vnd.oai.openapi`` to ``.json`` has to move the paragraph that
    says why it does not.
    """
    measured = {
        media_type: _sidecar_extension(media_type) for media_type in _STRUCTURED_MEDIA_TYPES
    }

    assert measured == RECORDED_SIDECAR_EXTENSIONS, (
        f"decision 7's three arms send `_STRUCTURED_MEDIA_TYPES` to {measured}, against "
        f"the {RECORDED_SIDECAR_EXTENSIONS} ADR-0037 records. A member here with no row "
        f"there is a structured type whose sidecar extension no governed record decides; "
        f"a row whose extension moved is the ADR describing a rule the arms do not "
        f"implement, and S2 builds the exporter from the ADR."
    )


def test_no_structured_media_type_is_spelled_as_text_while_its_subtype_names_a_format() -> None:
    """The drift the recorded table alone cannot see: a member added *with* a matching row.

    ``test_every_structured_media_type_takes_the_sidecar_extension_the_adr_records``
    compares the arms against extensions recorded by hand, so an editor who adds
    ``application/x-yaml`` to the constant **and** records ``.txt`` beside it --
    the arms' own answer -- leaves both green while a YAML body ships spelled as
    text. This is the independent oracle: the media type's own subtype tokens.

    The three drift spellings are asserted to disagree *first*, because an oracle
    that returned the arms' answer would make the emptiness below vacuous. They
    are outside ``_STRUCTURED_MEDIA_TYPES`` on purpose -- a positive control has
    to be a case the rule gets wrong, and every member of the live set is a case
    it gets right.
    """
    drift = ("application/x-yaml", "text/yaml", "application/x-json")

    for candidate in drift:
        assert _sidecar_extension(candidate) != _serialization_extension(candidate), (
            f"{candidate!r} is a spelling decision 7's arms send to "
            f"{_sidecar_extension(candidate)!r} while its subtype names "
            f"{_serialization_extension(candidate)!r}. This oracle has to see that "
            f"disagreement or the assertion below holds against anything."
        )

    disagreeing = {
        media_type: (_sidecar_extension(media_type), _serialization_extension(media_type))
        for media_type in _STRUCTURED_MEDIA_TYPES
        if _sidecar_extension(media_type) != _serialization_extension(media_type)
    }

    assert not disagreeing, (
        f"{disagreeing} -- each reads (what the arms give, what the subtype names). "
        f"ADR-0037's `.txt` rows are for types naming no serialization at all; a member "
        f"whose own subtype says JSON or YAML and lands `.txt` is the drift the "
        f"criterion sentence is written to catch, and it needs the arm the ADR gives "
        f"`text/x-yaml` rather than a row in the recorded table."
    )


def test_five_of_the_seven_structured_types_match_neither_endswith_arm() -> None:
    """The measurement the criterion paragraph corrects itself with, held both ways.

    An earlier draft selected ``text/x-yaml`` as "the one member matching neither
    ``endswith`` arm", and round 3's code review measured five. The corrected
    paragraph records the number, the five names and the reason two of them take
    ``.txt``, so all three move together or none of them is true.

    The count and the set are separate assertions because they fail apart: a
    member ending ``+json`` added to the constant leaves this five untouched and
    makes it five of *eight*, which is the arithmetic the sentence states. The
    three probes come first because the five is a property of
    ``MediaType.is_structured``'s suffix arm -- reused by decision 7, per the
    ADR -- and a suffix pair that moved in the source would otherwise be
    restated here rather than measured.
    """
    assert MediaType("application/example+json").is_structured, (
        "`is_structured` no longer admits an unlisted `+json` type, so the suffix arm "
        "below is not the one ADR-0037 says decision 7 reuses."
    )
    assert MediaType("application/example+yaml").is_structured, (
        "`is_structured` no longer admits an unlisted `+yaml` type; see above."
    )
    assert not MediaType("application/example+xml").is_structured, (
        "`is_structured` admits a third suffix, so `the two endswith arms` is no longer "
        "what the ADR says it is and the population below is measured with the wrong rule."
    )

    unseen = {
        media_type
        for media_type in _STRUCTURED_MEDIA_TYPES
        if not media_type.endswith(("+json", "+yaml"))
    }

    assert len(_STRUCTURED_MEDIA_TYPES) == 7, (
        f"`_STRUCTURED_MEDIA_TYPES` has {len(_STRUCTURED_MEDIA_TYPES)} members. ADR-0037 "
        f"writes `five of the seven` in decision 7's criterion paragraph and `has seven "
        f"members` in the Compliance bullet that promises this pin, so the denominator "
        f"moving falsifies both while the numerator below can stay green."
    )
    assert unseen == MATCHING_NEITHER_ENDSWITH_ARM, (
        f"the members matching neither `endswith` arm are {sorted(unseen)}, against the "
        f"{sorted(MATCHING_NEITHER_ENDSWITH_ARM)} ADR-0037 names. The paragraph lists "
        f"them individually, so a change here is a change to a sentence and not only to "
        f"a count."
    )


def test_only_text_x_yaml_needs_a_spelling_the_other_two_arms_never_reach() -> None:
    """The selection criterion as corrected: unique, but not by the count.

    ``text/x-yaml`` is named in the rule because dropping it changes an answer,
    and the corrected paragraph says exactly that -- *a rule without it spells a
    YAML body as text*. The falsified draft claimed uniqueness over "matches
    neither ``endswith`` arm", which four other members also satisfy, so this
    drives the property the rule actually rests on: run the same three arms with
    the spelling withdrawn and see which members move.

    Measured against the full rule rather than the recorded table, so this stays
    a statement about the arms alone: the table's own correctness is
    ``test_every_structured_media_type_takes_the_sidecar_extension_the_adr_records``.
    """
    withdrawn = frozenset({"application/yaml"})
    moved = {
        media_type
        for media_type in _STRUCTURED_MEDIA_TYPES
        if _sidecar_extension(media_type, yaml_spellings=withdrawn)
        != _sidecar_extension(media_type)
    }

    assert moved == {"text/x-yaml"}, (
        f"withdrawing `text/x-yaml` from the rule moves {sorted(moved)}. ADR-0037 names "
        f"it explicitly because it is the only member no other arm reaches with the "
        f"right extension: an empty set here means the naming is redundant and the "
        f"paragraph is wrong, and a second member means another spelling has become "
        f"load-bearing without the rule naming it."
    )


def test_the_rejected_refusing_rule_would_have_refused_five_of_the_seven_and_text_plain() -> None:
    """The alternatives table's measurement, driven through the refusal itself.

    ``body_extension`` is the shipped function the rejected first draft would
    have taken the sidecar extension from, and the row rejecting it is priced on
    what it refuses: five of the seven structured types, ``text/plain`` with
    them, and ``application/vnd.oai.openapi`` -- the rule's own motivating
    example -- among the five. That price is the whole argument for the accepted
    rule being total, and it is a measurement of a live mapping, so a media type
    added to ``_EXTENSIONS`` makes the recorded sentence false.

    **These five are not the criterion paragraph's five.** Both count five of
    seven and they share only three members: the criterion's population is the
    ``endswith``-invisible one, this one is whatever ``_EXTENSIONS`` omits. The
    two constants stay separate for that reason.
    """
    refusing = {
        media_type for media_type in _STRUCTURED_MEDIA_TYPES if _body_extension_refuses(media_type)
    }

    assert refusing == REFUSED_BY_BODY_EXTENSION, (
        f"`body_extension` refuses {sorted(refusing)} of `_STRUCTURED_MEDIA_TYPES`, "
        f"against the {sorted(REFUSED_BY_BODY_EXTENSION)} ADR-0037's alternatives table "
        f"prices the rejected rule at. `five of the seven structured types ... would "
        f"refuse` is that row's entire reason for rejecting it."
    )
    assert _body_extension_refuses(PLAIN_TEXT.value), (
        "`body_extension` now maps `text/plain`, and the alternatives row counts it "
        "beside the five as part of what the refusing rule would have cost."
    )


# ---------------------------------------------------------------------------
# Where the derived filename may appear: decision 2's family 3, and decision
# 7's sidecar bullet. Prose-only, for the reason the module docstring gives.
# ---------------------------------------------------------------------------


def test_the_adr_still_bounds_the_concept_body_by_the_content_type_split() -> None:
    """Family 3's bound, in the two halves a restoration of the refuted row would drop.

    Decision 2 closes its every-byte invariant by enumeration, so each family's
    *bound* cell is the closure for that family's bytes -- and family 3's bytes
    are a concept document's body, which is either the canonical body or a link
    to a sidecar. Round 3 refuted the row that read *The canonical body, plus the
    generated* ``## Relations`` *section*: it names no split, so the sidecar-link
    paragraph is bytes no family bounds, and a body that is not markdown has its
    filename emitted by a rule the closure does not cover. Round 3's closure
    verification restored that wording and measured the suite green; this is the
    pin that reddens on it.

    Two fragments, because they fail for different reasons. The first is the
    split itself -- without it the row describes only the markdown case, which is
    exactly the refuted shape. The second is the derivation bound, whose *and not
    a second one* is what bars the sidecar link from being spelled from anything
    but the two served inputs; the round-2 regression reached for the canonical
    body file's author-written suffix as that second derivation.
    """
    assert_the_adr_states(
        "When `contentType` is `text/markdown`, the canonical body; otherwise the "
        "generated sidecar-link paragraph",
        because=(
            "It is the split that puts the sidecar-link paragraph inside a family's "
            "bound at all. The refuted wording named only the canonical body, leaving "
            "the bytes of every non-markdown row's concept document unclosed while "
            "decision 2's enumeration still claimed to cover every byte."
        ),
    )
    assert_the_adr_states(
        "whose link text *and* target are both the sidecar's own derived filename — "
        "`theurian_body_file`'s derivation and not a second one, the stem from the item "
        "id and the extension from `contentType`, both served (decision 7)",
        because=(
            "`and not a second one` is the clause that keeps the link inside the "
            "disclosure bound: one derivation, from two served inputs. A row that "
            "bounds the paragraph without it admits a second rule for the same name, "
            "which is how an author-written suffix reached a bundle path in round 2."
        ),
    )


def test_the_adr_still_bars_the_author_written_name_from_the_sidecar_link_text() -> None:
    """The exclusion half of decision 7's link sentence, which is the half that was lost.

    The positive claim -- the link text is the derived filename -- reads as
    complete on its own, which is why deleting the clause after it costs nothing
    a reader notices. What it costs is the refusal: without *never the canonical
    body file's author-written name* the sentence no longer rules out the one
    substitution the ADR's alternatives table rejects in three ways, and the
    author-written name is a string no served payload publishes. Round 3's
    closure verification deleted that clause and measured the suite green.

    Pinned as one fragment spanning both halves, so the exclusion cannot be
    dropped while the claim it qualifies stays quoted: the positive half alone
    is what the falsified draft already said.
    """
    assert_the_adr_states(
        "**its link text is that same derived filename**, never the canonical body "
        "file's author-written name",
        because=(
            "The derived filename is served end to end; the canonical body file's own "
            "name is not, and `theurian_body_file` carried it into a bundle path for a "
            "round. This clause is where decision 7 refuses that substitution in the "
            "link text, which every reader of the bundle sees."
        ),
    )


def test_the_adr_still_states_the_three_arms_and_the_corrected_selection_criterion() -> None:
    """The prose half: the rule this module reimplements, and the measurement on it.

    The arms are pinned because ``_sidecar_extension`` above is a copy of them --
    a rule rewritten in the ADR would leave every fact assertion here green
    against a derivation no governed record states. The criterion sentences are
    pinned in two pieces: the first is the reason ``text/x-yaml`` is in the rule
    at all, and the second is the correction itself. Losing the second is how the
    falsified "one member matching neither `endswith` arm" comes back, since it
    reads as the more natural sentence of the two.
    """
    assert_the_adr_states(
        "- `application/json`, or anything ending `+json` → **`.json`**\n"
        "- `application/yaml`, `text/x-yaml`, or anything ending `+yaml` → **`.yaml`**\n"
        "- everything else → **`.txt`**",
        because=(
            "These three arms are what this module reimplements to walk the media types. "
            "A rule stated differently in the ADR makes every extension asserted here a "
            "measurement of a derivation no record holds."
        ),
    )
    assert_the_adr_states(
        "`text/x-yaml` is named explicitly because it is the one member of "
        "`domain/values.py::_STRUCTURED_MEDIA_TYPES` no other arm reaches with the right "
        "extension: the suffix rule cannot see it — `x-yaml` is a hyphen, not a `+yaml` "
        "suffix — and the exact spellings beside it carry `application/json` and "
        "`application/yaml` only, so a rule without it spells a YAML body as text.",
        because=(
            "This is the corrected selection criterion, and it is the sentence "
            "`test_only_text_x_yaml_needs_a_spelling_the_other_two_arms_never_reach` "
            "measures. The uniqueness is over *the extension the other arms reach*, "
            "never over matching an `endswith` arm."
        ),
    )
    assert_the_adr_states(
        '**"The one member matching neither `endswith` arm" is not that criterion, and an '
        "earlier draft of this sentence said it was.** Measured 2026-09-24: **five of the "
        "seven** match neither — `application/json`, `application/vnd.aai.asyncapi`, "
        "`application/vnd.oai.openapi`, `application/yaml` and `text/x-yaml` — and two of "
        "those five take `.txt` **by design**.",
        because=(
            "Round 3's code review found the earlier criterion false against this "
            "constant. The retraction, the five names and the `by design` clause are "
            "what stop it being rewritten back; the fact half of that measurement is "
            "`test_five_of_the_seven_structured_types_match_neither_endswith_arm`."
        ),
    )


def test_the_adr_still_states_what_the_rejected_refusing_rule_would_have_cost() -> None:
    """The prose half of the alternatives row: a rejection priced by measurement.

    The row is the ADR's answer to "why is the extension rule total?", and its
    answer is a number rather than a preference. A rewrite that keeps the
    rejection and drops the measurement leaves an S2 implementer free to read
    the refusal back in as a safety feature, which is the shape the first draft
    shipped.
    """
    assert_the_adr_states(
        "Measured 2026-09-24: `_EXTENSIONS` holds three media types against "
        "`_STRUCTURED_MEDIA_TYPES`' seven-plus, so **five of the seven** structured types "
        "and `text/plain` would refuse — including `application/vnd.oai.openapi`, the "
        "rule's own motivating example.",
        because=(
            "It is the price that makes `a gate-cleared row must never be refused` a "
            "measurement rather than a slogan, and "
            "`test_the_rejected_refusing_rule_would_have_refused_five_of_the_seven_and_text_plain` "
            "is the live half of it."
        ),
    )
