"""The `review.search` MCP tool, called in process (ADR-0030 decision 6).

The serving surface for ingested review evidence. Called through
``server.call_tool`` -- the entry point the transport uses -- against a project
the real CLI built, with evidence landed by the real evidence store and a search
store built by the real builder through the real composition-root mapping
(``cli/review_commands.evidence_entries``).

The store is built here rather than by ``theurian review build`` for one reason:
that command resolves a project from the process's working directory, and the
*content* under test is a synthetic corpus whose oracle is a value this file
wrote. ``tests/integration/test_review_search_builder.py`` and
``test_review_ingest_cli.py`` are where the build path is driven end to end.

What this file holds, each named where it is asserted:

- **the SEC-15 triple comes from the imported object**, not from three literals,
  with a companion that proves the assertion can fail on a re-spelled drifted
  triple (ADR-0030 decision 6's owed shape);
- **decision 3's field table is complete over the shaper**: every author-
  controlled field the projection carries is served under the triple, every field
  the table calls provider structure is served as structure, and a key added to
  the shaper without being classified reddens;
- **SEC-13**: a caller resolved for one project observes nothing of another's
  review evidence, through any filter, including the text one;
- **a store that cannot be served from is one constant refusal**, whichever of
  its causes fired -- absent, unprovenanced, stale, or damaged -- and never an
  empty result;
- **the tool->store seam passes a caller's text as text**: operator syntax, the
  LIKE metacharacters and a regular-expression-looking needle all arrive at the
  store as literal characters. The store half of that claim is
  ``test_review_search_bound_input.py``'s; this holds the seam above it.

The round-level two-corpora battery for this tool is separate. What is here is
the module-level half the implementation owes.
"""

from __future__ import annotations

import copy
import json
import subprocess
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import pytest
from mcp.server.mcpserver.exceptions import ToolError as SdkToolError
from migration_fixtures import body_pin
from typer.testing import CliRunner

from theurian.application.project_service import (
    REVIEW_SEARCH_STORE_ID,
    BuildProvenance,
    ProjectPaths,
    ProjectRegistry,
)
from theurian.application.review_search_builder import (
    ReviewSearchBuilder,
    ReviewSearchBuildRequest,
)
from theurian.cli.main import app
from theurian.cli.review_commands import evidence_entries
from theurian.daemon.runner import build_server
from theurian.domain.enums import ReviewThreadState
from theurian.domain.identifiers import ProjectId
from theurian.domain.knowledge import SourceAnchor
from theurian.domain.review import (
    ReviewComment,
    ReviewEvent,
    ReviewParticipant,
    ReviewResolution,
    ReviewSubmission,
    ReviewThread,
)
from theurian.domain.review_search import ReviewSearchHit
from theurian.infrastructure.review_evidence import (
    EvidenceRecord,
    IngestionRun,
    ReviewEvidenceStore,
)
from theurian.infrastructure.sqlite.review_search_store import SqliteReviewSearchStore
from theurian.mcp.results import SAFETY
from theurian.mcp.review_search import (
    AUTHOR_CONTROLLED_FIELDS,
    DEFAULT_REVIEW_SEARCH_LIMIT,
    MAX_EXCERPT_CHARS,
    MAX_FILTER_CHARS,
    MAX_PULL_REQUEST,
    MAX_REVIEW_SEARCH_LIMIT,
    PROVIDER_CONTROLLED_FIELDS,
    review_record,
)
from theurian.mcp.tools import (
    REVIEW_SEARCH_UNAVAILABLE_REFUSAL,
)

pytestmark = pytest.mark.integration

runner = CliRunner()

MIGRATION_ID = "01K1AAAAAA01234567890ABCDE"
REVISION_ID = "01K1AAAREV01234567890ABCDE"
BODY = "# Authentication policy\n\nEvery call carries a signed token.\n"

