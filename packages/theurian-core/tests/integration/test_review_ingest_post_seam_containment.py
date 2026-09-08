"""One observable, driven through the shipped CLI: a run always publishes a document.

``application/review_ingest_service.py`` states the claim this file exists to
hold, and states it over an **observable** rather than over an exception family::

    Whatever the provider answers with, a run ends with one of the two documents
    ``theurian review ingest`` publishes -- the run document, or
    ``{error, remedy}`` -- and never with a traceback.

Round two's R2-A is why the distinction is written down. The two record-scope
seams catch ``ReviewIngestRefusedError``, and a claim about that *family* says
nothing about a stage that raises outside it: a lone surrogate -- which
``json.loads`` decodes out of a wire-legal ``\\ud800`` and UTF-8 cannot encode --
reached ``layout._hashed`` and ``store._document`` as a bare
``UnicodeEncodeError``, so the command published **no document at all** while
records before it had already landed.

So every row here asserts the observable, at the CLI, and never that a
particular exception type is raised: the type is the mechanism and the mechanism
is allowed to change. The hostile value is planted at each of the three
post-seam stages the service's docstring tabulates, and the partial-landing
sentence is checked in the same breath, because a refusal that says "nothing was
written" over two landed files is a false statement to an operator whose
evidence has no rebuild.

The provider is canned for the landing rows -- what is under test is what the
*store* does with a value, not how the adapter obtained it -- and the adapter's
own half of the same claim is
``tests/integration/test_gh_review_provider.py::test_a_pull_request_whose_url_cannot_be_read_is_skipped_by_number``,
driven against a real spawn where it belongs.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import pytest
from fakes import CannedReviewProvider
from typer.testing import CliRunner

from theurian.cli import review_commands
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

pytestmark = pytest.mark.integration

runner = CliRunner()

PROJECT: Final = ProjectId("demo")
PROVIDER: Final = "github"
REPOSITORY: Final = "acme/order-service"

#: Exactly what ``json.loads(r'"\\ud800"')`` returns: a lone surrogate, which is
#: legal in a JSON document and has no UTF-8 encoding.
#:
#: **``chr`` rather than the escape literal ``"\\ud800"``, and the reason is the
#: bug this file is about, met one tool over.** Written as a literal, mypy infers
#: a ``Literal["\\ud800"]`` for the ``Final`` and then encodes that type into its
#: own cache -- which is a UTF-8 write of a lone surrogate, and crashes the whole
#: run with ``INTERNAL ERROR ... UnicodeEncodeError`` rather than a type error.
#: ``chr`` produces the identical value at run time and types as ``str``.
#:
#: :func:`test_the_wire_really_produces_this_code_point` is what ties the value
#: back to the wire -- without it every row below could be testing an input
#: nothing can send.
LONE_SURROGATE: Final = chr(0xD800)


def _participant() -> ReviewParticipant:
    return ReviewParticipant(provider=PROVIDER, external_id="U_kwDO1", display_name="Reviewer One")


def _event() -> ReviewEvent:
    return ReviewEvent(
        project_id=PROJECT,
        provider=PROVIDER,
        repository=REPOSITORY,
        number=42,
        title="Bound the retry budget",
        body="The retry loop is now bounded.",
        author=_participant(),
        created_at=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
        url=f"https://github.com/{REPOSITORY}/pull/42",
        head_commit="b" * 40,
        base_commit="c" * 40,
        head_ref_name="fix/retry-budget",
        labels=("security",),
    )


def _submission(event: ReviewEvent) -> ReviewSubmission:
    return ReviewSubmission(
        external_id="PRR_kwDO42",
        project_id=PROJECT,
        event_key=event.external_key,
        author=_participant(),
        body="Approving.",
        state="APPROVED",
        submitted_at=datetime(2026, 8, 1, 15, 0, tzinfo=UTC),
    )


def _thread(
    event: ReviewEvent,
    *,
    external_id: str = "PRRT_kwDO42",
    file_path: str = "src/order.py",
    body: str = "This retries forever.",
) -> ReviewThread:
    """One thread, with each of the three fields a row below poisons overridable.

    Spelled as three named parameters rather than ``**overrides``: the three are
    the whole population this file plants into, and a keyword bag would type as
    ``object`` and let a misspelt field name pass as a thread nobody poisoned.
    """
    return ReviewThread(
        external_id=external_id,
        project_id=PROJECT,
        event_key=event.external_key,
        file_path=file_path,
        comments=(
            ReviewComment(
                external_id="IC_kwDO42",
                author=_participant(),
                body=body,
                created_at=datetime(2026, 8, 1, 13, 0, tzinfo=UTC),
            ),
        ),
        state=ReviewThreadState.OPEN,
    )


def _git(root: Path, *args: str) -> None:
    subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607 - git resolved via PATH, args are test-controlled
        cwd=root,
        check=True,
        capture_output=True,
        env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull},
    )


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A git working tree with a resolvable ``.theurian/`` and the repository listed."""
    root = tmp_path / "demo"
    root.mkdir()
    _git(root, "init", "-b", "main")
    (root / ".theurian").mkdir()
    (root / ".theurian" / "config.yaml").write_text(
        "apiVersion: theurian.dev/v1\n"
        "providers:\n  review:\n    repositories:\n"
        f"      - {REPOSITORY}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(tmp_path / "datadir"))
    monkeypatch.chdir(root)
    yield root


def _run(monkeypatch: pytest.MonkeyPatch, provider: CannedReviewProvider) -> tuple[int, str, str]:
    """One ``review ingest --json``, as exit code, stdout and stderr.

    Both streams are returned raw rather than parsed, because *which* stream
    carries the document is part of the contract and a helper that guessed would
    hide a run that published on neither.
    """
    monkeypatch.setattr(review_commands, "GitHubReviewProvider", lambda **_kwargs: provider)
    result = runner.invoke(app, ["review", "ingest", REPOSITORY, "--json"], catch_exceptions=True)
    return result.exit_code, result.stdout, result.stderr


def _the_published_document(exit_code: int, out: str, err: str) -> dict[str, Any]:
    """The one document the run published, or a failure naming what it published instead.

    This is the observable itself, so the assertion lives here rather than in
    each row: a traceback, an empty run, or output that is not JSON are the three
    ways the claim breaks, and each gets its own sentence.
    """
    stream = out if out.strip().startswith("{") else err
    assert "Traceback (most recent call last)" not in (out + err), (
        f"the run published a traceback rather than a document (exit {exit_code}):\n"
        f"--- stdout ---\n{out}\n--- stderr ---\n{err}"
    )
    assert stream.strip(), (
        f"the run published nothing on either channel (exit {exit_code}). The help "
        f"promises a document at exit 1 as well as at exit 0."
    )
    parsed = json.loads(stream)
    assert isinstance(parsed, dict)
    document: dict[str, Any] = parsed
    return document


def test_the_wire_really_produces_this_code_point() -> None:
    """The premise every row below rests on, measured rather than assumed.

    A hostile-input test is only worth its input. ``\\ud800`` inside a JSON
    string is legal on the wire and ``json.loads`` decodes it into a lone
    surrogate, which is a ``str`` Python holds happily and UTF-8 declines to
    encode. If that stopped being true, every row here would be driving a value
    no provider can send and the guards would be untested.
    """
    decoded = json.loads(r'{"id": "PRRT_kwDO\ud800"}')["id"]

    assert decoded == f"PRRT_kwDO{LONE_SURROGATE}"
    with pytest.raises(UnicodeEncodeError):
        decoded.encode("utf-8")


@pytest.mark.parametrize(
    "where",
    ["thread id", "comment body", "thread file path"],
    ids=["record-key", "comment-body", "file-path"],
)
def test_a_surrogate_anywhere_in_a_record_is_a_graded_refusal_not_a_traceback(
    where: str, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RED means the landing seam can end a run with no document at all.

    Three positions, because the two mechanisms behind them are different and a
    guard keyed on either alone passes the other: the thread **id** becomes the
    record key and detonates in ``layout._hashed`` while the path is still being
    derived, and a **body** or a **file path** detonates a stage later in
    ``store._document``'s ``.encode``. The store keys its guard on the complement
    of ``TheurianError`` rather than on those two sites, which is what makes the
    third position pass without a third branch.

    ``catch_exceptions=True`` on the runner is deliberate: with it False the
    escape would surface as a raised exception in this test, which reads as a
    test error rather than as the product's own failure. The claim is about what
    an operator's terminal receives.
    """
    event = _event()
    if where == "thread id":
        poisoned = _thread(event, external_id=f"PRRT_kwDO{LONE_SURROGATE}")
    elif where == "comment body":
        poisoned = _thread(event, body=f"a body carrying {LONE_SURROGATE}")
    else:
        poisoned = _thread(event, file_path=f"src/{LONE_SURROGATE}.py")
    provider = CannedReviewProvider(
        (event,),
        threads={42: (poisoned,)},
        submissions={42: (_submission(event),)},
    )

    exit_code, out, err = _run(monkeypatch, provider)
    document = _the_published_document(exit_code, out, err)

    assert exit_code == 1, f"a record that could not be written exited {exit_code}"
    assert set(document) == {"error", "remedy"}, (
        f"the refusal published the run document rather than `{{error, remedy}}`: "
        f"{sorted(document)}"
    )
    assert "review-thread" in document["error"], (
        f"the refusal does not say which record it was about: {document['error']}"
    )
    assert "gh api graphql" in document["remedy"], (
        f"the remedy names no command a reader can run: {document['remedy']}"
    )


def test_a_landing_refusal_says_what_already_landed_rather_than_nothing_was_written(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RED means an operator is told a rollback happened that did not.

    ``ReviewEvidenceStore.write`` is atomic **per record** -- ``os.replace``
    publishes each one whole or not at all -- and not across a run. The pull
    request and its review submission are written before the poisoned thread is
    reached, and they stay on disk. A refusal that said "nothing was written"
    would send an operator looking for a rollback nothing performed, over an
    artefact whose loss no refetch recovers.

    The count is read out of the sentence and compared against the **files on
    disk**, so an off-by-one between what the store counted and what it landed is
    a red test rather than a plausible-looking number.
    """
    event = _event()
    provider = CannedReviewProvider(
        (event,),
        threads={42: (_thread(event, external_id=f"PRRT_kwDO{LONE_SURROGATE}"),)},
        submissions={42: (_submission(event),)},
    )

    exit_code, out, err = _run(monkeypatch, provider)
    document = _the_published_document(exit_code, out, err)
    landed = sorted(path.name for path in (project / ".theurian" / "review").rglob("*.json"))

    assert len(landed) == 2, f"the records before the refused one did not land: {landed}"
    assert "This record was not written" in document["error"], (
        f"the refusal does not say that this record is the one that was lost: {document['error']}"
    )
    assert f"the {len(landed)} record(s) this run wrote before it" in document["error"], (
        f"the refusal does not name the {len(landed)} records that did land: {document['error']}"
    )


def test_a_refusal_over_an_unencodable_record_is_itself_publishable(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RED means the refusal path raises the very error it was written to grade.

    The non-JSON branch of ``cli.commands._fail`` writes its message to a UTF-8
    stderr, and the value that reaches the refusal *is* the one nothing can
    encode. Echoing it would raise a second ``UnicodeEncodeError`` out of the one
    path that may not raise; ``bounded_quote``'s ``repr`` escapes exactly the
    range UTF-8 declines, which is why the store quotes rather than echoes here.

    Driven without ``--json`` on purpose: the JSON branch escapes to ASCII on its
    own, so it would pass whichever renderer the store used and prove nothing.
    """
    event = _event()
    provider = CannedReviewProvider(
        (event,),
        threads={42: (_thread(event, external_id=f"PRRT_kwDO{LONE_SURROGATE}"),)},
        submissions={42: (_submission(event),)},
    )
    monkeypatch.setattr(review_commands, "GitHubReviewProvider", lambda **_kwargs: provider)

    result = runner.invoke(app, ["review", "ingest", REPOSITORY], catch_exceptions=True)
    published = result.stdout + result.stderr

    assert "Traceback (most recent call last)" not in published, (
        f"the human-readable branch published a traceback:\n{published}"
    )
    assert result.exit_code == 1
    assert "\\ud800" in published, (
        f"the refusal names the record without the escaped code point, so either the "
        f"identity is missing or it was echoed raw:\n{published}"
    )
