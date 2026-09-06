"""How a review-ingestion run refuses, in a shape a caller can publish (ADR-0030).

Ingestion is the optional capability: ``requirements-analysis.md`` records
``Degraded`` as "a success-with-warnings terminal state, not a failure: a missing
``gh`` token must not prevent local knowledge from working". So every way this
arm declines carries the same envelope -- a **grade**, a summary of what was
refused, an optional contained **detail**, and a remedy -- rather than a
traceback or a bare string.

**The remedy is looked up, never passed in** (:data:`REMEDIES`). A remedy the
caller supplies is a remedy a caller can leave as a placeholder, and a suite that
only asserts it is non-empty passes on ``"Something went wrong."``. Keying it on
the grade makes the population closed: every member of :class:`RefusalGrade` has
exactly one recorded remedy, and
``tests/unit/test_review_ingest_refusals.py::test_every_grade_records_a_remedy_that_names_a_command_and_an_artefact``
reads the enum -- not a transcribed list -- so a grade added without a remedy
reddens before it can be raised.

**The cures are ``gh``-shaped, and that coupling has an owner.** Every entry in
:data:`REMEDIES` names a ``gh`` command, because ``github`` is the only provider
that exists: a GitLab adapter arriving later would tell its users to run
``gh auth login``. ADR-0030 declines to claim provider-neutrality until a second
provider exists (*What this does not close*, item 5), so keying the table on the
provider as well as the grade belongs to **the change that adds one** -- a
dimension added here on speculation would be a second key with one value in it.

**A grade is the only thing a refusal distinguishes.** Two inputs that earn the
same grade produce the same envelope shape, and the summaries name what the
caller already supplied -- the repository it asked for, the limit it exceeded --
rather than anything the request discovered about material the caller may not
read.

**One summary is the exception, named here rather than left for a reader to
find.** ``REPOSITORY_RESOLVED_ELSEWHERE`` echoes the ``owner/name`` GitHub
answered with, which is a *redirect target* and not what the caller supplied. It
is there because an operator cannot correct their own allowlist without it. What
bounds it is the surface rather than the ordering -- the rename check runs
*before* the private check, so the echoed name has not been shown to be public
when it is printed: the name comes from the operator's own authenticated ``gh``
resolving a repository their own ``.theurian/config.yaml`` lists, and no CLI
command, MCP tool or application service reaches the adapter that raises it. A
version that publishes these envelopes to somebody who is not the operator has to
re-take this decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from theurian.domain.errors import InvariantViolationError, TheurianError


class RefusalGrade(StrEnum):
    """Why the run declined, at the granularity a caller may act on.

    A ``StrEnum`` so the member and the string a report carries are one thing.
    The membership is deliberately coarse: an operator acts on "not allowlisted"
    the same way whether the name was absent from the list or malformed, and
    grading those apart would publish which of the two the request was.
    """

    #: The repository is not in ``providers.review.repositories``, or is not a
    #: name that key can hold. One grade for both: a name outside the published
    #: pattern can never match a validated entry, so telling them apart would
    #: report which shape the caller sent and nothing an operator acts on.
    REPOSITORY_NOT_ALLOWLISTED = "repository-not-allowlisted"
    #: The repository resolves as private. Refused at ingestion even when it is
    #: allowlisted: this version ingests no advisory-private GitHub surface
    #: (ADR-0030 decision 2).
    REPOSITORY_IS_PRIVATE = "repository-is-private"
    #: GitHub answered for a repository whose resolved ``owner/name`` is not the
    #: allowlisted one -- the rename redirect ADR-0030 decision 2 refuses rather
    #: than follows.
    REPOSITORY_RESOLVED_ELSEWHERE = "repository-resolved-elsewhere"
    #: The ``gh`` configuration the child would read carries a transport-override
    #: setting, so the request would not go where the argument vector says.
    TRANSPORT_OVERRIDE_CONFIGURED = "transport-override-configured"
    #: No ``gh`` binary was found, so nothing was spawned.
    TOOL_MISSING = "tool-missing"
    #: The installed ``gh`` is below the version floor this adapter is written
    #: against (ADR-0030 clause 8).
    TOOL_TOO_OLD = "tool-too-old"
    #: ``gh`` did not confirm an authenticated session for ``github.com``. It
    #: does not distinguish "not signed in" from "could not check", because the
    #: probe cannot: both are a session this run may not assume.
    TOOL_UNAUTHENTICATED = "tool-unauthenticated"
    #: ``gh`` ran and failed -- a non-zero exit, a timeout, or an output this
    #: adapter cannot read as the response it asked for.
    TOOL_FAILED = "tool-failed"
    #: A recorded bound was reached: the page cap, the pull-request cap, or the
    #: per-response byte cap. Reported, never a silent truncation.
    LIMIT_EXCEEDED = "limit-exceeded"


#: What a reader does about each grade. Every entry names an **artefact** to act
#: on and a **command** the reader can run, because a remedy that names neither
#: sends an operator back into the source.
#:
#: The text carries no interpolation on purpose: a template would let a caller
#: fill it, and then the population this table closes would be open again. What
#: varies -- the repository, the version floor, the bound that was reached -- goes
#: in the refusal's ``summary``, which is the field that describes *this* run.
REMEDIES: Final[dict[RefusalGrade, str]] = {
    RefusalGrade.REPOSITORY_NOT_ALLOWLISTED: (
        "Add the repository to `providers.review.repositories` in "
        "`.theurian/config.yaml`, spelled as GitHub resolves it -- "
        "`gh repo view <owner>/<name> --json nameWithOwner` prints that spelling."
    ),
    RefusalGrade.REPOSITORY_IS_PRIVATE: (
        "Review ingestion reads public repositories only in this version. Remove the "
        "repository from `providers.review.repositories` in `.theurian/config.yaml`; "
        "`gh repo view <owner>/<name> --json visibility` prints what GitHub reports."
    ),
    RefusalGrade.REPOSITORY_RESOLVED_ELSEWHERE: (
        "GitHub answered for a different repository, which is what a rename redirect "
        "looks like. Run `gh repo view <owner>/<name> --json nameWithOwner` to see "
        "where the listed name now points, and update "
        "`providers.review.repositories` in `.theurian/config.yaml` if the rename "
        "was expected."
    ),
    RefusalGrade.TRANSPORT_OVERRIDE_CONFIGURED: (
        "Remove the transport override from `config.yml` in the `gh` configuration "
        "directory this run would read -- the summary above names it, and "
        "`gh config list` prints the settings in force -- then run the ingestion again."
    ),
    RefusalGrade.TOOL_MISSING: (
        "Install the GitHub CLI (https://cli.github.com) so that `gh --version` "
        "answers, then run the ingestion again."
    ),
    RefusalGrade.TOOL_TOO_OLD: (
        "Upgrade the GitHub CLI (https://cli.github.com) to the version the summary "
        "above names; `gh --version` prints the installed one."
    ),
    RefusalGrade.TOOL_UNAUTHENTICATED: (
        "Sign in with `gh auth login --hostname github.com`, confirm with "
        "`gh auth status --hostname github.com`, then run the ingestion again."
    ),
    RefusalGrade.TOOL_FAILED: (
        "Run `gh api graphql --hostname github.com -f query='{viewer{login}}'` by "
        "hand to see the failure with its own output, then run the ingestion again."
    ),
    # Stated so it cures both directions. `limit` is refused below one as well as
    # above the cap, and "narrow the run" sends a caller who asked for zero the
    # wrong way. The two keep one grade because an operator does the same thing
    # about either -- change `limit` -- and this enum's membership is coarse on
    # purpose; what tells them apart is the summary, which names the number.
    RefusalGrade.LIMIT_EXCEEDED: (
        "Adjust the run's bounds and try it again: `limit` is how many pull "
        "requests to read and must be at least one and no more than the cap the "
        "summary above names, and `since_number` skips the pull requests already "
        "ingested. `gh api graphql --hostname github.com` with a smaller page is "
        "the same request by hand."
    ),
}

#: How much contained child output an envelope may carry. A spawned ``gh`` writes
#: whatever it likes to stderr, and an envelope is a published document: the
#: producer slices to this bound before constructing one, and a longer detail is
#: a bug in the producer rather than a megabyte in somebody's terminal.
MAX_REFUSAL_DETAIL_CHARS: Final = 2_000


@dataclass(frozen=True, slots=True)
class RefusalEnvelope:
    """One refusal, as a caller receives it.

    ``detail`` is the only field that can carry text this process did not write --
    a spawned child's stderr, contained. It is bounded at construction so an
    envelope cannot become the channel an unbounded child output travels down.
    """

    grade: RefusalGrade
    summary: str
    detail: str
    remedy: str

    def __post_init__(self) -> None:
        if not self.summary.strip():
            raise InvariantViolationError(
                "A review-ingestion refusal must say what it refused; "
                f"grade {self.grade.value!r} carries an empty summary."
            )
        if not self.remedy.strip():
            raise InvariantViolationError(
                f"Grade {self.grade.value!r} carries an empty remedy. "
                "Remedies are looked up in `REMEDIES`, never passed in."
            )
        if len(self.detail) > MAX_REFUSAL_DETAIL_CHARS:
            raise InvariantViolationError(
                f"A refusal detail is bounded at {MAX_REFUSAL_DETAIL_CHARS} characters "
                f"and this one is {len(self.detail)}. Slice the child's output where it "
                "is read, not here."
            )


class ReviewIngestRefusedError(TheurianError):
    """A review-ingestion run declined, carrying its whole envelope.

    Raised rather than returned because every refusal aborts the run that met it,
    and a caller that wants the envelope reads :attr:`envelope` instead of
    re-parsing a message. ``remedy`` is set from :data:`REMEDIES` by grade, so no
    call site can raise one without a cure.
    """

    def __init__(self, grade: RefusalGrade, summary: str, *, detail: str = "") -> None:
        self.envelope = RefusalEnvelope(
            grade=grade,
            summary=summary,
            detail=detail,
            remedy=REMEDIES[grade],
        )
        self.remedy = self.envelope.remedy
        super().__init__(summary)

    @property
    def grade(self) -> RefusalGrade:
        """The grade, so a caller need not reach through the envelope for it."""
        return self.envelope.grade
