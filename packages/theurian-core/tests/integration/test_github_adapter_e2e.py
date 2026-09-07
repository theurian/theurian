"""End-to-end fidelity of the GitHub review adapter against real ``gh``.

Every other test of this adapter drives a stand-in ``gh`` binary that replays a
constructed document (``test_gh_review_provider.py``'s ``FakeGh``). That proves
the adapter parses the shape the tests *believe* GitHub sends. It cannot prove
the belief. This module runs the **real** ``gh api graphql`` against Theurian's
own public repository -- the production read path, read-only -- and asserts that
the domain values the adapter builds from a live response carry the fields the
slice-1 adapter fetches: pull-request events (number, title, author, commits,
merge state) and their inline review threads (comments, bodies, resolution).

**Scope of the fidelity claim, stated honestly.** Slice 1 fetches events,
inline ``reviewThreads`` with their comments, and ``resolved_at``. It does NOT
fetch the five author-controlled evidence fields (top-level review bodies, PR
description, labels, branch name, milestone) -- those land in slice 2, so their
absence here is expected-and-owned, not a finding. And Theurian's own inline
review corpus is thin and monotone: ADR-0030 (lines 1122-1146) enumerates it as
**11 inline threads across PRs #12, #132, #224, #352, #569, every root comment
authored by ``github-advanced-security[bot]``** -- no varied human authors, no
CJK bodies, no long human prose. That diversity is under-exercised by this repo
by construction, which is exactly why slice 3 plants a frozen fixture. So this
module asserts fidelity on what the real corpus HAS (the recorded 11 threads,
including #352's five with ``resolved_at`` unset) and does not pretend to
exercise shapes the corpus lacks.

The known-positive PRs from the ADR are the harness's own positive control: a
clean read of theurian's recent window counts almost no threads (the bot's few),
so an assertion that "no rich threads exist" would pass vacuously against the
wrong window; targeting the recorded PRs proves the read path demonstrably hits
the planted positives before any absence is reported.

It is skipped, never failed, when ``gh`` is absent, unauthenticated, or the
network is unreachable -- a missing credential is not a defect in the adapter.
A response whose *shape* diverges from what the adapter parses IS a finding, and
it surfaces here as an assertion failure with the divergent field named.

The adapter forwards ``HOME``/``GH_CONFIG_DIR``/``XDG_CONFIG_HOME`` by value and
no token (``environment.FORWARDED_BY_VALUE``), so a real spawn authenticates
through the operator's own persisted ``gh`` credential exactly as the shipped
tool does. Passing the real ``HOME`` here is that same production behaviour, not
a configuration probe.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, cast

import pytest

from theurian.domain.enums import ReviewThreadState
from theurian.domain.identifiers import ProjectId
from theurian.domain.review import ReviewComment, ReviewEvent, ReviewParticipant, ReviewThread
from theurian.domain.review_ingest import RefusalGrade, ReviewIngestRefusedError
from theurian.infrastructure.github.environment import FORWARDED_BY_VALUE
from theurian.infrastructure.github.review_provider import GitHubReviewProvider

pytestmark = [pytest.mark.integration, pytest.mark.e2e, pytest.mark.asyncio]

#: Theurian's own public repository. The adapter is public-repos-only in v1, and
#: a project reading its own review history is the first real caller ADR-0030
#: names, so it is the honest fixture: rich threads, CJK reviewer bodies, and
#: every resolution state, all owned by the org running the test.
_REPOSITORY = "theurian/theurian"
_PROJECT = ProjectId("theurian")


def _gh_is_ready() -> str | None:
    """The reason a real run cannot happen, or ``None`` when it can.

    Read-only and side-effect-free: ``which`` and ``gh auth status``, no API
    call, no config mutation.
    """
    gh = shutil.which("gh")
    if gh is None:
        return "gh is not on PATH"
    try:
        completed = subprocess.run(  # noqa: S603 - argv[0] is a shutil.which-resolved absolute path
            [gh, "auth", "status"],
            capture_output=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"gh auth status could not run: {exc}"
    if completed.returncode != 0:
        return "gh is not authenticated (gh auth status exited non-zero)"
    return None


def _real_environment() -> dict[str, str]:
    """The parent environment a real spawn authenticates through.

    Only the variables the adapter forwards by value are worth passing; it
    strips everything else regardless. The real ``HOME`` is what lets the
    spawned ``gh`` reach the operator's keyring, which is how the shipped tool
    authenticates. The set is imported from the production constant rather than
    re-listed, so a new config-locator added there is forwarded here too.
    """
    return {name: os.environ[name] for name in FORWARDED_BY_VALUE if name in os.environ}


async def _read_or_skip(coro: object) -> object:
    """Await a real adapter read, degrading a transport refusal to a skip.

    The up-front ``gh auth status`` guard cannot cover a network that drops
    *between* the guard and a read, and it rests on ``gh`` exiting non-zero when
    offline (unpinned third-party behaviour). Either way a transport-layer
    ``ReviewIngestRefusedError`` here is an environment condition, not an adapter
    defect, so it skips rather than erroring the run -- which keeps a
    network-restricted CI job (``Full suite with no network``) green even on a
    machine where ``gh`` is authenticated locally. A refusal that is NOT
    transport (a page cap, a malformed document) is a real finding and is left to
    raise.
    """
    try:
        return await coro  # type: ignore[misc]
    except ReviewIngestRefusedError as exc:
        if exc.grade in _TRANSPORT_GRADES:
            pytest.skip(f"gh could not reach the API ({exc.grade}): {exc}")
        raise


#: The refusal grades that mean "the environment could not run the read", not
#: "the read returned a shape the adapter refuses". Keyed on the structured
#: :class:`RefusalGrade` the adapter attaches, never on message text: a
#: non-zero exit or empty output is ``TOOL_FAILED`` (``gh_cli`` grades it so),
#: the binary being gone is ``TOOL_MISSING``, and auth/version faults are their
#: own grades. A ``LIMIT_EXCEEDED`` or an allowlist/transport-override refusal is
#: NOT here -- those are real findings and are left to raise.
_TRANSPORT_GRADES: Final = frozenset(
    {
        RefusalGrade.TOOL_MISSING,
        RefusalGrade.TOOL_TOO_OLD,
        RefusalGrade.TOOL_UNAUTHENTICATED,
        RefusalGrade.TOOL_FAILED,
    }
)


@pytest.fixture(scope="module")
def _skip_reason() -> str | None:
    return _gh_is_ready()


@pytest.fixture
def provider(tmp_path: Path, _skip_reason: str | None) -> GitHubReviewProvider:
    if _skip_reason is not None:
        pytest.skip(_skip_reason)
    knowledge = tmp_path / "project" / ".theurian"
    knowledge.mkdir(parents=True)
    (knowledge / "config.yaml").write_text(
        "apiVersion: theurian.dev/v1\n"
        "providers:\n"
        "  review:\n"
        "    repositories:\n"
        f"      - {_REPOSITORY}\n",
        encoding="utf-8",
    )
    return GitHubReviewProvider(
        project_root=tmp_path / "project",
        config_file=knowledge / "config.yaml",
        parent_environment=_real_environment(),
        binary=None,  # locate the real gh on PATH
    )


async def _some_events(provider: GitHubReviewProvider, limit: int = 20) -> tuple[ReviewEvent, ...]:
    result = await _read_or_skip(provider.list_pull_requests(_PROJECT, _REPOSITORY, limit=limit))
    return cast("tuple[ReviewEvent, ...]", result)


async def _threads_of(
    provider: GitHubReviewProvider, event: ReviewEvent
) -> tuple[ReviewThread, ...]:
    result = await _read_or_skip(provider.get_threads(_PROJECT, event))
    return cast("tuple[ReviewThread, ...]", result)


async def _threads_across(
    provider: GitHubReviewProvider, events: tuple[ReviewEvent, ...], cap: int = 12
) -> list[ReviewThread]:
    """Threads gathered across events until ``cap`` are seen or events run out."""
    gathered: list[ReviewThread] = []
    for event in events:
        for thread in await _threads_of(provider, event):
            gathered.append(thread)
            if len(gathered) >= cap:
                return gathered
    return gathered


async def test_a_real_pull_request_read_yields_well_formed_events(
    provider: GitHubReviewProvider,
) -> None:
    """The list read returns real events whose required fields all parsed.

    This is the assertion the ``-f``/``-F`` GraphQL typing defect would have
    failed: an ``Int!`` sent as a string made the real query error where the
    stand-in could not type-check. If the live shape regresses on any required
    field, one of these attributes is missing or wrong-typed here.
    """
    events = await _some_events(provider)
    assert events, "theurian/theurian has merged pull requests; a real read returned none"
    for event in events:
        assert event.provider == "github"
        assert event.repository == _REPOSITORY
        assert event.number > 0
        assert event.title  # required text
        assert event.author.display_name  # required participant
        assert len(event.head_commit) == 40  # a real 40-hex oid
        assert len(event.base_commit) == 40
        assert event.url.startswith(f"https://github.com/{_REPOSITORY}/pull/")


async def test_branch_and_merge_fields_survive_a_real_read(
    provider: GitHubReviewProvider,
) -> None:
    """A merged PR carries its merge commit and merged-at; the fields the
    constructed fixture sets are the fields a real merged PR fills."""
    events = await _some_events(provider)
    merged = [e for e in events if e.merged]
    assert merged, "theurian/theurian's recent PRs are squash-merged; none read as merged"
    sample = merged[0]
    assert sample.merge_commit is not None and len(sample.merge_commit) == 40
    assert sample.merged_at is not None
    assert sample.merged_at >= sample.created_at


async def test_every_real_thread_the_repository_has_parses_without_error(
    provider: GitHubReviewProvider,
) -> None:
    """The adapter builds a well-formed ``ReviewThread`` from every inline
    review thread the repository actually has.

    This is the real-data fidelity claim, and it is scoped to *what exists*, not
    to *what the constructed tests assume exists*: see the module note and
    ``test_the_sample_documents_the_inline_thread_scarcity`` below. Every thread
    read here must anchor to its event, carry a non-empty comment tuple, and
    have each comment carry an author display name, a body, and an external id.
    A real thread that failed any of these is a live divergence between the
    adapter and GitHub's shape and fails here with the field named.
    """
    events = await _some_events(provider, limit=40)
    threads = await _threads_across(provider, events, cap=40)
    if not threads:
        pytest.skip("no inline review threads across the sampled PRs")
    for thread in threads:
        assert thread.event_key, "a thread parsed with no event anchor"
        assert thread.comments, f"thread {thread.external_id} parsed with an empty comment tuple"
        # Resolution is nullable by design; both states are valid parses. The
        # claim is that whichever GitHub sent round-tripped, not that both occur.
        for comment in thread.comments:
            assert isinstance(comment, ReviewComment)
            assert comment.author.display_name, "a comment parsed with no author name"
            assert comment.body, "a comment parsed with an empty body"
            assert comment.external_id, "a comment parsed with no external id"
            # Whatever length GitHub returned is the length the domain holds --
            # the adapter bounds its own echoed strings, never the ingested body.
            assert comment.body == comment.body.encode("utf-8").decode("utf-8"), (
                "a comment body did not survive a UTF-8 round trip (transcoding)"
            )


#: The known-positive review corpus recorded in ADR-0030 (lines 1122-1146),
#: measured by adversarial review and re-run by the orchestrator: the pull
#: requests that carry inline review threads, and how many each had. The read
#: path must reach these when pointed at them -- a positive control that any "no
#: rich threads" observation elsewhere is a property of the window, not a broken
#: read.
#:
#: These four were long merged when the ADR measured them, so their inline-thread
#: counts are frozen and asserted exactly.
_RECORDED_THREADS_FROZEN: Final[dict[int, int]] = {12: 2, 132: 1, 224: 2, 352: 5}
#: #569 was still OPEN at the ADR measurement (its one recorded thread was dated
#: 2026-09-05, the still-open note in the ADR). A count taken against a mutable,
#: still-open pull request is a FLOOR, not a pin -- it has since merged with two
#: bot threads (measured 2026-09-07, both `github-advanced-security`, monotone
#: growth, no shape change). So it is asserted `>=` its recorded one, and the
#: drift is a stale-ADR-count to correct, not an adapter divergence: a count
#: anchored to a resource that could still grow is a floor.
_RECORDED_OPEN_AT_MEASUREMENT: Final[dict[int, int]] = {569: 1}


def _event_for(number: int) -> ReviewEvent:
    """A minimal ``ReviewEvent`` for one known pull request.

    ``get_threads`` reads only ``event.repository`` and ``event.number`` and
    re-checks the allowlist itself (its docstring: a ``ReviewEvent`` is an
    ordinary value a caller can build), so the other fields are placeholders the
    thread read never consults.
    """
    return ReviewEvent(
        project_id=_PROJECT,
        provider="github",
        repository=_REPOSITORY,
        number=number,
        title=f"pull request #{number}",
        author=ReviewParticipant(provider="github", external_id="x", display_name="x"),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        url=f"https://github.com/{_REPOSITORY}/pull/{number}",
        head_commit="0" * 40,
        base_commit="0" * 40,
    )


async def test_the_recorded_thread_corpus_is_read_at_the_counts_the_adr_pins(
    provider: GitHubReviewProvider,
) -> None:
    """The read path hits ADR-0030's enumerated positive control at its counts.

    Theurian's inline review threads are thin and monotone -- almost none appear
    in a recent-window sample, so an "absence of rich threads" observation on the
    wrong window would pass vacuously. This targets the recorded PRs directly and
    asserts each yields the thread count the ADR pins, proving the read path
    demonstrably reaches the planted positives. Every root comment in this corpus
    is the security bot's, so this also exercises the resolved-thread and
    multi-comment shapes (#12 and #224 carry two, #352 five) that the recent
    window does not surface.
    """

    async def _read(number: int) -> int:
        threads = await _threads_of(provider, _event_for(number))
        for thread in threads:
            assert thread.event_key
            assert thread.comments  # every recorded thread has at least one comment
            for comment in thread.comments:
                assert comment.author.display_name
                assert comment.body
                assert comment.body == comment.body.encode("utf-8").decode("utf-8")
        return len(threads)

    frozen = {number: await _read(number) for number in _RECORDED_THREADS_FROZEN}
    assert frozen == _RECORDED_THREADS_FROZEN, (
        "the read path did not reach ADR-0030's frozen thread counts; "
        f"expected {_RECORDED_THREADS_FROZEN}, read {frozen}. These PRs were merged "
        "before the ADR measured them, so a divergence is a real adapter/API-shape "
        "regression, not corpus drift."
    )
    for number, floor in _RECORDED_OPEN_AT_MEASUREMENT.items():
        count = await _read(number)
        assert count >= floor, (
            f"#{number} read {count} threads, below its recorded floor {floor}; "
            "a still-open PR's count can only grow, so a drop is a real regression"
        )


async def test_pull_request_352_carries_five_resolved_threads_with_no_timestamp(
    provider: GitHubReviewProvider,
) -> None:
    """#352's five threads are resolved, and the resolution carries no timestamp.

    This is the resolved-with-no-timestamp nullable arm, not an unresolved one:
    measured 2026-09-07, #352's five threads are all ``isResolved: true``. The
    adapter records that state as a ``ReviewResolution(state=RESOLVED)`` whose
    ``resolved_at`` is structurally ``None`` -- GitHub's ``PullRequestReviewThread``
    carries no resolution timestamp (ADR-0030 decision 5), so the domain records
    the resolution as a fact and the timestamp as the absence it is. Asserting
    the *state* (not ``resolved_at``, which is invariant and so cannot fail) is
    what makes this a live positive control: if #352's threads were unresolved
    tomorrow, ``resolution`` would be ``None`` and this fails.
    """
    threads = await _threads_of(provider, _event_for(352))
    assert len(threads) == 5, f"#352 recorded as five threads; read {len(threads)}"
    for thread in threads:
        assert thread.resolution is not None, (
            f"#352's threads were recorded resolved; thread {thread.external_id} "
            "read as unresolved (resolution is None)"
        )
        assert thread.resolution.state is ReviewThreadState.RESOLVED
        # The timestamp is structurally absent for this provider object; recorded
        # here as the documented shape, not asserted as a falsifiable dimension.
        assert thread.resolution.resolved_at is None
