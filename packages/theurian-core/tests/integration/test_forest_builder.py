"""The RAPTOR forest builder: what a build writes into the node tables (ADR-0008).

**Written RED, ahead of the builder**, against an API that did not exist:
`application/forest_builder.py`, `index build --raptor` and `IndexStore`'s
node-write methods all arrived to satisfy this file. That is history now — the
builder is in the tree and these tests are green against it — and it is recorded
because the shape of the file follows from it: each test names the surface it
wanted rather than describing it in prose.

What these tests hold, and why each one is not already held elsewhere:

- **Opt-in.** ADR-0008 decision 10 ships the forest off by default, so that
  turning it on is somebody's decision rather than the side effect of an
  upgrade. A build without `--raptor` must write *zero* node rows -- not "few",
  not "only cheap ones".
- **Scope isolation, over rows a builder actually wrote.** ADR-0008 decision 1's
  guarantee is structural: a node whose children disagree on any of the six
  components has no tree to belong to. `tests/unit/test_raptor_scope.py` holds
  that at the value type; nothing has ever held it over a real build, because
  until now every node row in the suite was inserted with raw SQL.
- **Determinism.** ADR-0008 decision 9's two-corpus equality is reachable only
  if tree derivation is a pure function of (surviving rows, scope,
  configuration). That is a property of *this* CL even though the equality test
  belongs to the purge-closure one: an id or a text that moves between two
  derivations of the same state makes the later test unwritable rather than
  merely red.
- **The disclosure ceiling, over both halves.** ADR-0025 part 1 is a statement
  about the builder *and* the forest derived from it, because a query-time
  predicate leaves the withheld text in four FTS5 tables whose collection
  statistics score everything else. This is where a build under a declared
  ceiling meets a real corpus and a real forest.
- **The purge, over derived rows.** `test_index_purge_nodes.py` proves universal
  grounding against hand-written fixtures. This is the first time the traversal
  meets a graph the builder shaped, which is also the re-check
  `test_index_schema_v4.py`'s `nodes_trigram` isolation tests are owed: those
  were written against a corpus in which nothing ever wrote a node row.

`tests/unit/test_forest_derivation.py` is the other half -- the levels, the
thresholds, the declared child scopes and the summary budget, all of which are
properties of a derivation rather than of a file.

Real repositories, real index files and the real CLI under `tmp_path`, with
`THEURIAN_DATA_DIR` and `HOME` redirected -- the pattern
`test_index_fallback.py` establishes. Nothing here reaches the developer's own
machine, and nothing here starts a daemon or registers a service.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from collections.abc import Iterator, Sequence
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import pytest
from migration_fixtures import body_pin
from typer.testing import CliRunner

from theurian.application.authorization import SERVING_PROFILE_FILENAME
from theurian.application.project_service import ProjectPaths, read_active_index_pointer
from theurian.cli.main import app
from theurian.domain.enums import KnowledgeStatus, Sensitivity
from theurian.domain.identifiers import ProjectId
from theurian.domain.values import AclGroup, ContentHash, Scope, TenantId
from theurian.infrastructure.embedding.hashing import HashingEmbedding
from theurian.infrastructure.raptor.extractive import ExtractiveSummarizer

pytestmark = pytest.mark.integration

runner = CliRunner()

PROJECT: Final = "demo"

#: The three node levels ADR-0008 decision 2 builds, numbered upward from the
#: leaves -- the numbering `index_schema.py`'s `CHECK (level BETWEEN 1 AND 3)`
#: already assumes, named here so a test can say which tier it means.
DOCUMENT_LEVEL: Final = 1
DOMAIN_LEVEL: Final = 2
CATALOG_LEVEL: Final = 3


# -- Fixture corpus ----------------------------------------------------------


def _ulid(tag: str) -> str:
    """A valid ULID literal for a fixture, padded to 26 characters.

    Crockford base32 excludes I, L, O and U.
    `tests/unit/test_test_fixtures.py` guards *quoted* 26-character literals for
    exactly that, and an id assembled at runtime slips past that guard -- so the
    charset is asserted here rather than assumed. ``Z`` is the pad because it is
    in the alphabet and sorts last, which keeps a longer tag ordering ahead of a
    shorter one that shares its prefix.
    """
    value = f"01K1{tag}".ljust(26, "Z")
    assert len(value) == 26, f"{value!r} is not a 26-character ULID"
    assert not set(value) & set("ILOU"), f"{value!r} is not Crockford base32"
    return value


@dataclass(frozen=True, slots=True)
class Doc:
    """One knowledge item in a fixture corpus, and the axes a test varies.

    ``kind`` and ``namespace`` are separate because ADR-0008 decision 2 keys a
    Domain tree by *both*, while ``namespace`` alone is already a component of
    the scope tuple -- so within one scope it is ``kind`` that decides which
    Domain tree a document belongs to.
    """

    slug: str
    kind: str = "architecture"
    namespace: str = "backend"
    status: str = "approved"
    sensitivity: str = "internal"
    #: Overrides the title derived from the slug. Only the duplicate-content
    #: test sets it, and it has to: `IndexBuilder` prepends the revision's title
    #: to its body before chunking, so two items whose *files* are byte-identical
    #: still produce different chunks -- and therefore different summaries --
    #: while their titles differ. Duplicate content means duplicate indexed text.
    title: str = ""
    #: Overrides the body derived from the slug. The same test sets it, and has
    #: to: the derived body carries a marker keyed on the slug, so two documents
    #: can never be byte-identical without it. Since ADR-0027 the migration pins
    #: the body, so overwriting the file after `_write_corpus` is refused at
    #: load -- the override reaches the pin because `_migration` and
    #: `_write_corpus` both read the body through `_body`.
    body: str = ""

    @property
    def item_id(self) -> str:
        return f"{self.kind}.{self.slug}"

    @property
    def heading(self) -> str:
        return self.title or self.slug.replace("-", " ").title()

    @property
    def marker(self) -> str:
        """A token that appears in every sentence of this document and no other.

        Delimited at both ends so no marker is a substring of another: without
        the trailing delimiter, ``auth-policy``'s marker would be found inside
        ``auth-policy-copy``'s text and a node built from the wrong children
        would pass the check that exists to catch exactly that.
        """
        return f"mk-{self.slug}-mk"


#: Three headed sections, each above `chunking.MIN_CHARS` and below the 1000
#: character target, so every document in these corpora splits into exactly
#: three chunks. That count is load-bearing rather than incidental: a level is
#: skipped below `minChildrenPerSummary`, so a two-chunk document would produce
#: no node at all and a corpus built from one could show nothing.
_SECTIONS: Final = (
    ("Tokens", "Every call carries a signed token issued by the gateway service."),
    ("Rotation", "Tokens rotate on restart and expire after one hour of idle time."),
    ("Revocation", "The quarantine ledger records every revoked token and its reason."),
)


def _body(doc: Doc) -> str:
    if doc.body:
        return doc.body
    sections = "\n\n".join(
        f"## {heading}\n\n" + f"{doc.marker} {sentence} " * 4 for heading, sentence in _SECTIONS
    )
    return f"{doc.heading}\n\n{sections}\n"


def _migration(doc: Doc, ordinal: int) -> str:
    return f"""apiVersion: theurian.dev/v1
