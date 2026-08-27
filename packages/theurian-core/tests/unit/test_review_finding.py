"""The trailer grammar and the finding record (ADR-0029 decisions 1-3).

Pure-domain tests: the grammar and the trust boundary (pull_request/family/
specialist derived, not parsed), none of which needs a git repository. The git
adapter's scoping and loss-free mapping are exercised against real repositories in
``tests/integration/test_git_trailer_source.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

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

    ``_compute_parser_stamp`` hashes exactly five things (see its own docstring):
    the trailer key, the separator, the two closed vocabularies, and the alias
    map. It does **not** cover the parser's mechanics -- the space consumed after
    the key, the ``<reviewer> <SEVERITY>`` token split, and the column-0
    extraction rule that decides which body line reaches the parser at all -- so
    "every element of the grammar" overstates what this test can catch: a change
    to one of those mechanics leaves every case below green while the accepted
    set still changed (recorded gap, production commit ``ebec475``). What this
    test does pin is narrower and still real: if any of the five hashed
    constants stopped feeding the hash, a change to *that* constant would leave
    the stamp unchanged and a superseded store would read as current -- the
    forcing function silently broken for that element.

    Each case swaps one of the five constants for a materially different value
    and asserts the recomputed stamp differs from the live :data:`PARSER_STAMP`.
    Because the stamp
    is derived from the live module globals at call time, this exercises the real
    ``_compute_parser_stamp``: a source that dropped, say, the ``separator=`` line
    from the hashed material would produce the *same* stamp here for a changed
    separator, and the ``separator`` case would fail -- which is exactly the
    regression this pins against.
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
