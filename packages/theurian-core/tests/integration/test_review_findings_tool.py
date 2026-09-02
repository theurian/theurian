"""The `review.findings` MCP tool, called in process (ADR-0029 phase-2 slice-3).

The serving surface for landed ``Review-Finding:`` trailers. Called through
``server.call_tool`` -- the entry point the transport uses -- against a project
the real CLI built, with a findings store landed by the real adapter.

The store is written by :class:`SqliteReviewFindingStore` directly rather than by
``theurian findings build``, and that is deliberate: the build command reads
``refs/remotes/origin/main``, which a throwaway fixture repository does not have,
and the *content* under test here is a synthetic load whose oracle is a value
this file wrote. ``tests/integration/test_findings_build_cli.py`` is where the
build command is driven end to end.

Five properties this file exists to hold, each named where it is asserted below:

- a served row carries the SEC-15 triple, **and the check that says so can fail**
  (ADR-0029 Compliance: "a result missing the triple is rejected");
- every filter selects exactly its rows, and an unknown token or an over-bound
  value is refused naming the bound rather than silently scanning;
- a store that cannot be served from produces **one constant refusal** carrying
  the rebuild remedy -- never an empty result, and never a message that varies
  with what the store holds;
- a rejected trailer is not served, and its presence does not move a single byte
  of any response (the two-corpora differential ADR-0029's closure is stated as);
- a store **this installation did not build** is refused in the words a missing
  one gets, however well-formed the file is (ADR-0004, SEC-7, T-19).
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import subprocess
import sys
import threading
from collections.abc import Iterator
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError as SdkToolError
from migration_fixtures import body_pin
from typer.testing import CliRunner

from theurian.application.project_service import (
    FINDINGS_STORE_ID,
    BuildProvenance,
    ProjectPaths,
    ProjectRegistry,
)
from theurian.cli.main import app
from theurian.daemon.runner import build_server
from theurian.domain.knowledge import SourceAnchor
from theurian.domain.review_finding import (
    FindingLoad,
    FindingSeverity,
    RejectedTrailer,
    ReviewerToken,
    ReviewFinding,
)
from theurian.infrastructure.sqlite.findings_store import SqliteReviewFindingStore
from theurian.mcp.findings import (
    DEFAULT_FINDINGS_LIMIT,
    INERT_FILTER_REFUSAL,
    MAX_ECHOED_DIGITS,
    MAX_FILTER_CHARS,
    MAX_FINDINGS_LIMIT,
    MAX_PULL_REQUEST,
    max_finding_text_chars,
)
from theurian.mcp.results import SAFETY
from theurian.mcp.tools import (
    ADMISSION_WAIT_SECONDS,
    FINDINGS_CAPACITY_REFUSAL,
    FINDINGS_UNAVAILABLE_REFUSAL,
    MAX_CONCURRENT_SEARCHES,
    MAX_QUERY_CHARS,
)

pytestmark = pytest.mark.integration

#: CPython's own ceiling on rendering an integer as a string, read from the
#: interpreter rather than written as 4,300: it is configurable per process, and a
#: test that spelled the number would be asserting about a limit this run may not
#: have. It is the threshold the refusal-path tests below walk.
_INT_MAX_STR_DIGITS = sys.get_int_max_str_digits()

#: The repository root, for the one published statement of this tool's bounds.
#: Four parents up: ``integration`` -> ``tests`` -> ``theurian-core`` ->
#: ``packages``. The same reckoning ``test_mcp_tools.py`` uses.
REPO_ROOT = Path(__file__).resolve().parents[4]

runner = CliRunner()

MIGRATION_ID = "01K1AAAAAA01234567890ABCDE"
REVISION_ID = "01K1AAAREV01234567890ABCDE"
BODY = "# Authentication policy\n\nEvery call carries a signed token.\n"

MIGRATION = f"""apiVersion: theurian.dev/v1
id: {MIGRATION_ID}
createdAt: 2026-08-02T10:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.auth-policy
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.auth-policy
    revisionId: {REVISION_ID}
    contentFile: ../knowledge/architecture/auth-policy.md
    contentSha256: {body_pin(BODY)}
    metadata:
      title: Authentication policy
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/auth-policy.md
"""


def _sha(seed: str) -> str:
    """A 40-hex commit sha built from one character, so tests read declaratively."""
    return seed * 40


def _finding(  # noqa: PLR0913 - one keyword per filterable column
    sha: str,
    *,
    reviewer: ReviewerToken = ReviewerToken.CODE_REVIEW,
    severity: FindingSeverity = FindingSeverity.HIGH,
    text: str = "a finding",
    when: str = "2026-08-27T12:00:00+00:00",
    pull_request: int | None = None,
    family: str | None = None,
    specialist: str | None = None,
) -> ReviewFinding:
    return ReviewFinding(
        reviewer=reviewer,
        severity=severity,
        finding_text=text,
        anchor=SourceAnchor(provider="git", source_uri=sha, commit_sha=sha),
        pull_request=pull_request,
        date=datetime.fromisoformat(when),
        family=family,
        specialist=specialist,
    )


#: The corpus every serving test below reads. Three accepted findings across two
#: commits -- two of them sharing a commit, which is the *standard* shape of this
#: data (ADR-0029's closure measured 17 trailers on one commit) -- and one
#: rejected trailer whose raw line reads exactly like a real finding.
#:
#: The rejected member is load-bearing in every test that uses this load, not
#: only in the ones that name it: an assertion that a response holds three rows
#: proves nothing about withholding over a corpus with nothing to withhold.
LANDED = FindingLoad(
    accepted=(
        _finding(
            _sha("a"),
            reviewer=ReviewerToken.SECURITY,
            severity=FindingSeverity.CRITICAL,
            text="a bearer token reached the log",
            when="2026-08-25T09:00:00+00:00",
            pull_request=11,
            family="a published field",
            specialist="theurian-python",
        ),
        _finding(
            _sha("a"),
            reviewer=ReviewerToken.CODE_REVIEW,
            severity=FindingSeverity.LOW,
            text="a name reads as its opposite",
            when="2026-08-25T09:00:00+00:00",
            pull_request=11,
            family="a duration",
            specialist="theurian-tests",
        ),
        _finding(
            _sha("b"),
            reviewer=ReviewerToken.ADVERSARIAL,
            severity=FindingSeverity.HIGH,
            text="the test stays green with the code deleted",
            when="2026-08-26T09:00:00+00:00",
            pull_request=12,
            family="a published field",
            specialist="theurian-tests",
        ),
    ),
    rejected=(
        RejectedTrailer(
            _sha("c"),
            "Review-Finding: nonsense CRITICAL — the private key is in fixtures/",
            "unknown reviewer 'nonsense'",
        ),
    ),
)


def _run(*args: str) -> None:
    result = runner.invoke(app, [*args, "--json"], catch_exceptions=False)
    assert result.exit_code == 0, result.stdout + (result.stderr or "")


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ProjectRegistry]:
    """A registered, migrated project -- with no findings store yet.

    Built by the real CLI, because ``review.findings`` resolves through the same
    ``_resolve`` every project-scoped tool does: the registry entry, the active
    state pointer, and the ADR-0004/SEC-7 provenance check all have to be real
    for this tool to be reached at all.
    """
    root = tmp_path / "demo"
    root.mkdir()
    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603

    data_dir = tmp_path / "datadir"
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(data_dir))
    monkeypatch.chdir(root)
    _run("init")
    (root / ".theurian/knowledge/architecture/auth-policy.md").write_text(BODY)
    (root / f".theurian/migrations/{MIGRATION_ID}-auth.yaml").write_text(MIGRATION)
    _run("project", "register")
    _run("migrate", "apply")

    yield ProjectRegistry.default(data_dir)


def _store_path(registry: ProjectRegistry, project_id: str = "demo") -> Path:
    """Where this project's findings store lives, as the *build* command names it.

    Resolved through ``ProjectPaths.findings_for(FINDINGS_STORE_ID)`` -- the same
    call ``theurian findings build`` makes -- rather than by writing the filename
    out here. A test that spelled the path itself would keep passing if the tool
    and the build command ever stopped agreeing on it, which is exactly the
    failure the shared constant exists to prevent.
    """
    root = Path(registry.load()[project_id]["rootPath"])
    return ProjectPaths.of(root).findings_for(FINDINGS_STORE_ID)


def _record_provenance(registry: ProjectRegistry, project_id: str = "demo") -> None:
    """Record the store as this installation's, exactly as ``findings build`` does.

    Writing the file is not enough to make it servable: the tool refuses a store
    this installation has no record of building, because presence on disk is what
    a hostile clone manufactures (ADR-0004, SEC-7, T-19). These tests land the
    store with the adapter rather than the command -- the command reads
    ``refs/remotes/origin/main``, which a fixture repository does not have -- so
    they have to make the same provenance record the command makes, through the
    same class, keyed the same way.
    """
    root = Path(registry.load()[project_id]["rootPath"])
    BuildProvenance.for_registry(registry).record_findings(root, FINDINGS_STORE_ID)


def _plant(registry: ProjectRegistry, load: FindingLoad = LANDED) -> SqliteReviewFindingStore:
    """Land a store on disk and record **nothing** -- the hostile-clone shape.

    What a repository contributor can produce with ``git add -f``: a well-formed,
    current, fully readable store that this installation never built.
    """
    store = SqliteReviewFindingStore(_store_path(registry))
    store.replace_all(load)
    return store


def _land(registry: ProjectRegistry, load: FindingLoad = LANDED) -> SqliteReviewFindingStore:
    store = _plant(registry, load)
    _record_provenance(registry)
    return store


async def _call(registry: ProjectRegistry, **arguments: Any) -> dict[str, Any]:
    """One `review.findings` call, returned as the payload a client sees."""
    result = await build_server(registry).call_tool("review.findings", arguments)
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        payload: dict[str, Any] = structured
        return payload
    content: Any = result.content  # type: ignore[union-attr]
    loaded: dict[str, Any] = json.loads(content[0].text)
    return loaded


async def _call_failing(registry: ProjectRegistry, **arguments: Any) -> str:
    """One `review.findings` call that must fail, as the message a client reads."""
    with pytest.raises(SdkToolError) as raised:
        await _call(registry, **arguments)
    return str(raised.value)


def _texts(payload: dict[str, Any]) -> list[str]:
    return [row["findingText"] for row in payload["findings"]]


def carries_the_triple(row: dict[str, Any]) -> bool:
    """Whether ``row`` carries the SEC-15 trust triple, all three, correctly.

    A predicate rather than three inline assertions, so the "can fail" companion
    below governs the *same* check the acceptance test uses. A check asserted in
    one place and mutated in another proves nothing about the check that ships.
    """
    return (
        row.get("contentClassification") == "untrusted-knowledge"
        and row.get("mayContainInstructions") is True
        and row.get("executable") is False
    )


# -- AC-1: what a served finding is ----------------------------------------


@pytest.mark.asyncio
async def test_every_served_finding_carries_the_trust_triple(project: ProjectRegistry) -> None:
    """SEC-15, ADR-0029 decision 3: a finding's text is untrusted content.

    A reviewer's one-line finding is authored commit text, and it reads as an
    imperative because it *describes* what should change -- which is exactly the
    shape T-3 grades High when an agent takes it as an instruction addressed to
    it. The triple is what says otherwise, on every row rather than on the
    response, because a client renders rows.
    """
    _land(project)

    payload = await _call(project, projectId="demo")

    assert payload["count"] == 3
    assert payload["findings"], "an empty array would satisfy the loop below vacuously"
    for row in payload["findings"]:
        assert carries_the_triple(row), f"a served finding carries no trust triple: {row}"


def test_the_trust_triple_check_can_fail() -> None:
    """The companion ADR-0029's Compliance section names: the check must reject.

    ``carries_the_triple`` reporting *true* is only worth something if it can
    report false, and each of the three labels is separately load-bearing: a row
    marked ``executable: true`` invites an agent to run it, and one missing
    ``mayContainInstructions`` invites it to read a finding as an instruction. So
    every single-field mutation is asserted to be caught, not merely a wholesale
    empty row.
    """
    intact = {"findingText": "a finding", **SAFETY}
    assert carries_the_triple(intact), "the fixture's premise: an intact row passes"

    for key in SAFETY:
        assert not carries_the_triple({k: v for k, v in intact.items() if k != key}), (
            f"a row missing {key!r} was accepted; the triple check cannot fail and "
            f"the acceptance test above is asserting nothing"
        )
    assert not carries_the_triple({**intact, "executable": True})
    assert not carries_the_triple({**intact, "mayContainInstructions": False})
    assert not carries_the_triple({**intact, "contentClassification": "trusted"})


@pytest.mark.asyncio
async def test_a_served_row_is_the_stored_row_and_its_labels(project: ProjectRegistry) -> None:
    """The whole wire shape of one finding, pinned as a value rather than by key.

    Every key is always present, ``null`` included: ``pullRequest``, ``family``
    and ``specialist`` are ``None`` on every row the shipped git source produces
    (ADR-0029 D5), and a field that appeared only when set could not be told
    apart from a server that predates it.
    """
    _land(project)

    payload = await _call(project, projectId="demo", commitSha=_sha("b"))

    assert payload == {
        "count": 1,
        "truncated": False,
        "findings": [
            {
                "commitSha": _sha("b"),
                "position": 0,
                "reviewer": "adversarial",
                "severity": "HIGH",
                "findingText": "the test stays green with the code deleted",
                "provider": "git",
                "sourceUri": _sha("b"),
                "committedAt": "2026-08-26T09:00:00.000000+00:00",
                "pullRequest": 12,
                "family": "a published field",
                "specialist": "theurian-tests",
                "contentClassification": "untrusted-knowledge",
                "mayContainInstructions": True,
                "executable": False,
            }
        ],
    }


@pytest.mark.asyncio
async def test_the_response_holds_exactly_three_members(project: ProjectRegistry) -> None:
    """The population pin one level up: ``count``, ``truncated``, ``findings``.

    A member added here is a published value nothing holds the server to, and the
    ones that were considered and rejected are each a statistic over content this
    tool does not serve -- a rejected-trailer count, the store's stamp, an echo of
    the filters, a total before ``limit`` (see ``mcp/findings.findings_payload``).
    A future member has to argue that it is a function of the served rows, or of
    the served page's own boundary, before it can pass this.
    """
    _land(project)

    payload = await _call(project, projectId="demo")

    assert set(payload) == {"count", "truncated", "findings"}


@pytest.mark.asyncio
async def test_an_oversized_finding_is_served_bounded_and_visibly_cut(
    project: ProjectRegistry,
) -> None:
    """R1-3: the byte dimension, which nothing bounded (only the row count did).

    ``findingText`` is byte-preserved from a commit message, and a commit message
    line has no length limit -- so one planted trailer made a response arbitrarily
    large: a 2 MiB line served at ``limit=40`` measured 83.9 MB. The planting actor
    is T-5's contributor, and the store copies the line through without inspecting
    it, deliberately.

    Both halves are asserted, because a bound that fires is only half a bound: the
    long row comes back cut *and marked*, and the short row beside it comes back
    byte-identical. Without the second, a bound of one character would pass.
    """
    bound = max_finding_text_chars()
    ordinary = "a finding of ordinary length"
    _land(
        project,
        FindingLoad(
            accepted=(
                _finding(_sha("a"), text="x" * (bound * 3), when="2026-08-25T09:00:00+00:00"),
                _finding(_sha("b"), text=ordinary, when="2026-08-24T09:00:00+00:00"),
            ),
            rejected=(),
        ),
    )

    payload = await _call(project, projectId="demo")
    served = {row["commitSha"]: row["findingText"] for row in payload["findings"]}

    assert served[_sha("a")] == "x" * bound + "...", (
        "an over-long finding was not cut at the bound and marked; a truncated "
        "value that is not marked reads as the whole one"
    )
    assert served[_sha("b")] == ordinary, "a finding inside the bound was altered"


@pytest.mark.asyncio
async def test_the_findings_read_is_admission_gated_like_a_search(
    project: ProjectRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R1-3's other half: how many of these reads may run at once is bounded.

    A sync tool runs on a worker thread ``anyio.to_thread.run_sync`` dispatched,
    and cancelling the awaiting task does not stop that thread -- so a transport
    timeout bounds how long a caller waits and never how much the daemon spends.
    The cap is what bounds the spend rate, and without it this tool was a
    documented entry point with no ceiling on concurrent occupancy (T-6, SEC-8).

    Its own semaphore rather than ``knowledge.search``'s: the search refusal names
    "concurrent searches", and a findings flood refusing searches with it would
    make a published message false. So this asserts the *findings* refusal, which
    is what distinguishes the two gates from one shared one.

    Every wait is bounded: a stub left blocked is a worker thread the suite cannot
    finish, and it fails loudly here instead of hanging.
    """
    import theurian.mcp.tools as tools_module

    _land(project)
    release = threading.Event()
    all_entered = threading.Event()
    lock = threading.Lock()
    entered = 0

    class _BlockingStore:
        """Stands in for the adapter, occupying a permit until released."""

        def __init__(self, path: Path) -> None:
            self._path = path

        def serve_findings(self, query: Any) -> tuple[Any, ...]:  # noqa: ARG002 - port shape
            nonlocal entered
            with lock:
                entered += 1
                if entered >= MAX_CONCURRENT_SEARCHES:
                    all_entered.set()
            assert release.wait(timeout=5.0), "the gate's release was never set"
            return ()

    monkeypatch.setattr(tools_module, "SqliteReviewFindingStore", _BlockingStore)
    server = build_server(project)
    holders = [
        asyncio.create_task(server.call_tool("review.findings", {"projectId": "demo"}))
        for _ in range(MAX_CONCURRENT_SEARCHES)
    ]
    try:
        saturated = await asyncio.get_running_loop().run_in_executor(None, all_entered.wait, 5.0)
        assert saturated, f"only {entered} holders entered; the harness did not saturate"

        with pytest.raises(SdkToolError) as raised:
            await asyncio.wait_for(
                server.call_tool("review.findings", {"projectId": "demo"}),
                timeout=ADMISSION_WAIT_SECONDS + 5.0,
            )
    finally:
        release.set()
        await asyncio.wait_for(asyncio.gather(*holders, return_exceptions=True), timeout=5.0)

    assert FINDINGS_CAPACITY_REFUSAL in str(raised.value)
    assert "concurrent searches" not in str(raised.value), (
        "the findings gate answered with the search cap's message, so the two tools "
        "share one semaphore and one of the two refusals is false about its own load"
    )