id: {_ulid(f"M{ordinal:02d}")}
createdAt: 2026-08-05T10:00:00+09:00
author: engineer@example.com
operations:
  - op: createItem
    itemId: {doc.item_id}
    kind: {doc.kind}
    namespace: {doc.namespace}
    owner: platform-team
  - op: upsertRevision
    itemId: {doc.item_id}
    revisionId: {_ulid(f"R{ordinal:02d}")}
    contentFile: ../knowledge/{doc.kind}/{doc.slug}.md
    contentSha256: {body_pin(_body(doc))}
    metadata:
      title: {doc.heading}
      contentType: text/markdown
      kind: {doc.kind}
      namespace: {doc.namespace}
      status: {doc.status}
      sensitivity: {doc.sensitivity}
      owner: platform-team
      trustLevel: reviewed
      sourceAnchors:
        - provider: git
          sourceUri: git://demo/{doc.slug}.md
"""


def _write_corpus(root: Path, docs: Sequence[Doc]) -> None:
    for ordinal, doc in enumerate(docs):
        knowledge = root / ".theurian/knowledge" / doc.kind
        knowledge.mkdir(parents=True, exist_ok=True)
        (knowledge / f"{doc.slug}.md").write_text(_body(doc), encoding="utf-8")
        (root / f".theurian/migrations/{_ulid(f'M{ordinal:02d}')}-{doc.slug}.yaml").write_text(
            _migration(doc, ordinal), encoding="utf-8"
        )


# -- The project -------------------------------------------------------------


def _in(root: Path, *args: str) -> tuple[int, dict[str, Any]]:
    monkey = pytest.MonkeyPatch()
    monkey.chdir(root)
    try:
        result = runner.invoke(app, [*args, "--json"], catch_exceptions=False)
    finally:
        monkey.undo()
    stream = result.stdout if result.exit_code == 0 else (result.stderr or result.stdout)
    return result.exit_code, json.loads(stream) if stream.strip() else {}


def _must(root: Path, *args: str) -> dict[str, Any]:
    code, payload = _in(root, *args)
    assert code == 0, f"{' '.join(args)}: {payload}"
    return payload


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """An initialised, registered project with no knowledge and no index.

    ``HOME`` is redirected beside ``THEURIAN_DATA_DIR`` even though `index
    build` reads neither: this fixture shells out to `git`, and a test that
    reaches the developer's real home directory is a defect that surfaces
    somewhere else entirely.
    """
    root = tmp_path / "demo"
    root.mkdir()
    for args in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(args, cwd=root, check=True, capture_output=True)  # noqa: S603

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("THEURIAN_DATA_DIR", str(tmp_path / "datadir"))
    _must(root, "init")
    _must(root, "project", "register", "--project-id", PROJECT)
    yield root


def _built(root: Path, docs: Sequence[Doc], *args: str) -> Path:
    """Apply ``docs`` and build an index, returning the published build's file."""
    _write_corpus(root, docs)
    _must(root, "migrate", "apply")
    _must(root, "index", "build", *args)
    return _published_index(root)


def _published_index(root: Path) -> Path:
    payload = read_active_index_pointer(ProjectPaths.of(root)).payload
    assert payload is not None, "the project must have a published index"
    return ProjectPaths.of(root).index_for(str(payload["indexBuildId"]))


# -- Reading a built forest --------------------------------------------------


def _rows(path: Path, sql: str) -> list[dict[str, Any]]:
    """Every row of a query as plain dicts.

    ``closing`` rather than ``with sqlite3.connect(...)``: that context manager
    commits and does not close, and ``filterwarnings = error`` turns the leaked
    handle's ``ResourceWarning`` into a failure in whichever test happens to be
    running when it is collected.
    """
    with closing(sqlite3.connect(path)) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(sql)]


