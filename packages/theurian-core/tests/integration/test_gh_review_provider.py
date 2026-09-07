"""The ``gh`` review adapter, driven end to end against a stand-in child (ADR-0030).

**A real spawn, a fake ``gh``.** The child is a ``/bin/sh`` script this file
writes: it records every invocation's argv and environment, answers
``--version`` and ``auth status`` as the test asks it to, and prints a canned
GraphQL response chosen by the variables it was handed. That keeps the *adapter*
real -- the vector, the constructed environment, the bounded read, the refusal
ordering are all production code -- while the thing on the other side of the
process boundary is under the test's control.

Two properties would be untestable otherwise, and both are the point:

* **that an unallowlisted repository produces no spawn at all**, which is what
  distinguishes a control from a filter. The assertion is that the recorder is
  empty, and it can only be made where a spawn was possible;
* **that the child receives the constructed environment**, rather than that a
  function returns the right dictionary. ``test_gh_child_environment.py`` holds
  the mapping; this holds that the mapping arrives.

The script is ``/bin/sh`` and reaches only ``cat``, ``env`` and shell builtins,
because the child's ``PATH`` is the adapter's fixed literal -- a Python stand-in
would need an interpreter that literal does not promise.

**Which canned answer the child returns is chosen from the argv it was handed.**
A ``number=`` binding says the read is about one pull request, and both
per-pull-request documents carry one -- so the reviews read is told apart by
``submittedAt``, a token only ``PULL_REQUEST_REVIEWS`` selects. The two flags are
collected in one pass and combined afterwards, so the answer does not depend on
which order ``graphql_vector`` happens to emit the query and the variables in.
"""

from __future__ import annotations

import dataclasses
import json
import os
import pathlib
import re
import sys
from collections.abc import Iterator
from typing import Any, Final

import pytest

from theurian.domain.enums import ReviewThreadState
from theurian.domain.identifiers import ProjectId
from theurian.domain.ports.review_provider import SkippedPullRequest
from theurian.domain.review import ReviewEvent
from theurian.domain.review_ingest import (
    MAX_REFUSAL_SUMMARY_CHARS,
    RefusalGrade,
    ReviewIngestRefusedError,
)
from theurian.infrastructure.github import environment, limits
from theurian.infrastructure.github.review_provider import GitHubReviewProvider
from theurian.infrastructure.github.transport_guard import GH_CONFIG_FILE

pytestmark = pytest.mark.integration

PROJECT: Final = ProjectId("demo")
REPOSITORY: Final = "acme/order-service"

#: The widest pull-request number a GraphQL answer can carry into this adapter.
#:
#: ``json.loads`` converts a JSON integer literal with ``int()``, which CPython
#: refuses past ``sys.get_int_max_str_digits()`` -- so a literal one digit wider
#: never becomes a number at all: the whole document is refused as one this
#: adapter cannot read.
#: ``test_a_number_one_digit_wider_is_refused_as_an_unreadable_document`` is that
#: boundary's key, and it is what lets the summary-bound test above call this the
#: largest value its sentences can be asked to name.
_WIDEST_NUMBER: Final = 10 ** (sys.get_int_max_str_digits() - 1)

#: How much stdout a **probe** may produce before the read is refused, **written
#: out here and never imported**.
#:
#: The two probes are the only spawns whose output is bounded by something other
#: than the response cap, and they got their own constant precisely because
#: borrowing the stderr one made an oversized ``gh --version`` report a *stderr*
#: bound as a *response* bound. A fixture sized from the constant would then
#: overrun whatever the constant became and pass for every value of it, so the
#: number is restated here and both boundary tests are built from this one.
RECORDED_PROBE_STDOUT_BYTES: Final = 64 * 1024

#: Variables ``/bin/sh`` sets **for itself** on start-up, which are therefore in
#: the child's ``env`` output without having been passed by the adapter.
#: Measured on this machine and reproduced in CI: ``PWD`` and ``_`` from the
#: shell's own start-up, ``SHLVL`` from its nesting count. Subtracted from the
#: observed mapping rather than added to the expected one, because they are a
#: property of the stand-in child and not of what production passes -- a real
#: ``gh`` is not a shell and sets none of them.
#:
#: ``PWD`` is worth naming rather than merely excluding: the child learns the
#: working directory through the inherited ``cwd`` whatever the environment says,
#: because every child inherits one. The adapter passes no ``cwd``, which is also
#: what makes the transport guard's relative-path resolution agree with ``gh``'s.
_SHELL_ADDED: Final[frozenset[str]] = frozenset({"PWD", "SHLVL", "_"})

#: The script the stand-in ``gh`` runs. Everything it needs is a shell builtin or
#: lives in ``/bin`` or ``/usr/bin``, which is what the adapter's fixed ``PATH``
#: promises -- so this child is spawned under exactly the environment production
#: would give a real ``gh``.
_FAKE_GH = """\
#!/bin/sh
n=$(cat "{state}/count" 2>/dev/null || echo 0)
n=$((n + 1))
printf '%s' "$n" > "{state}/count"
printf '%s\\n' "$@" > "{state}/argv-$n"
printf '%s' "$0" > "{state}/argv0-$n"
/usr/bin/env > "{state}/env-$n"
cat > "{state}/stdin-$n"

case "$1" in
  --version) cat "{state}/{version_stdout}"; exit 0 ;;
  auth) printf 'auth probe stderr: {auth_stderr}\\n' >&2; exit {auth_exit} ;;
esac

kind=prs
page=1
per_pr=0
for a in "$@"; do
  case "$a" in
    number=*) per_pr=1 ;;
    after=*) page=2 ;;
    *submittedAt*) kind=reviews ;;
  esac
done
if [ "$per_pr" = 1 ] && [ "$kind" = prs ]; then kind=threads; fi
body="{state}/$kind$page.json"
if [ -f "$body" ]; then cat "$body"; exit 0; fi
printf 'no canned response for %s page %s\\n' "$kind" "$page" >&2
exit {query_exit}
"""


#: Where the stand-in child's ``--version`` stdout lives, byte for byte.
#:
#: A file the child ``cat``s rather than a ``printf`` inside the script, so the
#: bytes it writes are under the test's control **and counted in one place**. A
#: probe bound is measured in bytes of stdout, and a template that printed the
#: line would leave the test computing the line's length a second time.
_VERSION_STDOUT: Final = "version-stdout"


class FakeGh:
    """A stand-in ``gh`` on disk, plus the record of how it was called."""

    def __init__(self, directory: pathlib.Path) -> None:
        self.directory = directory
        self.binary = directory / "gh"

    @property
    def invocations(self) -> int:
        counter = self.directory / "count"
        return int(counter.read_text(encoding="utf-8")) if counter.exists() else 0

    def argv(self, index: int) -> list[str]:
        """One invocation's argument vector, without the binary path itself."""
        return (self.directory / f"argv-{index}").read_text(encoding="utf-8").splitlines()

    def spawned_as(self, index: int) -> str:
        """The vector's **first** element, as the child was handed it.

        ``"$@"`` starts at the first argument, so :meth:`argv` cannot see this.
        The path a ``#!`` script is invoked with reaches the interpreter
        unresolved -- a symlink stays a symlink -- which is exactly what makes it
        able to say whether ``locate_binary`` resolved the one it found.
        """
        return (self.directory / f"argv0-{index}").read_text(encoding="utf-8")

    def child_environment(self, index: int) -> dict[str, str]:
        """One invocation's environment, as the child itself reported it."""
        text = (self.directory / f"env-{index}").read_text(encoding="utf-8")
        return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)

    def stdin_of(self, index: int) -> str:
        """Everything one invocation could read from fd 0 before it saw EOF."""
        return (self.directory / f"stdin-{index}").read_text(encoding="utf-8")

    def answer(self, kind: str, page: int, payload: dict[str, Any]) -> None:
        """Give the child a canned response for one query kind and page."""
        self.answer_text(kind, page, json.dumps(payload))

    def answer_text(self, kind: str, page: int, document: str) -> None:
        """Give the child a canned response as literal text.

        :meth:`answer` renders a Python object, and there are documents no Python
        object renders into: an integer literal past the interpreter's digit
        limit is one -- ``json.dumps`` refuses it for the same reason
        ``json.loads`` refuses to read one. That shape is the boundary
        :data:`_WIDEST_NUMBER` is measured against, so it has to be writable
        without going through a Python ``int``.
        """
        (self.directory / f"{kind}{page}.json").write_text(document, encoding="utf-8")

    def pad_version_stdout_to(self, total: int) -> None:
        """Make ``--version``'s stdout exactly ``total`` bytes.

        The version line is kept first and unchanged, so the padded output is
        still one this adapter can read a version out of -- which is what makes
        the *bound* the thing under test rather than the parse. The length is
        asserted rather than assumed: a fixture that missed the boundary by a
        byte would report the cap as off by one when it is not.
        """
        printed = (self.directory / _VERSION_STDOUT).read_bytes()

        assert total >= len(printed), (
            f"{total} bytes cannot carry the {len(printed)}-byte version line"
        )
        (self.directory / _VERSION_STDOUT).write_bytes(printed + b"x" * (total - len(printed)))


@pytest.fixture
def fake_gh(tmp_path: pathlib.Path) -> Iterator[FakeGh]:
    """A stand-in ``gh`` that reports the recorded version floor and is authenticated."""
    yield _write_fake(tmp_path / "fake", version="2.86.0")


def _write_fake(
    directory: pathlib.Path,
    *,
    version: str,
    auth_exit: int = 0,
    auth_stderr: str = "none",
    query_exit: int = 1,
) -> FakeGh:
    directory.mkdir(parents=True)
    fake = FakeGh(directory)
    fake.binary.write_text(
        _FAKE_GH.format(
            state=directory,
            version_stdout=_VERSION_STDOUT,
            auth_exit=auth_exit,
            auth_stderr=auth_stderr,
            query_exit=query_exit,
        ),
        encoding="utf-8",
    )
    (directory / _VERSION_STDOUT).write_bytes(f"gh version {version} (2026-01-21)\n".encode())
    fake.binary.chmod(0o700)
    return fake


def _project(tmp_path: pathlib.Path, *entries: str) -> tuple[pathlib.Path, pathlib.Path]:
    """A project root whose ``config.yaml`` allowlists ``entries``."""
    knowledge = tmp_path / "project" / ".theurian"
    knowledge.mkdir(parents=True)
    listed = "\n".join(f"      - {entry}" for entry in entries)
    (knowledge / "config.yaml").write_text(
        "apiVersion: theurian.dev/v1\nproviders:\n  review:\n    repositories:\n" + listed + "\n",
        encoding="utf-8",
    )
    return tmp_path / "project", knowledge / "config.yaml"


def _provider(
    tmp_path: pathlib.Path,
    fake: FakeGh | None,
    *,
    entries: tuple[str, ...] = (REPOSITORY,),
    parent: dict[str, str] | None = None,
) -> GitHubReviewProvider:
    root, config = _project(tmp_path, *entries)
    return GitHubReviewProvider(
        project_root=root,
        config_file=config,
        parent_environment={"HOME": str(tmp_path / "home"), **(parent or {})},
        binary=fake.binary if fake is not None else None,
    )


async def _listed(
    provider: GitHubReviewProvider,
    *,
    since_number: int | None = None,
    limit: int = 100,
) -> tuple[ReviewEvent, ...]:
    """One window's events, asserting the listing skipped no pull request.

    Every driver that unwraps through here plants a well-formed pull request, so
    a skip would mean the adapter folded a fault the test never planted -- the
    failure a per-node ``try`` newly makes possible, and the one a bare
    ``.events`` would hide. Asserting it at the unwrap buys that control at every
    call site rather than at the one that thought of it.
    """
    listing = await provider.list_pull_requests(
        PROJECT, REPOSITORY, since_number=since_number, limit=limit
    )

    assert listing.skipped == (), (
        f"the listing skipped pull requests {[skip.number for skip in listing.skipped]} "
        f"and this driver planted no per-pull-request fault"
    )

    return listing.events


async def _one_skip(provider: GitHubReviewProvider) -> SkippedPullRequest:
    """The listing's single skipped pull request, from a fixture carrying one node.

    Two claims, both of which a bare ``listing.skipped[0]`` would leave unmade.
    *Returning at all* is what says the fault was answered rather than raised --
    the whole of what separates a record-scope fault from a repository-scope one
    at this seam -- and the empty ``events`` says the faulty pull request was not
    also recorded.
    """
    listing = await provider.list_pull_requests(PROJECT, REPOSITORY)

    assert listing.events == (), (
        f"the faulty pull request was recorded as well as skipped: "
        f"{[event.number for event in listing.events]}"
    )

    (skip,) = listing.skipped
    return skip


#: "the response does not carry this field at all", as distinct from carrying it
#: set to ``null``. Both are answers GitHub can give and they are different
#: documents, so the fixture has to be able to build each.
_ABSENT: Final = object()


