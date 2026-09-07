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
    "timeout": "a parameter; production passes REQUEST_TIMEOUT_SECONDS",
    "entry": "the allowlist entry, so the operator's own config and pattern-bounded",
    "field": "this adapter's own literal naming a response field",
    "what": "this adapter's own literal naming a read",
    "name": "a GraphQL variable name from a closed set",
    "selected_by": "one of three literals naming the variable that chose a directory",
    "key": "a member of TRANSPORT_OVERRIDE_KEYS",
    "named": "an OSError's strerror, the operating system's own short message",
    "arguments": "a probe's vector, which is this adapter's own literals",
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
    "event.repository": (
        "the allowlisted name; `{event}` would publish the whole record, response "
        "title and url included"
    ),
}

#: How the source text mentions the refusal as a call, for the count that keeps
#: the syntax walk honest about its own population.
_MENTIONS_THE_REFUSAL: Final = re.compile(r"\bReviewIngestRefusedError\(")

#: The helpers that bound a value on its way into a summary.
_ROUTERS: Final[frozenset[str]] = frozenset(
    {"bounded_echo", "bounded_quote", "_rendered", "rendered_version"}
)


def _source_files() -> list[pathlib.Path]:
    """Every module in the shipped package, in a stable order."""
    return sorted(pathlib.Path(review_ingest.__file__).parents[2].rglob("*.py"))


def _refusal_calls() -> list[tuple[pathlib.Path, ast.Call]]:
    """Every ``ReviewIngestRefusedError(...)`` construction the package makes."""
    found: list[tuple[pathlib.Path, ast.Call]] = []
    for path in _source_files():
        source = path.read_text(encoding="utf-8")
        if "ReviewIngestRefusedError(" not in source:
            continue
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == (
                "ReviewIngestRefusedError"
            ):
                found.append((path, node))
    return found


def _summary_of(call: ast.Call) -> ast.expr | None:
    """The summary argument, however it was passed.

    Positionally it is the **second** -- ``(grade, summary)`` -- and by keyword it
    is ``summary=``. Reading only the positional form is not a stylistic
    limitation: a producer that passed it by keyword would have every
    interpolation in it go unexamined, and the walk would report the same clean
    result it reports now.
    """
    if len(call.args) >= 2:
        return call.args[1]
    for keyword in call.keywords:
        if keyword.arg == "summary":
            return keyword.value
    return None


def _interpolations() -> list[tuple[str, str]]:
    """Every interpolated expression in every refusal summary the package builds.

    The population key, stated so it can be attacked: the **summary argument** --
    the second positional, or ``summary=`` -- of every
    ``ReviewIngestRefusedError(...)`` call under
    ``packages/theurian-core/src``. ``detail`` is excluded deliberately: it has a
    bound of its own, enforced by refusing at construction rather than by its
    producers.

    **What this key cannot see, said here rather than discovered later.** It
    matches the class by *name* at the call, so a qualified call
    (``review_ingest.ReviewIngestRefusedError(...)``) is invisible to it -- that
    one is caught, because
    :func:`test_the_walk_matches_every_call_the_source_text_mentions` counts the
    text too and the two then disagree. An **aliased import** escapes both, since
    neither the text nor the name matches. So does a summary built in a helper
    and passed in as a variable: the interpolation happens somewhere this does
    not look, and the variable itself reads as a bare name. The last is why
    :data:`_THIS_PACKAGES_OWN` is a table of *reasons* and not a list of names.
    """
    found: list[tuple[str, str]] = []
    for path, call in _refusal_calls():
        summary = _summary_of(call)
        if summary is None:
            continue
        for piece in ast.walk(summary):
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
            if line.lstrip().startswith("class ReviewIngestRefusedError("):
                definitions += 1
            else:
                mentions += 1

    assert definitions == 1, (
        f"the refusal class is defined {definitions} times, so the subtraction below "
        "is measuring something other than what it was written for"
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
