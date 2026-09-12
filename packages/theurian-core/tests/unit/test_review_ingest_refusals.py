"""Every way review ingestion declines carries a cure a reader can run (ADR-0030).

A refusal envelope is a published document, and the failure this file exists to
stop has been caught three times on this project: a remedy constant whose test
asserts only that it is non-empty, so ``"Something went wrong."`` ships. The
assertion here is shape, not length -- a remedy must name **a command the reader
can run** and **an artefact to act on** -- and :data:`_PLACEHOLDERS` is the
can-fail companion that keeps the checker honest.

**The population is the enum, read at run time.** ``RefusalGrade`` is iterated
rather than transcribed, so a grade added without a remedy reddens here before it
can be raised anywhere.
"""

from __future__ import annotations

import ast
import pathlib
import re
from typing import Final

import pytest

from theurian.domain import review_ingest
from theurian.domain.errors import InvariantViolationError
from theurian.domain.review_ingest import (
    MAX_REFUSAL_DETAIL_CHARS,
    MAX_REFUSAL_SUMMARY_CHARS,
    MAX_SUMMARY_ECHO_CHARS,
    REMEDIES,
    RefusalEnvelope,
    RefusalGrade,
    ReviewIngestRefusedError,
    bounded_echo,
    bounded_quote,
)

pytestmark = pytest.mark.unit

#: A backticked command whose first word is a program this project tells people
#: to run, with at least one argument after it. ``\x60gh\x60`` alone is a noun;
#: ``\x60gh auth login\x60`` is something a reader can type.
_COMMAND = re.compile(r"`(gh|git|theurian)\s+[^`]+`")

#: A token with a path, a dotted key, or a host in it -- ``.theurian/config.yaml``,
#: ``providers.review.repositories``, ``config.yml``, ``github.com``,
#: ``https://cli.github.com``. Matched anywhere in the text, backticked or not,
#: because an artefact is named in prose as often as in code font.
_ARTEFACT = re.compile(r"[\w~-]*[./][\w~./-]*[\w/]")

#: Strings that are not remedies, so the checker above can be shown to fail.
#:
#: The first two are the shapes that actually shipped elsewhere on this project.
#: The third and fourth are the near misses: a command with no argument, and an
#: artefact with nothing to run against it.
_PLACEHOLDERS: Final[tuple[str, ...]] = (
    "Something went wrong.",
    "Try again.",
    "Run `gh`.",
    "Edit `.theurian/config.yaml`.",
)


def _names_a_remedy(text: str) -> bool:
    """Whether ``text`` names both a runnable command and an artefact to act on."""
    return _COMMAND.search(text) is not None and _ARTEFACT.search(text) is not None


@pytest.mark.parametrize("placeholder", _PLACEHOLDERS, ids=_PLACEHOLDERS)
def test_the_remedy_check_rejects_a_placeholder(placeholder: str) -> None:
    """The can-fail companion: without it the row below asserts an always-true predicate.

    A test that only ever sees real remedies cannot tell a working checker from
    one that returns ``True``. Each string here is a shape that has shipped, or
    that is one word away from shipping: a sentence with no cure at all, a
    command with no arguments, and an artefact with nothing to run against it.
    """
    assert not _names_a_remedy(placeholder), (
        f"{placeholder!r} passed the remedy check, so the check cannot fail and the "
        "assertion over `REMEDIES` proves nothing. Fix `_names_a_remedy` before "
        "trusting a green result from "
        "`test_every_grade_records_a_remedy_that_names_a_command_and_an_artefact`."
    )


def test_every_grade_records_a_remedy_that_names_a_command_and_an_artefact() -> None:
    """RED means a refusal can be raised with no cure, or with a cure nobody can run.

    The population is ``RefusalGrade`` itself, so this is not a list somebody
    keeps in step: a member added to the enum with no ``REMEDIES`` row fails the
    first assertion, and one whose remedy is a placeholder fails the second.
    """
    missing = [grade.value for grade in RefusalGrade if grade not in REMEDIES]

    assert not missing, (
        f"{missing} have no recorded remedy. Every grade names a cure, because "
        "`ReviewIngestRefusedError` looks the remedy up rather than taking one -- a "
        "grade with no row cannot be raised at all."
    )

    unusable = [grade.value for grade in RefusalGrade if not _names_a_remedy(REMEDIES[grade])]

    assert not unusable, (
        f"{unusable} record a remedy that names no runnable command, no artefact, or "
        "neither:\n"
        + "\n".join(f"  {grade}: {REMEDIES[RefusalGrade(grade)]!r}" for grade in unusable)
        + "\n\nA remedy names the thing to act on and something the reader can type. "
        "A truthy string is not a remedy."
    )