def _pull_requests(
    *, private: object = False, resolved_name: str = REPOSITORY, **overrides: Any
) -> dict[str, Any]:
    node = {
        "number": 12,
        "title": "Refuse a symbolic link at every derived write target",
        "body": "The join check refused the leaf and not the directory itself.",
        "url": "https://github.com/acme/order-service/pull/12",
        "createdAt": "2026-09-01T10:00:00Z",
        "merged": True,
        "mergedAt": "2026-09-02T11:00:00Z",
        "headRefOid": "a" * 40,
        "baseRefOid": "b" * 40,
        "headRefName": "fix/refuse-an-escaping-knowledge-dir",
        "milestone": {"title": "Milestone 8"},
        "author": {"login": "utchy", "id": "MDQ6VXNlcjE="},
        "mergeCommit": {"oid": "c" * 40},
        # `pageInfo` is here because the document asks for it: an answer without
        # it is one this adapter refuses, so a fixture without it would be
        # driving a response GitHub does not send.
        "labels": {
            "pageInfo": {"hasNextPage": False},
            "nodes": [{"name": "security"}, {"name": "area/paths"}],
        },
        "closingIssuesReferences": {"pageInfo": {"hasNextPage": False}, "nodes": [{"number": 523}]},
        "commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": "SUCCESS"}}}]},
    }
    node.update(overrides)
    repository: dict[str, Any] = {
        "nameWithOwner": resolved_name,
        "pullRequests": {
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "nodes": [node],
        },
    }
    if private is not _ABSENT:
        repository["isPrivate"] = private
    return {"data": {"repository": repository}}


def _over_the_label_cap(template: dict[str, Any], number: int) -> dict[str, Any]:
    """One pull-request node whose labels overflow the single page they are asked for.

    A **record-scope** fault: the overflow is a fact about this pull request's
    own data. It carries ``LIMIT_EXCEEDED``, which is also what the listing's own
    page cap carries -- so an implementation that discriminated on the grade
    could not be told apart from one that discriminates on scope, except by
    driving both. That pair is
    ``test_one_grade_stops_the_listing_and_skips_one_of_its_nodes``.
    """
    return {
        **template,
        "number": number,
        "labels": {
            "pageInfo": {"hasNextPage": True},
            "nodes": [
                {"name": f"area/{index}"} for index in range(limits.MAX_LABELS_PER_PULL_REQUEST)
            ],
        },
    }


def _threads(*, has_more_comments: bool = False, resolved: bool = True) -> dict[str, Any]:
    return {
        "data": {
            "repository": {
                "nameWithOwner": REPOSITORY,
                "isPrivate": False,
                "pullRequest": {
                    "number": 12,
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": [
                            {
                                "id": "PRRT_1",
                                "isResolved": resolved,
                                "isOutdated": False,
                                "path": "src/order.py",
                                "line": 42,
                                "startLine": 40,
                                "resolvedBy": {"login": "utchy", "id": "MDQ6VXNlcjE="}
                                if resolved
                                else None,
                                "comments": {
                                    "pageInfo": {"hasNextPage": has_more_comments},
                                    "nodes": [
                                        {
                                            "id": "PRRC_1",
                                            "body": "Check the deadline before mutating state.",
                                            "createdAt": "2026-09-01T12:00:00Z",
                                            "originalCommit": {"oid": "d" * 40},
                                            "author": {"login": "utchy", "id": "MDQ6VXNlcjE="},
                                        }
                                    ],
                                },
                            }
                        ],
                    },
                },
            }
        }
    }


def _reviews(**overrides: Any) -> dict[str, Any]:
    """One page of top-level reviews, in the shape ``PULL_REQUEST_REVIEWS`` asks for."""
    node: dict[str, Any] = {
        "id": "PRR_1",
        "body": "The guard is right; the reason it gives is not.",
        "state": "CHANGES_REQUESTED",
        "submittedAt": "2026-09-01T13:00:00Z",
        "author": {"login": "utchy", "id": "MDQ6VXNlcjE="},
    }
    node.update(overrides)
    return {
        "data": {
            "repository": {
                "nameWithOwner": REPOSITORY,
                "isPrivate": False,
                "pullRequest": {
                    "number": 12,
                    "reviews": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": [node],
                    },
                },
            }
        }
    }


# -- the refusals that happen before anything is spawned ----------------------


@pytest.mark.asyncio
async def test_an_unallowlisted_repository_starts_no_process(
    tmp_path: pathlib.Path, fake_gh: FakeGh
) -> None:
    """AC-1. Not filtered after the fetch -- the fetch does not happen.

    The recorder being empty is the whole assertion, and it is the one a
    unit-level allowlist test cannot make: there, nothing could have been
    spawned anyway.
    """
    provider = _provider(tmp_path, fake_gh, entries=("acme/billing",))

    with pytest.raises(ReviewIngestRefusedError) as raised:
        await provider.list_pull_requests(PROJECT, REPOSITORY)

    assert raised.value.grade is RefusalGrade.REPOSITORY_NOT_ALLOWLISTED
    assert fake_gh.invocations == 0, (
        "the allowlist refused the repository and something was still spawned. "
        "An allowlist consulted after a process exists is a filter, not a control."
    )


@pytest.mark.asyncio
async def test_a_planted_transport_override_refuses_before_any_binary_probe(
    tmp_path: pathlib.Path, fake_gh: FakeGh
) -> None:
    """AC-3, half (ii-a): the refusal driver, which needs no ``gh`` and is never skipped.

    ADR-0030 splits clause 4(ii) in two because the halves have different
    requirements. This one drives the *control*: with the fixture in place the
    adapter refuses **before spawning**, so it is testable on any machine. The
    version read and the authentication probe are themselves spawns, which is
    why the check has to run ahead of both -- a check that ran after one has
    already handed the configuration a request.
    """
    config_dir = tmp_path / "ghconfig"
    config_dir.mkdir()
    # The filename comes from the constant here on purpose, and it is the one
    # place in the suite where that is right: what this asserts is the *order* --
    # refusal before spawn -- which holds whatever the file is called. Which name
    # `gh` actually writes is pinned test-side, once, by
    # `test_gh_transport_guard.py::test_the_file_this_check_opens_is_the_one_gh_writes`.
    (config_dir / GH_CONFIG_FILE).write_text(
        f"http_unix_socket: {tmp_path / 'planted.sock'}\n", encoding="utf-8"
    )
    provider = _provider(tmp_path, fake_gh, parent={"GH_CONFIG_DIR": str(config_dir)})

    with pytest.raises(ReviewIngestRefusedError) as raised:
        await provider.list_pull_requests(PROJECT, REPOSITORY)

    assert raised.value.grade is RefusalGrade.TRANSPORT_OVERRIDE_CONFIGURED
    assert fake_gh.invocations == 0, (
        "a transport override was configured and a process was still started. "
        "The version read and the auth probe are spawns: a check that runs after "
        "either has already handed the configuration a request."
    )


@pytest.mark.asyncio
async def test_no_gh_on_the_path_is_a_graded_refusal_not_a_traceback(
    tmp_path: pathlib.Path,
) -> None:
    """Clause 9's first state. Ingestion is optional; local knowledge is unaffected."""
    provider = _provider(tmp_path, None, parent={"PATH": str(tmp_path / "empty")})

    with pytest.raises(ReviewIngestRefusedError) as raised:
        await provider.list_pull_requests(PROJECT, REPOSITORY)

    assert raised.value.grade is RefusalGrade.TOOL_MISSING
    assert raised.value.remedy


# -- what the binary is asked, and under what environment ---------------------


@pytest.mark.asyncio
async def test_the_child_receives_the_constructed_environment_and_nothing_else(
    tmp_path: pathlib.Path, fake_gh: FakeGh
) -> None:
    """Clause 4, observed on the far side of the process boundary.

    ``test_gh_child_environment.py`` pins the mapping the adapter builds. This
    pins that the mapping is what a spawned child actually sees -- the half a
    unit test cannot reach, and the half a wrong ``env=`` keyword would break
    with every unit test green.
    """
    fake_gh.answer("prs", 1, _pull_requests())
    parent = {
        "GH_HOST": "evil.test",
        "GH_TOKEN": "parent-token",
        "HOME": str(tmp_path / "home"),
        "HTTPS_PROXY": "http://127.0.0.1:9",
        "PATH": "/parent/bin",
    }
    provider = _provider(tmp_path, fake_gh, parent=parent)

    await _listed(provider)
    seen = fake_gh.child_environment(1)
    passed = {name: value for name, value in seen.items() if name not in _SHELL_ADDED}

    assert passed == environment.child_environment(parent), (
        f"the child's own report of its environment is not what the adapter "
        f"constructed: {sorted(passed)}"
    )
    assert passed["PATH"] == environment.FIXED_PATH
    # Named individually as well as excluded by the equality, because these three
    # are the measured attack class and a reader of a failure should see which
    # one crossed rather than a set difference.
    assert "GH_HOST" not in seen
    assert "GH_TOKEN" not in seen
    assert "HTTPS_PROXY" not in seen


@pytest.mark.asyncio
async def test_the_child_cannot_read_the_parents_stdin(
    tmp_path: pathlib.Path, fake_gh: FakeGh
) -> None:
    """Clause 4 closes the environment; this closes the file descriptor beside it.

    ``stdin`` left unset is *inherited*, so a spawned ``gh`` reads whatever the
    calling process's fd 0 happens to be. **The parent's fd 0 is redirected here
    on purpose**: under ``pytest``'s default capture it is already
    ``/dev/null``, so a test that merely observed an empty read would pass
    against an inheriting spawn and could never fail. This one plants a marker
    on a pipe and puts the pipe on fd 0 first.

    The pipe's write end is closed before the spawn, so the stand-in child sees
    EOF either way and a regression is an assertion failure rather than a hang.
    """
    read_end, write_end = os.pipe()
    os.write(write_end, b"SECRET-ON-THE-PARENTS-STDIN")
    os.close(write_end)
    saved = os.dup(0)
    fake_gh.answer("prs", 1, _pull_requests())
    provider = _provider(tmp_path, fake_gh)
    try:
        os.dup2(read_end, 0)
        os.close(read_end)
        await _listed(provider)
    finally:
        os.dup2(saved, 0)
        os.close(saved)

    assert fake_gh.stdin_of(1) == "", (
        "the first spawned child read the parent's own stdin. Nothing but the "
        "adapter decides what a child may reach, and that includes its file "
        "descriptors."
    )


@pytest.mark.asyncio
async def test_the_recorded_argv_is_the_vector_the_clauses_describe(
    tmp_path: pathlib.Path, fake_gh: FakeGh
) -> None:
    """Clauses 2, 3 and 6 on a vector a real child reported receiving."""
    fake_gh.answer("prs", 1, _pull_requests())
    provider = _provider(tmp_path, fake_gh)

    await _listed(provider)
    argv = fake_gh.argv(3)

    assert argv[0] == "api"
    assert argv[1] == "graphql"
    assert "--hostname" in argv
    assert argv[argv.index("--hostname") + 1] == "github.com"
    assert "--paginate" not in argv
    assert "owner=acme" in argv
    assert "name=order-service" in argv
    assert not any(element.startswith("http") for element in argv)


@pytest.mark.asyncio
async def test_the_binary_the_child_is_spawned_as_is_the_resolved_absolute_path(
    tmp_path: pathlib.Path, fake_gh: FakeGh
) -> None:
    """Clause 5 on the path production takes, where the lookup actually happens.

    Every other driver here hands the provider an already-resolved ``binary``, so
    ``locate_binary`` -- the function that does the resolving -- never runs at
    all, and the vector's first element is whatever the test passed in. The unit
    pin next door asserts that element is *absolute*, which an unresolved
    ``shutil.which`` answer already is: on a ``PATH`` of absolute directories
    both forms pass it.

    So the discriminator is a **symlink**. ``gh`` is found through one, and what
    the child reports being spawned as is the link's target: a resolved path.
    Dropping ``.resolve()`` leaves the link itself in the vector -- still
    absolute, still on the recorded ``PATH``, and now a name whose meaning is
    whatever the link points at when the child is executed.
    """
    on_the_path = tmp_path / "on-the-path"
    on_the_path.mkdir()
    (on_the_path / "gh").symlink_to(fake_gh.binary)
    fake_gh.answer("prs", 1, _pull_requests())
    provider = _provider(tmp_path, None, parent={"PATH": str(on_the_path)})

    await _listed(provider)

    assert fake_gh.spawned_as(1) == str(fake_gh.binary.resolve())
    assert fake_gh.spawned_as(1) != str(on_the_path / "gh"), (
        "the child was spawned as the symlink `gh` was found through, not as the "
        "binary it resolves to. `locate_binary` resolves once, on purpose: an "
        "unresolved name is one whose target can change between the lookup and "
        "the spawn."
    )


@pytest.mark.asyncio
async def test_the_probes_run_once_per_adapter_rather_than_once_per_call(
    tmp_path: pathlib.Path, fake_gh: FakeGh
) -> None:
    """The authentication probe is itself a request, so repeating it spends a rate limit."""
    fake_gh.answer("prs", 1, _pull_requests())
    fake_gh.answer("threads", 1, _threads())
    provider = _provider(tmp_path, fake_gh)

    events = await _listed(provider)
    await provider.get_threads(PROJECT, events[0])

    assert fake_gh.argv(1) == ["--version"]
    assert fake_gh.argv(2)[:2] == ["auth", "status"]
    assert fake_gh.invocations == 4


# -- the refusals about the answer --------------------------------------------


