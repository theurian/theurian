"""The review-evidence cures, held over the module rather than over a list.

``infrastructure/review_evidence/cures.py`` states two rules for every string it
publishes -- a path is named relative to the review directory, and no cure offers
to delete a landed record -- and a rule stated once for a module has to be
checked over the module or it is a sentence about the members that existed when
it was written. So the population here is **reflected**: every public constant
and every public callable of that module, discovered at run time, with a probe
argument supplied per parameter *name*. A cure added later inherits every check,
and one that takes a parameter this file has never seen reddens in
:func:`test_every_cure_parameter_has_a_probe` rather than being skipped.

The module carries one **safety predicate** besides those two rules: a cure that
says a removal costs nothing may say it only where the shape holds no data of
its own. :func:`test_no_cure_claims_a_costless_removal_outside_the_shape_guard`
is where that is enforced, over the same reflected population and over every
shape the prober can answer, because the seam table below is now descriptive:
the store hands a shape to a cure and the cure routes it, so a table of
(seam, shape) pairs records where a reader ends up rather than deciding it.

Marked ``unit``; nothing here opens a file.
"""

from __future__ import annotations

import inspect
import re
import stat
from typing import Any, Final

import pytest

from theurian.infrastructure.review_evidence import cures
from theurian.security.regular_file import shape_that_is_not_a_regular_file

pytestmark = pytest.mark.unit

#: A backticked command whose first word is a program this project tells people
#: to run, with at least one argument after it -- the shape
#: ``test_review_ingest_refusals.py`` already holds the graded remedies to. A
#: cure that names no command sends the reader back into the source.
_COMMAND: Final = re.compile(r"`(gh|git|ls|theurian)\s+[^`]+`")

#: A token carrying a path, a dotted key or a host: ``.theurian/review/``,
#: ``ADR-0030``, ``github.com``.
_ARTEFACT: Final = re.compile(r"[\w~-]*[./][\w~./-]*[\w/]")

#: What a cure says when it offers to remove something. Present tense and
#: imperative, because that is how a cure is written.
_OFFERS_REMOVAL: Final = re.compile(r"\b(remove|delete|rm)\b", re.IGNORECASE)

#: A path spelled from the filesystem root. The lookbehind is what keeps
#: ``.theurian/review/x`` and ``https://github.com`` out of it: what this looks
#: for is a ``/`` that opens a path component rather than one that separates two.
_ABSOLUTE_PATH: Final = re.compile(r"(?<![\w.~/:-])/\w")

#: What a cure that offers a removal must *also* say: either that nothing is
#: lost, or that the thing removed is not the record. The rule this enforces is
#: the module's second one -- review evidence has no rebuild, so an unqualified
#: "remove it and run the ingestion again" over a landed record is data loss
#: published as advice.
_ACCOUNTS_FOR_THE_LOSS: Final = re.compile(
    r"(loses nothing|holds no review evidence|Nothing was written|Do not simply remove it"
    r"|Do not delete it|data loss)",
    re.IGNORECASE,
)

#: What a cure that offers a removal may **not** claim about a shape holding
#: other names. True of a pipe, a socket and a device node; false of a directory,
#: whose entries may be an operator's own files.
#:
#: **Three spellings rather than two, because a cure says it in its own words.**
#: ``planted_artefact_cure`` says "holds no bytes of its own, so removing it
#: loses nothing"; ``planted_temporary_cure`` says "it holds no review evidence
#: and removing it loses nothing". This pattern is what the safety predicate is
#: *derived from* -- no row below carries a hand-set "may claim" flag -- so a
#: spelling it cannot see is a claim nothing here guards.
_CLAIMS_NOTHING_IS_LOST: Final = re.compile(
    r"loses nothing|holds no bytes|holds no review evidence", re.IGNORECASE
)

