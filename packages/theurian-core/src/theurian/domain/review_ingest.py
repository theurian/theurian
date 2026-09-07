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
same grade produce the same envelope shape.

**Every summary is bounded, and it is bounded on the type.** A summary says what
was refused, and naming that often means naming a value from outside this
package: the repository a caller asked for, the ``owner/name`` GitHub answered
with, a pull request number, a review thread id. :meth:`RefusalEnvelope.__post_init__`
cuts any summary past :data:`MAX_REFUSAL_SUMMARY_CHARS` -- which is what makes
the bound a property of the *type* rather than a habit of its producers, so a
refusal a later change adds inherits it without anyone auditing the call sites.
``tests/unit/test_review_ingest_refusals.py::test_an_envelope_cuts_a_summary_past_the_recorded_bound``
is the key, and its sibling holds the same for the ``str()`` a caller prints.

**Producers cut their own echoes too, at :data:`MAX_SUMMARY_ECHO_CHARS`**, and
that is not redundant with the bound above. The type's cut takes the *end* of a
sentence, so a summary whose echoed value ran long would lose the cap it was
reporting and the remedy's context with it; cutting the value instead keeps the
sentence. :func:`bounded_echo` is where a producer does it and
:func:`bounded_quote` where the site wants the value quoted;
``security/review_allowlist.py``'s ``_rendered`` calls the second of those,
having previously kept a second copy of the same reasoning against a bound of
its own. **A producer picks the one that matches how it renders**: applying
``!r`` to a ``bounded_echo`` result bounds the wrong string, because quoting
expands control characters and the sentence pays for the expansion afterwards.
Both sites in this repository that quote got that ordering wrong before they got
it right, which is why the helper exists rather than the pattern.

**What needs routing is a value from outside this package, and only that.** A
summary also names this package's own numbers -- a page cap, a version floor,
``run_bounded``'s ``byte_cap`` (a parameter, but every production call site
passes a module constant) -- and those need no cut, because nothing outside
chooses them. The two populations are walked by
``tests/unit/test_review_ingest_refusals.py::test_every_summary_interpolation_is_routed_or_this_packages_own``,
which reads the package's own syntax rather than a list somebody keeps in step:
a producer that interpolates an outside value raw reddens there, which is the
check the caller-supplied ``limit`` escaped.

**The redirect target is the echo worth naming**, because it is the one that is
neither the caller's own nor an identifier: ``REPOSITORY_RESOLVED_ELSEWHERE``
echoes the ``owner/name`` GitHub answered with, and an operator cannot correct
their own allowlist without seeing it. What makes it safe is the surface rather
than the ordering -- the rename check runs *before* the private check, so the
echoed name has not been shown to be public when it is printed: the name comes
from the operator's own authenticated ``gh`` resolving a repository their own
``.theurian/config.yaml`` lists.

**That decision has been re-taken once, and the surface it rests on is named
rather than assumed.** ``theurian review ingest`` publishes these envelopes as
of ADR-0030 slice 2, so "nothing reaches the adapter" is no longer what makes
the echo safe. What makes it safe now is *who* reads it: a CLI command is an
operator surface, and every input to the sentence is that same operator's --
their ``gh``, their configuration file, their terminal. They are being shown
where their own allowlist entry now points. **The MCP tool slice 3 adds is not
that surface**, and it must re-take this decision on its own terms: a tool
answers an agent, and an agent is not the person whose ``gh`` resolved the name.
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
    #: adapter cannot read as the response it asked for -- **or was never run**,
    #: because the request could not be built: an argument this adapter could not
    #: render, or an answer whose shape it could not check.
    #:
    #: That last group is the one whose remedy is weakest, and it is recorded
    #: rather than smoothed over. The cure below says to run the request by hand,
    #: which for a never-built request reproduces the *shape* and not the
    #: failure, since the failure was in this adapter's own rendering. It keeps
    #: the shared cure anyway: the summary is the field that describes this run
    #: and it names the variable and says nothing was spawned, while the
    #: alternatives were both declined for recorded reasons -- a remedy template
    #: reopens the population :data:`REMEDIES` closes, and a grade per cause
    #: makes this enum report which internal step failed.
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

