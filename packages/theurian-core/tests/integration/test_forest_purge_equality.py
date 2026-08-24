"""The withdrawal purge re-derives the forest (ADR-0008 decision 9, ADR-0024).

**Written RED, ahead of the recompute.** The purge today is delete-only: a
withdrawal removes the withdrawn chunks and every node the surviving corpus can no
longer ground (``infrastructure/sqlite/index_purge.py``). ADR-0008 decision 9
rejects that and requires the opposite: withdrawal **re-derives each affected
tree from its surviving rows**, so a purged build's forest equals one built over a
corpus that never held the withdrawn rows -- the two-corpus equality
[ADR-0024](../../docs/adr/0024-a-purge-is-a-build.md) was accepted on, extended
from chunks to derived rows. ``test_a_purged_build_answers_as_if_the_rows_were_
never_indexed`` in ``test_index_purge.py`` holds that equality at the chunk
level; this file holds it for the forest.

Why delete-only is not the same thing, and why the failure is invisible without a
never-held corpus to compare against:

- **Clustering.** A Domain tree of four documents loses one to a withdrawal.
  Delete-only dooms the Domain node -- one of its edges names the withdrawn
  document node -- and removes it. A never-held corpus of the three survivors
  **builds** a Domain node over those three. The purged build is then *missing a
  node the never-held build has*, and content-addressing makes the survivor's
  node id and text move with the member set, so the survivor is not the same node
  either. This is the boundary ADR-0008 decision 9 names and the one that makes
  the equality test below RED.
- **Threshold.** A Domain tree of exactly three documents loses one. A never-held
  corpus of two skips the level -- ``minChildrenPerSummary`` is 3 -- and has no
  Domain node. Delete-only also removes it. The two *agree* here, which is why
  this boundary alone does not distinguish delete-only from a correct recompute;
  it distinguishes a **node-local recompute** (which would keep a two-child node
  and merely rewrite its text) from both, and ADR-0008 decision 9 rejects that
  too. The fixture exercises it so a node-local implementation is caught.

The equality target is reachable **iff tree derivation is a deterministic pure
function of (surviving rows, scope, configuration)** -- the extractive default
(``infrastructure/raptor/extractive.py``) is chosen for exactly that. A
non-deterministic provider -- none exists today -- falls back to deleting the
affected trees' nodes and recording the forest stale, which forfeits the equality
rather than faking it (ADR-0008 decision 9's final paragraphs). That fallback is a
distinct path, not the default, and this file does not exercise it with a fake
provider: doing so would test a wiring point the recompute CL has not yet built.

Real repositories, real index files and the real CLI under ``tmp_path``, with
``THEURIAN_DATA_DIR`` and ``HOME`` redirected **per project** -- the pattern
``test_forest_builder.py`` establishes, widened because the equality is *one query
against two corpora* and the two corpora are two projects that must nonetheless
agree on project id, item ids and revision ids for their trees to be comparable.
Nothing here reaches the developer's own machine, and nothing starts a daemon or
registers a service.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import pytest
from migration_fixtures import body_pin
from typer.testing import CliRunner

from theurian.application.project_service import ProjectPaths, read_active_index_pointer
from theurian.cli.main import app
from theurian.infrastructure.raptor.extractive import ExtractiveSummarizer

pytestmark = pytest.mark.integration

runner = CliRunner()

PROJECT: Final = "demo"

#: The three RAPTOR tiers, numbered upward from the leaves (ADR-0008 decision 2).
DOCUMENT_LEVEL: Final = 1
DOMAIN_LEVEL: Final = 2
CATALOG_LEVEL: Final = 3


# -- ULIDs and the fixture corpus --------------------------------------------


def _ulid(tag: str) -> str:
    """A valid ULID literal, padded to 26 Crockford base32 characters.

    Crockford base32 excludes I, L, O and U; ``tests/unit/test_test_fixtures.py``
    guards *quoted* 26-character literals for exactly that, and an id assembled at
    runtime slips past that guard -- so the charset is asserted here rather than
    assumed. ``Z`` is the pad because it is in the alphabet and sorts last.
    """
    value = f"01K1{tag}".ljust(26, "Z")
    assert len(value) == 26, f"{value!r} is not a 26-character ULID"
    assert not set(value) & set("ILOU"), f"{value!r} is not Crockford base32"
    return value


@dataclass(frozen=True, slots=True)
class Doc:
    """One knowledge item, with **content-stable identifiers keyed by ``code``**.

    The equality this file exists to hold compares two builds row by row, and a
    ``nodes`` row carries ``source_revision_id``. So a survivor that appears in
    both the withdrawn-corpus build and the never-held build has to carry the
    *same* revision id in each, or the rows would differ for a reason that is not
    the purge. Deriving the id from ``code`` -- a property of the document, not of
    its position in a corpus -- is what makes that hold across two projects.
    """

    slug: str
    code: str
    kind: str = "architecture"
    namespace: str = "backend"
    status: str = "approved"
    sensitivity: str = "internal"

    @property
    def item_id(self) -> str:
        return f"{self.kind}.{self.slug}"

    @property
    def heading(self) -> str:
        return self.slug.replace("-", " ").title()

    @property
    def marker(self) -> str:
        """A token in every sentence of this document and no other.

        Delimited at both ends so no marker is a substring of another, and unique
        per slug so two documents never derive the same chunk text -- which would
        collapse two content-addressed node ids into one.
        """
        return f"mk-{self.slug}-mk"

    @property
    def revision_id(self) -> str:
        return _ulid(f"R{self.code}")

    @property
    def migration_id(self) -> str:
        return _ulid(f"M{self.code}")


#: Three headed sections, each above ``chunking.MIN_CHARS`` and below the 1000
#: character target, so every document splits into exactly three chunks -- one
#: above ``minChildrenPerSummary``, so every document earns a Document node. Copied
#: from ``test_forest_builder.py`` because the count is load-bearing there and here
#: for the same reason: a two-chunk document earns no node and shows nothing.
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


def _migration(doc: Doc) -> str:
    return f"""apiVersion: theurian.dev/v1