def test_the_limit_cure_covers_the_caps_no_run_parameter_can_move() -> None:
    """One grade, two places a refusal lands, and the cure has to answer both (#597).

    ``LIMIT_EXCEEDED`` is raised by bounds an operator acts on differently, and
    the grade is deliberately **not** split -- the membership of
    :class:`RefusalGrade` is coarse on purpose, and what tells two refusals apart
    is the summary. That decision puts the whole burden on this one static text.

    **The cure routes on where the refusal landed rather than on which cap was
    reached**, and it did not always. The text this predicate list was first
    written against -- superseded at 33e63e9d, so the quotation below is
    deliberately a string the tree no longer holds -- split the grade by cap: a
    bound of the run, against "a per-record cap on one pull request's comments,
    linked issues or labels". Two faces belonged to neither arm: one pull request
    whose review threads or reviews need more than ``MAX_PAGES`` pages, and one
    page of those past ``MAX_RESPONSE_BYTES``. Both are raised inside
    ``_pages_of``, which asks for ``PAGE_SIZE`` records flat, so no run parameter
    reaches either, and neither is a comment, a linked issue or a label. The two
    arms are now:

    * **The refusal ended the run.** ``limit`` and ``since_number`` are the run's
      own parameters: a caller who asked for zero, or for more than
      ``MAX_PULL_REQUESTS``, changes the argument and runs again. The two
      backticked names are how a reader finds the knobs.
    * **The refusal was reported under ``skipped``, against one pull request's
      number.** Its comments, its linked issues, its labels, the pages its
      threads and reviews need, or the size of one answer about it -- properties
      of a single pull request's document, not of the run. A pull request
      carrying 51 labels exceeds the label cap on every invocation, at every
      ``limit``; the provider reports that pull request as skipped and the run
      continues with the rest.

    So the predicates below are not five ways of saying "the text looks right":

    * ``limit`` and ``since_number`` -- RED means the run-ended arm lost its cure
      while the other arm was being rewritten around it.
    * ``skipped`` -- RED means nothing tells the operator what happened to the
      51-label pull request. Without it a reader assumes the run stopped, or
      that the record landed truncated; it did neither, and "this pull request
      was skipped and the rest were ingested" is the only sentence that
      distinguishes those. It is also the word this test splits the cure on, so
      its absence takes the ``pages`` predicate down with it.
    * ``per-record`` -- RED means the second arm is unnamed, so a reader who
      cannot make the refusal go away by any choice of ``limit`` has nothing to
      tell them why, and no reason to stop trying.
    * ``pages``, **in the per-record half only** -- RED means the face that was
      routed to neither arm is unanswered again. It is asserted against the half
      of the cure after ``skipped`` rather than against the whole text, because
      the run-ended half says "over fewer pages" too: a predicate over the whole
      cure would stay green with the per-record pages clause deleted, which is
      the always-true shape this file exists to refuse.
    * **not** ``the cap the summary above names`` -- RED means the misdirection
      is back. For the 51-label pull request the summary names **50**, which is
      the *label* cap; a reader following that clause literally clamps ``limit``
      to 50 for no reason at all, and the refusal recurs unchanged. It is the
      one clause that actively sends the reader the wrong way, which is why it
      is asserted absent rather than merely not asserted present.

    The caps are named by symbol rather than by value: this is a domain-level
    unit test and the numbers live in ``infrastructure/github/limits.py``, where
    ``tests/unit/test_gh_argument_vector.py`` pins them.
    """
    cure = REMEDIES[RefusalGrade.LIMIT_EXCEEDED]
    # Everything after the word naming where a per-record refusal lands, which is
    # the axis the cure routes on. Empty when that word is absent, so the
    # `pages` predicate below fails alongside the `skipped` one rather than
    # reporting a half-truth about a cure that has lost its second arm entirely.
    per_record_arm = cure.partition("skipped")[2]

    broken = [
        (label, why)
        for label, holds, why in (
            (
                "names `limit`",
                "`limit`" in cure,
                "the caller-adjustable bound a run that asked for zero or for more than the "
                "pull-request cap has to change",
            ),
            (
                "names `since_number`",
                "`since_number`" in cure,
                "the other caller-adjustable bound, which skips the pull requests already ingested",
            ),
            (
                "says `skipped`",
                "skipped" in cure,
                "what happens to the pull request that tripped a per-record bound: it is "
                "reported as skipped and the run continues, which no other sentence says",
            ),
            (
                "says `per-record`",
                "per-record" in cure,
                "the second arm, whose bounds no run parameter moves -- unnamed, a "
                "reader keeps adjusting `limit` against a refusal that cannot answer to it",
            ),
            (
                "names `pages` in the half after `skipped`",
                "pages" in per_record_arm,
                "the per-record face the cure's earlier cap list covered nowhere: one pull "
                "request whose review threads or reviews need more than `MAX_PAGES` pages "
                "is reported as skipped, and `_pages_of` asks for `PAGE_SIZE` records flat, "
                "so no `limit` and no `since_number` reaches it",
            ),
            (
                "does not say `the cap the summary above names`",
                "the cap the summary above names" not in cure,
                "for a label overflow the summary names the label cap, so that clause tells "
                "the reader to clamp `limit` to a number belonging to another bound entirely",
            ),
        )
        if not holds
    ]

    assert not broken, (
        "`REMEDIES[LIMIT_EXCEEDED]` is the only cure either landing place gets, and it fails "
        + f"{len(broken)} of its predicates:\n"
        + "\n".join(f"  - {label}: {why}" for label, why in broken)
        + f"\n\nthe text it publishes is:\n  {cure!r}"
    )


def test_a_refusal_carries_the_recorded_remedy_and_cannot_be_handed_another() -> None:
    """RED means a call site can publish its own cure, which is how a placeholder gets in."""
    for grade in RefusalGrade:
        error = ReviewIngestRefusedError(grade, "a summary")

        assert error.remedy == REMEDIES[grade], f"{grade.value} carries a remedy of its own"
        assert error.envelope.remedy == REMEDIES[grade], (
            f"{grade.value}'s envelope and its `remedy` attribute disagree"
        )
        assert error.grade is grade


def test_an_envelope_refuses_an_empty_summary() -> None:
    """A refusal that cannot say what it refused is a bug at construction, not at render."""
    with pytest.raises(InvariantViolationError, match="empty summary"):
        RefusalEnvelope(
            grade=RefusalGrade.TOOL_MISSING,
            summary="   ",
            detail="",
            remedy=REMEDIES[RefusalGrade.TOOL_MISSING],
        )