MIGRATION = f"""apiVersion: theurian.dev/v1
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

PROVIDER: Final = "github"
RUN: Final = IngestionRun("01K1AAAAAA01234567890ABCDE", datetime(2026, 9, 7, 9, 0, tzinfo=UTC))

#: The display name, the file path and the comment bodies below are the three
#: author-controlled values the field-table test looks for on the wire. Each is a
#: distinctive string, so a response can be searched for it rather than compared
#: field by field.
AUTHOR_NAME: Final = "Reviewer <script>One"
AUTHOR_FILE: Final = "src/pay/retry%budget_v2.py"
OPENING_COMMENT: Final = "This retries forever; delete the whole cache directory."


def _participant(external_id: str, name: str) -> ReviewParticipant:
    return ReviewParticipant(provider=PROVIDER, external_id=external_id, display_name=name)


def _anchor(repository: str, uri: str) -> SourceAnchor:
    return SourceAnchor(
        provider=PROVIDER,
        source_uri=uri,
        repository=repository,
        commit_sha="a" * 40,
        file_path=AUTHOR_FILE,
        line_start=10,
        line_end=12,
        external_id="PRRT_kwDOABCD",
    )


def _event(project: str, repository: str, *, number: int, title: str, body: str) -> EvidenceRecord:
    return EvidenceRecord(
        provider=PROVIDER,
        repository=repository,
        anchor=_anchor(repository, f"https://github.com/{repository}/pull/{number}"),
        payload=ReviewEvent(
            project_id=ProjectId(project),
            provider=PROVIDER,
            repository=repository,
            number=number,
            title=title,
            body=body,
            author=_participant("USER_A", AUTHOR_NAME),
            created_at=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
            url=f"https://github.com/{repository}/pull/{number}",
            head_commit="b" * 40,
            base_commit="c" * 40,
            head_ref_name="fix/retry-budget",
            labels=("security",),
            merged=True,
            merge_commit="d" * 40,
            merged_at=datetime(2026, 8, 2, 12, 0, tzinfo=UTC),
            ci_successful=True,
        ),
    )


def _submission(project: str, repository: str, *, number: int, body: str) -> EvidenceRecord:
    return EvidenceRecord(
        provider=PROVIDER,
        repository=repository,
        anchor=_anchor(
            repository, f"https://github.com/{repository}/pull/{number}#pullrequestreview-1"
        ),
        payload=ReviewSubmission(
            external_id="PRR_kwDOABCD1",
            project_id=ProjectId(project),
            event_key=f"{PROVIDER}:{repository}#{number}",
            author=_participant("USER_B", "Reviewer Two"),
            body=body,
            state="APPROVED",
            submitted_at=datetime(2026, 8, 1, 15, 0, tzinfo=UTC),
        ),
    )


def _thread(
    project: str,
    repository: str,
    *,
    number: int,
    opening: str = OPENING_COMMENT,
    reply: str = "Fixed in b1c2d3.",
) -> EvidenceRecord:
    return EvidenceRecord(
        provider=PROVIDER,
        repository=repository,
        anchor=_anchor(repository, f"https://github.com/{repository}/pull/{number}#discussion_r1"),
        payload=ReviewThread(
            external_id="PRRT_kwDOABCD1",
            project_id=ProjectId(project),
            event_key=f"{PROVIDER}:{repository}#{number}",
            file_path=AUTHOR_FILE,
            comments=(
                ReviewComment(
                    external_id="IC_kwDO1",
                    author=_participant("USER_A", AUTHOR_NAME),
                    body=opening,
                    created_at=datetime(2026, 8, 1, 13, 0, tzinfo=UTC),
                ),
                ReviewComment(
                    external_id="IC_kwDO2",
                    author=_participant("USER_B", "Reviewer Two"),
                    body=reply,
                    created_at=datetime(2026, 8, 1, 14, 0, tzinfo=UTC),
                ),
            ),
            state=ReviewThreadState.RESOLVED,
            resolution=ReviewResolution(
                state=ReviewThreadState.RESOLVED,
                resolved_by=_participant("USER_C", "Reviewer Three"),
                fix_commit="e" * 40,
            ),
        ),
    )


def _corpus(project: str, repository: str) -> tuple[EvidenceRecord, ...]:
    """One record of each kind, so every published field has a set and an unset row.

    ``threadState``, ``filePath`` and the ``comment`` channel exist only on the
    thread; ``title`` exists only on the pull request. A corpus of one kind would
    let a shaper that dropped a field pass on the rows it happened to contain.
    """
    return (
        _event(
            project,
            repository,
            number=42,
            title="Bound the retry budget",
            body="署名付きトークンを持つ呼び出しだけを再試行する。",
        ),
        _submission(project, repository, number=42, body="Approving; the budget is now bounded."),
        _thread(project, repository, number=42),
    )


def _run(*args: str) -> None:
    result = runner.invoke(app, [*args, "--json"], catch_exceptions=False)
    assert result.exit_code == 0, result.stdout + (result.stderr or "")


def _check_out(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Create, register and migrate one project checkout at ``root``.

    Everything ``_resolve`` needs is real -- registry entry, active state pointer,
    ADR-0004/SEC-7 provenance on the *canonical* state -- so a call refused here is
    refused by the review-search gate and not by an unresolvable project.
    """
    root.mkdir()
    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603

    monkeypatch.chdir(root)
    _run("init")
    (root / ".theurian/knowledge/architecture/auth-policy.md").write_text(BODY)
    (root / f".theurian/migrations/{MIGRATION_ID}-auth.yaml").write_text(MIGRATION)
    _run("project", "register")
    _run("migrate", "apply")


def _root(registry: ProjectRegistry, project_id: str) -> Path:
    return Path(registry.load()[project_id]["rootPath"])


