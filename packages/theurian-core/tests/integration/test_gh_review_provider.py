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
"""

from __future__ import annotations

import json
import os
import pathlib
from collections.abc import Iterator
from typing import Any, Final

import pytest

from theurian.domain.enums import ReviewThreadState
from theurian.domain.identifiers import ProjectId
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
for a in "$@"; do
  case "$a" in
    number=*) kind=threads ;;
    after=*) page=2 ;;
  esac
done
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
        (self.directory / f"{kind}{page}.json").write_text(json.dumps(payload), encoding="utf-8")

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
        "url": "https://github.com/acme/order-service/pull/12",
        "createdAt": "2026-09-01T10:00:00Z",
        "merged": True,
        "mergedAt": "2026-09-02T11:00:00Z",
        "headRefOid": "a" * 40,
        "baseRefOid": "b" * 40,
        "author": {"login": "utchy", "id": "MDQ6VXNlcjE="},
        "mergeCommit": {"oid": "c" * 40},
        "closingIssuesReferences": {"nodes": [{"number": 523}]},
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

    await provider.list_pull_requests(PROJECT, REPOSITORY)
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
        await provider.list_pull_requests(PROJECT, REPOSITORY)
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

    await provider.list_pull_requests(PROJECT, REPOSITORY)
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

    await provider.list_pull_requests(PROJECT, REPOSITORY)

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

    events = await provider.list_pull_requests(PROJECT, REPOSITORY)
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

    events = await provider.list_pull_requests(PROJECT, REPOSITORY)

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

    events = await provider.list_pull_requests(PROJECT, REPOSITORY, limit=limit)

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

    events = await provider.list_pull_requests(PROJECT, REPOSITORY, limit=2)

    assert [event.number for event in events] == [14, 13], (
        f"the page carried three pull requests, the caller asked for two, and "
        f"{len(events)} came back."
    )


@pytest.mark.asyncio
async def test_a_response_that_never_stops_paging_is_stopped_by_the_page_cap(
    tmp_path: pathlib.Path, fake_gh: FakeGh
) -> None:
    """A repository -- or a hostile response -- cannot keep this adapter asking.

    **The request count is the assertion the grade cannot make.** A page cap of
    twenty-five stops an endless response too, and reports the same grade and the
    same recorded number in the same sentence -- so a message-only check passes
    against a loop that made five more requests than the record says it may. What
    bounds the work is how many times the child was spawned, which is counted
    here against the constant the refusal names.
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
    events = await provider.list_pull_requests(PROJECT, REPOSITORY)
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
    events = await provider.list_pull_requests(PROJECT, REPOSITORY)

    with pytest.raises(ReviewIngestRefusedError) as raised:
        await provider.get_threads(PROJECT, events[0])

    assert raised.value.grade is RefusalGrade.LIMIT_EXCEEDED
    assert str(limits.MAX_COMMENTS_PER_THREAD) in str(raised.value)


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
    """
    fake_gh.answer("prs", 1, _pull_requests(closingIssuesReferences={"nodes": [{"number": 0}]}))
    provider = _provider(tmp_path, fake_gh)

    with pytest.raises(ReviewIngestRefusedError) as raised:
        await provider.list_pull_requests(PROJECT, REPOSITORY)

    assert raised.value.grade is RefusalGrade.TOOL_FAILED
    assert "linked issue number" in str(raised.value)


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

    with pytest.raises(ReviewIngestRefusedError) as raised:
        await provider.list_pull_requests(PROJECT, REPOSITORY)

    assert raised.value.grade is RefusalGrade.TOOL_FAILED
    assert "createdAt" in str(raised.value)


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

    (event,) = await provider.list_pull_requests(PROJECT, REPOSITORY)

    assert event.author.external_id == "ghost"
    assert event.author.display_name == "ghost"


@pytest.mark.asyncio
async def test_a_merged_pull_request_with_no_merge_commit_is_refused(
    tmp_path: pathlib.Path, fake_gh: FakeGh
) -> None:
    """``ReviewEvent`` requires a merged pull request to record its merge commit.

    Same argument as the number bound: the domain invariant is real, and reaching
    it from here would be a traceback rather than an envelope. The summary names
    the pull request so a reader knows which answer was unreadable.
    """
    fake_gh.answer("prs", 1, _pull_requests(merged=True, mergeCommit=None))
    provider = _provider(tmp_path, fake_gh)

    with pytest.raises(ReviewIngestRefusedError) as raised:
        await provider.list_pull_requests(PROJECT, REPOSITORY)

    assert raised.value.grade is RefusalGrade.TOOL_FAILED
    assert f"{REPOSITORY}#12" in str(raised.value)


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

    with pytest.raises(ReviewIngestRefusedError) as raised:
        await provider.list_pull_requests(PROJECT, REPOSITORY)

    assert raised.value.grade is RefusalGrade.LIMIT_EXCEEDED
    assert f"{REPOSITORY}#12" in str(raised.value)
    assert str(limits.MAX_LINKED_ISSUES) in str(raised.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "shape",
    ("a merged pull request's number", "a capped pull request's number", "a resolved name"),
)
async def test_a_megabyte_of_answer_does_not_become_a_megabyte_of_summary(
    tmp_path: pathlib.Path, fake_gh: FakeGh, shape: str
) -> None:
    """A refusal names what it refused, and what it refused came from the answer.

    Every summary below interpolates a value this adapter read out of a GraphQL
    response, and a response is a document from somewhere else: a one-megabyte
    ``number`` produced a one-megabyte summary, published in whatever a caller
    prints it into. The envelope's ``detail`` was contained and its ``summary``
    was not.

    Both halves are asserted because they close different failures. The **cut**
    is what bounds the channel; the **cap the sentence was reporting** surviving
    the cut is what says the bound was applied to the value rather than to the
    end of the sentence -- a summary cut at its tail keeps the megabyte and loses
    the number an operator acts on.
    """
    million = "N" * 1_000_000
    payloads = {
        "a merged pull request's number": _pull_requests(
            number=million, merged=True, mergeCommit=None
        ),
        "a capped pull request's number": _pull_requests(
            number=million,
            closingIssuesReferences={"pageInfo": {"hasNextPage": True}, "nodes": []},
        ),
        "a resolved name": _pull_requests(resolved_name="R" * 1_000_000),
    }
    fake_gh.answer("prs", 1, payloads[shape])
    provider = _provider(tmp_path, fake_gh)

    with pytest.raises(ReviewIngestRefusedError) as raised:
        await provider.list_pull_requests(PROJECT, REPOSITORY)

    summary = raised.value.envelope.summary
    assert len(summary) <= MAX_REFUSAL_SUMMARY_CHARS, (
        f"the answer carried a megabyte and the published summary is {len(summary)} "
        f"characters. `summary` is a channel for text this process did not write, "
        f"and it is bounded on the type so that no producer has to remember it."
    )
    assert len(str(raised.value)) <= MAX_REFUSAL_SUMMARY_CHARS
    assert "cut from 1000000 characters" in summary, (
        "the megabyte was shortened without saying so; a value silently cut to look "
        "plausible is worse for a reader than one that is visibly incomplete"
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

    (event,) = await provider.list_pull_requests(PROJECT, REPOSITORY)

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
    """
    fake_gh.pad_version_stdout_to(RECORDED_PROBE_STDOUT_BYTES + 1)
    fake_gh.answer("prs", 1, _pull_requests())
    provider = _provider(tmp_path, fake_gh)

    with pytest.raises(ReviewIngestRefusedError) as raised:
        await provider.list_pull_requests(PROJECT, REPOSITORY)

    assert raised.value.grade is RefusalGrade.LIMIT_EXCEEDED
    assert str(RECORDED_PROBE_STDOUT_BYTES) in str(raised.value)
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

    (event,) = await provider.list_pull_requests(PROJECT, REPOSITORY)

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

    (event,) = await provider.list_pull_requests(PROJECT, REPOSITORY)

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
    events = await provider.list_pull_requests(PROJECT, REPOSITORY)

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
    events = await provider.list_pull_requests(PROJECT, REPOSITORY)

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

    events = await provider.list_pull_requests(PROJECT, REPOSITORY)

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

    assert await provider.list_pull_requests(PROJECT, REPOSITORY, since_number=12) == ()


@pytest.mark.asyncio
async def test_a_repository_reached_through_an_event_is_re_checked_against_the_allowlist(
    tmp_path: pathlib.Path, fake_gh: FakeGh
) -> None:
    """A ``ReviewEvent`` is an ordinary value a caller can build, so it is not evidence.

    Taking ``event.repository`` on faith would make the control depend on where
    the value came from, which is the shape a later caller gets wrong.
    """
    fake_gh.answer("prs", 1, _pull_requests())
    provider = _provider(tmp_path, fake_gh)
    (event,) = await provider.list_pull_requests(PROJECT, REPOSITORY)
    forged = ReviewEvent(
        project_id=PROJECT,
        provider="github",
        repository="acme/billing",
        number=event.number,
        title=event.title,
        author=event.author,
        created_at=event.created_at,
        url=event.url,
        head_commit=event.head_commit,
        base_commit=event.base_commit,
    )
    before = fake_gh.invocations

    with pytest.raises(ReviewIngestRefusedError) as raised:
        await provider.get_threads(PROJECT, forged)

    assert raised.value.grade is RefusalGrade.REPOSITORY_NOT_ALLOWLISTED
    assert fake_gh.invocations == before
