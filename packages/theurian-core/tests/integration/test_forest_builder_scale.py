"""RAPTOR forest cases that only a real build shows: three kinds, a
reclassification, and a name shared across tiers (ADR-0008).

Split out of ``test_forest_builder.py`` (already past 800 lines) so neither file
bloats. What lives here is what the *wiring* decides rather than what the pure
derivation does -- so every case drives the real ``index build`` through the CLI:

- **Kind reaches a Catalog.** ``index_builder`` reads ``kind`` off the revision's
  metadata and hands it to the forest as the Domain-tree discriminator.
  ``test_forest_builder.py`` never varies ``kind`` and never builds a Catalog
  node through the CLI, and ``test_forest_derivation.py`` builds the chunks
  by hand -- so the wiring that puts ``kind`` on the chunk is held by nothing.
- **Sensitivity follows the item, not the revision** (written RED). A revision is
  immutable, so a ``changeSensitivity`` moves the classification on the item
  while the revision keeps the label it was authored under. A build that reads
  the revision's label indexes stale authority (SEC-14).
- **A tree's name does not collide across tiers.** An ``itemId`` equal to a
  ``KnowledgeKind`` value makes a Document tree and a Domain tree share a scope
  and a discriminator, so only the joined ``node_type`` keeps their tree ids
  apart.

Real repositories, real index files, the real CLI under ``tmp_path`` with
``HOME`` and ``THEURIAN_DATA_DIR`` redirected -- nothing here reaches the
developer's machine, starts a daemon, or registers a service.
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

from theurian.application.project_service import ProjectPaths, read_active_index_pointer
from theurian.cli.main import app

pytestmark = pytest.mark.integration

runner = CliRunner()

PROJECT: Final = "demo"
DOCUMENT_LEVEL: Final = 1
DOMAIN_LEVEL: Final = 2
CATALOG_LEVEL: Final = 3


def _ulid(tag: str) -> str:
    value = f"01K1{tag}".ljust(26, "Z")
    assert len(value) == 26, f"{value!r} is not a 26-character ULID"
    assert not set(value) & set("ILOU"), f"{value!r} is not Crockford base32"
    return value


@dataclass(frozen=True, slots=True)
class Doc:
    """One knowledge item in a fixture corpus.

    ``item_id_override`` exists for the tree-name-collision case, which needs an
    ``itemId`` equal to a ``KnowledgeKind`` value -- something ``kind.slug`` can
    never produce.
    """

    slug: str
    kind: str = "architecture"
    namespace: str = "backend"
    status: str = "approved"
    sensitivity: str = "internal"
    item_id_override: str = ""

    @property
    def item_id(self) -> str:
        return self.item_id_override or f"{self.kind}.{self.slug}"

    @property
    def heading(self) -> str:
        return self.slug.replace("-", " ").title()

    @property
    def marker(self) -> str:
        return f"mk-{self.slug}-mk"


_SECTIONS: Final = (
    ("Tokens", "Every call carries a signed token issued by the gateway service."),
    ("Rotation", "Tokens rotate on restart and expire after one hour of idle time."),
    ("Revocation", "The quarantine ledger records every revoked token and its reason."),
)


def _body(doc: Doc) -> str:
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


def _reclassify(root: Path, item_id: str, sensitivity: str, *, tag: str) -> None:
    migration = f"""apiVersion: theurian.dev/v1
id: {_ulid(tag)}
createdAt: 2026-08-05T11:00:00+09:00
author: engineer@example.com
operations:
  - op: changeSensitivity
    itemId: {item_id}
    sensitivity: {sensitivity}
    reason: Reclassified after review
