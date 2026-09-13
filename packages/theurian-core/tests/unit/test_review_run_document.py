"""What ``theurian review ingest`` publishes about the pull requests it skipped (#656).

``cli/review_commands._payload`` is the one place a run becomes a document, and
until #656 its ``skipped`` entries were ``FetchRefusal.describe()`` strings --
grade and summary, no cure. :data:`~theurian.domain.review_ingest.REMEDIES`
records one cure per grade and a ``FetchRefusal`` then carried a copy of the one
its envelope held, so the cure existed, was correct, and reached nobody: an
operator whose pull request was skipped ``limit-exceeded`` read what was refused
and never what to do about it. #597 had just rewritten that cure with a per-record
arm addressed to exactly this audience.

**Grade-keyed rather than per item**, and these tests are where that shape is
held. The remedy is grade-constant by the domain's own design -- looked up, never
passed in -- so a copy per skipped pull request would publish one string many
times: ``limit-exceeded``'s cure alone is over a thousand characters. One entry
per grade publishes it at its natural cardinality, and the entries stay a mapping
so a caller reads ``skippedRemedies[grade]`` beside the ``skipped`` line that
names the grade.

**And the value is looked up at the publication site rather than carried there**
(round two). ``FetchRefusal`` held a ``remedy`` field, ``_payload`` published it,
and the offered closure was ``RefusalEnvelope``'s remedy invariant -- which
reaches the construction shapes that run ``__post_init__`` and not
``object.__setattr__``, a subclass, or a copy on a type that is not an envelope.
Round two planted the surviving shape: ``if exc.envelope.detail: skip =
replace(skip, remedy=f"{skip.remedy} gh said: {exc.envelope.detail}")`` in
``_fetch``, invisible because every canned refusal in the suite carried
``detail=""`` while production ones carry a spawned child's stderr. So the field
is gone and ``_payload`` indexes the table:
:func:`test_a_composed_cure_planted_past_the_invariant_still_publishes_the_table_row`
is what goes RED if the value is ever read off an object again, and :func:`_skip`
now carries a detail by default so no detail-gated arm can hide from this file.

Unit rather than integration: every claim here is about the function that builds
the document, driven with a real :class:`ReviewIngestReport` rather than a
stand-in, the way ``test_review_ingest_changelog_claims.py`` drives the same
function. ``tests/integration/test_review_ingest_cli.py`` is where the document is
read back off the shipped command, on both channels.
"""

from __future__ import annotations

import ast
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Final

import pytest

import theurian
from theurian.application.review_ingest_service import FetchRefusal, ReviewIngestReport
from theurian.cli.commands import _emit
from theurian.cli.review_commands import _payload
from theurian.domain.review_ingest import REMEDIES, RefusalGrade, ReviewIngestRefusedError

pytestmark = pytest.mark.unit

REPOSITORY: Final = "acme/order-service"

#: The package this file walks for envelope constructions, taken off the imported
#: module rather than composed from ``__file__`` and ``..`` -- the tests may be run
#: against an installed package, and a relative walk would then read a source tree
#: that is not the one under test.
_SOURCE_ROOT: Final = Path(next(iter(theurian.__path__)))

#: The published key this file is about. Spelled once, so a rename moves every
#: assertion below together rather than leaving some of them asserting about a key
#: the document no longer has.
_CURES: Final = "skippedRemedies"

#: The type whose ``summary`` the ``skipped`` lines publish, spelled as the string
#: the AST walk matches rather than taken off the imported class.
#: ``FetchRefusal.__name__`` would follow a rename silently and leave the walk
#: looking for whatever the class is called now, which is the one outcome the
#: walk's fail-closed guard exists to report as a failure a human classifies.
_FETCH_REFUSAL: Final = "FetchRefusal"