@pytest.mark.asyncio
async def test_a_private_repository_is_refused_at_ingestion(
    tmp_path: pathlib.Path, fake_gh: FakeGh
) -> None:
    """AC-2. Allowlisted or not: this version ingests no advisory-private surface."""
    fake_gh.answer("prs", 1, _pull_requests(private=True))
    provider = _provider(tmp_path, fake_gh)

    with pytest.raises(ReviewIngestRefusedError) as raised:
        await provider.list_pull_requests(PROJECT, REPOSITORY)

    assert raised.value.grade is RefusalGrade.REPOSITORY_IS_PRIVATE
    assert raised.value.remedy


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "visibility",
    (_ABSENT, None, "false", 0),
    ids=("the field absent", "the field null", "the string false", "the integer zero"),
)
async def test_a_repository_that_does_not_resolve_as_public_is_refused(
    tmp_path: pathlib.Path, fake_gh: FakeGh, visibility: object
) -> None:
    """AC-2's other half: the check is "definitely public", not "not definitely private".

    ``repo.get("isPrivate") is not False`` reads as a fussy spelling of "is
    ``True``" until the response is not the one GitHub sends. It is not: an
    answer with the field **absent**, one with it ``null``, and one carrying a
    truthy-or-falsy value of another type are all documents this adapter can
    receive, and none of them says the repository is public. Under "is ``True``"
    every one of them is ingested.

    Four shapes rather than one because the discriminator is the *type*: the
    string ``"false"`` and the integer ``0`` both mean "not the boolean
    ``False``", and a check that coerced would let a repository through on a
    field it never understood. The two the round found unpinned are the first
    two -- absent and null -- which is what a partial GraphQL response looks
    like when a field errored.
    """
    fake_gh.answer("prs", 1, _pull_requests(private=visibility))
    provider = _provider(tmp_path, fake_gh)

    with pytest.raises(ReviewIngestRefusedError) as raised:
        await provider.list_pull_requests(PROJECT, REPOSITORY)

    assert raised.value.grade is RefusalGrade.REPOSITORY_IS_PRIVATE
    assert raised.value.remedy


@pytest.mark.asyncio
async def test_a_rename_redirect_is_refused_rather_than_followed(
    tmp_path: pathlib.Path, fake_gh: FakeGh
) -> None:
    """GitHub redirects a renamed repository, so an allowlisted name can resolve elsewhere."""
    fake_gh.answer("prs", 1, _pull_requests(resolved_name="acme/billing-service"))
    provider = _provider(tmp_path, fake_gh)

    with pytest.raises(ReviewIngestRefusedError) as raised:
        await provider.list_pull_requests(PROJECT, REPOSITORY)

    assert raised.value.grade is RefusalGrade.REPOSITORY_RESOLVED_ELSEWHERE


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "resolved",
    ("acme/order", "cme/order-servic"),
    ids=("a prefix of the entry", "a substring at neither end"),
)
async def test_a_resolved_name_contained_in_the_entry_is_still_a_different_repository(
    tmp_path: pathlib.Path, fake_gh: FakeGh, resolved: str
) -> None:
    """The rename check is an equality, and only an equality demonstrates that.

    ``acme/billing-service`` is not a substring of ``acme/order-service``, so the
    test above passes just as well against a check that asks whether the resolved
    name is *contained in* the entry -- a one-character edit, and a real one:
    ``!=`` and ``not in`` differ by two characters on that line. ``acme/order``
    is a repository somebody else may own, and under a containment check GitHub
    answering for it would be accepted as an answer about ``acme/order-service``.

    Both cases are proper substrings, one anchored at the start and one at
    neither end, so the pin does not rest on where the shorter name sits.
    """
    fake_gh.answer("prs", 1, _pull_requests(resolved_name=resolved))
    provider = _provider(tmp_path, fake_gh)

    with pytest.raises(ReviewIngestRefusedError) as raised:
        await provider.list_pull_requests(PROJECT, REPOSITORY)

    assert raised.value.grade is RefusalGrade.REPOSITORY_RESOLVED_ELSEWHERE


@pytest.mark.asyncio
async def test_a_case_difference_is_not_a_rename(tmp_path: pathlib.Path, fake_gh: FakeGh) -> None:
    """The other direction: GitHub cases names as it likes, and a byte comparison refuses truth.

    The record keeps the **configured** spelling, so a project's own records read
    one way however GitHub happens to answer.
    """
    fake_gh.answer("prs", 1, _pull_requests(resolved_name="Acme/Order-Service"))
    provider = _provider(tmp_path, fake_gh)

    events = await _listed(provider)

    assert events[0].repository == REPOSITORY


# -- the caps -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_limit_past_the_recorded_cap_stops_before_spawning(
    tmp_path: pathlib.Path, fake_gh: FakeGh
) -> None:
    """Clause 7: exceeding a cap is a reported stop, never a quiet smaller answer."""
    provider = _provider(tmp_path, fake_gh)

    with pytest.raises(ReviewIngestRefusedError) as raised:
        await provider.list_pull_requests(PROJECT, REPOSITORY, limit=limits.MAX_PULL_REQUESTS + 1)

    assert raised.value.grade is RefusalGrade.LIMIT_EXCEEDED
    assert str(limits.MAX_PULL_REQUESTS) in str(raised.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", (0, -1), ids=("zero", "negative"))
async def test_a_limit_below_one_is_refused_with_a_summary_that_is_true(
    tmp_path: pathlib.Path, fake_gh: FakeGh, limit: int
) -> None:
    """The two bounds on ``limit`` share a grade and must not share a summary.

    Both are reported stops before anything is spawned, and an operator does the
    same thing about either. But the cap sentence -- "the recorded cap is 500, so
    the run stopped rather than quietly returning fewer than were asked for" --
    says nothing true about a request for zero, and a summary is the field that
    describes *this* run.
    """
    provider = _provider(tmp_path, fake_gh)

    with pytest.raises(ReviewIngestRefusedError) as raised:
        await provider.list_pull_requests(PROJECT, REPOSITORY, limit=limit)

    assert raised.value.grade is RefusalGrade.LIMIT_EXCEEDED
    assert str(limit) in str(raised.value)
    assert str(limits.MAX_PULL_REQUESTS) not in str(raised.value)
    assert fake_gh.invocations == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "limit",
    (10**4299, 10**4300, 10**4301, -(10**4301)),
    ids=(
        "the most digits str() will render",
        "one digit past what str() will render",
        "two digits past it",
        "the same, below one",
    ),
)
async def test_a_limit_too_large_to_render_is_still_a_summary_a_reader_can_act_on(
    tmp_path: pathlib.Path, fake_gh: FakeGh, limit: int
) -> None:
    """A caller's own argument is a value from outside, and it was interpolated raw.

    Two failures, one line apart, and the parameters are chosen to separate them.
    Past ``sys.get_int_max_str_digits()`` -- 4300 by default -- ``str()`` of an
    integer **raises**, so the f-string that built this summary put a
    ``ValueError`` out of the refusal path: the one path ADR-0030 clause 9 says
    must answer with an envelope. At exactly 4300 digits it rendered, and then
    the summary ran past the type's cut and lost its own tail -- so the operator
    was told a number was refused and not what the cap was.

    **``10**4299`` is the one that has 4300 digits**, and until it was added this
    docstring described a regime none of the parameters reached: ``10**4300``
    has 4301 and raises like the two after it, so every case was the raising one
    and the renders-then-overruns half went undriven while the prose claimed it.

    Both are asserted here: a graded envelope for every value, and a summary that
    still names ``MAX_PULL_REQUESTS`` in the over-the-cap cases. Nothing is
    spawned either way, so the recorder stays empty.
    """
    provider = _provider(tmp_path, fake_gh)

    with pytest.raises(ReviewIngestRefusedError) as raised:
        await provider.list_pull_requests(PROJECT, REPOSITORY, limit=limit)

    assert raised.value.grade is RefusalGrade.LIMIT_EXCEEDED
    assert raised.value.remedy
    assert len(raised.value.envelope.summary) <= MAX_REFUSAL_SUMMARY_CHARS
    if limit > 0:
        assert str(limits.MAX_PULL_REQUESTS) in str(raised.value), (
            "the summary was cut by the type before it reached the cap it was "
            "reporting, so the reader is told a number was refused and not what "
            "the bound is. Cutting the value is what keeps the sentence."
        )
    assert fake_gh.invocations == 0


@pytest.mark.asyncio
async def test_a_variable_too_large_to_render_refuses_instead_of_raising(
    tmp_path: pathlib.Path, fake_gh: FakeGh
) -> None:
    """The construction seam: an argv element that cannot be *built* never reaches a spawn.

    ``_start``'s ``(OSError, ValueError)`` catch closes what ``execve`` declines,
    and a review round credited it with closing "a value some later caller
    builds". It cannot: ``graphql_vector`` renders each element with an f-string,
    and ``str()`` of a large enough integer raises there -- a whole stage before
    any process exists. ``get_threads`` left that as a traceback, after its two
    probes had already been spawned.

    The event is built with a number the domain accepts and the interpreter will
    not render, which is the shape a caller can hand this adapter directly. The
    probes still run, because they are the adapter's one-time setup and the check
    belongs where every element is built rather than at each caller that might
    reach it -- but the answer is an envelope.
    """
    fake_gh.answer("prs", 1, _pull_requests())
    provider = _provider(tmp_path, fake_gh)
    (event,) = await _listed(provider)
    unrenderable = dataclasses.replace(event, number=10**5000)

    with pytest.raises(ReviewIngestRefusedError) as raised:
        await provider.get_threads(PROJECT, unrenderable)

    assert raised.value.grade is RefusalGrade.TOOL_FAILED
    assert "number" in str(raised.value), "the refusal does not name the variable it refused"
    assert raised.value.remedy


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "limit",
    (1, limits.MAX_PULL_REQUESTS),
    ids=("the smallest read there is", "the recorded cap exactly"),
)
async def test_a_limit_at_either_boundary_is_read_rather_than_refused(
    tmp_path: pathlib.Path, fake_gh: FakeGh, limit: int
) -> None:
    """The accepting side of both bounds, which is what makes them boundaries.

    ``limit < 1`` and ``limit > MAX_PULL_REQUESTS`` are the two refusals, and
    each has a driver one step outside it. Neither says anything about the value
    *on* the boundary, so ``<`` widening to ``<=`` and ``>`` widening to ``>=``
    would refuse a request the contract accepts with every test green -- and the
    caller would read it as the cap, because the grade and the summary are the
    cap's.

    **What is derived from what.** The number `MAX_PULL_REQUESTS` *is* is pinned
    test-side in ``test_gh_argument_vector.py``; what this drives is the
    comparison, and a comparison is killed at whatever value the constant holds.
    """
    fake_gh.answer("prs", 1, _pull_requests())
    provider = _provider(tmp_path, fake_gh)

    events = await _listed(provider, limit=limit)

    assert [event.number for event in events] == [12]


@pytest.mark.asyncio
async def test_a_read_stops_at_the_limit_rather_than_one_past_it(
    tmp_path: pathlib.Path, fake_gh: FakeGh
) -> None:
    """``limit`` is how many pull requests come back, not how many are exceeded.

    Every other driver of ``limit`` asks for more than the page carries, so the
    stop never fires and ``len(events) >= limit`` reads the same as
    ``len(events) > limit``. The page here carries **three** and the caller asks
    for two: the correct read answers two, and the off-by-one answers three --
    one more record than the caller asked for, from a page it had already
    decided to stop reading.
    """
    page = _pull_requests()
    nodes = page["data"]["repository"]["pullRequests"]["nodes"]
    nodes[:] = [{**nodes[0], "number": number} for number in (14, 13, 12)]
    fake_gh.answer("prs", 1, page)
    provider = _provider(tmp_path, fake_gh)

    events = await _listed(provider, limit=2)

    assert [event.number for event in events] == [14, 13], (
        f"the page carried three pull requests, the caller asked for two, and "
        f"{len(events)} came back."
    )


@pytest.mark.asyncio
async def test_a_response_that_never_stops_paging_is_stopped_by_the_page_cap(
    tmp_path: pathlib.Path, fake_gh: FakeGh
) -> None:
    """A repository -- or a hostile response -- cannot keep this adapter asking.

    **The request count is the assertion the grade cannot make.** A loop that
    kept asking to twenty-five pages stops an endless response too, and reports
    the same grade with the same ``limits.MAX_PAGES`` interpolated into the same
    sentence -- the refusal names the *constant*, never how many pages were
    actually read -- so a message-only check passes against a read that made five
    more requests than the record says it may. What bounds the work is how many
    times the child was spawned, which is counted here against that constant.
    """
    endless = _threads()
    endless["data"]["repository"]["pullRequest"]["reviewThreads"]["pageInfo"] = {
        "hasNextPage": True,
        "endCursor": "CURSOR",
    }
    fake_gh.answer("threads", 1, endless)
    fake_gh.answer("threads", 2, endless)
    fake_gh.answer("prs", 1, _pull_requests())
    provider = _provider(tmp_path, fake_gh)
    events = await _listed(provider)
    before = fake_gh.invocations

    with pytest.raises(ReviewIngestRefusedError) as raised:
        await provider.get_threads(PROJECT, events[0])

    assert raised.value.grade is RefusalGrade.LIMIT_EXCEEDED
    assert str(limits.MAX_PAGES) in str(raised.value)
    assert fake_gh.invocations - before == limits.MAX_PAGES, (
        f"the read asked for {fake_gh.invocations - before} pages before it "
        f"stopped, and the recorded cap it reports is {limits.MAX_PAGES}. The "
        f"refusal's wording is the same either way; the spawn count is not."
    )