def test_an_envelope_refuses_a_detail_longer_than_the_recorded_bound() -> None:
    """The child's output is contained by the producer, and this is what says so.

    ``detail`` is the one field carrying text this process did not write. An
    envelope is published, so an unbounded child stderr reaching it would be the
    channel the containment exists to close -- and a producer that forgets to
    slice fails here rather than in somebody's terminal.
    """
    with pytest.raises(InvariantViolationError, match="bounded at"):
        RefusalEnvelope(
            grade=RefusalGrade.TOOL_FAILED,
            summary="gh failed",
            detail="x" * (MAX_REFUSAL_DETAIL_CHARS + 1),
            remedy=REMEDIES[RefusalGrade.TOOL_FAILED],
        )


def test_a_detail_exactly_at_the_bound_is_accepted() -> None:
    """The boundary, so the check above is not off by one in the refusing direction."""
    envelope = RefusalEnvelope(
        grade=RefusalGrade.TOOL_FAILED,
        summary="gh failed",
        detail="x" * MAX_REFUSAL_DETAIL_CHARS,
        remedy=REMEDIES[RefusalGrade.TOOL_FAILED],
    )

    assert len(envelope.detail) == MAX_REFUSAL_DETAIL_CHARS


def test_an_envelope_cuts_a_summary_past_the_recorded_bound() -> None:
    """The bound that makes ``summary`` a bounded channel lives on the **type**.

    A summary names what was refused, and what was refused arrives from outside
    this package -- so a producer that interpolates a response value raw would
    otherwise publish whatever that value is. A megabyte of pull-request number
    produced a megabyte of summary, which is what this closes: not by asking
    every producer to remember, but by cutting here, where every refusal that
    exists and every refusal a later change adds passes through.

    **Cut and not refused**, deliberately. Raising on an oversized summary would
    replace a graded envelope with the traceback ADR-0030 clause 9 forbids, on
    the one path that exists to avoid exactly that.
    """
    envelope = RefusalEnvelope(
        grade=RefusalGrade.TOOL_FAILED,
        summary="gh answered with " + "N" * 1_000_000,
        detail="",
        remedy=REMEDIES[RefusalGrade.TOOL_FAILED],
    )

    assert len(envelope.summary) == MAX_REFUSAL_SUMMARY_CHARS, (
        f"a 1,000,017-character summary arrived at construction and left "
        f"{len(envelope.summary)} characters long. The bound is on the type so that "
        f"no producer has to remember it."
    )
    assert envelope.summary.startswith("gh answered with N")
    assert envelope.summary.endswith("(cut)"), (
        "the summary was shortened without saying so, which reads as a complete "
        "sentence that happens to stop"
    )


def test_a_summary_exactly_at_the_bound_is_kept_whole() -> None:
    """The boundary, so the cut above is not off by one in the cutting direction."""
    envelope = RefusalEnvelope(
        grade=RefusalGrade.TOOL_FAILED,
        summary="s" * MAX_REFUSAL_SUMMARY_CHARS,
        detail="",
        remedy=REMEDIES[RefusalGrade.TOOL_FAILED],
    )

    assert envelope.summary == "s" * MAX_REFUSAL_SUMMARY_CHARS


def test_the_exception_a_caller_prints_carries_the_cut_summary() -> None:
    """``str(exc)`` is a second publication of the same sentence, and it is the same one.

    ``ReviewIngestRefusedError`` passes the summary to ``TheurianError`` as well
    as to the envelope. Handing the *argument* over rather than the envelope's
    own value would leave the bound holding for ``envelope.summary`` and not for
    the string a caller prints -- one bounded channel and one unbounded one,
    carrying the same text.
    """
    error = ReviewIngestRefusedError(RefusalGrade.TOOL_FAILED, "boom " + "B" * 500_000)

    assert len(str(error)) == MAX_REFUSAL_SUMMARY_CHARS
    assert str(error) == error.envelope.summary


def test_bounded_echo_cuts_a_long_value_and_says_by_how_much() -> None:
    """A cut that does not say it cut reads as a value that happens to end there."""
    echoed = bounded_echo("R" * 1_000_000)

    assert echoed.startswith("R" * MAX_SUMMARY_ECHO_CHARS)
    assert "cut from 1000000 characters" in echoed


@pytest.mark.parametrize(
    "hostile",
    ("\x00", "\\", "\n", "\x1b"),
    ids=("a NUL", "a backslash", "a newline", "an escape"),
)
def test_a_quoted_echo_is_bounded_after_quoting_not_before(hostile: str) -> None:
    """Quoting is not length-preserving, so a bound taken before it is not a bound.

    ``repr`` expands one NUL into the four characters ``\\x00``. A producer
    writing ``{bounded_echo(x)!r}`` therefore bounds the *plain* text and then
    quadruples it, and the summary that carries it runs past the type's cut --
    which takes the sentence's tail, the outcome cutting the value exists to
    prevent. Every character here expands under ``repr``; the NUL is the one that
    was reproduced end to end.

    The assertion is on the **rendered** length, because that is what the sentence
    pays for. The old ordering is measured beside it so the test cannot pass by
    the two being equal.
    """
    value = hostile * 100_000

    quoted = bounded_quote(value)
    old_ordering = repr(bounded_echo(value))

    assert len(quoted) <= MAX_SUMMARY_ECHO_CHARS + len(" (cut from 100000 characters)"), (
        f"a quoted echo is {len(quoted)} characters, past what a bound applied to the "
        f"rendered form allows. A summary carries the rendering, not the value."
    )
    assert len(old_ordering) > len(quoted), (
        "quoting the value did not expand it, so this parametrisation cannot tell "
        "the two orderings apart and proves nothing about either"
    )
    # The marker counts the *rendering*, which is what was cut -- not the value.
    # For a character that escapes badly the two differ several-fold, and the
    # larger is the honest answer to how much is missing from the sentence.
    assert f"cut from {len(repr(value))} characters" in quoted, (
        f"the marker does not report the rendering's length; it reads {quoted[-40:]!r} "
        f"where `repr` of the value is {len(repr(value))} characters"
    )


