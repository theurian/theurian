"""``evidence.json`` on the accept-path secret scan (#361, SEC-11, T-15).

``accept`` scans everything it *lands*, and the recorded reason ``evidence.json``
was outside that population -- *accept moves neither the file nor the proposal
directory, so neither is an artifact this scan can be about* -- is true about
moving and does not follow. Three facts of this command put the file into Git
history without moving it: ``_remove_proposal_sources`` leaves it behind,
``_ACCEPT_STEPS[0]`` tells the author to open a pull request with the proposal
directory in it because the merge is the approval, and ``.theurian/proposals/``
is not git-ignored.

Measured on ``63e3851``: a proposal whose ``evidence.reasoning`` carries a
detectable token is accepted under the default ``block`` with ``findings=0``, and
the token-bearing file is still on disk in the directory the command has just
told the author to commit.

**This is an approval gate, and its posture is a refusal.** ``index build``'s
scan of the served corpus (#329) *signals* -- the content is already approved and
in the tree, so refusing would leave a project unable to build an index over what
it has already merged. ``accept`` is the last point before a human merges, so a
finding under ``block`` refuses and consumes nothing. The tests below pin that
difference where it is observable: the refusal arrives before any next step is
printed.

Its own module rather than an addition to ``test_proposal_secret_scan.py``,
which is already 3,500 lines; the fixtures are that module's, copied for the
reason it records.
"""

from __future__ import annotations

import base64
import hashlib
import json
import subprocess
from collections.abc import Collection, Iterator
from pathlib import Path
from typing import Final

import pytest
from fakes.clock import FrozenClock
from fakes.ids import SeededIdGenerator
from typer.testing import CliRunner

from theurian.application.project_service import ProjectPaths, initialize_project
from theurian.application.proposal_service import (
    _AT_EVIDENCE,
    EVIDENCE_FILE,
    DraftedProposal,
    ProposalError,
    ProposalRequest,
    ProposalService,
)
from theurian.cli.main import app
from theurian.cli.migration_pipeline import rehearse_migration_set
from theurian.cli.propose_commands import (
    _ACCEPT_STEPS,
    _ROTATE_ADVICE_STEP,
    _SKIPPED_CHANNEL_STEP,
    _accept_steps,
)
from theurian.domain.enums import KnowledgeKind
from theurian.domain.identifiers import AgentId, ItemId, MigrationId, ProjectId, RevisionId, TaskId
from theurian.domain.knowledge import SourceAnchor
from theurian.domain.migration import Migration, current_revision_in
from theurian.domain.project import DEFAULT_KNOWLEDGE_DIRECTORY
from theurian.domain.proposal import Evidence
from theurian.domain.values import MARKDOWN
from theurian.infrastructure.filesystem.migration_loader import (
    load_migrations,
    validate_migration_document,
)
from theurian.security.content_secrets import HIGH_ENTROPY, MAX_FINDINGS, scan_text
from theurian.security.project_config import SecretScanPolicy

pytestmark = pytest.mark.integration

SCHEMAS = Path(__file__).resolve().parents[4] / "schemas"

ANCHOR = SourceAnchor(
    provider="git",
    source_uri="https://github.com/acme/api/commit/0123456789abcdef",
    commit_sha="0123456789abcdef",
)

EVIDENCE = Evidence(
    agent_id=AgentId("claude-code"),
    task_id=TaskId("task-7"),
    model="claude-opus-5",
    reasoning="The review thread on #41 settled the retry budget at three attempts.",
    anchors=(ANCHOR,),
)

CLEAN_BODY: Final = "# Retry policy\n\nThree attempts, then fail loudly.\n"

#: Derived rather than drawn, for the reason ``test_proposal_secret_scan.py``'s
#: ``PLANTED_TOKEN`` records: a fresh ``token_urlsafe`` carries no digit in
#: 0.065% of draws, so a drawn fixture reddens the suite about once in 1,500 runs
#: for its own luck. Split from its seed so no credential-shaped literal exists
#: in the file.
PLANTED_TOKEN: Final = (
    base64.urlsafe_b64encode(hashlib.sha256(b"theurian evidence-scan fixture (#361)").digest())
    .decode()
    .rstrip("=")
)