#: How long a whole summary may be, **cut** at construction rather than refused.
#: A summary is a sentence or two this package wrote plus the values those
#: sentences name, and a producer cuts each of those at
#: :data:`MAX_SUMMARY_ECHO_CHARS` where it builds it -- so this is room for that
#: shape rather than a measurement of it. What it is *not* is a number a response
#: can reach: a megabyte of pull-request number arrives here and leaves as this
#: many characters.
#:
#: **The backstop is not decorative, and what it costs is why the value-side cut
#: exists.** A producer that interpolated a caller's own ``limit`` raw reached
#: this cut for real, and the summary it published was the sentence with its tail
#: removed -- so the reader lost "the recorded cap is 500", which is the number
#: they had to act on. Cutting the value keeps the sentence; cutting the sentence
#: is what happens when nobody cut the value.
MAX_REFUSAL_SUMMARY_CHARS: Final = 1_000

#: How much of one value from outside this package a summary may echo before
#: :func:`bounded_echo` or :func:`bounded_quote` cuts it and says by how much.
#: Generous against every identifier this adapter names -- an ``owner/name``, a
#: GraphQL node id, a pull request number -- and small enough that several in one
#: sentence still leave it inside :data:`MAX_REFUSAL_SUMMARY_CHARS`.
#:
#: **That last clause holds because the cut is applied to the rendered form.** A
#: bound taken before quoting is not a bound on what the sentence carries: the
#: site that quoted afterwards turned a cut value back into four times this many
#: characters, which is what :func:`bounded_quote` exists to stop.
MAX_SUMMARY_ECHO_CHARS: Final = 200

#: What the type's own cut appends, counted **inside**
#: :data:`MAX_REFUSAL_SUMMARY_CHARS` so the bound is the length of what a caller
#: receives and not that plus a marker.
_SUMMARY_CUT_MARKER: Final = "... (cut)"


def bounded_echo(value: object) -> str:
    """``value`` as a refusal summary may name it: cut, and saying that it was cut.

    A refusal names what it refused so the operator can act on it, and what it
    refused arrived from outside this package -- a GraphQL response, or a
    caller's own argument. Either can be a megabyte. The cut says how much was
    dropped rather than trailing off, because a value silently shortened to look
    plausible is worse for a reader than one that is visibly incomplete.

    The rendering is deliberately total. A refusal path is the one place an
    exception must not be raised -- ADR-0030 clause 9 wants an envelope, never a
    traceback -- and ``str()`` of an integer is not total: CPython refuses to
    render one past ``sys.get_int_max_str_digits()`` (4300 by default), which is
    a shape a JSON number can carry.

    **What guards that upstream guards one half of it.** A number parsed out of a
    response is bounded by ``json.loads``, which applies the same interpreter
    limit while parsing, so no producer reading a response can reach the failure.
    A number a *caller* passed is bounded by nothing at all, and
    ``list_pull_requests`` proved it: it interpolated a caller's ``limit`` raw
    and a 4301-digit one left the refusal path as a ``ValueError``. So the
    totality here is load-bearing rather than defensive, and it is why this
    answers with the type's name instead of raising.
    """
    try:
        text = str(value)
    except ValueError:
        # `int.__str__` past the interpreter's digit limit is the measured member
        # (`sys.set_int_max_str_digits`); the type name locates it without
        # rendering it.
        return _unrenderable(value)
    return _cut(text)


def bounded_quote(value: object) -> str:
    r"""``value`` **quoted** for a summary, bounded *after* quoting rather than before.

    The order is the whole of it, and getting it the other way round is a defect
    with no symptom until the value is hostile. A producer that writes
    ``{bounded_echo(x)!r}`` bounds the *plain* text and then quotes it, and
    quoting is not length-preserving: ``repr`` expands one NUL into the four
    characters ``\x00`` -- which this docstring can only say because it is raw,
    the earlier form having put a real NUL into ``__doc__``. So a megabyte of
    NULs cut to
    :data:`MAX_SUMMARY_ECHO_CHARS` came back four times that long, the summary ran
    past :data:`MAX_REFUSAL_SUMMARY_CHARS`, and the type's cut took the sentence's
    tail -- the exact outcome cutting the value is supposed to prevent.

    Quoting is worth having at a site that has it: a repository name arrives from
    a response, and a raw control character in a published sentence is a value
    pretending to be punctuation. This keeps that and pays for it inside the
    bound. What it costs is that a cut can land mid-escape, which is why the
    marker says the value was cut rather than leaving the reader to wonder --
    and the number it reports is the length of the **rendering**, because the
    rendering is what was cut. For a value that escapes badly the two differ by a
    lot, and the larger of them is the honest answer to "how much is missing from
    this sentence".
    """
    try:
        text = repr(value)
    except ValueError:
        return _unrenderable(value)
    return _cut(text)


