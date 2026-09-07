"""The vector handed to ``gh``, and the constants that bound it (ADR-0030 clauses 2, 3, 5-8).

Structural rather than behavioural, and that division is the point: what a
spawned ``gh`` *does* is outside every instrument this suite has, so the
properties worth pinning are the ones visible on this side of the boundary --
which endpoint, which flags, which host, which variable names, and what a page
after the first is allowed to change.

The behavioural half -- that these vectors are the ones a real spawn receives --
is ``tests/integration/test_gh_review_provider.py``, which records the argv of a
stand-in child and compares it against what this file describes.
"""

from __future__ import annotations

import ast
import pathlib
from typing import Final

import pytest

import theurian
from theurian.infrastructure.github import gh_cli, limits, queries

pytestmark = pytest.mark.unit

GH_CLI_SOURCE: Final = pathlib.Path(gh_cli.__file__)

#: The package as *imported*, the reckoning ``test_network_call_sites.py`` uses:
#: a hand-built relative path can drift from the installed package and would then
#: scan a directory with no spawn in it whatever the source did.
SRC: Final = pathlib.Path(theurian.__file__).resolve().parent

#: The two names a module has to reach to make a request through this adapter:
#: the function every request leaves through, and the class that builds the
#: vectors and holds the probed binary.
#:
#: ``GhCli`` is the half a scan on ``run_bounded`` alone cannot see. A second
#: module constructing one spawns through the recorded site, calls the recorded
#: helper from inside ``gh_cli.py``, and names neither -- so it adds no entry to
#: ``PROCESS_SPAWN_SITES`` and no entry to a ``run_bounded`` scan, while being a
#: second path to GitHub.
_REACHES_GITHUB: Final[frozenset[str]] = frozenset({"GhCli", "run_bounded"})

#: Where each of those names may appear, as ``(module path under theurian/, the
#: name)``. Three entries: ``gh_cli.py`` declares both, and
#: ``review_provider.py`` is the one consumer -- it imports ``GhCli`` and
#: annotates with it, and reaches ``run_bounded`` only through it.
GITHUB_REACHING_SITES: Final[frozenset[tuple[str, str]]] = frozenset(
    {
        ("infrastructure/github/gh_cli.py", "GhCli"),
        ("infrastructure/github/gh_cli.py", "run_bounded"),
        ("infrastructure/github/review_provider.py", "GhCli"),
    }
)

#: The syntax nodes that *introduce* a name: a call or an annotation, a dotted
#: reach, an import, and the ``def``/``class`` that declares it.
#:
#: ``Constant`` is deliberately absent, and that is what keeps prose out of the
#: population: two modules name ``gh_cli.run_bounded`` in a docstring, and a scan
#: reading string constants would list both and have to be silenced.
_NAMING_NODES: Final = (
    ast.Name,
    ast.Attribute,
    ast.alias,
    ast.ClassDef,
    ast.FunctionDef,
    ast.AsyncFunctionDef,
)

#: What ``gh`` would be told to do if this adapter ever followed a next-page
#: reference the *response* supplies rather than a cursor of its own choosing.
PAGINATE_FLAG: Final = "--paginate"


def _vector(document: str, variables: dict[str, str | int]) -> tuple[str, ...]:
    """The production vector, built by the adapter rather than transcribed here.

    ``GhCli.graphql_vector`` exists so this file asserts on the thing that is
    spawned. A test that rebuilt the vector from the same pieces would agree with
    itself however the adapter changed, which is the shape that survives its own
    mutation.
    """
    cli = gh_cli.GhCli(binary=pathlib.Path("/usr/local/bin/gh"), environment={})
    return cli.graphql_vector(document=document, variables=variables)


def test_the_endpoint_element_is_the_literal_graphql() -> None:
    """Clause 2: there is no path for a repository name to escape into.

    ``gh api repos/{owner}/{repo}/pulls`` interpolates caller data into a path.
    The GraphQL form has one path segment and it is a constant, which is what
    makes "identity travels as variables" a checkable property rather than a
    promise about quoting -- and it is also why T-7's scheme allowlist has no
    input here: there is no URL in the vector at all.
    """
    assert gh_cli.GRAPHQL_ENDPOINT == "graphql"

    vector = _vector(queries.PULL_REQUESTS, {"owner": "acme", "name": "order-service"})

    assert vector[1] == "api"
    assert vector[2] == "graphql"