#: Every shape ``security/regular_file.shape_that_is_not_a_regular_file`` can
#: answer, recomputed by running it over ``stat``'s **own** file-type constants
#: rather than by listing the shapes somebody has met.
#:
#: The hand-extended list is what this replaces. ``_PROBES`` carried one shape --
#: ``"a named pipe (FIFO)"`` -- so the walked population never rendered
#: ``planted_artefact_cure`` for ``"a directory"``, and the sentence "which holds
#: no bytes, so removing it loses nothing" shipped over a directory holding an
#: operator's file. A shape added to that function now reddens
#: :func:`test_every_seam_and_shape_the_prober_can_answer_names_a_cure` until
#: somebody says which cure a reader gets for it.
#: ``isinstance(..., int)`` is not decoration: ``stat.S_IFMT`` shares the prefix
#: and is the *function* that extracts a type from a mode.
_SHAPES: Final[frozenset[str]] = frozenset(
    shape
    for name in dir(stat)
    if name.startswith("S_IF") and isinstance(mode := getattr(stat, name), int)
    for shape in [shape_that_is_not_a_regular_file(mode)]
    if shape is not None
)

#: The one shape in the prober's range that is a container of other names,
#: spelled here because a ``str`` is what the guard takes and pinned to the
#: prober's own answer by
#: :func:`test_the_guard_names_the_one_shape_that_holds_other_names`.
_A_CONTAINER_OF_OTHER_NAMES: Final = "a directory"

#: The cure each store seam calls. Both take a shape and route it themselves, so
#: these two are the whole of what a seam chooses; which sentence a reader ends
#: up with is :data:`_CURE_FOR_SEAM_AND_SHAPE`.
_ENTRY_CURE_FOR_SEAM: Final[dict[str, str]] = {
    "the record's own path": "planted_artefact_cure",
    "the record's temporary": "planted_temporary_cure",
}

#: Which cure a reader ends up with per seam and shape. **Descriptive, and that
#: is the change**: the boolean that used to sit beside each cell said whether
#: the cure might claim a costless removal, and a hand-set boolean beside a cell
#: is exactly how ``planted_temporary_cure`` stayed out of the map while
#: publishing "removing it loses nothing" over a directory. The claim is now
#: derived from the rendered text
#: (:func:`test_no_cure_claims_a_costless_removal_outside_the_shape_guard`), and
#: this table is checked by *reaching* each destination through its seam's entry
#: cure rather than by being believed.
#:
#: Keyed on the prober's range. The temporary seam also names ``"a symbolic
#: link"``, which that function never answers -- ``_planted_shape`` adds it from
#: the errno and from ``lstat`` -- so this table does not claim to cover it;
#: ``test_review_evidence_writing_temporary.py`` drives that one.
_CURE_FOR_SEAM_AND_SHAPE: Final[dict[tuple[str, str], str]] = {
    ("the record's own path", "a directory"): "occupied_directory_cure",
    ("the record's own path", "a named pipe (FIFO)"): "planted_artefact_cure",
    ("the record's own path", "a socket"): "planted_artefact_cure",
    ("the record's own path", "a character device"): "planted_artefact_cure",
    ("the record's own path", "a block device"): "planted_artefact_cure",
    ("the record's own path", "a special file"): "planted_artefact_cure",
    ("the record's temporary", "a directory"): "occupied_temporary_cure",
    ("the record's temporary", "a named pipe (FIFO)"): "planted_temporary_cure",
    ("the record's temporary", "a socket"): "planted_temporary_cure",
    ("the record's temporary", "a character device"): "planted_temporary_cure",
    ("the record's temporary", "a block device"): "planted_temporary_cure",
    ("the record's temporary", "a special file"): "planted_temporary_cure",
}

#: The cures that carry the shape guard inside them, and so are the only ones
#: allowed to render a costless claim at all. Each is held to *both* halves by
#: :func:`test_each_guarded_cure_withholds_its_costless_claim_over_a_container`:
#: it withholds the claim over a container, and it still makes it over a shape
#: that holds nothing -- an exemption for a cure that no longer claims anything
#: would silently cover whatever took its name next.
_GUARDED_COSTLESS_CURES: Final[frozenset[str]] = frozenset(
    {"planted_artefact_cure", "planted_temporary_cure"}
)