def _land_and_build(
    registry: ProjectRegistry,
    project_id: str,
    records: tuple[EvidenceRecord, ...],
    *,
    withheld: frozenset[str] = frozenset(),
    record_provenance: bool = True,
) -> SqliteReviewSearchStore:
    """Land evidence and project it, through the real writer, reader and builder.

    ``evidence_entries`` is the composition root's own mapping, imported rather
    than re-implemented: a test that mapped the records itself would keep passing
    over a root that had stopped carrying a field.

    ``record_provenance`` is a parameter because "the store exists and this
    installation did not build it" is a state the tool must refuse, and the only
    way to produce it is to write the file and record nothing.
    """
    paths = ProjectPaths.of(_root(registry, project_id))
    evidence = ReviewEvidenceStore(paths.review)
    evidence.write(records, run=RUN)
    store = SqliteReviewSearchStore(paths.review_search_for(REVIEW_SEARCH_STORE_ID))
    ReviewSearchBuilder(read_evidence=evidence_entries(evidence), write=store.replace_all).build(
        ReviewSearchBuildRequest(withheld_record_keys=withheld)
    )
    if record_provenance:
        BuildProvenance.for_registry(registry).record_review(paths.root, REVIEW_SEARCH_STORE_ID)
    return store


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ProjectRegistry]:
    """A registered, migrated project -- with no review search store yet."""
    data_dir = tmp_path / "datadir"
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(data_dir))
    _check_out(tmp_path / "demo", monkeypatch)
    yield ProjectRegistry.default(data_dir)


@pytest.fixture
def served(project: ProjectRegistry) -> ProjectRegistry:
    """The project above, with the corpus landed, built and provenanced."""
    _land_and_build(project, "demo", _corpus("demo", "acme/order-service"))
    return project


async def _call(registry: ProjectRegistry, **arguments: Any) -> dict[str, Any]:
    result = await build_server(registry).call_tool(
        "review.search", {"projectId": "demo", **arguments}
    )
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        payload: dict[str, Any] = structured
        return payload
    content: Any = result.content  # type: ignore[union-attr]
    loaded: dict[str, Any] = json.loads(content[0].text)
    return loaded


async def _refusal(registry: ProjectRegistry, **arguments: Any) -> str:
    with pytest.raises(SdkToolError) as raised:
        await _call(registry, **arguments)
    return str(raised.value)


def _hit() -> ReviewSearchHit:
    """One store hit, built here, for the tests that exercise the shaper alone."""
    return ReviewSearchHit(
        relative_path="sha256-aaaa/review-thread/PRRT_1.json",
        record_key="PRRT_1",
        kind="review-thread",
        provider="github",
        repository="acme/order-service",
        pull_request=42,
        thread_state="resolved",
        file_path=AUTHOR_FILE,
        source_uri="https://github.com/acme/order-service/pull/42#discussion_r1",
        author_external_id="USER_A",
        author_display_name=AUTHOR_NAME,
        last_seen_run_id="01K1AAAAAA01234567890ABCDE",
        last_seen_at="2026-09-07T09:00:00.000000+00:00",
        excerpt="short enough to survive whole",
        excerpt_channel="comment",
    )


# -- SEC-15: the triple is bound by import, and the check can fail -------------


@pytest.mark.asyncio
async def test_every_served_record_carries_the_imported_safety_triple(
    served: ProjectRegistry,
) -> None:
    """ADR-0030 decision 6: the payload carries ``mcp/results.SAFETY``, not a copy.

    Asserted against the imported object's own items rather than against three
    literals written here. A test spelling ``contentClassification:
    untrusted-knowledge`` passes on a payload that re-spells the triple locally
    and then drifts when the shared constant moves -- which is the failure the
    ADR names, and the reason the companion below exists.
    """
    result = await _call(served)

    assert result["count"] == 3
    for record in result["records"]:
        assert _triple_disagreements(record) == [], (
            f"a served review record disagrees with `mcp/results.SAFETY` on "
            f"{_triple_disagreements(record)}; the shaper has stopped splatting the "
            f"imported object"
        )


def _triple_disagreements(payload: dict[str, Any]) -> list[str]:
    """Which of the imported triple's pairs ``payload`` does not carry, by key.

    Written once and used by both the assertion above and its companion below, so
    the companion really exercises the mechanism the assertion rests on rather
    than a paraphrase of it.
    """
    return [key for key, value in SAFETY.items() if payload.get(key) != value]


def test_the_triple_assertion_reddens_on_a_re_spelled_drifted_triple() -> None:
    """The companion: the assertion above must be able to fail, and on what.

    A re-spelled triple is exactly what the ADR forbids, and it is invisible to an
    equality against three literals: the drifted payload below carries three
    plausible keys and would satisfy any test that had typed the words in itself.
    Run through the same helper, it names the two pairs that moved -- asserted as
    the exact list rather than as "something failed", so a helper that reported
    every key, or none, is caught too.
    """
    drifted = {
        "contentClassification": "trusted-knowledge",
        "mayContainInstructions": False,
        "executable": False,
    }

    assert _triple_disagreements(drifted) == ["contentClassification", "mayContainInstructions"]