def _nodes(path: Path) -> dict[str, dict[str, Any]]:
    return {str(row["node_id"]): row for row in _rows(path, "SELECT * FROM nodes")}


def _chunks(path: Path) -> dict[str, dict[str, Any]]:
    return {str(row["chunk_id"]): row for row in _rows(path, "SELECT * FROM chunks")}


def _edges(path: Path) -> list[dict[str, Any]]:
    return _rows(path, "SELECT * FROM node_derivation")


def _sources(path: Path) -> dict[str, tuple[list[str], list[str]]]:
    """Per node: the chunk ids and the node ids its derivation edges name."""
    sources: dict[str, tuple[list[str], list[str]]] = {
        node_id: ([], []) for node_id in _nodes(path)
    }
    for edge in _edges(path):
        chunk_ids, node_ids = sources[str(edge["node_id"])]
        if edge["source_chunk_id"] is not None:
            chunk_ids.append(str(edge["source_chunk_id"]))
        else:
            node_ids.append(str(edge["source_node_id"]))
    return sources


def _grounding_chunks(path: Path, node_id: str) -> set[str]:
    """Every leaf chunk a node stands on, following node edges transitively.

    The set the scope-isolation claim is about: ADR-0008 decision 1 is a
    statement about the content a node's *text* was synthesized from, and at
    level 2 and above that content arrives through another node.
    """
    sources = _sources(path)
    seen: set[str] = set()
    frontier = [node_id]
    while frontier:
        chunk_ids, node_ids = sources[frontier.pop()]
        seen.update(chunk_ids)
        frontier.extend(node_ids)
    return seen


def _scope_of_chunk(chunk: dict[str, Any]) -> Scope:
    """The six-component scope a chunk row implies.

    A reference implementation stated independently of the builder's, so that a
    builder deriving scope some other way is a disagreement rather than a shared
    mistake. ``tenant_id`` and ``acl_group`` are the write-time enforced
    defaults: `migrate validate` and `migrate apply` refuse any other value
    until #119 lands an ``AuthorizationProvider``, so a chunk row cannot carry
    one.
    """
    return Scope(
        project_id=ProjectId(str(chunk["project_id"])),
        tenant_id=TenantId(),
        sensitivity=Sensitivity(str(chunk["sensitivity"])),
        acl_group=AclGroup(),
        namespace=str(chunk["namespace"]),
        status=KnowledgeStatus(str(chunk["status"])),
    )


# -- Opt-in, and what a forest build writes ----------------------------------


def test_a_build_without_the_raptor_flag_writes_no_summary_nodes(project: Path) -> None:
    """ADR-0008 decision 10: the forest ships off, so enabling it is a decision.

    A capability whose acceptance tests are owed and whose build cost is
    unmeasured must not arrive as the side effect of an upgrade. Zero rows
    rather than "a cheap forest": the point of the default is that an operator
    who never opts in has a hard guarantee, the way `--include-unapproved` gives
    one for drafts, rather than a filter someone is expected to remember.
    """
    docs = [Doc("auth-policy"), Doc("quota-policy")]

    index = _built(project, docs)

    assert _nodes(index) == {}, "a default build derived a forest nobody asked for"
    assert _edges(index) == [], "a default build wrote provenance for nodes it did not build"
    assert _chunks(index), "the fixture built no chunks either, so it shows nothing"


def test_the_raptor_flag_builds_one_document_node_per_item(project: Path) -> None:
    """The shape ADR-0008 decision 2 names at level 1: one tree per knowledge item.

    Two items of three chunks each, deliberately the smallest corpus that
    reaches level 1 and stops there: three chunks clear
    `minChildrenPerSummary`, and two document nodes do not, so a domain node
    here would be the "summary of one document" the ADR's Negative consequence
    rules out.
    """
    docs = [Doc("auth-policy"), Doc("quota-policy")]

    index = _built(project, docs, "--raptor")

    nodes = _nodes(index)
    chunks = _chunks(index)
    assert len(nodes) == 2, f"expected one document node per item, got {sorted(nodes)}"
    assert {row["level"] for row in nodes.values()} == {DOCUMENT_LEVEL}
    assert {row["node_type"] for row in nodes.values()} == {"document"}
    assert {frozenset(_grounding_chunks(index, node_id)) for node_id in nodes} == {
        frozenset(cid for cid, row in chunks.items() if row["item_id"] == doc.item_id)
        for doc in docs
    }