#: Child output a canned refusal carries, shaped like what ``gh`` writes to stderr
#: and distinctive enough to search a whole document for.
#:
#: **Non-empty on purpose, and that is the round-two lesson.** The surviving
#: composition was gated on ``if exc.envelope.detail`` and every canned refusal in
#: the suite passed ``detail=""``, so the arm was dead in every test and live in
#: production -- ``infrastructure/github/review_provider.py``'s two
#: ``TOOL_FAILED`` raises both pass ``detail=outcome.stderr``. A default detail
#: here means a detail-gated arm runs whenever this file runs.
_CHILD_STDERR: Final = "gh: ssh-rsa AAAAB3NzaC1yc2EAAAA-canary"


def _skip(number: int, grade: RefusalGrade, *, detail: str = _CHILD_STDERR) -> FetchRefusal:
    """One skipped pull request, built the way the ingest run builds one.

    Through :class:`ReviewIngestRefusedError` rather than by constructing a
    :class:`~theurian.domain.review_ingest.RefusalEnvelope` here, because that is
    the only construction that *looks the remedy up* -- the domain's rule is that a
    remedy is never passed in, and a test that passed one could pin a cure the
    shipped table does not hold.

    ``detail`` defaults to :data:`_CHILD_STDERR` rather than to ``""``, so every
    report this file composes carries the field that makes a detail-gated
    composition live. It reaches no assertion by itself; what it buys is that an
    arm reading it cannot be dead here while it is live in a real run.
    """
    refused = ReviewIngestRefusedError(
        grade, f"Pull request {REPOSITORY}#{number} was refused.", detail=detail
    )
    return FetchRefusal.of(REPOSITORY, number, refused.envelope)


def _published_cures(document: Mapping[str, object]) -> dict[str, str]:
    """The ``skippedRemedies`` mapping, narrowed once for every assertion below.

    ``_payload`` is annotated ``dict[str, object]`` because the document is
    heterogeneous, so an assertion that indexed this field directly would be
    indexing an ``object``. The two checks here are part of the claim rather than a
    cast to get past the type checker: the published shape is a mapping of grade to
    cure, and a value of any other shape fails here naming what arrived instead.
    """
    published = document[_CURES]
    assert isinstance(published, dict), (
        f"`{_CURES}` is published as a {type(published).__name__}, not a mapping of "
        "grade to cure, so a caller cannot look a cure up by the grade beside it"
    )
    wrong = {key: value for key, value in published.items() if not isinstance(value, str)}
    assert not wrong, f"`{_CURES}` carries a non-string cure: {wrong}"
    return published


def _published_skips(document: Mapping[str, object]) -> list[str]:
    """The ``skipped`` lines, narrowed for the join assertion, the same way."""
    published = document["skipped"]
    assert isinstance(published, list), (
        f"`skipped` is published as a {type(published).__name__}, not a list of lines"
    )
    return published


def _report(*skipped: FetchRefusal) -> ReviewIngestReport:
    """A run that landed nothing and skipped exactly what it was given."""
    return ReviewIngestReport(
        repository=REPOSITORY,
        policy="block",
        redacted=False,
        secrets_warned=False,
        pull_requests=0,
        review_submissions=0,
        review_threads=0,
        new=0,
        updated=0,
        kept=0,
        refused=(),
        findings=(),
        skipped=skipped,
    )


def test_a_skipped_pull_request_publishes_the_cure_recorded_for_its_grade() -> None:
    """#656: the recorded cure reaches a published surface at last.

    The whole of the defect was that it did not. ``skipped`` named the pull
    request and its grade, ``FetchRefusal`` held the remedy, and no document, no
    exit code and no stderr line carried it -- the run-ending raise was the only
    path on which ``limit-exceeded``'s cure ever reached an operator, and a
    *skipped* pull request never takes that path.

    Asserted as equality against :data:`REMEDIES` rather than as a substring
    check, because the delivery being lossy is as much a defect as it being
    absent: a truncated or summarised cure reads as a cure and sends the reader
    off with part of the instructions.
    """
    document = _payload(_report(_skip(42, RefusalGrade.LIMIT_EXCEEDED)))

    assert _published_cures(document) == {
        RefusalGrade.LIMIT_EXCEEDED.value: REMEDIES[RefusalGrade.LIMIT_EXCEEDED]
    }