@pytest.mark.asyncio
async def test_the_real_corpus_never_reaches_the_finding_text_bound(
    project: ProjectRegistry,
) -> None:
    """The bound is a ceiling on planted data, not a truncation of authored data.

    Measured against this repository's own history: the longest landed finding is
    193 characters, an order of magnitude inside the bound. Asserted against the
    live constant rather than restated, so raising or lowering the bound is
    checked against the corpus it has to clear rather than against a number
    somebody wrote down once.
    """
    longest_real_finding = 193

    assert longest_real_finding < max_finding_text_chars(), (
        f"the served-text bound is {max_finding_text_chars()}, which is not clear of "
        f"the longest finding this repository's own history holds "
        f"({longest_real_finding} characters as of 2026-09-02) -- so the bound would "
        f"start cutting authored review findings rather than planted ones"
    )


@pytest.mark.asyncio
async def test_the_count_sizes_the_array_it_is_returned_with(project: ProjectRegistry) -> None:
    """``count`` is ``len(findings)``, including under a truncating ``limit``.

    Not a total before the limit: that would be a count over rows the caller did
    not receive, which is a different promise and one this tool deliberately does
    not make.
    """
    _land(project)

    truncated = await _call(project, projectId="demo", limit=2)

    assert truncated["count"] == 2 == len(truncated["findings"])