def test_no_vector_element_is_built_by_formatting_a_repository_name() -> None:
    """Clause 2's second half, driven with a name chosen to be visible if it leaked.

    The owner and the name appear **only** as the value half of a ``-f
    name=value`` binding whose name comes from a closed set. An element that
    merely *contained* them would pass a weaker assertion, so this one locates
    every element they appear in and checks each is that shape.
    """
    vector = _vector(queries.PULL_REQUESTS, {"owner": "OWNER-MARKER", "name": "NAME-MARKER"})
    carrying = [element for element in vector if "MARKER" in element]

    assert carrying == ["owner=OWNER-MARKER", "name=NAME-MARKER"] or sorted(carrying) == [
        "name=NAME-MARKER",
        "owner=OWNER-MARKER",
    ], f"the repository name reached {carrying!r}, which is not a variable binding"
    for element in carrying:
        assert element.split("=", 1)[0] in queries.VARIABLE_NAMES


def test_every_variable_binding_names_a_variable_the_documents_declare() -> None:
    """A binding whose name is not in the closed set is a variable nobody declared."""
    vector = _vector(
        queries.REVIEW_THREADS,
        {"owner": "acme", "name": "order-service", "number": 1, "first": 50},
    )
    bindings = [
        element
        for index, element in enumerate(vector)
        if index and vector[index - 1] in {"-f", "-F"}
    ]

    assert bindings, "the vector carries no variable bindings, so this asserts nothing"
    for binding in bindings:
        assert binding.split("=", 1)[0] in queries.VARIABLE_NAMES, binding


#: The one spawned vector with no ``--hostname``, and why it is an exemption
#: rather than an omission: ``gh --version`` prints a compiled-in string. It
#: parses no host, opens no connection, and there is nothing an inherited
#: ``GH_HOST`` could redirect. Any *other* vector arriving here is a probe nobody
#: has judged, which is what the assertion below turns into a failure.
_MAKES_NO_REQUEST: Final[tuple[tuple[str, ...], ...]] = (("--version",),)


def _vector_call_sites() -> list[tuple[str, ...] | None]:
    """Every ``self.vector(...)`` call in ``gh_cli.py``, as its literal arguments.

    Read off the source rather than listed here, because clause 3's sentence is
    about a **population**: a fourth probe added later with no ``--hostname`` is
    precisely what the pin has to catch, and a transcribed list of three would
    agree with itself while missing it.

    A site whose arguments are not all literals reads as ``None`` --
    ``graphql_vector`` unpacks a list it builds -- and is checked as the vector
    it produces instead. Bare names are resolved against this module's own string
    constants, which is how ``GITHUB_HOSTNAME`` is read.
    """
    module = ast.parse(GH_CLI_SOURCE.read_text(encoding="utf-8"))
    sites: list[tuple[str, ...] | None] = []
    for node in ast.walk(module):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if not (isinstance(function, ast.Attribute) and function.attr == "vector"):
            continue
        arguments: list[str] = []
        for argument in node.args:
            constant = (
                getattr(gh_cli, argument.id, None) if isinstance(argument, ast.Name) else None
            )
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                arguments.append(argument.value)
            elif isinstance(constant, str):
                arguments.append(constant)
            else:
                sites.append(None)
                break
        else:
            sites.append(tuple(arguments))
    return sites


def test_the_hostname_is_pinned_in_every_vector_that_makes_a_request() -> None:
    """Clause 3: an inherited ``GH_HOST`` moved the request (ADR-0030 run B); this is run A.

    "Every vector" is measured, not transcribed. The literal call sites come out
    of the source, and the one site that builds its arguments is checked as the
    two vectors it produces -- so the assertion covers the version probe, the
    authentication probe and both GraphQL requests.

    What the scan measures is every ``vector(...)`` call **this module makes**. A
    vector assembled without that helper and handed straight to ``run_bounded``
    is outside it, which is one of the reasons the helper exists: it is also
    where the absolute binary path comes from (clause 5).

    The version probe is the exemption and it is named as one: it is asserted to
    carry no ``--hostname`` rather than skipped, so an exemption that stops being
    true reddens from either direction.
    """
    sites = _vector_call_sites()
    assert sites, "the source scan found no `vector(...)` call, so this asserts nothing"
    assert any(site is None for site in sites), (
        "every call site read as literal arguments, so `graphql_vector`'s built "
        "vector is no longer covered by the second half of this test"
    )

    for arguments in sites:
        if arguments is None:
            continue
        if arguments in _MAKES_NO_REQUEST:
            assert "--hostname" not in arguments
            continue
        assert "--hostname" in arguments, arguments
        assert arguments[arguments.index("--hostname") + 1] == gh_cli.GITHUB_HOSTNAME, arguments

    for document in (queries.PULL_REQUESTS, queries.REVIEW_THREADS):
        vector = _vector(document, {"owner": "acme", "name": "order-service"})
        assert "--hostname" in vector
        assert vector[vector.index("--hostname") + 1] == "github.com"