#: One probe value per parameter name a cure takes. Keyed by name rather than by
#: position so a cure taking ``relative`` gets the same probe wherever it sits in
#: the signature, and so an unfamiliar parameter is a red test rather than a
#: silent skip.
_PROBES: Final[dict[str, object]] = {
    "relative": "sha256-abc/pull-request/42.json",
    "source_uri": "https://github.com/acme/order-service/pull/42",
    "on_disk": "Pull-Request",
    "derived": "pull-request",
    "opened": "sha256-abc/pull-request/42.json.writing",
    "shape": "a named pipe (FIFO)",
}

#: The relative path probe, named separately because two tests assert about the
#: spelling it must appear under.
_RELATIVE: Final = "sha256-abc/pull-request/42.json"

#: The module's public names that are **not** cures, each with the reason. Both
#: build a *clause* another module's sentence embeds rather than a remedy a
#: reader acts on, so the command-and-artefact rule would be asking them for
#: something they are not for.
#:
#: A table rather than a filter, and it is itself checked
#: (:func:`test_every_exclusion_still_names_something`): an exclusion whose name
#: has been renamed away would otherwise silently widen into an exclusion of
#: nothing, and the cure it was meant to exclude would go unchecked.
_NOT_A_CURE: Final[dict[str, str]] = {
    "UNNAMED_REPOSITORY": (
        "a clause a refusal embeds -- ``<path>`, whose own repository could "
        "not be read, was listed ...` -- not a sentence a reader acts on"
    ),
    "repository_named_in": (
        "builds the same clause from a failing file's own bytes, so it names a "
        "repository rather than a cure"
    ),
}


def _public_names() -> list[str]:
    """Every name the cures module publishes, in a stable order."""
    return sorted(name for name in vars(cures) if not name.startswith("_"))


def _cure_constants() -> list[tuple[str, str]]:
    """Every public string constant that is a cure, as ``(name, text)``."""
    return [
        (name, value)
        for name in _public_names()
        if name not in _NOT_A_CURE and isinstance(value := getattr(cures, name), str)
        if name.isupper()
    ]


def _cure_callables() -> list[tuple[str, Any]]:
    """Every public cure defined *in* this module, as ``(name, function)``.

    ``__module__`` is the filter rather than ``callable()``: ``bounded_echo`` and
    ``json`` are imported into the module's namespace, and holding a cure's rules
    over an import would be a check about somebody else's code.
    """
    return [
        (name, value)
        for name in _public_names()
        if name not in _NOT_A_CURE and callable(value := getattr(cures, name))
        if getattr(value, "__module__", "") == cures.__name__
    ]


def test_every_exclusion_still_names_something() -> None:
    """RED means an exclusion outlived the member it excluded.

    :data:`_NOT_A_CURE` narrows a reflected population, which is the one thing a
    reflected population must not do silently: a stale entry is an exclusion that
    matches nothing today and would match the next member to take that name.
    """
    missing = [name for name in _NOT_A_CURE if not hasattr(cures, name)]

    assert not missing, (
        f"{missing} are excluded from the cure population and no longer exist. Remove "
        "the row, or correct it to the member's new name -- an exclusion nothing "
        "matches will silently exclude whatever takes that name next."
    )


def test_the_reflection_finds_a_population_at_all() -> None:
    """The can-fail companion: an empty reflection makes every row below vacuous.

    A structural check that finds nothing passes exactly as a correct one does.
    This asserts both halves are non-empty and that a named member of each is
    present, so a rename of the module or a move of the cures turns into a red
    test here rather than a clean report over nothing.
    """
    constants = dict(_cure_constants())
    callables = dict(_cure_callables())

    assert len(constants) >= 3, f"only {len(constants)} cure constants found: {sorted(constants)}"
    assert len(callables) >= 3, f"only {len(callables)} cure callables found: {sorted(callables)}"
    assert "UNREADABLE_CURE" in constants
    assert "planted_link_cure" in callables


