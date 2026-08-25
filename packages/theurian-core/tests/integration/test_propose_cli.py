"""``theurian propose`` and ``theurian propose accept``, invoked in-process.

The service tests own the packaging rules. These own the adapter: that the
option surface reaches them, that a refusal arrives as JSON with a remedy rather
than as a traceback, and -- the one thing only an end-to-end run can say -- that
a proposal drafted here, accepted here, and then handed to the *existing*
migration commands actually applies.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from types import FunctionType
from typing import Any

import pytest
import typer
import yaml
from hang_guard import CAN_INTERRUPT_A_HANG, fails_rather_than_hanging
from typer.testing import CliRunner

from theurian.application import migration_engine
from theurian.cli import commands, migration_pipeline, propose_commands
from theurian.cli.main import app
from theurian.cli.propose_commands import _ACCEPT_STEPS, _DRAFT_STEPS
from theurian.domain.project import GITIGNORE_BLOCK_START, GITIGNORE_SECTIONS
from theurian.infrastructure.filesystem import migration_loader

pytestmark = pytest.mark.integration

runner = CliRunner()

#: A ``chmod 0o000`` denies nothing to root and nothing on Windows, so a test
#: that needs the mode to actually refuse cannot run there (the offline CI job
#: runs as root). Same guard the service-level permission tests carry.
_CANNOT_BE_REFUSED_BY_A_MODE = sys.platform == "win32" or os.geteuid() == 0

#: A FIFO is the shape whose ``st_size`` bounds nothing, and interrupting the
#: block a missing guard would cause is what makes a regression fail rather than
#: stall the suite (``hang_guard``). Both halves are POSIX, so they are one skip.
_CAN_MAKE_A_BLOCKING_FILE = hasattr(os, "mkfifo") and CAN_INTERRUPT_A_HANG

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


def test_propose_says_where_the_apply_time_invariants_are_checked(project: Path) -> None:
    """The asymmetry a caller has to be told about, because nothing shows it.

    ``migrate validate`` is schema conformance plus the statically decidable set
    guards, by recorded design (#36), so a next-steps list that stopped at
    "validate it" would read as a green light for something it never checked.

    What the list has to name is where the apply-time invariants *are* settled,
    and ADR-0027 decision 2 moved that: ``propose accept`` replays the whole set
    before it moves anything, so they are checked before the pull request exists
    rather than after it merges. This inverts the assertion that pinned the old
    division -- it required the word "merge" in the same sentence -- because the
    sentence it pinned is now false.
    """
    _, payload = _draft(project)

    steps = " ".join(payload["nextSteps"]).lower()

    assert "schema" in steps
    assert "propose accept" in steps
    assert "source anchor" in steps, "the apply-time invariant a caller meets most often"
    assert "after the pull request has merged" not in steps


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


def test_a_flag_valued_draft_option_handed_to_accept_is_refused(project: Path) -> None:
    """``--authored-here`` and a non-default ``--source-provider`` are stray too.

    Both have non-``None`` defaults, so an earlier version of the stray check
    could not see them and dropped them silently. ``--authored-here`` is a flag,
    so its presence is unambiguous.
    """
    _, drafted = _draft(project)

    result = runner.invoke(
        app,
        ["propose", "--json", "--authored-here", "accept", drafted["proposalId"]],
        catch_exceptions=False,
    )
    payload = json.loads(result.stderr)

    assert result.exit_code == EXIT_INVALID_INPUT
    assert "--authored-here" in payload["error"]


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


# -- `--local`: the boundary is asked of Git, not of a pattern list ----------
#
# ADR-0028's owed compliance item is worded against Git deliberately. Membership
# in `GITIGNORE_ENTRIES` is not the property #265 needs: a pattern can be in the
# tuple, spelled in a way Git does not apply to the path it was meant for -- an
# absent trailing slash, a leading one that anchors it somewhere else, a case
# that does not match -- and a test phrased over the tuple passes while every
# byte of a private draft is still offered to `git add -A`. So the question goes
# to `git status` and `git check-ignore`, in a real repository, and the answer
# comes back from Git.


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run Git in ``root`` with the developer's own configuration out of reach.

    ``GIT_CONFIG_GLOBAL`` and ``GIT_CONFIG_SYSTEM`` are pointed at the null
    device so the ignore decision comes from this repository and from nothing
    else. Without them the measurement is taken through whatever the person
    running the suite has installed: a global ``core.excludesFile`` covering
    ``.theurian/`` would hide the local proposal from ``git status`` with the
    rule under test deleted, and the test would report the boundary working on a
    machine where the boundary does not exist. Where the ignore comes from *is*
    the requirement here, so it cannot be left to the environment.

    The repository-local identity the fixture wrote stays in effect: it lives in
    ``.git/config``, which neither variable reaches.
    """
    return subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607 - Git is a documented prerequisite; PATH lookup is the point
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull},
    )


def test_a_local_proposal_is_invisible_to_git_while_an_ordinary_one_is_not(
    project: Path,
) -> None:
    """ADR-0028's second owed item: `git status` is the thing that has to be silent.

    A private draft leaks through an absent-minded ``git add -A``, and what
    stands between the two is whether Git considers the path ignored -- not
    whether a string is present in a tuple in ``domain/project.py``.

    The ordinary proposal is drafted alongside as the control, and it carries the
    whole weight of the test being able to fail: without it, an ignore rule that
    swallowed ``.theurian/`` entirely, a ``git status`` invocation that errored,
    or a run in a directory that is not a repository would each read as the
    boundary working. Both are asserted from the same status output, so the two
    answers are one measurement rather than two.

    ``--untracked-files=all`` because the default collapses an untracked
    directory to a single entry: ``?? .theurian/`` names neither proposal and
    would make the local one look absent for a reason that has nothing to do
    with the ignore rule.
    """
    _, local = _draft(project, "--local")
    _, tracked = _draft(project)

    status = _git(project, "status", "--porcelain", "--untracked-files=all")

    assert status.returncode == 0, status.stderr
    lines = status.stdout.splitlines()
    assert local["proposalDirectory"] == f".theurian/proposals-local/{local['proposalId']}"
    assert [line for line in lines if tracked["proposalDirectory"] in line], (
        f"the committable proposal must still be offered to Git: {lines}"
    )
    assert not [line for line in lines if "proposals-local" in line], (
        f"a --local proposal reached git status: {lines}"
    )


def test_the_rule_that_hides_a_local_proposal_is_one_a_clone_would_inherit(
    project: Path,
) -> None:
    """The whole point of ADR-0028, and the half `git status` alone cannot see.

    #265 measured a fence that worked perfectly on one machine and reached no
    clone, because it lived in ``.git/info/exclude`` -- a file Git never
    transmits. A rule installed there would satisfy the status test above and
    discharge none of the requirement, so the source of the decision is asserted
    rather than its effect: ``git check-ignore -v`` names the file and line the
    answer came from, and it must be the working tree's ``.gitignore``, which is
    a tracked file that travels with the repository.

    Asked about a file *inside* the proposal directory rather than the directory
    itself, because that is what ``git add -A`` walks.
    """
    _, local = _draft(project, "--local")
    inside = f"{local['proposalDirectory']}/{local['migrationFile']}"

    checked = _git(project, "check-ignore", "-v", inside)

    assert checked.returncode == 0, f"Git does not ignore {inside}: {checked.stderr}"
    source, _, rest = checked.stdout.partition(":")
    assert source == ".gitignore", checked.stdout
    assert rest.split(":")[1].startswith(".theurian/proposals-local/"), checked.stdout


#: A base64url token derived from a fixed seed -- the shape the content scanner
#: flags -- split from its seed so no credential-shaped literal sits in the file
#: for gitleaks to judge (the discipline `test_content_secrets.py` records).
_PLANTED_TOKEN = (
    base64.urlsafe_b64encode(hashlib.sha256(b"propose-cli warn rotate fixture (#198)").digest())
    .decode()
    .rstrip("=")
)


def test_a_warn_acceptance_that_lands_a_secret_leads_with_rotate_guidance(
    project: Path,
) -> None:
    """code-review M-4 / adversarial M-3: `warn` proceeds, so the steps must warn.

    Under `secretScan: warn` the acceptance succeeds (exit 0) and the findings
    ride on `secretFindings` -- but the nextSteps used to open with "review the
    diff, then open a pull request", saying nothing about a body the scan believes
    carries a live credential. The exit code is 0, so the rotate instruction lives
    in the steps or it lives nowhere. It now leads them.
    """
    (project / ".theurian" / "config.yaml").write_text(
        'security:\n  secretScan: "warn"\n', encoding="utf-8"
    )
    (project / "body.md").write_text(
        f"# Retry policy\n\nThree attempts.\n\n    THEURIAN_MCP_TOKEN={_PLANTED_TOKEN}\n",
        encoding="utf-8",
    )
    _, drafted = _draft(project)

    code, accepted = _invoke("propose", "accept", drafted["proposalId"])

    assert code == 0, accepted
    assert accepted["secretScanPolicy"] == "warn"
    assert accepted["secretFindings"], "the fixture body must actually be flagged"
    first = accepted["nextSteps"][0]
    assert "rotate" in first and "exposed" in first, f"the rotate step does not lead: {first}"
    assert accepted["nextSteps"][1:] == list(_ACCEPT_STEPS), "the standing steps must follow intact"


def _roll_the_block_back_to_pre_adr_0028(root: Path) -> None:
    """Make the managed ``.gitignore`` block the one every 0.1.0.dev9 project has.

    ADR-0028 added the ``.theurian/proposals-local/`` section; a project
    initialised before it has the block without that section. Removing exactly
    that section reproduces the shipped stale block -- the reviewers'
    ``repro_local_ignore.sh`` shape, derived from the sections rather than pasted
    so it tracks the real block.
    """
    authored = GITIGNORE_SECTIONS[1]
    removal = authored.comment + "\n" + "".join(f"{entry}\n" for entry in authored.entries)
    gitignore = root / ".gitignore"
    gitignore.write_text(gitignore.read_text(encoding="utf-8").replace(removal, ""), "utf-8")


