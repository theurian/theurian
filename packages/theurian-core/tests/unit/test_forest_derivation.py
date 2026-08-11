"""Deriving a RAPTOR forest from leaf chunks (ADR-0008 decisions 2, 6, 9).

**Written RED, ahead of `theurian.application.forest_builder`.** The module does
not exist; every test here fails on the import until it does.

Unit rather than integration, and that is a claim about the thing being tested
rather than about speed. ADR-0008 decision 9 is reachable only if tree
derivation is a *pure function* of (surviving rows, scope, configuration), so a
derivation needing a database to run would already have failed the property
these tests exist to hold. `ForestBuilder` is handed chunks and a
`SummarizationProvider` and returns nodes; the store, the index file and the CLI
are `tests/integration/test_forest_builder.py`'s business.

`infrastructure/raptor/` was the obvious home for the builder and is the wrong
one. `tests/unit/test_layering.py::test_application_does_not_import_infrastructure`
walks the real import graph, and `application/index_builder.py` is where the
forest pass has to mount -- so a builder under `infrastructure/` could not be
called from the one place that must call it. ADR-0008 decision 7 puts
*summarization* behind a port and `docs/architecture/raptor.md` says the
hierarchy itself has none, which leaves the builder as application-layer policy
over ports that already exist.
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass, replace
from typing import Any, Final, final

import pytest

from theurian.domain.chunking import Chunk, IndexableChunk
from theurian.domain.enums import KnowledgeStatus, Sensitivity
from theurian.domain.identifiers import ProjectId
from theurian.domain.ports.summarization import SummarizationProvider
from theurian.domain.ranking import estimate_tokens
from theurian.domain.values import AclGroup, ContentHash, Scope, TenantId
from theurian.infrastructure.raptor.extractive import ExtractiveSummarizer

pytestmark = pytest.mark.unit

PROJECT: Final = "demo"

DOCUMENT_LEVEL: Final = 1
DOMAIN_LEVEL: Final = 2
CATALOG_LEVEL: Final = 3

REPO_ROOT: Final = pathlib.Path(__file__).resolve().parents[4]

_SENTENCES: Final = (
    "Every call carries a signed token issued by the gateway service.",
    "Tokens rotate on restart and expire after one hour of idle time.",
    "The quarantine ledger records every revoked token and its reason.",
)


@dataclass(frozen=True, slots=True)
class Axes:
    """The canonical facts a chunk carries that decide which tree it lands in.

    ``kind`` and ``namespace`` are separate because ADR-0008 decision 2 keys a
    Domain tree by *both*, while ``namespace`` alone is already a component of
    the scope tuple -- so within one scope it is ``kind`` that decides which
    Domain tree a document belongs to.
    """

    kind: str = "architecture"
    namespace: str = "backend"
    status: str = "approved"
    sensitivity: str = "internal"


#: The axes a test does not vary. A module-level singleton rather than a call in
#: a default argument, which ruff's B008 refuses; safe to share because `Axes` is
#: frozen and every variant below is built with `dataclasses.replace`.
DEFAULT_AXES: Final = Axes()


def _document(item: str, axes: Axes, *, chunks: int = 3) -> list[IndexableChunk]:
    """One item's leaf chunks, carrying the canonical facts a scope is derived from."""
    revision = f"rev-{item}"
    return [
        IndexableChunk(
            chunk=Chunk(
                chunk_id=f"{revision}#{ordinal}",
                ordinal=ordinal,
                text=f"{item} section {ordinal}. {_SENTENCES[ordinal % len(_SENTENCES)]}",
                heading="",
            ),
            project_id=PROJECT,
            item_id=item,
            revision_id=revision,
            status=axes.status,
            sensitivity=axes.sensitivity,
            trust_level="reviewed",
            namespace=axes.namespace,
            kind=axes.kind,
        )
        for ordinal in range(chunks)
    ]


def _corpus(
    *, kinds: int = 1, items_per_kind: int = 3, prefix: str = "", axes: Axes = DEFAULT_AXES
) -> list[IndexableChunk]:
    """``kinds`` x ``items_per_kind`` items, each of three chunks.

    ``prefix`` keeps item ids distinct when a test builds two corpora that
    differ only in a scope component: two items with one id are one Document
    tree, which would hide the very partition such a test is checking.
    """
    corpus: list[IndexableChunk] = []
    for kind in ("architecture", "operations", "security")[:kinds]:
        for number in range(items_per_kind):
            corpus.extend(_document(f"{kind}.{prefix}item-{number}", replace(axes, kind=kind)))
    return corpus