def test_paginate_is_absent_from_every_vector() -> None:
    """Clause 6: a destination the response chooses is the shape T-7 names.

    ``--paginate`` follows a next-page reference the response supplies, and
    exactly what it follows is behaviour of a binary this design pins only the
    version of. A cursor in a typed variable cannot become a destination.
    """
    for document in (queries.PULL_REQUESTS, queries.REVIEW_THREADS):
        vector = _vector(document, {"owner": "acme", "name": "order-service"})
        assert PAGINATE_FLAG not in vector


def test_a_second_page_changes_only_the_cursor() -> None:
    """The pagination property: same vector, one variable's value different."""
    base: dict[str, str | int] = {"owner": "acme", "name": "order-service", "first": 50}
    first = _vector(queries.PULL_REQUESTS, base)
    second = _vector(queries.PULL_REQUESTS, {**base, "after": "CURSOR"})

    added = [element for element in second if element not in first]

    assert added == ["after=CURSOR"], (
        f"a later page changed more than the cursor: {added!r}. Every page after "
        f"the first must be the same request with one variable's value moved."
    )


def test_two_mappings_with_the_same_variables_produce_byte_identical_vectors() -> None:
    """``graphql_vector``'s recorded property, driven against the input that can break it.

    "Two runs over the same input produce byte-identical vectors" is what the
    docstring promises, and a recorded argv compared against one built here rests
    on it. Building the vector twice from the *same* mapping cannot demonstrate
    it: a ``dict`` iterates in insertion order, so an implementation that emitted
    variables in whatever order it received them would agree with itself and
    pass.

    What separates them is two mappings carrying the same pairs in **different
    insertion orders** -- which is the ordinary case, since the provider builds
    ``variables`` a key at a time and adds ``after`` only on a later page.

    The mapping is checked to actually be in a different order first. Python
    preserves insertion order, but a fixture that quietly ended up identical
    would make this test agree with itself, which is the failure it is about.
    """
    forward: dict[str, str | int] = {
        "owner": "acme",
        "name": "order-service",
        "number": 12,
        "first": 50,
        "after": "CURSOR",
    }
    backward: dict[str, str | int] = dict(reversed(list(forward.items())))

    assert list(backward) != list(forward), "both fixtures iterate the same way"
    assert _vector(queries.REVIEW_THREADS, forward) == _vector(queries.REVIEW_THREADS, backward), (
        "the same five variables in two insertion orders produced two different "
        "vectors. A recorded argv can then only be compared against one built "
        "from a mapping assembled the same way, and two runs of the same request "
        "are two different spawns."
    )


def test_the_first_vector_element_is_an_absolute_path() -> None:
    """Clause 5: an unresolved name would let the child's PATH choose the executable.

    Clause 4 means whatever ``PATH`` the child sees is one this project
    constructed; clause 5 means it is not consulted for the executable at all.
    """
    vector = _vector(queries.PULL_REQUESTS, {"owner": "acme", "name": "order-service"})

    assert pathlib.Path(vector[0]).is_absolute()


def test_the_spawn_module_reaches_no_shell() -> None:
    """SEC-9, read off the module's own syntax tree rather than asserted in prose.

    ``shell=True`` and ``create_subprocess_shell`` are the two ways a vector
    becomes a string somebody's quoting has to be right about. Neither appears,
    and a source scan says so for the *whole* module rather than for the paths a
    test happens to drive.
    """
    tree = ast.parse(GH_CLI_SOURCE.read_text(encoding="utf-8"), filename=GH_CLI_SOURCE.name)
    shell_keywords = [
        node for node in ast.walk(tree) if isinstance(node, ast.keyword) and node.arg == "shell"
    ]
    shell_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "create_subprocess_shell"
    ]

    assert not shell_keywords, "the spawn module passes a `shell` keyword"
    assert not shell_calls, "the spawn module reaches `asyncio.create_subprocess_shell`"