def test_every_cure_parameter_has_a_probe() -> None:
    """RED means a cure was added whose arguments this file cannot supply.

    Without this the rows below would silently stop covering the new member:
    :func:`_call` would have nothing to pass and the cure would be skipped, which
    is the failure mode a reflected population exists to avoid.
    """
    unknown = [
        f"{name}({parameter})"
        for name, function in _cure_callables()
        for parameter in inspect.signature(function).parameters
        if parameter not in _PROBES
    ]

    assert not unknown, (
        f"{unknown} take a parameter this file has no probe for. Add one to `_PROBES` "
        "-- keyed by the parameter's name -- so the cure is covered by every check here."
    )


def _call(function: Any, **overrides: object) -> str:
    """One cure's text, built from the probe table with ``overrides`` applied."""
    arguments = {
        parameter: _PROBES[parameter]
        for parameter in inspect.signature(function).parameters
        if parameter in _PROBES
    }
    rendered = function(**{**arguments, **overrides})
    assert isinstance(rendered, str)
    return rendered


def _render(function: Any, shape: str) -> str:
    """One cure's text for ``shape``, or its only text when it takes no shape.

    ``_call`` applies its overrides blindly, so handing ``shape=`` to a cure
    written for a single shape -- ``occupied_directory_cure`` is one -- would be
    a ``TypeError`` rather than a rendering.
    """
    if "shape" in inspect.signature(function).parameters:
        return _call(function, shape=shape)
    return _call(function)


def _every_cure() -> list[tuple[str, str, str]]:
    """Every rendering this module publishes, as ``(label, cure name, text)``.

    A cure taking a ``shape`` is rendered **once per shape the prober can
    answer**, rather than once with whichever shape the probe table happened to
    carry: the two module rules are held per rendering, and the rendering is what
    an operator reads. Every shape rather than the ones a table maps to that
    cure, because the routing now lives *inside* the cure -- a lookup here would
    be the walk agreeing with the thing it is checking.

    The cure's own name travels beside the label so a row can ask which member
    produced a rendering; the label is what a shape-taking cure is identified by
    in the report, and it carries the shape.
    """
    rendered: list[tuple[str, str, str]] = []
    for name, function in _cure_callables():
        if "shape" not in inspect.signature(function).parameters:
            rendered.append((name, name, _call(function)))
            continue
        rendered += [
            (f"{name}[{shape}]", name, _call(function, shape=shape)) for shape in sorted(_SHAPES)
        ]
    return [(name, name, text) for name, text in _cure_constants()] + rendered


@pytest.mark.parametrize(
    ("name", "cure", "text"), _every_cure(), ids=[label for label, _, _ in _every_cure()]
)
def test_every_cure_names_a_command_and_an_artefact(name: str, cure: str, text: str) -> None:
    """RED means a cure sends the reader back into the source.

    The same shape ``test_review_ingest_refusals.py`` holds the graded remedies
    to, applied to the population that looks its cures up by call rather than by
    grade: a truthy string is not a remedy, and a suite asserting only that one
    is non-empty is how ``"Something went wrong."`` ships.
    """
    assert _COMMAND.search(text) is not None, f"{name} names no command a reader can run:\n{text}"
    assert _ARTEFACT.search(text) is not None, f"{name} names no artefact to act on:\n{text}"


@pytest.mark.parametrize(
    ("name", "cure", "text"), _every_cure(), ids=[label for label, _, _ in _every_cure()]
)
def test_a_cure_that_offers_a_removal_says_what_it_costs(name: str, cure: str, text: str) -> None:
    """RED means a cure tells an operator to delete something and does not say what.

    The module's second rule, and the one with teeth: review evidence is the
    source rather than derived state (ADR-0030 decision 3), so a refetch rebuilds
    nothing and "remove it and run the ingestion again" -- correct for every
    derived artefact ``security/no_follow.py`` covers -- is data loss here. A
    cure may still offer a removal; what it may not do is offer one silently.
    """
    if _OFFERS_REMOVAL.search(text) is None:
        return

    assert _ACCOUNTS_FOR_THE_LOSS.search(text) is not None, (
        f"{name} offers a removal and says nothing about what is lost:\n{text}\n\n"
        "Say that the artefact holds no evidence, that nothing was written through "
        "it, or that it must not simply be removed. Review evidence has no rebuild."
    )