id: {doc.migration_id}
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
    revisionId: {doc.revision_id}
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


def _withdrawal(docs: list[Doc], *, tag: str) -> str:
    """A single migration deprecating every item in ``docs``.

    Its id starts ``01K1W`` so it sorts after every create migration
    (``01K1M...``) and applies last -- an item must exist before it is deprecated.
    ``deprecateItem`` withholds the item at every build flavor, so the purge set is
    exactly these items regardless of ``--include-unapproved``.
    """
    ops = "".join(
        f"  - op: deprecateItem\n    itemId: {doc.item_id}\n"
        f"    reason: retired after the forest was built\n"
        for doc in docs
    )
    return (
        f"apiVersion: theurian.dev/v1\n"
        f"id: {_ulid(f'W{tag}')}\n"
        f"createdAt: 2026-08-05T11:00:00+09:00\n"
        f"author: engineer@example.com\n"
        f"operations:\n{ops}"
    )


def _write_corpus(root: Path, docs: list[Doc]) -> None:
    for doc in docs:
        knowledge = root / ".theurian/knowledge" / doc.kind
        knowledge.mkdir(parents=True, exist_ok=True)
        (knowledge / f"{doc.slug}.md").write_text(_body(doc), encoding="utf-8")
        (root / f".theurian/migrations/{doc.migration_id}-{doc.slug}.yaml").write_text(
            _migration(doc), encoding="utf-8"
        )


def _write_withdrawal(root: Path, docs: list[Doc], *, tag: str) -> None:
    (root / f".theurian/migrations/{_ulid(f'W{tag}')}-withdraw.yaml").write_text(
        _withdrawal(docs, tag=tag), encoding="utf-8"
    )


# -- A project, isolated in its own HOME and THEURIAN_DATA_DIR ----------------