"""
    (root / f".theurian/migrations/{_ulid(tag)}-reclassify.yaml").write_text(
        migration, encoding="utf-8"
    )


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

    ``HOME`` is redirected beside ``THEURIAN_DATA_DIR`` because ``init`` shells
    out to ``git``; nothing here touches the developer's real home.
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


def _published_index(root: Path) -> Path:
    payload = read_active_index_pointer(ProjectPaths.of(root)).payload
    assert payload is not None, "the project must have a published index"
    return ProjectPaths.of(root).index_for(str(payload["indexBuildId"]))


def _built(root: Path, docs: Sequence[Doc], *args: str) -> Path:
    _write_corpus(root, docs)
    _must(root, "migrate", "apply")
    _must(root, "index", "build", *args)
    return _published_index(root)


def _rows(path: Path, sql: str) -> list[dict[str, Any]]:
    with closing(sqlite3.connect(path)) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(sql)]


def _nodes(path: Path) -> dict[str, dict[str, Any]]:
    return {str(row["node_id"]): row for row in _rows(path, "SELECT * FROM nodes")}


def _chunks(path: Path) -> dict[str, dict[str, Any]]:
    return {str(row["chunk_id"]): row for row in _rows(path, "SELECT * FROM chunks")}


def _sources(path: Path) -> dict[str, tuple[list[str], list[str]]]:
    sources: dict[str, tuple[list[str], list[str]]] = {
        node_id: ([], []) for node_id in _nodes(path)
    }
    for edge in _rows(path, "SELECT * FROM node_derivation"):
        chunk_ids, node_ids = sources[str(edge["node_id"])]
        if edge["source_chunk_id"] is not None:
            chunk_ids.append(str(edge["source_chunk_id"]))
        else:
            node_ids.append(str(edge["source_node_id"]))
    return sources


def _grounding_chunks(path: Path, node_id: str) -> set[str]:
    sources = _sources(path)
    seen: set[str] = set()
    frontier = [node_id]
    while frontier:
        chunk_ids, node_ids = sources[frontier.pop()]
        seen.update(chunk_ids)
        frontier.extend(node_ids)
    return seen


# -- kind reaches a Catalog --------------------------------------------------


def test_a_three_kind_corpus_builds_a_catalog_over_one_domain_per_kind(project: Path) -> None:
    """Three kinds in one scope earn three Domain nodes and one Catalog over them.

    ``kind`` is the Domain-tree discriminator, read off the revision's metadata
    and wired onto the chunk by ``index_builder``. Every integration fixture in
    ``test_forest_builder.py`` leaves ``kind`` at its default, so the suite has
    never built more than one Domain tree, nor a Catalog node, through the real
    CLI -- and ``test_forest_derivation.py`` constructs its chunks directly,
    never exercising that wiring. A build that dropped the kind (indexing every
    item under one empty discriminator) would collapse the three Domain trees
    into one, lose the Catalog entirely, and be seen by nothing.

    Three items per kind so each kind clears ``minChildrenPerSummary`` at the
    Document tier and the three Domain nodes clear it at the Catalog tier.
    """
    docs = [
        Doc(f"{kind}-{number}", kind=kind)
        for kind in ("architecture", "operations", "security")
        for number in range(3)
    ]

    index = _built(project, docs, "--raptor")

    nodes = _nodes(index)
    domains = [node_id for node_id, row in nodes.items() if row["level"] == DOMAIN_LEVEL]
    catalogs = [node_id for node_id, row in nodes.items() if row["level"] == CATALOG_LEVEL]
    assert len(domains) == 3, (
        f"expected one Domain node per kind, got {len(domains)} -- the kind reached the "
        f"forest as an empty discriminator and collapsed three trees into fewer"
    )
    assert len(catalogs) == 1, (
        "three Domain nodes must earn exactly one Catalog node; none means the kind "
        "never partitioned the Domain tier, so the third level was unreachable"
    )
    assert nodes[catalogs[0]]["node_type"] == "catalog"
    chunk_sources, node_sources = _sources(index)[catalogs[0]]
    assert chunk_sources == [], "a Catalog node summarises Domain nodes, not chunks"
    assert sorted(node_sources) == sorted(domains), (
        "the Catalog must stand on exactly the three Domain nodes"
    )


# -- sensitivity follows the item (written RED) ------------------------------


def test_a_reclassified_item_is_reindexed_at_its_new_sensitivity(project: Path) -> None:
    """After a ``changeSensitivity`` and a rebuild, the item's chunks and its
    forest node carry the new label, not the one the revision was written under.

    Sensitivity decides who may read the content (SEC-14) and is a component of
    the scope tuple a RAPTOR tree *is* (ADR-0008 decision 1). A ``revision`` is
    immutable, so its metadata records the classification in force when it was
    authored; the authority for what the content *is now* is the item, which
    ``changeSensitivity`` moves. A builder reading ``revision.metadata`` stamps
    every chunk and node with the stale label, so a document reclassified
    ``restricted`` is indexed, and would be returned, as ``internal``.

    The rebuild is explicit here, and that is the contract rather than a test
    convenience: a reclassification does *not* auto-rebuild the index, and does
    not need to -- the live response is item-authoritative the instant the
    migration commits, before any rebuild
    (``test_mcp_tools.py::test_a_reclassification_shows_in_the_response_before_any_rebuild``).
    What this pins is the other half: given an ``index build``, the wiring
    re-derives at the item's current label, so a document reclassified
    ``restricted`` is indexed as ``restricted`` and not the label the revision was
    authored under.

    **"Does not auto-rebuild" is not "does not touch the index", and this test
    used to cite a pin that said the second.** It named
    ``test_a_reclassification_is_not_a_withdrawal``, which #119 phase 5 deleted
    with the decision it held: a ``changeSensitivity`` moving an item *past* the
    ceiling the published build ran under withdraws it from this deployment, so
    ``migrate apply`` purges its rows out of the published build in the same
    command -- ``test_migration_engine.py``'s
    ``test_a_reclassification_is_a_withdrawal_only_past_the_builds_own_ceiling``,
    ADR-0025 part 2. What that purge never does is *re-derive*, which is why an
    ``index build`` is still what puts this item back with its new label -- and
    why the reclassification here purges nothing to begin with: the build the
    fixture publishes runs under a ceiling that admits ``restricted``, so the
    level moved but the deployment's disclosure of it did not.
    """
    docs = [Doc("auth-policy", sensitivity="internal")]
    first = _built(project, docs, "--raptor")
    assert {row["sensitivity"] for row in _chunks(first).values()} == {"internal"}
    assert {row["sensitivity"] for row in _nodes(first).values()} == {"internal"}, (
        "the fixture did not build a document node at the original sensitivity"
    )

    _reclassify(project, docs[0].item_id, "restricted", tag="WSEN")
    _must(project, "migrate", "apply")
    _must(project, "index", "build", "--raptor")
    rebuilt = _published_index(project)

    assert {row["sensitivity"] for row in _chunks(rebuilt).values()} == {"restricted"}, (
        "a reclassified item's chunks were reindexed at the revision's old sensitivity"
    )
    assert _nodes(rebuilt), "the rebuild derived no forest, so the node label is untested"
    assert {row["sensitivity"] for row in _nodes(rebuilt).values()} == {"restricted"}, (
        "a reclassified item's summary node still carries the label the revision was "
        "authored under -- sensitivity is the item's, not the revision's (SEC-14)"
    )


# -- a tree's name does not collide across tiers -----------------------------


def test_an_item_named_like_a_kind_keeps_its_tree_id_distinct(project: Path) -> None:
    """A Document tree over an item named ``architecture`` and the Domain tree
    over kind ``architecture`` in one scope must not share a ``tree_id``.

    ``architecture`` is at once a legal ``itemId`` and a ``KnowledgeKind`` value,
    so the two trees share their scope *and* their discriminator; the joined
    ``node_type`` in ``tree_identity`` is the only thing left to separate them.
    Drop it and the document node and the domain node mint one ``tree_id`` -- a
    silently merged forest. The unit pin lives in ``test_raptor_scope.py``; this
    is the same collision reached through a real build, which is the only place
    the ``itemId``/``kind`` coincidence is actually constructed.

    Two more items of the same kind bring the Domain tier above the threshold so
    a Domain node exists to collide with.
    """
    docs = [
        Doc("architecture", kind="architecture", item_id_override="architecture"),
        Doc("auth-policy", kind="architecture"),
        Doc("quota-policy", kind="architecture"),
    ]
    named_like_kind = docs[0]
    assert named_like_kind.item_id == named_like_kind.kind, (
        "the fixture must give one item an id equal to its kind, or the collision the "
        "node_type join prevents cannot form"
    )

    index = _built(project, docs, "--raptor")

    nodes = _nodes(index)
    chunks = _chunks(index)
    domain = next(node_id for node_id, row in nodes.items() if row["level"] == DOMAIN_LEVEL)
    document = next(
        node_id
        for node_id, row in nodes.items()
        if row["level"] == DOCUMENT_LEVEL
        and {chunks[c]["item_id"] for c in _grounding_chunks(index, node_id)}
        == {named_like_kind.item_id}
    )

    assert nodes[document]["tree_id"] != nodes[domain]["tree_id"], (
        "the document tree over item 'architecture' and the domain tree over kind "
        "'architecture' share a tree_id -- the node_type is not part of the identity"
    )