def test_bounded_echo_renders_a_value_str_itself_refuses() -> None:
    """The rendering is total, because a refusal path may not raise.

    ``str()`` of an integer is not total: CPython refuses past
    ``sys.get_int_max_str_digits()`` -- 4300 by default -- and **both** halves of
    what a summary names can carry more digits than that. Only one of them is
    guarded upstream. A number out of a *response* is bounded by ``json.loads``,
    which applies the same interpreter limit while parsing; a number out of a
    *caller* is bounded by nothing, and was not -- ``list_pull_requests``
    interpolated a caller's own ``limit`` raw, and a 4301-digit one left the
    refusal path as the ``ValueError`` a refusal exists to replace. That producer
    routes through here now. The totality is the property, not a convenience:
    this helper is reached from paths where raising is the one thing forbidden.
    """
    echoed = bounded_echo(-(10**5000))

    assert echoed == "a value of type int this adapter cannot render"


#: Where a refusal summary may take a value from without routing it through a
#: bounding helper: names this package chooses the value of.
#:
#: Every member carries the reason it is safe, because the list is the *only*
#: thing standing between a raw interpolation and a published megabyte -- and a
#: name added here without a reason is how the caller-supplied `limit` sat
#: unnoticed. A new name reddens the walk below until somebody either routes it
#: or writes down why it needs no routing.
_THIS_PACKAGES_OWN: Final[dict[str, str]] = {
    "MAX_PULL_REQUESTS": "a module constant",
    "MAX_PAGES": "a module constant",
    "MAX_LINKED_ISSUES": "a module constant",
    "MAX_COMMENTS_PER_THREAD": "a module constant",
    "MAX_PROBE_STDOUT_BYTES": "a module constant",
    "MAX_REFUSAL_DETAIL_CHARS": "a module constant",
    "MAX_REPOSITORY_CHARS": "a module constant",
    "GITHUB_HOSTNAME": "a module constant",
    "GH_CONFIG_FILE": "a module constant",
    "byte_cap": "a parameter; both production call sites pass a module constant",
    "cap": "a loop variable over two module constants, MAX_LINKED_ISSUES and MAX_LABELS_*",
    "members": "one of two literals naming a capped connection's members",
    "timeout": "a parameter; production passes REQUEST_TIMEOUT_SECONDS",
    "entry": "the allowlist entry, so the operator's own config and pattern-bounded",
    "field": "this adapter's own literal naming a response field",
    # Not "this adapter's own literal" any more, and the correction is the point:
    # `_pages_of` builds `what` as `f"{read.subject} on #{bounded_echo(event.number)}"`,
    # so half of it is the pull request's number. That half is routed where it is
    # built rather than where it is spent, which this row has to say or a reader
    # takes the name for a constant.
    "what": (
        "this adapter's own literal naming a read, plus the pull request number, "
        "which `_pages_of` routes through `bounded_echo` where it composes them"
    ),
    "name": "a GraphQL variable name from a closed set",
    "selected_by": "one of three literals naming the variable that chose a directory",
    "key": "a member of TRANSPORT_OVERRIDE_KEYS",
    "named": "an OSError's strerror, the operating system's own short message",
    "arguments": "a probe's vector, which is this adapter's own literals",
    # -- the review-evidence store's own derived values (round two's widening) --
    "relative": (
        "a path this build derived, bounded by the layout: a hashed directory of 71 "
        "characters, a kind value from the enum, and a leaf `_FILESYSTEM_SAFE` bounds "
        "at 156 bytes"
    ),
    "opened": "the same derived path plus `_WRITING_SUFFIX`, so bounded the same way",
    "spelling": (
        "one of the two derived paths `_write_one` builds -- the record's, or the same "
        "plus `_WRITING_SUFFIX` -- so bounded by the layout either way"
    ),
    # -- the codec's own field locators (round three's widening to the raises) --
    "where": (
        "the codec's own dotted locator, composed from its literals and from `index` "
        "below: `record`, `record.author`, `record.comments[3].author`. It grows with "
        "the document's nesting depth and carries no value out of it"
    ),
    "index": (
        "`enumerate`'s counter over a list the document carried, so bounded by how "
        "many elements `MAX_SOURCE_FILE_BYTES` can hold -- seven digits at the very "
        "most, and a position rather than a value"
    ),
    "earlier": "an earlier record's derived path, from the same layout",
    "on_disk": (
        "a directory entry the review root already holds, bounded by the filesystem's "
        "own 255-byte component limit"
    ),
    "derived": "one component of a derived path, bounded by the layout",
    "shape": "one of `unbounded_shape`'s own literals naming a file type",
    "landing": "this store's own count of the bytes a record would land as",
    "MAX_SOURCE_FILE_BYTES": "a module constant",
    "UNNAMED_REPOSITORY": "a module constant naming the clause, not a value",
    "EVIDENCE_FORMAT_VERSION": "a module constant",
    # -- the published `describe` lines ---------------------------------------
    "inside": "a local composed from `bounded_quote(self.comment_id)` two lines up",
    "at": (
        "the caller's own location string; `ReviewSecretFinding.describe` composes it "
        "from `identity.describe()`, which routes both of its outside values"
    ),
}