@pytest.fixture
def paths(tmp_path: Path) -> Iterator[ProjectPaths]:
    root = tmp_path / "demo"
    root.mkdir()
    project = ProjectPaths(root=root, knowledge_dir=root / DEFAULT_KNOWLEDGE_DIRECTORY)
    initialize_project(project)
    yield project


@pytest.fixture
def service(paths: ProjectPaths) -> ProposalService:
    def current_revision(item_id: ItemId) -> RevisionId | None:
        loaded = load_migrations(paths.root, paths.migrations, SCHEMAS)
        return current_revision_in(loaded.migration_set, item_id)

    def landed_migration(migration_id: MigrationId) -> Migration | None:
        loaded = load_migrations(paths.root, paths.migrations, SCHEMAS)
        return loaded.migration_set.get(migration_id)

    def landed_migrations() -> Collection[Migration]:
        loaded = load_migrations(paths.root, paths.migrations, SCHEMAS)
        return loaded.migration_set

    return ProposalService(
        paths=paths,
        project_id=ProjectId("demo"),
        clock=FrozenClock(),
        ids=SeededIdGenerator(),
        validate=lambda document: validate_migration_document(document, SCHEMAS),
        current_revision=current_revision,
        landed_migration=landed_migration,
        landed_migrations=landed_migrations,
        rehearse=lambda candidate: rehearse_migration_set(candidate, clock=FrozenClock()),
    )


def _request(body: str = CLEAN_BODY) -> ProposalRequest:
    return ProposalRequest(
        item_id=ItemId("architecture.retry-policy"),
        title="Retry policy",
        kind=KnowledgeKind.ARCHITECTURE,
        owner="platform-team",
        author="platform-team@example.com",
        description="Record the retry budget the API review settled on.",
        body=body,
        content_type=MARKDOWN,
        evidence=EVIDENCE,
        source_anchors=(ANCHOR,),
    )


def _required(value: str | None, why: str) -> str:
    """``value``, refusing ``None`` -- an optional fixture field this test needs set."""
    assert value is not None, why
    return value


def _configure(paths: ProjectPaths, policy: str) -> None:
    """Write a config file selecting ``policy``, quoted so YAML keeps it a string.

    Quoted because a bare ``off`` is the boolean false under YAML 1.1, which
    ``test_project_config.py`` pins: written unquoted, the ``off`` case here would
    exercise the refusal for a malformed key and still look green.
    """
    paths.config.write_text(f'security:\n  secretScan: "{policy}"\n', encoding="utf-8")


def _with_a_secret_in_the_reasoning(
    service: ProposalService, *, local: bool = False, body: str = CLEAN_BODY
) -> DraftedProposal:
    """The ordinary proposal, with a credential pasted into ``evidence.reasoning``.

    Written after the draft rather than passed through :class:`Evidence`, because
    ``reasoning`` reaches the *evidence file* and nothing else: it is not a
    migration field, so a plant made at request time would still be testing this
    channel and nothing would be gained by the indirection. The body and the
    migration stay clean on purpose -- a proposal that leaked through both would
    be refused by the channels that already existed, and every test here would
    pass with the evidence channel absent.

    ``local`` drafts into the git-ignored ``.theurian/proposals-local/`` instead
    (ADR-0028), which is the case the scan's own docstring argues at length and
    which no test held: the skip mutation -- returning before the yield when
    ``location.local`` -- survived the suite.
    """
    drafted = service.draft(_request(body), local=local)
    evidence = drafted.directory / EVIDENCE_FILE
    document = json.loads(evidence.read_text(encoding="utf-8"))
    document["reasoning"] = f"Verified against staging with THEURIAN_TOKEN={PLANTED_TOKEN}"
    evidence.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return drafted