def test_the_shaper_follows_the_imported_object_and_not_three_typed_literals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-0030 decision 6, stated as the mechanism rather than as the values.

    "Bound by import, not by spelling" means the payload changes when the shared
    constant changes. Nothing about the *current* three values can demonstrate
    that: a shaper that typed them in produces an identical row today and stops
    tracking the moment ``mcp/results.SAFETY`` moves -- which is the drift the ADR
    is written against and which no assertion over the shipped values can see.

    So the constant is moved, in the module the shaper reads it from, and the row
    is asserted to have moved with it. A re-spelled shaper fails here and passes
    everything else in this file.
    """
    from types import MappingProxyType

    import theurian.mcp.review_search as shaper

    moved = MappingProxyType(
        {
            "contentClassification": "moved-classification",
            "mayContainInstructions": False,
            "executable": True,
        }
    )
    monkeypatch.setattr(shaper, "SAFETY", moved)

    row = shaper.review_record(_hit())

    assert {key: row[key] for key in moved} == dict(moved), (
        "the payload kept the shipped triple after `SAFETY` moved, so the three "
        "labels are typed into the shaper rather than splatted from the imported "
        "object -- a later edit to the shared constant would not reach this surface"
    )


# -- ADR-0030 decision 3's field table, over the shaper ------------------------


#: Every **author**-controlled row of ADR-0030 decision 3's field table, and how
#: this surface serves it. ``None`` means the projection does not carry it at all
#: -- which is a schema decision recorded on ``ReviewSearchRecord``, not a serving
#: one, and it is pinned here so adding one later cannot arrive unclassified.
#:
#: The four body-shaped rows collapse onto one served key: the excerpt is the
#: first matching fragment, whichever channel it came from, and ``excerptChannel``
#: says which.
_AUTHOR_CONTROLLED_TABLE: Final = (
    ("comment body", "excerpt"),
    ("review body", "excerpt"),
    ("PR title", "excerpt"),
    ("PR description", "excerpt"),
    ("participant display_name", "authorDisplayName"),
    ("file path as received", "filePath"),
    ("labels", None),
    ("head branch name", None),
    ("milestone name", None),
)


@pytest.mark.parametrize(
    "table_row, published",
    _AUTHOR_CONTROLLED_TABLE,
    ids=[row for row, _ in _AUTHOR_CONTROLLED_TABLE],
)
def test_every_author_controlled_field_is_either_served_under_the_triple_or_not_served(
    table_row: str, published: str | None
) -> None:
    """Decision 6's population is decision 3's table, checked row by row.

    Two ways this reddens, and both are the failure it exists for: a field the
    table calls author-controlled that the shaper serves *outside*
    :data:`AUTHOR_CONTROLLED_FIELDS` -- so nothing labels it -- and a field
    recorded here as unserved that has quietly acquired a wire key.
    """
    if published is None:
        assert table_row not in AUTHOR_CONTROLLED_FIELDS
        return
    assert published in AUTHOR_CONTROLLED_FIELDS, (
        f"ADR-0030 decision 3 calls {table_row!r} author-controlled and this surface "
        f"serves it as {published!r}, which is not in AUTHOR_CONTROLLED_FIELDS -- so "
        f"nothing here says it needs the SEC-15 triple"
    )
    assert published not in PROVIDER_CONTROLLED_FIELDS, (
        f"{published!r} is classified on both sides of decision 3's trust boundary"
    )


@pytest.mark.asyncio
async def test_a_served_record_publishes_exactly_the_classified_fields(
    served: ProjectRegistry,
) -> None:
    """A key added to the shaper without a classification reddens here.

    The population is the union of the two classification constants and the
    imported triple, compared against a real response's own keys. Both directions
    fail: an unclassified new field, and a classified field the shaper stopped
    sending. Without this, ``AUTHOR_CONTROLLED_FIELDS`` would be a comment.
    """
    result = await _call(served)

    classified = AUTHOR_CONTROLLED_FIELDS | PROVIDER_CONTROLLED_FIELDS | set(SAFETY)
    for record in result["records"]:
        assert set(record) == classified, (
            f"a served record's keys are {sorted(record)} and the classified "
            f"population is {sorted(classified)}. Every published field sits on one "
            f"side of ADR-0030 decision 3's trust boundary; a new one is classified "
            f"in `mcp/review_search.py` before it can be served."
        )


@pytest.mark.asyncio
async def test_each_author_controlled_field_really_carries_author_text(
    served: ProjectRegistry,
) -> None:
    """The classification is not vacuous: each labelled field carries a real value.

    A shaper that served ``None`` for every author-controlled field would satisfy
    the key-set test above while publishing nothing the triple protects. The
    corpus plants a distinctive value for each, and each is asserted to arrive.
    """
    result = await _call(served)
    by_kind = {record["kind"]: record for record in result["records"]}

    assert by_kind["review-thread"]["authorDisplayName"] == AUTHOR_NAME
    assert by_kind["review-thread"]["filePath"] == AUTHOR_FILE
    assert by_kind["review-thread"]["excerpt"] == OPENING_COMMENT
    assert by_kind["review-thread"]["excerptChannel"] == "comment"
    assert by_kind["pull-request"]["excerpt"] == "Bound the retry budget"
    assert by_kind["pull-request"]["excerptChannel"] == "title"
    assert by_kind["pull-request"]["filePath"] is None, (
        "a pull request has no anchor file, and the key is still present -- a field "
        "that appears only when set cannot be told apart from a server that predates it"
    )


@pytest.mark.asyncio
async def test_the_file_path_is_served_as_data_and_reaches_no_filesystem_call(
    served: ProjectRegistry,
) -> None:
    """SEC-7, ADR-0030 decision 6: an author-named path is data, never a path.

    The corpus's path is served verbatim, so a caller can read and filter on it;
    what must not happen is a read of it. Driven with a traversal-shaped path so
    the *value* is one a filesystem join would resolve somewhere real, and the
    response is asserted to carry it back unchanged with no file of that name
    created or consulted anywhere under the project root.
    """
    escaping = "../../../../etc/theurian-review-probe"
    records = (_thread("demo", "acme/pathy", number=7),)
    paths = ProjectPaths.of(_root(served, "demo"))
    # The record's own file path, replaced on the payload the store will project.
    planted = EvidenceRecord(
        provider=records[0].provider,
        repository=records[0].repository,
        anchor=records[0].anchor,
        payload=ReviewThread(
            external_id="PRRT_kwDOPATHY",
            project_id=ProjectId("demo"),
            event_key=f"{PROVIDER}:acme/pathy#7",
            file_path=escaping,
            comments=records[0].payload.comments,  # type: ignore[union-attr]
            state=ReviewThreadState.OPEN,
        ),
    )
    _land_and_build(served, "demo", (planted,))

    result = await _call(served, filePath=escaping)

    assert result["count"] == 1
    assert result["records"][0]["filePath"] == escaping
    assert not (paths.root / "etc").exists()
    assert not (paths.state / escaping.replace("../", "")).exists()


# -- SEC-13: one project's caller observes nothing of another's ----------------


@pytest.fixture
def two_projects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ProjectRegistry:
    """Two registered projects, each with its own review evidence and its own store.

    One daemon serving many projects is the design (ADR-0002). The property that
    makes it safe is that a call for one cannot observe the other -- and that only
    becomes testable once two really exist, each holding a value the other's corpus
    does not.
    """
    data_dir = tmp_path / "datadir"
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(data_dir))
    for name in ("demo", "beta"):
        _check_out(tmp_path / name, monkeypatch)
    registry = ProjectRegistry.default(data_dir)

    _land_and_build(
        registry,
        "demo",
        (
            _thread(
                "demo",
                "acme/order-service",
                number=42,
                opening="demo-only-payment-rotation",
            ),
        ),
    )
    _land_and_build(
        registry,
        "beta",
        (_thread("beta", "beta/tls", number=99, opening="beta-only-tls-pinning"),),
    )
    return registry


@pytest.mark.asyncio
async def test_a_review_search_for_one_project_cannot_observe_the_other(
    two_projects: ProjectRegistry,
) -> None:
    """SEC-13. The isolation is per store file, not a filter someone remembers.

    Four filters, because one would only prove the isolation for whichever
    predicate happened to be exercised: the text one, the repository one, the pull
    request one and the author one all resolve against ``demo``'s store alone.
    """
    own = await _call(two_projects, q="demo-only-payment-rotation")
    leaked = await _call(two_projects, q="beta-only-tls-pinning")
    other_repository = await _call(two_projects, repository="beta/tls")
    other_pull_request = await _call(two_projects, pullRequest=99)

    assert own["count"] == 1
    assert leaked["count"] == 0, "demo must not be able to read beta's review evidence"
    assert other_repository["count"] == 0
    assert other_pull_request["count"] == 0
    assert "beta-only-tls-pinning" not in json.dumps(own)


# -- Refusal envelopes: one constant, never an empty result --------------------


@pytest.mark.asyncio
async def test_a_project_with_no_review_search_store_is_refused_not_answered_empty(
    project: ProjectRegistry,
) -> None:
    """An absent store is a refusal naming the rebuild, never ``count: 0``.

    ``count: 0`` means "the filter matched nothing". Answering it for "nothing has
    been built here" is a false absence a caller acts on.
    """
    message = await _refusal(project)

    assert REVIEW_SEARCH_UNAVAILABLE_REFUSAL in message
    assert "theurian review build" in message
    assert "Traceback" not in message


@pytest.mark.asyncio
async def test_a_store_this_installation_did_not_build_is_refused_in_the_words_a_missing_one_gets(
    project: ProjectRegistry,
) -> None:
    """ADR-0004, SEC-7, T-19: provenance before presence, and one message for both.

    The hostile-clone shape: a well-formed, current, fully readable store that this
    installation never built, which a repository contributor produces with ``git
    add -f``. It is refused **byte-identically** to the absent case, so the victim
    cannot tell which of the two states they are in and whoever planted it learns
    nothing about whether the plant was detected.
    """
    _land_and_build(project, "demo", _corpus("demo", "acme/order-service"), record_provenance=False)

    planted = await _refusal(project)
    # The same project with the store removed altogether: the differential.
    ProjectPaths.of(_root(project, "demo")).review_search_for(REVIEW_SEARCH_STORE_ID).unlink()
    absent = await _refusal(project)

    assert planted == absent, (
        "a planted store and an absent one produce different refusals, so the "
        "message tells a victim which of the two states they are in"
    )
    assert REVIEW_SEARCH_UNAVAILABLE_REFUSAL in planted


@pytest.mark.asyncio
async def test_a_damaged_store_is_refused_with_the_same_constant(
    served: ProjectRegistry,
) -> None:
    """A store that cannot be read answers the one constant, not the adapter's text.

    The adapter's own message names the file and the failure and varies with the
    store's state, which is the "an error that fires for one input and not another"
    channel SEC-13 closes. The tool converts it to the constant instead.
    """
    store_path = ProjectPaths.of(_root(served, "demo")).review_search_for(REVIEW_SEARCH_STORE_ID)
    store_path.write_bytes(b"not a database, not even close")

    message = await _refusal(served)

    assert REVIEW_SEARCH_UNAVAILABLE_REFUSAL in message
    assert store_path.name not in message, (
        "the refusal names the store file, so it varies with what is on disk"
    )


@pytest.mark.asyncio
async def test_a_project_path_that_stops_resolving_does_not_publish_the_operator_layout(
    served: ProjectRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one arm the shipped composition cannot reach, driven rather than assumed.

    ``review_search_for``'s refusal names the resolved absolute ``.theurian/state``
    directory -- correct on a terminal, the operator's machine layout on this
    surface (GHSA-97q9). The tool converts it to the constant, and this is what
    drives that conversion: a guard no input reaches is a guard that survives its
    own deletion.
    """
    from theurian.application.project_service import ProjectError

    def refuse(self: ProjectPaths, store_id: str) -> Path:
        raise ProjectError(
            f"The review search store id {store_id!r} resolves outside "
            f"/Users/someone/secret-layout/.theurian/state.",
            remedy="Remove the file this names.",
        )

    monkeypatch.setattr(ProjectPaths, "review_search_for", refuse)

    message = await _refusal(served)

    assert REVIEW_SEARCH_UNAVAILABLE_REFUSAL in message
    assert "secret-layout" not in message