#: Expressions that are safe **in this exact shape and not otherwise**.
#:
#: The distinction is the whole point, and it was measured rather than argued.
#: ``exc``, ``value`` and ``event`` used to sit in the table above as bare names
#: with a reason that named a shape -- "reached only as ``type(exc).__name__``"
#: -- and nothing enforced the shape. So regressing ``_start``'s
#: ``{named or type(exc).__name__}`` to ``{exc}`` stayed green, and ``{exc}`` is
#: how an ``OSError``'s ``str()`` publishes the absolute path of the operator's
#: ``gh``: the disclosure a whole commit was written to close. Matching the
#: unparsed expression is what makes the reason enforceable instead of a note.
_EXEMPT_EXPRESSIONS: Final[dict[str, str]] = {
    "named or type(exc).__name__": (
        "an OS message or a class name; `{exc}` would publish the OSError's filename"
    ),
    "type(value).__name__": "a class name, not the value",
    "type(exc).__name__": "a class name, not the value",
    "type(parsed).__name__": "a class name, not the value",
    "type(stamp).__name__": "a class name, not the value",
    "type(comments).__name__": "a class name, not the value",
    "type(item).__name__": "a class name, not the value",
    # The reason rests on a *precondition*, and the precondition is driven rather
    # than trusted: every site interpolating this sits inside `get_threads` or
    # `get_reviews`, whose first act is `_allowlisted(event.repository)`, so the
    # value matched the schema's `[\w.-]+/[\w.-]+` under `re.ASCII` at no more
    # than `MAX_REPOSITORY_CHARS` before this sentence could exist.
    # `test_gh_review_provider.py`'s
    # `test_a_hostile_repository_on_an_event_is_refused_before_this_sentence_exists`
    # is what fails if a site ever interpolates it ahead of that call.
    "event.repository": (
        "the allowlisted name, pattern-bounded by the check `get_threads` and "
        "`get_reviews` run first; `{event}` would publish the whole record, "
        "response title and url included"
    ),
    # -- expressions safe in this shape only, from round two's widened key -----
    "record.kind.value": "an `EvidenceKind` member, so one of three module literals",
    "self.grade.value": "a `RefusalGrade` member, so one of the enum's own literals",
    "self.summary": "already cut by `RefusalEnvelope.__post_init__` at construction",
    "self.field": "a value of `_FIELD_LITERALS`, this module's own table",
    "self.line": "a line number this scanner counted",
    "self.column": "a column this scanner counted",
    "self.family": "a detector name from `content_secrets`' own table",
    "self.redacted": "bounded on `SecretFinding` itself, at four characters",
    "self.identity.describe()": (
        "another `describe` in this same walked population, which routes its own values"
    ),
    "_partial_landing(len(landed))": "this store's own sentence over its own count",
    "_partial_landing(landed)": "this store's own sentence over its own count",
    "repository_named_in(raw)": "routes the repository it reads through `bounded_echo`",
    "UNNAMED_REPOSITORY if raw is None else repository_named_in(raw)": (
        "the same two members as a ternary, on the read arm that has the file's bytes "
        "only when the read got that far. Exempt as this whole expression rather than "
        "as a `repository` parameter: a bare name would let any later caller pass an "
        "unrouted clause, which is the shape a caller's `limit` sat unnoticed under"
    ),
    "exc.derived": "a path this build derived, carried on `_FoldedPathError`",
    "record.relative_path": (
        "the path `_stored` derives from the record it just read: a 71-character hashed "
        "directory, a kind value from the enum, and a leaf `_FILESYSTEM_SAFE` bounds at "
        "156 bytes. Exempt as this expression rather than as `record`, which names an "
        "object whose other fields come straight out of the file"
    ),
    "self.run_id": (
        "unreachable from a landed file, measured rather than assumed: `_stored` builds "
        "the stamp through `_moment`, which refuses a naive `observedAt` before "
        "`IngestionRun` is constructed -- a 2,000,000-character `runId` beside a naive "
        "stamp produced a 214-character refusal. Production's other constructor, "
        "`new_ingestion_run`, takes a ULID from the injected ids"
    ),
    "exc.strerror or 'the read was refused'": (
        "the operating system's own short message, or this module's literal"
    ),
    "exc.strerror or 'the write was refused'": (
        "the operating system's own short message, or this module's literal"
    ),
    "exc.strerror or 'the rename was refused'": (
        "the operating system's own short message, or this module's literal"
    ),
    "exc": (
        "a `ReviewEvidenceError` this store built and this file already walks, or a "
        "`SecurityError`/`ValueError` from the codec whose messages name a field and a "
        "type rather than a value -- the two that did name a value, `formatVersion` and "
        "`kind`, route through `bounded_quote` at their raise sites. That last clause "
        "was a note nothing enforced until part 4 of `_interpolations`' key: replacing "
        "either routing with `!r` kept the suite green, and both raise sites are now "
        "inside this walk"
    ),
}
#: The refusal classes whose published sentence this file walks, mapped to
#: **which argument** carries that sentence.
#:
#: Two entries rather than one since round two. The walk was keyed on
#: ``ReviewIngestRefusedError`` alone, and the review-evidence store publishes
#: its refusals through a different class -- so ``{record.record_key!r}``
#: interpolated a provider node id raw and a 2,000,468-character one reached an
#: operator's terminal, sitting outside a population whose whole purpose is to
#: catch exactly that. The lesson is the ratchet rather than the fix: a *second*
#: published-sentence type is the shape this walk will meet again, so the key is
#: a table and the count check below ranges over it.
_REFUSAL_CLASSES: Final[dict[str, tuple[int, str]]] = {
    "ReviewIngestRefusedError": (1, "summary"),
    "ReviewEvidenceError": (0, "message"),
}

