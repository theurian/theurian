"""``theurian propose`` and ``theurian propose accept``, invoked in-process.

The service tests own the packaging rules. These own the adapter: that the
option surface reaches them, that a refusal arrives as JSON with a remedy rather
than as a traceback, and -- the one thing only an end-to-end run can say -- that
a proposal drafted here, accepted here, and then handed to the *existing*
migration commands actually applies.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from theurian.cli.main import app

pytestmark = pytest.mark.integration

runner = CliRunner()

EXIT_INVALID_INPUT = 2
EXIT_STATE_ERROR = 4

BODY = "# Retry policy\n\nThree attempts, then fail loudly.\n"

REASONING = "The review thread on #41 settled the retry budget at three attempts."

#: Everything but the two options a test varies: the body and the reasoning.
DRAFT: tuple[str, ...] = (
    "propose",
    "--item-id",
    "architecture.retry-policy",
    "--title",
    "Retry policy",
    "--kind",
    "architecture",
    "--owner",
    "platform-team",
    "--author",
    "platform-team@example.com",
    "--description",
    "Record the retry budget the API review settled on.",
    "--source-uri",
    "https://github.com/acme/api/commit/0123456789abcdef",
    "--source-commit",
    "0123456789abcdef",
    "--agent-id",
    "claude-code",
    "--task-id",
    "task-7",
    "--model",
    "claude-opus-5",
)


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    root = tmp_path / "demo"
    root.mkdir()
    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603

    monkeypatch.setenv("THEURIAN_DATA_DIR", str(tmp_path / "datadir"))
    monkeypatch.chdir(root)
    _invoke("init")
    (root / "body.md").write_text(BODY, encoding="utf-8")
    yield root


def _invoke(*args: str) -> tuple[int, dict[str, Any]]:
    result = runner.invoke(app, [*args, "--json"], catch_exceptions=False)
    stream = result.stdout if result.exit_code == 0 else (result.stderr or result.stdout)
    return result.exit_code, json.loads(stream) if stream.strip() else {}


def _draft(root: Path, *extra: str) -> tuple[int, dict[str, Any]]:
    return _invoke(*DRAFT, "--body-file", str(root / "body.md"), "--reasoning", REASONING, *extra)


# -- drafting --------------------------------------------------------------


def test_propose_writes_a_proposal_directory_a_reviewer_can_read(project: Path) -> None:
    code, payload = _draft(project)

    assert code == 0, payload
    directory = project / payload["proposalDirectory"]
    body = f"retry-policy.{payload['revisionId']}.md"
    # The migration and evidence sit at the top; the body mirrors its knowledge
    # sub-path, so it is under `architecture/` rather than flat -- which is what
    # lets accept find two same-leaf bodies apart.
    assert sorted(p.name for p in directory.iterdir()) == [
        payload["migrationFile"],
        "architecture",
        "evidence.json",
    ]
    assert (directory / "architecture" / body).is_file()
    # Written for where the migration will be after acceptance, not for the
    # directory it sits in now -- and named so the move renames nothing.
    assert payload["contentFile"] == f"../knowledge/architecture/{body}"
    assert payload["bodyDestination"] == f".theurian/knowledge/architecture/{body}"
    assert payload["bodyFile"] == f"{payload['proposalDirectory']}/architecture/{body}"
    assert payload["migrationFile"].startswith(payload["migrationId"])


def test_propose_says_that_validation_does_not_prove_applicability(project: Path) -> None:
    """The asymmetry a caller has to be told about, because nothing shows it.

    ``migrate validate`` is schema-only. The invariants ``migrate apply``
    enforces are checked after the pull request has already merged (#36), so a
    next-steps list that stopped at "validate it" would read as a green light.
    """
    _, payload = _draft(project)

    steps = " ".join(payload["nextSteps"]).lower()

    assert "schema" in steps
    assert "merge" in steps or "merges" in steps


def test_propose_refuses_a_proposal_with_no_reasoning(project: Path) -> None:
    """ADR-0013 point 5, arriving as an answer rather than as a traceback."""
    code, payload = _invoke(*DRAFT, "--body-file", str(project / "body.md"), "--reasoning", "   ")

    assert code == EXIT_INVALID_INPUT
    assert "evidence" in payload["error"]
    assert payload["remedy"]


def test_authored_here_drafts_without_a_source_uri_and_labels_the_revision(project: Path) -> None:
    """HIGH-4: --authored-here declares Theurian-origin knowledge, no external source.

    Before the fix it was unreachable: the CLI filled evidence's anchor from the
    same --source-uri as the metadata anchor, so --authored-here without
    --source-uri failed "no evidence" before the label path was reached. The
    service always supported it; only the CLI could not express it.
    """
    code, payload = _invoke(
        "propose",
        "--item-id",
        "convention.local",
        "--title",
        "Local convention",
        "--kind",
        "convention",
        "--owner",
        "team",
        "--author",
        "a@example.com",
        "--description",
        "Record a convention the team settled on with no external source.",
        "--body-file",
        str(project / "body.md"),
        "--authored-here",
        "--agent-id",
        "claude-code",
        "--task-id",
        "task-9",
        "--model",
        "claude-opus-5",
        "--reasoning",
        "The team decided this in the 2026-08 architecture review.",
    )

    assert code == 0, payload
    migration = (project / payload["proposalDirectory"] / payload["migrationFile"]).read_text()
    document = yaml.safe_load(migration)
    upsert = next(op for op in document["operations"] if op["op"] == "upsertRevision")
    assert upsert["metadata"]["labels"] == ["authored-in-theurian"]
    assert "sourceAnchors" not in upsert["metadata"]
    # Accept it, so the whole authored-here path is exercised end to end.
    accept_code, _ = _invoke("propose", "accept", payload["proposalId"])
    assert accept_code == 0


def test_propose_names_every_option_it_is_missing_at_once(project: Path) -> None:
    """One round trip per missing flag is how an agent burns a turn each time."""
    code, payload = _invoke("propose", "--item-id", "architecture.retry-policy")

    assert code == EXIT_INVALID_INPUT
    for option in ("--title", "--owner", "--author", "--body-file", "--reasoning"):
        assert option in payload["error"]
    assert payload["remedy"]


def test_propose_refuses_a_body_file_that_is_not_there(project: Path) -> None:
    """#205's shape: this command's own IO answers rather than unwinding."""
    code, payload = _invoke(
        *DRAFT, "--reasoning", REASONING, "--body-file", str(project / "absent.md")
    )

    assert code == EXIT_INVALID_INPUT
    assert "absent.md" in payload["error"]
    assert payload["remedy"]


def test_propose_refuses_an_unparseable_expected_revision(project: Path) -> None:
    code, payload = _draft(project, "--expected-revision", "not-a-ulid")

    assert code == EXIT_INVALID_INPUT
    assert payload["remedy"]


def test_a_draft_option_handed_to_accept_is_refused_rather_than_ignored(project: Path) -> None:
    """Silently ignoring it would report success for a change nobody made.

    ``--json`` sits before the verb because the refusal comes from the group's
    own parse, and Click scopes a group's options to the group: its own usage
    errors for this invocation print plain text too, whatever follows the verb.
    Emitting JSON here while the neighbouring refusal does not would be the
    inconsistent choice.
    """
    _, drafted = _draft(project)

    result = runner.invoke(
        app,
        ["propose", "--json", "--title", "Retry policy", "accept", drafted["proposalId"]],
        catch_exceptions=False,
    )
    payload = json.loads(result.stderr)

    assert result.exit_code == EXIT_INVALID_INPUT
    assert "--title" in payload["error"]
    assert payload["remedy"]


def test_propose_with_no_arguments_prints_its_help(project: Path) -> None:
    """Exit 2 with help, which is what every other group here already does.

    Click 8.4 raises ``NoArgsIsHelpError`` -- a ``UsageError`` -- so the help
    goes to stderr and the code is 2. Measured on ``theurian migrate`` as well,
    so this is the group convention rather than this command's own choice.
    """
    result = runner.invoke(app, ["propose"], catch_exceptions=False)
    migrate = runner.invoke(app, ["migrate"], catch_exceptions=False)

    assert result.exit_code == migrate.exit_code == 2
    assert "accept" in result.stderr
    assert "--item-id" in result.stderr


# -- acceptance ------------------------------------------------------------


def test_a_drafted_proposal_survives_acceptance_validation_and_apply(project: Path) -> None:
    """The whole flow ADR-0013 §4 describes, run end to end.

    This is the only test that can catch a ``contentFile`` written relative to
    the proposal directory: such a path parses, and then fails to resolve once
    the migration has moved (#205).
    """
    _, drafted = _draft(project)

    code, accepted = _invoke("propose", "accept", drafted["proposalId"])
    assert code == 0, accepted

    assert (project / ".theurian/migrations" / drafted["migrationFile"]).is_file()
    assert (project / drafted["bodyDestination"]).read_text() == BODY

    code, validated = _invoke("migrate", "validate")
    assert code == 0, validated
    assert validated["migrationCount"] == 1

    code, applied = _invoke("migrate", "apply")
    assert code == 0, applied
    assert applied["applied"] == [drafted["migrationId"]]


def test_two_proposals_for_one_item_both_land_and_both_apply(project: Path) -> None:
    """The measured face of #89 from the other side, and the one a run found.

    Under a fixed ``migration.yaml`` the second acceptance overwrote the first
    and reported nothing: validation then found one migration and applying it
    applied only that one. Each migration carries its own id here.

    The second half is the defect no test caught until this flow was run. With
    both bodies at ``architecture/retry-policy.md``, accepting the second
    replaced the body the first migration had pinned, and
    ``theurian migrate validate`` exited 4 for the whole project -- *"hashes to
    abc7cdb70713 but the migration pins 4f9c5503e198"* -- with nothing
    appliable afterwards. Running validate and apply *after* the second
    acceptance is what makes that reachable from here.
    """
    _, first = _draft(project)
    _invoke("propose", "accept", first["proposalId"])
    _invoke("migrate", "apply")
    (project / "body.md").write_text("# Retry policy\n\nFive attempts.\n", encoding="utf-8")
    _, second = _draft(project, "--expected-revision", first["revisionId"])

    code, accepted = _invoke("propose", "accept", second["proposalId"])
    assert code == 0, accepted

    code, validated = _invoke("migrate", "validate")
    assert code == 0, validated
    assert validated["migrationCount"] == 2

    # Both, not one: the second migration changed the state hash, so this
    # applies to a fresh database and replays the set from empty (ADR-0007).
    # That replay is the strongest form of the assertion -- migration 1 is
    # re-read from disk here, so a shared body path would record the *second*
    # revision's text under the first revision's id (FR-K4).
    code, applied = _invoke("migrate", "apply")
    assert code == 0, applied
    assert applied["applied"] == [first["migrationId"], second["migrationId"]]
    assert (project / first["bodyDestination"]).read_text() == BODY


def test_accepting_the_same_proposal_twice_is_refused(project: Path) -> None:
    _, drafted = _draft(project)
    _invoke("propose", "accept", drafted["proposalId"])
    (project / ".theurian/proposals" / drafted["proposalId"] / drafted["migrationFile"]).write_text(
        "apiVersion: theurian.dev/v1\n", encoding="utf-8"
    )

    code, payload = _invoke("propose", "accept", drafted["proposalId"])

    assert code == EXIT_STATE_ERROR
    assert payload["remedy"]
    assert (
        project / ".theurian/migrations" / drafted["migrationFile"]
    ).read_text() != "apiVersion: theurian.dev/v1\n"


def test_accept_reports_an_unknown_proposal_with_a_remedy(project: Path) -> None:
    code, payload = _invoke("propose", "accept", "01K9C7VN4TQZB2M8XR5HD3JFEW")

    assert code == 1
    assert payload["remedy"]


def test_accept_refuses_an_id_that_is_not_a_ulid(project: Path) -> None:
    """A proposal id names a directory, so it is bounded before it is joined."""
    code, payload = _invoke("propose", "accept", "../../etc")

    assert code == EXIT_INVALID_INPUT
    assert payload["remedy"]