def test_the_documents_interpolate_nothing() -> None:
    """A GraphQL document built by formatting is a document a name can be injected into.

    The two documents are module constants with no ``{`` placeholders and no
    f-string anywhere in their module, so the only thing that varies between two
    requests is the value of a declared variable.
    """
    tree = ast.parse(
        pathlib.Path(queries.__file__).read_text(encoding="utf-8"),
        filename="queries.py",
    )
    formatted = [node for node in ast.walk(tree) if isinstance(node, ast.JoinedStr)]

    assert not formatted, "a GraphQL document module builds a string by interpolation"
    assert "%s" not in queries.PULL_REQUESTS
    assert "%s" not in queries.REVIEW_THREADS


#: Every page size a document spells as a literal, with the constant whose value
#: it must be, as ``(the connection's field name, the constant's name)``.
#:
#: Two connections, because both are read against a cap the *provider* enforces
#: while the number that actually reaches GitHub is the literal here.
_PAGE_SIZE_LITERALS: Final[tuple[tuple[str, str, str], ...]] = (
    ("comments", "MAX_COMMENTS_PER_THREAD", "REVIEW_THREADS"),
    ("closingIssuesReferences", "MAX_LINKED_ISSUES", "PULL_REQUESTS"),
)


@pytest.mark.parametrize(
    ("connection", "constant", "document"),
    _PAGE_SIZE_LITERALS,
    ids=[case[0] for case in _PAGE_SIZE_LITERALS],
)
def test_a_page_size_the_document_spells_is_the_constant_that_names_the_cap(
    connection: str, constant: str, document: str
) -> None:
    """The constant and the literal are two numbers, and only one of them reaches GitHub.

    ``MAX_COMMENTS_PER_THREAD`` and ``MAX_LINKED_ISSUES`` are recorded bounds the
    provider refuses on: it reads ``hasNextPage`` and stops. But what decides
    whether ``hasNextPage`` is ever true is the ``first:`` **literal inside the
    document**, and the two can move apart. Lowering the constant alone leaves the
    query asking for a hundred and publishes a refusal naming a cap nothing
    enforces; lowering the literal alone truncates at the smaller number while the
    refusal never fires, which is the silent loss the cap exists to prevent.

    **This test interpolates and the document must not**, and that is not a
    contradiction: ``test_the_documents_interpolate_nothing`` holds
    ``queries.py`` to literals precisely so a repository name cannot be formatted
    into one. The consequence is that the number *is* transcribed there, and a
    transcription needs something outside it to be checked against -- which is
    this, built here where interpolation is free of that risk.
    """
    expected = f"{connection}(first: {getattr(limits, constant)})"

    assert expected in getattr(queries, document), (
        f"`queries.{document}` does not ask for `{expected}`. "
        f"`limits.{constant}` is the number the refusal message names and the "
        f"provider's `hasNextPage` check is priced against; the `first:` literal "
        f"in the document is the number GitHub is actually asked for. Move both "
        f"in the same change, or the cap is a message about a bound nothing sets."
    )