def _scope_of(chunk: IndexableChunk) -> Scope:
    """The six-component scope a chunk implies, stated independently of the builder.

    ``tenant_id`` and ``acl_group`` are the write-time enforced defaults: until
    #119 lands an ``AuthorizationProvider``, `migrate validate` and `migrate
    apply` refuse any other value, so no chunk can carry one.
    """
    return Scope(
        project_id=ProjectId(chunk.project_id),
        tenant_id=TenantId(),
        sensitivity=Sensitivity(chunk.sensitivity),
        acl_group=AclGroup(),
        namespace=chunk.namespace,
        status=KnowledgeStatus(chunk.status),
    )


def _derive(chunks: list[IndexableChunk], **options: int) -> tuple[Any, ...]:
    from theurian.application.forest_builder import (
        ForestBuilder,
        ForestOptions,
    )

    builder = ForestBuilder(summarizer=ExtractiveSummarizer(), options=ForestOptions(**options))
    return builder.derive(chunks)


def _levels(nodes: tuple[Any, ...]) -> list[int]:
    return [node.level for node in nodes]


def _leaves(node: Any, by_id: dict[str, Any]) -> set[str]:
    """The leaf chunk ids a node stands on, following node sources transitively."""
    chunk_ids = set(node.source_chunk_ids)
    for source in node.source_node_ids:
        chunk_ids |= _leaves(by_id[source], by_id)
    return chunk_ids


# -- Levels ------------------------------------------------------------------


def test_a_three_level_corpus_builds_a_document_a_domain_and_a_catalog_node() -> None:
    """ADR-0008 decision 2's three levels, and the fixture that reaches all of them.

    Nine items over three kinds in one scope: each item's three chunks clear
    `minChildrenPerSummary`, each kind's three document nodes clear it again,
    and three domain nodes clear it once more. Every test below that asserts a
    level is *absent* needs this one beside it, or "absent" is satisfied by a
    builder that never builds anything.

    Within one scope the namespace is fixed, so ADR-0008 decision 2's "one
    namespace or kind" reduces to `kind` -- which is why `IndexableChunk` has to
    carry it. Without that field a scope holds exactly one domain tree, the
    catalog level always has one child, and the forest is structurally incapable
    of the three levels this ADR names.
    """
    nodes = _derive(_corpus(kinds=3, items_per_kind=3))

    levels = _levels(nodes)
    assert levels.count(DOCUMENT_LEVEL) == 9
    assert levels.count(DOMAIN_LEVEL) == 3
    assert levels.count(CATALOG_LEVEL) == 1
    assert {node.node_type for node in nodes} == {"document", "domain", "catalog"}


def test_max_levels_of_one_stops_at_the_document_node() -> None:
    """`maxLevels` is a ceiling on tiers, and level 1 *is* a tier.

    ADR-0008 decision 2 names the Document Tree as the first level rather than
    as the leaves, so `maxLevels: 1` means document nodes and nothing above --
    not "no forest at all", which is what `enabled: false` means, and not "two
    tiers", which would make the schema's `minimum: 1` name a value that skips
    the only level a small corpus ever reaches.
    """
    nodes = _derive(_corpus(kinds=3, items_per_kind=3), max_levels=1)

    assert set(_levels(nodes)) == {DOCUMENT_LEVEL}
    assert len(nodes) == 9


def test_max_levels_of_two_stops_below_the_catalog() -> None:
    """The middle case, which the fixture above is sized to make non-vacuous.

    Three domain nodes earn a catalog node at the default of three levels --
    `test_a_three_level_corpus_builds_a_document_a_domain_and_a_catalog_node` is
    the control that says so -- so a catalog absent here is `maxLevels` acting
    rather than a threshold that was never met.
    """
    nodes = _derive(_corpus(kinds=3, items_per_kind=3), max_levels=2)

    levels = _levels(nodes)
    assert levels.count(DOMAIN_LEVEL) == 3
    assert CATALOG_LEVEL not in levels