@pytest.mark.asyncio
async def test_a_thread_past_the_comment_cap_is_reported_not_truncated(
    tmp_path: pathlib.Path, fake_gh: FakeGh
) -> None:
    """A record that looks whole and is not is worse than a refusal that says so."""
    fake_gh.answer("prs", 1, _pull_requests())
    fake_gh.answer("threads", 1, _threads(has_more_comments=True))
    provider = _provider(tmp_path, fake_gh)
    events = await _listed(provider)

    with pytest.raises(ReviewIngestRefusedError) as raised:
        await provider.get_threads(PROJECT, events[0])

    assert raised.value.grade is RefusalGrade.LIMIT_EXCEEDED
    assert str(limits.MAX_COMMENTS_PER_THREAD) in str(raised.value)


# -- what a refusal may spell out of a response --------------------------------

#: A bidirectional override: one character that reorders everything printed after
#: it, and **not** something ``cli/output.py``'s ``escape_terminal_controls``
#: touches -- that escapes C0, C1 and DEL, and U+202E is a format character in
#: none of those ranges. So the only thing standing between a node id GitHub
#: chose and an operator's terminal is whether the producer quoted it.
#:
#: It is also the expander the ordering argument needs: ``repr`` renders it as a
#: six-character escape, so bounding before quoting would let a value cut to the
#: echo bound come back six times that long.
#:
#: Spelled by code point because a literal one is invisible in a diff and
#: reorders every line it sits on -- which ``ruff``'s ``PLE2502`` also refuses.
_RIGHT_TO_LEFT_OVERRIDE: Final = "\u202e"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("has_more_comments", "grade"),
    ((True, RefusalGrade.LIMIT_EXCEEDED), (False, RefusalGrade.TOOL_FAILED)),
    ids=("over-the-comment-cap", "with-no-comments"),
)
async def test_a_thread_id_carrying_a_bidi_override_is_quoted_into_its_refusal(
    tmp_path: pathlib.Path,
    fake_gh: FakeGh,
    has_more_comments: bool,
    grade: RefusalGrade,
) -> None:
    """Both refusals that name a thread id, driven with a hostile one.

    The id is the channel a stand-in has to supply, because nothing the *caller*
    passes reaches these sentences: it is ``response.required_text(node["id"])``,
    a string the provider chose. Rendered bare it reordered the sentence that
    named it, and it survived the CLI's own escape, which does not reach a
    format character.

    Two rows because the two refusals are two producers of one sentence shape and
    a fix applied to one is invisible in a test of the other. The positive
    control is the second assertion -- the id has to still be *there*, escaped,
    or "no raw override reached the summary" would hold for a refusal that
    stopped naming the thread at all.
    """
    hostile_id = f"PRRT_{_RIGHT_TO_LEFT_OVERRIDE}42"
    page = _threads(has_more_comments=has_more_comments)
    thread = page["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"][0]
    thread["id"] = hostile_id
    if not has_more_comments:
        thread["comments"]["nodes"] = []
    fake_gh.answer("prs", 1, _pull_requests())
    fake_gh.answer("threads", 1, page)
    provider = _provider(tmp_path, fake_gh)
    events = await _listed(provider)

    with pytest.raises(ReviewIngestRefusedError) as raised:
        await provider.get_threads(PROJECT, events[0])

    summary = raised.value.envelope.summary
    assert raised.value.grade is grade
    assert _RIGHT_TO_LEFT_OVERRIDE not in summary, (
        f"a raw bidirectional override a response chose reached the summary: {summary!r}"
    )
    assert "\\u202e" in summary, "the id is no longer named at all, escaped or otherwise"
    assert "PRRT_" in summary


@pytest.mark.asyncio
async def test_a_hostile_repository_on_an_event_is_refused_before_this_sentence_exists(
    tmp_path: pathlib.Path, fake_gh: FakeGh
) -> None:
    """The precondition ``event.repository``'s raw interpolation rests on.

    Those refusal sentences spell ``event.repository`` unrouted, and the recorded
    reason is that it is the operator's own allowlisted name rather than a value
    a response chose. That holds only because ``get_threads`` calls
    ``_allowlisted`` **first**, so a ``ReviewEvent`` a caller built with a
    hostile repository never reaches the sentence.

    Driven from the caller's side, which is the only side that can supply one:
    the refusal has to be the allowlist's, the echo in *it* has to be quoted, and
    nothing may have been spawned.
    """
    fake_gh.answer("prs", 1, _pull_requests())
    provider = _provider(tmp_path, fake_gh)
    events = await _listed(provider)
    before = fake_gh.invocations
    hostile = dataclasses.replace(
        events[0], repository=f"acme/order{_RIGHT_TO_LEFT_OVERRIDE}service"
    )

    with pytest.raises(ReviewIngestRefusedError) as raised:
        await provider.get_threads(PROJECT, hostile)

    summary = raised.value.envelope.summary
    assert raised.value.grade is RefusalGrade.REPOSITORY_NOT_ALLOWLISTED
    assert _RIGHT_TO_LEFT_OVERRIDE not in summary, (
        f"the allowlist refusal published the override raw: {summary!r}"
    )
    assert fake_gh.invocations == before, "a repository the allowlist refuses was contacted"


# -- clause 9: an answer this adapter cannot read is an envelope, never a traceback


@pytest.mark.asyncio
@pytest.mark.parametrize("number", (0, -1), ids=("zero", "negative"))
async def test_a_pull_request_number_below_one_is_a_graded_refusal(
    tmp_path: pathlib.Path, fake_gh: FakeGh, number: int
) -> None:
    """``ReviewEvent`` bounds its number, and the bound must not be reached as a traceback.

    ``__post_init__`` raises ``InvariantViolationError`` on a number below one.
    That is the domain doing its job, and it is the wrong exception to leave this
    adapter by: clause 9 says every way this arm declines carries the same
    envelope. The refusal has to happen on the response, before the record is
    constructed from it.
    """
    fake_gh.answer("prs", 1, _pull_requests(number=number))
    provider = _provider(tmp_path, fake_gh)

    with pytest.raises(ReviewIngestRefusedError) as raised:
        await provider.list_pull_requests(PROJECT, REPOSITORY)

    assert raised.value.grade is RefusalGrade.TOOL_FAILED
    assert raised.value.remedy


@pytest.mark.asyncio
async def test_a_linked_issue_number_below_one_is_a_graded_refusal(
    tmp_path: pathlib.Path, fake_gh: FakeGh
) -> None:
    """The same check where no domain invariant would have caught it.

    A linked issue number is recorded as a string, so a zero reaches
    ``linked_issue_ids`` as ``"0"`` and reads downstream as an issue. Nothing
    below this adapter would have refused it.

    The refusal is the pull request's own, so it is answered as a skip: the
    unreadable number is a fact about *this* pull request's data and says nothing
    about the repository the rest of the window comes from.
    """
    fake_gh.answer(
        "prs",
        1,
        _pull_requests(
            closingIssuesReferences={"pageInfo": {"hasNextPage": False}, "nodes": [{"number": 0}]}
        ),
    )
    provider = _provider(tmp_path, fake_gh)

    skip = await _one_skip(provider)

    assert skip.number == 12
    assert skip.envelope.grade is RefusalGrade.TOOL_FAILED
    assert "linked issue number" in skip.envelope.summary


@pytest.mark.asyncio
async def test_a_cursor_carrying_a_nul_is_refused_rather_than_spawned(
    tmp_path: pathlib.Path, fake_gh: FakeGh
) -> None:
    """The one response value that re-enters the argument vector, checked for its bytes.

    ``asyncio.create_subprocess_exec`` raises ``ValueError`` on an argument with
    a NUL in it, and ``_start`` catches ``OSError``, so this used to leave the
    adapter as a traceback. The second page is canned as well, so a green result
    here cannot come from the paging simply not happening.
    """
    first = _pull_requests()
    first["data"]["repository"]["pullRequests"]["pageInfo"] = {
        "hasNextPage": True,
        "endCursor": "CURSOR\x00-1",
    }
    fake_gh.answer("prs", 1, first)
    fake_gh.answer("prs", 2, _pull_requests(number=11))
    provider = _provider(tmp_path, fake_gh)
    before = fake_gh.invocations

    with pytest.raises(ReviewIngestRefusedError) as raised:
        await provider.list_pull_requests(PROJECT, REPOSITORY)

    assert raised.value.grade is RefusalGrade.TOOL_FAILED
    assert raised.value.remedy
    assert fake_gh.invocations == before + 3, (
        "the version probe, the auth probe and the first page -- and no second "
        "page: the cursor is refused before it can be spawned"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cursor",
    ("CURSOR\ud800", "CURSOR\udc80"),
    ids=("a lone high surrogate", "a surrogate in the surrogateescape range"),
)
async def test_a_cursor_that_cannot_be_encoded_is_refused_rather_than_spawned(
    tmp_path: pathlib.Path, fake_gh: FakeGh, cursor: str
) -> None:
    """A surrogate is wire-legal JSON and is not an argument, so it stops at the boundary.

    **Both arrive through ``json.loads``, not through a Python literal handed to
    the adapter.** ``json.dumps`` writes each as the six-character escape a real
    GitHub answer could carry -- the assertion below reads the canned file back
    and checks the escape is what the child prints -- and the adapter decodes it
    into a lone surrogate the way it decodes every other response.

    The two behave differently one layer down, which is why both are driven.
    ``\\ud800`` cannot be encoded at all: it raised ``UnicodeEncodeError`` out of
    ``create_subprocess_exec``, an exception the spawn's ``except OSError``
    did not catch. ``\\udc80`` raises nothing -- ``surrogateescape`` maps it back
    to the byte ``0x80`` and the request is spawned with a cursor GitHub never
    sent. A check on error handling alone would miss the second entirely.

    The second page is canned, so a green result cannot come from the paging
    simply not happening.
    """
    first = _pull_requests()
    first["data"]["repository"]["pullRequests"]["pageInfo"] = {
        "hasNextPage": True,
        "endCursor": cursor,
    }
    fake_gh.answer("prs", 1, first)
    fake_gh.answer("prs", 2, _pull_requests(number=11))
    provider = _provider(tmp_path, fake_gh)

    assert (
        cursor.encode("ascii", "backslashreplace") in (fake_gh.directory / "prs1.json").read_bytes()
    ), (
        "the canned answer does not carry the surrogate as a JSON escape, so this "
        "test is driving a Python value rather than something a response can say"
    )

    with pytest.raises(ReviewIngestRefusedError) as raised:
        await provider.list_pull_requests(PROJECT, REPOSITORY)

    assert raised.value.grade is RefusalGrade.TOOL_FAILED
    assert raised.value.remedy
    assert fake_gh.invocations == 3, (
        "the version probe, the auth probe and the first page -- and no second "
        "page: the cursor is refused before it can be spawned"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cursor",
    ("", None, 12),
    ids=("an empty cursor", "a null cursor", "an integer cursor"),
)
async def test_another_page_with_no_usable_cursor_is_refused_not_read_as_the_last_page(
    tmp_path: pathlib.Path, fake_gh: FakeGh, cursor: object
) -> None:
    """``hasNextPage`` true and no cursor is a partial read, and it is graded as one.

    The three shapes are one decision: whatever ``endCursor`` is, the answer said
    there is more, and this adapter cannot ask for it. Returning the first page
    then presents part of an answer as the whole -- exactly what
    ``MAX_LINKED_ISSUES`` and the comment cap exist to refuse, arrived at from the
    other direction and silently.

    The second page is canned here too, so the refusal cannot be the paging
    failing for want of a response.
    """
    first = _pull_requests()
    first["data"]["repository"]["pullRequests"]["pageInfo"] = {
        "hasNextPage": True,
        "endCursor": cursor,
    }
    fake_gh.answer("prs", 1, first)
    fake_gh.answer("prs", 2, _pull_requests(number=11))
    provider = _provider(tmp_path, fake_gh)

    with pytest.raises(ReviewIngestRefusedError) as raised:
        await provider.list_pull_requests(PROJECT, REPOSITORY)

    assert raised.value.grade is RefusalGrade.TOOL_FAILED
    assert "pull requests" in str(raised.value)
    assert raised.value.remedy
    assert fake_gh.invocations == 3, (
        "a second page was asked for with a cursor the answer did not supply"
    )


@pytest.mark.asyncio
async def test_a_timestamp_with_no_offset_is_refused_rather_than_read_as_local_time(
    tmp_path: pathlib.Path, fake_gh: FakeGh
) -> None:
    """A naive datetime is a wrong measurement, and nothing below this adapter refuses one.

    ``datetime.fromisoformat`` parses ``2026-09-01T10:00:00`` happily and answers
    a datetime with no ``tzinfo``. Nothing in the domain rejects that --
    ``ReviewEvent`` bounds its number and its identifiers, not its timestamps --
    so an accepted naive value is recorded, compared against aware ones, and read
    downstream as the instant it names. Which instant that is depends on the
    reader's own zone, and no error ever fires.

    The refusal is the honest answer: this adapter records no timestamp it cannot
    place on a timeline. ``createdAt`` is the field driven because it is required
    -- ``mergedAt`` merely becomes ``None``, which is a quieter version of the
    same decision and a weaker thing to assert.
    """
    fake_gh.answer("prs", 1, _pull_requests(createdAt="2026-09-01T10:00:00"))
    provider = _provider(tmp_path, fake_gh)

    skip = await _one_skip(provider)

    assert skip.number == 12
    assert skip.envelope.grade is RefusalGrade.TOOL_FAILED
    assert "createdAt" in skip.envelope.summary