@pytest.mark.asyncio
async def test_findings_are_served_newest_first_and_truncated_in_that_order(
    project: ProjectRegistry,
) -> None:
    """A total order, so ``limit`` truncates a defined sequence.

    The two findings on commit ``a`` share an instant, so only the
    ``(commitSha, position)`` tiebreak keeps their order stable -- without it a
    two-row page would vary between runs over an unchanged store.
    """
    _land(project)

    everything = await _call(project, projectId="demo")
    page = await _call(project, projectId="demo", limit=2)

    assert _texts(everything) == [
        "the test stays green with the code deleted",
        "a bearer token reached the log",
        "a name reads as its opposite",
    ]
    assert _texts(page) == _texts(everything)[:2]


# -- AC-2: the filters ------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments, expected",
    [
        ({"reviewer": "security"}, ["a bearer token reached the log"]),
        ({"reviewer": "code-review"}, ["a name reads as its opposite"]),
        ({"reviewer": "adversarial"}, ["the test stays green with the code deleted"]),
        ({"severity": "CRITICAL"}, ["a bearer token reached the log"]),
        ({"severity": "MEDIUM"}, []),
        (
            {"commitSha": _sha("a")},
            ["a bearer token reached the log", "a name reads as its opposite"],
        ),
        ({"q": "bearer"}, ["a bearer token reached the log"]),
        ({"q": "BEARER"}, ["a bearer token reached the log"]),
        ({"q": "nothing matches this"}, []),
        (
            {"reviewer": "security", "severity": "CRITICAL"},
            ["a bearer token reached the log"],
        ),
        ({"reviewer": "security", "severity": "LOW"}, []),
    ],
    ids=[
        "reviewer-security",
        "reviewer-code-review",
        "reviewer-adversarial",
        "severity-critical",
        "severity-matching-nothing",
        "commit-sha",
        "q-substring",
        "q-substring-other-case",
        "q-matching-nothing",
        "two-filters-conjoined",
        "two-filters-conjoined-empty",
    ],
)
async def test_each_filter_returns_exactly_the_matching_findings(
    project: ProjectRegistry, arguments: dict[str, Any], expected: list[str]
) -> None:
    """Every filter this build can serve, driven through the wire surface.

    The three the build cannot serve -- ``pullRequest``, ``family``,
    ``specialist`` -- are refused rather than matched, and are driven by
    :func:`test_an_inert_axis_is_refused_rather_than_answered_empty` below. Their
    *store-level* predicates keep their own tests in ``test_findings_store.py``:
    the store can filter on them, and it is this build's source that derives no
    value, so the predicate is what a future source lifts the refusal onto.

    The two conjunction cases are why an ``OR`` would not pass: one row matches
    each filter alone, and only one matches both.
    """
    _land(project)

    payload = await _call(project, projectId="demo", **arguments)

    assert _texts(payload) == expected
    assert payload["count"] == len(expected)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        {"pullRequest": 11},
        {"pullRequest": MAX_PULL_REQUEST},
        {"family": "a published field"},
        {"specialist": "theurian-tests"},
        {"family": "a published field", "specialist": "theurian-tests"},
        {"reviewer": "security", "pullRequest": 11},
    ],
    ids=[
        "pull-request-that-a-fixture-row-carries",
        "pull-request-at-the-column-ceiling",
        "family",
        "specialist",
        "two-inert-axes",
        "an-inert-axis-beside-a-working-one",
    ],
)
async def test_an_inert_axis_is_refused_rather_than_answered_empty(
    project: ProjectRegistry, arguments: dict[str, Any]
) -> None:
    """R1-5: a filter published as working while no row can ever carry a value.

    ``theurian findings build`` sets all three ``NULL`` (ADR-0029 D5), so
    ``review.findings(pullRequest=N)`` answered ``count: 0`` for every N -- which
    a caller reads as "no findings were recorded on that PR", the exact
    misreadable absence ``commitSha``'s short-sha refusal exists to prevent, and
    worse, because no value would have worked.

    The fixture rows *do* carry values for all three, written directly through the
    adapter: the refusal is a statement about what the shipped **source** derives,
    not about what the store can hold, so a corpus where a match was available is
    the corpus that makes this test mean something.

    The last case is the one an "only when it is the only filter" fix would miss:
    an inert axis conjoined with a working one still narrows to nothing.
    """
    _land(project)

    message = await _call_failing(project, projectId="demo", **arguments)

    assert INERT_FILTER_REFUSAL in message
    assert "ADR-0029 D5" in message
    assert "theurian findings build" in message