#: The bounds whose test-side restatement lives **here**, with the value.
#:
#: **The admission rule, so the next constant does not have to be argued about.**
#: A bound belongs in this table when nothing else restates it test-side. A
#: second restatement in a second file is a second copy free to drift from the
#: first, which is the failure this whole table exists to prevent one level up.
#: So four of ``limits.py``'s constants are deliberately absent, each restated by
#: the file that *drives* it:
#:
#: * ``MAX_PROBE_STDOUT_BYTES`` -- ``test_gh_review_provider.py``'s
#:   ``RECORDED_PROBE_STDOUT_BYTES``, which sizes a probe's stdout to the
#:   boundary and one byte past it;
#: * ``MAX_CHILD_STDERR_BYTES`` -- ``test_gh_bounded_read.py``'s
#:   ``RECORDED_STDERR_BYTES``, where it is observed as a memory bound;
#: * ``MAX_GH_CONFIG_BYTES`` and ``MAX_REPOSITORY_CHARS`` -- the two
#:   fixture-independence rebuilds, in their own files.
#:
#: Three more are absent for a different reason: ``MAX_READ_BYTES_PER_CALL``,
#: ``MAX_SPAWNS_PER_CALL`` and ``MAX_SECONDS_PER_CALL`` are *derived*, so
#: restating a value here would pin a product rather than the derivation.
#: :func:`test_the_derived_per_call_ceiling_is_still_the_product_of_its_factors`
#: and :func:`test_the_derived_spawn_and_time_ceilings_are_still_their_derivations`
#: are their pins, and the figures ``limits.py``'s prose names are entailed by
#: those plus the factors below.
RECORDED_BOUNDS: Final[tuple[tuple[str, object], ...]] = (
    ("REQUEST_TIMEOUT_SECONDS", 30.0),
    ("REAP_SECONDS", 5.0),
    ("PAGE_SIZE", 50),
    ("MAX_PAGES", 20),
    ("MAX_PULL_REQUESTS", 500),
    ("MAX_COMMENTS_PER_THREAD", 100),
    ("MAX_LINKED_ISSUES", 20),
    ("MAX_RESPONSE_BYTES", 8 * 1024 * 1024),
    ("GH_VERSION_FLOOR", (2, 86, 0)),
)


@pytest.mark.parametrize(
    ("name", "value"), RECORDED_BOUNDS, ids=[name for name, _ in RECORDED_BOUNDS]
)
def test_each_recorded_bound_is_the_value_the_prose_names(name: str, value: object) -> None:
    """Clause 7's shape: a bound is a constant a test reads and prose can name.

    Written out here rather than imported for the same reason clause 4(i)'s
    mapping is: a test that reads the constant moves with it. Changing a cap is
    then a two-file diff somebody reviews, which is what a recorded limit means.

    ``MAX_LINKED_ISSUES`` joined its sibling here rather than resting on the
    document pin above. That pin holds the constant and the query literal
    *together*; it says nothing about either one's value, so moving both leaves
    it green -- which is the state ``MAX_COMMENTS_PER_THREAD`` was never in and
    the newer constant was.

    Which bounds belong in this table, and which are restated by the file that
    drives them instead, is :data:`RECORDED_BOUNDS`.
    """
    assert getattr(limits, name) == value, (
        f"`{name}` moved. A cap is a recorded number: move the prose that names "
        f"it in the same change, and say what the new bound costs."
    )


def test_the_derived_per_call_ceiling_is_still_the_product_of_its_factors() -> None:
    """``MAX_READ_BYTES_PER_CALL`` is a derivation, and a derivation can be hand-edited.

    It exists because neither factor states the ceiling a reader pricing one
    paginated call needs. Today it is written as ``MAX_PAGES *
    MAX_RESPONSE_BYTES``, and the risk a recorded number carries is that somebody
    writes the answer down instead: a literal ``160 * 1024 * 1024`` is correct on
    the day it is typed and silently wrong the first time either factor moves,
    with the constant's own prose still naming the old product.

    This is the only assertion that separates the two spellings, and it is why
    the figure is not restated in :data:`RECORDED_BOUNDS`: both factors are
    pinned there, so the ceiling the prose names follows from this and needs no
    third copy.
    """
    assert limits.MAX_READ_BYTES_PER_CALL == limits.MAX_PAGES * limits.MAX_RESPONSE_BYTES, (
        f"`MAX_READ_BYTES_PER_CALL` is {limits.MAX_READ_BYTES_PER_CALL} and "
        f"`MAX_PAGES * MAX_RESPONSE_BYTES` is "
        f"{limits.MAX_PAGES * limits.MAX_RESPONSE_BYTES}. The ceiling is recorded as a "
        f"derivation so it cannot drift from its factors; if it has become a literal, "
        f"the prose beside it names a product nothing computes."
    )


#: How many spawns the two probes are, restated here rather than imported.
#:
#: :data:`~theurian.infrastructure.github.limits.MAX_SPAWNS_PER_CALL` is written
#: as ``_PROBE_SPAWNS + MAX_PAGES``, so a pin that read ``_PROBE_SPAWNS`` would
#: be an identity and would stay green if a third probe were added and the
#: constant left alone. Written out, it is the second copy that has to move.
_RECORDED_PROBE_SPAWNS: Final = 2