def test_the_planted_value_is_one_the_detector_reports() -> None:
    """The positive control every refusal test here rests on.

    A plant the detector does not report is withheld by nothing at all, and a
    test asserting that the accept path refuses it would be green against a build
    with no evidence channel in it. The second half of the control -- that the
    plant reaches the *evidence file only*, so the channels that already existed
    are not what refuses -- belongs to the refusal test itself and is asserted
    there, against that run's own migration file.
    """
    families = [finding.family for finding in scan_text(PLANTED_TOKEN)]
    assert families == [HIGH_ENTROPY], (
        f"the detector reports {families or 'nothing'} for the plant, so a refusal test built "
        f"on it would be testing nothing"
    )


def test_a_secret_in_the_evidence_reasoning_is_refused_under_the_default(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """No configuration file at all: ``block`` is what an absent key selects.

    Reproduced on ``63e3851``: the same proposal was accepted with
    ``secretFindings: []``, and the token-bearing ``evidence.json`` was left in
    the directory the command's own first next step tells the author to commit.
    """
    assert not paths.config.exists(), "the fixture wrote a config file; the default is untested"
    drafted = _with_a_secret_in_the_reasoning(service)
    assert PLANTED_TOKEN not in drafted.migration_file.read_text(encoding="utf-8"), (
        "the plant reached the migration, so the field and byte channels would refuse it and "
        "this test would pass with the evidence channel absent"
    )

    with pytest.raises(ProposalError) as caught:
        service.accept(drafted.proposal_id)

    published = f"{caught.value}\n{caught.value.remedy}"
    assert _AT_EVIDENCE in published, f"the refusal does not name the evidence channel: {published}"
    assert HIGH_ENTROPY in published, f"the refusal does not name what matched: {published}"
    assert PLANTED_TOKEN not in published, f"the refusal quoted the credential: {published}"
    assert "rotate" in published, "the remedy does not say to rotate first"


def test_the_refusal_consumes_nothing_and_the_evidence_is_still_there(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """A refusal leaves the proposal correctable, which is ADR-0027 decision 2.

    The evidence file in particular has to survive: the author's next move is to
    take the credential out of it, and a scan that deleted what it refused would
    make the remedy impossible to follow.
    """
    drafted = _with_a_secret_in_the_reasoning(service)

    with pytest.raises(ProposalError):
        service.accept(drafted.proposal_id)

    assert drafted.migration_file.exists(), "the refusal consumed the migration"
    assert drafted.body_file.exists(), "the refusal consumed the body"
    assert (drafted.directory / EVIDENCE_FILE).exists(), "the refusal consumed the evidence"
    assert not list(paths.migrations.glob("*.yaml")), "the refusal landed a migration"


def test_warn_accepts_and_reports_the_evidence_finding_with_the_value_withheld(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """``warn`` is the posture a project picks for false positives, not for silence.

    The acceptance succeeds and the finding rides on the result, located by the
    channel's fixed literal and quoting at most four characters of the match --
    the same discipline every other channel holds.
    """
    _configure(paths, "warn")
    drafted = _with_a_secret_in_the_reasoning(service)

    accepted = service.accept(drafted.proposal_id)

    assert accepted.secret_scan.policy is SecretScanPolicy.WARN
    rendered = "\n".join(finding.describe() for finding in accepted.secret_scan.findings)
    assert _AT_EVIDENCE in rendered, f"no finding names the evidence channel: {rendered}"
    assert PLANTED_TOKEN not in rendered, f"the finding quoted the credential: {rendered}"


@pytest.mark.parametrize("local", [False, True], ids=["tracked", "local"])
def test_the_rotate_step_asserts_no_destination_for_an_evidence_only_finding(
    service: ProposalService, paths: ProjectPaths, local: bool
) -> None:
    """The step a ``warn`` acceptance prints must hold for the channel it is about.

    ``_ROTATE_ADVICE_STEP`` used to say the flagged value was "in the working
    tree, and in Git history once this is committed". That is true of the five
    channels an acceptance *lands* and false of the sixth: nothing lands the
    evidence record, and under ``--local`` it enters no commit at all -- so the
    step asserted a commit that :data:`_LOCAL_ACCEPT_FIRST_STEP`, printed
    directly below it, says will not happen. The service-side sibling was
    rewritten when the sixth channel landed; this one was missed.

    Both proposal locations, because the contradiction is only visible in one of
    them and the correction has to hold in both. The finding is evidence-only by
    construction -- the body and the migration are clean -- so a step that
    happens to be true about a landed body cannot stand in for it.
    """
    _configure(paths, "warn")
    drafted = _with_a_secret_in_the_reasoning(service, local=local)

    accepted = service.accept(drafted.proposal_id)

    locations = {finding.location for finding in accepted.secret_scan.findings}
    assert locations == {_AT_EVIDENCE}, (
        f"the finding is not evidence-only ({locations}), so this test would pass on a step "
        f"that is true about some other channel"
    )
    steps = _accept_steps(accepted)
    assert steps[0] is _ROTATE_ADVICE_STEP, "the rotate step is not the first thing said"
    for claim in ("Git history", "committed", "landed"):
        assert claim not in _ROTATE_ADVICE_STEP, (
            f"the rotate step still asserts {claim!r}, which is false for the evidence channel "
            f"and, under --local, contradicts the step printed directly below it: {steps[1]}"
        )
    assert "rotate" in _ROTATE_ADVICE_STEP, "the step no longer says the one thing it is for"


def test_a_local_proposals_evidence_record_is_scanned_the_same_way(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """The ``--local`` case the scan argues at length and no test held (round 1, M-5).

    ``_evidence_text``'s docstring devotes a paragraph to why a ``--local``
    proposal is scanned even though its record travels nowhere -- git-ignored
    keeps the bytes out of a *commit* and not off the disk, which is why
    ``_secret_refusal``'s rotate advice is already unconditional. Nothing
    exercised it: the skip mutation, returning early when ``location.local``,
    survived the suite with every other test green. Two reviewers measured that
    independently.

    The locality is asserted rather than assumed, because a fixture that quietly
    drafted into the tracked directory would make this test a duplicate of the
    one above while reading as the local case.
    """
    drafted = _with_a_secret_in_the_reasoning(service, local=True)
    assert drafted.directory.parent == paths.proposals_local, (
        f"the fixture drafted into {drafted.directory.parent}, not the git-ignored location, so "
        f"this is the tracked case under another name"
    )

    with pytest.raises(ProposalError) as caught:
        service.accept(drafted.proposal_id)

    published = f"{caught.value}\n{caught.value.remedy}"
    assert _AT_EVIDENCE in published, f"the local record was not scanned: {published}"
    assert PLANTED_TOKEN not in published, f"the refusal quoted the credential: {published}"
    assert "proposals-local" in published, "the remedy sends the author to the wrong directory"


def test_off_scans_the_evidence_record_no_more_than_anything_else(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """``off`` means off, and the evidence channel does not get an exception to it.

    The policy is read before any input is touched, which is what makes ``off``
    the one move a project with a false positive has. A channel that scanned
    regardless would take that move away.
    """
    _configure(paths, "off")
    drafted = _with_a_secret_in_the_reasoning(service)

    accepted = service.accept(drafted.proposal_id)

    assert accepted.secret_scan.policy is SecretScanPolicy.OFF
    assert accepted.secret_scan.findings == (), "off scanned something"


def test_an_unreadable_evidence_record_is_refused_under_block(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """A channel ``block`` cannot read is a channel it cannot clear.

    Without this the control has a one-line bypass: make ``evidence.json`` a
    symlink and the scan skips it, while Git commits the link and a human reading
    the pull request follows it to the credential. The refusal has to arrive as a
    ``ProposalError`` with a remedy rather than as a raw ``OSError``, because
    ``--json`` publishes ``{error, remedy}`` and nothing else (CP-2, #227).
    """
    drafted = service.draft(_request())
    evidence = drafted.directory / EVIDENCE_FILE
    evidence.unlink()
    evidence.symlink_to(paths.root.parent / "elsewhere.json")

    with pytest.raises(ProposalError) as caught:
        service.accept(drafted.proposal_id)

    published = f"{caught.value}\n{caught.value.remedy}"
    assert EVIDENCE_FILE in published, f"the refusal does not name the file: {published}"
    assert "symlink" in published, f"the refusal does not say what is wrong: {published}"
    assert "secretScan" in published, "the remedy does not name the key that selects the policy"
    assert str(paths.root) not in published, "the refusal published an absolute path"


def test_an_unreadable_evidence_record_does_not_stop_a_warn_acceptance(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """``warn`` proceeds past a finding, so it proceeds past a channel it cannot read.

    The asymmetry with the test above is the whole of the policy's meaning:
    ``block`` promises nothing it cannot clear gets past, and ``warn`` promises
    only to tell you. Refusing here would make ``warn`` stop an acceptance, which
    is the one thing it does not do.
    """
    _configure(paths, "warn")
    drafted = service.draft(_request())
    evidence = drafted.directory / EVIDENCE_FILE
    evidence.unlink()
    evidence.symlink_to(paths.root.parent / "elsewhere.json")

    accepted = service.accept(drafted.proposal_id)

    assert accepted.secret_scan.findings == (), "an unreadable record produced a finding"


def test_warn_says_which_channel_it_could_not_read_instead_of_looking_clean(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """An unreadable channel under ``warn`` is reported, not silently stepped over.

    ``warn`` proceeds past a record it cannot read, which is right -- ``warn``
    never stops an acceptance. What was wrong is that the result of proceeding
    was indistinguishable from a clean scan: no finding, no indicator, and an
    operator who chose ``warn`` had silently been given ``off`` for that channel
    with no way to find out (round 1, M-3). Reporting is the whole of what
    ``warn`` produces.

    The clean case is asserted beside it, because "the field is populated" is
    also satisfied by a field that is always populated.
    """
    _configure(paths, "warn")
    drafted = service.draft(_request())
    evidence = drafted.directory / EVIDENCE_FILE
    evidence.unlink()
    evidence.symlink_to(paths.root.parent / "elsewhere.json")

    accepted = service.accept(drafted.proposal_id)

    assert accepted.secret_scan.findings == (), "the fixture planted a finding as well as a skip"
    assert accepted.secret_scan.skipped == (_AT_EVIDENCE,), (
        f"an unreadable evidence record reports {accepted.secret_scan.skipped}, so under warn it "
        f"is indistinguishable from a clean scan"
    )
    steps = _accept_steps(accepted)
    assert steps[0] is _SKIPPED_CHANNEL_STEP, f"the author is not told, first: {steps[0]}"
    assert "secretScanSkipped" in steps[0], "the step does not name the field that says what"


def test_a_run_whose_budget_fills_first_never_opens_the_evidence_record(
    service: ProposalService, paths: ProjectPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The laziness the chain order buys, which was measured true and pinned by nothing.

    ``_scan_for_secrets`` chains the evidence channel after the five landed ones
    rather than appending it to a list, so a run whose finding budget fills in the
    bodies never reads ``evidence.json`` at all -- the file is not opened by an
    acceptance that was going to be refused anyway. Swapping the chain for a
    ``[*landed, *evidence]`` list is behaviour-identical everywhere the suite
    otherwise looks, and costs a read on every refusal.

    Counted at the read rather than timed: an I/O count is the property, and a
    duration would make this a flaky test about a fast disk.
    """
    reads: list[str] = []
    original = ProposalService._read_within_project

    def counting(self: ProposalService, path: Path) -> bytes:
        reads.append(path.name)
        return original(self, path)

    monkeypatch.setattr(ProposalService, "_read_within_project", counting)
    body = "# Retry policy\n\n" + "".join(
        f"    TOKEN_{index}={PLANTED_TOKEN}\n" for index in range(MAX_FINDINGS + 2)
    )
    drafted = _with_a_secret_in_the_reasoning(service)
    drafted.body_file.write_text(body, encoding="utf-8")

    with pytest.raises(ProposalError):
        service.accept(drafted.proposal_id)

    assert EVIDENCE_FILE not in reads, (
        f"the evidence record was read by a run whose budget was already full ({reads}), so the "
        f"channels are no longer consumed lazily"
    )


def _a_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A throwaway project the real CLI can be pointed at, and the redirection.

    ``init`` scopes a project to a Git working tree, so the repository is part of
    the fixture rather than an accident of where the suite runs -- the same setup
    ``test_propose_cli.py``'s own project fixture performs. ``HOME`` and
    ``THEURIAN_DATA_DIR`` are redirected and the working directory is set in the
    same call, because ``init`` resolves the project from ``Path.cwd()`` and
    takes no argument that says where.
    """
    root = tmp_path / "demo"
    root.mkdir()
    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603
    (tmp_path / "home").mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(tmp_path / "datadir"))
    monkeypatch.chdir(root)
    initialised = CliRunner().invoke(app, ["init", "--json"], catch_exceptions=False)
    assert initialised.exit_code == 0, initialised.output
    return root


def _draft_through_the_cli(runner: CliRunner, root: Path) -> str:
    """One ordinary proposal, drafted by the real command, returning its id."""
    body = root / "body.md"
    body.write_text(CLEAN_BODY, encoding="utf-8")
    drafted = runner.invoke(
        app,
        [
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
            "--body-file",
            str(body),
            "--agent-id",
            "claude-code",
            "--task-id",
            "task-7",
            "--model",
            "claude-opus-5",
            "--reasoning",
            EVIDENCE.reasoning,
            "--source-uri",
            ANCHOR.source_uri,
            "--source-commit",
            # The fixture anchor sets it, and the CLI needs a `str` -- asserted
            # rather than coalesced so a fixture edit that dropped it fails here
            # instead of drafting an anchor this test did not mean to write.
            _required(ANCHOR.commit_sha, "the fixture anchor has no commit sha"),
            "--json",
        ],
        catch_exceptions=False,
    )
    assert drafted.exit_code == 0, drafted.output
    return str(json.loads(drafted.stdout)["proposalId"])


def test_a_budget_that_fills_upstream_still_reports_the_channel_it_never_reached(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """The hole the laziness pin opened in the skipped note (round 2, M-1).

    ``skipped`` is appended from inside ``_evidence_text``, which
    ``_findings_in`` drives lazily -- so a run whose finding budget fills in the
    bodies never advances that generator, and the note it would have made is
    never made. The result published ``skipped=()`` for a run that had not
    looked: exactly the false "nothing was skipped" the field exists to stop,
    reached by the very optimisation the test above pins.

    The fix cannot simply open the file, because that is what the laziness pin
    forbids. It does not need to: a channel the run never reached is a channel it
    did not clear, and saying so needs no I/O at all.

    Both halves are asserted. The budget really is full -- otherwise the
    generator would have been reached and this would be the ordinary case under
    another name -- and the record really is unreadable.
    """
    _configure(paths, "warn")
    # Planted at draft time: rewriting the body afterwards breaks the migration's
    # own `contentSha256`, and the rehearsal then refuses before the scan result
    # is ever returned.
    drafted = _with_a_secret_in_the_reasoning(
        service,
        body="# Retry policy\n\n"
        + "".join(f"    TOKEN_{index}={PLANTED_TOKEN}\n" for index in range(MAX_FINDINGS + 2)),
    )
    evidence = drafted.directory / EVIDENCE_FILE
    evidence.unlink()
    evidence.symlink_to(paths.root.parent / "elsewhere.json")

    accepted = service.accept(drafted.proposal_id)

    assert len(accepted.secret_scan.findings) == MAX_FINDINGS, (
        f"the budget did not fill ({len(accepted.secret_scan.findings)} findings), so the "
        f"evidence generator was reached and this is not the case under test"
    )
    assert accepted.secret_scan.skipped == (_AT_EVIDENCE,), (
        f"a run that never reached the evidence channel reported {accepted.secret_scan.skipped}, "
        f"publishing 'nothing was skipped' for a channel it had not looked at"
    )


def test_the_payload_publishes_the_skipped_channels_and_not_only_the_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``secretScanSkipped`` itself, which nothing held (round 2, M-2).

    The only assertion on it was ``"secretScanSkipped" in steps[0]`` -- the
    *step's own text*, naming a field. Deleting the field from the payload left
    the suite green, and the failure mode is self-inconsistent output: the step
    goes on telling the reader to look in a key the document no longer carries.

    Driven through the real CLI, because the payload is the composition root's
    and the service cannot hold it.
    """
    runner = CliRunner()
    root = _a_project(tmp_path, monkeypatch)
    (root / ".theurian" / "config.yaml").write_text(
        'security:\n  secretScan: "warn"\n', encoding="utf-8"
    )
    proposal_id = _draft_through_the_cli(runner, root)
    evidence = root / ".theurian" / "proposals" / proposal_id / EVIDENCE_FILE
    evidence.unlink()
    evidence.symlink_to(tmp_path / "elsewhere.json")

    accepted = runner.invoke(
        app, ["propose", "accept", proposal_id, "--json"], catch_exceptions=False
    )

    assert accepted.exit_code == 0, accepted.output
    payload = json.loads(accepted.stdout)
    assert payload["secretScanSkipped"] == [_AT_EVIDENCE], (
        f"the payload does not publish what was skipped: {payload.get('secretScanSkipped')!r}"
    )
    assert _SKIPPED_CHANNEL_STEP in payload["nextSteps"], "the step and the field disagree"
    assert "secretScanSkipped" in _SKIPPED_CHANNEL_STEP, (
        "the step names a different field than the one published, so a reader is sent to a key "
        "that is not there"
    )


def test_a_flagged_body_and_an_unreadable_record_both_speak_in_priority_order(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """The both-fire branch, which the docstring claims and no test drove (round 2, M-3).

    ``_accept_steps`` says a skipped channel is prepended *after* the rotate
    step, "because a value already believed to be exposed outranks a channel
    nobody could look at", and that both can fire at once. The two existing tests
    drive one condition each -- findings with nothing skipped, and skipped with
    no findings -- so reversing the priority left the suite green.

    Both conditions are asserted present before the order is, because an ordering
    assertion over a one-element list passes for the wrong reason.
    """
    _configure(paths, "warn")
    drafted = _with_a_secret_in_the_reasoning(
        service, body=f"# Retry policy\n\n    TOKEN={PLANTED_TOKEN}\n"
    )
    evidence = drafted.directory / EVIDENCE_FILE
    evidence.unlink()
    evidence.symlink_to(paths.root.parent / "elsewhere.json")

    accepted = service.accept(drafted.proposal_id)

    assert accepted.secret_scan.findings, "no finding fired, so the rotate step is not in play"
    assert accepted.secret_scan.skipped, "nothing was skipped, so the skipped step is not in play"
    steps = _accept_steps(accepted)
    assert steps[0] is _ROTATE_ADVICE_STEP, (
        f"a value already believed exposed did not come first: {steps[0]}"
    )
    assert steps[1] is _SKIPPED_CHANNEL_STEP, (
        f"the skipped channel is not second, so both did not fire in the recorded order: {steps}"
    )


def test_a_clean_warn_acceptance_reports_no_skipped_channel(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """The negative control for the field above, and for the step it prepends."""
    _configure(paths, "warn")
    drafted = service.draft(_request())

    accepted = service.accept(drafted.proposal_id)

    assert accepted.secret_scan.skipped == (), (
        f"a readable record was reported as skipped: {accepted.secret_scan.skipped}"
    )
    assert _accept_steps(accepted)[0] is not _SKIPPED_CHANNEL_STEP, (
        "the skipped-channel step fires when nothing was skipped"
    )


def test_a_proposal_with_no_evidence_record_at_all_is_still_accepted(
    service: ProposalService,
) -> None:
    """Absent is not a failure, and this is the case that says so.

    ``draft`` writes the body, then the evidence, then the migration, so an
    interrupted draft legitimately has no record; a legacy proposal may have
    none either. A scan that treated absence as unreadable would refuse both.
    """
    drafted = service.draft(_request())
    (drafted.directory / EVIDENCE_FILE).unlink()

    accepted = service.accept(drafted.proposal_id)

    assert accepted.secret_scan.findings == (), "an absent record produced a finding"


def test_a_clean_evidence_record_is_not_reported(service: ProposalService) -> None:
    """The negative control: the ordinary record scans empty.

    Without it every test above is satisfied by a channel that reports
    everything, which would refuse every acceptance this product performs.
    """
    drafted = service.draft(_request())

    accepted = service.accept(drafted.proposal_id)

    assert accepted.secret_scan.findings == (), (
        f"a clean proposal was reported: {[f.describe() for f in accepted.secret_scan.findings]}"
    )


#: The clause of ``_ACCEPT_STEPS[0]`` whose absence the CLI test below asserts.
#: A clause and not the whole sentence, so a reworded step still reddens the
#: test: what must not reach the author is the *instruction*, not one spelling of
#: it. The step is asserted to still contain it, so a rewording that moves the
#: clause fails here rather than quietly making the absence assertion vacuous.
_COMMIT_THE_DIRECTORY: Final = "open a pull request with the proposal directory in it"


def test_the_refusal_arrives_before_the_author_is_told_to_commit_the_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ordering that makes this an approval gate rather than a report.

    The whole harm is a commit: the credential is on the author's own disk
    already, and what turns it into Git history is following step one. So the
    property to pin is not only *that* the acceptance is refused but that the
    instruction is never printed on the way. Driven through the real CLI, because
    the ordering is between the service and its composition root and neither
    alone can hold it.
    """
    assert _COMMIT_THE_DIRECTORY in _ACCEPT_STEPS[0], (
        "the step this test pins the absence of has been reworded; re-derive the clause, or the "
        "absence assertion below passes against a message that still says it"
    )
    runner = CliRunner()
    root = tmp_path / "demo"
    root.mkdir()
    # `init` scopes a project to a Git working tree, so the repository is part of
    # the fixture rather than an accident of where the suite runs -- the same
    # setup `test_propose_cli.py`'s own project fixture performs.
    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(tmp_path / "datadir"))
    monkeypatch.chdir(root)
    (tmp_path / "home").mkdir()

    initialised = runner.invoke(app, ["init", "--json"], catch_exceptions=False)
    assert initialised.exit_code == 0, initialised.output

    body = root / "body.md"
    body.write_text(CLEAN_BODY, encoding="utf-8")
    drafted = runner.invoke(
        app,
        [
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
            "--body-file",
            str(body),
            "--agent-id",
            "claude-code",
            "--task-id",
            "task-7",
            "--model",
            "claude-opus-5",
            "--reasoning",
            EVIDENCE.reasoning,
            "--source-uri",
            ANCHOR.source_uri,
            "--source-commit",
            # The fixture anchor sets it, and the CLI needs a `str` -- asserted
            # rather than coalesced so a fixture edit that dropped it fails here
            # instead of drafting an anchor this test did not mean to write.
            _required(ANCHOR.commit_sha, "the fixture anchor has no commit sha"),
            "--json",
        ],
        catch_exceptions=False,
    )
    assert drafted.exit_code == 0, drafted.output
    proposal_id = json.loads(drafted.stdout)["proposalId"]

    evidence = root / ".theurian" / "proposals" / proposal_id / EVIDENCE_FILE
    document = json.loads(evidence.read_text(encoding="utf-8"))
    document["reasoning"] = f"Verified against staging with THEURIAN_TOKEN={PLANTED_TOKEN}"
    evidence.write_text(json.dumps(document, indent=2), encoding="utf-8")

    accepted = runner.invoke(
        app, ["propose", "accept", proposal_id, "--json"], catch_exceptions=False
    )

    assert accepted.exit_code != 0, f"the acceptance was not refused: {accepted.output}"
    published = f"{accepted.stdout}\n{accepted.stderr}"
    assert PLANTED_TOKEN not in published, f"the CLI published the credential: {published}"
    assert _COMMIT_THE_DIRECTORY not in published, (
        f"the author was told to commit the proposal directory anyway: {published}"
    )
    assert evidence.exists(), "the refusal removed the record the author has to correct"
