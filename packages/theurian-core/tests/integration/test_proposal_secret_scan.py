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
from collections.abc import Callable, Collection, Iterator, Mapping
from dataclasses import replace
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
from theurian.security.content_secrets import HIGH_ENTROPY, scan_text
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


#: A migration filename as a knowledge author would quote one in a title. The
#: detector reports every string of this shape as a secret *unless* its ULID
#: subtraction runs first -- ``_looks_like_a_secret`` records the measurement:
#: all 26 of this repository's committed migration filenames scored 4.59 to 4.95
#: bits. Kept here as a whole filename rather than a bare ULID because that is
#: the string a person writes.
MIGRATION_FILENAME: Final = "01K3Z8Q9V4MRB7T2XNFCD5HGJW-retry-policy.yaml"

#: Where a contributor's own words reach the migration document, one entry per
#: field, each planting :data:`PLANTED_TOKEN` the way a leak actually arrives:
#: pasted into a sentence, a name, a label or a URL rather than sitting alone.
#:
#: Every value here was measured to be reported by ``scan_text`` on its own
#: (2026-08-24, all ``high-entropy-token``), so a test built on one is red
#: because the accept path did not scan the field and not because the detector
#: had nothing to find. The population is the document's author-controlled
#: strings, taken from ``_migration_document`` and ``_anchor_document`` rather
#: than from this file's imagination: the migration's own ``author`` and
#: ``description``, ``createItem``'s ``namespace`` and ``owner``, the revision
#: metadata's ``title``/``namespace``/``owner``/``labels``/``scope``, and every
#: string of every source anchor. ``sourceUri`` is the sharpest of them --
#: ``knowledge.search`` and ``knowledge.get`` publish it on every result, so a
#: credential there is disclosed to an agent that never reads the body.
#:
#: What is deliberately absent is the derived half: ``id``, ``revisionId``,
#: ``contentSha256``, ``createdAt``, ``contentFile``, ``contentType`` and the
#: enum fields. Those are Theurian's own output, and
#: ``test_a_title_quoting_a_migration_filename_is_still_accepted_under_block``
#: is what says so from the other side.
_METADATA_PLANTS: Final[Mapping[str, Callable[[ProposalRequest], ProposalRequest]]] = {
    "title": lambda request: replace(request, title=f"Retry policy {PLANTED_TOKEN}"),
    "description": lambda request: replace(
        request, description=f"Settled at three attempts. Staging token={PLANTED_TOKEN}"
    ),
    "author": lambda request: replace(request, author=f"{PLANTED_TOKEN}@example.com"),
    "owner": lambda request: replace(request, owner=f"platform-team-{PLANTED_TOKEN}"),
    "namespace": lambda request: replace(request, namespace=f"architecture-{PLANTED_TOKEN}"),
    "label": lambda request: replace(request, labels=(PLANTED_TOKEN,)),
    "scope-path": lambda request: replace(request, scope_paths=(f"src/**/{PLANTED_TOKEN}.py",)),
    "anchor-source-uri": lambda request: replace(
        request,
        source_anchors=(replace(ANCHOR, source_uri=f"https://ci.example/j?token={PLANTED_TOKEN}"),),
    ),
    "anchor-provider": lambda request: replace(
        request, source_anchors=(replace(ANCHOR, provider=f"git-{PLANTED_TOKEN}"),)
    ),
    "anchor-file-path": lambda request: replace(
        request, source_anchors=(replace(ANCHOR, file_path=f"deploy/{PLANTED_TOKEN}.env"),)
    ),
}


def _with_a_planted_secret(field: str, body: str = CLEAN_BODY) -> ProposalRequest:
    """The ordinary request, with ``field`` carrying a secret and the body clean.

    The body stays clean on purpose: a proposal that leaked through both would
    be refused by the body scan that already exists, and every metadata test
    here would pass with the new control absent.
    """
    return _METADATA_PLANTS[field](_request(body))


