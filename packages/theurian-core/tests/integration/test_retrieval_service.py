"""Hybrid retrieval end to end (FR-R1..R4, FR-R7).

A real canonical store, a real index file, and the real default embedder. The
only thing not exercised here is the MCP transport.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from collections.abc import Iterator, Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, final

import pytest
from fakes import truncating, whole
from typer.testing import CliRunner

from theurian.application.index_builder import IndexBuilder, IndexRequest
from theurian.application.project_service import ProjectPaths, read_active_state
from theurian.application.retrieval_service import (
    CandidateSource,
    ResultGate,
    ResultRequest,
    RetrievalError,
    RetrievalService,
    SearchOutcome,
    SearchRequest,
    Surfaced,
    within_budget,
)
from theurian.application.visibility import Visibility
from theurian.cli.main import app
from theurian.domain.chunking import IndexableChunk
from theurian.domain.ports.embedding import EmbeddingProvider
from theurian.domain.ranking import DENSE, Ranked, RetrievalMode, RetrieverPage, mode_of
from theurian.infrastructure.embedding import HashingEmbedding
from theurian.infrastructure.sqlite.index_store import SqliteIndexStore
from theurian.infrastructure.sqlite.store import SqliteCanonicalStore

pytestmark = pytest.mark.integration

#: A cell holding nothing this codebase says elsewhere, so a fragment of it in a
#: published message came out of the state database file and nowhere else.
SENTINEL = "ROTATE-ME sk-live-9f2a7c41d8e3 payroll band L7 = 240000"


def _mode(outcome: SearchOutcome) -> RetrievalMode:
    """The mode the tool would report for this outcome.

    `RetrievalService` deliberately no longer computes it. The mode describes
    the results a caller receives, and results only exist once the canonical
    store has said which candidates may be shown at all (SEC-13, FR-R5).
    """
    return mode_of(name for candidate in outcome.candidates for name in candidate.found_by)


class _NothingWithheld:
    """A `Visibility` that withholds nothing, for the pure ranking tests.

    Written out at every call site rather than defaulted in production, and that
    is the point: "everything is visible" is exactly the assumption that let a
    withheld document take a candidate slot, so a test that depends on it has to
    say so. A default parameter would let the next caller inherit it in silence.
    """

    def cleared(self, ranked: Sequence[Ranked]) -> tuple[Ranked, ...]:
        return tuple(ranked)


NOTHING_WITHHELD = _NothingWithheld()


def _candidates_from(service: RetrievalService, request: SearchRequest) -> CandidateSource:
    """What a composition root hands `ResultGate.admit`: retrieval, not results."""

    def source(visible: Visibility) -> SearchOutcome:
        return service.search(request, visible)

    return source


runner = CliRunner()

AUTH_BODY = """# Authentication policy

Every inbound call carries a signed JWT. The gateway verifies the signature
before any handler runs, and rejects an unsigned request with 401.

## Token rotation

Rotating a credential invalidates the previous one immediately.
"""

CACHE_BODY = """# Caching policy

Read-through cache with a two-minute TTL. Stale entries are evicted lazily
rather than on a timer, because a timer wakes the process for nothing.
"""

#: A document long enough to be split into four chunks, every one of which
#: contains the word `policy`.
#:
#: The per-item cap cannot be observed against a corpus of short documents. Both
#: other bodies here yield exactly one chunk each, so `len(items) ==
#: len(set(items))` was true whether or not `diversify` ran -- which is how the
#: cap's only test survived the cap being deleted. Measured: this body produces
#: four chunks at the default target size, and uncapped fusion returns all four.
PLAYBOOK_BODY = (
    "# Operations playbook\n\nThis playbook records the policy the platform team follows.\n\n"
) + "".join(
    f"## {section}\n\nThe {section.lower()} policy is reviewed each quarter by the platform "
    f"team. It states what the policy requires, who owns the policy, and when the policy is "
    f"next revisited. Nothing here changes without a migration.\n\n"
    for section in ("Escalation", "Retention", "Exceptions", "Review cadence")
)


def _migration(index: int, item: str, filename: str, title: str) -> str:
    letter = chr(ord("A") + index)
    return f"""apiVersion: theurian.dev/v1