@dataclass(frozen=True, slots=True)
class Project:
    """One initialised, registered project. Two of these are compared.

    Each carries its own ``home`` and ``datadir`` so that two projects registered
    under the same project id ``demo`` -- required for their trees to share a scope
    key -- do not collide in one per-user registry (``projects.json`` lives under
    ``THEURIAN_DATA_DIR``). The registry maps ``demo`` to *this* root in *this*
    datadir, and nothing crosses between the two.
    """

    root: Path
    home: Path
    datadir: Path


def _cli(project: Project, *args: str) -> tuple[int, dict[str, Any]]:
    """Invoke the real CLI inside ``project`` with its env redirected.

    ``HOME`` and ``THEURIAN_DATA_DIR`` are set for the duration of the call, never
    the developer's own -- the standing rule for anything that shells out (this
    fixture runs ``git``) or writes a registry.
    """
    monkey = pytest.MonkeyPatch()
    monkey.setenv("HOME", str(project.home))
    monkey.setenv("THEURIAN_DATA_DIR", str(project.datadir))
    monkey.chdir(project.root)
    try:
        result = runner.invoke(app, [*args, "--json"], catch_exceptions=False)
    finally:
        monkey.undo()
    stream = result.stdout if result.exit_code == 0 else (result.stderr or result.stdout)
    return result.exit_code, json.loads(stream) if stream.strip() else {}


def _must(project: Project, *args: str) -> dict[str, Any]:
    code, payload = _cli(project, *args)
    assert code == 0, f"{' '.join(args)}: {payload}"
    return payload


def _new_project(base: Path, name: str) -> Project:
    root = base / name
    root.mkdir()
    for command in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(command, cwd=root, check=True, capture_output=True)  # noqa: S603

    home = base / f"{name}-home"
    home.mkdir()
    project = Project(root=root, home=home, datadir=base / f"{name}-datadir")
    _must(project, "init")
    _must(project, "project", "register", "--project-id", PROJECT)
    return project


def _published_index(project: Project) -> Path:
    payload = read_active_index_pointer(ProjectPaths.of(project.root)).payload
    assert payload is not None, "the project must have a published index"
    return ProjectPaths.of(project.root).index_for(str(payload["indexBuildId"]))


def _build(project: Project, docs: list[Doc], *args: str) -> Path:
    """Apply ``docs`` and build an index, returning the published build's file."""
    _write_corpus(project.root, docs)
    _must(project, "migrate", "apply")
    _must(project, "index", "build", *args)
    return _published_index(project)


# -- Reading a forest into a comparable shape --------------------------------


def _rows(path: Path, sql: str) -> list[dict[str, Any]]:
    """Every row of a query as plain dicts.

    ``closing`` rather than ``with sqlite3.connect(...)``: that context manager
    commits and does not close, and ``filterwarnings = error`` turns the leaked
    handle's ``ResourceWarning`` into a failure in whichever test is running.
    """
    with closing(sqlite3.connect(path)) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(sql)]


@dataclass(frozen=True, slots=True)
class Forest:
    """One build's node graph, in a form two builds can be compared by value.

    ``index_build_id`` is dropped from every node row: it names the build, and two
    builds are two builds (``test_forest_builder.py`` drops the same column from
    its determinism comparison). Everything else -- ``node_id`` included, per
    ADR-0008 decision 9's insistence that a content-addressed id is what
    determinism across builds amounts to -- is compared. The derivation edges and
    the node vectors are compared too: the equality is over the *whole* derived
    layer, not the node text alone.
    """

    nodes: dict[str, tuple[tuple[str, Any], ...]]
    edges: tuple[tuple[Any, ...], ...]
    embeddings: dict[str, tuple[int, bytes]]

    #: The raw node rows, kept beside the comparable form so a test can ask about a
    #: level or a provider without re-reading the file.
    raw: dict[str, dict[str, Any]]

    def levels(self) -> list[int]:
        return [int(row["level"]) for row in self.raw.values()]

    def at_level(self, level: int) -> list[dict[str, Any]]:
        return [row for row in self.raw.values() if int(row["level"]) == level]