def _rendered(accepted: AcceptedProposal) -> str:
    """Every finding on a result as one string, the way a caller would print it.

    ``describe`` rather than any field of the finding: it is what
    ``_secret_refusal`` calls to build the message a contributor reads and what
    an ``accept --json`` document carries, so it is the surface a test may hold.
    Which *field* of a finding names the location is the implementation's to
    choose.
    """
    return "\n".join(finding.describe() for finding in accepted.secret_scan.findings)


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
    assert finding.location.endswith(".md"), f"the finding names {finding.location!r}"
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


# -- the migration document, not only the bodies (#336) ---------------------
#
# Everything above plants its secret in a body file, and every one of those
# tests passes while a credential in the *migration* sails through: the scan
# iterates body moves, and the parsed document -- in scope at the call site --
# is never read. Measured on 55fe588 through the real CLI at policy `block`
# (2026-08-24, https://github.com/theurian/theurian/issues/336): `propose` with
# a clean body and a secret in --title, --description and --label, then
# `propose accept`, exits 0 with `secretFindings: []` and lands all three values
# in .theurian/migrations/. SEC-11 says "scan content for secrets before it
# becomes an approved revision", and a revision's title is content.


@pytest.mark.parametrize("field", list(_METADATA_PLANTS))
def test_every_planted_field_reaches_the_migration_document_and_is_detectable(
    service: ProposalService, field: str
) -> None:
    """The guard on the tests below, which are worthless without it.

    Each of them asserts that the accept path refuses a secret in one field.
    Two things have to be true before that assertion means anything: the field
    has to reach the migration document at all -- ``labels`` and ``scope`` are
    written only when non-empty, and an anchor's optional strings only when set
    -- and the value has to be one the detector reports. A plant that failed
    either would leave those tests red for the fixture's reason rather than the
    control's, and one failing the first can be worse than red: a secret planted
    in ``evidence.reasoning`` never reaches the migration at all, so a refusal
    would mean ``evidence.json`` was scanned -- a different class, out of #336's
    scope -- while the metadata channel this file is about stayed open.

    The whole migration text is scanned rather than the field in isolation,
    which also pins the thing #336 is about: the value is in the document, in
    the tree ``migrate apply`` reads. A clean proposal's document scans empty
    here (measured 2026-08-24), so every finding this sees is the plant.
    """
    drafted = service.draft(_with_a_planted_secret(field))

    document = drafted.migration_file.read_text(encoding="utf-8")

    assert PLANTED_TOKEN in document, f"{field} never reached the migration document"
    families = {finding.family for finding in scan_text(document)}
    assert HIGH_ENTROPY in families, (
        f"the detector reports {families or 'nothing'} for a secret planted in {field}, so a "
        f"refusal test built on it would not be testing the accept path"
    )


@pytest.mark.parametrize("field", list(_METADATA_PLANTS))
def test_a_secret_in_the_migration_document_is_refused_by_default(
    service: ProposalService, paths: ProjectPaths, field: str
) -> None:
    """SEC-11's gate covers what the migration says, not only what it carries.

    A body is one of two ways a credential reaches an approved revision, and it
    is the way that gets reviewed: the other is a field a human skims. ``title``
    and ``sourceUri`` are published on every ``knowledge.search`` and
    ``knowledge.get`` result, so a secret there is disclosed to an agent that
    never opens the body -- which makes the metadata channel wider than the one
    already guarded, not narrower.

    Parametrized over the document's author-controlled strings rather than
    written once for ``title``, because a control that covers the field the fix
    was reported against and no other is the same defect one field along.
    """
    assert not paths.config.exists(), "the fixture wrote a config file; the default is untested"
    drafted = service.draft(_with_a_planted_secret(field))

    with pytest.raises(ProposalError) as caught:
        service.accept(drafted.proposal_id)

    assert HIGH_ENTROPY in str(caught.value), (
        f"a secret in {field} was not what refused the acceptance: {caught.value}"
    )