id: 01K1{letter}AAAAA01234567890ABCDE
createdAt: 2026-08-03T10:0{index}:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: {item}
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: {item}
    revisionId: 01K1{letter}REVAA01234567890ABCDE
    contentFile: ../knowledge/architecture/{filename}
    metadata:
      title: {title}
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/{filename}
"""


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    root = tmp_path / "demo"
    root.mkdir()
    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "t@example.com"],
        ["git", "config", "user.name", "T"],
    ):
        subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603

    monkeypatch.setenv("THEURIAN_DATA_DIR", str(tmp_path / "datadir"))
    monkeypatch.chdir(root)

    assert runner.invoke(app, ["init", "--json"]).exit_code == 0
    knowledge = root / ".theurian/knowledge/architecture"
    (knowledge / "auth.md").write_text(AUTH_BODY)
    (knowledge / "cache.md").write_text(CACHE_BODY)
    (root / ".theurian/migrations/01K1AAAAAA01234567890ABCDE-auth.yaml").write_text(
        _migration(0, "architecture.auth", "auth.md", "Authentication policy")
    )
    (root / ".theurian/migrations/01K1BAAAAA01234567890ABCDE-cache.yaml").write_text(
        _migration(1, "architecture.cache", "cache.md", "Caching policy")
    )
    assert runner.invoke(app, ["project", "register", "--json"]).exit_code == 0
    assert runner.invoke(app, ["migrate", "apply", "--json"]).exit_code == 0
    yield root


@pytest.fixture
def with_a_long_document(project: Path) -> Path:
    """`project`, plus one document that yields four chunks matching `policy`.

    Separate from `project` on purpose: the queries in the tests above are tuned
    against a two-document corpus, and a third document that talks about tokens
    and caches would change which item ranks first for them.
    """
    (project / ".theurian/knowledge/architecture/playbook.md").write_text(PLAYBOOK_BODY)
    (project / ".theurian/migrations/01K1CAAAAA01234567890ABCDE-playbook.yaml").write_text(
        _migration(2, "architecture.playbook", "playbook.md", "Operations playbook")
    )
    assert runner.invoke(app, ["migrate", "apply", "--json"]).exit_code == 0
    return project


def _database(project: Path) -> Path:
    """The canonical state the *pointer* names, resolved the way production does.

    A state database's filename is a content address, not a version, and applying
    a migration writes a new one beside the old rather than replacing it — that is
    what makes switching branches O(1) (ADR-0016). So any fixture that applies a
    second migration leaves two, and enumerating the directory picks between them
    by whatever order the filesystem hands back.

    That is not a style point. This helper is the oracle for every test here that
    retires an item and then asserts it is gone: reading the *stale* state gets an
    `architecture.cache` that is still `approved`, an index that indexes it, and
    a gate that admits it. macOS returned the fresh database and the suite was
    green; Linux returned the stale one and five tests failed, three of them
    SEC-13 assertions about retired knowledge never reaching a caller.

    The macOS pass was a property of these filenames, not of the platform: a
    standalone script running the same steps with shorter bodies — different
    content hashes, so different names — got the superseded database first on
    macOS too. Neither order is a guarantee anywhere.

    `active.json` is the only authority on which state is current, and
    `paths.state / active.database_filename` is exactly what `mcp/tools.py` and
    `cli/index_commands.py` resolve before opening the canonical store.
    """
    paths = ProjectPaths.of(project)
    active = read_active_state(paths)
    assert active is not None, "the fixture must have published a canonical state"
    return paths.state / active.database_filename


def _build(project: Path, *, embedder: EmbeddingProvider | None) -> Path:
    index_path = project / ".theurian/state/theurian-index-01K1DXAAAA.sqlite"
    builder = IndexBuilder(
        store_factory=SqliteCanonicalStore,
        index_factory=SqliteIndexStore,
        embedder=embedder,
    )
    builder.build(
        IndexRequest(
            database=_database(project),
            index_path=index_path,
            project_id="demo",
            state_hash="test-state",
            index_build_id="01K1DXAAAA01234567890ABCDE",
        )
    )
    return index_path


def _service(index_path: Path, embedder: EmbeddingProvider | None) -> RetrievalService:
    return RetrievalService(SqliteIndexStore(index_path), embedder)


# -- Building ----------------------------------------------------------------


def test_the_index_is_a_separate_file_from_the_canonical_store(project: Path) -> None:
    """ADR-0004. Deleting it must be a cache miss, never data loss."""
    index_path = _build(project, embedder=HashingEmbedding())

    assert index_path.is_file()
    assert index_path != _database(project)
    assert _database(project).is_file(), "the canonical store is untouched"


def test_every_approved_revision_is_chunked_and_indexed(project: Path) -> None:
    index_path = _build(project, embedder=None)

    assert SqliteIndexStore(index_path).chunk_count() >= 2


def test_the_index_records_the_state_it_was_built_from(project: Path) -> None:
    index_path = _build(project, embedder=HashingEmbedding())
    metadata = SqliteIndexStore(index_path).metadata()

    assert metadata["state_hash"] == "test-state"
    assert metadata["embedding_model"] == HashingEmbedding().model_id


# -- Searching ---------------------------------------------------------------


def test_a_query_finds_the_document_that_answers_it(project: Path) -> None:
    embedder = HashingEmbedding()
    service = _service(_build(project, embedder=embedder), embedder)

    outcome = service.search(SearchRequest(query="signed JWT", project_id="demo"), NOTHING_WITHHELD)

    assert outcome.candidates
    assert outcome.candidates[0].item_id == "architecture.auth"


def test_a_title_only_match_still_finds_the_document(project: Path) -> None:
    """The title is prepended before chunking, so a query naming only the
    document still reaches it without a second retriever and a fusion weight."""
    service = _service(_build(project, embedder=None), None)

    outcome = service.search(
        SearchRequest(query="Caching policy", project_id="demo"), NOTHING_WITHHELD
    )

    assert outcome.candidates[0].item_id == "architecture.cache"


def test_dense_is_off_by_default(project: Path) -> None:
    """Measured, not cautious: against a real corpus 91% of *unrelated*
    natural-language questions clear the similarity floor, while the lowest
    genuinely related query sits below the unrelated median. The bundled
    embedder measures English surface-form overlap, not topical relevance.
    """
    embedder = HashingEmbedding()
    service = _service(_build(project, embedder=embedder), embedder)

    outcome = service.search(
        SearchRequest(query="token rotation", project_id="demo"), NOTHING_WITHHELD
    )

    assert _mode(outcome) is RetrievalMode.LEXICAL
    assert service.embedding_model(use_dense=False) == ""


def test_dense_participates_when_asked_for(project: Path) -> None:
    """Kept and made opt-in rather than deleted, so the path stays exercised for
    the day a real model is configured through the same port."""
    embedder = HashingEmbedding()
    service = _service(_build(project, embedder=embedder), embedder)

    outcome = service.search(
        SearchRequest(query="signed JWT", project_id="demo", use_dense=True), NOTHING_WITHHELD
    )

    assert _mode(outcome) is RetrievalMode.HYBRID
    assert service.embedding_model(use_dense=True) == embedder.model_id


def test_an_index_without_embeddings_degrades_visibly_to_lexical(project: Path) -> None:
    """The failure this reporting exists for: a search that quietly lost its
    dense half must not look identical to a healthy one.

    The metadata assertion is the one this fixture decides. `embedding_model` is
    checked at `use_dense=False`, which returns on the function's first line
    before the index is consulted at all -- so it holds for a fixture built with
    an embedder just as well, and stood here for a while looking as though it
    said something about this one. What the fixture actually determines is the
    row the build wrote: an index built without an embedder must not claim one,
    because that empty string is what every later comparison against a
    configured model is made against.
    """
    index_path = _build(project, embedder=None)
    service = _service(index_path, HashingEmbedding())

    outcome = service.search(SearchRequest(query="signed JWT", project_id="demo"), NOTHING_WITHHELD)

    assert SqliteIndexStore(index_path).metadata()["embedding_model"] == ""
    assert _mode(outcome) is RetrievalMode.LEXICAL
    assert outcome.candidates, "lexical search still works"


@final
class _AnotherModel:
    """`HashingEmbedding`'s vectors, declared under a different ``model_id``.

    Deliberately the *same* arithmetic. A second embedder that produced different
    vectors would let a test pass because the query happened to score badly,
    which is not the property: the refusal is a policy about declared identity,
    not a quality threshold. With the vectors identical, dense retrieval would
    work perfectly if the refusal were removed -- so anything the test observes
    is the refusal and nothing else.

    ADR-0009 anticipates a second model shipping, which is when this branch stops
    being hypothetical.
    """

    model_id = "another-model-v1"
    model_revision = "1"
    dimension = HashingEmbedding.dimension

    def __init__(self) -> None:
        self._inner = HashingEmbedding()

    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return await self._inner.embed(texts)


def test_an_index_embedded_by_another_model_names_no_model(project: Path) -> None:
    """FR-R7. What a caller is told about a search whose dense half cannot run.

    One index, read twice: once through the embedder that built it and once
    through an embedder declaring a different ``model_id``. The second names no
    model, because scoring one model's query vector against another's document
    vectors is arithmetic without meaning -- comparable, and confidently wrong.

    Both halves are asserted. A function that returned ``""`` unconditionally
    satisfies the mismatch case on its own, and that is the shape this pair
    exists to rule out.
    """
    index_path = _build(project, embedder=HashingEmbedding())

    agreeing = _service(index_path, HashingEmbedding())
    disagreeing = _service(index_path, _AnotherModel())

    assert agreeing.embedding_model(use_dense=True) == HashingEmbedding.model_id
    assert disagreeing.embedding_model(use_dense=True) == ""


def test_an_index_embedded_by_another_model_is_not_scored_against_it(project: Path) -> None:
    """The refusal has to withhold the ranking, not just the label.

    "autentication" is a typo appearing nowhere in the corpus, so FTS5 and the
    trigram retriever both find nothing and any result at all came from the dense
    retriever. Under the matching embedder it answers; under the mismatched one
    the search must degrade to lexical and return nothing -- rather than return
    the same hit under a blank ``embeddingModel``, which is the failure a label
    without a behaviour would be.
    """
    index_path = _build(project, embedder=HashingEmbedding())

    agreeing = _service(index_path, HashingEmbedding()).search(
        SearchRequest(query="autentication", project_id="demo", use_dense=True), NOTHING_WITHHELD
    )
    disagreeing = _service(index_path, _AnotherModel()).search(
        SearchRequest(query="autentication", project_id="demo", use_dense=True), NOTHING_WITHHELD
    )

    assert agreeing.candidates, "the control: these vectors do bridge the typo"
    assert DENSE in agreeing.candidates[0].ranks
    assert disagreeing.candidates == ()


def test_a_model_mismatch_removes_only_the_dense_half(project: Path) -> None:
    """ADR-0004 again: a mismatched model is a lost optimisation, not a failure.

    Refusing the whole search would take away the lexical answer the index can
    still give perfectly well, over a disagreement about vectors the query did
    not need. The mode is what a caller reads to tell the two apart, so it is
    asserted rather than inferred from the results being non-empty.
    """
    index_path = _build(project, embedder=HashingEmbedding())

    outcome = _service(index_path, _AnotherModel()).search(
        SearchRequest(query="signed JWT", project_id="demo", use_dense=True), NOTHING_WITHHELD
    )

    assert outcome.candidates[0].item_id == "architecture.auth"
    assert _mode(outcome) is RetrievalMode.LEXICAL


def test_a_morphological_variant_is_found_only_with_the_dense_retriever(
    project: Path,
) -> None:
    """The reason the default embedder ships at all.

    The query has to be one FTS5 genuinely cannot answer. "credential rotating"
    is not: `unicode61` folds case, the body says "Rotating a credential", and
    the lexical retriever matched it on its own -- so the assertion held with
    the dense retriever switched off entirely. "autentication" is a typo that
    appears nowhere, which only character n-grams can bridge.
    """
    embedder = HashingEmbedding()
    index_path = _build(project, embedder=embedder)

    without = _service(index_path, embedder).search(
        SearchRequest(query="autentication", project_id="demo"), NOTHING_WITHHELD
    )
    with_dense = _service(index_path, embedder).search(
        SearchRequest(query="autentication", project_id="demo", use_dense=True), NOTHING_WITHHELD
    )

    assert without.candidates == (), "FTS5 matches terms exactly and finds nothing"
    assert with_dense.candidates, "n-grams bridge the morphological gap"
    assert with_dense.candidates[0].item_id == "architecture.auth"
    assert DENSE in with_dense.candidates[0].ranks, "the dense retriever is what surfaced it"
    assert _mode(with_dense) is RetrievalMode.DENSE


def test_another_project_is_never_returned(project: Path) -> None:
    """SEC-13, FR-R1: filtered before ranking, not after."""
    service = _service(_build(project, embedder=None), None)

    outcome = service.search(
        SearchRequest(query="signed JWT", project_id="somebody-else"), NOTHING_WITHHELD
    )

    assert outcome.candidates == ()


def test_a_long_document_cannot_take_every_slot(with_a_long_document: Path) -> None:
    """FR-R4. The cap is what buys back the ability to see a second opinion.

    A long document wins every lexical rank simply by containing the query terms
    many times. Here `architecture.playbook` is four chunks, all matching
    `policy`; without the cap it takes four of the caller's result slots, four
    shares of their budget, and pushes the two documents that answer the question
    from a different angle further down.

    The previous version of this test asserted the same property against a
    corpus in which every document produced exactly *one* chunk, so
    `len(items) == len(set(items))` was true whether or not `diversify` ran.
    Replacing the call with `tuple(fused)` left it green while one document took
    six slots.
    """
    service = _service(_build(with_a_long_document, embedder=None), None)

    outcome = service.search(
        SearchRequest(query="policy", project_id="demo", per_item=1), NOTHING_WITHHELD
    )

    items = [candidate.item_id for candidate in outcome.candidates]
    assert items.count("architecture.playbook") == 1, "one chunk of the long document, not four"
    assert len(items) == len(set(items))


def test_the_cap_is_the_only_reason_the_long_document_is_held_back(
    with_a_long_document: Path,
) -> None:
    """The control the test above is read against.

    Without it, "the playbook appears once" is satisfied by a corpus in which it
    could only ever have appeared once — which is exactly the state the previous
    fixture was in. Raising the cap must let the same query return the same
    document repeatedly, or the cap is not what is doing the work.
    """
    service = _service(_build(with_a_long_document, embedder=None), None)

    outcome = service.search(
        SearchRequest(query="policy", project_id="demo", per_item=4), NOTHING_WITHHELD
    )

    items = [candidate.item_id for candidate in outcome.candidates]
    assert items.count("architecture.playbook") > 1, "the fixture must be able to violate the cap"


def test_two_identical_searches_return_the_same_order(project: Path) -> None:
    """FR-R7. Without a total order, a pinned snapshot reproduces nothing."""
    embedder = HashingEmbedding()
    service = _service(_build(project, embedder=embedder), embedder)
    request = SearchRequest(query="policy token cache", project_id="demo")

    first = [c.chunk_id for c in service.search(request, NOTHING_WITHHELD).candidates]
    second = [c.chunk_id for c in service.search(request, NOTHING_WITHHELD).candidates]

    assert first == second


def test_a_query_embedded_by_a_different_model_refuses_to_score(project: Path) -> None:
    """Vectors from two models are comparable arithmetically and meaningless
    semantically. The search degrades to lexical rather than returning
    confident nonsense."""

    class OtherModel:
        """A second provider, not a subclass: HashingEmbedding is final, and a
        different model is a different implementation of the port anyway."""

        model_id = "some-other-model"
        model_revision = "1"
        dimension = HashingEmbedding.dimension

        async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
            return await HashingEmbedding().embed(texts)

    index_path = _build(project, embedder=HashingEmbedding())
    outcome = _service(index_path, OtherModel()).search(
        SearchRequest(query="signed JWT", project_id="demo"), NOTHING_WITHHELD
    )

    assert _mode(outcome) is RetrievalMode.LEXICAL
    assert outcome.candidates, "the lexical half still answers"


def test_the_index_and_the_canonical_store_are_distinguishable_by_name(
    project: Path,
) -> None:
    """They share a directory. A glob that could not tell them apart would hand
    a retrieval index to the canonical store -- which is exactly the mistake
    this test's own helper made first."""
    index_path = _build(project, embedder=None)

    assert index_path.name.startswith("theurian-index-")
    assert _database(project).name.startswith("theurian-state-")


