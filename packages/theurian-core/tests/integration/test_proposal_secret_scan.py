"""``propose accept`` under each secret-scan policy (SEC-11, ADR-0027 decision 3).

``test_content_secrets.py`` proves the detector detects and
``test_project_config.py`` proves the reader reads. This is the third thing, and
neither of those can say it: that the two are wired together on the accept path,
in the right order, with the right consequence.

**One test here goes red when the key stops taking effect**, which is what
``test_config_key_call_sites.py`` demands of the change that made
``security.secretScan`` real. Deleting the scan from ``accept`` leaves both unit
modules entirely green.

Its own file rather than an addition to ``test_proposal_service.py``, which is
already 3,000 lines: the fixtures below are that module's, copied because a
fixture shared across files would have to move to a ``conftest`` and take every
other test in that module with it. What is *not* copied is the rehearsal -- the
real pipeline, as there, because a stub would assert the opposite of what the
pre-check claims.
"""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Collection, Iterator, Mapping
from pathlib import Path
from typing import Final

import pytest
from fakes.clock import FrozenClock
from fakes.ids import SeededIdGenerator

from theurian.application.project_service import ProjectPaths, initialize_project
from theurian.application.proposal_service import (
    _MAX_NAMES_LISTED,
    AcceptedProposal,
    ProposalError,
    ProposalRequest,
    ProposalService,
)
from theurian.cli.migration_pipeline import rehearse_migration_set
from theurian.domain.enums import KnowledgeKind
from theurian.domain.errors import ProjectConfigError
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
from theurian.security.content_secrets import HIGH_ENTROPY
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

#: The same derivation ``test_content_secrets.py`` uses, and for the same reason:
#: a fresh ``secrets.token_urlsafe(32)`` contains no digit in 0.065% of draws, so
#: a drawn fixture would redden this suite for nothing about once in 1,500 runs.
#: Split from its seed so that no credential-shaped literal exists in the file.
PLANTED_TOKEN: Final = (
    base64.urlsafe_b64encode(
        hashlib.sha256(b"theurian accept-path secret-scan fixture (#198)").digest()
    )
    .decode()
    .rstrip("=")
)

CLEAN_BODY: Final = "# Retry policy\n\nThree attempts, then fail loudly.\n"
LEAKY_BODY: Final = f"# Retry policy\n\nThree attempts.\n\n    THEURIAN_MCP_TOKEN={PLANTED_TOKEN}\n"

#: A body carrying more secrets than the refusal lists, one per line so each is a
#: distinct finding. Two past the name cap, so the truncation is exercised.
_OVER_THE_CAP = _MAX_NAMES_LISTED + 2
MANY_SECRETS_BODY: Final = "# Retry policy\n\n" + "".join(
    f"    TOKEN_{index}={PLANTED_TOKEN}\n" for index in range(_OVER_THE_CAP)
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
        validate=_validate,
        current_revision=current_revision,
        landed_migration=landed_migration,
        landed_migrations=landed_migrations,
        rehearse=lambda candidate: rehearse_migration_set(candidate, clock=FrozenClock()),
    )


def _validate(document: Mapping[str, object]) -> None:
    validate_migration_document(document, SCHEMAS)