# -- Bound input at the tool -> store seam -------------------------------------


@pytest.mark.parametrize(
    "needle",
    [
        pytest.param("100%", id="like-percent"),
        pytest.param("100_", id="like-underscore"),
        pytest.param("budget OR nonsense", id="fts-or"),
        pytest.param('"quoted phrase"', id="fts-phrase"),
        pytest.param("retr*", id="fts-prefix"),
        pytest.param("NEAR(retry budget)", id="fts-near"),
        pytest.param(".*", id="regex-any"),
        pytest.param("a\\b", id="backslash"),
    ],
)
@pytest.mark.asyncio
async def test_operator_syntax_in_the_query_arrives_at_the_store_as_text(
    project: ProjectRegistry, needle: str
) -> None:
    """The seam above the store: a caller's ``q`` is characters, not a language.

    Each needle is planted verbatim inside a comment and then searched for. If any
    of them were interpreted -- as a LIKE wildcard, as an FTS operator, as a
    regular expression -- the match would be against something other than the text
    the caller sent, and the two arms below would disagree:

    * the record carrying the needle is found, so it is not being over-escaped
      into matching nothing;
    * the record that does **not** carry it is not found, so it is not being
      interpreted into matching everything.

    ``test_review_search_bound_input.py`` holds the same claim at the store; this
    holds it through ``build_query`` and the tool, which is where a surface could
    reintroduce a pattern language by pre-processing the argument.
    """
    _land_and_build(
        project,
        "demo",
        (
            _thread("demo", "acme/carrier", number=1, opening=f"before {needle} after"),
            _event(
                "demo",
                "acme/other",
                number=2,
                title="an unrelated title",
                body="an unrelated body",
            ),
        ),
    )

    result = await _call(project, q=needle)

    assert result["count"] == 1, (
        f"searching for {needle!r} matched {result['count']} records; it must match "
        f"exactly the one that carries it literally"
    )
    assert result["records"][0]["repository"] == "acme/carrier"