@pytest.mark.asyncio
async def test_a_deleted_author_is_recorded_under_githubs_own_name_for_one(
    tmp_path: pathlib.Path, fake_gh: FakeGh
) -> None:
    """``ghost`` is GitHub's word, spelled out here so the record keeps meaning one thing.

    A deleted account is the one author the response cannot identify, and
    ``ReviewParticipant.external_id`` may not be empty -- so the adapter puts a
    constant there. Which constant is not a free choice: ``ghost`` is what GitHub
    itself calls the account, so a reader meeting it in a record recognises it,
    and every deleted author collapses onto one participant rather than becoming
    a new person per event.

    The literal is written here rather than imported from ``GHOST_LOGIN``,
    because a test that reads the constant agrees with whatever the constant
    becomes. Changing it is a change to records already written -- the same
    account read as two people across two ingests -- and that is a decision, not
    a rename.
    """
    fake_gh.answer("prs", 1, _pull_requests(author=None))
    provider = _provider(tmp_path, fake_gh)

    (event,) = await _listed(provider)

    assert event.author.external_id == "ghost"
    assert event.author.display_name == "ghost"


@pytest.mark.parametrize(
    "author",
    ({"login": "utchy"}, {"login": "utchy", "id": None}),
    ids=("no id key at all", "an explicit null id"),
)
@pytest.mark.asyncio
async def test_an_author_the_response_gave_no_node_id_is_recorded_under_its_login(
    tmp_path: pathlib.Path, fake_gh: FakeGh, author: dict[str, Any]
) -> None:
    """``external_id`` is *node id or login*, and the fallback half is driven here.

    Two documents GitHub can send: an ``Actor`` implementation that is not a
    ``Node``, so the inline fragment contributes no ``id`` at all, and a
    partly-errored response that carries the key set to ``null`` beside a ``data``
    that otherwise looks ordinary -- the same shape ``response.boolean``'s
    docstring describes. Both leave the record identified by a string its owner
    chose and can change.

    **This case is why the ingestion gate scans ``external_id``.** Without a
    fixture whose author has no node id, the fallback is unreached by every
    adapter test: replacing the expression with ``node_id`` alone left 226 tests
    green in PR #596 round 1, while the login it dropped was the value that
    reached a landed file.
    """
    fake_gh.answer("prs", 1, _pull_requests(author=author))
    provider = _provider(tmp_path, fake_gh)

    (event,) = await _listed(provider)

    assert event.author.external_id == "utchy"
    assert event.author.display_name == "utchy"


@pytest.mark.asyncio
async def test_a_merged_pull_request_with_no_merge_commit_is_refused(
    tmp_path: pathlib.Path, fake_gh: FakeGh
) -> None:
    """``ReviewEvent`` requires a merged pull request to record its merge commit.

    Same argument as the number bound: the domain invariant is real, and reaching
    it from here would be a traceback rather than an envelope. The summary names
    the pull request so a reader knows which answer was unreadable -- and the
    skip names it a second way, by number, so a caller need not parse a sentence
    to know which record is missing.
    """
    fake_gh.answer("prs", 1, _pull_requests(merged=True, mergeCommit=None))
    provider = _provider(tmp_path, fake_gh)

    skip = await _one_skip(provider)

    assert skip.number == 12
    assert skip.repository == REPOSITORY
    assert skip.envelope.grade is RefusalGrade.TOOL_FAILED
    assert f"{REPOSITORY}#12" in skip.envelope.summary


@pytest.mark.asyncio
async def test_a_pull_request_past_the_linked_issue_cap_is_reported_not_truncated(
    tmp_path: pathlib.Path, fake_gh: FakeGh
) -> None:
    """The same treatment the comment cap gets, on the connection that had none.

    ``closingIssuesReferences`` paginates like every other connection and this
    adapter follows no cursor into it, so a pull request closing forty issues
    used to arrive looking exactly like one closing twenty -- a record naming
    half the issues, with nothing in it saying so.

    The payload carries a full page **and** ``hasNextPage``, which is what GitHub
    sends for the fortieth issue: a test that only over-filled ``nodes`` would
    pass against an implementation that reads neither.
    """
    over = _pull_requests(
        closingIssuesReferences={
            "pageInfo": {"hasNextPage": True},
            "nodes": [{"number": issue} for issue in range(1, limits.MAX_LINKED_ISSUES + 1)],
        }
    )
    fake_gh.answer("prs", 1, over)
    provider = _provider(tmp_path, fake_gh)

    skip = await _one_skip(provider)

    assert skip.number == 12
    assert skip.envelope.grade is RefusalGrade.LIMIT_EXCEEDED
    assert f"{REPOSITORY}#12" in skip.envelope.summary
    assert str(limits.MAX_LINKED_ISSUES) in skip.envelope.summary


@pytest.mark.asyncio
async def test_more_linked_issues_than_the_cap_is_refused_without_the_flag_saying_so(
    tmp_path: pathlib.Path, fake_gh: FakeGh
) -> None:
    """The other arm of the same guard, which nothing drove and a mutation deleted.

    The test above sends ``hasNextPage`` **and** a full page; this one sends a
    page one longer than the cap with ``hasNextPage`` false, which is what an
    answer looks like if the page size in the document and the constant ever
    disagree. With only the first test, deleting the ``len(nodes)`` clause is a
    change no test notices -- and then the constant is decorative and the
    effective cap is whatever the query literal says.
    """
    over = _pull_requests(
        closingIssuesReferences={
            "pageInfo": {"hasNextPage": False},
            "nodes": [{"number": issue} for issue in range(1, limits.MAX_LINKED_ISSUES + 2)],
        }
    )
    fake_gh.answer("prs", 1, over)
    provider = _provider(tmp_path, fake_gh)

    skip = await _one_skip(provider)

    assert skip.envelope.grade is RefusalGrade.LIMIT_EXCEEDED
    assert str(limits.MAX_LINKED_ISSUES) in skip.envelope.summary


@pytest.mark.asyncio
async def test_a_pull_request_past_the_label_cap_is_reported_not_truncated(
    tmp_path: pathlib.Path, fake_gh: FakeGh
) -> None:
    """The linked-issue treatment on the connection ADR-0030 puts author text in.

    ``labels`` paginates like every other connection and this adapter follows no
    cursor into it, so a pull request carrying sixty labels would arrive with
    fifty recorded and nothing saying the other ten exist. That is worse here than
    for a structural connection: a label is content the ingestion scan reads, so
    a silently dropped one is content nothing looked at.

    The payload carries a full page **and** ``hasNextPage``, which is what GitHub
    sends for the fifty-first label.
    """
    over = _pull_requests(
        labels={
            "pageInfo": {"hasNextPage": True},
            "nodes": [
                {"name": f"area/{index}"} for index in range(limits.MAX_LABELS_PER_PULL_REQUEST)
            ],
        }
    )
    fake_gh.answer("prs", 1, over)
    provider = _provider(tmp_path, fake_gh)

    skip = await _one_skip(provider)

    assert skip.number == 12
    assert skip.envelope.grade is RefusalGrade.LIMIT_EXCEEDED
    assert f"{REPOSITORY}#12" in skip.envelope.summary
    assert str(limits.MAX_LABELS_PER_PULL_REQUEST) in skip.envelope.summary


@pytest.mark.asyncio
async def test_more_labels_than_the_cap_is_refused_without_the_flag_saying_so(
    tmp_path: pathlib.Path, fake_gh: FakeGh
) -> None:
    """The other arm, for the reason its linked-issue twin has one.

    A page one longer than the cap with ``hasNextPage`` false is what an answer
    looks like if the ``first:`` literal in the document and the constant ever
    disagree. Without this case, deleting the node-count clause is a change no
    test notices and the effective cap becomes whatever the document says.
    """
    over = _pull_requests(
        labels={
            "pageInfo": {"hasNextPage": False},
            "nodes": [
                {"name": f"area/{index}"} for index in range(limits.MAX_LABELS_PER_PULL_REQUEST + 1)
            ],
        }
    )
    fake_gh.answer("prs", 1, over)
    provider = _provider(tmp_path, fake_gh)

    skip = await _one_skip(provider)

    assert skip.envelope.grade is RefusalGrade.LIMIT_EXCEEDED
    assert str(limits.MAX_LABELS_PER_PULL_REQUEST) in skip.envelope.summary


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name",
    (None, 12, _ABSENT, ""),
    ids=("a null name", "a numeric name", "no name field", "an empty name"),
)
async def test_a_label_whose_name_is_not_text_is_refused_rather_than_recorded(
    tmp_path: pathlib.Path, fake_gh: FakeGh, name: object
) -> None:
    """A label reaches the record as a string or the answer is refused.

    ``labels`` is untrusted content the scan reads, and every shape here is one a
    partly-errored GraphQL response can be -- the errored field comes back
    ``null`` beside a ``data`` that otherwise looks ordinary. Folding any of them
    into the empty string would put a label in the record that nobody wrote, and
    into the scan a value that came from this adapter rather than from GitHub.
    """
    label: dict[str, object] = {} if name is _ABSENT else {"name": name}
    fake_gh.answer(
        "prs", 1, _pull_requests(labels={"pageInfo": {"hasNextPage": False}, "nodes": [label]})
    )
    provider = _provider(tmp_path, fake_gh)

    skip = await _one_skip(provider)

    assert skip.envelope.grade is RefusalGrade.TOOL_FAILED
    assert "label name" in skip.envelope.summary


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "page_info",
    (_ABSENT, None, {"hasNextPage": "true"}, {}),
    ids=(
        "no pageInfo at all",
        "a null pageInfo",
        "hasNextPage as the string true",
        "a pageInfo with no hasNextPage",
    ),
)
async def test_a_label_paging_flag_that_is_not_a_boolean_is_refused_not_read_as_false(
    tmp_path: pathlib.Path, fake_gh: FakeGh, page_info: object
) -> None:
    """AC-3 on the connection whose overflow costs content rather than structure.

    Every shape here reads as *there is no next page* under a comparison against
    ``is True``, so the cap never fires and a truncated label set is recorded as
    a whole one. The nodes are inside the cap, so a refusal cannot be the count
    arm firing instead of the flag arm.
    """
    labels: dict[str, object] = {"nodes": [{"name": "security"}]}
    if page_info is not _ABSENT:
        labels["pageInfo"] = page_info
    fake_gh.answer("prs", 1, _pull_requests(labels=labels))
    provider = _provider(tmp_path, fake_gh)

    skip = await _one_skip(provider)

    assert skip.envelope.grade is RefusalGrade.TOOL_FAILED
    assert "labels" in skip.envelope.summary


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "page_info",
    (_ABSENT, None, {"hasNextPage": "true", "endCursor": "CURSOR-1"}, {"endCursor": "CURSOR-1"}),
    ids=(
        "no pageInfo at all",
        "a null pageInfo",
        "hasNextPage as the string true",
        "a pageInfo with no hasNextPage",
    ),
)
async def test_a_paging_flag_that_is_not_a_boolean_is_refused_not_read_as_false(
    tmp_path: pathlib.Path, fake_gh: FakeGh, page_info: object
) -> None:
    """Every shape here used to read as *there is no next page*, and return.

    That is the silent truncation the linked-issue cap and the comment cap exist
    to replace with a report, arriving through the door those caps do not watch:
    ``x is True`` is false for a missing field, for ``null``, and for the string
    ``"true"`` alike, so a partly-errored response -- where the errored field
    comes back ``null`` beside an otherwise ordinary ``data`` -- ended the read
    and the caller was handed a page as though it were the answer.

    The second page is canned, so a refusal cannot be the paging failing for want
    of a response.
    """
    first = _pull_requests()
    connection = first["data"]["repository"]["pullRequests"]
    if page_info is _ABSENT:
        del connection["pageInfo"]
    else:
        connection["pageInfo"] = page_info
    fake_gh.answer("prs", 1, first)
    fake_gh.answer("prs", 2, _pull_requests(number=11))
    provider = _provider(tmp_path, fake_gh)

    with pytest.raises(ReviewIngestRefusedError) as raised:
        await provider.list_pull_requests(PROJECT, REPOSITORY)

    assert raised.value.grade is RefusalGrade.TOOL_FAILED
    assert "hasNextPage" in str(raised.value)
    assert raised.value.remedy