# -- The CLI -----------------------------------------------------------------


def _invoke(*args: str) -> tuple[int, dict[str, Any]]:
    result = runner.invoke(app, [*args, "--json"], catch_exceptions=False)
    stream = result.stdout or result.stderr or ""
    return result.exit_code, json.loads(stream) if stream.strip() else {}


def test_index_build_writes_and_publishes_an_index(project: Path) -> None:
    code, payload = _invoke("index", "build")

    assert code == 0
    assert payload["chunks"] >= 2
    assert payload["published"] is True
    assert Path(str(payload["indexPath"])).is_file()


def test_a_fresh_index_reports_itself_fresh(project: Path) -> None:
    _invoke("index", "build")

    _, payload = _invoke("index", "status")

    assert payload["built"] is True
    assert payload["stale"] is False
    assert payload["remedy"] == ""


def test_changing_knowledge_makes_the_index_stale(project: Path) -> None:
    """A stale index is a correctness problem wearing the costume of a
    relevance problem: searches keep working and answer from knowledge that has
    changed."""
    _invoke("index", "build")

    (project / ".theurian/knowledge/architecture/auth.md").write_text(
        AUTH_BODY + "\n## Rate limiting\n\nOne hundred requests per minute.\n"
    )
    _, payload = _invoke("index", "status")

    assert payload["stale"] is True


def test_the_remedy_names_the_commands_in_the_order_they_must_run(project: Path) -> None:
    """Indexing before applying would build from a database that is itself
    behind, producing a fresh-looking index of stale knowledge."""
    _invoke("index", "build")
    (project / ".theurian/knowledge/architecture/auth.md").write_text(AUTH_BODY + "\nmore\n")

    _, payload = _invoke("index", "status")

    assert payload["knowledgeNotApplied"] is True
    assert payload["remedy"].index("migrate apply") < payload["remedy"].index("index build")


def test_all_three_hashes_are_reported(project: Path) -> None:
    """Comparing only the index against the database would call an index fresh
    whenever the database was equally out of date -- exactly when someone most
    needs to be told otherwise."""
    _invoke("index", "build")

    _, payload = _invoke("index", "status")

    assert payload["indexStateHash"]
    assert payload["builtStateHash"]
    assert payload["currentStateHash"]


def test_indexing_without_a_built_state_says_what_to_run_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "bare"
    root.mkdir()
    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "t@example.com"],
        ["git", "config", "user.name", "T"],
    ):
        subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(tmp_path / "datadir"))
    monkeypatch.chdir(root)
    runner.invoke(app, ["init", "--json"])

    code, payload = _invoke("index", "build")

    assert code != 0
    assert "migrate apply" in payload["remedy"]


def test_lexical_only_builds_are_supported(project: Path) -> None:
    """A machine that cannot or should not embed still gets search."""
    code, payload = _invoke("index", "build", "--no-embeddings")

    assert code == 0
    assert payload["embeddings"] == 0
    assert payload["embeddingModel"] == ""


# -- `index build` refuses rather than publishing something wrong -------------


@pytest.fixture
def renamed_without_rebuilding_state(project: Path) -> Path:
    """The project re-registered under a new id, with its state left alone.

    The exact half-finished rename the registry's refusal warns about: canonical
    rows are stamped with the id in force at `migrate apply` and `migrate apply`
    is idempotent, so nothing restamps them. The new id therefore addresses a
    project with no knowledge in it.
    """
    assert runner.invoke(app, ["project", "unregister", "demo", "--json"]).exit_code == 0
    assert (
        runner.invoke(
            app, ["project", "register", "--project-id", "demo-renamed", "--json"]
        ).exit_code
        == 0
    )
    return project


def test_a_build_that_would_publish_no_chunks_over_real_knowledge_is_refused(
    renamed_without_rebuilding_state: Path,
) -> None:
    """Publishing it would put a *correct-looking* empty index in place.

    Every later search then answers `count: 0` with `indexed: true`, and
    `theurian index status` reports nothing to do — the shape a project-id
    mismatch takes, and indistinguishable from "this team has made no such
    decision". The refusal is what turns it from silent into a message.
    """
    code, payload = _invoke("index", "build")

    assert code != 0
    assert "no chunks" in payload["error"]
    assert "'demo': 2 items" in payload["error"], "the id the knowledge is actually under"
    assert "'demo-renamed'" in payload["error"], "and the id this build ran as"


def test_the_refused_build_names_the_state_rebuild_and_publishes_nothing(
    renamed_without_rebuilding_state: Path,
) -> None:
    """Re-applying does not fix this, so the remedy must not say to.

    `migrate apply` is idempotent per project and the revision rows it would
    need are already spoken for by the first id; nothing short of deleting the
    state database recovers. A half-written index file left behind would be
    worse still — it is a file a later `index status` would find and believe.
    """
    _invoke("index", "build")

    assert "delete .theurian/state/" in _invoke("index", "build")[1]["remedy"]
    assert not list((renamed_without_rebuilding_state / ".theurian/state").glob("theurian-index-*"))
    assert not (renamed_without_rebuilding_state / ".theurian/state/active-index.json").exists()


