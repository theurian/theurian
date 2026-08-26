"""Review findings parsed from ``Review-Finding:`` commit trailers (ADR-0029).

A finding is a *pre-classified, human-authored review record*: a reviewer and a
severity drawn from closed vocabularies, plus a one-line summary a human wrote
into a signed commit. It needs no LLM promotion gate to become structured
(ADR-0029 decision 1, FR-V5), which is what makes it the git-native floor of the
FR-V review-ingestion family.

Three properties are load-bearing and are enforced here rather than left to the
adapter that reads git:

- **Only the two tokens are governed.** ``reviewer`` and ``severity`` are a
  closed vocabulary the parser validates; a token outside it is a malformed
  trailer, never a new value (decision 3). The vocabulary is a *superset* of the
  emitted installed base, not a subset -- decision 2 forbids the parser being
  stricter than the frozen lines it must read, so an emitted alias of a known
  reviewer (``code`` for ``code-review``, see :data:`_REVIEWER_ALIASES`) is
  normalised rather than refused. The narrow enums below are minted
  *beside* the record on purpose -- ``ReviewCommentCategory`` and
  ``ReviewThreadState`` are a different FR-V2 taxonomy whose values do not
  coincide with the trailer's, so reusing one would couple this vocabulary to a
  consumer that drifts independently.
- **The finding text is untrusted content** (decision 3): it is authored commit
  free text, byte-preserved verbatim, and the record types it as opaque. It is
  never parsed for structure -- in particular ``family`` and ``specialist`` are
  *derived* in a later slice, never read out of the text, which is why a text
  literally containing ``family=X`` still leaves that field unset (decision 1).
- **Provenance is a ``SourceAnchor``** (FR-S3): ``provider`` is fixed ``git`` and
  ``commit_sha`` pins the commit the trailer was read from, so the record reaches
  an immutable object rather than a mutable branch.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Final

from theurian.domain.errors import DomainError
from theurian.domain.knowledge import SourceAnchor

#: The trailer key. A body line that does not begin with it is not a finding.
TRAILER_KEY: Final = "Review-Finding:"

#: The separator between the two governed tokens and the free-text finding:
#: SPACE, EM DASH (U+2014), SPACE. Pinned as a wire contract (ADR-0029 decision
#: 2) -- 38 lines are already frozen in signed history, so a change to it is a
#: breaking change with a migration cost, not a parser convenience.
SEPARATOR: Final = " — "


class ReviewerToken(StrEnum):
    """The review agent that authored a finding -- a closed vocabulary (decision 2).

    Minted here rather than reusing a shared enum: no existing closed vocabulary
    carries these three values. These are the *canonical* spellings; the installed
    base also carries the alias ``code`` for :attr:`CODE_REVIEW` (see
    :data:`_REVIEWER_ALIASES`).
    """

    CODE_REVIEW = "code-review"
    SECURITY = "security"
    ADVERSARIAL = "adversarial"


#: Non-canonical reviewer spellings the emitted installed base carries, each naming
#: a reviewer that already exists. ``code`` is the code-review agent, abbreviated in
#: the trailers of one commit (``4c4a784``, merged by PR #379; measured 2026-08-26
#: on ``origin/main``): 9 of its lines read ``Review-Finding: code ...`` -- the
#: ``(#226)`` in that commit's subject is the *issue* it closed, not the PR.
#: ADR-0029 decision 2 forbids the
#: parser being stricter than the lines already frozen in signed history, so an
#: emitted spelling of a *known* reviewer is normalised to its canonical token
#: rather than refused -- it is ``code-review`` written short, not a new value
#: (decision 3), so the closed vocabulary is not widened, only its spelling. A
#: genuinely unknown token is still refused. Growing this map is the deliberate,
#: recorded act decision 2 calls a grammar change: a spelling is added only once it
#: is on public ``main``, and the live loss-free test
#: (``test_live_origin_main_maps_every_trailer_loss_free``) is what forces the
#: parser's accepted set to stay a superset of the installed base.
_REVIEWER_ALIASES: Final[dict[str, ReviewerToken]] = {"code": ReviewerToken.CODE_REVIEW}


class FindingSeverity(StrEnum):
    """A finding's severity -- the four-value scale (decision 2).

    The values are the uppercase tokens the trailer carries, so
    ``FindingSeverity(token)`` validates the wire form directly.
    """

    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class MalformedTrailerError(DomainError):
    """A ``Review-Finding:`` trailer does not satisfy the normative grammar.

    Raised rather than coerced: a first token that is not one of the three
    reviewers, or a second that is not one of the four severities, is a malformed
    trailer and never a new value (ADR-0029 decision 3). Refusing keeps the
    governed vocabulary closed, which is the property the trust boundary rests on.

    Carries the offending line and the reason so a reader can locate it, and a
    remedy naming the grammar. The line is a committed trailer frozen in signed
    history, so the remedy names the shape the parser expects rather than an edit
    to history that cannot be made.
    """

    def __init__(self, line: str, reason: str) -> None:
        self.line = line
        self.reason = reason
        self.remedy = (
            "A Review-Finding trailer is "
            "'Review-Finding: <reviewer> — <SEVERITY> — <finding>' with "
            "reviewer one of code-review/security/adversarial, severity one of "
            "CRITICAL/HIGH/MEDIUM/LOW, and the separator a spaced em dash."
        )
        super().__init__(f"Malformed Review-Finding trailer ({reason}): {line!r}")


@dataclass(frozen=True, slots=True)
class ReviewFinding:
    """A canonical review finding parsed from one commit trailer (decision 1).

    The mapping from trailer to record is total: every element the grammar
    carries maps to a named field, and the three fields the trailer does *not*
    carry -- ``pull_request``, ``family`` and ``specialist`` -- are derived in a
    later slice and left unset here rather than guessed. ``family`` and
    ``specialist`` would be guessed from the untrusted text; ``pull_request``
    would be guessed from the subject, which is unreliable (see the field below).
    """

    reviewer: ReviewerToken
    severity: FindingSeverity
    #: Untrusted authored content (decision 3), byte-preserved from the trailer.
    finding_text: str
    #: Provenance (FR-S3): ``provider == "git"`` and a non-null ``commit_sha``.
    anchor: SourceAnchor
    #: Derived, and ``None`` in this parse-only slice (ADR-0029 Amendment 1, D5).
    #: The trailing ``(#N)`` on a squash-merge subject is *not* reliably the PR: a
    #: subject often ends in the *issue* it closed. Measured 2026-08-26 on
    #: ``4c4a784``, resolving each trailer commit's trailing token against the
    #: GitHub API, **27 of 55 live findings (49.1%) would publish an issue number
    #: as the PR** -- ``4c4a784``'s ``(#226)`` is issue #226 (real PR #379) and
    #: ``ae2aea7``'s ``(#368)`` is issue #368 (real PR #382). The correct PR needs
    #: the GitHub merge API, which AC-3's no-network property excludes from this
    #: slice, so the derivation is left to the FR-V serving arm that has that
    #: context. This measurement is recorded here so no later slice re-derives the
    #: PR from the subject; the subject heuristic was deleted, not left dormant.
    pull_request: int | None
    #: The commit date, timezone-aware so a validity window is never silently
    #: shifted by a missing offset (the DTZ discipline this codebase enforces).
    date: datetime
    #: Derived by classification in a later slice (decision 4); never parsed from
    #: ``finding_text``. Unset in the parse-only slice.
    family: str | None = field(default=None)
    #: Derived from the fix commit's changed-file set in a later slice; never
    #: parsed from ``finding_text``. Unset in the parse-only slice.
    specialist: str | None = field(default=None)

    def __post_init__(self) -> None:
        if self.anchor.provider != "git":
            raise DomainError(f"ReviewFinding provenance must be git, got {self.anchor.provider!r}")
        if self.anchor.commit_sha is None:
            raise DomainError("ReviewFinding must anchor to a commit, but commit_sha is missing")
        if not self.finding_text:
            raise DomainError("ReviewFinding.finding_text must not be empty")
        if self.date.tzinfo is None:
            raise DomainError("ReviewFinding.date must be timezone-aware")

    @property
    def provider(self) -> str:
        """The fixed ``git`` provider, read through the anchor (decision 1)."""
        return self.anchor.provider

    @property
    def commit_sha(self) -> str:
        """The commit the trailer was parsed from (decision 1)."""
        sha = self.anchor.commit_sha
        if sha is None:  # pragma: no cover - forbidden by __post_init__
            raise DomainError("ReviewFinding anchor lost its commit sha")
        return sha


@dataclass(frozen=True, slots=True)
class RejectedTrailer:
    """A column-0 ``Review-Finding:`` line whose value failed the grammar (D3).

    Captured rather than raised or dropped: the corpus is signed and append-only,
    so history cannot be edited, and a fail-the-whole-load design would let a
    single quoted grammar example in any future commit body permanently brick the
    entire corpus with no forward fix (ADR-0029 Amendment 1, D3). Carries enough to
    locate the line -- the commit it is on, the raw line verbatim, and why it was
    refused -- without pretending it is a finding.
    """

    commit_sha: str
    raw_line: str
    reason: str


@dataclass(frozen=True, slots=True)
class FindingLoad:
    """The total result of a load: accepted findings and rejected keyed lines (D3).

    **The accounting invariant (AC-1, loss-free):** every column-0
    ``Review-Finding:`` line in the read history appears in exactly one of the two
    tuples. A line is never silently dropped, and a single malformed line never
    aborts the batch -- it is accounted as :class:`RejectedTrailer` while every
    well-formed sibling still loads. This is what makes "loss-free" hold under a
    corpus that cannot be edited.
    """

    accepted: tuple[ReviewFinding, ...]
    rejected: tuple[RejectedTrailer, ...]


def _reviewer(token: str, *, line: str) -> ReviewerToken:
    canonical = _REVIEWER_ALIASES.get(token)
    if canonical is not None:
        return canonical
    try:
        return ReviewerToken(token)
    except ValueError as exc:
        expected = ", ".join(r.value for r in ReviewerToken)
        raise MalformedTrailerError(
            line, f"unknown reviewer {token!r}; expected one of {expected}"
        ) from exc


def _severity(token: str, *, line: str) -> FindingSeverity:
    try:
        return FindingSeverity(token)
    except ValueError as exc:
        expected = ", ".join(s.value for s in FindingSeverity)
        raise MalformedTrailerError(
            line, f"unknown severity {token!r}; expected one of {expected}"
        ) from exc


def parse_trailer_line(line: str) -> tuple[ReviewerToken, FindingSeverity, str]:
    """Split one trailer line into its governed tokens and its opaque text.

    A trailer value is exactly one physical line (ADR-0029 Amendment 1, D2): there
    is no folding and no continuation. The caller passes a single line, and the
    remainder after the *first* separator is the finding text, returned
    byte-for-byte: a finding that itself contains a spaced em dash keeps it,
    because the split is on the first occurrence only (decision 2, the finding is
    opaque free text to end of line).

    Raises:
        MalformedTrailerError: if the key is absent, the separator is missing, the
            prefix is not exactly two space-separated tokens, either token is
            outside its closed vocabulary, or the finding text is empty.
    """
    if not line.startswith(TRAILER_KEY):
        raise MalformedTrailerError(line, "does not begin with the Review-Finding key")
    remainder = line[len(TRAILER_KEY) :]
    # Exactly one ASCII space follows the key by the grammar; consume it without
    # stripping, so nothing in the finding text is lost.
    if remainder.startswith(" "):
        remainder = remainder[1:]

    prefix, sep, finding_text = remainder.partition(SEPARATOR)
    if not sep:
        raise MalformedTrailerError(line, "missing the ' — ' separator (space, em dash, space)")
    if not finding_text:
        raise MalformedTrailerError(line, "carries no finding text after the separator")

    tokens = prefix.split(" ")
    if len(tokens) != 2:  # noqa: PLR2004 - the grammar is <reviewer> SP <SEVERITY>
        raise MalformedTrailerError(
            line, f"expected '<reviewer> <SEVERITY>' before the separator, got {prefix!r}"
        )
    return _reviewer(tokens[0], line=line), _severity(tokens[1], line=line), finding_text


def finding_from_trailer(line: str, *, commit_sha: str, committed_at: datetime) -> ReviewFinding:
    """Assemble a :class:`ReviewFinding` from a trailer line and its commit context.

    Pure: the git I/O that produces ``commit_sha`` and ``committed_at`` lives in
    the infrastructure adapter, so the whole grammar is unit-testable without a
    repository. ``pull_request``, ``family`` and ``specialist`` are left unset --
    they are derived in a later slice, never read from ``line`` (decision 1) and,
    for ``pull_request``, never guessed from the subject (D5): the subject is not
    passed in, so no code path can re-introduce the deleted heuristic.
    """
    reviewer, severity, finding_text = parse_trailer_line(line)
    return ReviewFinding(
        reviewer=reviewer,
        severity=severity,
        finding_text=finding_text,
        anchor=SourceAnchor(provider="git", source_uri=commit_sha, commit_sha=commit_sha),
        pull_request=None,
        date=committed_at,
    )