def test_min_children_per_summary_is_a_floor_the_caller_can_move() -> None:
    """The threshold is a parameter, and this corpus straddles it in both directions.

    Two documents earn no domain node at the default of three and do earn one at
    two. Asserting only the first half passes against a builder that never
    builds a domain node; asserting only the second passes against one that
    ignores the threshold entirely.
    """
    corpus = _corpus(kinds=1, items_per_kind=2)

    default = _derive(corpus)
    lowered = _derive(corpus, min_children_per_summary=2)

    assert _levels(default).count(DOCUMENT_LEVEL) == 2
    assert DOMAIN_LEVEL not in _levels(default)
    assert _levels(lowered).count(DOMAIN_LEVEL) == 1


def test_the_option_defaults_are_the_config_schemas_own() -> None:
    """`ForestOptions` and `schemas/config/project-config.schema.json` must agree.

    The builder takes these as parameters because nothing in `src/` reads
    `.theurian/config.yaml` at all -- ADR-0008 decision 10's amendment says so
    of `raptor.enabled`, and `docs/architecture/raptor.md` says the same of
    `minChildrenPerSummary`. Two independently written defaults that happen to
    agree today is exactly the shape that drifts the moment one of them is
    tuned, and the day a config loader lands the drift changes behaviour for
    everyone who never set either.
    """
    from theurian.application.forest_builder import ForestOptions

    schema = json.loads(
        (REPO_ROOT / "schemas/config/project-config.schema.json").read_text(encoding="utf-8")
    )
    raptor = schema["properties"]["raptor"]["properties"]

    assert ForestOptions().max_levels == raptor["maxLevels"]["default"]
    assert ForestOptions().min_children_per_summary == raptor["minChildrenPerSummary"]["default"]


# -- The declared child scopes -----------------------------------------------


def test_each_declared_child_scope_is_the_scope_of_the_source_it_summarises() -> None:
    """The half of decision 1's guarantee `SummaryNode` cannot hold.

    `SummaryNode.children` are *declarations*. The type refuses a child whose
    scope differs from the node's own, so a builder passing `(parent,) * n`
    satisfies it without consulting a single child and the refusal never fires
    -- `domain/raptor.py`'s module docstring states the obligation and names the
    builder CL as what discharges it.

    **What this can and cannot distinguish, said plainly, because the difference
    decides whether the assertion may ever be relaxed.** It cannot tell a
    correct grouping declared from the parent apart from one declared from the
    children: for a valid node the two are equal, by the type's own invariant,
    and no test can separate them. What it does catch is the harm the obligation
    is about -- a declaration that does not correspond to the source the
    provenance names. One declaration per source, and every source's *own* scope
    equal to it, so a clusterer that reached across a scope boundary produces a
    node whose declarations disagree with its sources however it filled
    `children`.

    The corpus mixes two statuses under one namespace and kind, which is
    precisely the grouping a builder keyed on `(namespace, kind)` alone would
    merge -- the case the Milestone 6 amendment added `status` to the tuple for.
    """
    approved = _corpus(kinds=1, items_per_kind=3, prefix="a-", axes=Axes(status="approved"))
    draft = _corpus(kinds=1, items_per_kind=3, prefix="d-", axes=Axes(status="draft"))
    scope_of_chunk = {c.chunk.chunk_id: _scope_of(c) for c in (*approved, *draft)}

    nodes = _derive([*approved, *draft])

    by_id = {node.node_id: node for node in nodes}
    assert len(nodes) == 8, "six document nodes and two domain nodes, one per status"
    for node in nodes:
        sources = [scope_of_chunk[c] for c in node.source_chunk_ids]
        sources += [by_id[n].node.scope for n in node.source_node_ids]
        assert sources, "a node with no sources declares children it was never given"
        assert len(node.node.children) == len(sources), (
            "one declared child scope per source, or a declaration stands for nothing"
        )
        assert set(node.node.children) == set(sources)