@pytest.fixture
def empty_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A registered, applied project holding no knowledge, so a build has 0 chunks.

    The only corpus that reaches the zero-chunk branch of `index build`. Every
    other fixture here indexes something, which is why the read that branch makes
    of the canonical store went unguarded for as long as it did.
    """
    root = tmp_path / "empty"
    root.mkdir()
    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "t@example.com"],
        ["git", "config", "user.name", "T"],
    ):
        subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(tmp_path / "datadir"))
    monkeypatch.chdir(root)
    assert runner.invoke(app, ["init", "--json"]).exit_code == 0
    assert runner.invoke(app, ["project", "register", "--json"]).exit_code == 0
    assert runner.invoke(app, ["migrate", "apply", "--json"]).exit_code == 0
    return root


def test_an_empty_project_may_still_publish_an_empty_index(empty_project: Path) -> None:
    """The control that keeps the refusal narrow.

    "No chunks" is only a defect when the canonical store *holds* knowledge. A
    project that has genuinely indexed nothing yet must still get an index, or
    the refusal would block the ordinary first build of an empty repository.
    """
    code, payload = _invoke("index", "build")

    assert code == 0
    assert payload["chunks"] == 0
    assert payload["published"] is True


def test_a_zero_chunk_build_asks_the_store_a_question_it_may_fail_to_answer(
    empty_project: Path,
) -> None:
    """CP-2 over the second store session a build opens, which nothing converted.

    `_run_build` converts the build's own read; this is a *different* session,
    opened afterwards to ask what the store held, and it reaches rows the build
    never touches -- `projects` among them. Measured against the real CLI before
    this was guarded: exit 1, empty stdout, and a Rich traceback whose
    ``__cause__`` published the damaged cell that
    `StateDatabaseUnreadableError` had just withheld from its own message.

    Asserted over the whole output rather than over `error`, because the escape
    this pins never reached a JSON document at all.
    """
    with closing(sqlite3.connect(_database(empty_project))) as raw, raw:
        raw.execute("UPDATE projects SET registered_at = ?", (SENTINEL,))

    result = runner.invoke(app, ["index", "build", "--json"])
    published = (result.stdout or "") + (result.stderr or "")

    assert result.exit_code != 0
    assert result.exception is None or isinstance(result.exception, SystemExit), (
        "an uncaught exception is the escape itself, whatever it says"
    )
    assert set(json.loads(published)) == {"error", "remedy"}, "the whole contract"
    assert "migrate apply" in json.loads(published)["remedy"]
    assert SENTINEL not in published, "and the cell stays inside the guard"
    assert not list((empty_project / ".theurian/state").glob("theurian-index-*.sqlite")), (
        "a refused build must leave no file a later `index status` would believe"
    )


def test_an_unreadable_state_database_fails_through_the_published_error_shape(
    project: Path,
) -> None:
    """CP-2. A corrupt state database used to escape as a raw
    `sqlite3.DatabaseError`: exit 1, empty stdout, a traceback at the user, and
    none of the `{"error", "remedy"}` shape every other command promises.

    In exactly the situation that is recoverable, too — canonical state rebuilds
    from Git-tracked migrations, and the remedy has to say so or a user will
    reach for a backup instead.
    """
    _database(project).write_bytes(b"this is not a database" * 64)

    code, payload = _invoke("index", "build")

    assert code != 0
    assert set(payload) == {"error", "remedy"}, "the whole contract, not merely an error key"
    assert "migrate apply" in payload["remedy"]
    assert "rebuilds from Git-tracked migrations" in payload["remedy"]


def test_a_failed_build_leaves_no_index_file_behind(project: Path) -> None:
    """A partial index is worse than none: it looks complete, ranks the fraction
    it holds, and never surfaces the rest — which reads as a relevance problem
    rather than a build failure, and so does not get investigated."""
    _database(project).write_bytes(b"this is not a database" * 64)

    _invoke("index", "build")

    assert not list((project / ".theurian/state").glob("theurian-index-*.sqlite"))


# -- Reclaiming superseded builds ---------------------------------------------


def test_reclaim_keeps_a_build_that_has_not_published_yet(tmp_path: Path) -> None:
    """A concurrent build must not lose the file it has just finished writing.

    Index build ids are ULIDs, so lexical order is creation order: an id greater
    than the published one can only belong to a build that started later and has
    not published yet. Reclaiming it would leave that build publishing a pointer
    to nothing.

    The first version of this deleted everything except the id *this process*
    built, which is the same bug seen from the other side — two concurrent
    builds each deleting the other's file.
    """
    from theurian.cli.index_commands import _publish, _reclaim

    paths = ProjectPaths.of(tmp_path)
    paths.state.mkdir(parents=True)
    for build_id in ("01K1AAAAAA", "01K1BBBBBB", "01K1CCCCCC"):
        paths.index_for(build_id).touch()
    _publish(
        paths,
        index_build_id="01K1BBBBBB",
        state_hash="s",
        project_id="demo",
        indexes_unapproved=False,
    )

    _reclaim(paths, keep="01K1BBBBBB")

    remaining = sorted(p.name for p in paths.state.glob("theurian-index-*.sqlite"))
    assert remaining == [
        "theurian-index-01K1BBBBBB.sqlite",
        "theurian-index-01K1CCCCCC.sqlite",
    ], "the published build and the one still in flight both survive"


def test_reclaim_measures_against_the_pointer_not_against_this_process(
    tmp_path: Path,
) -> None:
    """`keep` is what this process built; the pointer is what retrieval reads.

    When they disagree -- another build published while this one ran -- the
    pointer wins, or this process deletes the file every search is about to
    open. `SqliteIndexStore` holds no handle between calls, and `sqlite3.connect`
    *creates* an empty database at a deleted path, so the loss surfaces as a raw
    `no such table` at the agent rather than as the missing-file fallback.
    """
    from theurian.cli.index_commands import _publish, _reclaim

    paths = ProjectPaths.of(tmp_path)
    paths.state.mkdir(parents=True)
    for build_id in ("01K1AAAAAA", "01K1DDDDDD"):
        paths.index_for(build_id).touch()
    _publish(
        paths,
        index_build_id="01K1AAAAAA",
        state_hash="s",
        project_id="demo",
        indexes_unapproved=False,
    )

    _reclaim(paths, keep="01K1DDDDDD")

    assert paths.index_for("01K1AAAAAA").is_file(), "the published build survives a later `keep`"


def test_reclaim_takes_the_write_ahead_files_with_the_build(tmp_path: Path) -> None:
    """`-wal` and `-shm` sit beside the database and are meaningless without it.

    The whole id is parsed out of the filename first, because a substring
    comparison would treat a build whose id merely contains another as related
    to it.
    """
    from theurian.cli.index_commands import _publish, _reclaim

    paths = ProjectPaths.of(tmp_path)
    paths.state.mkdir(parents=True)
    for suffix in ("", "-wal", "-shm"):
        (paths.state / f"theurian-index-01K1AAAAAA.sqlite{suffix}").touch()
    paths.index_for("01K1BBBBBB").touch()
    _publish(
        paths,
        index_build_id="01K1BBBBBB",
        state_hash="s",
        project_id="demo",
        indexes_unapproved=False,
    )

    _reclaim(paths, keep="01K1BBBBBB")

    assert sorted(p.name for p in paths.state.glob("theurian-index-*")) == [
        "theurian-index-01K1BBBBBB.sqlite"
    ]


def test_retired_knowledge_is_never_indexed_even_when_asked_for(project: Path) -> None:
    """SEC-13, T-15. `--include-unapproved` reaches work in progress, never
    knowledge the team has retired.

    A deprecated or rejected revision is one the team decided must not be
    followed, and a rejected one is also where a secret that caused the
    rejection still lives. No flag reaches them.
    """
    (project / ".theurian/migrations/01K1DAAAAA01234567890ABCDE-deprecate.yaml").write_text(
        """apiVersion: theurian.dev/v1
id: 01K1DAAAAA01234567890ABCDE
createdAt: 2026-08-03T13:00:00+09:00
author: engineer@example.com
operations:
  - op: deprecateItem
    itemId: architecture.cache
    reason: superseded by the edge cache design
"""
    )
    assert runner.invoke(app, ["migrate", "apply", "--json"]).exit_code == 0

    index_path = project / ".theurian/state/theurian-index-01K1DXAAAA.sqlite"
    IndexBuilder(
        store_factory=SqliteCanonicalStore,
        index_factory=SqliteIndexStore,
        embedder=None,
    ).build(
        IndexRequest(
            database=_database(project),
            index_path=index_path,
            project_id="demo",
            state_hash="test-state",
            index_build_id="01K1DXAAAA",
            include_unapproved=True,  # asked for explicitly, and still refused
        )
    )

    hits = (
        SqliteIndexStore(index_path)
        .search_lexical("caching", project_id="demo", limit=50, include_unapproved=True)
        .rows
    )

    assert hits == (), "retired knowledge is not written to the index at all"


def test_the_surfaceable_statuses_exclude_everything_retired() -> None:
    """Guards the guard above: the set is what the builder consults, so a status
    quietly added to it becomes retrievable with one boolean."""
    from theurian.domain.enums import SURFACEABLE_STATUSES, KnowledgeStatus

    assert KnowledgeStatus.REJECTED not in SURFACEABLE_STATUSES
    assert KnowledgeStatus.DEPRECATED not in SURFACEABLE_STATUSES
    assert KnowledgeStatus.SUPERSEDED not in SURFACEABLE_STATUSES
    assert KnowledgeStatus.APPROVED in SURFACEABLE_STATUSES


def test_a_build_that_is_refused_the_path_does_not_delete_what_is_there(
    project: Path,
) -> None:
    """`create` refuses to overwrite; the cleanup then unlinked what it refused.

    That file is what `active-index.json` names, so a second build aimed at an
    already-published path took the published index with it and left every later
    search answering from a pointer to nothing. Unreachable from `theurian index
    build`, which mints a fresh ULID per build — but `IndexBuilder` is a public
    application-layer API and `create`'s contract says an existing file is left
    alone.

    Paired with `test_a_failed_build_leaves_no_index_file_behind`, which holds
    the other half: a build that *did* create the file still removes it.
    """
    from theurian.infrastructure.sqlite.index_store import IndexBuildError

    published = _build(project, embedder=None)
    builder = IndexBuilder(store_factory=SqliteCanonicalStore, index_factory=SqliteIndexStore)

    with pytest.raises(IndexBuildError):
        builder.build(
            IndexRequest(
                database=_database(project),
                index_path=published,
                project_id="demo",
                state_hash="another-state",
                index_build_id="01K1DXBBBB01234567890ABCDE",
            )
        )

    assert published.is_file(), "the published index must survive a build it refused to run"
    assert SqliteIndexStore(published).metadata()["state_hash"] == "test-state", (
        "and must still be the build it was, not a half-written replacement"
    )


# -- The canonical gate, reached without an MCP tool --------------------------
#
# Both of this milestone's extraction oracles lived in the *order* of gate,
# `limit` and budget, and neither could be seen from here: nothing exercised the
# gate except `knowledge.search`, and the budget's own unit tests measure
# arithmetic over an array of costs. Neither kind of test can say which side of
# the canonical store a number was computed on, which is the only thing that
# mattered.

DEPRECATE_MIGRATION = """apiVersion: theurian.dev/v1
id: 01K1EAAAAA01234567890ABCDE
createdAt: 2026-08-03T14:00:00+09:00
author: engineer@example.com
operations:
  - op: deprecateItem
    itemId: {item}
    reason: retired after the index was built
