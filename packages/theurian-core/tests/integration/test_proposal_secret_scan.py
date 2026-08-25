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
import json
import re
from collections import Counter
from collections.abc import Callable, Collection, Iterator, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Final

import pytest
import yaml
from fakes.clock import FrozenClock
from fakes.ids import SeededIdGenerator
from jsonschema import Draft202012Validator

from theurian.application.project_service import ProjectPaths, initialize_project
from theurian.application.proposal_service import (
    _AT_BODY_CONTENT,
    _AT_BODY_PATH,
    _AT_MIGRATION_BYTES,
    _AT_MIGRATION_NAME,
    _AUTHORED_ANCHOR_FIELDS,
    _AUTHORED_METADATA_FIELDS,
    _AUTHORED_MIGRATION_FIELDS,
    _AUTHORED_OPERATION_FIELDS,
    _MAX_NAMES_LISTED,
    AcceptedProposal,
    DraftedProposal,
    ProposalError,
    ProposalRequest,
    ProposalService,
    _document_findings,
)
from theurian.cli.migration_pipeline import rehearse_migration_set
from theurian.domain.enums import KnowledgeKind
from theurian.domain.errors import ProjectConfigError
from theurian.domain.identifiers import AgentId, ItemId, MigrationId, ProjectId, RevisionId, TaskId
from theurian.domain.knowledge import AUTHORED_IN_THEURIAN, SourceAnchor
from theurian.domain.migration import Migration, current_revision_in
from theurian.domain.project import DEFAULT_KNOWLEDGE_DIRECTORY
from theurian.domain.proposal import Evidence, is_migration_file_name
from theurian.domain.values import MARKDOWN
from theurian.infrastructure.filesystem.migration_loader import (
    load_migrations,
    validate_migration_document,
)
from theurian.security.content_secrets import (
    _MIN_CANDIDATE_CHARS,
    HIGH_ENTROPY,
    MAX_FINDINGS,
    SecretFinding,
    scan_text,
)
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
#: string of every source anchor that can carry one -- ``commitSha`` and
#: ``blobSha`` are pinned by the schema to ``^[0-9a-f]{7,64}$``, which no family
#: can match. ``sourceUri`` is the sharpest of them --
#: ``knowledge.search`` and ``knowledge.get`` publish it on every result, so a
#: credential there is disclosed to an agent that never reads the body.
#:
#: What is absent is of two kinds. The derived half a document cannot carry a
#: credential in -- ``id``, ``revisionId``, ``contentSha256`` and the enum
#: fields, Theurian's own output or a fixed vocabulary, which
#: ``test_a_title_quoting_a_migration_filename_is_still_accepted_under_block``
#: and ``test_a_schema_excluded_field_admits_no_reported_secret`` hold from the
#: other side. ``contentFile`` sits with them here and for a different reason: it
#: *can* carry one, and what lands is the path rather than the string, so it is
#: the artifact-level scan's (#349) and not this field walk's.
#: And ``createdAt``, ``contentType``, ``validFrom`` and ``validTo``
#: -- *scanned* since #336, but not reachable by a *request* edit: ``createdAt``
#: is stamped from the clock, ``contentType`` is ``request.content_type``, and
#: the date fields are never set by ``propose``. Each reaches the document only
#: hand-authored, so they are driven by :data:`_HAND_AUTHORED` (the three
#: metadata strings) and by :func:`_a_proposal_carrying_a_secret_in_created_at`
#: (the top-level stamp) below instead.
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
    "anchor-repository": lambda request: replace(
        request, source_anchors=(replace(ANCHOR, repository=f"acme/{PLANTED_TOKEN}"),)
    ),
    "anchor-external-id": lambda request: replace(
        request, source_anchors=(replace(ANCHOR, external_id=f"JIRA-{PLANTED_TOKEN}"),)
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
    carries the finding before they open the pull request. The finding names the
    body content by a fixed channel literal and an index, not by the body's landed
    path: when the path is itself the credential, a location built from it would
    republish the value (#360). The path a reviewer opens stays on the result, in
    ``bodies[index].destination``.
    """
    _configure(paths, SecretScanPolicy.WARN.value)

    accepted = _accept(service, paths, LEAKY_BODY)

    assert accepted.secret_scan.policy is SecretScanPolicy.WARN
    assert [f.finding.family for f in accepted.secret_scan.findings] == [HIGH_ENTROPY]
    (finding,) = accepted.secret_scan.findings
    assert finding.location == f"{_AT_BODY_CONTENT}[0]", (
        f"the body-content finding is not located by its channel literal: {finding.location!r}"
    )
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
    coming from both places at once: re-measured 2026-08-24 after the anchor
    plants were added, this fixture produces eleven -- one body finding and ten
    metadata ones, ``namespace`` and ``owner`` counting twice because
    ``createItem`` and the revision metadata each carry them -- so it is over the
    cap from either direction.

    **Eleven did not move when :data:`_METADATA_PLANTS` grew by two, and that is
    a property of the table rather than a coincidence.** Every anchor plant
    rebuilds ``source_anchors`` from the pristine :data:`ANCHOR`, so applying
    them in sequence leaves only the last one standing: the anchor contribution
    here is one finding whatever the table's anchor count is, and it moved from
    ``filePath`` to ``externalId`` when the two new rows were appended. Adding a
    *non-anchor* plant raises this number; adding an anchor plant does not.
    Recorded because a reader who adds a row and finds the count unchanged would
    otherwise reasonably suspect the scan, which is the wrong place to look.
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


#: A lower-case prefix and ``sk-`` followed by forty lower-case hexadecimal
#: characters -- the shape #350 measured as invisible to the detector, and the
#: same shape as :data:`_ID_SHAPED_TOKEN` below, which is what #336's own tests
#: plant. Derived from a fixed seed and split from it for the reasons
#: :data:`PLANTED_TOKEN` records: not drawn, so the suite does not redden for
#: nothing, and no credential-shaped literal in the file.
#:
#: A seed of its own rather than a reuse, so that re-seeding either fixture cannot
#: silently change what the other one tests. The *shape* is deliberately shared.
_GLUED_TOKEN: Final = (
    "sk-" + hashlib.sha256(b"theurian glued-prefix accept-path fixture (#350)").hexdigest()[:40]
)

#: The title as somebody rotating a credential would actually write it: a verb, an
#: environment name, and the retired value pasted on the end. Nothing here
#: requires knowing anything about the detector -- which is what makes it the
#: bound on the control rather than an evasion of it.
_GLUED_TITLE: Final = f"rotate staging-{_GLUED_TOKEN}"


def test_a_title_gluing_a_credential_behind_a_stage_prefix_is_refused_under_block(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """The bound on the control #336 shipped, held where that control runs (#350).

    ``title`` has been scanned since #336 and this proposal is accepted anyway,
    because the *detector* reports nothing for it: the candidate run
    ``staging-sk-<hex40>`` is consumed whole by the generic family, refused by its
    class gate for want of an upper-case character, and the single non-overlapping
    pass never retries the word boundary the internal ``-`` provides -- which is
    where ``openai-api-key`` would have matched. ``test_content_secrets.py`` owns
    that mechanism. This case owns the consequence, and the consequence is the
    part that decides whether SEC-11's shipped control holds on a realistic input.

    It is the mirror of
    :func:`test_a_title_quoting_a_migration_filename_is_still_accepted_under_block`
    directly above, and **the pair is what closes the class**: a title that merely
    *quotes* one of Theurian's own identifiers must still be accepted, and a title
    that *carries* a credential behind a lower-case run must not. Either assertion
    alone is satisfied by a detector that has failed in the opposite direction --
    one by a detector that reports nothing, the other by one that reports
    everything.

    The detector is deliberately **not** asked directly here, unlike the test
    above. That question belongs to the unit layer and is answered there; asking
    it here would move this case's red onto a fact the unit tests already own and
    leave the accept path -- the only thing this file can speak for -- unexercised.

    The family is asserted rather than the bare refusal, because a title can be
    rejected for reasons that have nothing to do with a credential, and a test
    that accepted any ``ProposalError`` would go green on one of those.
    """
    assert not paths.config.exists(), "the fixture wrote a config file; the default is untested"
    assert len(f"staging-{_GLUED_TOKEN}") >= _MIN_CANDIDATE_CHARS, (
        f"the glued run is under the {_MIN_CANDIDATE_CHARS}-character candidate floor, so the "
        f"generic family never consumes it and this case has stopped describing #350's class -- "
        f"a run that short is reported today, and this would pass without exercising anything"
    )
    drafted = service.draft(replace(_request(CLEAN_BODY), title=_GLUED_TITLE))

    with pytest.raises(ProposalError) as caught:
        service.accept(drafted.proposal_id)

    assert "openai-api-key" in str(caught.value), (
        f"the acceptance was refused, but not for the credential glued into the title: "
        f"{caught.value}"
    )


# -- the operations `propose` does not write (#336) -------------------------
#
# `_migration_document` emits two operation types, `createItem` and
# `upsertRevision`, out of the fourteen `schemas/migrations/migration.schema.json`
# declares (counted from its `operation.oneOf`, 2026-08-24). So every test above
# reaches `_AUTHORED_OPERATION_FIELDS` through `namespace` and `owner` alone, and
# even those two only as one half of a pair -- the same names are in
# `_AUTHORED_METADATA_FIELDS`, and a drafted proposal carries the value in both.
#
# The rest is not dead code. `accept` reads a *committed* proposal directory
# (ADR-0013 point 7), and a contributor may hand-write any operation the schema
# allows into the migration it holds; the allowlist is the schema's population
# for exactly that reason. Measured on 8e755a4 against all 3,546 tests, each of
# these deletions was individually green:
#
#   * any one of `alias`, `description`, `format`, `itemId`, `note`, `reason`,
#     `sourceItemId`, `sourceUri`, `specId`, `supersededBy`, `targetItemId` --
#     and all eleven at once -- from `_AUTHORED_OPERATION_FIELDS`;
#   * `namespace` or `owner` from the same tuple, which the metadata entry of
#     the same name silently covered for;
#   * the single-`anchor` branch of `_authored_strings`, which only
#     `addEvidence` reaches.
#
# What follows is what turns each of those deletions red.

#: A lowercase, hyphen-separated credential, for the six fields below that the
#: schema constrains to ``$defs/itemId``: ``alias``, ``itemId``, ``sourceItemId``,
#: ``targetItemId``, ``specId`` and ``supersededBy``.
#:
#: That pattern -- ``^[a-z0-9]+(?:-[a-z0-9]+)*(?:\.[a-z0-9]+...)*$`` -- admits no
#: upper-case character, and the generic family's class gate requires one, so the
#: two obvious plants both fail for opposite reasons: :data:`PLANTED_TOKEN` is
#: rejected by the pattern, and a lower-case token of the same length is passed
#: over by the detector. ``sk-`` followed by twenty or more of the candidate class
#: is the ``openai-api-key`` family instead -- a pattern family, which
#: ``scan_text`` reports without consulting the class gate at all -- so the value
#: is simultaneously a valid ``itemId`` and a reported secret, which is what makes
#: those six fields testable at all. Split from its seed for the reason
#: :data:`PLANTED_TOKEN` is.
_ID_SHAPED_TOKEN: Final = (
    "sk-" + hashlib.sha256(b"theurian hand-authored operation fixture (#336)").hexdigest()[:40]
)

#: A clean ``itemId`` of the same shape, for the control below.
_ID_SHAPED_CLEAN: Final = "architecture.timeout-policy"

_SENTENCE_SECRET: Final = f"Rotated on 2026-08-24; the retired staging value was {PLANTED_TOKEN}."
_SENTENCE_CLEAN: Final = "Rotated on 2026-08-24; the retired staging value is gone."

_URI_SECRET: Final = f"https://specs.example/openapi.yaml?token={PLANTED_TOKEN}"
_URI_CLEAN: Final = "https://specs.example/openapi.yaml"

#: Where the hand-authored operation sits once it is appended to the two
#: ``_migration_document`` writes. Named rather than spelled inline because the
#: assertions below pin the *whole* location, index included.
_HAND_AUTHORED_INDEX: Final = 2

_ITEM: Final = "architecture.retry-policy"

#: A second body, for the second ``upsertRevision`` the two metadata entries
#: below need. It has to exist on disk: :meth:`ProposalService._body_moves` runs
#: *before* the scan does and refuses a ``contentFile`` with no file behind it,
#: so an upsert whose body is missing never reaches the control under test.
_SECOND_BODY: Final = b"# Timeout policy\n\nFive seconds, then fail loudly.\n"

#: Where that body sits, both inside the proposal directory and under
#: ``knowledge/``. One constant for both, so the ``contentFile`` the operation
#: declares and the file the fixture writes cannot drift apart.
_SECOND_BODY_TAIL: Final = "architecture/timeout-policy-01K9AAAAAA0000000000000009.md"

#: The revision the second upsert creates. Crockford base32 with no ``I``, ``L``,
#: ``O`` or ``U``, which the fixture guard enforces.
_SECOND_REVISION: Final = "01K9AAAAAA0000000000000009"


def _second_upsert(**metadata: str) -> dict[str, object]:
    """A second ``upsertRevision``, for a second item, with a metadata block of its own.

    This is the only way the *metadata* half of the allowlist can be driven
    independently. A drafted proposal copies one ``namespace`` and one ``owner``
    into both ``createItem`` and the revision metadata, so a plant in either
    reaches both and the finding it produces cannot say which entry found it --
    which is how ``namespace`` and ``owner`` sat in
    ``_AUTHORED_METADATA_FIELDS`` untested while looking covered. Here
    ``createItem`` at index 0 stays clean and only this block carries the value.
    """
    return {
        "op": "upsertRevision",
        "itemId": _ID_SHAPED_CLEAN,
        "revisionId": _SECOND_REVISION,
        "contentFile": f"../knowledge/{_SECOND_BODY_TAIL}",
        "contentSha256": hashlib.sha256(_SECOND_BODY).hexdigest(),
        "metadata": {
            "title": "Timeout policy",
            "contentType": MARKDOWN.value,
            "kind": "architecture",
            "namespace": "architecture",
            "status": "approved",
            "owner": "platform-team",
            **metadata,
        },
    }


@dataclass(frozen=True)
class _HandAuthored:
    """One hand-written operation, in two versions of the one field it is about.

    ``build`` takes the value that field carries, so the planted and the clean
    document differ in exactly one string and nothing else -- which is what lets
    the control below say that the operation contributes no finding of its own.
    """

    #: The operation, with ``value`` in the field this entry is about.
    build: Callable[[str], dict[str, object]]
    #: A value the detector reports, and the schema accepts for this field.
    secret: str
    #: A value of the same shape that the detector does not report.
    clean: str
    #: The location ``_authored_strings`` gives it, below the operation.
    at: str
    #: Which family the planted value matches.
    family: str


def _evidence_anchor(source_uri: str) -> dict[str, object]:
    """A ``$defs/sourceAnchor`` for ``addEvidence``, which carries exactly one."""
    return {"provider": "git", "sourceUri": source_uri}


#: One entry per name in ``_AUTHORED_OPERATION_FIELDS``, plus the single
#: ``anchor`` only ``addEvidence`` carries, plus the revision-metadata strings a
#: second ``upsertRevision`` carries -- ``namespace``, ``owner``,
#: ``contentType``, ``validFrom`` and ``validTo`` -- each planted in the
#: operation type a contributor would actually write it in. That every allowlist
#: entry has a fixture is asserted rather than stated:
#: ``test_every_drivable_allowlist_entry_has_a_fixture_that_reaches_it``.
#:
#: Deliberately absent are ``tenantId`` and ``aclGroup``. Neither is an operation
#: field -- both sit on revision metadata, which
#: :data:`_METADATA_PLANTS` above already reaches -- and neither can carry a
#: credential into an applied revision: ``migration_engine`` refuses any
#: ``tenantId`` but ``local`` and any ``aclGroup`` but ``default`` (issue #63),
#: on ``migrate validate`` and ``migrate apply`` alike. The schema itself does
#: *not* pin them, so the refusal is the engine's rather than the document's.
#:
#: ``commitSha`` and ``blobSha`` are absent for the reason the allowlist itself
#: records: the schema pins both to ``^[0-9a-f]{7,64}$``, which no family can
#: match. ``kind``, ``relationType``, ``sensitivity`` and ``status`` are enums.
_HAND_AUTHORED: Final[Mapping[str, _HandAuthored]] = {
    "deprecateItem.reason": _HandAuthored(
        build=lambda value: {"op": "deprecateItem", "itemId": _ITEM, "reason": value},
        secret=_SENTENCE_SECRET,
        clean=_SENTENCE_CLEAN,
        at="reason",
        family=HIGH_ENTROPY,
    ),
    "deprecateItem.supersededBy": _HandAuthored(
        build=lambda value: {"op": "deprecateItem", "itemId": _ITEM, "supersededBy": value},
        secret=_ID_SHAPED_TOKEN,
        clean=_ID_SHAPED_CLEAN,
        at="supersededBy",
        family="openai-api-key",
    ),
    "addRelation.note": _HandAuthored(
        build=lambda value: {
            "op": "addRelation",
            "sourceItemId": _ITEM,
            "relationType": "related_to",
            "targetItemId": _ID_SHAPED_CLEAN,
            "note": value,
        },
        secret=_SENTENCE_SECRET,
        clean=_SENTENCE_CLEAN,
        at="note",
        family=HIGH_ENTROPY,
    ),
    "addRelation.sourceItemId": _HandAuthored(
        build=lambda value: {
            "op": "addRelation",
            "sourceItemId": value,
            "relationType": "related_to",
            "targetItemId": _ID_SHAPED_CLEAN,
        },
        secret=_ID_SHAPED_TOKEN,
        clean=_ITEM,
        at="sourceItemId",
        family="openai-api-key",
    ),
    "addRelation.targetItemId": _HandAuthored(
        build=lambda value: {
            "op": "addRelation",
            "sourceItemId": _ITEM,
            "relationType": "related_to",
            "targetItemId": value,
        },
        secret=_ID_SHAPED_TOKEN,
        clean=_ID_SHAPED_CLEAN,
        at="targetItemId",
        family="openai-api-key",
    ),
    "addAlias.alias": _HandAuthored(
        build=lambda value: {"op": "addAlias", "alias": value, "itemId": _ITEM},
        secret=_ID_SHAPED_TOKEN,
        clean=_ID_SHAPED_CLEAN,
        at="alias",
        family="openai-api-key",
    ),
    "addEvidence.itemId": _HandAuthored(
        build=lambda value: {
            "op": "addEvidence",
            "itemId": value,
            "anchor": _evidence_anchor(_URI_CLEAN),
            "description": _SENTENCE_CLEAN,
        },
        secret=_ID_SHAPED_TOKEN,
        clean=_ITEM,
        at="itemId",
        family="openai-api-key",
    ),
    "addEvidence.description": _HandAuthored(
        build=lambda value: {
            "op": "addEvidence",
            "itemId": _ITEM,
            "anchor": _evidence_anchor(_URI_CLEAN),
            "description": value,
        },
        secret=_SENTENCE_SECRET,
        clean=_SENTENCE_CLEAN,
        at="description",
        family=HIGH_ENTROPY,
    ),
    "addEvidence.anchor.sourceUri": _HandAuthored(
        build=lambda value: {
            "op": "addEvidence",
            "itemId": _ITEM,
            "anchor": _evidence_anchor(value),
            "description": _SENTENCE_CLEAN,
        },
        secret=_URI_SECRET,
        clean=_URI_CLEAN,
        at="anchor.sourceUri",
        family=HIGH_ENTROPY,
    ),
    "registerSpecification.specId": _HandAuthored(
        build=lambda value: {
            "op": "registerSpecification",
            "specId": value,
            "itemId": _ITEM,
            "sourceUri": _URI_CLEAN,
            "format": "application/yaml",
        },
        secret=_ID_SHAPED_TOKEN,
        clean=_ID_SHAPED_CLEAN,
        at="specId",
        family="openai-api-key",
    ),
    "registerSpecification.sourceUri": _HandAuthored(
        build=lambda value: {
            "op": "registerSpecification",
            "specId": _ID_SHAPED_CLEAN,
            "itemId": _ITEM,
            "sourceUri": value,
            "format": "application/yaml",
        },
        secret=_URI_SECRET,
        clean=_URI_CLEAN,
        at="sourceUri",
        family=HIGH_ENTROPY,
    ),
    "registerSpecification.format": _HandAuthored(
        build=lambda value: {
            "op": "registerSpecification",
            "specId": _ID_SHAPED_CLEAN,
            "itemId": _ITEM,
            "sourceUri": _URI_CLEAN,
            "format": value,
        },
        secret=f"application/{_ID_SHAPED_TOKEN}",
        clean="application/yaml",
        at="format",
        family="openai-api-key",
    ),
    # The last two are the entries `propose` *does* emit -- but only ever from
    # `createItem`, whose namespace and owner it copies into the revision
    # metadata as well. So `_METADATA_PLANTS` above plants each of them in both
    # places at once, and the finding it asserts on can come from either:
    # measured 2026-08-24, dropping `namespace` or `owner` from
    # `_AUTHORED_OPERATION_FIELDS` alone leaves the entire suite green, because
    # `_AUTHORED_METADATA_FIELDS` still carries the same name. These two put the
    # secret where only the operation-level entry can find it, so each is
    # load-bearing on its own rather than as one half of a pair.
    "changeOwner.owner": _HandAuthored(
        build=lambda value: {"op": "changeOwner", "itemId": _ITEM, "owner": value},
        secret=f"platform-team-{PLANTED_TOKEN}",
        clean="platform-team",
        at="owner",
        family=HIGH_ENTROPY,
    ),
    "createItem.namespace": _HandAuthored(
        build=lambda value: {
            "op": "createItem",
            "itemId": _ID_SHAPED_CLEAN,
            "kind": "architecture",
            "namespace": value,
            "owner": "platform-team",
        },
        secret=f"architecture-{PLANTED_TOKEN}",
        clean="architecture",
        at="namespace",
        family=HIGH_ENTROPY,
    ),
    # The mirror of the two above, and the other half of the same shadowing. The
    # `at` of each carries a dot, so the operation-level pin below skips them:
    # what they drive is `_AUTHORED_METADATA_FIELDS`, reached through
    # `_metadata_strings`, not the operation tuple.
    "upsertRevision.metadata.namespace": _HandAuthored(
        build=lambda value: _second_upsert(namespace=value),
        secret=f"architecture-{PLANTED_TOKEN}",
        clean="architecture",
        at="metadata.namespace",
        family=HIGH_ENTROPY,
    ),
    "upsertRevision.metadata.owner": _HandAuthored(
        build=lambda value: _second_upsert(owner=value),
        secret=f"platform-team-{PLANTED_TOKEN}",
        clean="platform-team",
        at="metadata.owner",
        family=HIGH_ENTROPY,
    ),
    # `contentType`, `validFrom` and `validTo` (#336). `contentType` lands and is
    # published on every `knowledge.search`/`knowledge.get` result; the two date
    # fields are `date-time` by an annotation this pre-validation scan does not
    # enforce, so an author writes an arbitrary string into any of them. The
    # media type must still be a *valid* media type to be schema-valid, so its
    # secret is the `sk-`-prefixed `openai-api-key` shape (the only detectable
    # value the `^[a-z0-9][a-z0-9!#$&^_.+-]*/...$` pattern admits), exactly as
    # `registerSpecification.format` above; the date fields carry no pattern, so a
    # bare token is both admissible and detectable.
    "upsertRevision.metadata.contentType": _HandAuthored(
        build=lambda value: _second_upsert(contentType=value),
        secret=f"text/{_ID_SHAPED_TOKEN}",
        clean=MARKDOWN.value,
        at="metadata.contentType",
        family="openai-api-key",
    ),
    "upsertRevision.metadata.validFrom": _HandAuthored(
        build=lambda value: _second_upsert(validFrom=value),
        secret=PLANTED_TOKEN,
        clean="2026-08-24T00:00:00+00:00",
        at="metadata.validFrom",
        family=HIGH_ENTROPY,
    ),
    "upsertRevision.metadata.validTo": _HandAuthored(
        build=lambda value: _second_upsert(validTo=value),
        secret=PLANTED_TOKEN,
        clean="2026-08-24T00:00:00+00:00",
        at="metadata.validTo",
        family=HIGH_ENTROPY,
    ),
}


def _hand_authored_proposal(
    service: ProposalService, entry: _HandAuthored, value: str
) -> DraftedProposal:
    """The ordinary proposal with one hand-written operation appended to it.

    ``draft`` lays down the directory ``accept`` reads -- the ULID-named
    migration, the body at the sub-path its ``contentFile`` points to, and the
    pinned digest of that body -- and the migration YAML is then rewritten the
    way a contributor editing their own proposal before opening the pull request
    rewrites it. Building the whole directory from literals instead would test
    this file's copy of that layout, and would go green against an ``accept``
    that had stopped reading the real one.

    The appended operation is the *only* difference from a document every test
    above already accepts, so nothing but that operation can be what refuses.
    """
    drafted = service.draft(_request(CLEAN_BODY))
    document = yaml.safe_load(drafted.migration_file.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    operations = document["operations"]
    assert isinstance(operations, list)
    assert len(operations) == _HAND_AUTHORED_INDEX, (
        f"draft wrote {len(operations)} operations, so the appended one is not at index "
        f"{_HAND_AUTHORED_INDEX} and the locations asserted below name the wrong operation"
    )

    operation = entry.build(value)
    operations.append(operation)
    if operation["op"] == "upsertRevision":
        # `_body_moves` runs before the scan and refuses a `contentFile` with no
        # file behind it, so a second upsert without its body would be refused
        # for a reason that has nothing to do with the secret in it.
        body = drafted.directory / _SECOND_BODY_TAIL
        body.parent.mkdir(parents=True, exist_ok=True)
        body.write_bytes(_SECOND_BODY)
    drafted.migration_file.write_text(
        yaml.safe_dump(document, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    return drafted


#: Allowlist entries no fixture in this file can drive, each with the reason it
#: is unreachable rather than merely untested. Subtracted from the population the
#: pin below demands, so that "nothing tests this" and "nothing can test this"
#: stay different states with different evidence instead of one silent gap.
_UNDRIVABLE: Final[Mapping[str, str]] = {
    "metadata.tenantId": (
        "migration_engine refuses any value but 'local', on migrate validate and migrate apply "
        "alike (#63), so no token can reach an applied revision through it"
    ),
    "metadata.aclGroup": "the same, for any value but 'default'",
    "anchor.commitSha": (
        "the detector's class gate cannot fire on any value matching ^[0-9a-f]{7,64}$: the "
        "generic family needs an upper-case letter and every prefix family (sk-, ghp_, AKIA, "
        "xox, AIza) needs a character lower-case hex cannot spell. The scan runs before "
        "validation, so it is the class gate -- not the schema rejecting the document -- that "
        "makes commitSha unreportable. Pinned by "
        "test_a_lowercase_hex_anchor_sha_cannot_fire_the_detector"
    ),
    "anchor.blobSha": "the same pattern, the same class gate, the same pinning test",
}


def _allowlist_population() -> set[str]:
    """Every location the scan's four allowlists can produce, as ``<level>.<field>``.

    Read from the constants themselves rather than listed here, so a name added
    to any of them joins this population automatically and the pin below turns
    red until something drives it. The two structural walks are added by hand
    because :func:`_metadata_strings` reaches them by shape rather than through
    a tuple of names.
    """
    return (
        {f"migration.{name}" for name in _AUTHORED_MIGRATION_FIELDS}
        | {f"operation.{name}" for name in _AUTHORED_OPERATION_FIELDS}
        | {f"metadata.{name}" for name in _AUTHORED_METADATA_FIELDS}
        | {f"anchor.{name}" for name in _AUTHORED_ANCHOR_FIELDS}
        | {"metadata.labels", "metadata.scope.paths"}
    )


def _allowlist_entry_of(location: str) -> str:
    """Which allowlist entry produced a finding at ``location``.

    ``migration.operations[1].metadata.sourceAnchors[0].filePath`` becomes
    ``anchor.filePath``: the indices go, and the container decides the level.
    Order matters -- an anchor sits *inside* a metadata block, so its two
    prefixes are tried before the metadata one, and every operation-level prefix
    before the bare migration one.

    An unrecognised location raises rather than being dropped. A shape this does
    not know is a walk the pin below cannot account for, and silently ignoring it
    would let a whole new location family go untested while the assertion stayed
    green.
    """
    path = re.sub(r"\[\d+\]", "", location)
    for prefix, level in (
        ("migration.operations.metadata.sourceAnchors.", "anchor"),
        ("migration.operations.anchor.", "anchor"),
        ("migration.operations.metadata.", "metadata"),
        ("migration.operations.", "operation"),
        ("migration.", "migration"),
    ):
        if path.startswith(prefix):
            return f"{level}.{path.removeprefix(prefix)}"
    raise AssertionError(f"a finding location no rule here recognises: {location!r}")


def _a_proposal_carrying_a_secret_in_created_at(
    service: ProposalService, value: str = PLANTED_TOKEN
) -> DraftedProposal:
    """Draft a proposal, then rewrite its migration's top-level ``createdAt`` to ``value``.

    ``createdAt`` is Theurian's own output -- ``_migration_document`` stamps it
    from the clock -- so no *request* edit can carry a secret there, which is why
    it is absent from :data:`_METADATA_PLANTS` and undrivable by
    :data:`_HAND_AUTHORED` (that helper only *appends* operations, never touches
    the top level). ``accept`` reads a *committed* document, though, and a
    contributor may hand-edit any field: this rewrites the drafted migration the
    way a contributor edits their own proposal before opening the pull request,
    at the one level the operation-append path leaves alone.
    """
    drafted = service.draft(_request(CLEAN_BODY))
    document = yaml.safe_load(drafted.migration_file.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    document["createdAt"] = value
    drafted.migration_file.write_text(
        yaml.safe_dump(document, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    return drafted


def _entries_reached_by(drafted: DraftedProposal) -> set[str]:
    """The allowlist entries the document scan actually reports for one proposal."""
    document = yaml.safe_load(drafted.migration_file.read_text(encoding="utf-8"))
    return {_allowlist_entry_of(f.location) for f in _document_findings(document)}


def test_every_drivable_allowlist_entry_has_a_fixture_that_reaches_it(
    service: ProposalService,
) -> None:
    """The pin that stops this class reopening on any of the four allowlists.

    #336 shipped with eleven of ``_AUTHORED_OPERATION_FIELDS``' thirteen names
    driven by nothing at all, and with ``namespace`` and ``owner`` driven in two
    tuples at once by a plant that could not say which of them found it -- a
    shadow, which is the same gap wearing the other tuple's name. Both are closed
    above. Nothing structural stopped either, and nothing would stop the next
    one: the allowlists grow whenever the schema grows a string field, and the
    natural edit is the tuple alone.

    **The observed side is measured, not declared.** Each fixture is built and
    its document put through the real walker, and what is collected is the
    finding's *location*. That is what makes a shadow impossible to mistake for
    coverage: ``operations[0].namespace`` and ``operations[1].metadata.namespace``
    are different locations for the same word, so a plant that reaches only one
    of them cannot report the other as driven.

    **Equality, in both directions.** A population entry nothing reaches is the
    gap; a reached entry outside the population is a fixture whose finding is
    coming from somewhere the allowlists do not name, which is a defect in this
    file's model of the scan rather than a harmless extra.

    What this deliberately does *not* do is prove the ``accept`` path refuses --
    it calls the walker directly, because several fixtures here carry operations
    the pre-check would refuse for their own unrelated reasons. Refusal is the
    per-entry tests' job; coverage of the population is this one's.
    """
    reached: set[str] = set()

    for field in _METADATA_PLANTS:
        reached |= _entries_reached_by(service.draft(_with_a_planted_secret(field)))
    for entry in _HAND_AUTHORED.values():
        reached |= _entries_reached_by(_hand_authored_proposal(service, entry, entry.secret))
    # `createdAt` is neither a request field nor an appended operation, so it has
    # its own mechanism -- a top-level rewrite -- and would otherwise be the one
    # newly scanned field (#336) with no fixture reaching it.
    reached |= _entries_reached_by(_a_proposal_carrying_a_secret_in_created_at(service))
    # `contentFile` joined the operation allowlist in #349: its parsed value is
    # scanned, so a secret-shaped body path reaches `operation.contentFile`
    # through the field walk, where no metadata or hand-authored plant does.
    reached |= _entries_reached_by(
        _a_proposal_whose_body_lands_at(service, f"architecture/{PLANTED_TOKEN}.md")
    )

    expected = _allowlist_population() - set(_UNDRIVABLE)
    assert reached == expected, (
        f"allowlist entries no fixture reaches: {sorted(expected - reached)}; "
        f"reached but named by no allowlist: {sorted(reached - expected)}"
    )


def test_a_secret_in_the_top_level_created_at_is_refused_without_reproducing_it(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """``createdAt`` is scanned like any author-controlled string, and redacted (#336).

    ``createdAt`` is Theurian's own output, but ``accept`` reads a *committed*
    document a contributor may hand-edit. Before #336 scanned it, a secret there
    was caught only by the rehearsal's RFC 3339 parse -- which reported the value
    *verbatim* in its refusal and in the ``accept --json`` payload something
    logs. Scanning it pre-empts that: the scan runs before the rehearsal and
    refuses with a **redacted** finding that names the field and quotes at most
    four characters, and nothing lands.

    This is the disclosure the round-two review surfaced -- the date-time fields'
    own refusal echoing the secret -- closed for the top-level field the
    :data:`_HAND_AUTHORED` metadata plants cannot reach. The redaction assertion
    is what distinguishes the scan's refusal from the rehearsal's: a test that
    only checked the exception type would pass under either, and only one of them
    keeps the credential out of the message.
    """
    assert not paths.config.exists(), "the fixture wrote a config file; the default is untested"
    drafted = _a_proposal_carrying_a_secret_in_created_at(service)
    before = {p.relative_to(drafted.directory).as_posix() for p in drafted.directory.rglob("*")}

    with pytest.raises(ProposalError) as caught:
        service.accept(drafted.proposal_id)

    message = str(caught.value)
    assert HIGH_ENTROPY in message, f"a secret in createdAt was not what refused: {caught.value}"
    assert "migration.createdAt" in message, f"the refusal does not name the field: {message}"
    assert PLANTED_TOKEN not in message, (
        f"the scan refusal reproduced the secret the rehearsal used to echo verbatim: {message}"
    )
    assert PLANTED_TOKEN not in caught.value.remedy, (
        f"the remedy reproduced the secret: {caught.value.remedy!r}"
    )
    assert {
        p.relative_to(drafted.directory).as_posix() for p in drafted.directory.rglob("*")
    } == before, "the proposal directory changed on a refused acceptance"
    assert not (paths.migrations / drafted.migration_file.name).exists(), (
        "the migration landed despite carrying a secret in createdAt"
    )


@pytest.mark.parametrize("name", list(_HAND_AUTHORED))
def test_a_hand_authored_operation_is_schema_valid_with_and_without_its_planted_secret(
    service: ProposalService, name: str
) -> None:
    """The guard that keeps the tests below about the scan and not about the schema.

    The scan runs *before* stage-1 validation, so a document the schema would
    reject still reaches it -- which means a fixture that quietly violated a
    ``pattern`` would produce a refusal that looked identical and proved nothing
    about the allowlist. Six of the table's fields are ``$defs/itemId`` and one
    is a media type; a base64url token in any of them is not a document
    ``accept`` could ever have applied, so the refusal it produced would be a
    refusal of an impossible input.

    Both versions are validated. The planted one is what the tests below feed
    ``accept``; the clean one is what the control feeds it, and a control that
    was schema-invalid would be a control refused for its own reason.
    """
    entry = _HAND_AUTHORED[name]

    for value in (entry.secret, entry.clean):
        drafted = _hand_authored_proposal(service, entry, value)
        document = yaml.safe_load(drafted.migration_file.read_text(encoding="utf-8"))

        _validate(document)


@pytest.mark.parametrize("name", list(_HAND_AUTHORED))
def test_a_hand_authored_operation_carries_no_finding_until_its_field_is_planted(
    service: ProposalService, name: str
) -> None:
    """The control on the fixture: the operation itself is clean, the plant is not.

    Two failures this catches, both of which would leave the refusal tests below
    green while testing nothing:

    * the operation's *other* fields being detectable -- a ``sourceUri`` or a
      ``specId`` chosen carelessly -- so the refusal fires on a field this entry
      is not about;
    * the planted value not being detectable at all in the field's schema-legal
      spelling, which is a live risk here because six of these fields are
      lowercase-only and the generic family's class gate needs an upper-case
      character. A refusal test built on such a value would be red for the
      fixture's reason rather than the control's.

    The whole migration text is scanned rather than the field alone, so this
    also pins what #336 is about: the value is in the document that would land
    in ``.theurian/migrations/``.
    """
    entry = _HAND_AUTHORED[name]

    clean = _hand_authored_proposal(service, entry, entry.clean)
    planted = _hand_authored_proposal(service, entry, entry.secret)

    assert scan_text(clean.migration_file.read_text(encoding="utf-8")) == (), (
        f"the {name} operation is reported as carrying a secret before anything was planted "
        f"in it, so a refusal on it names the wrong field"
    )
    planted_text = planted.migration_file.read_text(encoding="utf-8")
    assert entry.secret in planted_text, f"{name} never reached the migration document"
    assert [f.family for f in scan_text(planted_text)] == [entry.family], (
        f"the detector reports {[f.family for f in scan_text(planted_text)]} for a secret "
        f"planted in {name}, not exactly one {entry.family}"
    )


@pytest.mark.parametrize("name", list(_HAND_AUTHORED))
def test_a_secret_in_a_hand_authored_operation_is_refused_and_the_field_is_named(
    service: ProposalService, paths: ProjectPaths, name: str
) -> None:
    """SEC-11's gate covers the operations a contributor writes, not only the two we do.

    ``propose`` emits ``createItem`` and ``upsertRevision``; ``accept`` takes any
    of the schema's fourteen operation types, because a proposal directory is a
    contributor's committed input (ADR-0013 point 7). A control that covered only
    what the generator produces would be a control on Theurian's own output, and
    the input it is there to judge would walk past it -- ``registerSpecification``
    carries a ``sourceUri`` and ``deprecateItem`` a free-text ``reason``, both of
    which land in ``.theurian/migrations/`` and are read by every later
    ``migrate apply``.

    The location is asserted whole -- operation index and field -- rather than by
    field name alone. Which operation a secret is in is the actionable half of the
    report for a migration carrying several, and
    :class:`~theurian.application.proposal_service.ProposalSecretFinding`
    publishes that spelling in its own docstring. If it is deliberately changed,
    this is the test that should have to be changed with it.
    """
    entry = _HAND_AUTHORED[name]
    assert not paths.config.exists(), "the fixture wrote a config file; the default is untested"
    drafted = _hand_authored_proposal(service, entry, entry.secret)

    with pytest.raises(ProposalError) as caught:
        service.accept(drafted.proposal_id)

    message = str(caught.value)
    assert entry.family in message, (
        f"a secret in {name} was not what refused the acceptance: {caught.value}"
    )
    assert f"migration.operations[{_HAND_AUTHORED_INDEX}].{entry.at}" in message, (
        f"the refusal does not name the field the secret is in: {message}"
    )


@pytest.mark.parametrize("name", list(_HAND_AUTHORED))
def test_a_refused_hand_authored_operation_consumes_nothing_and_is_not_quoted_back(
    service: ProposalService, paths: ProjectPaths, name: str
) -> None:
    """The two properties an exception-type assertion does not reach (#307, SEC-11).

    An implementation that refused *after* moving the files would satisfy the
    test above while leaving the credential in the tree ``migrate apply`` reads,
    and one that assembled its message from field *values* rather than from
    findings would route around the four-character bound
    :class:`~theurian.security.content_secrets.SecretFinding` enforces on itself.
    Both matter more on this path than on the body path: a hand-authored
    operation is the shape a contributor arrives with, and the refusal is printed
    to a terminal and published under ``accept --json`` into something that logs.

    **``match`` is what keeps this test about the scan.** A bare
    ``pytest.raises(ProposalError)`` is satisfied by any refusal, and several of
    these fixtures have a second reason to be refused -- the two
    ``upsertRevision`` cases add a revision for an item no ``createItem``
    introduces, which the pre-check rejects on its own. Reproduced: with
    ``namespace`` dropped from ``_AUTHORED_METADATA_FIELDS`` the scan finds
    nothing, the pre-check refuses instead, and every assertion below still
    holds. Naming the family makes the refusal under test the secret one, which
    also re-pins the ordering -- the scan runs before the pre-check, so the
    secret is what the contributor is told about first.
    """
    entry = _HAND_AUTHORED[name]
    drafted = _hand_authored_proposal(service, entry, entry.secret)
    before = {p.relative_to(drafted.directory).as_posix() for p in drafted.directory.rglob("*")}

    with pytest.raises(ProposalError, match=re.escape(entry.family)) as caught:
        service.accept(drafted.proposal_id)

    assert {
        p.relative_to(drafted.directory).as_posix() for p in drafted.directory.rglob("*")
    } == before, "the proposal directory changed on a refused acceptance"
    assert not (paths.migrations / drafted.migration_file.name).exists(), (
        "the migration landed in .theurian/migrations/ despite the refusal -- the secret is now "
        "in the tree the migration set reads"
    )
    assert not drafted.body_destination.exists()
    assert entry.secret not in str(caught.value), (
        f"the refusal reproduced the secret it refused: {caught.value}"
    )
    assert entry.secret not in caught.value.remedy, (
        f"the remedy reproduced the secret it refused: {caught.value.remedy!r}"
    )


# -- the allowlist is the schema's population, or it drifts (#336) -----------
#
# `_AUTHORED_*` is a hand-maintained allowlist, and the schema is the authority
# for which string fields a migration may carry. Nothing binds the two: a
# schema that grows a string field grows the set an author can put a credential
# in, and the natural edit is the schema alone. The adversarial round proved
# the gap live -- mutation `n1` (the schema gains an unscanned metadata string)
# survived the whole suite, and two fields, `validFrom`/`validTo`, were already
# missing from both the allowlist and the docstring's derived-exclusion list.
#
# The pin below reads *both* sides from source -- the allowlist from the
# constants, the population from the schema JSON -- and demands every schema
# string field be either scanned or excluded for a reason that is *tested*, not
# asserted in a comment. A field that is neither reddens it.

_MIGRATION_SCHEMA: Final[Mapping[str, Any]] = json.loads(
    (SCHEMAS / "migrations" / "migration.schema.json").read_text(encoding="utf-8")
)
_SCHEMA_DEFS: Final[Mapping[str, Any]] = _MIGRATION_SCHEMA["$defs"]


def _resolve_ref(node: Mapping[str, Any]) -> Mapping[str, Any]:
    """Follow a ``$ref`` to the ``$def`` it names, chasing chains to the leaf."""
    while "$ref" in node:
        node = _SCHEMA_DEFS[node["$ref"].split("/")[-1]]
    return node


def _is_string_leaf(node: Mapping[str, Any]) -> bool:
    """Whether ``node`` describes a scalar that carries a string an author writes.

    A plain string, a nullable string (``["string", "null"]``), a ``const`` or an
    ``enum`` of strings, or a ``oneOf`` any branch of which is one of those -- the
    last is ``expectedRevision``, a ULID-or-null. An object, an array, a number
    or an integer is not a string leaf.
    """
    node = _resolve_ref(node)
    declared = node.get("type")
    if declared == "string" or (isinstance(declared, list) and "string" in declared):
        return True
    if isinstance(node.get("const"), str):
        return True
    if "enum" in node:
        return True
    if "oneOf" in node:
        return any(_is_string_leaf(branch) for branch in node["oneOf"])
    return False


def _is_string_array(node: Mapping[str, Any]) -> bool:
    """Whether ``node`` is an array whose items are string leaves (``labels``, ``dependsOn``)."""
    node = _resolve_ref(node)
    return node.get("type") == "array" and _is_string_leaf(node.get("items", {}))


#: The three ``$defs`` that are their own population buckets rather than ordinary
#: sub-objects. The recursive walk below does not descend into a reference to one
#: of them: ``operation`` and ``revisionMetadata`` are enumerated by their own
#: loops in :func:`_schema_string_properties`, and ``sourceAnchor`` collapses to
#: the ``anchor.`` bucket wherever it appears -- an anchor's fields are keyed
#: ``anchor.<field>`` whether they sit on an ``addEvidence`` operation or in a
#: ``sourceAnchors`` list, exactly as the walker keys them and as
#: :func:`_allowlist_entry_of` maps a finding location back.
_BUCKET_ROOT_DEFS: Final = frozenset({"operation", "revisionMetadata", "sourceAnchor"})


def _ref_name(node: Mapping[str, Any]) -> str | None:
    """The ``$defs`` name a ``$ref`` node points at, or ``None`` if it is not a ref."""
    ref = node.get("$ref")
    return ref.split("/")[-1] if isinstance(ref, str) else None


def _references_bucket_root(node: Mapping[str, Any]) -> bool:
    """Whether ``node`` is a reference to a bucket-root def, or an array of one."""
    if _ref_name(node) in _BUCKET_ROOT_DEFS:
        return True
    if node.get("type") == "array":
        items = node.get("items", {})
        return isinstance(items, Mapping) and _ref_name(items) in _BUCKET_ROOT_DEFS
    return False


def _string_fields_in(
    obj_schema: Mapping[str, Any], prefix: str
) -> Iterator[tuple[str, Mapping[str, Any]]]:
    """Every string / string-array field an object schema declares, keyed by dotted path.

    Recurses into every inline nested object and object-array, so a string field
    the schema grows at any depth -- ``scope.owner``, a new
    ``metadata.<obj>.<field>``, a ``metadata.<arr>[].<field>`` -- surfaces under
    its own key rather than being missed by a walk that stopped at the object's
    own properties. ``prefix`` is the bucket-rooted path already accumulated
    (``"metadata"``, ``"metadata.scope"``); a nested object extends it with
    ``.<name>`` and an object-array with ``.<name>[]``, so the key a new field
    gets is one no allowlist constant names and the pin below reddens on it.

    A reference to a bucket-root def (:data:`_BUCKET_ROOT_DEFS`) is not descended
    here. ``operation`` and ``revisionMetadata`` are enumerated by their own loops
    in :func:`_schema_string_properties`, and ``sourceAnchor`` is enumerated once
    under the ``anchor`` prefix and collapses there wherever it appears; descending
    a reference to any of them would re-key an existing bucket rather than reach a
    field the walker's four hand-listed levels miss, which is the only thing this
    recursion exists to catch.
    """
    obj_schema = _resolve_ref(obj_schema)
    for name, node in obj_schema.get("properties", {}).items():
        if _references_bucket_root(node):
            continue
        if _is_string_leaf(node) or _is_string_array(node):
            yield f"{prefix}.{name}", node
            continue
        resolved = _resolve_ref(node)
        if resolved.get("type") == "object":
            yield from _string_fields_in(resolved, f"{prefix}.{name}")
        elif resolved.get("type") == "array":
            items = resolved.get("items", {})
            if isinstance(items, Mapping) and _resolve_ref(items).get("type") == "object":
                yield from _string_fields_in(_resolve_ref(items), f"{prefix}.{name}[]")


def _schema_string_properties() -> dict[str, Mapping[str, Any]]:
    """Every string / string-array field the schema declares, keyed ``<level>.<field>``.

    The four levels mirror the walker's own descent so the population lines up
    with :func:`_allowlist_population` term for term: ``migration`` (the top
    level), ``operation`` (the union across all fourteen ``op*`` definitions,
    which is what the walker reads because ``op`` is untrusted until validation),
    ``metadata`` (a revision's own block, walked to any depth) and ``anchor`` (a
    source anchor, wherever it appears, collapsed to one bucket exactly as the
    walker and :func:`_allowlist_entry_of` collapse it). :func:`_string_fields_in`
    recurses into every inline nested object and object-array within each level,
    so a string field the schema grows at any depth -- not only the four the
    walker hand-lists -- joins this population and the pin below reddens until it
    is scanned or excluded. The value kept is the field's *subschema*, so the
    exclusion test below can plant a value against the field's own pattern rather
    than against this file's idea of it.
    """
    population: dict[str, Mapping[str, Any]] = {}

    population.update(_string_fields_in(_MIGRATION_SCHEMA, "migration"))

    for branch in _SCHEMA_DEFS["operation"]["oneOf"]:
        op_schema = _SCHEMA_DEFS[branch["$ref"].split("/")[-1]]
        population.update(_string_fields_in(op_schema, "operation"))

    population.update(_string_fields_in(_SCHEMA_DEFS["revisionMetadata"], "metadata"))
    population.update(_string_fields_in(_SCHEMA_DEFS["sourceAnchor"], "anchor"))

    return population


#: Every schema string field that is deliberately *not* scanned, with the
#: category of its mechanical reason. Each reason is exercised by
#: :func:`test_a_schema_excluded_field_admits_no_reported_secret`: the schema
#: rejects a planted secret, and every value it admits is undetectable.
#:
#: ``operation.contentFile`` is **not** here: the review of #349 moved it into
#: :data:`_AUTHORED_OPERATION_FIELDS` (its parsed value carries what its bytes and
#: its resolved landed path can miss), so it is scanned rather than excluded.
#:
#: What is **not** here is the leftover: ``migration.createdAt``,
#: ``metadata.contentType``, ``metadata.validFrom`` and ``metadata.validTo``.
#: Those are author-controlled strings the schema admits a credential into and
#: no mechanism excludes, so the pin reddens on them until they move into the
#: allowlist -- which is the drift #336 exists to close. ``createdAt`` is folded
#: in with ``validFrom``/``validTo`` on purpose: measured 2026-08-24, a secret in
#: any of the three is refused by the accept-path rehearsal's RFC-3339 parse
#: *and reproduced verbatim in that refusal*, so all three behave identically and
#: splitting them would be an untested distinction.
_EXCLUDED: Final[Mapping[str, tuple[str, str]]] = {
    "migration.apiVersion": (
        "const",
        "a const; the only value the schema admits is 'theurian.dev/v1'",
    ),
    "migration.id": ("ulid", "$defs/ulid: upper-case Crockford base32, which the class gate bars"),
    "migration.dependsOn": ("ulid", "an array of $defs/ulid, each barred by the class gate"),
    "operation.op": ("const", "a per-operation const discriminator; no free value is admitted"),
    "operation.kind": ("enum", "$defs/kind, a fixed vocabulary of eleven lower-case words"),
    "operation.sensitivity": ("enum", "$defs/sensitivity, four fixed labels"),
    "operation.trustLevel": ("enum", "$defs/trustLevel, four fixed labels"),
    "operation.revisionId": ("ulid", "$defs/ulid, barred by the class gate"),
    "operation.expectedRevision": ("ulid", "a $defs/ulid or null, barred by the class gate"),
    "operation.contentSha256": (
        "hex",
        "^[0-9a-f]{64}$: lower-case hex, which the generic class gate (needs upper case) bars",
    ),
    "operation.relationType": ("enum", "$defs/relationType, fourteen fixed labels"),
    "operation.status": ("enum", "the specification status enum: draft/active/superseded/retired"),
    "metadata.kind": ("enum", "$defs/kind"),
    "metadata.status": ("enum", "$defs/status, six fixed labels"),
    "metadata.trustLevel": ("enum", "$defs/trustLevel"),
    "metadata.sensitivity": ("enum", "$defs/sensitivity"),
}

#: A real ULID -- upper-case Crockford base32, no I/L/O/U -- the fixture guard
#: accepts. The highest-entropy value ``$defs/ulid`` admits, kept to show the
#: class gate still does not fire on it.
_A_REAL_ULID: Final = "01K3Z8Q9V4MRB7T2XNFCD5HGJW"

#: 64 lower-case hex characters: the highest-entropy value ``contentSha256`` and
#: the anchor SHAs admit, and still undetectable because the class gate wants an
#: upper-case letter.
_A_HEX64: Final = hashlib.sha256(b"theurian content-sha fixture (#336)").hexdigest()


def _subschema_admits(subschema: Mapping[str, Any], value: object) -> bool:
    """Whether the field's own subschema, with the shared ``$defs``, accepts ``value``."""
    root = {"$defs": _SCHEMA_DEFS, **subschema}
    return Draft202012Validator(root).is_valid(value)


def _admissible_literals(subschema: Mapping[str, Any]) -> list[str]:
    """The string values a const or enum field admits, for the undetectable check."""
    node = _resolve_ref(subschema)
    if isinstance(node.get("const"), str):
        return [node["const"]]
    return [value for value in node.get("enum", []) if isinstance(value, str)]


def test_the_allowlist_covers_every_string_field_the_schema_declares(
    service: ProposalService,
) -> None:
    """The load-bearing pin: the scan's population is the schema's, or it has drifted.

    This is the closure argument for the whole ``n1`` class -- "the schema grew a
    string field the scan does not read". It cannot be closed by listing the
    fields anyone thought of, because that list is exactly what drifts. So both
    sides are read from source: the scanned set from the ``_AUTHORED_*``
    constants (:func:`_allowlist_population`), the population from the schema JSON
    (:func:`_schema_string_properties`), which recurses into every nested object
    and object-array the schema declares -- so a string field added at any depth
    (``scope.owner``, a new ``metadata.<obj>.<field>``, a
    ``metadata.<arr>[].<field>``) is in the population rather than missed by a walk
    that stopped at the levels the walker hand-lists. Every schema string field
    must be one of two things, and the pin fails naming any that is neither:

    * **scanned** -- its name reaches the walker through an allowlist constant, or
    * **excluded** -- it is in :data:`_EXCLUDED`, whose every entry is proved
      unable to carry a reported secret by a test, not by a sentence.

    At HEAD the pin is **green**: every string field the schema declares is either
    scanned or excluded-with-a-tested-mechanism, and there is no third state. The
    four fields it names as neither when they are absent -- ``metadata.contentType``,
    ``metadata.validFrom``, ``metadata.validTo`` and ``migration.createdAt`` -- are
    author-controlled strings the schema admits a credential into; before #336's
    fix they were in no allowlist and no exclusion, so this pin was red on them.
    It went green in the commit that added those four to the allowlist (not to
    :data:`_EXCLUDED`): they may not move into :data:`_EXCLUDED`, because
    :func:`test_a_schema_excluded_field_admits_no_reported_secret` would then fail
    on them -- the schema admits a detectable value, so no exclusion mechanism
    holds -- which is why the same failure would list them again the moment they
    were dropped from the scanned set.

    The reverse direction is asserted too: an allowlist name the schema does not
    declare is a scan reading a field that cannot exist, which is a defect in the
    allowlist rather than a harmless extra.
    """
    population = set(_schema_string_properties())
    scanned = _allowlist_population()
    excluded = set(_EXCLUDED)

    phantom = scanned - population
    assert not phantom, f"the allowlist scans fields the schema does not declare: {sorted(phantom)}"
    assert set(_UNDRIVABLE) <= population, (
        f"_UNDRIVABLE names fields the schema does not declare: "
        f"{sorted(set(_UNDRIVABLE) - population)}"
    )
    misclassified = excluded & scanned
    assert not misclassified, (
        f"a field is both scanned and excluded, which hides which one is load-bearing: "
        f"{sorted(misclassified)}"
    )

    unclassified = population - scanned - excluded
    assert not unclassified, (
        "schema string fields that are neither scanned nor excluded -- an author can land a "
        f"credential in each and nothing reads it: {sorted(unclassified)}. Move each into an "
        "_AUTHORED_* constant (scanned) or, only if a mechanism truly bars a reported secret, "
        "into _EXCLUDED with a tested reason."
    )


@pytest.mark.parametrize(
    "name", [name for name, (category, _) in _EXCLUDED.items() if category != "scope"]
)
def test_a_schema_excluded_field_admits_no_reported_secret(name: str) -> None:
    """Every exclusion is a *tested* mechanism, so no field is excluded by assertion alone.

    An allowlist gap and a genuine exclusion look identical in a comment: both say
    "this field is not scanned". They differ in whether a secret *can* live there.
    For each excluded field this plants a real detector-family value and shows two
    things:

    * **the schema rejects that shape** -- the field's own subschema (const, enum,
      ULID pattern or lower-case-hex pattern) refuses the planted value, so a
      document carrying it is not one ``migrate apply`` would ever accept; and
    * **every value the schema admits is undetectable** -- each const/enum literal,
      and the highest-entropy value a ULID or hex field admits, scans clean.

    Together these are stronger than "the scan does not look here": they are "there
    is nothing here to find". If a future schema change loosened one of these
    fields so a credential became admissible, the first assertion would fail and
    force it out of :data:`_EXCLUDED` and into the scanned set -- which is exactly
    what must happen to ``contentType``/``validFrom``/``validTo`` now.
    """
    category, _reason = _EXCLUDED[name]
    subschema = _schema_string_properties()[name]

    planted: object = [PLANTED_TOKEN] if _is_string_array(subschema) else PLANTED_TOKEN
    assert not _subschema_admits(subschema, planted), (
        f"{name}'s subschema admits a base64url token, so excluding it hides a real gap: "
        f"the scan runs before validation and this value would reach a landed migration"
    )

    if category in ("const", "enum"):
        for literal in _admissible_literals(subschema):
            assert scan_text(literal) == (), (
                f"{name} admits {literal!r}, which the detector reports -- the exclusion is unsafe"
            )
    elif category == "ulid":
        admissible: object = [_A_REAL_ULID] if _is_string_array(subschema) else _A_REAL_ULID
        assert _subschema_admits(subschema, admissible), (
            f"{_A_REAL_ULID!r} is not a valid value for {name}; the class-gate check is vacuous"
        )
        assert scan_text(_A_REAL_ULID) == (), "a ULID is reported as a secret; the exclusion fails"
    elif category == "hex":
        assert _subschema_admits(subschema, _A_HEX64), (
            f"{_A_HEX64!r} is not admitted by {name}; the class-gate check is vacuous"
        )
        assert scan_text(_A_HEX64) == (), "lower-case hex is reported; the exclusion fails"
    else:  # pragma: no cover - the parametrization filters 'scope' out
        raise AssertionError(f"unknown exclusion category for {name}: {category}")


def test_a_secret_shaped_content_file_is_refused_by_the_artifact_scan(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """A secret-shaped ``contentFile`` is refused, and scanned as a parsed field (#336, #349).

    Before the review of #349 the parsed ``contentFile`` was excluded from the
    field walk and left to the artifact channels. That was a gap: a credential in
    a ``..``-removed segment or spelled with YAML escapes reaches neither the
    landed path nor the raw bytes, so ``contentFile`` joined
    :data:`_AUTHORED_OPERATION_FIELDS`. Here the credential survives resolution,
    so it is over-determined -- both the field walk and the landed-path channel
    see it -- which is what lets this assert both halves:

    * the acceptance **is** refused, naming the family, and nothing lands; and
    * :func:`_document_findings` -- the field walk alone -- now reports the
      credential, and *only* it, at ``migration.operations[1].contentFile``, so
      the refusal reaches the field walk rather than only the artifact channels.
      The ``..``-removed and escaped faces the landed path and the raw bytes miss
      are pinned separately, by
      ``test_an_escaped_traversal_content_file_is_refused_under_block``.

    The body is moved to the path the ``contentFile`` names, unlike the version
    of this test that preceded #349: a ``contentFile`` that backs no file is
    refused by ``_body_moves`` *before* the scan runs, so a fixture that left the
    body where ``draft`` put it would never reach the control under test at all.
    """
    assert not paths.config.exists(), "the fixture wrote a config file; the default is untested"
    tail = f"architecture/{PLANTED_TOKEN}.md"
    drafted = _a_proposal_whose_body_lands_at(service, tail)
    document = yaml.safe_load(drafted.migration_file.read_text(encoding="utf-8"))

    with pytest.raises(ProposalError) as caught:
        service.accept(drafted.proposal_id)

    assert HIGH_ENTROPY in str(caught.value), (
        f"a credential in contentFile was not what refused the acceptance: {caught.value}"
    )
    assert {f.location for f in _document_findings(document)} == {
        f"migration.operations[{_UPSERT_INDEX}].contentFile"
    }, (
        "the field walk did not report the secret-shaped contentFile at its own location; #349 "
        "moved contentFile into the scanned set"
    )
    assert PLANTED_TOKEN not in str(caught.value), (
        f"the refusal reproduced the credential it refused: {caught.value}"
    )
    assert not (paths.migrations / drafted.migration_file.name).exists(), (
        "the migration landed despite naming a body whose path is a credential"
    )
    assert not (paths.knowledge / tail).exists(), "the body landed under a credential-shaped name"


def _xnn(value: str) -> str:
    """``value`` with every character spelled as a ``\\xNN`` YAML escape."""
    return "".join(f"\\x{ord(character):02x}" for character in value)


def _unnnn(value: str) -> str:
    """``value`` with every character spelled as a ``\\uNNNN`` YAML escape."""
    return "".join(f"\\u{ord(character):04x}" for character in value)


#: The three ways one hand-authored ``contentFile`` can carry a credential in a
#: path segment that ``..`` then removes -- as plain text, and spelled so the
#: migration's raw bytes never hold the decoded value. Each parses back to the
#: same traversal, the ``..`` drops the secret-bearing segment from the landed
#: path, and the two escaped spellings never reach the raw bytes, so the parsed
#: ``contentFile`` field walk is the only channel that can see any of them (#349).
_TRAVERSAL_SEGMENT_SPELLINGS: Final[Mapping[str, str]] = {
    "plain": PLANTED_TOKEN,
    "x-escaped": _xnn(PLANTED_TOKEN),
    "u-escaped": _unnnn(PLANTED_TOKEN),
}


@pytest.mark.parametrize("spelling", list(_TRAVERSAL_SEGMENT_SPELLINGS))
def test_an_escaped_traversal_content_file_is_refused_under_block(
    service: ProposalService, paths: ProjectPaths, spelling: str
) -> None:
    """A credential in a ``..``-removed ``contentFile`` segment is caught by the field walk (#349).

    The HIGH the review of #349 reproduced: a hand-authored ``contentFile`` naming
    ``../knowledge/<secret>/../architecture/note.md`` lands its body at
    ``architecture/note.md`` -- the ``<secret>`` segment collapsed away -- so the
    landed-path channel never sees it, and when the segment is spelled ``\\xNN`` or
    ``\\uNNNN`` the migration's raw bytes never hold the decoded value either. The
    parsed ``contentFile``, now in :data:`_AUTHORED_OPERATION_FIELDS`, is the only
    channel that carries it, which is why pattern enumeration could not close the
    class -- three spellings, one field.

    The ``plain`` case is the control: the same traversal, readable, where the raw
    bytes *do* spell the secret, so it isolates the ``..`` collapse (the landed
    path is clean under every spelling) from the escape decoding the other two add.
    """
    assert not paths.config.exists(), "the fixture wrote a config file; the default is untested"
    segment = _TRAVERSAL_SEGMENT_SPELLINGS[spelling]
    landed_tail = "architecture/note.md"
    drafted = _a_proposal_whose_body_lands_at(
        service, landed_tail, spelled=f'"../knowledge/{segment}/../architecture/note.md"'
    )
    text = drafted.migration_file.read_text(encoding="utf-8")

    assert PLANTED_TOKEN not in landed_tail, "the fixture leaves the secret in the landed path"
    if spelling != "plain":
        assert scan_text(text) == (), (
            f"the {spelling} contentFile is detectable in the migration's own bytes, so this no "
            f"longer isolates the parsed-field channel: {scan_text(text)}"
        )
    parsed = yaml.safe_load(text)["operations"][_UPSERT_INDEX]["contentFile"]
    assert parsed.endswith(f"/{PLANTED_TOKEN}/../architecture/note.md"), (
        f"the contentFile did not parse back to the traversal this case is about: {parsed!r}"
    )

    with pytest.raises(ProposalError) as caught:
        service.accept(drafted.proposal_id)

    assert HIGH_ENTROPY in str(caught.value), (
        f"a credential in a ..-removed contentFile segment was not what refused: {caught.value}"
    )
    assert PLANTED_TOKEN not in str(caught.value) and PLANTED_TOKEN not in caught.value.remedy, (
        f"the refusal reproduced the credential it refused: {caught.value}"
    )
    assert not (paths.migrations / drafted.migration_file.name).exists(), (
        "the migration landed despite naming a contentFile whose removed segment is a credential"
    )
    assert not (paths.knowledge / landed_tail).exists(), "the body landed despite the refusal"


@pytest.mark.parametrize("value", [_A_HEX64, "abcdef1", "a" * 40, "f" * 64, "0123456789abcdef"])
def test_a_lowercase_hex_anchor_sha_cannot_fire_the_detector(value: str) -> None:
    """``commitSha``/``blobSha`` are scanned but unreportable, and here is why (SEC-11).

    :data:`_UNDRIVABLE` records that the anchor SHAs cannot be driven to a finding.
    Its reason must be the class gate, not the schema: the scan runs *before*
    validation, so it is not the schema pinning ``^[0-9a-f]{7,64}$`` that stops a
    finding -- the scan would happily look -- but that no value of that shape
    clears the generic family's class gate, which needs an upper-case letter, and
    no prefix family (``sk-``, ``ghp_``, ``AKIA``...) can be spelled in lower-case
    hex. This plants representative values across the whole length range and shows
    the detector reports nothing, so the recorded reason has teeth.
    """
    assert re.fullmatch(r"[0-9a-f]{7,64}", value), "the fixture value is not schema-admissible hex"

    assert scan_text(value) == (), (
        f"the detector reported a finding for lower-case hex {value!r}; the _UNDRIVABLE reason "
        "for commitSha/blobSha (the class gate cannot fire) is false"
    )


# -- the walker's core, held against the mutations round one survived (#336) --
#
# Round one's adversarial pass found these mutations of `_document_findings` and
# `_scan_for_secrets` survived the whole suite. Each test below was confirmed to
# go red under its named mutation via a runtime patch (production byte-identical);
# the mutation -> failure mapping is in the review notes.


def _distinct_token(seed: object) -> str:
    """A high-entropy token unique to ``seed``, as a *distinct* string object.

    Distinct objects matter: the walker dedupes by :func:`id`, so two fields
    holding the same object collapse to one finding (which is the point of the m1
    test). Everywhere else that would swallow findings a test means to count, so
    each is minted from its own seed.
    """
    digest = hashlib.sha256(f"theurian walker fixture {seed}".encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def _one_upsert_metadata(metadata: Mapping[str, object]) -> dict[str, object]:
    """A document of one ``upsertRevision`` carrying ``metadata``, for the walker unit tests.

    The walker takes raw parsed YAML and shape-checks each level, so a minimal
    dict is a faithful input -- no body, no schema-valid surroundings. What the
    walker reads is ``operations[i].metadata``, which is exactly this.
    """
    return {"operations": [{"op": "upsertRevision", "metadata": dict(metadata)}]}


def test_the_walker_scans_a_shared_string_object_once(service: ProposalService) -> None:
    """The T-6 alias-bomb defence: sharing one string across many slots stays O(1) findings.

    PyYAML collapses an alias to the *same* object, so a hostile 4 MiB document can
    reference one string a few million times through ``labels: [*s, *s, ...]``. The
    walker visits each object once, by :func:`id`, so that document yields one
    finding and one scan, not millions. Dropping the dedup (``_unvisited`` always
    true) makes the same document scan the shared object once per reference.

    Pinned observably: one string object placed at thirty list positions yields a
    single finding at the first position. Under the mutation it yields
    :data:`MAX_FINDINGS` -- the cap is all that stops it -- so the count is what
    tells the two apart, not a timing.
    """
    shared = PLANTED_TOKEN
    document = _one_upsert_metadata({"labels": [shared] * 30})

    findings = _document_findings(document)

    assert len(findings) == 1, (
        f"a shared string object produced {len(findings)} findings, not one -- the id() dedup "
        "that bounds the alias bomb is not holding"
    )
    assert findings[0].location.endswith("labels[0]"), findings[0].location


def test_the_finding_budget_is_shared_across_all_fields_not_per_field(
    service: ProposalService,
) -> None:
    """The document scan stops at :data:`MAX_FINDINGS` over *all* fields together (#336).

    The budget is a property of the whole document, not of each field: a per-field
    ceiling is no ceiling, because the field count is the hostile input's to
    choose. This plants more than the cap across several fields -- five single
    secrets, then a label holding twenty-five, then three more paths -- so both the
    running-total budget and the loop's break are load-bearing.

    Reproduced against the two survivors: a *per-field* budget
    (``remaining = MAX_FINDINGS`` each time) lets the twenty-five-secret label run
    to twenty-five and reports more than the cap; *removing the break* lets the
    trailing fields each add one past the cap through ``scan_text``'s
    append-before-check. Both make this count something other than the cap.
    """
    metadata: dict[str, object] = {
        name: _distinct_token(name)
        for name in ("aclGroup", "namespace", "owner", "tenantId", "title")
    }
    metadata["labels"] = [" ".join(_distinct_token(("label", index)) for index in range(25))]
    metadata["scope"] = {"paths": [_distinct_token(("path", index)) for index in range(3)]}
    document = _one_upsert_metadata(metadata)

    findings = _document_findings(document)

    assert len(findings) == MAX_FINDINGS, (
        f"the document scan reported {len(findings)} findings, not the {MAX_FINDINGS} cap -- the "
        "budget is per-field or the break was removed, either of which is unbounded on the "
        "document's own field count"
    )


def test_a_finding_names_the_exact_list_index_it_sits_at(service: ProposalService) -> None:
    """A finding's location carries the list index, so a reviewer opens the right line (#336).

    A secret in ``labels[3]`` must be reported as ``labels[3]``. Reporting every
    list finding at ``[0]`` would send a reviewer to the wrong element of a
    multi-value field -- and under ``warn`` that is the whole actionable content of
    the report. The three leading values are clean, so the one finding is the plant
    and its index is the assertion.
    """
    document = _one_upsert_metadata({"labels": ["one", "two", "three", PLANTED_TOKEN]})

    findings = _document_findings(document)

    assert len(findings) == 1, f"expected one finding, got {[f.location for f in findings]}"
    assert findings[0].location == "migration.operations[0].metadata.labels[3]", (
        f"the finding names {findings[0].location!r}, not the index the secret sits at"
    )


def test_a_mapping_after_a_non_mapping_is_located_by_its_list_index(
    service: ProposalService,
) -> None:
    """``_mappings_in`` numbers a mapping by its list position, not among the mappings (#336).

    ``operations`` and ``sourceAnchors`` are lists a hand-authored migration may
    mix with anything -- a stray scalar, a ``null`` a YAML edit left behind -- and
    the walker still has to name each mapping where a reviewer opening the file
    will find it. Here a non-mapping element precedes the one operation that
    carries a secret, so that operation sits at list index 1 while it is the
    *first* mapping in the list. The reported location must read ``operations[1]``,
    the position a reviewer counts to, not ``operations[0]``, its rank among the
    mappings.

    This is exactly the property :func:`~theurian.application.proposal_service
    ._mappings_in`'s docstring claims and that nothing drove until now: round two's
    adversarial pass found the mutation that replaces ``enumerate(value)`` with a
    counter incremented only on a mapping -- so the index becomes the rank among
    mappings -- surviving the whole suite. Under that mutation this finding is
    reported at ``operations[0]``, which points a reviewer at the stray scalar, so
    the exact index is the assertion and a bare ``in`` check would not catch it.
    """
    document = {
        "operations": [
            "a stray scalar a hand edit left in the list",
            {"op": "changeOwner", "itemId": _ITEM, "owner": PLANTED_TOKEN},
        ]
    }

    findings = _document_findings(document)

    assert len(findings) == 1, f"expected one finding, got {[f.location for f in findings]}"
    assert findings[0].location == "migration.operations[1].owner", (
        f"the finding names {findings[0].location!r}, not the list index the mapping sits at -- "
        "_mappings_in numbered the operation among the mappings rather than by its list position"
    )


def test_the_walker_reads_every_field_regardless_of_the_operations_op(
    service: ProposalService,
) -> None:
    """``op`` is untrusted, so a mislabelled operation cannot steer a field past the scan.

    The scan runs before stage-1 validation, so ``op`` is a value an author picks,
    not a checked discriminator. The walker therefore reads the metadata and anchor
    fields of *every* operation rather than branching on ``op`` -- otherwise a
    ``deprecateItem`` carrying a bogus ``metadata`` or ``anchor`` block would hide a
    credential from the scan while still landing it in the tree. Here the secrets
    sit under an operation labelled ``deprecateItem`` (which the schema gives no
    metadata or anchor) and must still be found.
    """
    document = {
        "operations": [
            {
                "op": "deprecateItem",
                "itemId": "architecture.retry-policy",
                "metadata": {"title": _distinct_token("title")},
                "anchor": {"provider": "git", "sourceUri": _distinct_token("uri")},
            }
        ]
    }

    locations = {finding.location for finding in _document_findings(document)}

    assert "migration.operations[0].metadata.title" in locations, (
        f"a secret in a mislabelled op's metadata was skipped: {sorted(locations)}"
    )
    assert "migration.operations[0].anchor.sourceUri" in locations, (
        f"a secret in a mislabelled op's anchor was skipped: {sorted(locations)}"
    )


def test_body_findings_are_listed_before_document_findings(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """The two finding sources are one ordered list, bodies first (#336).

    ``_scan_for_secrets`` concatenates the body findings and then the document's,
    and the order is part of the contract: a reviewer reading a ``warn`` result, or
    the capped refusal list, meets the reviewed artefact (the body) before the
    skimmed one (the metadata). A secret in the body and one in the title produce
    two findings; the body-content finding must come first. Swapping the
    concatenation order -- which round one's mutation did and the suite did not
    notice -- puts the metadata finding first.
    """
    _configure(paths, SecretScanPolicy.WARN.value)
    drafted = service.draft(_with_a_planted_secret("title", LEAKY_BODY))

    accepted = service.accept(drafted.proposal_id)

    locations = [finding.location for finding in accepted.secret_scan.findings]
    assert len(locations) >= 2, f"expected a body and a metadata finding, got {locations}"
    assert locations[0] == f"{_AT_BODY_CONTENT}[0]", (
        f"the first finding is not the body; body findings must precede the document's: {locations}"
    )
    assert any("title" in location for location in locations[1:]), (
        f"the metadata finding is missing or ahead of the body: {locations}"
    )


def test_a_metadata_only_migration_is_still_scanned(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """The document scan does not depend on the migration moving a body (#336).

    ``accept`` scans the parsed document itself, not only the bodies it stages, so a
    migration that moves *no* body -- a lone ``createItem``, a ``changeOwner``, a
    ``deprecateItem`` -- is still scanned. Gating the document scan on there being a
    body to move (round one's mutation) is the difference between this refusing and
    this **accepting the credential into the tree**: reproduced, the mutated build
    lands the ``createItem`` with the secret in its namespace under ``block``.
    """
    assert not paths.config.exists(), "the fixture wrote a config file; the default is untested"
    drafted = service.draft(_request(CLEAN_BODY))
    document = yaml.safe_load(drafted.migration_file.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    document["operations"] = [
        {
            "op": "createItem",
            "itemId": _ITEM,
            "kind": "architecture",
            "namespace": f"architecture-{PLANTED_TOKEN}",
            "owner": "platform-team",
        }
    ]
    drafted.migration_file.write_text(
        yaml.safe_dump(document, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )

    with pytest.raises(ProposalError) as caught:
        service.accept(drafted.proposal_id)

    assert HIGH_ENTROPY in str(caught.value), (
        f"a body-less migration with a secret in its namespace was not refused: {caught.value}"
    )
    assert not (paths.migrations / drafted.migration_file.name).exists(), (
        "the migration landed despite carrying a secret -- the document scan was skipped because "
        "no body moved"
    )


# -- the artifacts accept lands, not only the fields it parses (#349) ---------
#
# Everything above scans two things: a body's bytes, and the *parsed* value of a
# field the walker's allowlists name. `accept` does not land parsed values. It
# lands three artifacts, and each carries author-written characters that no
# parse survives:
#
#   * the migration file's own bytes -- a YAML comment is stripped by
#     `yaml.safe_load` before the field scan ever sees the document, and a field
#     *as written* can differ from its parsed value;
#   * the migration file's *name*, whose slug after the ULID prefix is free-form
#     on a hand-authored proposal (`_MIGRATION_FILE_NAME` admits any lower-case
#     kebab run); and
#   * each body's landed path under `.theurian/knowledge/`, directory components
#     included -- `_commit` calls `destination.parent.mkdir(parents=True)`, so a
#     component of a hand-authored `contentFile` becomes a directory in the tree.
#
# Measured on 08319af through the real ProposalService at policy `block`
# (2026-08-25, https://github.com/theurian/theurian/issues/349): all four faces
# below are accepted with `secretFindings: []` and land -- the comment into
# `.theurian/migrations/`, the credential-named migration file into the same
# directory, and the credential-named body (leaf and directory alike) into
# `.theurian/knowledge/`.
#
# `evidence.json` and the proposal directory's own name are deliberately absent:
# `accept` moves neither, so neither is an artifact this scan can be about.


def _hex_tail(seed: bytes, length: int) -> str:
    """``length`` lower-case hex characters derived from ``seed``.

    Split from its seed for the reason :data:`PLANTED_TOKEN` records: not drawn,
    so the suite cannot redden for a fixture's luck, and no credential-shaped
    literal exists in the file.
    """
    return hashlib.sha256(seed).hexdigest()[:length]


#: The two families a *migration filename* can carry, and why they are the only
#: two. The slug is `[a-z0-9]+(-[a-z0-9]+)*`, so `aws-access-key-id` and
#: `google-api-key` (upper-case) and `github-token` and `stripe-secret-key`
#: (underscore) cannot be spelled in one at all; `openai-api-key` (`sk-`) and
#: `slack-token` (`xox`) can. Measured 2026-08-25. A body *path* is less
#: restricted and all five are reachable there, which is why the two channels do
#: not share a fixture.
_FILENAME_SECRET: Final = "sk-" + _hex_tail(b"theurian migration-filename fixture (#349)", 40)
_SECOND_FILENAME_SECRET: Final = "sk-" + _hex_tail(b"theurian second filename fixture (#349)", 40)

#: The same shape for a body leaf. A separate seed per channel so that re-seeding
#: one fixture cannot silently change what another one tests.
_LEAF_SECRET: Final = "sk-" + _hex_tail(b"theurian body-leaf fixture (#349)", 40)
_SECOND_LEAF_SECRET: Final = "sk-" + _hex_tail(b"theurian second body-leaf fixture (#349)", 40)

#: A Slack bot token, for the *directory component* face. Its family's repetition
#: class admits `-`, so the whole credential is spellable inside one path
#: component -- which is the point: the leaf beside it (`note.md`) is clean, so a
#: scan of the leaf alone finds nothing and only the full landed path does.
_DIRECTORY_SECRET: Final = (
    "xoxb-"
    + _hex_tail(b"theurian landed-path fixture (#349)", 10)
    + "-"
    + _hex_tail(b"theurian landed-path tail (#349)", 24)
)

#: The credential a contributor leaves in a YAML comment while rotating one. A
#: base64url token rather than a prefixed one, because a comment is free text and
#: this is the shape a paste produces.
_COMMENT_SECRET: Final = _distinct_token("#349 yaml comment")

#: Where `_migration_document` puts the `upsertRevision` a drafted proposal
#: carries. Named because every helper below rewrites that operation's
#: `contentFile`, and an index that silently moved would leave them rewriting
#: `createItem` -- which has no `contentFile`, so the fixture would land a body
#: at its drafted path and the test would go green having exercised nothing.
_UPSERT_INDEX: Final = 1

#: A placeholder for the one scalar a fixture has to write as *text* rather than
#: through `yaml.safe_dump`, so that a `\x`/`\u` escape survives into the file.
_CONTENT_FILE_PLACEHOLDER: Final = "CONTENT-FILE-PLACEHOLDER"


def _a_proposal_whose_migration_carries_a_comment(
    service: ProposalService, comment: str, *, body: str = CLEAN_BODY
) -> DraftedProposal:
    """The ordinary proposal, with ``comment`` prepended to its migration as a YAML comment.

    Written by text manipulation and not through ``yaml.safe_dump``, which has no
    way to emit a comment at all -- and that is the whole point of the face: a
    comment exists only in the bytes, so the parsed document the field scan reads
    cannot carry it.

    ``body`` stays clean for every face that is about the comment alone. It is
    given only by the cross-channel budget test below, which needs one proposal
    loading *two* channels at once -- the body's bytes and the migration's.
    """
    drafted = service.draft(_request(body))
    text = drafted.migration_file.read_text(encoding="utf-8")
    drafted.migration_file.write_text(f"# {comment}\n{text}", encoding="utf-8")
    return drafted


def _a_proposal_named_for(
    service: ProposalService, slug: str, *, replacing: RevisionId | None = None
) -> tuple[DraftedProposal, Path]:
    """The ordinary proposal, with its migration file renamed ``<its own ULID>-<slug>.yaml``.

    Renamed rather than drafted under that name: ``draft`` derives the slug from
    the title, and the face is a *committed* proposal directory (ADR-0013 point
    7) whose file a contributor renamed by hand. The ULID prefix is the
    proposal's own, so ``_require_filename_matches_id`` -- which runs before the
    scan and compares the prefix with the document's ``id`` -- still passes and
    cannot be what refuses.

    Returns the drafted proposal and the renamed file, because
    ``DraftedProposal.migration_file`` now points at a path that no longer
    exists.

    ``replacing`` is the revision this one supersedes, which ``draft`` demands of
    the *second* proposal for an item whose first has already been accepted --
    the shape the two-acceptance pins below need.
    """
    drafted = service.draft(replace(_request(CLEAN_BODY), expected_revision=replacing))
    renamed = drafted.directory / f"{drafted.migration_id.value}-{slug}.yaml"
    assert is_migration_file_name(renamed.name), (
        f"{renamed.name!r} is not a name accept recognises as a migration, so the refusal under "
        f"test would be 'this proposal holds no migration' rather than anything about a secret"
    )
    drafted.migration_file.rename(renamed)
    return drafted, renamed


def _a_proposal_whose_body_lands_at(
    service: ProposalService,
    tail: str,
    *,
    spelled: str | None = None,
    replacing: RevisionId | None = None,
) -> DraftedProposal:
    """The ordinary proposal, with its one body moved to ``tail`` and the migration repointed.

    ``tail`` is the path relative to ``.theurian/knowledge/``, which is also the
    sub-path the body occupies inside the proposal directory -- one string for
    both, exactly as :meth:`ProposalService._body_moves` requires, so the body is
    found and read and the scan is reached.

    The body's *bytes* are untouched, so the ``contentSha256`` the draft pinned
    still matches and the rehearsal has nothing of its own to refuse. What
    changes is only where the file will land.

    ``spelled`` is the literal YAML scalar to write for ``contentFile`` when the
    test needs the *written* form to differ from the parsed one -- a ``\\x``
    escape, which ``yaml.safe_dump`` would never produce. When it is ``None`` the
    ordinary dumped form is used.

    ``replacing`` is the revision this one supersedes, which ``draft`` demands of
    the *second* proposal for an item whose first has already been accepted --
    the shape the two-acceptance pins below need.
    """
    drafted = service.draft(replace(_request(CLEAN_BODY), expected_revision=replacing))
    document = yaml.safe_load(drafted.migration_file.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    upsert = document["operations"][_UPSERT_INDEX]
    assert upsert["op"] == "upsertRevision", (
        f"operations[{_UPSERT_INDEX}] is a {upsert['op']!r}, not the upsertRevision this helper "
        f"repoints; the body would land at its drafted path and the face would go untested"
    )

    upsert["contentFile"] = _CONTENT_FILE_PLACEHOLDER if spelled else f"../knowledge/{tail}"
    text = yaml.safe_dump(document, sort_keys=False, allow_unicode=True)
    if spelled is not None:
        text = text.replace(_CONTENT_FILE_PLACEHOLDER, spelled)
    drafted.migration_file.write_text(text, encoding="utf-8")

    moved = drafted.directory / tail
    moved.parent.mkdir(parents=True, exist_ok=True)
    drafted.body_file.rename(moved)
    return drafted


def test_a_secret_in_a_yaml_comment_is_invisible_to_the_parsed_field_scan(
    service: ProposalService,
) -> None:
    """The guard that makes the comment tests below about a *new* channel (#349).

    If the field scan happened to see a comment, every assertion below would hold
    against the shipped build and this file would report a fix that was never
    made. It cannot: ``yaml.safe_load`` discards comments before
    :func:`_document_findings` is handed anything, so the parsed document carries
    no trace of one. Both halves are asserted -- the detector *does* report the
    planted value in the file's bytes, and the walker reports nothing for the
    parsed document -- because either alone is satisfied by a fixture that
    planted nothing.
    """
    drafted = _a_proposal_whose_migration_carries_a_comment(service, _COMMENT_SECRET)

    text = drafted.migration_file.read_text(encoding="utf-8")

    assert [f.family for f in scan_text(text)] == [HIGH_ENTROPY], (
        f"the detector does not report the planted comment, so a refusal test built on it would "
        f"not be testing the accept path: {scan_text(text)}"
    )
    assert _document_findings(yaml.safe_load(text)) == (), (
        "the parsed-field scan reports the comment, so these tests would pass without the "
        "artifact-level scan #349 asks for"
    )


def test_a_secret_in_a_yaml_comment_of_the_migration_is_refused_under_block(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """SEC-11's gate covers the bytes that land, not only the values that parse (#349).

    A comment is where a rotation note goes -- *the old staging value was X* --
    and it survives acceptance verbatim: ``_commit`` writes the migration's
    original bytes to ``.theurian/migrations/``, so the comment is in the tree
    every later ``migrate apply`` reads and in the pull request a human merges.
    The field scan cannot reach it by construction (the guard above), so this is
    red until the scan reads the migration's raw bytes.

    Measured on 08319af: this acceptance exits 0 and the comment lands.
    """
    assert not paths.config.exists(), "the fixture wrote a config file; the default is untested"
    drafted = _a_proposal_whose_migration_carries_a_comment(service, _COMMENT_SECRET)

    with pytest.raises(ProposalError) as caught:
        service.accept(drafted.proposal_id)

    assert HIGH_ENTROPY in str(caught.value), (
        f"a secret in a YAML comment was not what refused the acceptance: {caught.value}"
    )
    assert not (paths.migrations / drafted.migration_file.name).exists(), (
        "the migration landed in .theurian/migrations/ despite the refusal -- the comment is now "
        "in the tree the migration set reads"
    )
    assert _COMMENT_SECRET not in str(caught.value), (
        f"the refusal reproduced the secret it refused: {caught.value}"
    )


def test_a_secret_in_a_yaml_comment_is_reported_under_warn(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """``warn`` has to *say* what it let through, on the new channel as on the old ones.

    A ``warn`` that scanned the bytes and reported nothing would be
    indistinguishable from ``off`` for the one artifact that lands unparsed. The
    landed migration is asserted to still hold the comment, so this is the whole
    of ``warn`` on this channel: it proceeds, and it tells a reviewer to go and
    look.
    """
    _configure(paths, SecretScanPolicy.WARN.value)
    drafted = _a_proposal_whose_migration_carries_a_comment(service, _COMMENT_SECRET)

    accepted = service.accept(drafted.proposal_id)

    rendered = _rendered(accepted)
    assert accepted.secret_scan.policy is SecretScanPolicy.WARN
    assert HIGH_ENTROPY in rendered, (
        f"warn reported nothing for a secret in a comment: {rendered!r}"
    )
    assert _COMMENT_SECRET not in rendered, (
        f"a warning reproduces the token it warns about: {rendered!r}"
    )
    landed = paths.migrations / drafted.migration_file.name
    assert _COMMENT_SECRET in landed.read_text(encoding="utf-8"), (
        "warn refused the acceptance, or landed a migration this fixture did not write"
    )


def test_a_secret_in_the_migration_s_own_filename_is_refused_under_block(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """The filename is an artifact too, and its slug is the contributor's (#349).

    ``.theurian/migrations/`` names files ``<ulid>-<slug>.yaml`` and only the
    ULID is Theurian's: the slug is free-form lower-case kebab on a hand-authored
    proposal, which is enough to spell an ``openai-api-key``. The name lands in a
    Git tree, is printed by every ``migrate`` listing, and is quoted in the
    refusals this very module renders -- and it appears nowhere in the migration's
    own bytes, so neither the body scan, the field scan nor a raw-bytes scan of
    the document can see it.

    Measured on 08319af: this acceptance exits 0 and the file lands under that
    name. The family is asserted rather than the bare refusal, because a
    hand-renamed migration has other ways to be refused -- a name the pattern
    does not admit, or a ULID prefix disagreeing with the document's ``id`` --
    and a test that accepted any ``ProposalError`` would go green on one of them.
    """
    assert not paths.config.exists(), "the fixture wrote a config file; the default is untested"
    drafted, renamed = _a_proposal_named_for(service, f"staging-{_FILENAME_SECRET}")

    with pytest.raises(ProposalError) as caught:
        service.accept(drafted.proposal_id)

    assert "openai-api-key" in str(caught.value), (
        f"a credential in the migration's filename was not what refused the acceptance: "
        f"{caught.value}"
    )
    assert not (paths.migrations / renamed.name).exists(), (
        "the migration landed in .theurian/migrations/ despite the refusal -- the credential is "
        "now a filename in the tree the migration set reads"
    )


def test_a_secret_in_a_landed_body_leaf_is_refused_under_block(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """A body's landed *name* is as much a landed artifact as its bytes (#349).

    ``contentFile`` decides where the body goes, and a hand-authored one may name
    anything inside ``.theurian/knowledge/``. The bytes here are the ordinary
    clean body -- so the body scan that has existed since #198 finds nothing --
    and the credential is only in the leaf the file lands under.

    Measured on 08319af: this acceptance exits 0 and
    ``.theurian/knowledge/architecture/staging-sk-<hex40>.md`` is created.
    """
    assert not paths.config.exists(), "the fixture wrote a config file; the default is untested"
    tail = f"architecture/staging-{_LEAF_SECRET}.md"
    drafted = _a_proposal_whose_body_lands_at(service, tail)

    with pytest.raises(ProposalError) as caught:
        service.accept(drafted.proposal_id)

    assert "openai-api-key" in str(caught.value), (
        f"a credential in the body's landed name was not what refused the acceptance: "
        f"{caught.value}"
    )
    assert not (paths.knowledge / tail).exists(), (
        "the body landed despite the refusal -- the credential is now a filename under "
        ".theurian/knowledge/"
    )


def test_a_secret_in_a_landed_body_s_directory_component_is_refused_under_block(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """The scanned path is the whole landed path, not the leaf (#349).

    ``_commit`` calls ``destination.parent.mkdir(parents=True)``, so every
    component of a hand-authored ``contentFile`` becomes a real directory in
    ``.theurian/knowledge/``. Here the leaf is ``note.md`` -- clean, and clean on
    its own under the detector (measured 2026-08-25) -- while the directory it
    lands in is a Slack bot token. An implementation that scanned
    ``destination.name`` would pass the test above this one and land this
    credential, which is why the two faces are separate tests rather than a
    parametrization over one.

    ``slack-token`` and ``openai-api-key`` are also the only two families a
    *filename* channel can carry, so this case doubles as the second family's
    accept-path coverage.
    """
    assert not paths.config.exists(), "the fixture wrote a config file; the default is untested"
    tail = f"{_DIRECTORY_SECRET}/note.md"
    drafted = _a_proposal_whose_body_lands_at(service, tail)

    with pytest.raises(ProposalError) as caught:
        service.accept(drafted.proposal_id)

    assert "slack-token" in str(caught.value), (
        f"a credential in the body's landed *directory* was not what refused the acceptance: "
        f"{caught.value}"
    )
    assert not (paths.knowledge / tail).parent.exists(), (
        "the directory was created despite the refusal -- the credential is now a directory name "
        "under .theurian/knowledge/"
    )


def test_a_landed_path_secret_the_migration_bytes_do_not_spell_is_still_refused(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """A credential spelled only in an escaped, ``..``-resolved path is still refused (#349).

    The ``b`` of ``xoxb`` is written as the YAML escape ``\\x62`` in a
    double-quoted scalar, so:

    * the migration's **bytes** spell ``xox\\x62-...``, which the detector does
      not report (asserted below, so a detector that later did would redden here
      loudly rather than turn this into a test of nothing); while
    * both the **parsed** ``contentFile`` and the **landed path** decode to
      ``xoxb-.../note.md``, a Slack bot token.

    So a raw-bytes scan alone would let it through; it is refused because the
    value is read as a *decoded* string. **This no longer isolates the landed-path
    channel.** #349 round 2 put ``contentFile`` in
    :data:`_AUTHORED_OPERATION_FIELDS`, and the landed path is a subset of the
    parsed ``contentFile`` (``..`` only ever drops segments), so any secret in the
    landed path is in the parsed value too -- the field walk catches this fixture
    as well. The landed-path channel can no longer be isolated by any fixture, and
    whether it remains worth keeping beside the field walk is a separate question
    (recorded for the #349 round-2 report).
    """
    assert not paths.config.exists(), "the fixture wrote a config file; the default is untested"
    tail = f"{_DIRECTORY_SECRET}/note.md"
    spelled = f'"../knowledge/{tail.replace("xoxb", "xox\\x62", 1)}"'
    drafted = _a_proposal_whose_body_lands_at(service, tail, spelled=spelled)
    text = drafted.migration_file.read_text(encoding="utf-8")
    assert scan_text(text) == (), (
        f"the escaped spelling is detectable in the migration's own bytes, so this no longer "
        f"distinguishes the decoded channels from a raw-bytes scan: {scan_text(text)}"
    )
    assert yaml.safe_load(text)["operations"][_UPSERT_INDEX]["contentFile"].endswith(tail), (
        "the escaped scalar did not parse back to the path the body was moved to; the fixture "
        "is not describing the face it claims"
    )

    with pytest.raises(ProposalError) as caught:
        service.accept(drafted.proposal_id)

    assert "slack-token" in str(caught.value), (
        f"a credential spelled only in the landed path was not what refused the acceptance: "
        f"{caught.value}"
    )
    assert not (paths.knowledge / tail).parent.exists(), "the directory was created anyway"


#: The two name channels, each with two credentials of its own family. Two so the
#: pins below can tell a *fixed* location from one built out of the name that was
#: found: a location derived from the name differs between the two runs, a
#: location naming the channel does not.
_NAME_CHANNEL_SECRETS: Final[Mapping[str, tuple[str, str]]] = {
    "migration-filename": (_FILENAME_SECRET, _SECOND_FILENAME_SECRET),
    "body-path": (_LEAF_SECRET, _SECOND_LEAF_SECRET),
}


def _a_proposal_carrying(
    service: ProposalService, channel: str, secret: str, *, replacing: RevisionId | None = None
) -> DraftedProposal:
    """A proposal whose only credential is in the artifact ``channel`` names."""
    if channel == "migration-filename":
        drafted, _renamed = _a_proposal_named_for(service, f"staging-{secret}", replacing=replacing)
        return drafted
    return _a_proposal_whose_body_lands_at(
        service, f"architecture/staging-{secret}.md", replacing=replacing
    )


@pytest.mark.parametrize("channel", list(_NAME_CHANNEL_SECRETS))
def test_a_name_channel_refusal_never_reproduces_the_name_it_refuses(
    service: ProposalService, paths: ProjectPaths, channel: str
) -> None:
    """A refusal that quotes the offending name is a second copy of the credential (#349).

    The two channels added here are the ones where the *location* of a finding is
    itself attacker-chosen text. ``SecretFinding`` bounds what a finding may quote
    of the match to four characters and refuses construction past it, but a
    location assembled from a filename or a landed path routes straight around
    that bound: the whole point of the name is that it *is* the credential.
    ``ProposalSecretFinding.describe`` renders the location into the refusal a
    terminal prints and into the ``accept --json`` document something logs, and
    ``_destination_of`` already records the same stance for the sibling path
    refusals (SEC-7 forbids reflecting an authored ``contentFile``, #233).

    The whole rendered refusal is asserted -- message and remedy together --
    rather than the finding's fields, because a message assembled anywhere else
    on the path would satisfy a field-level check and still print the credential.
    """
    assert not paths.config.exists(), "the fixture wrote a config file; the default is untested"
    secret, _second = _NAME_CHANNEL_SECRETS[channel]
    drafted = _a_proposal_carrying(service, channel, secret)

    with pytest.raises(ProposalError) as caught:
        service.accept(drafted.proposal_id)

    assert secret not in str(caught.value), (
        f"the refusal reproduced the credential it found in the {channel}: {caught.value}"
    )
    assert secret not in caught.value.remedy, (
        f"the remedy reproduced the credential it found in the {channel}: {caught.value.remedy!r}"
    )


@pytest.mark.parametrize("channel", list(_NAME_CHANNEL_SECRETS))
def test_a_name_channel_finding_locates_by_the_channel_rather_than_by_the_name(
    service: ProposalService, paths: ProjectPaths, channel: str
) -> None:
    """The location of a name-channel finding is a fixed literal, not the name (#349).

    The test above holds that the credential is not echoed; this holds *why* it
    is not, in the form that survives an implementation change. Two acceptances
    under ``warn``, in one project, carrying two different credentials in the
    same channel: a location built from the offending name differs between them,
    and a location naming the channel -- *the migration's filename*, *the body's
    landed path* -- is identical. Equality is asserted after non-emptiness, so a
    build that reports nothing at all (which is 08319af) fails here rather than
    passing on two empty sets.

    Held as *equal and free of both names* rather than as a spelling, because
    which literal is used is the implementation's choice. What may not vary is
    that it is a literal.

    ``warn`` rather than ``block``: a refusal raises before a caller can read the
    findings, so the location only reaches a test through the success result.

    One item across both acceptances, the second stating the revision it
    replaces. Two *different* items would let an implementation that located a
    finding by the item id -- a location that names no credential, and so passes
    the sibling test above -- fail this one for a reason it is not about.
    """
    _configure(paths, SecretScanPolicy.WARN.value)
    first_secret, second_secret = _NAME_CHANNEL_SECRETS[channel]

    first_drafted = _a_proposal_carrying(service, channel, first_secret)
    first = service.accept(first_drafted.proposal_id)
    second_drafted = _a_proposal_carrying(
        service, channel, second_secret, replacing=first_drafted.revision_id
    )
    second = service.accept(second_drafted.proposal_id)

    first_locations = {finding.location for finding in first.secret_scan.findings}
    second_locations = {finding.location for finding in second.secret_scan.findings}
    assert first_locations, f"warn reported nothing for a credential in the {channel}"
    assert first_locations == second_locations, (
        f"two credentials in the same {channel} produced different finding locations "
        f"({sorted(first_locations)} and {sorted(second_locations)}), so a location is built out "
        f"of the name that was found rather than naming the channel"
    )
    for location in first_locations | second_locations:
        assert first_secret not in location and second_secret not in location, (
            f"a finding's location is a verbatim copy of the credential it reports: {location!r}"
        )


def _a_leaky_body_landing_at(service: ProposalService, tail: str) -> DraftedProposal:
    """A proposal whose body is ``LEAKY_BODY`` and whose ``contentFile`` lands it at ``tail``.

    The dirty-body sibling of :func:`_a_proposal_whose_body_lands_at`, which the
    body-content location tests below need: only a body that carries a secret
    produces a body-content finding, and only then can that finding's *location*
    echo the landed path or spoof another channel's literal.
    """
    drafted = service.draft(_request(LEAKY_BODY))
    document = yaml.safe_load(drafted.migration_file.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    upsert = document["operations"][_UPSERT_INDEX]
    assert upsert["op"] == "upsertRevision", (
        f"operations[{_UPSERT_INDEX}] is a {upsert['op']!r}, not the upsertRevision this repoints"
    )
    upsert["contentFile"] = f"../knowledge/{tail}"
    drafted.migration_file.write_text(
        yaml.safe_dump(document, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    moved = drafted.directory / tail
    moved.parent.mkdir(parents=True, exist_ok=True)
    drafted.body_file.rename(moved)
    return drafted


def test_a_body_content_finding_never_spoofs_the_migration_filename_channel(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """A body-content finding is located by its own literal, never a path an author chose (#360).

    The spoof the review of #349 reproduced: the body-content channel located a
    finding by the body's landed path, so a body whose content carries a secret,
    landed at ``.theurian/knowledge/the migration filename``, produced a finding
    whose location string *equalled* :data:`_AT_MIGRATION_NAME`. The refusal then
    pointed a maintainer at the (clean) migration filename and its remedy invited
    turning the scan off -- a misdirection channel an author controls. Located by
    :data:`_AT_BODY_CONTENT` instead, a body-content finding can never wear another
    channel's literal.

    ``warn`` rather than ``block``: a refusal raises before a caller can read the
    findings, so the location only reaches a test through the success result.
    """
    _configure(paths, SecretScanPolicy.WARN.value)
    drafted = _a_leaky_body_landing_at(service, _AT_MIGRATION_NAME)

    accepted = service.accept(drafted.proposal_id)

    locations = {finding.location for finding in accepted.secret_scan.findings}
    assert f"{_AT_BODY_CONTENT}[0]" in locations, (
        f"the leaky body produced no body-content finding at its literal: {sorted(locations)}"
    )
    assert _AT_MIGRATION_NAME not in locations, (
        f"a body-content finding wears the migration-filename channel's literal, spoofing it: "
        f"{sorted(locations)}"
    )


def test_a_body_content_finding_does_not_echo_a_credential_shaped_landed_path(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """When the landed path is the credential, the refusal must not print it (#360).

    The echo the review of #349 reproduced: the body-content channel located its
    finding by the landed path, so a dirty body landed at
    ``architecture/staging-<credential>.md`` produced a finding whose *location*
    was the full credential -- printed verbatim into the refusal a terminal shows
    and the ``accept --json`` document logs, walking straight around the
    four-character bound :class:`~theurian.security.content_secrets.SecretFinding`
    holds on the match and this PR leans on. The name channels beside it already
    redact to four characters; located by :data:`_AT_BODY_CONTENT`, so does this.

    ``block`` is the default, so the whole refusal -- message and remedy -- is
    asserted free of the credential, and a body-content finding located by the
    literal is asserted present so the case is not vacuous.
    """
    assert not paths.config.exists(), "the fixture wrote a config file; the default is untested"
    tail = f"architecture/staging-{_LEAF_SECRET}.md"
    drafted = _a_leaky_body_landing_at(service, tail)

    with pytest.raises(ProposalError) as caught:
        service.accept(drafted.proposal_id)

    assert f"{_AT_BODY_CONTENT}[0]:" in str(caught.value), (
        f"no body-content finding located by its literal reached the refusal: {caught.value}"
    )
    assert _LEAF_SECRET not in str(caught.value), (
        f"the refusal echoed the credential from the landed path: {caught.value}"
    )
    assert _LEAF_SECRET not in caught.value.remedy, (
        f"the remedy echoed the credential from the landed path: {caught.value.remedy!r}"
    )
    assert not (paths.knowledge / tail).exists(), "the body landed despite the refusal"


#: The four faces, as the ``off`` policy has to see them: a builder and the
#: artifact that must exist afterwards, relative to the project root.
def _a_comment_face(service: ProposalService) -> tuple[DraftedProposal, str, str]:
    drafted = _a_proposal_whose_migration_carries_a_comment(service, _COMMENT_SECRET)
    return drafted, drafted.migration_file.name, ""


def _a_filename_face(service: ProposalService) -> tuple[DraftedProposal, str, str]:
    drafted, renamed = _a_proposal_named_for(service, f"staging-{_FILENAME_SECRET}")
    return drafted, renamed.name, ""


def _a_body_face(service: ProposalService, tail: str) -> tuple[DraftedProposal, str, str]:
    drafted = _a_proposal_whose_body_lands_at(service, tail)
    return drafted, drafted.migration_file.name, tail


#: Each face as ``off`` has to see it: a builder, and the two artifacts that must
#: exist afterwards -- the landed migration's name, and the body's tail under
#: ``.theurian/knowledge/`` where the face has one. Both are named rather than
#: probed as "some file appeared", because a landed *anything* is satisfied by a
#: build that renamed or relocated the artifact this face is about.
_ARTIFACT_FACES: Final[
    Mapping[str, Callable[[ProposalService], tuple[DraftedProposal, str, str]]]
] = {
    "yaml-comment": _a_comment_face,
    "migration-filename": _a_filename_face,
    "body-leaf": lambda service: _a_body_face(service, f"architecture/staging-{_LEAF_SECRET}.md"),
    "body-directory": lambda service: _a_body_face(service, f"{_DIRECTORY_SECRET}/note.md"),
}


@pytest.mark.parametrize("face", list(_ARTIFACT_FACES))
def test_off_leaves_every_landed_artifact_unscanned(
    service: ProposalService, paths: ProjectPaths, face: str
) -> None:
    """The escape hatch has to cover the new channels too, or it is not one.

    ``block`` is the default and there is no per-finding suppression, so a project
    that hits a false positive in a filename has exactly one move. An
    implementation that scanned the artifacts unconditionally -- before the policy
    is consulted, which is where the cheapest patch puts it -- would leave that
    project unable to accept anything, and would do it while reporting the policy
    as ``off``. The sibling of ``test_off_leaves_the_migration_document_unscanned_too``
    for the three artifact channels.
    """
    _configure(paths, SecretScanPolicy.OFF.value)
    drafted, migration_name, body_tail = _ARTIFACT_FACES[face](service)

    accepted = service.accept(drafted.proposal_id)

    assert accepted.secret_scan.policy is SecretScanPolicy.OFF
    assert accepted.secret_scan.findings == (), f"off scanned the {face} artifact anyway"
    assert (paths.migrations / migration_name).exists(), (
        f"off refused the acceptance, or landed a migration the {face} fixture did not name"
    )
    if body_tail:
        assert (paths.knowledge / body_tail).exists(), (
            f"the body did not land at the path the {face} fixture named"
        )


def test_an_ordinary_proposal_s_own_artifacts_carry_no_secret_shaped_name(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """The false positive that would make this control the first thing switched off (#349).

    Every artifact this scan newly reads is one Theurian *generates*, and all
    three are built around a ULID: ``<ulid>-<slug>.yaml`` and
    ``<namespace>/<leaf>.<ulid>.md``. A ULID is high-entropy by construction and
    the detector only tolerates one because ``_looks_like_a_secret`` subtracts it
    before judging anything -- so extending the scan to the *names* points that
    subtraction at strings that are almost entirely ULID. If an ordinary
    acceptance starts failing, the project turns the scan off and SEC-11's
    control is gone for the bodies too.

    The fixture's own ids cannot say this: :class:`SeededIdGenerator` mints
    ``00000000000000000000000002``, twenty-six digits, which no family can match
    and which would leave this green against a build that reports every real
    ULID. So the migration id, the revision id, and both artifact names are
    rewritten to real Crockford base32 ULIDs -- the same shape
    :data:`MIGRATION_FILENAME` uses, and the shape ``01K1IDX...`` is not.

    ``scan_text`` is asked directly about both names as well, so a future change
    that made them genuinely detectable fails here loudly rather than turning
    this into a test of nothing.
    """
    assert not paths.config.exists(), "the fixture wrote a config file; the default is untested"
    revision = _A_REAL_ULID[:-1] + "X"
    migration_name = f"{_A_REAL_ULID}-retry-policy.yaml"
    tail = f"architecture/retry-policy.{revision}.md"
    assert scan_text(migration_name) == () and scan_text(tail) == (), (
        f"an ordinary migration name or landed body path is detectable on its own, so this "
        f"asserts nothing: {scan_text(migration_name)} {scan_text(tail)}"
    )
    drafted = _a_proposal_whose_body_lands_at(service, tail)
    document = yaml.safe_load(drafted.migration_file.read_text(encoding="utf-8"))
    document["id"] = _A_REAL_ULID
    document["operations"][_UPSERT_INDEX]["revisionId"] = revision
    drafted.migration_file.unlink()
    (drafted.directory / migration_name).write_text(
        yaml.safe_dump(document, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )

    accepted = service.accept(drafted.proposal_id)

    assert accepted.secret_scan.findings == (), (
        f"an ordinary proposal's own generated artifact names were reported as secrets: "
        f"{_rendered(accepted)}"
    )
    assert (paths.migrations / migration_name).exists()
    assert (paths.knowledge / tail).exists()


def test_a_field_value_spelled_with_yaml_escapes_is_still_refused_under_block(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """The parsed-field scan is not subsumed by a raw-bytes one, and must not be dropped (#349).

    #349 adds a scan of the migration's *bytes*, which reads every field as
    written and so looks like a superset of the field scan #336 added. It is not.
    A double-quoted YAML scalar may spell any character as ``\\xNN`` or
    ``\\uNNNN``: here every character of the token is escaped, so the bytes hold
    only three-character runs (``x74``, ``x35``, ...) that no family matches --
    asserted below -- while the value ``migrate apply`` records, and that
    ``knowledge.search`` and ``knowledge.get`` publish on every result, is the
    credential itself.

    Green on 08319af and green after the fix. It is here so that the fix cannot
    simplify the parsed-field walk away in favour of the bytes: delete
    ``_document_findings`` from ``_scan_for_secrets`` and this is the test that
    goes red.
    """
    assert not paths.config.exists(), "the fixture wrote a config file; the default is untested"
    drafted = service.draft(_request(CLEAN_BODY))
    document = yaml.safe_load(drafted.migration_file.read_text(encoding="utf-8"))
    document["operations"][_UPSERT_INDEX]["metadata"]["title"] = "TITLE-PLACEHOLDER"
    escaped = "".join(f"\\x{ord(character):02x}" for character in PLANTED_TOKEN)
    text = yaml.safe_dump(document, sort_keys=False, allow_unicode=True).replace(
        "TITLE-PLACEHOLDER", f'"{escaped}"'
    )
    drafted.migration_file.write_text(text, encoding="utf-8")
    assert scan_text(text) == (), (
        f"the escaped spelling is detectable in the migration's bytes, so this no longer "
        f"distinguishes the two channels: {scan_text(text)}"
    )
    assert (
        yaml.safe_load(text)["operations"][_UPSERT_INDEX]["metadata"]["title"] == PLANTED_TOKEN
    ), (
        "the escaped title did not parse back to the token; the fixture is not describing the "
        "face it claims"
    )

    with pytest.raises(ProposalError) as caught:
        service.accept(drafted.proposal_id)

    assert "migration.operations[1].metadata.title" in str(caught.value), (
        f"a token spelled only in escapes was not caught by the parsed-field scan: {caught.value}"
    )
    assert PLANTED_TOKEN not in str(caught.value), (
        f"the refusal reproduced the secret it refused: {caught.value}"
    )


#: How far past the ceiling the two-channel budget fixture reaches, and the load
#: each channel carries. Derived from :data:`MAX_FINDINGS` rather than written as
#: literals so the fixture follows the constant: the body holds most of the
#: ceiling and less than all of it, and the comment holds twice the overflow. So
#: neither channel fills the budget alone, the two together offer
#: ``MAX_FINDINGS + _BUDGET_OVERFLOW``, and a budget granted per channel reports
#: exactly that many.
_BUDGET_OVERFLOW: Final = 5
_BUDGET_BODY_SECRETS: Final = MAX_FINDINGS - _BUDGET_OVERFLOW
_BUDGET_COMMENT_SECRETS: Final = 2 * _BUDGET_OVERFLOW

#: One token per line, so each is a separate finding at one location -- the body's
#: landed path. Distinct tokens rather than one repeated, so nothing can collapse
#: them (the discipline :func:`_distinct_token` records).
_BUDGET_BODY: Final = "# Retry policy\n\nThree attempts.\n\n" + "".join(
    f"    TOKEN_{index}={_distinct_token(('budget body', index))}\n"
    for index in range(_BUDGET_BODY_SECRETS)
)

#: The same load in the one channel a comment reaches: the migration's own bytes.
#: On one line, because a comment is one line and `_a_proposal_whose_migration
#: _carries_a_comment` writes it as one.
_BUDGET_COMMENT: Final = " ".join(
    _distinct_token(("budget comment", index)) for index in range(_BUDGET_COMMENT_SECRETS)
)


def test_the_finding_budget_is_shared_across_channels_not_per_channel(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """One ceiling covers every channel the acceptance lands, never one each (#349).

    The sibling of ``test_the_finding_budget_is_shared_across_all_fields_not_per_field``,
    one level out. That test drives the *parsed-field* channel alone, so it holds
    :func:`_findings_in`'s own running total and says nothing about how
    ``_scan_for_secrets`` composes the five channels around it. A version that
    called ``_findings_in`` once per channel and concatenated the results keeps
    every field-level assertion green while publishing up to five times
    :data:`MAX_FINDINGS` into a refusal message and an ``accept --json``
    document -- and how many bodies, fields and landed paths a proposal carries
    is the contributor's number, so a ceiling that resets at a channel boundary
    is no ceiling.

    The fixture loads two channels and neither one alone: the body carries
    ``MAX_FINDINGS - _BUDGET_OVERFLOW`` findings and the migration's YAML comment
    carries ``2 * _BUDGET_OVERFLOW``, which offers twenty-five findings to a
    twenty-finding budget. Shared, the body spends fifteen and the comment is
    truncated at the five that are left. Per channel, the two report twenty-five.

    Both guards are asserted before the acceptance, because either channel
    quietly scanning clean would leave the count at or under the cap and this
    green with nothing crossed.

    ``warn`` rather than ``block``: a refusal raises before a caller can count
    anything, so the findings only reach a test through the success result.

    Measured on bf40533 in a prepared mutation tree (2026-08-25), against a
    ``_scan_for_secrets`` that calls ``_findings_in`` once per channel and
    concatenates: the other 168 tests in this file all pass under it, and this
    one reports 25 findings against the 20 cap. Unmutated the split is
    ``{body: 15, the migration file as written: 5}``.
    """
    _configure(paths, SecretScanPolicy.WARN.value)
    drafted = _a_proposal_whose_migration_carries_a_comment(
        service, _BUDGET_COMMENT, body=_BUDGET_BODY
    )
    migration_text = drafted.migration_file.read_text(encoding="utf-8")
    assert len(scan_text(_BUDGET_BODY)) == _BUDGET_BODY_SECRETS, (
        f"the body offers {len(scan_text(_BUDGET_BODY))} findings, not the "
        f"{_BUDGET_BODY_SECRETS} this test spends the budget with"
    )
    assert len(scan_text(migration_text)) == _BUDGET_COMMENT_SECRETS, (
        f"the migration's bytes offer {len(scan_text(migration_text))} findings, not the "
        f"{_BUDGET_COMMENT_SECRETS} that have to outlast what the body already spent"
    )
    assert _document_findings(yaml.safe_load(migration_text)) == (), (
        "the parsed-field channel found something too, so the split asserted below is over three "
        "channels rather than the two this fixture loads"
    )

    accepted = service.accept(drafted.proposal_id)

    findings = accepted.secret_scan.findings
    assert len(findings) == MAX_FINDINGS, (
        f"the accept-path scan reported {len(findings)} findings over two channels, not the "
        f"{MAX_FINDINGS} cap -- the budget is granted per channel, which bounds nothing, since "
        f"the number of channels and the bodies, fields and paths inside them are the input's"
    )
    spent = Counter(finding.location for finding in findings)
    assert sorted(spent.values()) == [_BUDGET_OVERFLOW, _BUDGET_BODY_SECRETS], (
        f"the {MAX_FINDINGS} findings are not split {_BUDGET_BODY_SECRETS}/{_BUDGET_OVERFLOW} "
        f"between the two channels but {dict(spent)} -- the second channel was not truncated by "
        f"what the first had already spent"
    )


# -- two bodies, and the index that keeps their channels paired (#349) ---------
#
# Everything above lands at most one body, so the two body channels only ever
# report `[0]`, and a suite that never lands two cannot tell the real
# `_landed_text` from three mutations round one's adversarial pass confirmed
# survive it (2026-08-25):
#
#   * the content index frozen to `[0]`, so two dirty bodies' content collapses
#     onto one location;
#   * the landed-path index frozen to `[0]`, the same for the path channel; and
#   * the landed paths paired to the wrong bodies (the `landed` tuple built over
#     `reversed(moves)`), so `the landed path of body[0]` names body 1's path.
#
# The one test below lands two bodies, each carrying a credential of its *own
# family* in BOTH its content and its landed path, so a finding's
# `(location, family)` pair names exactly one plant and a family that has moved
# off the index it belongs to is a failed assertion. Four families, one per
# (body, channel), rather than four redacted prefixes: a family is a whole word
# a mispair cannot half-match, where a four-character prefix of two `sk-` tokens
# is one character apart.

#: Body 0's content credential -- a base64url `high-entropy-token`, the family a
#: prose body leaks. Split from its seed for the reason :data:`PLANTED_TOKEN`
#: records, and measured `high-entropy-token` on its own (2026-08-25).
_BODY0_CONTENT_SECRET: Final = _distinct_token("#349 two-body content 0")

#: Body 1's content credential -- a `github-token`, a *different* family from body
#: 0's so the content channel's two findings are told apart by family and not only
#: by the index under test.
_BODY1_CONTENT_SECRET: Final = "ghp_" + _hex_tail(b"theurian two-body content 1 (#349)", 36)

#: Body 0's landed-path credential -- an `openai-api-key`, the shape
#: :data:`_LEAF_SECRET` proves a body leaf can carry.
_BODY0_PATH_SECRET: Final = "sk-" + _hex_tail(b"theurian two-body path 0 (#349)", 40)

#: Body 1's landed-path credential -- a `slack-token`, the fourth distinct family,
#: the shape :data:`_DIRECTORY_SECRET` proves a path segment can carry.
_BODY1_PATH_SECRET: Final = (
    "xoxb-"
    + _hex_tail(b"theurian two-body path 1 head (#349)", 10)
    + "-"
    + _hex_tail(b"theurian two-body path 1 tail (#349)", 24)
)

_BODY0_CONTENT: Final = f"# Alpha\n\nThree attempts.\n\n    TOKEN={_BODY0_CONTENT_SECRET}\n"
_BODY1_CONTENT: Final = f"# Beta\n\nFive seconds.\n\n    GITHUB={_BODY1_CONTENT_SECRET}\n"
_BODY0_TAIL: Final = f"architecture/staging-{_BODY0_PATH_SECRET}.md"
_BODY1_TAIL: Final = f"architecture/staging-{_BODY1_PATH_SECRET}.md"

#: The two bodies in order, so the migration operations, the on-disk files and the
#: expected `(location, family)` pairs are all driven from one source of truth.
#: Each entry is (itemId, revisionId, contentFile tail, body text, content family,
#: path family). The revision ids are real Crockford base32 -- no I/L/O/U -- which
#: the fixture guard enforces; the seeded generator's numeric ids would not be.
_TWO_BODIES: Final = (
    (
        "architecture.alpha-notes",
        "01K9AAAAAA0000000000000021",
        _BODY0_TAIL,
        _BODY0_CONTENT,
        HIGH_ENTROPY,
        "openai-api-key",
    ),
    (
        "architecture.beta-notes",
        "01K9AAAAAA0000000000000022",
        _BODY1_TAIL,
        _BODY1_CONTENT,
        "github-token",
        "slack-token",
    ),
)


def _two_body_migration(migration_id: str) -> str:
    """A migration landing two bodies, each with a distinct credential in its path.

    Hand-authored rather than drafted for the reason ``accept`` reads a committed
    proposal directory (ADR-0013 point 7): ``propose`` emits one body per
    proposal, so two upsertRevisions naming two ``contentFile`` paths is a shape
    only a contributor writes. Mirrors ``_hand_authored_two_body_migration`` in
    ``test_proposal_service.py`` -- two upsertRevisions, no ``createItem`` (an
    upsert creates the item), each pinning its body's digest and declaring the
    ``authored-in-theurian`` label INV-8 requires.

    The ``id`` is quoted because the seeded generator's ids are all digits, which
    YAML would coerce to an int; the ``contentFile`` is quoted because its value
    is a credential-shaped path and quoting keeps YAML from guessing at it.
    """
    operations = "".join(
        "- op: upsertRevision\n"
        f"  itemId: {item_id}\n"
        f"  revisionId: {revision_id}\n"
        f'  contentFile: "../knowledge/{tail}"\n'
        f"  contentSha256: {hashlib.sha256(text.encode()).hexdigest()}\n"
        "  metadata:\n"
        f"    title: {item_id.rpartition('.')[2]}\n"
        "    contentType: text/markdown\n"
        "    kind: architecture\n"
        "    namespace: architecture\n"
        "    status: approved\n"
        "    owner: platform-team\n"
        f"    labels: ['{AUTHORED_IN_THEURIAN}']\n"
        for item_id, revision_id, tail, text, _content_family, _path_family in _TWO_BODIES
    )
    return (
        "apiVersion: theurian.dev/v1\n"
        f"id: '{migration_id}'\n"
        "createdAt: '2026-08-02T12:00:00+00:00'\n"
        "author: platform-team@example.com\n"
        "operations:\n"
    ) + operations


def _a_two_body_proposal(service: ProposalService) -> DraftedProposal:
    """The ordinary proposal, rewritten to land the two bodies of :data:`_TWO_BODIES`.

    ``draft`` lays down the directory ``accept`` reads; its migration and body are
    then replaced with the two-body migration and the two bodies at the sub-paths
    their ``contentFile`` names, exactly as ``accept`` will find them.
    """
    drafted = service.draft(_request(CLEAN_BODY))
    drafted.migration_file.write_text(
        _two_body_migration(drafted.migration_id.value), encoding="utf-8"
    )
    drafted.body_file.unlink()
    for _item_id, _revision_id, tail, text, _content_family, _path_family in _TWO_BODIES:
        body = drafted.directory / tail
        body.parent.mkdir(parents=True, exist_ok=True)
        body.write_text(text, encoding="utf-8")
    return drafted


def test_two_bodies_pair_each_channel_finding_with_its_own_index(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """Each body channel's index names the body it came from, with two bodies in hand (#349).

    The adversarial MEDIUM-1 that no single-body test can reach: with one body
    every ``[index]`` is ``[0]``, so the content index frozen to ``[0]``, the
    landed-path index frozen to ``[0]``, and the landed paths paired to the wrong
    bodies all read identically to the real thing. Two bodies tell them apart.

    Each body carries a credential of a *different family* in both its content and
    its landed path, so the four findings this asserts on --
    ``the content of body[0]``, ``the content of body[1]``,
    ``the landed path of body[0]``, ``the landed path of body[1]`` -- each name a
    unique family. A frozen index drops the ``[1]`` finding; a reversal or a swap
    puts a family on the wrong index. Either way one of the four
    ``(location, family)`` pairs is gone.

    Under ``warn`` so the findings ride out on the success result -- ``block``
    would raise before a caller could read them. The same path credentials are
    over-reported by the parsed ``contentFile`` field and the migration's bytes;
    presence of the four body-channel pairs is asserted, never the total, because
    the double report is by design (``_scan_for_secrets`` records why).
    """
    _configure(paths, SecretScanPolicy.WARN.value)
    # The plants are what the pairing rests on: each has to be detectable and of
    # the family this test tells the others apart by, or a missing pair would be
    # the fixture's fault and not the index's.
    assert [f.family for f in scan_text(_BODY0_CONTENT)] == [HIGH_ENTROPY]
    assert [f.family for f in scan_text(_BODY1_CONTENT)] == ["github-token"]
    assert [f.family for f in scan_text(_BODY0_TAIL)] == ["openai-api-key"]
    assert [f.family for f in scan_text(_BODY1_TAIL)] == ["slack-token"]

    drafted = _a_two_body_proposal(service)
    accepted = service.accept(drafted.proposal_id)

    assert accepted.secret_scan.policy is SecretScanPolicy.WARN
    pairs = {(f.location, f.finding.family) for f in accepted.secret_scan.findings}
    assert (f"{_AT_BODY_CONTENT}[0]", HIGH_ENTROPY) in pairs, (
        f"body 0's content credential is not at its own index: {sorted(pairs)}"
    )
    assert (f"{_AT_BODY_CONTENT}[1]", "github-token") in pairs, (
        f"body 1's content credential is not at its own index -- the content index is frozen or "
        f"the bodies are mispaired: {sorted(pairs)}"
    )
    assert (f"{_AT_BODY_PATH}[0]", "openai-api-key") in pairs, (
        f"body 0's landed-path credential is not at its own index: {sorted(pairs)}"
    )
    assert (f"{_AT_BODY_PATH}[1]", "slack-token") in pairs, (
        f"body 1's landed-path credential is not at its own index -- the path index is frozen to "
        f"[0] or the landed paths are paired to the wrong bodies: {sorted(pairs)}"
    )


def test_a_replaced_body_s_new_content_is_still_scanned_under_block(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """A body that replaces an existing file is scanned like any other (#349, SEC-11).

    The adversarial MEDIUM-2: the content channel iterates every body move, and
    skipping the ones whose destination already exists (``move.replaced``) survives
    the suite, because nothing lands a replacement carrying a *new* credential.
    Here one does. The destination pre-exists as a stray file no landed revision
    pins -- so ``_refuse_if_a_replacement_breaks_an_existing_pin`` waves it through
    (``test_accept_replaces_an_unpinned_file_at_the_destination`` in
    ``test_proposal_service.py`` proves a *clean* replacement of exactly this shape
    is accepted) -- and the replacing body's bytes carry the secret.

    So the only thing that can refuse this acceptance is the content scan, and the
    family assertion says it was: skip the replaced body and the credential lands
    in ``.theurian/knowledge/`` while the accept exits clean. The secret is in the
    body's bytes alone -- the body lands at its drafted ULID path, which is clean --
    so no other channel covers for a content scan that skipped it.
    """
    assert not paths.config.exists(), "the fixture wrote a config file; the default is untested"
    drafted = service.draft(_request(LEAKY_BODY))
    # Make the destination pre-exist, so this body is a replacement. The file is
    # unpinned -- no landed migration reads it -- so the replacement is legitimate
    # and the only remaining reason to refuse is the secret in the replacing bytes.
    drafted.body_destination.parent.mkdir(parents=True, exist_ok=True)
    drafted.body_destination.write_text("stale, pinned by nothing\n", encoding="utf-8")

    with pytest.raises(ProposalError) as caught:
        service.accept(drafted.proposal_id)

    assert HIGH_ENTROPY in str(caught.value), (
        f"a replaced body's new content was not what refused the acceptance -- the content scan "
        f"skipped it because its destination already existed: {caught.value}"
    )
    assert drafted.body_destination.read_text(encoding="utf-8") == "stale, pinned by nothing\n", (
        "the replacing body landed despite the refusal -- the credential overwrote the file that "
        "was there"
    )


def test_off_touches_no_input_before_the_policy_is_read(
    service: ProposalService, paths: ProjectPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``off`` does not scan and discard; it never scans (#349, adversarial MEDIUM-3).

    ``off`` returning an empty finding list is satisfied two ways: the policy read
    gates the scan (the real code), or the scan runs and its findings are thrown
    away. The observable result is identical -- an empty list either way -- so
    moving the ``off`` early-return to *after* the scan survives the suite.

    What tells them apart is whether the detector was ever asked. This records
    every call to ``scan_text`` -- the one function ``_findings_in`` drives per
    channel -- and asserts it was called zero times. The body carries a credential,
    so a scan that ran would have something to find and something to record;
    ``calls`` staying empty is the proof that ``off`` reads the policy before it
    touches a single input, which is what makes the escape hatch an escape hatch
    (``_scan_for_secrets`` records why per-finding suppression is not offered).
    """
    _configure(paths, SecretScanPolicy.OFF.value)
    calls: list[str] = []

    def recording_scan_text(
        text: str, *, max_findings: int = MAX_FINDINGS
    ) -> tuple[SecretFinding, ...]:
        calls.append(text)
        return scan_text(text, max_findings=max_findings)

    # Patched by dotted path -- the name `_findings_in` looks up in its own module
    # globals -- rather than through the module object, so the accept path's scan
    # is the recorded one wherever it reads the name.
    monkeypatch.setattr("theurian.application.proposal_service.scan_text", recording_scan_text)

    accepted = _accept(service, paths, LEAKY_BODY)

    assert accepted.secret_scan.policy is SecretScanPolicy.OFF
    assert calls == [], (
        "off scanned inputs and discarded the findings; the policy read must gate the scan, or "
        f"`off` pays the scan it promises not to run: {len(calls)} call(s)"
    )
    assert PLANTED_TOKEN in accepted.bodies[0].destination.read_text(encoding="utf-8"), (
        "off refused, or landed a body this fixture did not write"
    )


def test_a_migration_bytes_finding_is_located_by_the_migration_bytes_channel(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """A migration-bytes finding names *the migration file as written*, and means it (#349).

    The adversarial MEDIUM-5: a channel's location literal carries the reviewer's
    only pointer to where to look, and blanking ``_AT_MIGRATION_BYTES`` to ``"x"``
    survives the suite -- the comment tests above assert the family is reported and
    the migration does not land, never the *location* the finding wears.

    A YAML comment is the one input only the migration's raw bytes reach
    (``test_a_secret_in_a_yaml_comment_is_invisible_to_the_parsed_field_scan``
    proves the parsed field walk cannot), so under ``warn`` the sole finding is the
    bytes channel's, and its location is asserted to be the literal string --
    **hardcoded, not the imported ``_AT_MIGRATION_BYTES``**: the mutation this
    kills blanks that constant, and a test importing it would move with the
    mutation and never redden. If the literal is deliberately reworded, this is the
    test that should change with it.
    """
    _configure(paths, SecretScanPolicy.WARN.value)
    drafted = _a_proposal_whose_migration_carries_a_comment(service, _COMMENT_SECRET)

    accepted = service.accept(drafted.proposal_id)

    locations = {finding.location for finding in accepted.secret_scan.findings}
    assert "the migration file as written" in locations, (
        f"the comment finding is not located by the migration-bytes channel literal, so a reader "
        f"cannot tell which artifact to open: {sorted(locations)}"
    )


def test_a_landed_path_finding_is_located_by_the_landed_path_channel(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """A landed-path finding names *the landed path of body[N]*, its one distinct value (#349).

    Two mutations survive the suite together: blanking ``_AT_BODY_PATH`` to
    ``"x"``, and dropping the landed-path channel's ``yield`` outright. The second
    survives because #349 moved ``contentFile`` into the scanned field set, so the
    parsed field and the migration's bytes now report every credential the landed
    path does -- the landed-path channel's *only* remaining value is its distinct
    location, and a test that asserts a refusal or a family cannot see it go.

    So this pins the location and nothing else. The credential is over-reported --
    it is in the parsed ``contentFile`` and in the bytes too -- so **presence** of
    ``the landed path of body[0]`` is asserted, not that it is the only finding.
    The literal is **hardcoded, not built from the imported ``_AT_BODY_PATH``**:
    blanking that constant would move an imported expectation with it. Under
    ``warn`` so the finding rides out on the success result.
    """
    _configure(paths, SecretScanPolicy.WARN.value)
    tail = f"architecture/staging-{_LEAF_SECRET}.md"
    drafted = _a_proposal_whose_body_lands_at(service, tail)

    accepted = service.accept(drafted.proposal_id)

    locations = {finding.location for finding in accepted.secret_scan.findings}
    assert "the landed path of body[0]" in locations, (
        f"no finding is located by the landed-path channel -- its literal is blanked or the "
        f"channel's yield is gone, and the field and bytes reports carry no landed-path pointer: "
        f"{sorted(locations)}"
    )


def test_a_body_content_finding_is_located_by_the_body_content_channel(
    service: ProposalService, paths: ProjectPaths
) -> None:
    """A body-content finding names *the content of body[N]*, and means it (adversarial MEDIUM).

    The gap the two channel-location tests above left. Every sibling channel
    literal is pinned by a *hardcoded* string, but ``_AT_BODY_CONTENT`` was only
    ever asserted through the imported constant
    (``test_warn_lands_the_body_and_reports_what_it_found``), so blanking it to
    ``"x"`` moved that expectation with the mutation and survived the whole suite.
    A location an author cannot forge is the whole point of #360's fixed literal;
    an unpinned one is a location that says nothing when it is wrong.

    Hardcoded, not the imported ``_AT_BODY_CONTENT``: a test built on the constant
    reddens for no mutation of it. ``warn`` so the finding rides out on the success
    result. The index suffix is part of the literal a body-content location wears
    (:data:`_AT_BODY_PATH` and the metadata channels below carry one; the two name
    channels do not), so it is asserted with the finding, not stripped off it.
    """
    _configure(paths, SecretScanPolicy.WARN.value)

    accepted = _accept(service, paths, LEAKY_BODY)

    locations = {finding.location for finding in accepted.secret_scan.findings}
    assert "the content of body[0]" in locations, (
        f"the leaky body's finding is not located by the body-content channel literal, so a "
        f"reader cannot tell which artifact to open: {sorted(locations)}"
    )


def test_the_four_base_channel_literals_are_mutually_distinct() -> None:
    """No artifact channel wears another channel's location literal (adversarial MEDIUM).

    The blanking mutation the test above kills names *nothing*; this kills the
    sharper one it exposed -- a channel that names *another* channel. Colliding
    ``_AT_BODY_CONTENT`` with ``_AT_MIGRATION_NAME`` survives every location test,
    because a body-content location carries a ``[N]`` index suffix: the collided
    ``"the migration filename[0]"`` is never ``== "the migration filename"``, so
    ``test_a_body_content_finding_never_spoofs_the_migration_filename_channel``'s
    ``_AT_MIGRATION_NAME not in locations`` stays green -- which is exactly the
    HIGH-2 spoof this PR closed, walked back in one edit. Pinned over the constants
    directly, because a channel wearing another's literal misdirects a reviewer
    whatever suffix the finding carries.
    """
    literals = (_AT_BODY_CONTENT, _AT_BODY_PATH, _AT_MIGRATION_BYTES, _AT_MIGRATION_NAME)
    assert len(set(literals)) == len(literals), (
        f"two artifact channels share a location literal, so one spoofs the other: {literals}"
    )