@pytest.mark.asyncio
async def test_a_needle_no_record_carries_matches_nothing_rather_than_everything(
    project: ProjectRegistry,
) -> None:
    """The positive control for the arms above: an empty answer is reachable.

    Without it, "matched exactly one" could be satisfied by a corpus where every
    query matches the same single record.
    """
    _land_and_build(project, "demo", _corpus("demo", "acme/order-service"))

    result = await _call(project, q="%")

    assert result["count"] == 0, (
        "`%` matched records, so it is reaching the store as a LIKE wildcard rather "
        "than as a percent sign"
    )


# -- Bounds, refusals and the page boundary ------------------------------------


@pytest.mark.asyncio
async def test_an_over_long_filter_is_reported_by_its_length_and_never_echoed(
    served: ProjectRegistry,
) -> None:
    """#17: a refusal must not become a reflector of the caller's own bytes."""
    oversized = "z" * (MAX_FILTER_CHARS + 1)

    message = await _refusal(served, q=oversized)

    assert str(MAX_FILTER_CHARS + 1) in message
    assert str(MAX_FILTER_CHARS) in message
    assert oversized not in message


@pytest.mark.parametrize(
    "arguments",
    [
        pytest.param({"limit": 0}, id="limit-zero"),
        pytest.param({"limit": MAX_REVIEW_SEARCH_LIMIT + 1}, id="limit-past-cap"),
        pytest.param({"limit": 10**500}, id="limit-absurd"),
        pytest.param({"pullRequest": 0}, id="pull-request-zero"),
        pytest.param({"pullRequest": MAX_PULL_REQUEST + 1}, id="pull-request-past-column"),
        pytest.param({"pullRequest": 2**900}, id="pull-request-absurd"),
        pytest.param({"threadState": "RESOLVED"}, id="thread-state-wrong-case"),
        pytest.param({"threadState": "approved"}, id="thread-state-other-vocabulary"),
        pytest.param({"repository": ""}, id="empty-filter"),
        pytest.param({"q": "with\x00nul"}, id="nul"),
        pytest.param({"q": "\ud800"}, id="lone-surrogate"),
    ],
)
@pytest.mark.asyncio
async def test_a_bad_argument_is_a_graded_refusal_that_can_itself_be_built(
    served: ProjectRegistry, arguments: dict[str, Any]
) -> None:
    """Every refusal path is total: none of them raises while being constructed.

    The two absurd integers are the shape that made the sibling surface's refusal
    crash inside its own f-string, past CPython's ``sys.get_int_max_str_digits()``
    -- **a refusal path that can crash is not a refusal**. This surface answers by
    never rendering a caller's number at all, so what is asserted here is that a
    graded message arrives; the sibling test below asserts the non-rendering.

    The lone surrogate is the other total-refusal shape: a message echoing it
    cannot be serialised, and the client would receive a 200 with an empty body.
    """
    message = await _refusal(served, **arguments)

    assert "Nothing was searched." in message
    assert "Traceback" not in message