def _forest(path: Path) -> Forest:
    raw = {str(row["node_id"]): row for row in _rows(path, "SELECT * FROM nodes")}
    nodes = {
        node_id: tuple(
            sorted((key, value) for key, value in row.items() if key != "index_build_id")
        )
        for node_id, row in raw.items()
    }
    edges = tuple(
        sorted(
            (str(e["node_id"]), e["source_chunk_id"], e["source_node_id"])
            for e in _rows(path, "SELECT * FROM node_derivation")
        )
    )
    embeddings = {
        str(row["node_id"]): (int(row["dimension"]), bytes(row["vector"]))
        for row in _rows(path, "SELECT * FROM node_embeddings")
    }
    return Forest(nodes=nodes, edges=edges, embeddings=embeddings, raw=raw)


def _grounding_revisions(forest: Forest, node_id: str) -> set[str]:
    """Every leaf revision a node stands on, following node edges transitively.

    A Document node names its revision directly in ``source_revision_id``; a node
    above it reaches its leaves only through its ``source_node_id`` edges. So this
    walks the edges down to the Document nodes and collects their revision stamps
    -- the set that says *which items* a summary was synthesized from.
    """
    children: dict[str, list[str]] = {}
    for node, _chunk, source_node in forest.edges:
        if source_node is not None:
            children.setdefault(node, []).append(str(source_node))

    seen: set[str] = set()
    frontier = [node_id]
    while frontier:
        current = frontier.pop()
        row = forest.raw.get(current)
        if row is not None and row["source_revision_id"]:
            seen.add(str(row["source_revision_id"]))
        frontier.extend(children.get(current, []))
    return seen


# -- The corpora -------------------------------------------------------------

#: One scope (namespace ``backend``, sensitivity ``internal``, status
#: ``approved``), four Domain trees keyed by ``kind`` (ADR-0008 decision 2). The
#: shape is chosen so one withdrawal exercises every boundary the ADR names:
#:
#: - **architecture** has four documents; withdrawing one leaves three -- a
#:   Domain node the never-held corpus *builds* and delete-only *deletes*
#:   (clustering);
#: - **testing** has three; withdrawing one leaves two -- a level the never-held
#:   corpus *skips* (threshold);
#: - **decision** and **security** have three each and lose nothing -- unaffected
#:   Domain trees in the *affected scope*, which the whole-scope re-derivation must
#:   reproduce byte-for-byte;
#: - three surviving Domain nodes earn a **Catalog** node, which delete-only
#:   destroys (its edges name the withdrawn kinds' Domain nodes) and the never-held
#:   corpus rebuilds over the three that remain.
_ARCHITECTURE = [Doc(f"arch-{n}", code=f"A{c}", kind="architecture") for n, c in enumerate("ABCD")]
_DECISION = [Doc(f"dec-{n}", code=f"B{c}", kind="decision") for n, c in enumerate("ABC")]
_SECURITY = [Doc(f"sec-{n}", code=f"C{c}", kind="security") for n, c in enumerate("ABC")]
_TESTING = [Doc(f"tst-{n}", code=f"D{c}", kind="testing") for n, c in enumerate("ABC")]

_ALL = [*_ARCHITECTURE, *_DECISION, *_SECURITY, *_TESTING]
#: The two withdrawn items: one from the four-document kind (clustering) and one
#: from a three-document kind (threshold).
_WITHDRAWN = [_ARCHITECTURE[0], _TESTING[0]]
_SURVIVORS = [doc for doc in _ALL if doc not in _WITHDRAWN]


# -- The owed test: two corpora, one forest ----------------------------------


