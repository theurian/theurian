"""``theurian review ingest`` through the real CLI (ADR-0030 decisions 1--4).

Drives the shipped Typer app against a hermetic git repository under
``tmp_path``. Two provider arrangements, and each answers a different question:

* **A canned provider**, installed by monkeypatching the name
  ``cli/review_commands.py`` constructs, for everything about *landing*: exit
  codes, the published document, what reaches disk, and the retryability
  sequence. Nothing is spawned and nothing reaches the network.
* **The real** :class:`GitHubReviewProvider`, for the two refusals that happen
  **before any process exists** -- a repository the allowlist does not name, and
  a ``limit`` outside the recorded bound. Those are the ones a stand-in could
  not honestly stand in for: the claim is that no child is started, and only the
  real adapter can be said to have started none.

Every write goes under ``tmp_path``; the repository's own ``.theurian/`` is
never touched.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import pytest
from fakes import CannedReviewProvider, ReadKey
from typer.testing import CliRunner

from theurian.application.review_landing_gate import REDACTED_DISPLAY_NAME
from theurian.cli import review_commands
from theurian.cli.commands import EXIT_STATE_ERROR
from theurian.cli.main import app
from theurian.domain.enums import ReviewThreadState
from theurian.domain.identifiers import ProjectId
from theurian.domain.review import (
    ReviewComment,
    ReviewEvent,
    ReviewParticipant,
    ReviewSubmission,
    ReviewThread,
)
from theurian.domain.review_ingest import RefusalGrade, ReviewIngestRefusedError
from theurian.infrastructure.github.limits import MAX_PULL_REQUESTS

pytestmark = pytest.mark.integration

runner = CliRunner()

PROJECT: Final = ProjectId("demo")
PROVIDER: Final = "github"
REPOSITORY: Final = "acme/order-service"

#: AWS's own published example key, which this repository's detector reports as
#: ``aws-access-key-id``. An input a person can recognise, rather than an
#: expectation computed from the detector under test.
SECRET: Final = "AKIAIOSFODNN7EXAMPLE"  # noqa: S105 - AWS's published example key, not a credential

#: U+202E, spelled by code point because a literal one is invisible in a diff and
#: reorders every line it sits on. Six characters under ``repr``, so it crosses
#: the bound-then-quote trip threshold rather than sitting under it.
RIGHT_TO_LEFT_OVERRIDE: Final = "\u202e"

_NEEDS_SYMLINKS = pytest.mark.skipif(
    sys.platform == "win32", reason="symlinks need privileges on Windows"
)


# -- canned domain values -----------------------------------------------------


def _participant(name: str = "Reviewer One") -> ReviewParticipant:
    return ReviewParticipant(provider=PROVIDER, external_id="U_kwDO1", display_name=name)


def _event(number: int = 42) -> ReviewEvent:
    return ReviewEvent(
        project_id=PROJECT,
        provider=PROVIDER,
        repository=REPOSITORY,
        number=number,
        title="Bound the retry budget",
        body="The retry loop is now bounded.",
        author=_participant(),
        created_at=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
        url=f"https://github.com/{REPOSITORY}/pull/{number}",
        head_commit="b" * 40,
        base_commit="c" * 40,
        head_ref_name="fix/retry-budget",
        labels=("security",),
        milestone="0.2.0",
    )


def _submission(event: ReviewEvent) -> ReviewSubmission:
    return ReviewSubmission(
        external_id=f"PRR_kwDO{event.number}",
        project_id=PROJECT,
        event_key=event.external_key,
        author=_participant("Reviewer Two"),
        body="Approving; the budget is bounded now.",
        state="APPROVED",
        submitted_at=datetime(2026, 8, 1, 15, 0, tzinfo=UTC),
    )


def _thread(event: ReviewEvent, **overrides: object) -> ReviewThread:
    thread = ReviewThread(
        external_id=f"PRRT_kwDO{event.number}",
        project_id=PROJECT,
        event_key=event.external_key,
        file_path="src/order.py",
        comments=(
            ReviewComment(
                external_id=f"IC_kwDO{event.number}",
                author=_participant(),
                body="This retries forever.",
                created_at=datetime(2026, 8, 1, 13, 0, tzinfo=UTC),
            ),
        ),
        state=ReviewThreadState.OPEN,
        line_end=12,
    )
    return replace(thread, **overrides)  # type: ignore[arg-type]


def _canned(
    events: tuple[ReviewEvent, ...] = (),
    *,
    threads: dict[int, tuple[ReviewThread, ...]] | None = None,
    refusals: dict[ReadKey, ReviewIngestRefusedError] | None = None,
) -> CannedReviewProvider:
    given = threads or {}
    return CannedReviewProvider(
        events,
        threads={event.number: given.get(event.number, (_thread(event),)) for event in events},
        submissions={event.number: (_submission(event),) for event in events},
        refusals=refusals,
    )


def _over_cap(number: int) -> ReviewIngestRefusedError:
    return ReviewIngestRefusedError(
        RefusalGrade.LIMIT_EXCEEDED,
        f"Review thread on {REPOSITORY}#{number} carries more than the recorded cap.",
    )


# -- the project and the invocation -------------------------------------------


def _git(root: Path, *args: str) -> None:
    """One git command under a configuration this fixture, not the developer, owns."""
    subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607 - git resolved via PATH, args are test-controlled
        cwd=root,
        check=True,
        capture_output=True,
        env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull},
    )


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A git working tree with a `.theurian/` the command can resolve.

    No ``theurian init``: the command needs a resolvable project, not an
    initialised one, and writing the managed ``.gitignore`` block here would put
    ``.theurian/review`` nowhere near the question these tests ask.
    """
    root = tmp_path / "demo"
    root.mkdir()
    _git(root, "init", "-b", "main")
    (root / ".theurian").mkdir()
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(tmp_path / "datadir"))
    monkeypatch.chdir(root)
    yield root


