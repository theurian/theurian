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

#: The function every request leaves through. One name, so the scan below asks
#: one question.
_SPAWN_HELPER: Final = "run_bounded"

#: What ``gh`` would be told to do if this adapter ever followed a next-page
#: reference the *response* supplies rather than a cursor of its own choosing.
PAGINATE_FLAG: Final = "--paginate"


def _vector(document: str, variables: dict[str, str]) -> tuple[str, ...]:
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
        {"owner": "acme", "name": "order-service", "number": "1", "first": "50"},
    )
    bindings = [
        element for index, element in enumerate(vector) if index and vector[index - 1] == "-f"
    ]

    assert bindings, "the vector carries no `-f` bindings, so this asserts nothing"
    for binding in bindings:
        assert binding.split("=", 1)[0] in queries.VARIABLE_NAMES, binding


def test_the_hostname_is_pinned_in_every_vector() -> None:
    """Clause 3: an inherited ``GH_HOST`` moved the request (ADR-0030 run B); this is run A."""
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
    base = {"owner": "acme", "name": "order-service", "first": "50"}
    first = _vector(queries.PULL_REQUESTS, base)
    second = _vector(queries.PULL_REQUESTS, {**base, "after": "CURSOR"})

    added = [element for element in second if element not in first]

    assert added == ["after=CURSOR"], (
        f"a later page changed more than the cursor: {added!r}. Every page after "
        f"the first must be the same request with one variable's value moved."
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


@pytest.mark.parametrize(
    ("name", "value"),
    (
        ("REQUEST_TIMEOUT_SECONDS", 30.0),
        ("PAGE_SIZE", 50),
        ("MAX_PAGES", 20),
        ("MAX_PULL_REQUESTS", 500),
        ("MAX_COMMENTS_PER_THREAD", 100),
        ("MAX_RESPONSE_BYTES", 8 * 1024 * 1024),
        ("GH_VERSION_FLOOR", (2, 86, 0)),
    ),
    ids=(
        "REQUEST_TIMEOUT_SECONDS",
        "PAGE_SIZE",
        "MAX_PAGES",
        "MAX_PULL_REQUESTS",
        "MAX_COMMENTS_PER_THREAD",
        "MAX_RESPONSE_BYTES",
        "GH_VERSION_FLOOR",
    ),
)
def test_each_recorded_bound_is_the_value_the_prose_names(name: str, value: object) -> None:
    """Clause 7's shape: a bound is a constant a test reads and prose can name.

    Written out here rather than imported for the same reason clause 4(i)'s
    mapping is: a test that reads the constant moves with it. Changing a cap is
    then a two-file diff somebody reviews, which is what a recorded limit means.
    """
    assert getattr(limits, name) == value, (
        f"`{name}` moved. A cap is a recorded number: move the prose that names "
        f"it in the same change, and say what the new bound costs."
    )


def test_only_the_spawn_module_names_the_spawn_helper() -> None:
    """Clause 1's second half: exactly one module may reach GitHub.

    The equality in ``test_network_call_sites.py`` pins which modules can start a
    program at all. This pins the layer above it: a second module calling
    ``run_bounded`` would spawn through the recorded site while adding no new
    entry to that set -- a fetch path that is invisible to the pin meant to catch
    exactly this.

    The scan reads names over the whole imported package, which is the same bound
    ``test_network_call_sites.py`` records for itself: a helper reached under a
    name assembled at run time passes.
    """
    naming = sorted(
        path.relative_to(SRC).as_posix()
        for path in sorted(SRC.rglob("*.py"))
        if any(
            isinstance(node, ast.Name | ast.Attribute | ast.alias)
            and _SPAWN_HELPER
            in (
                getattr(node, "id", None),
                getattr(node, "attr", None),
                getattr(node, "name", None),
            )
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=path.name))
        )
    )

    assert naming == ["infrastructure/github/gh_cli.py"], (
        f"`{_SPAWN_HELPER}` is named in {naming}, and exactly one module may reach "
        f"GitHub (ADR-0030 clause 1). A second caller spawns through the recorded "
        f"site and adds no entry to `PROCESS_SPAWN_SITES`, so the equality there "
        f"cannot see it."
    )


def test_the_pull_request_cap_bites_before_the_page_cap_on_that_read() -> None:
    """Both caps stay reachable, rather than one shadowing the other for ever.

    A cap no input can reach is a cap no test can drive, and an untested cap is
    the shape ADR-0030 grades as unproven. The pull-request cap is 10 pages and
    the page cap is 20, so the first stops a pull-request read and the second
    stops a thread read, which is how each has an input that reaches it.
    """
    assert limits.MAX_PULL_REQUESTS < limits.MAX_PAGES * limits.PAGE_SIZE