def test_a_purged_forest_equals_one_that_never_held_the_withdrawn_rows(tmp_path: Path) -> None:
    """ADR-0008 decision 9's two-corpus equality, realised for the derived layer.

    The milestone deliverable. A build that held the withdrawn rows and had them
    purged must produce a forest **identical** -- node rows, derivation edges and
    node vectors -- to a build over a corpus that never held them. Delete-only
    cannot: it removes the affected Domain and Catalog nodes rather than
    re-deriving them over the survivors, so the purged build is missing nodes the
    never-held build has. That is the leak T-17a is about, one tier up -- a
    ``raptorPath.nodeId`` that a stale forest would still route to.

    The ``stale`` control -- the pre-purge build, which still holds the withdrawn
    rows -- is asserted *different* from the never-held build, so the equality is
    not satisfied by two forests that happen to agree for some unrelated reason.
    """
    withheld = _new_project(tmp_path, "with-withdrawn")
    stale = _forest(_build(withheld, _ALL, "--raptor"))

    _write_withdrawal(withheld.root, _WITHDRAWN, tag="D1")
    _must(withheld, "migrate", "apply")
    purged = _forest(_published_index(withheld))

    never_held = _new_project(tmp_path, "never-held")
    fresh = _forest(_build(never_held, _SURVIVORS, "--raptor"))

    # The fixture must actually reach both tiers a delete-only purge damages, or
    # the equality below would hold vacuously against a shallow forest.
    assert fresh.at_level(CATALOG_LEVEL), "the never-held corpus built no catalog node"
    rebuilt = [
        node_id
        for node_id, row in fresh.raw.items()
        if int(row["level"]) == DOMAIN_LEVEL
        and _grounding_revisions(fresh, node_id) == {doc.revision_id for doc in _ARCHITECTURE[1:]}
    ]
    assert len(rebuilt) == 1, "the never-held corpus did not rebuild the clustered Domain node"

    assert stale.nodes != fresh.nodes, (
        "the pre-purge build equals the never-held one, so the equality proves nothing"
    )
    assert purged.nodes == fresh.nodes, "a purged forest must equal one that never held the rows"
    assert purged.edges == fresh.edges, "the derivation edges must match too"
    assert purged.embeddings == fresh.embeddings, "the node vectors must match too"


# -- Clustering, isolated: the Domain node is rebuilt, not deleted -----------


def test_a_withdrawal_rebuilds_a_domain_node_over_its_surviving_children(tmp_path: Path) -> None:
    """The clustering boundary on its own -- the crisp face of the equality's RED.

    A Domain tree of four documents loses one. A never-held corpus of three builds
    a Domain node over them; delete-only dooms the four-child node -- one edge
    names the withdrawn document node -- and removes it, so the purged build has
    **no** Domain node. Content-addressing means the three-child node is not the
    old node minus a child either: its id and text are a function of its member
    set. This asserts the purged build has a Domain node standing on exactly the
    three survivors, which delete-only cannot satisfy.
    """
    withheld = _new_project(tmp_path, "clustering")
    _build(withheld, _ARCHITECTURE, "--raptor", "--no-embeddings")

    _write_withdrawal(withheld.root, [_ARCHITECTURE[0]], tag="C1")
    _must(withheld, "migrate", "apply")
    purged = _forest(_published_index(withheld))

    survivors = {doc.revision_id for doc in _ARCHITECTURE[1:]}
    domain_nodes = [row for row in purged.raw.values() if int(row["level"]) == DOMAIN_LEVEL]
    rebuilt = [
        node_id
        for node_id, row in purged.raw.items()
        if int(row["level"]) == DOMAIN_LEVEL and _grounding_revisions(purged, node_id) == survivors
    ]
    assert len(rebuilt) == 1, (
        "the purged build must re-derive the Domain node over its three surviving children, "
        f"got {len(domain_nodes)} Domain node(s)"
    )
    assert purged.raw[rebuilt[0]]["node_type"] == "domain"


# -- Threshold: the level is skipped, and the parent does not linger ----------