def _request(body: str, item: str = "architecture.retry-policy") -> ProposalRequest:
    return ProposalRequest(
        item_id=ItemId(item),
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


def _configure(paths: ProjectPaths, policy: str) -> None:
    """Write a config file selecting ``policy``, quoted so YAML keeps it a string.

    Quoted because a bare ``off`` is the boolean false under YAML 1.1 --
    ``test_project_config.py::test_a_bare_off_is_refused_with_the_quoting_cure``
    is where that is pinned. Written the unquoted way, the ``off`` case here would
    exercise the refusal instead of the policy and would still look green.
    """
    paths.config.write_text(f'security:\n  secretScan: "{policy}"\n', encoding="utf-8")


def _accept(service: ProposalService, paths: ProjectPaths, body: str) -> AcceptedProposal:
    drafted = service.draft(_request(body))
    return service.accept(drafted.proposal_id)


def test_a_body_carrying_a_secret_is_refused_by_default(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """No configuration file at all, and the acceptance is still refused.

    This is the whole of ADR-0027 decision 3's default in one assertion: a
    project that has never heard of ``security.secretScan`` gets ``block``. The
    fixture writes no config file, so nothing here selects the policy -- if the
    reader's fallback moved, this goes red.
    """
    assert not paths.config.exists(), "the fixture wrote a config file; the default is untested"
    drafted = service.draft(_request(LEAKY_BODY))

    with pytest.raises(ProposalError) as caught:
        service.accept(drafted.proposal_id)

    assert HIGH_ENTROPY in str(caught.value), (
        f"the refusal does not name what matched: {caught.value}"
    )
    assert "secretScan" in caught.value.remedy, (
        f"the remedy does not name the key that selects the policy: {caught.value.remedy!r}"
    )


def test_a_refusal_lists_at_most_the_name_cap_and_does_not_reveal_the_count(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """The refusal bounds its own listing (adversarial M-2, pinning the slice).

    ``_secret_refusal`` slices ``findings`` to :data:`_MAX_NAMES_LISTED` before
    handing them to ``_names``, which is the *second* of two ceilings and, unlike
    the first, deliberately suppresses ``_names``' own "and N more" tail: how many
    bodies a proposal carries is the contributor's number, not one this refusal
    republishes. Dropping the slice -- ``findings`` passed whole -- lets that tail
    fire, revealing the count. Reproduced: without it a body with
    ``_MAX_NAMES_LISTED + 2`` findings appends "and 2 more"; with it, exactly the
    cap and no tail.
    """
    drafted = service.draft(_request(MANY_SECRETS_BODY))

    with pytest.raises(ProposalError) as caught:
        service.accept(drafted.proposal_id)

    message = str(caught.value)
    assert message.count(HIGH_ENTROPY) == _MAX_NAMES_LISTED, (
        f"the refusal listed {message.count(HIGH_ENTROPY)} findings, not the {_MAX_NAMES_LISTED} "
        f"cap: {message}"
    )
    assert "more" not in message, (
        f"the refusal revealed how many findings there were past the cap: {message}"
    )


def test_a_refused_acceptance_consumes_nothing(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """The recovery property, which an exit-code assertion alone does not reach.

    A version that refused *after* moving the files would pass a test that only
    checked the exception. What #307 asked for is that the proposal survives its
    own rejection -- so the author still has the sources to correct rather than a
    body already sitting in ``.theurian/knowledge/`` with a secret in it.

    That second half matters more here than anywhere else on the accept path:
    the thing not moved is a credential.
    """
    drafted = service.draft(_request(LEAKY_BODY))
    before = {p.relative_to(drafted.directory).as_posix() for p in drafted.directory.rglob("*")}

    with pytest.raises(ProposalError):
        service.accept(drafted.proposal_id)

    assert {
        p.relative_to(drafted.directory).as_posix() for p in drafted.directory.rglob("*")
    } == before, "the proposal directory changed on a refused acceptance"
    assert drafted.body_file.read_text(encoding="utf-8") == LEAKY_BODY
    assert not drafted.body_destination.exists(), (
        "the body landed in .theurian/knowledge/ despite the refusal -- the secret is now "
        "in the tree the migration set reads"
    )
    assert not (paths.migrations / drafted.migration_file.name).exists()


def test_the_scan_runs_before_the_body_reaches_any_filesystem(service: ProposalService) -> None:
    """The ordering the accept path states, held as behaviour rather than a comment.

    The pre-check's dry replay stages the incoming bodies into a throwaway tree
    before it replays them. Scanning after it would mean a blocked body's bytes
    had been written somewhere -- briefly, and to a temporary directory, but
    written -- before anything decided to refuse.

    Reached by giving the replay its own reason to refuse: this is ADR-0027's
    racing face, two proposals drafted before either acceptance so both claim the
    item's first revision. The second one carries a secret *and* would be refused
    by the replay for a revision conflict, so the message says which check ran
    first. A test that planted only the secret would pass under either order.
    """
    clean = service.draft(_request(CLEAN_BODY))
    racing = service.draft(_request(LEAKY_BODY))
    service.accept(clean.proposal_id)

    with pytest.raises(ProposalError) as caught:
        service.accept(racing.proposal_id)

    assert HIGH_ENTROPY in str(caught.value), (
        f"the replay's revision conflict was reported before the secret was, so the scan "
        f"runs after the bodies have been staged: {caught.value}"
    )


def test_off_skips_the_scan_and_the_body_lands(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """The escape hatch, without which ``block`` by default is a dead end.

    A false positive has no per-finding suppression, so a project that hits one
    has exactly this: turn the scan off, or turn it down. It has to work, and it
    has to be visible in the result -- an empty finding list under ``off`` means
    "nothing was scanned", not "nothing was found", which is why the policy rides
    on the result beside it.
    """
    _configure(paths, SecretScanPolicy.OFF.value)

    accepted = _accept(service, paths, LEAKY_BODY)

    assert accepted.secret_scan.policy is SecretScanPolicy.OFF
    assert accepted.secret_scan.findings == ()
    assert PLANTED_TOKEN in accepted.bodies[0].destination.read_text(encoding="utf-8")


def test_warn_lands_the_body_and_reports_what_it_found(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """Proceeding is only half of ``warn``; the other half is that it says so.

    A ``warn`` that accepted silently would be indistinguishable from ``off`` to
    every caller, and the point of the policy is that a human is told which body
    to look at before they open the pull request. The finding names the body by
    the path it has under ``knowledge/`` -- the same relative path it has inside
    the proposal directory, so the string is right on both sides of the move.
    """
    _configure(paths, SecretScanPolicy.WARN.value)

    accepted = _accept(service, paths, LEAKY_BODY)

    assert accepted.secret_scan.policy is SecretScanPolicy.WARN
    assert [f.finding.family for f in accepted.secret_scan.findings] == [HIGH_ENTROPY]
    (finding,) = accepted.secret_scan.findings
    assert finding.body.endswith(".md"), f"the finding names {finding.body!r}"
    assert PLANTED_TOKEN not in finding.describe(), (
        f"a warning reproduces the token it warns about: {finding.describe()!r}"
    )
    assert accepted.bodies[0].destination.exists()


@pytest.mark.parametrize("policy", list(SecretScanPolicy), ids=lambda p: p.value)
def test_a_clean_body_is_accepted_under_every_policy(
    service: ProposalService, paths: ProjectPaths, policy: SecretScanPolicy
) -> None:
    """The case that has to keep working, and the one a blunt guard breaks.

    A scan that refused everything would satisfy every assertion above. This is
    what says the control is a filter rather than a wall -- including under
    ``block``, where the ordinary proposal has to sail through untouched.
    """
    _configure(paths, policy.value)

    accepted = _accept(service, paths, CLEAN_BODY)

    assert accepted.secret_scan.policy is policy
    assert accepted.secret_scan.findings == ()
    assert accepted.bodies[0].destination.read_text(encoding="utf-8") == CLEAN_BODY


def test_a_config_this_build_cannot_read_refuses_the_acceptance(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """A typo about a security control is surfaced, not guessed around.

    ``warm`` is not ``warn``, and coercing it to ``block`` would leave the
    operator watching acceptances fail with no idea why. The refusal is a
    ``ProjectConfigError`` rather than a ``ProposalError`` on purpose: the
    proposal is fine and its author has nothing to correct, so it must not arrive
    as "your proposal is broken" (#227).
    """
    _configure(paths, "warm")
    drafted = service.draft(_request(CLEAN_BODY))

    with pytest.raises(ProjectConfigError) as caught:
        service.accept(drafted.proposal_id)

    assert not drafted.body_destination.exists(), "the acceptance moved files before refusing"
    assert caught.value.remedy, "a config refusal reached the caller with no remedy (#227)"