@pytest.mark.asyncio
async def test_a_merged_flag_that_is_not_a_boolean_is_refused_not_read_as_unmerged(
    tmp_path: pathlib.Path, fake_gh: FakeGh
) -> None:
    """``merged`` selects a guard, so reading it loosely disables the guard silently.

    The payload is the shape that makes the failure visible: ``merged`` as the
    string ``"true"`` and **no merge commit**. Under ``node.get("merged") is
    True`` the string is not-merged, the merge-commit guard never runs, and a
    record is written saying the pull request was never merged -- from an answer
    that says it was.
    """
    fake_gh.answer("prs", 1, _pull_requests(merged="true", mergeCommit=None))
    provider = _provider(tmp_path, fake_gh)

    skip = await _one_skip(provider)

    assert skip.envelope.grade is RefusalGrade.TOOL_FAILED
    assert "merged" in skip.envelope.summary


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "flags",
    ({"isResolved": "true"}, {"isOutdated": None}),
    ids=("isResolved as the string true", "isOutdated null"),
)
async def test_a_thread_flag_that_is_not_a_boolean_is_refused_not_folded_into_open(
    tmp_path: pathlib.Path, fake_gh: FakeGh, flags: dict[str, Any]
) -> None:
    """The thread's two state flags fold three ways, so a bad one is unrecoverable.

    ``isResolved`` and ``isOutdated`` choose between ``RESOLVED``, ``OUTDATED``
    and ``OPEN``, and every unreadable value folds into ``OPEN`` -- a resolved
    thread recorded as open, with nothing in the record saying the flag was not
    a flag. ``isOutdated`` is driven even in the shape where the resolved branch
    would not reach it, because it is read unconditionally on purpose: whether an
    answer is checkable must not depend on what another field in it says.
    """
    threads = _threads()
    threads["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"][0].update(flags)
    fake_gh.answer("prs", 1, _pull_requests())
    fake_gh.answer("threads", 1, threads)
    provider = _provider(tmp_path, fake_gh)
    (event,) = await _listed(provider)

    with pytest.raises(ReviewIngestRefusedError) as raised:
        await provider.get_threads(PROJECT, event)

    assert raised.value.grade is RefusalGrade.TOOL_FAILED
    assert next(iter(flags)) in str(raised.value)


def _assert_the_summary_is_bounded(summary: str, planted: int) -> None:
    """The two halves of the bound, wherever a summary names a value from an answer.

    Both are asserted because they close different failures. The **cut** is what
    bounds the channel; the **cap the sentence was reporting** surviving the cut
    is what says the bound was applied to the value rather than to the end of the
    sentence -- a summary cut at its tail keeps the megabyte and loses the number
    an operator acts on.
    """
    assert len(summary) <= MAX_REFUSAL_SUMMARY_CHARS, (
        f"the answer carried {planted} characters and the published summary is "
        f"{len(summary)} characters. `summary` is a channel for text this process "
        f"did not write, and it is bounded on the type so that no producer has to "
        f"remember it."
    )
    cut = re.search(r"cut from (\d+) characters", summary)
    assert cut is not None, (
        "the planted value was shortened without saying so; a value silently cut to "
        "look plausible is worse for a reader than one that is visibly incomplete"
    )
    # The number is the length of what was cut, which at a site that quotes is the
    # *rendering* rather than the raw value -- `repr` of a megabyte string is two
    # characters longer, and more than that once anything in it needs escaping.
    assert int(cut[1]) >= planted


@pytest.mark.asyncio
async def test_a_megabyte_of_answer_does_not_become_a_megabyte_of_summary(
    tmp_path: pathlib.Path, fake_gh: FakeGh
) -> None:
    """A refusal names what it refused, and what it refused came from the answer.

    The summary here interpolates a value this adapter read out of a GraphQL
    response, and a response is a document from somewhere else: a one-megabyte
    resolved name produced a one-megabyte summary, published in whatever a caller
    prints it into. The envelope's ``detail`` was contained and its ``summary``
    was not.

    This is the **repository-scope** half -- the resolved name is a fact about
    the answer as a whole, so it raises. Its record-scope twin below plants the
    same class of value in a summary that comes back as a skip, because a bound
    that held only on the raising path would leave the returned one open.
    """
    fake_gh.answer("prs", 1, _pull_requests(resolved_name="R" * 1_000_000))
    provider = _provider(tmp_path, fake_gh)

    with pytest.raises(ReviewIngestRefusedError) as raised:
        await provider.list_pull_requests(PROJECT, REPOSITORY)

    _assert_the_summary_is_bounded(raised.value.envelope.summary, 1_000_000)
    assert len(str(raised.value)) <= MAX_REFUSAL_SUMMARY_CHARS


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    (
        _pull_requests(number=_WIDEST_NUMBER, merged=True, mergeCommit=None),
        _pull_requests(
            number=_WIDEST_NUMBER,
            closingIssuesReferences={"pageInfo": {"hasNextPage": True}, "nodes": []},
        ),
    ),
    ids=("a merged pull request's number", "a capped pull request's number"),
)
async def test_a_wide_number_is_bounded_in_a_skipped_pull_requests_summary(
    tmp_path: pathlib.Path, fake_gh: FakeGh, payload: dict[str, Any]
) -> None:
    """The same bound on the channel that returns rather than raises.

    Both summaries name the pull request's ``number``, and both now arrive as a
    skip. A skip's envelope is published exactly as a raised one is, so it needs
    the same bound -- and it is a *different* code path to reach it, which is why
    this is driven rather than argued from the raising twin.

    **The plant is an integer rather than a megabyte string, and what changed is
    the read order rather than the bound.** The number goes through
    ``positive_integer`` and the window is applied to it before the record is
    built, so a ``number`` that is not an integer never reaches either sentence.
    What still reaches them is a wide integer, and :data:`_WIDEST_NUMBER` is the
    widest one that can --
    ``test_a_number_one_digit_wider_is_refused_as_an_unreadable_document`` is
    that ceiling's key.
    """
    fake_gh.answer("prs", 1, payload)
    provider = _provider(tmp_path, fake_gh)

    skip = await _one_skip(provider)

    assert skip.number == _WIDEST_NUMBER
    _assert_the_summary_is_bounded(skip.envelope.summary, len(str(_WIDEST_NUMBER)))


@pytest.mark.asyncio
async def test_a_number_one_digit_wider_is_refused_as_an_unreadable_document(
    tmp_path: pathlib.Path, fake_gh: FakeGh
) -> None:
    """The key for :data:`_WIDEST_NUMBER` being the widest, and not merely wide.

    ``json.loads`` converts an integer literal with ``int()``, and CPython
    refuses that past ``sys.get_int_max_str_digits()``. So a wider ``number``
    does not arrive as a large number this adapter has to bound -- the whole
    answer stops being readable, one stage earlier and at the repository scope,
    because a document that cannot be parsed says nothing about any one pull
    request in it.

    Written as literal text rather than through :meth:`FakeGh.answer`, because
    ``json.dumps`` refuses the same literal from the other side: there is no
    Python ``int`` that renders into this document.
    """
    wider = "9" * (sys.get_int_max_str_digits() + 1)
    document = json.dumps(_pull_requests()).replace('"number": 12', f'"number": {wider}', 1)

    assert wider in document, "the fixture's pull-request number was not the one replaced"

    fake_gh.answer_text("prs", 1, document)
    provider = _provider(tmp_path, fake_gh)

    with pytest.raises(ReviewIngestRefusedError) as raised:
        await provider.list_pull_requests(PROJECT, REPOSITORY)

    assert raised.value.grade is RefusalGrade.TOOL_FAILED
    assert "cannot read as a GraphQL response" in str(raised.value)


@pytest.mark.asyncio
async def test_a_hostile_resolved_name_leaves_the_refusal_sentence_intact(
    tmp_path: pathlib.Path, fake_gh: FakeGh
) -> None:
    """The rename refusal quotes what GitHub answered, and quoting has to fit the bound.

    ``repr`` turns one NUL into four characters, so a value cut to the echo bound
    and quoted *afterwards* came back four times that long: the summary ran past
    :data:`MAX_REFUSAL_SUMMARY_CHARS`, the type's cut took its tail, and the
    sentence lost the part that tells the operator nothing was read. Cutting the
    value is supposed to be what keeps the sentence -- so a value chosen to
    expand under quoting is where that claim is actually tested.

    A megabyte of NULs is the shape reproduced; the assertions are that the
    refusal is still the rename one, that the summary is inside the bound without
    the type having to cut it, and that its last sentence survived.
    """
    fake_gh.answer("prs", 1, _pull_requests(resolved_name="\x00" * 1_000_000))
    provider = _provider(tmp_path, fake_gh)

    with pytest.raises(ReviewIngestRefusedError) as raised:
        await provider.list_pull_requests(PROJECT, REPOSITORY)

    summary = raised.value.envelope.summary
    assert raised.value.grade is RefusalGrade.REPOSITORY_RESOLVED_ELSEWHERE
    assert len(summary) <= MAX_REFUSAL_SUMMARY_CHARS
    assert summary.endswith("Nothing was read from the answer."), (
        f"the summary lost its own tail to the type's cut, so the reader is told a "
        f"name did not match and not that nothing was read from the answer. It ends "
        f"{summary[-60:]!r}."
    )
    assert "\\x00" in summary, (
        "the summary does not carry the NULs in their escaped form, so either the "
        "quoting was dropped or the echo was cut before it could show one"
    )
    assert "\x00" not in summary, (
        "a raw NUL from a response reached the published sentence: quoting is what "
        "keeps a control character from arriving as punctuation in a document "
        "somebody prints"
    )


# -- the version floor and the authentication probe ---------------------------


@pytest.mark.asyncio
async def test_a_gh_below_the_floor_is_refused_and_the_message_names_the_floor(
    tmp_path: pathlib.Path,
) -> None:
    """Clause 8: a floor with a test, not prose asking for "a recent gh"."""
    fake = _write_fake(tmp_path / "old", version="2.85.0")
    provider = _provider(tmp_path, fake)

    with pytest.raises(ReviewIngestRefusedError) as raised:
        await provider.list_pull_requests(PROJECT, REPOSITORY)

    assert raised.value.grade is RefusalGrade.TOOL_TOO_OLD
    assert "2.86.0" in str(raised.value)
    assert fake.invocations == 1, "nothing beyond the version probe should have run"


@pytest.mark.asyncio
async def test_a_version_too_long_to_convert_is_a_graded_refusal_not_a_traceback(
    tmp_path: pathlib.Path,
) -> None:
    """The version probe reads a number out of a binary's own output, and ``int()`` can refuse.

    CPython declines to convert a string past ``sys.get_int_max_str_digits()``,
    4300 by default. So a ``gh`` printing a five-thousand-digit major version --
    a broken build, or one chosen to be -- put a ``ValueError`` out of the probe
    rather than the envelope clause 9 promises, on the path whose entire job is
    to answer with a grade.

    ``TOOL_TOO_OLD`` is the honest grade: an output this adapter cannot parse is
    a binary it has no measurement of, which is the state that refusal already
    names, and the message names the floor it could not compare against.

    The digits are written past the interpreter's own limit rather than past
    ``_MAX_VERSION_DIGITS``, so the input is the one that actually raised and not
    a value chosen to sit on the new bound.
    """
    fake = _write_fake(tmp_path / "vast", version=f"{'9' * 5000}.86.0")
    provider = _provider(tmp_path, fake)

    with pytest.raises(ReviewIngestRefusedError) as raised:
        await provider.list_pull_requests(PROJECT, REPOSITORY)

    assert raised.value.grade is RefusalGrade.TOOL_TOO_OLD
    assert "2.86.0" in str(raised.value)
    assert raised.value.remedy
    assert fake.invocations == 1, "nothing beyond the version probe should have run"


def test_the_probe_stdout_bound_this_file_drives_is_the_one_the_probes_pass() -> None:
    """The restated number and the enforced one are two things, so they are compared.

    :data:`RECORDED_PROBE_STDOUT_BYTES` is what the two boundary tests below size
    their version output from. If the probes' own constant moved and this one did
    not, both would be driving a boundary that is no longer the boundary --
    passing, and about the wrong number.
    """
    assert limits.MAX_PROBE_STDOUT_BYTES == RECORDED_PROBE_STDOUT_BYTES, (
        f"the probes are capped at {limits.MAX_PROBE_STDOUT_BYTES} bytes of stdout and "
        f"this file drives {RECORDED_PROBE_STDOUT_BYTES}. A cap is a recorded number: "
        f"move the prose that names it in the same change, and say what the new one "
        f"costs."
    )


@pytest.mark.asyncio
async def test_a_probe_at_the_recorded_stdout_bound_is_read_rather_than_refused(
    tmp_path: pathlib.Path, fake_gh: FakeGh
) -> None:
    """The accepting side of the probe cap, which is what makes it a boundary.

    Without it the refusal below proves nothing: a cap of zero refuses an
    oversized probe just as well, and so does a read that never ran. This is one
    byte smaller than the refusal case, and it is read -- the version parses out
    of the front of it and the run continues to an answer.
    """
    fake_gh.pad_version_stdout_to(RECORDED_PROBE_STDOUT_BYTES)
    fake_gh.answer("prs", 1, _pull_requests())
    provider = _provider(tmp_path, fake_gh)

    (event,) = await _listed(provider)

    assert event.number == 12