"""


def _retire_after_the_build(project: Path, item: str) -> None:
    """Withhold one item, leaving the index that still ranks it in place."""
    (project / ".theurian/migrations/01K1EAAAAA01234567890ABCDE-retire.yaml").write_text(
        DEPRECATE_MIGRATION.format(item=item)
    )
    assert runner.invoke(app, ["migrate", "apply", "--json"]).exit_code == 0


def _bare_shape(surfaced: Surfaced) -> dict[str, Any]:
    """A stand-in for the tool's wire shape, so the gate can be read on its own.

    Only the three fields these tests assert on. The real shaper lives in
    :mod:`theurian.mcp.search`, which is the point of injecting it.
    """
    return {
        "itemId": surfaced.revision.item_id.value,
        "status": surfaced.status.value,
        "fusedScore": round(surfaced.candidate.fused_score, 6),
    }


def _enumerate_the_state_directory_worst_first(
    monkeypatch: pytest.MonkeyPatch, project: Path
) -> None:
    """Make `Path.glob` hand back the state the pointer names *last*.

    A directory's enumeration order is not a guarantee. Measured: APFS and ext4
    disagree about the same two files, and APFS disagrees with itself for a
    different pair — the names are content addresses, so a shorter document is a
    different pair. A helper that enumerates therefore cannot be tested by
    running it; whichever order the machine gives, the test measures the machine.

    This removes the luck. The state the pointer names sorts last, so the *first*
    entry is deterministically a state that is no longer current, on any
    filesystem. Only the canonical prefix is reordered: `migrate apply` enumerates
    the migrations directory through the same method, and re-sorts what it finds.
    """
    paths = ProjectPaths.of(project)
    active = read_active_state(paths)
    assert active is not None, "the fixture must have published a canonical state"
    current = paths.state / active.database_filename
    original = Path.glob

    def worst_first(self: Path, pattern: str, **kwargs: Any) -> Iterator[Path]:
        found = original(self, pattern, **kwargs)
        if not pattern.startswith("theurian-state-"):
            return found
        # `sorted` is stable and exactly one entry compares True, so the last
        # element is the pointer's and the first is not.
        return iter(sorted(found, key=lambda path: path == current))

    monkeypatch.setattr(Path, "glob", worst_first)


def test_the_canonical_store_is_the_state_the_pointer_names(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SEC-13, T-15, ADR-0016. The oracle every retirement test here rests on.

    `_database` used to enumerate the state directory and take the first entry.
    Applying a migration writes a *new* content-addressed database beside the old
    one rather than replacing it, so from the second `migrate apply` onwards there
    are two, and "first" is whatever the filesystem says. macOS said the fresh
    one and the suite was green; Linux said the stale one, and five tests failed —
    three of them SEC-13 assertions that retired knowledge never reaches a caller,
    which were passing against a state in which nothing had been retired yet.

    The two assertions are the mechanism and its consequence: the resolved
    database must be the one that *records* the retirement, and an index built
    from it must not hold the retired item even when explicitly asked for it.

    This test cannot be green by luck. The order is forced adversarially rather
    than observed, so an implementation that enumerates fails it on every
    platform — including the one the defect hid on for the whole milestone.
    """
    _retire_after_the_build(project, "architecture.cache")
    state = ProjectPaths.of(project).state
    assert len(list(state.glob("theurian-state-*.sqlite"))) == 2, (
        "the retirement must leave the superseded state on disk, or nothing is being chosen between"
    )

    _enumerate_the_state_directory_worst_first(monkeypatch, project)

    with closing(sqlite3.connect(f"file:{_database(project)}?mode=ro", uri=True)) as raw:
        recorded = raw.execute(
            "SELECT status FROM knowledge_items WHERE item_id = ?", ("architecture.cache",)
        ).fetchone()
    assert recorded == ("deprecated",), "the resolved state is the one that records the retirement"

    hits = (
        SqliteIndexStore(_build(project, embedder=None))
        .search_lexical("caching", project_id="demo", limit=50, include_unapproved=True)
        .rows
    )
    assert hits == (), "and so the retired item is never written to the index"


def test_the_limit_is_applied_to_results_and_not_to_candidates(project: Path) -> None:
    """SEC-13, FR-R5. A withheld candidate must not consume a result slot.

    The document that is retired here is whichever one the ranking puts first,
    read off the ranking rather than assumed, so the test cannot quietly become
    vacuous by retiring something that was never going to be in the way.

    With `limit=1` and the gate applied last, the caller receives the best
    document they are allowed to see. With `limit` applied to candidates — the
    shipped behaviour — they receive nothing, and `count: 0` states that
    something matched and may not be read.
    """
    index_path = _build(project, embedder=None)
    service = _service(index_path, None)
    request = SearchRequest(query="policy", project_id="demo", per_item=1)
    ranked = [c.item_id for c in service.search(request, NOTHING_WITHHELD).candidates]
    assert len(ranked) >= 2, "the query must reach more than one document"

    _retire_after_the_build(project, ranked[0])
    resolved = ResultGate(store_factory=SqliteCanonicalStore, shape=_bare_shape).admit(
        ResultRequest(
            database=_database(project),
            project_id="demo",
            include_unapproved=False,
            limit=1,
            budget_tokens=32_000,
        ),
        _candidates_from(service, request),
    )

    assert [result["itemId"] for result in resolved.results] == [ranked[1]]


def test_the_scores_the_gate_publishes_are_computed_over_the_survivors(
    project: Path,
) -> None:
    """The same oracle in `fusedScore`, at the layer that closes it.

    RRF scores `1 / (k + rank)`, so a withheld chunk ranked above a visible one
    pushed that visible one's published score down by a rank. Ranking through the
    canonical store rather than after it gives the score the caller would have
    seen had the withheld document never been indexed — which is what makes it
    independent of what they may not read, rather than merely rounded.
    """
    index_path = _build(project, embedder=None)
    service = _service(index_path, None)
    request = SearchRequest(query="policy", project_id="demo", per_item=1)
    ranked = [c.item_id for c in service.search(request, NOTHING_WITHHELD).candidates]

    def admit() -> tuple[dict[str, Any], ...]:
        return (
            ResultGate(store_factory=SqliteCanonicalStore, shape=_bare_shape)
            .admit(
                ResultRequest(
                    database=_database(project),
                    project_id="demo",
                    include_unapproved=False,
                    limit=10,
                    budget_tokens=32_000,
                ),
                _candidates_from(service, request),
            )
            .results
        )

    before = admit()
    _retire_after_the_build(project, ranked[0])
    after = admit()

    assert [result["itemId"] for result in before] == ranked
    assert [result["itemId"] for result in after] == ranked[1:]
    assert [result["fusedScore"] for result in after] == [
        result["fusedScore"] for result in before[: len(after)]
    ], "the survivors must score as though the withheld document had never existed"


def test_a_limit_below_one_is_refused_with_a_remedy() -> None:
    """An impossible object does not get to exist and fail later.

    Clamping a zero to one would hide the arithmetic that produced it: `limit` is
    clamped at the tool boundary, so a zero arriving here was computed.
    """
    with pytest.raises(RetrievalError) as raised:
        ResultRequest(
            database=Path("unused"),
            project_id="demo",
            include_unapproved=False,
            limit=0,
            budget_tokens=100,
        )

    assert "Pass 1 or more" in str(raised.value)


def test_the_reserved_envelope_is_spent_before_the_results_are() -> None:
    """FR-R4. What arrives in the caller's window is the whole message.

    Pure, and deliberately not routed through a tool: this is arithmetic, and the
    defect it guards was that the arithmetic was applied to the wrong total.
    """
    results: list[dict[str, Any]] = [{"body": "x" * 400}, {"body": "y" * 400}]

    unreserved = within_budget(results, budget_tokens=250)
    reserved = within_budget(results, budget_tokens=250, reserved_tokens=120)

    assert unreserved.dropped == 0, "both fit when only the results are charged"
    assert reserved.dropped == 1, "one does not, once the envelope is charged too"
    assert reserved.used_tokens < unreserved.used_tokens


def test_a_budget_smaller_than_the_envelope_still_answers() -> None:
    """The floor `take_within_budget` promises, held through the subtraction.

    A naive `budget - reserved` goes to zero or negative, and a zero budget is a
    `RankingError` — turning "your budget is small" into a tool failure, or into
    the empty answer an agent reads as "we have no such decision".
    """
    starved = within_budget([{"body": "x" * 400}], budget_tokens=5, reserved_tokens=10_000)

    assert len(starved.results) == 1
    assert starved.used_tokens > 0


# -- Which chunk gets published, and who is allowed to decide it -------------
#
# `diversify(per_item=1)` keeps one chunk per document. Run before the canonical
# store has had its say, *which* chunk it keeps is decided by a ranking that
# still holds withheld rows -- so two queries differing only in a token no
# visible document contains published two different `excerpt`s for the same
# document, nine tokens apart. Re-fusing afterwards cannot repair it: the
# discarded chunk is not in the list to be re-fused.
#
# Scripted rather than built from a corpus. The channel needs one exact rank
# arrangement, and a corpus that happens to produce it today is a corpus that
# stops producing it the next time anything about chunking changes. These ranks
# are the ones measured to move it: the withheld row sits between the two chunks
# in one retriever and above both in the other, which is enough to swap which of
# them RRF puts first.

_VISIBLE = "architecture.notes"
_WITHHELD = "architecture.runbook"
_FIRST, _SECOND = "01K1AAAREV01234567890ABCDE#0", "01K1AAAREV01234567890ABCDE#1"
_RETRACTED = "01K1BBBREV01234567890ABCDE#0"


def _row(chunk_id: str, item_id: str) -> Ranked:
    return Ranked(chunk_id=chunk_id, item_id=item_id, revision_id=chunk_id.partition("#")[0])


@final
class _ScriptedIndex:
    """An index whose retrievers answer from a script.

    A fake rather than a fixture because the arrangement of ranks *is* the test.
    Only the read half is implemented; an index that is never built has no use
    for the write half, and a stub that raises says so more clearly than one that
    silently accepts.
    """

    def __init__(self, lexical: tuple[Ranked, ...], substring: tuple[Ranked, ...]) -> None:
        self._lexical = lexical
        self._substring = substring

    def create(self, *, index_build_id: str, state_hash: str) -> None:
        raise NotImplementedError

    def derive_purged(
        self,
        target: Path,
        *,
        revision_ids: Sequence[str],
        index_build_id: str,
        state_hash: str,
    ) -> int:
        raise NotImplementedError

    def add_chunks(self, chunks: Sequence[IndexableChunk]) -> int:
        raise NotImplementedError

    def add_embeddings(self, vectors: Sequence[tuple[str, Sequence[float]]]) -> int:
        raise NotImplementedError

    def record_embedding_model(self, *, model_id: str, dimension: int) -> None:
        raise NotImplementedError

    def metadata(self) -> Mapping[str, object]:
        return {}

    # Every argument below is named by the `IndexStore` protocol and ignored --
    # hence the `noqa`s -- because the script, not the query, decides the answer.
    # `limit` is the exception and is honoured, through `fakes.truncating`: these
    # two stand in for the `LIMIT`-bearing retrievers, so they are exhausted
    # exactly when the script did not fill the ask. Reporting exhaustion any
    # other way ends the depth loop for a reason the real store would not, and
    # the tests below would go green measuring that instead.
    def search_lexical(
        self,
        query: str,  # noqa: ARG002
        *,
        project_id: str,  # noqa: ARG002
        limit: int,
        include_unapproved: bool,  # noqa: ARG002
    ) -> RetrieverPage:
        return truncating(self._lexical, limit)

    def search_substring(
        self,
        query: str,  # noqa: ARG002
        *,
        project_id: str,  # noqa: ARG002
        limit: int,
        include_unapproved: bool,  # noqa: ARG002
    ) -> RetrieverPage:
        return truncating(self._substring, limit)

    def search_dense(
        self,
        query_vector: Sequence[float],  # noqa: ARG002
        *,
        project_id: str,  # noqa: ARG002
        include_unapproved: bool,  # noqa: ARG002
    ) -> RetrieverPage:
        return whole(())

    def chunk_texts(
        self,
        chunk_ids: Sequence[str],
        *,
        project_id: str,  # noqa: ARG002
    ) -> Mapping[str, str]:
        return {chunk_id: f"passage of {chunk_id}" for chunk_id in chunk_ids}


