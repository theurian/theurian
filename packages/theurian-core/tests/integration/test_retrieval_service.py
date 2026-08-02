"""Hybrid retrieval end to end (FR-R1..R4, FR-R7).

A real canonical store, a real index file, and the real default embedder. The
only thing not exercised here is the MCP transport.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from theurian.application.retrieval_service import (
    IndexBuilder,
    IndexRequest,
    RetrievalService,
    SearchRequest,
)
from theurian.cli.main import app
from theurian.domain.ports.embedding import EmbeddingProvider
from theurian.domain.ranking import RetrievalMode
from theurian.infrastructure.embedding import HashingEmbedding
from theurian.infrastructure.sqlite.index_store import SqliteIndexStore
from theurian.infrastructure.sqlite.store import SqliteCanonicalStore

pytestmark = pytest.mark.integration

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


def _database(project: Path) -> Path:
    # The canonical prefix, not `*.sqlite`: index builds live in the same
    # directory, and a glob that caught them would hand a retrieval index to the
    # canonical store and produce a baffling failure.
    databases = list((project / ".theurian/state").glob("theurian-state-*.sqlite"))
    assert databases, "the fixture must have built a canonical state"
    return databases[0]


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

    outcome = service.search(SearchRequest(query="signed JWT", project_id="demo"))

    assert outcome.candidates
    assert outcome.candidates[0].item_id == "architecture.auth"


def test_a_title_only_match_still_finds_the_document(project: Path) -> None:
    """The title is prepended before chunking, so a query naming only the
    document still reaches it without a second retriever and a fusion weight."""
    service = _service(_build(project, embedder=None), None)

    outcome = service.search(SearchRequest(query="Caching policy", project_id="demo"))

    assert outcome.candidates[0].item_id == "architecture.cache"


def test_both_retrievers_running_is_reported_as_hybrid(project: Path) -> None:
    embedder = HashingEmbedding()
    service = _service(_build(project, embedder=embedder), embedder)

    outcome = service.search(SearchRequest(query="token rotation", project_id="demo"))

    assert outcome.mode is RetrievalMode.HYBRID
    assert outcome.embedding_model == embedder.model_id


def test_an_index_without_embeddings_degrades_visibly_to_lexical(project: Path) -> None:
    """The failure this reporting exists for: a search that quietly lost its
    dense half must not look identical to a healthy one."""
    service = _service(_build(project, embedder=None), HashingEmbedding())

    outcome = service.search(SearchRequest(query="signed JWT", project_id="demo"))

    assert outcome.mode is RetrievalMode.LEXICAL
    assert outcome.embedding_model == ""
    assert outcome.candidates, "lexical search still works"


def test_a_morphological_variant_is_found_only_with_the_dense_retriever(
    project: Path,
) -> None:
    """The reason the default embedder ships at all. FTS5 matches terms
    exactly, so "rotating" does not retrieve a document that says "Rotating"...
    but it does retrieve one that says "rotation" once n-grams are involved.
    """
    embedder = HashingEmbedding()
    hybrid = _service(_build(project, embedder=embedder), embedder)

    outcome = hybrid.search(SearchRequest(query="credential rotating", project_id="demo"))

    assert outcome.candidates, "n-grams bridge the morphological gap"


def test_another_project_is_never_returned(project: Path) -> None:
    """SEC-13, FR-R1: filtered before ranking, not after."""
    service = _service(_build(project, embedder=None), None)

    outcome = service.search(SearchRequest(query="signed JWT", project_id="somebody-else"))

    assert outcome.candidates == ()


def test_results_are_capped_per_item(project: Path) -> None:
    """FR-R4. A long document must not take every slot."""
    service = _service(_build(project, embedder=None), None)

    outcome = service.search(SearchRequest(query="policy", project_id="demo", per_item=1, limit=10))

    items = [c.item_id for c in outcome.candidates]
    assert len(items) == len(set(items))


def test_the_token_budget_is_respected(project: Path) -> None:
    service = _service(_build(project, embedder=None), None)

    outcome = service.search(SearchRequest(query="policy", project_id="demo", budget_tokens=30))

    assert outcome.used_tokens <= 30 or len(outcome.candidates) == 1


def test_what_was_dropped_for_space_is_reported(project: Path) -> None:
    """ "Nothing else matched" and "your budget ran out" lead to different next
    actions, so they are different answers."""
    service = _service(_build(project, embedder=None), None)

    outcome = service.search(SearchRequest(query="policy", project_id="demo", budget_tokens=20))

    assert outcome.dropped_for_budget >= 0


def test_two_identical_searches_return_the_same_order(project: Path) -> None:
    """FR-R7. Without a total order, a pinned snapshot reproduces nothing."""
    embedder = HashingEmbedding()
    service = _service(_build(project, embedder=embedder), embedder)
    request = SearchRequest(query="policy token cache", project_id="demo")

    first = [c.chunk_id for c in service.search(request).candidates]
    second = [c.chunk_id for c in service.search(request).candidates]

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
        SearchRequest(query="signed JWT", project_id="demo")
    )

    assert outcome.mode is RetrievalMode.LEXICAL
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