def test_a_document_nodes_provenance_names_the_revision_it_was_built_from(project: Path) -> None:
    """ADR-0008 decision 5's provenance columns, on rows a build actually wrote.

    `source_revision_id` is the one with teeth: `index_purge._DOOMED` dooms a
    node whose stamp names a withdrawn revision *whatever its edges still point
    at*, so a builder that left it empty would produce a forest the purge cannot
    retire by revision. `index_build_id` matters for the same reason one level
    up -- a node claiming a build it is not in is a provenance record that can
    be checked against nothing. `summary_prompt_hash` is what ADR-0008 decision
    5 compares to decide staleness, so a placeholder there makes every node
    permanently fresh.
    """
    docs = [Doc("auth-policy"), Doc("quota-policy")]

    index = _built(project, docs, "--raptor")

    chunks = _chunks(index)
    published = read_active_index_pointer(ProjectPaths.of(project)).payload
    assert published is not None
    assert _nodes(index), "no node was written, so no provenance can be wrong"
    for node_id, row in _nodes(index).items():
        revisions = {chunks[c]["revision_id"] for c in _grounding_chunks(index, node_id)}
        assert len(revisions) == 1, "the fixture put two revisions under one document node"
        assert row["source_revision_id"] == revisions.pop(), (
            "a document node's revision stamp must name the revision its chunks came from"
        )
        assert row["index_build_id"] == published["indexBuildId"]
        assert row["summary_model"] == ExtractiveSummarizer.model_id
        assert row["summary_model_revision"] == ExtractiveSummarizer.model_revision
        assert row["summary_prompt_hash"] == ExtractiveSummarizer.prompt_hash, (
            "the node must carry the identity of the provider that wrote it, not a "
            "placeholder -- ADR-0008 decision 5 decides staleness by comparing this"
        )
        assert row["text"], "a node with no text summarises nothing"
        assert row["content_hash"] == ContentHash.of_text(str(row["text"])).value
        assert row["project_id"] == PROJECT
        assert row["sensitivity"] == "internal"
        assert row["status"] == "approved"


def test_every_node_gets_a_vector_from_the_same_embedder_the_chunks_did(
    project: Path,
) -> None:
    """`node_embeddings` exists so a summary's vector has somewhere to live.

    `embeddings` is keyed on `chunk_id REFERENCES chunks` and a node id is not a
    chunk id, so v4 added the table ahead of any writer of it
    (`index_schema.py`). This is that writer, and embedding is not optional the
    moment a forest exists: a forest with no vectors is a forest dense retrieval
    can never reach -- the capability would exist, be reported, and answer
    nothing.

    A partial embedding is what `IndexBuilder._embed` already refuses for
    chunks, for the reason it gives: the dense retriever ranks the embedded half
    and silently never surfaces the rest, which reads as a relevance problem
    rather than a build problem. That refusal has to cover nodes too, so this
    asserts *every* node, not "some".

    `--no-embeddings` is the control. Without it, a builder that never embedded
    anything and a build that was asked not to would look the same.
    """
    docs = [Doc("auth-policy"), Doc("quota-policy")]

    index = _built(project, docs, "--raptor")

    nodes = _nodes(index)
    vectors = {str(row["node_id"]): row for row in _rows(index, "SELECT * FROM node_embeddings")}
    assert nodes, "no node was built"
    assert set(vectors) == set(nodes), (
        f"nodes without a vector: {sorted(set(nodes) - set(vectors))} -- dense retrieval "
        f"would rank the embedded ones and never surface these"
    )
    for node_id, row in nodes.items():
        assert vectors[node_id]["dimension"] == HashingEmbedding.dimension
        assert row["embedding_model"] == HashingEmbedding.model_id
        assert row["embedding_dimension"] == HashingEmbedding.dimension

    _must(project, "index", "build", "--raptor", "--no-embeddings")
    bare = _published_index(project)
    assert _nodes(bare), "the control must still derive a forest, or it shows nothing"
    assert _rows(bare, "SELECT * FROM node_embeddings") == [], (
        "`--no-embeddings` must reach the forest too, or the flag means half of what it says"
    )


def test_a_nodes_text_comes_from_its_own_children_and_no_others(project: Path) -> None:
    """The builder hands each node its own children, not the corpus.

    `ExtractiveSummarizer` "cannot state a fact the children do not contain"
    because it selects sentences verbatim, and `tests/unit/
    test_extractive_summarizer.py` pins that at the adapter. What is pinned here
    is the *call site*: a builder summarising the whole corpus per node would
    satisfy every provenance and scope column above while writing another
    item's text into this item's summary. Every sentence of every document
    carries a marker unique to it, so the markers present in a node's text name
    exactly the documents its text was drawn from.
    """
    docs = [Doc("auth-policy"), Doc("quota-policy"), Doc("cache-policy")]

    index = _built(project, docs, "--raptor")

    chunks = _chunks(index)
    by_item = {doc.item_id: doc.marker for doc in docs}
    assert _nodes(index), "no node was written"
    for node_id, row in _nodes(index).items():
        own = {by_item[str(chunks[c]["item_id"])] for c in _grounding_chunks(index, node_id)}
        present = {marker for marker in by_item.values() if marker in str(row["text"])}
        assert present, f"node {node_id} carries no source marker at all"
        assert present <= own, (
            f"node {node_id} emitted {sorted(present - own)}, from documents it is not built from"
        )


def test_rebuilding_the_same_state_produces_a_byte_identical_forest(project: Path) -> None:
    """ADR-0008 decision 9 rests on this, and the test that needs it cannot state it.

    A purged build's forest can equal one built from a corpus that never held
    the withdrawn rows only if tree derivation is a deterministic pure function
    of (surviving rows, scope, configuration). That equality test belongs to the
    purge-closure CL; the property belongs here, because a node id or a text
    that moves between two derivations of one unchanged state makes the later
    test unwritable rather than merely red.

    `index_build_id` is the one column allowed to differ: it names the build,
    and two builds are two builds. Everything else -- `node_id` included, per
    decision 9's insistence that a content-addressed id is what determinism plus
    stability across builds amounts to -- must be identical.
    """
    docs = [Doc("auth-policy"), Doc("quota-policy"), Doc("cache-policy")]

    first = _built(project, docs, "--raptor")
    _must(project, "index", "build", "--raptor")
    second = _published_index(project)
    assert first != second, "the second build must be its own file"

    def _comparable(path: Path) -> list[list[tuple[str, Any]]]:
        return sorted(
            sorted((key, value) for key, value in row.items() if key != "index_build_id")
            for row in _nodes(path).values()
        )

    def _shape(path: Path) -> list[list[tuple[str, Any]]]:
        return sorted(sorted(edge.items()) for edge in _edges(path))

    assert _comparable(first), "the fixture derived no forest, so the equality is vacuous"
    assert _comparable(first) == _comparable(second)
    assert _shape(first) == _shape(second)