@pytest.mark.asyncio
async def test_a_probe_one_byte_past_the_recorded_stdout_bound_is_refused(
    tmp_path: pathlib.Path, fake_gh: FakeGh
) -> None:
    """The probes' own cap, driven -- the one recorded bound with no input reaching it.

    ``gh --version`` prints a line and ``gh auth status`` a short report, so the
    64 KiB ceiling is generous by orders of magnitude against either. That is
    exactly why nothing reached it: a stand-in child that behaves prints
    thirty-odd bytes, and a bound no input meets is a bound no test can drive.

    The response is canned as well, so a green result here cannot come from the
    run failing for some other reason: lift the cap and this call **succeeds**,
    which is a `DID NOT RAISE` rather than a differently-graded refusal.

    **The grade is ``TOOL_FAILED`` and that is the point of asserting it.** A
    response past its cap is ``LIMIT_EXCEEDED``, because there the bound is one
    a caller can act on -- ask for fewer pull requests, narrow the run. A probe
    takes no bounds from anybody, so the same grade would hand this operator a
    remedy for a cause they do not have: 64 KiB out of ``gh --version`` says the
    binary is not the one this adapter is written against.
    """
    fake_gh.pad_version_stdout_to(RECORDED_PROBE_STDOUT_BYTES + 1)
    fake_gh.answer("prs", 1, _pull_requests())
    provider = _provider(tmp_path, fake_gh)

    with pytest.raises(ReviewIngestRefusedError) as raised:
        await provider.list_pull_requests(PROJECT, REPOSITORY)

    assert raised.value.grade is RefusalGrade.TOOL_FAILED
    assert str(RECORDED_PROBE_STDOUT_BYTES) in str(raised.value)
    assert "gh --version" in str(raised.value), (
        "the refusal does not name the probe that overran, so a reader cannot tell "
        "which of the two spawns produced 64 KiB"
    )
    assert fake_gh.invocations == 1, (
        "the version probe overran its cap and something was spawned after it"
    )


@pytest.mark.asyncio
async def test_an_unauthenticated_gh_is_a_graded_envelope_carrying_its_own_stderr(
    tmp_path: pathlib.Path,
) -> None:
    """Clause 9's second state, and the stderr half of it.

    The child's stderr surfaces **only** inside the envelope. It is bounded at
    construction, so a debug-verbose child cannot make a refusal into a log.
    """
    fake = _write_fake(
        tmp_path / "unauth", version="2.86.0", auth_exit=1, auth_stderr="not-logged-in"
    )
    provider = _provider(tmp_path, fake)

    with pytest.raises(ReviewIngestRefusedError) as raised:
        await provider.list_pull_requests(PROJECT, REPOSITORY)

    assert raised.value.grade is RefusalGrade.TOOL_UNAUTHENTICATED
    assert "not-logged-in" in raised.value.envelope.detail
    assert "not-logged-in" not in str(raised.value)
    assert "not-logged-in" not in raised.value.remedy


@pytest.mark.asyncio
async def test_a_failed_query_reports_the_childs_own_words_inside_the_envelope(
    tmp_path: pathlib.Path, fake_gh: FakeGh
) -> None:
    """A non-zero ``gh`` is a refusal with a remedy, never a traceback."""
    provider = _provider(tmp_path, fake_gh)

    with pytest.raises(ReviewIngestRefusedError) as raised:
        await provider.list_pull_requests(PROJECT, REPOSITORY)

    assert raised.value.grade is RefusalGrade.TOOL_FAILED
    assert "no canned response" in raised.value.envelope.detail
    assert raised.value.remedy


# -- what the records carry ---------------------------------------------------


@pytest.mark.asyncio
async def test_a_pull_request_maps_onto_the_domain_record(
    tmp_path: pathlib.Path, fake_gh: FakeGh
) -> None:
    """Every field FR-V1 names, carried as the provider gave it."""
    fake_gh.answer("prs", 1, _pull_requests())
    provider = _provider(tmp_path, fake_gh)

    (event,) = await _listed(provider)

    assert event.provider == "github"
    assert event.repository == REPOSITORY
    assert event.number == 12
    assert event.title == "Refuse a symbolic link at every derived write target"
    assert event.author.external_id == "MDQ6VXNlcjE="
    assert event.author.display_name == "utchy"
    assert event.head_commit == "a" * 40
    assert event.base_commit == "b" * 40
    assert event.merged is True
    assert event.merge_commit == "c" * 40
    assert event.merged_at is not None
    assert event.ci_successful is True
    assert event.linked_issue_ids == ("523",)
    assert event.external_key == "github:acme/order-service#12"


@pytest.mark.asyncio
async def test_the_author_controlled_pull_request_fields_arrive_verbatim(
    tmp_path: pathlib.Path, fake_gh: FakeGh
) -> None:
    """ADR-0030 decision 3's untrusted row, on the fields that look structural.

    A description, a label, a head branch name and a milestone name are chosen by
    whoever opened the pull request. They are what slice 2's secret scan reads, so
    a record that dropped them would leave the scan reading nothing and passing --
    the shape a guard no input reaches always has.

    Every value is asserted **verbatim**, because the record's contract is that
    they are carried rather than interpreted: a normalisation here is content the
    scan would never see in the form it was written.
    """
    fake_gh.answer("prs", 1, _pull_requests())
    provider = _provider(tmp_path, fake_gh)

    (event,) = await _listed(provider)

    assert event.body == "The join check refused the leaf and not the directory itself."
    assert event.labels == ("security", "area/paths")
    assert event.head_ref_name == "fix/refuse-an-escaping-knowledge-dir"
    assert event.milestone == "Milestone 8"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "milestone", (None, _ABSENT), ids=("a null milestone", "no milestone field at all")
)
async def test_a_pull_request_in_no_milestone_records_none_rather_than_a_name(
    tmp_path: pathlib.Path, fake_gh: FakeGh, milestone: object
) -> None:
    """ADR-0030 decision 5: the honest value for what the provider does not record.

    ``milestone`` is the one author-controlled field on a pull request that
    GitHub answers with nothing, and both shapes of nothing are answers it gives:
    a ``null`` beside the other fields, and -- on a partly-errored response -- the
    key absent altogether. Neither may become a name, because a fabricated
    milestone is a value every consumer downstream reads as one somebody chose.
    """
    payload = _pull_requests()
    node = payload["data"]["repository"]["pullRequests"]["nodes"][0]
    if milestone is _ABSENT:
        del node["milestone"]
    else:
        node["milestone"] = milestone
    fake_gh.answer("prs", 1, payload)
    provider = _provider(tmp_path, fake_gh)

    (event,) = await _listed(provider)

    assert event.milestone is None


@pytest.mark.asyncio
async def test_a_pull_request_with_no_labels_records_an_empty_set_not_a_refusal(
    tmp_path: pathlib.Path, fake_gh: FakeGh
) -> None:
    """The ordinary case the cap must not swallow: a connection with nothing in it.

    An empty ``labels`` connection is what most pull requests answer with, and it
    is not an overflow: a cap that refused it would refuse the common case, and a
    cap that read it as unreadable would refuse every unlabelled pull request.
    """
    fake_gh.answer(
        "prs", 1, _pull_requests(labels={"pageInfo": {"hasNextPage": False}, "nodes": []})
    )
    provider = _provider(tmp_path, fake_gh)

    (event,) = await _listed(provider)

    assert event.labels == ()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "expected"),
    (
        ("SUCCESS", True),
        ("FAILURE", False),
        ("ERROR", False),
        ("PENDING", None),
        ("EXPECTED", None),
        ("A_STATE_THIS_ADAPTER_HAS_NEVER_HEARD_OF", None),
        (None, None),
    ),
    ids=("SUCCESS", "FAILURE", "ERROR", "PENDING", "EXPECTED", "unrecognised", "absent"),
)
async def test_an_unrecognised_ci_state_becomes_unknown_never_failed(
    tmp_path: pathlib.Path, fake_gh: FakeGh, state: str | None, expected: bool | None
) -> None:
    """ADR-0030 decision 5's rule, and its load-bearing half is the default.

    The mapping is stated by semantics rather than by enumerating the API's enum,
    because a schema may add a member. What must never happen is a member this
    adapter does not recognise being read downstream as *failed*.
    """
    rollup = {"nodes": [{"commit": {"statusCheckRollup": {"state": state}}}]}
    fake_gh.answer("prs", 1, _pull_requests(commits=rollup))
    provider = _provider(tmp_path, fake_gh)

    (event,) = await _listed(provider)

    assert event.ci_successful is expected


@pytest.mark.asyncio
async def test_a_resolved_thread_records_an_unknown_resolution_time(
    tmp_path: pathlib.Path, fake_gh: FakeGh
) -> None:
    """The API object carries no resolution timestamp, so the record says so.

    Filling it with the ingestion time, or the last comment's, would be a
    fabricated measurement every consumer downstream reads as real.
    """
    fake_gh.answer("prs", 1, _pull_requests())
    fake_gh.answer("threads", 1, _threads())
    provider = _provider(tmp_path, fake_gh)
    events = await _listed(provider)

    (thread,) = await provider.get_threads(PROJECT, events[0])

    assert thread.state is ReviewThreadState.RESOLVED
    assert thread.resolution is not None
    assert thread.resolution.resolved_at is None
    assert thread.resolution.resolved_by is not None
    assert thread.resolution.resolved_by.display_name == "utchy"
    assert thread.file_path == "src/order.py"
    assert thread.commit_sha == "d" * 40
    assert thread.event_key == events[0].external_key
    assert thread.comments[0].body == "Check the deadline before mutating state."
    assert thread.comments[0].category is None


@pytest.mark.asyncio
async def test_an_unresolved_thread_records_no_resolution(
    tmp_path: pathlib.Path, fake_gh: FakeGh
) -> None:
    """The invariant runs the other way too: an open thread carries no resolution."""
    fake_gh.answer("prs", 1, _pull_requests())
    fake_gh.answer("threads", 1, _threads(resolved=False))
    provider = _provider(tmp_path, fake_gh)
    events = await _listed(provider)

    (thread,) = await provider.get_threads(PROJECT, events[0])

    assert thread.state is ReviewThreadState.OPEN
    assert thread.resolution is None


@pytest.mark.asyncio
async def test_a_second_page_is_asked_for_with_a_cursor(
    tmp_path: pathlib.Path, fake_gh: FakeGh
) -> None:
    """Clause 6, driven: two pages, and the only thing that moved is ``after``."""
    first = _pull_requests()
    first["data"]["repository"]["pullRequests"]["pageInfo"] = {
        "hasNextPage": True,
        "endCursor": "CURSOR-1",
    }
    second = _pull_requests(number=11, url="https://github.com/acme/order-service/pull/11")
    fake_gh.answer("prs", 1, first)
    fake_gh.answer("prs", 2, second)
    provider = _provider(tmp_path, fake_gh)

    events = await _listed(provider)

    assert [event.number for event in events] == [12, 11]
    page_one, page_two = fake_gh.argv(3), fake_gh.argv(4)
    assert "after=CURSOR-1" in page_two
    assert [element for element in page_two if element not in page_one] == ["after=CURSOR-1"]


@pytest.mark.asyncio
async def test_since_number_stops_the_read_where_the_caller_asked(
    tmp_path: pathlib.Path, fake_gh: FakeGh
) -> None:
    """Incremental ingestion: a re-run does not refetch the whole history."""
    fake_gh.answer("prs", 1, _pull_requests())
    provider = _provider(tmp_path, fake_gh)

    assert await _listed(provider, since_number=12) == ()


@pytest.mark.asyncio
async def test_since_number_steps_over_an_excluded_pull_request_however_bad_it_is(
    tmp_path: pathlib.Path, fake_gh: FakeGh
) -> None:
    """A pull request outside the window is one whose data is never read at all.

    The record used to be built before the boundary was checked, so a pull
    request *at* ``since_number`` could still refuse the run on a field nobody
    asked to read: #100's labels overflow their cap, the refusal fires while the
    boundary check is still a statement away, and ``--since 100`` -- the way an
    operator steps past a known-bad record -- could not step past it. The two
    newer pull requests, which are the whole point of an incremental re-run,
    never came back.

    The poison node is **last** in the page, so a read that answers the two above
    it has genuinely walked as far as the boundary rather than stopped early for
    an unrelated reason.

    The listing is unwrapped by hand rather than through :func:`_listed`, because
    the empty ``skipped`` is a claim this case makes rather than a control it
    inherits: an excluded pull request is not a skipped one. Reporting it would
    put a record the caller deliberately did not ask for into the run's report,
    and make every incremental re-run read as unclean forever.
    """
    page = _pull_requests()
    nodes = page["data"]["repository"]["pullRequests"]["nodes"]
    nodes[:] = [
        {**nodes[0], "number": 102},
        {**nodes[0], "number": 101},
        _over_the_label_cap(nodes[0], 100),
    ]
    fake_gh.answer("prs", 1, page)
    provider = _provider(tmp_path, fake_gh)

    listing = await provider.list_pull_requests(PROJECT, REPOSITORY, since_number=100)

    assert [event.number for event in listing.events] == [102, 101]
    assert listing.skipped == (), (
        f"pull request 100 is outside the window and was reported as skipped anyway: "
        f"{[skip.number for skip in listing.skipped]}"
    )


@pytest.mark.asyncio
async def test_a_poison_newest_pull_request_does_not_deny_the_rest_of_the_repository(
    tmp_path: pathlib.Path, fake_gh: FakeGh
) -> None:
    """One pathological pull request costs its own record and no other.

    The over-cap node is **first** in the page -- the newest pull request, which
    is where every default window starts -- because that is the position from
    which a raised refusal denied the whole repository at any ``--limit``: there
    is no ``--since`` an operator can pass that steps *forward* over it.

    What the skip carries is asserted rather than merely counted. A caller has to
    be able to name which pull request is missing without parsing a sentence, and
    to hand its remedy to whoever runs the command.
    """
    page = _pull_requests()
    nodes = page["data"]["repository"]["pullRequests"]["nodes"]
    nodes[:] = [
        _over_the_label_cap(nodes[0], 14),
        {**nodes[0], "number": 13},
        {**nodes[0], "number": 12},
    ]
    fake_gh.answer("prs", 1, page)
    provider = _provider(tmp_path, fake_gh)

    listing = await provider.list_pull_requests(PROJECT, REPOSITORY)

    assert [event.number for event in listing.events] == [13, 12]
    (skip,) = listing.skipped
    assert skip.number == 14
    assert skip.repository == REPOSITORY
    assert skip.envelope.grade is RefusalGrade.LIMIT_EXCEEDED
    assert str(limits.MAX_LABELS_PER_PULL_REQUEST) in skip.envelope.summary
    assert "gh api graphql" in skip.envelope.remedy