class _WithoutTheRunbook:
    """The canonical store's answer once one document has been retired."""

    def cleared(self, ranked: Sequence[Ranked]) -> tuple[Ranked, ...]:
        return tuple(row for row in ranked if row.item_id != _WITHHELD)


def test_a_withheld_row_cannot_choose_which_chunk_of_a_visible_document_is_published() -> None:
    """SEC-13, FR-R4. `per_item` must cap what is *visible*, not what was ranked.

    Two indexes of the same visible document, differing only in whether a
    withheld document was ever written. The published candidate — one chunk,
    because `per_item=1` — has to be the same chunk, at the same score, in both.

    The arrangement is chosen so it would not be. With the withheld row present,
    RRF puts the second chunk first (`1/61 + 1/63` against `1/63 + 1/62`); with
    it absent the two chunks tie and the tie breaks on chunk id, which puts the
    first chunk first. A gate applied after `diversify` sees only the survivor of
    that choice and cannot tell that the choice was made for it.
    """
    request = SearchRequest(query="gateway", project_id="demo", per_item=1)
    first, second = _row(_FIRST, _VISIBLE), _row(_SECOND, _VISIBLE)
    retracted = _row(_RETRACTED, _WITHHELD)

    never_written = RetrievalService(_ScriptedIndex((second, first), (first, second)))
    still_indexed = RetrievalService(
        _ScriptedIndex((second, retracted, first), (retracted, first, second))
    )

    control = never_written.search(request, NOTHING_WITHHELD)
    probe = still_indexed.search(request, _WithoutTheRunbook())

    assert [c.chunk_id for c in control.candidates] == [_FIRST], "the fixture must be arranged"
    assert [(c.chunk_id, c.fused_score, c.found_by) for c in probe.candidates] == [
        (c.chunk_id, c.fused_score, c.found_by) for c in control.candidates
    ], "the withheld row must not reach the per-document cap that chooses the excerpt"
    assert probe.passages == control.passages, "and so must not choose the passage either"


# -- T-17a: BM25 collection statistics count withheld documents --------------
#
# Everything above pins a channel that was *closed*. This one pins a channel
# that is **open, measured, and accepted for Milestone 5** (threat model T-17a,
# issue #15). It is here because the acceptance is conditional on it.
#
# FTS5's `bm25` weights each query phrase by
#
#     idf = log((N - nHit + 0.5) / (nHit + 0.5))
#
# where `nHit` counts the rows matching *that phrase*. The visibility gate takes
# withheld rows out of the result; it cannot take them out of the statistics the
# surviving rows are scored against. So while an index is stale, a withheld
# document containing one of the query's terms reweights the *visible* rows
# against each other, and the published order can change.
#
# **What that channel is bounded by is the *oracle*, not the order**, and the
# threat model is emphatic about the difference because conflating the two is how
# the entry's bound came to be written wrongly twice. A term that occurs in no
# visible row leaves every visible row with `tf = 0` for that phrase, so a probe
# for it reads nothing back: what an attacker gets is confirmation that a withheld
# document contains a term they can already see elsewhere, not extraction of an
# arbitrary secret. That is a statement about what can be *asked*, and it says
# nothing about which published values move.
#
# **`idf` is not the only statistic the withheld rows move, and no vocabulary
# precondition attaches to the other one.** BM25's length normalisation is
# `k1 * (1 - b + b * D / avgdl)`, a function of each row's *own* length `D`, so it
# is not a common factor across rows the way a shared `idf` is -- and `avgdl` is
# an average over the whole collection, including rows no caller may see.
# Retiring a long document therefore reweights the visible rows against each other
# while sharing not one term with the query. Measured on this pipeline and
# controlled at the FTS5 layer, with `nHit` pinned at (2, 2) so `idf` is identical
# by construction:
#
#     fresh (never held it)      N=22  avgdl= 8.73   isolation, retention
#     stale, long withheld rows  N=26  avgdl=18.46   retention, isolation  FLIPPED
#     stale, mean-length rows    N=26  avgdl= 8.62   isolation, retention  same
#
# The third row is the control that names the cause: adding the same number of
# rows without moving `avgdl` moves nothing, so it is the length norm and not `N`.
# A sweep found 1,218 such corpora.
#
# Three things follow for the tests, and they pull in different directions:
#
# - `test_a_withheld_document_changes_nothing_a_caller_can_see` asserts the
#   equality this breaks, and passes. It passes because its corpus cannot move
#   far enough to reorder itself, which is a fact about that fixture and not
#   about the property. A green suite is therefore not evidence here.
# - So the fixture below is built to flip, and `test_the_bm25_probe_corpus_can
#   _still_flip` asserts that it still can. Without the guard, a later edit to
#   these bodies would turn the test into one more assertion that holds for the
#   wrong reason.
# - The guard fixes only *that* the corpus flips, which one channel or the other
#   satisfies. `test_removing_the_shared_term_from_the_visible_bodies_stops_this
#   _corpus_flipping` is the control that isolates `idf`, and
#   `test_a_withheld_document_sharing_no_vocabulary_still_reorders_the_visible
#   _ones` is what stops that control being read as a proof of a vocabulary
#   precondition on the order. They have to be read as a pair; separately, each
#   says something the other denies. Together they are the T-17a acceptance's
#   third condition.
#
# When Milestone 6 lands blue/green builds (ADR-0022) the window closes and the
# test below starts failing. That is the intended alarm: it is a pin on an
# accepted residual's *scope*, so the acceptance has to be revisited by whoever
# makes it stop reproducing -- in either direction.

#: The term the withheld document shares with a visible one. Shared on purpose:
#: it is the precondition for the channel, and a probe term absent from visible
#: content moves nothing at all.
PROBE_TERM = "quarantine"

#: The second phrase, carried only by visible documents. Two phrases are needed
#: because the channel is a *reweighting between* phrases -- a single-phrase
#: query scales every row by the same `idf` and preserves their order.
COMMON_TERM = "ledger"

PROBE_QUERY = f"{PROBE_TERM} {COMMON_TERM}"


# The two visible documents, deliberately close and deliberately opposite. One
# leans on the probe term, the other on the common term, so a shift in the probe
# term's weight moves them against each other rather than together.
#
# Each names its own term three times and the other once, so neither dominates
# and both carry the probe term — which is the precondition the `idf` channel
# needs. The repetition is the fixture's whole content: term counts and document
# lengths are what BM25 reads, so this text is tuned rather than written, and the
# guard below is what says whether the tuning still holds.
#
# Built by a function of the shared term so the control can substitute it. The
# substitute is the same part of speech and the same token count, so the only
# collection statistic that moves between the two corpora is the `idf` of the
# phrase itself -- which is what makes the control a control rather than a second
# corpus.
def _isolation_body(probe: str) -> str:
    return (
        "# Tenant isolation\n\n"
        + f"The {probe} step isolates one tenant. " * 3
        + f"The {COMMON_TERM} records that it happened.\n"
    )


def _retention_body(probe: str) -> str:
    return (
        "# Records retention\n\n"
        + f"The {COMMON_TERM} keeps records for seven years. " * 3
        + f"The {probe} names it.\n"
    )


ISOLATION_BODY = _isolation_body(PROBE_TERM)
RETENTION_BODY = _retention_body(PROBE_TERM)

#: What the control puts where the shared term was. A real word the corpus does
#: not otherwise use, so the visible rows keep their length and their shape and
#: lose only their `tf` for the phrase the withheld document carries.
NEUTRAL_TERM = "handover"

#: The document that is withheld, in two chunks.
#:
#: Two because that is the shape the reproduction in T-17a used. It is **not** a
#: floor, and this corpus is a second measurement saying so: merged to a single
#: chunk it still flips. The count is a property of the corpus, so no threshold
#: should be read into it in either direction. The guard below asserts the number
#: this fixture actually produces — not the number a flip requires — so a chunker
#: change that silently merged them is caught as a fixture drifting off the shape
#: the threat model describes, rather than showing up later as an unexplained
#: non-flip.
INCIDENT_BODY = "# Payment tenant incident\n\n" + "".join(
    f"## {section}\n\n"
    + f"The {PROBE_TERM} rehearsal for the payment tenant is recorded here. " * 6
    + "\n\n"
    for section in ("Rehearsal", "Finding")
)

