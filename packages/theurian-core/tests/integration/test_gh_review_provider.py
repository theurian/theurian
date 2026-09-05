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
import pathlib
from collections.abc import Iterator
from typing import Any, Final

import pytest

from theurian.domain.enums import ReviewThreadState
from theurian.domain.identifiers import ProjectId
from theurian.domain.review import ReviewEvent
from theurian.domain.review_ingest import RefusalGrade, ReviewIngestRefusedError
from theurian.infrastructure.github import environment, limits
from theurian.infrastructure.github.review_provider import GitHubReviewProvider
from theurian.infrastructure.github.transport_guard import GH_CONFIG_FILE

pytestmark = pytest.mark.integration

PROJECT: Final = ProjectId("demo")
REPOSITORY: Final = "acme/order-service"

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
/usr/bin/env > "{state}/env-$n"

case "$1" in
  --version) printf 'gh version {version} (2026-01-21)\\n'; exit 0 ;;
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

    def child_environment(self, index: int) -> dict[str, str]:
        """One invocation's environment, as the child itself reported it."""
        text = (self.directory / f"env-{index}").read_text(encoding="utf-8")
        return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)

    def answer(self, kind: str, page: int, payload: dict[str, Any]) -> None:
        """Give the child a canned response for one query kind and page."""
        (self.directory / f"{kind}{page}.json").write_text(json.dumps(payload), encoding="utf-8")


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
            version=version,
            auth_exit=auth_exit,
            auth_stderr=auth_stderr,
            query_exit=query_exit,
        ),
        encoding="utf-8",
    )
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


def _pull_requests(
    *, private: bool = False, resolved_name: str = REPOSITORY, **overrides: Any
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
    return {
        "data": {
            "repository": {
                "nameWithOwner": resolved_name,
                "isPrivate": private,
                "pullRequests": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [node],
                },
            }
        }
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
async def test_a_response_that_never_stops_paging_is_stopped_by_the_page_cap(
    tmp_path: pathlib.Path, fake_gh: FakeGh
) -> None:
    """A repository -- or a hostile response -- cannot keep this adapter asking."""
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

    with pytest.raises(ReviewIngestRefusedError) as raised:
        await provider.get_threads(PROJECT, events[0])

    assert raised.value.grade is RefusalGrade.LIMIT_EXCEEDED
    assert str(limits.MAX_PAGES) in str(raised.value)


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