def test_a_run_that_skipped_nothing_publishes_an_empty_mapping_and_no_cure_text() -> None:
    """The key is part of the shape, not something that appears on trouble.

    Always present, so a caller scripting ``--json | jq '.skippedRemedies'`` reads
    a mapping on every run rather than ``null`` on the good ones -- the discipline
    ``secretFindings`` already holds one document over.

    The second half is the one a shape assertion misses: an empty mapping is worth
    nothing if the cure text arrived somewhere else in the document anyway. A run
    that skipped nothing has no audience for any of these cures, so none of them
    may be in it -- checked over the whole rendered document and over every grade
    in the table, rather than over the one grade the tests above happen to use.
    """
    document = _payload(_report())

    assert _published_cures(document) == {}

    rendered = json.dumps(document)
    leaked = sorted(grade.value for grade in RefusalGrade if REMEDIES[grade][:60] in rendered)
    assert not leaked, (
        f"a run that skipped nothing published the cure for {leaked} anyway, so the "
        "empty mapping above is not the whole claim: something else in this document "
        "carries cure text."
    )


def test_two_pull_requests_skipped_for_one_reason_publish_that_cure_once() -> None:
    """The reason the mapping is keyed on the grade rather than on the item.

    ``REMEDIES`` is a lookup by grade -- the domain's docstring says the remedy is
    "looked up, never passed in", and ``RefusalEnvelope``'s construction refuses an
    empty one -- so two pull requests refused for the same reason carry the *same*
    string. Publishing it per item would put a cure of over a thousand characters
    into the document once per skipped pull request, and a run may skip as many as
    its window holds.

    The count is asserted over the rendered document rather than over the mapping,
    because the mapping cannot repeat a key by construction: what this rules out is
    a *second* copy elsewhere, which is exactly what a per-item field would have
    been.
    """
    two = _report(
        _skip(42, RefusalGrade.LIMIT_EXCEEDED),
        _skip(41, RefusalGrade.LIMIT_EXCEEDED),
    )

    document = _payload(two)

    assert len(_published_skips(document)) == 2, "both pull requests must still be named"
    assert _published_cures(document) == {
        RefusalGrade.LIMIT_EXCEEDED.value: REMEDIES[RefusalGrade.LIMIT_EXCEEDED]
    }
    rendered = json.dumps(document)
    assert rendered.count(REMEDIES[RefusalGrade.LIMIT_EXCEEDED][:60]) == 1, (
        "the cure appears more than once in the document, which is the repetition the "
        "grade-keyed shape exists to avoid"
    )