def test_a_withdrawal_below_threshold_leaves_no_domain_node(tmp_path: Path) -> None:
    """The threshold boundary -- the case a node-local recompute gets wrong.

    A Domain tree of exactly three documents loses one; two survive, below
    ``minChildrenPerSummary`` (3), so the never-held corpus skips the level. A
    correct re-derivation reproduces that absence. A **node-local recompute** --
    which ADR-0008 decision 9 rejects alongside delete-only -- would keep a
    two-child Domain node and merely rewrite its text, which is the summary of one
    or two children the ADR's Negative consequence rules out. Delete-only agrees
    with the never-held corpus here, so this does not distinguish it; it is the
    guard that catches node-local recompute, and it must go on holding.
    """
    withheld = _new_project(tmp_path, "threshold")
    _build(withheld, _TESTING, "--raptor", "--no-embeddings")

    _write_withdrawal(withheld.root, [_TESTING[0]], tag="T1")
    _must(withheld, "migrate", "apply")
    purged = _forest(_published_index(withheld))

    assert purged.at_level(DOCUMENT_LEVEL), "the purged build lost the surviving Document nodes"
    assert DOMAIN_LEVEL not in purged.levels(), (
        "two surviving documents earned a Domain node, which is a summary of two children"
    )
    survivors = {doc.revision_id for doc in _TESTING[1:]}
    grounded = {rev for node_id in purged.raw for rev in _grounding_revisions(purged, node_id)}
    assert grounded == survivors, "a withdrawn revision still grounds a surviving node"


# -- An unaffected scope is untouched ----------------------------------------


def test_a_withdrawal_in_one_scope_leaves_another_scopes_forest_byte_identical(
    tmp_path: Path,
) -> None:
    """The re-derivation is bounded to the affected scope (ADR-0008 decision 9).

    "Affected" is the ancestor closure of the withdrawn rows, and no other scope is
    touched. Two scopes here -- ``backend`` and ``frontend``, differing in the
    namespace component of the scope tuple (ADR-0008 decision 1) -- and the
    withdrawal falls only in ``backend``. Every ``frontend`` node row must be
    byte-identical before and after: its ids, texts and provenance columns are a
    function of rows the withdrawal never removed. Byte-identity holds whether an
    unaffected scope is copied or re-derived, because derivation is deterministic;
    what this rules out is a re-derivation that reaches across the scope boundary
    and disturbs a tree it had no business rebuilding.
    """
    frontend = [
        Doc(f"fe-{n}", code=f"F{c}", kind="architecture", namespace="frontend")
        for n, c in enumerate("ABC")
    ]
    withheld = _new_project(tmp_path, "two-scopes")
    before = _forest(_build(withheld, [*_ARCHITECTURE, *frontend], "--raptor", "--no-embeddings"))

    frontend_before = {
        node_id: row
        for node_id, row in before.nodes.items()
        if _grounding_revisions(before, node_id) <= {doc.revision_id for doc in frontend}
    }
    assert frontend_before, "the fixture built no frontend nodes, so there is nothing to preserve"

    _write_withdrawal(withheld.root, [_ARCHITECTURE[0]], tag="S1")
    _must(withheld, "migrate", "apply")
    after = _forest(_published_index(withheld))

    frontend_after = {
        node_id: row
        for node_id, row in after.nodes.items()
        if _grounding_revisions(after, node_id) <= {doc.revision_id for doc in frontend}
    }
    assert frontend_after == frontend_before, (
        "a withdrawal in the backend scope disturbed the frontend scope's forest"
    )


# -- A chunk-only index takes no forest path ---------------------------------


def test_a_withdrawal_against_a_chunk_only_index_touches_no_node_tables(tmp_path: Path) -> None:
    """The recompute fires only when a forest is present (orchestrator-settled).

    A build made without ``--raptor`` holds zero node rows, so there is no forest
    to re-derive. The withdrawal must do exactly today's chunk purge -- the
    withdrawn item's chunks gone, the survivors' chunks intact -- and leave the
    node tables empty rather than taking a recompute path that has nothing to
    stand on. A guard so an "always re-derive" implementation does not run, or
    fail, over a chunk-only index.
    """
    withheld = _new_project(tmp_path, "chunk-only")
    _build(withheld, _ARCHITECTURE, "--no-embeddings")

    _write_withdrawal(withheld.root, [_ARCHITECTURE[0]], tag="K1")
    _must(withheld, "migrate", "apply")
    index = _published_index(withheld)

    assert _rows(index, "SELECT * FROM nodes") == [], "a chunk-only purge wrote node rows"
    assert _rows(index, "SELECT * FROM node_derivation") == [], "a chunk-only purge wrote edges"
    revisions = {
        str(row["revision_id"]) for row in _rows(index, "SELECT DISTINCT revision_id FROM chunks")
    }
    assert _ARCHITECTURE[0].revision_id not in revisions, "the withdrawn chunk survived the purge"
    assert {doc.revision_id for doc in _ARCHITECTURE[1:]} <= revisions, (
        "the chunk purge removed a surviving revision"
    )