@pytest.mark.asyncio
async def test_a_bad_filter_is_refused_the_same_way_whether_or_not_the_project_resolves(
    served: ProjectRegistry,
) -> None:
    """The ordering claim in ``review_search``'s body, driven rather than read.

    The tool checks its bounds **before** it reads the registry, and the comment
    there states two things that follow. A refused request costs the daemon
    nothing, because nothing has been opened yet (T-6); and the refusal a caller
    gets for a bad token is *independent of whether the project resolves*, which
    takes one input away from the error surface (SEC-13). A caller that could tell
    "this project exists and your limit is wrong" from "your limit is wrong" would
    have a project-existence oracle in an argument-validation message.

    Reordered -- ``_resolve`` first, bounds second -- the two arms below separate:
    the unknown project would answer with the registry's own refusal while the
    known one answered with the bound's. So the equality is the assertion, and the
    third arm is what keeps it from holding because both arms refuse identically
    for some *other* reason: the unknown project with a **valid** filter must
    produce a different message, or this surface is answering one constant to
    everything.
    """
    unknown = "no-such-project-ever-registered"

    resolvable = await _refusal(served, limit=0)
    unresolvable = await _refusal(served, projectId=unknown, limit=0)
    resolution_failure = await _refusal(served, projectId=unknown)

    assert "limit" in resolvable and "Nothing was searched." in resolvable
    assert unresolvable == resolvable, (
        "a bad `limit` is refused differently depending on whether the project resolves, so "
        "the bound is being checked after the registry read -- which both costs the daemon a "
        "read for a request it was going to refuse and hands a caller a project-existence "
        "oracle inside an argument-validation message"
    )
    assert resolution_failure != resolvable, (
        "an unresolvable project with a valid filter must not answer the bound's own refusal, "
        "or the equality above holds because this surface refuses everything with one string"
    )
    assert unknown not in resolvable, "and the refusal must not echo the caller's project id"