def test_a_node_never_mixes_two_statuses_under_one_namespace_and_kind() -> None:
    """The clustering failure the test above is shaped to catch, asserted directly.

    Its own test because the two go red for different reasons and a reader has
    to be able to tell them apart: this one when the *grouping* crosses a scope
    boundary, the one above when the *declaration* does not match the source. A
    builder keyed on `(namespace, kind)` alone fails both; a builder that groups
    correctly and declares `(parent,) * n` fails neither, which is exactly what
    the value type already cannot see.
    """
    approved = _corpus(kinds=1, items_per_kind=3, prefix="a-", axes=Axes(status="approved"))
    draft = _corpus(kinds=1, items_per_kind=3, prefix="d-", axes=Axes(status="draft"))
    scope_of_chunk = {c.chunk.chunk_id: _scope_of(c) for c in (*approved, *draft)}
    assert len({scope.status for scope in scope_of_chunk.values()}) == 2

    nodes = _derive([*approved, *draft])

    by_id = {node.node_id: node for node in nodes}
    assert _levels(nodes).count(DOMAIN_LEVEL) == 2, (
        "two statuses under one namespace and kind are two scopes, so two domain trees"
    )
    for node in nodes:
        leaves = _leaves(node, by_id)
        assert leaves, f"node {node.node_id} stands on no chunk at all"
        assert len({scope_of_chunk[c] for c in leaves}) == 1


# -- Determinism -------------------------------------------------------------


def test_the_derivation_does_not_depend_on_the_order_chunks_arrive_in() -> None:
    """ADR-0008 decision 9 makes the sort part of the identity function, and this
    is the reason it gives.

    "A purge that rewrites a tree can produce the same children in a different
    physical order than the never-held build did, which alone would break the
    equality this decision rests on." So the builder's own iteration order must
    not reach the output: children are ordered canonically -- chunks by their
    ordinal within a revision, nodes by their content-addressed id -- before
    anything is hashed or summarised.

    Reversal rather than a shuffle, because a shuffle needs a seed to be
    reproducible and a reversal is the permutation most likely to expose a
    builder that simply kept its input order.
    """
    corpus = _corpus(kinds=2, items_per_kind=3)

    forward = _derive(corpus)
    backward = _derive(list(reversed(corpus)))

    def _shape(nodes: tuple[Any, ...]) -> list[tuple[Any, ...]]:
        return sorted(
            (
                node.node_id,
                node.tree_id,
                node.level,
                node.text,
                tuple(node.source_chunk_ids),
                tuple(node.source_node_ids),
            )
            for node in nodes
        )

    assert _shape(forward), "the fixture derived nothing, so the equality is vacuous"
    assert _shape(forward) == _shape(backward)


def test_a_node_id_is_the_hash_of_its_tree_level_and_sorted_child_hashes() -> None:
    """ADR-0008 decision 9's identity function, recomputed from the node's own parts.

    The integration counterpart applies the same recipe to rows read back out of
    a built index; this one applies it to the values the builder returns, so a
    store that rewrote an id on the way in could not make both pass.
    """
    from theurian.domain.raptor import node_identity

    corpus = _corpus(kinds=1, items_per_kind=3)
    text_of = {c.chunk.chunk_id: c.chunk.text for c in corpus}

    nodes = _derive(corpus)

    by_id = {node.node_id: node for node in nodes}
    assert len(nodes) == 4, "three document nodes and one domain node"
    for node in nodes:
        children = [ContentHash.of_text(text_of[c]) for c in node.source_chunk_ids]
        children += [ContentHash.of_text(by_id[n].text) for n in node.source_node_ids]
        assert (
            node_identity(
                tree_id=ContentHash(node.tree_id),
                level=node.level,
                child_hashes=children,
            ).value
            == node.node_id
        )
        assert node.content_hash == ContentHash.of_text(node.text).value


def test_the_identity_function_ignores_the_order_child_hashes_are_given_in() -> None:
    """ "Sorted lexicographically" is in decision 9's definition, not in its prose.

    Stated on the function rather than only through a built forest, because this
    is the degree of freedom the ADR removes by name: two derivations that agree
    on every child and disagree on the order they visited them must produce one
    id. A function preserving the caller's order passes every whole-forest
    determinism test above, which fixes the order on both sides.
    """
    from theurian.domain.raptor import node_identity

    tree = ContentHash.of_text("a tree")
    children = [ContentHash.of_text(text) for text in ("gamma", "alpha", "beta")]

    forward = node_identity(tree_id=tree, level=DOCUMENT_LEVEL, child_hashes=children)
    reversed_ = node_identity(
        tree_id=tree, level=DOCUMENT_LEVEL, child_hashes=list(reversed(children))
    )

    assert forward == reversed_
    assert forward != node_identity(tree_id=tree, level=DOMAIN_LEVEL, child_hashes=children), (
        "level is part of the identity: two tiers of one tree are two nodes"
    )
    assert forward != node_identity(
        tree_id=ContentHash.of_text("another tree"),
        level=DOCUMENT_LEVEL,
        child_hashes=children,
    ), "tree_id is part of the identity: duplicate content in two trees is two nodes"