@pytest.mark.asyncio
async def test_the_inert_axis_refusal_is_one_constant_whatever_was_sent(
    project: ProjectRegistry,
) -> None:
    """One message for three axes and every value, over two disjoint corpora.

    A refusal that named which axis fired, or quoted the value, would be a second
    input to an error channel SEC-13 keeps at one message -- and a refusal that
    varied with the store would be a channel back into the corpus it declines to
    search. Both are checked here: same messages across values and axes, and
    across two corpora with nothing in common.
    """
    sent: tuple[dict[str, Any], ...] = (
        {"pullRequest": 1},
        {"pullRequest": 999_999},
        {"family": "a published field"},
        {"family": "something else entirely"},
        {"specialist": "theurian-python"},
    )

    _land(project)
    first = {await _call_failing(project, projectId="demo", **q) for q in sent}
    _land(
        project, FindingLoad(accepted=(_finding(_sha("f"), text="a disjoint corpus"),), rejected=())
    )
    second = {await _call_failing(project, projectId="demo", **q) for q in sent}

    assert len(first) == 1, f"the refusal varied with the argument: {first}"
    assert first == second, f"the refusal varied with the corpus: {first} vs {second}"


@pytest.mark.asyncio
async def test_a_wildcard_in_the_text_filter_is_a_literal_character(
    project: ProjectRegistry,
) -> None:
    """``q`` is a substring, never a pattern: ``%`` matches a percent sign.

    Unescaped, a caller who typed ``%`` would get every finding back and read it
    as "everything matches my search" -- a wrong answer wearing the shape of a
    broad one.
    """
    _land(
        project,
        FindingLoad(
            accepted=(
                _finding(_sha("a"), text="a 100% regression", when="2026-08-25T09:00:00+00:00"),
                _finding(_sha("b"), text="plain text", when="2026-08-24T09:00:00+00:00"),
            ),
            rejected=(),
        ),
    )

    assert _texts(await _call(project, projectId="demo", q="%")) == ["a 100% regression"]
    assert _texts(await _call(project, projectId="demo", q="_")) == []


# -- AC-5: a rejected trailer is not reachable ------------------------------


@pytest.mark.asyncio
async def test_no_response_carries_a_byte_of_a_rejected_trailer(project: ProjectRegistry) -> None:
    """AC-5: the rejected row's raw line and reason reach no response, on any path.

    Both fields are author-controlled untrusted text with no reviewed serving
    surface, and the fixture's rejected line is written to look like a real
    finding carrying a secret -- so a leak would read as an ordinary extra row.
    Searched for over the *serialized* response rather than over a field list,
    because a leak that arrived inside ``sourceUri`` is the same disclosure as one
    inside ``findingText``, and a field-by-field check only ever covers the fields
    somebody thought of.
    """
    store = _land(project)
    rejected = store.dump().rejected
    assert len(rejected) == 1, "the fixture's premise: the store really holds a rejected row"

    for arguments in (
        {},
        {"limit": MAX_FINDINGS_LIMIT},
        {"commitSha": _sha("c")},
        {"q": "private key"},
        {"q": "nonsense"},
        {"severity": "CRITICAL"},
    ):
        payload = await _call(project, projectId="demo", **arguments)
        serialized = json.dumps(payload, ensure_ascii=False)
        assert "private key" not in serialized
        assert rejected[0].raw_line not in serialized
        assert rejected[0].reason not in serialized
        assert "nonsense" not in serialized


@pytest.mark.asyncio
async def test_a_rejected_trailer_moves_no_byte_of_any_response(project: ProjectRegistry) -> None:
    """The two-corpora differential ADR-0029 states its closure as.

    One corpus holds a rejected trailer; the other never did. Every response must
    be **byte-identical** across the two -- not merely equal on the fields
    somebody enumerated, which is the check that missed a candidate-displacement
    leak the last time this project ran it field by field. Serialized with sorted
    keys so key *order* cannot mask a difference or manufacture one.
    """
    with_rejected = _land(project, LANDED)
    without_rejected = FindingLoad(accepted=LANDED.accepted, rejected=())

    queries: tuple[dict[str, Any], ...] = (
        {},
        {"limit": 1},
        {"limit": MAX_FINDINGS_LIMIT},
        {"reviewer": "security"},
        {"severity": "CRITICAL"},
        {"commitSha": _sha("c")},
        {"q": "the"},
    )
    captured = [await _call(project, projectId="demo", **q) for q in queries]
    served_with = [json.dumps(payload, sort_keys=True) for payload in captured]
    assert with_rejected.dump().rejected, "the premise: the first corpus really held one"
    assert any(payload["truncated"] for payload in captured), (
        "the premise: at least one capture has `truncated` true. A differential over "
        "captures where the new member is false everywhere would compare it in one "
        "state only, and the state that could carry a bit is the other one"
    )
    assert any(not payload["truncated"] for payload in captured), (
        "the premise: at least one capture has `truncated` false"
    )

    with_rejected.replace_all(without_rejected)
    assert not with_rejected.dump().rejected, "the premise: the second corpus holds none"
    served_without = [
        json.dumps(await _call(project, projectId="demo", **q), sort_keys=True) for q in queries
    ]

    assert served_with == served_without


def _numbered(count: int, *, rejected: tuple[RejectedTrailer, ...] = ()) -> FindingLoad:
    """``count`` accepted findings on distinct commits, newest first when served."""
    return FindingLoad(
        accepted=tuple(
            _finding(
                _sha(chr(ord("a") + index)),
                text=f"finding number {index}",
                when="2026-08-25T09:00:00+00:00",
            )
            for index in range(count)
        ),
        rejected=rejected,
    )


