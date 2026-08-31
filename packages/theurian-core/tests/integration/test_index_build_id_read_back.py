"""``add_nodes`` reads ``index_metadata.index_build_id`` back (ADR-0024 decision 2, #426).

ADR-0024 decision 2 used to say **nothing in ``src/`` reads
``index_metadata.index_build_id`` or ``built_at`` back**, which made a purge copy
inheriting its parent's identity "latent rather than broken". #426 split that per
column, because the reader it predicted has arrived and it arrived *inside the
purge*: ``SqliteIndexStore.add_nodes`` selects the column out of the file it is
writing into, to stamp each summary node with the build it belongs to.

**Nothing asserted that.** ``test_index_purge_nodes.py::test_restamp_updates_
survivors_index_build_id_too`` covers the ``nodes`` half -- that ``_restamp``
repairs a survivor whose row still names the build it was copied from -- and
``test_index_purge.py::test_a_purged_build_names_itself_in_its_own_metadata``
covers the ``index_metadata`` half. Neither exercises the *link* between them,
which is the sentence the ADR now carries: that ``add_nodes`` takes the id from
the file rather than from its caller. A build that hardcoded the stamp, or took it
as an argument, passed both.

**Why the link matters rather than being an implementation detail.**
``purge_into`` runs the forest recompute *before* ``_restamp``, so the nodes a
``--raptor`` purge writes are stamped with the parent's id and ``_restamp``'s
``UPDATE nodes SET index_build_id`` is what repairs them. If ``add_nodes`` stopped
reading the file, the two statements would be repairing a stamp that no longer
came from anywhere consistent, and the disagreement ADR-0022 and ADR-0024 exist to
prevent -- a file whose own record of itself disagrees with the pointer naming it
-- would reappear one level down, in a column nothing serves and therefore nothing
would report.

**What these tests enforce.** That the value written into ``nodes.index_build_id``
is whatever ``index_metadata`` says at the moment of the write, that a file with no
``index_metadata`` row is refused with a sentence rather than an
``AttributeError`` from the middle of a batch, and that the id is not a parameter
a caller could disagree with. The first is the sharp one: it sets the metadata row
to a build id the caller never mentions, so an implementation taking the value
from anywhere else -- a constant, an argument, the path -- writes a different
value and reddens.

**What they do not enforce.** They say nothing about ``built_at``, whose claim is
an absence and is held by
``tests/unit/test_index_metadata_claims.py``, and nothing about *when* the purge
runs the recompute relative to the restamp -- that ordering is
``test_forest_purge_recompute.py``'s and ``test_index_purge_nodes.py``'s.

The two behaviour tests use real SQLite files under ``tmp_path`` and a real
``ForestBuilder`` over a real ``ExtractiveSummarizer``. The signature pin reads
no database at all: it parses every ``add_nodes`` the imported package declares
-- three at ``5a14145``, the ``SqliteIndexStore`` adapter plus the ``IndexStore``
and ``ForestRecomputeStore`` protocols -- so that a fourth declaration is covered
by the change that writes it rather than by whoever remembers to add it here.
Nothing reaches the developer's machine, nothing touches the network, and nothing
starts a daemon.
"""

from __future__ import annotations

import ast
import pathlib
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Final

import pytest

import theurian
from theurian.application.forest_builder import ForestBuilder
from theurian.domain.chunking import Chunk, IndexableChunk
from theurian.domain.raptor import IndexableNode
from theurian.infrastructure.raptor.extractive import ExtractiveSummarizer
from theurian.infrastructure.sqlite.index_store import IndexBuildError, SqliteIndexStore

pytestmark = pytest.mark.integration

PROJECT: Final = "demo"

#: The package as *imported*, the reckoning the structural test modules use: a
#: hand-built relative path can drift from the installed package and would then
#: walk a directory with no declaration in it whatever the source did.
SRC: Final = pathlib.Path(theurian.__file__).resolve().parent


def _parameter_names(function: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[str, ...]:
    """Every name ``function`` binds from its call, in every argument role."""
    arguments = function.args
    names = [
        argument.arg
        for argument in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs)
    ]
    names.extend(
        argument.arg for argument in (arguments.vararg, arguments.kwarg) if argument is not None
    )
    return tuple(names)