# -- The default re-derivation uses the deterministic extractive provider -----


def test_the_re_derived_forest_carries_the_extractive_default_identity(tmp_path: Path) -> None:
    """The recompute runs the deterministic extractive default (ADR-0008 decision 9).

    The equality target is reachable only for a deterministic pure provider, and
    the extractive default is that provider. A re-derived node therefore carries
    the extractive provider's identity in the three provenance columns ADR-0008
    decision 5 decides staleness by -- ``summary_model``, ``summary_model_
    revision``, ``summary_prompt_hash``. This asserts it on the *rebuilt* Domain
    node, which exists only once the recompute runs; delete-only leaves no such
    node, so this is RED until the recompute lands.

    The non-deterministic fallback -- delete the affected trees' nodes and record
    the forest stale -- is a **distinct path, not the default**, and forfeits this
    equality rather than faking it. It has no provider to exercise it today; a fake
    would test a wiring point that does not yet exist, so it is documented here and
    left for the CL that builds the branch.
    """
    withheld = _new_project(tmp_path, "extractive-default")
    _build(withheld, _ARCHITECTURE, "--raptor", "--no-embeddings")

    _write_withdrawal(withheld.root, [_ARCHITECTURE[0]], tag="E1")
    _must(withheld, "migrate", "apply")
    purged = _forest(_published_index(withheld))

    survivors = {doc.revision_id for doc in _ARCHITECTURE[1:]}
    rebuilt = [
        row
        for node_id, row in purged.raw.items()
        if int(row["level"]) == DOMAIN_LEVEL and _grounding_revisions(purged, node_id) == survivors
    ]
    assert rebuilt, "no re-derived Domain node, so the provider it ran cannot be checked"
    row = rebuilt[0]
    assert row["summary_model"] == ExtractiveSummarizer.model_id
    assert row["summary_model_revision"] == ExtractiveSummarizer.model_revision
    assert row["summary_prompt_hash"] == ExtractiveSummarizer.prompt_hash, (
        "the re-derived node must carry the deterministic provider's identity, not a placeholder"
    )


# -- Determinism of the recompute --------------------------------------------


def test_the_re_derived_purge_is_byte_identical_across_two_runs(tmp_path: Path) -> None:
    """Two applications of one withdrawal produce one forest (ADR-0008 decision 9).

    The equality across two *corpora* rests on determinism across two *runs*: a
    node id or a text that moved between two purges of the same state would make
    the corpus equality unwritable rather than merely red. Two independent
    projects apply the same corpus and the same withdrawal; the purged forests must
    be identical, ``index_build_id`` aside. The presence of a re-derived Domain
    node is asserted too, so this fails now (delete-only leaves none) rather than
    passing on two empty agreements.
    """
    first = _new_project(tmp_path, "determinism-a")
    _build(first, _ARCHITECTURE, "--raptor")
    _write_withdrawal(first.root, [_ARCHITECTURE[0]], tag="M1")
    _must(first, "migrate", "apply")
    one = _forest(_published_index(first))

    second = _new_project(tmp_path, "determinism-b")
    _build(second, _ARCHITECTURE, "--raptor")
    _write_withdrawal(second.root, [_ARCHITECTURE[0]], tag="M1")
    _must(second, "migrate", "apply")
    two = _forest(_published_index(second))

    assert DOMAIN_LEVEL in one.levels(), "the recompute produced no Domain node to be stable"
    assert one.nodes == two.nodes, "two purges of one state produced different node rows"
    assert one.edges == two.edges, "two purges of one state produced different edges"
    assert one.embeddings == two.embeddings, "two purges of one state produced different vectors"