# -- The summary budget ------------------------------------------------------


@final
class _RecordingSummarizer:
    """A `SummarizationProvider` that records the budget it was handed.

    Deliberately not a mock: it satisfies the port and returns real text, so the
    builder runs its whole path. What it adds is the one observation the port's
    shape cannot give -- ADR-0008 decision 6's `max_tokens` constraint binds the
    *caller*, and no property of a summarizer can hold it.
    """

    model_id = "theurian-test-recording"
    model_revision = "1"
    prompt_hash = "0" * 64

    def __init__(self) -> None:
        self.budgets: list[int] = []
        self.child_counts: list[int] = []

    async def summarize(self, texts: tuple[str, ...], *, scope: Scope, max_tokens: int) -> str:
        assert isinstance(scope, Scope), "the port hands a real scope, not a placeholder"
        self.budgets.append(max_tokens)
        self.child_counts.append(len(texts))
        return " ".join(texts)[:200]


def test_the_summary_budget_is_a_constant_and_not_a_share_of_the_corpus() -> None:
    """ADR-0008 decision 6's carrier (c), closed at the only place that can close it.

    "`max_tokens` must never be a corpus-derived quantity ... a builder that
    divided a shared budget by the number of documents would change a visible
    node's text when a withheld document was added or removed, while the
    summariser itself read nothing it should not."
    `tests/unit/test_extractive_summarizer.py` holds carriers (a) and (c) *at
    the adapter*, with a negative control proving that harness can see a
    corpus-derived budget. It cannot see one the builder computes, because the
    adapter is handed the result and never the recipe.

    Two corpora of different sizes with different cluster sizes, one recorder
    each: every budget recorded must be the same number, and that number must be
    `ForestOptions.summary_max_tokens`. A builder dividing a shared budget by
    document count records two different values across the pair; one scaling
    with cluster size records several within a single run, which is why the
    small corpus is asserted to contain clusters of more than one size.

    **Four items, not three, and the number is the whole fixture.** A Domain
    node's cluster is its *document count*, and a Document node's is its
    *chunk count* -- which `_document` fixes at three. At three items those two
    coincide, every recorded cluster is 3, and a budget scaled by cluster size
    would record one value exactly as a constant does; the guard below said so
    when this test was written RED against three. A fourth item moves the Domain
    cluster off the chunk count without touching anything else.
    """
    from theurian.application.forest_builder import (
        ForestBuilder,
        ForestOptions,
    )

    options = ForestOptions()
    small, large = _RecordingSummarizer(), _RecordingSummarizer()
    assert isinstance(small, SummarizationProvider), "the fake must satisfy the port"

    ForestBuilder(summarizer=small, options=options).derive(_corpus(kinds=1, items_per_kind=4))
    ForestBuilder(summarizer=large, options=options).derive(_corpus(kinds=3, items_per_kind=3))

    assert len(set(small.child_counts)) > 1, (
        "every cluster in the small corpus is the same size, so a budget scaled by "
        "cluster size would be indistinguishable from a constant"
    )
    assert len(large.budgets) > len(small.budgets), "the two corpora must differ in size"
    assert set(small.budgets) | set(large.budgets) == {options.summary_max_tokens}


def test_a_nodes_text_stays_within_the_configured_budget() -> None:
    """The budget is a promise about the payload, not a hint to the summarizer.

    `estimate_tokens` prices every other budget in this system
    (`domain/ranking.take_within_budget`) and is what the extractive default
    charges against, so a node exceeding it here means the builder passed
    something other than what it was configured with.
    """
    from theurian.application.forest_builder import ForestOptions

    budget = 40
    assert ForestOptions().summary_max_tokens > budget, (
        "the fixture must lower the default rather than raise it, or a node that fits "
        "the default fits this one for free"
    )

    nodes = _derive(_corpus(kinds=3, items_per_kind=3), summary_max_tokens=budget)

    assert nodes, "no node was derived, so no budget was charged"
    for node in nodes:
        assert estimate_tokens(node.text) <= budget, (
            f"node {node.node_id} costs more than the budget it was built under"
        )