def _add_nodes_declarations() -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Every ``add_nodes`` declared in the imported package, as ``(label, parameters)``.

    Derived rather than transcribed, which is the whole point of it. The hand-written
    pair this replaced named the adapter and the port and missed
    ``withdrawal_purge.ForestRecomputeStore.add_nodes`` -- the protocol the purge
    holds its building-file store as, and so the declaration closest to the caller
    ADR-0024 decision 2's clause is about.

    Class-scoped only: a module-level ``def add_nodes`` would be a free function
    nothing in this design has, and including one would give the pin below a label
    with no class to name.
    """
    found: list[tuple[str, tuple[str, ...]]] = []
    for path in sorted(SRC.rglob("*.py")):
        module = path.relative_to(SRC).as_posix()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.ClassDef):
                continue
            found.extend(
                (f"{module}::{node.name}.{member.name}", _parameter_names(member))
                for member in node.body
                if isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef)
                and member.name == "add_nodes"
            )
    return tuple(sorted(found))


#: Every ``add_nodes`` the shipped package declares, adapter and protocol alike.
ADD_NODES_DECLARATIONS: Final = _add_nodes_declarations()

#: The build the file is created as -- the identity a `Connection.backup` copy
#: inherits from its parent.
PARENT_BUILD: Final = "01K1000000000000000000PRNT"

#: The build the file is restamped to. Written into `index_metadata` directly and
#: never passed to `add_nodes`, which is the whole mechanism under test: a node
#: can only carry this value by having been read out of the file.
RESTAMPED_BUILD: Final = "01K1000000000000000000RSTM"

#: Three documents of three chunks each: at the default
#: `min_children_per_summary` of 3, three chunks earn a Document node and three
#: Document nodes earn a Domain node, so the forest has more than one tier and the
#: stamp is asserted over nodes at more than one level.
REVISIONS: Final = (
    "01K1000000000000000000AB1",
    "01K1000000000000000000CD2",
    "01K1000000000000000000EF3",
)


def _chunks() -> list[IndexableChunk]:
    """A small corpus whose documents are textually distinct.

    The per-document token keeps two documents from deriving identical summary
    text, which would collapse two content-addressed node ids into one and let an
    assertion over "every node" hold for the wrong reason.
    """
    return [
        IndexableChunk(
            chunk=Chunk(
                chunk_id=f"{revision}#{ordinal}",
                ordinal=ordinal,
                text=(
                    f"Document {index} section {ordinal}. The gateway issues token "
                    f"m{index}s{ordinal} on restart. Rotation expires hourly."
                ),
                heading="Section",
            ),
            project_id=PROJECT,
            item_id=f"architecture.doc-{index}",
            revision_id=revision,
            served_content_sha256=f"body-of-{revision}",
            status="approved",
            sensitivity="internal",
            trust_level="reviewed",
            namespace="backend",
            kind="architecture",
        )
        for index, revision in enumerate(REVISIONS)
        for ordinal in range(3)
    ]


@pytest.fixture
def forest() -> list[IndexableNode]:
    """A real derived forest, from the real builder over the real summarizer.

    Built once per test rather than hand-assembled, because an ``IndexableNode``
    written by hand can carry a shape the builder never produces -- and what is
    under test is what ``add_nodes`` does to nodes the product actually makes.
    """
    nodes = ForestBuilder(summarizer=ExtractiveSummarizer()).derive(_chunks())

    assert nodes, "the builder derived no nodes, so the stamp assertions would be vacuous"
    assert len({node.level for node in nodes}) > 1, (
        "the derived forest has a single tier, so the stamp is asserted over one "
        "level rather than over the tree this corpus is sized to produce"
    )
    return list(nodes)


@pytest.fixture
def store(tmp_path: Path) -> SqliteIndexStore:
    """An index created as ``PARENT_BUILD`` with the corpus written into it."""
    store = SqliteIndexStore(tmp_path / "theurian-index-parent.sqlite")
    store.create(index_build_id=PARENT_BUILD, state_hash="s" * 64)
    store.add_chunks(_chunks())
    return store


def _restamp(path: Path, index_build_id: str) -> None:
    """Rewrite ``index_metadata.index_build_id`` the way ``index_purge._restamp`` does.

    Issued directly rather than through ``derive_purged`` so the test pins one
    thing: that ``add_nodes`` consults this row. Driving the whole purge would
    make a failure ambiguous between the read and the purge's own ordering, which
    ``test_index_purge_nodes.py`` already owns.
    """
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            "UPDATE index_metadata SET index_build_id = ? WHERE id = 1", (index_build_id,)
        )


def _node_build_ids(path: Path) -> set[str]:
    with closing(sqlite3.connect(path)) as connection:
        return {row[0] for row in connection.execute("SELECT index_build_id FROM nodes")}


def test_add_nodes_stamps_every_node_with_the_build_id_the_metadata_row_names(
    store: SqliteIndexStore, forest: list[IndexableNode]
) -> None:
    """RED means ``add_nodes`` stopped taking the stamp from the file it writes into.

    ADR-0024 decision 2: ``index_build_id`` is read back, "rather than taking it
    as an argument that could disagree with the file it writes into".

    The file is created as ``PARENT_BUILD`` and its metadata row is then moved to
    ``RESTAMPED_BUILD`` -- a value ``add_nodes`` is never handed and that appears
    nowhere in its arguments. So a node can carry it only by having been stamped
    from the row. An implementation that hardcoded the value, took it from a
    parameter, derived it from the filename, or cached the id from ``create``
    writes ``PARENT_BUILD`` or something else and fails here; the previous stamp
    is asserted absent as well as the new one present, because "every node carries
    the new id" and "no node carries the old one" fail on different mutations when
    a batch is written in more than one statement.
    """
    _restamp(store.path, RESTAMPED_BUILD)

    written = store.add_nodes(
        forest, embedding_model="", embedding_model_revision="", embedding_dimension=0
    )

    assert written == len(forest), f"{written} of {len(forest)} nodes were written"
    assert _node_build_ids(store.path) == {RESTAMPED_BUILD}, (
        f"the nodes carry {sorted(_node_build_ids(store.path))}, not the "
        f"`index_metadata.index_build_id` the file names ({RESTAMPED_BUILD!r}). "
        f"`add_nodes` is no longer reading the column out of the file it writes "
        f"into, so a purge's recompute -- which runs before `_restamp` -- would "
        f"stamp nodes from somewhere the restamp does not repair (ADR-0024 "
        f"decision 2)."
    )


def test_add_nodes_refuses_a_file_whose_index_metadata_row_is_missing(
    store: SqliteIndexStore, forest: list[IndexableNode]
) -> None:
    """RED means a half-built index takes nodes that can name no build.

    The other side of the same read. If the row is gone -- a file that is not an
    index, or one whose creation did not finish -- there is no answer to "which
    build is this node in", and a node written anyway is unprovenanced in a column
    nothing serves and nothing would report.

    The refusal has to be the product's own error with the product's own remedy.
    Removing the ``row is None`` guard does not make this pass: the very next line
    subscripts the row, so the failure arrives as a ``TypeError`` from inside a
    store method, which is what this asserts against rather than merely asserting
    that "something raised".
    """
    with closing(sqlite3.connect(store.path)) as connection, connection:
        connection.execute("DELETE FROM index_metadata")

    with pytest.raises(IndexBuildError) as refusal:
        store.add_nodes(
            forest, embedding_model="", embedding_model_revision="", embedding_dimension=0
        )

    assert "no index_metadata row" in str(refusal.value), (
        f"the refusal no longer says why the file cannot take a node: {refusal.value}"
    )
    assert "theurian index build" in str(refusal.value), (
        f"the refusal no longer names the remedy: {refusal.value}"
    )
    assert not _node_build_ids(store.path), "nodes were written into a file with no build identity"


def test_every_declared_add_nodes_is_one_the_signature_pin_reads() -> None:
    """The population control for the signature pin, asserted before it is searched.

    :data:`ADD_NODES_DECLARATIONS` is derived, so the pin below covers a fourth
    ``add_nodes`` on the day it is written. A derivation that resolved nothing
    reports "no declaration takes the argument" in exactly the same way a clean
    tree does, so the population is established first.

    The two named here are the two ADR-0024 decision 2's clause is *about*: the
    adapter that reads the column and the port a second adapter would be written
    against. They are required by name rather than by count -- a count would
    redden on a third protocol that is nobody's problem, while a missing adapter
    means the derivation stopped seeing the module the decision names.
    """
    labels = {label for label, _ in ADD_NODES_DECLARATIONS}

    assert labels >= {
        "infrastructure/sqlite/index_store.py::SqliteIndexStore.add_nodes",
        "domain/ports/index_store.py::IndexStore.add_nodes",
    }, (
        f"the `add_nodes` derivation no longer finds the adapter and the port "
        f"ADR-0024 decision 2 reasons about; it found {sorted(labels)}. The pin "
        f"below would report an absence about a population it never located."
    )


@pytest.mark.parametrize(
    ("label", "parameters"),
    ADD_NODES_DECLARATIONS,
    ids=[case[0] for case in ADD_NODES_DECLARATIONS],
)
def test_add_nodes_takes_no_build_id_argument(label: str, parameters: tuple[str, ...]) -> None:
    """RED means the stamp became something a caller can disagree with the file about.

    ADR-0024 decision 2's clause is not only that the column is read but that it
    is read *instead of* being passed: "rather than taking it as an argument that
    could disagree with the file it writes into". A refactor that added the
    parameter would leave the behaviour test above green as long as every caller
    happened to pass the matching value -- and the purge is exactly the caller that
    would not, since it recomputes the forest before ``_restamp`` moves the id.

    **Every declaration in the package, derived rather than transcribed.** The
    adapter and the port were named by hand here until round one, and the list was
    short by one: ``withdrawal_purge.ForestRecomputeStore.add_nodes`` is a third
    declaration, and it is the protocol the *purge* holds its building-file store
    as -- the one caller that recomputes the forest before ``_restamp`` moves the
    id, which is to say the caller the clause exists for. Walking for the
    declarations means a fourth one is covered by the change that writes it.
    """
    assert "index_build_id" not in parameters, (
        f"{label} now takes `index_build_id` as an argument: {list(parameters)}. "
        f"ADR-0024 decision 2 says the stamp is read out of the file being written "
        f"into precisely so a caller cannot disagree with it; adding the parameter "
        f"reinstates that disagreement and the decision has to be corrected in the "
        f"same change."
    )