#: How the source text mentions either class as a call, for the count that keeps
#: the syntax walk honest about its own population.
_MENTIONS_THE_REFUSAL: Final = re.compile("|".join(rf"\b{name}\(" for name in _REFUSAL_CLASSES))

#: Where a *published* sentence is composed outside a refusal constructor.
#:
#: ``describe()`` is the shape: a report's own one-line rendering, published by
#: ``cli/review_commands.py::_payload`` into ``refused``, ``findings`` and
#: ``skipped``. It is not a refusal and carries no remedy, so the walk above
#: cannot see it -- and a raw ``pull_request_number`` in one is 4,300 digits
#: times a 500-slot window, which is 2.17 MB of published document.
_DESCRIBE_METHOD: Final = "describe"

#: Which modules' ``describe`` methods this file's bound applies to.
#:
#: **Scoped to the review-ingestion publish path, and the scope is derived
#: rather than chosen.** ``_payload`` publishes three lists of ``describe()``
#: lines, and the types behind them are ``ReviewRecordIdentity`` and
#: ``ReviewSecretFinding`` (``application/review_landing_gate.py``),
#: ``FetchRefusal`` (``application/review_ingest_service.py``), and
#: ``SecretFinding`` (``security/content_secrets.py``), which the second of those
#: delegates to. :func:`test_the_describe_scope_covers_every_type_the_command_publishes`
#: is what fails when a fourth type joins that document.
#:
#: The package has ``describe`` methods elsewhere -- ``application/index_builder.py``
#: and ``security/tokens.py`` -- and they are deliberately outside this file's
#: subject rather than exempted inside it: writing a "why this value is bounded"
#: line about a subsystem nobody analysed here is how an exemption table stops
#: meaning anything.
_DESCRIBE_MODULES: Final[frozenset[str]] = frozenset(
    {"review_landing_gate.py", "review_ingest_service.py", "content_secrets.py"}
)

#: The helpers that bound a value on its way into a summary.
_ROUTERS: Final[frozenset[str]] = frozenset(
    {"bounded_echo", "bounded_quote", "_rendered", "rendered_version"}
)

#: The module every ``remedy=`` on the evidence path resolves into.
#:
#: A ``remedy`` is a **published** string -- ``cli.commands._fail`` prints it
#: beside the error and ``--json`` puts it in the document -- and its
#: interpolations happen inside these functions rather than at the refusal, so
#: the sentence-argument walk above cannot see one. Measured: dropping
#: ``bounded_echo`` from ``oversized_record_cure`` entirely, so a provider-chosen
#: URL was interpolated raw, left the whole review suite green.
_CURES_MODULE: Final = "cures.py"

#: Where the review-evidence package raises something ``_read_one`` republishes.
#:
#: ``_read_one``'s ``(ValueError, DomainError)`` arm interpolates ``{exc}``, so
#: every message raised under this directory is a *second* published sentence
#: with no refusal constructor of its own. ``_EXEMPT_EXPRESSIONS["exc"]`` already
#: rested on that -- its reason names two ``bounded_quote`` routings at their
#: raise sites -- and nothing enforced it: replacing either with ``!r`` kept the
#: length bound off and the suite green, twice.
_REPUBLISHED_PACKAGE: Final = "review_evidence"


def _source_files() -> list[pathlib.Path]:
    """Every module in the shipped package, in a stable order."""
    return sorted(pathlib.Path(review_ingest.__file__).parents[2].rglob("*.py"))


def _refusal_calls() -> list[tuple[pathlib.Path, ast.Call]]:
    """Every construction of a class in :data:`_REFUSAL_CLASSES` the package makes."""
    found: list[tuple[pathlib.Path, ast.Call]] = []
    for path in _source_files():
        source = path.read_text(encoding="utf-8")
        if not any(f"{name}(" in source for name in _REFUSAL_CLASSES):
            continue
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") in _REFUSAL_CLASSES:
                found.append((path, node))
    return found


def _summary_of(call: ast.Call) -> ast.expr | None:
    """The published-sentence argument, however it was passed.

    Its position differs per class -- ``ReviewIngestRefusedError(grade, summary)``
    puts it second, ``ReviewEvidenceError(message, *, remedy)`` first -- so
    :data:`_REFUSAL_CLASSES` carries the index and the keyword together. Reading
    only the positional form is not a stylistic limitation: a producer that
    passed it by keyword would have every interpolation in it go unexamined, and
    the walk would report the same clean result it reports now.
    """
    index, keyword_name = _REFUSAL_CLASSES[getattr(call.func, "id", "")]
    if len(call.args) > index:
        return call.args[index]
    for keyword in call.keywords:
        if keyword.arg == keyword_name:
            return keyword.value
    return None