@pytest.mark.parametrize(
    ("name", "cure", "text"), _every_cure(), ids=[label for label, _, _ in _every_cure()]
)
def test_no_cure_publishes_a_path_spelled_from_the_root(name: str, cure: str, text: str) -> None:
    """RED means a cure could carry the operator's machine layout into a paste.

    A remedy is text a caller may paste into an issue or a chat, and an absolute
    one carries their home directory with it -- the class GHSA-97q9 closed one
    surface over. Every cure here is handed a *relative* path and the caller keeps
    the absolute one, so a match means somebody reached for a value this module
    is deliberately not given.
    """
    found = _ABSOLUTE_PATH.search(text)

    assert found is None, (
        f"{name} publishes a path spelled from the filesystem root, at "
        f"{text[found.start() : found.start() + 40]!r}:\n{text}\n\n"
        "Name it relative to the review directory. The caller holds the absolute "
        "path and does not pass it, so a remedy that spells one is quoting the "
        "operator's machine layout into text they may paste elsewhere."
    )


@pytest.mark.parametrize(
    ("name", "function"), _cure_callables(), ids=[name for name, _ in _cure_callables()]
)
def test_a_cure_that_takes_a_path_spells_it_under_the_review_directory(
    name: str, function: Any
) -> None:
    """RED means a cure names a path a reader cannot locate.

    The other half of the rule above: the relative path is *spelled* under
    ``.theurian/review/`` rather than left bare, which is what tells the reader
    where to look without naming their home directory.
    """
    if "relative" not in inspect.signature(function).parameters:
        return
    text = _call(function)

    assert _RELATIVE in text, f"{name} was given a path and named none of it:\n{text}"
    assert f".theurian/review/{_RELATIVE}" in text, (
        f"{name} names `{_RELATIVE}` without the `.theurian/review/` prefix, so a "
        f"reader cannot tell where it sits:\n{text}"
    )


def test_the_shape_range_is_recomputed_and_reaches_more_than_one_member() -> None:
    """The can-fail companion for the derivation the two rows below range over.

    ``_SHAPES`` is computed by running the prober over ``stat``'s own file-type
    constants, so a shape added to it arrives here without anybody transcribing
    one. A derivation that quietly answered nothing -- or answered only the shape
    the probe table already carried -- would make the coverage row vacuous, which
    is exactly the state that let a directory reach the wrong cure.
    """
    assert "a directory" in _SHAPES, (
        "the prober no longer names a directory, so the split this file exists to hold "
        "has nothing to be about"
    )
    assert len(_SHAPES) >= 5, f"the prober answered only {sorted(_SHAPES)}"
    assert shape_that_is_not_a_regular_file(stat.S_IFREG) is None, (
        "a regular file is named as a wrong shape, so every row here is about the ordinary case"
    )


def test_the_guard_names_the_one_shape_that_holds_other_names() -> None:
    """The can-fail companion for the predicate every row below rests on.

    ``cures._shape_holds_other_names`` compares against the word the prober
    answers for ``stat.S_IFDIR`` rather than a literal, so a guard that stopped
    matching would be a guard that is off and every row below would pass. Both
    directions are asserted: the word is the one this file spells, and no other
    shape the store can hand a cure is treated as a container.
    """
    assert shape_that_is_not_a_regular_file(stat.S_IFDIR) == _A_CONTAINER_OF_OTHER_NAMES, (
        "the prober no longer answers the word this file spells, so the guard compares "
        "against something no seam produces"
    )
    assert cures._shape_holds_other_names(_A_CONTAINER_OF_OTHER_NAMES)
    held = sorted(shape for shape in _SHAPES if cures._shape_holds_other_names(shape))
    assert held == [_A_CONTAINER_OF_OTHER_NAMES], f"the guard now covers {held}"
    assert not cures._shape_holds_other_names("a symbolic link"), (
        "a symbolic link holds only itself, so removing one loses the link and not what "
        "it points at -- treating it as a container would withhold a true sentence"
    )