@pytest.mark.asyncio
async def test_a_truncated_page_says_so_and_a_complete_one_says_so(
    project: ProjectRegistry,
) -> None:
    """R1-4: a full page and the whole answer used to be indistinguishable.

    Measured on the real corpus: ``(code-review, MEDIUM)`` matched 128 findings,
    served 100, and reported ``count: 100`` with nothing to say more existed --
    which a caller reads as "exactly 100 exist". The published remedy ("narrow by
    filter") was false against that same corpus, because the axes left to narrow
    on are ``null`` on every row this build produces.

    Both directions, because a flag that is always true is as useless as one that
    is always false.
    """
    _land(project, _numbered(5))

    page = await _call(project, projectId="demo", limit=2)
    whole = await _call(project, projectId="demo", limit=MAX_FINDINGS_LIMIT)

    assert page["count"] == 2
    assert page["truncated"] is True
    assert whole["count"] == 5
    assert whole["truncated"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [1, 2, 4, 5])
async def test_truncated_reads_the_page_boundary_and_not_one_row_either_side(
    project: ProjectRegistry, limit: int
) -> None:
    """The off-by-one, walked across the boundary a five-row corpus provides.

    ``limit == 5`` is the case a ``>=`` comparison gets wrong: the read asks for
    six, five come back, and a page that is exactly the whole answer must not
    claim more exists. ``limit == 4`` is the case a probe that never asked for the
    extra row gets wrong.
    """
    _land(project, _numbered(5))

    payload = await _call(project, projectId="demo", limit=limit)

    assert payload["count"] == min(limit, 5)
    assert payload["truncated"] is (limit < 5)


@pytest.mark.asyncio
async def test_the_truncation_signal_is_computed_over_servable_rows_alone(
    project: ProjectRegistry,
) -> None:
    """``truncated`` must not become a one-bit channel for the rejected population.

    The differential above answers "does the whole response move"; this answers
    the sharper question the new member opens, at the boundary where a leak would
    show: with exactly ``limit`` **accepted** rows and a rejected trailer present,
    ``truncated`` must be false. If the probe row could be a rejected trailer, the
    corpus that holds one would answer true and the corpus that never did would
    answer false -- one bit, on demand, about content this tool refuses to serve.

    Structurally it holds because the probe goes through the same
    ``findings``-only statement every served row does; this is the assertion that
    would notice if that ever stopped being true.
    """
    store = _land(
        project,
        _numbered(
            3,
            rejected=(
                RejectedTrailer(
                    _sha("z"),
                    "Review-Finding: nonsense CRITICAL — the private key is in fixtures/",
                    "unknown reviewer 'nonsense'",
                ),
            ),
        ),
    )
    assert store.dump().rejected, "the premise: the corpus really holds a rejected trailer"

    at_the_boundary = await _call(project, projectId="demo", limit=3)
    inside_it = await _call(project, projectId="demo", limit=2)

    assert at_the_boundary["truncated"] is False, (
        "a rejected trailer filled the probe slot: `truncated` now carries a bit "
        "about rows this tool does not serve"
    )
    assert inside_it["truncated"] is True, "the premise: the boundary case is not vacuous"

    store.replace_all(_numbered(3))
    assert not store.dump().rejected, "the premise: the second corpus holds none"
    assert json.dumps(
        await _call(project, projectId="demo", limit=3), sort_keys=True
    ) == json.dumps(at_the_boundary, sort_keys=True)
    assert json.dumps(
        await _call(project, projectId="demo", limit=2), sort_keys=True
    ) == json.dumps(inside_it, sort_keys=True)


# -- AC-3: the store cannot be served from ----------------------------------


@pytest.mark.asyncio
async def test_a_project_with_no_findings_store_is_refused_not_answered_empty(
    project: ProjectRegistry,
) -> None:
    """ "Never built" must not read as "no findings" (ADR-0029 AC-3).

    ``count: 0`` here would be a false absence a caller acts on -- and this is the
    default state of every project until somebody runs the build, so it is the
    answer most callers meet first.
    """
    assert not _store_path(project).exists(), "the premise: no store has been built"

    message = await _call_failing(project, projectId="demo")

    # `in`, not `==`: the SDK prefixes a failing tool's message with "Error
    # executing tool review.findings: ". That prefix is the transport's and is
    # constant; what this file pins is the refusal Theurian wrote.
    assert FINDINGS_UNAVAILABLE_REFUSAL in message
    assert "theurian findings build" in message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage",
    ["stale-parser-stamp", "stale-schema-version", "unreadable-file", "missing-file"],
)
async def test_every_unservable_store_gives_the_same_constant_refusal(
    project: ProjectRegistry, damage: str
) -> None:
    """One message for four states, and it is a constant (SEC-13, AC-3).

    A message that named which state fired would publish something about a file
    the caller cannot read, and would let a caller tell "stale" from "absent" from
    "corrupt" -- the error-distinguishability family, arriving through a refusal
    rather than through a field. The cure is the same rebuild for all four, so
    there is nothing a distinction would buy.
    """
    _land(project)
    path = _store_path(project)
    if damage == "stale-parser-stamp":
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("UPDATE findings_metadata SET parser_stamp = 'old' WHERE id = 1")
            connection.commit()
    elif damage == "stale-schema-version":
        with closing(sqlite3.connect(path)) as connection:
            connection.execute(
                "UPDATE findings_metadata SET findings_schema_version = -1 WHERE id = 1"
            )
            connection.commit()
    elif damage == "unreadable-file":
        path.write_bytes(b"not a database at all")
    else:
        path.unlink()

    message = await _call_failing(project, projectId="demo")

    assert FINDINGS_UNAVAILABLE_REFUSAL in message


@pytest.mark.asyncio
async def test_a_store_this_installation_did_not_build_is_not_served(
    project: ProjectRegistry,
) -> None:
    """ADR-0004, SEC-7, T-19: presence on disk is not evidence of anything.

    The findings store is derived and git-ignored, so a repository contributor can
    force-add a fabricated one past that ignore and a victim who clones, registers
    and serves -- without ever running ``theurian findings build`` -- was handed
    its rows as this repository's own review history (PR #504 round 1, R1-1). The
    planted store here is well-formed, current and readable: nothing about the
    *file* is wrong, which is exactly why file-shaped checks cannot catch it.
    """
    planted = _plant(project)
    assert planted.dump().findings, "the premise: the planted store really holds rows"

    message = await _call_failing(project, projectId="demo")

    assert FINDINGS_UNAVAILABLE_REFUSAL in message
    assert "bearer token" not in message, "a refused response quoted the planted store"


@pytest.mark.asyncio
async def test_recording_the_build_is_what_makes_the_same_store_servable(
    project: ProjectRegistry,
) -> None:
    """The other arm, over one unchanged file: provenance is the whole difference.

    The store's bytes are written once and never touched again. What changes
    between the refusal and the rows is a record in ``THEURIAN_DATA_DIR`` --
    outside the repository, the one place a repository contributor cannot write --
    which is the discriminator :class:`BuildProvenance` exists to be.
    """
    _plant(project)
    before = await _call_failing(project, projectId="demo")
    digest = _store_path(project).read_bytes()

    _record_provenance(project)
    payload = await _call(project, projectId="demo")

    assert FINDINGS_UNAVAILABLE_REFUSAL in before
    assert payload["count"] == 3
    assert _store_path(project).read_bytes() == digest, (
        "the store was rewritten between the two calls, so this test compared two "
        "different files rather than one file's provenance"
    )