def _describe_methods() -> list[tuple[pathlib.Path, ast.FunctionDef]]:
    """Every ``describe`` method the package defines.

    The second half of the population, and a genuinely different shape: a
    ``describe`` composes a **published** line -- ``cli/review_commands.py``
    emits three lists of them -- while carrying no remedy and constructing no
    refusal, so a walk keyed on a refusal class cannot see one.
    """
    return [
        (path, node)
        for path in _source_files()
        if path.name in _DESCRIBE_MODULES
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.FunctionDef) and node.name == _DESCRIBE_METHOD
    ]


def test_the_describe_scope_covers_every_type_the_command_publishes() -> None:
    """RED means a fourth published list escapes this file's bound.

    :data:`_DESCRIBE_MODULES` is a scope, and a scope narrows a population --
    the one thing a derived population must not do by hand. So the derivation is
    checked: every type whose ``describe()`` ``cli/review_commands.py::_payload``
    publishes must be **defined** in one of the scoped modules, and each of those
    modules must actually contain a ``describe``.
    """
    published = {"ReviewRecordIdentity", "ReviewSecretFinding", "FetchRefusal", "SecretFinding"}
    defined: set[str] = set()
    scoped: set[str] = set()
    for path, method in _describe_methods():
        scoped.add(path.name)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ClassDef) and any(
                isinstance(body, ast.FunctionDef) and body.name == _DESCRIBE_METHOD
                for body in node.body
            ):
                defined.add(node.name)
        assert method.name == _DESCRIBE_METHOD

    assert scoped == _DESCRIBE_MODULES, (
        f"{sorted(_DESCRIBE_MODULES - scoped)} are scoped in and define no `describe`, so "
        "the scope names a file that has moved or been renamed."
    )
    assert published <= defined, (
        f"{sorted(published - defined)} are published by `review ingest` as `describe()` "
        f"lines and are defined outside `_DESCRIBE_MODULES`. Widen the scope -- a "
        f"published line whose values nothing bounds is a megabyte in somebody's "
        f"terminal."
    )


def _remedy_arguments() -> list[tuple[pathlib.Path, ast.AST]]:
    """The ``remedy=`` argument of every refusal, and the cures it resolves into.

    Two shapes, because a remedy is composed in two places. An f-string passed
    straight to ``remedy=`` interpolates at the refusal; every other remedy on
    this path is a call into ``cures.py``, which interpolates inside its own
    functions. Walking the module wholesale rather than resolving each callee is
    the honest option here -- it is a module whose every public member is a
    published string, which its own docstring is what states.
    """
    sentences: list[tuple[pathlib.Path, ast.AST]] = [
        (path, keyword.value)
        for path, call in _refusal_calls()
        for keyword in call.keywords
        if keyword.arg == "remedy"
    ]
    sentences += [
        (path, ast.parse(path.read_text(encoding="utf-8")))
        for path in _source_files()
        if path.name == _CURES_MODULE and path.parent.name == _REPUBLISHED_PACKAGE
    ]
    return sentences


def _republished_raises() -> list[tuple[pathlib.Path, ast.AST]]:
    """Every message the evidence package raises that ``_read_one`` republishes.

    Its ``(ValueError, DomainError)`` arm renders ``{exc}`` into a
    ``ReviewEvidenceError``, so a message raised anywhere under this package is
    a published sentence built with no refusal constructor in sight -- and
    ``read_source_file``'s cap is the only thing bounding the document it was
    read out of, which is 8 MiB.
    """
    return [
        (path, node.exc.args[0])
        for path in _source_files()
        if path.parent.name == _REPUBLISHED_PACKAGE
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call) and node.exc.args
    ]


def _interpolations() -> list[tuple[str, str]]:
    """Every interpolated expression in every sentence this package publishes.

    The population key, stated so it can be attacked, in four parts under
    ``packages/theurian-core/src``:

    1. the **sentence argument** of every call to a class in
       :data:`_REFUSAL_CLASSES`;
    2. every f-string inside a ``describe`` method in :data:`_DESCRIBE_MODULES`;
    3. every ``remedy=`` argument, and the whole of
       ``review_evidence/cures.py``, which is where the rest of them interpolate;
    4. every ``raise <Class>(<message>)`` under ``review_evidence/``, because
       ``EvidenceReader._read_one`` republishes ``{exc}``.

    ``detail`` is excluded deliberately: it has a bound of its own, enforced by
    refusing at construction rather than by its producers.

    Parts 3 and 4 are the widening a verdict pass forced, and each was measured
    rather than argued. Dropping ``bounded_echo`` from ``oversized_record_cure``
    left a provider-chosen URL interpolated raw into a printed remedy, green.
    Replacing ``bounded_quote`` with ``!r`` at either of ``_stored``'s two value-
    naming raises took the length bound off a sentence composed from a landed
    file, green -- while the reason recorded beside ``_EXEMPT_EXPRESSIONS["exc"]``
    cited exactly those two routings as what made the exemption safe.

    **What this key still cannot see, said here rather than discovered later.**
    It matches a class by *name* at the call, so a qualified call
    (``review_ingest.ReviewIngestRefusedError(...)``) is invisible to it -- that
    one is caught, because
    :func:`test_the_walk_matches_every_call_the_source_text_mentions` counts the
    text too and the two then disagree. An **aliased import** escapes both, since
    neither the text nor the name matches. So does a summary built in a helper
    and passed in as a variable: the interpolation happens somewhere this does
    not look, and the variable itself reads as a bare name. The last is why
    :data:`_THIS_PACKAGES_OWN` is a table of *reasons* and not a list of names.
    Part 3 covers ``cures.py`` and no other remedy-composing module, and part 4
    covers ``review_evidence/`` and not the packages whose raises reach a caller
    by some other route.
    """
    found: list[tuple[str, str]] = []
    sentences: list[tuple[pathlib.Path, ast.AST]] = [
        (path, summary)
        for path, call in _refusal_calls()
        if (summary := _summary_of(call)) is not None
    ]
    sentences += list(_describe_methods())
    sentences += _remedy_arguments()
    sentences += _republished_raises()
    for path, sentence in sentences:
        for piece in ast.walk(sentence):
            if isinstance(piece, ast.FormattedValue):
                found.append((f"{path.name}:{piece.lineno}", ast.unparse(piece.value)))
    return found