@pytest.mark.parametrize("field", ["title", "anchor-source-uri"])
def test_a_refused_metadata_secret_leaves_the_proposal_intact(
    service: ProposalService, paths: ProjectPaths, field: str
) -> None:
    """The recovery property, on the new surface (#307's requirement, #336's input).

    The same reasoning as ``test_a_refused_acceptance_consumes_nothing``: an
    author whose acceptance is refused must still hold the files to correct. It
    needs saying separately here because the metadata refusal fires from a
    different input -- a parsed document rather than a staged body -- and an
    implementation that scanned after the move would satisfy every assertion in
    the test above this one.

    The migration matters more than the body on this path: the body is clean, so
    what must not reach ``.theurian/migrations/`` is the document holding the
    credential.
    """
    drafted = service.draft(_with_a_planted_secret(field))
    before = {p.relative_to(drafted.directory).as_posix() for p in drafted.directory.rglob("*")}

    with pytest.raises(ProposalError):
        service.accept(drafted.proposal_id)

    assert {
        p.relative_to(drafted.directory).as_posix() for p in drafted.directory.rglob("*")
    } == before, "the proposal directory changed on a refused acceptance"
    assert not (paths.migrations / drafted.migration_file.name).exists(), (
        "the migration landed in .theurian/migrations/ despite the refusal -- the secret is now "
        "in the tree the migration set reads"
    )
    assert not drafted.body_destination.exists()
    assert PLANTED_TOKEN in drafted.migration_file.read_text(encoding="utf-8"), (
        "the proposal's own migration no longer holds the value the author has to correct"
    )