def test_every_seam_and_shape_the_prober_can_answer_names_a_cure() -> None:
    """RED means a shape reaches a store seam and nobody said what a reader gets.

    The population is recomputed and taken over **both** seams, so a seventh
    shape added to ``shape_that_is_not_a_regular_file`` lands here twice rather
    than silently inheriting a costless claim -- which is how ``"a directory"``
    inherited "removing it loses nothing" at the record's own path, and then
    again at the temporary after the first seam was fixed alone.
    """
    expected = {(seam, shape) for seam in _ENTRY_CURE_FOR_SEAM for shape in _SHAPES}

    assert set(_CURE_FOR_SEAM_AND_SHAPE) == expected, (
        f"{sorted(expected ^ set(_CURE_FOR_SEAM_AND_SHAPE))} are named on one side of the "
        "seam-and-shape table and not the other. Say which cure a reader ends up with "
        "there; whether it may claim the removal costs nothing is derived from the text."
    )
    for (seam, shape), cure in _CURE_FOR_SEAM_AND_SHAPE.items():
        assert hasattr(cures, cure), (
            f"{shape} at {seam} names a cure `{cure}` this module does not define"
        )


@pytest.mark.parametrize(
    ("seam", "shape"),
    sorted(_CURE_FOR_SEAM_AND_SHAPE),
    ids=[f"{seam}-{shape}" for seam, shape in sorted(_CURE_FOR_SEAM_AND_SHAPE)],
)
def test_each_seam_and_shape_reaches_the_cure_the_table_names(seam: str, shape: str) -> None:
    """RED means the descriptive table describes a route nothing takes.

    A table that is not decided by the code has to be *reached* by it or it is a
    comment: the seam's entry cure is rendered for the shape and compared against
    the destination cure's own rendering, so a routing change that stops going
    where this says reddens instead of leaving the table quietly wrong.
    """
    entry = getattr(cures, _ENTRY_CURE_FOR_SEAM[seam])
    destination = getattr(cures, _CURE_FOR_SEAM_AND_SHAPE[(seam, shape)])

    assert _render(entry, shape) == _render(destination, shape), (
        f"{_ENTRY_CURE_FOR_SEAM[seam]} does not answer "
        f"`{_CURE_FOR_SEAM_AND_SHAPE[(seam, shape)]}` for {shape} at {seam}:\n"
        f"{_render(entry, shape)}"
    )


@pytest.mark.parametrize(
    ("name", "cure", "text"), _every_cure(), ids=[label for label, _, _ in _every_cure()]
)
def test_no_cure_claims_a_costless_removal_outside_the_shape_guard(
    name: str, cure: str, text: str
) -> None:
    """RED means a cure can tell an operator that deleting their own files is free.

    **The safety predicate, and it is the enforcement the seam tables are not.**
    Measured twice, one seam apart: ``planted_artefact_cure``'s "which holds no
    bytes, so removing it loses nothing" was published over a directory holding a
    file somebody wrote, the store's ``_publish`` gained a ``S_ISDIR`` branch,
    and ``_temporary_refusal`` -- which had no such branch -- went on publishing
    ``planted_temporary_cure``'s "it holds no review evidence and removing it
    loses nothing" over the same plant at ``<record>.writing``.

    So the claim is allowed only from a cure that takes the shape and withholds
    it itself. A third cure that makes the claim reddens here whatever seam calls
    it and whatever a table says, which is what
    :func:`test_the_walk_reddens_on_a_cure_that_claims_without_the_guard` drives
    with a planted member.
    """
    claimed = _CLAIMS_NOTHING_IS_LOST.search(text)
    if claimed is None:
        return

    assert cure in _GUARDED_COSTLESS_CURES, (
        f"{name} says a removal costs nothing, at "
        f"{text[claimed.start() : claimed.start() + 60]!r}, and carries no shape guard:\n"
        f"{text}\n\n"
        f"Take the shape and route a container of other names to a move-style cure, as "
        f"{sorted(_GUARDED_COSTLESS_CURES)} do. A seam that never heard of the split "
        f"still gets it right that way; a guard at the seam does not."
    )