@pytest.mark.asyncio
async def test_a_planted_store_is_refused_in_the_same_words_as_a_missing_one(
    project: ProjectRegistry,
) -> None:
    """The two states a caller must not be able to tell apart (SEC-13).

    A refusal that named the provenance arm would tell whoever planted the store
    that the plant was detected, and would tell the victim a story about a file
    only the attacker wrote. One string, or the error channel carries a second
    input.
    """
    missing = await _call_failing(project, projectId="demo")
    _plant(project)
    planted = await _call_failing(project, projectId="demo")

    assert planted == missing, (
        f"a planted store and a missing one are distinguishable:\n{planted}\n{missing}"
    )


@pytest.mark.asyncio
async def test_the_unservable_refusal_does_not_vary_with_what_the_store_holds(
    project: ProjectRegistry,
) -> None:
    """The other half of "constant": it does not move with the corpus either.

    A refusal that varied with the store's content -- its size, its commits, the
    text of a finding -- would be a channel back into exactly what the refusal is
    declining to serve. Two stores with disjoint content, both made unservable the
    same way, must produce one string.
    """
    messages = set()
    for load in (
        LANDED,
        FindingLoad(
            accepted=(_finding(_sha("f"), text="a completely different corpus"),) * 1,
            rejected=(RejectedTrailer(_sha("f"), "Review-Finding: junk", "unknown reviewer"),),
        ),
    ):
        _land(project, load)
        path = _store_path(project)
        path.write_bytes(b"not a database at all")
        messages.add(await _call_failing(project, projectId="demo"))

    assert len(messages) == 1, f"the refusal varied with the store's content: {messages}"
    assert FINDINGS_UNAVAILABLE_REFUSAL in messages.pop()


# -- AC-4: bounds and vocabularies ------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments, expected_fragments",
    [
        ({"reviewer": "code"}, ["code-review, security, adversarial", "'code'"]),
        ({"reviewer": "SECURITY"}, ["code-review, security, adversarial"]),
        ({"severity": "high"}, ["CRITICAL, HIGH, MEDIUM, LOW", "'high'"]),
        ({"severity": "URGENT"}, ["CRITICAL, HIGH, MEDIUM, LOW"]),
        ({"limit": 0}, [f"between 1 and {MAX_FINDINGS_LIMIT}"]),
        ({"limit": MAX_FINDINGS_LIMIT + 1}, [f"between 1 and {MAX_FINDINGS_LIMIT}"]),
        ({"limit": -5}, [f"between 1 and {MAX_FINDINGS_LIMIT}"]),
        ({"pullRequest": 0}, ["must be a positive number"]),
        ({"commitSha": "141cf6f"}, ["40 or 64 lower-case hex"]),
        ({"commitSha": "Z" * 40}, ["40 or 64 lower-case hex"]),
        ({"q": ""}, ["`q` is empty"]),
        ({"family": ""}, ["`family` is empty"]),
    ],
    ids=[
        "reviewer-historical-alias",
        "reviewer-wrong-case",
        "severity-wrong-case",
        "severity-unknown",
        "limit-zero",
        "limit-over-cap",
        "limit-negative",
        "pull-request-zero",
        "commit-sha-short",
        "commit-sha-not-hex",
        "q-empty",
        "family-empty",
    ],
)
async def test_a_value_outside_its_bound_is_refused_naming_the_bound(
    project: ProjectRegistry, arguments: dict[str, Any], expected_fragments: list[str]
) -> None:
    """AC-4: refused, and told what to send -- not clamped, not silently empty.

    Each of these has a plausible silent alternative that is worse. An over-cap
    ``limit`` clamped to 100 reads as the whole answer; a short ``commitSha``
    matched literally returns nothing and reads as "no findings on that commit";
    an unknown reviewer token treated as "no filter" returns everything. The
    refusal is the only one of the four that cannot be misread.

    The historical alias ``code`` is here on purpose: the parser normalises it in
    signed history, so no stored row carries it, and accepting it at this surface
    would be a second spelling of one value.
    """
    _land(project)

    message = await _call_failing(project, projectId="demo", **arguments)

    for fragment in expected_fragments:
        assert fragment in message, f"the refusal did not name {fragment!r}: {message}"


#: Every string filter this tool publishes. The population for the two
#: transportability refusals below, so a seventh filter added without the guard
#: fails here rather than being covered by whichever three somebody listed.
STRING_FILTERS = ("reviewer", "severity", "family", "specialist", "commitSha", "q")


@pytest.mark.asyncio
@pytest.mark.parametrize("filter_name", STRING_FILTERS)
@pytest.mark.parametrize(
    "value", ["\x00", "log\x00zzz", "\x00log"], ids=["only", "after", "before"]
)
async def test_a_nul_in_any_string_filter_is_refused_naming_the_byte(
    project: ProjectRegistry, filter_name: str, value: str
) -> None:
    """R1-6: SQLite's pattern walker stops at a NUL, so "matched literally" was false.

    ``q="\\x00"`` matched every row and ``q="log\\x00zzz"`` became a suffix match --
    the mechanism is pinned at its source in
    ``test_findings_store.py::test_a_nul_truncates_the_like_pattern_while_its_neighbour_bytes_do_not``.
    Refusing is the honest fix rather than the conservative one: a git
    commit-message line cannot carry a NUL, so no stored value contains one and no
    legitimate filter needs one. Every string filter, not only ``q``: the byte is
    equally impossible in a reviewer token and a sha, and a guard applied to the
    one filter somebody was thinking about is how the next filter arrives without
    it.
    """
    _land(project)

    message = await _call_failing(project, projectId="demo", **{filter_name: value})

    assert "NUL byte" in message, f"the refusal did not name the byte: {message}"
    assert "U+0000" in message


@pytest.mark.asyncio
@pytest.mark.parametrize("filter_name", STRING_FILTERS)
async def test_an_untransportable_string_filter_is_refused_rather_than_folded(
    project: ProjectRegistry, filter_name: str
) -> None:
    """A lone surrogate is refused here, where ``knowledge.search`` folds it.

    ``"\\ud800"`` is a ``str`` Python accepts and UTF-8 cannot encode, so it died
    as a ``UnicodeEncodeError`` at the SQLite bind -- a crash where the contract
    promises a graded refusal (R1-2 face iii). ``knowledge.search`` answers the
    same shape by substituting (``mcp/tools.py``'s ``encode("utf-8", "replace")``)
    because refusing is the behaviour a search box must not have; this tool answers
    a filtered question, where folding would search for a value the caller did not
    send and report ``count: 0`` about it. The divergence is deliberate and is
    recorded at both ends.
    """
    _land(project)

    message = await _call_failing(project, projectId="demo", **{filter_name: "a\ud800b"})

    assert "not transportable text" in message, f"the refusal was not the graded one: {message}"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value, matches",
    [("log\x01zzz", []), ("\x01", []), ("token", ["a token reached the log"])],
    ids=["neighbour-byte-inside", "neighbour-byte-alone", "ordinary-substring"],
)
async def test_a_byte_next_to_nul_is_matched_literally_rather_than_refused(
    project: ProjectRegistry, value: str, matches: list[str]
) -> None:
    """The control on the refusal above: it is about NUL, not about control bytes.

    A refusal wide enough to catch ``\\x01`` would be a filter a caller cannot use
    to search for text that legitimately contains one. ``\\x01`` is not special to
    ``LIKE``, so it is matched literally -- and matching literally is what this
    filter promises.
    """
    _land(
        project,
        FindingLoad(
            accepted=(_finding(_sha("a"), text="a token reached the log"),),
            rejected=(),
        ),
    )

    payload = await _call(project, projectId="demo", q=value)

    assert _texts(payload) == matches


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments, expected_fragments",
    [
        ({"pullRequest": MAX_PULL_REQUEST + 1}, [f"no larger than {MAX_PULL_REQUEST}"]),
        ({"limit": 2**63}, [f"between 1 and {MAX_FINDINGS_LIMIT}"]),
    ],
    ids=["pull-request-past-the-column", "limit-past-the-column"],
)
async def test_an_integer_wider_than_the_column_is_refused_rather_than_crashing(
    project: ProjectRegistry, arguments: dict[str, Any], expected_fragments: list[str]
) -> None:
    """R1-2 face i: the bind's ``OverflowError`` was caught by no layer.

    ``pullRequest = 2**63`` passed every check on this surface and died binding
    the parameter, so a caller received a crash where the published contract
    promises a refusal naming a bound. The bound is the column's, not a policy:
    the value one past it is the first that cannot be stored, and therefore the
    first that could never match a row.
    """
    _land(project)

    message = await _call_failing(project, projectId="demo", **arguments)

    for fragment in expected_fragments:
        assert fragment in message, f"the refusal did not name {fragment!r}: {message}"