def test_a_metadata_refusal_does_not_reproduce_the_secret_it_reports(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """A report that quotes the credential is a second copy of it.

    ``SecretFinding`` bounds this at four characters for the body path and
    refuses construction past it, but a message assembled from *field values*
    rather than from findings would route around that: naming the offending
    field is useful, echoing what is in it is not. The refusal is printed to a
    terminal and, under ``--json``, published into a document something will
    log.
    """
    drafted = service.draft(_with_a_planted_secret("title"))

    with pytest.raises(ProposalError) as caught:
        service.accept(drafted.proposal_id)

    assert PLANTED_TOKEN not in str(caught.value), (
        f"the refusal reproduced the secret it refused: {caught.value}"
    )
    assert PLANTED_TOKEN not in caught.value.remedy, (
        f"the remedy reproduced the secret it refused: {caught.value.remedy!r}"
    )


@pytest.mark.parametrize(
    ("field", "named"), [("title", "title"), ("anchor-source-uri", "sourceuri")]
)
def test_warn_lands_the_proposal_and_names_the_metadata_it_found(
    service: ProposalService, paths: ProjectPaths, field: str, named: str
) -> None:
    """Under ``warn`` a metadata finding has to be findable by the human reading it.

    ``warn`` exists so a reviewer is told where to look before they open the
    pull request. A finding that named the body file would send them to a file
    the secret is not in -- the body here is clean -- so the assertion is that
    the rendering points at the field and not at a body path.

    Held loosely on purpose: how the location is spelled is the implementation's
    choice, and pinning ``metadata.title`` would make an equally correct
    ``operations[1].metadata.title`` a failure. What may not vary is that the
    field is named and that no ``.md`` body is blamed for it.
    """
    _configure(paths, SecretScanPolicy.WARN.value)
    drafted = service.draft(_with_a_planted_secret(field))

    accepted = service.accept(drafted.proposal_id)

    rendered = _rendered(accepted)
    assert accepted.secret_scan.policy is SecretScanPolicy.WARN
    assert accepted.secret_scan.findings, f"warn reported nothing for a secret in {field}"
    assert HIGH_ENTROPY in rendered, f"the finding does not name what matched: {rendered!r}"
    assert named in rendered.lower().replace("_", ""), (
        f"the finding does not name the field the secret is in: {rendered!r}"
    )
    assert ".md" not in rendered, (
        f"the finding blames a body file for a secret that is in the migration: {rendered!r}"
    )
    assert PLANTED_TOKEN not in rendered, (
        f"a warning reproduces the token it warns about: {rendered!r}"
    )
    assert (paths.migrations / drafted.migration_file.name).exists(), "warn refused the acceptance"


def test_one_listing_bound_covers_the_body_and_the_metadata_together(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """The cap is over both kinds of finding, not one cap each.

    ``test_a_refusal_lists_at_most_the_name_cap_and_does_not_reveal_the_count``
    holds the bound for a leaky body, and it stays green against an
    implementation that scans the document into a *second* list with a second
    cap: nine findings would then print ten lines and, worse, say that the
    proposal leaked on both sides. This is the same assertion with the findings
    coming from both places at once: measured 2026-08-24, this fixture produces
    eleven -- one body finding and ten metadata ones, ``namespace`` and ``owner``
    counting twice because ``createItem`` and the revision metadata each carry
    them -- so it is over the cap from either direction.
    """
    request = _request(LEAKY_BODY)
    for plant in _METADATA_PLANTS.values():
        request = plant(request)
    drafted = service.draft(request)

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


def test_off_leaves_the_migration_document_unscanned_too(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """The escape hatch has to cover the whole control, or it is not one.

    ``block`` is the default and there is no per-finding suppression, so a
    project that hits a false positive in a title has exactly one move. An
    implementation that scanned metadata unconditionally -- before the policy is
    consulted, which is where the cheapest patch puts it -- would leave that
    project unable to accept anything, and would do it while reporting the
    policy as ``off``.
    """
    _configure(paths, SecretScanPolicy.OFF.value)
    drafted = service.draft(_with_a_planted_secret("title"))

    accepted = service.accept(drafted.proposal_id)

    assert accepted.secret_scan.policy is SecretScanPolicy.OFF
    assert accepted.secret_scan.findings == (), "off scanned the migration document anyway"
    landed = paths.migrations / drafted.migration_file.name
    assert PLANTED_TOKEN in landed.read_text(encoding="utf-8")


def test_a_title_quoting_a_migration_filename_is_still_accepted_under_block(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """The false positive that would make this control the first thing switched off.

    Theurian's own derived identifiers are high-entropy by construction, and the
    detector only tolerates them because ``_looks_like_a_secret`` subtracts
    ULIDs before judging anything -- its docstring records that all 26 committed
    migration filenames in this repository were otherwise reported as secrets,
    at 4.59 to 4.95 bits. Bodies have been scanned since #198, so that
    subtraction is already load-bearing for prose; extending the scan to titles
    and descriptions points it at the strings most likely to *cite* a migration.

    A knowledge item titled after the migration that introduced it is an
    ordinary thing to write. If accepting it takes turning the scan off, the
    project turns the scan off, and SEC-11's control is gone for the bodies too.

    Both halves are the assertion: ``scan_text`` is asked directly, so a future
    change that made the title genuinely detectable would fail here loudly
    rather than turn this into a test of nothing.
    """
    assert not paths.config.exists(), "the fixture wrote a config file; the default is untested"
    title = f"Retry policy, introduced by {MIGRATION_FILENAME}"
    assert scan_text(title) == (), (
        f"the fixture title is detectable on its own, so this asserts nothing: {title!r}"
    )
    drafted = service.draft(replace(_request(CLEAN_BODY), title=title))

    accepted = service.accept(drafted.proposal_id)

    assert accepted.secret_scan.findings == (), (
        f"a title quoting a migration filename was reported as a secret: {_rendered(accepted)}"
    )
    assert (paths.migrations / drafted.migration_file.name).exists()