# -- minChildrenPerSummary ---------------------------------------------------


def test_two_document_nodes_do_not_earn_a_domain_node(project: Path) -> None:
    """ADR-0008's Negative consequence: never a summary of one document.

    The threshold exists because a level with too few children produces a
    paraphrase, which costs tokens and adds nothing. Two documents in one
    namespace and kind is one below the default of three.
    """
    docs = [Doc("auth-policy"), Doc("quota-policy")]

    index = _built(project, docs, "--raptor")

    levels = [row["level"] for row in _nodes(index).values()]
    assert levels.count(DOCUMENT_LEVEL) == 2, "the fixture did not reach level 1 at all"
    assert DOMAIN_LEVEL not in levels, "two documents were summarised into a domain node"
    assert CATALOG_LEVEL not in levels


def test_three_document_nodes_earn_one_domain_node_over_exactly_those_three(
    project: Path,
) -> None:
    """The positive case, without which the test above passes against a builder
    that never builds a domain node at all.

    The catalog level is still absent, and for the same reason the domain level
    is absent above: one domain node is one child, below the threshold. That is
    the shallow-forest shape ADR-0024's purge cost argument rests on -- and
    `index_schema.py` records that no column enforces it, so it is a property of
    this builder or of nothing.
    """
    docs = [Doc("auth-policy"), Doc("quota-policy"), Doc("cache-policy")]

    index = _built(project, docs, "--raptor")

    nodes = _nodes(index)
    domain = [node_id for node_id, row in nodes.items() if row["level"] == DOMAIN_LEVEL]
    assert len(domain) == 1, f"expected exactly one domain node, got {domain}"
    assert CATALOG_LEVEL not in [row["level"] for row in nodes.values()], (
        "one domain node is below the threshold and must not be summarised into a catalog"
    )
    assert nodes[domain[0]]["node_type"] == "domain"

    chunk_sources, node_sources = _sources(index)[domain[0]]
    assert chunk_sources == [], "a domain node summarises document nodes, not chunks"
    assert sorted(node_sources) == sorted(
        node_id for node_id, row in nodes.items() if row["level"] == DOCUMENT_LEVEL
    )


# -- Six-component isolation, over rows the builder wrote --------------------


@pytest.mark.parametrize(
    ("axis", "docs", "flags"),
    [
        (
            "namespace",
            [Doc(f"doc-{n}", namespace="backend" if n < 3 else "frontend") for n in range(6)],
            (),
        ),
        (
            "sensitivity",
            [Doc(f"doc-{n}", sensitivity="internal" if n < 3 else "public") for n in range(6)],
            (),
        ),
        (
            "status",
            [Doc(f"doc-{n}", status="approved" if n < 3 else "draft") for n in range(6)],
            ("--include-unapproved",),
        ),
    ],
)
def test_no_node_stands_on_chunks_that_disagree_on_a_scope_component(
    project: Path, axis: str, docs: list[Doc], flags: tuple[str, ...]
) -> None:
    """ADR-0008 decision 1, held over a real build for the first time.

    `SummaryNode.__post_init__` refuses children whose declared scope disagrees
    with the node's own, and `tests/unit/test_raptor_scope.py` is exhaustive
    over the six components -- at the value type. That refusal never fires
    against a builder that declares its own scope n times, because there is then
    nothing for it to disagree with. What this asserts instead is the thing the
    declaration is *about*: every leaf chunk a node's text was synthesized from,
    reached transitively, agrees on all six components.

    The `status` axis is the one the Milestone 6 amendment added the component
    for, and it is the only axis reachable only under a build flavor: a default
    build holds no draft at all, so a scope-blind clusterer could never be
    caught by one. Three documents per value on every axis, so both sides clear
    `minChildrenPerSummary` and the corpus reaches level 2 -- a clusterer
    ignoring the axis would merge six document nodes into one domain node, which
    is the failure this exists to catch.
    """
    index = _built(project, docs, "--raptor", *flags)

    chunks = _chunks(index)
    assert len({str(row[axis]) for row in chunks.values()}) == 2, (
        f"the corpus does not vary {axis}, so isolation on it is untested"
    )

    nodes = _nodes(index)
    assert any(row["level"] == DOMAIN_LEVEL for row in nodes.values()), (
        "no node above level 1 was built, so a cross-scope cluster had no chance to form"
    )
    for node_id, row in nodes.items():
        scopes = {_scope_of_chunk(chunks[c]) for c in _grounding_chunks(index, node_id)}
        assert len(scopes) == 1, (
            f"node {node_id} stands on chunks from {len(scopes)} scopes, differing in {axis}"
        )
        scope = scopes.pop()
        assert row["project_id"] == scope.project_id.value
        assert row["sensitivity"] == scope.sensitivity.value
        assert row["status"] == scope.status.value