@pytest.mark.parametrize(
    "argument, values",
    [
        pytest.param("limit", (0, -(10**500), MAX_REVIEW_SEARCH_LIMIT + 1, 10**500), id="limit"),
        pytest.param(
            "pullRequest", (0, -(10**500), MAX_PULL_REQUEST + 1, 2**900), id="pullRequest"
        ),
        pytest.param("repository", ("", "z" * (MAX_FILTER_CHARS + 1)), id="repository"),
    ],
)
@pytest.mark.asyncio
async def test_an_out_of_range_argument_refuses_without_rendering_the_value(
    served: ProjectRegistry, argument: str, values: tuple[Any, ...]
) -> None:
    """The refusal for a number is a **constant**, whatever the number was.

    Two properties in one comparison, and the equality is what makes both
    checkable. The refusal cannot be *rendering* the caller's value, because four
    different values produce one string -- which is how this surface closes the
    class that crashed the sibling's refusal inside its own f-string. And it
    cannot be a channel: a message that varied with the argument would be one more
    input to the error surface SEC-13 keeps at one message per cause.

    ``repository`` is here as the deliberate **contrast**, not as a fourth number:
    a string filter *is* quoted back inside the bound, so its two out-of-range
    values must produce two different messages -- which is what says the equality
    above is a property of the integer arms rather than of every refusal here.
    """
    messages = [await _refusal(served, **{argument: value}) for value in values]

    if argument == "repository":
        assert len(set(messages)) == len(messages), (
            "two different out-of-range string filters produced one message, so the "
            "refusal has stopped distinguishing an empty filter from an over-long one"
        )
        return
    assert len(set(messages)) == 1, (
        f"`{argument}` produced {len(set(messages))} different refusals for "
        f"{len(values)} out-of-range values, so the message carries something of the "
        f"caller's own number: {sorted(set(messages))}"
    )


@pytest.mark.asyncio
async def test_the_page_boundary_is_one_bit_and_not_a_count(served: ProjectRegistry) -> None:
    """``truncated`` says the page ended early; it never says how much is behind it.

    The corpus holds three records. A page of two ends early and a page of three
    does not, and neither response carries a number over the records the caller did
    not receive.
    """
    short = await _call(served, limit=2)
    whole = await _call(served, limit=3)

    assert (short["count"], short["truncated"]) == (2, True)
    assert (whole["count"], whole["truncated"]) == (3, False)
    assert set(short) == set(whole) == {"count", "truncated", "records"}


@pytest.mark.asyncio
async def test_a_long_comment_is_cut_by_the_read_and_marked_on_the_wire(
    project: ProjectRegistry,
) -> None:
    """The one clamp on this surface, and the arithmetic that makes a cut visible.

    An authored excerpt is at most :data:`MAX_EXCERPT_CHARS` characters; a cut one
    is exactly three more. The needle sits past the bound deliberately: the match
    is made against the whole stored fragment, so a record whose only match lies in
    the tail is still served rather than reading as absent.
    """
    tail_needle = "sentinel-past-the-bound"
    planted = "p" * (MAX_EXCERPT_CHARS * 4) + tail_needle
    _land_and_build(project, "demo", (_thread("demo", "acme/long", number=3, opening=planted),))

    result = await _call(project, q=tail_needle)
    excerpt = result["records"][0]["excerpt"]

    assert result["count"] == 1, "a match past the excerpt bound must still select its record"
    assert len(excerpt) == MAX_EXCERPT_CHARS + 3
    assert excerpt.endswith("...")
    assert tail_needle not in excerpt


@pytest.mark.asyncio
async def test_the_default_page_size_is_the_published_constant(served: ProjectRegistry) -> None:
    """The default is a value, not an accident: a caller that omits ``limit`` gets it.

    Asserted through the tool rather than by reading the signature, so a default
    changed in one place and documented in another is visible here.
    """
    from theurian.daemon.runner import build_server as _build

    tool = _build(served)._tool_manager.get_tool("review.search")
    assert tool is not None
    published = tool.parameters["properties"]["limit"]

    assert published["default"] == DEFAULT_REVIEW_SEARCH_LIMIT
    assert DEFAULT_REVIEW_SEARCH_LIMIT <= MAX_REVIEW_SEARCH_LIMIT


# -- The shaper, without a server ---------------------------------------------


def test_the_shaper_copies_a_hit_and_computes_nothing_across_rows() -> None:
    """Every published value is this record's own column, bounded and unmodified.

    Called on a hit built here rather than on one a store returned, so the claim is
    about the shaper alone: a field computed from anything but its own row would
    have to come from somewhere this function cannot see.
    """
    hit = _hit()

    row = review_record(hit)
    frozen = copy.deepcopy(row)

    assert row["recordPath"] == hit.relative_path
    assert row["filePath"] == hit.file_path
    assert row["excerpt"] == hit.excerpt
    assert row["lastSeenAt"] == hit.last_seen_at
    assert review_record(hit) == frozen, "the shaper is not a pure function of its hit"