def test_the_walk_reddens_on_a_cure_that_claims_without_the_guard() -> None:
    """The positive control: the row above is not passing over nothing.

    A structural walk that found no claim passes exactly as a correct one does,
    and the claim's spelling is the part that drifts. A rendering carrying the
    sentence under a name outside the guarded pair is fed to the same predicate
    the row above applies, in each of the three spellings the pattern knows.
    """
    planted = [
        ("planted_third_cure", "planted_third_cure", f"Remove it -- {claim}.")
        for claim in (
            "removing it loses nothing",
            "it holds no bytes",
            "it holds no review evidence",
        )
    ]

    caught = [
        name
        for name, cure, text in planted
        if _CLAIMS_NOTHING_IS_LOST.search(text) is not None and cure not in _GUARDED_COSTLESS_CURES
    ]

    assert len(caught) == len(planted), (
        f"the predicate saw {len(caught)} of {len(planted)} planted claims, so a cure "
        f"spelling one of the others would pass the row above"
    )


@pytest.mark.parametrize(
    "cure", sorted(_GUARDED_COSTLESS_CURES), ids=sorted(_GUARDED_COSTLESS_CURES)
)
def test_each_guarded_cure_withholds_its_costless_claim_over_a_container(cure: str) -> None:
    """RED means an exemption covers a cure that does not carry the guard.

    Both halves, because either one alone is the failure mode. Over a container
    the cure must withhold the claim and print what is *inside* -- the question
    that decides whether removing it is safe. Over a shape that holds nothing it
    must still make it: an exemption for a cure that no longer claims anything is
    stale, and would silently cover whatever took that name next.
    """
    assert hasattr(cures, cure), (
        f"`{cure}` is exempted from the costless-claim row and no longer exists. Remove "
        f"the name, or correct it -- an exemption nothing matches will cover whatever "
        f"takes that name next."
    )
    function = getattr(cures, cure)
    over_a_container = _render(function, _A_CONTAINER_OF_OTHER_NAMES)
    over_a_pipe = _render(function, "a named pipe (FIFO)")

    assert _CLAIMS_NOTHING_IS_LOST.search(over_a_pipe) is not None, (
        f"{cure} no longer says what removing a named pipe costs, so its place among "
        f"{sorted(_GUARDED_COSTLESS_CURES)} is an exemption of nothing:\n{over_a_pipe}"
    )
    claimed = _CLAIMS_NOTHING_IS_LOST.search(over_a_container)
    assert claimed is None, (
        f"{cure} tells an operator that removing {_A_CONTAINER_OF_OTHER_NAMES} costs "
        f"nothing, at {over_a_container[claimed.start() : claimed.start() + 60]!r}:\n"
        f"{over_a_container}\n\n"
        f"A directory holds other names, and nothing at either seam can tell whose they "
        f"are."
    )
    assert "ls -la" in over_a_container, (
        f"{cure} does not print what is *inside* the artefact, which is the question "
        f"that decides whether removing it is safe:\n{over_a_container}"
    )


def test_a_repository_is_named_only_when_the_bytes_say_it() -> None:
    """The best-effort fragment, in both directions.

    Not a cure -- it builds the clause a refusal uses to say *which* repository a
    failing file is about -- but it lives in this module because the reason it
    exists is the same one: the directory is a hash, so a refusal naming only the
    path identifies the file without identifying what the file is about.
    """
    named = cures.repository_named_in(b'{"repository": "acme/order-service"}')

    assert "acme/order-service" in named

    for unreadable in (b"\xff\xfe", b"not json", b"[]", b'{"repository": "  "}', b"{}"):
        assert cures.repository_named_in(unreadable) == cures.UNNAMED_REPOSITORY, (
            f"{unreadable!r} produced a repository name out of bytes that name none"
        )
