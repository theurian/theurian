"""Review findings parsed from ``Review-Finding:`` commit trailers (ADR-0029).

A finding is a *pre-classified, human-authored review record*: a reviewer and a
severity drawn from closed vocabularies, plus a one-line summary a human wrote
into a commit. It needs no LLM promotion gate to become structured
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

import hashlib
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
#: 2) -- 55 lines across 7 commits are already frozen in signed history (measured
#: 2026-08-26 on ``origin/main`` @ ``4c4a784``), so a change to it is a breaking
#: change with a migration cost, not a parser convenience.
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
#: (``test_live_origin_main_accounts_for_every_trailer_loss_free``) is what forces
#: the parser's accepted set to stay a superset of the installed base.
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

    Raised rather than coerced: the parser refuses any first token that is neither
    a canonical reviewer ``{code-review, security, adversarial}`` nor a registered
    historical alias (``code`` -> ``code-review``, ADR-0029 Amendment 1's alias
    note), and any second token that is not one of the four severities. Refusing an
    *unknown* token -- rather than coining it -- keeps the governed vocabulary
    closed, which is the property the trust boundary rests on (decision 3); the
    alias set is a deliberate, recorded superset of the frozen installed base, not
    a loosening of that closure.

    Carries the offending line and the reason so a reader can locate it, and a
    remedy naming the grammar. The line is a committed trailer frozen in signed
    history, so the remedy names the shape the parser expects rather than an edit
    to history that cannot be made.
    """

    def __init__(self, line: str, reason: str) -> None:
        self.line = line
        self.reason = reason
        # The reviewer and SEVERITY are SPACE-separated; the single spaced em dash
        # separates SEVERITY from the finding. An earlier remedy put an em dash
        # between reviewer and SEVERITY too -- a shape the parser itself rejects.
        self.remedy = (
            "A Review-Finding trailer is "
            "'Review-Finding: <reviewer> <SEVERITY> — <finding>' with "
            "reviewer one of code-review/security/adversarial, severity one of "
            "CRITICAL/HIGH/MEDIUM/LOW, and a spaced em dash before the finding."
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
    """A ``Review-Finding:`` line, or a whole record, that could not become a finding (D3).

    Captured rather than raised or dropped: the corpus is signed and append-only,
    so history cannot be edited, and a fail-the-whole-load design would let a
    single quoted grammar example in any future commit body permanently brick the
    entire corpus with no forward fix (ADR-0029 Amendment 1, D3). Carries enough to
    locate the failure -- the commit it is on, the raw text verbatim, and why it was
    refused -- without pretending it is a finding.

    Two kinds land here. A **grammar** rejection is a column-0 ``Review-Finding:``
    line whose value failed the grammar; its ``raw_line`` is that trailer line. A
    **metadata** rejection is a whole record whose committer date git emitted
    outside ``datetime``'s range (a crafted year >= 10000): the record cannot carry
    a valid date -- a published, order-bearing field -- so its trailers are skipped
    and the record is accounted once, with ``raw_line`` the offending ``%cI`` value.
    In both cases ``commit_sha`` is git's own ``%H`` (D4), never author-forgeable.
    """

    commit_sha: str
    raw_line: str
    reason: str


@dataclass(frozen=True, slots=True)
class FindingLoad:
    """The total result of a load: accepted findings and rejected keyed lines (D3).

    **The accounting invariant (AC-1, loss-free):** the load never aborts, and no
    record is silently dropped. Every column-0 ``Review-Finding:`` line on a record
    with a valid committer date appears in exactly one of the two tuples; a record
    whose committer date is unrepresentable (a crafted year >= 10000) is accounted
    as a single record-level :class:`RejectedTrailer`, its trailers skipped rather
    than lost. A single malformed line, and a single crafted date, each stay one
    :class:`RejectedTrailer` while every well-formed sibling still loads -- which is
    what makes "loss-free" hold under a corpus that cannot be edited.

    **The population the invariant ranges over is stated, because it was once
    narrower than the sentence above implied** (#410). A "column-0 line" is a
    ``\\n``-delimited line of the **whole commit message** -- subject included --
    whose first character starts the key. It was the ``%b`` *body* until #410: git's
    ``%b`` drops the first paragraph rather than the first line, so a trailer folded
    into an unseparated subject was in neither tuple and the invariant was false for
    it. Two bounds remain, both consequences of "line" meaning ``\\n``-delimited:
    a message separated by lone ``CR`` bytes is a **single** line, so at most its
    first line is a candidate -- if that line is not keyed the message carries no
    finding, and if it *is* keyed the CR-joined remainder (any further trailers, a
    sign-off) is that one finding's opaque byte-preserved text (D2), never further
    findings (#404 R1-4); and a line's *offset* in the message is not part of the
    key, so a keyed subject is a finding.
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


def keyed_lines(message: str) -> tuple[tuple[int, str], ...]:
    """The candidate trailer lines of one commit message, with their line indexes.

    **The D1 extraction rule, and it lives here because it is grammar.** Which
    lines of a message are even *candidates* decides the accepted set exactly as
    much as what :func:`parse_trailer_line` does with one, so it belongs beside the
    rest of the grammar rather than inside the git adapter that reads a message --
    where it was unreachable to :data:`PARSER_STAMP` without inverting the layering
    (#406). It touches no I/O and takes no git type, so nothing about the move
    crosses a boundary the other way.

    A candidate is a ``\\n``-delimited line whose first character starts
    :data:`TRAILER_KEY`: column 0, anywhere in the message. Not git's own
    ``%(trailers)``, which reads only the last paragraph and would drop the ~82% of
    this repository's trailers that sit ahead of the ``Signed-off-by:`` paragraph;
    and not a stripped or folded line, because a trailer value is exactly one
    physical line (D2), so an indented continuation is ordinary message text.

    The index is the line's position in the message, returned rather than
    recomputed so a caller's ordering key and this rule cannot drift apart.
    """
    return tuple(
        (position, line)
        for position, line in enumerate(message.split("\n"))
        if line.startswith(TRAILER_KEY)
    )


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


# --- the parser stamp -------------------------------------------------------
#
# Below the grammar it measures, because it runs it: the probe matrix calls
# `keyed_lines` and `parse_trailer_line`, so it cannot be computed before they
# exist. Everything from here down is import-time and pure.


class _MatchingBaseline(StrEnum):
    """A plain ``StrEnum`` that adds nothing, used to cancel enum machinery.

    :func:`_matching_surface` subtracts this class's attribute set from a governed
    vocabulary's, so whatever ``EnumType`` puts on a ``StrEnum`` -- and *whatever a
    future interpreter puts there instead* -- appears on both sides and cancels.
    What survives the subtraction is exactly what this codebase's own source added.
    That is what makes the surface a total account of the class body rather than an
    enumeration of the hooks someone thought of, and interpreter-independent
    without any claim about a particular CPython's enum internals.

    It carries a docstring on purpose: ``__doc__`` is an attribute like any other,
    and a baseline without one would leave ``__doc__`` uncancelled on every
    governed vocabulary.
    """

    MEMBER = "member"


#: The commit messages the stamp runs the grammar over. Each is chosen for one
#: named mechanic -- the ones :data:`PARSER_STAMP`'s docstring lists -- and the
#: probe's *answer*, not its text, is what enters the hash. Written as messages
#: rather than lines so :func:`keyed_lines`'s extraction rule is inside the probe
#: too, and so the two halves of the grammar are measured as the one function they
#: compose into.
_STAMP_PROBES: Final[tuple[str, ...]] = (
    # Extraction (D1): column 0 anywhere in the message, subject included; an
    # indented line and a line carrying the key mid-way are not candidates.
    "Review-Finding: security HIGH — a keyed subject\n"
    "an ordinary line\n"
    "    Review-Finding: security HIGH — indented\n"
    "text before Review-Finding: security HIGH — mid-line\n"
    "Review-Finding: security HIGH — at column zero",
    # The separator after the key: exactly one ASCII space is consumed, and no
    # other whitespace is (measured line numbers are 1-based within this probe). A
    # TAB-tolerant widening flips line 4; an lstrip-tolerant one flips lines 3 and 4.
    "Review-Finding: code-review HIGH — one space\n"
    "Review-Finding:code-review HIGH — no space\n"
    "Review-Finding:  code-review HIGH — two spaces\n"
    "Review-Finding:\tcode-review HIGH — a tab",
    # The `<reviewer> SP <SEVERITY>` arity: exactly two tokens before the
    # separator. A `split(" ", 1)` or a `>= 2` widening flips line 2 (measured);
    # line 3 is the too-few-tokens control, refused under both and by the grammar.
    "Review-Finding: code-review HIGH — exactly two\n"
    "Review-Finding: code-review HIGH extra — three tokens\n"
    "Review-Finding: code-review — one token",
    # The alias lookup, and the matching behaviour layered on both vocabularies.
    # A case-folding `_missing_` hook, a `__new__` override or a case-insensitive
    # alias lookup flips lines 2 through 5 without moving a single member value.
    "Review-Finding: code HIGH — a registered alias\n"
    "Review-Finding: CODE HIGH — the alias upper-cased\n"
    "Review-Finding: CODE-REVIEW HIGH — a member upper-cased\n"
    "Review-Finding: Security HIGH — a member title-cased\n"
    "Review-Finding: security high — a severity lower-cased",
    # The separator itself, split on the FIRST occurrence; the empty-text refusal;
    # and byte-preservation of the finding text (decision 3). Line 1's text is
    # `a — b — c`, so an `rsplit` (split on the LAST separator) changes it to `c`.
    # Line 5's text carries leading and trailing spaces, so a `.strip()` on the
    # finding text changes it -- the byte-preservation face no other probe covers
    # (#404 R1-6). Line 4 is the empty-text control, refused.
    "Review-Finding: security HIGH — a — b — c\n"
    "Review-Finding: security HIGH - an ASCII hyphen\n"
    "Review-Finding: security HIGH —no spaces around it\n"
    "Review-Finding: security HIGH — \n"
    "Review-Finding: security HIGH —  padded text, trailing kept  ",
)


def _matching_surface(vocabulary: type[StrEnum]) -> tuple[str, ...]:
    """Everything this codebase's source added to a governed vocabulary's class body.

    Answers the face #406's follow-up comment names: matching behaviour layered
    *on* the five hashed vocabularies. An ``Enum._missing_`` hook, a ``__new__``
    override, an ``__init__`` or a ``__str__`` widens or reshapes what
    ``ReviewerToken(...)`` accepts while every member *value* -- and so every
    literal the vocabulary section hashes -- stays byte-identical.

    The account is total **over the class's own body**, not a list of hooks. Every
    entry of ``vars(cls)`` that is not a member is recorded as ``name=provenance``,
    where provenance is the attribute's ``__qualname__`` when it has one and its
    type's name otherwise; the same set is taken over :class:`_MatchingBaseline` and
    subtracted. Machinery appears identically on both sides and cancels --
    ``__new__`` reads ``Enum.__new__`` for a plain ``StrEnum`` and for a widened one
    alike, which is why a user ``__new__`` is caught through the ``_new_member_`` /
    ``__new_member__`` entries ``EnumType`` moves it to (measured), not through
    ``__new__`` -- and an override is caught because its qualname roots in *this*
    class rather than in ``str``, ``Enum`` or ``StrEnum``.

    **``vars(cls)`` is the class's *own* body, so a matching change made outside it
    is not in this surface** (#404 R1-6): a ``_missing_`` or ``__new__`` placed on a
    **base class** the vocabulary inherits, or injected by a metaclass ``__call__``,
    does not appear here. That is not a hole in the stamp, only in *this section*: a
    base-class hook that widens what ``ReviewerToken(...)`` accepts is caught by the
    behaviour section (:data:`_STAMP_PROBES`), because a probe verdict changes. A
    base-class change that altered matching in a way **no probe distinguishes** would
    be missed by both -- the same residual the behaviour section carries, and it owes
    a probe (``test_review_finding.py`` drives both faces:
    ``_the_matching_section_is_a_load_bearing_input...`` isolates this section, and
    ``_a_base_class_matching_hook_escapes_the_surface...`` shows the behaviour
    backstop).

    Two consequences worth stating. Nothing here depends on knowing what a given
    CPython's enum machinery contains, so a Python upgrade that reshapes it moves
    both sides equally and leaves the stamp still. And removing a vocabulary's
    class docstring *does* move the stamp -- ``__doc__`` goes from ``str`` to
    ``NoneType`` -- a conservative false positive, never a false negative.
    """
    baseline = {
        (name, _provenance(value))
        for name, value in vars(_MatchingBaseline).items()
        if name not in _MatchingBaseline.__members__
    }
    surface = {
        (name, _provenance(value))
        for name, value in vars(vocabulary).items()
        if name not in vocabulary.__members__
    }
    return tuple(sorted(f"{name}={provenance}" for name, provenance in surface - baseline))


def _provenance(value: object) -> str:
    """Where an attribute came from: its qualified name, or its type's name."""
    qualname = getattr(value, "__qualname__", None)
    return qualname if isinstance(qualname, str) else type(value).__name__


def _probe_verdict(line: str) -> str:
    """One probe line's answer: the mapping it produces, or that it was refused.

    The *reason* a refusal carries is deliberately not part of the answer. A reason
    is a message to a human; rewording one is prose, and a stamp that moved on
    prose would be noise a reader learns to ignore rather than a signal.
    """
    try:
        reviewer, severity, finding_text = parse_trailer_line(line)
    except MalformedTrailerError:
        return "refused"
    return f"accepted({reviewer.value},{severity.value},{finding_text})"


def _compute_parser_stamp() -> str:
    """The trailer grammar's identity: its vocabularies, its matching, its behaviour.

    A store records this beside its schema version, so a file parsed by a
    superseded grammar is *detectable* from the mismatch (ADR-0029 AC-4). Three
    sections, because three different kinds of change alter what the parser accepts
    and no one of them sees the other two:

    - **Vocabulary** -- the five closed-vocabulary literals decision 2 names: the
      key (:data:`TRAILER_KEY`), the separator (:data:`SEPARATOR`), the two
      vocabularies' member values, and the alias map. Total over those values;
      each has been shown to move the stamp independently by mutation.
    - **Matching** -- :func:`_matching_surface` over each vocabulary: a total
      account of what this codebase's source added to the class body, which is
      where an ``Enum._missing_`` or ``__new__`` widening lives (#406's follow-up
      comment). Every member value can stay byte-identical while that surface
      changes, which is exactly why the vocabulary section cannot see it.
    - **Behaviour** -- the answer the grammar gives to every line of every probe in
      :data:`_STAMP_PROBES`, run through the whole path: :func:`keyed_lines`'s
      column-0 extraction rule and then :func:`parse_trailer_line`'s mechanics --
      the single space consumed after the key, the two-token split, the alias
      lookup, the first-occurrence separator split. Those mechanics were the
      unbound half #406 was filed for.

    **Why behaviour and not source text.** The question the stamp answers is
    "would this parser read the corpus differently from the one that built the
    store", and that is a question about the accepted set, not about characters in
    a file. So a behaviour-preserving refactor correctly leaves the stamp still,
    while any widening that changes an answer moves it. It also makes the stamp
    interpreter-independent by construction: it is Python *semantics*, not a
    representation of Python syntax, so no bytecode digest or ``ast.dump`` shape
    can drift it across a version upgrade and silently mark every store stale.

    **The residual, stated.** The behaviour section is exact for the mechanics its
    probes distinguish, and a widening that no probe separates would leave it
    still -- so a mechanics change owes a probe, and adding one is itself the
    recorded act. The other two sections carry no such residual: both are total
    over their populations. See :data:`FINDINGS_SCHEMA_VERSION`'s own note for
    what stays manual on the *storage* side, which this stamp does not reach.

    Deterministic: the vocabularies, the alias map and the matching surfaces are
    all serialized in sorted order, and the probes are a fixed tuple -- so the
    stamp is a pure function of the grammar and not of dict or set iteration order
    (no ``hash()``, no unordered iteration reaching an output).
    """
    material = "\n".join(
        [
            f"key={TRAILER_KEY}",
            f"separator={SEPARATOR}",
            "reviewers=" + ",".join(sorted(token.value for token in ReviewerToken)),
            "aliases="
            + ",".join(f"{raw}->{token.value}" for raw, token in sorted(_REVIEWER_ALIASES.items())),
            "severities=" + ",".join(sorted(severity.value for severity in FindingSeverity)),
            "reviewer-matching=" + ";".join(_matching_surface(ReviewerToken)),
            "severity-matching=" + ";".join(_matching_surface(FindingSeverity)),
            *(
                f"probe[{probe_index}][{line_index}]={_probe_verdict(line)}"
                for probe_index, probe in enumerate(_STAMP_PROBES)
                for line_index, line in keyed_lines(probe)
            ),
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


#: The current trailer-grammar identity (see :func:`_compute_parser_stamp`). A
#: derived store records this beside its schema version; a stored value that no
#: longer equals this one means the file was parsed by a superseded grammar --
#: *detectable* staleness (ADR-0029, AC-4). Today the store's one writer rebuilds
#: wholesale from git history on every run regardless of this comparison; a reader
#: that trusts a store in place, and rebuilds only on a detected mismatch, is the
#: serving slice this signal is for.
PARSER_STAMP: Final = _compute_parser_stamp()
