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

Marked ``unit``; nothing here opens a file.
"""

from __future__ import annotations

import inspect
import re
from typing import Any, Final

import pytest

from theurian.infrastructure.review_evidence import cures

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

#: One probe value per parameter name a cure takes. Keyed by name rather than by
#: position so a cure taking ``relative`` gets the same probe wherever it sits in
#: the signature, and so an unfamiliar parameter is a red test rather than a
#: silent skip.
_PROBES: Final[dict[str, object]] = {
    "relative": "sha256-abc/pull-request/42.json",
    "source_uri": "https://github.com/acme/order-service/pull/42",
    "on_disk": "Pull-Request",
    "derived": "pull-request",
    "landed": 3,
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


def _call(function: Any) -> str:
    """One cure's text, built from the probe table."""
    rendered = function(
        **{
            parameter: _PROBES[parameter]
            for parameter in inspect.signature(function).parameters
            if parameter in _PROBES
        }
    )
    assert isinstance(rendered, str)
    return rendered


def _every_cure() -> list[tuple[str, str]]:
    """Every cure this module publishes, constant or rendered, as ``(name, text)``."""
    return _cure_constants() + [(name, _call(function)) for name, function in _cure_callables()]


@pytest.mark.parametrize(("name", "text"), _every_cure(), ids=[name for name, _ in _every_cure()])
def test_every_cure_names_a_command_and_an_artefact(name: str, text: str) -> None:
    """RED means a cure sends the reader back into the source.

    The same shape ``test_review_ingest_refusals.py`` holds the graded remedies
    to, applied to the population that looks its cures up by call rather than by
    grade: a truthy string is not a remedy, and a suite asserting only that one
    is non-empty is how ``"Something went wrong."`` ships.
    """
    assert _COMMAND.search(text) is not None, f"{name} names no command a reader can run:\n{text}"
    assert _ARTEFACT.search(text) is not None, f"{name} names no artefact to act on:\n{text}"


@pytest.mark.parametrize(("name", "text"), _every_cure(), ids=[name for name, _ in _every_cure()])
def test_a_cure_that_offers_a_removal_says_what_it_costs(name: str, text: str) -> None:
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


@pytest.mark.parametrize(("name", "text"), _every_cure(), ids=[name for name, _ in _every_cure()])
def test_no_cure_publishes_a_path_spelled_from_the_root(name: str, text: str) -> None:
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