def test_the_derived_spawn_and_time_ceilings_are_still_their_derivations() -> None:
    """The same argument as the byte ceiling, for the two that price a call's cost.

    Neither number is stated by any single constant: 22 children and 770 seconds
    come out of the page cap, the probe count and the per-spawn ceiling together,
    and a reader pricing one ``list_pull_requests`` or ``get_threads`` call needs
    the products. Written as derivations so they cannot drift from their factors
    -- a hand-typed ``770.0`` is right on the day it is typed and silently wrong
    the first time the timeout or the reap moves, with the prose beside it still
    naming the old figure.

    The factors themselves are pinned in :data:`RECORDED_BOUNDS`, so between that
    table and these two assertions the published numbers are entailed rather than
    transcribed.
    """
    assert limits.MAX_SPAWNS_PER_CALL == _RECORDED_PROBE_SPAWNS + limits.MAX_PAGES, (
        f"`MAX_SPAWNS_PER_CALL` is {limits.MAX_SPAWNS_PER_CALL} and the two probes "
        f"plus {limits.MAX_PAGES} pages is "
        f"{_RECORDED_PROBE_SPAWNS + limits.MAX_PAGES}. A probe added or removed is a "
        f"change to what one call may start: move the constant and this number "
        f"together, and say what the new ceiling costs."
    )
    assert limits.MAX_SECONDS_PER_CALL == limits.MAX_SPAWNS_PER_CALL * (
        limits.REQUEST_TIMEOUT_SECONDS + limits.REAP_SECONDS
    ), (
        f"`MAX_SECONDS_PER_CALL` is {limits.MAX_SECONDS_PER_CALL} and its factors give "
        f"{limits.MAX_SPAWNS_PER_CALL * (limits.REQUEST_TIMEOUT_SECONDS + limits.REAP_SECONDS)}. "
        f"The wall-clock ceiling is recorded as a derivation so it cannot drift; if it "
        f"has become a literal, the prose beside it names a figure nothing computes."
    )


def _github_reaching_sites() -> set[tuple[str, str]]:
    """Every ``(module, name)`` in the imported package naming one of :data:`_REACHES_GITHUB`.

    :data:`_NAMING_NODES` is the population key. Declarations are read as well as
    uses, so the two modules are described symmetrically -- ``gh_cli.py``
    declares both names, ``review_provider.py`` imports one -- and so a module
    that *redefines* either name is a site rather than a silence.
    """
    return {
        (path.relative_to(SRC).as_posix(), name)
        for path in sorted(SRC.rglob("*.py"))
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=path.name))
        if isinstance(node, _NAMING_NODES)
        for name in (
            getattr(node, "id", None),
            getattr(node, "attr", None),
            getattr(node, "name", None),
        )
        if name in _REACHES_GITHUB
    }


def test_only_the_recorded_modules_name_the_spawn_helper_or_the_class_that_calls_it() -> None:
    """Clause 1's second half: exactly one module may reach GitHub.

    The equality in ``test_network_call_sites.py`` pins which modules can start a
    program at all. This pins the layer above it: a second module reaching this
    adapter spawns through the recorded site while adding no new entry to that
    set -- a fetch path that is invisible to the pin meant to catch exactly this.

    **Two names, because one of them is not enough.** A scan on ``run_bounded``
    alone is satisfied by a module that constructs a ``GhCli`` and calls
    ``graphql`` on it: the spawn happens inside ``gh_cli.py``, the helper is
    named inside ``gh_cli.py``, and the new module names neither. That is a
    second path to GitHub passing both this pin and ``PROCESS_SPAWN_SITES``,
    which is why the population here is ``{GhCli, run_bounded}`` and the
    assertion is an equality over ``(module, name)`` pairs rather than over
    modules.

    The scan reads names over the whole imported package, which is the same bound
    ``test_network_call_sites.py`` records for itself: a helper reached under a
    name assembled at run time passes.
    """
    sites = _github_reaching_sites()

    assert sites == GITHUB_REACHING_SITES, (
        f"the modules naming {sorted(_REACHES_GITHUB)} are "
        f"{sorted(sites)}, and this pin records {sorted(GITHUB_REACHING_SITES)}.\n\n"
        f"Exactly one module may reach GitHub (ADR-0030 clause 1). A second caller "
        f"spawns through the recorded site and adds no entry to "
        f"`PROCESS_SPAWN_SITES`, so the equality there cannot see it. If a site is "
        f"MISSING instead, the adapter has been moved or removed and ADR-0030's "
        f"clauses describe a path that is gone."
    )