def test_nodes_in_different_scopes_never_share_a_tree(project: Path) -> None:
    """The other half of decision 1: a tree *is* the scope, so two scopes are two trees.

    Uniform children are not enough on their own. Two nodes whose children are
    each internally uniform can still carry one `tree_id` if the builder derived
    it from something coarser than the six-component tuple -- and `tree_id` is
    the only column on `nodes` that can express tenant, acl_group or namespace
    at all (`docs/architecture/raptor.md`, "Two gaps the tables leave open").
    """
    docs = [Doc(f"doc-{n}", namespace="backend" if n < 3 else "frontend") for n in range(6)]

    index = _built(project, docs, "--raptor")

    chunks = _chunks(index)
    trees: dict[str, set[Scope]] = {}
    for node_id, row in _nodes(index).items():
        trees.setdefault(str(row["tree_id"]), set()).update(
            _scope_of_chunk(chunks[c]) for c in _grounding_chunks(index, node_id)
        )

    assert len(trees) >= 2, "one tree over two scopes is what this rules out; build both"
    for tree_id, scopes in trees.items():
        assert len(scopes) == 1, f"tree {tree_id} spans {len(scopes)} scopes"


# -- Content-addressed node identity -----------------------------------------


def test_a_node_id_is_recomputable_from_its_tree_level_and_childrens_hashes(
    project: Path,
) -> None:
    """ADR-0008 decision 9's identity function, checked against the stored rows.

    "A deterministic function of (`tree_id`, level, the children's content
    hashes sorted lexicographically), joined with the same unit separator
    `Scope.key` uses and hashed." Both the sort and the encoding are part of the
    definition rather than implementation detail: a purge that rewrites a tree
    can produce the same children in a different physical order than the
    never-held build did, and an id that moved between the two would break the
    equality decision 9 rests on.

    The exact join and hex encoding are pinned against a literal in
    `tests/unit/test_raptor_scope.py`. What this adds is that the ids in a built
    index were produced by *that* function rather than by something else that is
    also deterministic -- a counter, a ULID, a hash of the text.
    """
    from theurian.domain.raptor import node_identity

    docs = [Doc("auth-policy"), Doc("quota-policy"), Doc("cache-policy")]

    index = _built(project, docs, "--raptor")

    chunks = _chunks(index)
    nodes = _nodes(index)
    assert len(nodes) == 4, "three document nodes and one domain node, or this says less"
    for node_id, row in nodes.items():
        chunk_ids, node_ids = _sources(index)[node_id]
        children = [ContentHash.of_text(str(chunks[c]["text"])) for c in chunk_ids]
        children += [ContentHash.of_text(str(nodes[n]["text"])) for n in node_ids]
        assert (
            node_identity(
                tree_id=ContentHash(str(row["tree_id"])),
                level=int(row["level"]),
                child_hashes=children,
            ).value
            == node_id
        )


def test_two_items_with_identical_content_get_different_document_node_ids(
    project: Path,
) -> None:
    """The descendant of the tree-id collision ADR-0008 decision 9 records.

    "`tree_id` for a Document tree includes the item's identity, without which
    two document trees holding duplicate content mint the same id for different
    nodes." Duplicate content is not exotic -- a copied runbook, a template, a
    document split in two -- and one id for two nodes is either a primary key
    violation at write time or a silently merged forest, depending on which
    insert runs second.

    The copy takes the original's *title* as well as its body. `IndexBuilder`
    prepends the title to the body before chunking, so two identical files under
    different titles are not identical content once indexed -- measured, their
    summaries differed by the one word, and the guard below said so.
    """
    original = Doc("auth-policy")
    docs = [
        original,
        Doc("auth-policy-copy", title=original.heading, body=_body(original)),
    ]
    _write_corpus(project, docs)
    _must(project, "migrate", "apply")
    _must(project, "index", "build", "--raptor")

    nodes = _nodes(_published_index(project))
    assert len({str(row["content_hash"]) for row in nodes.values()}) == 1, (
        "the two items no longer hold identical content, so the collision this rules "
        "out cannot occur in this fixture"
    )
    assert len(nodes) == 2, (
        f"expected one document node per item, got {sorted(nodes)} -- two nodes sharing "
        f"an id is a PRIMARY KEY collision, so one of them was never written"
    )
    assert len({str(row["tree_id"]) for row in nodes.values()}) == 2, (
        "two document trees over different items share a tree_id, which is the cause "
        "rather than a second symptom: identical children under one tree id are one id"
    )


# -- The canonical facts a chunk carries -------------------------------------


def test_a_chunks_namespace_carries_the_value_its_item_was_registered_with(
    project: Path,
) -> None:
    """`chunks.namespace` exists, is `NOT NULL DEFAULT ''`, and until this CL the
    builder never populated it -- `index_schema.py` says so in the column
    comment: "`namespace` is not even populated by the builder".

    It stops being cosmetic here. Namespace is a component of the scope tuple,
    so a forest built from rows whose namespace is uniformly empty partitions on
    five components while claiming six, and every isolation test above would
    pass over a corpus that had silently collapsed into one scope. Asserted on a
    *default* build, because the column belongs to the chunk rather than to the
    forest.
    """
    docs = [Doc("auth-policy", namespace="backend"), Doc("quota-policy", namespace="frontend")]

    index = _built(project, docs)

    assert {str(row["item_id"]): str(row["namespace"]) for row in _chunks(index).values()} == {
        docs[0].item_id: "backend",
        docs[1].item_id: "frontend",
    }


# -- The purge, over rows the builder wrote ----------------------------------