def test_a_local_draft_brings_a_stale_ignore_block_current_before_it_writes(
    project: Path,
) -> None:
    """HIGH-2: `--local` confidentiality cannot rest on an ignore rule that is absent.

    Every project initialised by the shipped 0.1.0.dev9 has a managed block that
    predates ADR-0028, so it does *not* ignore `.theurian/proposals-local/`. A
    `--local` draft there wrote a private body to a directory Git tracks while its
    own nextSteps asserted it would not appear in `git status`.

    The draft now brings the block current before writing, so the body really is
    ignored and the assertion the command prints is verified reality. Asked of
    Git, not of the tuple, for the reason the sibling tests give -- and the stale
    block is rolled back to the *shipped* shape, which the ADR-0028 compliance
    tests structurally cannot reach because they re-init with the current block.
    """
    _roll_the_block_back_to_pre_adr_0028(project)
    assert ".theurian/proposals-local/" not in (project / ".gitignore").read_text("utf-8"), (
        "the fixture must actually be stale, or this asserts nothing"
    )

    code, local = _draft(project, "--local")

    assert code == 0, local
    inside = f"{local['proposalDirectory']}/{local['migrationFile']}"
    checked = _git(project, "check-ignore", "-v", inside)
    assert checked.returncode == 0, f"a --local body is not git-ignored after the draft: {inside}"
    assert ".theurian/proposals-local/" in (project / ".gitignore").read_text("utf-8"), (
        "the draft did not bring the managed block current"
    )
    assert any("proposals-local" in step and "git status" in step for step in local["nextSteps"]), (
        f"the --local step is missing from nextSteps: {local['nextSteps']}"
    )


def test_a_local_draft_is_refused_when_the_ignore_rule_cannot_be_written(
    project: Path,
) -> None:
    """The other arm of HIGH-2: refuse rather than write a private body unignored.

    When the managed block cannot be brought current -- here a `.gitignore` with a
    second start marker, which `ensure_gitignore` refuses to rewrite rather than
    guess which rules are Theurian's -- `--local` refuses with a remedy and writes
    no proposal directory, instead of landing a private body Git would track.
    """
    gitignore = project / ".gitignore"
    gitignore.write_text(
        gitignore.read_text("utf-8") + f"\n{GITIGNORE_BLOCK_START}\nsomething\n", "utf-8"
    )

    code, refusal = _draft(project, "--local")

    assert code == EXIT_INVALID_INPUT, refusal
    assert refusal.get("remedy"), refusal
    # `init` creates an empty `proposals-local/`; the refusal must not have written
    # a proposal *into* it while the ignore rule was unestablished.
    assert not list((project / ".theurian" / "proposals-local").iterdir()), (
        "a proposal was written despite the ignore rule being unestablished"
    )


def test_accepting_a_local_proposal_says_what_stays_behind_and_what_does_not(
    project: Path,
) -> None:
    """The step it replaces cannot be followed for a `--local` proposal.

    The ordinary first step tells the author to open a pull request *with the
    proposal directory in it*, and for a local one that instruction is only
    carryable through ``git add -f`` -- the publication the flag was chosen to
    prevent. The migration and the body have left the ignored directory by the
    time this prints, so the pull request is complete without it; what stays
    behind is ``evidence.json``, and a reviewer who is not told will look for a
    file that was never going to arrive.

    The tracked wording is asserted absent as well as the local wording present:
    a step list that printed both would be self-contradicting, and one that
    printed neither is what a mistyped branch produces.
    """
    _, drafted = _draft(project, "--local")

    code, accepted = _invoke("propose", "accept", drafted["proposalId"])

    assert code == 0, accepted
    first = accepted["nextSteps"][0]
    assert ".theurian/proposals-local/" in first, first
    assert "evidence.json" in first, first
    assert first != _ACCEPT_STEPS[0], "the committable wording is what this replaces"
    assert accepted["nextSteps"][1:] == list(_ACCEPT_STEPS[1:]), "only the first step differs"


def test_an_ordinary_acceptance_keeps_the_step_that_names_the_pull_request(
    project: Path,
) -> None:
    """The control for the test above: the default is the committable one.

    ADR-0013 point 7 is not reversed by ADR-0028 -- a proposal is still review
    input that travels in the pull request -- so the local wording must reach
    only a proposal that asked for it. Without this, a change that printed the
    local step unconditionally would leave every assertion above green while
    telling every author that their proposal is git-ignored.
    """
    _, drafted = _draft(project)

    code, accepted = _invoke("propose", "accept", drafted["proposalId"])

    assert code == 0, accepted
    assert accepted["nextSteps"] == list(_ACCEPT_STEPS)


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


def test_an_update_without_expected_revision_is_refused_at_the_cli(project: Path) -> None:
    """HIGH-5 (#210) at the process edge: no unguarded update reaches a file.

    After the first proposal is accepted and applied, the item exists in
    approved state. A second draft for it with no ``--expected-revision`` used
    to write an update that validated and then failed at ``migrate apply`` --
    after merge. It is refused at draft now, exit 2 with the revision to pass.
    """
    _, first = _draft(project)
    _invoke("propose", "accept", first["proposalId"])
    _invoke("migrate", "apply")

    code, payload = _draft(project)

    assert code == EXIT_INVALID_INPUT
    assert "already exists" in payload["error"]
    assert first["revisionId"] in payload["remedy"]


def test_re_accepting_an_accepted_proposal_exits_with_the_state_code(project: Path) -> None:
    """#254: the natural already-accepted case, which used to fold into exit 1.

    The published table said 4 for "that migration is already in place", and the
    only way to reach 4 was the hand-built state the next test constructs. Simply
    running ``accept`` twice -- the way a caller meets this -- exited 1 alongside
    "no such proposal", so a script driving a corpus could not tell "this one has
    already landed, skip it" from "this proposal is not there, stop".

    4 is the knowledge-state code, and both faces of "the change is already in
    place" now carry it. It is the one answer whose meaning is *do not draft this
    again*, which is why it must not share a code with the interrupted draft
    above, whose meaning is exactly the opposite.
    """
    _, drafted = _draft(project)
    first, _ = _invoke("propose", "accept", drafted["proposalId"])
    assert first == 0

    code, payload = _invoke("propose", "accept", drafted["proposalId"])

    assert code == EXIT_STATE_ERROR
    assert "appears to have been accepted" in payload["error"]
    assert "pull request" in payload["remedy"]


def test_exit_four_also_covers_a_migration_set_that_cannot_be_read(project: Path) -> None:
    """The third case the published table names, and the reason 4 is not "done".

    Resolving the project loads the approved migration set, and a set that cannot
    be read exits 4 from there -- before ``accept`` dispatches at all. So exit 4
    does not mean "already accepted": here it means the proposal is still
    waiting, and a caller that skips on 4 abandons it. Reproduced with the body a
    landed migration pins removed, which is one of three measured shapes
    (unparseable YAML and a digest that no longer matches are the others, and all
    three arrive through the same ``MigrationError`` branch).
    """
    _, landed = _draft(project)
    _invoke("propose", "accept", landed["proposalId"])
    (project / "body.md").write_text("# Other\n\nText.\n", encoding="utf-8")
    _, waiting = _draft(project, "--item-id", "architecture.other-policy")
    (project / landed["bodyDestination"]).unlink()

    code, payload = _invoke("propose", "accept", waiting["proposalId"])

    assert code == EXIT_STATE_ERROR
    assert "could not be read" in payload["error"]
    proposal = project / ".theurian/proposals" / waiting["proposalId"]
    assert list(proposal.glob("*.yaml")), "the proposal is still waiting, not accepted"


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


def test_an_interrupted_draft_is_diagnosed_as_one_rather_than_as_accepted(
    project: Path,
) -> None:
    """#253 at the process edge: the remedy must not discard the drafted work.

    The interruption is reproduced by removing the file a killed ``propose``
    would never have written -- the migration is the last of the three -- leaving
    the body and ``evidence.json`` behind. That shape reported "no action is
    needed. Review the change and open a pull request" while
    ``.theurian/migrations/`` held nothing, so a reader following the remedy
    would have opened a pull request containing no change at all.
    """
    _, drafted = _draft(project)
    directory = project / ".theurian/proposals" / drafted["proposalId"]
    (directory / drafted["migrationFile"]).unlink()

    code, payload = _invoke("propose", "accept", drafted["proposalId"])

    assert code == 1
    assert "pull request" not in payload["remedy"]
    assert "theurian propose" in payload["remedy"]
    assert not list((project / ".theurian/migrations").glob("*.yaml"))
    assert (project / drafted["bodyFile"]).is_file(), "the draft's body is still there to lose"


def test_a_success_payload_cannot_forge_output_through_a_body_path(project: Path) -> None:
    """#253 round three, CLASS C: the exit-0 stdout path escapes controls too.

    ``propose accept`` emits ``bodyFiles`` and ``migrationFile`` on success via
    ``_relative`` -> ``_emit`` -> ``_render``, which wrote raw. A hand-authored
    ``contentFile`` carrying ``ESC``/``CR`` therefore forged the tool's *own
    success output* -- a channel the refusal-path ``_names`` never covered, since
    the accept succeeds. Reproduced end to end: the migration's ``contentFile`` is
    rewritten to carry the control bytes and the body moved to the matching path,
    so accept lands it and prints the path. The render sink escapes them now.
    """
    _, drafted = _draft(project)
    directory = project / ".theurian/proposals" / drafted["proposalId"]
    migration = directory / drafted["migrationFile"]
    forged_leaf = "architecture/\x1b[2Kx.\x1b[2Kforged.md"
    document = yaml.safe_load(migration.read_text(encoding="utf-8"))
    old_content_file = drafted["contentFile"]
    new_content_file = f"../knowledge/{forged_leaf}"
    for operation in document["operations"]:
        if operation.get("contentFile") == old_content_file:
            operation["contentFile"] = new_content_file
    migration.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")
    old_body = directory / Path(drafted["bodyFile"]).relative_to(Path(drafted["proposalDirectory"]))
    new_body = directory / forged_leaf
    new_body.parent.mkdir(parents=True, exist_ok=True)
    old_body.rename(new_body)

    result = runner.invoke(
        app, ["propose", "accept", drafted["proposalId"]], catch_exceptions=False
    )

    assert result.exit_code == 0, result.stdout
    assert "\x1b" not in result.stdout and "\r" not in result.stdout
    assert "\\x1b" in result.stdout, "the control byte is rendered as a visible escape"