def test_the_pull_request_cap_bites_before_the_page_cap_on_that_read() -> None:
    """Both caps stay reachable, rather than one shadowing the other for ever.

    A cap no input can reach is a cap no test can drive, and an untested cap is
    the shape ADR-0030 grades as unproven. The pull-request cap is 10 pages and
    the page cap is 20, so the first stops a pull-request read and the second
    stops a thread read, which is how each has an input that reaches it.

    **On full pages**, which is what this inequality is about and all it claims.
    What ``MAX_PULL_REQUESTS`` counts is pull requests collected, not pages read,
    so a read of short pages reaches the page cap first -- and then a
    ``list_pull_requests`` call stops exactly where a ``get_threads`` call does.
    ``limits.MAX_SPAWNS_PER_CALL`` is priced against that reading, not this one.
    """
    assert limits.MAX_PULL_REQUESTS < limits.MAX_PAGES * limits.PAGE_SIZE


def test_an_integer_variable_is_typed_and_every_caller_derived_value_is_raw() -> None:
    """The flag is chosen by the value's type, and both directions are load-bearing.

    **The `-F` direction was found by running it.** With every variable on `-f`,
    `gh` sends them all as GraphQL `String`s and the API answers
    ``Variable $first of type Int! was provided invalid value`` -- measured
    against the live API on 2026-09-06, and invisible to every test that answers
    with a canned payload, because a stand-in child does not type-check a
    document.

    **The `-f` direction is the security half.** A ``-F`` value opening with
    ``@`` is read by ``gh`` as a *filename to send*, so a caller-derived string
    -- an owner, a repository name, a pagination cursor -- must never travel on
    it. Every such value here is a `str` and every `str` goes on ``-f``.
    """
    vector = _vector(
        queries.REVIEW_THREADS,
        {"owner": "acme", "name": "order-service", "number": 12, "first": 50, "after": "CUR"},
    )
    flagged = {
        vector[index]: vector[index - 1]
        for index in range(1, len(vector))
        if vector[index - 1] in {"-f", "-F"}
    }

    assert flagged["number=12"] == "-F"
    assert flagged["first=50"] == "-F"
    assert flagged["owner=acme"] == "-f"
    assert flagged["name=order-service"] == "-f"
    assert flagged["after=CUR"] == "-f"

    typed = [binding for binding, flag in flagged.items() if flag == "-F"]
    assert all(binding.split("=", 1)[1].isdigit() for binding in typed), (
        f"a non-integer value reached `-F`: {typed}. `gh` reads a `-F` value "
        f"opening with `@` as a filename to send, so nothing a caller chose may "
        f"travel on that flag."
    )


def test_a_boolean_never_reaches_the_flag_that_types_an_integer() -> None:
    """The guard nothing real drives, driven synthetically so it survives deletion.

    ``bool`` is a subclass of ``int`` in Python, so ``isinstance(value, int)`` is
    true of ``True`` and the routing would send it on ``-F`` -- the flag chosen
    because the documents declare ``$first`` and ``$number`` as ``Int!``. A
    Boolean is not an ``Int``, and the value that arrived would not be the one
    the document asked for.

    **No call site passes one**, which is exactly the problem this test solves: a
    guard no input reaches is a guard whose deletion nothing notices, and the
    variables today are strings the caller supplied and integers this adapter
    produced. The input is therefore synthetic and says so. The variable name is
    a declared one so the vector stays a shape the documents would accept, and
    only the value's *type* is the thing under test.
    """
    vector = _vector(queries.PULL_REQUESTS, {"owner": "acme", "first": True})
    flagged = {
        vector[index]: vector[index - 1]
        for index in range(1, len(vector))
        if vector[index - 1] in {"-f", "-F"}
    }

    assert flagged["first=True"] == "-f", (
        "a `bool` reached `-F`, the flag that exists because the documents type "
        "two variables as `Int!`. `isinstance(True, int)` is True and nothing else "
        "in the routing notices, so dropping the `not isinstance(value, bool)` "
        "clause is a change with no other symptom."
    )
