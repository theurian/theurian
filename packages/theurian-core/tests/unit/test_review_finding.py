"""The trailer grammar and the finding record (ADR-0029 decisions 1-3).

Pure-domain tests: the grammar, the trust boundary (family/specialist derived,
not parsed), and the trailing-``(#N)`` PR rule, none of which needs a git
repository. The git adapter's scoping and loss-free mapping are exercised against
real repositories in ``tests/integration/test_git_trailer_source.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from theurian.domain.errors import DomainError
from theurian.domain.knowledge import SourceAnchor
from theurian.domain.review_finding import (
    FindingSeverity,
    MalformedTrailerError,
    ReviewerToken,
    ReviewFinding,
    finding_from_trailer,
    parse_trailer_line,
    pull_request_from_subject,
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
        subject="fix: something (#1)",
    )
    assert finding.family is None
    assert finding.specialist is None
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


# --- the trailing (#N) PR rule (decision 1, MEDIUM-2) ----------------------


@pytest.mark.parametrize(
    ("subject", "expected"),
    [
        ("docs: add ADR-0029 (#368)", 368),
        ("fix(security): scan what accept lands (#349) (#363)", 363),  # issue then PR
        ("fix(security): scan what accept lands (#349) (#363) ", 363),  # trailing space
        ("chore: no pr reference here", None),
        ("feat: mentions (#349) mid-subject only", None),  # not trailing -> not the PR
        ("feat: three refs (#1) (#2) (#3)", 3),
    ],
)
def test_pull_request_is_the_trailing_reference(subject: str, expected: int | None) -> None:
    assert pull_request_from_subject(subject) == expected


# --- the record's construction invariants (decision 1) ---------------------


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
