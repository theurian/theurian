"""The trailer grammar and the finding record (ADR-0029 decisions 1-3).

Pure-domain tests: the grammar and the trust boundary (pull_request/family/
specialist derived, not parsed), none of which needs a git repository. The git
adapter's scoping and loss-free mapping are exercised against real repositories in
``tests/integration/test_git_trailer_source.py``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final, override

import pytest

from theurian.domain import review_finding
from theurian.domain.errors import DomainError
from theurian.domain.knowledge import SourceAnchor
from theurian.domain.review_finding import (
    PARSER_STAMP,
    FindingSeverity,
    MalformedTrailerError,
    ReviewerToken,
    ReviewFinding,
    finding_from_trailer,
    keyed_lines,
    parse_trailer_line,
)

_WHEN = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
_SHA = "0123456789abcdef0123456789abcdef01234567"


def _anchor(commit_sha: str = _SHA) -> SourceAnchor:
    return SourceAnchor(provider="git", source_uri=commit_sha, commit_sha=commit_sha)


# --- AC-2: token validation, refuse-don't-coin -----------------------------


@pytest.mark.parametrize("reviewer", [r.value for r in ReviewerToken])
@pytest.mark.parametrize("severity", [s.value for s in FindingSeverity])
def test_every_valid_token_pair_parses(reviewer: str, severity: str) -> None:
    """The whole closed vocabulary parses; the finding text rides through opaque."""
    line = f"Review-Finding: {reviewer} {severity} — a one-line finding"
    parsed_reviewer, parsed_severity, text = parse_trailer_line(line)
    assert parsed_reviewer == ReviewerToken(reviewer)
    assert parsed_severity == FindingSeverity(severity)
    assert text == "a one-line finding"


@pytest.mark.parametrize(
    "reviewer_token",
    ["reviewer", "code_review", "adversarial-review", "Security", "", "code-review "],
)
def test_a_reviewer_outside_the_three_is_refused(reviewer_token: str) -> None:
    """A first token outside the three reviewers is malformed, never a new value."""
    line = f"Review-Finding: {reviewer_token} HIGH — text"
    with pytest.raises(MalformedTrailerError):
        parse_trailer_line(line)


@pytest.mark.parametrize("severity_token", ["High", "CRITICALS", "SEVERE", "medium", "P0", ""])
def test_a_severity_outside_the_four_is_refused(severity_token: str) -> None:
    """A second token outside the four severities is malformed, never a new value."""
    line = f"Review-Finding: adversarial {severity_token} — text"
    with pytest.raises(MalformedTrailerError):
        parse_trailer_line(line)


def test_the_code_alias_normalises_to_code_review() -> None:
    """The installed base's ``code`` spelling is the code-review reviewer.

    ADR-0029 decision 2 forbids the parser being stricter than the frozen lines it
    must read, and a merged PR (#226) emitted ``Review-Finding: code ...`` for the
    code-review agent. It is normalised to the canonical token -- not coined as a
    new value (decision 3) -- so the loss-free mapping (AC-1) can read it while the
    vocabulary stays closed. A genuinely unknown token is still refused above
    (``code_review`` with an underscore is not ``code``).
    """
    reviewer, severity, text = parse_trailer_line("Review-Finding: code HIGH — a finding")
    assert reviewer is ReviewerToken.CODE_REVIEW
    assert severity is FindingSeverity.HIGH
    assert text == "a finding"


@pytest.mark.parametrize(
    "token",
    [
        "codex",  # shares the 'code' prefix; a prefix/substring match would widen
        "codereview",  # a substring superset; must not match by containment
        "Code",  # a case variant; a case-fold alias would widen
        "CODE",  # a case variant; a case-fold alias would widen
        "code-reviewer",  # a plausible-but-wrong spelling of the reviewer
        " code",  # leading space; the alias is the exact token, not a stripped one
        "code ",  # trailing space; likewise
        "\u0441ode",  # Cyrillic ES (U+0441) for the 'c': a homoglyph, a different token
    ],
)
def test_the_code_alias_does_not_widen_acceptance(token: str) -> None:
    """The ``code`` alias is one exact token, not a family of near-spellings.

    Decision 2 admits the *exact* historical spelling ``code`` as a normalised
    alias of ``code-review``; it does not open a case-insensitive, prefix, or
    substring match. Each token here is one edit away from ``code`` and must still
    be refused -- otherwise the closed vocabulary the trust boundary rests on
    (decision 3) would have widened silently. This is the refuse edge that pins the
    alias next to the accept edge above.
    """
    with pytest.raises(MalformedTrailerError):
        parse_trailer_line(f"Review-Finding: {token} HIGH — text")


def test_a_valid_token_where_a_refused_one_was_parses() -> None:
    """The control: the refusals above are about the token, not the line shape.

    Same line, same separator, same text -- only the token differs -- so a green
    parse here proves the refusal was the vocabulary check firing, not an
    unrelated grammar fault.
    """
    with pytest.raises(MalformedTrailerError):
        parse_trailer_line("Review-Finding: reviewer-x CRITICAL — text")
    reviewer, severity, text = parse_trailer_line("Review-Finding: security CRITICAL — text")
    assert reviewer is ReviewerToken.SECURITY
    assert severity is FindingSeverity.CRITICAL
    assert text == "text"


@pytest.mark.parametrize(
    "line",
    [
        "Signed-off-by: someone",  # not the key at all
        "Review-Finding: security HIGH no separator here",  # no em-dash separator
        "Review-Finding: security HIGH -- text",  # hyphens, not an em dash
        "Review-Finding: security — text",  # only one token before the separator
        "Review-Finding: security HIGH LOW — text",  # three tokens before the separator
        "Review-Finding: security HIGH — ",  # empty finding text
    ],
)
def test_structurally_malformed_lines_are_refused(line: str) -> None:
    with pytest.raises(MalformedTrailerError):
        parse_trailer_line(line)


def test_a_double_space_after_the_key_is_malformed() -> None:
    """Exactly one ASCII space follows the key; a second space is not consumed.

    The parser consumes a single space after the key rather than left-stripping,
    so a doubled space leaves an empty first token and the two-token prefix check
    rejects the line. Pinning single-consume (not ``lstrip``) matters because the
    finding text is byte-preserved: a left-strip that reached past the key is the
    kind of quiet whitespace coercion the loss-free mapping (AC-1) forbids.
    """
    with pytest.raises(MalformedTrailerError):
        parse_trailer_line("Review-Finding:  security HIGH — text")


# --- AC-5: family/specialist are derived, never parsed ---------------------


def test_family_and_specialist_are_never_parsed_from_the_text() -> None:
    """A text literally naming family/specialist still leaves both fields unset.

    The one-line finding is untrusted content (decision 3); the derived labels
    come from classification and the changed-file set in a later slice, never from
    the text. So a text that literally spells them out is preserved verbatim and
    the fields stay ``None``.
    """
    text = "family=A-published-field specialist=theurian-python is the whole finding"
    finding = finding_from_trailer(
        f"Review-Finding: code-review MEDIUM — {text}",
        commit_sha=_SHA,
        committed_at=_WHEN,
    )
    assert finding.family is None
    assert finding.specialist is None
    assert finding.pull_request is None
    assert finding.finding_text == text


# --- byte-preservation of the opaque remainder -----------------------------


def test_an_embedded_separator_is_kept_because_only_the_first_splits() -> None:
    """A finding that itself contains ' — ' keeps it: the split is on the first."""
    _, _, text = parse_trailer_line("Review-Finding: adversarial HIGH — a — b — c")
    assert text == "a — b — c"


def test_finding_text_preserves_trailing_and_internal_bytes() -> None:
    """Loss-free means byte-preservation: references and trailing spaces survive."""
    text = "byte-identical body accepted under a second item id (recorded, #64)  "
    _, _, parsed = parse_trailer_line(f"Review-Finding: adversarial HIGH — {text}")
    assert parsed == text


# --- the record's construction invariants (decision 1) ---------------------


@pytest.mark.parametrize(
    "sha",
    [
        "0123456789abcdef0123456789abcdef01234567",
        "fedcba9876543210fedcba9876543210fedcba98",
    ],
)
def test_the_source_uri_is_the_commit_sha_not_a_constant(sha: str) -> None:
    """FR-S3: the anchor's ``source_uri`` is *this* commit's sha, not a fixed literal.

    Two distinct shas are asserted so a mutation that pinned ``source_uri`` to any
    constant (``"git"``, the empty-guarded key, an unrelated string) fails on at
    least one -- the field is required to vary with the commit, not merely to be
    present.
    """
    finding = finding_from_trailer(
        "Review-Finding: security HIGH — a finding",
        commit_sha=sha,
        committed_at=_WHEN,
    )
    assert finding.anchor.source_uri == sha
    assert finding.anchor.commit_sha == sha


def test_a_finding_carries_provider_git_and_the_commit_sha() -> None:
    """AC-4 at the record level: provider is git and commit_sha is the anchor's."""
    finding = ReviewFinding(
        reviewer=ReviewerToken.SECURITY,
        severity=FindingSeverity.HIGH,
        finding_text="text",
        anchor=_anchor(),
        pull_request=363,
        date=_WHEN,
    )
    assert finding.provider == "git"
    assert finding.commit_sha == _SHA
    assert finding.family is None
    assert finding.specialist is None


def test_a_finding_rejects_a_non_git_provenance() -> None:
    with pytest.raises(DomainError):
        ReviewFinding(
            reviewer=ReviewerToken.SECURITY,
            severity=FindingSeverity.HIGH,
            finding_text="text",
            anchor=SourceAnchor(provider="github", source_uri="x", commit_sha=_SHA),
            pull_request=None,
            date=_WHEN,
        )


def test_a_finding_requires_a_commit_anchor() -> None:
    with pytest.raises(DomainError):
        ReviewFinding(
            reviewer=ReviewerToken.SECURITY,
            severity=FindingSeverity.HIGH,
            finding_text="text",
            anchor=SourceAnchor(provider="git", source_uri="x", commit_sha=None),
            pull_request=None,
            date=_WHEN,
        )


def test_a_finding_rejects_empty_text() -> None:
    with pytest.raises(DomainError):
        ReviewFinding(
            reviewer=ReviewerToken.SECURITY,
            severity=FindingSeverity.HIGH,
            finding_text="",
            anchor=_anchor(),
            pull_request=None,
            date=_WHEN,
        )


def test_a_finding_rejects_a_naive_date() -> None:
    with pytest.raises(DomainError):
        ReviewFinding(
            reviewer=ReviewerToken.SECURITY,
            severity=FindingSeverity.HIGH,
            finding_text="text",
            anchor=_anchor(),
            pull_request=None,
            date=datetime(2026, 8, 26, 12, 0),  # noqa: DTZ001 - the naive date is the fixture
        )


def test_keyed_lines_is_column_zero_only_and_keeps_the_line_index() -> None:
    """#404 R1-6: the extraction rule is column-0 exact -- indented and mid-line excluded.

    A direct driver for :func:`keyed_lines` at its own level, not only through the
    stamp. The stamp's behaviour section would also redden an lstrip-widening (an
    indented trailer becomes a candidate, so a probe verdict moves) -- this pins it
    one layer lower so the extraction rule is held whether or not the stamp reaches
    it. Every ``\\n``-delimited line whose first character starts the key is a
    candidate, at its original message index; an indented line and a line carrying
    the key mid-way are not.
    """
    message = (
        "Review-Finding: security HIGH — a keyed subject\n"  # 0: candidate
        "an ordinary line\n"  # 1: not keyed
        "    Review-Finding: security HIGH — indented\n"  # 2: indented, not column 0
        "text before Review-Finding: security HIGH — mid-line\n"  # 3: mid-line, not column 0
        "Review-Finding: adversarial LOW — at column zero"  # 4: candidate
    )

    extracted = keyed_lines(message)

    assert [index for index, _line in extracted] == [0, 4], (
        "keyed_lines returned a non-column-0 line (an lstrip- or contains-widening), "
        "or lost the original line index"
    )
    assert [line.split(" — ")[1] for _index, line in extracted] == [
        "a keyed subject",
        "at column zero",
    ]


# --- PARSER_STAMP: the forcing function covers every grammar element --------


def test_the_parser_stamp_is_the_live_derivation_not_a_frozen_hash() -> None:
    """PARSER_STAMP is recomputed from the grammar, so it cannot drift silently.

    The stamp is a forcing function (ADR-0029 decision 2): a derived findings store
    records it, and a store built under a superseded grammar reads as stale and is
    rebuilt. That only works if the published constant is the *current* derivation,
    not a value someone froze once. Pinning ``PARSER_STAMP == _compute_parser_stamp()``
    is the anchor the element-sensitivity test below rests on -- together they say
    the stamp both equals the live derivation and moves when any part of it does.
    """
    assert review_finding._compute_parser_stamp() == PARSER_STAMP


def _reviewer_enum_with_extra() -> type[StrEnum]:
    """A reviewer vocabulary with one extra member -- a grammar change to detect."""
    return StrEnum(  # type: ignore[return-value]
        "ExtraReviewerToken",
        {
            "CODE_REVIEW": "code-review",
            "SECURITY": "security",
            "ADVERSARIAL": "adversarial",
            "EXTRA": "an-added-reviewer",
        },
    )


def _severity_enum_with_extra() -> type[StrEnum]:
    """A severity scale with one extra member -- a grammar change to detect."""
    return StrEnum(  # type: ignore[return-value]
        "ExtraFindingSeverity",
        {
            "CRITICAL": "CRITICAL",
            "HIGH": "HIGH",
            "MEDIUM": "MEDIUM",
            "LOW": "LOW",
            "TRIVIAL": "TRIVIAL",
        },
    )


@pytest.mark.parametrize(
    "attribute, replacement",
    [
        pytest.param("TRAILER_KEY", "Review-Note:", id="key"),
        pytest.param("SEPARATOR", " -- ", id="separator"),
        pytest.param("ReviewerToken", None, id="reviewer-vocabulary"),
        pytest.param("FindingSeverity", None, id="severity-scale"),
        pytest.param("_REVIEWER_ALIASES", None, id="alias-map-added"),
        pytest.param("_REVIEWER_ALIASES", {}, id="alias-map-removed"),
    ],
)
def test_the_parser_stamp_changes_when_any_grammar_element_changes(
    monkeypatch: pytest.MonkeyPatch, attribute: str, replacement: object
) -> None:
    """Each of the five hashed vocabulary constants is an input to the stamp.

    This covers the stamp's **vocabulary** section only: the trailer key, the
    separator, the two closed vocabularies, and the alias map. The other two
    sections have their own drivers below --
    :func:`test_the_parser_stamp_moves_when_a_parser_mechanic_widens` for the
    behaviour probes, and
    :func:`test_the_parser_stamp_moves_when_a_vocabulary_gains_a_matching_hook` for
    the matching surface -- because a change to one section leaves the other two
    byte-identical, which is the whole reason there are three.

    Until #406 the vocabulary section was the *only* section, so a widening of the
    parser's mechanics or of a vocabulary's matching behaviour left every case
    below green while the accepted set had changed (recorded gap, production commit
    ``ebec475``). What this case set pins, then and now, is that if any of the five
    hashed constants stopped feeding the hash, a change to *that* constant would
    leave the stamp unchanged and a superseded store would read as current -- the
    forcing function silently broken for that element.

    Each case swaps one of the five constants for a materially different value and
    asserts the recomputed stamp differs from the live :data:`PARSER_STAMP`.
    Because the stamp is derived from the live module globals at call time, this
    exercises the real ``_compute_parser_stamp``: a source that dropped, say, the
    ``separator=`` line from the hashed material would produce the *same* stamp
    here for a changed separator, and the ``separator`` case would fail -- which is
    exactly the regression this pins against.
    """
    if attribute == "ReviewerToken":
        replacement = _reviewer_enum_with_extra()
    elif attribute == "FindingSeverity":
        replacement = _severity_enum_with_extra()
    elif attribute == "_REVIEWER_ALIASES" and replacement is None:
        # A second alias for an existing reviewer: adding a spelling is the recorded
        # grammar change of decision 2, so the stamp must move for it.
        replacement = {**review_finding._REVIEWER_ALIASES, "sec": ReviewerToken.SECURITY}

    monkeypatch.setattr(review_finding, attribute, replacement)

    assert review_finding._compute_parser_stamp() != PARSER_STAMP, (
        f"changing the grammar's {attribute} left PARSER_STAMP unchanged: that "
        f"element no longer feeds the stamp, so a store built under an old grammar "
        f"that differs only in {attribute} would read as current instead of being "
        f"rebuilt (ADR-0029 decision 2, AC-4)."
    )


# --- #406: the mechanics and the matching surface feed the stamp too --------
#
# Each widening below is written as a stand-in for the corresponding source edit:
# it normalises exactly what the widened mechanic would have tolerated and then
# delegates to the real parser, so it changes precisely one answer class and
# nothing else. The equivalent edits made directly in the source of
# `parse_trailer_line` / `keyed_lines` are demonstrated on PR #492; these are the
# committed drivers, so a future change that unbinds a mechanic reddens here
# rather than waiting for someone to re-run a mutation by hand.

_ParseTrailer = Callable[[str], tuple[ReviewerToken, FindingSeverity, str]]


def _tolerating_a_tab_after_the_key(original: _ParseTrailer) -> _ParseTrailer:
    """Widen the one mechanic that consumes a single ASCII space after the key."""

    def parse(line: str) -> tuple[ReviewerToken, FindingSeverity, str]:
        key = review_finding.TRAILER_KEY
        if line.startswith(key + "\t"):
            line = key + " " + line[len(key) + 1 :]
        return original(line)

    return parse


def _tolerating_extra_prefix_tokens(original: _ParseTrailer) -> _ParseTrailer:
    """Widen the `<reviewer> SP <SEVERITY>` arity to "the first two tokens win"."""

    governed_arity = 2  # the `<reviewer> SP <SEVERITY>` count this widening drops

    def parse(line: str) -> tuple[ReviewerToken, FindingSeverity, str]:
        key, separator = review_finding.TRAILER_KEY, review_finding.SEPARATOR
        prefix, found, text = line.removeprefix(key).lstrip(" ").partition(separator)
        tokens = prefix.split(" ")
        if found and len(tokens) > governed_arity:
            line = f"{key} {tokens[0]} {tokens[1]}{separator}{text}"
        return original(line)

    return parse


def _tolerating_a_case_insensitive_alias(original: _ParseTrailer) -> _ParseTrailer:
    """Widen the alias lookup `_reviewer` applies to the first prefix token."""

    def parse(line: str) -> tuple[ReviewerToken, FindingSeverity, str]:
        key = review_finding.TRAILER_KEY
        remainder = line.removeprefix(key).lstrip(" ")
        first, space, rest = remainder.partition(" ")
        if space and first.lower() in review_finding._REVIEWER_ALIASES:
            line = f"{key} {first.lower()} {rest}"
        return original(line)

    return parse


def _tolerating_an_indented_trailer(
    _original: Callable[[str], tuple[tuple[int, str], ...]],
) -> Callable[[str], tuple[tuple[int, str], ...]]:
    """Widen the column-0 extraction rule to accept a leading-whitespace line."""

    def extract(message: str) -> tuple[tuple[int, str], ...]:
        return tuple(
            (position, line.lstrip())
            for position, line in enumerate(message.split("\n"))
            if line.lstrip().startswith(review_finding.TRAILER_KEY)
        )

    return extract


@pytest.mark.parametrize(
    "mechanic, target, widen",
    [
        pytest.param(
            "the single ASCII space consumed after the key",
            "parse_trailer_line",
            _tolerating_a_tab_after_the_key,
            id="space-after-key",
        ),
        pytest.param(
            "the <reviewer> SP <SEVERITY> two-token split",
            "parse_trailer_line",
            _tolerating_extra_prefix_tokens,
            id="two-token-split",
        ),
        pytest.param(
            "the alias lookup applied to the first token",
            "parse_trailer_line",
            _tolerating_a_case_insensitive_alias,
            id="alias-lookup",
        ),
        pytest.param(
            "the column-0 extraction rule",
            "keyed_lines",
            _tolerating_an_indented_trailer,
            id="column-zero-extraction",
        ),
    ],
)
def test_the_parser_stamp_moves_when_a_parser_mechanic_widens(
    monkeypatch: pytest.MonkeyPatch,
    mechanic: str,
    target: str,
    widen: Callable[..., object],
) -> None:
    """#406: each of the four unbound mechanics now feeds the stamp, via the probes.

    These four decide the accepted set as surely as the vocabularies do, and until
    #406 none of them reached the stamp: a store built under the old mechanics read
    as current under the widened ones. The stamp's behaviour section runs
    :data:`_STAMP_PROBES` through :func:`keyed_lines` and
    :func:`parse_trailer_line`, so a widening that changes any probe's answer moves
    it.

    Each case widens exactly one mechanic and asserts two things in order: that the
    widening really did change an answer -- the premise, without which the stamp
    assertion would be vacuous -- and that the stamp moved with it. The premise
    check is what keeps this honest if a probe is ever deleted: a widening nothing
    distinguishes fails here at the first assertion, naming the mechanic that lost
    its probe, rather than silently passing.
    """
    original = getattr(review_finding, target)
    before = review_finding._compute_parser_stamp()
    monkeypatch.setattr(review_finding, target, widen(original))

    widened_answers = [
        review_finding._probe_verdict(line)
        for probe in review_finding._STAMP_PROBES
        for _position, line in review_finding.keyed_lines(probe)
    ]
    monkeypatch.setattr(review_finding, target, original)
    original_answers = [
        review_finding._probe_verdict(line)
        for probe in review_finding._STAMP_PROBES
        for _position, line in review_finding.keyed_lines(probe)
    ]
    assert widened_answers != original_answers, (
        f"widening {mechanic} changed no probe answer, so no probe distinguishes "
        f"it -- the stamp assertion below would pass vacuously. Add a probe for "
        f"this mechanic to _STAMP_PROBES."
    )

    monkeypatch.setattr(review_finding, target, widen(original))
    assert review_finding._compute_parser_stamp() != before, (
        f"widening {mechanic} left PARSER_STAMP unchanged, so a store built under "
        f"the narrower grammar reads as current under the wider one (ADR-0029 AC-4, "
        f"issue #406)."
    )


def test_the_parser_stamp_moves_when_a_vocabulary_gains_a_matching_hook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#406's second face: a widened `_missing_` moves the stamp with no value changed.

    An ``Enum._missing_`` hook widens what ``ReviewerToken(...)`` accepts while
    every member's *value* stays byte-identical -- so the stamp's vocabulary
    section, which serializes those values, cannot see it. The matching section
    can: it records everything this codebase's source put on the class body, taken
    as a difference against a plain ``StrEnum`` so the interpreter's own enum
    machinery cancels.

    Both halves are asserted, because either alone would be misleading: that the
    five hashed *values* really are unchanged (the premise -- otherwise this would
    be re-testing the vocabulary section), and that the stamp moved anyway.
    """

    class WidenedReviewerToken(StrEnum):
        """A reviewer vocabulary that accepts its own members in any case."""

        CODE_REVIEW = "code-review"
        SECURITY = "security"
        ADVERSARIAL = "adversarial"

        @classmethod
        @override
        def _missing_(cls, value: object) -> WidenedReviewerToken | None:
            if isinstance(value, str):
                return cls.__members__.get(value.upper().replace("-", "_"))
            return None

    # The premise: the member values are identical, so the vocabulary section of
    # the material is byte-for-byte what it was.
    assert sorted(t.value for t in WidenedReviewerToken) == sorted(t.value for t in ReviewerToken)
    # ... and the widening is real: the narrow vocabulary refuses what it accepts.
    assert WidenedReviewerToken("CODE-REVIEW") is WidenedReviewerToken.CODE_REVIEW
    with pytest.raises(ValueError, match="CODE-REVIEW"):
        ReviewerToken("CODE-REVIEW")

    monkeypatch.setattr(review_finding, "ReviewerToken", WidenedReviewerToken)

    assert review_finding._compute_parser_stamp() != PARSER_STAMP, (
        "a vocabulary that gained an Enum._missing_ hook left PARSER_STAMP "
        "unchanged: matching behaviour is unbound again, so a store built before "
        "the hook reads as current after it (issue #406's follow-up face)."
    )


def test_the_matching_surface_is_empty_for_a_vocabulary_that_adds_nothing() -> None:
    """The matching section's control: today both vocabularies add nothing at all.

    Without this, the sibling test above could pass for the wrong reason -- a
    surface that reported some constant non-empty noise would also "change" when a
    hook landed. Measured here instead: a plain ``StrEnum`` and both governed
    vocabularies all reduce to the empty tuple, so every entry the stamp ever sees
    in this section is something this codebase's own source put there.
    """

    class Plain(StrEnum):
        """A plain vocabulary, for the control."""

        ONLY = "only"

    assert review_finding._matching_surface(Plain) == ()
    assert review_finding._matching_surface(ReviewerToken) == ()
    assert review_finding._matching_surface(FindingSeverity) == ()


def _probe_verdicts() -> list[str]:
    """Every probe line's verdict, in order -- the stamp's behaviour section."""
    return [
        review_finding._probe_verdict(line)
        for probe in review_finding._STAMP_PROBES
        for _position, line in review_finding.keyed_lines(probe)
    ]


def test_the_matching_section_is_a_load_bearing_input_the_behaviour_section_cannot_replace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#404 R1-6: blinding the matching section moves the stamp, so a test must catch it.

    The sibling ``..._gains_a_matching_hook`` widens *acceptance*, which the
    behaviour probes also catch, so it left the matching section itself unheld:
    ``_matching_surface`` returning ``()`` unconditionally, or the two
    ``*-matching=`` material lines being dropped, both survived the suite green
    (measured). This isolates the section: a vocabulary with an **inert** extra
    member -- one that widens ``vars(cls)`` but changes no acceptance -- moves the
    stamp *only* through the matching surface, because every probe verdict is
    byte-identical before and after.

    So the premise is asserted first (the probe verdicts do not move), and then the
    stamp must. Blinding ``_matching_surface`` to ``()`` or deleting its material
    lines leaves the stamp unmoved under this fixture, which reddens here -- exactly
    the mutations that used to survive.
    """

    class InertlyExtendedReviewerToken(StrEnum):
        """A reviewer vocabulary with an inert helper -- same members, same acceptance."""

        CODE_REVIEW = "code-review"
        SECURITY = "security"
        ADVERSARIAL = "adversarial"

        def _an_inert_helper(self) -> None:
            """Widens vars(cls); never consulted by the parser."""

    # Premise 1: the member values are identical (this is not the vocabulary
    # section moving), and acceptance is unchanged.
    assert sorted(t.value for t in InertlyExtendedReviewerToken) == sorted(
        t.value for t in ReviewerToken
    )
    with pytest.raises(ValueError, match="CODE-REVIEW"):
        InertlyExtendedReviewerToken("CODE-REVIEW")  # still refused, like the real one

    before = _probe_verdicts()
    monkeypatch.setattr(review_finding, "ReviewerToken", InertlyExtendedReviewerToken)

    # Premise 2: the behaviour section is byte-identical, so it cannot be what
    # moves the stamp -- only the matching surface changed.
    assert _probe_verdicts() == before, (
        "the inert member changed a probe verdict, so this fixture no longer "
        "isolates the matching section"
    )
    assert review_finding._matching_surface(InertlyExtendedReviewerToken) != (), (
        "the inert member is not in the matching surface, so nothing to isolate"
    )

    assert review_finding._compute_parser_stamp() != PARSER_STAMP, (
        "an inert change to a vocabulary's class body left PARSER_STAMP unchanged: "
        "the matching section is not a load-bearing input, so a hook the behaviour "
        "probes do not distinguish would go unstamped (#404 R1-6)"
    )


def test_a_base_class_matching_hook_escapes_the_surface_but_the_behaviour_section_catches_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#404 R1-6: the matching surface reads the class's OWN body, so a base hook escapes it.

    ``_matching_surface`` reads ``vars(cls)`` -- the class's *own* body -- so a
    ``_missing_`` (or ``__new__``) placed on a **base class** the vocabulary
    inherits, or injected by a metaclass ``__call__``, is not in the surface at
    all. The section's totality is therefore over the class body, not over every
    way matching can widen. This pins the residual and shows the backstop: such a
    hook that widens *acceptance* is caught by the behaviour section instead, so
    the overall stamp still moves.
    """

    class _CaseFoldingBase(StrEnum):
        @classmethod
        @override
        def _missing_(cls, value: object) -> _CaseFoldingBase | None:
            if isinstance(value, str):
                return cls.__members__.get(value.upper().replace("-", "_"))
            return None

    class BaseHookReviewerToken(_CaseFoldingBase):
        CODE_REVIEW = "code-review"
        SECURITY = "security"
        ADVERSARIAL = "adversarial"

        __doc__ = ReviewerToken.__doc__  # cancel __doc__ so only the base hook differs

    # The residual: the base-class hook is NOT in the subclass's own body ...
    assert "_missing_" not in vars(BaseHookReviewerToken)
    assert review_finding._matching_surface(BaseHookReviewerToken) == (), (
        "the base-class hook leaked into the class-body surface; the residual this "
        "test records no longer holds"
    )
    # ... yet it widens acceptance, which the behaviour probes DO see.
    assert BaseHookReviewerToken("CODE-REVIEW") is BaseHookReviewerToken.CODE_REVIEW

    monkeypatch.setattr(review_finding, "ReviewerToken", BaseHookReviewerToken)

    assert review_finding._compute_parser_stamp() != PARSER_STAMP, (
        "a base-class _missing_ that the matching surface cannot see also went "
        "unseen by the behaviour probes, so the stamp missed a real widening"
    )


#: The exact seeds ``test_projection.py`` / ``test_extractive_summarizer.py``
#: cross-check (ADR-0020), pinned rather than left to the default random seed:
#: two runs under *unpinned* seeds can coincidentally agree, so pinning three
#: known-different seeds is what turns "these two happened to match" into "the
#: stamp is invariant under the iteration order the seed perturbs". ``0``/``1``
#: were once found to tie-break the same way while ``999`` differed.
_HASH_SEEDS: Final = ("0", "1", "999")


def test_the_parser_stamp_is_byte_identical_across_pinned_hash_seeds() -> None:
    """The stamp is a function of the grammar, not of a process (ADR-0029 AC-6).

    A store records this value and a later process compares against it, so a stamp
    that varied per run would mark every store stale at once. Separate interpreters
    under **pinned, different** ``PYTHONHASHSEED`` values, not two calls in this one:
    a same-process comparison cannot see a ``PYTHONHASHSEED``-dependent iteration
    order, and two *unpinned* processes can coincidentally draw seeds that agree --
    which is exactly the way a derived constant most often stops being
    deterministic. If the stamp iterated a ``set`` or ``dict`` whose order the seed
    perturbs, at least one of the three seeds would disagree.

    This is also what the choice of a *behavioural* bind buys over a bytecode or
    ``ast.dump`` digest: the material is Python semantics, so nothing in it can
    drift when the interpreter changes underneath unchanged source.
    """
    read_stamp = "from theurian.domain.review_finding import PARSER_STAMP; print(PARSER_STAMP)"
    results = {
        subprocess.run(  # noqa: S603
            [sys.executable, "-c", read_stamp],
            check=True,
            capture_output=True,
            text=True,
            env={"PYTHONHASHSEED": seed, "PATH": os.environ.get("PATH", "/usr/bin:/bin")},
        ).stdout.strip()
        for seed in _HASH_SEEDS
    }

    assert results == {PARSER_STAMP}, (
        f"PARSER_STAMP varies with PYTHONHASHSEED: {results} -- it iterates a set "
        f"or dict whose order the seed perturbs, so a store would read as stale "
        f"across machines that drew a different seed"
    )