#: Corpus filler. None of it carries either term, so it changes `N` and nothing
#: else -- which is what a real project's unrelated knowledge does. Present
#: because `idf` is degenerate on a corpus of three rows: with too few documents
#: the log term collapses and both visible rows score identically, so the fixture
#: would be measuring a tie-break rather than a reweighting.
NOISE_BODY = (
    "# Deployment window {number}\n\nRelease {number} goes out on Thursday after the "
    "staging soak has run for a day.\n"
)
NOISE_DOCUMENTS = 6

INCIDENT_TITLE = "Payment tenant incident"

# -- The same channel with the vocabulary taken away ---------------------------
#
# A second corpus, tuned the same way and for the opposite claim: the withheld
# document here carries neither query term, as a token or as a substring, so the
# `idf` of every query phrase is identical in both indexes by construction. All
# that changes between them is `avgdl`.
#
# Found by sweeping the same knobs the corpus above was tuned on -- how often
# each visible document names each term, how much unrelated filler it carries,
# and how long the withheld document is. The sweep was stopped after twelve
# configurations reordered the published answer; this is one of the twelve, so
# the shape is not a needle.

#: A sentence that shares nothing with `PROBE_QUERY` -- checked as a substring
#: rather than as a word, because the trigram retriever matches substrings and a
#: body containing `ledgers` would share vocabulary with the query on that
#: retriever while looking disjoint on this one.
DISJOINT_SENTENCE = "Rotation of the signing material happened overnight during the freeze. "

#: A word only the withheld document uses, so a test can ask which index holds
#: that document without asking the query -- which cannot find it in either.
WITHHELD_ONLY_TERM = "signing"

DISJOINT_INCIDENT_TITLE = "Payment incident"
DISJOINT_INCIDENT_BODY = f"# {DISJOINT_INCIDENT_TITLE}\n\n" + DISJOINT_SENTENCE * 30

#: The visible pair for that corpus. Same two documents and the same two terms;
#: only the proportions differ, because a corpus that reorders under a shift in
#: `avgdl` is tuned against a different quantity than one that reorders under a
#: shift in `idf`.
DISJOINT_ISOLATION_BODY = (
    "# Tenant isolation\n\n"
    + f"The {PROBE_TERM} step isolates one tenant. "
    + f"The {COMMON_TERM} records that it happened. "
    + "\n"
)
DISJOINT_RETENTION_BODY = (
    "# Records retention\n\n"
    + f"The {COMMON_TERM} keeps records for seven years. "
    + f"The {PROBE_TERM} names it. " * 3
    + "Filler sentence about the platform team. " * 2
    + "\n"
)

PROBE_MIGRATION = """apiVersion: theurian.dev/v1
id: {mid}
createdAt: 2026-08-03T15:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: architecture.{slug}
    kind: architecture
    namespace: backend
    owner: platform-team
  - op: upsertRevision
    itemId: architecture.{slug}
    revisionId: {rid}
    contentFile: ../knowledge/architecture/{slug}.md
    metadata:
      title: {title}
      contentType: text/markdown
      kind: architecture
      namespace: backend
      status: approved
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/{slug}.md
"""

RETIRE_INCIDENT = """apiVersion: theurian.dev/v1
id: 01K1R0AAAA01234567890ABCDE
createdAt: 2026-08-03T16:00:00+09:00
author: engineer@example.com
operations:
  - op: deprecateItem
    itemId: architecture.incident
    reason: retired after the index was built
"""

WITHHELD_ITEM = "architecture.incident"


@dataclass(frozen=True, slots=True)
class _BM25Probe:
    """One project and two builds of it, differing only in the withheld document.

    ``stale`` was built while the incident note was approved and still holds its
    chunks; ``fresh`` was built after it was retired and never held them. Both
    are searched through the *same* canonical state, so the only difference
    between the two answers is what the index counts.
    """

    project: Path
    stale: Path
    fresh: Path


def _probe_document(project: Path, tag: str, slug: str, title: str, body: str) -> None:
    (project / f".theurian/knowledge/architecture/{slug}.md").write_text(body)
    (project / f".theurian/migrations/01K1{tag}AAAA01234567890ABCDE-{slug}.yaml").write_text(
        PROBE_MIGRATION.format(
            mid=f"01K1{tag}AAAA01234567890ABCDE",
            rid=f"01K1{tag}REVA01234567890ABCDE",
            slug=slug,
            title=title,
        )
    )


def _build_probe_index(project: Path, name: str, build_id: str) -> Path:
    index_path = project / f".theurian/state/theurian-index-{name}.sqlite"
    IndexBuilder(
        store_factory=SqliteCanonicalStore,
        index_factory=SqliteIndexStore,
        embedder=None,
    ).build(
        IndexRequest(
            database=_database(project),
            index_path=index_path,
            project_id="demo",
            state_hash="probe-state",
            index_build_id=build_id,
        )
    )
    return index_path


def _probe_corpus(
    project: Path, *, isolation: str, retention: str, incident: str, incident_title: str
) -> _BM25Probe:
    """Publish one corpus, build an index, retire the incident note, build again.

    Both builds see the same two visible documents and the same filler; they
    differ only in whether the incident note's chunks are in the file. The
    retirement happens between them, so the *canonical* state both searches run
    against is the one that withholds it.
    """
    _probe_document(project, "P0", "isolation", "Tenant isolation", isolation)
    _probe_document(project, "P1", "retention", "Records retention", retention)
    _probe_document(project, "P2", "incident", incident_title, incident)
    for number in range(NOISE_DOCUMENTS):
        _probe_document(
            project,
            f"Q{number}",
            f"window-{number}",
            f"Deployment window {number}",
            NOISE_BODY.format(number=number),
        )
    assert runner.invoke(app, ["migrate", "apply", "--json"]).exit_code == 0

    # `STAAAA`, not `STALEA`: Crockford base32 has no L, and the fixture guard in
    # `tests/unit/test_test_fixtures.py` is what caught the readable spelling.
    stale = _build_probe_index(project, "01K1STAAAA", "01K1STAAAA01234567890ABCDE")

    (project / ".theurian/migrations/01K1R0AAAA01234567890ABCDE-retire.yaml").write_text(
        RETIRE_INCIDENT
    )
    assert runner.invoke(app, ["migrate", "apply", "--json"]).exit_code == 0

    fresh = _build_probe_index(project, "01K1FRESHA", "01K1FRESHA01234567890ABCDE")

    return _BM25Probe(project=project, stale=stale, fresh=fresh)


@pytest.fixture
def bm25_probe(project: Path) -> _BM25Probe:
    """A corpus built to exhibit T-17a, and both indexes it takes to see it."""
    return _probe_corpus(
        project,
        isolation=ISOLATION_BODY,
        retention=RETENTION_BODY,
        incident=INCIDENT_BODY,
        incident_title=INCIDENT_TITLE,
    )


@pytest.fixture
def bm25_probe_without_the_shared_term(project: Path) -> _BM25Probe:
    """`bm25_probe`, with the shared term gone from the *visible* bodies.

    The withheld document still carries it, so the `idf` of that phrase still
    moves between the two indexes -- but no visible row has a `tf` for it, which
    is what the T-17a bound says makes the channel unreachable.
    """
    return _probe_corpus(
        project,
        isolation=_isolation_body(NEUTRAL_TERM),
        retention=_retention_body(NEUTRAL_TERM),
        incident=INCIDENT_BODY,
        incident_title=INCIDENT_TITLE,
    )


@pytest.fixture
def bm25_probe_sharing_no_vocabulary(project: Path) -> _BM25Probe:
    """A corpus whose withheld document has no term of the query at all."""
    return _probe_corpus(
        project,
        isolation=DISJOINT_ISOLATION_BODY,
        retention=DISJOINT_RETENTION_BODY,
        incident=DISJOINT_INCIDENT_BODY,
        incident_title=DISJOINT_INCIDENT_TITLE,
    )


def _matching_chunks(index_path: Path, term: str) -> int:
    """How many chunks of an index match one term -- FTS5's `nHit` for it.

    The quantity BM25's `idf` is computed from, read back through the same
    retriever a search uses. Compared between the two indexes, it says whether a
    difference in the published order could be the `idf` channel at all.
    """
    return len(
        SqliteIndexStore(index_path)
        .search_lexical(term, project_id="demo", limit=200, include_unapproved=False)
        .rows
    )


def _published_order(probe: _BM25Probe, index_path: Path) -> list[str]:
    """The item ids a caller receives, in the order they receive them."""
    service = _service(index_path, None)
    request = SearchRequest(query=PROBE_QUERY, project_id="demo", per_item=1)
    resolved = ResultGate(store_factory=SqliteCanonicalStore, shape=_bare_shape).admit(
        ResultRequest(
            database=_database(probe.project),
            project_id="demo",
            include_unapproved=False,
            limit=10,
            budget_tokens=32_000,
        ),
        _candidates_from(service, request),
    )
    return [result["itemId"] for result in resolved.results]


def test_a_withheld_document_can_still_reorder_the_visible_ones(bm25_probe: _BM25Probe) -> None:
    """T-17a. The accepted residual, pinned so its scope cannot grow unnoticed.

    **This test asserts that a leak is present.** It is not a regression test for
    a fix; it is the third of the three conditions the T-17a acceptance carries,
    and it exists because the entry admits the suite is green there by a property
    of one fixture rather than by a proof of the property.

    Two builds of one project, searched through one canonical state: the stale
    index still holds the retired incident note, the fresh one never did. The
    caller may read neither, and receives the same two visible documents from
    both — in a different order, because the withheld note's chunks are counted
    in the `idf` of a term the visible documents share.

    Asserted as inequality of the published order rather than of a score,
    because the order is what a caller acts on and a score is what an
    implementation happens to expose. A difference here is reachable through
    `knowledge.search` with no parameters: `fusedScore`, the hit order and — with
    `per_item=1`, the only mode the MCP surface has — which `excerpt` is
    published all move with it.

    When Milestone 6 closes the stale window (ADR-0022, issue #15) this test goes
    red, and it is meant to: whoever makes it stop reproducing is the person who
    should be deleting the acceptance from the threat model in the same change.
    """
    stale = _published_order(bm25_probe, bm25_probe.stale)
    fresh = _published_order(bm25_probe, bm25_probe.fresh)

    assert stale != fresh, (
        "T-17a says the statistics carry the withheld document into the visible "
        "order; if this no longer reproduces, the threat model entry and its "
        "acceptance are out of date"
    )


