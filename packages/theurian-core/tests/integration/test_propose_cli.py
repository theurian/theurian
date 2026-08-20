"""``theurian propose`` and ``theurian propose accept``, invoked in-process.

The service tests own the packaging rules. These own the adapter: that the
option surface reaches them, that a refusal arrives as JSON with a remedy rather
than as a traceback, and -- the one thing only an end-to-end run can say -- that
a proposal drafted here, accepted here, and then handed to the *existing*
migration commands actually applies.
"""

from __future__ import annotations

import json
import re
import sqlite3
import subprocess
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from theurian.cli.main import app
from theurian.cli.propose_commands import _DRAFT_STEPS

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
    assert "already been accepted" in payload["error"]
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


def test_accept_reports_an_unknown_proposal_with_a_remedy(project: Path) -> None:
    code, payload = _invoke("propose", "accept", "01K9C7VN4TQZB2M8XR5HD3JFEW")

    assert code == 1
    assert payload["remedy"]


def test_accept_refuses_an_id_that_is_not_a_ulid(project: Path) -> None:
    """A proposal id names a directory, so it is bounded before it is joined."""
    code, payload = _invoke("propose", "accept", "../../etc")

    assert code == EXIT_INVALID_INPUT
    assert payload["remedy"]


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
