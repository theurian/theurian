"""Tree and node identity for RAPTOR summaries (ADR-0008 decisions 1, 2, 9).

ADR-0008 decision 1 says a node's tree is determined by the six-component scope
tuple -- ``(project, tenant, sensitivity, acl_group, namespace, status)`` as of
the Milestone 6 amendment -- so a node whose children differ in any component
cannot exist: there is no tree it could belong to. That is a structural
guarantee only if construction itself refuses the mismatch; :class:`SummaryNode`
is where that refusal lives.

:attr:`SummaryNode.children` are the DECLARED child scopes, not the children's
own summary nodes -- the invariant below guarantees only that these
declarations agree with the node's own scope. Deriving each declaration from
the scope the actual child was built with is the builder's obligation, not
something this type can check: a builder that passes ``(parent,) * n`` for
every node satisfies this type without ever consulting a real child.
:class:`IndexableNode` is what closes that half: it refuses a node whose
declared children do not stand one-per-source, so a declaration that
corresponds to no source cannot be constructed. That is the part a test can
hold, and does -- ``test_an_indexable_node_refuses_more_declared_children_than_
sources`` pins the constructible defect. The other part is not testable, and the
honesty matters: for a *valid* node, a declaration copied from the parent and
one derived from the child are equal by this type's own scope invariant, so no
test separates a builder that consulted its children from one that passed
``(parent,) * n``. The builder
(:mod:`theurian.application.forest_builder`) supplies each declaration from the
child it summarises; the guarantee that it corresponds to a real source is
structural, and the guarantee that a *correct* one was chosen rests on the
builder's grouping being right (``tests/unit/test_forest_derivation.py``'s
scope-boundary tests), not on distinguishing the two indistinguishable forms.

The identity functions live here rather than in the builder because they are
what ADR-0008 decision 9 is *about*: two derivations of one state must produce
one id, so the recipe has to be a pure function of the node's own parts and
stated somewhere a purge, a build and a test can all reach.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, fields
from enum import StrEnum
from typing import Final, final

from theurian.domain.errors import InvariantViolationError
from theurian.domain.values import ContentHash, Scope

#: The separator :attr:`Scope.key` joins its components with, reused by both
#: identity functions below so that a scope key, a tree id and a node id are
#: built from one encoding rule rather than three. Every component either
#: rejects control characters at construction (``Scope``'s do) or is a hex
#: digest or a decimal integer written here, so the separator cannot occur
#: inside a component and two different tuples cannot render one string.
_SEPARATOR: Final = "\x1f"


class NodeType(StrEnum):
    """What a summary node summarises, which is what its level means.

    ADR-0008 decision 2 names exactly these three tiers, numbered upward from
    the leaves -- the numbering ``index_schema.py``'s ``CHECK (level BETWEEN 1
    AND 3)`` already assumes. The pair is kept as one declaration in
    :data:`TIERS` rather than as a level constant beside a string, because a row
    whose ``level`` and ``node_type`` disagree describes a tier the forest does
    not have.
    """

    DOCUMENT = "document"
    DOMAIN = "domain"
    CATALOG = "catalog"


#: The tiers in level order: ``TIERS[level - 1]`` is what a node at that level
#: summarises. Its length is the deepest forest ADR-0008 decision 2 describes,
#: and the index schema's ``CHECK`` is the same number written in SQL.
TIERS: Final = (NodeType.DOCUMENT, NodeType.DOMAIN, NodeType.CATALOG)

#: The highest ``level`` a node may carry. Named rather than spelled ``3``
#: because the CHECK constraint in `index_schema.py` is the same bound in
#: another language, and a build that produced a level the file refuses would
#: fail as an opaque `IntegrityError` at insert time.
MAX_LEVEL: Final = len(TIERS)


def tree_identity(*, scope: Scope, node_type: NodeType, discriminator: str) -> ContentHash:
    """The tree a node belongs to: its scope, its tier, and what partitions it.

    ADR-0008 decision 1 makes the six-component scope the tree boundary, and
    decision 9 records why that is not the whole key: "``tree_id`` for a
    Document tree includes the item's identity, without which two document trees
    holding duplicate content mint the same id for different nodes". Duplicate
    content is ordinary -- a copied runbook, a template, a document split in two
    -- and one id for two nodes is either a primary key violation at write time
    or a silently merged forest, depending on which insert runs second.

    So ``discriminator`` is what partitions the scope at this tier: the item for
    a Document tree, the kind for a Domain tree, and nothing for the Catalog,
    which is the scope itself. ``node_type`` is joined as well as the
    discriminator, so a Document tree over an item named ``x`` and a Domain tree
    over a kind named ``x`` cannot render one key.
    """
    return ContentHash.of_text(_SEPARATOR.join((scope.key, node_type.value, discriminator)))


def node_identity(
    *, tree_id: ContentHash, level: int, child_hashes: Iterable[ContentHash]
) -> ContentHash:
    """ADR-0008 decision 9's identity function.

    "A deterministic function of (``tree_id``, level, the children's content
    hashes sorted lexicographically), joined with the same unit separator
    ``Scope.key`` uses and hashed."

    **The sort is part of the definition, not an implementation detail.** A
    purge that rewrites a tree can produce the same children in a different
    physical order than a build over a corpus that never held the withdrawn rows
    did, and an id that moved between the two would break the two-corpus
    equality decision 9 rests on -- which is a property of the *whole response*,
    not of one column, so a moved id is not a cosmetic difference.

    **Level is part of it** because two tiers of one tree are two nodes, and a
    Domain node built from one Document node's text would otherwise collide with
    it whenever a threshold of 1 were configured.

    Raises:
        InvariantViolationError: ``child_hashes`` is empty. Such an id is a
            function of ``(tree_id, level)`` alone, so every childless node in a
            tree would share it -- and a node with no children summarises
            nothing, which :class:`SummaryNode` already refuses to exist.
    """
    ordered = sorted(child_hash.value for child_hash in child_hashes)
    if not ordered:
        raise InvariantViolationError(
            "node_identity needs at least one child hash -- an id over no children "
            "is a function of (tree_id, level) alone, so every childless node in a "
            "tree collides. Build the node from the children it summarises "
            "(ADR-0008 decision 9)."
        )
    return ContentHash.of_text(_SEPARATOR.join((tree_id.value, str(level), *ordered)))


def _differing_components(node_scope: Scope, child_scope: Scope) -> tuple[str, ...]:
    """Names of the ``Scope`` fields where ``node_scope`` and ``child_scope`` disagree.

    Named rather than the child's full repr in the raised message: a component
    name diagnoses a scope mismatch without echoing the tenant, acl_group or
    namespace value into operator-facing output.
    """
    return tuple(
        f.name
        for f in fields(node_scope)
        if getattr(node_scope, f.name) != getattr(child_scope, f.name)
    )


@final
@dataclass(frozen=True, slots=True)
class SummaryNode:
    """A node in a RAPTOR tree, identified by the scope its children share.

    Per ADR-0008 decision 1, a node's tree is the six-component scope tuple. A
    node built from children that disagree on any component -- project, tenant,
    sensitivity, acl_group, namespace, or status -- has no tree to belong to, so
    construction refuses it rather than producing a node an isolation check would
    later have to catch.

    ``@final``: a subclass overriding ``__post_init__`` could mint a node whose
    children were never checked against its scope, which would defeat the
    guarantee above without touching this file.
    """

    scope: Scope
    children: tuple[Scope, ...]

    def __post_init__(self) -> None:
        # Frozen freezes the binding, not what it points at: a list handed to
        # the constructor is not automatically this dataclass's own storage, so
        # a caller mutating that list afterward would mutate a node it was told
        # is immutable (measured). Normalised first, before either check below,
        # so nothing here can observe the caller's original list.
        object.__setattr__(self, "children", tuple(self.children))
        if not self.children:
            raise InvariantViolationError(
                "SummaryNode must have at least one child -- a node with no "
                "children summarises nothing and has no tree to belong to"
            )
        for child_scope in self.children:
            if child_scope != self.scope:
                differing = ", ".join(_differing_components(self.scope, child_scope))
                raise InvariantViolationError(
                    f"SummaryNode child scope differs from the node's own scope "
                    f"in {differing} -- a node whose children disagree on scope "
                    "has no tree to belong to (ADR-0008 decision 1)"
                )

    @property
    def tree_id(self) -> ContentHash:
        """The scope boundary this node's tree sits inside.

        Total over the six-component scope tuple, because ``Scope.key`` joins
        all six and component validation keeps the encoding unambiguous
        (``values.py``), so two distinct scopes cannot produce one value -- the
        isolation half of ADR-0008 decision 1.

        **It is not the id a ``nodes`` row carries, and the difference is
        decision 9's.** A scope holds more than one tree: one per item at the
        Document tier and one per kind at the Domain tier, or two items with
        duplicate content mint the same id for different nodes. What a built
        forest stores is :func:`tree_identity`, which adds the tier and that
        within-scope partition on top of this.
        """
        return self.scope.digest


@final
@dataclass(frozen=True, slots=True)
class IndexableNode:
    """A summary node together with everything a ``nodes`` row records.

    Carried alongside :class:`SummaryNode` rather than inside it for the reason
    :class:`~theurian.domain.chunking.IndexableChunk` is carried alongside
    ``Chunk``: scope membership is a property of the tree, and these are
    properties of the build that wrote the row.

    **This is where the declaration obligation the module docstring names is
    discharged.** ``SummaryNode`` cannot see whether a declared child scope
    corresponds to anything, because it is handed scopes and not children. Here
    the sources are named, so a node whose declarations do not stand
    one-per-source cannot be constructed -- which is what makes
    ``node.children`` evidence about the sources rather than a restatement of
    the node's own scope.

    ``embedding_model`` and its dimension are deliberately absent: the summary
    is what this value is, and which embedder vectorises it is a fact about the
    build (:meth:`~theurian.domain.ports.index_store.IndexStore.add_nodes`
    takes it). A node derived with no embedder configured is the same node.
    """

    node: SummaryNode
    #: Content-addressed, per :func:`node_identity`. Stored rather than
    #: recomputed because recomputing it needs the children's *texts*, which a
    #: node does not carry -- only their ids.
    node_id: str
    #: Per :func:`tree_identity`, not :attr:`SummaryNode.tree_id`.
    tree_id: str
    level: int
    text: str
    summary_model: str
    summary_model_revision: str
    #: ADR-0008 decision 5 decides staleness by comparing this against the
    #: configured provider's, so a placeholder here makes every node
    #: permanently fresh.
    summary_prompt_hash: str
    #: The revision this node's text was written against, or empty above the
    #: Document tier. ``index_purge._DOOMED`` dooms a node whose stamp names a
    #: withdrawn revision *whatever its edges still point at*, and a node built
    #: from other nodes has no single revision to name -- its withdrawal
    #: reaches it through those edges instead. Empty is safe there because a
    #: revision id is a ULID and no withdrawal set can contain ``""``.
    source_revision_id: str
    source_chunk_ids: tuple[str, ...] = ()
    source_node_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        # Normalised before anything reads them, for the reason `SummaryNode`
        # normalises `children`: frozen freezes the binding, not the list a
        # caller may still hold.
        object.__setattr__(self, "source_chunk_ids", tuple(self.source_chunk_ids))
        object.__setattr__(self, "source_node_ids", tuple(self.source_node_ids))
        if not 1 <= self.level <= MAX_LEVEL:
            raise InvariantViolationError(
                f"IndexableNode level must be 1..{MAX_LEVEL}, got {self.level} -- a row "
                f"claiming another tier invents one the forest does not have, and the "
                f"index schema refuses it as an opaque constraint failure at insert "
                f"time (ADR-0008 decision 2)"
            )
        # Constructed for their validation and discarded: both are digests, and
        # `ContentHash` is where "64 lowercase hex characters" is decided. A
        # malformed id would otherwise travel as far as a `nodes` row, where no
        # constraint refuses it and every later comparison against a recomputed
        # id fails without saying why.
        ContentHash(self.node_id)
        ContentHash(self.tree_id)
        self._refuse_a_declaration_standing_for_nothing()

    def _refuse_a_declaration_standing_for_nothing(self) -> None:
        """One declared child scope per source, and no source named twice.

        The first is the obligation ``SummaryNode`` cannot hold: ``(parent,) *
        n`` satisfies that type without consulting a child, so a clusterer that
        reached across a scope boundary would be caught by nothing. Counting
        the declarations against the sources is what makes the mismatch
        unconstructible.

        The second is the schema's two partial unique indexes on
        ``node_derivation`` -- refused here so a duplicated source arrives as a
        sentence naming the node rather than as an ``IntegrityError`` from the
        middle of a batch insert.
        """
        sources = len(self.source_chunk_ids) + len(self.source_node_ids)
        if len(self.node.children) != sources:
            raise InvariantViolationError(
                f"IndexableNode declares {len(self.node.children)} child scope(s) for "
                f"{sources} source(s) -- a declaration that stands for no source is not "
                f"evidence about anything the node was built from (ADR-0008 decision 1)"
            )
        for label, ids in (("chunk", self.source_chunk_ids), ("node", self.source_node_ids)):
            if len(set(ids)) != len(ids):
                raise InvariantViolationError(
                    f"IndexableNode names a source {label} twice -- `node_derivation` is "
                    f"unique per (node, source), so one of the two edges would be refused"
                )

    @property
    def node_type(self) -> str:
        """What this node summarises, derived from its level rather than stored.

        A row whose ``level`` and ``node_type`` disagree describes a tier the
        forest does not have, and two fields that must agree are two fields to
        keep in step. :data:`TIERS` is the single declaration.
        """
        return TIERS[self.level - 1].value

    @property
    def content_hash(self) -> str:
        """The hash of :attr:`text`, computed rather than carried.

        Stored on the row so a reader can verify the text against it; derived
        here so a build cannot write a hash of anything else.
        """
        return ContentHash.of_text(self.text).value

    @property
    def scope(self) -> Scope:
        return self.node.scope

    def edges(self) -> Sequence[tuple[str, str | None, str | None]]:
        """This node's ``node_derivation`` rows: exactly one source per edge.

        Assembled here rather than at the adapter because the schema's CHECK --
        one of ``source_chunk_id`` and ``source_node_id`` populated, never both
        -- is a property of what a derivation edge *is*, not of SQLite.
        """
        return [(self.node_id, chunk_id, None) for chunk_id in self.source_chunk_ids] + [
            (self.node_id, None, node_id) for node_id in self.source_node_ids
        ]