def _value_sources(text: str) -> list[str]:
    """The names an interpolated expression takes its **value** from.

    A method name is not one of them: ``' '.join(arguments)`` takes its value
    from ``arguments``, and ``join`` is how it is spelled. Collecting the callee
    of a call would make every rendering helper look like an unbounded source and
    push the answer into :data:`_THIS_PACKAGES_OWN` as noise, which is the
    opposite of what that table is for.
    """
    tree = ast.parse(text)
    called = {node.func for node in ast.walk(tree) if isinstance(node, ast.Call)}
    return [node.id for node in ast.walk(tree) if isinstance(node, ast.Name)] + [
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node not in called
    ]


def test_the_walk_matches_every_call_the_source_text_mentions() -> None:
    """The walk's key is a name at a call site, so a different call shape is silent.

    Two counts of one population, taken different ways. The text knows nothing
    about syntax and finds every mention; the walk knows the syntax and finds
    every call it recognises. While they agree, the walk is looking at all of it.

    A **keyword** summary (``summary=``) used to fall out of the walk while the
    text still counted it, and a **qualified** call
    (``review_ingest.ReviewIngestRefusedError(...)``) still does -- the walk
    matches ``node.func.id``, which a qualified call does not have. Either now
    shows up here as a disagreement rather than as a clean report over a
    population that quietly shrank.

    The class's own ``class ReviewIngestRefusedError(TheurianError):`` line
    matches the text and is not a call, so it is subtracted by name rather than
    by counting it out.
    """
    mentions = 0
    definitions = 0
    for path in _source_files():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not _MENTIONS_THE_REFUSAL.search(line):
                continue
            if any(line.lstrip().startswith(f"class {name}(") for name in _REFUSAL_CLASSES):
                definitions += 1
            else:
                mentions += 1

    assert definitions == len(_REFUSAL_CLASSES), (
        f"{definitions} of the {len(_REFUSAL_CLASSES)} refusal classes are defined in the "
        "package, so the subtraction below is measuring something other than what it was "
        "written for"
    )
    assert len(_refusal_calls()) == mentions, (
        f"the source text mentions the refusal as a call {mentions} times and the "
        f"walk recognises {len(_refusal_calls())}. A call the walk cannot see is a "
        "summary nobody checks: give it the plain, unqualified form, or teach "
        "`_refusal_calls` the shape and say here why the two counts still line up."
    )


def test_the_summary_walk_finds_the_interpolations_it_is_meant_to_judge() -> None:
    """The can-fail companion: an empty walk would make the row below always green.

    A structural check that finds nothing passes for the same reason a correct
    one does. This asserts the walk reaches a population at all, and that it
    reaches the two shapes the next test classifies -- so a rename of
    ``ReviewIngestRefusedError`` or a move of the package turns into a red test
    here rather than a silent all-clear there.
    """
    found = _interpolations()

    assert len(found) > 20, (
        f"the walk found {len(found)} interpolations across the package, which is "
        "too few to be the real population -- the refusal class or the source root "
        "has moved and this check is now watching nothing."
    )
    rendered = {text for _, text in found}
    assert any("bounded_echo" in text for text in rendered), "no routed member found"
    assert any(text in _THIS_PACKAGES_OWN for text in rendered), "no own-value member found"


def test_every_summary_interpolation_is_routed_or_this_packages_own() -> None:
    """RED means a producer can publish a value nothing bounded.

    A summary is a published document and the values it names come from two
    places. One is **outside** this package -- a GraphQL response, a caller's own
    argument -- and every one of those goes through a bounding helper, because
    either can be a megabyte and ``str()`` of a large enough integer does not
    return at all. The other is this package's own numbers and literals, which
    need no cut because nothing outside chooses them.

    This walks the package's syntax rather than a list somebody keeps in step,
    which is the point: the defect it exists to catch was a caller's ``limit``
    interpolated raw into two summaries, sitting beside fifteen echoes that were
    routed correctly. Reading the call sites did not find it; three review rounds
    did not find it either.
    """
    unrouted = [
        f"  {where}  {text}"
        for where, text in _interpolations()
        if not any(router in text for router in _ROUTERS)
        and text not in _EXEMPT_EXPRESSIONS
        and not all(part in _THIS_PACKAGES_OWN for part in _value_sources(text))
    ]

    assert not unrouted, (
        "a refusal summary interpolates a value that is neither routed through a "
        "bounding helper nor this package's own:\n"
        + "\n".join(unrouted)
        + "\n\nRoute it through `bounded_echo` (or `bounded_quote` where the site "
        "quotes); or, if it needs no bound, add the *name* to `_THIS_PACKAGES_OWN` "
        "when any use of it is safe, and the *whole expression* to "
        "`_EXEMPT_EXPRESSIONS` when only this shape is -- with the reason either "
        "way. A summary is published; an unbounded value in one is a megabyte in "
        "somebody's terminal, or a `ValueError` out of the one path that may not "
        "raise."
    )