def _cut(text: str) -> str:
    """``text`` at :data:`MAX_SUMMARY_ECHO_CHARS`, saying by how much it was cut."""
    if len(text) <= MAX_SUMMARY_ECHO_CHARS:
        return text
    return f"{text[:MAX_SUMMARY_ECHO_CHARS]} (cut from {len(text)} characters)"


def _unrenderable(value: object) -> str:
    """What a summary says about a value that cannot be rendered at all."""
    return f"a value of type {type(value).__name__} this adapter cannot render"


@dataclass(frozen=True, slots=True)
class RefusalEnvelope:
    """One refusal, as a caller receives it.

    ``detail`` is the field that carries a spawned child's stderr, and the only
    one that does. Its producer slices it to :data:`MAX_REFUSAL_DETAIL_CHARS`
    and construction **refuses** an oversized one, so a chatty child cannot turn
    an envelope into a log and a producer that forgets to slice fails here.

    ``summary`` is bounded too, and by **cutting** rather than by refusing. The
    asymmetry is the point rather than an oversight: a summary names what was
    refused, and what was refused is often a value from a response or a caller,
    so an oversized summary is a hostile *answer* arriving on a refusal path.
    Raising there would replace the graded envelope with the traceback ADR-0030
    clause 9 forbids -- the exact failure the refusal was constructed to avoid.
    ``detail`` can afford to refuse because an oversized one is a bug in this
    package rather than something somebody sent.
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
        if len(self.summary) > MAX_REFUSAL_SUMMARY_CHARS:
            kept = MAX_REFUSAL_SUMMARY_CHARS - len(_SUMMARY_CUT_MARKER)
            object.__setattr__(self, "summary", self.summary[:kept] + _SUMMARY_CUT_MARKER)
        if len(self.detail) > MAX_REFUSAL_DETAIL_CHARS:
            raise InvariantViolationError(
                f"A refusal detail is bounded at {MAX_REFUSAL_DETAIL_CHARS} characters "
                f"and this one is {len(self.detail)}. Slice the child's output where it "
                "is read, not here."
            )


class ReviewIngestRefusedError(TheurianError):
    """A review-ingestion run declined, carrying its whole envelope.

    Raised rather than returned because a refusal aborts the operation that
    raised it, and a caller that wants the envelope reads :attr:`envelope`
    instead of re-parsing a message. ``remedy`` is set from :data:`REMEDIES` by
    grade, so no call site can raise one without a cure.

    **The operation, not the run**, and the narrowing is a correction rather
    than a hedge. This said "every refusal aborts the run that met it", which was
    true when nothing caught one and is false now that two seams do: a listing
    converts a single pull request's refusal into a
    :class:`~theurian.domain.ports.review_provider.SkippedPullRequest` it
    *returns*, and an ingestion run converts a per-pull-request fetch's refusal
    into a reported skip. Whether a given refusal ends a run is therefore the
    catching seam's decision, recorded on the port and on
    ``application/review_ingest_service.py``, and not a property of this class.
    """

    def __init__(self, grade: RefusalGrade, summary: str, *, detail: str = "") -> None:
        self.envelope = RefusalEnvelope(
            grade=grade,
            summary=summary,
            detail=detail,
            remedy=REMEDIES[grade],
        )
        self.remedy = self.envelope.remedy
        # The envelope's summary and not the argument: the envelope is where the
        # cut happens, and `str(exc)` is a second publication of the same
        # sentence -- passing the raw one would leave the bound holding for
        # `envelope.summary` and not for the exception a caller prints.
        super().__init__(self.envelope.summary)

    @property
    def grade(self) -> RefusalGrade:
        """The grade, so a caller need not reach through the envelope for it."""
        return self.envelope.grade