_WITHDRAWAL: Final = f"""apiVersion: theurian.dev/v1
id: {_ulid("WDEP")}
createdAt: 2026-08-05T11:00:00+09:00
author: engineer@example.com
operations:
  - op: deprecateItem
    itemId: architecture.auth-policy
    reason: retired after the forest was built
"""


def _withdraw(root: Path) -> Path:
    (root / f".theurian/migrations/{_ulid('WDEP')}-deprecate.yaml").write_text(
        _WITHDRAWAL, encoding="utf-8"
    )
    _must(root, "migrate", "apply")
    return _published_index(root)


def test_withdrawing_an_item_takes_its_document_node_and_the_domain_node_above_it(
    project: Path,
) -> None:
    """ADR-0024 decision 8's universal grounding, met by a graph the builder shaped.

    Every node row the suite has purged until now was written by raw SQL in the
    test that purged it, so the traversal has never seen the shape its own cost
    argument assumes. A withdrawal deletes the item's chunks; the document node
    standing on them is then ungrounded, and the domain node standing on *that*
    has an edge naming a row that is gone -- doomed by the same rule, one step
    up. A summary cannot be partially grounded any more than it can be partially
    withdrawn.

    The two unaffected document nodes must survive: a purge that took the whole
    forest would satisfy every assertion about the withdrawn item and destroy
    the property ADR-0024 was accepted on.
    """
    docs = [Doc("auth-policy"), Doc("quota-policy"), Doc("cache-policy")]
    before = _built(project, docs, "--raptor")

    chunks_before = _chunks(before)
    doomed = {
        node_id
        for node_id in _nodes(before)
        if any(
            chunks_before[c]["item_id"] == docs[0].item_id
            for c in _grounding_chunks(before, node_id)
        )
    }
    domain = {node_id for node_id, row in _nodes(before).items() if row["level"] == DOMAIN_LEVEL}
    assert len(doomed) == 2, (
        f"the fixture must give the withdrawn item a document node and a domain ancestor "
        f"before the purge, got {sorted(doomed)}"
    )
    assert domain and domain <= doomed, "the domain node must be one of the two"

    after = _withdraw(project)

    assert after != before, "a purge is a build: it must publish a new file (ADR-0024)"
    surviving = _nodes(after)
    assert not (doomed & set(surviving)), (
        f"{sorted(doomed & set(surviving))} survived a withdrawal it is built from"
    )
    assert len(surviving) == 2, (
        f"the two unaffected document nodes must survive, got {sorted(surviving)}"
    )


def _terms(path: Path, table: str) -> set[str]:
    """Every term an external-content FTS5 table currently indexes.

    `fts5vocab` reads the *index* rather than the content table, which is the
    point: a delete trigger that never fired leaves terms here with no row
    behind them, and querying `nodes` would not show it.
    """
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(f"CREATE VIRTUAL TABLE temp.v USING fts5vocab('main', '{table}', 'row')")
        return {str(row[0]) for row in connection.execute("SELECT term FROM temp.v")}


@pytest.mark.parametrize("table", ["nodes_fts", "nodes_trigram"])
def test_a_purged_forest_leaves_no_residue_in_a_node_text_index(project: Path, table: str) -> None:
    """The re-check `test_index_schema_v4.py`'s isolation tests are owed.

    `nodes_fts` and `nodes_trigram` are external-content FTS5 tables kept in
    step with `nodes` by trigger. Those triggers were added and tested against a
    corpus in which nothing ever wrote a node row, so "the node text indexes are
    isolated from the chunk ones" was, until this CL, a statement about two
    empty tables. Here the forest is real and the purge is real, and a withdrawn
    node's terms must be gone from both -- an external-content FTS5 table whose
    delete trigger never fired keeps answering for rowids that no longer exist.

    The withdrawn item's marker is the discriminator: it appears in every
    sentence of that document and in no other, so a term derived from it
    surviving in the index is residue and not coincidence.
    """
    docs = [Doc("auth-policy"), Doc("quota-policy"), Doc("cache-policy")]
    before = _built(project, docs, "--raptor")

    assert _nodes(before), "no node was indexed, so residue cannot be shown absent"
    indexed_before = _terms(before, table)
    assert indexed_before, f"{table} holds nothing -- its insert trigger never fired"
    gone = {term for term in indexed_before if term in docs[0].marker.lower()}
    assert gone, f"{table} never indexed the withdrawn document's marker"

    after = _withdraw(project)

    surviving = " ".join(str(row["text"]) for row in _nodes(after).values()).lower()
    indexed_after = _terms(after, table)
    assert indexed_after, f"{table} is empty after a purge that left nodes standing"
    for term in indexed_after:
        assert term in surviving, (
            f"{table} still indexes {term!r}, which no surviving node's text contains"
        )


# -- The disclosure ceiling, over both halves (ADR-0025 part 1) --------------


def _declare_a_ceiling(data_dir: Path, ceiling: Sensitivity) -> None:
    """Write the deployment serving profile ``theurian index build`` will read.

    Mode 0600 is not tidiness. ``load_serving_profile`` refuses a profile other
    local users can reach, and ``write_text`` under the usual umask leaves 0644 --
    so a test that skipped this would exercise the refusal rather than the
    ceiling, and would say "the build failed" while looking like a withholding.
    """
    auth = data_dir / "auth"
    auth.mkdir(parents=True, exist_ok=True)
    profile = auth / SERVING_PROFILE_FILENAME
    profile.write_text(f"{ceiling.value}\n", encoding="utf-8")
    profile.chmod(0o600)


