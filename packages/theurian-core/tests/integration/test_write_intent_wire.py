"""The write-intent tools driven over the wire (ADR-0032 decisions 3, 4).

``generateMigrationDraft``'s v1 operation partition and the evidence-disagreement
refusal are pinned in isolation elsewhere -- ``test_draft_only_proposals.py`` for
the partition set, ``test_generate_migration_draft.py`` for the service entry.
This drives them **through the registered tool over the transport**, which is
where a wiring defect lives: that the tool forwards the caller's document to the
v1 gate, that it wires the top-level ``agentId``/``taskId`` into the evidence
disagreement check, and that the two CLI-only guarantees decision 3 names arrive
as *schema* refusals rather than as failures from inside the generator.

Everything goes through ``mcp_session`` for the reason
``test_input_validation_wire.py`` records: ``server.call_tool`` is the SDK's tool
dispatcher and sits below the ``ServerMiddleware`` tier, so an in-process call
never reaches the input-validation seat these schema refusals come from.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Final

import pytest
from git_harness import commit_migrations
from jsonschema import Draft202012Validator
from migration_fixtures import body_pin
from typer.testing import CliRunner

from theurian.application.project_service import ProjectRegistry
from theurian.application.proposal_service import _REFUSED_TO_CLI, _REFUSED_TO_CONTENT_PATH
from theurian.cli.main import app
from theurian.daemon.runner import build_server
from theurian.daemon.server import MAX_REQUEST_BODY_BYTES
from theurian.domain.migration import OperationKind
from theurian.mcp.validation import _mismatch

from mcp_wire_session import mcp_session  # isort: skip

#: The published input schema for ``knowledge.proposeChange`` -- the same file the
#: SEC-12 middleware loads.
_PROPOSE_CHANGE_SCHEMA: Final = (
    Path(__file__).resolve().parents[4] / "schemas/mcp/knowledge-propose-change-input.schema.json"
)

pytestmark = pytest.mark.integration

runner = CliRunner()

MIGRATION_ID: Final = "01K1AAAAAA01234567890ABCDE"
REVISION_ID: Final = "01K1AAAREV01234567890ABCDE"
BODY: Final = "# Authentication policy\n\nEvery call carries a signed token.\n"

MIGRATION: Final = f"""apiVersion: theurian.dev/v1
id: {MIGRATION_ID}
createdAt: 2026-08-02T10:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.auth-policy
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.auth-policy
    revisionId: {REVISION_ID}
    contentFile: ../knowledge/architecture/auth-policy.md
    contentSha256: {body_pin(BODY)}
    metadata:
      title: Authentication policy
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/auth-policy.md
"""

#: A well-formed evidence object: the four fields ADR-0013 point 5 requires,
#: non-empty, so a write-intent call reaches the behaviour under test rather than
#: an evidence refusal.
EVIDENCE: Final = {
    "agentId": "claude-code",
    "taskId": "task-7",
    "model": "claude-opus-5",
    "reasoning": "The alias review settled that the item should be deprecated.",
}


def _run(*args: str) -> None:
    if args[:2] == ("migrate", "apply"):
        commit_migrations()
    result = runner.invoke(app, [*args, "--json"], catch_exceptions=False)
    assert result.exit_code == 0, result.stdout + (result.stderr or "")


@pytest.fixture
def demo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ProjectRegistry]:
    """A registered ``demo`` project with one approved item, built by the real CLI.

    The write-intent tools resolve the project through ``_resolve`` before they do
    anything else (the read-side gates they inherit), so a wire call needs a real
    registered, built project -- an empty registry would refuse every call at
    resolution and never reach the behaviour under test.
    """
    root = tmp_path / "demo"
    root.mkdir()
    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603

    data_dir = tmp_path / "datadir"
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(data_dir))
    monkeypatch.chdir(root)

    _run("init")
    (root / ".theurian/knowledge/architecture/auth-policy.md").write_text(BODY)
    (root / f".theurian/migrations/{MIGRATION_ID}-auth.yaml").write_text(MIGRATION)
    _run("project", "register")
    _run("migrate", "apply")

    yield ProjectRegistry.default(data_dir)


def _document(*operations: dict[str, Any]) -> dict[str, Any]:
    """A caller migration document without the identity the service mints."""
    return {"author": "platform-team@example.com", "operations": list(operations)}


def _proposals(registry: ProjectRegistry) -> set[str]:
    """The proposal directory names under the demo project, both locations."""
    root = Path(registry.load()["demo"]["rootPath"])
    found: set[str] = set()
    for parent in (root / ".theurian/proposals", root / ".theurian/proposals-local"):
        if parent.exists():
            found |= {entry.name for entry in parent.iterdir() if entry.is_dir()}
    return found


# -- Decision 3: generateMigrationDraft refuses four kinds, admits the rest -


@pytest.mark.parametrize("kind", sorted(op.value for op in _REFUSED_TO_CONTENT_PATH))
def test_a_content_operation_is_refused_to_the_content_path_over_the_wire(
    demo: ProjectRegistry, tmp_path: Path, kind: str
) -> None:
    """createItem/upsertRevision are refused through the tool, redirected to the content path.

    Parametrized over ``_REFUSED_TO_CONTENT_PATH`` rather than a hand-written pair,
    so the driving count moves with the enum-derived set (ADR-0032 decision 3): a
    kind added to that set joins this by existing, and one removed leaves it. The
    v1 gate reads only ``op``, so a minimal operation reaches the refusal.

    **The redirect target reaches the wire.** The tool body catches the v1 gate's
    ``ProposalError`` and re-raises through ``_with_remedy``, which folds
    ``exc.remedy`` into the message, so the "Use the knowledge.proposeChange tool"
    redirect is what a wire caller actually reads -- not just the message-half
    "it moves knowledge content, which the content path owns". Before that catch, the
    refusal reached the ``_forwarding`` seam, which crosses a below-body
    ``TheurianError`` as ``ToolError(str(exc))`` and dropped ``exc.remedy``, so an
    arbitrary-vendor agent hitting a pulled kind was told the refusal with no
    destination (the MEDIUM cluster 3 found). The remedy's wording is pinned at the
    service level by ``test_generate_migration_draft.py``; this pins that it crosses.
    """
    with mcp_session(build_server(demo), tmp_path / "data") as call:
        answer = call(
            "knowledge.generateMigrationDraft",
            {
                "projectId": "demo",
                "document": _document({"op": kind, "itemId": "architecture.auth-policy"}),
                "evidence": EVIDENCE,
            },
        )

    result = answer["result"]
    assert result["isError"] is True, answer
    text = result["content"][0]["text"]
    assert kind in text, text
    assert "content path" in text, text
    # The redirect itself, folded in by `_with_remedy`. RED before the tool-body
    # catch, when the `_forwarding` seam dropped `exc.remedy`: the whole point of the
    # refusal, so a caller has somewhere to go (ADR-0032 decision 3).
    assert "knowledge.proposeChange" in text, text
    assert _proposals(demo) == set(), "a refused content operation wrote a proposal"


@pytest.mark.parametrize("kind", sorted(op.value for op in _REFUSED_TO_CLI))
def test_a_read_control_operation_is_refused_to_the_cli_over_the_wire(
    demo: ProjectRegistry, tmp_path: Path, kind: str
) -> None:
    """changeSensitivity/restoreItem are refused through the tool, redirected to the CLI.

    Parametrized over ``_REFUSED_TO_CLI`` for the same reason as above: the two
    non-content read-control movers are pulled in v1 because the reviewer of the
    pull request cannot see what they admit, and are authored through the CLI
    instead.

    As with the content-path family, the redirect reaches the wire: the tool body
    routes the v1 gate's ``ProposalError`` through ``_with_remedy``, so a caller reads
    both the message-half "it changes who may read an item, and the reviewer of the
    pull request cannot see what that admits" *and* the "theurian migrate apply"
    redirect that tells them where to author it instead. The redirect used to stay
    behind at the ``_forwarding`` seam (the MEDIUM cluster 3 found); the remedy's
    wording is pinned at the service level by ``test_generate_migration_draft.py``.
    """
    with mcp_session(build_server(demo), tmp_path / "data") as call:
        answer = call(
            "knowledge.generateMigrationDraft",
            {
                "projectId": "demo",
                "document": _document({"op": kind, "itemId": "architecture.auth-policy"}),
                "evidence": EVIDENCE,
            },
        )

    result = answer["result"]
    assert result["isError"] is True, answer
    text = result["content"][0]["text"]
    assert kind in text, text
    assert "who may read an item" in text, text
    # The CLI redirect itself, folded in by `_with_remedy`. RED before the tool-body
    # catch dropped `exc.remedy` at the `_forwarding` seam (ADR-0032 decision 3).
    assert "theurian migrate apply" in text, text
    assert _proposals(demo) == set(), "a refused read-control operation wrote a proposal"


def test_the_wire_refused_set_is_exactly_the_two_pulled_pairs(demo: ProjectRegistry) -> None:
    """The refused set is asserted against ``OperationKind``, not listed here.

    Four of the fourteen kinds are refused: the two content movers and the two
    read-control movers. The parametrized cases above drive whatever those two
    frozensets hold; this pins that they are disjoint, that they are four in all,
    and that the admitted set is the remaining ten -- so a fifteenth kind added to
    the enum lands in none of the three and forces a deliberate routing rather than
    an admit-by-omission (ADR-0032 decision 3).
    """
    refused = _REFUSED_TO_CONTENT_PATH | _REFUSED_TO_CLI

    assert _REFUSED_TO_CONTENT_PATH.isdisjoint(_REFUSED_TO_CLI)
    assert len(refused) == 4
    assert refused <= set(OperationKind)
    assert {op.value for op in _REFUSED_TO_CONTENT_PATH} == {"createItem", "upsertRevision"}
    assert {op.value for op in _REFUSED_TO_CLI} == {"changeSensitivity", "restoreItem"}


def test_an_admitted_operation_lands_a_proposal_over_the_wire(
    demo: ProjectRegistry, tmp_path: Path
) -> None:
    """At least one admitted kind lands a proposal, so the refusals above are not
    satisfied by a tool that refuses everything (ADR-0032 decision 3).

    ``deprecateItem`` is an admitted kind and needs only an ``itemId``. The tool
    returns the drafted proposal's identifiers, and the proposal directory really
    lands under the project.
    """
    with mcp_session(build_server(demo), tmp_path / "data") as call:
        answer = call(
            "knowledge.generateMigrationDraft",
            {
                "projectId": "demo",
                "document": _document(
                    {"op": "deprecateItem", "itemId": "architecture.auth-policy"}
                ),
                "evidence": EVIDENCE,
            },
        )

    result = answer["result"]
    assert result["isError"] is False, answer
    landed = result["structuredContent"]
    assert landed["proposalId"] in _proposals(demo), landed


# -- Decision 4: agentId/taskId stated twice and disagreeing is refused ------


@pytest.mark.parametrize("field", ["agentId", "taskId"])
def test_a_top_level_identity_that_disagrees_with_evidence_is_refused(
    demo: ProjectRegistry, tmp_path: Path, field: str
) -> None:
    """The tool wires the top-level tool-context identity into the disagreement check.

    ``evidence`` is authoritative; a top-level ``agentId``/``taskId`` that
    disagrees with its evidence counterpart is refused rather than resolved by
    precedence, so one identity cannot be recorded in ``evidence.json`` and a
    different one in whatever observes the request (ADR-0032 decision 4). Driven
    through the tool because the wiring -- that the top-level field reaches
    ``_wire_evidence`` at all -- is what a handler defect would drop.
    """
    with mcp_session(build_server(demo), tmp_path / "data") as call:
        answer = call(
            "knowledge.generateMigrationDraft",
            {
                "projectId": "demo",
                "document": _document(
                    {"op": "deprecateItem", "itemId": "architecture.auth-policy"}
                ),
                "evidence": EVIDENCE,
                field: "a-different-identity",
            },
        )

    result = answer["result"]
    assert result["isError"] is True, answer
    text = result["content"][0]["text"]
    assert field in text and "stated twice" in text, text
    assert _proposals(demo) == set(), "a refused disagreement wrote a proposal"


@pytest.mark.parametrize("field", ["agentId", "taskId"])
def test_a_top_level_identity_that_agrees_is_accepted(
    demo: ProjectRegistry, tmp_path: Path, field: str
) -> None:
    """Control: agreement is accepted, so the refusal is not satisfied by refusing
    every call that carries the top-level field at all (ADR-0032 decision 4)."""
    with mcp_session(build_server(demo), tmp_path / "data") as call:
        answer = call(
            "knowledge.generateMigrationDraft",
            {
                "projectId": "demo",
                "document": _document(
                    {"op": "deprecateItem", "itemId": "architecture.auth-policy"}
                ),
                "evidence": EVIDENCE,
                field: EVIDENCE[field],
            },
        )

    result = answer["result"]
    assert result["isError"] is False, answer
    assert result["structuredContent"]["proposalId"] in _proposals(demo)


def test_the_top_level_identity_absent_is_accepted(demo: ProjectRegistry, tmp_path: Path) -> None:
    """Control: the top-level field absent is accepted -- the disagreement check is
    the exception, not the rule, so a call carrying only the evidence identity is
    served (ADR-0032 decision 4)."""
    with mcp_session(build_server(demo), tmp_path / "data") as call:
        answer = call(
            "knowledge.generateMigrationDraft",
            {
                "projectId": "demo",
                "document": _document(
                    {"op": "deprecateItem", "itemId": "architecture.auth-policy"}
                ),
                "evidence": EVIDENCE,
            },
        )

    result = answer["result"]
    assert result["isError"] is False, answer
    assert result["structuredContent"]["proposalId"] in _proposals(demo)


# -- Decision 3: the CLI-only guarantees arrive as schema refusals -----------


def test_duplicate_labels_are_refused_by_the_schema_over_the_wire(
    demo: ProjectRegistry, tmp_path: Path
) -> None:
    """A duplicate label is a *schema* refusal naming the key path, not a generator
    failure (ADR-0032 decision 3).

    The CLI deduplicates ``--label`` before the generator ever sees the pair; the
    wire path has no such step, so the published input schema sets ``uniqueItems``
    on ``labels[]`` and the SEC-12 middleware refuses the duplicate ahead of the
    handler, naming the offending key. Asserted on the middleware's own wording
    (``published input schema``) and the ``labels`` key path, so a duplicate that
    reached the generator -- and failed there against the migration schema's own
    ``uniqueItems`` over a document the caller cannot see -- would be RED.
    """
    with mcp_session(build_server(demo), tmp_path / "data") as call:
        answer = call(
            "knowledge.proposeChange",
            {
                "projectId": "demo",
                "itemId": "architecture.retry-policy",
                "title": "Retry policy",
                "kind": "architecture",
                "owner": "platform-team",
                "author": "platform-team@example.com",
                "description": "Record the retry budget.",
                "body": "# Retry policy\n\nThree attempts.\n",
                "contentType": "text/markdown",
                "evidence": EVIDENCE,
                "labels": ["authored-in-theurian", "authored-in-theurian"],
            },
        )

    result = answer["result"]
    assert result["isError"] is True, answer
    text = result["content"][0]["text"]
    assert "published input schema" in text, text
    assert "labels" in text, text
    assert "uniqueItems" in text, text
    assert _proposals(demo) == set(), "a duplicate-label refusal wrote a proposal"


def test_the_body_size_bound_is_a_schema_constraint_on_the_body_key(demo: ProjectRegistry) -> None:
    """An over-long body is a *schema* refusal naming the ``body`` key path, not a
    generator failure (ADR-0032 decision 3).

    The CLI caps the body at the body file's ``stat().st_size``; there is no file
    on this path, so the published input schema carries a ``maxLength`` on ``body``
    and the input-validation seat owns body size -- the generator never sees an
    over-long body. This drives that bound against the same published schema the
    SEC-12 middleware loads: an over-``maxLength`` body is a ``maxLength`` violation
    on the ``body`` key, and the middleware's own refusal rendering names that key
    path and does not echo the (large) value back.

    **Driven against the loaded schema rather than over the full transport,
    deliberately, and this is reported.** Over the wire two tighter gates fire
    first: the transport cap (``MAX_REQUEST_BODY_BYTES`` bytes) and
    ``mcp/validation.py``'s render bound (``MAX_PARAMS_RENDERED_CHARS`` =
    12,582,912 rendered characters, checked before ``jsonschema``). Both sit below
    this ``maxLength`` (26,214,400 code points, equal to the transport cap), so the
    ``jsonschema`` ``maxLength`` is a belt behind them and never the gate a wire
    request meets. What this pins is that the bound *is* a schema constraint on the
    ``body`` key, which is the ADR's "refused at the schema" property; the
    render-bound shadowing is recorded as a finding, not papered over.
    """
    schema = json.loads(_PROPOSE_CHANGE_SCHEMA.read_text(encoding="utf-8"))
    body_schema = schema["properties"]["body"]

    assert body_schema.get("maxLength") == MAX_REQUEST_BODY_BYTES, (
        f"the published schema does not bound `body` at the transport cap: "
        f"{body_schema.get('maxLength')} (transport cap {MAX_REQUEST_BODY_BYTES})"
    )

    # The published `body` subschema, validated in an object so the failing path
    # is the `body` key. `iter_errors` over `{"body": <over>}` yields exactly the
    # maxLength violation.
    validator = Draft202012Validator({"type": "object", "properties": {"body": body_schema}})
    over_long = "x" * (body_schema["maxLength"] + 1)
    maxlength_errors = [
        error
        for error in validator.iter_errors({"body": over_long})
        if error.validator == "maxLength" and list(error.absolute_path) == ["body"]
    ]

    assert maxlength_errors, (
        "the published schema does not refuse an over-length body as a `maxLength` "
        "violation on the `body` key, so body size would be a generator concern rather "
        "than a schema one (ADR-0032 decision 3)"
    )

    refusal = _mismatch("knowledge.proposeChange", maxlength_errors[0])
    assert "body" in refusal.message, refusal.message
    assert "published input schema" in refusal.message, refusal.message
    assert over_long not in refusal.message, "the refusal echoed the oversized body back"


# -- The proposeChange tool's own catch folds its designed remedy in ---------


def test_the_concurrency_guard_redirect_reaches_the_wire(
    demo: ProjectRegistry, tmp_path: Path
) -> None:
    """proposeChange's optimistic-concurrency refusal names its cure on the wire.

    ``knowledge.proposeChange`` has its own ``except ProposalError`` seam, separate
    from ``generateMigrationDraft``'s, so it is driven separately: an update to an
    item that already exists, with no ``expectedRevision``, is refused by
    ``_check_expected_revision`` with a ``ProposalError`` whose ``remedy`` names the
    concrete cure ("Pass --expected-revision <current> to update it"). The demo item
    ``architecture.auth-policy`` is approved and in view of the caller-scoped
    revision read, so the guard fires the "already exists" branch.

    The assertion is on the *remedy*'s own phrase ("Pass --expected-revision"), which
    lives only in ``exc.remedy`` and not in the message -- so it is RED before the
    tool-body catch, when the ``_forwarding`` seam crossed the refusal as
    ``str(exc)`` and dropped the cure, and GREEN once ``_with_remedy`` folds it in
    (ADR-0032 decision 3). A source anchor is supplied so the request clears INV-8
    and the empty-field checks and actually reaches the concurrency guard.
    """
    with mcp_session(build_server(demo), tmp_path / "data") as call:
        answer = call(
            "knowledge.proposeChange",
            {
                "projectId": "demo",
                "itemId": "architecture.auth-policy",
                "title": "Authentication policy",
                "kind": "architecture",
                "owner": "platform-team",
                "author": "platform-team@example.com",
                "description": "Tighten the token lifetime.",
                "body": "# Authentication policy\n\nTokens live one hour.\n",
                "contentType": "text/markdown",
                "evidence": EVIDENCE,
                "sourceAnchors": [{"provider": "git", "sourceUri": "git://demo/auth-policy.md"}],
            },
        )

    result = answer["result"]
    assert result["isError"] is True, answer
    text = result["content"][0]["text"]
    # The message-half already says an update must state a revision; the cure that
    # names *which* revision to pass lives only in the remedy, dropped by
    # `_forwarding` until the tool body routed the refusal through `_with_remedy`.
    assert "already exists at revision" in text, text
    assert "Pass --expected-revision" in text, text
    assert _proposals(demo) == set(), "a refused concurrency guard wrote a proposal"