def test_the_bm25_probe_corpus_can_still_flip(bm25_probe: _BM25Probe) -> None:
    """Guards the test above, whose whole meaning is in its fixture.

    "The two orders differ" is satisfiable by two answers that differ in
    *membership*, which would be an ordinary bug and not this channel at all. So
    the preconditions are asserted rather than assumed:

    the same two documents, in both answers
        a reorder, not a different result set;
    the incident note in neither
        the gate is doing its job, and the flip is the statistics rather than a
        withheld document being published;
    its chunks still in the stale index, and not in the fresh one
        the two indexes really differ in what they count;
    more than one of those chunks
        the shape the reproduction used, so a chunker change that merged them
        shows up here as a broken fixture and not as a mysterious non-flip;
    both visible documents carrying the probe term
        the bound T-17a rests on, and the precondition of the `idf` channel
        specifically: a term absent from visible content leaves every visible row
        with `tf = 0` for that phrase, so `idf` cannot reweight them. A fixture
        that lost the shared vocabulary would stop being able to flip *through
        that channel* while still looking correct.

    **What this guard cannot do is say which channel produced the flip.** It
    fixes only that the corpus flips, and two of the configurations measured
    while writing it hold every precondition above with the flip driven by the
    length norm instead. That is what
    `test_removing_the_shared_term_from_the_visible_bodies_stops_this_corpus
    _flipping` adds -- and what
    `test_a_withheld_document_sharing_no_vocabulary_still_reorders_the_visible
    _ones` stops anyone reading as a proof of the bound.
    """
    stale = _published_order(bm25_probe, bm25_probe.stale)
    fresh = _published_order(bm25_probe, bm25_probe.fresh)
    withheld_chunks = (
        SqliteIndexStore(bm25_probe.stale)
        .search_lexical(PROBE_QUERY, project_id="demo", limit=50, include_unapproved=False)
        .rows
    )
    fresh_chunks = (
        SqliteIndexStore(bm25_probe.fresh)
        .search_lexical(PROBE_QUERY, project_id="demo", limit=50, include_unapproved=False)
        .rows
    )

    assert sorted(stale) == sorted(fresh), "the answers must differ in order and nothing else"
    assert set(stale) == {"architecture.isolation", "architecture.retention"}
    assert WITHHELD_ITEM not in stale, "the gate must still withhold it from both answers"
    assert len([row for row in withheld_chunks if row.item_id == WITHHELD_ITEM]) > 1, (
        "the stale index must still hold more than one matching chunk of it"
    )
    assert not [row for row in fresh_chunks if row.item_id == WITHHELD_ITEM], (
        "and the fresh index must never have held any"
    )
    assert PROBE_TERM in ISOLATION_BODY and PROBE_TERM in RETENTION_BODY, (
        "the probe term must be in the visible vocabulary, or nothing can move"
    )


def test_removing_the_shared_term_from_the_visible_bodies_stops_this_corpus_flipping(
    bm25_probe_without_the_shared_term: _BM25Probe,
) -> None:
    """Isolates the `idf` channel on the corpus above -- and only that channel.

    The same two visible documents, the same filler, the same withheld incident
    note still naming `quarantine` a dozen times. One substitution: the visible
    bodies say `handover` where they said `quarantine`. Every visible row now has
    `tf = 0` for the phrase whose `idf` moves between the two indexes, and the
    published order stops moving with it.

    That is the mechanism T-17a describes, made to switch off. Without it the
    guard above fixes only *that* the corpus flips, which the length norm
    satisfies just as well. Measured, by sweeping 108 settings of the fixture's
    own knobs through this same integration fixture: at one `quarantine` in the
    isolation body, two `ledger`s and six filler sentences in the retention body,
    and sixty repetitions in the incident note, the guard and
    `test_a_withheld_document_can_still_reorder_the_visible_ones` both stay green
    and this test alone fails — the corpus has drifted onto the other channel and
    nothing but this said so.

    **Passing here does not establish a vocabulary precondition on the published
    order, and must not be read as if it did.** It establishes one thing about one
    corpus: on *this* one, the shared term is what moves the answer. The general
    claim -- that a withheld document sharing no vocabulary with the query cannot
    move a visible result -- is false, was stated by T-17a, and has been retracted
    there; the test below is the counterexample it was retracted on. Two of the
    108 configurations in the sweep above defeat this control outright, so "the
    control holds" is a fact about the corpus it is run on.

    So the two are a pair, and they are T-17a's third acceptance condition
    together rather than separately. Read alone, this one says the channel is
    bounded by shared vocabulary; read alone, the next says it is not. Both are
    true of what they actually test, and only together do they say what the scope
    of the accepted residual is. Deleting either leaves the other stating a
    half-truth with a green suite behind it.
    """
    probe = bm25_probe_without_the_shared_term
    stale = _published_order(probe, probe.stale)
    fresh = _published_order(probe, probe.fresh)

    assert PROBE_TERM not in _isolation_body(NEUTRAL_TERM), "the substitution must have happened"
    assert PROBE_TERM not in _retention_body(NEUTRAL_TERM), "in both visible bodies"
    assert PROBE_TERM in INCIDENT_BODY, "and the withheld document must still carry the term"
    assert _matching_chunks(probe.fresh, PROBE_TERM) == 0, (
        "no visible row may match the phrase, or `tf` is not zero and this isolates nothing"
    )
    assert _matching_chunks(probe.stale, PROBE_TERM) > 0, (
        "while the stale index must still match it, so the phrase's `idf` really "
        "does differ between the two"
    )
    assert set(stale) == {"architecture.isolation", "architecture.retention"}
    assert stale == fresh, (
        "with no visible `tf` for the moved phrase, the `idf` channel cannot "
        "reach the published order"
    )


def test_a_withheld_document_sharing_no_vocabulary_still_reorders_the_visible_ones(
    bm25_probe_sharing_no_vocabulary: _BM25Probe,
) -> None:
    """T-17a's third condition: the channel with no vocabulary precondition.

    **This test asserts that a leak is present**, and it is the one the T-17a
    acceptance is conditional on — condition 3 names it. The bound this
    falsifies is one the threat model used to state and has since retracted: that
    a withheld document containing none of the query's terms leaves every visible
    row with `tf = 0` and therefore moves nothing. That holds for `idf`, which
    the control above demonstrates. It does not hold for BM25 as a whole, and the
    entry now records both channels.

    The length normalisation `k1 * (1 - b + b * D / avgdl)` divides each row's
    own length by an average taken over the *whole collection*, withheld rows
    included. It is therefore not a common factor across rows, and moving `avgdl`
    reorders them. The withheld document here names neither query term -- not as
    a token and not as a substring, which is the form the trigram retriever
    matches -- so `nHit` is identical in both indexes and `idf` is identical with
    it. It is asserted here rather than assumed, because a fixture that leaked
    one query term into the withheld body would silently become the already
    documented `idf` case wearing a new name.

    **What this pins is the equality, not a wider oracle**, and the two are worth
    keeping apart because conflating them is how T-17a's bound was written wrongly
    twice. The equality — every published value equals what the same query would
    return had the withheld documents never been indexed — is broken here, on a
    corpus that shares no word with the query, so it is broken on *any* stale
    index whatever the withheld documents say. The extraction oracle is unchanged:
    `avgdl` and `N` are query-independent, so within one index a caller varying
    their probe cannot move them and cannot ask a question about withheld content.
    Reading even the aggregate length they carry means comparing against an index
    that never held those documents — that is, across an `index build`, which is
    the operation that removes them.

    Like the tests above, this goes red when Milestone 6 closes the stale window
    (ADR-0022, issue #15), and it is meant to: whoever makes it stop reproducing
    is the person who should be rewriting the T-17a entry rather than deleting
    this file.
    """
    probe = bm25_probe_sharing_no_vocabulary
    stale = _published_order(probe, probe.stale)
    fresh = _published_order(probe, probe.fresh)

    for term in (PROBE_TERM, COMMON_TERM):
        assert term not in DISJOINT_INCIDENT_BODY, (
            f"the withheld document must not contain {term!r} in any form, or "
            f"this measures the `idf` channel the threat model already records"
        )
        assert _matching_chunks(probe.stale, term) == _matching_chunks(probe.fresh, term) > 0, (
            f"`nHit` for {term!r} must be identical in both indexes and non-zero: "
            f"identical so `idf` cannot be what moved, non-zero so the term is in play"
        )

    assert _matching_chunks(probe.stale, WITHHELD_ONLY_TERM) > 1, (
        "the stale index must still hold the withheld document, in more than one chunk"
    )
    assert _matching_chunks(probe.fresh, WITHHELD_ONLY_TERM) == 0, (
        "and the fresh index must never have held it"
    )
    assert sorted(stale) == sorted(fresh), "the answers must differ in order and nothing else"
    assert set(stale) == {"architecture.isolation", "architecture.retention"}
    assert WITHHELD_ITEM not in stale, "the gate withholds it from both answers, as it should"
    assert stale != fresh, (
        "a withheld document sharing no vocabulary with the query still reorders "
        "the visible results; if this no longer reproduces, the T-17a bound has "
        "become true and the threat model can say so"
    )