def test_three_skips_for_two_reasons_publish_one_cure_per_distinct_grade(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Two reasons, two cures, and the caller finds each one by looking its grade up.

    ``ReviewIngestReport.skipped`` is ordered -- the listing's refusals first, then
    the fetches' -- and this test used to pin that order onto the mapping,
    asserting ``list(published)`` against the report's own sequence under a name
    that said "in first seen order". Two things were wrong with that. The mapping
    ``_payload`` returns is not a layer any caller reads, and the layer that *is*
    does not carry the order: ``_emit`` serialises with ``sort_keys=True``, so the
    same document these three skips produce publishes ``limit-exceeded`` first on
    the ``--json`` channel and ``tool-failed`` first on the human one. A caller
    scripting on position would have read the mapping the wrong way round with
    this test green.

    So the claim is the one the shape really supports -- a cure per *distinct*
    grade, each the recorded row, the repeat collapsed -- and it is asserted at
    the emitted layer as a **set**, which is the property both channels share.
    """
    document = _payload(
        _report(
            _skip(42, RefusalGrade.TOOL_FAILED),
            _skip(41, RefusalGrade.LIMIT_EXCEEDED),
            _skip(40, RefusalGrade.TOOL_FAILED),
        )
    )

    published = _published_cures(document)

    assert published == {
        RefusalGrade.TOOL_FAILED.value: REMEDIES[RefusalGrade.TOOL_FAILED],
        RefusalGrade.LIMIT_EXCEEDED.value: REMEDIES[RefusalGrade.LIMIT_EXCEEDED],
    }

    # The emitted layer, through the shipped emitter rather than a second spelling
    # of its arguments: what a `--json` caller receives is `_emit`'s bytes, and the
    # key set is what survives its sort.
    _emit(document, as_json=True)
    emitted = json.loads(capsys.readouterr().out)
    assert isinstance(emitted[_CURES], dict)
    assert set(emitted[_CURES]) == {
        RefusalGrade.TOOL_FAILED.value,
        RefusalGrade.LIMIT_EXCEEDED.value,
    }, (
        "the published document carries a different set of grades than the mapping "
        f"`_payload` built: {emitted[_CURES]}"
    )
    assert list(emitted[_CURES]) == sorted(emitted[_CURES]), (
        "the `--json` channel no longer publishes these keys sorted, so `_payload`'s "
        "docstring is describing a channel that has moved -- re-read `_emit` and say "
        "what it does now instead"
    )


def test_only_the_grades_this_run_met_are_published_never_the_whole_table() -> None:
    """Which rows reach the field, which is the half a value assertion cannot make.

    The document's audience is the operator of *this* run, and the table holds a
    cure for every grade the provider can refuse with -- most of them about
    repositories and tooling this run never touched. Publishing the table would
    hand a caller instructions for faults that did not happen, and would make the
    field say nothing about the run at all.

    Asserted as a strict subset plus an equality with the run's own grades, so it
    reddens both for a row that should not be there and for a run whose grade is
    missing.
    """
    skips = (_skip(42, RefusalGrade.LIMIT_EXCEEDED), _skip(41, RefusalGrade.TOOL_FAILED))

    published = _published_cures(_payload(_report(*skips)))

    assert set(published) == {skip.grade.value for skip in skips}
    assert set(published) < {grade.value for grade in RefusalGrade}, (
        "this run met two grades and the document published every grade in the table"
    )


def test_a_skipped_entry_and_its_cure_are_joined_by_the_grade_string() -> None:
    """The join a reader has to make, and the only thing that makes the mapping usable.

    ``skipped`` is a list of rendered lines and ``skippedRemedies`` is keyed on the
    grade; a caller pairs them by reading the grade out of the line. That works
    only while the line spells the grade exactly as the key does -- both come from
    ``RefusalGrade.value`` today, and a describe that started printing a prettier
    label would break the pairing while every other assertion in this file stayed
    green.
    """
    skips = (_skip(42, RefusalGrade.LIMIT_EXCEEDED), _skip(41, RefusalGrade.TOOL_FAILED))

    document = _payload(_report(*skips))

    published = _published_cures(document)

    for skip, line in zip(skips, _published_skips(document), strict=True):
        assert skip.grade.value in line, (
            f"the published line for {skip.identity.pull_request_number} does not spell "
            f"the grade that keys its cure, so a caller cannot pair the two: {line!r}"
        )
        assert published[skip.grade.value]


def _refusal_envelope_remedy_arguments() -> list[tuple[str, int, str]]:
    """Every ``RefusalEnvelope(...)`` in ``src/``, with how its ``remedy`` is built.

    Walked over the whole package's AST rather than over the one module that has
    such a call today, because the population this field's disclosure claim rests on
    is *every* construction anywhere -- an adapter that built one in
    ``infrastructure/github/`` would be outside a scan of the domain module and
    would still reach ``skippedRemedies``.

    The third element is the shape of the ``remedy`` argument: ``REMEDIES[...]``
    for a table lookup, ``<missing>`` when the call passes none, and
    ``ast.dump``'s rendering of anything else -- which is the case that fails the
    assertion, with the expression printed.
    """
    found: list[tuple[str, int, str]] = []
    for module in sorted(_SOURCE_ROOT.rglob("*.py")):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "RefusalEnvelope"
            ):
                continue
            passed = [keyword for keyword in node.keywords if keyword.arg == "remedy"]
            if not passed:
                shape = "<missing>"
            else:
                argument = passed[0].value
                looked_up = (
                    isinstance(argument, ast.Subscript)
                    and isinstance(argument.value, ast.Name)
                    and argument.value.id == "REMEDIES"
                )
                shape = "REMEDIES[...]" if looked_up else ast.dump(argument)
            found.append((module.name, node.lineno, shape))
    return found


def test_every_published_cure_is_a_row_of_the_recorded_table_and_never_a_passed_string() -> None:
    """A source-hygiene sweep, and **not** what closes the run document's key.

    What it holds is the *other* publication of a remedy. A run-ending refusal
    leaves ``cli/review_commands`` through ``_fail(str(exc), remedy=exc.remedy)``,
    which puts the envelope's own field on stderr, so a cure composed at a
    construction site would be fetched text in a refusal document. This walk keeps
    those sites readable -- a reviewer sees the lookup in the diff rather than
    inferring it from a raise three layers down.

    **It was offered as the run document's closure twice and is not that.** It
    reads constructions **by name** -- an ``ast.Call`` whose func is
    ``RefusalEnvelope`` -- and a construction need not spell the name: round one
    planted ``replace(exc.envelope, remedy=f"...{detail}")`` in the ``gh``
    provider and the whole suite stayed green. The type's invariant
    (``tests/unit/test_review_ingest_refusals.py::
    test_an_envelope_refuses_a_remedy_that_is_not_the_row_recorded_for_its_grade``)
    covers ``replace`` and the named call and stops at ``object.__setattr__``. What
    closes ``skippedRemedies`` is that it no longer reads any of this: the value is
    ``REMEDIES`` indexed at the publication site, which
    :func:`test_a_composed_cure_planted_past_the_invariant_still_publishes_the_table_row`
    holds.
    """
    sites = _refusal_envelope_remedy_arguments()

    assert sites, (
        "no `RefusalEnvelope(...)` construction was found at all, so this assertion "
        "holds vacuously. The class has been renamed or the envelope is built some "
        "other way; follow it here before trusting a green result."
    )
    passed_in = [site for site in sites if site[2] != "REMEDIES[...]"]
    assert not passed_in, (
        f"a refusal envelope is built with a remedy that is not a `REMEDIES` row: "
        f"{passed_in}.\n\n"
        "`skippedRemedies` publishes that string on stdout, so a remedy composed from "
        "anything a provider answered would put fetched text into the run document. "
        "Look the cure up by grade -- the domain's rule is that a remedy is never "
        "passed in -- or give this key its own screening before the change lands."
    )


def test_every_cure_a_composed_document_publishes_is_a_row_of_the_table() -> None:
    """The same premise read off the document, which is where a caller meets it.

    The check above is about how an envelope is *built*. This one asks the published
    mapping, over a run that met every grade, whether every value in it is a string
    this repository wrote: no AST, and no knowledge of which module builds what.

    **What it does not reach is worth saying, because the obvious reading is
    wrong.** The report it asks about is composed here, from :func:`_skip`, so a
    provider that composed a cure of its own is invisible to it. Measured at round
    one: with ``replace(exc.envelope, remedy=f"...{detail}")`` planted in the ``gh``
    provider and the type's invariant removed, this file and
    ``test_gh_review_provider.py`` ran green together (129 passed, in a throwaway
    clone). So this is a ratchet over the document's *shape* -- a future field that
    summarised or templated a cure reddens here -- and not a containment argument.
    The one below is the containment argument.

    **And the comparison is against the table, so the table is what has to be
    unable to move.** ``set(REMEDIES.values())`` is both sides of this assertion:
    a write to the table shifts the expected answer along with the published one,
    and this reads green. Two things stop that, neither of them here. The table is
    a ``MappingProxyType`` annotated ``Final``, so the item write raises
    ``TypeError`` and the rebinding is refused by ``mypy`` -- measured, both. And
    what holds the table's *text* without reading its values is
    ``tests/unit/test_review_ingest_refusals.py::
    test_every_recorded_remedy_is_a_plain_literal_with_nothing_interpolated``,
    which reads the rows out of the syntax tree: a row turned into an f-string
    reddens exactly there and nowhere else, this file included.

    A subset rather than an equality, because a run meets some grades and not
    others; the equality on *which* grades appear is
    :func:`test_only_the_grades_this_run_met_are_published_never_the_whole_table`'s.
    """
    skips = tuple(_skip(40 + offset, grade) for offset, grade in enumerate(RefusalGrade))

    published = _published_cures(_payload(_report(*skips)))

    assert published, "the composed run published no cure at all, so this holds vacuously"
    assert set(published.values()) <= set(REMEDIES.values()), (
        "the run document published a cure that is not a row of `REMEDIES`, so "
        "`skippedRemedies` is carrying a string this repository did not write: "
        f"{sorted(set(published.values()) - set(REMEDIES.values()))}"
    )


def test_a_composed_cure_planted_past_the_invariant_still_publishes_the_table_row() -> None:
    """The containment: the published cure is a lookup, not a value that travelled.

    Round two's finding was that the closure for this key was
    ``RefusalEnvelope``'s remedy invariant, and an invariant on one type cannot
    close a field that is *copied* out of it -- ``FetchRefusal`` took the string as
    an ordinary ``str``, ``_payload`` published that copy, and the composition the
    reviewer planted was gated on a ``detail`` no canned refusal carried.

    So the plant here goes past the invariant on purpose. ``object.__setattr__``
    writes a frozen field without re-running ``__post_init__``, which is the one
    shape the invariant provably does not see -- and it is the *strongest* plant
    available: anything an adapter could do to an envelope leaves the envelope in
    this state or a weaker one. The envelope then carries a cure with a spawned
    child's stderr in it, is handed to :meth:`FetchRefusal.of`, and the document is
    asked what it published.

    RED when ``_payload`` reads a remedy off any object on the way -- which is the
    shipped code of two rounds ago, and the shape a future refactor would restore
    by "avoiding the extra lookup".
    """
    refused = ReviewIngestRefusedError(
        RefusalGrade.TOOL_FAILED, f"Pull request {REPOSITORY}#42 was refused.", detail=_CHILD_STDERR
    )
    composed = f"{REMEDIES[RefusalGrade.TOOL_FAILED]} gh said: {_CHILD_STDERR}"
    object.__setattr__(refused.envelope, "remedy", composed)

    assert refused.envelope.remedy == composed, (
        "the plant did not land, so this test proves nothing about what survives it. "
        "`RefusalEnvelope` is a frozen slots dataclass and `object.__setattr__` is how "
        "a write past `__post_init__` is spelled; if that has stopped working, find the "
        "shape that replaces it before trusting a green result."
    )

    document = _payload(_report(FetchRefusal.of(REPOSITORY, 42, refused.envelope)))

    assert _published_cures(document) == {
        RefusalGrade.TOOL_FAILED.value: REMEDIES[RefusalGrade.TOOL_FAILED]
    }, "the planted cure reached the published document, so this key reads a carried value"
    assert _CHILD_STDERR not in json.dumps(document), (
        f"the child's stderr is somewhere in the run document: {json.dumps(document)!r}. "
        "`detail` is the field that carries what a spawned `gh` wrote and no published "
        "key may be composed from it."
    )


def _fetch_refusal_constructions() -> list[str]:
    """Every ``FetchRefusal(...)`` call in ``src/``, by path relative to the package.

    The companion population to :func:`_refusal_envelope_remedy_arguments`, and its
    subject is now the ``summary``: the ``skipped`` lines publish
    ``FetchRefusal.summary``, and what bounds that string is
    :meth:`~theurian.domain.review_ingest.RefusalEnvelope.__post_init__`'s cut at
    ``MAX_REFUSAL_SUMMARY_CHARS``. This type reaches that bound only by being built
    through :meth:`FetchRefusal.of`, which copies an already-constructed envelope's
    value. A call that built one directly would take ``summary`` as an ordinary
    string and put it in the run document uncut.

    Relative to the package root rather than by basename, because two modules may
    share a name and a bare ``review_provider.py`` does not say which tree it is in.

    Matches the bare name and the attribute spelling
    (``review_ingest_service.FetchRefusal(...)``), because either is a
    construction. ``of``'s own body is invisible to this walk by construction: it
    calls ``cls(...)``.
    """
    found: list[str] = []
    for module in sorted(_SOURCE_ROOT.rglob("*.py")):
        for node in ast.walk(ast.parse(module.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            named = (isinstance(func, ast.Name) and func.id == _FETCH_REFUSAL) or (
                isinstance(func, ast.Attribute) and func.attr == _FETCH_REFUSAL
            )
            if named:
                found.append(module.relative_to(_SOURCE_ROOT).as_posix())
    return found


def test_nothing_builds_a_fetch_refusal_except_the_constructor_that_copies_an_envelope() -> None:
    """Why a published ``skipped`` line carries a bounded summary and not a free string.

    :class:`FetchRefusal` is a plain frozen dataclass whose ``summary`` is a ``str``
    with no validation of its own -- the cut that bounds it lives on
    :class:`~theurian.domain.review_ingest.RefusalEnvelope`, and this type reaches
    it only by being built through :meth:`FetchRefusal.of`, which copies the fields
    off an already-constructed envelope. A direct construction anywhere would put
    an uncut summary in the run document.

    **Re-aimed rather than retired** (round two). It was written about the
    ``remedy`` field, which no longer exists: ``_payload`` indexes ``REMEDIES``
    itself, so a direct construction can no longer decide what cure is published.
    It can still decide what ``describe()`` prints, and that string is published
    under ``skipped`` -- so the walk keeps a subject.

    **Fail-closed**: the class has to be found in the walked source and ``of`` has
    to be called from it, or the emptiness below is a rename rather than a property.
    """
    service = _SOURCE_ROOT / "application" / "review_ingest_service.py"
    source = service.read_text(encoding="utf-8")

    assert f"class {_FETCH_REFUSAL}:" in source, (
        f"`{_FETCH_REFUSAL}` is not defined in {service.name} any more, so the walk "
        "below is looking for a name nothing builds. Follow the type to its new home "
        "before trusting a green result."
    )
    assert f"{_FETCH_REFUSAL}.of(" in source, (
        f"nothing in {service.name} calls `{_FETCH_REFUSAL}.of(...)`, so the report's "
        "skips are being built some other way and this test is asserting about a "
        "constructor nobody uses."
    )

    direct = _fetch_refusal_constructions()

    assert not direct, (
        f"`{_FETCH_REFUSAL}` is constructed directly in {direct}, bypassing `of` -- "
        "which is the only thing that ties its `summary` to an envelope's, and so to "
        "the cut `RefusalEnvelope.__post_init__` applies. `describe()` puts that field "
        "in the run document, so a direct construction can publish an unbounded "
        "sentence. Build it from an envelope, or give this type a bound of its own."
    )