@pytest.mark.asyncio
async def test_one_grade_stops_the_listing_and_skips_one_of_its_nodes(
    tmp_path: pathlib.Path,
) -> None:
    """``LIMIT_EXCEEDED`` from both scopes, and only one of them ends the read.

    The listing's page cap is this adapter's own machinery; a pull request's
    label cap is one node's data. Both raise ``LIMIT_EXCEEDED`` inside this
    module, so an implementation that decided by reading the grade -- or one
    whose ``try`` reached one statement too wide -- answers the same for both.
    Driving the pair in one test is what makes the scope rule falsifiable.

    Two stand-in children and two project roots, because each half needs its own
    canned answers and ``_project`` creates the directory it allowlists.
    """
    halting = _write_fake(tmp_path / "halting", version="2.86.0")
    endless = _pull_requests()
    endless["data"]["repository"]["pullRequests"]["pageInfo"] = {
        "hasNextPage": True,
        "endCursor": "CURSOR",
    }
    halting.answer("prs", 1, endless)
    halting.answer("prs", 2, endless)

    with pytest.raises(ReviewIngestRefusedError) as raised:
        await _provider(tmp_path / "a", halting).list_pull_requests(PROJECT, REPOSITORY)

    assert raised.value.grade is RefusalGrade.LIMIT_EXCEEDED
    assert str(limits.MAX_PAGES) in str(raised.value)

    skipping = _write_fake(tmp_path / "skipping", version="2.86.0")
    page = _pull_requests()
    nodes = page["data"]["repository"]["pullRequests"]["nodes"]
    nodes[:] = [_over_the_label_cap(nodes[0], 12)]
    skipping.answer("prs", 1, page)

    listing = await _provider(tmp_path / "b", skipping).list_pull_requests(PROJECT, REPOSITORY)

    assert [skip.number for skip in listing.skipped] == [12]
    assert listing.skipped[0].envelope.grade is raised.value.grade, (
        "the two halves no longer share a grade, so this pair can no longer tell a "
        "scope-reading implementation from a grade-reading one"
    )


@pytest.mark.asyncio
async def test_a_repository_reached_through_an_event_is_re_checked_against_the_allowlist(
    tmp_path: pathlib.Path, fake_gh: FakeGh
) -> None:
    """A ``ReviewEvent`` is an ordinary value a caller can build or alter, so it is not evidence.

    Taking ``event.repository`` on faith would make the control depend on where
    the value came from, which is the shape a later caller gets wrong. The forged
    value is an adapter-returned record with one field replaced, which is the
    cheapest form the mistake takes: everything else about it is genuine.
    """
    fake_gh.answer("prs", 1, _pull_requests())
    provider = _provider(tmp_path, fake_gh)
    (event,) = await _listed(provider)
    forged = dataclasses.replace(event, repository="acme/billing")
    before = fake_gh.invocations

    with pytest.raises(ReviewIngestRefusedError) as raised:
        await provider.get_threads(PROJECT, forged)

    assert raised.value.grade is RefusalGrade.REPOSITORY_NOT_ALLOWLISTED
    assert fake_gh.invocations == before


# -- the top-level reviews read -----------------------------------------------


async def _one_event(tmp_path: pathlib.Path, fake: FakeGh) -> tuple[GitHubReviewProvider, Any]:
    """A provider and the pull request it just read, so a reviews test starts there."""
    fake.answer("prs", 1, _pull_requests())
    provider = _provider(tmp_path, fake)
    (event,) = await _listed(provider)
    return provider, event


@pytest.mark.asyncio
async def test_a_top_level_review_maps_onto_the_domain_record(
    tmp_path: pathlib.Path, fake_gh: FakeGh
) -> None:
    """FR-V1's *reviews*, carried as the provider gave them.

    ``state`` is the field worth watching: it arrives here as a member GitHub
    documents, and the record keeps the provider's spelling rather than mapping
    it onto a vocabulary of this model's own.
    """
    fake_gh.answer("reviews", 1, _reviews())
    provider, event = await _one_event(tmp_path, fake_gh)

    (submission,) = await provider.get_reviews(PROJECT, event)

    assert submission.external_id == "PRR_1"
    assert submission.event_key == event.external_key
    assert submission.author.external_id == "MDQ6VXNlcjE="
    assert submission.author.display_name == "utchy"
    assert submission.body == "The guard is right; the reason it gives is not."
    assert submission.state == "CHANGES_REQUESTED"
    assert submission.submitted_at is not None


@pytest.mark.asyncio
async def test_a_review_state_this_adapter_has_never_heard_of_is_carried_not_folded(
    tmp_path: pathlib.Path, fake_gh: FakeGh
) -> None:
    """The reason the record holds a string and not a closed set.

    A schema may add a review state, and both answers a closed set could give are
    wrong: refusing the record loses evidence over a member that is perfectly
    valid upstream, and folding it into a default records a verdict nobody gave.
    Neither happens -- the spelling GitHub sent is what the record carries.
    """
    fake_gh.answer("reviews", 1, _reviews(state="A_STATE_THIS_ADAPTER_HAS_NEVER_HEARD_OF"))
    provider, event = await _one_event(tmp_path, fake_gh)

    (submission,) = await provider.get_reviews(PROJECT, event)

    assert submission.state == "A_STATE_THIS_ADAPTER_HAS_NEVER_HEARD_OF"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "submitted", (None, _ABSENT), ids=("a null submittedAt", "no submittedAt field at all")
)
async def test_a_review_that_was_never_submitted_records_no_time(
    tmp_path: pathlib.Path, fake_gh: FakeGh, submitted: object
) -> None:
    """ADR-0030 decision 5, on the reviews read: never the ingestion time.

    ``submittedAt`` is nullable, and a review that was started and not submitted
    has no submission time at all. Filling it with the ingestion time, or the
    pull request's, is a measurement nobody took that every reader downstream
    takes for one.
    """
    payload = _reviews()
    node = payload["data"]["repository"]["pullRequest"]["reviews"]["nodes"][0]
    if submitted is _ABSENT:
        del node["submittedAt"]
    else:
        node["submittedAt"] = submitted
    fake_gh.answer("reviews", 1, payload)
    provider, event = await _one_event(tmp_path, fake_gh)

    (submission,) = await provider.get_reviews(PROJECT, event)

    assert submission.submitted_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "named", "sent"),
    (
        ("id", "review id", None),
        ("state", "review state", None),
        ("state", "review state", "   "),
        ("id", "review id", "\t\n"),
    ),
    ids=("no id", "no state", "a state of only spaces", "an id of only whitespace"),
)
async def test_a_review_missing_a_field_its_identity_needs_is_a_graded_refusal(
    tmp_path: pathlib.Path, fake_gh: FakeGh, field: str, named: str, sent: str | None
) -> None:
    """Clause 9 on the new read: an unreadable answer is an envelope, never a traceback.

    ``ReviewSubmission`` raises ``InvariantViolationError`` on an empty
    ``external_id`` and on a blank ``state``, so folding either to the empty
    string would leave this adapter as the traceback the ADR forbids -- on a
    response shape a partly-errored GraphQL answer produces routinely, with the
    errored field back as ``null`` beside a ``data`` that looks ordinary.

    **The whitespace cases are the same fault one character further on.** The
    domain's guard on ``state`` is ``.strip()``-keyed, so a ``"   "`` walked
    straight through an emptiness check into the invariant this refusal exists to
    pre-empt; the identifier beside it is carried for the same reason a screen
    that admits a value no reader can name is not a screen.
    """
    fake_gh.answer("reviews", 1, _reviews(**{field: sent}))
    provider, event = await _one_event(tmp_path, fake_gh)

    with pytest.raises(ReviewIngestRefusedError) as raised:
        await provider.get_reviews(PROJECT, event)

    assert raised.value.grade is RefusalGrade.TOOL_FAILED
    assert named in str(raised.value)
    assert raised.value.remedy


@pytest.mark.asyncio
async def test_a_review_state_the_provider_padded_is_carried_as_the_provider_spelled_it(
    tmp_path: pathlib.Path, fake_gh: FakeGh
) -> None:
    """The positive control on the whitespace screen: it screens, it does not normalise.

    A state is carried against no closed set, so the adapter is not the layer
    that decides what one looks like. Without this, ``required_text`` could
    return ``value.strip()`` and pass every refusal case above while silently
    editing a value the record exists to carry verbatim.
    """
    fake_gh.answer("reviews", 1, _reviews(state=" APPROVED "))
    provider, event = await _one_event(tmp_path, fake_gh)

    (submission,) = await provider.get_reviews(PROJECT, event)

    assert submission.state == " APPROVED "


@pytest.mark.asyncio
async def test_a_second_page_of_reviews_is_asked_for_with_a_cursor(
    tmp_path: pathlib.Path, fake_gh: FakeGh
) -> None:
    """The shared page walker, driven on the read it was extracted for.

    Both per-pull-request reads go through one loop now, so this is the assertion
    that the second of them paginates at all rather than inheriting the property
    from its sibling's test.
    """
    first = _reviews()
    first["data"]["repository"]["pullRequest"]["reviews"]["pageInfo"] = {
        "hasNextPage": True,
        "endCursor": "CURSOR-R1",
    }
    fake_gh.answer("reviews", 1, first)
    fake_gh.answer("reviews", 2, _reviews(id="PRR_2", state="APPROVED"))
    provider, event = await _one_event(tmp_path, fake_gh)
    # Counted from where the pull-request read left off rather than written out:
    # the two probes and that read come first, and a transcribed index would move
    # the day another spawn is added ahead of this one.
    before = fake_gh.invocations

    submissions = await provider.get_reviews(PROJECT, event)

    assert [submission.external_id for submission in submissions] == ["PRR_1", "PRR_2"]
    page_one, page_two = fake_gh.argv(before + 1), fake_gh.argv(before + 2)
    assert "after=CURSOR-R1" in page_two
    assert [element for element in page_two if element not in page_one] == ["after=CURSOR-R1"]


@pytest.mark.asyncio
async def test_a_reviews_read_that_never_stops_paging_is_stopped_by_the_page_cap(
    tmp_path: pathlib.Path, fake_gh: FakeGh
) -> None:
    """The page cap bounds the new read too, and its report names which read it was.

    The canned answer always claims another page, which is the shape no element
    cap can stop: ``get_reviews`` has no per-pull-request record cap of its own,
    exactly as ``get_threads`` has none, so ``MAX_PAGES`` is the whole bound and
    a test that never reached it would leave that unproven.
    """
    endless = _reviews()
    endless["data"]["repository"]["pullRequest"]["reviews"]["pageInfo"] = {
        "hasNextPage": True,
        "endCursor": "CURSOR-R1",
    }
    fake_gh.answer("reviews", 1, endless)
    fake_gh.answer("reviews", 2, endless)
    provider, event = await _one_event(tmp_path, fake_gh)

    with pytest.raises(ReviewIngestRefusedError) as raised:
        await provider.get_reviews(PROJECT, event)

    assert raised.value.grade is RefusalGrade.LIMIT_EXCEEDED
    assert str(limits.MAX_PAGES) in str(raised.value)
    assert f"reviews on #{event.number}" in str(raised.value)


@pytest.mark.asyncio
async def test_a_repository_reached_through_get_reviews_is_re_checked_against_the_allowlist(
    tmp_path: pathlib.Path, fake_gh: FakeGh
) -> None:
    """The allowlist is a control on the new read as well, and it produces no spawn.

    The second per-pull-request read is a second door to the same check, and a
    door that only the first read is tested through is a control this file cannot
    say holds. The recorder being unchanged is the whole assertion.
    """
    fake_gh.answer("reviews", 1, _reviews())
    provider, event = await _one_event(tmp_path, fake_gh)
    forged = dataclasses.replace(event, repository="acme/billing")
    before = fake_gh.invocations

    with pytest.raises(ReviewIngestRefusedError) as raised:
        await provider.get_reviews(PROJECT, forged)

    assert raised.value.grade is RefusalGrade.REPOSITORY_NOT_ALLOWLISTED
    assert fake_gh.invocations == before


@pytest.mark.asyncio
async def test_a_private_repository_is_refused_on_the_reviews_read_too(
    tmp_path: pathlib.Path, fake_gh: FakeGh
) -> None:
    """Every page of every read is checked, not the first page of the first read.

    ``_repository_of`` runs on each answer the walker receives, so a repository
    that resolves as private mid-read refuses there. Driving it through
    ``get_reviews`` is what says the new read did not route around the check.
    """
    private = _reviews()
    private["data"]["repository"]["isPrivate"] = True
    fake_gh.answer("reviews", 1, private)
    provider, event = await _one_event(tmp_path, fake_gh)

    with pytest.raises(ReviewIngestRefusedError) as raised:
        await provider.get_reviews(PROJECT, event)

    assert raised.value.grade is RefusalGrade.REPOSITORY_IS_PRIVATE