def test_the_render_sink_escapes_every_control_and_keeps_printable_unicode(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The central sink, driven directly: escape every control, keep CJK.

    ``_emit`` in text mode is the output path for every command's success payload,
    so escaping controls here closes the class for all of them. A value's own
    ``\\n``/``\\t`` is escaped too -- the output's structural whitespace is
    ``_render``'s own f-strings, added outside the sink, so a newline *inside a
    value* only ever appends a line that reads as the tool's. The one raw
    newline expected below is the record separator ``_render`` writes between
    entries, never one a value carried.
    """
    from theurian.cli.commands import _emit

    _emit(
        {
            "path": "a\x1b[2K\rforged",
            "title": "再試行ポリシー",
            "lines": ["one\ntwo\ttab", "x\x7f\x9by"],
        },
        as_json=False,
    )

    out = capsys.readouterr().out
    assert "\x1b" not in out and "\r" not in out and "\x7f" not in out and "\x9b" not in out
    assert "\\x1b" in out and "\\x7f" in out and "\\x9b" in out
    assert "再試行ポリシー" in out, "printable non-ASCII is untouched"
    assert "one\\x0atwo\\x09tab" in out, "a value's own newline and tab are escaped"
    # The list entry rendered on one line: the value carried no raw newline through.
    assert "  - one\\x0atwo\\x09tab\n" in out


def test_the_render_sink_escapes_a_control_in_a_key(capsys: pytest.CaptureFixture[str]) -> None:
    """Keys go through the sink too, not only values.

    No payload key carries a control today -- every key is a code literal -- so no
    CLI input drives this. It is a structural guarantee: the sink escapes whatever
    it is handed, and a nested data dict's keys are the one place a future payload
    could carry an untrusted key. Driven directly with a synthetic control-char
    key; dies if the key is not routed through the sink.
    """
    from theurian.cli.commands import _emit

    _emit({"a\x1b[2Kb": "value"}, as_json=False)

    out = capsys.readouterr().out
    assert "\x1b" not in out and "\\x1b" in out


def test_the_main_emit_sink_escapes_controls(capsys: pytest.CaptureFixture[str]) -> None:
    """``main._emit`` -- ``--version`` and ``compat check`` -- routes through the sink.

    Its fields are repr'd or validated upstream, so no CLI input reaches it with a
    raw control byte; routing it through the shared sink is what makes the "every
    emitter uses the sink" invariant structural rather than dependent on that. The
    guarantee is driven directly with a synthetic control-char value; dies if
    ``main._emit`` stops escaping.
    """
    from theurian.cli.main import _emit as main_emit

    main_emit({"a\x1b[2Kkey": "b\x1b[2K\rforged"}, as_json=False)

    out = capsys.readouterr().out
    assert "\x1b" not in out and "\r" not in out
    assert "\\x1b" in out and "\\x0d" in out
    # Both halves of the line escaped: dropping the key's sanitizer leaves a raw
    # ESC in the key, which the assertion above catches. main._emit renders
    # `key: value`, so the key precedes the first `: ` and the value follows it.
    key_text = out.partition(": ")[0]
    assert "\x1b" not in key_text and "\\x1b" in key_text


def test_the_fail_sink_escapes_controls_on_the_error_path(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``_fail``'s stderr text is sanitized too, not only ``_render``'s stdout.

    The CHANGELOG credits the error path as closed; only the service layer's
    ``_names`` was tested for it before. A control character in the message or the
    remedy -- from a source that skipped ``_names`` -- must not reach the terminal
    raw. Dies if ``escape_terminal_controls`` is dropped from ``_fail``.
    """
    from theurian.cli.commands import _fail

    with pytest.raises(typer.Exit):
        _fail("a\x1b[2K\rforged", remedy="r\ru\x9bx", as_json=False, code=1)

    err = capsys.readouterr().err
    assert "\x1b" not in err and "\r" not in err and "\x9b" not in err
    assert "\\x1b" in err and "\\x9b" in err


def test_accept_reports_an_unknown_proposal_with_a_remedy(project: Path) -> None:
    code, payload = _invoke("propose", "accept", "01K9C7VN4TQZB2M8XR5HD3JFEW")

    assert code == 1
    assert payload["remedy"]


def test_accept_refuses_an_id_that_is_not_a_ulid(project: Path) -> None:
    """A proposal id names a directory, so it is bounded before it is joined."""
    code, payload = _invoke("propose", "accept", "../../etc")

    assert code == EXIT_INVALID_INPUT
    assert payload["remedy"]


@pytest.mark.skipif(_CANNOT_BE_REFUSED_BY_A_MODE, reason="POSIX permission bits, and not as root")
def test_accept_publishes_a_json_document_for_a_proposal_it_cannot_read(project: Path) -> None:
    """#227: ``--json`` published nothing at all when the directory was unreadable.

    Confirmed through the installed CLI in a sandbox: after ``chmod 000`` on
    ``.theurian/proposals/<id>``, ``theurian propose accept <id> --json`` exited 1
    having written **zero bytes** to stdout, with the traceback bottoming at
    ``PermissionError: [Errno 13] Permission denied: .../evidence.json``. CP-2
    says a ``--json`` failure is an ``{error, remedy}`` document, so a caller
    parsing stdout gets a decode error and no remedy -- the one failure mode
    ``--json`` exists to remove.

    Invoked with ``catch_exceptions=True``, unlike the ``_invoke`` helper: the
    defect is precisely that an exception escapes the command, so it is caught
    here and named in a failed assertion rather than propagated as a test error.
    ``SystemExit`` is what a *translated* failure exits with, so it is the one
    exception this asserts nothing about.
    """
    _, drafted = _draft(project)
    directory = project / ".theurian/proposals" / drafted["proposalId"]
    directory.chmod(0o000)
    try:
        result = runner.invoke(
            app, ["propose", "accept", drafted["proposalId"], "--json"], catch_exceptions=True
        )
    finally:
        directory.chmod(0o755)

    escaped = None if isinstance(result.exception, SystemExit) else result.exception
    assert escaped is None, f"an exception escaped instead of a --json document: {escaped!r}"
    assert result.exit_code != 0, "an unreadable proposal is not an acceptance"
    # The *whole* stream is parsed, not searched: that is what rules out a
    # traceback printed beside the document as well as one printed instead of it.
    # (In-process the runner captures the exception rather than printing it, so a
    # `"Traceback" not in output` check would hold here whatever the code did.)
    payload = json.loads(result.stderr or result.stdout)
    assert payload["error"].strip()
    assert payload["remedy"].strip()


def _accept_catching(proposal_id: str) -> tuple[int, object, str]:
    """Invoke ``propose accept --json`` and return exit, escaped exception, stream.

    ``catch_exceptions=True`` unlike ``_invoke``: class B is precisely that an
    exception escapes the command, so it is caught and named here rather than
    raised as a test error. ``SystemExit`` is what a *translated* failure exits
    with, so it is the one exception this reports as "none escaped".
    """
    result = runner.invoke(app, ["propose", "accept", proposal_id, "--json"], catch_exceptions=True)
    escaped = None if isinstance(result.exception, SystemExit) else result.exception
    return result.exit_code, escaped, (result.stdout or "") + (result.stderr or "")


def _poison_content_file(root: Path, drafted: dict[str, Any], quoted_value: str) -> None:
    """Repoint the drafted migration's ``contentFile`` at a hand-authored value.

    ``accept`` computes its body moves before its pre-check runs, so a value the
    schema rejects reaches ``_destination_of``'s ``resolve()`` unfiltered -- ahead
    of stage 1, which would refuse the document (ADR-0027 decision 2). The order
    is what makes these faults reachable at all, not an absence of validation:
    the moves feed the pre-check, so they are computed first.
    ``quoted_value`` is a YAML double-quoted scalar so its escapes decode to the
    real code points.
    """
    migration = root / drafted["proposalDirectory"] / drafted["migrationFile"]
    text = migration.read_text(encoding="utf-8")
    replaced = text.replace(
        f"contentFile: {drafted['contentFile']}", f"contentFile: {quoted_value}"
    )
    assert replaced != text, "the contentFile anchor did not match"
    migration.write_text(replaced, encoding="utf-8")


@pytest.mark.skipif(_CANNOT_BE_REFUSED_BY_A_MODE, reason="POSIX permission bits, and not as root")
def test_accept_reports_a_completed_move_whose_source_cleanup_could_not_finish(
    project: Path,
) -> None:
    """CP-2 (code review + orchestrator ``repro_readonly_proposal_dir``): a landed
    accept whose trailing cleanup fails must not read as a non-landing.

    At ``0o555`` the proposal directory lists, stats and reads, so the examination
    phase and ``_commit``'s writes all succeed and the migration and body land.
    Only the trailing ``unlink`` of the proposal's own now-copied files -- outside
    ``_commit``'s write guard -- cannot run, and its ``PermissionError`` escaped
    ``accept`` raw: exit 1, both streams empty. Exit 1's contract is "nothing
    landed", so a caller re-drafts and mints a duplicate migration (#89). The move
    completed, so this reports success with a remedy naming the leftover, never a
    failure.
    """
    _, drafted = _draft(project)
    directory = project / ".theurian/proposals" / drafted["proposalId"]
    directory.chmod(0o555)
    try:
        code, escaped, stream = _accept_catching(drafted["proposalId"])
    finally:
        directory.chmod(0o755)

    assert escaped is None, f"an exception escaped instead of a document: {escaped!r}"
    assert code == 0, f"the migration and body landed, so this is a success: {stream}"
    assert (project / ".theurian/migrations" / drafted["migrationFile"]).is_file()
    assert (project / drafted["bodyDestination"]).read_text() == BODY
    payload = json.loads(stream)
    assert payload["proposalId"] == drafted["proposalId"]
    # The leftover is named so an operator can finish the cleanup by hand.
    assert f".theurian/proposals/{drafted['proposalId']}" in payload["remedy"]


@pytest.mark.skipif(_CANNOT_BE_REFUSED_BY_A_MODE, reason="POSIX permission bits, and not as root")
def test_accept_publishes_a_json_document_when_the_migrations_dir_cannot_be_made(
    project: Path,
) -> None:
    """CP-2 (security review + orchestrator ``repro_cli_cp2``): ``_commit``'s
    opening ``mkdir`` escaped ``accept`` raw.

    With ``.theurian/migrations/`` absent and ``.theurian/`` unwritable,
    ``_commit``'s ``self._paths.migrations.mkdir(...)`` -- outside its own write
    guard -- raised ``PermissionError`` and it left ``accept`` untranslated: exit
    1, zero bytes on both streams. Moving the ``mkdir`` inside the guard (the
    rollback set is empty there) makes it a CP-2 ``{error, remedy}`` document, and
    nothing lands.
    """
    _, drafted = _draft(project)
    shutil.rmtree(project / ".theurian/migrations")
    (project / ".theurian").chmod(0o555)
    try:
        code, escaped, stream = _accept_catching(drafted["proposalId"])
    finally:
        (project / ".theurian").chmod(0o755)

    assert escaped is None, f"an exception escaped instead of a document: {escaped!r}"
    assert code != 0, "an accept that could not write is not a success"
    payload = json.loads(stream)
    assert payload["error"].strip()
    assert payload["remedy"].strip()
    assert not (project / ".theurian/migrations" / drafted["migrationFile"]).exists()
    # The OSError's text carries the absolute path; the translated document names
    # it project-relative and must not leak the machine's own directory.
    assert str(project) not in json.dumps(payload), "the absolute path must not leak"


def test_accept_publishes_a_json_document_for_a_nul_in_the_content_file(project: Path) -> None:
    """CP-2 (adversarial e14): a NUL in ``contentFile`` escaped ``accept`` raw.

    ``resolve()`` raises ``ValueError`` (*"embedded null character in path"*)
    before any containment check, and it is not an ``OSError``, so the examination
    clause did not catch it: exit 1, a Rich traceback, zero bytes on stdout. The
    fix widens the ``resolve()`` translation to ``(ValueError, OSError)`` as the
    loader does.
    """
    _, drafted = _draft(project)
    _poison_content_file(project, drafted, '"../knowledge/architecture/a\\0b.md"')

    code, escaped, stream = _accept_catching(drafted["proposalId"])

    assert escaped is None, f"an exception escaped instead of a document: {escaped!r}"
    assert code != 0
    payload = json.loads(stream)
    assert payload["error"].strip()
    assert payload["remedy"].strip()


def test_accept_publishes_a_json_document_for_a_surrogate_in_the_content_file(
    project: Path,
) -> None:
    """CP-2 (adversarial e14): a lone surrogate in ``contentFile`` escaped ``accept`` raw.

    Its ``resolve()`` raises ``UnicodeEncodeError`` -- a ``ValueError`` subclass,
    which is why the widening is ``ValueError`` and not the NUL's exact type.
    """
    _, drafted = _draft(project)
    _poison_content_file(project, drafted, '"../knowledge/architecture/a\\uD800b.md"')

    code, escaped, stream = _accept_catching(drafted["proposalId"])

    assert escaped is None, f"an exception escaped instead of a document: {escaped!r}"
    assert code != 0
    payload = json.loads(stream)
    assert payload["error"].strip()
    assert payload["remedy"].strip()


@pytest.mark.skipif(
    not _CAN_MAKE_A_BLOCKING_FILE, reason="needs os.mkfifo and an interruptible timer"
)
def test_accept_names_the_proposal_body_whose_size_bounds_nothing(project: Path) -> None:
    """The refusal must name the file it refused, and cure the right object.

    ``read_source_file`` deliberately names nothing -- its argument is the
    author's own string -- so every caller that holds a name it has decided is
    safe to print re-attaches it. ``_read_within_project`` did not, and the
    resulting payload named **no path at all**: reproduced through the installed
    CLI in a sandboxed HOME, ``propose accept --json`` over a FIFO body published
    ``error: The referenced file is a named pipe (FIFO), not a regular file`` and
    a remedy pointing at the ``contentFile``. 0.1.0.dev9 printed the path here,
    from a message that interpolated it, so this was a diagnosability regression
    and not a gap.

    Both halves are wrong together, which is why both are asserted. The
    ``contentFile`` this migration names is *fine*; what is not a regular file is
    the body sitting in the proposal directory, and a remedy naming the other one
    sends a reader to edit a path that was never the problem.

    The name attached is the one the caller built -- the proposal's ULID plus the
    normalized ``knowledge/`` tail ``_body_moves`` resolved -- never the author's
    ``contentFile`` string, whose echo is what
    ``tests/unit/test_path_security.py::
    test_no_reachable_refusal_branch_echoes_the_attacker_supplied_path`` forbids.
    It is the same string the sibling symlink refusal at this call site already
    prints.

    The timer makes a removed shape guard fail rather than stall the suite: the
    read would otherwise block in ``open()`` for a writer that never comes.
    """
    _, drafted = _draft(project)
    tail = Path(drafted["bodyDestination"]).relative_to(".theurian/knowledge")
    body = project / drafted["proposalDirectory"] / tail
    body.unlink()
    os.mkfifo(body)

    with fails_rather_than_hanging(15, waiting_for="propose accept over a FIFO body"):
        code, payload = _invoke("propose", "accept", drafted["proposalId"])

    named = (Path(drafted["proposalDirectory"]) / tail).as_posix()
    assert code == 1
    assert payload["error"] == (
        f"{named!r} names a file that is a named pipe (FIFO), not a regular file"
    )
    assert payload["remedy"] == (
        f"Replace the file {named!r} names with a regular file, then retry. The size "
        f"Theurian checks before it opens a file bounds nothing about what a read of a "
        f"named pipe (FIFO) returns, so it is refused unread."
    )
    assert drafted["contentFile"] not in payload["remedy"], (
        "the contentFile is not what is wrong here, so the cure must not name it"
    )


def test_accept_writes_its_json_failure_document_to_stderr_leaving_stdout_clean(
    project: Path,
) -> None:
    """CP-2 / ADR-0013: a ``--json`` accept failure is an ``{error, remedy}``
    document on *stderr*, and stdout stays a clean machine channel.

    ``_fail`` writes the failure document to stderr while ``_emit`` writes the
    success payload to stdout (``cli/commands.py``), so a caller can read stdout
    for a result and stderr for a fault without the two colliding. The ADR-cited
    accept-failure tests read a *concatenation* of both streams
    (:func:`_accept_catching` returns ``stdout + stderr``), so none of them would
    notice the document drifting onto stdout -- a regression that would let a
    caller parsing stdout read an error as a success payload, or miss the remedy
    where the contract puts it. This is the one accept-path test that pins the
    split the others assume.

    The failing input is a NUL in ``contentFile`` (adversarial e14): deterministic
    and mode-independent, so this needs no root skip. Measured here: on this
    failure stdout is exactly empty and stderr carries the whole document.
    """
    _, drafted = _draft(project)
    _poison_content_file(project, drafted, '"../knowledge/architecture/a\\0b.md"')

    result = runner.invoke(
        app, ["propose", "accept", drafted["proposalId"], "--json"], catch_exceptions=True
    )

    assert result.exit_code != 0, "a poisoned contentFile is not an acceptance"
    # stdout is the machine result channel; a failure must leave it empty, not
    # print the error document there where a success parser would consume it.
    assert result.stdout == "", f"stdout must stay a clean channel on a failure: {result.stdout!r}"
    # The whole document is on stderr, parseable as one object -- error and remedy.
    payload = json.loads(result.stderr)
    assert payload["error"].strip()
    assert payload["remedy"].strip()


# -- accept validates before it moves (ADR-0027 decision 2) -----------------
#
# The service tests own the three faces #307 demonstrated. These own the two
# things only a process-level run can say: which exit code a refusal carries,
# and that the set left behind is one `theurian migrate apply` really applies.


def _contents(root: Path) -> dict[str, bytes]:
    """Every regular file under ``root``, keyed by its relative path.

    Bytes rather than names: a refusal that rewrote a proposal's migration in
    place would leave the name set unchanged. No digest is taken, so nothing here
    can agree with a broken hash -- the comparison is byte equality.
    """
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


def test_a_proposal_racing_another_onto_one_item_is_refused_and_the_rest_applies(
    project: Path,
) -> None:
    """ADR-0027 decision 2's stage 4: the face stages 1 to 3 cannot see.

    Both proposals are drafted *before either acceptance*, so both claim their
    item's first revision and neither could have carried an
    ``--expected-revision``: the item did not exist when they were written. The
    pair is schema-valid, passes every statically decidable set guard and exits 0
    on ``migrate validate`` -- and can never be applied. Measured on #316 before
    the pre-check shipped: both acceptances exited 0, both proposals were
    consumed, and the project was left validate-green and apply-red forever, with
    the only recovery being to delete a landed migration --
    ``plugins/claude-code/commands/propose.md`` forbids exactly that to the
    documented actor.

    So this is the test that goes RED if the dry replay is removed while stages
    1 to 3 stay, and the three face tests in ``test_proposal_service.py`` do not.

    Exit 1 and not 4: the fault is this proposal's own claim on the item, and
    nothing landed, so re-drafting against the revision now in place is the
    recovery -- which the remedy names.
    """
    _, first = _draft(project)
    (project / "body.md").write_text("# Retry policy\n\nFive attempts.\n", encoding="utf-8")
    _, second = _draft(project)
    code, accepted = _invoke("propose", "accept", first["proposalId"])
    assert code == 0, accepted
    directory = project / ".theurian/proposals" / second["proposalId"]
    before = _contents(directory)

    code, payload = _invoke("propose", "accept", second["proposalId"])

    assert code == 1, payload
    assert "Revision conflict" in payload["error"]
    assert "--expected-revision" in payload["remedy"]
    assert _contents(directory) == before, "the refused proposal must survive intact"
    # And what is left is a set that really applies -- the half a refusal alone
    # does not prove, since refusing everything would satisfy the assertions above.
    code, validated = _invoke("migrate", "validate")
    assert code == 0, validated
    assert validated["migrationCount"] == 1
    code, applied = _invoke("migrate", "apply")
    assert code == 0, applied
    assert applied["applied"] == [first["migrationId"]]


def test_a_fault_in_the_landed_set_is_not_reported_as_this_proposals_fault(
    project: Path,
) -> None:
    """#227's ``{error, remedy}`` for the fault direction the proposal did not cause.

    ``accept``'s failure surface widened with the pre-check: it now loads and
    replays the project's whole migration set, so a fault that predates the
    proposal can refuse an acceptance. Reporting that under exit 1 would be
    false in the half that matters -- exit 1's contract is "nothing landed and
    drafting again is the recovery", and a second draft here mints a duplicate
    for a fault this proposal does not have (#89). It takes exit 4, the code
    already reserved for "read the knowledge state before doing anything".

    The landed fault is a racing pair placed by hand, which is the channel
    ADR-0027's fourth residue names: only ``accept`` gained the replay, so a
    migration copied into ``.theurian/migrations/`` never passes through it. The
    set loads and validates green, and ``migrate apply`` refuses it -- asserted
    here, because it is what makes the accept-side refusal agree with apply
    rather than merely coincide with it.
    """
    _, first = _draft(project)
    (project / "body.md").write_text("# Retry policy\n\nFive attempts.\n", encoding="utf-8")
    _, second = _draft(project)
    _invoke("propose", "accept", first["proposalId"])
    _hand_place(project, second)
    assert _invoke("migrate", "validate")[0] == 0, "the landed pair must be validate-green"
    assert _invoke("migrate", "apply")[0] == EXIT_STATE_ERROR, "and apply-red"
    (project / "body.md").write_text("# Other\n\nText.\n", encoding="utf-8")
    _, waiting = _draft(project, "--item-id", "architecture.other-policy")
    directory = project / ".theurian/proposals" / waiting["proposalId"]
    before = _contents(directory)

    code, payload = _invoke("propose", "accept", waiting["proposalId"])

    assert code == EXIT_STATE_ERROR, payload
    assert "with or without this proposal" in payload["error"]
    assert ".theurian/migrations/" in payload["remedy"]
    # Never a re-draft: this proposal is not the cause, and drafting it again
    # would mint a second migration for a fault it does not have (#89).
    assert "draft" not in payload["remedy"].lower(), payload["remedy"]
    assert _contents(directory) == before, "the refused proposal must survive intact"


def _hand_place(root: Path, drafted: dict[str, Any]) -> None:
    """Move a drafted proposal's files into place without going through ``accept``.

    The one channel that still reaches a validate-green, apply-red set (ADR-0027
    residue 4). The proposal directory is removed afterwards, so what is left is
    indistinguishable from a migration a contributor committed by hand.
    """
    directory = root / ".theurian/proposals" / drafted["proposalId"]
    shutil.copy(directory / drafted["migrationFile"], root / ".theurian/migrations")
    destination = root / drafted["bodyDestination"]
    destination.parent.mkdir(parents=True, exist_ok=True)
    body = directory / Path(drafted["bodyFile"]).relative_to(drafted["proposalDirectory"])
    shutil.copy(body, destination)
    shutil.rmtree(directory)


# -- one pipeline, not two (ADR-0027 decision 2's hard condition) -----------
#
# The closure argument is that `accept` and `migrate apply` *cannot* disagree
# about whether a set is usable, because there is one pipeline rather than two.
# A test comparing the two answers on a fixture holds for exactly as long as the
# two happen to agree, which is the failure the hard condition exists to prevent
# -- so these walk the bytecode instead, in the shape
# `test_mcp_tools.py::test_no_registered_tool_can_reach_a_canonical_write` uses
# for the MCP write boundary.


def _referenced_names(function: Any) -> set[str]:
    """Every attribute and global name reachable from a function's own code.

    Recursing into nested code objects is load-bearing here and not a
    generalisation: ``propose_commands._service`` hands the service its
    dependencies as lambdas, so ``rehearse_migration_set`` and
    ``validate_migration_document`` live in the lambdas' code objects and are
    absent from ``_service.__code__.co_names``.
    """
    seen: set[str] = set()
    pending = [function.__code__]
    while pending:
        code = pending.pop()
        seen.update(code.co_names)
        pending.extend(c for c in code.co_consts if hasattr(c, "co_names"))
    return seen


def _names_through_local_helpers(function: Any) -> set[str]:
    """Names reachable from ``function``, following helpers defined beside it.

    ``migrate validate`` reaches the whole-set guards through a wrapper in its
    own module (``_refuse_a_set_a_static_guard_rejects``, which turns a refusal
    into an exit code and a remedy), so a walk that stopped at the command
    callback would see the wrapper's name and miss what it calls. Only functions
    defined in the same module are followed, which keeps the closure bounded --
    and is why the walk stops at the application-layer guard set rather than
    descending into it.
    """
    seen: set[str] = set()
    visited = {function}
    pending = [function]
    while pending:
        current = pending.pop()
        names = _referenced_names(current)
        seen |= names
        for name in names:
            target = current.__globals__.get(name)
            if (
                isinstance(target, FunctionType)
                and target.__module__ == function.__module__
                and target not in visited
            ):
                visited.add(target)
                pending.append(target)
    return seen


def _callback(app: typer.Typer, name: str) -> Any:
    """The function a registered command dispatches to.

    Looked up through the registration rather than imported by name, so these
    walk what the CLI actually runs: a module-level function nothing registers
    would otherwise satisfy every assertion below.
    """
    command = next(c for c in app.registered_commands if c.name == name)
    assert command.callback is not None, f"the {name} command has no callback to walk"
    return command.callback


def test_the_accept_replay_and_migrate_apply_reach_one_apply_function() -> None:
    """ADR-0027 decision 2's hard condition, which no behavioural test can hold.

    Two halves, and a re-implementation fails one or the other. The name has to
    be reached from both -- a replay-shaped subset that never calls it fails
    here -- and the name has to resolve, through each module's *own* globals, to
    one object, so a second definition under the same name fails too.
    """
    apply_command = _callback(commands.migrate_app, "apply")
    reached = _referenced_names(apply_command)

    assert "apply_migration_set" in reached, "migrate apply no longer runs the shared pipeline"
    assert "apply_migration_set" in _referenced_names(migration_pipeline.rehearse_migration_set)
    assert (
        apply_command.__globals__["apply_migration_set"]
        is migration_pipeline.rehearse_migration_set.__globals__["apply_migration_set"]
    ), "the replay and migrate apply now hold two definitions of one pipeline"
    # The control that proves this walk can answer "no": a real apply must not
    # route through the rehearsal, and a walk that reported every name in the
    # interpreter would claim it does.
    assert "rehearse_migration_set" not in reached


def test_the_accept_pre_check_reaches_the_loaders_own_entry_points_and_every_guard() -> None:
    """The pre-check's stages are the loader's and ``validate``'s, not copies.

    Stage 1 is the schema entry ``draft`` already calls, stage 2 is the loader's
    own read (which is where a declared pin is verified), and stage 3 is the
    whole-set guards.

    **Stage 3 is asked differently than it used to be, because the thing it was
    asking about is gone.** This compared two *populations* of ``refuse_*``
    names, one walked out of ``migrate validate``'s own code and one out of the
    replay's, and it could do that only because each of them named the guards
    itself. That hand-listing is what
    :func:`~theurian.application.migration_engine.run_static_migration_guards`
    removes: both paths now call one function, so "a fourth guard added to
    ``migrate validate`` and not to the replay" is not a state the code can be
    in, and a population comparison between two call sites that no longer list
    anything would compare two empty sets and pass.

    So the check moves to where the list now lives. Both paths are held to
    reaching the shared function and to resolving it to the *same object*, and
    the population check is applied to that function's own body. The residual it
    still catches is the one that survived the refactor: a fourth guard added to
    neither -- or one silently dropped from the set, which a hard-coded triple
    beside the call sites would not have caught either.
    """
    wiring = _referenced_names(propose_commands._service)
    replay = migration_pipeline.rehearse_migration_set
    validate_command = _callback(commands.migrate_app, "validate")

    assert "_service" in _referenced_names(_callback(propose_commands.propose_app, "accept"))
    assert {"rehearse_migration_set", "validate_migration_document"} <= wiring, wiring
    assert (
        propose_commands._service.__globals__["validate_migration_document"]
        is migration_loader.validate_migration_document
    ), "stage 1 is a second schema check, not the loader's own"
    assert propose_commands._service.__globals__["rehearse_migration_set"] is replay, (
        "the accept wiring replays through something other than the shared pipeline"
    )
    assert replay.__globals__["load_migrations"] is migration_loader.load_migrations, (
        "stage 2 is a second loader, so a declared pin is verified twice and by two rules"
    )

    guards = "run_static_migration_guards"
    assert guards in _names_through_local_helpers(validate_command), (
        "migrate validate no longer runs the shared static guard set"
    )
    assert guards in _referenced_names(replay), "the replay no longer runs the shared guard set"
    assert validate_command.__globals__[guards] is replay.__globals__[guards], (
        "validate and the replay hold two definitions of one guard set"
    )

    shared = migration_engine.run_static_migration_guards
    assert validate_command.__globals__[guards] is shared, (
        "the CLI reaches a guard set that is not the application layer's"
    )
    assert {
        "refuse_unenforceable_scope",
        "refuse_duplicate_content_files",
        "refuse_alias_item_id_collision",
    } <= _referenced_names(shared), _referenced_names(shared)


# -- governed metadata (#249) ----------------------------------------------
#
# `propose` could express no migration-governed metadata beyond the label
# `--authored-here` implies, so a corpus produced by the shipped
# propose -> accept flow could only ever hold `trustLevel: unverified`,
# `sensitivity: internal` and no scope. Measured on the first dogfooding slice
# (2026-08-18): maintainer-merged ADRs that are public on GitHub were published
# as unverified and internal, next to `status: approved`, and both fields reach
# every retrieval result. A revision is immutable, so a field omitted at draft
# time costs a new revision and a duplicated body file to add later.
#
# These tests are about which values reach the *staged migration*, because that
# file is what a human reviews and the only thing `migrate apply` reads.

#: The four fields the new options write, spelled as the migration schema
#: spells them (`schemas/migrations/migration.schema.json`, `revisionMetadata`).
GOVERNED_FIELDS = ("trustLevel", "sensitivity", "scope", "labels")

#: Every metadata field today's generator writes for the `DRAFT` invocation, so
#: the compatibility pin below can state the whole set rather than four
#: absences. `labels` is not among them: it appears only under `--authored-here`.
DEFAULT_METADATA_FIELDS = frozenset(
    {"title", "contentType", "kind", "namespace", "status", "owner", "sourceAnchors"}
)


def _staged_metadata(root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """The ``upsertRevision`` metadata of the migration a draft staged.

    Read out of the file rather than out of the command's own payload: the
    migration is what a reviewer reads and what `migrate apply` applies, and a
    value that reached the JSON response without reaching the YAML would be the
    defect rather than the proof.
    """
    migration = root / payload["proposalDirectory"] / payload["migrationFile"]
    document = yaml.safe_load(migration.read_text(encoding="utf-8"))
    upsert = next(op for op in document["operations"] if op["op"] == "upsertRevision")
    metadata = upsert["metadata"]
    assert isinstance(metadata, dict)
    return metadata


def _published_revision(root: Path, revision_id: str) -> dict[str, Any]:
    """The governed columns ``migrate apply`` wrote for one revision.

    Read straight out of the state database. These columns are what every
    published result is built from, so a raw read cannot be satisfied by a
    caller-side default filling a field the migration never carried.
    """
    databases = sorted((root / ".theurian" / "state").glob("*.sqlite"))
    assert len(databases) == 1, databases
    with closing(sqlite3.connect(f"file:{databases[0]}?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT trust_level, sensitivity, labels, scope_paths FROM knowledge_revisions "
            "WHERE revision_id = ?",
            (revision_id,),
        ).fetchall()
    assert len(rows) == 1, rows
    return dict(rows[0])


def test_a_draft_stages_the_trust_level_and_sensitivity_it_was_given(project: Path) -> None:
    """#249: without these, an accurate value is unreachable from this surface.

    Both fields are optional to the schema and the loader applies its defaults
    when they are absent -- `unverified` and `internal`. That is the honest
    answer for an agent's unreviewed draft and the wrong one for knowledge a
    maintainer has already reviewed and published: the first dogfooding slice
    seeded three approved, publicly readable ADRs and the canonical store
    published `trustLevel: unverified` beside `status: approved` for all three.
    Hand-editing the generated YAML was the only remedy, which does not scale to
    a corpus and is not a documented flow.
    """
    code, payload = _draft(project, "--trust-level", "reviewed", "--sensitivity", "public")

    assert code == 0, payload
    metadata = _staged_metadata(project, payload)
    assert metadata["trustLevel"] == "reviewed"
    assert metadata["sensitivity"] == "public"


def test_repeated_scope_paths_are_staged_in_the_order_they_were_given(project: Path) -> None:
    """#249: `scope.paths` is what Milestone 8's drift detection will read.

    Repeatable rather than one comma-separated value: a glob may legitimately
    contain a comma, and a rule that governs `src/**` and `docs/adr/*.md`
    governs two patterns rather than one string. The order is asserted and not
    the set, because the schema's `paths` is an ordered array, the migration is
    reviewed as text, and a caller who lists the most-touched directory first
    must read it back first rather than wherever a set iteration puts it.
    """
    code, payload = _draft(project, "--scope-path", "src/**", "--scope-path", "docs/adr/*.md")

    assert code == 0, payload
    # The whole object, so a stray sibling key would fail here as well: the
    # schema declares `scope` with `additionalProperties: false`, and a
    # migration that carries one is refused after the pull request has merged.
    assert _staged_metadata(project, payload)["scope"] == {"paths": ["src/**", "docs/adr/*.md"]}


def test_repeated_labels_are_staged_in_the_order_they_were_given(project: Path) -> None:
    """#249: labels are how a corpus is grouped for anything the enums cannot say.

    Ordered for the same reason as `scope.paths`, and asserted as a list rather
    than a set so that a generator which sorts or dedupes silently is visible
    here rather than in a reviewer's diff.
    """
    code, payload = _draft(project, "--label", "retention", "--label", "pii")

    assert code == 0, payload
    assert _staged_metadata(project, payload)["labels"] == ["retention", "pii"]


def test_user_labels_and_the_authored_here_label_all_reach_one_revision(project: Path) -> None:
    """The merge case: `--authored-here` writes a label of its own (INV-8).

    `--authored-here` is not a synonym for `--label authored-in-theurian`: it is
    what satisfies INV-8 for knowledge with no external source, and dropping it
    when the caller also passes `--label` would make a schema-valid migration
    that `migrate apply` then refuses for having no source anchor -- after the
    pull request has merged. The other direction, dropping the caller's labels,
    reports success for a change nobody made.

    Placement is not asserted, only membership and the absence of duplicates:
    where the generator puts the implied label among the caller's is its own
    choice, and pinning it here would make a cosmetic change look like a defect.
    """
    code, payload = _draft(project, "--authored-here", "--label", "retention", "--label", "pii")

    assert code == 0, payload
    labels = _staged_metadata(project, payload)["labels"]
    assert sorted(labels) == ["authored-in-theurian", "pii", "retention"]
    assert len(labels) == len(set(labels)), labels
    # The caller's own two keep their relative order whatever else is in there.
    assert labels.index("retention") < labels.index("pii")


def test_a_label_that_repeats_the_authored_here_label_is_staged_once(project: Path) -> None:
    """A duplicate is not a cosmetic problem: the schema refuses the whole file.

    `revisionMetadata.labels` declares `uniqueItems: true`, so concatenating the
    implied label onto a caller's identical one produces a document the
    generator's own validation rejects -- turning a legal invocation into a
    refusal for a proposal that says exactly what the caller meant.
    """
    code, payload = _draft(
        project, "--authored-here", "--label", "authored-in-theurian", "--label", "retention"
    )

    assert code == 0, payload
    assert _staged_metadata(project, payload)["labels"].count("authored-in-theurian") == 1


@pytest.mark.parametrize(
    ("option", "value", "allowed"),
    [
        (
            "--trust-level",
            "trusted",
            ("unverified", "inferred", "reviewed", "authoritative"),
        ),
        (
            "--sensitivity",
            "secret",
            ("public", "internal", "confidential", "restricted"),
        ),
    ],
)
def test_a_governed_value_outside_the_schema_enum_is_refused_by_name(
    project: Path, option: str, value: str, allowed: tuple[str, ...]
) -> None:
    """The refusal has to name the four spellings, or it costs a turn to guess.

    `trusted` and `secret` are the plausible wrong words -- neither is in the
    schema's enum. Letting either through would produce a document the
    generator's own validation rejects, and the reader would be told which
    JSON Schema keyword failed rather than which words are allowed. This is the
    same failure `theurian propose` is shaped against everywhere else: an agent
    that has to re-invoke once per rejected guess spends a turn each time.

    Asserted on the *content* of the message rather than on the exit code
    alone, because an unknown option already exits 2 and names nothing.
    """
    result = runner.invoke(
        app,
        [
            *DRAFT,
            "--body-file",
            str(project / "body.md"),
            "--reasoning",
            REASONING,
            option,
            value,
            "--json",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == EXIT_INVALID_INPUT
    # Click's own parameter refusals print plain text even under `--json` (the
    # same asymmetry `test_a_draft_option_handed_to_accept_is_refused` records),
    # and it wraps to the terminal width, so the words are matched and not the
    # sentence.
    message = " ".join((result.stderr or result.stdout).split())
    assert value in message, message
    for spelling in allowed:
        assert spelling in message, message
    assert not [path for path in (project / ".theurian/proposals").iterdir() if path.is_dir()]


def test_a_new_draft_option_handed_to_accept_is_refused_rather_than_ignored(
    project: Path,
) -> None:
    """Every draft option is refused after a verb; a new one must be too.

    Click parses a group's options whatever follows them, so
    `theurian propose --trust-level reviewed accept <id>` parses and the trust
    level is dropped -- reporting success for a change nobody made. Two options
    already have that pinned above; this is the same class for the ones #249
    adds, and the check they must be added to (`_refuse_stray_options`) is a
    dictionary that a new option silently fails to appear in.
    """
    _, drafted = _draft(project)

    result = runner.invoke(
        app,
        ["propose", "--json", "--trust-level", "reviewed", "accept", drafted["proposalId"]],
        catch_exceptions=False,
    )

    # The exit code first: an option that is merely dropped leaves `accept`
    # reporting success on stdout, and parsing the empty stderr for a refusal
    # would report that as a decoding failure rather than as the silent drop.
    assert result.exit_code == EXIT_INVALID_INPUT, result.stdout
    payload = json.loads(result.stderr)
    assert "--trust-level" in payload["error"]
    assert payload["remedy"]


def test_a_draft_given_none_of_the_governed_options_stages_what_it_always_did(
    project: Path,
) -> None:
    """The compatibility pin. This one passes today and must keep passing.

    Omitting the options must change nothing: the four fields stay *absent* from
    the YAML rather than being written at their schema defaults. Absent and
    `trustLevel: unverified` load identically -- the loader applies the default
    -- but they do not read identically to a human, and writing an unasked-for
    `unverified` into every generated migration would state a judgement the
    caller never made. Reviewed as text is the whole point of ADR-0013's format.

    The set is asserted rather than the bytes because this file drives the real
    CLI: the ids and the timestamp differ on every run, so a byte-for-byte pin
    would need a frozen clock this surface does not take. The service tests own
    that granularity.
    """
    code, payload = _draft(project)

    assert code == 0, payload
    metadata = _staged_metadata(project, payload)
    present = [field for field in GOVERNED_FIELDS if field in metadata]
    assert present == [], f"a default draft grew {present}"
    assert set(metadata) == DEFAULT_METADATA_FIELDS


def test_an_update_stages_the_governed_metadata_it_was_given(project: Path) -> None:
    """#249 applies to both flows, or an already-seeded corpus stays wrong.

    An update is the flow that *corrects* a false `trustLevel` on knowledge
    already in the set -- the dogfooding slice's own remedy -- so options that
    worked only on a new item would leave every seeded revision uncorrectable
    except by hand-editing YAML, which is the state #249 reports.

    Accepting the first proposal is what makes the item exist in the approved
    migration set, which is where the draft guard reads the current revision
    from; without it this second draft is refused for naming a revision that
    does not exist (#210). `expectedRevision` is asserted so a fixture that
    stopped reaching the update branch fails here rather than passing silently.
    """
    _, first = _draft(project)
    accept_code, accepted = _invoke("propose", "accept", first["proposalId"])
    assert accept_code == 0, accepted

    code, payload = _draft(
        project,
        "--expected-revision",
        first["revisionId"],
        "--trust-level",
        "authoritative",
        "--sensitivity",
        "confidential",
        "--scope-path",
        "src/api/**",
        "--label",
        "retention",
    )

    assert code == 0, payload
    assert payload["expectedRevision"] == first["revisionId"], "this must be an update"
    metadata = _staged_metadata(project, payload)
    assert metadata["trustLevel"] == "authoritative"
    assert metadata["sensitivity"] == "confidential"
    assert metadata["scope"] == {"paths": ["src/api/**"]}
    assert metadata["labels"] == ["retention"]


def test_a_draft_that_omits_trust_and_sensitivity_surfaces_their_defaults(project: Path) -> None:
    """#249's surfacing: an omitted governed field must not default in silence.

    The migration keeps the two keys absent -- writing `unverified`/`internal`
    into every draft would state a judgement nobody made, and the compatibility
    pin above holds that line -- but absent is exactly where the false positive
    hides: the loader fills the defaults and every result publishes them. So the
    draft *names*, in its machine-readable next steps, which fields defaulted, to
    what, and the option that sets each. This is the whole reason #249 is a fix
    and not only an enhancement.
    """
    code, payload = _draft(project)

    assert code == 0, payload
    steps = " ".join(payload["nextSteps"])
    assert "trustLevel" in steps and "unverified" in steps and "--trust-level" in steps, steps
    assert "sensitivity" in steps and "internal" in steps and "--sensitivity" in steps, steps


def test_a_draft_that_sets_trust_and_sensitivity_surfaces_no_default(project: Path) -> None:
    """Supplied, there is no default to warn about, so the note is gone.

    Neither `unverified` nor `internal` appears anywhere in the next steps: the
    other steps never name a governed default, so their absence is an unambiguous
    signal that the warning was suppressed rather than merely reworded.
    """
    code, payload = _draft(project, "--trust-level", "reviewed", "--sensitivity", "public")

    assert code == 0, payload
    steps = " ".join(payload["nextSteps"])
    assert "unverified" not in steps, steps
    assert "internal" not in steps, steps


def test_governed_metadata_survives_acceptance_and_reaches_the_derived_store(
    project: Path,
) -> None:
    """#249 as it was found: what the store holds is what a caller is told.

    The staged YAML is checked above; this is the far end of the pipe. `migrate
    apply` writes these four into `knowledge_revisions`, and `trustLevel` and
    `sensitivity` are published on every retrieval result out of exactly those
    columns. A generator that wrote a shape the loader does not read --
    `scopePaths` beside `scope`, say -- satisfies every assertion above and
    still publishes the defaults, which is the failure #249 describes.
    """
    _, drafted = _draft(
        project,
        "--trust-level",
        "reviewed",
        "--sensitivity",
        "public",
        "--scope-path",
        "src/**",
        "--label",
        "retention",
    )
    accept_code, accepted = _invoke("propose", "accept", drafted["proposalId"])
    assert accept_code == 0, accepted
    apply_code, applied = _invoke("migrate", "apply")
    assert apply_code == 0, applied

    published = _published_revision(project, drafted["revisionId"])

    assert published["trust_level"] == "reviewed"
    assert published["sensitivity"] == "public"
    assert json.loads(published["scope_paths"]) == ["src/**"]
    assert json.loads(published["labels"]) == ["retention"]


# -- the governed-defaults note (#249) -------------------------------------
#
# The note the draft surfaces when a governed field is omitted is the whole
# reason #249 is a fix and not only an enhancement: the migration is left
# without the keys (the compatibility pin holds that), so the *only* thing that
# tells a caller a reviewed, public ADR is about to publish `unverified`/
# `internal` is this next step. The adversarial reviewer found the round-1
# surfacing tests could not fail -- five mutations survived them -- because they
# asserted the *absence* of two strings rather than the shape of the note. The
# tests below pin the note positively.


def _governed_note(payload: dict[str, Any]) -> str:
    """The single governed-defaults next step, selected by its signature.

    Found by the phrase `will publish` -- which no other step carries -- rather
    than by position, so a test that reads the note's *content* does not also
    silently pin its *place*. The ordering has its own test. Asserting exactly
    one match is what makes a malformed empty note (mutation ``if False``) visible
    here: it still carries the signature, so the count, not a substring, catches it.
    """
    notes: list[str] = [step for step in payload["nextSteps"] if "will publish" in step]
    assert len(notes) == 1, payload["nextSteps"]
    return notes[0]


def test_the_surfaced_default_equals_the_value_the_store_will_publish(project: Path) -> None:
    """MEDIUM-1: the warned default and the published default are one value.

    The shared ``DEFAULT_TRUST_LEVEL``/``DEFAULT_SENSITIVITY`` constants close the
    original divergence structurally; this guards against re-divergence. It reads
    the default the note *names* and the column ``migrate apply`` actually *wrote*,
    both at runtime, and asserts they agree -- so reintroducing a literal in either
    the note or the loader that drifts from the shared constant fails here. A note
    that promised ``unverified`` while the store published something else would be
    the exact false reassurance #249 exists to remove.
    """
    code, drafted = _draft(project)
    assert code == 0, drafted

    note = _governed_note(drafted)
    surfaced_trust = re.search(r"trustLevel:\s*(\S+)", note)
    surfaced_sensitivity = re.search(r"sensitivity:\s*(\S+)", note)
    assert surfaced_trust and surfaced_sensitivity, note

    accept_code, accepted = _invoke("propose", "accept", drafted["proposalId"])
    assert accept_code == 0, accepted
    apply_code, applied = _invoke("migrate", "apply")
    assert apply_code == 0, applied

    published = _published_revision(project, drafted["revisionId"])
    assert surfaced_trust.group(1) == published["trust_level"], note
    assert surfaced_sensitivity.group(1) == published["sensitivity"], note


def test_a_draft_that_sets_both_governed_fields_carries_no_note_at_all(project: Path) -> None:
    """MEDIUM-2: supplied both, the note is *absent*, not merely string-free.

    The round-1 test asserted only that ``unverified``/``internal`` do not appear,
    which the mutation ``if not omitted: return None`` -> ``if False: return None``
    survived: with both fields given it builds an empty, malformed note ("`` was
    not set, so this revision will publish  -- the schema default -- ...`") that
    names neither default, so the old test stayed green while the response grew a
    broken step. Pinned positively here: the next steps are exactly the baseline
    list, first step included, and no step carries the note's signature phrases.
    """
    code, payload = _draft(project, "--trust-level", "reviewed", "--sensitivity", "public")

    assert code == 0, payload
    steps = payload["nextSteps"]
    assert len(steps) == len(_DRAFT_STEPS), steps
    assert steps[0] == _DRAFT_STEPS[0], steps
    for step in steps:
        assert "will publish" not in step, step
        assert "schema default" not in step, step


def test_a_draft_that_sets_only_trust_surfaces_only_the_sensitivity_default(project: Path) -> None:
    """MEDIUM-3: one field omitted names that field alone, in the singular.

    ``--trust-level`` supplied and ``--sensitivity`` omitted: the note must warn
    about ``sensitivity``/``internal``/``--sensitivity`` and must not mention the
    field the caller did set. ``was not set`` is asserted verbatim so the singular
    grammar branch is exercised -- it goes RED under both ``len(omitted) > 1`` ->
    ``> 0`` (which makes ``plural`` true for one field) and the ``'was'`` -> ``'were'``
    literal flip, either of which would have this single-field note read "were".
    """
    code, payload = _draft(project, "--trust-level", "reviewed")

    assert code == 0, payload
    note = _governed_note(payload)
    assert "sensitivity" in note, note
    assert "internal" in note, note
    assert "--sensitivity" in note, note
    assert "was not set" in note, note
    assert "trustLevel" not in note, note
    assert "unverified" not in note, note
    assert "--trust-level" not in note, note


def test_a_draft_that_sets_only_sensitivity_surfaces_only_the_trust_default(project: Path) -> None:
    """MEDIUM-3, the mirror: ``--sensitivity`` set, ``--trust-level`` omitted.

    The note warns about ``trustLevel``/``unverified``/``--trust-level`` and names
    nothing about the sensitivity the caller set. ``was not set`` verbatim kills the
    same two mutations as its sibling above by pinning the singular grammar the note
    uses when exactly one field is missing.
    """
    code, payload = _draft(project, "--sensitivity", "public")

    assert code == 0, payload
    note = _governed_note(payload)
    assert "trustLevel" in note, note
    assert "unverified" in note, note
    assert "--trust-level" in note, note
    assert "was not set" in note, note
    assert "sensitivity" not in note, note
    assert "internal" not in note, note
    assert "--sensitivity" not in note, note


def test_the_governed_defaults_note_is_the_first_next_step(project: Path) -> None:
    """LOW: the warning leads, so a caller reading top-down meets it first.

    When owed, the note is prepended (``[note, *steps]``). Pinned at index 0 so the
    mutation ``[*steps, note]`` -- which buries the warning under three procedural
    steps a hurried caller may not reach -- goes RED here.
    """
    code, payload = _draft(project)

    assert code == 0, payload
    assert "will publish" in payload["nextSteps"][0], payload["nextSteps"]


# -- blank / invalid governed input, refused at draft (#249) ---------------


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_scope_path_is_refused_and_stages_nothing(project: Path, blank: str) -> None:
    """LOW: a scope glob that matches nothing is an authoring slip, not a rule.

    ``revisionMetadata.scope.paths`` items carry no ``minLength``, so ``""`` or a
    whitespace-only value would stage a glob that can never apply and reads in
    review as a mistake. Refused by name, and -- the half a bare exit code would
    miss -- no proposal directory is left behind.
    """
    code, payload = _draft(project, "--scope-path", blank)

    assert code == EXIT_INVALID_INPUT, payload
    assert "--scope-path" in payload["error"], payload
    assert payload["remedy"]
    assert not [p for p in (project / ".theurian/proposals").iterdir() if p.is_dir()]


def test_an_empty_label_is_refused_and_stages_nothing(project: Path) -> None:
    """LOW: an empty label groups by nothing; refuse it before it stages."""
    code, payload = _draft(project, "--label", "")

    assert code == EXIT_INVALID_INPUT, payload
    assert "--label" in payload["error"], payload
    assert payload["remedy"]
    assert not [p for p in (project / ".theurian/proposals").iterdir() if p.is_dir()]


def test_a_label_with_a_control_character_is_refused_by_name(project: Path) -> None:
    """LOW: a newline in a label corrupts the reviewed migration text.

    ``labels.items`` has no ``pattern`` forbidding control characters the way
    ``title``/``owner`` do, so a newline would split the label across YAML lines
    and a NUL would truncate it. The refusal names the cause -- a control
    character -- rather than exiting on an opaque code, and stages nothing.
    """
    code, payload = _draft(project, "--label", "a\nb")

    assert code == EXIT_INVALID_INPUT, payload
    assert "control character" in payload["error"], payload
    assert payload["remedy"]
    assert not [p for p in (project / ".theurian/proposals").iterdir() if p.is_dir()]


def test_a_label_bearing_a_space_is_allowed(project: Path) -> None:
    """LOW: the guard forbids control characters, not printable whitespace.

    A space (U+0020) sits exactly on the C0 ceiling and is legitimate inside a
    label, so it must stage unchanged. This pins the guard to control characters
    rather than to "any whitespace": a mutation that broadened it to ``<=`` the
    ceiling would refuse ``a b`` and fail here, where the refusal tests could not
    see the over-block.
    """
    code, payload = _draft(project, "--label", "a b")

    assert code == 0, payload
    assert _staged_metadata(project, payload)["labels"] == ["a b"]


@pytest.mark.parametrize(("option", "value"), [("--label", "x"), ("--scope-path", "src/**")])
def test_a_list_valued_draft_option_handed_to_accept_is_refused(
    project: Path, option: str, value: str
) -> None:
    """LOW: the repeatable options are stray after a verb too, not just the scalars.

    Click parses a group's options whatever follows them, so
    ``theurian propose --label x accept <id>`` parses and drops the label --
    reporting success for a change nobody made. The existing stray test pins only
    the scalar ``--trust-level``; ``--scope-path`` and ``--label`` arrive as
    ``None``/list rather than a scalar default, so a stray check that tested them
    wrongly could silently drop them. Pinned here for both.
    """
    _, drafted = _draft(project)

    result = runner.invoke(
        app,
        ["propose", "--json", option, value, "accept", drafted["proposalId"]],
        catch_exceptions=False,
    )

    assert result.exit_code == EXIT_INVALID_INPUT, result.stdout
    payload = json.loads(result.stderr)
    assert option in payload["error"], payload
    assert payload["remedy"]


# -- the SEC-11 secret scan's output surface (#198, ADR-0027 decision 3) ----


#: A body carrying a credential-shaped string, derived rather than drawn.
#:
#: The seed is hashed at run time so no credential-shaped literal exists in this
#: file: gitleaks runs its default ruleset over this repository's whole history,
#: and an allowlist keyed on such a literal is a place a real credential can
#: hide. ``test_content_secrets.py`` records the same reasoning for its own
#: fixture, and the 0.065% digit-free draw rate that rules out a fresh
#: ``token_urlsafe``.
def _leaky_body() -> str:
    import base64
    import hashlib

    token = (
        base64.urlsafe_b64encode(hashlib.sha256(b"theurian propose-cli secret fixture").digest())
        .decode()
        .rstrip("=")
    )
    return f"# Retry policy\n\nThree attempts.\n\n    THEURIAN_MCP_TOKEN={token}\n"


def _warned_accept(project: Path) -> tuple[Any, dict[str, Any]]:
    """Draft a leaky proposal under ``warn`` and accept it, returning both streams."""
    (project / ".theurian" / "config.yaml").write_text(
        "security:\n  secretScan: warn\n", encoding="utf-8"
    )
    (project / "body.md").write_text(_leaky_body(), encoding="utf-8")
    _, drafted = _draft(project)

    rendered = runner.invoke(
        app, ["propose", "accept", drafted["proposalId"]], catch_exceptions=False
    )
    assert rendered.exit_code == 0, rendered.stdout + rendered.stderr
    return rendered, drafted


def test_a_warned_acceptance_renders_a_finding_a_person_can_act_on(project: Path) -> None:
    """``warn``'s whole contract is that somebody reads it, so it has to be readable.

    Measured against the real CLI before this test existed: publishing the
    finding as a *mapping* -- the shape ``ingest --json`` uses for its
    ``warnings`` -- reached a terminal as
    ``{'body': 'architecture/...', 'family': 'high-entropy-token', ...}``,
    because ``_render`` prints a list entry through ``escape_terminal_controls``,
    which stringifies a mapping with ``repr``. Tolerable for a report nobody has
    to act on; not tolerable for the one output whose purpose is that a person
    looks at the body before opening a pull request.

    So the line is pinned in the shape every compiler and linter emits, and the
    absence of a ``repr`` is pinned with it -- a mapping would satisfy "contains
    the family" and fail here.
    """
    rendered, drafted = _warned_accept(project)

    line = next(
        stripped
        for raw in rendered.stdout.splitlines()
        if (stripped := raw.strip()).startswith("- architecture/")
    )
    assert re.fullmatch(
        rf"- architecture/retry-policy\.{drafted['revisionId']}\.md:5:24: "
        rf"high-entropy-token \(\S{{4}}\.\.\.\)",
        line,
    ), f"the finding does not render as `<body>:<line>:<column>: <family> (<prefix>)`: {line!r}"
    assert "{'body'" not in rendered.stdout, (
        f"a finding reaches the terminal as a Python mapping repr:\n{rendered.stdout}"
    )


def test_a_warned_acceptance_publishes_the_policy_beside_the_findings(project: Path) -> None:
    """An empty finding list means two different things, and the policy is what tells them apart.

    Under ``warn`` an empty list says the bodies were scanned and are clean;
    under ``off`` it says nothing was scanned at all. A caller that saw only the
    list would read the second as the first -- which is the more dangerous
    direction, since ``off`` is what a project sets after a false positive and
    then forgets.

    Both keys are part of the shape rather than fields that appear on trouble: a
    key a caller only ever sees when something is wrong is a key callers learn
    not to read.
    """
    (project / ".theurian" / "config.yaml").write_text(
        "security:\n  secretScan: warn\n", encoding="utf-8"
    )
    (project / "body.md").write_text(_leaky_body(), encoding="utf-8")
    _, drafted = _draft(project)

    code, payload = _invoke("propose", "accept", drafted["proposalId"])

    assert code == 0, payload
    assert payload["secretScanPolicy"] == "warn"
    assert len(payload["secretFindings"]) == 1, payload
    assert "high-entropy-token" in payload["secretFindings"][0]
    # The report is a locator, never a second copy of what it reports.
    assert _leaky_body().split("=")[-1].strip() not in json.dumps(payload), (
        "the accept payload reproduces the token it is warning about"
    )


def test_a_clean_acceptance_still_publishes_an_empty_finding_list(project: Path) -> None:
    """The ordinary run, which is where a "only on trouble" key would go unnoticed."""
    _, drafted = _draft(project)

    code, payload = _invoke("propose", "accept", drafted["proposalId"])

    assert code == 0, payload
    assert payload["secretScanPolicy"] == "block", "no config file must select block"
    assert payload["secretFindings"] == []