@pytest.mark.asyncio
async def test_the_largest_storable_pull_request_passes_the_bound_and_meets_the_axis(
    project: ProjectRegistry,
) -> None:
    """The other side of the ceiling, which a refusal test alone cannot hold.

    A bound is two claims, and the one that goes silently wrong is "everything
    inside it still works". Since ``pullRequest`` is an inert axis in this build,
    "works" means *reaching the axis refusal rather than the bound's*: an
    off-by-one in the ceiling would answer the wrong refusal for a value the store
    can hold, and would keep answering it after a future source makes the axis
    live.

    This is also what keeps :func:`_pull_request` from becoming a guard no input
    reaches. The bounds run before the inert-axis refusal precisely so that both
    stay reachable and separately observable through the one public surface.
    """
    _land(project)

    message = await _call_failing(project, projectId="demo", pullRequest=MAX_PULL_REQUEST)

    assert INERT_FILTER_REFUSAL in message
    assert "no larger than" not in message, (
        "the largest storable pull request was refused by the column bound, so the "
        "ceiling is one too low"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("argument", ["pullRequest", "limit"])
@pytest.mark.parametrize(
    "digits",
    [_INT_MAX_STR_DIGITS - 1, _INT_MAX_STR_DIGITS, _INT_MAX_STR_DIGITS + 1, 10_000],
    ids=["under-the-render-limit", "at-the-render-limit", "past-it", "far-past-it"],
)
async def test_the_refusal_for_an_absurd_number_is_built_rather_than_crashing(
    project: ProjectRegistry, argument: str, digits: int
) -> None:
    """R1-2 face ii: the refusal path itself must be total.

    CPython refuses to render an integer past ``sys.get_int_max_str_digits()``
    (4,300), so the f-string that was to carry "got {value}" raised ``ValueError``
    *while building the promised refusal* -- the tool crashed on the very input its
    contract says it refuses. The parametrisation walks the threshold from below to
    far past it, because a fix that measured the number only when it was "very
    large" would still crash at 4,301.

    The digit count is asserted as the *description*, and the number itself as
    absent: this is the integer half of "never echo an over-long input" -- a
    refusal quoting a 4,301-digit integer is a reflector of whatever the caller
    sent.
    """
    _land(project)
    value = 10 ** (digits - 1)

    message = await _call_failing(project, projectId="demo", **{argument: value})

    if digits > MAX_ECHOED_DIGITS:
        assert f"a {digits}-digit number" in message, (
            f"the refusal did not describe the number by its size: {message}"
        )
        assert "0" * (MAX_ECHOED_DIGITS + 1) not in message, (
            "the refusal quoted a run of the caller's own digits back, which is the "
            "reflector the digit-count description exists to avoid"
        )
    assert len(message) < MAX_QUERY_CHARS, (
        f"a {digits}-digit argument provoked a {len(message)}-character refusal"
    )


@pytest.mark.asyncio
async def test_an_over_long_filter_is_reported_by_length_and_never_echoed(
    project: ProjectRegistry,
) -> None:
    """The amplifier this project already closed for ``query`` and ``itemId``.

    Echoing an over-long value back turns a refusal into a ~1x reflector of
    whatever the caller sent (#17). The length is named instead -- which is what
    the caller needs -- and the bytes stay out of the response.
    """
    _land(project)
    oversized = "z" * (MAX_FILTER_CHARS + 500)

    message = await _call_failing(project, projectId="demo", q=oversized)

    assert str(MAX_FILTER_CHARS) in message
    assert str(len(oversized)) in message
    assert oversized not in message
    assert len(message) < len(oversized), "the refusal is smaller than what provoked it"


@pytest.mark.asyncio
async def test_a_bad_filter_is_refused_before_the_store_is_reached(
    project: ProjectRegistry,
) -> None:
    """Bounds are checked before any file is opened (T-6), and that is observable.

    With no store built at all, a request carrying a bad token comes back with the
    *token's* refusal rather than the store's: the only way that ordering holds is
    if nothing touched the store first. It also keeps the refusal a caller gets
    for a malformed filter independent of the project's own state.
    """
    assert not _store_path(project).exists(), "the premise: there is no store to read"

    message = await _call_failing(project, projectId="demo", reviewer="nobody")

    assert "code-review, security, adversarial" in message
    assert FINDINGS_UNAVAILABLE_REFUSAL not in message


@pytest.mark.asyncio
async def test_a_sha256_repository_s_commit_sha_is_served_not_refused(
    project: ProjectRegistry,
) -> None:
    """A 64-character sha is a full sha, and this build says so in one place only.

    ``_COMMIT_SHA`` accepts 40 or 64 lower-case hex, because git's ``%H`` is 64
    characters in a SHA-256 repository -- and nothing drove the second arm:
    narrowing the pattern to 40 characters passed the whole suite (measured
    2026-09-02 against ``e808c82``; mutation ``commit-sha-40-only``).

    What that mutation would ship is a refusal a caller cannot act on. Their sha
    *is* the full one their own ``git log`` prints; being told to send a full sha
    instead names no reachable value, and the filter they came for is closed to
    them for as long as their repository stays on SHA-256.
    """
    sha256 = "d" * 64
    _land(
        project,
        FindingLoad(
            accepted=(_finding(sha256, text="a finding on a sha-256 commit"),),
            rejected=(),
        ),
    )

    payload = await _call(project, projectId="demo", commitSha=sha256)

    assert _texts(payload) == ["a finding on a sha-256 commit"]
    assert payload["findings"][0]["commitSha"] == sha256


@pytest.mark.asyncio
async def test_a_bad_filter_is_refused_before_the_project_is_resolved(
    project: ProjectRegistry,
) -> None:
    """Bounds run before the registry read, not only before the store read.

    ``test_a_bad_filter_is_refused_before_the_store_is_reached`` pins the second
    half of that ordering; this pins the first, which nothing held -- moving
    ``_resolve`` ahead of ``build_query`` passed the whole suite (measured
    2026-09-02 against ``e808c82``; mutation ``resolve-before-bounds``), because
    every bound test above sends a project that resolves.

    Two things ride on the order. A refused request must cost the daemon nothing
    (T-6): no registry file read, no state database opened, before a value that
    was never going to be searched with is rejected. And the refusal a caller
    reads for a malformed filter must be the same one whatever the project's own
    state is -- otherwise the error channel carries a second input, which is one
    more thing SEC-13 has to reason about.
    """
    message = await _call_failing(project, projectId="not-registered", reviewer="nobody")

    assert "code-review, security, adversarial" in message
    assert "not registered" not in message, (
        "the project was resolved before the filter was bounded: a caller sending a "
        "bad token learns whether the project exists, and the daemon read the "
        "registry to answer a request it was always going to refuse"
    )


# -- The numbers themselves, which a symbolic assertion cannot hold ---------


def test_the_published_bounds_are_the_bounds_this_build_enforces() -> None:
    """The one independent statement of three numbers nothing else held.

    Every bound test above names ``MAX_FINDINGS_LIMIT``,
    ``DEFAULT_FINDINGS_LIMIT`` and ``MAX_FILTER_CHARS`` symbolically, which is
    what a bound test should do -- and is also why the *numbers* went unheld:
    raising the cap tenfold, dropping the default to three, and widening the
    filter bound from 200 to 5,000 each passed the whole suite (measured
    2026-09-02 against ``e808c82``; mutations ``findings-cap-1000``,
    ``findings-default-3`` and ``filter-chars-5000``, 4801 tests green under
    each). Those three measurements stand as records of that commit; what
    changed since is this pin, not the mutations' behaviour then.

    ``docs/protocol/mcp-tools.md`` is where all three are published, so it is
    the pin: each is recomputed here from the live constant rather than
    restated. A client reads the limit row to size its own paging, and a build
    whose cap is not the published cap has told it something false about how
    much of an answer it is getting; the filter bound is published because a
    caller writing a ``q`` has no other way to learn where the refusal starts.
    """
    published = (REPO_ROOT / "docs/protocol/mcp-tools.md").read_text(encoding="utf-8")

    row = f"| `limit` | at most {MAX_FINDINGS_LIMIT}, default {DEFAULT_FINDINGS_LIMIT} |"
    assert row in published, (
        f"docs/protocol/mcp-tools.md does not carry {row!r}. Either a bound moved "
        f"and the published table now describes a build that does not exist, or the "
        f"row was reworded -- in which case update this pin *and* check that the new "
        f"wording still states both numbers. These two constants have no schema to "
        f"hold them the way `maxLength: 2000` holds `knowledge.search`'s `query`, so "
        f"this row is the only thing standing between a caller and a false statement "
        f"about how much of an answer they received."
    )
    bound = f"is bounded at {MAX_FILTER_CHARS} characters"
    assert bound in published, (
        f"docs/protocol/mcp-tools.md does not carry {bound!r}. `MAX_FILTER_CHARS` is "
        f"an amplification control (#17) as well as a filter bound: it decides how "
        f"long a caller-controlled string this surface will quote back in a refusal. "
        f"Widening it silently was green against the whole suite until this sentence "
        f"was published, which is why the number and not only the property is held."
    )


@pytest.mark.asyncio
async def test_a_call_with_no_limit_returns_the_default_page_not_the_whole_store(
    project: ProjectRegistry,
) -> None:
    """The default is applied, and it is a page rather than the corpus.

    Every other serving test here reads a three-row store, where the default
    cannot be observed at all: three rows come back whether the default is 20,
    three, or not applied. So the one thing ``DEFAULT_FINDINGS_LIMIT`` exists to
    do -- keep the common call a page rather than a context-budget event -- was
    driven by nothing.

    The store is deliberately larger than the default here, and the premise is
    asserted: a corpus that fit inside the default would make the equality below
    true for the wrong reason.
    """
    oversized = FindingLoad(
        accepted=tuple(
            _finding(_sha("a"), text=f"finding number {index}", when="2026-08-25T09:00:00+00:00")
            for index in range(DEFAULT_FINDINGS_LIMIT + 5)
        ),
        rejected=(),
    )
    store = _land(project, oversized)
    assert len(store.dump().findings) > DEFAULT_FINDINGS_LIMIT, (
        "the premise: the store holds more findings than one default page"
    )

    payload = await _call(project, projectId="demo")

    assert payload["count"] == DEFAULT_FINDINGS_LIMIT == len(payload["findings"])


@pytest.mark.asyncio
@pytest.mark.parametrize("filter_name", ["reviewer", "severity", "commitSha"])
async def test_a_refusal_is_never_a_bigger_reflector_than_the_published_echo(
    project: ProjectRegistry, filter_name: str
) -> None:
    """What ``MAX_FILTER_CHARS`` buys, asserted as a property and not as a number.

    A value *inside* the length bound is quoted back, deliberately: a typo is
    what the refusal exists to make visible. That makes the bound an
    amplification control (#17), and nothing published stated it -- widening it
    from 200 to 5,000 passed the whole suite (measured 2026-09-02 against
    ``e808c82``; mutation ``filter-chars-5000``). The number is published now,
    and ``test_the_published_bounds_are_the_bounds_this_build_enforces`` holds
    the record against the constant; this test is the other half, and it is the
    half that survives the number changing: what it asserts is that whatever the
    bound *is*, it admits every legitimate value and refuses to become a
    reflector.

    So the property is pinned against two live values instead of the number:

    * every legitimate filter value fits -- a 64-character SHA-256 commit sha,
      the longest reviewer token, the longest severity -- so no real query is
      refused for its length;
    * the largest refusal a caller can provoke stays under ``MAX_QUERY_CHARS``,
      the size of the one caller-controlled string this daemon already publishes
      as safe to echo (``knowledge.search``'s ``query``, ``maxLength: 2000`` in
      its schema). A findings refusal may not be a bigger reflector than the
      echo that number was chosen for.

    The three filters here are exactly the ones that quote: ``family``,
    ``specialist`` and ``q`` accept any in-bound string and answer ``count: 0``
    rather than refusing, so they echo nothing to bound.
    """
    longest_legitimate = max(
        # A SHA-256 repository's commit sha, the longest value any of these
        # filters legitimately carries.
        64,
        *(len(member.value) for member in ReviewerToken),
        *(len(member.value) for member in FindingSeverity),
    )
    assert longest_legitimate < MAX_FILTER_CHARS, (
        f"MAX_FILTER_CHARS is {MAX_FILTER_CHARS}, shorter than the longest "
        f"legitimate filter value ({longest_legitimate}); a real query would be "
        f"refused for its length"
    )
    _land(project)

    message = await _call_failing(
        project, projectId="demo", **{filter_name: "z" * MAX_FILTER_CHARS}
    )

    assert len(message) < MAX_QUERY_CHARS, (
        f"a `{filter_name}` at the length bound provoked a {len(message)}-character "
        f"refusal, past the {MAX_QUERY_CHARS} this daemon publishes as the largest "
        f"caller-controlled string it will echo. MAX_FILTER_CHARS is {MAX_FILTER_CHARS}: "
        f"a refusal that quotes the caller's own token turns this surface into a "
        f"reflector as soon as the bound is wide enough to be worth pointing at "
        f"something else."
    )


# -- The project gate -------------------------------------------------------


@pytest.mark.asyncio
async def test_an_unregistered_project_is_refused_with_the_registered_ids(
    project: ProjectRegistry,
) -> None:
    """The same gate every project-scoped tool passes (ADR-0002, SEC-13).

    The refusal names what *is* registered -- an id is not another project's
    content -- and never anything about what any project holds.
    """
    _land(project)

    message = await _call_failing(project, projectId="not-registered")

    assert "not registered" in message
    assert "demo" in message
    assert "bearer token" not in message


@pytest.mark.asyncio
async def test_the_tool_requires_an_explicit_project(project: ProjectRegistry) -> None:
    """No implicit "current project" (ADR-0002): many agents share one daemon."""
    _land(project)

    with pytest.raises(SdkToolError):
        await _call(project)