def _settings(root: Path, *, policy: str | None = None, allowlisted: bool = True) -> None:
    """Write the project's configuration, naming only what a test needs."""
    lines = ["apiVersion: theurian.dev/v1"]
    if policy is not None:
        lines += ["security:", f'  secretScan: "{policy}"']
    if allowlisted:
        lines += ["providers:", "  review:", "    repositories:", f"      - {REPOSITORY}"]
    (root / ".theurian" / "config.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _install(monkeypatch: pytest.MonkeyPatch, provider: CannedReviewProvider) -> None:
    """Put ``provider`` where the composition root constructs the real adapter."""
    monkeypatch.setattr(review_commands, "GitHubReviewProvider", lambda **_kwargs: provider)


def _invoke(*args: str) -> tuple[int, dict[str, Any]]:
    result = runner.invoke(app, [*args, "--json"], catch_exceptions=False)
    stream = result.stdout or result.stderr or ""
    return result.exit_code, json.loads(stream) if stream.strip() else {}


def _landed(root: Path) -> set[str]:
    review = root / ".theurian" / "review"
    if not review.exists():
        return set()
    return {str(path.relative_to(review)) for path in review.rglob("*.json")}


# -- a clean run --------------------------------------------------------------


def test_a_clean_run_reports_counts_and_exits_zero(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The published document: counts and identities, and nothing that was fetched."""
    _settings(project)
    _install(monkeypatch, _canned((_event(42),)))

    code, payload = _invoke("review", "ingest", REPOSITORY)

    assert code == 0
    assert payload["clean"] is True
    assert payload["repository"] == REPOSITORY
    assert payload["secretScanPolicy"] == "block"
    assert payload["participantNamesRedacted"] is False
    assert payload["landed"] == {
        "pullRequests": 1,
        "reviewSubmissions": 1,
        "reviewThreads": 1,
        "total": 3,
    }
    assert (payload["new"], payload["updated"], payload["kept"]) == (3, 0, 0)
    assert payload["refused"] == []
    assert payload["findings"] == []
    assert payload["skipped"] == []
    assert len(_landed(project)) == 3

    # No evidence content is served. The bodies and titles the provider answered
    # with are on disk; none of them is in what the command published.
    document = json.dumps(payload)
    for fetched in ("The retry loop is now bounded", "This retries forever", "Reviewer One"):
        assert fetched not in document, f"the report published fetched content: {fetched!r}"


def test_a_second_invocation_updates_rather_than_adds(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refetch is a refresh, and the counts say which it was."""
    _settings(project)
    _install(monkeypatch, _canned((_event(42),)))

    first_code, first = _invoke("review", "ingest", REPOSITORY)
    second_code, second = _invoke("review", "ingest", REPOSITORY)

    assert (first_code, second_code) == (0, 0)
    assert (first["new"], first["updated"]) == (3, 0)
    assert (second["new"], second["updated"], second["kept"]) == (0, 3, 0)
    assert len(_landed(project)) == 3


# -- scope-matched containment, through the CLI -------------------------------


def test_a_skipped_pull_request_exits_one_and_is_named_by_identity(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-1 through the CLI: the neighbour lands, the run is not clean, exit 1."""
    _settings(project)
    _install(
        monkeypatch,
        _canned((_event(42), _event(41)), refusals={("get_threads", 42): _over_cap(42)}),
    )

    code, payload = _invoke("review", "ingest", REPOSITORY)

    assert code == 1
    assert payload["clean"] is False
    assert payload["landed"]["total"] == 3
    assert len(payload["skipped"]) == 1
    assert "#42" in payload["skipped"][0]
    assert "limit-exceeded" in payload["skipped"][0]
    assert not any("42" in name for name in _landed(project))


def test_a_repository_the_allowlist_does_not_name_is_refused_before_any_process(
    project: Path,
) -> None:
    """The real adapter, and the claim is that no child was started.

    No provider is installed here: ``GitHubReviewProvider`` is the one under
    test, and ``security/review_allowlist.py`` refuses before ``_ready`` is
    reached, so no ``gh`` is located, spawned or probed. The evidence directory
    is not created either -- a refusal at the repository scope writes nothing.
    """
    _settings(project, allowlisted=False)

    code, payload = _invoke("review", "ingest", REPOSITORY)

    assert code == 1
    assert "providers.review.repositories" in payload["remedy"]
    assert "gh repo view" in payload["remedy"]
    assert _landed(project) == set()


def test_a_limit_outside_the_recorded_bound_is_refused_with_a_cure(project: Path) -> None:
    """The adapter's graded bound, reachable from an operator surface at last.

    ``--limit`` carries no Typer ``min``/``max`` on purpose: Click's usage error
    would exit 2 with prose nobody wrote, while the adapter's own refusal names
    the bound and the cure. This is the case that says the refusal is reachable.
    """
    _settings(project)

    code, payload = _invoke("review", "ingest", REPOSITORY, "--limit", str(MAX_PULL_REQUESTS + 1))

    assert code == 1
    assert str(MAX_PULL_REQUESTS) in payload["error"]
    assert "`limit`" in payload["remedy"]
    assert _landed(project) == set()


# -- the gate's three policies, through the CLI -------------------------------


def test_block_exits_one_and_the_flagged_unit_is_absent_on_disk(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-4, ``block``: the flagged record never becomes a file and exit is 1."""
    _settings(project, policy="block")
    _install(
        monkeypatch,
        _canned((_event(42),), threads={42: (_thread(_event(42), file_path=f"src/{SECRET}.py"),)}),
    )

    code, payload = _invoke("review", "ingest", REPOSITORY)

    assert code == 1
    assert payload["clean"] is False
    assert payload["landed"]["reviewThreads"] == 0
    assert payload["landed"]["pullRequests"] == 1
    assert payload["refused"] == ["'acme/order-service'#42 record 'PRRT_kwDO42'"]
    assert len(payload["findings"]) == 1
    assert not any("PRRT" in name for name in _landed(project))


def test_warn_exits_zero_and_still_reports_the_finding(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-4, ``warn``: landed, reported, exit 0 -- the project's recorded choice."""
    _settings(project, policy="warn")
    _install(
        monkeypatch,
        _canned((_event(42),), threads={42: (_thread(_event(42), file_path=f"src/{SECRET}.py"),)}),
    )

    code, payload = _invoke("review", "ingest", REPOSITORY)

    assert code == 0
    assert payload["clean"] is True
    assert payload["secretScanPolicy"] == "warn"
    assert payload["landed"]["reviewThreads"] == 1
    assert payload["refused"] == []
    assert len(payload["findings"]) == 1
    assert len(_landed(project)) == 3


def test_off_scans_nothing_and_lands_everything(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-4, ``off``: no findings at all, and the same record lands."""
    _settings(project, policy="off")
    _install(
        monkeypatch,
        _canned((_event(42),), threads={42: (_thread(_event(42), file_path=f"src/{SECRET}.py"),)}),
    )

    code, payload = _invoke("review", "ingest", REPOSITORY)

    assert code == 0
    assert payload["secretScanPolicy"] == "off"
    assert payload["findings"] == []
    assert len(_landed(project)) == 3


def test_the_report_never_quotes_more_of_the_match_than_the_type_allows(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The published finding carries the four-character prefix and no more.

    The positive control is the second assertion: the finding has to *be* there,
    or "the secret is absent" would hold for a run that found nothing.
    """
    _settings(project, policy="warn")
    _install(
        monkeypatch,
        _canned((_event(42),), threads={42: (_thread(_event(42), file_path=f"src/{SECRET}.py"),)}),
    )

    _code, payload = _invoke("review", "ingest", REPOSITORY)

    (finding,) = payload["findings"]
    assert "aws-access-key-id" in finding
    assert SECRET not in json.dumps(payload)
    assert SECRET[:5] not in finding, f"the report quotes five characters of the match: {finding!r}"


# -- retryability, through the CLI --------------------------------------------


def test_a_skipped_record_lands_on_the_next_invocation_of_the_same_command(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-3 through the CLI: same argv twice, no marker anywhere between them.

    The second invocation names the same window with the same options. Nothing
    is reset and nothing is carried forward, so a marker that had counted the
    skipped pull request as seen would leave the second run with nothing to land
    and this would go RED.
    """
    _settings(project)
    _install(monkeypatch, _canned((_event(42),), refusals={("get_threads", 42): _over_cap(42)}))

    first_code, first = _invoke("review", "ingest", REPOSITORY)
    assert first_code == 1
    assert first["landed"]["total"] == 0
    assert _landed(project) == set()

    _install(monkeypatch, _canned((_event(42),)))
    second_code, second = _invoke("review", "ingest", REPOSITORY)

    assert second_code == 0
    assert second["clean"] is True
    assert second["landed"]["total"] == 3
    assert len(_landed(project)) == 3


# -- the containment and context refusals -------------------------------------


@_NEEDS_SYMLINKS
def test_an_escaping_review_directory_is_refused_before_anything_is_fetched(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``.theurian/review`` delivered as a link out of the tree exits 4 with a cure.

    The provider is installed and then asserted **unused**: the composition root
    resolves the evidence directory before it constructs an adapter, so a clone
    carrying this link is refused with nothing fetched.
    """
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (project / ".theurian" / "review").symlink_to(outside, target_is_directory=True)
    _settings(project)
    provider = _canned((_event(42),))
    _install(monkeypatch, provider)

    code, payload = _invoke("review", "ingest", REPOSITORY)

    assert code == EXIT_STATE_ERROR
    assert "review" in payload["error"]
    assert payload["remedy"]
    assert provider.reads == []
    assert list(outside.iterdir()) == []


def test_an_unloadable_migration_is_reported_as_a_document(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#205's obligation for a new ``_require_project`` caller.

    Every command reaching ``resolve_context`` inherits its migration loading, so
    a migration that will not load must arrive as ``{error, remedy}`` rather than
    as a traceback. ``test_resolve_context_call_sites.py`` names this test as the
    discharge for this command's entry in its enumeration.
    """
    migrations = project / ".theurian" / "migrations"
    migrations.mkdir()
    (migrations / "0001-broken.yaml").write_text(": not : valid : yaml :\n", encoding="utf-8")
    _settings(project)
    _install(monkeypatch, _canned((_event(42),)))

    code, payload = _invoke("review", "ingest", REPOSITORY)

    assert code != 0
    assert payload["error"]
    assert payload["remedy"]
    assert _landed(project) == set()


# -- the text surface ---------------------------------------------------------


def test_the_text_report_escapes_a_control_character_a_provider_chose(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A node id carrying U+202E reaches the terminal escaped, not raw.

    Two sinks stand between the provider and the terminal and this drives both:
    ``ReviewRecordIdentity.describe`` quotes the value, and ``_render`` escapes
    what it prints. The id is the provider's own string, which is the channel a
    stand-in has to supply because no test input reaches it from the caller.
    """
    _settings(project, policy="block")
    hostile = _thread(_event(42), file_path=f"src/{SECRET}.py")
    hostile = replace(hostile, external_id=f"PRRT{RIGHT_TO_LEFT_OVERRIDE}42")
    _install(monkeypatch, _canned((_event(42),), threads={42: (hostile,)}))

    result = runner.invoke(app, ["review", "ingest", REPOSITORY], catch_exceptions=False)

    assert result.exit_code == 1
    assert RIGHT_TO_LEFT_OVERRIDE not in result.stdout, (
        "a control character a provider chose reached the terminal unescaped"
    )
    assert "PRRT" in result.stdout, "the refused record must still be named"


def test_the_help_says_the_evidence_is_source_and_whose_decision_committing_it_is(
    project: Path,
) -> None:
    """The operator who reads only ``--help`` is the one who would delete it.

    ``.theurian/review/`` is not in the managed ``.gitignore`` block and no
    refetch recovers a deleted upstream comment, so both halves are stated where
    that operator will actually look.
    """
    result = runner.invoke(app, ["review", "ingest", "--help"], catch_exceptions=False)

    assert result.exit_code == 0
    collapsed = " ".join(result.stdout.split())
    assert "source" in collapsed and "not a cache" in collapsed
    assert "data loss rather than a rebuild" in collapsed
    assert "the project's decision" in collapsed


def test_a_redacting_project_lands_the_placeholder_and_says_so(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-12 end to end: the report says redaction ran and the files carry it."""
    (project / ".theurian" / "config.yaml").write_text(
        "apiVersion: theurian.dev/v1\n"
        "providers:\n"
        "  review:\n"
        "    repositories:\n"
        f"      - {REPOSITORY}\n"
        "    redactParticipantNames: true\n",
        encoding="utf-8",
    )
    _install(monkeypatch, _canned((_event(42),)))

    code, payload = _invoke("review", "ingest", REPOSITORY)

    assert code == 0
    assert payload["participantNamesRedacted"] is True
    landed_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((project / ".theurian" / "review").rglob("*.json"))
    )
    assert REDACTED_DISPLAY_NAME in landed_text
    assert "Reviewer One" not in landed_text
    assert "Reviewer Two" not in landed_text
    assert "U_kwDO1" in landed_text