def _only_from(withheld: Doc, visible: Sequence[Doc], terms: set[str]) -> set[str]:
    """The indexed terms that could only have come from ``withheld``.

    Written as "in that document's text and in no other document's" rather than
    as a token list, so it means the same thing for a word index and for a
    trigram one: `nodes_fts` indexes words while `chunks_trigram` indexes
    three-character sequences, and an assertion phrased in either one's units
    would silently pass over the other.
    """
    elsewhere = " ".join(_body(doc) for doc in visible).lower()
    body = _body(withheld).lower()
    return {term for term in terms if term in body and term not in elsewhere}


@pytest.mark.parametrize("table", ["chunks_fts", "chunks_trigram", "nodes_fts", "nodes_trigram"])
def test_an_above_ceiling_document_reaches_neither_half_of_the_index(
    project: Path, tmp_path: Path, table: str
) -> None:
    """ADR-0025 part 1's owed test, over both halves and all four text indexes.

    The ADR's compliance section recorded this as owed and said why it could not
    be written: ``IndexBuilder._build``'s only scope gate was status, and
    ``ForestBuilder.derive`` inherits whatever the builder wrote. Both halves have
    to participate, because a query-time predicate alone leaves the withheld text
    in FTS5 -- leaf text in `chunks_fts`/`chunks_trigram`, summary text in
    `nodes_fts`/`nodes_trigram` -- where it is scored against as collection
    statistics whether or not any query can return it (T-17a on the sensitivity
    axis). So the assertion is over the *index*, not over a response: no chunk
    row, no summary node, and no term that could only have come from the withheld
    document.

    Two builds of one corpus, which is what makes the absence mean anything. The
    first runs under a declared ``restricted`` ceiling, where every level is
    served: it pins that this document *is* indexable and that this table really
    does index a term unique to it -- without which the second build's silence
    would be the silence of a fixture nothing ever reached. The second runs under
    an ``internal`` ceiling. Both are declared the way an operator declares one,
    in the profile file beside the token, and both are the shipped path end to
    end: `theurian index build` reads it through the same provider the daemon
    serves through.

    **The control build declares its ceiling rather than inheriting the shipped
    default**, and it inherited it until the flip. Once ``DEFAULT_CEILING`` became
    ``internal``, an undeclared build was already the *gated* one: the control
    indexed nothing of the withheld document and the assertion below -- that the
    document is indexable at all -- went RED. What the control has to be is a
    build allowed to hold the row; which ceiling produces that is incidental, so
    it is now said out loud rather than borrowed from a constant that moved.

    The forest is asserted non-empty on both sides. A ceiling that took the whole
    forest with it would satisfy every absence below and destroy the capability.
    """
    visible = (Doc("auth-policy"), Doc("quota-policy"))
    withheld = Doc("payroll-bands", sensitivity="confidential")
    _write_corpus(project, (*visible, withheld))
    _must(project, "migrate", "apply")

    _declare_a_ceiling(tmp_path / "datadir", Sensitivity.RESTRICTED)
    _must(project, "index", "build", "--raptor")
    served_everything = _published_index(project)
    assert withheld.item_id in {
        str(row["item_id"]) for row in _chunks(served_everything).values()
    }, "a `restricted` ceiling must index a confidential document, or the control build is not one"
    assert _nodes(served_everything), "no forest was derived, so neither half can be shown empty"
    discriminating = _only_from(withheld, visible, _terms(served_everything, table))
    assert discriminating, (
        f"{table} indexed no term unique to the withheld document, so its absence below "
        f"would prove nothing about the ceiling"
    )

    _declare_a_ceiling(tmp_path / "datadir", Sensitivity.INTERNAL)
    _must(project, "index", "build", "--raptor")
    gated = _published_index(project)

    assert gated != served_everything, "the second build must have published a new file"
    assert _chunks(gated), "the gated build indexed nothing at all"
    assert withheld.item_id not in {str(row["item_id"]) for row in _chunks(gated).values()}, (
        "an item above the deployment's ceiling was written into `chunks`"
    )
    assert _nodes(gated), "the ceiling took the whole forest, not just the withheld document"
    assert not any(withheld.marker in str(row["text"]) for row in _nodes(gated).values()), (
        "a summary node was derived from an above-ceiling document's text"
    )
    assert not (discriminating & _terms(gated, table)), (
        f"{table} holds {sorted(discriminating & _terms(gated, table))}, terms that appear in "
        f"no document this build was allowed to write -- the withheld text is in the file's "
        f"collection statistics even though no row of it can be returned"
    )


# -- Reporting ---------------------------------------------------------------


def test_the_build_report_says_whether_it_derived_a_forest_and_how_large(
    project: Path,
) -> None:
    """A build that derives a forest and says nothing about it cannot be attributed.

    The forest is the expensive half -- ADR-0008 decision 3's amendment records
    that nothing has measured it -- and `index build --json` is the only surface
    reporting what a build did. Both fields matter: the count alone cannot tell
    a forest-free build apart from one whose corpus fell below every threshold,
    which is the same confusion `indexesUnapproved` exists to prevent for
    drafts.
    """
    docs = [Doc("auth-policy"), Doc("quota-policy")]
    _write_corpus(project, docs)
    _must(project, "migrate", "apply")

    on = _must(project, "index", "build", "--raptor")
    assert on["raptor"] is True
    assert on["nodes"] == len(_nodes(_published_index(project))) == 2

    off = _must(project, "index", "build")
    assert off["raptor"] is False
    assert off["nodes"] == 0
